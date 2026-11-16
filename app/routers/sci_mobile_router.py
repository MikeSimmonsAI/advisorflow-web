"""EVOSYS PRO MOBILE - the program workspace on a phone (SCI first, Restland the same).

Three small reads for /app/program/...: the Dashboard's numbers and queue, the
Replies list, and the launcher's unanswered count. They answer from the same
records and the same rules as SCI Command (sci_command_router), scoped exactly
like it: the caller's program workspace (server-resolved, never taken from the
URL), a location only when it is one of that workspace's own, test and demo
families never counted. Reading a thread and sending a reply use the existing
endpoints (/program/command/family/{id}, /program/command/reply) unchanged.

Kept light on purpose: no analytics, no campaign catalogue, no system checks.

Time zone: the program day is Central (America/Chicago), as everywhere in SCI
(_central_day_start_utc); booked times are stored as Central wall time.
"""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.deps import get_db, require_tenant_or_observer
from app.models.models import BookingLink, EmailMessage, Lead, Message, User
from app.models.program_models import BookingPageEvent, ProgramResponse, ProgramSourceRecord
from app.routers import sci_command_router as C
from app.routers.program_router import _program
from app.services import test_records as _tr

router = APIRouter(prefix="/program/command/mobile", tags=["sci-mobile"])

TIME_ZONE = "America/Chicago"
QUEUE_PREVIEW = 5
UPCOMING_N = 3
BOOKED = ("booked", "confirmed")


def _scope(db: Session, user: User, location_id: Optional[str]):
    prog = _program(db, user, managers_only=False)
    org = prog.organization_id
    locs = C._locations(db, org)
    if location_id and location_id not in locs:
        # Another workspace's location is answered like one that does not exist.
        raise HTTPException(status_code=404, detail="Location not found.")
    return prog, org, locs


def _real_leads(db: Session, org: str, location_id: Optional[str]):
    """The workspace's real families (no test records), optionally one location's."""
    q = db.query(Lead.id).filter(Lead.organization_id == org, Lead.is_test.isnot(True))
    if location_id:
        q = q.filter(Lead.id.in_(db.query(ProgramSourceRecord.lead_id).filter(
            ProgramSourceRecord.organization_id == org, ProgramSourceRecord.location_id == location_id)))
    return q


def _open_rows(db: Session, org: str, location_id: Optional[str]):
    rq = _tr.exclude_test_responses(db.query(ProgramResponse).filter(ProgramResponse.organization_id == org))
    if location_id:
        rq = rq.filter(ProgramResponse.location_id == location_id)
    return rq, rq.filter(ProgramResponse.handling_status != "closed").all()


def _needs_reply(i: dict) -> bool:
    """SCI Command's own "Needs reply" filter (sci_command_router._command_response)."""
    return (i["handling_status"] in C._NEEDS_REPLY and i["class"] != "auto_reply"
            and not i["ai_answered"] and not i["is_test"])


def _queue(db: Session, org: str, locs: dict, rows: list) -> list:
    """Every open conversation as an SCI Command inbox line, newest reply per
    family. Only the newest row of each family is expanded (the same row the
    desktop inbox shows), so the cost is one line per family."""
    from app.services.programs import campaign_attribution as CA
    newest = {}
    for r in sorted(rows, key=lambda r: r.received_at or datetime.min, reverse=True):
        newest.setdefault(r.lead_id, r)
    if not newest:
        return []
    return C._inbox_items(db, org, list(newest.values()), locs, CA.Memberships(db, org), None, set())


def _line(i: dict) -> dict:
    """What a phone needs to choose what to handle next."""
    return {k: i.get(k) for k in ("lead_id", "response_id", "name", "location", "location_id", "channel",
                                  "channel_label", "summary", "received_at", "badge", "class", "handling_status",
                                  "sla_breached", "human_handoff", "home_visit", "ai_answered", "sender_name",
                                  "subject", "owner")}


def _needs_count(db: Session, org: str, locs: dict, rows: list) -> int:
    """The Needs-reply count without building every inbox line: only the
    families whose newest open reply is still waiting are expanded."""
    newest = {}
    for r in sorted(rows, key=lambda r: r.received_at or datetime.min, reverse=True):
        newest.setdefault(r.lead_id, r)
    waiting = [r for r in newest.values() if r.handling_status in C._NEEDS_REPLY and r.response_class != "auto_reply"]
    if not waiting:
        return 0
    from app.services.programs import campaign_attribution as CA
    items = C._inbox_items(db, org, waiting, locs, CA.Memberships(db, org), None, set())
    return sum(1 for i in items if _needs_reply(i))


def _day_bounds_local():
    """Today in Central wall time (booked_time is stored that way)."""
    now = C._local_now()
    start = datetime.combine(now.date(), datetime.min.time())
    return now, start, start + timedelta(days=1)


