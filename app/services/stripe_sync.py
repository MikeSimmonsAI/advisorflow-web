"""Turn a Stripe object into local rows. Stripe is authoritative throughout.

THE RULES THIS MODULE ENFORCES

1. STATUS COMES FROM THE OBJECT, NEVER FROM THE EVENT NAME.
   Stripe does not guarantee webhook delivery order: `invoice.paid` can arrive
   before `invoice.finalized`. Deriving status from which event turned up means
   a late `invoice.finalized` can mark a paid invoice unpaid. So the payload's
   own `status` field wins, always.

2. AN OLDER EVENT NEVER OVERWRITES NEWER STATE.
   Each row records the Stripe `created` time of the last event applied to it.
   An event created before that is stale and is skipped, however late it
   arrives.

3. AN UNKNOWN CUSTOMER CREATES NOTHING.
   If no organization holds this `stripe_customer_id`, the object is not ours
   to record. Return None and let the caller log it as ignored. Creating an
   organization from a webhook would let anything Stripe sends invent a tenant.

4. IDEMPOTENT BY STRIPE ID.
   Upserting the same object twice produces one row with the same values.

5. NO MONEY ARITHMETIC HERE. Stripe already sends minor units as integers, so
   they are stored as received. `app/services/money.py` owns every conversion
   in the other direction.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.billing_models import (INVOICE_KIND_IMPLEMENTATION,
                                       INVOICE_KIND_ONE_TIME,
                                       INVOICE_KIND_SUBSCRIPTION,
                                       PAYMENT_FAILED,
                                       PAYMENT_PARTIALLY_REFUNDED,
                                       PAYMENT_PENDING, PAYMENT_REFUNDED,
                                       PAYMENT_SUCCEEDED, Invoice,
                                       InvoiceLineItem, Payment)
from app.models.models import Organization

log = logging.getLogger(__name__)


def _ts(value) -> Optional[datetime]:
    """Stripe sends unix seconds. Naive UTC, matching the rest of this codebase."""
    if value in (None, "", 0):
        return None
    try:
        return datetime.utcfromtimestamp(int(value))
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _get(obj: Any, key: str, default=None):
    """Stripe objects behave like dicts; test fixtures are dicts. Both work."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def organization_for_customer(db: Session, stripe_customer_id: Optional[str]):
    if not stripe_customer_id:
        return None
    return (db.query(Organization)
            .filter(Organization.stripe_customer_id == stripe_customer_id)
            .first())


def _is_stale(row, event_created_at: Optional[datetime]) -> bool:
    """True when this event predates what has already been applied.

    Only skips on a STRICTLY older event. Two events with the same timestamp
    are applied in arrival order, which is correct: Stripe emits several events
    for one state change within the same second and each carries the same
    object state.
    """
    if row is None or event_created_at is None or row.last_event_at is None:
        return False
    return event_created_at < row.last_event_at


def _stamp(row, event_id: Optional[str], event_created_at: Optional[datetime]) -> None:
    if event_id:
        row.last_event_id = event_id
    if event_created_at:
        row.last_event_at = event_created_at


def _invoice_kind(stripe_invoice: Any) -> str:
    if _get(stripe_invoice, "subscription"):
        return INVOICE_KIND_SUBSCRIPTION
    lines = (_get(stripe_invoice, "lines") or {})
    data = _get(lines, "data") or []
    for line in data:
        desc = (_get(line, "description") or "").lower()
        if "implementation" in desc or "onboarding" in desc or "setup" in desc:
            return INVOICE_KIND_IMPLEMENTATION
    return INVOICE_KIND_ONE_TIME


def _brand_name(db: Session, org: Organization) -> Optional[str]:
    """The customer-facing brand, which is NOT the legal seller.

    Read from the organization's platform. The legal entity name is taken from
    Stripe's own `account_name` on the invoice - a fact Stripe reports, not a
    constant written into this file.
    """
    platform = getattr(org, "platform", None)
    if platform is not None:
        return getattr(platform, "name", None)
    return None


