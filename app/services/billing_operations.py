"""BILLING OPERATIONS — customers, invoices and subscriptions.

Everything here takes a `BillingScope` (app/services/billing_access.py) rather
than an organization id. That is the tenant-safety design and not a style
choice: a function that accepts an org id can be called with somebody else's,
and then the check has to exist at every call site instead of once. The scope
has already been resolved from the ACTIVE authorized workspace, so there is no
id in any signature below that a caller could substitute.

The three rules this module is built around:

  MONEY IS COPIED, NEVER DERIVED. Subscription amounts come from the
  BillingAgreement, which copied them from the approved deal. No price list is
  consulted - not package_pricing, and emphatically not the legacy PLANS dict.

  A RETRY IS NOT A SECOND CHARGE. Every create carries a stable idempotency key
  built from the thing being created, and the local guard runs first: an
  agreement that already names a subscription returns it rather than making
  another one. Stripe's key is the backstop, not the plan.

  STRIPE SUCCEEDED AND WE FAILED IS A REPORTED EVENT. Where a local write
  follows a Stripe create, the failure path logs the orphaned id through
  stripe_gateway.log_orphan rather than raising and forgetting it.

Stripe Product and Price objects are created here when a subscription needs
them, and they are an IMPLEMENTATION DETAIL of executing an agreement. They are
not the pricing source of truth and nothing reads a price back out of Stripe to
decide what to charge.
"""

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.billing_agreement_models import (AGREEMENT_LIVE_STATUSES,
                                                 BillingAgreement)
from app.models.billing_models import Invoice, Payment
from app.services import stripe_gateway as gw
from app.services.billing_access import BillingScope
from app.services.money import from_cents

logger = logging.getLogger(__name__)


class BillingOperationRefused(ValueError):
    """The operation is not valid for this organization or this object."""


# ── Customers ───────────────────────────────────────────────────────────────

def get_customer_id(scope: BillingScope) -> Optional[str]:
    """The organization's Stripe customer, or None. Creates nothing."""
    org = scope.organization
    return getattr(org, "stripe_customer_id", None) if org else None


def ensure_customer(db: Session, scope: BillingScope) -> str:
    """The organization's Stripe customer id, creating one only if needed.

    NOT CALLED JUST BECAUSE AN ORGANIZATION EXISTS. Every caller here is
    already executing something billable - a subscription, an invoice - so a
    customer is created when money is about to be involved and not before. A
    Stripe account full of customers who never bought anything is a
    reconciliation problem nobody needs.

    Idempotent twice over: the existing id short-circuits, and the create
    carries a key derived from the organization so two racing callers get the
    same customer rather than two.
    """
    org = scope.organization
    if org is None:
        raise BillingOperationRefused("No organization in scope.")
    if getattr(org, "stripe_customer_id", None):
        return org.stripe_customer_id

    s = gw.client()
    metadata = {"organization_id": org.id, "organization_slug": org.slug or ""}
    if getattr(org, "platform_id", None):
        metadata["platform_id"] = org.platform_id
    agreement = current_agreement(db, scope)
    if agreement is not None:
        # The brand and legal entity as the agreement recorded them, so a
        # Stripe customer can be traced back without joining four tables.
        metadata["brand_name"] = agreement.brand_name or ""
        metadata["merchant_legal_name"] = agreement.merchant_legal_name or ""
        if agreement.merchant_entity_id:
            metadata["merchant_entity_id"] = agreement.merchant_entity_id

    customer = gw.call(
        s.Customer.create,
        name=org.name,
        email=getattr(org, "billing_email", None) or None,
        metadata=metadata,
        idempotency_key_=gw.idempotency_key("customer", org.id))

    customer_id = getattr(customer, "id", None) or customer["id"]
    try:
        org.stripe_customer_id = customer_id
        db.commit()
    except Exception:
        db.rollback()
        # The customer EXISTS in Stripe. Losing the id here is how an
        # organization ends up with a second customer on the next attempt.
        gw.log_orphan("Customer.create", customer_id,
                      {"organization_id": org.id})
        raise
    return customer_id


# ── Agreement reads ─────────────────────────────────────────────────────────

def current_agreement(db: Session, scope: BillingScope
                      ) -> Optional[BillingAgreement]:
    from app.services import billing_agreement as agreements
    if scope.organization is None:
        return None
    return agreements.current_for_organization(db, scope.organization.id)


