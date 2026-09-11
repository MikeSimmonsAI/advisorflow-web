"""SELLER-ASSISTED CATALOGUE SALES — the gate, not the engine.

`catalog_purchase` is tested on its own; what matters here is WHO may ask it
for what. A route that charges another company's customer, or lets a rep type
a number over a fixed price, is a commercial defect whether or not the billing
code underneath it is perfect.

  * a rep sells only for brands they actually sell for;
  * a fixed item cannot be repriced on a deal, not by a little, not with a note;
  * a quoted item needs an amount AND the authority to set one;
  * a resent link is the SAME link — a second one is a second way to pay;
  * a payment link is customer data and is scoped like customer data.
"""
import itertools

import pytest

from app.models.billing_models import BillingInterval
from app.models.catalog_models import (BrandCatalogItem, CatalogItemKind,
                                       CatalogPricingMode)
from app.models.models import Organization, Platform, User
from app.models.purchase_models import (CatalogPurchase, PricingSource,
                                        PurchaseStatus)
from app.models.sales_models import (BrandSalesOrg, Membership,
                                     ROLE_SALES_MANAGER, ROLE_SALES_REP,
                                     SCOPE_BRAND_SALES_ORG)
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


# ═══════════════════════════════════════════════════════════════════════════
# A brand, its sales org, its customer, and people who sell for it
# ═══════════════════════════════════════════════════════════════════════════

def _platform(db, name="EvoSys Pro"):
    p = Platform(name=name, slug="plat-%d" % next(_SEQ))
    db.add(p)
    db.commit()
    return p


def _sales_org(db, platform):
    b = BrandSalesOrg(platform_id=platform.id, name="%s Sales" % platform.name,
                      slug="bso-%d" % next(_SEQ))
    db.add(b)
    db.commit()
    return b


def _customer_org(db, platform, *, subscribed=True):
    org = Organization(name="ZZ Customer %d" % next(_SEQ),
                       slug="zz-cust-%d" % next(_SEQ), plan="growth")
    org.platform_id = platform.id
    if subscribed:
        org.stripe_customer_id = "cus_zz"
        org.stripe_subscription_id = "sub_zz"
        org.billing_status = "active"
    db.add(org)
    db.commit()
    return org


def _seller(db, sales_org, role=ROLE_SALES_REP):
    u = User(organization_id=None, email="seller%d@evosys.live" % next(_SEQ),
             password_hash=hash_password("x"), full_name="Seller",
             role="advisor", must_change_password=False)
    db.add(u)
    db.commit()
    db.add(Membership(user_id=u.id, scope_type=SCOPE_BRAND_SALES_ORG,
                      scope_id=sales_org.id, role=role, is_active=True))
    db.commit()
    return u


def _headers(db, user):
    return {"Authorization": "Bearer %s" % create_access_token(user, db)}


def _catalog_item(db, platform, **kw):
    base = dict(
        platform_id=platform.id, key="extra_users-%d" % next(_SEQ),
        name="Additional Users", kind=CatalogItemKind.RECURRING_ADDON,
        pricing_mode=CatalogPricingMode.FIXED, amount_cents=2500,
        currency="usd", billing_interval=BillingInterval.MONTH,
        self_service=True, seller_assisted=True, is_active=True,
        sort_order=0,
    )
    base.update(kw)
    item = BrandCatalogItem(**base)
    db.add(item)
    db.commit()
    return item


@pytest.fixture
def world(db_session):
    """One brand, its sales org, a customer, a rep and a manager."""
    platform = _platform(db_session)
    sales_org = _sales_org(db_session, platform)
    return {
        "platform": platform,
        "sales_org": sales_org,
        "customer": _customer_org(db_session, platform),
        "rep": _seller(db_session, sales_org, ROLE_SALES_REP),
        "manager": _seller(db_session, sales_org, ROLE_SALES_MANAGER),
    }


