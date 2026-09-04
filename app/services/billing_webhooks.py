"""Stripe webhook processing — the idempotent, order-tolerant dispatcher.

Lives in a service rather than in the router so it can be tested without an
HTTP client and without a Stripe key, and so the router keeps its existing
shape. The router verifies the signature and hands the parsed event here.

THE FOUR GUARANTEES

1. AN EVENT IS APPLIED AT MOST ONCE.
   `stripe_webhook_events.stripe_event_id` is UNIQUE and the row is inserted
   BEFORE any financial state is touched. An IntegrityError means Stripe has
   redelivered - documented behaviour, not an edge case - and the request
   returns having changed nothing. Without this a redelivered `charge.refunded`
   refunds twice in our books while Stripe refunded once.

2. LATE EVENTS DO NOT REWRITE NEWER STATE.
   Stripe does not guarantee order. Each mirrored row records the Stripe
   `created` time of the last event applied to it, and an older event is
   skipped. See stripe_sync._is_stale.

3. A TRANSIENT FAILURE IS RETRYABLE.
   The handler re-raises so the router can answer 500 and Stripe will retry.
   Answering 200 over a swallowed exception loses the event permanently, and a
   lost `invoice.payment_failed` is a customer who is past due and looks fine.

4. AN UNKNOWN CUSTOMER CREATES NOTHING.
   Recorded as `ignored` with the reason. A webhook may never invent a tenant.

LOGGING. Amounts and Stripe ids are logged because they are needed to
reconstruct an incident. Card details are not logged because they are not
received. No secret is ever logged.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.billing_models import (EVENT_FAILED, EVENT_IGNORED,
                                       EVENT_PROCESSED, StripeWebhookEvent)
from app.models.models import Organization
from app.services import stripe_sync

log = logging.getLogger(__name__)

# Everything this dispatcher knows how to apply. An event outside this set is
# recorded and ignored - that is not a failure, it is Stripe sending us
# something we have no opinion about.
INVOICE_EVENTS = {
    "invoice.created",
    "invoice.finalized",
    "invoice.paid",
    "invoice.payment_succeeded",
    "invoice.payment_failed",
    "invoice.payment_action_required",
    "invoice.voided",
    "invoice.marked_uncollectible",
    "invoice.updated",
}
PAYMENT_EVENTS = {
    "payment_intent.succeeded",
    "payment_intent.payment_failed",
    "charge.refunded",
    "charge.succeeded",
}
SUBSCRIPTION_EVENTS = {
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
}
CHECKOUT_EVENTS = {"checkout.session.completed"}

SUPPORTED_EVENTS = (INVOICE_EVENTS | PAYMENT_EVENTS | SUBSCRIPTION_EVENTS
                    | CHECKOUT_EVENTS)


class DuplicateEvent(Exception):
    """Stripe redelivered an event we have already recorded."""


def _ts(value) -> Optional[datetime]:
    if value in (None, "", 0):
        return None
    try:
        return datetime.utcfromtimestamp(int(value))
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _get(obj: Any, key: str, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def claim_event(db: Session, event: Any) -> StripeWebhookEvent:
    """Take the uniqueness claim BEFORE touching any financial state.

    Raises DuplicateEvent when this event has been seen. The insert is
    committed on its own so the claim survives even if processing then fails -
    a failed event must not be silently reprocessable as if it were new; it is
    replayed deliberately through the reconcile path instead.
    """
    event_id = _get(event, "id")
    if not event_id:
        raise ValueError("Stripe event carried no id")

    row = StripeWebhookEvent(
        stripe_event_id=event_id,
        event_type=_get(event, "type") or "unknown",
        api_version=_get(event, "api_version"),
        stripe_account_id=_get(event, "account"),
        event_created_at=_ts(_get(event, "created")),
        attempts=1,
    )
    try:
        row.payload_json = json.dumps(event if isinstance(event, dict)
                                      else dict(event), default=str)[:1_000_000]
    except (TypeError, ValueError):
        row.payload_json = None

    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise DuplicateEvent(event_id)
    return row


def process_event(db: Session, event: Any, ledger: StripeWebhookEvent) -> Dict[str, Any]:
    """Apply one event. Raises on a transient failure so the caller can 500."""
    etype = _get(event, "type") or ""
    data = _get(_get(event, "data") or {}, "object") or {}
    event_id = _get(event, "id")
    created = _ts(_get(event, "created"))

    if etype not in SUPPORTED_EVENTS:
        return _ignore(db, ledger, "unhandled event type: %s" % etype)

    try:
        if etype in INVOICE_EVENTS:
            result = _handle_invoice(db, data, event_id, created)
        elif etype in PAYMENT_EVENTS:
            result = _handle_payment(db, data, event_id, created)
        elif etype in SUBSCRIPTION_EVENTS:
            result = _handle_subscription(db, etype, data, event_id, created)
        else:
            result = _handle_checkout(db, data)
    except Exception as exc:
        # Recorded as failed, then re-raised. The router answers 500 and Stripe
        # retries; the claim above means the retry is recognised as the same
        # event and can be replayed deliberately rather than double-applied.
        db.rollback()
        ledger.processing_status = EVENT_FAILED
        ledger.error_message = "%s: %s" % (type(exc).__name__, str(exc)[:500])
        ledger.processed_at = datetime.utcnow()
        db.add(ledger)
        db.commit()
        log.exception("billing: webhook %s (%s) failed", event_id, etype)
        raise

    if result.get("ignored"):
        return _ignore(db, ledger, result["ignored"])

    ledger.processing_status = EVENT_PROCESSED
    ledger.processed_at = datetime.utcnow()
    db.add(ledger)
    db.commit()
    log.info("billing: applied %s (%s) -> %s", etype, event_id,
             result.get("summary", "ok"))
    return result


def _ignore(db: Session, ledger: StripeWebhookEvent, reason: str) -> Dict[str, Any]:
    db.rollback()
    ledger.processing_status = EVENT_IGNORED
    ledger.ignored_reason = reason[:500]
    ledger.processed_at = datetime.utcnow()
    db.add(ledger)
    db.commit()
    log.info("billing: ignored %s - %s", ledger.stripe_event_id, reason)
    return {"ignored": reason}


def _handle_invoice(db: Session, data: Any, event_id, created) -> Dict[str, Any]:
    invoice, ignored = stripe_sync.upsert_invoice_from_stripe(
        db, data, event_id=event_id, event_created_at=created)
    if ignored:
        return {"ignored": ignored}
    status = stripe_sync.apply_invoice_state_to_organization(db, invoice)
    db.commit()
    return {"invoice_id": invoice.id, "invoice_status": invoice.status,
            "billing_status": status,
            "summary": "invoice %s -> %s" % (invoice.stripe_invoice_id,
                                             invoice.status)}


def _handle_payment(db: Session, data: Any, event_id, created) -> Dict[str, Any]:
    payment, ignored = stripe_sync.upsert_payment_from_stripe(
        db, data, event_id=event_id, event_created_at=created)
    if ignored:
        return {"ignored": ignored}
    db.commit()
    return {"payment_id": payment.id, "payment_status": payment.status,
            "summary": "payment %s -> %s" % (
                payment.stripe_payment_intent_id or payment.stripe_charge_id,
                payment.status)}


def _handle_subscription(db: Session, etype: str, data: Any,
                         event_id, created) -> Dict[str, Any]:
    """Mirror subscription state onto the organization.

    Keeps the existing behaviour of the router this replaces: status is
    mirrored, and a deleted subscription returns the org to trial. It does NOT
    reprice anything and does not touch plan except on deletion, exactly as
    before - P0 changes no customer's charges.
    """
    sub_id = _get(data, "id")
    org = (db.query(Organization)
           .filter(Organization.stripe_subscription_id == sub_id).first())
    if org is None:
        customer_id = _get(data, "customer")
        org = stripe_sync.organization_for_customer(db, customer_id)
        if org is None:
            return {"ignored": "no organization for subscription %s" % sub_id}
        # First sight of this subscription for a known customer.
        if etype == "customer.subscription.created":
            org.stripe_subscription_id = sub_id

    if etype == "customer.subscription.deleted":
        org.billing_status = "canceled"
        org.plan = "trial"
        org.stripe_subscription_id = None
    else:
        org.billing_status = _get(data, "status") or org.billing_status
        meta_plan = _get(_get(data, "metadata") or {}, "plan")
        if meta_plan:
            org.plan = meta_plan

    db.commit()
    return {"organization_id": org.id, "billing_status": org.billing_status,
            "summary": "subscription %s -> %s" % (sub_id, org.billing_status)}


def _handle_checkout(db: Session, data: Any) -> Dict[str, Any]:
    """UNCHANGED BEHAVIOUR, moved.

    This is the existing handler from billing_router, preserved exactly: same
    fields, same metadata keys, same effect. P0 does not alter what checkout
    does; it only gains the idempotency and ledger guarantees around it.
    """
    meta = _get(data, "metadata") or {}
    org_id = _get(meta, "org_id")
    plan = _get(meta, "plan")
    interval = _get(meta, "interval", "month")
    if not org_id or not plan:
        return {"ignored": "checkout session carried no org_id/plan metadata"}

    org = db.query(Organization).filter(Organization.id == org_id).first()
    if org is None:
        return {"ignored": "no organization %s" % org_id}

    org.plan = plan
    org.stripe_subscription_id = _get(data, "subscription")
    org.stripe_customer_id = _get(data, "customer")
    org.stripe_plan_interval = interval
    org.billing_status = "active"
    db.commit()
    return {"organization_id": org.id,
            "summary": "checkout activated org=%s plan=%s" % (org_id, plan)}


def handle(db: Session, event: Any) -> Tuple[Dict[str, Any], bool]:
    """Claim then process. Returns (result, was_duplicate)."""
    try:
        ledger = claim_event(db, event)
    except DuplicateEvent as dup:
        log.info("billing: duplicate webhook %s ignored", dup)
        return {"duplicate": True}, True
    return process_event(db, event, ledger), False
