"""A BROWSER REDIRECT IS NOT PROOF OF PAYMENT, AND A RETRY IS NOT A SECOND SALE.

WHAT THIS FILE DEFENDS

  1. THE SIGNATURE IS THE DOOR. An unverified body writes nothing at all.
  2. ONE EVENT IS PROCESSED ONCE. Stripe retries for three days; the unique
     insert on `stripe_event_id` is the lock, and a duplicate is a 2xx no-op.
  3. ONE PAYMENT IS BANKED ONCE. `invoice.paid` and `invoice.payment_succeeded`
     describe the same money and must collapse to ONE BillingPayment, because
     the collection reference is derived from the INVOICE and never from the
     event id.
  4. CANCELLATION IS NOT DELETION. A subscription ending updates billing state
     and touches no deal, proposal, invoice, payment or plan history.

NOTHING HERE REACHES STRIPE. The signature check is the only Stripe call on
this path and it is patched; every other test drives `billing_webhook` with
the payload shape Stripe sends.
"""

import itertools
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
import stripe

from app.models.billing_models import (BillingEvent, BillingEventOutcome,
                                       BillingInvoice, BillingPayment,
                                       BrandBillingPlan, SubscriptionStatus)
from app.models.models import Organization, Platform, Proposal, User
from app.services import billing_webhook
from app.services.auth_service import hash_password

_SEQ = itertools.count(1)

FAKE_SECRET_KEY = "sk_test_FAKE_not_a_real_key"
FAKE_WEBHOOK_SECRET = "whsec_FAKE_not_a_real_secret"


def _platform(db, label="Alpha"):
    p = Platform(name=label, slug="%s-%d" % (label.lower(), next(_SEQ)))
    db.add(p)
    db.commit()
    return p


def _plan(db, platform, key, *, monthly=49700, annual=None):
    plan = BrandBillingPlan(platform_id=platform.id, key=key,
                            name=key.title(), monthly_cents=monthly,
                            annual_cents=annual, currency="usd",
                            is_purchasable=True, is_active=True)
    db.add(plan)
    db.commit()
    return plan


def _org(db, platform, **kw):
    n = next(_SEQ)
    kw.setdefault("stripe_customer_id", "cus_test_%d" % n)
    kw.setdefault("plan", "trial")
    org = Organization(name="Customer %d" % n, slug="customer-%d" % n,
                       platform_id=platform.id, is_active=True, **kw)
    db.add(org)
    db.commit()
    return org


@pytest.fixture()
def brand(db_session):
    platform = _platform(db_session)
    _plan(db_session, platform, "starter", monthly=49700)
    _plan(db_session, platform, "growth", monthly=99700, annual=1196400)
    return platform


@pytest.fixture()
def org(db_session, brand):
    return _org(db_session, brand, billing_plan_key="starter", plan="starter",
                stripe_subscription_id="sub_live_1", billing_status="active")


def _unix(dt):
    """Stripe sends UTC unix seconds; `_ts` reads them back as naive UTC.

    Built with an explicit UTC tzinfo rather than `dt.timestamp()`, which would
    interpret a naive datetime in the MACHINE's local zone and make these
    assertions pass or fail depending on where the suite is run.
    """
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


def _invoice(org, *, invoice_id, amount=49700, status="paid",
             paid_at=None, billing_reason="subscription_cycle",
             payment_intent=None, charge=None):
    """The shape Stripe actually sends, trimmed to the fields we read."""
    paid_at = paid_at or datetime(2026, 9, 1, 12, 0, 0)
    return {
        "id": invoice_id,
        "object": "invoice",
        "customer": org.stripe_customer_id,
        "subscription": org.stripe_subscription_id,
        "status": status,
        "currency": "usd",
        "amount_due": amount,
        "amount_paid": amount if status == "paid" else 0,
        "billing_reason": billing_reason,
        "hosted_invoice_url": "https://invoice.stripe.com/i/%s" % invoice_id,
        "period_start": _unix(datetime(2026, 9, 1)),
        "period_end": _unix(datetime(2026, 10, 1)),
        "status_transitions": {"paid_at": _unix(paid_at)},
        "payment_intent": payment_intent,
        "charge": charge,
    }