@pytest.fixture
def stripe(monkeypatch):
    """Stripe, faked at the same seam the engine's own suite uses."""
    from app.services import catalog_purchase

    calls = []

    class SubscriptionItem:
        @staticmethod
        def create(**kw):
            calls.append(("subscription_item.create", kw))
            return {"id": "si_%d" % len(calls), "price": {"id": "price_x"}}

        @staticmethod
        def delete(item_id, **kw):
            calls.append(("subscription_item.delete", item_id))
            return {"deleted": True}

    class _Session:
        @staticmethod
        def create(**kw):
            calls.append(("checkout.create", kw))
            return {"id": "cs_%d" % len(calls),
                    "url": "https://pay.example/cs_%d" % len(calls)}

    class checkout:
        Session = _Session

    class Fake:
        pass

    fake = Fake()
    fake.SubscriptionItem = SubscriptionItem
    fake.checkout = checkout
    fake.calls = calls
    fake.of = lambda name: [kw for c, kw in calls if c == name]

    monkeypatch.setattr(catalog_purchase, "_stripe", lambda: fake)
    monkeypatch.setattr(catalog_purchase, "_brand_base_url",
                        lambda db, org: "https://app.example")
    import app.routers.billing_router as br
    monkeypatch.setattr(br, "_get_or_create_customer", lambda org, db: "cus_zz")
    monkeypatch.setattr(br, "_brand_display_name", lambda db, org: "EvoSys Pro")
    return fake


class TestOnlyASellerGetsIn:

    def test_the_catalogue_needs_a_token(self, client, world):
        r = client.get("/sales/catalog/customers/%s" % world["customer"].id)
        assert r.status_code == 401

    def test_selling_needs_a_token(self, client, world):
        r = client.post("/sales/catalog/customers/%s/sell"
                        % world["customer"].id, json={"item": "x"})
        assert r.status_code == 401

    def test_an_ordinary_user_with_no_sales_membership_is_refused(
            self, client, db_session, world):
        u = User(organization_id=world["customer"].id,
                 email="nobody%d@x.com" % next(_SEQ),
                 password_hash=hash_password("x"), full_name="Nobody",
                 role="advisor", must_change_password=False)
        db_session.add(u)
        db_session.commit()

        r = client.get("/sales/catalog/customers/%s" % world["customer"].id,
                       headers=_headers(db_session, u))
        assert r.status_code == 403


class TestASellerSellsOnlyForTheirOwnBrand:
    """Being a sales member somewhere is not permission to sell everywhere."""

    def _foreign(self, db_session):
        other_platform = _platform(db_session, name="Other Brand")
        _sales_org(db_session, other_platform)
        return _customer_org(db_session, other_platform)

    def test_another_brands_customer_is_not_visible(self, client, db_session,
                                                    world):
        foreign = self._foreign(db_session)
        r = client.get("/sales/catalog/customers/%s" % foreign.id,
                       headers=_headers(db_session, world["rep"]))
        assert r.status_code == 404

    def test_another_brands_customer_cannot_be_sold_to(self, client,
                                                       db_session, world,
                                                       stripe):
        foreign = self._foreign(db_session)
        item = _catalog_item(db_session, world["platform"])

        r = client.post("/sales/catalog/customers/%s/sell" % foreign.id,
                        json={"item": item.key},
                        headers=_headers(db_session, world["rep"]))
        assert r.status_code == 404
        assert stripe.of("subscription_item.create") == []

    def test_a_missing_customer_and_a_foreign_one_answer_alike(
            self, client, db_session, world):
        """Distinguishing them would turn the route into a directory of other
        brands' customers."""
        foreign = self._foreign(db_session)
        h = _headers(db_session, world["rep"])

        a = client.get("/sales/catalog/customers/%s" % foreign.id, headers=h)
        b = client.get("/sales/catalog/customers/does-not-exist", headers=h)
        assert a.status_code == b.status_code == 404

    def test_another_brands_payment_link_cannot_be_resent(
            self, client, db_session, world, stripe):
        """A payment link is customer data. Walking purchase ids must not
        collect another brand's live links."""
        foreign = self._foreign(db_session)
        p = CatalogPurchase(
            organization_id=foreign.id, platform_id=foreign.platform_id,
            item_key="migration", item_name="Data Migration",
            kind=CatalogItemKind.ONE_TIME, amount_cents=75000, currency="usd",
            quantity=1, status=PurchaseStatus.PENDING,
            pricing_source=PricingSource.CATALOGUE,
            checkout_url="https://pay.example/secret")
        db_session.add(p)
        db_session.commit()

        r = client.post("/sales/catalog/purchases/%s/resend" % p.id,
                        headers=_headers(db_session, world["rep"]))
        assert r.status_code == 404
        assert "secret" not in r.text


