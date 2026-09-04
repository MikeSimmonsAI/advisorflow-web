"""P8 — BILLING INTEGRITY: does the mirror still agree with Stripe, and with itself.

WHY THIS EXISTS AS ITS OWN PHASE

Every phase before this one made billing *do* something. This one asks whether
what it did is still true. A mirror diverges silently: a webhook exhausts its
retries during a deploy, a customer is replaced in the dashboard by hand, an
agreement is signed and never executed — and nothing throws. The local row just
keeps saying whatever it last heard, and the first person to notice is usually
a customer who was billed wrong.

THE RULE THIS MODULE IS BUILT AROUND: REPORT FIRST, NEVER REPRICE.

`run()` has no mutation path at all. Repairs live in `apply_repair()`, which
defaults to a dry run, and which refuses outright anything that would change
what a customer pays. That refusal is not a policy check bolted on top — it is
a whitelist: a discrepancy code that is not in `SAFE_REPAIRS` has no code path
to a write, whatever a caller asks for.

SAFE LOCAL MIRROR REPAIR versus FINANCIAL CHANGE

The distinction is the whole design, so it is worth stating plainly.

  SAFE means: Stripe is the authority, we already know what it says, and the
  local row disagrees. Copying Stripe's answer into our row changes no money —
  it corrects a record of money that already moved. Refreshing an invoice
  status, re-running a failed webhook from its stored payload, recomputing
  billing_status from invoices we already hold.

  A FINANCIAL CHANGE means: something a human decided, or must decide. Creating
  or cancelling a subscription, changing an amount, a currency, a term, a legal
  seller, a brand. Nothing here does any of those, and it will not do them on
  request. It reports them with `requires_human: true` and stops.

The awkward middle — "Stripe says $349 and the agreement says $499" — is a
FINANCIAL disagreement, not a stale mirror, because either side could be the
one that is wrong and picking one silently reprices somebody. It is reported
with both numbers and never resolved.

STRIPE IS READ-ONLY HERE, ALWAYS

`retrieve` and `list`. No create, no modify, no finalize, no void, no charge.
The one write this module can perform is to our own database, and only for the
codes listed in `SAFE_REPAIRS`.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.billing_agreement_models import (AGREEMENT_LIVE_STATUSES,
                                                 BillingAgreement)
from app.models.billing_models import (EVENT_FAILED, EVENT_PROCESSED,
                                       EVENT_RECEIVED, Invoice, Payment,
                                       StripeWebhookEvent)
from app.models.models import Organization, Platform
from app.services import stripe_gateway as gw
from app.services.money import from_cents, to_cents

logger = logging.getLogger(__name__)

# ── severity ────────────────────────────────────────────────────────────────
#
# CRITICAL is reserved for "money is wrong or unaccounted for right now".
# HIGH is "a customer is being billed incorrectly or not at all". MEDIUM is a
# record that disagrees with itself. LOW is untidy. The point of the grading is
# that a queue of forty findings is triaged in the order money is at risk.
CRITICAL = "critical"
HIGH = "high"
MEDIUM = "medium"
LOW = "low"
_SEVERITY_ORDER = {CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3}

# ── the discrepancy catalogue ───────────────────────────────────────────────
#
# `safe` marks a code whose repair copies proven Stripe state into our own row
# and moves no money. EVERYTHING ELSE requires a human, and `apply_repair`
# refuses it structurally rather than by policy.
DISCREPANCIES: Dict[str, Dict[str, Any]] = {
    # ── stale mirror. Stripe is right, we are behind. SAFE. ────────────────
    "stale_invoice_status": {
        "severity": HIGH, "safe": True,
        "what": "The local invoice status disagrees with Stripe's.",
        "repair": "Re-mirror the invoice from Stripe."},
    "missing_local_invoice": {
        "severity": HIGH, "safe": True,
        "what": "Stripe has an invoice for this customer that was never "
                "mirrored locally.",
        "repair": "Mirror it from Stripe."},
    "stale_org_billing_status": {
        "severity": HIGH, "safe": True,
        "what": "The organization's billing status disagrees with its own "
                "invoices.",
        "repair": "Recompute billing_status from the invoices already held."},
    "unresolved_past_due": {
        "severity": CRITICAL, "safe": True,
        "what": "Stripe reports this subscription past due and the "
                "organization is marked active, so nothing is chasing it.",
        "repair": "Refresh billing_status from proven Stripe state."},
    "recovered_but_past_due": {
        "severity": HIGH, "safe": True,
        "what": "Stripe reports this subscription healthy and the "
                "organization is still marked past due.",
        "repair": "Refresh billing_status from proven Stripe state."},
    "webhook_failed": {
        "severity": HIGH, "safe": True,
        "what": "A Stripe webhook event failed processing and its consequence "
                "was never applied.",
        "repair": "Reprocess the stored event payload."},
    "webhook_stuck": {
        "severity": MEDIUM, "safe": True,
        "what": "A webhook event was received and never reached a terminal "
                "state.",
        "repair": "Reprocess the stored event payload."},

    # ── money disagreements. A HUMAN DECIDES. ──────────────────────────────
    "amount_disagreement": {
        "severity": CRITICAL, "safe": False,
        "what": "Stripe is charging an amount the BillingAgreement does not "
                "name.",
        "repair": "Human review. Either side could be the wrong one, and "
                  "picking one silently reprices a customer."},
    "currency_disagreement": {
        "severity": CRITICAL, "safe": False,
        "what": "Stripe is charging in a currency the BillingAgreement does "
                "not name.",
        "repair": "Human review."},
    "deal_amount_disagreement": {
        "severity": HIGH, "safe": False,
        "what": "The BillingAgreement does not match the approved deal it "
                "came from.",
        "repair": "Human review. This may be an approved change or a defect."},

    # ── references that do not resolve. A HUMAN DECIDES. ───────────────────
    "missing_stripe_customer": {
        "severity": CRITICAL, "safe": False,
        "what": "The organization names a Stripe customer that Stripe does "
                "not have.",
        "repair": "Human review. Creating a replacement would orphan every "
                  "invoice and payment attached to the old one."},
    "missing_stripe_subscription": {
        "severity": CRITICAL, "safe": False,
        "what": "The agreement names a Stripe subscription that Stripe does "
                "not have. Nothing is being billed.",
        "repair": "Human review. Starting a new subscription is a billing "
                  "decision, not a repair."},
    "missing_stripe_invoice": {
        "severity": MEDIUM, "safe": False,
        "what": "A local invoice names a Stripe invoice that Stripe does not "
                "have.",
        "repair": "Human review."},
    "customer_owned_by_another_organization": {
        "severity": CRITICAL, "safe": False,
        "what": "Stripe's customer metadata names a different organization "
                "than the one holding the reference.",
        "repair": "Human review. Invoices are being attributed to the wrong "
                  "tenant."},
    "duplicate_customer_mapping": {
        "severity": CRITICAL, "safe": False,
        "what": "More than one organization names the same Stripe customer, "
                "so invoices are being attributed across tenants.",
        "repair": "Human review."},
    "duplicate_subscription_mapping": {
        "severity": CRITICAL, "safe": False,
        "what": "More than one BillingAgreement names the same Stripe "
                "subscription.",
        "repair": "Human review."},

    # ── structural. A HUMAN DECIDES. ───────────────────────────────────────
    "agreement_without_subscription": {
        "severity": HIGH, "safe": False,
        "what": "A live agreement has no Stripe subscription. Nothing is "
                "being billed.",
        "repair": "Human review. Executing an agreement is a billing action."},
    "subscription_without_agreement": {
        "severity": MEDIUM, "safe": False,
        "what": "Stripe is billing this organization and no BillingAgreement "
                "records what was agreed.",
        "repair": "Human review. See the P5 migration tooling."},
    "merchant_mismatch": {
        "severity": HIGH, "safe": False,
        "what": "The agreement's legal seller is not the entity its brand "
                "resolves to.",
        "repair": "Human review. The seller on an issued invoice is a legal "
                  "fact, not a field to correct."},
    "brand_mismatch": {
        "severity": MEDIUM, "safe": False,
        "what": "The agreement's brand is not the organization's brand.",
        "repair": "Human review."},
    "orphan_invoice": {
        "severity": HIGH, "safe": False,
        "what": "An invoice names a Stripe customer its organization does not "
                "hold.",
        "repair": "Human review."},
    "orphan_payment": {
        "severity": MEDIUM, "safe": False,
        "what": "A payment references an invoice that does not exist locally.",
        "repair": "Human review."},
}

SAFE_REPAIRS = frozenset(k for k, v in DISCREPANCIES.items() if v["safe"])
HUMAN_REVIEW = frozenset(k for k, v in DISCREPANCIES.items() if not v["safe"])

# Stripe subscription statuses that mean the customer owes money.
_STRIPE_TROUBLE = ("past_due", "unpaid", "incomplete_expired")
_STRIPE_HEALTHY = ("active", "trialing")


class RepairRefused(ValueError):
    """This repair would change money, or is not a known discrepancy."""


# ── finding construction ────────────────────────────────────────────────────

def _finding(code: str, org: Optional[Organization], *,
             agreement: Optional[BillingAgreement] = None,
             platform: Optional[Platform] = None,
             local_ref: Optional[str] = None,
             stripe_ref: Optional[str] = None,
             local_value: Any = None, stripe_value: Any = None,
             detail: str = "", target_type: Optional[str] = None,
             target_id: Optional[str] = None) -> Dict[str, Any]:
    """One discrepancy, with everything needed to judge it without a second query.

    A queue row that forces the reader to go and look something up is a queue
    row that does not get actioned.
    """
    spec = DISCREPANCIES[code]
    return {
        "code": code,
        "severity": spec["severity"],
        "what": spec["what"],
        "detail": detail,
        "organization_id": org.id if org is not None else None,
        "organization_name": org.name if org is not None else None,
        "brand_name": (agreement.brand_name if agreement is not None else None)
                      or (platform.name if platform is not None else None),
        "merchant_legal_name": (agreement.merchant_legal_name
                                if agreement is not None else None),
        "local_ref": local_ref,
        "stripe_ref": stripe_ref,
        "local_value": local_value,
        "stripe_value": stripe_value,
        "proposed_action": spec["repair"],
        "safe_repair": spec["safe"],
        # The field an operator reads first. A safe repair still SHOWS its
        # proposal; it simply does not need a decision to be correct.
        "requires_human": not spec["safe"],
        "target_type": target_type,
        "target_id": target_id,
    }


def _money(cents: Optional[int], currency: Optional[str]) -> Optional[str]:
    if cents is None:
        return None
    return "%s %s" % (from_cents(cents, currency or "USD"), (currency or "USD"))


def _get(obj: Any, key: str, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


# ── Stripe reads. RETRIEVE AND LIST ONLY. ───────────────────────────────────

def stripe_view(org: Organization, invoice_limit: int = 100) -> Dict[str, Any]:
    """Everything Stripe will tell us about one organization, read-only.

    Every failure mode is a RESULT rather than an exception: a reconciliation
    run that dies on the first unreachable customer is a run nobody finishes,
    and "Stripe does not have this customer" is itself the finding.
    """
    out: Dict[str, Any] = {
        "available": False, "unavailable_reason": None,
        "customer": None, "customer_missing": False,
        "subscription": None, "subscription_missing": False,
        "invoices": [], "invoices_read": False,
    }
    customer_id = getattr(org, "stripe_customer_id", None)
    try:
        s = gw.client()
    except (gw.StripeUnavailable, gw.LiveModeRefused) as exc:
        out["unavailable_reason"] = str(exc)
        return out
    out["available"] = True

    if customer_id:
        try:
            out["customer"] = gw.call(s.Customer.retrieve, customer_id)
            if _get(out["customer"], "deleted"):
                out["customer_missing"] = True
        except gw.StripeOperationFailed:
            out["customer_missing"] = True
        except gw.StripeUnavailable as exc:
            out["unavailable_reason"] = str(exc)
            return out

    subscription_id = _local_subscription_id(org)
    if subscription_id:
        try:
            out["subscription"] = gw.call(s.Subscription.retrieve,
                                          subscription_id)
        except gw.StripeOperationFailed:
            out["subscription_missing"] = True
        except gw.StripeUnavailable as exc:
            out["unavailable_reason"] = str(exc)

    if customer_id and not out["customer_missing"]:
        try:
            listing = gw.call(s.Invoice.list, customer=customer_id,
                              limit=invoice_limit)
            data = (listing.get("data") if isinstance(listing, dict)
                    else getattr(listing, "data", None)) or []
            out["invoices"] = list(data)
            out["invoices_read"] = True
        except (gw.StripeOperationFailed, gw.StripeUnavailable) as exc:
            out["unavailable_reason"] = str(exc)
    return out


def _local_subscription_id(org: Organization) -> Optional[str]:
    return getattr(org, "stripe_subscription_id", None)


# ── the checks ──────────────────────────────────────────────────────────────

def check_organization(db: Session, org: Organization,
                       view: Optional[Dict[str, Any]] = None
                       ) -> List[Dict[str, Any]]:
    """Every discrepancy detectable for one organization. WRITES NOTHING.

    `view` is a Stripe read; omit it for a local-only pass, which is the mode
    a dashboard uses and the mode that still works during a Stripe outage.
    """
    from app.services import billing_agreement as agreements
    from app.services import merchant_entity as entity_svc

    findings: List[Dict[str, Any]] = []
    agreement = agreements.current_for_organization(db, org.id)
    platform = (db.query(Platform).filter(Platform.id == org.platform_id).first()
                if getattr(org, "platform_id", None) else None)
    invoices = (db.query(Invoice)
                .filter(Invoice.organization_id == org.id).all())
    payments = (db.query(Payment)
                .filter(Payment.organization_id == org.id).all())
    customer_id = getattr(org, "stripe_customer_id", None)
    subscription_id = _local_subscription_id(org)

    def add(code, **kw):
        findings.append(_finding(code, org, agreement=agreement,
                                 platform=platform, **kw))

    # ── structural, local only ─────────────────────────────────────────────
    if agreement is not None and not agreement.stripe_subscription_id:
        add("agreement_without_subscription", local_ref=agreement.id,
            local_value=_money(agreement.recurring_amount_cents,
                               agreement.currency),
            detail="Agreement %s is %s and names no subscription."
                   % (agreement.id, agreement.status),
            target_type="billing_agreement", target_id=agreement.id)
    if subscription_id and agreement is None:
        add("subscription_without_agreement", stripe_ref=subscription_id,
            target_type="organization", target_id=org.id)

    if agreement is not None:
        expected_entity = entity_svc.resolve_for_platform(db, platform)
        if (expected_entity is not None and agreement.merchant_entity_id
                and agreement.merchant_entity_id != expected_entity.id):
            add("merchant_mismatch", local_ref=agreement.id,
                local_value=agreement.merchant_legal_name,
                stripe_value=expected_entity.legal_name,
                detail="The agreement was issued by %s; the brand now "
                       "resolves to %s."
                       % (agreement.merchant_legal_name,
                          expected_entity.legal_name),
                target_type="billing_agreement", target_id=agreement.id)
        if (agreement.platform_id and getattr(org, "platform_id", None)
                and agreement.platform_id != org.platform_id):
            add("brand_mismatch", local_ref=agreement.id,
                target_type="billing_agreement", target_id=agreement.id)
        findings.extend(_check_agreement_against_deal(db, org, agreement,
                                                      platform))

    # ── orphans ────────────────────────────────────────────────────────────
    for invoice in invoices:
        if (invoice.stripe_customer_id and customer_id
                and invoice.stripe_customer_id != customer_id):
            add("orphan_invoice", local_ref=invoice.id,
                stripe_ref=invoice.stripe_invoice_id,
                local_value=invoice.stripe_customer_id,
                stripe_value=customer_id,
                detail="Invoice %s names customer %s; the organization holds "
                       "%s." % (invoice.number or invoice.id,
                                invoice.stripe_customer_id, customer_id),
                target_type="invoice", target_id=invoice.id)
    invoice_ids = {i.id for i in invoices}
    for payment in payments:
        if payment.invoice_id and payment.invoice_id not in invoice_ids:
            add("orphan_payment", local_ref=payment.id,
                local_value=payment.invoice_id,
                detail="Payment %s references invoice %s, which does not "
                       "exist for this organization."
                       % (payment.id, payment.invoice_id),
                target_type="payment", target_id=payment.id)

    # ── billing_status vs our own invoices ─────────────────────────────────
    expected = _expected_billing_status(invoices, org)
    actual = getattr(org, "billing_status", None)
    if expected is not None and actual is not None and expected != actual:
        add("stale_org_billing_status", local_ref=org.id,
            local_value=actual, stripe_value=expected,
            detail="Marked %s; its invoices say %s." % (actual, expected),
            target_type="organization", target_id=org.id)

    if view is None or not view.get("available"):
        return findings

    # ── against Stripe ─────────────────────────────────────────────────────
    if customer_id and view.get("customer_missing"):
        add("missing_stripe_customer", local_ref=org.id,
            stripe_ref=customer_id,
            detail="Stripe has no customer %s." % customer_id,
            target_type="organization", target_id=org.id)
    customer_meta = _get(view.get("customer"), "metadata") or {}
    meta_org = _get(customer_meta, "organization_id")
    if meta_org and meta_org != org.id:
        add("customer_owned_by_another_organization", local_ref=org.id,
            stripe_ref=customer_id, local_value=org.id, stripe_value=meta_org,
            target_type="organization", target_id=org.id)

    if subscription_id and view.get("subscription_missing"):
        add("missing_stripe_subscription",
            local_ref=agreement.id if agreement else org.id,
            stripe_ref=subscription_id,
            detail="Stripe has no subscription %s." % subscription_id,
            target_type="billing_agreement",
            target_id=agreement.id if agreement else None)

    subscription = view.get("subscription")
    if subscription is not None:
        findings.extend(_check_subscription(db, org, agreement, platform,
                                            subscription))
    if view.get("invoices_read"):
        findings.extend(_check_invoices(db, org, agreement, platform,
                                        invoices, view["invoices"]))
    return findings


def _check_agreement_against_deal(db, org, agreement, platform):
    """The agreement versus the approved deal it was built from.

    A disagreement here is not necessarily wrong - an approved change produces
    one legitimately - which is exactly why it is reported rather than
    corrected.
    """
    from app.models.implementation_models import Implementation
    if not agreement.implementation_id:
        return []
    impl = (db.query(Implementation)
            .filter(Implementation.id == agreement.implementation_id).first())
    if impl is None:
        return []
    deal_cents = to_cents(getattr(impl, "recurring_amount", None),
                          getattr(impl, "currency", None) or "USD")
    if deal_cents is None or agreement.recurring_amount_cents is None:
        return []
    if deal_cents == agreement.recurring_amount_cents:
        return []
    return [_finding(
        "deal_amount_disagreement", org, agreement=agreement,
        platform=platform, local_ref=agreement.id,
        local_value=_money(agreement.recurring_amount_cents,
                           agreement.currency),
        stripe_value=_money(deal_cents, getattr(impl, "currency", None)),
        detail="The agreement charges %s; implementation %s approved %s."
               % (_money(agreement.recurring_amount_cents, agreement.currency),
                  impl.id, _money(deal_cents, getattr(impl, "currency", None))),
        target_type="billing_agreement", target_id=agreement.id)]


def _check_subscription(db, org, agreement, platform, subscription):
    """Stripe's live subscription versus what we believe about it."""
    findings = []
    status = _get(subscription, "status")

    def add(code, **kw):
        findings.append(_finding(code, org, agreement=agreement,
                                 platform=platform, **kw))

    billing_status = getattr(org, "billing_status", None)
    if status in _STRIPE_TROUBLE and billing_status not in ("past_due",
                                                            "canceled"):
        add("unresolved_past_due", local_ref=org.id,
            stripe_ref=_get(subscription, "id"),
            local_value=billing_status, stripe_value=status,
            detail="Stripe says %s; the organization is marked %s, so nothing "
                   "is chasing it." % (status, billing_status),
            target_type="organization", target_id=org.id)
    if status in _STRIPE_HEALTHY and billing_status == "past_due":
        add("recovered_but_past_due", local_ref=org.id,
            stripe_ref=_get(subscription, "id"),
            local_value=billing_status, stripe_value=status,
            detail="Stripe says %s; the organization is still marked past due."
                   % status,
            target_type="organization", target_id=org.id)

    if agreement is None:
        return findings

    items = (_get(_get(subscription, "items") or {}, "data") or [])
    if not items:
        return findings
    price = _get(items[0], "price") or {}
    stripe_cents = _get(price, "unit_amount")
    stripe_currency = (_get(price, "currency") or "").upper() or None

    # THE ONE THAT MATTERS MOST. Reported with both numbers, never resolved:
    # either side could be the wrong one and picking one reprices a customer.
    if (stripe_cents is not None and agreement.recurring_amount_cents is not None
            and stripe_cents != agreement.recurring_amount_cents):
        add("amount_disagreement", local_ref=agreement.id,
            stripe_ref=_get(subscription, "id"),
            local_value=_money(agreement.recurring_amount_cents,
                               agreement.currency),
            stripe_value=_money(stripe_cents, stripe_currency),
            detail="Stripe charges %s; the agreement names %s."
                   % (_money(stripe_cents, stripe_currency),
                      _money(agreement.recurring_amount_cents,
                             agreement.currency)),
            target_type="billing_agreement", target_id=agreement.id)
    if (stripe_currency and agreement.currency
            and stripe_currency != (agreement.currency or "").upper()):
        add("currency_disagreement", local_ref=agreement.id,
            stripe_ref=_get(subscription, "id"),
            local_value=agreement.currency, stripe_value=stripe_currency,
            target_type="billing_agreement", target_id=agreement.id)
    return findings