def _subscription(org, *, status="active", interval="month", plan_meta=None,
                  cancel_at_period_end=False, period_end=None, sub_id=None):
    return {
        "id": sub_id or org.stripe_subscription_id or "sub_new_1",
        "object": "subscription",
        "customer": org.stripe_customer_id,
        "status": status,
        "cancel_at_period_end": cancel_at_period_end,
        "current_period_end": _unix(period_end or datetime(2026, 10, 1)),
        "trial_end": None,
        "items": {"data": [{"id": "si_1",
                            "price": {"id": "price_1",
                                      "recurring": {"interval": interval}}}]},
        "metadata": ({"plan": plan_meta} if plan_meta else {}),
    }


def _event(event_type, obj, evt_id=None):
    return {"id": evt_id or "evt_test_%d" % next(_SEQ),
            "type": event_type,
            "data": {"object": obj}}


@pytest.fixture()
def stripe_env(monkeypatch):
    """Obviously-fake credentials. Nothing in this suite may hold a real key."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", FAKE_SECRET_KEY)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", FAKE_WEBHOOK_SECRET)
    return {"secret_key": FAKE_SECRET_KEY, "webhook_secret": FAKE_WEBHOOK_SECRET}


# ═══════════════════════════════════════════════════════════════════════════
# 10-11. THE SIGNATURE IS THE DOOR
# ═══════════════════════════════════════════════════════════════════════════

def test_an_invalid_signature_is_refused_and_writes_nothing(
        client, db_session, org, stripe_env):
    """A forged POST must not be able to move billing state. Verification is
    the only thing standing between an anonymous request and a paid flag."""
    def _refuse(body, sig, secret, *a, **kw):
        raise stripe.error.SignatureVerificationError("Invalid signature", sig)

    with patch.object(stripe.Webhook, "construct_event", side_effect=_refuse):
        response = client.post(
            "/billing/webhook",
            content=b'{"id":"evt_forged","type":"invoice.paid"}',
            headers={"stripe-signature": "t=1,v1=notasignature"})

    assert response.status_code == 400
    assert db_session.query(BillingEvent).count() == 0
    assert db_session.query(BillingPayment).count() == 0
    db_session.refresh(org)
    assert org.billing_status == "active"


def test_a_webhook_with_no_signature_header_is_refused(
        client, db_session, org, stripe_env):
    def _refuse(body, sig, secret, *a, **kw):
        raise stripe.error.SignatureVerificationError("No signature", sig or "")

    with patch.object(stripe.Webhook, "construct_event", side_effect=_refuse):
        response = client.post("/billing/webhook", content=b"{}")
    assert response.status_code == 400
    assert db_session.query(BillingEvent).count() == 0


def test_a_valid_signature_is_accepted_and_processed(
        client, db_session, org, stripe_env):
    event = _event("customer.subscription.updated",
                   _subscription(org, status="active", interval="month"),
                   evt_id="evt_signed_ok")

    with patch.object(stripe.Webhook, "construct_event", return_value=event):
        response = client.post(
            "/billing/webhook", content=b'{"anything":"the signature decides"}',
            headers={"stripe-signature": "t=1,v1=a-valid-looking-signature"})

    assert response.status_code == 200
    body = response.json()
    assert body["received"] is True
    assert body["ok"] is True
    assert body["duplicate"] is False

    row = (db_session.query(BillingEvent)
           .filter(BillingEvent.stripe_event_id == "evt_signed_ok").first())
    assert row is not None
    assert row.outcome == BillingEventOutcome.PROCESSED
    assert row.organization_id == org.id
    assert row.processed_at is not None


def test_the_webhook_is_refused_outright_when_no_secret_is_configured(
        client, db_session, org, monkeypatch):
    """No signing secret means no way to verify anybody, so the endpoint must
    not accept a body it cannot authenticate."""
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    response = client.post("/billing/webhook", content=b"{}")
    assert response.status_code == 503
    assert db_session.query(BillingEvent).count() == 0


# ═══════════════════════════════════════════════════════════════════════════
# 12. RETRY IDEMPOTENCY — the same event id twice runs once
# ═══════════════════════════════════════════════════════════════════════════

def test_the_same_event_id_delivered_twice_processes_once(db_session, org):
    """Stripe retries on timeout, on any non-2xx, and on its own schedule for
    three days. The second delivery must be a 2xx no-op, not a second payment."""
    event = _event("invoice.paid",
                   _invoice(org, invoice_id="in_retry_1"),
                   evt_id="evt_retry_1")

    first = billing_webhook.handle_event(db_session, event)
    assert first["duplicate"] is False

    second = billing_webhook.handle_event(db_session, event)
    assert second["ok"] is True
    assert second["duplicate"] is True

    assert db_session.query(BillingEvent).filter(
        BillingEvent.stripe_event_id == "evt_retry_1").count() == 1
    assert db_session.query(BillingPayment).count() == 1
    assert db_session.query(BillingInvoice).count() == 1


def test_a_duplicate_delivery_does_not_double_the_banked_amount(db_session, org):
    event = _event("invoice.paid",
                   _invoice(org, invoice_id="in_retry_2", amount=99700),
                   evt_id="evt_retry_2")
    billing_webhook.handle_event(db_session, event)
    billing_webhook.handle_event(db_session, event)
    billing_webhook.handle_event(db_session, event)

    payments = db_session.query(BillingPayment).all()
    assert len(payments) == 1
    assert payments[0].amount_cents == 99700


# ═══════════════════════════════════════════════════════════════════════════
# 13. RELATED EVENTS ARE ONE PAYMENT — the reference comes from the INVOICE
# ═══════════════════════════════════════════════════════════════════════════

def test_two_related_events_for_one_invoice_bank_exactly_one_payment(
        db_session, org):
    """invoice.paid and invoice.payment_succeeded are one card charge described
    twice. Keying the payment on the EVENT id would look like idempotency and
    would credit the same money twice."""
    invoice = _invoice(org, invoice_id="in_shared_1", amount=49700,
                       payment_intent="pi_shared_1", charge="ch_shared_1")

    paid = billing_webhook.handle_event(
        db_session, _event("invoice.paid", invoice, evt_id="evt_paid_1"))
    succeeded = billing_webhook.handle_event(
        db_session, _event("invoice.payment_succeeded", invoice,
                           evt_id="evt_succeeded_1"))

    # Two DIFFERENT events, both accepted and both recorded in the ledger.
    assert paid["duplicate"] is False and succeeded["duplicate"] is False
    assert db_session.query(BillingEvent).count() == 2

    # ONE payment.
    payments = db_session.query(BillingPayment).all()
    assert len(payments) == 1
    assert payments[0].collection_reference == "stripe:in_shared_1"
    assert payments[0].amount_cents == 49700

    # And the second event says why it banked nothing, so a zero-earning
    # payment cannot be mistaken for a broken one.
    second = (db_session.query(BillingEvent)
              .filter(BillingEvent.stripe_event_id == "evt_succeeded_1").first())
    assert "no double" in (second.detail or "").lower()


def test_the_collection_reference_prefers_the_invoice_over_the_intent(db_session):
    """An ORDER, not a choice: every related event carries the same invoice id,
    which is the only thing that collapses them."""
    from app.services import billing_compensation
    assert billing_compensation.collection_reference_for(
        stripe_invoice_id="in_1", stripe_payment_intent_id="pi_1",
        stripe_charge_id="ch_1") == "stripe:in_1"
    assert billing_compensation.collection_reference_for(
        stripe_payment_intent_id="pi_1", stripe_charge_id="ch_1") == "stripe:pi_1"
    assert billing_compensation.collection_reference_for(
        stripe_charge_id="ch_1") == "stripe:ch_1"
    assert billing_compensation.collection_reference_for() is None


# ═══════════════════════════════════════════════════════════════════════════
# 14. invoice.paid MIRRORS THE INVOICE AND UPDATES BILLING STATE
# ═══════════════════════════════════════════════════════════════════════════

def test_invoice_paid_creates_a_local_invoice_and_a_payment(db_session, org):
    """God Mode's revenue panels read this mirror. Calling Stripe per page load
    would make the dashboard as available as Stripe's API on its worst day."""
    paid_at = datetime(2026, 9, 3, 9, 30, 0)
    invoice = _invoice(org, invoice_id="in_mirror_1", amount=49700,
                       paid_at=paid_at, billing_reason="subscription_create")
    result = billing_webhook.handle_event(
        db_session, _event("invoice.paid", invoice, evt_id="evt_mirror_1"))
    assert result["ok"] is True

    mirrored = (db_session.query(BillingInvoice)
                .filter(BillingInvoice.stripe_invoice_id == "in_mirror_1").first())
    assert mirrored is not None
    assert mirrored.organization_id == org.id
    assert mirrored.platform_id == org.platform_id
    assert mirrored.status == "paid"
    assert mirrored.amount_paid_cents == 49700
    assert mirrored.amount_due_cents == 49700
    assert mirrored.paid_at == paid_at
    assert mirrored.period_end == datetime(2026, 10, 1)
    assert mirrored.hosted_invoice_url.endswith("in_mirror_1")
    assert mirrored.billing_plan_key == "starter"

    payment = db_session.query(BillingPayment).one()
    assert payment.organization_id == org.id
    assert payment.platform_id == org.platform_id
    assert payment.amount_cents == 49700
    assert payment.collected_at == paid_at
    assert payment.is_initial is True          # billing_reason subscription_create
    assert payment.refunded_cents == 0