class TestWhatTheOfferListShows:

    def test_it_lists_seller_assisted_items(self, client, db_session, world):
        item = _catalog_item(db_session, world["platform"])
        r = client.get("/sales/catalog/customers/%s" % world["customer"].id,
                       headers=_headers(db_session, world["rep"]))
        assert r.status_code == 200
        assert item.key in [o["key"] for o in r.json()["offers"]]

    def test_a_self_service_only_item_is_not_offered_to_a_rep(
            self, client, db_session, world):
        """`seller_assisted` and `self_service` are separate decisions, so the
        rep's list is a different set from the customer's."""
        item = _catalog_item(db_session, world["platform"],
                             self_service=True, seller_assisted=False)
        r = client.get("/sales/catalog/customers/%s" % world["customer"].id,
                       headers=_headers(db_session, world["rep"]))
        assert item.key not in [o["key"] for o in r.json()["offers"]]

    def test_an_inactive_item_is_not_offered(self, client, db_session, world):
        item = _catalog_item(db_session, world["platform"], is_active=False)
        r = client.get("/sales/catalog/customers/%s" % world["customer"].id,
                       headers=_headers(db_session, world["rep"]))
        assert item.key not in [o["key"] for o in r.json()["offers"]]

    def test_an_item_with_no_price_is_not_offered(self, client, db_session,
                                                  world):
        """A price nobody entered is not free."""
        item = _catalog_item(db_session, world["platform"], amount_cents=None)
        r = client.get("/sales/catalog/customers/%s" % world["customer"].id,
                       headers=_headers(db_session, world["rep"]))
        assert item.key not in [o["key"] for o in r.json()["offers"]]

    def test_no_stripe_identifier_reaches_the_seller_screen(
            self, client, db_session, world):
        _catalog_item(db_session, world["platform"],
                      stripe_product_id="prod_secret",
                      stripe_price_id="price_secret")
        r = client.get("/sales/catalog/customers/%s" % world["customer"].id,
                       headers=_headers(db_session, world["rep"]))
        assert "prod_secret" not in r.text
        assert "price_secret" not in r.text

    def test_a_customer_with_no_subscription_cannot_take_addons(
            self, client, db_session, world):
        """Said plainly so a rep is not left guessing why a control is off."""
        bare = _customer_org(db_session, world["platform"], subscribed=False)
        r = client.get("/sales/catalog/customers/%s" % bare.id,
                       headers=_headers(db_session, world["rep"]))
        assert r.json()["can_sell_addons"] is False

    def test_a_rep_is_told_they_may_not_price_quoted_work(
            self, client, db_session, world):
        r = client.get("/sales/catalog/customers/%s" % world["customer"].id,
                       headers=_headers(db_session, world["rep"]))
        authority = r.json()["authority"]
        assert authority["may_price"] is False
        assert authority["reason"]

    def test_a_manager_may_price_quoted_work(self, client, db_session, world):
        r = client.get("/sales/catalog/customers/%s" % world["customer"].id,
                       headers=_headers(db_session, world["manager"]))
        assert r.json()["authority"]["may_price"] is True

    def test_what_the_customer_already_holds_is_marked(self, client,
                                                       db_session, world,
                                                       stripe):
        item = _catalog_item(db_session, world["platform"])
        h = _headers(db_session, world["rep"])
        client.post("/sales/catalog/customers/%s/sell" % world["customer"].id,
                    json={"item": item.key}, headers=h)

        r = client.get("/sales/catalog/customers/%s" % world["customer"].id,
                       headers=h)
        offer = [o for o in r.json()["offers"] if o["key"] == item.key][0]
        assert offer["already_held"] is True


