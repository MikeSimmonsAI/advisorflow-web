"""
Deferred plan changes — the Stripe Subscription Schedule layer.

═══════════════════════════════════════════════════════════════════════════
WHY THIS FILE EXISTS: A DEFECT IN WHAT SHIPPED BEFORE IT
═══════════════════════════════════════════════════════════════════════════

The first implementation of the downgrade path did this:

    stripe.Subscription.modify(
        sub_id,
        items=[{"id": item_id, "price": lower_price}],
        proration_behavior="none",
        billing_cycle_anchor="unchanged",
    )

believing that deferred the change to the end of the paid period. IT DOES
NOT. `billing_cycle_anchor: "unchanged"` only says "do not reset the billing
period start"; the ITEM SWAP TAKES EFFECT IMMEDIATELY. So a customer who
downgraded was moved to the lower plan the same second, having already paid
for the higher one for the rest of the month, and `proration_behavior: none`
meant they got no credit for the difference either. Worst of both: less
product, same money.

The decided policy is the opposite - a downgrade takes effect at the END of
the period the customer already paid for, and they keep the tier they bought
until it runs out. Stripe's mechanism for that is a SUBSCRIPTION SCHEDULE:
phase one is what they have now, ending at the current period end; phase two
is the new plan, starting there.

═══════════════════════════════════════════════════════════════════════════
ONE SCHEDULE PER SUBSCRIPTION, EVER
═══════════════════════════════════════════════════════════════════════════

A customer who clicks "downgrade to Starter", changes their mind, and clicks
"downgrade to Growth" must end up with ONE schedule saying Growth - not two
schedules disagreeing about what happens at the period boundary. Stripe will
happily refuse a second schedule on a subscription that already has one, and
"the API errored" is not an answer a customer should receive for pressing a
button twice.

So the schedule id is stored on the organization and every subsequent request
UPDATES that schedule. Releasing it (on upgrade, or on cancelling the pending
change) hands the subscription back to normal billing with no phase two.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

import stripe

log = logging.getLogger(__name__)


class ScheduleError(Exception):
    """The payment processor would not schedule the change."""


def _ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError, OSError):
        return None


def _phase_items(price_id: Optional[str], price_data: Optional[dict]) -> list:
    """Schedule phases require a PRICE, not inline price_data.

    Stripe's Subscription Schedule API will not accept an ad-hoc `price_data`
    block the way `Subscription.modify` will - a phase has to name a real Price
    object. That is a constraint worth stating out loud rather than discovering
    at runtime: a brand whose catalogue has no Stripe price id mapped CANNOT
    have deferred downgrades, and the caller is told so plainly instead of
    getting an opaque Stripe error.
    """
    if not price_id:
        raise ScheduleError(
            "A scheduled plan change needs a Stripe Price for the target plan. "
            "This brand's catalogue has no Stripe price id mapped for it, so "
            "the change cannot be scheduled for the end of the billing period. "
            "Map the plan's Stripe price in God Mode > Billing first.")
    return [{"price": price_id, "quantity": 1}]


def schedule_change_at_period_end(subscription_id: str, *,
                                  existing_schedule_id: Optional[str],
                                  target_price_id: Optional[str],
                                  target_plan_key: str,
                                  org_id: str) -> dict:
    """Move this subscription to `target_price_id` when the paid period ends.

    Returns {"schedule_id", "effective_at"}. Idempotent by construction: given
    an existing schedule id it MODIFIES that schedule rather than creating a
    second one, so a customer pressing the button twice - or a retry, or two
    browser tabs - converges on one answer instead of two contradictory phases.
    """
    items = _phase_items(target_price_id, None)

    # ── Recovering the existing schedule, if there is a usable one ────────
    #
    # THIS RETRIEVE HAS ITS OWN try, DELIBERATELY. It used to sit inside the
    # broad one below, which meant a stored schedule id Stripe no longer
    # returns at all - deleted, or left over from a different account after an
    # environment swap - raised, got swallowed by the outer handler, and came
    # back as "the processor would not schedule the plan change". That
    # organization's downgrade button then failed FOREVER, on the state of an
    # object nobody had ever seen, and the only escape was knowing to call
    # cancel-pending-change first.
    #
    # A schedule we cannot read is, for every purpose here, a schedule that is
    # not there. Fall through and create one.
    schedule = None
    if existing_schedule_id:
        try:
            schedule = stripe.SubscriptionSchedule.retrieve(existing_schedule_id)
            # A released or cancelled schedule cannot be edited either.
            if schedule.get("status") in ("released", "canceled"):
                schedule = None
        except Exception as exc:
            log.info("billing_schedule: stored schedule %s could not be read "
                     "(%s) - treating it as absent and creating a new one",
                     existing_schedule_id, exc)
            schedule = None

    try:
        if schedule is None:
            schedule = stripe.SubscriptionSchedule.create(
                from_subscription=subscription_id)

        phases = list(schedule.get("phases") or [])
        if not phases:
            raise ScheduleError(
                "The payment processor returned a schedule with no current "
                "phase, so there is nothing to schedule a change against.")

        current = phases[0]
        current_end = current.get("end_date")

        # PHASE ONE IS PRESERVED EXACTLY AS IT IS. It is what the customer
        # already paid for, and rewriting it is how somebody loses a period
        # they bought. Only its items and boundaries are carried forward.
        keep = {
            "items": [{"price": (i.get("price") if isinstance(i.get("price"), str)
                                 else (i.get("price") or {}).get("id")),
                       "quantity": i.get("quantity") or 1}
                      for i in (current.get("items") or [])],
            "start_date": current.get("start_date"),
            "end_date": current_end,
        }
        # A phase Stripe generated from the live subscription may carry no
        # explicit end date; the period end is then the boundary.
        if not keep["end_date"]:
            keep.pop("end_date")
            keep["iterations"] = 1

        updated = stripe.SubscriptionSchedule.modify(
            schedule["id"],
            end_behavior="release",   # after phase two, hand back to normal billing
            phases=[keep, {"items": items, "iterations": 1,
                           "metadata": {"org_id": org_id, "plan": target_plan_key}}],
            metadata={"org_id": org_id, "pending_plan": target_plan_key},
        )
    except ScheduleError:
        raise
    except Exception as exc:                       # pragma: no cover - network
        log.warning("billing_schedule: could not schedule change on %s: %s",
                    subscription_id, exc)
        raise ScheduleError(
            "The payment processor would not schedule the plan change.")

    effective = None
    try:
        ph = list(updated.get("phases") or [])
        if len(ph) > 1:
            effective = _ts(ph[1].get("start_date"))
        elif ph:
            effective = _ts(ph[0].get("end_date"))
    except Exception:
        effective = None

    return {"schedule_id": updated["id"], "effective_at": effective}


def release(schedule_id: Optional[str]) -> bool:
    """Drop a pending change. The subscription returns to normal billing.

    Used when an upgrade supersedes a scheduled downgrade: somebody who
    downgrades on Monday and upgrades on Tuesday must not still drop a tier at
    month end because a schedule nobody cancelled was still sitting there.

    Returns True if a schedule was released. A schedule that is already gone is
    not an error - the desired state is "no pending change", and it holds.
    """
    if not schedule_id:
        return False
    try:
        stripe.SubscriptionSchedule.release(schedule_id)
        return True
    except Exception as exc:
        log.info("billing_schedule: release of %s did not apply (%s) - "
                 "treating as already absent", schedule_id, exc)
        return False
