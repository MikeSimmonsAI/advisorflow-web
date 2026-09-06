"""MAY THIS PERSON QUOTE THIS PRICE?

One question, one answer, one place. Every caller that can move money on a deal
asks here, so "a rep may discount 10%" is a fact with a single definition rather
than a rule re-implemented per endpoint and drifting between them.

EACH COMPONENT IS JUDGED ON ITS OWN
-----------------------------------
Setup and monthly carry separate ceilings and are checked separately. The trade
this exists to catch is a deal that guts the recurring rate and rescues the
total by inflating the one-time fee: the TCV passes, and the business has sold
its actual revenue stream for a bigger cheque today. So a component that
breaches its own ceiling breaches, whatever the total does.

THE ANSWER IS NEVER "NO"
------------------------
It is ALLOWED, or it is NEEDS_APPROVAL. A rep who negotiated something below
the floor has not done something forbidden - they have done something a manager
has to agree to, and the product's job is to route it, not to lose it. Only a
malformed or nonsensical request is refused outright.

WHAT THIS DOES NOT DO
---------------------
It does not write prices. Callers apply the price when this says they may, and
raise an approval request when it says they must. Keeping the judgement out of
the writing is what lets the approval path re-ask the same question later with
the manager as the actor and get a consistent answer.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.pricing_policy_models import (
    ELEVATED_PLATFORM_ROLES, ELEVATED_SALES_ROLES,
    FALLBACK_ELEVATED_MAX_DISCOUNT_PCT, FALLBACK_REP_MAX_DISCOUNT_PCT,
    PricingPolicy,
)
from app.models.sales_models import ROLE_SALES_MANAGER, ROLE_SALES_REP
from app.services import package_pricing as pp
from app.services import sales_access as sa

# Outcomes.
ALLOWED         = "allowed"
NEEDS_APPROVAL  = "needs_approval"
REFUSED         = "refused"

# Components, named so a breach can say which half of the money it is about.
COMPONENT_SETUP   = "implementation"
COMPONENT_MONTHLY = "monthly"
COMPONENT_TERM    = "term"


def _dec(v) -> Optional[Decimal]:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


# ── Who the actor is, in pricing terms ───────────────────────────────────────

def actor_role(user, db: Session, brand_sales_org_id: Optional[str]) -> str:
    """The role this policy engine should price against.

    god_admin and super_admin resolve to manager rather than to a role of their
    own: platform authority is broader than a sales manager's everywhere else in
    this codebase, and inventing a third tier here would mean a policy row could
    accidentally fence the platform owner out of their own pricing.
    """
    if getattr(user, "role", None) in ELEVATED_PLATFORM_ROLES:
        return ROLE_SALES_MANAGER
    if sa.is_sales_manager(user, db, brand_sales_org_id):
        return ROLE_SALES_MANAGER
    return ROLE_SALES_REP


def _is_elevated(role: str) -> bool:
    return role in (ROLE_SALES_MANAGER,) or role in ELEVATED_SALES_ROLES


# ── Resolving the policy ─────────────────────────────────────────────────────

def resolve_policy(db: Session, brand_sales_org_id: Optional[str],
                   role: str) -> Optional[PricingPolicy]:
    """Most specific match wins. See PricingPolicy's docstring for the order."""
    rows = (db.query(PricingPolicy)
            .filter(PricingPolicy.is_active.is_(True))
            .all())
    if not rows:
        return None

    def rank(p: PricingPolicy) -> int:
        org_match  = (p.brand_sales_org_id == brand_sales_org_id)
        role_match = (p.role == role)
        if org_match and role_match:
            return 4
        if org_match and p.role is None:
            return 3
        if p.brand_sales_org_id is None and role_match:
            return 2
        if p.brand_sales_org_id is None and p.role is None:
            return 1
        return 0

    scored = [(rank(p), p) for p in rows]
    scored = [(r, p) for r, p in scored if r > 0]
    if not scored:
        return None
    scored.sort(key=lambda t: t[0], reverse=True)
    return scored[0][1]


def ceilings_for(db: Session, brand_sales_org_id: Optional[str],
                 role: str) -> Dict[str, Any]:
    """The discount ceilings that apply, and where they came from.

    `None` for a percentage means UNLIMITED, not zero - a distinction the
    policy table is explicit about and this must not flatten.
    """
    policy = resolve_policy(db, brand_sales_org_id, role)
    if policy is not None:
        return {
            "source": "policy",
            "policy_id": policy.id,
            "max_discount_pct_setup": _dec(policy.max_discount_pct_setup),
            "max_discount_pct_monthly": _dec(policy.max_discount_pct_monthly),
            "min_term_months": policy.min_term_months,
        }
    # No policy configured. Fall back to the behaviour that existed before this
    # system: a rep discounts nothing, an elevated role is unbounded. Installing
    # the engine changes nobody's authority until somebody configures a policy.
    if _is_elevated(role):
        pct = FALLBACK_ELEVATED_MAX_DISCOUNT_PCT
    else:
        pct = Decimal(FALLBACK_REP_MAX_DISCOUNT_PCT)
    return {
        "source": "fallback",
        "policy_id": None,
        "max_discount_pct_setup": pct,
        "max_discount_pct_monthly": pct,
        "min_term_months": None,
    }


