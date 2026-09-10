"""
Deal → billing. The one missing link in the sale-to-collected-money path.

===========================================================================
WHAT WAS ACTUALLY MISSING, AND WHAT ALREADY WORKED
===========================================================================

Almost all of this path already existed and is reused untouched:

  deal_pricing.resolve()      the negotiated setup fee, MRR and term
  billing_catalog             brand-scoped plans and Stripe price ids
  billing_router /checkout    subscription checkout, server-resolved price
  billing_webhook             signature, idempotency, invoice + payment rows
  billing_compensation        payment → Implementation → Opportunity → earn
  provisioning                Won → customer Organization + Implementation

What did NOT exist is the join between the two halves. A rep negotiated a
setup fee and a monthly rate, a manager approved it against the floors, the
proposal snapshotted it, the customer accepted — and then nothing could
charge any of it. The customer's own admin had to visit their Billing screen
and pick a plan out of the catalogue at catalogue price, which is not what was
sold. That is the triple-entry this module removes.

Two concrete holes it fills:

1. THE SETUP FEE HAD NO STRIPE PATH AT ALL. `/billing/checkout` is
   `mode="subscription"`; a one-time implementation fee could not be charged
   anywhere in the codebase.

2. `BrandPackage.billing_plan_key` WAS INERT. It was a nullable column that
   nothing read, so the sold package could not name the subscription plan the
   customer should end up on. It is now the configured mapping this module
   resolves through — per brand, in data, never inferred from a name match.

===========================================================================
FAIL CLOSED, AND SAY WHY
===========================================================================

Every refusal here returns a named blocker rather than a guess. The failure
mode this avoids is the expensive one: charging a customer an amount nobody
agreed to. If the sold package has no billing plan mapped, this reports
`package_not_mapped_to_billing_plan` and charges nothing — it does NOT match
on the key by coincidence, because `BrandPackage.key` and
`BrandBillingPlan.key` are two different vocabularies that happen to share
some words today and would silently diverge tomorrow.

===========================================================================
THE NEGOTIATED AMOUNT IS AUTHORITATIVE FOR SETUP, THE CATALOGUE FOR RECURRING
===========================================================================

Deliberately asymmetric.

The SETUP fee is a per-deal number by design (`implementation_fee`,
`setup_discount`, custom deals) and is charged as an ad-hoc amount taken from
the deal's own resolved figure. It has already passed `pricing_authority`
floors and, where breached, manager approval — so it is an approved figure,
not a customer-supplied one.

The RECURRING rate is charged against the brand's catalogue Stripe price. A
subscription is a standing instruction that must survive plan changes,
proration and schedule advances, all of which the existing billing code drives
off `stripe_price_id`. Minting an ad-hoc recurring price per deal would put
those flows onto an object none of them can resolve. When a deal's negotiated
MRR differs from the catalogue rate for the mapped plan, that is reported as
`recurring_rate_differs_from_catalogue` — visible, refused, and somebody
decides — rather than quietly billing the wrong one of the two.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.services import billing_catalog, deal_pricing

log = logging.getLogger("deal_billing")

# ── blockers ────────────────────────────────────────────────────────────────
# Every string here is a thing a person can act on. None of them is "invalid".
B_PRICING_INCOMPLETE = "deal_pricing_incomplete"
B_NO_CUSTOMER_ORG = "no_customer_organization_yet"
B_NOT_WON = "opportunity_is_not_won"
B_NO_BRAND = "customer_org_has_no_brand"
B_PACKAGE_UNMAPPED = "package_not_mapped_to_billing_plan"
B_PLAN_UNAVAILABLE = "mapped_billing_plan_not_purchasable"
B_NO_PRICE_ID = "mapped_plan_has_no_stripe_price_for_interval"
B_RATE_MISMATCH = "recurring_rate_differs_from_catalogue"
B_NOTHING_TO_CHARGE = "deal_has_nothing_to_charge"
B_SUBSCRIPTION_EXISTS = "customer_already_has_a_subscription"

BLOCKER_TEXT = {
    B_PRICING_INCOMPLETE:
        "This deal's pricing is not established, so there is no agreed amount "
        "to charge. Select a package or set a custom rate first.",
    B_NO_CUSTOMER_ORG:
        "No customer organization exists for this deal yet. Provision the "
        "customer first — billing attaches to the customer, not the deal.",
    B_NOT_WON:
        "Only a Won opportunity can be billed.",
    B_NO_BRAND:
        "The customer organization is not attached to a brand, so no plan "
        "catalogue applies.",
    B_PACKAGE_UNMAPPED:
        "The package sold on this deal is not mapped to a billing plan, so the "
        "recurring charge cannot be set up. Map it in the brand's package "
        "catalogue (billing_plan_key).",
    B_PLAN_UNAVAILABLE:
        "The billing plan this package maps to is not currently purchasable "
        "for this brand.",
    B_NO_PRICE_ID:
        "The mapped billing plan has no Stripe price configured for this "
        "interval.",
    B_RATE_MISMATCH:
        "The monthly rate negotiated on this deal does not match the catalogue "
        "rate for the mapped plan. Charging either one silently would be wrong; "
        "align the deal, the mapping or the catalogue.",
    B_NOTHING_TO_CHARGE:
        "This deal has neither a setup fee nor a recurring rate to charge.",
    B_SUBSCRIPTION_EXISTS:
        "This customer already has a subscription. Starting another checkout "
        "would bill them twice — change the existing plan instead.",
}


def _cents(amount: Optional[Decimal]) -> Optional[int]:
    """Decimal money -> integer cents. None stays None: not known is not zero."""
    if amount is None:
        return None
    return int((amount * 100).quantize(Decimal("1")))


def _blocker(code: str, **extra) -> Dict[str, Any]:
    row = {"code": code, "message": BLOCKER_TEXT.get(code, code)}
    row.update(extra)
    return row


def customer_org_for(db: Session, opp) -> Optional[Any]:
    """The customer Organization this deal produced, or None.

    Goes through the SAME two joins the compensation bridge uses —
    `Opportunity.customer_organization_id`, else the Implementation row — so
    billing and compensation can never disagree about which customer a deal
    became. A third way of answering this question is how two subsystems end up
    charging and paying on different organizations.
    """
    from app.models.implementation_models import Implementation
    from app.models.models import Organization

    org_id = getattr(opp, "customer_organization_id", None)
    if not org_id:
        impl = (db.query(Implementation)
                .filter(Implementation.opportunity_id == opp.id)
                .order_by(Implementation.created_at.desc())
                .first())
        org_id = getattr(impl, "organization_id", None)
    if not org_id:
        return None
    return db.query(Organization).filter(Organization.id == org_id).first()


def mapped_plan(db: Session, platform_id: Optional[str], package):
    """The BrandBillingPlan a sold package maps to, via configured data only.

    `BrandPackage.billing_plan_key` is the mapping. It is deliberately NOT
    inferred from `BrandPackage.key`: those are two vocabularies that share
    some words today ("starter", "growth") and would diverge the first time a
    brand renames a package or sells one that is not a subscription tier. A
    coincidental match is not a configuration.
    """
    key = getattr(package, "billing_plan_key", None)
    if not key:
        return None, B_PACKAGE_UNMAPPED
    plan = billing_catalog.resolve_plan(db, platform_id, key)
    if plan is None:
        return None, B_PLAN_UNAVAILABLE
    return plan, None


def terms_for(db: Session, opp) -> Dict[str, Any]:
    """What this deal would charge, and everything standing in the way.

    Never raises and never charges. Returns the whole picture so a caller can
    render it to a salesperson before anybody's card is touched.
    """
    res = deal_pricing.resolve(db, opp)
    setup = res["implementation_fee"]
    mrr = res["mrr"]

    blockers: List[Dict[str, Any]] = []
    out: Dict[str, Any] = {
        "opportunity_id": opp.id,
        "stage": getattr(opp, "stage", None),
        "pricing_source": res["source_label"],
        "structure": res["structure"],
        "structure_label": res["structure_label"],
        "proposal_id": res["proposal_id"],
        "proposal_number": res["proposal_number"],
        "package_name": res["package_name"],
        "is_custom_rate": res["is_custom_rate"],
        "term_months": res["term_months"],
        # Money as cents + a decimal string. Never a float.
        "setup_cents": _cents(setup),
        "setup_amount": None if setup is None else str(setup),
        "recurring_cents": _cents(mrr),
        "recurring_amount": None if mrr is None else str(mrr),
        "interval": "month",
        "plan": None,
        "customer_organization_id": None,
        "stripe_customer_exists": False,
        "charges": [],
        "blockers": blockers,
    }

    if not res["pricing_complete"] and setup is None and mrr is None:
        blockers.append(_blocker(B_PRICING_INCOMPLETE,
                                 detail=res["incomplete_reason"]))
        return out
    if setup is None and mrr is None:
        blockers.append(_blocker(B_NOTHING_TO_CHARGE))
        return out

    if (getattr(opp, "stage", None) or "").lower() != "won":
        blockers.append(_blocker(B_NOT_WON))

    org = customer_org_for(db, opp)
    if org is None:
        blockers.append(_blocker(B_NO_CUSTOMER_ORG))
        return out

    out["customer_organization_id"] = org.id
    out["customer_organization_name"] = org.name
    out["stripe_customer_exists"] = bool(getattr(org, "stripe_customer_id", None))

    # ── the recurring half ────────────────────────────────────────────────
    if mrr is not None:
        platform_id = billing_catalog.platform_id_for_org(db, org)
        if not platform_id:
            blockers.append(_blocker(B_NO_BRAND))
        else:
            plan, why = mapped_plan(db, platform_id, res["package"])
            if why:
                blockers.append(_blocker(
                    why, package=res["package_name"],
                    billing_plan_key=getattr(res["package"], "billing_plan_key", None)))
            else:
                price_id = billing_catalog.stripe_price_id_for(plan, "month")
                cat_cents = billing_catalog.price_cents_for(plan, "month")
                out["plan"] = {"key": plan.key, "name": plan.name,
                               "catalogue_cents": cat_cents,
                               "stripe_price_configured": bool(price_id)}
                if not price_id:
                    blockers.append(_blocker(B_NO_PRICE_ID, plan=plan.key))
                elif cat_cents is not None and _cents(mrr) != cat_cents:
                    # Reported, never silently resolved either way.
                    blockers.append(_blocker(
                        B_RATE_MISMATCH, plan=plan.key,
                        deal_cents=_cents(mrr), catalogue_cents=cat_cents))
                else:
                    out["charges"].append({
                        "kind": "subscription", "plan": plan.key,
                        "interval": "month", "cents": cat_cents,
                        "stripe_price_id_configured": True})

        existing = (getattr(org, "billing_status", None) or "").lower()
        if getattr(org, "stripe_subscription_id", None):
            from app.routers.billing_router import SubscriptionStatus
            if existing in SubscriptionStatus.OCCUPIED:
                blockers.append(_blocker(B_SUBSCRIPTION_EXISTS))

    # ── the one-time half ─────────────────────────────────────────────────
    if setup is not None and _cents(setup):
        out["charges"].append({"kind": "setup_fee", "cents": _cents(setup),
                               "label": "Implementation fee"})

    out["billable"] = bool(out["charges"]) and not blockers
    return out