def test_a_second_invoice_for_the_same_org_is_a_second_payment(db_session, org):
    """Different money. The dedup must not swallow a renewal."""
    billing_webhook.handle_event(db_session, _event(
        "invoice.paid", _invoice(org, invoice_id="in_month_1"), "evt_m1"))
    billing_webhook.handle_event(db_session, _event(
        "invoice.paid", _invoice(org, invoice_id="in_month_2"), "evt_m2"))
    refs = {p.collection_reference for p in db_session.query(BillingPayment).all()}
    assert refs == {"stripe:in_month_1", "stripe:in_month_2"}


def test_a_failed_payment_records_the_invoice_and_withdraws_nothing(
        db_session, org):
    """past_due is recorded. No entitlement is taken away, because the
    failed-payment consequence is a brand policy nobody has set."""
    from app.services import entitlements
    invoice = _invoice(org, invoice_id="in_failed_1", status="open",
                       amount=49700)
    billing_webhook.handle_event(
        db_session, _event("invoice.payment_failed", invoice, "evt_failed_1"))

    db_session.refresh(org)
    assert org.billing_status == SubscriptionStatus.PAST_DUE
    assert org.is_active is True
    assert db_session.query(BillingInvoice).count() == 1
    # No money arrived, so nothing was banked.
    assert db_session.query(BillingPayment).count() == 0
    assert entitlements.billing_suspension_reason(db_session, org) is None


