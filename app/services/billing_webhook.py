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

    _capture_card_summary(org, invoice)

    db.flush()
    return row


def _capture_card_summary(org: Organization, invoice: dict) -> None:
    """Remember WHICH CARD paid, and nothing else about it.

    Brand, last four, expiry. That is what lets a customer recognise the card
    on file; it is not a card number, not a CVC, and not a token that could
    charge anything. The instrument stays at Stripe and is only ever changed
    through the Portal.

    DEFENSIVE ON EVERY LEVEL, because Stripe's invoice payload does not
    guarantee any of this. Where the shape is not what we expect, the existing
    values are left alone rather than blanked: a screen that says "Visa ....4242"
    from last month is far better than one that silently forgets the card
    because one webhook arrived without a charge expanded. And nothing is ever
    invented - no default brand, no placeholder digits.
    """
    try:
        charge = invoice.get("charge")
        details = None
        if isinstance(charge, dict):
            details = (charge.get("payment_method_details") or {})
        if not details:
            pi = invoice.get("payment_intent")
            if isinstance(pi, dict):
                charges = ((pi.get("charges") or {}).get("data") or [])
                if charges and isinstance(charges[0], dict):
                    details = charges[0].get("payment_method_details") or {}
        card = (details or {}).get("card") or {}
        last4 = card.get("last4")
        if not last4:
            return
        org.billing_card_brand = card.get("brand") or org.billing_card_brand
        org.billing_card_last4 = str(last4)
        if card.get("exp_month"):
            org.billing_card_exp_month = int(card["exp_month"])
        if card.get("exp_year"):
            org.billing_card_exp_year = int(card["exp_year"])
    except Exception:                                    # pragma: no cover
        log.debug("billing_webhook: no usable card summary on invoice %s",
                  invoice.get("id"))


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
    trial_end = _ts(sub.get("trial_end"))
    org.billing_trial_end = trial_end

    items = ((sub.get("items") or {}).get("data") or [])

    # ══════════════════════════════════════════════════════════════════════
    # WHEN THE NEXT BILL LANDS — READ FROM WHEREVER THIS API VERSION PUTS IT
    # ══════════════════════════════════════════════════════════════════════
    #
    # Caught on the first real subscription test: an ACTIVE, correctly-charged
    # subscription showed a blank "Next bill" everywhere. Nothing was broken at
    # Stripe and nothing was broken in the webhook's plumbing — the field had
    # MOVED. Stripe's 2025-03-31 API version removed `current_period_end` from
    # the Subscription object and put a period on each subscription ITEM, since
    # a multi-item subscription can have items on different periods.
    #
    # No `api_version` is pinned anywhere in this codebase, so the account's
    # default version decides the shape and can change under us without a
    # deploy. Reading one location and accepting NULL when it is absent is how
    # a renewal date silently disappears from a billing screen. So: take the
    # subscription's own field when this version still has it, otherwise read
    # it from the item — which item, and why it matters, is settled next.

    # ══════════════════════════════════════════════════════════════════════
    # WHICH ITEM IS THE PLAN — because a subscription now has several
    # ══════════════════════════════════════════════════════════════════════
    #
    # This read `items[0]` and nothing else, which was correct for exactly as
    # long as every subscription had exactly one item. A recurring add-on is an
    # ITEM on the customer's existing subscription — that is the whole design,
    # and the alternative was a second subscription — so the moment anybody
    # bought one there were two, and STRIPE DOES NOT PROMISE AN ORDER.
    #
    # With the add-on first, `items[0]` would have meant: the interval read
    # from the add-on (an annual customer with a monthly add-on shown as
    # monthly), the price resolving to no plan, and the commitment left
    # unresolved — so a scheduled downgrade landing without `plan` metadata,
    # which is the case the price-first resolution was built for, would have
    # silently failed to sync.
    #
    # So the plan item is the one whose price this brand's catalogue
    # recognises, whatever position it sits in. Only if NONE resolve does this
    # fall back to the first item, which preserves the old behaviour for the
    # case it was right for: a Custom deal billed against an inline price that
    # is deliberately in no catalogue.
    from app.services import billing_catalog
    platform_id = getattr(org, "platform_id", None)

    def _price_of(item):
        price = (item or {}).get("price") or {}
        return price if isinstance(price, dict) else {}

    plan_item = None
    resolved = None
    for it in items:
        pid = _price_of(it).get("id")
        if not pid:
            continue
        candidate = billing_catalog.resolve_plan_by_price_id(db, platform_id, pid)
        if candidate is not None:
            plan_item, resolved = it, candidate
            break
    if plan_item is None and items:
        plan_item = items[0]

    price_id = _price_of(plan_item).get("id") if plan_item else None
    interval = (_price_of(plan_item).get("recurring") or {}).get("interval")
    if interval:
        org.stripe_plan_interval = interval

    # WHEN THE NEXT BILL LANDS. The PLAN item's period is the answer whenever
    # this version puts the period on items and we know which item is the plan:
    # an add-on added mid-period can carry its own dates, and taking the
    # furthest-out across everything would push the renewal date out by
    # whatever the newest add-on happens to say. `max()` remains the fallback
    # for a subscription whose plan item we could not identify.
    period_end = _ts(sub.get("current_period_end"))
    if period_end is None and resolved is not None:
        # Only when the plan item was IDENTIFIED by its price. A `plan_item`
        # that is merely the first of several unrecognised ones is not a plan
        # item, and trusting its dates would be the arbitrary reading this
        # change exists to remove.
        period_end = _ts((plan_item or {}).get("current_period_end"))
    if period_end is None:
        item_ends = [_ts((it or {}).get("current_period_end")) for it in items]
        item_ends = [t for t in item_ends if t is not None]
        if item_ends:
            period_end = max(item_ends)
    if period_end:
        org.billing_current_period_end = period_end

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
    # `resolved` is already the plan whose price we matched above, if any — the
    # search for the plan ITEM and the resolution of the PLAN are the same
    # question asked once, rather than twice with a chance of disagreeing.
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

        # ── AND WHICH RATE OF THAT PLAN ───────────────────────────────────
        #
        # The same price that just told us the tier also tells us the
        # commitment, and it is the only thing that can: Starter's committed
        # $500/mo and month-to-month $597/mo are both the MONTH interval and
        # differ only by Price. Resolving it here — in the one place that
        # already trusts the price over metadata — means every reader can price
        # the subscription at the rate the customer is genuinely on instead of
        # falling through to the term rate and understating MRR by the size of
        # a discount that was never earned.
        #
        # Only overwritten when the price actually resolves to one of this
        # plan's commitments. A price we cannot classify leaves whatever was
        # already known alone rather than blanking it, because "we stopped
        # recognising the price" is not evidence the customer changed terms.
        commitment = billing_catalog.commitment_for_price_id(resolved, price_id)
        if commitment:
            if (org.billing_commitment
                    and org.billing_commitment != commitment):
                log.info("billing_webhook: org %s moved from %s to %s on "
                         "subscription %s", org.id, org.billing_commitment,
                         commitment, sub.get("id"))
            org.billing_commitment = commitment
        elif price_id:
            log.warning(
                "billing_webhook: subscription %s is on price %s, which is "
                "not one of plan %r's mapped prices - leaving the recorded "
                "commitment (%r) unchanged for org %s",
                sub.get("id"), price_id, resolved.key,
                org.billing_commitment, org.id)

        # A PENDING CHANGE THAT HAS NOW LANDED IS NO LONGER PENDING.
        #
        # When the schedule's second phase starts, the subscription arrives on
        # the target price and this is where that becomes true locally. Clearing
        # the markers here - rather than on a timer, or on the customer next
        # loading the page - is what keeps "what plan am I on" answerable from
        # one place.
        # THE COMMITMENT IS PART OF "HAS IT LANDED", not a detail of it.
        #
        # Comparing the tier alone was wrong in a way that quietly lost a
        # scheduled change. A commitment-only downgrade — Growth committed-term
        # to Growth month-to-month — has a pending plan key of "growth", which
        # is what the customer is ALREADY on. So the very next
        # subscription.updated matched, cleared the markers and dropped the
        # schedule id, and this platform forgot a change Stripe still had
        # scheduled. Nothing would have said so until the rate moved on its own.
        #
        # A pending commitment is compared only when one was recorded, so a
        # pending change written before that column existed still lands the way
        # it always did.
        _pending_commitment = getattr(org, "billing_pending_commitment", None)
        _commitment_landed = (_pending_commitment is None
                              or _pending_commitment == commitment)
        if (org.billing_pending_plan_key
                and org.billing_pending_plan_key == resolved.key
                and _commitment_landed):
            log.info("billing_webhook: scheduled change to %s (%s) is now in "
                     "effect for org %s (was %s)", resolved.key,
                     _pending_commitment or "same commitment", org.id, previous)
            org.billing_pending_plan_key = None
            org.billing_pending_commitment = None
            org.billing_pending_effective_at = None
            org.stripe_schedule_id = None

        # THE PLAN MOVED, SO HELD PROSPECTS MAY NOW FIT.
        #
        # This is the moment an upgrade actually becomes true - not when the
        # customer clicked, but when Stripe says so - and it is therefore the
        # right place to let inbound leads that were held over capacity back
        # into normal working.
        #
        # Release only. Nothing is enrolled in a cadence, mailed, texted or
        # handed to the AI: five thousand held leads released straight into
        # outbound would spend the customer's money and their sending
        # reputation on a decision they never made. They become ordinary new
        # leads waiting for the customer's own action.
        #
        # Best-effort by design. A failure to release must never turn a
        # successful billing event into a failed webhook, because Stripe would
        # then retry a payment we have already banked.
        if previous != resolved.key:
            try:
                from app.services import lead_capacity
                released = lead_capacity.release_available(db, org)
                if released["released"]:
                    log.info("billing_webhook: plan change %s -> %s released %d "
                             "held lead(s) for org %s (%d still held)",
                             previous, resolved.key, released["released"],
                             org.id, released["still_held"])
            except Exception:                            # pragma: no cover
                log.exception("billing_webhook: could not release held leads "
                              "for org %s after plan change - the billing "
                              "event itself stands", org.id)


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
    """A checkout finished.

    FOR A SUBSCRIPTION, records ids only and deliberately does NOT mark
    anything paid: the subscription and invoice events carry the authoritative
    state, and they arrive for renewals too, so letting them own it means one
    code path rather than two that must agree.

    FOR A ONE-TIME SETUP PAYMENT there is no invoice event and no subscription
    event — `mode="payment"` produces neither — so this IS the authoritative
    moment, and it is handled explicitly below rather than falling through and
    leaving the money invisible.
    """
    if obj.get("customer") and not org.stripe_customer_id:
        org.stripe_customer_id = obj.get("customer")
    sub_id = obj.get("subscription")
    if sub_id:
        org.stripe_subscription_id = sub_id

    # ══ THE ONE-TIME SETUP PAYMENT ══════════════════════════════════════
    # Routed on `mode`, corroborated by `metadata.part`. A payment-mode session
    # cannot start a subscription and must never touch subscription state; a
    # subscription-mode session must never mark the setup fee paid. That
    # separation is the whole point of splitting the two checkouts.
    if (obj.get("mode") or "") == "payment":
        # ══ WHICH one-time obligation did this settle? ═══════════════════
        #
        # There are now two payment-mode shapes, and they must not be
        # confused: the implementation SETUP FEE, and a one-time CATALOGUE
        # purchase (a migration, a training session, custom work). Routed on
        # `metadata.purpose`, which the catalogue checkout sets explicitly.
        #
        # Absent means setup, which is correct for every session created
        # before the catalogue existed — those carry no purpose and are
        # setup fees by construction. A catalogue purchase must never mark the
        # setup fee paid, and a setup fee must never be recorded as a
        # purchase: "an add-on purchase must not mark Setup paid" is a rule,
        # not a preference.
        if (obj.get("metadata") or {}).get("purpose") == "catalog_purchase":
            _handle_catalog_purchase_payment(db, org, obj)
        else:
            _handle_setup_payment(db, org, obj)
        return

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