class TestARepMayNotRepriceAFixedItem:

    def test_a_figure_against_a_fixed_item_is_refused(self, client,
                                                      db_session, world,
                                                      stripe):
        """Not by a little, not with a note. An item that should be negotiable
        is configured as QUOTED — a God Mode decision, not a rep's."""
        item = _catalog_item(db_session, world["platform"])
        r = client.post(
            "/sales/catalog/customers/%s/sell" % world["customer"].id,
            json={"item": item.key, "quoted_amount_cents": 100,
                  "note": "special deal"},
            headers=_headers(db_session, world["rep"]))

        assert r.status_code == 400
        assert "repriced" in r.json()["detail"]
        assert stripe.of("subscription_item.create") == []

    def test_even_a_manager_cannot_reprice_a_fixed_item(self, client,
                                                        db_session, world,
                                                        stripe):
        """Authority is about QUOTED work. It is not a licence to overwrite a
        settled catalogue price."""
        item = _catalog_item(db_session, world["platform"])
        r = client.post(
            "/sales/catalog/customers/%s/sell" % world["customer"].id,
            json={"item": item.key, "quoted_amount_cents": 100},
            headers=_headers(db_session, world["manager"]))
        assert r.status_code == 400
        assert stripe.of("subscription_item.create") == []

    def test_the_catalogue_amount_is_what_gets_charged(self, client,
                                                       db_session, world,
                                                       stripe):
        item = _catalog_item(db_session, world["platform"], amount_cents=2500)
        r = client.post(
            "/sales/catalog/customers/%s/sell" % world["customer"].id,
            json={"item": item.key},
            headers=_headers(db_session, world["rep"]))
        assert r.status_code == 200
        assert r.json()["amount_cents"] == 2500
        assert r.json()["pricing_source"] == PricingSource.CATALOGUE


class TestQuotedWorkNeedsAnAmountAndTheAuthorityToSetIt:

    def _quoted(self, db_session, world, **kw):
        return _catalog_item(db_session, world["platform"],
                             key="custom_dev-%d" % next(_SEQ),
                             name="Custom Development",
                             kind=CatalogItemKind.ONE_TIME,
                             billing_interval=None,
                             pricing_mode=CatalogPricingMode.QUOTED,
                             amount_cents=None, **kw)

    def test_a_quoted_item_with_no_amount_is_refused(self, client, db_session,
                                                     world, stripe):
        item = self._quoted(db_session, world)
        r = client.post(
            "/sales/catalog/customers/%s/sell" % world["customer"].id,
            json={"item": item.key},
            headers=_headers(db_session, world["manager"]))
        assert r.status_code == 400
        assert "needs an amount" in r.json()["detail"]

    def test_a_rep_may_not_set_the_amount(self, client, db_session, world,
                                          stripe):
        """403, not 400: the request is well-formed and this person may not
        make it."""
        item = self._quoted(db_session, world)
        r = client.post(
            "/sales/catalog/customers/%s/sell" % world["customer"].id,
            json={"item": item.key, "quoted_amount_cents": 250000},
            headers=_headers(db_session, world["rep"]))
        assert r.status_code == 403
        assert stripe.of("checkout.create") == []

    def test_a_manager_may_set_the_amount(self, client, db_session, world,
                                          stripe):
        item = self._quoted(db_session, world)
        r = client.post(
            "/sales/catalog/customers/%s/sell" % world["customer"].id,
            json={"item": item.key, "quoted_amount_cents": 250000,
                  "note": "Scoped on the 3rd"},
            headers=_headers(db_session, world["manager"]))

        assert r.status_code == 200
        body = r.json()
        assert body["amount_cents"] == 250000
        assert body["pricing_source"] == PricingSource.QUOTED

    def test_a_quoted_amount_never_rides_a_mapped_price(self, client,
                                                        db_session, world,
                                                        stripe):
        """The mapped Price charges the catalogue amount, which is exactly the
        figure a quoted item does not have."""
        item = self._quoted(db_session, world, stripe_price_id="price_mapped")
        client.post("/sales/catalog/customers/%s/sell" % world["customer"].id,
                    json={"item": item.key, "quoted_amount_cents": 250000},
                    headers=_headers(db_session, world["manager"]))

        line = stripe.of("checkout.create")[0]["line_items"][0]
        assert "price" not in line
        assert line["price_data"]["unit_amount"] == 250000


