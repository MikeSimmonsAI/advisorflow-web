"""VOLT Zoom lane - interviews and orientations.

WHICH METHOD SHIPS: STAFF-SUPPLIED PARTICIPANT JOIN LINKS.
The repository has a Zoom Server-to-Server integration
(app/services/meeting_providers/zoom.py), but its credentials are per sales
BRAND (MeetingProviderConfig) for booked sales meetings. Nothing authorizes it
for VOLT, so VOLT does not call Zoom. Staff create the meeting in Zoom and paste
its participant join URL here. Saving a link records a schedule in VOLT; it
never creates, edits or cancels anything in Zoom, and the screens say so.

SELF-BOOKING (2026-11-06). A session with a capacity is AVAILABILITY: staff
open booking, and an application staff have made eligible (schedule_stage)
can book one session of that kind for its project. Capacity is enforced in
the booking transaction - the application row and the session row are locked
(SELECT ... FOR UPDATE), the active bookings are counted under that lock, and
only then is the seat taken. A double click, a retry or a second tab on the
same application finds its own active booking and returns it; two people
racing for the last seat get one booking and one "no longer available".
VOLT never creates, moves or cancels the meeting in Zoom: closing, rescheduling
or canceling here changes VOLT's availability and what attendees see. Staff
make the matching change in Zoom themselves.

THE LINK IS ACCESS-CONTROLLED, NOT JUST HIDDEN. The applicant API returns
`join_url` only to an applicant assigned to a session that is not canceled.
Start links (/s/), host keys (zak=) and anything not https on a Zoom host are
refused at save time, so a host credential can never reach an applicant.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.volt_models import (
    VoltApplication, VoltProject, VoltRep, VoltSession, VoltSessionAttendee,
)
from app.services.volt import common as C

KINDS = ("interview", "orientation")
MAX_CAPACITY = 500
ACTIVE = "active"
ATTENDANCE = ("invited", "attended", "no_show")
_ZOOM_HOST = re.compile(r"^(?:[a-z0-9-]+\.)*(zoom\.us|zoomgov\.com)$")


def _err(field: str, msg: str):
    raise HTTPException(status_code=422, detail={"message": msg, "fields": {field: msg}})


def validate_join_url(raw: str) -> str:
    url = (raw or "").strip()
    try:
        u = urlparse(url)
    except ValueError:
        _err("join_url", "Paste the participant join link from Zoom.")
    if u.scheme != "https":
        _err("join_url", "The Zoom link must start with https://")
    host = (u.hostname or "").lower()
    if not _ZOOM_HOST.match(host):
        _err("join_url", "That is not a Zoom link. Paste the participant join link (zoom.us/j/...).")
    if u.username or u.password or u.port not in (None, 443):
        _err("join_url", "Paste the participant join link exactly as Zoom shows it.")
    path = u.path or ""
    if path.startswith("/s/") or "zak" in {k.lower() for k in parse_qs(u.query)}:
        _err("join_url", "That is a HOST start link. Paste the participant join link instead (zoom.us/j/...).")
    if not re.fullmatch(r"/(?:j|w)/\d{9,12}/?", path):
        _err("join_url", "Paste the participant join link (it looks like https://zoom.us/j/12345678901).")
    return url[:600]


def _tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo((name or "").strip())
    except (ZoneInfoNotFoundError, ValueError):
        _err("timezone", "Choose a time zone.")


def _starts(local: str, tz: ZoneInfo) -> datetime:
    try:
        naive = datetime.fromisoformat((local or "").strip())
    except ValueError:
        _err("starts_local", "Enter the date and time.")
    if naive.tzinfo is not None:
        naive = naive.replace(tzinfo=None)
    return naive.replace(tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)


def _assign(db, org_id, sess: VoltSession, serials: List[int]) -> None:
    wanted = set()
    for s in serials or []:
        try:
            wanted.add(int(s))
        except (TypeError, ValueError):
            continue
    if wanted:
        ok = {r.serial for r in db.query(VoltRep).filter(VoltRep.organization_id == org_id,
                                                         VoltRep.serial.in_(wanted)).all()}
        if ok != wanted:
            _err("rep_ids", "One of those applicants is not in this program.")
    rows = db.query(VoltSessionAttendee).filter(VoltSessionAttendee.session_id == sess.id).all()
    # Staff assignment manages ASSIGNED rows only; an applicant's own booking
    # is never removed by editing the assignee list.
    have = {a.rep_serial: a for a in rows if (a.source or "assigned") == "assigned"}
    booked = {a.rep_serial for a in rows if a.source == "booked"}
    for s, a in have.items():
        if s not in wanted:
            db.delete(a)
    for s in wanted - set(have) - booked:
        db.add(VoltSessionAttendee(session_id=sess.id, rep_serial=s, source="assigned", booking_status=ACTIVE))


def save(db: Session, org_id: str, body: Dict[str, Any], actor, sess: Optional[VoltSession] = None) -> VoltSession:
    kind = (body.get("kind") or (sess.kind if sess else "")).strip().lower()
    if kind not in KINDS:
        _err("kind", "Choose interview or orientation.")
    project_id = body.get("project_id") or (sess.project_id if sess else None)
    project = db.get(VoltProject, project_id) if project_id else None
    if project is None or project.organization_id != org_id:
        _err("project_id", "Choose a project.")
    tzname = body.get("timezone") or (sess.timezone if sess else "")
    tz = _tz(tzname)
    starts = _starts(body.get("starts_local"), tz) if body.get("starts_local") else (sess.starts_at if sess else None)
    if starts is None:
        _err("starts_local", "Enter the date and time.")
    try:
        duration = int(body.get("duration_minutes") or (sess.duration_minutes if sess else 30))
    except (TypeError, ValueError):
        duration = 0
    if not 5 <= duration <= 480:
        _err("duration_minutes", "Duration must be between 5 and 480 minutes.")
    join_url = validate_join_url(body.get("join_url") or (sess.join_url if sess else ""))
    instructions = str(body.get("instructions") if body.get("instructions") is not None
                       else (sess.instructions if sess else "") or "")[:2000]
    title = str(body.get("title") or (sess.title if sess else "") or "")[:140] or None
    capacity = sess.capacity if sess else None
    if "capacity" in body:
        raw = body.get("capacity")
        if raw in (None, ""):
            capacity = None
        else:
            try:
                capacity = int(raw)
            except (TypeError, ValueError):
                _err("capacity", "Capacity must be a whole number, at least 1.")
            if not 1 <= capacity <= MAX_CAPACITY:
                _err("capacity", "Capacity must be between 1 and %d." % MAX_CAPACITY)
    if sess is not None and capacity is not None:
        taken = active_count(db, sess.id)
        if capacity < taken:
            _err("capacity", "%d applicant%s already booked. Capacity can't go below that; cancel bookings first."
                 % (taken, " is" if taken == 1 else "s are"))
    cutoff = sess.booking_cutoff_minutes if sess else None
    if "booking_cutoff_minutes" in body:
        raw = body.get("booking_cutoff_minutes")
        try:
            cutoff = None if raw in (None, "") else int(raw)
        except (TypeError, ValueError):
            _err("booking_cutoff_minutes", "Enter the cutoff in minutes.")
        if cutoff is not None and not 0 <= cutoff <= 20160:
            _err("booking_cutoff_minutes", "The cutoff must be between 0 and 20160 minutes (14 days).")
    if sess is None and starts <= C.now():
        _err("starts_local", "That time has already passed. Choose a future date and time.")
    if sess is None:
        sess = VoltSession(organization_id=org_id, created_by=actor.id)
        db.add(sess)
        action = "session_created"
        old_start = None
    else:
        if sess.status == "canceled":
            raise HTTPException(status_code=409, detail="This session was canceled. Create a new one.")
        action = "session_updated"
        old_start = sess.starts_at
    sess.kind, sess.project_id, sess.timezone = kind, project.id, tz.key
    sess.starts_at, sess.duration_minutes, sess.join_url = starts, duration, join_url
    sess.instructions, sess.title = instructions, title
    sess.capacity, sess.booking_cutoff_minutes = capacity, cutoff
    if "booking_open" in body:
        sess.booking_open = bool(body.get("booking_open")) and capacity is not None
    rescheduled = old_start is not None and old_start != starts
    if rescheduled:
        sess.rescheduled_at = C.now()
    db.flush()
    if "rep_ids" in body:
        _assign(db, org_id, sess, body.get("rep_ids") or [])
        db.flush()
        if sess.capacity is not None and active_count(db, sess.id) > sess.capacity:
            _err("rep_ids", "That is more people than this session's capacity of %d." % sess.capacity)
    C.audit(db, org_id, actor, "session", sess.id, "session_rescheduled" if rescheduled else action,
            field="starts_at", old=C.iso(old_start), new=C.iso(starts))
    sess._rescheduled = rescheduled                       # the router sends notices
    return sess


def cancel(db, org_id, sess: VoltSession, actor, reason: Optional[str]) -> VoltSession:
    if sess.status != "canceled":
        sess.status = "canceled"
        sess.canceled_at = C.now()
        sess.cancel_reason = (reason or "").strip()[:1000] or None
        sess.booking_open = False
        C.audit(db, org_id, actor, "session", sess.id, "session_canceled", reason=reason)
    return sess


def set_booking_open(db, org_id, sess: VoltSession, open_: bool, actor) -> VoltSession:
    """Close: no new bookings, existing ones stay. Reopen: bookable again
    only while there is a free seat - capacity is checked at booking."""
    if sess.status == "canceled":
        raise HTTPException(status_code=409, detail="This session was canceled.")
    if sess.capacity is None:
        _err("capacity", "Set a capacity before opening this session for booking.")
    if bool(sess.booking_open) != bool(open_):
        sess.booking_open = bool(open_)
        C.audit(db, org_id, actor, "session", sess.id, "booking_opened" if open_ else "booking_closed")
    return sess


# ── self-booking ─────────────────────────────────────────────────────────────

def _is_active(a: VoltSessionAttendee) -> bool:
    # NULL = a row from the assigned-only lane, which holds its seat.
    return a.booking_status in (None, ACTIVE)


def active_count(db, session_id: str) -> int:
    return (db.query(VoltSessionAttendee).filter(VoltSessionAttendee.session_id == session_id)
            .filter((VoltSessionAttendee.booking_status.is_(None)) | (VoltSessionAttendee.booking_status == ACTIVE))
            .count())


def active_booking(db, app: VoltApplication) -> Optional[VoltSessionAttendee]:
    """This application's live booking for its current scheduling stage."""
    if not app.schedule_stage:
        return None
    rows = (db.query(VoltSessionAttendee, VoltSession)
            .join(VoltSession, VoltSession.id == VoltSessionAttendee.session_id)
            .filter(VoltSessionAttendee.application_id == app.id, VoltSessionAttendee.booking_status == ACTIVE,
                    VoltSession.kind == app.schedule_stage, VoltSession.status == "scheduled").all())
    return rows[0][0] if rows else None


