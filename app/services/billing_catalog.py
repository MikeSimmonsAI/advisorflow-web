"""
THE SERVER'S ONLY SOURCE OF SUBSCRIPTION PRICE.

Everything about what a customer organization is charged is resolved here,
from the brand that owns them and a plan key. The browser identifies a plan by
name and an interval by name. It never sends an amount, and it never sends a
Stripe price id, because both are things a customer would be delighted to
choose for themselves.

WHAT THIS REPLACES

A module-global `PLANS` dict in billing_router.py, plus a second, independently
maintained copy of the same prices as a literal array in the React bundle. The
two already disagreed about the annual discount before anyone noticed. Neither
could be scoped to a brand, so a second white-label brand's customers would
have been shown, and charged, EvoSys Pro's prices.

THE OTHER CATALOGUE

`sales_models.BrandPackage` is a DIFFERENT catalogue for a DIFFERENT purpose:
what the sales team sells (one-time implementation fees, $1,497 / $2,495 /
$4,995) and what compensation is calculated from. It keys on the same three
strings as this one. Nothing in this module reads it, and nothing in this
module may translate between them - `BrandPackage.billing_plan_key` is the
sanctioned join point and it is deliberately still NULL.
"""

from __future__ import annotations

import json
import logging
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.billing_models import (BillingInterval, BrandBillingPlan)
from app.models.models import Organization

log = logging.getLogger(__name__)


class PlanNotAvailable(Exception):
    """This brand does not offer that plan, or does not offer it this way.

    ONE EXCEPTION FOR EVERY WAY A PLAN CAN BE UNAVAILABLE, deliberately. A
    caller that could tell "no such plan" from "that plan is not purchasable"
    from "that plan has no annual price" would be an oracle for the shape of a
    brand's catalogue. The message carries the detail for logs and for the
    admin who is looking at their own brand; the HTTP layer returns one status.
    """


def platform_id_for_org(db: Session, org: Organization) -> Optional[str]:
    """Which brand bills this organization.

    Returns None rather than guessing. A NULL platform_id is a real state -
    an organization created before brands existed, or one whose platform row
    was removed - and the correct response is to refuse to bill rather than to
    fall back to whichever brand happens to be first in the table.
    """
    return getattr(org, "platform_id", None)


def plans_for(db: Session, platform_id: str, *, include_inactive: bool = False
              ) -> List[BrandBillingPlan]:
    """Every plan this brand offers, in display order."""
    q = (db.query(BrandBillingPlan)
         .filter(BrandBillingPlan.platform_id == platform_id))
    if not include_inactive:
        q = q.filter(BrandBillingPlan.is_active == True)  # noqa: E712
    return q.order_by(BrandBillingPlan.sort_order.asc(),
                      BrandBillingPlan.monthly_cents.asc().nullslast()).all()


def resolve_plan(db: Session, platform_id: Optional[str], key: Optional[str]
                 ) -> Optional[BrandBillingPlan]:
    """One plan, or None. Never raises - callers decide what absence means."""
    if not platform_id or not key:
        return None
    return (db.query(BrandBillingPlan)
            .filter(BrandBillingPlan.platform_id == platform_id,
                    BrandBillingPlan.key == key)
            .first())


def require_purchasable(db: Session, platform_id: Optional[str],
                        key: Optional[str], interval: str) -> BrandBillingPlan:
    """The checkout gate. Raises PlanNotAvailable unless this exact
    (brand, plan, interval) is something a customer may actually buy.

    THIS IS THE PRICE-TAMPERING DEFENCE. The request names a plan; this
    function decides whether that plan exists FOR THIS BRAND, is active, is
    self-serve purchasable, and has a price for the interval asked for. A
    plan key belonging to a different brand resolves to nothing here, so one
    brand's customer cannot buy another brand's tier - and cannot discover
    that it exists.
    """
    if interval not in BillingInterval.ALL:
        raise PlanNotAvailable("Unknown billing interval: %r" % (interval,))

    plan = resolve_plan(db, platform_id, key)
    if plan is None:
        raise PlanNotAvailable("No plan %r is available." % (key,))
    if not plan.is_active:
        raise PlanNotAvailable("Plan %r is not currently available." % (key,))
    if not plan.is_purchasable:
        # Enterprise and similar: listed and quoted, never self-serve.
        raise PlanNotAvailable(
            "Plan %r is not available for self-service purchase." % (key,))

    if price_cents_for(plan, interval) is None and stripe_price_id_for(plan, interval) is None:
        raise PlanNotAvailable(
            "Plan %r has no %s price configured." % (key, interval))
    return plan


