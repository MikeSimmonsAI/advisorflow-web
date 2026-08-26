"""
Proposal work queues and the closing view — Checkpoint 4.

WHAT DO I NEED TO DO NEXT, not a report.

Every list here is a queue with an action attached. That constraint is why
there is no "proposals by status" breakdown and no counts-by-month: a
salesperson opening My Day is deciding what to touch in the next ten minutes,
and a number they cannot act on is clutter competing with the ones they can.

The queues are deliberately disjoint where it matters — a proposal appears in
"ready to send" OR "to finish", never both — so the same deal does not demand
attention twice for the same reason.
"""
import logging
from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.models import (
    Proposal, PortalEvent, User,
    PROP_DRAFT, PROP_INTERNAL_REVIEW, PROP_READY, PROP_SENT, PROP_VIEWED,
    PROP_ACCEPTED, PROP_DECLINED, PROP_CHANGE_REQUESTED, PROP_EXPIRED,
    PROP_SUPERSEDED, PROPOSAL_STATUS_LABELS, PORTAL_EVENT_LABELS,
)
from app.models.sales_models import Opportunity
from app.models.scheduling_models import (
    SalesAppointment, AppointmentParticipant, MeetingType, APPT_CANCELLED,
)

log = logging.getLogger(__name__)

# A proposal sent this long ago with no view is a follow-up, not a wait.
STALE_UNVIEWED_HOURS = 48
# "Expiring" has to mean something a person can act on this week.
EXPIRING_WITHIN_DAYS = 7


def _live_proposals(db: Session, opportunity_ids: List[str]):
    """Every non-superseded proposal on these deals, newest version first."""
    if not opportunity_ids:
        return []
    return (db.query(Proposal)
            .filter(Proposal.opportunity_id.in_(opportunity_ids),
                    Proposal.deleted_at.is_(None),
                    Proposal.sales_status.isnot(None),
                    Proposal.sales_status != PROP_SUPERSEDED)
            .order_by(Proposal.version.desc())
            .all())


def _brief(db: Session, p: Proposal, opp: Optional[Opportunity],
           reason: str = None, urgency: str = None, names: dict = None) -> dict:
    """One row in a queue. Carries the REASON it is here, because a list of
    proposals with no explanation is a report, not a work queue.

    `names` is an optional user_id -> full_name map. A rep's own queue never
    needed the owner (every row is theirs); a manager's does, and looking each
    one up per row would fire a query per line of a screen. Passing the map in
    keeps this a pure in-memory classifier with exactly one query behind it.
    """
    owner_id = opp.owner_user_id if opp else None
    return {
        "proposal_id": p.id,
        "opportunity_id": p.opportunity_id,
        "company": (opp.company_name if opp else None) or p.client_company,
        "owner_user_id": owner_id,
        "owner_name": (names or {}).get(owner_id),
        "proposal_number": p.proposal_number,
        "version": p.version or 1,
        "status": p.sales_status,
        "status_label": PROPOSAL_STATUS_LABELS.get(p.sales_status, p.sales_status),
        "amount": float(p.final_amount) if p.final_amount is not None else None,
        "currency": p.currency or "USD",
        "expires_at": p.expires_at,
        "sent_at": p.sent_at,
        "first_viewed_at": p.first_viewed_at,
        "last_viewed_at": p.last_viewed_at,
        "reason": reason,
        "urgency": urgency,          # amber | red | None
    }


def _hours_since(then: Optional[datetime], now: datetime) -> Optional[float]:
    if then is None:
        return None
    return (now - then).total_seconds() / 3600.0