# ═══════════════════════════════════════════════════════════════════════════
# 26. SUBSCRIPTION STATE PROPAGATES TO THE ORGANIZATION
# ═══════════════════════════════════════════════════════════════════════════

def test_subscription_updates_propagate_status_period_end_and_interval(
        db_session, org):
    """Stripe's own vocabulary, passed through unchanged. A parallel
    vocabulary would need a translation table that has to stay correct
    forever, and the first missed status becomes "unknown" on a billing screen."""
    period_end = datetime(2027, 3, 15, 8, 0, 0)
    sub = _subscription(org, status=SubscriptionStatus.PAST_DUE,
                        interval="year", plan_meta="growth",
                        cancel_at_period_end=True, period_end=period_end)
    billing_webhook.handle_event(
        db_session, _event("customer.subscription.updated", sub, "evt_sub_1"))

    db_session.refresh(org)
    assert org.billing_status == SubscriptionStatus.PAST_DUE
    assert org.billing_current_period_end == period_end
    assert org.stripe_plan_interval == "year"
    assert org.billing_cancel_at_period_end is True
    assert org.billing_plan_key == "growth"
    assert org.plan == "growth"


def test_a_plan_from_metadata_is_validated_against_the_brands_catalogue(
        db_session, org):
    """The previous handler copied metadata["plan"] onto org.plan with no
    membership check, so anyone able to edit a subscription in the Stripe
    dashboard could write an arbitrary string several screens read."""
    sub = _subscription(org, status="active", plan_meta="platinum-unlimited")
    billing_webhook.handle_event(
        db_session, _event("customer.subscription.updated", sub, "evt_meta_1"))
    db_session.refresh(org)
    assert org.billing_plan_key == "starter"      # unchanged
    assert org.plan == "starter"
    assert org.billing_status == "active"


def test_a_plan_key_from_another_brand_is_not_written_by_a_webhook(
        db_session, brand, org):
    """Brand B's tier is not in Brand A's catalogue, so it does not resolve."""
    other = _platform(db_session, "Beta")
    _plan(db_session, other, "beta-elite", monthly=500000)
    sub = _subscription(org, status="active", plan_meta="beta-elite")
    billing_webhook.handle_event(
        db_session, _event("customer.subscription.updated", sub, "evt_meta_2"))
    db_session.refresh(org)
    assert org.billing_plan_key == "starter"