def _agreement_in_scope(db: Session, scope: BillingScope,
                        agreement_id: str) -> BillingAgreement:
    """Load one agreement, refusing anything outside the active workspace.

    The id is applied INSIDE the organization-scoped query rather than looked
    up and checked afterwards. Fetch-then-check is the pattern that leaks: the
    row is already in memory and every future edit is one `return` away from
    handing it back.
    """
    if scope.organization is None:
        raise BillingOperationRefused("No organization in scope.")
    agreement = (db.query(BillingAgreement)
                 .filter(BillingAgreement.id == agreement_id,
                         BillingAgreement.organization_id == scope.organization.id)
                 .first())
    if agreement is None:
        # 404-shaped, not 403: confirming the row exists would make this an
        # enumeration oracle for another tenant's agreements.
        raise BillingOperationRefused("No such billing agreement.")
    return agreement


# ── Subscriptions ───────────────────────────────────────────────────────────

def create_subscription(db: Session, scope: BillingScope,
                        agreement_id: str) -> Dict[str, Any]:
    """Execute a BillingAgreement as a Stripe subscription.

    THE AMOUNT AND CURRENCY COME FROM THE AGREEMENT. Nothing is looked up, and
    a Stripe Price is created to carry exactly those values rather than being
    selected from a catalogue that might have moved.

    NO DUPLICATE FOR THE SAME AGREEMENT. An agreement that already names a
    subscription returns it untouched - that is the local guard, and it runs
    before any Stripe call so a retry costs nothing. The idempotency key on
    the create is the second line of defence for two callers racing.
    """
    agreement = _agreement_in_scope(db, scope, agreement_id)

    if agreement.stripe_subscription_id:
        return {"subscription_id": agreement.stripe_subscription_id,
                "created": False,
                "agreement_id": agreement.id}

    if agreement.status not in AGREEMENT_LIVE_STATUSES:
        raise BillingOperationRefused(
            "Agreement %s is %s. Only a live agreement can be executed as a "
            "subscription." % (agreement.id, agreement.status))
    if not agreement.recurring_amount_cents:
        raise BillingOperationRefused(
            "Agreement %s has no recurring amount, so there is nothing to "
            "subscribe to." % agreement.id)

    customer_id = ensure_customer(db, scope)
    s = gw.client()

    product_name = (agreement.package_name
                    or ("%s subscription" % (agreement.brand_name or "Service")))
    product = gw.call(
        s.Product.create,
        name=product_name,
        metadata={"billing_agreement_id": agreement.id,
                  "organization_id": agreement.organization_id},
        idempotency_key_=gw.idempotency_key("product", agreement.id))
    product_id = getattr(product, "id", None) or product["id"]

    price = gw.call(
        s.Price.create,
        unit_amount=agreement.recurring_amount_cents,
        currency=(agreement.currency or "USD").lower(),
        recurring={"interval": agreement.billing_interval or "month"},
        product=product_id,
        metadata={"billing_agreement_id": agreement.id},
        idempotency_key_=gw.idempotency_key("price", agreement.id))
    price_id = getattr(price, "id", None) or price["id"]

    subscription = gw.call(
        s.Subscription.create,
        customer=customer_id,
        items=[{"price": price_id}],
        metadata={"billing_agreement_id": agreement.id,
                  "organization_id": agreement.organization_id},
        idempotency_key_=gw.idempotency_key("subscription", agreement.id))
    subscription_id = getattr(subscription, "id", None) or subscription["id"]

    try:
        from app.services import billing_agreement as agreements
        agreements.attach_stripe_subscription(db, agreement, subscription_id,
                                              price_id)
    except Exception:
        db.rollback()
        gw.log_orphan("Subscription.create", subscription_id,
                      {"agreement_id": agreement.id,
                       "organization_id": agreement.organization_id})
        raise

    return {"subscription_id": subscription_id, "created": True,
            "agreement_id": agreement.id, "price_id": price_id}