class TestASellerCannotProduceAnOutcomeTheCustomerCouldNot:
    """The seller route runs the same engine as the customer's own button, so
    a recurring add-on cannot become a second subscription by coming in
    through a different door."""

    def test_an_addon_becomes_a_subscription_item(self, client, db_session,
                                                  world, stripe):
        item = _catalog_item(db_session, world["platform"])
        r = client.post(
            "/sales/catalog/customers/%s/sell" % world["customer"].id,
            json={"item": item.key, "quantity": 2},
            headers=_headers(db_session, world["rep"]))

        assert r.status_code == 200
        kw = stripe.of("subscription_item.create")[0]
        assert kw["subscription"] == "sub_zz"
        assert kw["quantity"] == 2
        assert stripe.of("checkout.create") == []

    def test_selling_an_addon_to_an_unsubscribed_customer_is_refused(
            self, client, db_session, world, stripe):
        """An add-on has nothing to attach to. Creating a subscription here
        would be the double-billing defect coming back."""
        bare = _customer_org(db_session, world["platform"], subscribed=False)
        item = _catalog_item(db_session, world["platform"])

        r = client.post("/sales/catalog/customers/%s/sell" % bare.id,
                        json={"item": item.key},
                        headers=_headers(db_session, world["rep"]))
        assert r.status_code == 409
        assert stripe.of("subscription_item.create") == []

    def test_selling_the_same_addon_twice_is_refused(self, client, db_session,
                                                     world, stripe):
        item = _catalog_item(db_session, world["platform"])
        h = _headers(db_session, world["rep"])
        url = "/sales/catalog/customers/%s/sell" % world["customer"].id

        assert client.post(url, json={"item": item.key},
                           headers=h).status_code == 200
        again = client.post(url, json={"item": item.key}, headers=h)
        assert again.status_code == 409
        assert len(stripe.of("subscription_item.create")) == 1

    def test_a_one_time_sale_is_a_payment_checkout(self, client, db_session,
                                                   world, stripe):
        item = _catalog_item(db_session, world["platform"], key="migration",
                             name="Data Migration",
                             kind=CatalogItemKind.ONE_TIME,
                             billing_interval=None, amount_cents=75000)
        r = client.post(
            "/sales/catalog/customers/%s/sell" % world["customer"].id,
            json={"item": item.key},
            headers=_headers(db_session, world["rep"]))

        assert r.status_code == 200
        kw = stripe.of("checkout.create")[0]
        assert kw["mode"] == "payment"
        assert "subscription_data" not in kw
        assert r.json()["status"] == PurchaseStatus.PENDING

    def test_an_item_that_is_not_seller_assisted_cannot_be_sold(
            self, client, db_session, world, stripe):
        item = _catalog_item(db_session, world["platform"],
                             seller_assisted=False)
        r = client.post(
            "/sales/catalog/customers/%s/sell" % world["customer"].id,
            json={"item": item.key},
            headers=_headers(db_session, world["rep"]))
        assert r.status_code == 400

    def test_another_brands_item_cannot_be_sold(self, client, db_session,
                                                world, stripe):
        other_platform = _platform(db_session, name="Other Brand")
        foreign_item = _catalog_item(db_session, other_platform)

        r = client.post(
            "/sales/catalog/customers/%s/sell" % world["customer"].id,
            json={"item": foreign_item.key},
            headers=_headers(db_session, world["rep"]))
        assert r.status_code == 400

    def test_the_rep_is_recorded_as_the_seller(self, client, db_session,
                                               world, stripe):
        """How a commission question is answered later, rather than inferred
        from timestamps."""
        item = _catalog_item(db_session, world["platform"])
        r = client.post(
            "/sales/catalog/customers/%s/sell" % world["customer"].id,
            json={"item": item.key},
            headers=_headers(db_session, world["rep"]))

        row = (db_session.query(CatalogPurchase)
               .filter(CatalogPurchase.id == r.json()["id"]).first())
        assert row.sold_by_user_id == world["rep"].id

    def test_no_stripe_identifier_comes_back_from_a_sale(self, client,
                                                         db_session, world,
                                                         stripe):
        item = _catalog_item(db_session, world["platform"])
        r = client.post(
            "/sales/catalog/customers/%s/sell" % world["customer"].id,
            json={"item": item.key},
            headers=_headers(db_session, world["rep"]))

        for prefix in ("si_", "sub_", "price_", "cus_"):
            assert prefix not in r.text, "leaked %s: %s" % (prefix, r.text)


