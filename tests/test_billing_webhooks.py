"""Stripe webhook processing.

NO LIVE STRIPE. Every event here is a fixture dict shaped like Stripe's own
payload. Nothing in this file has a network call or an API key.

The cases are the ones that cost money when they are wrong: a redelivered
event, an event that arrives out of order, a failed payment, and an event for a
customer that is not ours.
"""
from datetime import datetime, timedelta

import pytest

from app.models.billing_models import (EVENT_FAILED, EVENT_IGNORED,
                                       EVENT_PROCESSED, Invoice, Payment,
                                       StripeWebhookEvent)
from app.models.models import Organization
from app.services import billing_webhooks, stripe_sync

NOW = int(datetime(2026, 9, 4, 12, 0, 0).timestamp())
CUST = "cus_TEST123"


# ── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture()
def billed_org(db_session):
    org = Organization(name="Billed Co", slug="billed-co", plan="growth",
                       stripe_customer_id=CUST, billing_status="active")
    db_session.add(org)
    db_session.commit()
    return org


def _invoice_event(etype="invoice.paid", *, status="paid", event_id="evt_1",
                   invoice_id="in_1", created=NOW, attempt_count=0,
                   amount_due=99700, amount_paid=99700, customer=CUST):
    return {
        "id": event_id, "type": etype, "created": created,
        "api_version": "2024-06-20",
        "data": {"object": {
            "id": invoice_id, "object": "invoice", "customer": customer,
            "subscription": "sub_1", "number": "INV-0001", "status": status,
            "currency": "usd", "subtotal": amount_due, "total": amount_due,
            "amount_due": amount_due, "amount_paid": amount_paid,
            "attempt_count": attempt_count,
            "account_name": "EVO INTEGRATED SOLUTIONS LLC",
            "hosted_invoice_url": "https://pay.stripe.test/i/1",
            "invoice_pdf": "https://pay.stripe.test/i/1.pdf",
            "status_transitions": {"paid_at": created if status == "paid" else None},
            "lines": {"data": [{
                "id": "il_1", "description": "EvoSys Pro Growth",
                "quantity": 1, "amount": amount_due, "currency": "usd",
                "price": {"unit_amount": amount_due},
                "period": {"start": created, "end": created + 2592000},
            }]},
        }},
    }


def _charge_refunded_event(event_id="evt_ref", amount=99700, refunded=99700):
    return {
        "id": event_id, "type": "charge.refunded", "created": NOW,
        "data": {"object": {
            "id": "ch_1", "object": "charge", "customer": CUST,
            "payment_intent": "pi_1", "invoice": "in_1",
            "amount": amount, "amount_refunded": refunded,
            "currency": "usd", "status": "succeeded",
            "payment_method_details": {"card": {"brand": "visa", "last4": "4242"}},
        }},
    }


# ── 1. a valid webhook is applied ───────────────────────────────────────────

def test_valid_invoice_paid_is_mirrored(db_session, billed_org):
    result, dup = billing_webhooks.handle(db_session, _invoice_event())
    assert dup is False
    assert result.get("ignored") is None

    inv = db_session.query(Invoice).filter_by(stripe_invoice_id="in_1").one()
    assert inv.status == "paid"
    assert inv.total_cents == 99700
    assert inv.organization_id == billed_org.id
    assert inv.number == "INV-0001"
    assert inv.hosted_invoice_url.endswith("/i/1")

    ledger = db_session.query(StripeWebhookEvent).filter_by(
        stripe_event_id="evt_1").one()
    assert ledger.processing_status == EVENT_PROCESSED


def test_line_items_are_captured(db_session, billed_org):
    billing_webhooks.handle(db_session, _invoice_event())
    inv = db_session.query(Invoice).filter_by(stripe_invoice_id="in_1").one()
    assert len(inv_lines(db_session, inv)) == 1
    assert inv_lines(db_session, inv)[0].description == "EvoSys Pro Growth"


def inv_lines(db, invoice):
    from app.models.billing_models import InvoiceLineItem
    return db.query(InvoiceLineItem).filter_by(invoice_id=invoice.id).all()


def test_the_legal_entity_and_the_brand_are_both_snapshotted(db_session, billed_org):
    """EVO INTEGRATED SOLUTIONS LLC sells; EvoSys Pro is what the customer
    bought. An issued invoice must keep saying both."""
    billing_webhooks.handle(db_session, _invoice_event())
    inv = db_session.query(Invoice).filter_by(stripe_invoice_id="in_1").one()
    assert inv.merchant_legal_name == "EVO INTEGRATED SOLUTIONS LLC"
    assert inv.bill_to_org_name == "Billed Co"


# ── 2. duplicate delivery ───────────────────────────────────────────────────