def _appointment(db: Session, b: BookingLink, names: dict, locs: dict, loc_of: dict) -> dict:
    lead_name = names.get(b.lead_id) or "Family"
    return {"booking_id": b.id, "lead_id": b.lead_id, "name": lead_name,
            "when": C._booking_time.wire(db, b), "when_text": C._booked_text(db, b),
            "label": getattr(b, "appt_label", None), "meeting_type": getattr(b, "meeting_type", None),
            "status": b.status, "location": locs.get(loc_of.get(b.lead_id))}


@router.get("/summary")
def mobile_summary(location_id: Optional[str] = None, db: Session = Depends(get_db),
                   user: User = Depends(require_tenant_or_observer)):
    prog, org, locs = _scope(db, user, location_id)
    start = C._central_day_start_utc()
    real = _real_leads(db, org, location_id)

    texts = db.query(Message.id).filter(Message.lead_id.in_(real), Message.sent_at >= start).count()
    emails = (db.query(EmailMessage.id).filter(EmailMessage.lead_id.in_(real), EmailMessage.sent_at >= start,
                                               EmailMessage.status.notin_(("failed", "bounced"))).count())
    rq, rows = _open_rows(db, org, location_id)
    replies_today = rq.filter(ProgramResponse.received_at >= start,
                              ProgramResponse.response_class.notin_(("auto_reply",))).count()
    # BOOKED TODAY = the moment of booking was today (page, AI setter or a
    # person: every program booking records it, calendar_router), never the
    # appointment's own date.
    booked_today = (db.query(BookingPageEvent.booking_link_id)
                    .filter(BookingPageEvent.organization_id == org, BookingPageEvent.event == "booked",
                            BookingPageEvent.created_at >= start, BookingPageEvent.lead_id.in_(real))
                    .distinct().count())

    queue = _queue(db, org, locs, rows)
    waiting = [i for i in queue if _needs_reply(i)]

    now, day0, day1 = _day_bounds_local()
    bq = (db.query(BookingLink).filter(BookingLink.lead_id.in_(real), BookingLink.status.in_(BOOKED),
                                       BookingLink.booked_time.isnot(None)))
    taking_place_today = bq.filter(BookingLink.booked_time >= day0, BookingLink.booked_time < day1).count()
    upcoming_rows = (bq.filter(BookingLink.booked_time >= now - timedelta(minutes=30))
                     .order_by(BookingLink.booked_time).limit(UPCOMING_N).all())
    ids = [b.lead_id for b in upcoming_rows]
    names = {l.id: ("%s %s" % (l.first_name or "", l.last_name or "")).strip().title()
             for l in db.query(Lead).filter(Lead.id.in_(ids or ["-"])).all()}
    loc_of = {lid: loc for lid, loc in db.query(ProgramSourceRecord.lead_id, ProgramSourceRecord.location_id)
              .filter(ProgramSourceRecord.organization_id == org, ProgramSourceRecord.lead_id.in_(ids or ["-"])).all()}
    return {
        "organization_id": org,
        "time_zone": TIME_ZONE,
        "location_id": location_id,
        "locations": [{"id": k, "name": v} for k, v in sorted(locs.items(), key=lambda kv: kv[1])],
        "stats": {"sent_today": {"total": texts + emails, "texts": texts, "emails": emails},
                  "replies_today": replies_today,
                  "booked_today": booked_today},
        "taking_place_today": taking_place_today,
        "needs_reply": {"count": len(waiting), "items": [_line(i) for i in waiting[:QUEUE_PREVIEW]]},
        "upcoming": [_appointment(db, b, names, locs, loc_of) for b in upcoming_rows],
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }


@router.get("/replies")
def mobile_replies(location_id: Optional[str] = None, limit: int = Query(60, ge=1, le=200),
                   db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    """Open conversations, the ones that need a person first (newest first in
    each group, as SCI Command's inbox)."""
    prog, org, locs = _scope(db, user, location_id)
    _rq, rows = _open_rows(db, org, location_id)
    queue = [i for i in _queue(db, org, locs, rows) if not i["is_test"]]
    waiting = [i for i in queue if _needs_reply(i)]
    rest = [i for i in queue if not _needs_reply(i)]
    ordered = waiting + rest
    return {"organization_id": org, "needs_reply": len(waiting), "total": len(ordered),
            "items": [dict(_line(i), needs_reply=_needs_reply(i)) for i in ordered[:limit]],
            "updated_at": datetime.utcnow().isoformat() + "Z"}


@router.get("/count")
def mobile_count(location_id: Optional[str] = None, db: Session = Depends(get_db),
                 user: User = Depends(require_tenant_or_observer)):
    """The launcher card's number: conversations that need a reply."""
    prog, org, locs = _scope(db, user, location_id)
    _rq, rows = _open_rows(db, org, location_id)
    return {"organization_id": org, "needs_reply": _needs_count(db, org, locs, rows)}
