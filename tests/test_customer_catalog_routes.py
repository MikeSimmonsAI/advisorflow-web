"""THE CUSTOMER'S OWN CATALOGUE — what the browser may ask for.

The engine is tested in `test_catalog_purchase.py`. What matters here is the
same thing that mattered for the plan catalogue:

  * THE BROWSER NAMES AN ITEM, NEVER A PRICE. A customer naming their own
    amount is the tampering this codebase already closed on plans; an add-on
    is no different, and the request model is the proof.
  * ONLY SELF-SERVICE ITEMS ARE OFFERED. A brand may sell something through a
    person without putting it behind a button.
  * WHAT THEY ALREADY HAVE IS NOT OFFERED AGAIN. Buying a second copy bills
    twice for one thing.
  * TENANT SCOPE IS IN THE QUERY. Another customer's purchase id resolves to
    nothing, not to a 403 that confirms it exists.

NOTHING HERE REACHES STRIPE.
"""
import itertools

import pytest
import stripe

from app.models.billing_models import (BillingInterval, BrandBillingPlan,
                                       SubscriptionStatus)
from app.models.catalog_models import (BrandCatalogItem, CatalogItemKind,
                                       CatalogPricingMode)
from app.models.models import Organization, Platform, User
from app.models.purchase_models import (CatalogPurchase, PricingSource,
                                        PurchaseStatus)
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


@pytest.fixture(autouse=True)
def stripe_env(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_FAKE_not_a_real_key")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_FAKE_not_a_real_secret")


@pytest.fixture(autouse=True)
def no_real_stripe_calls(monkeypatch):
    """The suite must never reach api.stripe.com."""
    def _refuse(*a, **kw):
        raise RuntimeError("A test tried to make a real Stripe request.")
    monkeypatch.setattr(stripe.Customer, "create", _refuse)


@pytest.fixture
def fake_stripe(monkeypatch):
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


def _world(db, *, subscribed=True):
    n = next(_SEQ)
    platform = Platform(name="EvoSys Pro", slug="plat-cc-%d" % n)
    db.add(platform)
    db.commit()

    plan = BrandBillingPlan(platform_id=platform.id, key="growth-%d" % n,
                            name="Growth", monthly_cents=99700, currency="usd",
                            is_purchasable=True, is_active=True)
    db.add(plan)
    db.commit()

    kw = {}
    if subscribed:
        kw = {"stripe_subscription_id": "sub_cc_%d" % n,
              "billing_status": SubscriptionStatus.ACTIVE}
    org = Organization(name="ZZ Cust %d" % n, slug="zz-cc-%d" % n,
                       platform_id=platform.id, plan=plan.key,
                       billing_plan_key=plan.key,
                       stripe_customer_id="cus_cc_%d" % n, **kw)
    db.add(org)
    db.commit()
    return {"platform": platform, "plan": plan, "org": org}


def _admin_headers(db, org):
    u = User(organization_id=org.id, email="admin%d@evosyspro.live" % next(_SEQ),
             password_hash=hash_password("AdminPass123!"), full_name="Admin",
             role="org_admin", must_change_password=False)
    db.add(u)
    db.commit()
    return {"Authorization": "Bearer " + create_access_token(u, db)}


def _item(db, platform, **kw):
    base = dict(
        platform_id=platform.id, key="extra_users-%d" % next(_SEQ),
        name="Additional Users", kind=CatalogItemKind.RECURRING_ADDON,
        pricing_mode=CatalogPricingMode.FIXED, amount_cents=2500,
        currency="usd", billing_interval=BillingInterval.MONTH,
        self_service=True, seller_assisted=True, is_active=True, sort_order=0)
    base.update(kw)
    item = BrandCatalogItem(**base)
    db.add(item)
    db.commit()
    return item