def _cutoff(sess: VoltSession) -> datetime:
    return sess.starts_at - timedelta(minutes=sess.booking_cutoff_minutes or 0)


def bookable_reason(db, sess: VoltSession, app: VoltApplication, taken: Optional[int] = None) -> Optional[str]:
    """None when this application may book this session now, else why not."""
    if sess.organization_id != app.organization_id or sess.project_id != app.project_id:
        return "not_for_you"
    if not app.schedule_stage or sess.kind != app.schedule_stage:
        return "not_for_you"
    if sess.status != "scheduled":
        return "canceled"
    if sess.capacity is None:
        return "not_for_you"
    if not sess.booking_open:
        return "closed"
    if C.now() >= _cutoff(sess):
        return "past"
    if (active_count(db, sess.id) if taken is None else taken) >= sess.capacity:
        return "full"
    return None


UNAVAILABLE = {"canceled": "That session was canceled.", "closed": "Booking for that time is closed.",
               "past": "That time is no longer available to book.", "full": "That time just filled up.",
               "not_for_you": "That time is not available for your application."}


def eligible(app: VoltApplication) -> bool:
    return bool(app.schedule_stage) and app.status in ("submitted", "needs_update", "approved", "started")


def available_for(db, app: VoltApplication) -> List[Dict[str, Any]]:
    """Open times this application may choose. Never a join link."""
    if not eligible(app):
        return []
    rows = (db.query(VoltSession).filter(VoltSession.organization_id == app.organization_id,
                                         VoltSession.project_id == app.project_id,
                                         VoltSession.kind == app.schedule_stage,
                                         VoltSession.status == "scheduled", VoltSession.booking_open.is_(True),
                                         VoltSession.capacity.isnot(None), VoltSession.starts_at > C.now())
            .order_by(VoltSession.starts_at.asc()).all())
    out = []
    for s in rows:
        taken = active_count(db, s.id)
        if bookable_reason(db, s, app, taken) is not None:
            continue
        out.append({"id": s.id, "kind": s.kind, "title": s.title, "starts_at": C.iso(s.starts_at),
                    "timezone": s.timezone, "starts_local": _local(s), "duration_minutes": s.duration_minutes,
                    "spots_left": s.capacity - taken, "capacity": s.capacity})
    return out