def get_subscription(db: Session, scope: BillingScope) -> Dict[str, Any]:
    """The active subscription as this application describes it.

    Reads the AGREEMENT first and Stripe second. The agreement is the local
    truth about what was agreed; Stripe is asked only for live execution
    state, and a Stripe outage degrades this to what we know rather than
    failing the page.
    """
    agreement = current_agreement(db, scope)
    org = scope.organization
    out = {
        "has_subscription": bool(agreement and agreement.stripe_subscription_id),
        "agreement_id": agreement.id if agreement else None,
        "status": agreement.status if agreement else None,
        "currency": agreement.currency if agreement else None,
        "recurring_amount_cents": (agreement.recurring_amount_cents
                                   if agreement else None),
        "billing_interval": agreement.billing_interval if agreement else None,
        "billing_status": getattr(org, "billing_status", None) if org else None,
        "stripe_subscription_id": (agreement.stripe_subscription_id
                                   if agreement else None),
        "stripe_state": None,
        "current_period_end": None,
    }
    if not out["stripe_subscription_id"]:
        return out
    try:
        s = gw.client()
        sub = gw.call(s.Subscription.retrieve, out["stripe_subscription_id"])
        out["stripe_state"] = _get(sub, "status")
        out["current_period_end"] = _get(sub, "current_period_end")
    except gw.StripeUnavailable:
        logger.info("subscription state unavailable from Stripe for org=%s",
                    getattr(org, "id", None))
    except gw.StripeOperationFailed as exc:
        logger.info("subscription %s could not be retrieved: %s",
                    out["stripe_subscription_id"], exc)
    return out


def cancel_subscription(db: Session, scope: BillingScope, agreement_id: str,
                        at_period_end: bool = True) -> Dict[str, Any]:
    """Cancel the subscription executing an agreement.

    Defaults to cancelling AT PERIOD END: the customer has paid for the period
    they are in, and ending it immediately takes away service they bought.
    Immediate cancellation stays available and explicit.
    """
    agreement = _agreement_in_scope(db, scope, agreement_id)
    if not agreement.stripe_subscription_id:
        raise BillingOperationRefused(
            "Agreement %s has no subscription to cancel." % agreement.id)

    s = gw.client()
    if at_period_end:
        sub = gw.call(s.Subscription.modify, agreement.stripe_subscription_id,
                      cancel_at_period_end=True)
    else:
        sub = gw.call(s.Subscription.delete, agreement.stripe_subscription_id)

    # LOCAL STATE IS NOT CHANGED HERE. The subscription is not over until
    # Stripe says it is, and P0's webhook handler is what applies that. Writing
    # "cancelled" now would contradict Stripe for anything cancelled at period
    # end, and would duplicate business logic the webhook already owns.
    return {"subscription_id": agreement.stripe_subscription_id,
            "cancel_at_period_end": bool(at_period_end),
            "stripe_state": _get(sub, "status"),
            "agreement_id": agreement.id}


# ── Invoices ────────────────────────────────────────────────────────────────

def _invoice_in_scope(db: Session, scope: BillingScope,
                      invoice_id: str) -> Invoice:
    """One invoice, matched inside the organization-scoped query.

    Accepts either the local id or the Stripe id, because a UI holds one and a
    Stripe link holds the other - but BOTH are matched with organization_id in
    the same filter, so guessing either kind of id gets the same nothing.
    """
    if scope.organization is None:
        raise BillingOperationRefused("No organization in scope.")
    org_id = scope.organization.id
    invoice = (db.query(Invoice)
               .filter(Invoice.organization_id == org_id,
                       ((Invoice.id == invoice_id)
                        | (Invoice.stripe_invoice_id == invoice_id)))
               .first())
    if invoice is None:
        raise BillingOperationRefused("No such invoice.")
    return invoice