def _check_invoices(db, org, agreement, platform, local_invoices,
                    stripe_invoices):
    """Both directions: what Stripe has that we do not, and what we have stale."""
    findings = []
    by_stripe_id = {i.stripe_invoice_id: i for i in local_invoices}
    seen = set()

    def add(code, **kw):
        findings.append(_finding(code, org, agreement=agreement,
                                 platform=platform, **kw))

    for remote in stripe_invoices:
        remote_id = _get(remote, "id")
        if not remote_id:
            continue
        seen.add(remote_id)
        local = by_stripe_id.get(remote_id)
        if local is None:
            add("missing_local_invoice", stripe_ref=remote_id,
                stripe_value=_get(remote, "status"),
                detail="Stripe invoice %s (%s) has no local record."
                       % (_get(remote, "number") or remote_id,
                          _get(remote, "status")),
                target_type="stripe_invoice", target_id=remote_id)
            continue
        remote_status = _get(remote, "status")
        if remote_status and local.status != remote_status:
            add("stale_invoice_status", local_ref=local.id,
                stripe_ref=remote_id, local_value=local.status,
                stripe_value=remote_status,
                detail="Invoice %s is %s locally and %s at Stripe."
                       % (local.number or local.id, local.status,
                          remote_status),
                target_type="invoice", target_id=local.id)

    for local in local_invoices:
        if local.stripe_invoice_id and local.stripe_invoice_id not in seen:
            add("missing_stripe_invoice", local_ref=local.id,
                stripe_ref=local.stripe_invoice_id,
                local_value=local.status,
                detail="Local invoice %s names %s, which Stripe's listing for "
                       "this customer does not include."
                       % (local.number or local.id, local.stripe_invoice_id),
                target_type="invoice", target_id=local.id)
    return findings


