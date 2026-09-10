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

===========================================================================
THE ONE EXCEPTION: A CUSTOM DEAL
===========================================================================

A Custom package's price is not in the catalogue — that is what the word
means. So the rule above, applied to it, would refuse every custom deal
forever: its rate can never equal a tier it was created to differ from.

A custom recurring rate is therefore billed as an INLINE Stripe price at the
deal's own figure — on the same footing as the setup fee above, and for the
same reason: every path that can write a custom rate has already passed this
platform's pricing authority. On the opportunity, `sales_router` judges the
figures with `pricing_authority.evaluate()` before writing and routes a breach
to a manager; on a proposal, only a manager can set one at all.

What IS refused is a real signal rather than an absent one: a figure a manager
DENIED, and a rate whose approval decision is still PENDING. See
`custom_recurring_authority()`, which also records why demanding an approval
row instead would make every genuine Custom deal permanently unbillable.

This narrows nothing for standard tiers: a Starter, Growth or Professional
deal still bills only against its brand's configured Stripe price.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.billing_models import BillingCommitment
from app.services import billing_catalog, deal_pricing, plan_limits

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
B_CUSTOM_DENIED = "custom_recurring_rate_was_denied"
B_CUSTOM_DECISION_PENDING = "custom_pricing_decision_still_pending"

# HOW a recurring charge gets its amount. The router branches on this rather
# than on "is `plan` None", so a future third mode has to declare itself here
# instead of arriving as an absence somebody else's code reads as a default.
PRICING_CATALOGUE = "catalogue_price"     # the brand's configured Stripe price
PRICING_CUSTOM = "custom_approved"        # an inline price at an approved figure

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
        "The mapped billing plan has no Stripe price configured for what this "
        "deal agreed to. A term agreement and a month-to-month deal are two "
        "different prices on the same plan, and each needs its own.",
    B_RATE_MISMATCH:
        "The monthly rate negotiated on this deal does not match the catalogue "
        "rate for the mapped plan. Charging either one silently would be wrong; "
        "align the deal, the mapping or the catalogue.",
    B_NOTHING_TO_CHARGE:
        "This deal has neither a setup fee nor a recurring rate to charge.",
    B_SUBSCRIPTION_EXISTS:
        "This customer already has a subscription. Starting another checkout "
        "would bill them twice — change the existing plan instead.",
    B_CUSTOM_DENIED:
        "A manager denied this exact monthly rate. It must not be charged. "
        "Agree a rate that was not refused, or have the decision revisited.",
    B_CUSTOM_DECISION_PENDING:
        "A custom pricing decision on this deal is still waiting on a manager. "
        "The rate is not settled yet, so charging it now could bill the customer "
        "an amount that is about to change.",
}


def _cents(amount: Optional[Decimal]) -> Optional[int]:
    """Decimal money -> integer cents. None stays None: not known is not zero."""
    if amount is None:
        return None
    return int((amount * 100).quantize(Decimal("1")))


# ── which obligation a blocker actually blocks ──────────────────────────────
# The setup fee and the subscription are collected separately, so a reason that
# only concerns one of them must not hold the other hostage. "This customer
# already has a subscription" is a real reason not to start a second one; it is
# not a reason the implementation fee cannot be collected. Blocking both on it
# is the same coupling that produced one combined charge, moved into readiness.
#
# Anything NOT listed here blocks BOTH, on purpose: a new blocker is assumed to
# be about the deal as a whole until somebody decides otherwise. Failing closed
# is the right default when the question is whether to charge someone.
BOTH_PARTS = ("setup", "subscription")
SUBSCRIPTION_ONLY = ("subscription",)

