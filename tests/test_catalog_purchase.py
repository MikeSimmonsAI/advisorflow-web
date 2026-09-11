"""BUYING FROM THE CATALOGUE — and the obligations staying separate.

The rules under test are the ones that cost money when they break:

  * a recurring add-on joins the EXISTING subscription and never becomes a
    second one (this codebase has already paid for that mistake once);
  * a one-time service is a payment-mode checkout that starts no subscription
    and never marks the setup fee paid;
  * removing an add-on removes ONE ITEM, never the subscription;
  * an unpaid checkout grants nothing — not entitlements, not revenue;
  * a fixed item cannot be repriced, and a quoted one cannot be sold without
    an amount and the authority to set it.
"""
import pytest

from app.models.billing_models import BillingInterval
from app.models.catalog_models import (BrandCatalogItem, CatalogItemKind,
                                       CatalogPricingMode)
from app.models.purchase_models import (CatalogPurchase, PricingSource,
                                        PurchaseStatus)
from app.services import catalog_purchase


def _item(**kw) -> BrandCatalogItem:
    base = dict(
        id="item_1", platform_id="plat_1", key="extra_users",
        name="Additional Users", kind=CatalogItemKind.RECURRING_ADDON,
        pricing_mode=CatalogPricingMode.FIXED, amount_cents=2500,
        currency="usd", billing_interval=BillingInterval.MONTH,
        stripe_product_id=None, stripe_price_id=None,
        self_service=True, seller_assisted=True, is_active=True,
        customer_description=None, internal_description=None,
        entitlement_key=None, entitlement_value=None,
        category=None, sort_order=0,
    )
    base.update(kw)
    return BrandCatalogItem(**base)


def _one_time(**kw) -> BrandCatalogItem:
    return _item(key="migration", name="Data Migration",
                 kind=CatalogItemKind.ONE_TIME, billing_interval=None,
                 amount_cents=75000, **kw)


class _Org:
    id = "org_1"
    platform_id = "plat_1"
    name = "ZZ Test"
    stripe_customer_id = "cus_1"
    stripe_subscription_id = "sub_1"
    billing_status = "active"


class _FakeStripe:
    def __init__(self):
        self.calls = []
        outer = self

        class SubscriptionItem:
            @staticmethod
            def create(**kw):
                outer.calls.append(("subscription_item.create", kw))
                return {"id": "si_new", "price": {"id": "price_x"}}

            @staticmethod
            def delete(item_id, **kw):
                outer.calls.append(("subscription_item.delete", item_id))
                return {"deleted": True}

        class Subscription:
            @staticmethod
            def delete(*a, **kw):      # pragma: no cover - must never be hit
                outer.calls.append(("subscription.delete", a))
                raise AssertionError(
                    "removing an add-on must never delete the subscription")

        class _Session:
            @staticmethod
            def create(**kw):
                outer.calls.append(("checkout.create", kw))
                return {"id": "cs_new", "url": "https://pay.example/cs_new"}

        class checkout:
            Session = _Session

        self.SubscriptionItem = SubscriptionItem
        self.Subscription = Subscription
        self.checkout = checkout

    def of(self, name):
        return [kw for call, kw in self.calls if call == name]


@pytest.fixture
def stripe(monkeypatch):
    fake = _FakeStripe()
    monkeypatch.setattr(catalog_purchase, "_stripe", lambda: fake)
    # WHERE THE CUSTOMER COMES BACK TO, now resolved by `stripe_return` rather
    # than by a base string this module concatenated itself. The fake mirrors
    # exactly what the real resolver produces — a branded origin, an
    # allowlisted path, and `part` carried on BOTH the success and the cancel
    # so the confirmation can say what was bought either way.
    monkeypatch.setattr(
        catalog_purchase, "_return_targets",
        lambda db, org, part: {
            "success_url": "https://app.example/billing?part=%s&success=1" % part,
            "cancel_url": "https://app.example/billing?canceled=1&part=%s" % part,
        })
    import app.routers.billing_router as br
    monkeypatch.setattr(br, "_get_or_create_customer", lambda org, db: "cus_1")
    monkeypatch.setattr(br, "_brand_display_name", lambda db, org: "EvoSys Pro")
    return fake