def price_cents_for(plan: BrandBillingPlan, interval: str) -> Optional[int]:
    if interval == BillingInterval.YEAR:
        return plan.annual_cents
    return plan.monthly_cents


def stripe_price_id_for(plan: BrandBillingPlan, interval: str) -> Optional[str]:
    if interval == BillingInterval.YEAR:
        return plan.stripe_price_id_annual
    return plan.stripe_price_id_monthly


def features_for(plan: BrandBillingPlan) -> List[str]:
    if not plan.features_json:
        return []
    try:
        parsed = json.loads(plan.features_json)
        return [str(x) for x in parsed] if isinstance(parsed, list) else []
    except (ValueError, TypeError):
        # A malformed features blob is a display problem, never a billing one.
        log.warning("billing_catalog: unparseable features_json on plan %s", plan.id)
        return []


# ──────────────────────────────────────────────────────────────────────────────
# Upgrade vs downgrade
# ──────────────────────────────────────────────────────────────────────────────

UPGRADE = "upgrade"
DOWNGRADE = "downgrade"
LATERAL = "lateral"


def classify_change(db: Session, current_plan: Optional[BrandBillingPlan],
                    current_interval: Optional[str],
                    target_plan: BrandBillingPlan, target_interval: str) -> str:
    """Is this move up, down, or sideways?

    IT MATTERS BECAUSE THE TWO DIRECTIONS HAVE DIFFERENT, DECIDED POLICIES:
    an upgrade takes effect immediately with proration; a downgrade waits for
    the end of the period the customer already paid for and issues no credit.
    Getting the direction wrong either charges someone early or hands them a
    tier they have not paid for, so the rule is written down here once rather
    than inferred at each call site.

    THE RULE, in order:

    1. Compare the MONTHLY-EQUIVALENT price. Annual is divided by 12 so that
       "Starter annual" and "Starter monthly" compare as the same tier rather
       than annual looking like a twelve-fold upgrade.
    2. If the tier prices are equal, the INTERVAL breaks the tie. Moving to
       annual is treated as an upgrade: the customer is committing more money
       sooner and should get the change immediately. Moving to monthly is
       treated as a downgrade: they are reducing commitment, and the annual
       period they already bought should run its course.
    3. Genuinely identical - same plan, same interval - is LATERAL, and the
       caller should do nothing rather than send a no-op to Stripe.

    A plan with no comparable price (a quoted Enterprise tier) cannot be
    reached through self-serve checkout at all, so it never arrives here.
    """
    target_monthly = _monthly_equivalent_cents(target_plan, target_interval)

    if current_plan is None:
        # No subscription yet. Not a change at all - the caller should be
        # creating one, not modifying one - but "upgrade" is the safe reading
        # if anything does route here: it applies immediately, which is what
        # someone who just paid expects.
        return UPGRADE

    current_monthly = _monthly_equivalent_cents(current_plan, current_interval or BillingInterval.MONTH)

    if current_monthly is None or target_monthly is None:
        # Cannot compare honestly. Treat as lateral so no timing policy is
        # applied on the strength of a number we do not have.
        return LATERAL

    if target_monthly > current_monthly:
        return UPGRADE
    if target_monthly < current_monthly:
        return DOWNGRADE

    # Same tier price - the interval decides.
    if current_interval == target_interval:
        return LATERAL
    if target_interval == BillingInterval.YEAR:
        return UPGRADE
    return DOWNGRADE


def _monthly_equivalent_cents(plan: BrandBillingPlan, interval: str) -> Optional[int]:
    """Annual prices divided by 12 so tiers compare on the same axis."""
    if interval == BillingInterval.YEAR:
        if plan.annual_cents is None:
            return None
        return plan.annual_cents // 12
    return plan.monthly_cents
