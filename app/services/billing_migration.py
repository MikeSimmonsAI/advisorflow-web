"""P5 — MOVING NEW BILLING OFF THE LEGACY `PLANS` CATALOGUE, WITHOUT REPRICING.

THE ONE RULE THIS MODULE EXISTS TO ENFORCE

An existing customer pays what they actually agreed to pay. Not what today's
catalogue says that plan costs, not what a formula reconstructs, not what a
tidy migration would prefer. Every function here is built so that the safest
possible outcome of running it is a REPORT, and the worst possible outcome of
a bug in it is a report that is wrong - never a customer whose bill changed.

That is why nothing in this module mutates by default. `reconcile_*` cannot
write at all - it has no code path that does. `propose_legacy_agreement` is a
dry run unless a caller passes `apply=True` explicitly, and even then it
refuses when the evidence disagrees with itself.

EVIDENCE PRIORITY, AND WHY THE CATALOGUE IS LAST

When reconstructing what a legacy customer is on, the order is:

  1. STRIPE. What the customer is actually being charged, right now, by the
     system that actually charges them. Nothing outranks this: it is not a
     record of an intention, it is the intention already executed.
  2. THE APPROVED DEAL. `Implementation.recurring_amount` - a human approved
     this number for this customer.
  3. LOCAL BILLING COLUMNS. `organizations.plan` and friends. Weakest of the
     three, because a plan KEY is not a price.

The catalogue - `PLANS`, `brand_packages` - is REFERENCE ONLY and appears in
every report purely so a human can see the gap. It is never authority for
reconstruction. A customer on a legacy or negotiated rate would be repriced by
any code that treated it as authority, which is the exact failure this phase
exists to prevent.

WHEN THE EVIDENCE CONFLICTS, NOTHING IS GUESSED

Stripe saying $349 and the Implementation saying $499 is a real business
question - a discount somebody approved, a price change never applied, a
migration half-done. Answering it by picking one is how a customer silently
gets a new bill. `propose_legacy_agreement` returns `needs_review` with both
numbers and writes nothing.
"""

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.billing_agreement_models import (AGREEMENT_LIVE_STATUSES,
                                                 INTERVAL_MONTH,
                                                 SOURCE_MIGRATION,
                                                 BillingAgreement)
from app.models.models import Organization
from app.services import stripe_gateway as gw
from app.services.money import from_cents, to_cents

logger = logging.getLogger(__name__)

# Stripe statuses that mean the customer is on the hook for money.
STRIPE_LIVE_STATUSES = ("active", "trialing", "past_due", "unpaid")

# Report verdicts.
OK = "ok"
NEEDS_REVIEW = "needs_review"
NO_EVIDENCE = "no_evidence"
NOT_BILLED = "not_billed"


class MigrationRefused(ValueError):
    """The migration will not proceed on this evidence."""


# ── evidence gathering — every function here is READ ONLY ───────────────────

def catalogue_reference(plan: Optional[str]) -> Optional[Dict[str, Any]]:
    """What the legacy catalogue says a plan key costs. REFERENCE ONLY.

    Imported from the router lazily and deliberately not re-exported: this is
    the ONLY place in the P5 code that reads `PLANS`, it is used only to put a
    comparison number in a report, and no caller may use its return value to
    price anything. Treating it as authority is how a customer on a negotiated
    rate gets repriced to list.
    """
    if not plan:
        return None
    from app.routers.billing_router import PLANS
    entry = PLANS.get(plan)
    if entry is None:
        return None
    return {
        "plan": plan,
        "name": entry.get("name"),
        "monthly_cents": entry.get("monthly_cents"),
        "monthly": (str(from_cents(entry["monthly_cents"], "USD"))
                    if entry.get("monthly_cents") else None),
        "note": "REFERENCE ONLY - never used to price anything",
    }