def upsert_invoice_from_stripe(db: Session, stripe_invoice: Any,
                               event_id: Optional[str] = None,
                               event_created_at: Optional[datetime] = None
                               ) -> Tuple[Optional[Invoice], Optional[str]]:
    """Mirror one Stripe invoice. Returns (invoice, ignored_reason)."""
    stripe_id = _get(stripe_invoice, "id")
    if not stripe_id:
        return None, "invoice payload carried no id"

    customer_id = _get(stripe_invoice, "customer")
    org = organization_for_customer(db, customer_id)
    if org is None:
        # Not ours. Recorded as ignored by the caller; nothing is created.
        return None, "no organization for stripe_customer_id=%s" % customer_id

    row = (db.query(Invoice)
           .filter(Invoice.stripe_invoice_id == stripe_id).first())

    if _is_stale(row, event_created_at):
        log.info("billing: skipping stale event %s for invoice %s",
                 event_id, stripe_id)
        return row, None

    if row is None:
        row = Invoice(stripe_invoice_id=stripe_id, organization_id=org.id)
        db.add(row)

    row.organization_id = org.id
    row.platform_id = getattr(org, "platform_id", None)
    row.stripe_customer_id = customer_id
    row.stripe_subscription_id = _get(stripe_invoice, "subscription")
    row.number = _get(stripe_invoice, "number")
    row.kind = _invoice_kind(stripe_invoice)

    # STRIPE'S STATUS, verbatim. Never inferred from the event name.
    row.status = _get(stripe_invoice, "status") or row.status
    row.collection_method = _get(stripe_invoice, "collection_method")
    row.currency = (_get(stripe_invoice, "currency") or "usd").upper()

    row.subtotal_cents = _get(stripe_invoice, "subtotal")
    row.tax_cents = _get(stripe_invoice, "tax")
    row.total_cents = _get(stripe_invoice, "total")
    row.amount_paid_cents = _get(stripe_invoice, "amount_paid")
    row.amount_due_cents = _get(stripe_invoice, "amount_due")
    row.amount_refunded_cents = _get(stripe_invoice, "amount_refunded") or 0

    row.hosted_invoice_url = _get(stripe_invoice, "hosted_invoice_url")
    row.invoice_pdf = _get(stripe_invoice, "invoice_pdf")
    row.description = _get(stripe_invoice, "description")

    row.period_start = _ts(_get(stripe_invoice, "period_start"))
    row.period_end = _ts(_get(stripe_invoice, "period_end"))
    row.due_date = _ts(_get(stripe_invoice, "due_date"))
    row.finalized_at = _ts(_get(stripe_invoice, "status_transitions", {}) and
                           _get(_get(stripe_invoice, "status_transitions"), "finalized_at"))
    row.paid_at = _ts(_get(_get(stripe_invoice, "status_transitions") or {}, "paid_at"))
    row.voided_at = _ts(_get(_get(stripe_invoice, "status_transitions") or {}, "voided_at"))
    row.marked_uncollectible_at = _ts(
        _get(_get(stripe_invoice, "status_transitions") or {}, "marked_uncollectible_at"))

    row.attempt_count = _get(stripe_invoice, "attempt_count") or 0
    row.next_payment_attempt = _ts(_get(stripe_invoice, "next_payment_attempt"))

    err = _get(stripe_invoice, "last_finalization_error") or {}
    row.last_payment_error_code = _get(err, "code")
    row.last_payment_error_message = _get(err, "message")

    # SNAPSHOTS. The legal seller and the brand are different things and both
    # are recorded, because an invoice already issued must keep saying who
    # issued it even after a rename or a restructure.
    row.merchant_legal_name = _get(stripe_invoice, "account_name") or row.merchant_legal_name
    row.brand_name = _brand_name(db, org) or row.brand_name
    row.bill_to_org_name = org.name
    row.bill_to_email = (_get(stripe_invoice, "customer_email")
                         or row.bill_to_email)

    _stamp(row, event_id, event_created_at)
    db.flush()
    _sync_line_items(db, row, stripe_invoice)
    return row, None