BLOCKER_SCOPE = {
    # All of these are about the recurring half's plumbing — a plan mapping, a
    # Stripe price, a rate comparison, an existing subscription. None of them
    # says anything about whether the one-time fee is agreed or collectable.
    B_NO_BRAND: SUBSCRIPTION_ONLY,
    B_PACKAGE_UNMAPPED: SUBSCRIPTION_ONLY,
    B_PLAN_UNAVAILABLE: SUBSCRIPTION_ONLY,
    B_NO_PRICE_ID: SUBSCRIPTION_ONLY,
    B_RATE_MISMATCH: SUBSCRIPTION_ONLY,
    B_SUBSCRIPTION_EXISTS: SUBSCRIPTION_ONLY,
    # The custom-pricing approvals are decisions about the MONTHLY figure
    # specifically — `_custom_authority` compares monthly cents and nothing
    # else — so an unsettled one does not make the setup fee unsettled.
    B_CUSTOM_DENIED: SUBSCRIPTION_ONLY,
    B_CUSTOM_DECISION_PENDING: SUBSCRIPTION_ONLY,
}


def blocks_part(blocker: Dict[str, Any], part: str) -> bool:
    """Does this blocker stand in the way of billing `part`?"""
    return part in blocker.get("applies_to", BOTH_PARTS)


def blockers_for_part(blockers, part: str):
    return [b for b in blockers if blocks_part(b, part)]


def _blocker(code: str, **extra) -> Dict[str, Any]:
    row = {"code": code, "message": BLOCKER_TEXT.get(code, code),
           "applies_to": list(BLOCKER_SCOPE.get(code, BOTH_PARTS))}
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


def implementation_for(db: Session, opp) -> Optional[Any]:
    """The Implementation row this deal produced, or None.

    The setup fee's payment state lives on it, because the implementation IS
    what the setup fee pays for. Resolved by the same join `customer_org_for`
    uses, so the two cannot disagree about which record a deal became.
    """
    from app.models.implementation_models import Implementation
    return (db.query(Implementation)
            .filter(Implementation.opportunity_id == opp.id)
            .order_by(Implementation.created_at.desc())
            .first())


def billing_state(db: Session, opp) -> dict:
    """The TWO obligations and where each one stands. Never one merged status.

    ═══════════════════════════════════════════════════════════════════════
    A SINGLE "paid" FLAG CANNOT ANSWER THIS QUESTION
    ═══════════════════════════════════════════════════════════════════════
    A customer who has paid to be implemented but not started their
    subscription is a normal state. So is one whose subscription is running
    while the implementation fee is still outstanding. Collapsing them loses
    both, and it is what let a $3,500 combined charge look reasonable.

    SETUP reads from the Implementation, which only the webhook marks paid.
    SUBSCRIPTION reads from the Organization, where the subscription and
    invoice webhooks already keep the authoritative state — it is not copied,
    because a copy is a second answer that goes stale.
    """
    impl = implementation_for(db, opp)
    org = customer_org_for(db, opp)

    setup_status = getattr(impl, "setup_payment_status", None) or "not_sent"
    sub_status = getattr(org, "billing_status", None)

    return {
        "setup": {
            # not_sent | checkout_pending | paid | failed
            "status": setup_status,
            "checkout_url": getattr(impl, "setup_checkout_url", None),
            "checkout_session_id": getattr(impl, "setup_checkout_session_id", None),
            "payment_intent_id": getattr(impl, "setup_payment_intent_id", None),
            "paid_cents": getattr(impl, "setup_paid_cents", None),
            "paid_at": getattr(impl, "setup_paid_at", None),
        },
        "subscription": {
            # NULL means no subscription has ever existed — deliberately not
            # "inactive", which would read as one that stopped.
            "status": sub_status,
            "stripe_subscription_id": getattr(org, "stripe_subscription_id", None),
            "plan_key": getattr(org, "billing_plan_key", None),
            "current_period_end": getattr(org, "billing_current_period_end", None),
            "checkout_url": getattr(impl, "subscription_checkout_url", None),
            "checkout_session_id": getattr(
                impl, "subscription_checkout_session_id", None),
            "checkout_at": getattr(impl, "subscription_checkout_at", None),
        },
    }


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