def _expected_billing_status(invoices, org) -> Optional[str]:
    """What this organization's own invoices say its status should be.

    Deliberately narrow, and it mirrors `stripe_sync.apply_invoice_state_to_
    organization` rather than inventing a second rule: an ATTEMPTED open
    invoice is a failed collection, an unattempted one is simply not due yet.
    A cancelled organization is left alone - a cancelled customer with an
    unpaid final invoice is cancelled, not past due.
    """
    current = getattr(org, "billing_status", None)
    if current in (None, "", "canceled", "trialing"):
        return None
    failed = [i for i in invoices
              if i.status == "open" and (i.attempt_count or 0) > 0]
    if failed:
        return "past_due"
    if any(i.status == "open" for i in invoices):
        return current
    return "active" if current == "past_due" else None


# ── cross-organization checks ───────────────────────────────────────────────

def check_duplicates(db: Session) -> List[Dict[str, Any]]:
    """References that more than one tenant claims.

    A shared Stripe customer means invoices are being attributed across
    tenants, which is both a billing error and a data-isolation one.
    """
    findings = []
    by_customer: Dict[str, List[Organization]] = {}
    for org in db.query(Organization).all():
        customer_id = getattr(org, "stripe_customer_id", None)
        if customer_id:
            by_customer.setdefault(customer_id, []).append(org)
    for customer_id, orgs in by_customer.items():
        if len(orgs) < 2:
            continue
        names = ", ".join(o.name for o in orgs)
        for org in orgs:
            findings.append(_finding(
                "duplicate_customer_mapping", org, stripe_ref=customer_id,
                local_value=customer_id,
                detail="Stripe customer %s is claimed by: %s."
                       % (customer_id, names),
                target_type="organization", target_id=org.id))

    by_subscription: Dict[str, List[BillingAgreement]] = {}
    for agreement in db.query(BillingAgreement).filter(
            BillingAgreement.stripe_subscription_id.isnot(None)).all():
        by_subscription.setdefault(agreement.stripe_subscription_id,
                                   []).append(agreement)
    for subscription_id, rows in by_subscription.items():
        if len(rows) < 2:
            continue
        for agreement in rows:
            org = (db.query(Organization)
                   .filter(Organization.id == agreement.organization_id)
                   .first())
            findings.append(_finding(
                "duplicate_subscription_mapping", org, agreement=agreement,
                stripe_ref=subscription_id, local_ref=agreement.id,
                detail="Subscription %s is named by %d agreements."
                       % (subscription_id, len(rows)),
                target_type="billing_agreement", target_id=agreement.id))
    return findings