def _handle_setup_payment(db: Session, org: Organization, obj: dict) -> None:
    """The one-time implementation fee arrived. Setup state only.

    ═══════════════════════════════════════════════════════════════════════
    WHAT THIS MUST NOT DO
    ═══════════════════════════════════════════════════════════════════════
    Not start a subscription. Not set `billing_status`. Not stamp a plan on the
    organization. A customer who pays to be implemented has bought exactly one
    thing, and inferring a subscription from it would put them on a plan they
    are not paying for and would make the Billing screen claim recurring
    revenue that does not exist.

    IDEMPOTENT THROUGH THE SAME CONSTRAINT EVERY OTHER PAYMENT USES.
    `BillingPayment.collection_reference` is unique, so a replayed
    `checkout.session.completed` — Stripe retries, and a person can re-send one
    from the dashboard — finds the row already there and banks nothing twice.
    The event-level dedupe upstream catches the common case; this catches the
    case where the SAME payment arrives under a different event id.

    UNPAID SESSIONS ARE NOT PAYMENTS. `payment_status` is checked explicitly:
    a completed session can be `unpaid` (an async method still clearing), and
    treating "the customer finished the form" as "the money arrived" is the
    exact error this whole split exists to prevent.
    """
    from app.models.billing_models import BillingPayment
    from app.models.implementation_models import Implementation

    meta = obj.get("metadata") or {}
    opportunity_id = meta.get("opportunity_id")

    impl = None
    if opportunity_id:
        impl = (db.query(Implementation)
                .filter(Implementation.opportunity_id == opportunity_id)
                .order_by(Implementation.created_at.desc())
                .first())
    if impl is None:
        impl = (db.query(Implementation)
                .filter(Implementation.organization_id == org.id)
                .order_by(Implementation.created_at.desc())
                .first())

    payment_status = (obj.get("payment_status") or "").lower()
    if payment_status not in ("paid", "no_payment_required"):
        # Recorded as still pending rather than silently ignored: a session
        # that completed without paying is a thing somebody needs to see.
        if impl is not None and impl.setup_payment_status != "paid":
            impl.setup_payment_status = "checkout_pending"
        log.info("setup payment session %s completed but payment_status=%s",
                 obj.get("id"), payment_status or "unknown")
        return

    pi = obj.get("payment_intent")
    # The payment intent is the money. The session id is the fallback so a
    # session without one still dedupes against itself rather than banking
    # twice.
    reference = "stripe_pi:%s" % pi if pi else "stripe_cs:%s" % obj.get("id")

    existing = (db.query(BillingPayment)
                .filter(BillingPayment.collection_reference == reference)
                .first())

    amount = obj.get("amount_total")
    if existing is None:
        db.add(BillingPayment(
            organization_id=org.id,
            platform_id=getattr(org, "platform_id", None),
            collection_reference=reference,
            stripe_payment_intent_id=pi,
            currency=(obj.get("currency") or "usd"),
            amount_cents=amount or 0,
            # NOT a subscription payment. `is_initial` distinguishes a first
            # subscription invoice from a renewal for compensation purposes,
            # and a setup fee is neither.
            is_initial=False,
            opportunity_id=opportunity_id,
            collected_at=datetime.utcnow(),
            # COMPENSATION IS NOT DECIDED HERE. Whether an implementation fee
            # earns commission is a compensation-policy question with its own
            # engine and its own rules; recording the money is this function's
            # whole job. The reason is stated rather than left blank so an
            # unpaid commission cannot be mistaken for a bug.
            earned_compensation=False,
            compensation_skipped_reason=(
                "One-time setup fee. Compensation on implementation fees is a "
                "policy decision and is not configured."),
        ))

    if impl is not None:
        impl.setup_payment_status = "paid"
        impl.setup_payment_intent_id = pi
        impl.setup_paid_cents = amount
        impl.setup_paid_at = datetime.utcnow()
        if obj.get("id"):
            impl.setup_checkout_session_id = obj.get("id")


