"""
Stripe webhook processing. THE AUTHORITATIVE SOURCE OF BILLING STATE.

═══════════════════════════════════════════════════════════════════════════
A BROWSER REDIRECT IS NOT PROOF OF PAYMENT
═══════════════════════════════════════════════════════════════════════════

Checkout's success URL is a page anybody can visit. The customer can close the
tab before it loads, a card can fail after the session completes, and a
subscription can lapse months later with no browser involved at all. So no
billing state is ever written from a redirect: the webhook writes it, and the
webhook is signature-verified.

═══════════════════════════════════════════════════════════════════════════
IDEMPOTENCY IS THE WHOLE DESIGN, NOT A FEATURE
═══════════════════════════════════════════════════════════════════════════

Stripe retries. On a timeout, on any non-2xx, and on its own schedule for up
to three days - and it can deliver the same event twice after a success. One
card charge also produces SEVERAL DIFFERENT events describing the same money:
invoice.paid, invoice.payment_succeeded, payment_intent.succeeded,
charge.succeeded.

Naive handling therefore double-counts revenue and pays commission two, three
or four times for one payment. Three independent guards prevent that:

  1. `billing_events.stripe_event_id` is UNIQUE. A retried event is recorded
     as a duplicate and does nothing.
  2. `billing_payments.collection_reference` is UNIQUE, and the reference is
     derived from the INVOICE (falling back to payment intent, then charge) -
     never from the event id. Four different events about one invoice all
     resolve to one reference and therefore one payment row.
  3. `compensation_entries` is unique on (opportunity, payee, level,
     collection_reference), so even a direct double-call to earn() returns
     what already exists.

Guard 2 is the one that matters most and the one that is easiest to get
wrong. Keying a payment on the event id looks like idempotency and is not:
every related event has a different event id.

═══════════════════════════════════════════════════════════════════════════
NEVER LOGGED, NEVER STORED
═══════════════════════════════════════════════════════════════════════════

The signing secret, the API key, card details, and the raw event payload. The
payload is large, changes shape between API versions, and carries customer PII
we have no reason to hold a second copy of. Stripe OBJECT IDS (evt_, in_, pi_,
sub_, cus_) are public identifiers and are safe to store and log; they are what
makes the ledger answerable without keeping payloads.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.billing_models import (BillingEvent, BillingEventOutcome,
                                       BillingInvoice, BillingPayment,
                                       SubscriptionStatus)
from app.models.models import Organization
from app.services import billing_compensation

log = logging.getLogger(__name__)


# Events we act on. Anything else is recorded as IGNORED rather than dropped:
# "we received this and chose not to act" is a different and more useful fact
# than silence, especially when someone is asking why a subscription did not
# update.
HANDLED_EVENTS = (
    "checkout.session.completed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "invoice.paid",
    "invoice.payment_succeeded",
    "invoice.payment_failed",
    "charge.refunded",
)

# Events that describe MONEY ARRIVING. Deliberately only the invoice pair:
# payment_intent.succeeded and charge.succeeded describe the same money and
# are recorded as IGNORED, because for subscription billing the invoice is the
# one object every related event agrees on. Adding them here would not create
# duplicate PAYMENTS (the collection reference prevents that) but it would add
# a second path into the same code for no benefit.
PAYMENT_EVENTS = ("invoice.paid", "invoice.payment_succeeded")


def _ts(value: Any) -> Optional[datetime]:
    """Stripe's unix timestamps -> naive UTC, matching the rest of the schema."""
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError, OSError):
        return None


def _org_by_customer(db: Session, customer_id: Optional[str]) -> Optional[Organization]:
    if not customer_id:
        return None
    return (db.query(Organization)
            .filter(Organization.stripe_customer_id == customer_id)
            .first())


def _org_by_subscription(db: Session, sub_id: Optional[str]) -> Optional[Organization]:
    if not sub_id:
        return None
    return (db.query(Organization)
            .filter(Organization.stripe_subscription_id == sub_id)
            .first())


