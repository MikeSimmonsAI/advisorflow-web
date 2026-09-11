"""A SALE A SELLER CAN MAKE AND CANNOT UNMAKE IS A MISTAKE WITH NO CORRECTION.

Selling was one-way. A rep could attach an add-on to a customer's subscription
and nothing in the sales workspace could take it off again — the only remove
control lived on the customer's own Billing page, which is no help at all for a
customer whose account has no users yet. That is the exact customer a rep sells
to during onboarding.

And an unpaid checkout could not be taken back at all, by anybody. The link
kept working, so a service quoted in error could still be paid for days later
by a customer who was never told it was withdrawn.

  * an ACTIVE add-on is removed — one subscription ITEM, never the
    subscription;
  * a PENDING checkout is withdrawn AND expired at Stripe, because a row that
    says "withdrawn" beside a link that still takes money is the worst of both
    records;
  * a PAID purchase is refused — money that arrived is a refund conversation
    with its own authority, and flipping the row would hide it;
  * another brand's purchase cannot be touched.
"""
import itertools

import pytest

from app.models.billing_models import BillingInterval, SubscriptionStatus
from app.models.catalog_models import (BrandCatalogItem, CatalogItemKind,
                                       CatalogPricingMode)
from app.models.models import Organization, Platform, User
from app.models.purchase_models import (CatalogPurchase, PricingSource,
                                        PurchaseStatus)
from app.models.sales_models import (BrandSalesOrg, Membership,
                                     ROLE_SALES_MANAGER, SCOPE_BRAND_SALES_ORG)
from app.services import catalog_purchase
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


@pytest.fixture
def stripe(monkeypatch):
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

    class Subscription:
        @staticmethod
        def delete(*a, **kw):          # pragma: no cover - must never be hit
            raise AssertionError(
                "removing an add-on must never delete the subscription")

    class _Session:
        @staticmethod
        def create(**kw):
            calls.append(("checkout.create", kw))
            return {"id": "cs_%d" % len(calls),
                    "url": "https://pay.example/cs_%d" % len(calls)}

        @staticmethod
        def expire(session_id, **kw):
            calls.append(("checkout.expire", session_id))
            return {"id": session_id, "status": "expired"}

    class checkout:
        Session = _Session

    class Fake:
        pass

    fake = Fake()
    fake.SubscriptionItem = SubscriptionItem
    fake.Subscription = Subscription
    fake.checkout = checkout
    fake.calls = calls
    fake.of = lambda name: [kw for c, kw in calls if c == name]

    monkeypatch.setattr(catalog_purchase, "_stripe", lambda: fake)
    monkeypatch.setattr(catalog_purchase, "_brand_base_url",
                        lambda db, org: "https://app.example")
    import app.routers.billing_router as br
    monkeypatch.setattr(br, "_get_or_create_customer", lambda org, db: "cus_1")
    monkeypatch.setattr(br, "_brand_display_name", lambda db, org: "EvoSys Pro")
    return fake


@pytest.fixture
def world(db_session):
    n = next(_SEQ)
    platform = Platform(name="EvoSys Pro", slug="plat-wd-%d" % n)
    db_session.add(platform)
    db_session.commit()

    sales_org = BrandSalesOrg(platform_id=platform.id, name="Sales",
                              slug="bso-wd-%d" % n)
    org = Organization(name="ZZ Withdraw %d" % n, slug="zz-wd-%d" % n,
                       platform_id=platform.id, plan="growth",
                       stripe_customer_id="cus_wd_%d" % n,
                       stripe_subscription_id="sub_wd_%d" % n,
                       billing_status=SubscriptionStatus.ACTIVE)
    db_session.add_all([sales_org, org])
    db_session.commit()

    seller = User(organization_id=None, email="rep%d@evosys.live" % next(_SEQ),
                  password_hash=hash_password("x"), full_name="Rep",
                  role="advisor", must_change_password=False)
    db_session.add(seller)
    db_session.commit()
    db_session.add(Membership(user_id=seller.id,
                              scope_type=SCOPE_BRAND_SALES_ORG,
                              scope_id=sales_org.id, role=ROLE_SALES_MANAGER,
                              is_active=True))
    db_session.commit()

    return {"platform": platform, "sales_org": sales_org, "org": org,
            "seller": seller,
            "headers": {"Authorization": "Bearer "
                        + create_access_token(seller, db_session)}}