def commitment_for(res: Dict[str, Any]) -> str:
    """Which of the plan's two monthly prices this deal agreed to.

    Read off the deal's own resolved STRUCTURE rather than off `billing_option`
    directly, because the structure is what `package_pricing.quote()` actually
    priced. A deal whose billing option says term_agreement but which produced
    no committed term came back as month-to-month, and the amount already
    quoted to the customer is the month-to-month one; billing must charge what
    was quoted, not what the option field aspired to.

    Anything that is not clearly month-to-month resolves to the committed rate,
    which is the pre-existing behaviour of `monthly_cents`.
    """
    if res.get("structure") == deal_pricing.STRUCTURE_M2M:
        return BillingCommitment.MONTH_TO_MONTH
    return BillingCommitment.TERM


def _custom_product_name(res: Dict[str, Any]) -> str:
    """What the customer will see on the invoice line for a custom rate.

    The sold package's own name where there is one, because that is the word
    the customer's paperwork already uses. No amount and no unit basis go in
    here: Stripe renders the amount itself, and a name that restates it becomes
    wrong the moment the subscription is ever changed.
    """
    name = (res.get("package_name") or "").strip()
    return "%s — monthly" % name if name else "Monthly subscription"


def _request_monthly_cents(req) -> Optional[int]:
    """What monthly figure a custom-deal request is asking for, in cents.

    `requested_unit_price` x `requested_min_units` — the same arithmetic
    `package_pricing.custom_rate()` uses to turn the agreement into a monthly
    rate, so a request can be compared to a deal's MRR without either side
    re-deriving it differently.
    """
    unit = getattr(req, "requested_unit_price", None)
    if unit is None:
        return None
    units = getattr(req, "requested_min_units", None)
    try:
        units = int(units) if units is not None else 1
    except (TypeError, ValueError):
        units = 1
    if units < 1:
        units = 1
    return _cents(Decimal(str(unit)) * Decimal(units))


