"""EACH OBLIGATION KEEPS ITS OWN MONEY.

There are now TWO payment-mode checkout shapes on this platform — the
implementation SETUP FEE and a one-time CATALOGUE purchase — and they arrive
at the same webhook, in the same event type, looking almost identical. The
only thing separating them is `metadata.purpose`.

That is a thin thread to hang money on, which is exactly why it is tested
here rather than trusted:

  * a catalogue purchase MUST NOT mark the setup fee paid;
  * a setup fee MUST NOT be recorded as a catalogue purchase;
  * neither may start a subscription, set a billing status, or stamp a plan;
  * an unpaid session banks nothing — "they finished the form" is not "the
    money arrived";
  * a replayed event banks nothing twice, from the event side OR the money
    side.

Nothing here reaches Stripe. These are the payloads Stripe sends, handed
straight to the handler.
"""
import itertools

import pytest

from app.models.billing_models import BillingPayment
from app.models.catalog_models import (BrandCatalogItem, CatalogItemKind,
                                       CatalogPricingMode)
from app.models.implementation_models import Implementation
from app.models.models import Organization, Platform
from app.models.purchase_models import (CatalogPurchase, PricingSource,
                                        PurchaseStatus)
from app.models.sales_models import BrandSalesOrg, Opportunity
from app.services import billing_webhook

_SEQ = itertools.count(1)


@pytest.fixture
def world(db_session):
    n = next(_SEQ)
    platform = Platform(name="EvoSys Pro", slug="plat-wh-%d" % n)
    db_session.add(platform)
    db_session.commit()

    org = Organization(name="ZZ Webhook %d" % n, slug="zz-wh-%d" % n,
                       platform_id=platform.id, plan="growth",
                       stripe_customer_id="cus_wh_%d" % n,
                       stripe_subscription_id="sub_wh_%d" % n,
                       billing_status="active", billing_plan_key="growth")
    item = BrandCatalogItem(
        platform_id=platform.id, key="migration-%d" % n,
        name="Data Migration", kind=CatalogItemKind.ONE_TIME,
        pricing_mode=CatalogPricingMode.FIXED, amount_cents=75000,
        currency="usd", billing_interval=None, self_service=True,
        seller_assisted=True, is_active=True, sort_order=0)
    sales_org = BrandSalesOrg(platform_id=platform.id, name="EvoSys Sales",
                              slug="bso-wh-%d" % n)
    db_session.add_all([org, item, sales_org])
    db_session.commit()

    # An implementation hangs off a won opportunity. Built here rather than
    # stubbed because the setup-fee half of these tests only means anything
    # against the row the real handler actually writes to.
    opp = Opportunity(brand_sales_org_id=sales_org.id,
                      company_name="ZZ Webhook %d" % n,
                      stage="won", status="won")
    db_session.add(opp)
    db_session.commit()

    impl = Implementation(organization_id=org.id, opportunity_id=opp.id,
                          platform_id=platform.id, status="not_started")
    db_session.add(impl)
    db_session.commit()

    purchase = CatalogPurchase(
        organization_id=org.id, platform_id=platform.id,
        catalog_item_id=item.id, item_key=item.key, item_name=item.name,
        kind=CatalogItemKind.ONE_TIME, amount_cents=75000, currency="usd",
        quantity=1, status=PurchaseStatus.PENDING,
        pricing_source=PricingSource.CATALOGUE,
        stripe_checkout_session_id="cs_wh_%d" % n,
        checkout_url="https://pay.example/cs_wh_%d" % n)
    db_session.add(purchase)
    db_session.commit()

    return {"platform": platform, "org": org, "item": item,
            "impl": impl, "purchase": purchase, "n": n}


def _session(world, *, purpose="catalog_purchase", payment_status="paid",
             payment_intent=None, amount=75000, session_id=None):
    """A `checkout.session.completed` object, trimmed to the fields read."""
    meta = {"org_id": world["org"].id}
    if purpose == "catalog_purchase":
        meta.update({"purpose": "catalog_purchase",
                     "catalog_purchase_id": world["purchase"].id,
                     "catalog_key": world["item"].key})
    elif purpose == "setup":
        meta["part"] = "setup"

    return {
        "id": session_id or ("cs_evt_%d" % next(_SEQ)),
        "object": "checkout.session",
        "mode": "payment",
        "customer": world["org"].stripe_customer_id,
        "payment_status": payment_status,
        "payment_intent": payment_intent,
        "currency": "usd",
        "amount_total": amount,
        "metadata": meta,
    }