def _sync_line_items(db: Session, invoice: Invoice, stripe_invoice: Any) -> None:
    lines = _get(stripe_invoice, "lines") or {}
    data = _get(lines, "data") or []
    if not data:
        return
    existing = {li.stripe_line_item_id: li
                for li in db.query(InvoiceLineItem)
                .filter(InvoiceLineItem.invoice_id == invoice.id).all()}
    for i, line in enumerate(data):
        sid = _get(line, "id")
        row = existing.get(sid)
        if row is None:
            row = InvoiceLineItem(invoice_id=invoice.id, stripe_line_item_id=sid)
            db.add(row)
        row.description = _get(line, "description")
        row.quantity = _get(line, "quantity")
        row.amount_cents = _get(line, "amount")
        price = _get(line, "price") or {}
        row.unit_amount_cents = _get(price, "unit_amount")
        row.currency = (_get(line, "currency") or invoice.currency or "usd").upper()
        period = _get(line, "period") or {}
        row.period_start = _ts(_get(period, "start"))
        row.period_end = _ts(_get(period, "end"))
        row.proration = bool(_get(line, "proration"))
        row.source = "stripe"
        row.sort_order = i
    db.flush()


def _payment_status(obj: Any, refunded_cents: int, amount_cents: Optional[int]) -> str:
    if refunded_cents and amount_cents and refunded_cents >= amount_cents:
        return PAYMENT_REFUNDED
    if refunded_cents:
        return PAYMENT_PARTIALLY_REFUNDED
    status = (_get(obj, "status") or "").lower()
    if status in ("succeeded", "paid"):
        return PAYMENT_SUCCEEDED
    if status in ("requires_payment_method", "canceled", "failed"):
        return PAYMENT_FAILED
    return PAYMENT_PENDING