def _ago(then: Optional[datetime], now: datetime) -> str:
    """Human elapsed time. A rep reads 'yesterday', not a timestamp."""
    h = _hours_since(then, now)
    if h is None:
        return ""
    if h < 1:
        return "just now"
    if h < 24:
        return "%dh ago" % int(h)
    d = int(h // 24)
    return "yesterday" if d == 1 else "%d days ago" % d


def proposal_queues(db: Session, opportunities: List[Opportunity],
                    now: Optional[datetime] = None, limit: int = 10,
                    names: dict = None) -> dict:
    """The six proposal queues for My Day. Each one is a call to action.

    `limit` caps each list; `counts` are always the honest full totals. A rep
    with twelve deals never noticed the cap. A manager over six reps would see
    ten rows out of sixty and have no way to tell — so the caller that widens
    the scope is the caller that must raise the cap.

    `names` maps user_id -> full_name for owner attribution. Optional, because
    a rep's own queue does not need it and should not pay for it.
    """
    now = now or datetime.utcnow()
    opp_by_id = {o.id: o for o in opportunities}
    props = _live_proposals(db, list(opp_by_id.keys()))

    to_finish, ready, viewed, follow_up, expiring = [], [], [], [], []

    for p in props:
        opp = opp_by_id.get(p.opportunity_id)
        st = p.sales_status

        # Started and abandoned. The most common way a deal quietly dies.
        if st in (PROP_DRAFT, PROP_INTERNAL_REVIEW):
            to_finish.append(_brief(db, p, opp, "Draft — not sent yet"))
            continue

        # Finished but never sent. One click from being in front of a buyer.
        if st == PROP_READY:
            ready.append(_brief(db, p, opp, "Ready — send it", "amber"))
            continue

        # The customer did something. This is the highest-value signal on the
        # whole screen, so it gets its own queue rather than a status badge.
        if st == PROP_VIEWED:
            viewed.append(_brief(
                db, p, opp,
                "Opened it %s" % _ago(p.last_viewed_at or p.first_viewed_at, now),
                "amber"))

        if st == PROP_CHANGE_REQUESTED:
            follow_up.append(_brief(db, p, opp,
                                    "Asked for a change — revise it", "red"))
        elif st == PROP_DECLINED:
            follow_up.append(_brief(db, p, opp, "Declined — follow up", "red"))
        elif st == PROP_EXPIRED:
            follow_up.append(_brief(db, p, opp, "Expired — re-issue or close", "red"))
        elif st == PROP_SENT:
            # Sent and silent. NOT a failure yet — but past two days it is a
            # phone call, not a wait.
            hrs = _hours_since(p.sent_at, now)
            if hrs is not None and hrs >= STALE_UNVIEWED_HOURS:
                follow_up.append(_brief(
                    db, p, opp,
                    "Sent %s, still unopened" % _ago(p.sent_at, now), "amber"))

        # Expiring soon, and still live enough to matter.
        if st in (PROP_SENT, PROP_VIEWED, PROP_READY) and p.expires_at:
            days = (p.expires_at - now).total_seconds() / 86400.0
            if 0 <= days <= EXPIRING_WITHIN_DAYS:
                expiring.append(_brief(
                    db, p, opp,
                    "Expires in %d day%s" % (int(days), "" if int(days) == 1 else "s"),
                    "red" if days <= 2 else "amber"))

    if names:
        for bucket in (to_finish, ready, viewed, follow_up, expiring):
            for row in bucket:
                row["owner_name"] = names.get(row.get("owner_user_id"))

    return {
        "to_finish": to_finish[:limit],
        "ready_to_send": ready[:limit],
        "recently_viewed": viewed[:limit],
        "follow_up_required": follow_up[:limit],
        "expiring": expiring[:limit],
        "counts": {
            "to_finish": len(to_finish),
            "ready_to_send": len(ready),
            "recently_viewed": len(viewed),
            "follow_up_required": len(follow_up),
            "expiring": len(expiring),
        },
    }


# ── the closing view ────────────────────────────────────────────────────────

def _user_brief(db: Session, user_id: Optional[str]) -> Optional[dict]:
    if not user_id:
        return None
    u = db.query(User).filter(User.id == user_id).first()
    if u is None:
        return None
    return {"id": u.id, "full_name": u.full_name, "email": u.email}


def closing_view(db: Session, opp: Opportunity,
                 now: Optional[datetime] = None) -> dict:
    """Everything needed to close ONE deal, on one screen, with the warnings.

    Assembled from data Checkpoint 4 already produces — no new tables, nothing
    inferred. The warnings are the point: a closing screen that only shows
    status tells a rep what they already knew, whereas "sent four days ago,
    never opened" tells them what to do this afternoon.
    """
    now = now or datetime.utcnow()
    from app.services import proposal_service as ps

    prop = ps.current_proposal(db, opp.id)
    warnings = []

    # ── meetings ────────────────────────────────────────────────────────────
    appts = (db.query(SalesAppointment)
             .filter(SalesAppointment.opportunity_id == opp.id,
                     SalesAppointment.status != APPT_CANCELLED)
             .order_by(SalesAppointment.starts_at.asc()).all())
    last_meeting = None
    next_meeting = None
    for a in appts:
        if a.ends_at < now:
            last_meeting = a
        elif next_meeting is None:
            next_meeting = a

    def appt_brief(a):
        if a is None:
            return None
        from app.services import appointment_meetings as apmeet
        mt = (db.query(MeetingType).filter(MeetingType.id == a.meeting_type_id).first()
              if a.meeting_type_id else None)
        return {
            "id": a.id, "title": a.title,
            "meeting_type": mt.name if mt else None,
            "starts_at": a.starts_at, "timezone": a.timezone,
            "confirmation_status": a.confirmation_status,
            "video": apmeet.meeting_out(apmeet.get_meeting_row(db, a.id)),
        }

    # ── buyer activity ──────────────────────────────────────────────────────
    last_activity = None
    activity_count = 0
    if prop is not None:
        rows = (db.query(PortalEvent)
                .filter(PortalEvent.opportunity_id == opp.id)
                .order_by(PortalEvent.occurred_at.desc()).all())
        activity_count = len(rows)
        if rows:
            e = rows[0]
            last_activity = {
                "event_type": e.event_type,
                "label": PORTAL_EVENT_LABELS.get(e.event_type, e.event_type),
                "detail": e.label,
                "occurred_at": e.occurred_at,
                "ago": _ago(e.occurred_at, now),
            }


    # ── warnings ────────────────────────────────────────────────────────────
    # Ordered by what should be dealt with first, not alphabetically. A rep
    # reads the top one and acts.
    def warn(level, text, action=None):
        warnings.append({"level": level, "text": text, "action": action})

    if prop is None:
        warn("amber", "No proposal on this deal yet.", "Create a proposal")
    else:
        st = prop.sales_status
        if st == PROP_DECLINED:
            warn("red", "The customer declined this proposal.",
                 "Call them, then revise or close the deal")
        elif st == PROP_CHANGE_REQUESTED:
            warn("red", "The customer asked for a change.",
                 "Create version %d" % ((prop.version or 1) + 1))
        elif st == PROP_EXPIRED:
            warn("red", "The proposal has expired.", "Re-issue it or close the deal")
        elif prop.expires_at and prop.expires_at < now:
            # Belt and braces: expiry has passed but the sweep has not run.
            warn("red", "The proposal is past its expiry date.", "Re-issue it")

        if st in (PROP_DRAFT, PROP_INTERNAL_REVIEW):
            warn("amber", "The proposal has never been sent.", "Finish and send it")
        elif st == PROP_READY:
            warn("amber", "The proposal is ready but has not been sent.", "Send it")
        elif st == PROP_SENT:
            hrs = _hours_since(prop.sent_at, now)
            if prop.first_viewed_at is None:
                if hrs is not None and hrs >= STALE_UNVIEWED_HOURS:
                    warn("amber", "Sent %s and still not opened."
                         % _ago(prop.sent_at, now), "Call them")
                else:
                    warn(None, "Sent — waiting for them to open it.")

        if activity_count == 0 and prop.sent_at is not None:
            warn("amber", "They have never opened the deal room.",
                 "Check the address, or call")

        if prop.expires_at and prop.expires_at >= now and st in (PROP_SENT, PROP_VIEWED):
            days = int((prop.expires_at - now).total_seconds() / 86400.0)
            if days <= EXPIRING_WITHIN_DAYS:
                warn("amber" if days > 2 else "red",
                     "Proposal expires in %d day%s." % (days, "" if days == 1 else "s"),
                     "Follow up or extend it")

    if not (opp.next_action or "").strip():
        warn("amber", "No next action set on this deal.", "Decide the next step")
    elif opp.next_action_due_at and opp.next_action_due_at < now:
        warn("amber", "The next action is overdue: %s" % opp.next_action)

    if next_meeting is None and opp.status == "open":
        warn("amber", "Nothing is scheduled with them.", "Book the next meeting")

    # The brand's sales manager — who a rep escalates a discount or a stuck
    # deal to. Resolved through the SAME helper the scheduler uses to fill a
    # sales_manager seat, so the person named here is the person a Closing Call
    # would actually be booked with.
    manager = None
    try:
        from app.services.meeting_roles import brand_members
        from app.models.sales_models import ROLE_SALES_MANAGER
        mgrs = brand_members(db, opp.brand_sales_org_id, role=ROLE_SALES_MANAGER)
        manager = _user_brief(db, mgrs[0].id) if mgrs else None
    except Exception:
        # A brand with no manager configured is a real state, not an error.
        log.exception("could not resolve sales manager for brand %s",
                      opp.brand_sales_org_id)
        manager = None

    return {
        "opportunity_id": opp.id,
        "company": opp.company_name,
        "stage": opp.stage,
        "salesperson": _user_brief(db, opp.owner_user_id),
        "manager": manager,
        "next_action": opp.next_action,
        "next_action_due_at": opp.next_action_due_at,
        "proposal": None if prop is None else {
            "id": prop.id,
            "proposal_number": prop.proposal_number,
            "version": prop.version or 1,
            "status": prop.sales_status,
            "status_label": PROPOSAL_STATUS_LABELS.get(prop.sales_status,
                                                       prop.sales_status),
            "amount": float(prop.final_amount) if prop.final_amount is not None else None,
            "currency": prop.currency or "USD",
            "expires_at": prop.expires_at,
            "sent_at": prop.sent_at,
            "first_viewed_at": prop.first_viewed_at,
            "last_viewed_at": prop.last_viewed_at,
            "accepted_at": prop.accepted_at,
            "declined_at": prop.declined_at,
            "change_requested_at": prop.change_requested_at,
            "customer_response_note": prop.customer_response_note,
        },
        "portal": {
            "opened": activity_count > 0,
            "event_count": activity_count,
            "last_activity": last_activity,
        },
        "last_meeting": appt_brief(last_meeting),
        "next_meeting": appt_brief(next_meeting),
        "warnings": warnings,
        # One number the pipeline board can badge without walking the list.
        "attention_count": len([w for w in warnings if w["level"] in ("amber", "red")]),
    }