def stripe_evidence(org: Organization) -> Dict[str, Any]:
    """What Stripe is ACTUALLY charging this organization. Read-only.

    `Subscription.retrieve` and nothing else. A Stripe outage degrades this to
    "unknown" rather than failing the report, because a reconciliation run that
    dies on the first unreachable customer is a reconciliation run nobody
    finishes.
    """
    out = {
        "stripe_customer_id": getattr(org, "stripe_customer_id", None),
        "stripe_subscription_id": getattr(org, "stripe_subscription_id", None),
        "status": None,
        "recurring_amount_cents": None,
        "currency": None,
        "interval": None,
        "is_live": False,
        "unavailable_reason": None,
    }
    sub_id = out["stripe_subscription_id"]
    if not sub_id:
        return out
    try:
        s = gw.client()
        sub = gw.call(s.Subscription.retrieve, sub_id)
    except (gw.StripeUnavailable, gw.LiveModeRefused) as exc:
        out["unavailable_reason"] = str(exc)
        return out
    except gw.StripeOperationFailed as exc:
        # Most often "no such subscription" - a stale local id, which is
        # itself a finding rather than an error.
        out["unavailable_reason"] = str(exc)
        return out

    out["status"] = _get(sub, "status")
    out["is_live"] = out["status"] in STRIPE_LIVE_STATUSES
    items = (_get(_get(sub, "items") or {}, "data") or [])
    if items:
        price = _get(items[0], "price") or {}
        out["recurring_amount_cents"] = _get(price, "unit_amount")
        out["currency"] = (_get(price, "currency") or "").upper() or None
        recurring = _get(price, "recurring") or {}
        out["interval"] = _get(recurring, "interval")
    out["recurring_amount"] = (
        str(from_cents(out["recurring_amount_cents"], out["currency"] or "USD"))
        if out["recurring_amount_cents"] is not None else None)
    return out


def implementation_evidence(db: Session, org: Organization) -> Dict[str, Any]:
    """The approved deal's billing intent, if this organization came from one."""
    from app.models.implementation_models import Implementation
    impl = (db.query(Implementation)
            .filter(Implementation.organization_id == org.id)
            .order_by(Implementation.created_at.desc())
            .first())
    if impl is None:
        return {"implementation_id": None, "recurring_amount_cents": None,
                "setup_fee_cents": None, "currency": None}
    currency = getattr(impl, "currency", None) or "USD"
    return {
        "implementation_id": impl.id,
        "recurring_amount_cents": to_cents(
            getattr(impl, "recurring_amount", None), currency),
        "setup_fee_cents": to_cents(
            getattr(impl, "implementation_fee", None), currency),
        "currency": currency,
        "billing_option": getattr(impl, "billing_option", None),
        "contract_term_months": getattr(impl, "contract_term_months", None),
        "billing_start_date": getattr(impl, "billing_start_date", None),
    }


def agreement_evidence(db: Session, org: Organization) -> Dict[str, Any]:
    """The BillingAgreement in force, if one exists."""
    from app.services import billing_agreement as agreements
    agreement = agreements.current_for_organization(db, org.id)
    if agreement is None:
        return {"agreement_id": None, "status": None,
                "recurring_amount_cents": None, "currency": None}
    return {
        "agreement_id": agreement.id,
        "status": agreement.status,
        "source": agreement.source,
        "recurring_amount_cents": agreement.recurring_amount_cents,
        "recurring_amount": (str(from_cents(agreement.recurring_amount_cents,
                                            agreement.currency or "USD"))
                             if agreement.recurring_amount_cents else None),
        "setup_fee_cents": agreement.setup_fee_cents,
        "currency": agreement.currency,
        "billing_interval": agreement.billing_interval,
        "stripe_subscription_id": agreement.stripe_subscription_id,
    }


# ── reconciliation — DRY RUN IS THE ONLY MODE ──────────────────────────────