def create_draft_invoice(db: Session, scope: BillingScope,
                         line_items: List[Dict[str, Any]],
                         description: Optional[str] = None,
                         days_until_due: int = 30,
                         request_id: Optional[str] = None) -> Dict[str, Any]:
    """A DRAFT invoice with its line items. Nothing is charged yet.

    Draft is the whole point: an invoice becomes a financial document when it
    is finalized, so everything before that is reviewable and reversible. Line
    items are attached to the customer first and swept into the draft by
    Stripe, which is Stripe's own model rather than one invented here.

    Amounts arrive as integer minor units and are passed through untouched. No
    float arithmetic happens anywhere on this path.

    DUPLICATE PROTECTION IS OPT-IN, AND THAT IS DELIBERATE. Unlike a
    subscription, there is no local uniqueness key for "this invoice": billing
    the same organization the same amount twice in a month is a perfectly
    normal thing to want, so deriving a key from the contents would silently
    return the FIRST invoice and look like it worked. The caller therefore
    supplies `request_id` - one id per submission, reused on retry - and only
    then does a retry collapse. Without it, two submissions are two invoices,
    which is correct behaviour for two deliberate requests.

    Nothing is charged either way: this is a DRAFT, and finalizing is a
    separate explicit call.
    """
    if not line_items:
        raise BillingOperationRefused(
            "An invoice needs at least one line item.")
    for item in line_items:
        amount = item.get("amount_cents")
        if not isinstance(amount, int) or isinstance(amount, bool):
            raise BillingOperationRefused(
                "Line item amounts must be integer minor units; got %r."
                % (amount,))
    currencies = {(i.get("currency") or "").upper() for i in line_items}
    currencies.discard("")
    if len(currencies) > 1:
        # Stripe would reject this on the second item, after the first has
        # already been attached to the customer. Refusing up front means no
        # half-built invoice is left behind.
        raise BillingOperationRefused(
            "One invoice cannot mix currencies; got %s."
            % ", ".join(sorted(currencies)))

    customer_id = ensure_customer(db, scope)
    agreement = current_agreement(db, scope)
    # The invoice currency comes from the FIRST line item, then the agreement,
    # then USD. Read explicitly from line_items[0] rather than from whatever
    # the validation loop above left behind - a loop variable that survives its
    # loop is how the last item silently decides the whole invoice.
    currency = (line_items[0].get("currency")
                or (agreement.currency if agreement else None) or "USD").lower()
    s = gw.client()

    invoice = gw.call(
        s.Invoice.create,
        customer=customer_id,
        collection_method="send_invoice",
        days_until_due=days_until_due,
        description=description,
        auto_advance=False,
        metadata={"organization_id": scope.organization.id,
                  "billing_agreement_id": agreement.id if agreement else ""},
        idempotency_key_=(gw.idempotency_key("invoice", scope.organization.id,
                                             request_id)
                          if request_id else None))
    invoice_id = getattr(invoice, "id", None) or invoice["id"]

    try:
        for item in line_items:
            gw.call(s.InvoiceItem.create,
                    customer=customer_id,
                    invoice=invoice_id,
                    amount=item["amount_cents"],
                    currency=(item.get("currency") or currency).lower(),
                    description=item.get("description") or "Charge")
    except Exception:
        # The DRAFT exists in Stripe with some of its items. It charges nobody
        # - a draft is not a financial document - but it is real and somebody
        # has to know it is there, so it is reported rather than swallowed.
        gw.log_orphan("Invoice.create", invoice_id,
                      {"organization_id": scope.organization.id,
                       "reason": "line items incomplete"})
        raise

    return {"stripe_invoice_id": invoice_id, "status": "draft",
            "line_item_count": len(line_items)}


def finalize_invoice(db: Session, scope: BillingScope,
                     invoice_id: str) -> Dict[str, Any]:
    """Turn a draft into a real financial document.

    Refused for the two states an invoice never leaves. Anything else - draft,
    or an already-open invoice whose local mirror is behind - is passed to
    Stripe, because guessing from a possibly stale mirror is worse than
    letting the authority answer.
    """
    invoice = _invoice_in_scope(db, scope, invoice_id)
    if invoice.status in ("paid", "void"):
        raise BillingOperationRefused(
            "Invoice %s is %s and cannot be finalized."
            % (invoice.number or invoice.id, invoice.status))
    s = gw.client()
    finalized = gw.call(s.Invoice.finalize_invoice, invoice.stripe_invoice_id)
    return _mirror(db, finalized)


def send_invoice(db: Session, scope: BillingScope,
                 invoice_id: str) -> Dict[str, Any]:
    """Ask Stripe to email the invoice to the customer."""
    invoice = _invoice_in_scope(db, scope, invoice_id)
    s = gw.client()
    sent = gw.call(s.Invoice.send_invoice, invoice.stripe_invoice_id)
    return _mirror(db, sent)