def _lock(db, model, pk):
    return db.query(model).filter(model.id == pk).with_for_update().first()


def book(db, rep: VoltRep, app_id: str, session_id: str, actor):
    """Take a seat. Returns (attendee_row, created: bool)."""
    app = _lock(db, VoltApplication, app_id)
    if app is None or app.rep_serial != rep.serial:
        raise HTTPException(status_code=404, detail="Application not found.")
    if not eligible(app):
        raise HTTPException(status_code=409, detail={"message": "Scheduling isn't open for this application yet.",
                                                     "code": "not_eligible"})
    current = active_booking(db, app)
    if current is not None:
        if current.session_id == session_id:
            return current, False                       # a repeat / retry: the same booking
        raise HTTPException(status_code=409, detail={
            "message": "You already have a Zoom time booked for this application. Cancel it first to choose again.",
            "code": "already_booked"})
    sess = _lock(db, VoltSession, session_id)
    if sess is None or sess.organization_id != app.organization_id:
        raise HTTPException(status_code=409, detail={"message": UNAVAILABLE["not_for_you"], "code": "slot_unavailable"})
    why = bookable_reason(db, sess, app)
    if why is not None:
        raise HTTPException(status_code=409, detail={"message": UNAVAILABLE[why] + " Choose another time.",
                                                     "code": "slot_unavailable", "reason": why})
    row = (db.query(VoltSessionAttendee).filter(VoltSessionAttendee.session_id == sess.id,
                                                VoltSessionAttendee.rep_serial == rep.serial).first())
    now = C.now()
    if row is None:
        row = VoltSessionAttendee(session_id=sess.id, rep_serial=rep.serial)
        db.add(row)
    row.source, row.application_id, row.booking_status = "booked", app.id, ACTIVE
    row.booked_at, row.canceled_at, row.cancel_reason, row.attendance = now, None, None, "invited"
    db.flush()
    C.audit(db, app.organization_id, actor, "application", app.id, "zoom_booked", field=sess.kind,
            new="%s %s" % (sess.id, C.iso(sess.starts_at)))
    return row, True