def reconcile_organization(db: Session, org: Organization,
                           include_stripe: bool = True) -> Dict[str, Any]:
    """Compare every billing record for one organization. WRITES NOTHING.

    There is no `apply` parameter and no mutation anywhere below, by design:
    a reconciliation tool that can also fix things is a reconciliation tool
    somebody eventually runs with the wrong flag against production.

    Disagreements are listed as findings with both values. Deciding which one
    is right is a business question, and this reports it rather than answering
    it.
    """
    local = {
        "organization_id": org.id,
        "name": org.name,
        "plan": getattr(org, "plan", None),
        "billing_status": getattr(org, "billing_status", None),
        "stripe_customer_id": getattr(org, "stripe_customer_id", None),
        "stripe_subscription_id": getattr(org, "stripe_subscription_id", None),
        "stripe_plan_interval": getattr(org, "stripe_plan_interval", None),
    }
    stripe_state = (stripe_evidence(org) if include_stripe
                    else {"unavailable_reason": "stripe read skipped",
                          "recurring_amount_cents": None, "is_live": False,
                          "status": None, "currency": None, "interval": None,
                          "stripe_subscription_id": local["stripe_subscription_id"],
                          "stripe_customer_id": local["stripe_customer_id"]})
    impl = implementation_evidence(db, org)
    agreement = agreement_evidence(db, org)
    catalogue = catalogue_reference(local["plan"])

    findings: List[Dict[str, str]] = []

    def finding(code, detail):
        findings.append({"code": code, "detail": detail})

    stripe_cents = stripe_state.get("recurring_amount_cents")
    impl_cents = impl.get("recurring_amount_cents")
    agr_cents = agreement.get("recurring_amount_cents")

    # 1. The agreement must match what Stripe actually charges.
    if agr_cents is not None and stripe_cents is not None \
            and agr_cents != stripe_cents:
        finding("agreement_vs_stripe",
                "Agreement says %s and Stripe is charging %s."
                % (_money(agr_cents, agreement.get("currency")),
                   _money(stripe_cents, stripe_state.get("currency"))))

    # 2. The approved deal vs what is actually charged. Very often a legitimate
    #    approved discount, which is exactly why it is reported and not fixed.
    if impl_cents is not None and stripe_cents is not None \
            and impl_cents != stripe_cents:
        finding("deal_vs_stripe",
                "Approved deal says %s and Stripe is charging %s. This may be "
                "an approved discount - confirm before changing anything."
                % (_money(impl_cents, impl.get("currency")),
                   _money(stripe_cents, stripe_state.get("currency"))))

    # 3. Billed by Stripe with no agreement: the P5 backlog, one row each.
    if stripe_state.get("is_live") and agreement["agreement_id"] is None:
        finding("live_without_agreement",
                "Stripe is billing this organization and it has no "
                "BillingAgreement. It is a legacy customer.")

    # 4. An agreement naming a different subscription than the organization.
    agr_sub = agreement.get("stripe_subscription_id")
    if agr_sub and local["stripe_subscription_id"] \
            and agr_sub != local["stripe_subscription_id"]:
        finding("subscription_mismatch",
                "Agreement names subscription %s; the organization names %s."
                % (agr_sub, local["stripe_subscription_id"]))

    # 5. A local subscription id Stripe does not recognise.
    if local["stripe_subscription_id"] and stripe_state.get("status") is None \
            and stripe_state.get("unavailable_reason") \
            and include_stripe:
        finding("stripe_unreadable",
                "Subscription %s could not be read from Stripe: %s"
                % (local["stripe_subscription_id"],
                   stripe_state["unavailable_reason"]))

    # 6. Catalogue drift. REPORTED, NEVER ACTED ON - this is the finding that
    #    would become a repricing bug if anybody "fixed" it automatically.
    cat_cents = (catalogue or {}).get("monthly_cents")
    if cat_cents is not None and stripe_cents is not None \
            and cat_cents != stripe_cents:
        finding("catalogue_drift",
                "Catalogue lists %s for plan '%s'; this customer is being "
                "charged %s. The customer's actual price stands - the "
                "catalogue is reference only."
                % (_money(cat_cents, "USD"), local["plan"],
                   _money(stripe_cents, stripe_state.get("currency"))))

    if not stripe_state.get("is_live") and agreement["agreement_id"] is None \
            and impl["implementation_id"] is None:
        verdict = NOT_BILLED
    elif findings:
        verdict = NEEDS_REVIEW
    else:
        verdict = OK

    return {
        "verdict": verdict,
        "dry_run": True,
        "local": local,
        "stripe": stripe_state,
        "implementation": impl,
        "agreement": agreement,
        "catalogue_reference": catalogue,
        "findings": findings,
    }