class TestResendingIsTheSameLink:
    """Creating a second session for one obligation gives the customer two
    live links, and paying both is a real thing customers do."""

    def _pending(self, client, db_session, world):
        item = _catalog_item(db_session, world["platform"], key="migration",
                             name="Data Migration",
                             kind=CatalogItemKind.ONE_TIME,
                             billing_interval=None, amount_cents=75000)
        r = client.post(
            "/sales/catalog/customers/%s/sell" % world["customer"].id,
            json={"item": item.key},
            headers=_headers(db_session, world["rep"]))
        return r.json()

    def test_it_returns_the_stored_link(self, client, db_session, world,
                                        stripe):
        sold = self._pending(client, db_session, world)
        before = len(stripe.of("checkout.create"))

        r = client.post("/sales/catalog/purchases/%s/resend" % sold["id"],
                        headers=_headers(db_session, world["rep"]))

        assert r.status_code == 200
        assert r.json()["checkout_url"] == sold["checkout_url"]
        assert len(stripe.of("checkout.create")) == before

    def test_a_paid_purchase_has_nothing_to_pay(self, client, db_session,
                                                world, stripe):
        sold = self._pending(client, db_session, world)
        row = (db_session.query(CatalogPurchase)
               .filter(CatalogPurchase.id == sold["id"]).first())
        row.status = PurchaseStatus.PAID
        db_session.commit()

        r = client.post("/sales/catalog/purchases/%s/resend" % sold["id"],
                        headers=_headers(db_session, world["rep"]))
        assert r.status_code == 409

    def test_an_addon_has_no_link_to_resend(self, client, db_session, world,
                                            stripe):
        """It was never collected through a checkout."""
        item = _catalog_item(db_session, world["platform"])
        sold = client.post(
            "/sales/catalog/customers/%s/sell" % world["customer"].id,
            json={"item": item.key},
            headers=_headers(db_session, world["rep"])).json()

        r = client.post("/sales/catalog/purchases/%s/resend" % sold["id"],
                        headers=_headers(db_session, world["rep"]))
        assert r.status_code == 409

    def test_a_missing_purchase_is_a_404(self, client, db_session, world):
        r = client.post("/sales/catalog/purchases/nope/resend",
                        headers=_headers(db_session, world["rep"]))
        assert r.status_code == 404