class TestARecurringAddonJoinsTheExistingSubscription:

    def test_it_creates_a_subscription_item_not_a_subscription(
            self, db_session, stripe):
        org = _Org()
        p = catalog_purchase.add_recurring_addon(db_session, org, _item())

        assert stripe.of("subscription_item.create"), (
            "an add-on must attach to the existing subscription")
        assert not stripe.of("checkout.create"), (
            "an add-on must not start a checkout — that is how a second "
            "subscription gets created")
        assert stripe.of("subscription_item.create")[0]["subscription"] == "sub_1"
        assert p.status == PurchaseStatus.ACTIVE
        assert p.stripe_subscription_item_id == "si_new"

    def test_it_is_refused_when_there_is_no_subscription(self, db_session, stripe):
        org = _Org()
        org.stripe_subscription_id = None

        with pytest.raises(catalog_purchase.PurchaseRefused) as exc:
            catalog_purchase.add_recurring_addon(db_session, org, _item())

        assert "second subscription" in str(exc.value).lower()
        assert not stripe.calls, "nothing may reach Stripe on a refusal"

    def test_it_is_refused_when_the_subscription_is_not_live(
            self, db_session, stripe):
        org = _Org()
        org.billing_status = "canceled"
        with pytest.raises(catalog_purchase.PurchaseRefused):
            catalog_purchase.add_recurring_addon(db_session, org, _item())

    def test_the_same_addon_cannot_be_added_twice(self, db_session, stripe):
        """Two rows both claiming to be the live one, and two charges for one
        thing."""
        org = _Org()
        catalog_purchase.add_recurring_addon(db_session, org, _item())

        with pytest.raises(catalog_purchase.PurchaseRefused) as exc:
            catalog_purchase.add_recurring_addon(db_session, org, _item())
        assert "already has" in str(exc.value).lower()

    def test_a_one_time_item_cannot_be_added_as_an_addon(self, db_session, stripe):
        with pytest.raises(catalog_purchase.PurchaseRefused):
            catalog_purchase.add_recurring_addon(db_session, _Org(), _one_time())

    def test_the_recurring_price_carries_an_interval(self, db_session, stripe):
        catalog_purchase.add_recurring_addon(db_session, _Org(), _item())
        kw = stripe.of("subscription_item.create")[0]
        assert kw["price_data"]["recurring"]["interval"] == "month"

    def test_a_mapped_price_is_used_when_the_amount_matches(
            self, db_session, stripe):
        """An ad-hoc price per purchase is the anonymous-Price problem the tier
        catalogue already fixed."""
        catalog_purchase.add_recurring_addon(
            db_session, _Org(), _item(stripe_price_id="price_mapped"))
        kw = stripe.of("subscription_item.create")[0]
        assert kw.get("price") == "price_mapped"
        assert "price_data" not in kw


class TestRemovingAnAddonRemovesOneItem:

    def test_it_deletes_the_item_and_not_the_subscription(
            self, db_session, stripe):
        """The fake raises if Subscription.delete is ever called."""
        org = _Org()
        p = catalog_purchase.add_recurring_addon(db_session, org, _item())

        out = catalog_purchase.remove_recurring_addon(db_session, org, p)

        assert stripe.of("subscription_item.delete") == ["si_new"]
        assert out.status == PurchaseStatus.CANCELED
        assert out.canceled_at is not None

    def test_another_customers_purchase_cannot_be_removed(
            self, db_session, stripe):
        org = _Org()
        p = catalog_purchase.add_recurring_addon(db_session, org, _item())

        other = _Org()
        other.id = "org_other"
        with pytest.raises(catalog_purchase.PurchaseRefused) as exc:
            catalog_purchase.remove_recurring_addon(db_session, other, p)
        assert "another customer" in str(exc.value).lower()

    def test_a_stripe_failure_still_records_the_removal(self, db_session,
                                                        stripe, monkeypatch):
        """An item Stripe no longer has is already gone. Refusing would leave a
        row claiming the customer pays for something they do not."""
        org = _Org()
        p = catalog_purchase.add_recurring_addon(db_session, org, _item())

        def _boom(item_id, **kw):
            raise RuntimeError("no such item")
        monkeypatch.setattr(stripe.SubscriptionItem, "delete", _boom)

        out = catalog_purchase.remove_recurring_addon(db_session, org, p)
        assert out.status == PurchaseStatus.CANCELED