def _event(obj, evt_id=None):
    return {"id": evt_id or "evt_wh_%d" % next(_SEQ),
            "type": "checkout.session.completed",
            "data": {"object": obj}}


def _fire(db, obj, evt_id=None):
    return billing_webhook.handle_event(db, _event(obj, evt_id))


class TestACataloguePurchaseSettlesOnlyItself:

    def test_it_marks_the_purchase_paid(self, db_session, world):
        _fire(db_session, _session(world, payment_intent="pi_cat_1"))
        db_session.refresh(world["purchase"])

        assert world["purchase"].status == PurchaseStatus.PAID
        assert world["purchase"].paid_at is not None
        assert world["purchase"].stripe_payment_intent_id == "pi_cat_1"

    def test_it_does_not_mark_the_setup_fee_paid(self, db_session, world):
        """THE ONE THAT MATTERS. Crediting one obligation with another's money
        is the defect the whole separation exists to prevent."""
        _fire(db_session, _session(world, payment_intent="pi_cat_2"))
        db_session.refresh(world["impl"])

        assert world["impl"].setup_payment_status != "paid"
        assert world["impl"].setup_paid_at is None
        assert world["impl"].setup_paid_cents is None

    def test_it_does_not_touch_subscription_state(self, db_session, world):
        org = world["org"]
        before = (org.stripe_subscription_id, org.billing_status,
                  org.billing_plan_key, org.plan)

        _fire(db_session, _session(world, payment_intent="pi_cat_3"))
        db_session.refresh(org)

        assert (org.stripe_subscription_id, org.billing_status,
                org.billing_plan_key, org.plan) == before

    def test_it_banks_the_money_once(self, db_session, world):
        _fire(db_session, _session(world, payment_intent="pi_cat_4"))

        payments = (db_session.query(BillingPayment)
                    .filter(BillingPayment.organization_id == world["org"].id)
                    .all())
        assert len(payments) == 1
        assert payments[0].amount_cents == 75000
        assert payments[0].collection_reference == "stripe_pi:pi_cat_4"

    def test_the_money_is_neither_a_first_invoice_nor_a_renewal(
            self, db_session, world):
        """`is_initial` tells those two apart for compensation. A one-time
        service is a third thing, and the reason is stated so an unpaid
        commission cannot be mistaken for a bug."""
        _fire(db_session, _session(world, payment_intent="pi_cat_5"))
        payment = (db_session.query(BillingPayment)
                   .filter(BillingPayment.organization_id == world["org"].id)
                   .first())

        assert payment.is_initial is False
        assert payment.earned_compensation is False
        assert payment.compensation_skipped_reason
        assert world["item"].key in payment.compensation_skipped_reason


class TestASetupFeeIsStillASetupFee:

    def test_a_session_with_no_purpose_settles_the_setup_fee(self, db_session,
                                                             world):
        """Every session created before the catalogue existed carries no
        `purpose`. Absent must keep meaning setup, or the fix breaks the
        thing it was added next to."""
        _fire(db_session, _session(world, purpose=None,
                                   payment_intent="pi_setup_1"))
        db_session.refresh(world["impl"])

        assert world["impl"].setup_payment_status == "paid"
        assert world["impl"].setup_payment_intent_id == "pi_setup_1"

    def test_an_explicit_setup_session_settles_the_setup_fee(self, db_session,
                                                             world):
        _fire(db_session, _session(world, purpose="setup",
                                   payment_intent="pi_setup_2"))
        db_session.refresh(world["impl"])
        assert world["impl"].setup_payment_status == "paid"

    def test_a_setup_fee_is_not_recorded_as_a_catalogue_purchase(
            self, db_session, world):
        _fire(db_session, _session(world, purpose="setup",
                                   payment_intent="pi_setup_3"))
        db_session.refresh(world["purchase"])

        assert world["purchase"].status == PurchaseStatus.PENDING
        assert world["purchase"].paid_at is None

    def test_a_setup_fee_does_not_start_a_subscription(self, db_session,
                                                       world):
        """Kept here alongside its neighbour so the two shapes are checked
        against the same list rather than against each other."""
        org = world["org"]
        org.stripe_subscription_id = None
        org.billing_status = None
        db_session.commit()

        _fire(db_session, _session(world, purpose="setup",
                                   payment_intent="pi_setup_4"))
        db_session.refresh(org)

        assert org.stripe_subscription_id is None
        assert org.billing_status is None