class TestTheBrowserNamesAnItemNeverAPrice:

    def test_the_request_model_has_no_amount_field(self):
        """The structural guarantee. A field that does not exist cannot be
        sent, cannot be trusted by mistake, and cannot be added without this
        test failing and somebody having to think about it."""
        from app.routers.billing_router import PurchaseRequest
        assert set(PurchaseRequest.model_fields) == {"item", "quantity"}

    def test_an_amount_in_the_body_is_ignored(self, client, db_session,
                                              fake_stripe):
        w = _world(db_session)
        item = _item(db_session, w["platform"], amount_cents=2500)

        r = client.post("/billing/catalog/purchase",
                        json={"item": item.key, "quantity": 1,
                              "amount_cents": 1, "price": "price_cheap"},
                        headers=_admin_headers(db_session, w["org"]))

        assert r.status_code == 200
        assert r.json()["amount_cents"] == 2500

    def test_a_quoted_item_cannot_be_bought_directly(self, client, db_session,
                                                     fake_stripe):
        """It has no catalogue price by design, and the customer is not the
        one who gets to supply the missing figure."""
        w = _world(db_session)
        item = _item(db_session, w["platform"],
                     pricing_mode=CatalogPricingMode.QUOTED,
                     amount_cents=None)

        r = client.post("/billing/catalog/purchase", json={"item": item.key},
                        headers=_admin_headers(db_session, w["org"]))
        assert r.status_code == 400
        assert "account manager" in r.json()["detail"]


class TestWhatIsOffered:

    def test_a_self_service_item_is_offered(self, client, db_session):
        w = _world(db_session)
        item = _item(db_session, w["platform"])
        r = client.get("/billing/catalog",
                       headers=_admin_headers(db_session, w["org"]))

        assert r.status_code == 200
        assert item.key in [i["key"] for i in r.json()["available"]]

    def test_a_seller_only_item_is_not_offered(self, client, db_session):
        w = _world(db_session)
        item = _item(db_session, w["platform"], self_service=False)
        r = client.get("/billing/catalog",
                       headers=_admin_headers(db_session, w["org"]))
        assert item.key not in [i["key"] for i in r.json()["available"]]

    def test_another_brands_item_is_not_offered(self, client, db_session):
        """One brand's customer must not discover another brand's catalogue."""
        w = _world(db_session)
        other = _world(db_session)
        foreign = _item(db_session, other["platform"])

        r = client.get("/billing/catalog",
                       headers=_admin_headers(db_session, w["org"]))
        assert foreign.key not in [i["key"] for i in r.json()["available"]]

    def test_another_brands_item_cannot_be_bought(self, client, db_session,
                                                  fake_stripe):
        w = _world(db_session)
        other = _world(db_session)
        foreign = _item(db_session, other["platform"])

        r = client.post("/billing/catalog/purchase",
                        json={"item": foreign.key},
                        headers=_admin_headers(db_session, w["org"]))
        assert r.status_code == 400
        assert fake_stripe.of("subscription_item.create") == []

    def test_what_they_hold_is_not_offered_again(self, client, db_session,
                                                 fake_stripe):
        w = _world(db_session)
        item = _item(db_session, w["platform"])
        h = _admin_headers(db_session, w["org"])
        client.post("/billing/catalog/purchase", json={"item": item.key},
                    headers=h)

        body = client.get("/billing/catalog", headers=h).json()
        assert item.key not in [i["key"] for i in body["available"]]
        assert item.key in [p["item_key"] for p in body["mine"]]

    def test_the_server_says_whether_addons_can_attach(self, client,
                                                       db_session):
        bare = _world(db_session, subscribed=False)
        r = client.get("/billing/catalog",
                       headers=_admin_headers(db_session, bare["org"]))
        assert r.json()["can_buy_addons"] is False

    def test_no_stripe_identifier_reaches_the_customer_screen(
            self, client, db_session):
        w = _world(db_session)
        _item(db_session, w["platform"], stripe_product_id="prod_secret",
              stripe_price_id="price_secret")

        r = client.get("/billing/catalog",
                       headers=_admin_headers(db_session, w["org"]))
        assert "prod_secret" not in r.text
        assert "price_secret" not in r.text