class TestAOneTimeServiceIsAPaymentAndNotASubscription:

    def test_the_session_is_payment_mode(self, db_session, stripe):
        org = _Org()
        catalog_purchase.start_one_time_checkout(db_session, org, _one_time())

        kw = stripe.of("checkout.create")[0]
        assert kw["mode"] == "payment"

    def test_it_carries_no_subscription_data_at_all(self, db_session, stripe):
        """Not `subscription_data: {}` — absent. A present key is one edit away
        from a value, and a value turns one charge into a subscription."""
        org = _Org()
        catalog_purchase.start_one_time_checkout(db_session, org, _one_time())

        kw = stripe.of("checkout.create")[0]
        assert "subscription_data" not in kw

    def test_the_line_has_no_recurring_shape(self, db_session, stripe):
        org = _Org()
        catalog_purchase.start_one_time_checkout(db_session, org, _one_time())

        line = stripe.of("checkout.create")[0]["line_items"][0]
        assert "recurring" not in (line.get("price_data") or {})

    def test_it_creates_no_subscription_item(self, db_session, stripe):
        org = _Org()
        catalog_purchase.start_one_time_checkout(db_session, org, _one_time())
        assert stripe.of("subscription_item.create") == []

    def test_the_purpose_distinguishes_it_from_the_setup_fee(
            self, db_session, stripe):
        """The setup fee is also a payment-mode session. Without `purpose` the
        webhook would have two identical shapes and no way to tell which
        obligation the money settled."""
        org = _Org()
        catalog_purchase.start_one_time_checkout(db_session, org, _one_time())

        meta = stripe.of("checkout.create")[0]["metadata"]
        assert meta["purpose"] == "catalog_purchase"

    def test_the_session_names_the_purchase_row(self, db_session, stripe):
        """Created before the session, so the payment has somewhere to land."""
        org = _Org()
        p = catalog_purchase.start_one_time_checkout(db_session, org,
                                                     _one_time())

        meta = stripe.of("checkout.create")[0]["metadata"]
        assert meta["catalog_purchase_id"] == p.id

    def test_it_is_pending_until_the_webhook_says_otherwise(
            self, db_session, stripe):
        """A customer who opened a link has not paid."""
        org = _Org()
        p = catalog_purchase.start_one_time_checkout(db_session, org,
                                                     _one_time())
        assert p.status == PurchaseStatus.PENDING
        assert p.paid_at is None

    def test_the_link_is_persisted(self, db_session, stripe):
        """A seller must be able to resend it without browser history."""
        org = _Org()
        p = catalog_purchase.start_one_time_checkout(db_session, org,
                                                     _one_time())
        assert p.checkout_url == "https://pay.example/cs_new"

    def test_a_recurring_item_is_refused_here(self, db_session, stripe):
        org = _Org()
        with pytest.raises(catalog_purchase.PurchaseRefused):
            catalog_purchase.start_one_time_checkout(db_session, org, _item())

    def test_a_failed_checkout_leaves_no_orphan_row(self, db_session, stripe,
                                                    monkeypatch):
        """The row is flushed before the session exists. If Stripe refuses, a
        row claiming a purchase nobody can pay for must not survive."""
        org = _Org()

        def _boom(**kw):
            raise RuntimeError("stripe is unhappy")
        monkeypatch.setattr(stripe.checkout.Session, "create", _boom)

        before = db_session.query(CatalogPurchase).count()
        with pytest.raises(catalog_purchase.PurchaseRefused):
            catalog_purchase.start_one_time_checkout(db_session, org,
                                                     _one_time())
        assert db_session.query(CatalogPurchase).count() == before


