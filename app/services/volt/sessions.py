"""VOLT Zoom lane - interviews and orientations.

WHICH METHOD SHIPS: STAFF-SUPPLIED PARTICIPANT JOIN LINKS.
The repository has a Zoom Server-to-Server integration
(app/services/meeting_providers/zoom.py), but its credentials are per sales
BRAND (MeetingProviderConfig) for booked sales meetings. Nothing authorizes it
for VOLT, so VOLT does not call Zoom. Staff create the meeting in Zoom and paste
its participant join URL here. Saving a link records a schedule in VOLT; it
never creates, edits or cancels anything in Zoom, and the screens say so.

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
    have = {a.rep_serial: a for a in db.query(VoltSessionAttendee).filter(VoltSessionAttendee.session_id == sess.id).all()}
    for s, a in have.items():
        if s not in wanted:
            db.delete(a)
    for s in wanted - set(have):
        db.add(VoltSessionAttendee(session_id=sess.id, rep_serial=s))


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
    db.flush()
    if "rep_ids" in body:
        _assign(db, org_id, sess, body.get("rep_ids") or [])
    C.audit(db, org_id, actor, "session", sess.id, action, field="starts_at", old=C.iso(old_start), new=C.iso(starts))
    return sess


def cancel(db, org_id, sess: VoltSession, actor, reason: Optional[str]) -> VoltSession:
    if sess.status != "canceled":
        sess.status = "canceled"
        sess.canceled_at = C.now()
        C.audit(db, org_id, actor, "session", sess.id, "session_canceled", reason=reason)
    return sess


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
    att = db.query(VoltSessionAttendee).filter(VoltSessionAttendee.session_id == sess.id).all()
    reps = {r.serial: r for r in db.query(VoltRep).filter(VoltRep.serial.in_([a.rep_serial for a in att])).all()} if att else {}
    return {"id": sess.id, "kind": sess.kind, "title": sess.title, "project": C.project_dict(project) if project else None,
            "starts_at": C.iso(sess.starts_at), "ends_at": C.iso(sess.starts_at + timedelta(minutes=sess.duration_minutes)),
            "timezone": sess.timezone, "starts_local": _local(sess), "duration_minutes": sess.duration_minutes,
            "join_url": sess.join_url, "link_method": sess.link_method, "instructions": sess.instructions,
            "status": sess.status, "canceled_at": C.iso(sess.canceled_at),
            "attendees": [dict(C.public_rep(reps.get(a.rep_serial)) or {"rep_id": a.rep_serial}, attendance=a.attendance)
                          for a in att]}


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
        if s.starts_at + timedelta(minutes=s.duration_minutes) < cutoff and s.status != "canceled":
            continue
        project = db.get(VoltProject, s.project_id)
        live = s.status == "scheduled"
        out.append({"id": s.id, "kind": s.kind, "title": s.title, "project": project.name if project else None,
                    "starts_at": C.iso(s.starts_at), "timezone": s.timezone, "starts_local": _local(s),
                    "duration_minutes": s.duration_minutes, "instructions": s.instructions if live else None,
                    "status": s.status, "attendance": a.attendance,
                    "join_url": s.join_url if live else None})
    return out