def _item(db, world, **kw):
    base = dict(
        platform_id=world["platform"].id, key="item-%d" % next(_SEQ),
        name="Additional Users", kind=CatalogItemKind.RECURRING_ADDON,
        pricing_mode=CatalogPricingMode.FIXED, amount_cents=2500,
        currency="usd", billing_interval=BillingInterval.MONTH,
        self_service=True, seller_assisted=True, is_active=True, sort_order=0)
    base.update(kw)
    item = BrandCatalogItem(**base)
    db.add(item)
    db.commit()
    return item


def _sell(client, world, item, **body):
    return client.post(
        "/sales/catalog/customers/%s/sell" % world["org"].id,
        json={"item": item.key, **body}, headers=world["headers"])


class TestASellerCanUnmakeASale:

    def test_an_addon_can_be_removed(self, client, db_session, world, stripe):
        item = _item(db_session, world)
        sold = _sell(client, world, item).json()

        r = client.post("/sales/catalog/purchases/%s/cancel" % sold["id"],
                        headers=world["headers"])

        assert r.status_code == 200
        assert r.json()["status"] == PurchaseStatus.CANCELED
        assert stripe.of("subscription_item.delete") == ["si_1"]

    def test_removing_an_addon_leaves_the_subscription(self, client,
                                                       db_session, world,
                                                       stripe):
        item = _item(db_session, world)
        sold = _sell(client, world, item).json()

        client.post("/sales/catalog/purchases/%s/cancel" % sold["id"],
                    headers=world["headers"])
        db_session.refresh(world["org"])

        assert world["org"].stripe_subscription_id is not None
        assert world["org"].billing_status == SubscriptionStatus.ACTIVE

    def test_the_same_item_can_then_be_sold_again(self, client, db_session,
                                                  world, stripe):
        """Removing has to actually free the key, or a correction leaves the
        customer unable to buy the thing they meant to buy."""
        item = _item(db_session, world)
        sold = _sell(client, world, item).json()
        client.post("/sales/catalog/purchases/%s/cancel" % sold["id"],
                    headers=world["headers"])

        again = _sell(client, world, item)
        assert again.status_code == 200

    def test_a_pending_checkout_is_withdrawn_and_expired(self, client,
                                                         db_session, world,
                                                         stripe):
        """A row saying "withdrawn" beside a link that still takes money is the
        worst of both records."""
        item = _item(db_session, world, kind=CatalogItemKind.ONE_TIME,
                     billing_interval=None, amount_cents=75000)
        sold = _sell(client, world, item).json()
        assert sold["status"] == PurchaseStatus.PENDING

        r = client.post("/sales/catalog/purchases/%s/cancel" % sold["id"],
                        headers=world["headers"])

        assert r.status_code == 200
        assert r.json()["status"] == PurchaseStatus.CANCELED
        assert r.json()["checkout_url"] is None
        assert stripe.of("checkout.expire"), "the link must stop working"

    def test_a_withdrawn_link_is_no_longer_resendable(self, client, db_session,
                                                      world, stripe):
        item = _item(db_session, world, kind=CatalogItemKind.ONE_TIME,
                     billing_interval=None, amount_cents=75000)
        sold = _sell(client, world, item).json()
        client.post("/sales/catalog/purchases/%s/cancel" % sold["id"],
                    headers=world["headers"])

        r = client.post("/sales/catalog/purchases/%s/resend" % sold["id"],
                        headers=world["headers"])
        assert r.status_code == 409

    def test_an_expiry_stripe_refuses_still_withdraws_locally(
            self, client, db_session, world, stripe, monkeypatch):
        """A session that cannot be expired is usually one that already
        expired. Refusing here would leave the row claiming an obligation."""
        item = _item(db_session, world, kind=CatalogItemKind.ONE_TIME,
                     billing_interval=None, amount_cents=75000)
        sold = _sell(client, world, item).json()

        def _boom(session_id, **kw):
            raise RuntimeError("already expired")
        monkeypatch.setattr(stripe.checkout.Session, "expire", _boom)

        r = client.post("/sales/catalog/purchases/%s/cancel" % sold["id"],
                        headers=world["headers"])
        assert r.status_code == 200
        assert r.json()["status"] == PurchaseStatus.CANCELED