def test_duplicate_event_is_a_no_op(db_session, billed_org):
    """Stripe documents that it redelivers. The second delivery must change
    nothing."""
    billing_webhooks.handle(db_session, _invoice_event())
    first = db_session.query(Invoice).filter_by(stripe_invoice_id="in_1").one()
    first_updated = first.updated_at

    result, dup = billing_webhooks.handle(db_session, _invoice_event())
    assert dup is True
    assert result == {"duplicate": True}

    assert db_session.query(StripeWebhookEvent).count() == 1
    assert db_session.query(Invoice).count() == 1
    db_session.refresh(first)
    assert first.updated_at == first_updated


def test_a_redelivered_refund_refunds_once(db_session, billed_org):
    """The failure this ledger exists to prevent: Stripe refunded once, and
    without the uniqueness claim our books would say twice."""
    billing_webhooks.handle(db_session, _invoice_event())
    billing_webhooks.handle(db_session, _charge_refunded_event())
    billing_webhooks.handle(db_session, _charge_refunded_event())

    payments = db_session.query(Payment).all()
    assert len(payments) == 1
    assert payments[0].refunded_cents == 99700
    assert payments[0].status == "refunded"


# ── 3. out-of-order delivery ────────────────────────────────────────────────

def test_a_stale_event_does_not_overwrite_newer_state(db_session, billed_org):
    """Stripe does not guarantee order. A late invoice.finalized must not mark
    a paid invoice unpaid."""
    billing_webhooks.handle(db_session, _invoice_event(
        etype="invoice.paid", status="paid", event_id="evt_new", created=NOW))

    billing_webhooks.handle(db_session, _invoice_event(
        etype="invoice.finalized", status="open", event_id="evt_old",
        created=NOW - 600, attempt_count=0))

    inv = db_session.query(Invoice).filter_by(stripe_invoice_id="in_1").one()
    assert inv.status == "paid", "an older event overwrote newer state"


def test_same_timestamp_events_are_applied_in_arrival_order(db_session, billed_org):
    """Stripe emits several events for one change within the same second;
    each carries the same object state, so neither should be skipped."""
    billing_webhooks.handle(db_session, _invoice_event(
        etype="invoice.finalized", status="open", event_id="e1", created=NOW))
    billing_webhooks.handle(db_session, _invoice_event(
        etype="invoice.paid", status="paid", event_id="e2", created=NOW))
    inv = db_session.query(Invoice).filter_by(stripe_invoice_id="in_1").one()
    assert inv.status == "paid"


# ── 4. payment failure — the live gap this phase fixes ──────────────────────

def test_invoice_payment_failed_marks_the_org_past_due(db_session, billed_org):
    """Before P0 this event was not handled at all, so an organization whose
    card declined stayed 'active' until somebody opened Stripe."""
    assert billed_org.billing_status == "active"

    billing_webhooks.handle(db_session, _invoice_event(
        etype="invoice.payment_failed", status="open", event_id="evt_fail",
        amount_paid=0, attempt_count=1))

    db_session.refresh(billed_org)
    assert billed_org.billing_status == "past_due"

    inv = db_session.query(Invoice).filter_by(stripe_invoice_id="in_1").one()
    assert inv.status == "open"
    assert inv.attempt_count == 1


def test_an_open_invoice_with_no_attempt_is_not_past_due(db_session, billed_org):
    """An invoice that is merely not due yet is not a failure, and calling it
    past due would alarm somebody for nothing."""
    billing_webhooks.handle(db_session, _invoice_event(
        etype="invoice.finalized", status="open", event_id="evt_open",
        amount_paid=0, attempt_count=0))
    db_session.refresh(billed_org)
    assert billed_org.billing_status == "active"


def test_paying_clears_past_due_only_when_nothing_else_is_open(db_session, billed_org):
    billing_webhooks.handle(db_session, _invoice_event(
        etype="invoice.payment_failed", status="open", event_id="f1",
        invoice_id="in_A", amount_paid=0, attempt_count=1))
    billing_webhooks.handle(db_session, _invoice_event(
        etype="invoice.payment_failed", status="open", event_id="f2",
        invoice_id="in_B", amount_paid=0, attempt_count=1))
    db_session.refresh(billed_org)
    assert billed_org.billing_status == "past_due"

    billing_webhooks.handle(db_session, _invoice_event(
        etype="invoice.paid", status="paid", event_id="p1", invoice_id="in_A"))
    db_session.refresh(billed_org)
    assert billed_org.billing_status == "past_due", "in_B is still open"

    billing_webhooks.handle(db_session, _invoice_event(
        etype="invoice.paid", status="paid", event_id="p2", invoice_id="in_B"))
    db_session.refresh(billed_org)
    assert billed_org.billing_status == "active"


def test_a_cancelled_org_is_not_dragged_back_to_past_due(db_session, billed_org):
    """A cancelled customer with an unpaid final invoice is cancelled, not
    past due."""
    billed_org.billing_status = "canceled"
    db_session.commit()
    billing_webhooks.handle(db_session, _invoice_event(
        etype="invoice.payment_failed", status="open", event_id="evt_x",
        amount_paid=0, attempt_count=2))
    db_session.refresh(billed_org)
    assert billed_org.billing_status == "canceled"