def cancel_booking(db, rep: VoltRep, app_id: str, actor, *, by_staff: bool = False, reason: Optional[str] = None):
    """Free the seat. Applicants may cancel up to the session's cutoff; after
    that, support helps. Returns the canceled row or None."""
    app = _lock(db, VoltApplication, app_id)
    if app is None or app.rep_serial != rep.serial:
        raise HTTPException(status_code=404, detail="Application not found.")
    row = active_booking(db, app)
    if row is None:
        return None
    sess = _lock(db, VoltSession, row.session_id)
    if not by_staff and C.now() >= _cutoff(sess):
        raise HTTPException(status_code=409, detail={
            "message": "It's too close to the start time to change this online. Email support@voltmarketing.live and we'll help.",
            "code": "past_cutoff"})
    row.booking_status, row.canceled_at = "canceled", C.now()
    row.cancel_reason = (reason or ("Canceled by staff" if by_staff else "Canceled by the applicant"))[:1000]
    C.audit(db, app.organization_id, actor, "application", app.id, "zoom_booking_canceled", field=sess.kind,
            old=sess.id, reason=row.cancel_reason)
    return row


def release_for_application(db, app: VoltApplication, actor, reason: str) -> int:
    rows = (db.query(VoltSessionAttendee).filter(VoltSessionAttendee.application_id == app.id,
                                                 VoltSessionAttendee.booking_status == ACTIVE).all())
    for r in rows:
        r.booking_status, r.canceled_at, r.cancel_reason = "canceled", C.now(), reason
        C.audit(db, app.organization_id, actor, "application", app.id, "zoom_booking_canceled", old=r.session_id,
                reason=reason)
    return len(rows)


def set_schedule_stage(db, org_id, app: VoltApplication, stage: Optional[str], actor) -> VoltApplication:
    stage = (stage or "").strip().lower() or None
    if stage is not None and stage not in KINDS:
        _err("stage", "Choose interview, orientation or off.")
    if stage is not None and app.status in ("draft", "withdrawn"):
        raise HTTPException(status_code=409, detail="Only a submitted application can reach the Zoom step.")
    if app.schedule_stage == stage:
        return app
    old = app.schedule_stage
    app.schedule_stage = stage
    app.schedule_enabled_at = C.now() if stage else None
    app.schedule_enabled_by = actor.id if stage else None
    # Switching this off (or to another stage) does NOT cancel a booking.
    C.audit(db, org_id, actor, "application", app.id, "schedule_stage", field="schedule_stage", old=old, new=stage)
    return app


