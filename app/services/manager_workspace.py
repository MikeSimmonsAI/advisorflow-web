"""Sales Manager command workspace — Checkpoint 5.

WHAT THIS ANSWERS. Six questions, in the order a manager actually asks them:
what is my team doing today · what needs my attention · where are deals stuck ·
who needs help · what requires my approval · what should I do next.

WHAT THIS IS NOT. It is not an analytics dashboard. Nothing here is a chart, a
trend, or a leaderboard. Every row on every section names a deal or a person and
says what to do about it. A number a manager cannot act on is a number that
costs attention and returns nothing.

IT IS ALSO NOT SURVEILLANCE. The per-rep section carries workload and blockage —
open deals, what is overdue, what is waiting on the customer, when the record was
last touched. It deliberately carries no message counts, no response times, no
activity minutes, and no ranking. A manager screen that measures effort rather
than obstacles turns into a stick, and reps then manage the metric.

WHY IT IS A SEPARATE SERVICE. `closing_view` in proposal_workqueue answers the
same shape of question for ONE deal and costs about ten queries doing it. That is
right for one deal and catastrophic for sixty: a manager view that looped it
would fire five hundred queries to draw one screen. Everything here is batched —
the query count is flat regardless of how many reps or deals the brand has.

SCOPING. Every query in this module filters on brand_sales_org_id explicitly.
Nothing is scoped by "whatever the caller can see", because for a god_admin that
is every brand, and a silently-cross-brand total is a number that looks right and
is wrong.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.models import (
    User, Proposal, PortalEvent,
    PROP_DRAFT, PROP_INTERNAL_REVIEW, PROP_READY, PROP_SENT, PROP_VIEWED,
    PROP_ACCEPTED, PROP_DECLINED, PROP_CHANGE_REQUESTED, PROP_EXPIRED,
    PROP_SUPERSEDED, PROPOSAL_STATUS_LABELS,
)
from app.models.sales_models import (
    BrandSalesOrg, Opportunity, OpportunityEvent, Membership,
    ROLE_SALES_MANAGER, ROLE_SALES_REP, SCOPE_BRAND_SALES_ORG,
    STAGE_CLOSING, STAGE_PROPOSAL, STAGE_WON, STAGE_LOST, STAGE_LIVE,
    STAGE_ONBOARDING, STAGE_DEMO_BUILD, STAGE_DISCOVERY, STAGE_LABELS,
)
from app.models.scheduling_models import (
    SalesAppointment, AppointmentParticipant, MeetingType, APPT_CANCELLED,
    CONF_PENDING,
)
from app.models.calendar_models import SYNC_NEEDS_ATTENTION, SYNC_LABELS
from app.models.meeting_models import (
    AppointmentMeeting, MEET_NEEDS_ATTENTION, MEET_LABELS,
)
from app.services import availability as _av
from app.services import proposal_workqueue as _pwq
from app.services import pricing_approvals as _appr

log = logging.getLogger(__name__)

# A deal nobody has touched in this long is not "in progress".
STALE_ACTIVITY_DAYS = 10
# Consistent with the rep-side workqueue rather than a second opinion.
STALE_UNVIEWED_HOURS = _pwq.STALE_UNVIEWED_HOURS
EXPIRING_WITHIN_DAYS = _pwq.EXPIRING_WITHIN_DAYS
# Matches _attention()'s threshold on the rep board so one deal is not "stalled"
# on the manager screen and fine on the rep's.
STALLED_IN_STAGE_DAYS = 21

_CLOSED_STAGES = (STAGE_WON, STAGE_LOST, STAGE_LIVE, STAGE_ONBOARDING)


# ── small helpers ───────────────────────────────────────────────────────────

def _f(v):
    return None if v is None else float(v)


def _ago(then: Optional[datetime], now: datetime) -> Optional[str]:
    return _pwq._ago(then, now) if then else None


def _days_since(then: Optional[datetime], now: datetime) -> Optional[int]:
    if then is None:
        return None
    return max(0, int((now - then).total_seconds() // 86400))


def _item(kind: str, level: str, title: str, detail: str, opp: Opportunity,
          names: Dict[str, str], action: str = None,
          proposal_id: str = None, appointment_id: str = None) -> dict:
    """One attention row. Always names the deal, the person, and the next move.

    `level` is red or amber — the same two-tone vocabulary the rep screens use,
    so a manager and a rep reading the same deal see the same urgency.
    """
    return {
        "kind": kind,
        "level": level,
        "title": title,
        "detail": detail,
        "action": action,
        "opportunity_id": opp.id,
        "company": opp.company_name,
        "stage": opp.stage,
        "stage_label": STAGE_LABELS.get(opp.stage, opp.stage),
        "owner_user_id": opp.owner_user_id,
        "owner_name": names.get(opp.owner_user_id),
        "deal_value": _f(opp.deal_value),
        "proposal_id": proposal_id,
        "appointment_id": appointment_id,
    }


# ── the batched loaders ─────────────────────────────────────────────────────

def team_members(db: Session, brand_sales_org_id: str) -> List[dict]:
    """The brand's sellers, one query.

    Filtered to the two sales roles on purpose. `brand_members` with no role
    returns everyone holding any membership, which includes a god_admin who
    happens to hold one — and a platform owner listed as a rep with zero deals
    makes every team total wrong.
    """
    rows = (db.query(User, Membership)
            .join(Membership, Membership.user_id == User.id)
            .filter(Membership.scope_type == SCOPE_BRAND_SALES_ORG,
                    Membership.scope_id == brand_sales_org_id,
                    Membership.is_active.is_(True),
                    Membership.role.in_((ROLE_SALES_MANAGER, ROLE_SALES_REP)),
                    User.is_active.is_(True))
            .order_by(User.full_name.asc())
            .all())
    out, seen = [], set()
    for u, m in rows:
        if u.id in seen:            # a user holding both roles appears once
            continue
        seen.add(u.id)
        out.append({
            "id": u.id,
            "full_name": u.full_name,
            "email": u.email,
            "role": m.role,
            "role_label": ("Sales Manager" if m.role == ROLE_SALES_MANAGER
                           else "Sales Representative"),
        })
    return out


def _last_activity_map(db: Session, opp_ids: List[str]) -> Dict[str, datetime]:
    """opportunity_id -> the most recent thing that happened on it.

    TWO sources, deliberately. `opportunity_events` records what the team did;
    `portal_events` records what the BUYER did. A deal where the customer opened
    the proposal an hour ago is not stale just because nobody on our side has
    typed anything since — and reading only our own events would say it was.

    Two grouped queries, not one per deal.
    """
    if not opp_ids:
        return {}
    out: Dict[str, datetime] = {}
    for row in (db.query(OpportunityEvent.opportunity_id,
                         func.max(OpportunityEvent.occurred_at))
                .filter(OpportunityEvent.opportunity_id.in_(opp_ids))
                .group_by(OpportunityEvent.opportunity_id).all()):
        if row[1] is not None:
            out[row[0]] = row[1]
    for row in (db.query(PortalEvent.opportunity_id,
                         func.max(PortalEvent.occurred_at))
                .filter(PortalEvent.opportunity_id.in_(opp_ids))
                .group_by(PortalEvent.opportunity_id).all()):
        if row[1] is None:
            continue
        cur = out.get(row[0])
        if cur is None or row[1] > cur:
            out[row[0]] = row[1]
    return out


def _portal_counts(db: Session, opp_ids: List[str]) -> Dict[str, dict]:
    """opportunity_id -> {count, last_at}. One grouped query."""
    if not opp_ids:
        return {}
    rows = (db.query(PortalEvent.opportunity_id,
                     func.count(PortalEvent.id),
                     func.max(PortalEvent.occurred_at))
            .filter(PortalEvent.opportunity_id.in_(opp_ids))
            .group_by(PortalEvent.opportunity_id).all())
    return {r[0]: {"count": int(r[1] or 0), "last_at": r[2]} for r in rows}


def _current_proposals(db: Session, opp_ids: List[str]) -> Dict[str, Proposal]:
    """opportunity_id -> its live proposal (highest version), one query."""
    out: Dict[str, Proposal] = {}
    for p in _pwq._live_proposals(db, opp_ids):      # already ordered version DESC
        out.setdefault(p.opportunity_id, p)
    return out


def _day_window(day, tzname: str):
    """The UTC instants bounding one LOCAL day in the brand's timezone.

    Not `utcnow().date()`. A manager in Chicago opening this at 8am must see
    their team's Chicago day, not a UTC window that already dropped this
    morning's 7am call.
    """
    start = _av.local_to_utc(day, 0, tzname)
    end = _av.local_to_utc(day, 24 * 60, tzname)
    return start, end


# ── section: team today ─────────────────────────────────────────────────────

def team_today(db: Session, org: BrandSalesOrg, members: List[dict],
               day=None, now=None) -> dict:
    """Every meeting the team has today, by person, with what it is.

    Four queries total: appointments, participants, meeting types, video rows.
    """
    now = now or datetime.utcnow()
    tz = org.timezone or "America/Chicago"
    day = day or _av.utc_to_local(now, tz).date()
    start, end = _day_window(day, tz)

    appts = (db.query(SalesAppointment)
             .filter(SalesAppointment.brand_sales_org_id == org.id,
                     SalesAppointment.status != APPT_CANCELLED,
                     SalesAppointment.starts_at >= start,
                     SalesAppointment.starts_at < end)
             .order_by(SalesAppointment.starts_at.asc()).all())

    appt_ids = [a.id for a in appts]
    parts_by_appt: Dict[str, List[AppointmentParticipant]] = {}
    if appt_ids:
        for p in (db.query(AppointmentParticipant)
                  .filter(AppointmentParticipant.appointment_id.in_(appt_ids)).all()):
            parts_by_appt.setdefault(p.appointment_id, []).append(p)

    type_ids = [a.meeting_type_id for a in appts if a.meeting_type_id]
    types = {}
    if type_ids:
        types = {t.id: t for t in db.query(MeetingType)
                 .filter(MeetingType.id.in_(type_ids)).all()}

    videos = {}
    if appt_ids:
        videos = {m.appointment_id: m for m in db.query(AppointmentMeeting)
                  .filter(AppointmentMeeting.appointment_id.in_(appt_ids)).all()}

    opp_ids = [a.opportunity_id for a in appts if a.opportunity_id]
    companies = {}
    if opp_ids:
        companies = {o.id: o.company_name for o in db.query(Opportunity)
                     .filter(Opportunity.id.in_(opp_ids)).all()}

    by_user: Dict[str, List[dict]] = {m["id"]: [] for m in members}
    kinds = {"discovery": 0, "demo": 0, "proposal": 0, "closing": 0,
             "internal": 0, "other": 0}
    unconfirmed = 0

    for a in appts:
        mt = types.get(a.meeting_type_id)
        key = (mt.key if mt else "") or ""
        if "discovery" in key and "demo" in key:
            bucket = "demo"                     # discovery+demo counts as a demo
        elif "discovery" in key:
            bucket = "discovery"
        elif "demo" in key:
            bucket = "demo"
        elif "proposal" in key:
            bucket = "proposal"
        elif "closing" in key:
            bucket = "closing"
        elif mt is not None and getattr(mt, "is_internal", False):
            bucket = "internal"
        else:
            bucket = "other"
        kinds[bucket] += 1
        if a.confirmation_status == CONF_PENDING:
            unconfirmed += 1

        vid = videos.get(a.id)
        row = {
            "id": a.id,
            "title": a.title,
            "meeting_type": mt.name if mt else None,
            "kind": bucket,
            "starts_at": a.starts_at,
            "starts_at_local": _av.utc_to_local(a.starts_at, a.timezone or tz),
            "timezone": a.timezone or tz,
            "duration_minutes": (
                int((a.ends_at - a.starts_at).total_seconds() // 60)
                if a.starts_at and a.ends_at else None),
            "opportunity_id": a.opportunity_id,
            "company": companies.get(a.opportunity_id),
            "confirmation_status": a.confirmation_status,
            "join_url": a.meeting_url,
            "video_needs_attention": bool(
                vid is not None and vid.status in MEET_NEEDS_ATTENTION),
        }
        for p in parts_by_appt.get(a.id, []):
            if p.user_id in by_user:
                by_user[p.user_id].append(row)

    people = []
    for m in members:
        rows = by_user.get(m["id"], [])
        people.append({
            "user_id": m["id"],
            "name": m["full_name"],
            "role": m["role"],
            "role_label": m["role_label"],
            "meeting_count": len(rows),
            "meetings": rows,
            # "Nothing booked" is a fact worth stating, not an empty row.
            "clear": len(rows) == 0,
        })

    return {
        "date": day,
        "timezone": tz,
        "total_meetings": len(appts),
        "unconfirmed": unconfirmed,
        "by_kind": kinds,
        "people": people,
        "working_today": sum(1 for p in people if p["meeting_count"] > 0),
    }


# ── section: attention required ─────────────────────────────────────────────

def attention(db: Session, org: BrandSalesOrg, opps: List[Opportunity],
              names: Dict[str, str], now=None) -> dict:
    """Everything that is stuck, late, failed, or waiting — with the reason.

    Ordered red before amber, then by deal value, so the most expensive problem
    a manager can still fix is the first thing they read.
    """
    now = now or datetime.utcnow()
    opp_ids = [o.id for o in opps]
    opp_by_id = {o.id: o for o in opps}
    props = _current_proposals(db, opp_ids)
    last_act = _last_activity_map(db, opp_ids)
    portal = _portal_counts(db, opp_ids)

    items: List[dict] = []

    # ── proposal states the customer put us in ──
    for opp in opps:
        p = props.get(opp.id)
        if p is None:
            continue
        st = p.sales_status
        if st == PROP_DECLINED:
            items.append(_item("proposal_declined", "red", "Proposal declined",
                               p.customer_response_note or "No reason given.",
                               opp, names, "Call them, then revise or close", p.id))
        elif st == PROP_CHANGE_REQUESTED:
            items.append(_item("change_requested", "red", "Customer asked for a change",
                               p.customer_response_note or "No detail given.",
                               opp, names, "Create version %d" % ((p.version or 1) + 1), p.id))
        # Expiry is read from the DATE, not the status. The sweep that would set
        # PROP_EXPIRED is not wired to anything, so a status-only check would
        # report zero expired proposals forever.
        elif p.expires_at and p.expires_at < now and st in (
                PROP_SENT, PROP_VIEWED, PROP_READY, PROP_EXPIRED):
            items.append(_item("proposal_expired", "red", "Proposal has expired",
                               "Expired %s." % _ago(p.expires_at, now),
                               opp, names, "Re-issue it or close the deal", p.id))
        elif st == PROP_SENT:
            hrs = _pwq._hours_since(p.sent_at, now)
            if hrs is not None and hrs >= STALE_UNVIEWED_HOURS:
                items.append(_item("proposal_unopened", "amber",
                                   "Sent and never opened",
                                   "Sent %s. No portal activity." % _ago(p.sent_at, now),
                                   opp, names, "Check the address, or call", p.id))
        if p.expires_at and st in (PROP_SENT, PROP_VIEWED, PROP_READY):
            days = (p.expires_at - now).total_seconds() / 86400.0
            if 0 <= days <= EXPIRING_WITHIN_DAYS:
                items.append(_item("proposal_expiring",
                                   "red" if days <= 2 else "amber",
                                   "Proposal expires in %d day%s" % (
                                       int(days), "" if int(days) == 1 else "s"),
                                   "%s %s on the table." % (
                                       p.currency or "USD", _f(p.final_amount)),
                                   opp, names, "Follow up or extend it", p.id))
        if st == PROP_READY:
            items.append(_item("proposal_ready", "amber", "Finished but never sent",
                               "Version %d is ready." % (p.version or 1),
                               opp, names, "Send it", p.id))

    # ── deals the team has let go quiet ──
    for opp in opps:
        if opp.stage in _CLOSED_STAGES:
            continue
        if opp.next_action_due_at and opp.next_action_due_at < now:
            items.append(_item("overdue_action", "amber", "Next action is overdue",
                               opp.next_action or "No description.",
                               opp, names, "Reset the date or do it"))
        elif not opp.next_action:
            items.append(_item("no_next_action", "amber", "No next action set",
                               "Nothing decided for this deal.",
                               opp, names, "Decide the next step"))

        days_stage = (max(0, (now - opp.stage_changed_at).days)
                      if opp.stage_changed_at else None)
        if days_stage is not None and days_stage >= STALLED_IN_STAGE_DAYS:
            items.append(_item("stalled", "amber",
                               "Stalled %d days in %s" % (
                                   days_stage, STAGE_LABELS.get(opp.stage, opp.stage)),
                               "No stage movement since %s." % _ago(opp.stage_changed_at, now),
                               opp, names, "Move it or close it"))

        touched = last_act.get(opp.id)
        d = _days_since(touched, now)
        if d is not None and d >= STALE_ACTIVITY_DAYS:
            items.append(_item("no_activity", "amber", "No activity for %d days" % d,
                               "Last recorded activity %s." % _ago(touched, now),
                               opp, names, "Make contact"))
        elif touched is None:
            items.append(_item("no_activity", "amber", "Nothing has ever been recorded",
                               "This deal has no timeline entries.",
                               opp, names, "Make contact"))

    # ── the plumbing failing under them ──
    # Both of these are brand-indexed, so they are one query each regardless of
    # how many meetings the team has.
    sync_rows = (db.query(AppointmentParticipant, SalesAppointment)
                 .join(SalesAppointment,
                       SalesAppointment.id == AppointmentParticipant.appointment_id)
                 .filter(SalesAppointment.brand_sales_org_id == org.id,
                         SalesAppointment.status != APPT_CANCELLED,
                         SalesAppointment.ends_at >= now - timedelta(days=1),
                         AppointmentParticipant.sync_status.in_(SYNC_NEEDS_ATTENTION))
                 .all())
    for part, appt in sync_rows:
        opp = opp_by_id.get(appt.opportunity_id)
        if opp is None:
            continue
        items.append(_item(
            "calendar_sync", "amber",
            "Calendar sync: %s" % SYNC_LABELS.get(part.sync_status, part.sync_status),
            "%s — %s" % (names.get(part.user_id) or "A participant",
                         part.sync_error or "no detail recorded"),
            opp, names, "Reconnect the calendar or retry", None, appt.id))

    video_rows = (db.query(AppointmentMeeting, SalesAppointment)
                  .join(SalesAppointment,
                        SalesAppointment.id == AppointmentMeeting.appointment_id)
                  .filter(AppointmentMeeting.brand_sales_org_id == org.id,
                          AppointmentMeeting.status.in_(MEET_NEEDS_ATTENTION),
                          SalesAppointment.status != APPT_CANCELLED,
                          SalesAppointment.ends_at >= now - timedelta(days=1))
                  .all())
    for meet, appt in video_rows:
        opp = opp_by_id.get(appt.opportunity_id)
        if opp is None:
            continue
        items.append(_item(
            "video_failed", "red",
            "Video meeting %s" % MEET_LABELS.get(meet.status, meet.status),
            meet.provider_error or "No error recorded.",
            opp, names, "Retry the video link before the call", None, appt.id))

    order = {"red": 0, "amber": 1}
    items.sort(key=lambda i: (order.get(i["level"], 2), -(i["deal_value"] or 0)))

    groups: Dict[str, int] = {}
    per_owner: Dict[str, int] = {}
    for i in items:
        groups[i["kind"]] = groups.get(i["kind"], 0) + 1
        if i["owner_user_id"]:
            per_owner[i["owner_user_id"]] = per_owner.get(i["owner_user_id"], 0) + 1

    return {
        "items": items,
        "total": len(items),
        "red": sum(1 for i in items if i["level"] == "red"),
        "by_kind": groups,
        "by_owner": per_owner,
        "portal": portal,          # reused by the closing pipeline, already loaded
        "proposals": props,
        "last_activity": last_act,
    }


# ── section: closing pipeline ───────────────────────────────────────────────

def closing_pipeline(db: Session, opps: List[Opportunity], names: Dict[str, str],
                     props: Dict[str, Proposal], portal: Dict[str, dict],
                     last_act: Dict[str, datetime], appt_map: Dict[str, SalesAppointment],
                     now=None) -> dict:
    """Every deal close enough to name a number on.

    Takes everything it needs as arguments rather than querying again — this is
    the same data `attention` already loaded, and loading it twice to draw two
    sections of one screen is how a page becomes slow for no reason.
    """
    now = now or datetime.utcnow()
    rows = []
    total = 0.0
    for opp in opps:
        p = props.get(opp.id)
        # In the closing stage, or has a live proposal in front of a customer.
        live = p is not None and p.sales_status in (
            PROP_SENT, PROP_VIEWED, PROP_ACCEPTED, PROP_CHANGE_REQUESTED)
        if opp.stage not in (STAGE_CLOSING, STAGE_PROPOSAL) and not live:
            continue
        if opp.stage in (STAGE_WON, STAGE_LOST):
            continue

        pt = portal.get(opp.id) or {}
        nxt = appt_map.get(opp.id)
        value = _f(opp.deal_value) if opp.deal_value is not None else (
            _f(p.final_amount) if p is not None else None)
        if value:
            total += value
        rows.append({
            "opportunity_id": opp.id,
            "company": opp.company_name,
            "contact_name": opp.contact_name,
            "stage": opp.stage,
            "stage_label": STAGE_LABELS.get(opp.stage, opp.stage),
            "owner_user_id": opp.owner_user_id,
            "owner_name": names.get(opp.owner_user_id),
            "deal_value": value,
            "currency": (p.currency if p else None) or "USD",
            "proposal_number": p.proposal_number if p else None,
            "proposal_version": (p.version or 1) if p else None,
            "proposal_status": p.sales_status if p else None,
            "proposal_status_label": (
                PROPOSAL_STATUS_LABELS.get(p.sales_status, p.sales_status) if p else None),
            "proposal_expires_at": p.expires_at if p else None,
            "buyer_events": pt.get("count", 0),
            "buyer_last_at": pt.get("last_at"),
            "buyer_last_ago": _ago(pt.get("last_at"), now),
            "last_touch_at": last_act.get(opp.id),
            "last_touch_ago": _ago(last_act.get(opp.id), now),
            "next_action": opp.next_action,
            "next_action_due_at": opp.next_action_due_at,
            "next_meeting_at": nxt.starts_at if nxt else None,
            "next_meeting_title": nxt.title if nxt else None,
            "expected_close_at": opp.expected_close_at if hasattr(
                opp, "expected_close_at") else None,
        })

    # Nearest expiry first — that is the clock a manager is actually racing.
    rows.sort(key=lambda r: (r["proposal_expires_at"] is None,
                             r["proposal_expires_at"] or now))
    return {"rows": rows, "count": len(rows), "total_value": total}


# ── section: rep activity ───────────────────────────────────────────────────

def rep_rollup(db: Session, members: List[dict], opps: List[Opportunity],
               props: Dict[str, Proposal], last_act: Dict[str, datetime],
               attention_by_owner: Dict[str, int], today: dict,
               now=None) -> List[dict]:
    """Workload and blockage per person. No effort metrics, by design."""
    now = now or datetime.utcnow()
    meetings_today = {p["user_id"]: p["meeting_count"] for p in today["people"]}

    by_owner: Dict[str, List[Opportunity]] = {}
    for o in opps:
        if o.owner_user_id:
            by_owner.setdefault(o.owner_user_id, []).append(o)

    out = []
    for m in members:
        mine = by_owner.get(m["id"], [])
        overdue = sum(1 for o in mine
                      if o.next_action_due_at and o.next_action_due_at < now
                      and o.stage not in _CLOSED_STAGES)
        demos = sum(1 for o in mine if o.stage == STAGE_DEMO_BUILD)
        awaiting_send = 0
        awaiting_customer = 0
        for o in mine:
            p = props.get(o.id)
            if p is None:
                continue
            if p.sales_status in (PROP_DRAFT, PROP_INTERNAL_REVIEW, PROP_READY):
                awaiting_send += 1
            elif p.sales_status in (PROP_SENT, PROP_VIEWED):
                awaiting_customer += 1
        touched = [last_act.get(o.id) for o in mine if last_act.get(o.id)]
        newest = max(touched) if touched else None
        out.append({
            "user_id": m["id"],
            "name": m["full_name"],
            "email": m["email"],
            "role": m["role"],
            "role_label": m["role_label"],
            "open_deals": len(mine),
            "needs_attention": attention_by_owner.get(m["id"], 0),
            "overdue_actions": overdue,
            "meetings_today": meetings_today.get(m["id"], 0),
            "demos_to_build": demos,
            "proposals_awaiting_send": awaiting_send,
            "proposals_with_customer": awaiting_customer,
            "pipeline_value": sum(_f(o.deal_value) or 0 for o in mine),
            "last_recorded_activity": newest,
            "last_recorded_activity_ago": _ago(newest, now),
        })
    out.sort(key=lambda r: (-r["needs_attention"], -r["open_deals"]))
    return out


# ── the one call the screen makes ───────────────────────────────────────────

def overview(db: Session, org: BrandSalesOrg, day=None, now=None) -> dict:
    """Everything the manager command screen renders, in one batched pass."""
    now = now or datetime.utcnow()

    members = team_members(db, org.id)
    names = {m["id"]: m["full_name"] for m in members}

    opps = (db.query(Opportunity)
            .filter(Opportunity.brand_sales_org_id == org.id,
                    Opportunity.status == "open")
            .all())
    # An owner who has left the team still owns rows; name them anyway rather
    # than rendering a blank where a person should be.
    missing = {o.owner_user_id for o in opps
               if o.owner_user_id and o.owner_user_id not in names}
    if missing:
        for u in db.query(User).filter(User.id.in_(list(missing))).all():
            names[u.id] = u.full_name

    today = team_today(db, org, members, day=day, now=now)
    att = attention(db, org, opps, names, now=now)

    from app.routers.sales_router import next_appt_map
    appt_map = next_appt_map(db, opps)

    closing = closing_pipeline(db, opps, names, att["proposals"], att["portal"],
                               att["last_activity"], appt_map, now=now)
    reps = rep_rollup(db, members, opps, att["proposals"], att["last_activity"],
                      att["by_owner"], today, now=now)

    _appr.sweep_stale(db, org.id, now=now)
    pending = _appr.pending_for_brand(db, org.id)
    decided = _appr.recent_decided_for_brand(db, org.id, limit=8)

    queues = _pwq.proposal_queues(db, opps, now=now, limit=25, names=names)

    return {
        "brand_sales_org_id": org.id,
        "brand_name": org.name,
        "timezone": org.timezone,
        "generated_at": now,
        "team": members,
        "team_today": today,
        "attention": {k: v for k, v in att.items()
                      if k in ("items", "total", "red", "by_kind", "by_owner")},
        "approvals": {
            "pending": [_appr.request_out(db, r) for r in pending],
            "pending_count": len(pending),
            "recent": [_appr.request_out(db, r) for r in decided],
        },
        "closing_pipeline": closing,
        "reps": reps,
        "proposal_queues": queues,
    }


def rep_detail(db: Session, org: BrandSalesOrg, user_id: str, now=None) -> dict:
    """One rep's book, for the drill-down.

    Returns deal rows that link straight into the existing Opportunity Detail
    screen. It deliberately does not restate that screen — a manager who wants
    the timeline, the proposal or the buyer activity opens the deal, where all
    three already live.
    """
    now = now or datetime.utcnow()
    person = db.query(User).filter(User.id == user_id).first()
    opps = (db.query(Opportunity)
            .filter(Opportunity.brand_sales_org_id == org.id,
                    Opportunity.owner_user_id == user_id,
                    Opportunity.status == "open")
            .all())
    names = {user_id: person.full_name if person else None}
    props = _current_proposals(db, [o.id for o in opps])
    last_act = _last_activity_map(db, [o.id for o in opps])

    from app.routers.sales_router import next_appt_map, _card
    appt_map = next_appt_map(db, opps)

    rows = []
    for o in opps:
        p = props.get(o.id)
        card = _card(o, db, appt_map, names=names)
        card["proposal_status"] = p.sales_status if p else None
        card["proposal_status_label"] = (
            PROPOSAL_STATUS_LABELS.get(p.sales_status, p.sales_status) if p else None)
        card["proposal_number"] = p.proposal_number if p else None
        card["last_touch_at"] = last_act.get(o.id)
        card["last_touch_ago"] = _ago(last_act.get(o.id), now)
        rows.append(card)
    rows.sort(key=lambda r: (r["attention"] is None, -(r["deal_value"] or 0)))

    return {
        "user_id": user_id,
        "name": person.full_name if person else None,
        "email": person.email if person else None,
        "open_deals": len(rows),
        "deals": rows,
        "generated_at": now,
    }