def reconcile_all(db: Session, limit: int = 500,
                  include_stripe: bool = True) -> Dict[str, Any]:
    """Every organization, compared. WRITES NOTHING.

    Platform-wide, so the caller must already hold platform authority - that
    check belongs at the route, not here, and the route enforces it.
    """
    orgs = (db.query(Organization).order_by(Organization.name).limit(limit)
            .all())
    reports = [reconcile_organization(db, o, include_stripe=include_stripe)
               for o in orgs]
    by_verdict: Dict[str, int] = {}
    for r in reports:
        by_verdict[r["verdict"]] = by_verdict.get(r["verdict"], 0) + 1
    codes: Dict[str, int] = {}
    for r in reports:
        for f in r["findings"]:
            codes[f["code"]] = codes.get(f["code"], 0) + 1
    return {
        "dry_run": True,
        "organization_count": len(reports),
        "by_verdict": by_verdict,
        "findings_by_code": codes,
        "organizations": reports,
    }


# ── legacy agreement reconstruction ────────────────────────────────────────

def propose_legacy_agreement(db: Session, org: Organization,
                             apply: bool = False,
                             created_by: Optional[str] = None
                             ) -> Dict[str, Any]:
    """What a BillingAgreement for this legacy customer WOULD say.

    DRY RUN UNLESS `apply=True`. The default is the safe one because the
    dangerous version of this function is the one somebody runs across every
    organization to "finish the migration".

    THE AMOUNT IS TAKEN FROM WHAT THE CUSTOMER IS ACTUALLY PAYING - Stripe
    first, the approved deal second. The catalogue is not consulted for the
    amount at all; it appears in the output only as `catalogue_reference` so a
    human can see the gap.

    Refuses rather than guesses when:
      * there is no evidence of a price at all
      * Stripe and the approved deal disagree materially
      * an agreement already exists (P2 owns that record; this never edits one)
    """
    existing = agreement_evidence(db, org)
    if existing["agreement_id"]:
        return {"status": "already_migrated", "dry_run": not apply,
                "applied": False, "agreement": existing,
                "reason": "This organization already has a BillingAgreement. "
                          "It is not rebuilt, edited or re-derived here."}

    stripe_state = stripe_evidence(org)
    impl = implementation_evidence(db, org)
    stripe_cents = stripe_state.get("recurring_amount_cents")
    impl_cents = impl.get("recurring_amount_cents")

    if stripe_cents is None and impl_cents is None:
        return {"status": NO_EVIDENCE, "dry_run": not apply, "applied": False,
                "reason": "No Stripe subscription amount and no approved deal "
                          "amount. There is nothing to preserve, and the "
                          "catalogue price is not evidence of what this "
                          "customer pays.",
                "catalogue_reference": catalogue_reference(
                    getattr(org, "plan", None))}

    if stripe_cents is not None and impl_cents is not None \
            and stripe_cents != impl_cents:
        return {
            "status": NEEDS_REVIEW, "dry_run": not apply, "applied": False,
            "reason": "Stripe charges %s and the approved deal says %s. That "
                      "difference is a business question - an approved "
                      "discount, a price change never applied, or a mistake - "
                      "and it is not answered by picking one."
                      % (_money(stripe_cents, stripe_state.get("currency")),
                         _money(impl_cents, impl.get("currency"))),
            "stripe": stripe_state, "implementation": impl,
            "catalogue_reference": catalogue_reference(
                getattr(org, "plan", None))}

    # WHAT THE CUSTOMER ACTUALLY PAYS. Stripe outranks the deal because it is
    # the intention already executed, not a record of one.
    if stripe_cents is not None:
        source_of_truth = "stripe_subscription"
        amount_cents = stripe_cents
        currency = stripe_state.get("currency") or impl.get("currency") or "USD"
        interval = stripe_state.get("interval") or INTERVAL_MONTH
    else:
        source_of_truth = "approved_implementation"
        amount_cents = impl_cents
        currency = impl.get("currency") or "USD"
        interval = INTERVAL_MONTH

    proposal = {
        "organization_id": org.id,
        "organization_name": org.name,
        "source": SOURCE_MIGRATION,
        "amount_source": source_of_truth,
        "recurring_amount_cents": amount_cents,
        "recurring_amount": str(from_cents(amount_cents, currency)),
        "currency": currency,
        "billing_interval": interval,
        "setup_fee_cents": None,
        "implementation_id": impl.get("implementation_id"),
        "stripe_customer_id": stripe_state.get("stripe_customer_id"),
        "stripe_subscription_id": stripe_state.get("stripe_subscription_id"),
    }
    result = {
        "status": OK,
        "dry_run": not apply,
        "applied": False,
        "proposal": proposal,
        # Shown so a human can see how far this customer is from list price.
        # NOT used to build the proposal above.
        "catalogue_reference": catalogue_reference(getattr(org, "plan", None)),
    }
    if not apply:
        return result

    agreement = _write_legacy_agreement(db, org, proposal, created_by)
    result["applied"] = True
    result["agreement_id"] = agreement.id
    return result