def test_a_checkout_completion_records_ids_without_marking_anything_paid(
        db_session, brand):
    """The subscription and invoice events own paid state, and they arrive for
    renewals too, so one code path owns it rather than two that must agree."""
    fresh = _org(db_session, brand, stripe_customer_id="cus_checkout_1")
    session_obj = {"id": "cs_test_1", "object": "checkout.session",
                   "customer": "cus_checkout_1", "subscription": "sub_from_cs",
                   "metadata": {"org_id": fresh.id, "plan": "growth",
                                "interval": "year"}}
    billing_webhook.handle_event(
        db_session, _event("checkout.session.completed", session_obj, "evt_cs_1"))

    db_session.refresh(fresh)
    assert fresh.stripe_subscription_id == "sub_from_cs"
    assert fresh.billing_plan_key == "growth"
    assert fresh.stripe_plan_interval == "year"
    # Nothing was marked paid by a completed checkout.
    assert fresh.billing_status is None
    assert db_session.query(BillingPayment).count() == 0


# ═══════════════════════════════════════════════════════════════════════════
# 21 & 27. CANCELLATION IS NOT DELETION
# ═══════════════════════════════════════════════════════════════════════════

def _commercial_history(db, org):
    """A deal, a proposal and a paid invoice behind one customer."""
    from app.models.sales_models import BrandSalesOrg, Opportunity

    seller = User(organization_id=None,
                  email="seller%d@evosyspro.live" % next(_SEQ),
                  password_hash=hash_password("x"), full_name="Seller",
                  role="advisor", must_change_password=False)
    db.add(seller)
    db.commit()

    sales_org = BrandSalesOrg(platform_id=org.platform_id, name="Alpha Sales",
                              slug="alpha-sales-%d" % next(_SEQ))
    db.add(sales_org)
    db.commit()

    deal = Opportunity(brand_sales_org_id=sales_org.id,
                       owner_user_id=seller.id, company_name="Won Deal",
                       stage="closing", status="won",
                       customer_organization_id=org.id)
    db.add(deal)
    db.commit()

    proposal = Proposal(organization_id=org.id, created_by_id=seller.id,
                        brand_sales_org_id=sales_org.id,
                        opportunity_id=deal.id, title="Starter proposal",
                        status="published")
    db.add(proposal)
    db.commit()

    billing_webhook.handle_event(db, _event(
        "invoice.paid", _invoice(org, invoice_id="in_history_1"),
        "evt_history_1"))
    return {"seller": seller, "sales_org": sales_org, "deal": deal,
            "proposal": proposal}


def test_cancellation_preserves_every_commercial_record(db_session, org):
    """A card expiring must never delete a customer. Billing state changes and
    nothing else: not the workspace, not the users, not the deal, proposal,
    invoice or payment history."""
    from app.models.sales_models import Opportunity

    history = _commercial_history(db_session, org)
    invoice_id = db_session.query(BillingInvoice).one().id
    payment_id = db_session.query(BillingPayment).one().id

    billing_webhook.handle_event(db_session, _event(
        "customer.subscription.deleted",
        _subscription(org, status="canceled"), "evt_deleted_1"))

    db_session.refresh(org)
    assert org.billing_status == SubscriptionStatus.CANCELED

    # Every commercial row survives, unedited.
    assert db_session.query(Opportunity).filter(
        Opportunity.id == history["deal"].id).first() is not None
    assert db_session.query(Opportunity).filter(
        Opportunity.id == history["deal"].id).first().status == "won"
    assert db_session.query(Proposal).filter(
        Proposal.id == history["proposal"].id).first() is not None
    assert db_session.query(BillingInvoice).filter(
        BillingInvoice.id == invoice_id).first() is not None
    assert db_session.query(BillingPayment).filter(
        BillingPayment.id == payment_id).first() is not None

    # And the workspace itself is untouched — offboarding is a separate,
    # deliberate decision made through customer_lifecycle.
    assert org.is_active is True
    assert db_session.query(Organization).filter(
        Organization.id == org.id).first() is not None