def upsert_payment_from_stripe(db: Session, obj: Any,
                               event_id: Optional[str] = None,
                               event_created_at: Optional[datetime] = None
                               ) -> Tuple[Optional[Payment], Optional[str]]:
    """Mirror a PaymentIntent or a Charge. Returns (payment, ignored_reason)."""
    intent_id = (_get(obj, "payment_intent")
                 if _get(obj, "object") == "charge" else _get(obj, "id"))
    charge_id = (_get(obj, "id") if _get(obj, "object") == "charge"
                 else _get(obj, "latest_charge"))
    if not intent_id and not charge_id:
        return None, "payment payload carried no usable id"

    customer_id = _get(obj, "customer")
    org = organization_for_customer(db, customer_id)
    if org is None:
        return None, "no organization for stripe_customer_id=%s" % customer_id

    row = None
    if intent_id:
        row = (db.query(Payment)
               .filter(Payment.stripe_payment_intent_id == intent_id).first())
    if row is None and charge_id:
        row = (db.query(Payment)
               .filter(Payment.stripe_charge_id == charge_id).first())

    if _is_stale(row, event_created_at):
        log.info("billing: skipping stale event %s for payment %s",
                 event_id, intent_id or charge_id)
        return row, None

    if row is None:
        row = Payment(organization_id=org.id)
        db.add(row)

    row.organization_id = org.id
    row.stripe_payment_intent_id = intent_id or row.stripe_payment_intent_id
    row.stripe_charge_id = charge_id or row.stripe_charge_id
    row.stripe_invoice_id = _get(obj, "invoice") or row.stripe_invoice_id
    row.amount_cents = _get(obj, "amount") or _get(obj, "amount_received")
    row.currency = (_get(obj, "currency") or "usd").upper()
    row.refunded_cents = _get(obj, "amount_refunded") or 0
    row.status = _payment_status(obj, row.refunded_cents or 0, row.amount_cents)

    # ── PAYMENT METHOD SUMMARY — NOT CARD-ONLY (widened in P6) ─────────────
    #
    # This read only the `card` sub-object, so an ACH debit, a Link payment or
    # a wallet mirrored with brand and last4 both null and rendered on the
    # billing screen as if the method were unknown. The method was never
    # unknown; this code only knew how to read one kind.
    #
    # Stripe's own `type` names which sub-object carries the detail, so the
    # type is recorded verbatim and the detail is read from whatever it names.
    # A method neither Stripe nor this code has seen before still records what
    # it was rather than arriving blank.
    #
    # DISPLAY FIELDS ONLY, and the set is unchanged: a type, a brand, and the
    # last four digits. Stripe does not send a PAN or a full account number and
    # this stores neither - which is what keeps this application out of PCI
    # scope beyond SAQ-A.
    method = (_get(obj, "payment_method_details")
              or _get(obj, "charges") or {})
    method_type = _get(method, "type")
    detail = _get(method, method_type) if method_type else None
    if detail is None:
        # Older payloads, and any shape without a `type`, still carry `card`.
        detail = _get(method, "card")
        if detail is not None:
            method_type = method_type or "card"
    if method_type:
        row.payment_method_type = method_type
    if detail:
        # `brand` is card-specific; a bank account has `bank_name` instead and
        # a wallet may have neither. last4 is common to more of them than
        # brand is, which is why they are read independently.
        row.payment_method_brand = (_get(detail, "brand")
                                    or _get(detail, "bank_name")
                                    or row.payment_method_brand)
        row.payment_method_last4 = (_get(detail, "last4")
                                    or row.payment_method_last4)

    err = _get(obj, "last_payment_error") or _get(obj, "failure_message") or {}
    if isinstance(err, dict):
        row.failure_code = _get(err, "code") or _get(obj, "failure_code")
        row.failure_message = _get(err, "message")
    else:
        row.failure_code = _get(obj, "failure_code")
        row.failure_message = str(err) if err else None

    if row.status == PAYMENT_SUCCEEDED and row.paid_at is None:
        row.paid_at = _ts(_get(obj, "created")) or datetime.utcnow()
    if row.refunded_cents and row.refunded_at is None:
        row.refunded_at = datetime.utcnow()

    # Link to the invoice we already mirror, if we do.
    if row.stripe_invoice_id and row.invoice_id is None:
        inv = (db.query(Invoice)
               .filter(Invoice.stripe_invoice_id == row.stripe_invoice_id).first())
        if inv is not None:
            row.invoice_id = inv.id

    _stamp(row, event_id, event_created_at)
    db.flush()
    return row, None


# ── the organization's headline billing state ───────────────────────────────

def apply_invoice_state_to_organization(db: Session, invoice: Invoice) -> Optional[str]:
    """Mirror an invoice's consequence onto Organization.billing_status.

    THIS IS THE FIX FOR THE LIVE GAP. Before it, a failed payment changed
    nothing the platform could see and an organization stayed 'active' with a
    declined card.

    Deliberately narrow: it only ever moves the org between the states an
    INVOICE can justify, and it never touches `plan` or the subscription id -
    those belong to subscription events. It also never overrides a cancelled
    subscription, because a cancelled customer with an unpaid final invoice is
    cancelled, not past due.
    """
    org = (db.query(Organization)
           .filter(Organization.id == invoice.organization_id).first())
    if org is None:
        return None
    if (org.billing_status or "") == "canceled":
        return org.billing_status

    if invoice.status == "paid":
        # Only clears past_due when nothing else is outstanding.
        outstanding = (db.query(Invoice)
                       .filter(Invoice.organization_id == org.id,
                               Invoice.status == "open",
                               Invoice.id != invoice.id)
                       .count())
        org.billing_status = "past_due" if outstanding else "active"
    elif invoice.status == "open" and (invoice.attempt_count or 0) > 0:
        # An open invoice that has been ATTEMPTED and is still open is a
        # failed collection. An open invoice with no attempt yet is simply not
        # due, and calling that past due would alarm somebody for nothing.
        org.billing_status = "past_due"
    elif invoice.status == "uncollectible":
        org.billing_status = "past_due"

    return org.billing_status