class TestWhatCancellingRefuses:

    def _paid_one_time(self, db_session, world):
        p = CatalogPurchase(
            organization_id=world["org"].id,
            platform_id=world["platform"].id,
            item_key="migration", item_name="Data Migration",
            kind=CatalogItemKind.ONE_TIME, amount_cents=75000, currency="usd",
            quantity=1, status=PurchaseStatus.PAID,
            pricing_source=PricingSource.CATALOGUE)
        db_session.add(p)
        db_session.commit()
        return p

    def test_paid_money_is_a_refund_conversation(self, client, db_session,
                                                 world, stripe):
        """Flipping a paid row to cancelled would hide the money rather than
        settle it."""
        paid = self._paid_one_time(db_session, world)

        r = client.post("/sales/catalog/purchases/%s/cancel" % paid.id,
                        headers=world["headers"])

        assert r.status_code == 409
        db_session.refresh(paid)
        assert paid.status == PurchaseStatus.PAID

    def test_an_already_removed_addon_is_refused(self, client, db_session,
                                                 world, stripe):
        item = _item(db_session, world)
        sold = _sell(client, world, item).json()
        client.post("/sales/catalog/purchases/%s/cancel" % sold["id"],
                    headers=world["headers"])

        again = client.post("/sales/catalog/purchases/%s/cancel" % sold["id"],
                            headers=world["headers"])
        assert again.status_code == 409
        assert len(stripe.of("subscription_item.delete")) == 1

    def test_another_brands_purchase_cannot_be_cancelled(self, client,
                                                         db_session, world,
                                                         stripe):
        """The same brand scope the rest of this router uses: 404, not 403."""
        n = next(_SEQ)
        other_platform = Platform(name="Other", slug="plat-other-wd-%d" % n)
        db_session.add(other_platform)
        db_session.commit()
        other_sales = BrandSalesOrg(platform_id=other_platform.id,
                                    name="Other Sales",
                                    slug="bso-other-wd-%d" % n)
        foreign = Organization(name="Foreign %d" % n, slug="foreign-wd-%d" % n,
                               platform_id=other_platform.id, plan="growth")
        db_session.add_all([other_sales, foreign])
        db_session.commit()

        theirs = CatalogPurchase(
            organization_id=foreign.id, platform_id=other_platform.id,
            item_key="extra_users", item_name="Additional Users",
            kind=CatalogItemKind.RECURRING_ADDON, amount_cents=2500,
            currency="usd", quantity=1, status=PurchaseStatus.ACTIVE,
            pricing_source=PricingSource.CATALOGUE,
            stripe_subscription_item_id="si_theirs")
        db_session.add(theirs)
        db_session.commit()

        r = client.post("/sales/catalog/purchases/%s/cancel" % theirs.id,
                        headers=world["headers"])

        assert r.status_code == 404
        db_session.refresh(theirs)
        assert theirs.status == PurchaseStatus.ACTIVE
        assert stripe.of("subscription_item.delete") == []

    def test_cancelling_needs_a_token(self, client, db_session, world):
        assert client.post(
            "/sales/catalog/purchases/anything/cancel").status_code == 401


class TestTheCustomerCanWithdrawTheirOwnUnpaidCheckout:
    """A payment page nobody meant to leave open is a charge waiting to
    surprise somebody."""

    def _admin_headers(self, db, org):
        u = User(organization_id=org.id,
                 email="admin%d@evosyspro.live" % next(_SEQ),
                 password_hash=hash_password("AdminPass123!"),
                 full_name="Admin", role="org_admin",
                 must_change_password=False)
        db.add(u)
        db.commit()
        return {"Authorization": "Bearer " + create_access_token(u, db)}

    def test_the_customers_own_endpoint_withdraws_it(self, client, db_session,
                                                     world, stripe):
        item = _item(db_session, world, kind=CatalogItemKind.ONE_TIME,
                     billing_interval=None, amount_cents=75000)
        sold = _sell(client, world, item).json()

        r = client.post("/billing/catalog/purchase/%s/remove" % sold["id"],
                        headers=self._admin_headers(db_session, world["org"]))

        assert r.status_code == 200
        assert r.json()["status"] == PurchaseStatus.CANCELED
        assert stripe.of("checkout.expire")

    def test_another_customers_purchase_is_not_found(self, client, db_session,
                                                     world, stripe):
        item = _item(db_session, world, kind=CatalogItemKind.ONE_TIME,
                     billing_interval=None, amount_cents=75000)
        sold = _sell(client, world, item).json()

        n = next(_SEQ)
        other = Organization(name="Other cust %d" % n,
                             slug="other-cust-wd-%d" % n,
                             platform_id=world["platform"].id, plan="growth")
        db_session.add(other)
        db_session.commit()

        r = client.post("/billing/catalog/purchase/%s/remove" % sold["id"],
                        headers=self._admin_headers(db_session, other))
        assert r.status_code == 404
        assert stripe.of("checkout.expire") == []