def mark_attendance(db, org_id, sess: VoltSession, serial: int, value: str, actor):
    if value not in ATTENDANCE:
        _err("attendance", "Choose attended, no-show or invited.")
    a = (db.query(VoltSessionAttendee).filter(VoltSessionAttendee.session_id == sess.id,
                                              VoltSessionAttendee.rep_serial == int(serial)).first())
    if a is None:
        raise HTTPException(status_code=404, detail="That applicant is not assigned to this session.")
    old, a.attendance = a.attendance, value
    C.audit(db, org_id, actor, "session", sess.id, "attendance", field=C.rep_code(serial), old=old, new=value)
    return a


def staff_dict(db, sess: VoltSession) -> Dict[str, Any]:
    project = db.get(VoltProject, sess.project_id)
    att_all = db.query(VoltSessionAttendee).filter(VoltSessionAttendee.session_id == sess.id).all()
    att = [a for a in att_all if _is_active(a)]
    reps = {r.serial: r for r in db.query(VoltRep).filter(VoltRep.serial.in_([a.rep_serial for a in att])).all()} if att else {}
    return {"id": sess.id, "kind": sess.kind, "title": sess.title, "project": C.project_dict(project) if project else None,
            "starts_at": C.iso(sess.starts_at), "ends_at": C.iso(sess.starts_at + timedelta(minutes=sess.duration_minutes)),
            "timezone": sess.timezone, "starts_local": _local(sess), "duration_minutes": sess.duration_minutes,
            "join_url": sess.join_url, "link_method": sess.link_method, "instructions": sess.instructions,
            "status": sess.status, "canceled_at": C.iso(sess.canceled_at), "cancel_reason": sess.cancel_reason,
            "capacity": sess.capacity, "booked": len(att),
            "spots_left": (max(0, sess.capacity - len(att)) if sess.capacity is not None else None),
            "booking_open": bool(sess.booking_open), "booking_cutoff_minutes": sess.booking_cutoff_minutes,
            "rescheduled_at": C.iso(sess.rescheduled_at),
            "availability": _availability(sess, len(att)),
            "attendees": [dict(C.public_rep(reps.get(a.rep_serial)) or {"rep_id": a.rep_serial}, attendance=a.attendance,
                               source=a.source or "assigned", application_id=a.application_id,
                               booked_at=C.iso(a.booked_at))
                          for a in att],
            "canceled_bookings": len(att_all) - len(att)}


def _availability(sess: VoltSession, taken: int) -> str:
    if sess.status == "canceled":
        return "canceled"
    if sess.starts_at <= C.now():
        return "past"
    if sess.capacity is None:
        return "assigned_only"
    if taken >= sess.capacity:
        return "full"
    if not sess.booking_open:
        return "closed"
    return "open"


def _local(sess: VoltSession) -> str:
    try:
        return sess.starts_at.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(sess.timezone)).strftime("%Y-%m-%dT%H:%M")
    except Exception:  # pragma: no cover
        return C.iso(sess.starts_at)


def for_rep(db, rep: VoltRep) -> List[Dict[str, Any]]:
    """Only sessions this applicant is assigned to. The link only while live."""
    rows = (db.query(VoltSession, VoltSessionAttendee)
            .join(VoltSessionAttendee, VoltSessionAttendee.session_id == VoltSession.id)
            .filter(VoltSessionAttendee.rep_serial == rep.serial, VoltSession.organization_id == rep.organization_id)
            .order_by(VoltSession.starts_at.asc()).all())
    out = []
    cutoff = C.now() - timedelta(hours=12)
    for s, a in rows:
        if not _is_active(a):
            continue                                    # a booking that was canceled
        if s.starts_at + timedelta(minutes=s.duration_minutes) < cutoff and s.status != "canceled":
            continue
        project = db.get(VoltProject, s.project_id)
        live = s.status == "scheduled"
        out.append({"id": s.id, "kind": s.kind, "title": s.title, "project": project.name if project else None,
                    "starts_at": C.iso(s.starts_at), "timezone": s.timezone, "starts_local": _local(s),
                    "duration_minutes": s.duration_minutes, "instructions": s.instructions if live else None,
                    "status": s.status, "attendance": a.attendance, "application_id": a.application_id,
                    "source": a.source or "assigned", "cancel_reason": s.cancel_reason if not live else None,
                    "rescheduled_at": C.iso(s.rescheduled_at),
                    "applicant_can_cancel": bool(live and a.source == "booked" and C.now() < _cutoff(s)),
                    "join_url": s.join_url if (live and _is_active(a)) else None})
    return out
