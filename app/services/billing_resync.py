"""RE-READ WHAT STRIPE SAYS, AND WRITE IT DOWN AGAIN.

════════════════════════════════════════════════════════════════════════════
THIS DOES NOT MAKE THIS PLATFORM AUTHORITATIVE. IT DOES THE OPPOSITE.
════════════════════════════════════════════════════════════════════════════

Every billing column on an Organization is a MIRROR of what Stripe told us
through a verified webhook. That rule does not change here. This module
retrieves the subscription from Stripe and hands it to the SAME
`billing_webhook.apply_subscription` a webhook would have — so the mirror is
refreshed from the original, never from an opinion formed on this side.

WHY IT HAS TO EXIST. A webhook is delivered once, at one moment, and processed
by whatever code was deployed then. Three ordinary things break the mirror and
no amount of care in the webhook handler prevents any of them:

  A COLUMN THAT DID NOT EXIST YET. `billing_commitment` was added the same
  afternoon a live subscription changed plan. The event arrived minutes before
  the deploy, so it was handled correctly by the code of the time and the
  column stayed NULL. Nothing is wrong, nothing will fix itself, and the next
  event on that subscription might be a month away.

  A FIELD THAT MOVED. Stripe's 2025-03-31 API version took
  `current_period_end` off the Subscription and put a period on each item. No
  api_version is pinned in this codebase, so the payload shape can change
  without a deploy — and everything mirrored before the reader was taught the
  new shape is simply missing.

  A DELIVERY THAT FAILED. An endpoint pointed at the wrong environment, an
  outage, a signing secret rotated mid-flight. Stripe retries for three days
  and then stops.

In all three the money at Stripe is right and the local copy is stale. The
honest repair is to ask Stripe again.

WHAT IT WILL NOT DO. It never writes to Stripe — no modify, no cancel, no
price change. It never invents a value: a subscription Stripe does not
recognise leaves the record untouched and says so. It records what changed so
a refresh is as auditable as the webhook it stands in for.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.models import Organization

log = logging.getLogger(__name__)


class ResyncRefused(RuntimeError):
    """A precondition the caller can fix — not a processor failure."""


# The columns a refresh is allowed to touch. Written as an explicit list rather
# than "whatever apply_subscription happens to set", so that a future change to
# the webhook handler cannot silently widen what an operator's refresh button
# rewrites. Everything here is mirrored FROM Stripe; nothing here is a local
# decision, and nothing about the customer, their users or their data appears.
MIRRORED_FIELDS = (
    "stripe_subscription_id",
    "billing_status",
    "billing_plan_key",
    "plan",
    "billing_commitment",
    "stripe_plan_interval",
    "billing_current_period_end",
    "billing_cancel_at_period_end",
    "billing_trial_end",
    "billing_pending_plan_key",
    "billing_pending_commitment",
    "billing_pending_effective_at",
    "stripe_schedule_id",
)


def _snapshot(org: Organization) -> Dict[str, Any]:
    return {f: getattr(org, f, None) for f in MIRRORED_FIELDS}


def _diff(before: Dict[str, Any], after: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Only what actually moved, rendered as strings for an audit row."""
    out = []
    for field in MIRRORED_FIELDS:
        was, now = before.get(field), after.get(field)
        if was != now:
            out.append({"field": field,
                        "from": None if was is None else str(was),
                        "to": None if now is None else str(now)})
    return out


def _read_schedule(sub: Dict[str, Any]) -> Dict[str, Any]:
    """What Stripe still has BOOKED for this subscription, if anything.

    A deferred plan change is a Subscription Schedule, and no webhook payload
    this platform handles carries its phases. So the one question an operator
    asks when a scheduled downgrade looks wrong — "is it still there?" — has
    had no answer inside the product at all.

    READ ONLY, AND NEVER FATAL. A schedule that cannot be retrieved reports
    itself as unknown rather than as absent: "Stripe has no scheduled change"
    and "we could not ask" are opposite facts, and rendering them the same is
    how somebody concludes a customer's downgrade was lost when it was not.
    """
    import stripe

    raw = sub.get("schedule")
    schedule_id = raw.get("id") if isinstance(raw, dict) else raw
    if not schedule_id:
        return {"known": True, "has_schedule": False, "schedule_id": None,
                "next_phase": None,
                "explanation": "Stripe has no scheduled change for this "
                               "subscription."}
    try:
        sched = stripe.SubscriptionSchedule.retrieve(schedule_id)
    except Exception as exc:                             # pragma: no cover
        log.warning("billing_resync: could not read schedule %s: %s",
                    schedule_id, exc)
        return {"known": False, "has_schedule": None,
                "schedule_id": schedule_id, "next_phase": None,
                "explanation": "Stripe named a schedule but would not return "
                               "it, so what is booked is unknown - not absent."}

    # The phase that has not started. `current_phase` names the one running, so
    # anything starting later is what the customer is waiting for.
    current_end = ((sched.get("current_phase") or {}).get("end_date"))
    upcoming = None
    for phase in (sched.get("phases") or []):
        start = phase.get("start_date")
        if current_end and start and start >= current_end:
            prices = [((it.get("price") or {}).get("id")
                       if isinstance(it.get("price"), dict) else it.get("price"))
                      for it in (phase.get("items") or [])]
            upcoming = {"starts_at": start,
                        "price_ids": [p for p in prices if p]}
            break

    return {"known": True, "has_schedule": True, "schedule_id": schedule_id,
            "status": sched.get("status"), "next_phase": upcoming,
            "explanation": ("Stripe has a change booked for this subscription."
                            if upcoming else
                            "Stripe has a schedule on this subscription but no "
                            "phase after the current one.")}


