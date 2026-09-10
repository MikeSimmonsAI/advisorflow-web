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

from app.models.billing_models import (BillingCommitment, BillingInterval,
                                       BrandBillingPlan)
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


def resolve_plan_by_price_id(db: Session, platform_id: Optional[str],
                             price_id: Optional[str]) -> Optional[BrandBillingPlan]:
    """Which plan is this Stripe Price? THE DURABLE LOOKUP.

    A Stripe price id is created by us, mapped in the brand's own catalogue,
    and is what the customer is actually charged against. Unlike a metadata
    string it cannot be edited into something else from the Stripe dashboard,
    and unlike a plan key it is unambiguous across brands.

    ══════════════════════════════════════════════════════════════════════
    A PRICE IS NEVER RESOLVED WITHOUT A BRAND. NO SCOPE MEANS NO ANSWER.
    ══════════════════════════════════════════════════════════════════════

    This function's first version filtered on platform_id only `if platform_id`
    - so an organization with no platform (one created before brands existed,
    or whose platform row was removed) searched the WHOLE catalogue and got
    back whichever brand's plan happened to match first. A Stripe Price
    belonging to Brand A would have resolved as a valid plan for a Brand B
    organization, and the webhook would have written Brand A's plan key onto
    it. Cross-brand contamination through a convenience `if`.

    A missing platform is not a wildcard. It is a missing answer, and the
    caller handles that by leaving the plan alone rather than by guessing.
    """
    if not price_id or not platform_id:
        if price_id and not platform_id:
            log.warning(
                "billing_catalog: refusing to resolve Stripe price %s with no "
                "brand scope - a price must resolve within one brand's "
                "catalogue or not at all", price_id)
        return None
    # THE MONTH-TO-MONTH PRICE IS MATCHED HERE TOO, and it has to be. This
    # lookup is how `billing_webhook` turns the price on a live subscription
    # back into a plan key on every renewal. Omitting the third column would
    # mean every month-to-month customer resolved to no plan at all — losing
    # their tier, and with it their entitlements, silently and permanently.
    return (db.query(BrandBillingPlan)
            .filter(BrandBillingPlan.platform_id == platform_id)
            .filter((BrandBillingPlan.stripe_price_id_monthly == price_id)
                    | (BrandBillingPlan.stripe_price_id_annual == price_id)
                    | (BrandBillingPlan.stripe_price_id_month_to_month == price_id))
            .first())


def interval_for_price_id(plan: BrandBillingPlan, price_id: str) -> Optional[str]:
    """Which interval that price represents on this plan.

    Both monthly prices — committed and month-to-month — are the MONTH
    interval. They differ in commitment, not in how often the card is charged.
    """
    if not price_id:
        return None
    if plan.stripe_price_id_annual == price_id:
        return BillingInterval.YEAR
    if price_id in (plan.stripe_price_id_monthly,
                    plan.stripe_price_id_month_to_month):
        return BillingInterval.MONTH
    return None


def commitment_for_price_id(plan: BrandBillingPlan,
                            price_id: Optional[str]) -> Optional[str]:
    """Which commitment that price represents, or None if it is not this plan's.

    Lets a subscription that already exists at Stripe be read back as "Starter,
    month-to-month" rather than just "Starter" — so a screen, and a plan-change
    decision, work off the rate the customer is actually on.
    """
    if not price_id:
        return None
    if plan.stripe_price_id_month_to_month == price_id:
        return BillingCommitment.MONTH_TO_MONTH
    if price_id in (plan.stripe_price_id_monthly, plan.stripe_price_id_annual):
        return BillingCommitment.TERM
    return None


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


def price_cents_for(plan: BrandBillingPlan, interval: str,
                    commitment: Optional[str] = None) -> Optional[int]:
    """The configured amount for this (plan, interval, commitment).

    `commitment` defaults to the TERM rate, which is what `monthly_cents` has
    always held — so every existing caller resolves exactly what it did before
    the commitment axis existed.

    NO FALLING BACK BETWEEN COMMITMENTS. A month-to-month request against a
    plan with no month-to-month price returns None, not the term rate. The term
    rate is a discount earned by a commitment this customer did not make, and
    handing it over on a missing-configuration technicality is a real revenue
    leak that nobody would ever see in a log.
    """
    if interval == BillingInterval.YEAR:
        # Annual is a prepayment, which is itself a commitment; there is no
        # separate no-commitment annual price and inventing one would be a
        # pricing decision.
        return plan.annual_cents
    if commitment == BillingCommitment.MONTH_TO_MONTH:
        return plan.month_to_month_cents
    return plan.monthly_cents


def stripe_price_id_for(plan: BrandBillingPlan, interval: str,
                        commitment: Optional[str] = None) -> Optional[str]:
    """The Stripe Price for this (plan, interval, commitment). Same rules."""
    if interval == BillingInterval.YEAR:
        return plan.stripe_price_id_annual
    if commitment == BillingCommitment.MONTH_TO_MONTH:
        return plan.stripe_price_id_month_to_month
    return plan.stripe_price_id_monthly


def commitment_label(commitment: Optional[str], term_months: Optional[int] = None) -> str:
    """How the commitment reads to a person, on a screen or an invoice line."""
    if commitment == BillingCommitment.MONTH_TO_MONTH:
        return "Month-to-month"
    if term_months:
        return "%d-month agreement" % int(term_months)
    return "Term agreement"


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


def _monthly_equivalent_cents(plan: BrandBillingPlan, interval: str,
                              commitment: Optional[str] = None) -> Optional[int]:
    """Annual prices divided by 12 so tiers compare on the same axis.

    `commitment` defaults to None, which `price_cents_for` reads as the TERM
    rate — exactly what this returned before the argument existed. That default
    is deliberate for `classify_change` above, which compares two TIERS and
    must not call a customer's move an upgrade or a downgrade merely because
    their commitment changed; the commitment axis is a pricing question, not a
    tier question.

    Callers reporting what a customer actually PAYS must pass it. Both rates of
    a tier are the MONTH interval, so a reader that passes interval alone
    reports every month-to-month customer at the discounted term rate.
    """
    cents = price_cents_for(plan, interval, commitment)
    if cents is None:
        return None
    if interval == BillingInterval.YEAR:
        return cents // 12
    return cents