def custom_recurring_authority(db: Session, opp,
                               mrr: Optional[Decimal]) -> Dict[str, Any]:
    """On whose authority this custom monthly rate may be charged — or why not.

    WHY THIS IS NOT "FIND AN APPROVAL, OR REFUSE"
    ---------------------------------------------
    That was the first version of this function, and it was wrong: it would
    have made every real Custom deal permanently unbillable.

    A custom-deal approval REQUEST only comes into existence when
    `pricing_authority.evaluate()` returns NEEDS_APPROVAL, which requires a
    catalogue rate to measure a discount against. The EvoSys "Multi-Tenant /
    Custom" package has no monthly rate and no setup fee — that is the entire
    point of it — so `discount_pct` is None, every component reports
    `within: True`, the verdict is ALLOWED, and NO REQUEST IS EVER CREATED.
    Demanding an approved request would therefore refuse the deal while making
    the approval that would clear it impossible to obtain. A blocker nobody can
    act on is worse than no check.

    WHERE THE AUTHORITY ACTUALLY IS. Every path that writes a custom recurring
    rate already goes through this platform's own pricing authority:

      on the OPPORTUNITY — `sales_router` judges the proposed figures with
      `pricing_authority.evaluate()` BEFORE writing, and writes only on
      ALLOWED; a breach becomes a request that a manager decides, and
      `approve_custom_deal()` does the writing with the manager's authority.

      on a PROPOSAL — `proposal_service.apply_custom_rate()` requires
      `can_override_price` (sales manager or god). A rep cannot set one, and no
      route writes those columns straight from a request body.

    So a custom rate that EXISTS has already passed the same gate the setup fee
    passed — and this module already charges the setup fee on exactly that
    reasoning (see the module docstring). Treating the recurring half
    differently would not be stricter; it would just be inconsistent.

    WHAT IS WORTH REFUSING, THEN. Two things that are real signals rather than
    an absence of one:

      a manager DENIED this exact figure. Whatever put it on the deal, a
      refused amount must not reach a card.

      a custom pricing decision is still PENDING on this deal. The rate is
      under review; billing now can charge an amount that is about to change.

    Both clear the moment a person decides, so neither is a dead end.
    """
    out: Dict[str, Any] = {"basis": None, "approval_id": None,
                           "approved_at": None, "blocker": None,
                           "blocker_extra": {}}
    if mrr is None:
        return out

    from app.models.sales_models import (APPROVAL_APPROVED, APPROVAL_DENIED,
                                         APPROVAL_PENDING,
                                         PricingApprovalRequest)

    want = _cents(mrr)
    rows = (db.query(PricingApprovalRequest)
            .filter(PricingApprovalRequest.opportunity_id == opp.id,
                    PricingApprovalRequest.request_kind == "custom_deal")
            .order_by(PricingApprovalRequest.requested_at.desc())
            .all())

    # Refused at this exact amount. Checked FIRST — a denial is the one signal
    # that must not be overtaken by anything else on the deal.
    for req in rows:
        if (req.status == APPROVAL_DENIED
                and _request_monthly_cents(req) == want):
            out["blocker"] = B_CUSTOM_DENIED
            out["blocker_extra"] = {"deal_cents": want,
                                    "denied_request_id": req.id}
            return out

    # A question still open with a manager. At ANY figure: what is pending is
    # the price, so "the deal already stands at something chargeable" is not a
    # reason to charge it while somebody is deciding.
    for req in rows:
        if req.status == APPROVAL_PENDING:
            out["blocker"] = B_CUSTOM_DECISION_PENDING
            out["blocker_extra"] = {"pending_request_id": req.id,
                                    "pending_cents": _request_monthly_cents(req),
                                    "deal_cents": want}
            return out

    # A manager approved this figure explicitly. Reported because it is the
    # strongest provenance there is, not because the charge depends on it.
    for req in rows:
        if (req.status == APPROVAL_APPROVED
                and _request_monthly_cents(req) == want):
            out["basis"] = "manager_approval"
            out["approval_id"] = req.id
            out["approved_at"] = req.decided_at
            return out

    # Set under the actor's own authority, which pricing_authority granted at
    # the time it was written. The same standing as the setup fee.
    out["basis"] = "pricing_authority"
    return out


