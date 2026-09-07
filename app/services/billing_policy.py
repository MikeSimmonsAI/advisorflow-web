"""
Brand billing POLICY — the only module allowed to read BrandBillingConfig.

═══════════════════════════════════════════════════════════════════════════
THE RULE THIS MODULE EXISTS TO ENFORCE
═══════════════════════════════════════════════════════════════════════════

AN UNSET POLICY MEANS DO NOTHING. It never means "use a sensible default."

That distinction is the entire point. Several billing decisions here were
deliberately left open, and the tempting thing - a `grace_days or 7`, a
`suspend if past_due` - would turn a decision nobody made into behaviour
customers feel. A default grace period cuts off a paying customer on a
schedule nobody chose. A default cancellation timing deletes the remainder of
a period somebody already paid for. A default trial gives away revenue.

So every accessor below returns a value that says "undecided" as loudly as it
can, and every caller is written to treat that as "take no action, and be able
to say why." `describe()` exists so a screen can show an administrator exactly
which policies are live and which are still open, rather than presenting
inaction as if it were a rule.

WHAT IS DECIDED, AND WHAT IS NOT

  DECIDED  upgrade timing and proration      immediate, with proration
  DECIDED  downgrade timing and proration    at period end, no credit/refund
  OPEN     failed payment consequence        POLICY REQUIRED
  OPEN     subscription cancellation timing  POLICY REQUIRED
  OPEN     trial length                      POLICY REQUIRED
  OPEN     refund / chargeback clawback      POLICY REQUIRED

The decided pair is stored as configuration rather than written into the
billing engine, so a second brand can choose differently without a code
change. That is the platform rule - build once, configure per brand - applied
to billing, which is the half of this system that did not previously follow it.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.models.billing_models import (BrandBillingConfig, ChangeTiming,
                                       ProrationBehavior)
from app.services import billing_catalog

log = logging.getLogger(__name__)


# The value every "not decided" accessor returns. A real string rather than
# None so it cannot be mistaken for a missing lookup, and so it shows up
# legibly in a log line and on an admin screen.
POLICY_REQUIRED = "policy_required"


def config_for(db: Session, platform_id: Optional[str]) -> Optional[BrandBillingConfig]:
    if not platform_id:
        return None
    return (db.query(BrandBillingConfig)
            .filter(BrandBillingConfig.platform_id == platform_id)
            .first())


# ──────────────────────────────────────────────────────────────────────────────
# Plan change — DECIDED
# ──────────────────────────────────────────────────────────────────────────────

def change_behavior(db: Session, platform_id: Optional[str], direction: str) -> dict:
    """How a plan change of this direction should be applied.

    Returns {"timing", "proration", "configured"}. `configured` is False when
    the brand has no config row or has not set this direction, and in that case
    the caller must refuse the change rather than pick a timing itself - a plan
    change applied on a guessed schedule either bills someone early or gives
    away a tier.

    There is deliberately NO fallback to the EvoSys values here. They are
    EvoSys's decision, seeded into EvoSys's config row, not a universal truth
    about how software should be billed.
    """
    cfg = config_for(db, platform_id)

    if direction == billing_catalog.UPGRADE:
        timing = getattr(cfg, "upgrade_timing", None) if cfg else None
        proration = getattr(cfg, "upgrade_proration", None) if cfg else None
    elif direction == billing_catalog.DOWNGRADE:
        timing = getattr(cfg, "downgrade_timing", None) if cfg else None
        proration = getattr(cfg, "downgrade_proration", None) if cfg else None
    else:
        # Lateral: nothing to time and nothing to prorate.
        return {"timing": None, "proration": ProrationBehavior.NONE,
                "configured": True, "direction": direction}

    if not timing or not proration:
        return {"timing": None, "proration": None, "configured": False,
                "direction": direction}

    return {"timing": timing, "proration": proration, "configured": True,
            "direction": direction}


# ──────────────────────────────────────────────────────────────────────────────
# Failed payment — OPEN
# ──────────────────────────────────────────────────────────────────────────────

def past_due_behavior(db: Session, platform_id: Optional[str]) -> dict:
    """What a past-due subscription costs the customer in access.

    UNSET BY DESIGN AND SHIPPED THAT WAY. Today a failed payment updates
    billing_status and withdraws nothing, and this function preserves exactly
    that until somebody decides otherwise. The switch is real, so turning it on
    is a configuration change rather than a code change - but nothing is turned
    on by shipping the switch.

    `suspends` False with `configured` False is the "we have not decided"
    answer. A caller must never read `suspends is False` as "the brand decided
    not to suspend"; that is what `configured` distinguishes.
    """
    cfg = config_for(db, platform_id)
    grace = getattr(cfg, "past_due_grace_days", None) if cfg else None
    suspend = getattr(cfg, "suspend_on_past_due", None) if cfg else None

    if suspend is None:
        return {"suspends": False, "grace_days": None, "configured": False,
                "reason": POLICY_REQUIRED}

    return {"suspends": bool(suspend), "grace_days": grace, "configured": True,
            "reason": None}


# ──────────────────────────────────────────────────────────────────────────────
# Cancellation — OPEN
# ──────────────────────────────────────────────────────────────────────────────

def cancel_behavior(db: Session, platform_id: Optional[str]) -> dict:
    """Immediate, or at the end of the paid period.

    NOT DECIDED. The difference is a customer's money: cancelling immediately
    on a period they already paid for takes back access they bought, and
    cancelling at period end keeps charging nothing while they use it out.
    Both are defensible and neither is inferable, so an unconfigured brand's
    cancel request is refused with this reason rather than resolved by a
    default.

    SEPARATE FROM CUSTOMER LIFECYCLE CANCELLATION. This is "stop charging the
    card." customer_lifecycle.py handles "this customer is leaving," which
    suspends a workspace and keeps every record. CANCELLATION IS NOT DELETION
    holds for both, and neither one implies the other: a customer can stop
    paying while their data stays, and an offboarding can be scheduled while
    the subscription runs out its term.
    """
    cfg = config_for(db, platform_id)
    timing = getattr(cfg, "cancel_timing", None) if cfg else None
    if timing not in ChangeTiming.ALL:
        return {"timing": None, "configured": False, "reason": POLICY_REQUIRED}
    return {"timing": timing, "configured": True, "reason": None}


# ──────────────────────────────────────────────────────────────────────────────
# Trial — OPEN
# ──────────────────────────────────────────────────────────────────────────────

def trial_days(db: Session, platform_id: Optional[str]) -> Optional[int]:
    """None means no trial is offered. Never guessed: a trial length nobody
    configured is a revenue decision made by a default value."""
    cfg = config_for(db, platform_id)
    days = getattr(cfg, "trial_days", None) if cfg else None
    if days is None or days <= 0:
        return None
    return int(days)


# ──────────────────────────────────────────────────────────────────────────────
# Clawback — OPEN, AND NOT IMPLEMENTED
# ──────────────────────────────────────────────────────────────────────────────

def clawback_behavior(db: Session, platform_id: Optional[str]) -> dict:
    """What a refund or chargeback does to compensation already earned.

    NOTHING, TODAY, AND THAT IS THE CORRECT BEHAVIOUR UNTIL A POLICY EXISTS.

    A refund is RECORDED against the payment (BillingPayment.refunded_cents)
    and reported. It does not touch compensation history. Paid compensation is
    a record of money that left the business and went to a person; rewriting it
    because a customer later charged back would make the ledger disagree with
    the bank, and would do it silently.

    When a policy is decided it will be expressed as APPEND-ONLY adjustment
    entries referencing the original collection, the Stripe reversal and the
    original compensation entry - a new row saying "this was reversed", never
    an edit to the row that says "this was paid."
    """
    cfg = config_for(db, platform_id)
    policy = getattr(cfg, "clawback_policy", None) if cfg else None
    if not policy:
        return {"adjusts_compensation": False, "configured": False,
                "reason": POLICY_REQUIRED}
    # No policy vocabulary is implemented yet. A brand that has written
    # something into this column has recorded an intention, not enabled a
    # behaviour, and saying so is better than acting on a string nothing
    # understands.
    return {"adjusts_compensation": False, "configured": True,
            "reason": "recorded_not_implemented", "policy": policy}


# ──────────────────────────────────────────────────────────────────────────────
# Reporting
# ──────────────────────────────────────────────────────────────────────────────

def describe(db: Session, platform_id: Optional[str]) -> dict:
    """Every policy and whether it is actually decided.

    For the God billing screen. An administrator should be able to see at a
    glance which of these are live rules and which are still open questions,
    because "nothing happens when a payment fails" looks identical to "billing
    is broken" unless somebody says which it is.
    """
    cfg = config_for(db, platform_id)
    up = change_behavior(db, platform_id, billing_catalog.UPGRADE)
    down = change_behavior(db, platform_id, billing_catalog.DOWNGRADE)
    past_due = past_due_behavior(db, platform_id)
    cancel = cancel_behavior(db, platform_id)
    clawback = clawback_behavior(db, platform_id)

    open_items = []
    if not up["configured"]:
        open_items.append("upgrade timing/proration")
    if not down["configured"]:
        open_items.append("downgrade timing/proration")
    if not past_due["configured"]:
        open_items.append("failed-payment consequence")
    if not cancel["configured"]:
        open_items.append("subscription cancellation timing")
    if not clawback["configured"]:
        open_items.append("refund/chargeback clawback")

    return {
        "platform_id": platform_id,
        "has_config_row": cfg is not None,
        "upgrade": up,
        "downgrade": down,
        "past_due": past_due,
        "cancellation": cancel,
        "trial_days": trial_days(db, platform_id),
        "clawback": clawback,
        "policy_required": open_items,
        "explanation": (
            "Anything listed in policy_required is UNDECIDED, and the billing "
            "engine takes no action on it. That is deliberate: an unset policy "
            "means do nothing, never a default. Nothing suspends a customer, "
            "cancels early, or adjusts paid compensation until the "
            "corresponding policy is configured for this brand."
        ),
    }