class TestBuyingAndRemoving:

    def test_an_addon_joins_the_existing_subscription(self, client, db_session,
                                                      fake_stripe):
        w = _world(db_session)
        item = _item(db_session, w["platform"])

        r = client.post("/billing/catalog/purchase",
                        json={"item": item.key, "quantity": 2},
                        headers=_admin_headers(db_session, w["org"]))

        assert r.status_code == 200
        kw = fake_stripe.of("subscription_item.create")[0]
        assert kw["subscription"] == w["org"].stripe_subscription_id
        assert kw["quantity"] == 2
        assert fake_stripe.of("checkout.create") == []

    def test_an_addon_without_a_subscription_is_refused(self, client,
                                                        db_session,
                                                        fake_stripe):
        bare = _world(db_session, subscribed=False)
        item = _item(db_session, bare["platform"])

        r = client.post("/billing/catalog/purchase", json={"item": item.key},
                        headers=_admin_headers(db_session, bare["org"]))
        assert r.status_code == 409
        assert fake_stripe.of("subscription_item.create") == []

    def test_a_one_time_service_returns_a_pending_checkout(self, client,
                                                           db_session,
                                                           fake_stripe):
        w = _world(db_session)
        item = _item(db_session, w["platform"], key="migration",
                     name="Data Migration", kind=CatalogItemKind.ONE_TIME,
                     billing_interval=None, amount_cents=75000)

        r = client.post("/billing/catalog/purchase", json={"item": item.key},
                        headers=_admin_headers(db_session, w["org"]))

        assert r.status_code == 200
        body = r.json()
        assert body["status"] == PurchaseStatus.PENDING
        assert body["checkout_url"]
        assert fake_stripe.of("checkout.create")[0]["mode"] == "payment"

    def test_removing_an_addon_removes_one_item(self, client, db_session,
                                                fake_stripe):
        w = _world(db_session)
        item = _item(db_session, w["platform"])
        h = _admin_headers(db_session, w["org"])
        sold = client.post("/billing/catalog/purchase", json={"item": item.key},
                           headers=h).json()

        r = client.post("/billing/catalog/purchase/%s/remove" % sold["id"],
                        headers=h)

        assert r.status_code == 200
        assert r.json()["status"] == PurchaseStatus.CANCELED
        assert len(fake_stripe.of("subscription_item.delete")) == 1

    def test_removing_does_not_end_the_subscription(self, client, db_session,
                                                    fake_stripe):
        """A customer dropping an add-on has not asked to stop being a
        customer."""
        w = _world(db_session)
        item = _item(db_session, w["platform"])
        h = _admin_headers(db_session, w["org"])
        sold = client.post("/billing/catalog/purchase", json={"item": item.key},
                           headers=h).json()

        client.post("/billing/catalog/purchase/%s/remove" % sold["id"],
                    headers=h)
        db_session.refresh(w["org"])

        assert w["org"].stripe_subscription_id is not None
        assert (w["org"].billing_status or "").lower() in SubscriptionStatus.OCCUPIED


class TestTenantScope:

    def test_the_catalogue_needs_a_token(self, client, db_session):
        assert client.get("/billing/catalog").status_code == 401

    def test_buying_needs_a_token(self, client, db_session):
        assert client.post("/billing/catalog/purchase",
                           json={"item": "x"}).status_code == 401

    def test_another_customers_purchase_cannot_be_removed(self, client,
                                                          db_session,
                                                          fake_stripe):
        """Scoped in the QUERY: it resolves to nothing rather than to a 403
        that confirms the id exists."""
        w = _world(db_session)
        item = _item(db_session, w["platform"])
        sold = client.post("/billing/catalog/purchase", json={"item": item.key},
                           headers=_admin_headers(db_session, w["org"])).json()

        other = _world(db_session)
        r = client.post("/billing/catalog/purchase/%s/remove" % sold["id"],
                        headers=_admin_headers(db_session, other["org"]))

        assert r.status_code == 404
        assert len(fake_stripe.of("subscription_item.delete")) == 0

    def test_another_customers_purchases_are_not_listed(self, client,
                                                        db_session,
                                                        fake_stripe):
        w = _world(db_session)
        item = _item(db_session, w["platform"])
        client.post("/billing/catalog/purchase", json={"item": item.key},
                    headers=_admin_headers(db_session, w["org"]))

        other = _world(db_session)
        body = client.get("/billing/catalog",
                          headers=_admin_headers(db_session, other["org"])).json()
        assert body["mine"] == []
