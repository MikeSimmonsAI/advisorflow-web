"""
Lead capacity — what happens when a prospect arrives and the plan is full.

═══════════════════════════════════════════════════════════════════════════
THE DECISION THIS FILE EXISTS TO EXPRESS
═══════════════════════════════════════════════════════════════════════════

The first implementation of the lead ceiling refused EVERY creation path
equally, public webhooks included. That is defensible as billing and
indefensible as product: a real family filled in a form, and the platform
threw them away because of a number on an invoice. The customer never learns
the prospect existed, and no upgrade can bring them back.

So the two kinds of arrival are separated, and they are separated by WHO
INITIATED, not by which router the code happens to live in:

  USER-INITIATED - somebody at the customer clicked something. Manual create,
  their own spreadsheet import, their own bulk action. They are present, they
  can be told, and they can act on it. REFUSED, with a structured
  PLAN_CAPACITY_REACHED payload that names the resource, the current count and
  the limit.

  EXTERNAL ARRIVAL - a website form, a social or CRM webhook, an advertising
  or scheduling integration, an automation. Nobody at the customer is watching
  and the prospect is real. HELD, never dropped.

═══════════════════════════════════════════════════════════════════════════
WHAT "HELD" MEANS, EXACTLY
═══════════════════════════════════════════════════════════════════════════

The lead is written to the leads table like any other, keeping its source,
received timestamp, organization, payload, dedupe keys and attribution,
because it IS an ordinary Lead row - flagged, not diverted into a parallel
system. What the flag changes is what the platform may spend on it:

  no SMS · no email · no voice · no cadence enrollment · no cadence steps ·
  no AI conversation · not selected by auto-send · EXCLUDED by qualification

That list is enforced by a direct check at each of those sites rather than
here, because this codebase has no single send chokepoint - `dnc` and
`is_duplicate` are enforced exactly the same way, at the same sites. A held
lead that could still be texted would be the "unlimited paid-resource
consumption" this design exists to prevent.

A held lead does NOT count toward plan usage. If it did, the customer's
"2,600 of 2,500 used" would be a number they cannot act on, and the ceiling
would stop meaning anything the moment it was crossed.

═══════════════════════════════════════════════════════════════════════════
WHAT RELEASE DOES, AND WHAT IT DELIBERATELY DOES NOT
═══════════════════════════════════════════════════════════════════════════

When headroom appears - an upgrade lands, or leads are removed - held leads
are released OLDEST FIRST, up to the headroom that actually exists.

Release clears the hold and nothing else. It does not enrol anything in a
cadence, send anything, or start an AI conversation. Releasing five thousand
held leads straight into outbound would spend the customer's money and their
sending reputation on a decision they never made; they become ordinary new
leads waiting for the customer's own action.

NOT DONE HERE, ON PURPOSE: no overage billing, no automatic upgrade, no
deleting one lead to make room for another, no silent discard.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import HTTPException, status as http_status
from sqlalchemy.orm import Session

from app.models.models import Lead, Organization
from app.services import plan_limits

log = logging.getLogger(__name__)


# The one state string. A held lead's `status` is untouched.
OVER_CAPACITY = "over_capacity"

# Why it is held. Only one reason exists today; it is stored rather than
# assumed so a second ceiling later does not need a schema change to be
# distinguishable from this one.
REASON_MAX_LEADS = "max_leads"

# The structured error code the API returns on a user-initiated refusal.
PLAN_CAPACITY_REACHED = "PLAN_CAPACITY_REACHED"


def is_held(lead: Optional[Lead]) -> bool:
    """The predicate every paid-resource path calls.

    A function rather than an inline `== OVER_CAPACITY` at nine call sites, so
    that a future second hold state cannot half-apply: adding it here reaches
    every gate at once.
    """
    return getattr(lead, "capacity_state", None) == OVER_CAPACITY


def held_query(db: Session, org_id: str):
    return (db.query(Lead)
            .filter(Lead.organization_id == org_id,
                    Lead.capacity_state == OVER_CAPACITY))


def held_count(db: Session, org_id: Optional[str]) -> int:
    if not org_id:
        return 0
    return held_query(db, org_id).count()


def capacity_error_detail(db: Session, org: Optional[Organization]) -> dict:
    """The structured refusal body. Machine-readable first, prose second.

    `current` and `limit` are the numbers the customer needs to decide whether
    to upgrade. A bare "forbidden" tells them nothing and sends them to the
    wrong person.
    """
    check = plan_limits.check(db, org, plan_limits.LIMIT_LEADS, adding=0)
    return {
        "error": PLAN_CAPACITY_REACHED,
        "resource": "leads",
        "current": check.get("used"),
        "limit": check.get("limit"),
        "held": held_count(db, getattr(org, "id", None)),
        "message": ("This plan includes up to %s leads and %s are in use. "
                    "Upgrade the plan to add more."
                    % (check.get("limit"), check.get("used"))),
    }


def require_capacity_user_initiated(db: Session, org: Optional[Organization],
                                    adding: int = 1) -> None:
    """A person is present and can be told. Refuse cleanly.

    402 rather than 403, matching the rest of the entitlement layer: they are
    not unauthorized, their PLAN does not include this, and those two problems
    go to different people.
    """
    result = plan_limits.check(db, org, plan_limits.LIMIT_LEADS, adding=adding)
    if result["allowed"]:
        return
    raise HTTPException(status_code=http_status.HTTP_402_PAYMENT_REQUIRED,
                        detail=capacity_error_detail(db, org))


def hold_if_over_capacity(db: Session, lead: Lead,
                          org: Optional[Organization] = None,
                          *, counter: "plan_limits.CapacityCounter | None" = None,
                          reason: str = REASON_MAX_LEADS) -> bool:
    """Called by EXTERNAL arrival paths, immediately before `db.add(lead)`.

    Returns True if the lead was flagged as held. The caller adds and commits
    it either way - that is the whole point. The signature makes the wrong
    thing awkward to write: there is no variant of this that discards a lead.

    `counter` lets a batch path decide against one count instead of re-counting
    per row; without one, capacity is checked directly.
    """
    if org is None and getattr(lead, "organization_id", None):
        org = (db.query(Organization)
               .filter(Organization.id == lead.organization_id).first())

    if counter is not None:
        room = counter.has_room(1)
        if room:
            counter.take(1)
    else:
        room = plan_limits.check(db, org, plan_limits.LIMIT_LEADS,
                                 adding=1)["allowed"]

    if room:
        return False

    lead.capacity_state = OVER_CAPACITY
    lead.capacity_held_at = datetime.utcnow()
    lead.capacity_hold_reason = reason
    lead.capacity_released_at = None

    log.info("lead_capacity: holding inbound lead for org %s (source=%s) - "
             "plan lead limit reached; the prospect is kept, not dropped",
             getattr(lead, "organization_id", None),
             getattr(lead, "source", None) or getattr(lead, "source_file", None))
    return True


def release_available(db: Session, org: Optional[Organization],
                      *, actor=None, limit: int = 1000) -> dict:
    """Release held leads into whatever headroom now exists. Oldest first.

    Oldest first because the queue is a queue: the family who enquired in
    March should not stay held while April's are let through.

    Returns a report rather than a count, so a caller can say what happened
    without re-querying. Releasing nothing is a normal, common outcome and is
    not an error.
    """
    out = {"released": 0, "still_held": 0, "headroom": None, "org_id": None}
    if org is None:
        return out
    out["org_id"] = org.id

    ceiling = plan_limits.limit_for(db, org, plan_limits.LIMIT_LEADS)
    still = held_count(db, org.id)

    if ceiling is None:
        # Unlimited. Everything held was held under a ceiling that no longer
        # applies, so all of it is released.
        headroom = still
    else:
        used = plan_limits.usage_for(db, org, plan_limits.LIMIT_LEADS)
        headroom = max(0, ceiling - used)

    out["headroom"] = headroom
    if headroom <= 0 or still == 0:
        out["still_held"] = still
        return out

    rows = (held_query(db, org.id)
            .order_by(Lead.capacity_held_at.asc(), Lead.created_at.asc())
            .limit(min(headroom, limit))
            .all())

    now = datetime.utcnow()
    for lead in rows:
        lead.capacity_state = None
        lead.capacity_released_at = now
        # `status` was never touched while held, so there is nothing to
        # restore. The lead simply becomes actionable again.

    db.flush()
    out["released"] = len(rows)
    out["still_held"] = held_count(db, org.id)

    # AUDITED ONLY WHEN THERE IS AN ACTOR.
    #
    # `audit_log_entries.actor_user_id` is NOT NULL, and the most common
    # release has no actor at all - it is triggered by a Stripe webhook, where
    # the "who" is Stripe. Attempting the insert anyway raises inside the
    # flush, and a caught exception is not enough: the failed flush poisons the
    # session, so the release itself would roll back. Losing the release to
    # save the audit row is exactly the wrong trade.
    if rows and getattr(actor, "id", None):
        try:
            from app.routers.audit_log_router import log_action
            log_action(
                db,
                organization_id=org.id,
                actor_user_id=getattr(actor, "id", None),
                action="lead_capacity.release",
                target_type="organization",
                target_id=org.id,
                details={"released": out["released"],
                         "still_held": out["still_held"],
                         "headroom": headroom,
                         "plan": getattr(
                             plan_limits.effective_plan(db, org), "key", None)},
                platform_id=getattr(org, "platform_id", None),
                commit=False,
            )
        except Exception:                                # pragma: no cover
            log.exception("lead_capacity: failed to audit release for org %s",
                          org.id)

    log.info("lead_capacity: released %d held lead(s) for org %s (%d still held)",
             out["released"], org.id, out["still_held"])
    return out


def report(db: Session, org: Optional[Organization]) -> dict:
    """What the Billing and Leads screens need to say about held prospects."""
    if org is None:
        return {"held": 0, "oldest_held_at": None}
    rows = held_query(db, org.id)
    oldest = rows.order_by(Lead.capacity_held_at.asc()).first()
    return {
        "held": rows.count(),
        "oldest_held_at": getattr(oldest, "capacity_held_at", None),
    }