class TestTheWebhookIsTheOnlyThingThatBanksTheMoney:

    def _pending(self, db_session, stripe, org):
        return catalog_purchase.start_one_time_checkout(db_session, org,
                                                        _one_time())

    def test_a_verified_session_marks_it_paid(self, db_session, stripe):
        org = _Org()
        p = self._pending(db_session, stripe, org)

        catalog_purchase.mark_one_time_paid(db_session, org, {
            "metadata": {"catalog_purchase_id": p.id},
            "payment_intent": "pi_1"})
        db_session.commit()
        db_session.refresh(p)

        assert p.status == PurchaseStatus.PAID
        assert p.paid_at is not None
        assert p.stripe_payment_intent_id == "pi_1"

    def test_replaying_the_same_event_banks_nothing_twice(self, db_session,
                                                          stripe):
        """Stripe retries. So does a human clicking resend in the dashboard."""
        org = _Org()
        p = self._pending(db_session, stripe, org)
        obj = {"metadata": {"catalog_purchase_id": p.id},
               "payment_intent": "pi_1"}

        catalog_purchase.mark_one_time_paid(db_session, org, obj)
        db_session.commit()
        first_paid_at = p.paid_at

        catalog_purchase.mark_one_time_paid(db_session, org, obj)
        db_session.commit()
        db_session.refresh(p)

        assert p.paid_at == first_paid_at
        assert p.status == PurchaseStatus.PAID

    def test_a_session_for_another_org_is_ignored(self, db_session, stripe):
        """Tenant isolation at the money layer: a webhook that named someone
        else's purchase must not settle it."""
        org = _Org()
        p = self._pending(db_session, stripe, org)

        other = _Org()
        other.id = "org_other"
        out = catalog_purchase.mark_one_time_paid(db_session, other, {
            "metadata": {"catalog_purchase_id": p.id}})

        assert out is None
        db_session.refresh(p)
        assert p.status == PurchaseStatus.PENDING

    def test_a_session_naming_no_purchase_is_ignored(self, db_session, stripe):
        org = _Org()
        assert catalog_purchase.mark_one_time_paid(
            db_session, org, {"metadata": {}}) is None

    def test_a_session_naming_a_missing_purchase_is_ignored(self, db_session,
                                                            stripe):
        org = _Org()
        assert catalog_purchase.mark_one_time_paid(
            db_session, org,
            {"metadata": {"catalog_purchase_id": "nope"}}) is None

    def test_paying_for_a_service_touches_no_subscription_state(
            self, db_session, stripe):
        """A customer who bought a migration bought exactly one thing."""
        org = _Org()
        p = self._pending(db_session, stripe, org)
        before = (org.stripe_subscription_id, org.billing_status)

        catalog_purchase.mark_one_time_paid(db_session, org, {
            "metadata": {"catalog_purchase_id": p.id}})

        assert (org.stripe_subscription_id, org.billing_status) == before
        assert stripe.of("subscription_item.create") == []