def _handle_catalog_purchase_payment(db: Session, org: Organization,
                                     obj: dict) -> None:
    """A one-time CATALOGUE purchase was paid. Purchase and payment only.

    ═══════════════════════════════════════════════════════════════════════
    WHAT THIS MUST NOT DO — the same list the setup fee carries, for the
    same reason, plus one more.
    ═══════════════════════════════════════════════════════════════════════
    Not start a subscription. Not set `billing_status`. Not stamp a plan on
    the organization. AND NOT MARK THE SETUP FEE PAID: a customer who bought a
    data migration has not paid their implementation fee, and crediting one
    obligation with another's money is the defect the whole separation exists
    to prevent.

    UNPAID SESSIONS ARE NOT PAYMENTS. `payment_status` is checked before
    anything is banked — a completed session can be `unpaid` while an async
    method clears, and "they finished the form" is not "the money arrived".

    IDEMPOTENT TWICE OVER: the purchase row refuses to move out of PAID a
    second time, and `BillingPayment.collection_reference` is unique. A Stripe
    retry, or somebody resending the event from the dashboard, banks nothing
    twice from either direction.
    """
    from app.models.billing_models import BillingPayment
    from app.services import catalog_purchase

    payment_status = (obj.get("payment_status") or "").lower()
    if payment_status not in ("paid", "no_payment_required"):
        log.info("catalog purchase session %s completed but payment_status=%s",
                 obj.get("id"), payment_status or "unknown")
        return

    purchase = catalog_purchase.mark_one_time_paid(db, org, obj)
    if purchase is None:
        return

    pi = obj.get("payment_intent")
    reference = "stripe_pi:%s" % pi if pi else "stripe_cs:%s" % obj.get("id")
    existing = (db.query(BillingPayment)
                .filter(BillingPayment.collection_reference == reference)
                .first())
    if existing is not None:
        return

    db.add(BillingPayment(
        organization_id=org.id,
        platform_id=getattr(org, "platform_id", None),
        collection_reference=reference,
        stripe_payment_intent_id=pi,
        currency=(obj.get("currency") or purchase.currency or "usd"),
        amount_cents=obj.get("amount_total") or 0,
        # Neither a first subscription invoice nor a renewal. `is_initial`
        # exists to tell those two apart for compensation, and a one-time
        # service is a third thing.
        is_initial=False,
        collected_at=datetime.utcnow(),
        # COMPENSATION IS NOT DECIDED HERE. Whether selling a service earns
        # commission is a policy question with its own engine; recording the
        # money is this function's whole job. The reason is stated so an
        # unpaid commission cannot be mistaken for a bug.
        earned_compensation=False,
        compensation_skipped_reason=(
            "One-time catalogue purchase (%s). Compensation on services is a "
            "policy decision and is not configured." % purchase.item_key),
    ))


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
    org.billing_pending_commitment = None

    # ══ THE ADD-ONS WENT WITH IT ════════════════════════════════════════
    #
    # A recurring add-on is an ITEM on this subscription, so Stripe deleted it
    # along with the subscription and is billing for none of them. Rows left
    # ACTIVE would then claim the customer pays for something nobody is
    # charging them for — and, worse, would keep GRANTING the capacity those
    # add-ons bought, because `plan_limits` counts live purchases. A cancelled
    # customer holding five purchased seats forever is not a policy anyone
    # chose.
    #
    # ONE-TIME PURCHASES ARE UNTOUCHED. A migration somebody paid for was
    # delivered and settled; ending a subscription does not unbuy it.
    from app.models.catalog_models import CatalogItemKind as _Kind
    from app.models.purchase_models import CatalogPurchase as _Purchase
    from app.models.purchase_models import PurchaseStatus as _PStatus
    _ended = (db.query(_Purchase)
              .filter(_Purchase.organization_id == org.id,
                      _Purchase.kind == _Kind.RECURRING_ADDON,
                      _Purchase.status == _PStatus.ACTIVE)
              .all())
    for _p in _ended:
        _p.status = _PStatus.CANCELED
        _p.canceled_at = datetime.utcnow()
    if _ended:
        log.info("billing_webhook: subscription ended for %s - closed %d "
                 "recurring add-on(s) that went with it", org.id, len(_ended))

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