def claim_event(db: Session, event_id: str, event_type: str) -> Tuple[Optional[BillingEvent], bool]:
    """Record this event, or discover it has already been handled.

    Returns (event_row, is_new). THE UNIQUE INSERT IS THE LOCK. Checking for an
    existing row and then inserting would leave a window in which two
    concurrent deliveries of the same event both see nothing and both proceed -
    which is exactly the race a webhook endpoint gets, because Stripe's retry
    can arrive while the first attempt is still running.

    A duplicate is a SUCCESS from the caller's point of view: the event has
    been handled, so the endpoint must return 2xx or Stripe will keep retrying
    it for three days.
    """
    row = BillingEvent(
        stripe_event_id=event_id,
        event_type=event_type,
        outcome=BillingEventOutcome.PROCESSED,
        received_at=datetime.utcnow(),
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = (db.query(BillingEvent)
                    .filter(BillingEvent.stripe_event_id == event_id)
                    .first())
        if existing is None:
            # The insert failed for some reason OTHER than this event id
            # already being present. Returning (None, False) here would have
            # the caller report "already handled" for an event that was never
            # handled at all - a 200 that tells Stripe to stop retrying
            # something we silently dropped. Unreachable on the current schema,
            # where this row's only unique constraint is the event id, but
            # "unreachable" is a property of today's schema and not of this
            # function. Re-raise so the caller's error path records a FAILED
            # event instead of a false duplicate.
            raise
        return existing, False
    return row, True


def record_payment(db: Session, *, org: Organization, invoice: dict,
                   is_initial: bool) -> Tuple[Optional[BillingPayment], bool]:
    """One row per underlying payment. Returns (payment, created).

    `created` False means this money was already banked - by a sibling event,
    or by a retry - and the caller must not earn compensation again.
    """
    reference = billing_compensation.collection_reference_for(
        stripe_invoice_id=invoice.get("id"),
        stripe_payment_intent_id=invoice.get("payment_intent"),
        stripe_charge_id=invoice.get("charge"),
    )
    if not reference:
        return None, False

    existing = (db.query(BillingPayment)
                .filter(BillingPayment.collection_reference == reference)
                .first())
    if existing:
        return existing, False

    amount = invoice.get("amount_paid")
    if amount is None:
        amount = invoice.get("amount_due") or 0

    payment = BillingPayment(
        organization_id=org.id,
        platform_id=getattr(org, "platform_id", None),
        collection_reference=reference,
        stripe_invoice_id=invoice.get("id"),
        stripe_payment_intent_id=invoice.get("payment_intent"),
        stripe_charge_id=invoice.get("charge"),
        currency=(invoice.get("currency") or "usd"),
        amount_cents=int(amount or 0),
        is_initial=bool(is_initial),
        collected_at=_ts(invoice.get("status_transitions", {}).get("paid_at"))
        or datetime.utcnow(),
    )
    db.add(payment)
    try:
        db.flush()
    except IntegrityError:
        # Lost a race with a sibling event. The other one banked it.
        db.rollback()
        return (db.query(BillingPayment)
                .filter(BillingPayment.collection_reference == reference)
                .first()), False
    return payment, True


def upsert_invoice(db: Session, *, org: Organization, invoice: dict) -> BillingInvoice:
    """Mirror a Stripe invoice locally so revenue reporting has a source.

    God Mode's MRR / Collected / Past-Due panels render as honest empty
    placeholders annotated "needs invoices table" - this is that table. Mirrored
    rather than fetched live so a dashboard is not as slow, or as available, as
    Stripe's API on its worst day.
    """
    inv_id = invoice.get("id")
    row = (db.query(BillingInvoice)
           .filter(BillingInvoice.stripe_invoice_id == inv_id)
           .first())
    if row is None:
        row = BillingInvoice(stripe_invoice_id=inv_id,
                             organization_id=org.id)
        db.add(row)

    row.organization_id = org.id
    row.platform_id = getattr(org, "platform_id", None)
    row.stripe_subscription_id = invoice.get("subscription")
    row.stripe_customer_id = invoice.get("customer")
    row.status = invoice.get("status")
    row.currency = invoice.get("currency") or "usd"
    row.amount_due_cents = invoice.get("amount_due")
    row.amount_paid_cents = invoice.get("amount_paid")
    row.hosted_invoice_url = invoice.get("hosted_invoice_url")
    row.period_start = _ts(invoice.get("period_start"))
    row.period_end = _ts(invoice.get("period_end"))
    row.paid_at = _ts((invoice.get("status_transitions") or {}).get("paid_at"))
    row.billing_plan_key = getattr(org, "billing_plan_key", None)
    row.billing_interval = getattr(org, "stripe_plan_interval", None)
    db.flush()
    return row


def apply_subscription(db: Session, org: Organization, sub: dict) -> None:
    """Copy a Stripe subscription's state onto the organization.

    Status is Stripe's own vocabulary, passed through unchanged. Inventing a
    parallel vocabulary would mean a translation table that has to stay correct
    forever, and the first status we failed to map would quietly become
    "unknown" on a screen somebody bills from.
    """
    org.stripe_subscription_id = sub.get("id") or org.stripe_subscription_id
    status = sub.get("status")
    if status:
        org.billing_status = status
    org.billing_cancel_at_period_end = bool(sub.get("cancel_at_period_end"))
    period_end = _ts(sub.get("current_period_end"))
    if period_end:
        org.billing_current_period_end = period_end
    trial_end = _ts(sub.get("trial_end"))
    org.billing_trial_end = trial_end

    items = ((sub.get("items") or {}).get("data") or [])
    price_id = None
    if items:
        price = (items[0] or {}).get("price") or {}
        recurring = price.get("recurring") or {}
        interval = recurring.get("interval")
        if interval:
            org.stripe_plan_interval = interval
        price_id = price.get("id") if isinstance(price, dict) else None

    # ══════════════════════════════════════════════════════════════════════
    # THE PLAN IS RESOLVED FROM THE STRIPE PRICE ID FIRST. METADATA IS A
    # FALLBACK, NOT THE SOURCE.
    # ══════════════════════════════════════════════════════════════════════
    #
    # THE PRICE IS THE DURABLE IDENTIFIER. It is what the customer is actually
    # being charged against, it is created by us and mapped in the brand's own
    # catalogue, and it cannot be edited into something else from the Stripe
    # dashboard the way a metadata string can.
    #
    # This used to read metadata ONLY, which broke in a specific and important
    # case: when a SUBSCRIPTION SCHEDULE advances to its second phase - the
    # mechanism a deferred downgrade uses - the resulting subscription.updated
    # event is not guaranteed to carry our `plan` metadata. The price on the
    # item always changes. So a scheduled downgrade would have taken effect at
    # Stripe and silently failed to sync here, leaving the customer paying the
    # lower price while every screen still showed the higher plan.
    #
    # Either way the answer is validated against THIS BRAND's catalogue before
    # anything is written, so an unrecognised value is ignored rather than
    # stored.
    resolved = None
    from app.services import billing_catalog
    platform_id = getattr(org, "platform_id", None)

    if price_id:
        resolved = billing_catalog.resolve_plan_by_price_id(db, platform_id, price_id)

    if resolved is None:
        meta_plan = (sub.get("metadata") or {}).get("plan")
        if meta_plan:
            resolved = billing_catalog.resolve_plan(db, platform_id, meta_plan)
            if resolved is None:
                log.warning(
                    "billing_webhook: subscription %s carries plan metadata %r "
                    "that is not in platform %s's catalogue - ignoring it "
                    "rather than writing an unrecognised plan onto org %s",
                    sub.get("id"), meta_plan, platform_id, org.id)

    if resolved is not None:
        previous = org.billing_plan_key
        org.billing_plan_key = resolved.key
        org.plan = resolved.key

        # A PENDING CHANGE THAT HAS NOW LANDED IS NO LONGER PENDING.
        #
        # When the schedule's second phase starts, the subscription arrives on
        # the target price and this is where that becomes true locally. Clearing
        # the markers here - rather than on a timer, or on the customer next
        # loading the page - is what keeps "what plan am I on" answerable from
        # one place.
        if org.billing_pending_plan_key and org.billing_pending_plan_key == resolved.key:
            log.info("billing_webhook: scheduled change to %s is now in effect "
                     "for org %s (was %s)", resolved.key, org.id, previous)
            org.billing_pending_plan_key = None
            org.billing_pending_effective_at = None
            org.stripe_schedule_id = None


def handle_event(db: Session, event: dict) -> dict:
    """Process one verified Stripe event. Returns a summary for the response.

    NEVER RAISES FOR A BUSINESS REASON. An event about a customer we cannot
    place, a subscription for a deleted organization, or a payment against an
    unsold org are all ordinary. Raising would make Stripe retry a permanent
    condition every few hours for three days.
    """
    event_id = event.get("id")
    event_type = event.get("type") or ""
    obj = ((event.get("data") or {}).get("object") or {})

    row, is_new = claim_event(db, event_id, event_type)
    if not is_new:
        # Already handled. 2xx, do nothing, and say so.
        return {"ok": True, "duplicate": True, "event_type": event_type,
                "detail": "Event already processed."}

    result = {"ok": True, "duplicate": False, "event_type": event_type,
              "earned_compensation": False}

    try:
        if event_type not in HANDLED_EVENTS:
            row.outcome = BillingEventOutcome.IGNORED
            row.detail = "No handler for this event type."
            row.processed_at = datetime.utcnow()
            db.commit()
            result["ignored"] = True
            return result

        # ── Resolve the organization ──────────────────────────────────────
        customer_id = obj.get("customer")
        org = _org_by_customer(db, customer_id)
        if org is None:
            org = _org_by_subscription(db, obj.get("subscription") or obj.get("id"))

        row.stripe_object_id = obj.get("id")
        row.stripe_customer_id = customer_id
        row.stripe_subscription_id = obj.get("subscription") or (
            obj.get("id") if event_type.startswith("customer.subscription") else None)

        if org is None:
            row.outcome = BillingEventOutcome.IGNORED
            row.detail = ("No organization matches this Stripe customer or "
                          "subscription. Recorded, not actioned.")
            row.processed_at = datetime.utcnow()
            db.commit()
            result["ignored"] = True
            result["detail"] = row.detail
            return result

        row.organization_id = org.id
        row.platform_id = getattr(org, "platform_id", None)

        # ── Dispatch ──────────────────────────────────────────────────────
        if event_type == "checkout.session.completed":
            _handle_checkout_completed(db, org, obj)

        elif event_type in ("customer.subscription.created",
                            "customer.subscription.updated"):
            apply_subscription(db, org, obj)

        elif event_type == "customer.subscription.deleted":
            _handle_subscription_deleted(db, org, obj)

        elif event_type in PAYMENT_EVENTS:
            earned = _handle_invoice_paid(db, org, obj, row)
            result["earned_compensation"] = earned

        elif event_type == "invoice.payment_failed":
            _handle_payment_failed(db, org, obj)

        elif event_type == "charge.refunded":
            _handle_charge_refunded(db, org, obj, row)

        row.processed_at = datetime.utcnow()
        db.commit()
        return result

    except Exception as exc:                       # pragma: no cover - defensive
        db.rollback()
        # Re-record the failure. The event row was rolled back with everything
        # else, so it is written again as FAILED - otherwise the event would
        # look unseen and a retry would repeat a failing path with no trace of
        # the first attempt.
        try:
            failed = BillingEvent(
                stripe_event_id=event_id, event_type=event_type,
                outcome=BillingEventOutcome.FAILED,
                detail="Handler error: %s" % str(exc)[:400],
                received_at=datetime.utcnow(), processed_at=datetime.utcnow())
            db.add(failed)
            db.commit()
        except Exception:
            db.rollback()
        log.exception("billing_webhook: handler failed for %s (%s)",
                      event_id, event_type)
        # 200 with ok=False: retrying a deterministic bug does not fix it, and
        # a 500 would have Stripe hammer the same failing path for days.
        return {"ok": False, "duplicate": False, "event_type": event_type,
                "detail": "Handler error recorded."}


# ──────────────────────────────────────────────────────────────────────────────
# Individual handlers
# ──────────────────────────────────────────────────────────────────────────────

def _handle_checkout_completed(db: Session, org: Organization, obj: dict) -> None:
    """A checkout finished. Records ids only.

    Deliberately does NOT mark anything paid. The subscription and invoice
    events carry the authoritative state, and they arrive for renewals too, so
    letting them own it means one code path rather than two that must agree.
    """
    if obj.get("customer") and not org.stripe_customer_id:
        org.stripe_customer_id = obj.get("customer")
    sub_id = obj.get("subscription")
    if sub_id:
        org.stripe_subscription_id = sub_id

    meta_plan = (obj.get("metadata") or {}).get("plan")
    meta_interval = (obj.get("metadata") or {}).get("interval")
    if meta_plan:
        from app.services import billing_catalog
        resolved = billing_catalog.resolve_plan(
            db, getattr(org, "platform_id", None), meta_plan)
        if resolved is not None:
            org.billing_plan_key = resolved.key
            org.plan = resolved.key
    if meta_interval in ("month", "year"):
        org.stripe_plan_interval = meta_interval


def _handle_subscription_deleted(db: Session, org: Organization, obj: dict) -> None:
    """The subscription ended. CANCELLATION IS NOT DELETION.

    Billing state is updated and the subscription id cleared so a new one can
    be created. Nothing else is touched: not the workspace, not the users, not
    the deal, proposal, invoice, payment, compensation, implementation or audit
    history. Customer offboarding is a separate, deliberate decision made
    through customer_lifecycle.py - a card expiring must never delete a
    customer.
    """
    org.billing_status = SubscriptionStatus.CANCELED
    org.stripe_subscription_id = None
    org.billing_cancel_at_period_end = False
    org.billing_pending_plan_key = None
    # `plan` and `billing_plan_key` are deliberately LEFT AS THEY WERE. What
    # they bought is a historical fact, and entitlement decisions are made from
    # billing_status by a policy that is currently unset - not by silently
    # rewriting the plan to something they never chose.


def _handle_invoice_paid(db: Session, org: Organization, obj: dict,
                         event_row: BillingEvent) -> bool:
    """Money arrived. Bank it once, then attempt compensation once."""
    upsert_invoice(db, org=org, invoice=obj)

    billing_reason = obj.get("billing_reason") or ""
    is_initial = billing_reason in ("subscription_create", "manual", "")

    payment, created = record_payment(db, org=org, invoice=obj,
                                      is_initial=is_initial)
    if payment is None:
        event_row.detail = "Invoice carried no usable payment identifier."
        return False

    if not created:
        # A sibling event already banked this money. Recording the reason
        # matters: without it this looks identical to a payment that earned
        # nothing because no rule applied.
        event_row.detail = ("Payment already recorded under %s - no double "
                            "count." % payment.collection_reference)
        return False

    outcome = billing_compensation.earn_for_payment(db, payment, commit=False)
    event_row.earned_compensation = bool(outcome.get("earned"))
    event_row.compensation_note = (
        "Earned %d compensation entr%s." % (outcome["entries"],
                                            "y" if outcome["entries"] == 1 else "ies")
        if outcome.get("earned")
        else "No compensation earned: %s" % (outcome.get("reason") or "unknown")
    )
    return bool(outcome.get("earned"))


def _handle_payment_failed(db: Session, org: Organization, obj: dict) -> None:
    """A payment failed.

    Records the invoice and moves the organization to past_due. IT WITHDRAWS
    NOTHING. Whether a past-due customer loses access, and after how long, is
    an unset brand policy - see billing_policy.past_due_behavior. Suspending on
    a grace period nobody chose would cut off a paying customer over a card
    that expired on a Friday.
    """
    upsert_invoice(db, org=org, invoice=obj)
    org.billing_status = SubscriptionStatus.PAST_DUE
    # NO compensation. Failed payment is not collected funds, and earn() is not
    # called at all here - there is no path from this handler to the engine.


def _handle_charge_refunded(db: Session, org: Organization, obj: dict,
                            event_row: BillingEvent) -> None:
    """Money went back. Recorded, never clawed back from anyone's commission."""
    reference = billing_compensation.collection_reference_for(
        stripe_invoice_id=obj.get("invoice"),
        stripe_payment_intent_id=obj.get("payment_intent"),
        stripe_charge_id=obj.get("id"),
    )
    payment = None
    if reference:
        payment = (db.query(BillingPayment)
                   .filter(BillingPayment.collection_reference == reference)
                   .first())
    if payment is None:
        event_row.detail = ("Refund for a charge with no local payment row - "
                            "recorded only.")
        return

    outcome = billing_compensation.record_refund(
        db, payment, refunded_cents=int(obj.get("amount_refunded") or 0),
        commit=False)
    event_row.detail = outcome["explanation"]