class TestAnUnpaidSessionIsNotAPayment:

    def test_an_unpaid_catalogue_session_banks_nothing(self, db_session,
                                                       world):
        """A completed session can be `unpaid` while an async method clears."""
        _fire(db_session, _session(world, payment_status="unpaid",
                                   payment_intent="pi_unpaid_1"))
        db_session.refresh(world["purchase"])

        assert world["purchase"].status == PurchaseStatus.PENDING
        assert db_session.query(BillingPayment).filter(
            BillingPayment.organization_id == world["org"].id).count() == 0

    def test_a_session_needing_no_payment_still_settles(self, db_session,
                                                        world):
        """A fully discounted item is paid in the only sense that matters."""
        _fire(db_session, _session(world, payment_status="no_payment_required",
                                   amount=0))
        db_session.refresh(world["purchase"])
        assert world["purchase"].status == PurchaseStatus.PAID


class TestNothingIsBankedTwice:

    def test_the_same_event_delivered_twice_banks_once(self, db_session,
                                                       world):
        """Stripe retries on timeout, on any non-2xx, and on its own schedule
        for three days."""
        obj = _session(world, payment_intent="pi_dup_1")
        first = _fire(db_session, obj, evt_id="evt_dup_1")
        second = _fire(db_session, obj, evt_id="evt_dup_1")

        assert first["duplicate"] is False
        assert second["duplicate"] is True
        assert db_session.query(BillingPayment).filter(
            BillingPayment.organization_id == world["org"].id).count() == 1

    def test_the_same_money_under_a_new_event_id_banks_once(self, db_session,
                                                            world):
        """The event-level dedupe cannot catch this one — somebody resending
        from the dashboard produces a DIFFERENT event id for the same payment.
        `collection_reference` is what stops it."""
        obj = _session(world, payment_intent="pi_dup_2")
        _fire(db_session, obj, evt_id="evt_dup_2a")
        _fire(db_session, obj, evt_id="evt_dup_2b")

        payments = (db_session.query(BillingPayment)
                    .filter(BillingPayment.organization_id == world["org"].id)
                    .all())
        assert len(payments) == 1
        assert payments[0].amount_cents == 75000

    def test_a_replay_does_not_move_the_paid_timestamp(self, db_session,
                                                       world):
        obj = _session(world, payment_intent="pi_dup_3")
        _fire(db_session, obj, evt_id="evt_dup_3a")
        db_session.refresh(world["purchase"])
        first_paid_at = world["purchase"].paid_at

        _fire(db_session, obj, evt_id="evt_dup_3b")
        db_session.refresh(world["purchase"])

        assert world["purchase"].paid_at == first_paid_at

    def test_a_session_with_no_payment_intent_dedupes_on_itself(
            self, db_session, world):
        obj = _session(world, payment_intent=None, session_id="cs_nopi_1")
        _fire(db_session, obj, evt_id="evt_nopi_a")
        _fire(db_session, obj, evt_id="evt_nopi_b")

        payments = (db_session.query(BillingPayment)
                    .filter(BillingPayment.organization_id == world["org"].id)
                    .all())
        assert len(payments) == 1
        assert payments[0].collection_reference == "stripe_cs:cs_nopi_1"