def _write_legacy_agreement(db: Session, org: Organization,
                            proposal: Dict[str, Any],
                            created_by: Optional[str]) -> BillingAgreement:
    """Write the proposal. Reached only from an explicit `apply=True`.

    Records `source=migration` and the amount source in `notes`, so a later
    reader can tell a reconstructed agreement from one that came from an
    approved deal - and can tell which evidence it was reconstructed FROM.

    NO STRIPE CALL. This records what is already happening; it does not make
    anything happen. Creating or modifying a subscription here is precisely
    the repricing this phase forbids.
    """
    from app.models.models import Platform
    from app.services import merchant_entity as entity_svc

    platform = None
    if getattr(org, "platform_id", None):
        platform = (db.query(Platform)
                    .filter(Platform.id == org.platform_id).first())
    entity = entity_svc.resolve_for_platform(db, platform)

    agreement = BillingAgreement(
        organization_id=org.id,
        merchant_entity_id=entity.id if entity else None,
        platform_id=platform.id if platform else None,
        implementation_id=proposal.get("implementation_id"),
        source=SOURCE_MIGRATION,
        status="active",
        currency=proposal["currency"],
        recurring_amount_cents=proposal["recurring_amount_cents"],
        billing_interval=proposal["billing_interval"],
        stripe_customer_id=proposal.get("stripe_customer_id"),
        stripe_subscription_id=proposal.get("stripe_subscription_id"),
        merchant_legal_name=entity.legal_name if entity else None,
        brand_name=platform.name if platform else None,
        organization_name=org.name,
        created_by=created_by,
        notes="Reconstructed by P5 migration from %s. Amount preserved as "
              "billed; catalogue pricing was not consulted."
              % proposal["amount_source"],
    )
    db.add(agreement)
    db.commit()
    db.refresh(agreement)
    logger.info("P5: reconstructed legacy agreement %s for org=%s from %s",
                agreement.id, org.id, proposal["amount_source"])
    return agreement


# ── helpers ────────────────────────────────────────────────────────────────

def _get(obj: Any, key: str, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _money(cents: Optional[int], currency: Optional[str]) -> str:
    if cents is None:
        return "nothing"
    return "%s %s" % (from_cents(cents, currency or "USD"), currency or "USD")