def void_invoice(db: Session, scope: BillingScope,
                 invoice_id: str) -> Dict[str, Any]:
    """Void a finalized, unpaid invoice.

    A PAID INVOICE IS NEVER VOIDED. Stripe refuses it too, but refusing here
    means the answer is a clear sentence rather than a translated Stripe error,
    and it costs no API call to say so.
    """
    invoice = _invoice_in_scope(db, scope, invoice_id)
    if invoice.status == "paid":
        raise BillingOperationRefused(
            "Invoice %s is paid and cannot be voided. Refund it instead."
            % (invoice.number or invoice.id))
    s = gw.client()
    voided = gw.call(s.Invoice.void_invoice, invoice.stripe_invoice_id)
    return _mirror(db, voided)


def _mirror(db: Session, stripe_invoice: Any) -> Dict[str, Any]:
    """Write Stripe's answer through P0's existing mirror and describe it.

    P0 already owns invoice mirroring, including its staleness guard. Calling
    it here rather than writing columns directly is what keeps one
    implementation of "what does this Stripe invoice mean locally".
    """
    from app.services.stripe_sync import upsert_invoice_from_stripe
    row, ignored_reason = upsert_invoice_from_stripe(db, stripe_invoice)
    if row is None:
        # Stripe accepted the operation and the mirror declined the object.
        # The Stripe-side change is REAL and already happened, so this is an
        # orphan report, not a rollback.
        stripe_id = _get(stripe_invoice, "id")
        gw.log_orphan("Invoice.mirror", stripe_id,
                      {"reason": ignored_reason or "invoice not mirrored"})
        raise BillingOperationRefused(
            "Stripe accepted the change but the invoice could not be "
            "recorded locally: %s" % (ignored_reason or "unknown reason"))
    # upsert_invoice_from_stripe FLUSHES; it does not commit, because the
    # webhook handler that owns it commits its own unit of work. There is no
    # such caller here, so the commit belongs at this boundary - without it
    # the mirror of a finalize or a void is discarded when the request ends
    # while the Stripe-side change stands.
    try:
        db.commit()
    except Exception:
        db.rollback()
        gw.log_orphan("Invoice.mirror", row.stripe_invoice_id,
                      {"reason": "local commit failed after Stripe change"})
        raise
    return describe_invoice(row)