class TestAPurchaseIsSettledOnlyForItsOwnCustomer:

    def test_a_session_naming_another_orgs_purchase_settles_nothing(
            self, db_session, world):
        """Tenant isolation at the money layer."""
        n = next(_SEQ)
        other = Organization(name="Other %d" % n, slug="other-wh-%d" % n,
                             platform_id=world["platform"].id, plan="growth",
                             stripe_customer_id="cus_other_%d" % n)
        db_session.add(other)
        db_session.commit()

        obj = _session(world, payment_intent="pi_cross_1")
        obj["customer"] = other.stripe_customer_id

        _fire(db_session, obj)
        db_session.refresh(world["purchase"])
        assert world["purchase"].status == PurchaseStatus.PENDING

    def test_a_session_naming_no_purchase_settles_nothing(self, db_session,
                                                          world):
        obj = _session(world, payment_intent="pi_nopurchase_1")
        obj["metadata"].pop("catalog_purchase_id")

        _fire(db_session, obj)
        db_session.refresh(world["purchase"])

        assert world["purchase"].status == PurchaseStatus.PENDING
        assert db_session.query(BillingPayment).filter(
            BillingPayment.organization_id == world["org"].id).count() == 0


class TestWhenTheSubscriptionEndsTheAddOnsEndWithIt:
    """A recurring add-on is an ITEM on the subscription, so Stripe deleted it
    along with the subscription and is billing for none of them. A row left
    ACTIVE would claim the customer pays for something nobody charges them for
    — and would keep granting the capacity it bought."""

    def _addon(self, db_session, world, status=PurchaseStatus.ACTIVE):
        p = CatalogPurchase(
            organization_id=world["org"].id,
            platform_id=world["platform"].id,
            item_key="extra_users", item_name="Additional Users",
            kind=CatalogItemKind.RECURRING_ADDON, amount_cents=2500,
            currency="usd", quantity=1, status=status,
            pricing_source=PricingSource.CATALOGUE,
            stripe_subscription_item_id="si_gone")
        db_session.add(p)
        db_session.commit()
        return p

    def _end_it(self, db_session, world):
        return billing_webhook.handle_event(db_session, {
            "id": "evt_end_%d" % next(_SEQ),
            "type": "customer.subscription.deleted",
            "data": {"object": {
                "id": world["org"].stripe_subscription_id,
                "customer": world["org"].stripe_customer_id,
                "status": "canceled",
                "items": {"data": []}}}})

    def test_an_active_addon_is_closed(self, db_session, world):
        addon = self._addon(db_session, world)
        self._end_it(db_session, world)
        db_session.refresh(addon)

        assert addon.status == PurchaseStatus.CANCELED
        assert addon.canceled_at is not None

    def test_it_stops_granting_capacity(self, db_session, world):
        """The defect that matters: a cancelled customer holding five
        purchased seats forever."""
        from app.services import catalog_purchase
        addon = self._addon(db_session, world)
        addon.item_key = "max_users"
        db_session.commit()

        self._end_it(db_session, world)
        assert catalog_purchase.entitlement_totals(
            db_session, world["org"]) == {}

    def test_a_paid_one_time_purchase_is_untouched(self, db_session, world):
        """A migration somebody paid for was delivered. Ending a subscription
        does not unbuy it."""
        world["purchase"].status = PurchaseStatus.PAID
        db_session.commit()

        self._end_it(db_session, world)
        db_session.refresh(world["purchase"])
        assert world["purchase"].status == PurchaseStatus.PAID

    def test_another_customers_addon_is_untouched(self, db_session, world):
        n = next(_SEQ)
        other = Organization(name="Other %d" % n, slug="other-end-%d" % n,
                             platform_id=world["platform"].id, plan="growth",
                             stripe_customer_id="cus_other_end_%d" % n,
                             stripe_subscription_id="sub_other_end_%d" % n,
                             billing_status="active")
        db_session.add(other)
        db_session.commit()
        theirs = CatalogPurchase(
            organization_id=other.id, platform_id=world["platform"].id,
            item_key="extra_users", item_name="Additional Users",
            kind=CatalogItemKind.RECURRING_ADDON, amount_cents=2500,
            currency="usd", quantity=1, status=PurchaseStatus.ACTIVE,
            pricing_source=PricingSource.CATALOGUE)
        db_session.add(theirs)
        db_session.commit()

        self._end_it(db_session, world)
        db_session.refresh(theirs)
        assert theirs.status == PurchaseStatus.ACTIVE

    def test_the_customer_is_not_deleted(self, db_session, world):
        """Cancellation is not deletion — the existing rule, restated because
        this handler now writes to a second table."""
        self._addon(db_session, world)
        self._end_it(db_session, world)
        db_session.refresh(world["org"])

        assert world["org"].is_active is not False
        assert world["org"].billing_plan_key == "growth"