def terms_for(db: Session, opp) -> Dict[str, Any]:
    """What this deal would charge, and everything standing in the way.

    Never raises and never charges. Returns the whole picture so a caller can
    render it to a salesperson before anybody's card is touched.
    """
    res = deal_pricing.resolve(db, opp)
    setup = res["implementation_fee"]
    mrr = res["mrr"]

    # The commercial commitment, resolved once and used everywhere below, so the
    # price lookup, the blocker detail and the rendered label cannot disagree.
    commitment = commitment_for(res)
    commitment_label = billing_catalog.commitment_label(
        commitment, res.get("term_months"))

    blockers: List[Dict[str, Any]] = []
    out: Dict[str, Any] = {
        "opportunity_id": opp.id,
        "stage": getattr(opp, "stage", None),
        # Both, because they legitimately differ: a provisioned customer is
        # status Won at stage Onboarding, and a screen that shows only the stage
        # makes the Won check look wrong when it is right.
        "status": getattr(opp, "status", None),
        "pricing_source": res["source_label"],
        "structure": res["structure"],
        "structure_label": res["structure_label"],
        "proposal_id": res["proposal_id"],
        "proposal_number": res["proposal_number"],
        "package_name": res["package_name"],
        "is_custom_rate": res["is_custom_rate"],
        "term_months": res["term_months"],
        # WHAT THE CUSTOMER COMMITTED TO, named. A recurring figure with no
        # commitment beside it is two different deals sharing one number.
        "commitment": commitment,
        "commitment_label": commitment_label,
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
        # DEFAULTS THAT SURVIVE AN EARLY RETURN. Two paths below bail out as
        # soon as there is nothing to charge, and a caller that reads
        # `billable_parts["setup"]` must get False there rather than a
        # KeyError — an exception on a money screen renders as a blank panel,
        # which reads as "fine" to whoever is looking at it.
        "billable": False,
        "billable_parts": {"setup": False, "subscription": False},
        "state": None,
    }

    if not res["pricing_complete"] and setup is None and mrr is None:
        blockers.append(_blocker(B_PRICING_INCOMPLETE,
                                 detail=res["incomplete_reason"]))
        return out
    if setup is None and mrr is None:
        blockers.append(_blocker(B_NOTHING_TO_CHARGE))
        return out

    # WON IS `status`, NOT `stage`, AND THE DIFFERENCE WAS A HARD BLOCKER.
    #
    # This read `opp.stage` and, in the flow it exists to serve, NOTHING COULD
    # EVER BE BILLED. `provisioning.provision_customer` moves the deal to
    # STAGE_ONBOARDING at the moment it creates the customer organization, and
    # says so in as many words: "status stays 'won' and won_at is untouched:
    # this deal was won, and every Won metric in the codebase filters on
    # status." So billing required a customer organization (which only
    # provisioning creates) AND a stage of "won" (which provisioning
    # immediately ends) — two conditions the normal path cannot satisfy at the
    # same time. Live verification is what found it; the unit tests all set
    # stage and status together and never saw it.
    #
    # `status` is also what `compensation.earn()` checks. Billing and
    # compensation reading the same field is the point: the payment this module
    # produces is what compensation is calculated from, and two subsystems with
    # two definitions of Won would eventually disagree about one deal.
    #
    # `stage` is deliberately NOT accepted as a fallback. `status` is NOT NULL
    # on the model, so there is no legacy row with nothing to read — and a
    # fallback would reintroduce exactly the thing this comment is about: a
    # second definition of Won, under which a deal parked at stage "won" with
    # status "open" is chargeable but earns nobody a commission.
    _status = (getattr(opp, "status", None) or "").lower()
    if _status != "won":
        blockers.append(_blocker(B_NOT_WON, status=_status or None,
                                 stage=(getattr(opp, "stage", None) or None)))

    org = customer_org_for(db, opp)
    if org is None:
        blockers.append(_blocker(B_NO_CUSTOMER_ORG))
        return out

    out["customer_organization_id"] = org.id
    out["customer_organization_name"] = org.name
    out["stripe_customer_exists"] = bool(getattr(org, "stripe_customer_id", None))

    # ── the recurring half ────────────────────────────────────────────────
    if mrr is not None:
        if res["is_custom_rate"]:
            # A CUSTOM RATE IS BILLED AS AN INLINE RECURRING PRICE, at the
            # deal's own agreed figure. It deliberately does NOT go through the
            # catalogue: requiring a custom rate to equal one of the standard
            # tiers would make custom deals unbillable, and matching it against
            # the nearest tier would bill an amount nobody agreed to.
            #
            # The rate's authority — and the two things worth refusing — come
            # from custom_recurring_authority(); read its docstring before
            # tightening this, because the obvious tightening (demand an
            # approved request) makes every real Custom deal unbillable.
            auth = custom_recurring_authority(db, opp, mrr)
            out["custom_pricing"] = {
                "authority": auth["basis"],
                "approval_id": auth["approval_id"],
                "approved_at": auth["approved_at"],
                # WHAT THIS CUSTOMER IS ACTUALLY ENTITLED TO, read from the
                # entitlement engine rather than asserted here.
                #
                # An inline price belongs to no catalogue plan, so the webhook
                # correctly leaves `billing_plan_key` unset. `plan_limits` then
                # falls through to this customer's own recorded agreement, and
                # says plainly when there isn't one — which is NEEDS
                # CONFIGURATION, not a decision, and not unlimited.
                "entitlement": plan_limits.entitlement_state(db, org),
                # Named where the sold package DOES map to a tier, so whoever
                # decides that policy can see which tier was on the table. It
                # is deliberately not applied: writing this key onto the org
                # while the subscription runs on an inline price would leave
                # every plan-change flow driving off a price the customer is
                # not on, and would show them a catalogue rate they are not
                # paying.
                "mapped_plan_key": getattr(res["package"], "billing_plan_key", None),
            }
            if auth["blocker"]:
                blockers.append(_blocker(auth["blocker"],
                                         **auth["blocker_extra"]))
            else:
                out["charges"].append({
                    "kind": "subscription",
                    "pricing_mode": PRICING_CUSTOM,
                    # No catalogue plan is involved, and saying so explicitly
                    # keeps the webhook from mapping this org onto a tier.
                    "plan": None,
                    "interval": "month",
                    "commitment": commitment,
                    "cents": _cents(mrr),
                    "label": _custom_product_name(res),
                    "authority": auth["basis"],
                    "approval_id": auth["approval_id"],
                    "stripe_price_id_configured": False})
        else:
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
                    # WHICH OF THE PLAN'S TWO MONTHLY PRICES THIS DEAL AGREED TO.
                    # A month-to-month deal must resolve the no-commitment rate;
                    # resolving the term rate would hand it a discount it never
                    # committed for, and would then compare the deal's own
                    # correctly-quoted $597 against a $500 catalogue figure and
                    # report a mismatch on a deal that is perfectly consistent.
                    price_id = billing_catalog.stripe_price_id_for(
                        plan, "month", commitment)
                    cat_cents = billing_catalog.price_cents_for(
                        plan, "month", commitment)
                    out["plan"] = {"key": plan.key, "name": plan.name,
                                   "catalogue_cents": cat_cents,
                                   "commitment": commitment,
                                   "stripe_price_configured": bool(price_id)}
                    if cat_cents is None and not price_id:
                        # The plan exists but has nothing configured for THIS
                        # commitment. Named as the missing-price case rather
                        # than falling back to the other commitment's rate.
                        blockers.append(_blocker(
                            B_NO_PRICE_ID, plan=plan.key,
                            commitment=commitment, detail=(
                                "No %s price is configured for this plan."
                                % commitment_label)))
                    elif not price_id:
                        blockers.append(_blocker(B_NO_PRICE_ID, plan=plan.key,
                                                 commitment=commitment))
                    elif cat_cents is not None and _cents(mrr) != cat_cents:
                        # Reported, never silently resolved either way.
                        blockers.append(_blocker(
                            B_RATE_MISMATCH, plan=plan.key,
                            commitment=commitment,
                            deal_cents=_cents(mrr), catalogue_cents=cat_cents))
                    else:
                        out["charges"].append({
                            "kind": "subscription",
                            "pricing_mode": PRICING_CATALOGUE,
                            "plan": plan.key,
                            "interval": "month",
                            "commitment": commitment,
                            "cents": cat_cents,
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

    # ── readiness, PER OBLIGATION ─────────────────────────────────────────
    # `billable` is the whole-deal answer and stays exactly as it was, because
    # other callers read it. But the two halves are collected separately, so
    # each needs its own answer: a customer who already has a subscription can
    # still be sent an implementation-fee page, and a plan whose Stripe price
    # is missing does not make the setup fee uncollectable.
    _kinds = {c["kind"] for c in out["charges"]}
    out["billable_parts"] = {
        "setup": ("setup_fee" in _kinds
                  and not blockers_for_part(blockers, "setup")),
        "subscription": ("subscription" in _kinds
                         and not blockers_for_part(blockers, "subscription")),
    }

    # ── where each obligation actually stands ─────────────────────────────
    # Attached to the same payload the seller's panel already reads, so the
    # screen showing WHAT would be charged is the screen showing WHAT HAS BEEN.
    # A second endpoint for the state would be a second round trip and a second
    # moment, and the two could disagree on screen.
    try:
        out["state"] = billing_state(db, opp)
    except Exception:                                   # pragma: no cover
        # Readiness must still render if the state read fails; a panel that
        # goes blank tells a rep less than one that shows the terms.
        log.exception("deal_billing: could not resolve billing state")
        out["state"] = None
    return out
