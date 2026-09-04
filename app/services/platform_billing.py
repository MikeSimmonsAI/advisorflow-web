"""P7 — THE BACK-OFFICE BILLING COMMAND CENTER.

A DIFFERENT SURFACE FROM P6, NOT A WIDER VERSION OF IT

P6 answers "what is MY organization billed". This answers "what is happening to
billing across EVERY organization", and the two must not converge. The
authority is different in kind: a customer's org_admin holds billing authority
over one tenant by membership, and that has nothing to do with whether they may
look at another company's invoices. So nothing here consults `BillingScope`
resolution, no function here takes a caller's workspace, and every route in
front of it requires god_admin AND the non-delegable `platform_billing`
capability.

THE SCOPE FACTORY, AND WHY IT IS THE DANGEROUS PART

P4's operations all take a `BillingScope`, deliberately, so that no caller can
name another tenant's organization. P7 genuinely does need to act on an
arbitrary organization, so `platform_scope()` below builds one. That function
is the single place where the tenant guarantee is bypassed, and it is bypassed
by AUTHORITY rather than by accident: it is called only from routes already
past two platform checks, it never reads a caller-supplied scope, and the
organization it names is loaded from the database rather than trusted from the
request. Everything downstream of it is the same P4 code the customer surface
uses, with the same ownership filters inside it - which is why a guessed
invoice id still cannot cross from the organization the operator selected.

NO FAKE MONEY

Every figure this module reports is summed from the local mirror P0 maintains,
in integer minor units, grouped by currency so nothing is added across units
that cannot be added. There is no MRR, no ARR and no revenue projection,
because normalising a mixed-interval mixed-currency book into one number
requires assumptions this code has no authority to make. Contracted recurring
value is reported PER INTERVAL AND PER CURRENCY, which is the honest form of
the same question. A financial dashboard that is approximately right is worse
than one that answers a narrower question exactly.

WHAT THE MIRROR CAN AND CANNOT TELL YOU

Amounts here are what STRIPE TOLD US, as of the last webhook we processed. That
is authoritative for money that moved, and it is not a substitute for Stripe's
own reporting: an unprocessed webhook makes a number stale rather than wrong,
and P8's reconciliation is what proves the mirror complete. Every response says
so in `basis`, so a number lifted out of this screen carries its own caveat.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.billing_agreement_models import (AGREEMENT_LIVE_STATUSES,
                                                 BillingAgreement)
from app.models.billing_models import Invoice, Payment
from app.models.models import Organization, Platform
from app.services import billing_operations as ops
from app.services.billing_access import BillingScope
from app.services.money import from_cents

logger = logging.getLogger(__name__)

MIRROR_BASIS = ("Local mirror of Stripe, current as of the last processed "
                "webhook. Authoritative for money that moved; not a "
                "substitute for Stripe's own reporting.")

# Needs-attention codes. Ordered worst first - the queue is read top down by
# somebody with limited time.
ATTENTION_ORDER = (
    "payment_failed",
    "invoice_overdue",
    "org_past_due",
    "no_payment_method",
    "agreement_not_executed",
    "subscription_without_agreement",
    "billing_not_configured",
)


class PlatformBillingRefused(ValueError):
    """The platform operation is not valid for this organization."""


# ── the authority seam ──────────────────────────────────────────────────────

def platform_scope(org: Organization) -> BillingScope:
    """A full-authority scope over ONE named organization.

    THE ONE PLACE THE TENANT GUARANTEE IS SET ASIDE, and it is set aside by
    authority rather than by accident. Callers are routes that have already
    required god_admin and `platform_billing`; the organization is loaded from
    the database by `organization_or_refuse` below, never taken from a request
    body; and the scope is constructed here rather than accepted from anywhere.

    Downstream, this is an ordinary `BillingScope`, so every P4 ownership
    filter still applies against the organization it names. That is what keeps
    a guessed invoice id from crossing out of the organization an operator
    selected, even with platform authority.
    """
    if org is None:
        raise PlatformBillingRefused("No organization.")
    return BillingScope(None, org, True, True)


def organization_or_refuse(db: Session, organization_id: str) -> Organization:
    org = (db.query(Organization)
           .filter(Organization.id == organization_id).first())
    if org is None:
        raise PlatformBillingRefused("No such organization.")
    return org


# ── money, summed honestly ──────────────────────────────────────────────────

def _by_currency(rows, amount_attr: str, currency_attr: str = "currency"
                 ) -> Dict[str, int]:
    """Sum minor units PER CURRENCY.

    Never one total. Adding 100 USD to 100 CAD produces a number that is not
    money in any currency, and a dashboard that does it looks right until
    somebody acts on it.
    """
    out: Dict[str, int] = {}
    for row in rows:
        amount = getattr(row, amount_attr, None)
        if not amount:
            continue
        currency = (getattr(row, currency_attr, None) or "USD").upper()
        out[currency] = out.get(currency, 0) + int(amount)
    return out


def _money_map(totals: Dict[str, int]) -> List[Dict[str, Any]]:
    """A per-currency total, with the integer kept alongside the string."""
    return [{"currency": c, "cents": v, "amount": str(from_cents(v, c))}
            for c, v in sorted(totals.items())]


def _is_overdue(invoice: Invoice, now: datetime) -> bool:
    return bool(invoice.status == "open"
                and invoice.due_date is not None
                and invoice.due_date < now)


# ── the dashboard ───────────────────────────────────────────────────────────

def command_center(db: Session, limit: int = 500) -> Dict[str, Any]:
    """One query pass over the whole book. ONE ENDPOINT, NOT ONE PER ORG.

    Built as a single aggregate deliberately: a dashboard assembled from a
    call per organization is slow at fifty customers and a denial of service at
    five hundred, and it invites the frontend to compute totals - which is
    where a financial dashboard starts disagreeing with itself.

    Reads only the local mirror. NO STRIPE CALL: this screen must load during a
    Stripe outage, and the numbers on it are about money that already moved.
    """
    now = datetime.utcnow()
    orgs = db.query(Organization).order_by(Organization.name).limit(limit).all()
    org_by_id = {o.id: o for o in orgs}
    platforms = {p.id: p for p in db.query(Platform).all()}

    invoices = db.query(Invoice).all()
    payments = db.query(Payment).all()
    agreements = (db.query(BillingAgreement)
                  .filter(BillingAgreement.status.in_(AGREEMENT_LIVE_STATUSES))
                  .all())

    open_invoices = [i for i in invoices if i.status == "open"]
    overdue = [i for i in open_invoices if _is_overdue(i, now)]
    succeeded = [p for p in payments if p.status == "succeeded"]
    failed = [p for p in payments if p.status == "failed"]
    executed = [a for a in agreements if a.stripe_subscription_id]
    past_due_orgs = [o for o in orgs
                     if getattr(o, "billing_status", None) == "past_due"]

    # Contracted recurring value, PER INTERVAL AND PER CURRENCY. Deliberately
    # NOT called MRR: turning a mixed book of monthly and annual agreements in
    # more than one currency into a single monthly number takes an FX rate and
    # an annualisation rule, neither of which this code is entitled to invent.
    recurring: Dict[str, Dict[str, int]] = {}
    for agreement in agreements:
        if not agreement.recurring_amount_cents:
            continue
        interval = agreement.billing_interval or "month"
        currency = (agreement.currency or "USD").upper()
        recurring.setdefault(interval, {})
        recurring[interval][currency] = (
            recurring[interval].get(currency, 0)
            + int(agreement.recurring_amount_cents))

    return {
        "basis": MIRROR_BASIS,
        "generated_at": now,
        "counts": {
            "organizations": len(orgs),
            "organizations_with_live_agreement": len({a.organization_id
                                                      for a in agreements}),
            "active_subscriptions": len(executed),
            "open_invoices": len(open_invoices),
            "overdue_invoices": len(overdue),
            "failed_payments": len(failed),
            "organizations_past_due": len(past_due_orgs),
        },
        "money": {
            # Every one of these is a sum of integer minor units from the
            # mirror, grouped by currency. None is a projection.
            "open_invoice_total": _money_map(
                _by_currency(open_invoices, "amount_due_cents")),
            "overdue_total": _money_map(
                _by_currency(overdue, "amount_due_cents")),
            "payments_recorded": _money_map(
                _by_currency(succeeded, "amount_cents")),
            "failed_payment_total": _money_map(
                _by_currency(failed, "amount_cents")),
            "contracted_recurring": {
                interval: _money_map(totals)
                for interval, totals in sorted(recurring.items())
            },
        },
        "needs_attention": needs_attention(db, orgs=orgs, invoices=invoices,
                                           payments=payments,
                                           agreements=agreements,
                                           platforms=platforms, now=now),
        "recent_invoices": [_invoice_row(i, org_by_id, platforms)
                            for i in sorted(
                                invoices,
                                key=lambda i: i.created_at or datetime.min,
                                reverse=True)[:15]],
        "recent_payments": [_payment_row(p, org_by_id)
                            for p in sorted(
                                payments,
                                key=lambda p: p.created_at or datetime.min,
                                reverse=True)[:15]],
    }


def needs_attention(db: Session, orgs=None, invoices=None, payments=None,
                    agreements=None, platforms=None, now=None
                    ) -> List[Dict[str, Any]]:
    """The operational queue: what a human has to do something about.

    EVERY ROW IS A FACT FROM THE MIRROR, not a heuristic. "This invoice is open
    and its due date has passed" is checkable; "this customer looks like a
    churn risk" is not, and does not belong on a billing screen.

    Rows carry enough to act on without opening anything else - who, which
    brand, which legal seller, how much, since when - and nothing sensitive: no
    payment method detail beyond what P6's safe summary already allows, and no
    Stripe secret of any kind.
    """
    now = now or datetime.utcnow()
    if orgs is None:
        orgs = db.query(Organization).all()
    if invoices is None:
        invoices = db.query(Invoice).all()
    if payments is None:
        payments = db.query(Payment).all()
    if agreements is None:
        agreements = (db.query(BillingAgreement)
                      .filter(BillingAgreement.status.in_(AGREEMENT_LIVE_STATUSES))
                      .all())
    if platforms is None:
        platforms = {p.id: p for p in db.query(Platform).all()}

    org_by_id = {o.id: o for o in orgs}
    agreement_by_org: Dict[str, BillingAgreement] = {}
    for a in agreements:
        agreement_by_org.setdefault(a.organization_id, a)

    rows: List[Dict[str, Any]] = []

    def add(code, org, *, amount_cents=None, currency=None, since=None,
            detail="", target_type=None, target_id=None, agreement=None):
        if org is None:
            return
        platform = platforms.get(getattr(org, "platform_id", None))
        agreement = agreement or agreement_by_org.get(org.id)
        rows.append({
            "code": code,
            "organization_id": org.id,
            "organization_name": org.name,
            "brand_name": (agreement.brand_name if agreement else None)
                          or (platform.name if platform else None),
            "merchant_legal_name": (agreement.merchant_legal_name
                                    if agreement else None),
            "amount_cents": amount_cents,
            "amount": (str(from_cents(amount_cents, currency or "USD"))
                       if amount_cents else None),
            "currency": (currency or "USD").upper() if amount_cents else None,
            "since": since,
            "detail": detail,
            "target_type": target_type,
            "target_id": target_id,
        })

    for payment in payments:
        if payment.status != "failed":
            continue
        add("payment_failed", org_by_id.get(payment.organization_id),
            amount_cents=payment.amount_cents, currency=payment.currency,
            since=payment.created_at,
            detail=payment.failure_message or "Payment failed.",
            target_type="payment", target_id=payment.id)

    for invoice in invoices:
        if _is_overdue(invoice, now):
            add("invoice_overdue", org_by_id.get(invoice.organization_id),
                amount_cents=invoice.amount_due_cents,
                currency=invoice.currency, since=invoice.due_date,
                detail="Invoice %s is open past its due date."
                       % (invoice.number or invoice.id),
                target_type="invoice", target_id=invoice.id)

    for org in orgs:
        agreement = agreement_by_org.get(org.id)
        if getattr(org, "billing_status", None) == "past_due":
            add("org_past_due", org, detail="Stripe reports this account past due.",
                target_type="organization", target_id=org.id)
        if agreement is not None and not agreement.stripe_subscription_id:
            add("agreement_not_executed", org, agreement=agreement,
                amount_cents=agreement.recurring_amount_cents,
                currency=agreement.currency, since=agreement.created_at,
                detail="A live agreement has no Stripe subscription. Nothing "
                       "is being billed.",
                target_type="agreement", target_id=agreement.id)
        if agreement is not None and not getattr(org, "stripe_customer_id", None):
            add("billing_not_configured", org, agreement=agreement,
                detail="An agreement exists but the organization has no "
                       "Stripe customer.",
                target_type="organization", target_id=org.id)
        if getattr(org, "stripe_subscription_id", None) and agreement is None:
            add("subscription_without_agreement", org,
                detail="Stripe is billing this organization and it has no "
                       "BillingAgreement. Legacy customer.",
                target_type="organization", target_id=org.id)

    order = {code: i for i, code in enumerate(ATTENTION_ORDER)}
    rows.sort(key=lambda r: (order.get(r["code"], 99),
                             r["organization_name"] or ""))
    return rows


def _invoice_row(invoice: Invoice, org_by_id, platforms) -> Dict[str, Any]:
    org = org_by_id.get(invoice.organization_id)
    out = ops.describe_invoice(invoice)
    out["organization_id"] = invoice.organization_id
    out["organization_name"] = org.name if org else None
    return out


def _payment_row(payment: Payment, org_by_id) -> Dict[str, Any]:
    org = org_by_id.get(payment.organization_id)
    out = ops.describe_payment(payment)
    out["organization_id"] = payment.organization_id
    out["organization_name"] = org.name if org else None
    return out


# ── search and selection ────────────────────────────────────────────────────

FILTERS = ("all", "active", "past_due", "payment_failed", "open_invoices",
           "active_subscriptions", "no_agreement", "needs_attention")


def organizations(db: Session, q: Optional[str] = None,
                  status: str = "all", platform_id: Optional[str] = None,
                  limit: int = 200) -> Dict[str, Any]:
    """Find an organization to work on.

    One row per organization with the operational facts a back-office user
    filters on. The operator NEVER switches their own workspace to get here -
    that is the P6 authority path and it is not how cross-organization
    administration should work; selecting a row opens that organization's
    detail under platform authority instead.
    """
    now = datetime.utcnow()
    query = db.query(Organization)
    if q:
        like = "%%%s%%" % q.strip()
        query = query.filter(or_(Organization.name.ilike(like),
                                 Organization.slug.ilike(like)))
    if platform_id:
        query = query.filter(Organization.platform_id == platform_id)
    orgs = query.order_by(Organization.name).limit(limit).all()
    org_ids = [o.id for o in orgs]
    platforms = {p.id: p for p in db.query(Platform).all()}

    invoices = (db.query(Invoice)
                .filter(Invoice.organization_id.in_(org_ids)).all()
                if org_ids else [])
    payments = (db.query(Payment)
                .filter(Payment.organization_id.in_(org_ids),
                        Payment.status == "failed").all()
                if org_ids else [])
    agreements = (db.query(BillingAgreement)
                  .filter(BillingAgreement.organization_id.in_(org_ids),
                          BillingAgreement.status.in_(AGREEMENT_LIVE_STATUSES))
                  .all() if org_ids else [])

    agreement_by_org: Dict[str, BillingAgreement] = {}
    for a in agreements:
        agreement_by_org.setdefault(a.organization_id, a)

    rows = []
    for org in orgs:
        agreement = agreement_by_org.get(org.id)
        mine = [i for i in invoices if i.organization_id == org.id]
        open_mine = [i for i in mine if i.status == "open"]
        overdue_mine = [i for i in open_mine if _is_overdue(i, now)]
        failed_mine = [p for p in payments if p.organization_id == org.id]
        platform = platforms.get(getattr(org, "platform_id", None))
        outstanding = _by_currency(open_mine, "amount_due_cents")
        rows.append({
            "organization_id": org.id,
            "organization_name": org.name,
            "slug": org.slug,
            "is_active": org.is_active,
            "brand_name": platform.name if platform else None,
            "platform_id": getattr(org, "platform_id", None),
            "merchant_legal_name": (agreement.merchant_legal_name
                                    if agreement else None),
            "billing_status": getattr(org, "billing_status", None),
            "plan": getattr(org, "plan", None),
            "has_agreement": agreement is not None,
            "agreement_status": agreement.status if agreement else None,
            "recurring_amount": (str(from_cents(agreement.recurring_amount_cents,
                                                agreement.currency or "USD"))
                                 if agreement and agreement.recurring_amount_cents
                                 else None),
            "currency": agreement.currency if agreement else None,
            "billing_interval": agreement.billing_interval if agreement else None,
            "has_subscription": bool(agreement
                                     and agreement.stripe_subscription_id),
            "open_invoice_count": len(open_mine),
            "overdue_invoice_count": len(overdue_mine),
            "failed_payment_count": len(failed_mine),
            "outstanding": _money_map(outstanding),
        })

    def keep(row):
        if status in (None, "", "all"):
            return True
        if status == "active":
            return row["has_subscription"] and row["billing_status"] != "past_due"
        if status == "past_due":
            return row["billing_status"] == "past_due"
        if status == "payment_failed":
            return row["failed_payment_count"] > 0
        if status == "open_invoices":
            return row["open_invoice_count"] > 0
        if status == "active_subscriptions":
            return row["has_subscription"]
        if status == "no_agreement":
            return not row["has_agreement"]
        if status == "needs_attention":
            return bool(row["failed_payment_count"]
                        or row["overdue_invoice_count"]
                        or row["billing_status"] == "past_due"
                        or (row["has_agreement"] and not row["has_subscription"]))
        return True

    kept = [r for r in rows if keep(r)]
    return {"basis": MIRROR_BASIS, "filter": status, "query": q,
            "count": len(kept), "organizations": kept}


# ── one organization, in full ───────────────────────────────────────────────

def organization_detail(db: Session, organization_id: str) -> Dict[str, Any]:
    """Everything the back office needs about ONE organization's billing.

    Built on the SAME P4 describe functions the customer surface uses, through
    a platform scope. Two reasons that matters: an operator and a customer
    looking at the same invoice see the same numbers, and there is one
    implementation of "what does this Stripe object mean locally" rather than a
    second one that drifts.

    Adds what only the back office may see - agreement history, the Stripe
    customer and subscription references - and nothing that is secret.
    """
    org = organization_or_refuse(db, organization_id)
    scope = platform_scope(org)
    from app.services import billing_agreement as agreements
    from app.services import merchant_entity as entity_svc

    overview = ops.billing_overview(db, scope)
    history = agreements.history_for_organization(db, org.id)
    current = agreements.current_for_organization(db, org.id)
    platform = (db.query(Platform).filter(Platform.id == org.platform_id).first()
                if getattr(org, "platform_id", None) else None)
    # THE AGREEMENT'S OWN ENTITY FIRST. An agreement records the legal seller
    # as it was when the deal was signed, and that snapshot outranks whatever
    # the brand resolves to today - the whole point of P1's snapshot is that a
    # later restructure does not rewrite who issued an existing invoice.
    entity = None
    if current is not None and current.merchant_entity_id:
        from app.models.billing_entity_models import MerchantEntity
        entity = (db.query(MerchantEntity)
                  .filter(MerchantEntity.id == current.merchant_entity_id)
                  .first())
    if entity is None:
        entity = entity_svc.resolve_for_platform(db, platform)

    overview["identity"] = {
        "organization_id": org.id,
        "organization_name": org.name,
        "slug": org.slug,
        "is_active": org.is_active,
        "brand_name": platform.name if platform else None,
        "platform_id": getattr(org, "platform_id", None),
        "merchant_entity_id": entity.id if entity else None,
        "merchant_legal_name": entity.legal_name if entity else None,
        # REFERENCES, NOT CREDENTIALS. A Stripe customer or subscription id is
        # an identifier an operator needs to cross-check against the dashboard;
        # neither is secret and neither authorises anything on its own. No
        # Stripe key is ever returned by any route in this module.
        "stripe_customer_id": getattr(org, "stripe_customer_id", None),
        "stripe_subscription_id": getattr(org, "stripe_subscription_id", None),
    }
    overview["agreement_history"] = [ops._describe_agreement(a) for a in history]
    overview["basis"] = MIRROR_BASIS
    return overview