class TestTheServerDecidesThePrice:

    def test_a_fixed_item_takes_the_catalogue_amount(self):
        assert catalog_purchase.resolve_amount(_item()) == 2500

    def test_a_fixed_item_cannot_be_repriced_on_a_deal(self):
        """A figure against a fixed item is an attempt to reprice it."""
        with pytest.raises(catalog_purchase.PurchaseRefused) as exc:
            catalog_purchase.resolve_amount(_item(), 100)
        assert "cannot be repriced" in str(exc.value)

    def test_passing_the_same_amount_is_not_a_repricing(self):
        assert catalog_purchase.resolve_amount(_item(), 2500) == 2500

    def test_a_fixed_item_with_no_price_cannot_be_sold(self):
        with pytest.raises(catalog_purchase.PurchaseRefused) as exc:
            catalog_purchase.resolve_amount(_item(amount_cents=None))
        assert "not free" in str(exc.value)

    def test_a_quoted_item_needs_a_figure(self):
        q = _item(pricing_mode=CatalogPricingMode.QUOTED, amount_cents=None)
        with pytest.raises(catalog_purchase.PurchaseRefused) as exc:
            catalog_purchase.resolve_amount(q)
        assert "needs a price" in str(exc.value)

    def test_a_quoted_item_takes_the_figure_it_is_given(self):
        q = _item(pricing_mode=CatalogPricingMode.QUOTED, amount_cents=None)
        assert catalog_purchase.resolve_amount(q, 123400) == 123400

    def test_a_negative_price_is_refused(self):
        q = _item(pricing_mode=CatalogPricingMode.QUOTED, amount_cents=None)
        with pytest.raises(catalog_purchase.PurchaseRefused):
            catalog_purchase.resolve_amount(q, -1)

    def test_the_recorded_source_says_who_set_the_price(self, db_session,
                                                        stripe):
        org = _Org()
        p = catalog_purchase.add_recurring_addon(db_session, org, _item())
        assert p.pricing_source == PricingSource.CATALOGUE

    def test_a_quoted_sale_is_recorded_as_quoted(self, db_session, stripe):
        org = _Org()
        q = _item(key="custom_dev", pricing_mode=CatalogPricingMode.QUOTED,
                  amount_cents=None)
        p = catalog_purchase.add_recurring_addon(db_session, org, q,
                                                 amount_cents=50000)
        assert p.pricing_source == PricingSource.QUOTED
        assert p.amount_cents == 50000

    def test_a_mapped_price_is_used_when_it_matches(self, db_session, stripe):
        org = _Org()
        catalog_purchase.add_recurring_addon(
            db_session, org, _item(stripe_price_id="price_mapped"))
        kw = stripe.of("subscription_item.create")[0]
        assert kw.get("price") == "price_mapped"
        assert "price_data" not in kw

    def test_a_quoted_amount_never_rides_a_mapped_price(self, db_session,
                                                        stripe):
        """The mapped Price charges the catalogue amount. Using it for a
        negotiated figure would bill a number nobody agreed to."""
        org = _Org()
        q = _item(pricing_mode=CatalogPricingMode.QUOTED, amount_cents=None,
                  stripe_price_id="price_mapped")
        catalog_purchase.add_recurring_addon(db_session, org, q,
                                             amount_cents=777)
        kw = stripe.of("subscription_item.create")[0]
        assert "price" not in kw
        assert kw["price_data"]["unit_amount"] == 777

    def test_the_amount_is_snapshotted_not_referenced(self, db_session,
                                                      stripe):
        """A brand raising a price next quarter must not restate what a
        customer already agreed to."""
        org = _Org()
        item = _item()
        p = catalog_purchase.add_recurring_addon(db_session, org, item)
        item.amount_cents = 9900
        db_session.refresh(p)
        assert p.amount_cents == 2500