# ── 5. unknown customer ─────────────────────────────────────────────────────

def test_unknown_customer_creates_nothing(db_session, billed_org):
    """A webhook may never invent a tenant."""
    result, dup = billing_webhooks.handle(db_session, _invoice_event(
        event_id="evt_unknown", customer="cus_SOMEBODY_ELSE"))

    assert "ignored" in result
    assert db_session.query(Invoice).count() == 0
    assert db_session.query(Organization).count() == 1

    ledger = db_session.query(StripeWebhookEvent).filter_by(
        stripe_event_id="evt_unknown").one()
    assert ledger.processing_status == EVENT_IGNORED
    assert "no organization" in ledger.ignored_reason


def test_unknown_subscription_for_unknown_customer_is_ignored(db_session, billed_org):
    result, _ = billing_webhooks.handle(db_session, {
        "id": "evt_sub_unknown", "type": "customer.subscription.updated",
        "created": NOW,
        "data": {"object": {"id": "sub_nope", "customer": "cus_NOPE",
                            "status": "active"}},
    })
    assert "ignored" in result


# ── 6. handler failure / retry behaviour ────────────────────────────────────

def test_a_transient_failure_raises_so_stripe_retries(db_session, billed_org, monkeypatch):
    """Answering 200 over a swallowed exception loses the event permanently,
    and a lost invoice.payment_failed is a customer who looks fine."""
    def boom(*a, **kw):
        raise RuntimeError("database is briefly unavailable")

    monkeypatch.setattr(stripe_sync, "upsert_invoice_from_stripe", boom)

    with pytest.raises(RuntimeError):
        billing_webhooks.handle(db_session, _invoice_event(event_id="evt_boom"))

    ledger = db_session.query(StripeWebhookEvent).filter_by(
        stripe_event_id="evt_boom").one()
    assert ledger.processing_status == EVENT_FAILED
    assert "database is briefly unavailable" in ledger.error_message


def test_an_unsupported_event_is_recorded_not_failed(db_session, billed_org):
    result, _ = billing_webhooks.handle(db_session, {
        "id": "evt_other", "type": "customer.discount.created",
        "created": NOW, "data": {"object": {"id": "di_1"}},
    })
    assert "ignored" in result
    ledger = db_session.query(StripeWebhookEvent).filter_by(
        stripe_event_id="evt_other").one()
    assert ledger.processing_status == EVENT_IGNORED


# ── 7. subscription synchronisation, behaviour preserved ────────────────────

def test_subscription_updated_mirrors_status(db_session, billed_org):
    billed_org.stripe_subscription_id = "sub_1"
    db_session.commit()
    billing_webhooks.handle(db_session, {
        "id": "evt_sub_u", "type": "customer.subscription.updated",
        "created": NOW,
        "data": {"object": {"id": "sub_1", "customer": CUST,
                            "status": "past_due"}},
    })
    db_session.refresh(billed_org)
    assert billed_org.billing_status == "past_due"


def test_subscription_deleted_returns_org_to_trial(db_session, billed_org):
    """Existing behaviour from the router this replaced, unchanged."""
    billed_org.stripe_subscription_id = "sub_1"
    db_session.commit()
    billing_webhooks.handle(db_session, {
        "id": "evt_sub_d", "type": "customer.subscription.deleted",
        "created": NOW,
        "data": {"object": {"id": "sub_1", "customer": CUST,
                            "status": "canceled"}},
    })
    db_session.refresh(billed_org)
    assert billed_org.billing_status == "canceled"
    assert billed_org.plan == "trial"
    assert billed_org.stripe_subscription_id is None


def test_checkout_completed_behaviour_is_unchanged(db_session):
    """P0 moved this handler; it must do exactly what it did before."""
    org = Organization(name="New Co", slug="new-co", plan="trial",
                       billing_status="trialing")
    db_session.add(org)
    db_session.commit()

    billing_webhooks.handle(db_session, {
        "id": "evt_co", "type": "checkout.session.completed", "created": NOW,
        "data": {"object": {
            "id": "cs_1", "customer": "cus_NEW", "subscription": "sub_NEW",
            "metadata": {"org_id": org.id, "plan": "growth", "interval": "month"},
        }},
    })
    db_session.refresh(org)
    assert org.plan == "growth"
    assert org.billing_status == "active"
    assert org.stripe_customer_id == "cus_NEW"
    assert org.stripe_subscription_id == "sub_NEW"
    assert org.stripe_plan_interval == "month"


# ── no pricing was changed by any of this ───────────────────────────────────

def test_p0_never_writes_a_price(db_session, billed_org):
    """P0 is reliability only. Nothing here may alter what a customer pays."""
    before = (billed_org.plan, billed_org.stripe_plan_interval)
    billing_webhooks.handle(db_session, _invoice_event(event_id="evt_price"))
    db_session.refresh(billed_org)
    assert (billed_org.plan, billed_org.stripe_plan_interval) == before