# ── Catalogue reference points ───────────────────────────────────────────────

def _catalogue_setup(pkg) -> Optional[Decimal]:
    """The package's own one-time fee, WITHOUT any per-deal override.

    Passing opp=None is the whole point: the override is the thing being
    measured, so measuring it against itself would report every deal as a 0%
    discount.
    """
    return pp.implementation_fee(pkg, None)


def _catalogue_monthly(pkg, billing_option: Optional[str]) -> Optional[Decimal]:
    """The catalogue rate for the option being quoted.

    A term deal is measured against the CONTRACTED rate, not the
    month-to-month one. Measuring against month-to-month would report the
    ordinary term discount - the one the catalogue itself offers - as a breach
    on every single agreement.
    """
    return pp.monthly_rate(pkg, billing_option, custom=None)


def discount_pct(catalogue: Optional[Decimal],
                 quoted: Optional[Decimal]) -> Optional[Decimal]:
    """How far below catalogue this quote sits, as a percentage.

    None when there is nothing to compare against - an uncatalogued package has
    no reference price, so it has no discount, and reporting 0% would claim a
    comparison that was never possible. A quote ABOVE catalogue returns a
    negative number and is never a breach.
    """
    if catalogue is None or quoted is None:
        return None
    if catalogue <= 0:
        return None
    return ((catalogue - quoted) / catalogue) * Decimal(100)


# ── The judgement ────────────────────────────────────────────────────────────

def evaluate(db: Session, user, pkg, *,
             brand_sales_org_id: Optional[str],
             billing_option: Optional[str],
             proposed_setup: Optional[Any] = None,
             proposed_monthly: Optional[Any] = None,
             proposed_term_months: Optional[int] = None) -> Dict[str, Any]:
    """May this actor quote these figures on this package?

    Anything passed as None is "not being changed" and is not judged. A caller
    editing only the monthly rate does not have its setup fee re-examined
    against a ceiling it was already inside.
    """
    role = actor_role(user, db, brand_sales_org_id)
    caps = ceilings_for(db, brand_sales_org_id, role)

    breaches: List[Dict[str, Any]] = []
    components: List[Dict[str, Any]] = []

    def judge(component: str, catalogue: Optional[Decimal],
              quoted: Optional[Decimal], ceiling: Optional[Decimal]) -> None:
        if quoted is None:
            return
        pct = discount_pct(catalogue, quoted)
        row = {
            "component": component,
            "catalogue": float(catalogue) if catalogue is not None else None,
            "quoted": float(quoted),
            "discount_pct": float(pct) if pct is not None else None,
            "ceiling_pct": float(ceiling) if ceiling is not None else None,
            "within": True,
        }
        # An unlimited ceiling, or nothing to measure against, is inside by
        # definition. A discount of zero or less is too.
        if ceiling is not None and pct is not None and pct > ceiling:
            row["within"] = False
            row["over_by_pct"] = float(pct - ceiling)
            breaches.append(row)
        components.append(row)

    judge(COMPONENT_SETUP, _catalogue_setup(pkg), _dec(proposed_setup),
          caps["max_discount_pct_setup"])
    judge(COMPONENT_MONTHLY, _catalogue_monthly(pkg, billing_option),
          _dec(proposed_monthly), caps["max_discount_pct_monthly"])

    # A term shorter than the policy allows is a discount wearing a commitment:
    # the agreement rate is earned by the length, so selling the rate with less
    # of the length is the same giveaway by another route.
    min_term = caps.get("min_term_months")
    if (min_term and proposed_term_months
            and int(proposed_term_months) < int(min_term)):
        row = {"component": COMPONENT_TERM, "catalogue": None,
               "quoted": int(proposed_term_months),
               "discount_pct": None, "ceiling_pct": None,
               "min_term_months": int(min_term), "within": False}
        breaches.append(row)
        components.append(row)

    return {
        "outcome": NEEDS_APPROVAL if breaches else ALLOWED,
        "role": role,
        "ceilings": {
            "source": caps["source"],
            "policy_id": caps["policy_id"],
            "max_discount_pct_setup": (float(caps["max_discount_pct_setup"])
                                       if caps["max_discount_pct_setup"] is not None else None),
            "max_discount_pct_monthly": (float(caps["max_discount_pct_monthly"])
                                         if caps["max_discount_pct_monthly"] is not None else None),
            "min_term_months": caps.get("min_term_months"),
        },
        "components": components,
        "breaches": breaches,
        "summary": _summary(breaches),
    }


def _summary(breaches: List[Dict[str, Any]]) -> Optional[str]:
    """One sentence a screen can show without re-deriving anything."""
    if not breaches:
        return None
    parts = []
    for b in breaches:
        if b["component"] == COMPONENT_TERM:
            parts.append("a %d-month term is below the %d-month minimum"
                         % (b["quoted"], b["min_term_months"]))
        else:
            parts.append("%s is %.1f%% below catalogue, past the %.1f%% limit"
                         % (b["component"], b["discount_pct"], b["ceiling_pct"]))
    return "Manager approval required: " + "; ".join(parts) + "."