def resync_subscription(db: Session, org: Organization,
                        dry_run: bool = False) -> Dict[str, Any]:
    """Refresh one organization's subscription mirror from Stripe.

    `dry_run` retrieves and compares without writing, so an operator can see
    what a refresh would change before changing it — the same preview-then-
    apply shape the brand provisioning endpoint uses.

    Returns what moved. An empty `changed` list is the good outcome and the
    common one: it means the webhook mirror was already correct, which is worth
    being able to demonstrate rather than assume.
    """
    sub_id = getattr(org, "stripe_subscription_id", None)
    if not sub_id:
        raise ResyncRefused(
            "This organization has no subscription recorded, so there is "
            "nothing to refresh. A customer who has never subscribed is not a "
            "stale mirror.")

    import stripe
    from app.routers.billing_router import _stripe_client
    from app.services import billing_webhook

    _stripe_client()
    try:
        # EXPAND THE ITEMS' PRICES. `apply_subscription` resolves the plan and
        # the commitment from the price OBJECT on the item; a bare id string
        # would leave both unresolvable and the refresh would report "nothing
        # changed" while changing nothing for the wrong reason.
        sub = stripe.Subscription.retrieve(sub_id, expand=["items.data.price"])
    except Exception as exc:
        log.warning("billing_resync: could not read subscription %s for org "
                    "%s: %s", sub_id, org.id, exc)
        raise ResyncRefused(
            "The payment processor did not return that subscription. Nothing "
            "was changed.")

    before = _snapshot(org)

    # THE WEBHOOK'S OWN FUNCTION. Not a second implementation of it — a refresh
    # that mirrored Stripe slightly differently from the webhook would create
    # two versions of the truth and no way to tell which a given row came from.
    billing_webhook.apply_subscription(db, org, sub)

    # AND THE SCHEDULE, which no webhook payload carries. A deferred downgrade
    # lives on a Subscription Schedule, and until now nothing on this platform
    # could answer "does Stripe still have that change booked" — which is the
    # question somebody asks precisely when they suspect it went missing.
    # Answering it from the product beats answering it from a dashboard.
    schedule = _read_schedule(sub)

    after = _snapshot(org)
    changed = _diff(before, after)

    if dry_run:
        # Put every field back exactly as it was. The comparison needed the
        # real function to run, so the rollback has to be explicit rather than
        # relying on the caller not to commit.
        for field, value in before.items():
            setattr(org, field, value)
        db.rollback()

    # DOES WHAT STRIPE HAS BOOKED MATCH WHAT THIS PLATFORM SHOWS? Reported
    # rather than reconciled: restoring a pending marker from a schedule means
    # deciding which plan and commitment that schedule's price represents, and
    # a refresh that guessed would write a change nobody made. Saying the two
    # disagree is enough to act on, and it is honest about which side is which.
    _shows_pending = bool(getattr(org, "billing_pending_plan_key", None))
    disagreement = None
    if schedule.get("known"):
        if schedule.get("next_phase") and not _shows_pending:
            disagreement = (
                "Stripe has a change booked for this subscription that this "
                "platform is not showing. The customer is expecting it; the "
                "screens are not. Re-record the change, or cancel it at "
                "Stripe, rather than leaving the two disagreeing.")
        elif _shows_pending and not schedule.get("has_schedule"):
            disagreement = (
                "This platform shows a pending change but Stripe has no "
                "schedule for it, so nothing will happen on the date the "
                "customer was given.")

    return {
        "organization_id": org.id,
        "stripe_subscription_id": sub_id,
        "dry_run": bool(dry_run),
        "changed": changed,
        "unchanged": not changed,
        "schedule": schedule,
        "shows_pending_locally": _shows_pending,
        "disagreement": disagreement,
        "explanation": (
            "Read from Stripe and applied through the same function the "
            "webhook uses. Nothing was written TO Stripe. An empty change list "
            "means the webhook mirror was already correct. The schedule is "
            "reported separately because no webhook carries it."
        ),
    }