class TestOnlyPaidCapacityCounts:
    """Entitlements come from what the customer IS paying for — not from what
    they started a checkout for. Granting capacity on an unpaid link is how a
    platform gives away the thing it is trying to sell."""

    def _persisted(self, db_session, **kw):
        item = _item(**kw)
        item.id = None
        db_session.add(item)
        db_session.flush()
        return item

    def _purchase(self, db_session, item, status, quantity=1):
        p = CatalogPurchase(
            organization_id="org_1", platform_id="plat_1",
            catalog_item_id=item.id, item_key=item.key, item_name=item.name,
            kind=item.kind, amount_cents=item.amount_cents or 0,
            currency="usd", quantity=quantity, status=status,
            pricing_source=PricingSource.CATALOGUE)
        db_session.add(p)
        db_session.flush()
        return p

    def test_an_active_addon_grants_its_capacity(self, db_session):
        org = _Org()
        item = self._persisted(db_session, entitlement_key="max_users",
                               entitlement_value=5)
        self._purchase(db_session, item, PurchaseStatus.ACTIVE)

        assert catalog_purchase.entitlement_totals(
            db_session, org) == {"max_users": 5}

    def test_quantity_multiplies_the_grant(self, db_session):
        org = _Org()
        item = self._persisted(db_session, entitlement_key="max_users",
                               entitlement_value=5)
        self._purchase(db_session, item, PurchaseStatus.ACTIVE, quantity=3)

        assert catalog_purchase.entitlement_totals(
            db_session, org) == {"max_users": 15}

    def test_a_pending_checkout_grants_nothing(self, db_session):
        org = _Org()
        item = self._persisted(db_session, entitlement_key="max_users",
                               entitlement_value=5)
        self._purchase(db_session, item, PurchaseStatus.PENDING)

        assert catalog_purchase.entitlement_totals(db_session, org) == {}

    def test_a_canceled_addon_grants_nothing(self, db_session):
        org = _Org()
        item = self._persisted(db_session, entitlement_key="max_users",
                               entitlement_value=5)
        self._purchase(db_session, item, PurchaseStatus.CANCELED)

        assert catalog_purchase.entitlement_totals(db_session, org) == {}

    def test_a_paid_one_time_purchase_can_grant_capacity(self, db_session):
        org = _Org()
        item = self._persisted(db_session, key="lead_pack",
                               kind=CatalogItemKind.ONE_TIME,
                               billing_interval=None,
                               entitlement_key="max_leads",
                               entitlement_value=1000)
        self._purchase(db_session, item, PurchaseStatus.PAID)

        assert catalog_purchase.entitlement_totals(
            db_session, org) == {"max_leads": 1000}

    def test_two_items_granting_the_same_key_add_up(self, db_session):
        org = _Org()
        a = self._persisted(db_session, key="pack_a",
                            entitlement_key="max_users", entitlement_value=5)
        b = self._persisted(db_session, key="pack_b",
                            entitlement_key="max_users", entitlement_value=2)
        self._purchase(db_session, a, PurchaseStatus.ACTIVE)
        self._purchase(db_session, b, PurchaseStatus.ACTIVE)

        assert catalog_purchase.entitlement_totals(
            db_session, org) == {"max_users": 7}

    def test_an_item_that_grants_nothing_is_not_a_zero_entry(self, db_session):
        """Training, a migration, priority support: real purchases that change
        what is DELIVERED without changing what the software permits."""
        org = _Org()
        item = self._persisted(db_session, key="training")
        self._purchase(db_session, item, PurchaseStatus.ACTIVE)

        assert catalog_purchase.entitlement_totals(db_session, org) == {}

    def test_another_customers_purchase_grants_nothing_here(self, db_session):
        """Tenant isolation on the entitlement surface."""
        org = _Org()
        item = self._persisted(db_session, entitlement_key="max_users",
                               entitlement_value=5)
        p = self._purchase(db_session, item, PurchaseStatus.ACTIVE)
        p.organization_id = "org_other"
        db_session.flush()

        assert catalog_purchase.entitlement_totals(db_session, org) == {}


class TestWhatTheCustomerIsShown:

    def test_no_stripe_identifier_reaches_the_customer(self, db_session,
                                                       stripe):
        """Raw processor ids are internal plumbing. A customer screen that
        prints them teaches people to read them, and then someone pastes one
        into a support ticket."""
        org = _Org()
        p = catalog_purchase.add_recurring_addon(db_session, org, _item())
        out = catalog_purchase.purchase_out(p)

        blob = repr(out)
        for prefix in ("si_", "sub_", "price_", "cus_", "pi_"):
            assert prefix not in blob, "leaked %s: %r" % (prefix, out)

    def test_the_checkout_url_does_reach_the_customer(self, db_session,
                                                      stripe):
        """It is the thing they have to open. A link nobody can reach is not
        a link."""
        org = _Org()
        p = catalog_purchase.start_one_time_checkout(db_session, org,
                                                     _one_time())
        assert catalog_purchase.purchase_out(p)["checkout_url"]

    def test_the_line_total_is_computed_once_by_the_server(self, db_session,
                                                           stripe):
        """So two screens cannot disagree about what 3 x $25/mo comes to."""
        org = _Org()
        p = catalog_purchase.add_recurring_addon(db_session, org, _item(),
                                                 quantity=3)
        assert catalog_purchase.purchase_out(p)["total_cents"] == 7500

    def test_the_server_states_whether_it_can_be_removed(self, db_session,
                                                         stripe):
        org = _Org()
        active = catalog_purchase.add_recurring_addon(db_session, org, _item())
        pending = catalog_purchase.start_one_time_checkout(db_session, org,
                                                           _one_time())

        assert catalog_purchase.purchase_out(active)["removable"] is True
        assert catalog_purchase.purchase_out(pending)["removable"] is False

    def test_purchases_for_is_scoped_to_the_customer(self, db_session, stripe):
        org = _Org()
        catalog_purchase.add_recurring_addon(db_session, org, _item())

        other = _Org()
        other.id = "org_other"
        assert catalog_purchase.purchases_for(db_session, other) == []