# ── webhook health ──────────────────────────────────────────────────────────

def webhook_health(db: Session, stale_after_minutes: int = 30,
                   window_hours: int = 168) -> Dict[str, Any]:
    """Is Stripe's side of the conversation being heard.

    A billing mirror is only as current as its webhook pipeline, and a
    pipeline that stopped is invisible from every other screen: nothing errors,
    numbers simply stop moving. This is the screen that shows it.

    NO PAYLOADS AND NO SECRETS. Counts, types, ages and error text - never the
    stored event body, which carries customer payment detail, and never the
    signing secret, which is not in the database at all.
    """
    now = datetime.utcnow()
    since = now - timedelta(hours=window_hours)
    events = (db.query(StripeWebhookEvent)
              .filter(StripeWebhookEvent.received_at >= since).all())

    by_status: Dict[str, int] = {}
    for event in events:
        status = event.processing_status or EVENT_RECEIVED
        by_status[status] = by_status.get(status, 0) + 1

    failed = [e for e in events if e.processing_status == EVENT_FAILED]
    stuck = [e for e in events
             if e.processing_status == EVENT_RECEIVED
             and e.received_at
             and (now - e.received_at) > timedelta(minutes=stale_after_minutes)]
    processed = [e for e in events if e.processing_status == EVENT_PROCESSED]
    redelivered = [e for e in events if (e.attempts or 0) > 1]

    def newest(rows, attr):
        stamps = [getattr(r, attr) for r in rows if getattr(r, attr, None)]
        return max(stamps) if stamps else None

    failure_types: Dict[str, int] = {}
    for event in failed:
        failure_types[event.event_type] = failure_types.get(event.event_type,
                                                            0) + 1

    return {
        "window_hours": window_hours,
        "received": len(events),
        "by_status": by_status,
        "processed": len(processed),
        "failed": len(failed),
        "stuck": len(stuck),
        "redelivered": len(redelivered),
        "last_processed_at": newest(processed, "processed_at"),
        "last_failed_at": newest(failed, "received_at"),
        "last_received_at": newest(events, "received_at"),
        "repeated_failure_types": [
            {"event_type": t, "count": c}
            for t, c in sorted(failure_types.items(), key=lambda kv: -kv[1])],
        "oldest_stuck_minutes": (
            int((now - min(e.received_at for e in stuck)).total_seconds() // 60)
            if stuck else None),
        # Enough to act on, and no payload.
        "failing_events": [
            {"id": e.id, "stripe_event_id": e.stripe_event_id,
             "event_type": e.event_type, "attempts": e.attempts,
             "received_at": e.received_at,
             "error_message": (e.error_message or "")[:300]}
            for e in sorted(failed, key=lambda e: e.received_at or now,
                            reverse=True)[:25]],
    }


def webhook_findings(db: Session, stale_after_minutes: int = 30
                     ) -> List[Dict[str, Any]]:
    """Failed and stuck events, as queue rows alongside every other finding."""
    now = datetime.utcnow()
    findings = []
    rows = (db.query(StripeWebhookEvent)
            .filter(StripeWebhookEvent.processing_status.in_(
                [EVENT_FAILED, EVENT_RECEIVED])).all())
    for event in rows:
        if event.processing_status == EVENT_FAILED:
            code = "webhook_failed"
        elif (event.received_at
              and (now - event.received_at) > timedelta(
                  minutes=stale_after_minutes)):
            code = "webhook_stuck"
        else:
            continue
        # NO ORGANIZATION ON A WEBHOOK FINDING, and that is not an omission.
        # `stripe_webhook_events` records the event, not a tenant: which
        # organization an event concerns is inside its payload, and reading a
        # stored body to attribute a queue row would mean parsing customer
        # payment detail to draw a label. A failed webhook is a platform
        # problem and is triaged as one; replaying it attributes itself
        # correctly through the same handler live delivery uses.
        findings.append(_finding(
            code, None, local_ref=event.id, stripe_ref=event.stripe_event_id,
            local_value=event.processing_status,
            detail="%s after %d attempt(s): %s"
                   % (event.event_type, event.attempts or 0,
                      (event.error_message or "no error recorded")[:200]),
            target_type="webhook_event", target_id=event.id))
    return findings


# ── the run ─────────────────────────────────────────────────────────────────

def run(db: Session, organization_id: Optional[str] = None,
        include_stripe: bool = True, limit: int = 200) -> Dict[str, Any]:
    """A full integrity pass. WRITES NOTHING — there is no code path that does.

    Scoped to one organization or across all of them; the caller's authority
    decides which, and that check belongs at the route.
    """
    started = datetime.utcnow()
    query = db.query(Organization)
    if organization_id:
        query = query.filter(Organization.id == organization_id)
    orgs = query.order_by(Organization.name).limit(limit).all()

    findings: List[Dict[str, Any]] = []
    stripe_unavailable = None
    for org in orgs:
        view = None
        if include_stripe:
            view = stripe_view(org)
            if not view["available"] and stripe_unavailable is None:
                stripe_unavailable = view["unavailable_reason"]
        findings.extend(check_organization(db, org, view))

    if organization_id is None:
        findings.extend(check_duplicates(db))
        findings.extend(webhook_findings(db))

    findings.sort(key=lambda f: (_SEVERITY_ORDER.get(f["severity"], 9),
                                 f["organization_name"] or "", f["code"]))
    by_code: Dict[str, int] = {}
    by_severity: Dict[str, int] = {}
    for f in findings:
        by_code[f["code"]] = by_code.get(f["code"], 0) + 1
        by_severity[f["severity"]] = by_severity.get(f["severity"], 0) + 1

    return {
        # Said in the response, not just in a docstring: this run changed
        # nothing, and could not have.
        "dry_run": True,
        "mutations_performed": 0,
        "scope": organization_id or "all organizations",
        "organizations_checked": len(orgs),
        "stripe_checked": bool(include_stripe and stripe_unavailable is None),
        "stripe_unavailable_reason": stripe_unavailable,
        "started_at": started,
        "finished_at": datetime.utcnow(),
        "total_findings": len(findings),
        "by_severity": by_severity,
        "by_code": by_code,
        "requires_human": sum(1 for f in findings if f["requires_human"]),
        "safe_repairs_available": sum(1 for f in findings
                                      if f["safe_repair"]),
        "findings": findings,
    }


# ── repair ──────────────────────────────────────────────────────────────────

def apply_repair(db: Session, code: str, target_id: str,
                 dry_run: bool = True) -> Dict[str, Any]:
    """Apply ONE safe local-mirror repair. DRY RUN UNLESS TOLD OTHERWISE.

    THE REFUSAL IS STRUCTURAL. A code outside `SAFE_REPAIRS` has no branch
    below that writes anything — it is not a policy check that a future edit
    could weaken by accident, it is the absence of an implementation. Every
    financial and business change lives in that absence deliberately: creating
    or cancelling a subscription, changing an amount, a currency, a term, a
    legal seller or a brand are decisions, and a reconciliation tool must not
    be able to make one.

    Every repair here copies state we can PROVE from Stripe, or recomputes a
    local field from rows we already hold. None moves money.
    """
    if code not in DISCREPANCIES:
        raise RepairRefused("%r is not a known discrepancy." % code)
    if code not in SAFE_REPAIRS:
        raise RepairRefused(
            "%s is a business decision, not a mirror repair: %s"
            % (code, DISCREPANCIES[code]["repair"]))

    handler = _REPAIRS[code]
    plan = handler(db, target_id, apply=False)
    result = {"code": code, "target_id": target_id, "dry_run": dry_run,
              "applied": False, "plan": plan}
    if dry_run or not plan.get("actionable"):
        return result
    result["outcome"] = handler(db, target_id, apply=True)
    result["applied"] = bool(result["outcome"].get("changed"))
    return result


def _repair_invoice_from_stripe(db: Session, invoice_id: str, apply: bool):
    """Re-mirror one invoice from Stripe. Stripe is the authority for status."""
    from app.services.stripe_sync import upsert_invoice_from_stripe
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if invoice is None:
        return {"actionable": False, "reason": "No such local invoice."}
    try:
        s = gw.client()
        remote = gw.call(s.Invoice.retrieve, invoice.stripe_invoice_id)
    except (gw.StripeUnavailable, gw.StripeOperationFailed) as exc:
        return {"actionable": False,
                "reason": "Stripe could not confirm this invoice: %s" % exc}
    remote_status = _get(remote, "status")
    if not apply:
        return {"actionable": remote_status != invoice.status,
                "from": invoice.status, "to": remote_status,
                "stripe_ref": invoice.stripe_invoice_id}
    row, ignored = upsert_invoice_from_stripe(db, remote)
    if row is None:
        db.rollback()
        return {"changed": False, "reason": ignored or "not mirrored"}
    db.commit()
    return {"changed": True, "status": row.status,
            "stripe_ref": row.stripe_invoice_id}


def _repair_missing_local_invoice(db: Session, stripe_invoice_id: str,
                                  apply: bool):
    """Mirror an invoice Stripe has and we never recorded.

    OWNERSHIP IS PROVEN BY STRIPE, NOT ASSERTED BY THE CALLER: the upsert
    resolves the organization from the invoice's own customer id and refuses
    when no local organization holds it.
    """
    from app.services.stripe_sync import upsert_invoice_from_stripe
    try:
        s = gw.client()
        remote = gw.call(s.Invoice.retrieve, stripe_invoice_id)
    except (gw.StripeUnavailable, gw.StripeOperationFailed) as exc:
        return {"actionable": False,
                "reason": "Stripe could not confirm this invoice: %s" % exc}
    if not apply:
        return {"actionable": True, "from": None,
                "to": _get(remote, "status"), "stripe_ref": stripe_invoice_id}
    row, ignored = upsert_invoice_from_stripe(db, remote)
    if row is None:
        db.rollback()
        return {"changed": False,
                "reason": ignored or "no local organization holds this customer"}
    db.commit()
    return {"changed": True, "status": row.status, "local_ref": row.id}


def _repair_billing_status(db: Session, organization_id: str, apply: bool):
    """Recompute billing_status from invoices already held. No Stripe call."""
    org = (db.query(Organization)
           .filter(Organization.id == organization_id).first())
    if org is None:
        return {"actionable": False, "reason": "No such organization."}
    invoices = (db.query(Invoice)
                .filter(Invoice.organization_id == org.id).all())
    expected = _expected_billing_status(invoices, org)
    if expected is None or expected == org.billing_status:
        return {"actionable": False, "reason": "Already consistent.",
                "from": org.billing_status}
    if not apply:
        return {"actionable": True, "from": org.billing_status, "to": expected}
    before = org.billing_status
    org.billing_status = expected
    db.commit()
    return {"changed": True, "from": before, "to": expected}


def _repair_billing_status_from_stripe(db: Session, organization_id: str,
                                       apply: bool):
    """Refresh billing_status from the LIVE subscription state.

    For the two findings where Stripe and our flag disagree. It writes only
    that one column, and never `plan` or a subscription reference - those
    belong to subscription events, and widening this would let a repair change
    what a customer is on.
    """
    org = (db.query(Organization)
           .filter(Organization.id == organization_id).first())
    if org is None:
        return {"actionable": False, "reason": "No such organization."}
    view = stripe_view(org, invoice_limit=1)
    subscription = view.get("subscription")
    if subscription is None:
        return {"actionable": False,
                "reason": view.get("unavailable_reason")
                          or "Stripe has no subscription to read."}
    status = _get(subscription, "status")
    if status in _STRIPE_TROUBLE:
        expected = "past_due"
    elif status in _STRIPE_HEALTHY:
        expected = "active"
    else:
        return {"actionable": False,
                "reason": "Stripe status %r does not map to a local billing "
                          "status." % status}
    if expected == org.billing_status:
        return {"actionable": False, "reason": "Already consistent."}
    if not apply:
        return {"actionable": True, "from": org.billing_status, "to": expected,
                "stripe_value": status}
    before = org.billing_status
    org.billing_status = expected
    db.commit()
    return {"changed": True, "from": before, "to": expected,
            "stripe_value": status}


def _repair_webhook_event(db: Session, event_row_id: str, apply: bool):
    """Reprocess a failed or stuck event FROM ITS STORED PAYLOAD.

    Not a re-request to Stripe and not a fabricated event: the body we already
    received, run through the same handler live processing uses, so a
    reprocessed event cannot take a different path from a first-delivery one.
    The handler's own idempotency and staleness guards still apply, which is
    what makes replaying safe.
    """
    from app.services import billing_webhooks
    event = (db.query(StripeWebhookEvent)
             .filter(StripeWebhookEvent.id == event_row_id).first())
    if event is None:
        return {"actionable": False, "reason": "No such webhook event."}
    if event.processing_status == EVENT_PROCESSED:
        return {"actionable": False, "reason": "Already processed."}
    if not event.payload_json:
        return {"actionable": False,
                "reason": "The event body was not retained; it cannot be "
                          "replayed and must be redelivered from Stripe."}
    if not apply:
        return {"actionable": True, "from": event.processing_status,
                "to": "reprocess", "event_type": event.event_type}
    try:
        payload = json.loads(event.payload_json)
    except ValueError:
        return {"changed": False, "reason": "The stored body is not readable."}
    try:
        outcome = billing_webhooks.process_event(db, payload, event)
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning("webhook replay failed for %s: %s", event.id, exc)
        return {"changed": False, "reason": "Reprocessing failed again: %s"
                                            % str(exc)[:200]}
    return {"changed": event.processing_status == EVENT_PROCESSED,
            "status": event.processing_status, "outcome": outcome}


_REPAIRS = {
    "stale_invoice_status": _repair_invoice_from_stripe,
    "missing_local_invoice": _repair_missing_local_invoice,
    "stale_org_billing_status": _repair_billing_status,
    "unresolved_past_due": _repair_billing_status_from_stripe,
    "recovered_but_past_due": _repair_billing_status_from_stripe,
    "webhook_failed": _repair_webhook_event,
    "webhook_stuck": _repair_webhook_event,
}

# Every safe code must have an implementation, and nothing else may have one.
assert set(_REPAIRS) == set(SAFE_REPAIRS), (
    "SAFE_REPAIRS and _REPAIRS disagree: %s"
    % (set(_REPAIRS) ^ set(SAFE_REPAIRS)))