def _get(obj: Any, key: str, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


# ── Application-shaped reads ────────────────────────────────────────────────

def describe_invoice(invoice: Invoice) -> Dict[str, Any]:
    """One invoice as this application talks about it.

    Deliberately NOT raw Stripe JSON. The frontend should not have to know
    Stripe's object shape, and a response built here can be changed without
    every screen learning a new Stripe field name.
    """
    return {
        "id": invoice.id,
        "stripe_invoice_id": invoice.stripe_invoice_id,
        "number": invoice.number,
        "status": invoice.status,
        "kind": invoice.kind,
        "currency": invoice.currency,
        "total_cents": invoice.total_cents,
        "amount_due_cents": invoice.amount_due_cents,
        "amount_paid_cents": invoice.amount_paid_cents,
        "total": _decimal_str(invoice.total_cents, invoice.currency),
        "amount_due": _decimal_str(invoice.amount_due_cents, invoice.currency),
        "description": invoice.description,
        "period_start": invoice.period_start,
        "period_end": invoice.period_end,
        "due_date": invoice.due_date,
        "paid_at": invoice.paid_at,
        "created_at": invoice.created_at,
        "hosted_invoice_url": invoice.hosted_invoice_url,
        "invoice_pdf": invoice.invoice_pdf,
        "merchant_legal_name": invoice.merchant_legal_name,
        "brand_name": invoice.brand_name,
        "last_payment_error": invoice.last_payment_error_message,
        "next_payment_attempt": invoice.next_payment_attempt,
    }


def describe_payment(payment: Payment) -> Dict[str, Any]:
    return {
        "id": payment.id,
        "invoice_id": payment.invoice_id,
        "status": payment.status,
        "currency": payment.currency,
        "amount_cents": payment.amount_cents,
        "amount": _decimal_str(payment.amount_cents, payment.currency),
        # The column is refunded_cents. Invoice uses amount_refunded_cents;
        # they are different models and the names do not match.
        "refunded_cents": payment.refunded_cents,
        "refunded": _decimal_str(payment.refunded_cents, payment.currency),
        "payment_method_brand": payment.payment_method_brand,
        "payment_method_last4": getattr(payment, "payment_method_last4", None),
        "failure_code": payment.failure_code,
        "failure_message": payment.failure_message,
        "paid_at": payment.paid_at,
        "created_at": payment.created_at,
    }


def _decimal_str(cents: Optional[int], currency: Optional[str]) -> Optional[str]:
    """Minor units to a display string, through money.from_cents.

    NO FLOAT ARITHMETIC. from_cents returns a Decimal and this stringifies it;
    the cents value stays authoritative and is returned alongside so a caller
    that needs to compute uses the integer.
    """
    if cents is None:
        return None
    return str(from_cents(cents, currency or "USD"))


def list_invoices(db: Session, scope: BillingScope,
                  limit: int = 100) -> List[Dict[str, Any]]:
    if scope.organization is None:
        return []
    rows = (db.query(Invoice)
            .filter(Invoice.organization_id == scope.organization.id)
            .order_by(Invoice.created_at.desc())
            .limit(limit).all())
    return [describe_invoice(r) for r in rows]


def list_payments(db: Session, scope: BillingScope,
                  limit: int = 100) -> List[Dict[str, Any]]:
    if scope.organization is None:
        return []
    rows = (db.query(Payment)
            .filter(Payment.organization_id == scope.organization.id)
            .order_by(Payment.created_at.desc())
            .limit(limit).all())
    return [describe_payment(r) for r in rows]


def billing_overview(db: Session, scope: BillingScope) -> Dict[str, Any]:
    """Everything the organization Billing screen needs, in one call.

    Built for P6 so the UI makes one request and never talks to Stripe. The
    past-due block is computed from the LOCAL mirror P0 maintains, which is
    what makes it available during a Stripe outage.
    """
    org = scope.organization
    invoices = list_invoices(db, scope)
    payments = list_payments(db, scope)
    agreement = current_agreement(db, scope)

    outstanding = [i for i in invoices
                   if i["status"] == "open" and (i["amount_due_cents"] or 0) > 0]
    outstanding_cents = sum(i["amount_due_cents"] or 0 for i in outstanding)
    failed = [p for p in payments if p["status"] == "failed"]

    return {
        "organization": {
            "id": org.id if org else None,
            "name": org.name if org else None,
            "billing_status": getattr(org, "billing_status", None) if org else None,
            "plan": getattr(org, "plan", None) if org else None,
        },
        "merchant": {
            "legal_name": agreement.merchant_legal_name if agreement else None,
            "brand_name": agreement.brand_name if agreement else None,
        },
        "agreement": _describe_agreement(agreement),
        "subscription": get_subscription(db, scope),
        "invoices": invoices,
        "payments": payments,
        "past_due": {
            "is_past_due": (getattr(org, "billing_status", None) == "past_due"
                            if org else False),
            "outstanding_invoice_count": len(outstanding),
            "outstanding_cents": outstanding_cents,
            "outstanding": _decimal_str(
                outstanding_cents,
                agreement.currency if agreement else "USD"),
            "failed_payment_count": len(failed),
        },
        "permissions": {"can_view": scope.can_view,
                        "can_manage": scope.can_manage},
    }


def _describe_agreement(agreement: Optional[BillingAgreement]
                        ) -> Optional[Dict[str, Any]]:
    if agreement is None:
        return None
    return {
        "id": agreement.id,
        "status": agreement.status,
        "currency": agreement.currency,
        "package_name": agreement.package_name,
        "recurring_amount_cents": agreement.recurring_amount_cents,
        "recurring_amount": _decimal_str(agreement.recurring_amount_cents,
                                         agreement.currency),
        "setup_fee_cents": agreement.setup_fee_cents,
        "setup_fee": _decimal_str(agreement.setup_fee_cents,
                                  agreement.currency),
        "billing_interval": agreement.billing_interval,
        "billing_option": agreement.billing_option,
        "contract_term_months": agreement.contract_term_months,
        "billing_start_date": agreement.billing_start_date,
        "trial_end": agreement.trial_end,
        "unit_label": agreement.unit_label,
        "min_units": agreement.min_units,
        "merchant_legal_name": agreement.merchant_legal_name,
        "brand_name": agreement.brand_name,
    }