def test_a_cancelled_subscription_clears_the_id_and_keeps_the_plan(
        db_session, org):
    """What they bought is a historical fact. Rewriting `plan` to something
    they never chose is how a customer loses a record of what they paid for —
    and entitlement decisions are made from billing_status by a policy that is
    currently unset, not by silently editing the plan."""
    org.billing_pending_plan_key = "growth"
    org.billing_cancel_at_period_end = True
    db_session.commit()

    billing_webhook.handle_event(db_session, _event(
        "customer.subscription.deleted",
        _subscription(org, status="canceled"), "evt_deleted_2"))

    db_session.refresh(org)
    assert org.billing_status == SubscriptionStatus.CANCELED
    assert org.stripe_subscription_id is None      # a new one can be created
    assert org.billing_cancel_at_period_end is False
    assert org.billing_pending_plan_key is None
    # NOT rewritten.
    assert org.plan == "starter"
    assert org.billing_plan_key == "starter"
    # The customer relationship itself is retained.
    assert org.stripe_customer_id is not None


def test_a_cancelled_org_can_start_a_new_subscription_afterwards(db_session, org):
    billing_webhook.handle_event(db_session, _event(
        "customer.subscription.deleted",
        _subscription(org, status="canceled"), "evt_deleted_3"))
    db_session.refresh(org)
    assert org.stripe_subscription_id is None

    billing_webhook.handle_event(db_session, _event(
        "customer.subscription.created",
        _subscription(org, status="active", sub_id="sub_second_life",
                      plan_meta="growth"), "evt_created_1"))
    db_session.refresh(org)
    assert org.stripe_subscription_id == "sub_second_life"
    assert org.billing_status == "active"
    assert org.billing_plan_key == "growth"


# ═══════════════════════════════════════════════════════════════════════════
# THE LEDGER — recorded and not actioned is a different fact from silence
# ═══════════════════════════════════════════════════════════════════════════

def test_an_unhandled_event_type_is_recorded_as_ignored_not_dropped(
        db_session, org):
    result = billing_webhook.handle_event(db_session, _event(
        "payment_intent.succeeded",
        {"id": "pi_1", "customer": org.stripe_customer_id, "amount": 49700},
        "evt_ignored_1"))
    assert result["ok"] is True
    assert result.get("ignored") is True
    row = (db_session.query(BillingEvent)
           .filter(BillingEvent.stripe_event_id == "evt_ignored_1").one())
    assert row.outcome == BillingEventOutcome.IGNORED
    assert db_session.query(BillingPayment).count() == 0


def test_an_event_for_an_unknown_customer_is_recorded_and_not_actioned(
        db_session, brand):
    """An event we cannot place is ordinary — an unknown customer, a
    subscription for a deleted org. Raising would make Stripe retry a
    permanent condition every few hours for three days."""
    unknown = {"id": "in_orphan", "customer": "cus_nobody_here",
               "subscription": None, "status": "paid", "currency": "usd",
               "amount_due": 49700, "amount_paid": 49700,
               "status_transitions": {}}
    result = billing_webhook.handle_event(
        db_session, _event("invoice.paid", unknown, "evt_orphan_1"))
    assert result["ok"] is True
    assert result.get("ignored") is True
    row = (db_session.query(BillingEvent)
           .filter(BillingEvent.stripe_event_id == "evt_orphan_1").one())
    assert row.outcome == BillingEventOutcome.IGNORED
    assert row.organization_id is None
    assert db_session.query(BillingPayment).count() == 0
    assert db_session.query(BillingInvoice).count() == 0


def test_the_event_ledger_stores_public_ids_and_never_a_secret(
        db_session, org, stripe_env):
    """Stripe object ids are public identifiers and are what make the ledger
    answerable. No payload, no key, no card detail is ever written."""
    invoice = _invoice(org, invoice_id="in_ledger_1",
                       payment_intent="pi_ledger_1", charge="ch_ledger_1")
    billing_webhook.handle_event(
        db_session, _event("invoice.paid", invoice, "evt_ledger_1"))

    row = (db_session.query(BillingEvent)
           .filter(BillingEvent.stripe_event_id == "evt_ledger_1").one())
    assert row.stripe_object_id == "in_ledger_1"
    assert row.stripe_customer_id == org.stripe_customer_id
    assert row.stripe_subscription_id == org.stripe_subscription_id
    assert row.platform_id == org.platform_id

    stored = " ".join(str(getattr(row, c.name)) for c in row.__table__.columns)
    assert FAKE_SECRET_KEY not in stored
    assert FAKE_WEBHOOK_SECRET not in stored
    assert "sk_" not in stored and "whsec_" not in stored
