"""ACTIVITY for EvoSys Pro Mobile: Updates and Upcoming, from records that exist.

Nothing is stored here. Every item comes from a source the platform already
keeps, and every source applies the SAME access rule as the screen that owns
it, on the server:

  workspace bookings   BookingLink -> Lead in a workspace this person may open.
                       "May open" = the organizations in
                       workspace_access.authorized_contexts(), the exact list
                       desktop Program Access is built from. Test leads never
                       show. Only the appointment's kind, time, status and
                       place are sent - no family name, phone or email.
  sales appointments   SalesAppointment where this person is a participant
                       (the rule the Back Office uses for "my appointments").
  chat mentions        TeamChatMessage naming this person, in a room this
                       person can still read (team_chat.load_room's rule).

Each source runs on its own: one failing is reported as unavailable and the
others still answer. Tapping an item opens its destination, which re-checks
access itself (the workspace, the Back Office, the chat room).

TIME: bookings keep wall-clock time in the booking's zone (booking_time
convention, America/Chicago by default) and are sent with their offset via
booking_time.wire; sales appointments and messages are UTC. "Upcoming" for a
day uses the person's own zone, the same way the rest of EvoSys does.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.models import BookingLink, Lead, Organization, User

log = logging.getLogger(__name__)

UPDATES_DAYS = 14
MAX_UPDATES = 60
DEFAULT_TZ = "America/Chicago"
ACTIVE = ("booked", "confirmed")


def user_tz(db: Session, user: User) -> str:
    for cand in (getattr(user, "booking_timezone", None), _org_tz(db, getattr(user, "organization_id", None))):
        if cand:
            try:
                ZoneInfo(cand)
                return cand
            except (ZoneInfoNotFoundError, ValueError):
                continue
    return DEFAULT_TZ


def _org_tz(db: Session, org_id: Optional[str]) -> Optional[str]:
    if not org_id:
        return None
    o = db.query(Organization).filter(Organization.id == org_id).first()
    return getattr(o, "timezone", None) if o else None


def _iso_utc(dt: Optional[datetime]) -> Optional[str]:
    return dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z") if dt else None


def _workspaces(db: Session, user: User, host_slug: Optional[str]) -> Dict[str, Dict[str, Any]]:
    """org id -> {name, program flags} for every workspace this login may open."""
    from app.services import workspace_access
    ctx = workspace_access.authorized_contexts(db, user, host_slug=host_slug)
    out = {}
    for w in ctx.get("workspace_contexts") or []:
        oid = w.get("organization_id")
        if oid:
            out[oid] = {"name": w.get("organization_name"), "is_sci": bool(w.get("program_is_sci"))}
    return out


def _booking_title(b: BookingLink, ws: Dict[str, Any]) -> str:
    return (getattr(b, "appt_label", None) or "Appointment").strip()


def _booking_json(db: Session, b: BookingLink, lead: Lead, ws: Dict[str, Any]) -> Dict[str, Any]:
    from app.services import booking_time
    minutes = getattr(b, "appt_duration", None)
    return {"organization_id": lead.organization_id, "workspace": ws.get("name"),
            "title": _booking_title(b, ws), "starts_at": booking_time.wire(db, b),
            "minutes": minutes, "status": b.status,
            "where": "Home visit" if (getattr(b, "meeting_type", None) or "").lower().startswith("home") else None}


# ── Sources ──────────────────────────────────────────────────────────────────

def _booking_rows(db: Session, org_ids: List[str]):
    if not org_ids:
        return (db.query(BookingLink, Lead).join(Lead, Lead.id == BookingLink.lead_id)
                .filter(BookingLink.id.is_(None)))
    return (db.query(BookingLink, Lead).join(Lead, Lead.id == BookingLink.lead_id)
            .filter(Lead.organization_id.in_(org_ids), Lead.is_test.isnot(True),
                    BookingLink.booked_time.isnot(None)))


def _booking_updates(db: Session, user: User, wss: Dict[str, Dict[str, Any]], since: datetime) -> List[Dict[str, Any]]:
    items = []
    rows = (_booking_rows(db, list(wss)).filter(
        (BookingLink.created_at >= since) | (BookingLink.cancelled_at >= since) | (BookingLink.rescheduled_at >= since))
        .limit(400).all())
    for b, lead in rows:
        ws = wss.get(lead.organization_id, {})
        base = _booking_json(db, b, lead, ws)
        if b.status == "cancelled" and b.cancelled_at and b.cancelled_at >= since:
            change, what, at = "cancelled", "Appointment cancelled.", b.cancelled_at
        elif b.rescheduled_at and b.rescheduled_at >= since:
            change, what, at = "updated", "Appointment updated.", b.rescheduled_at
        elif b.status in ACTIVE + ("completed",) and b.created_at and b.created_at >= since:
            change, what, at = "booked", "Appointment booked.", b.created_at
        else:
            continue
        items.append({**base, "kind": "booking", "change": change, "id": "booking:%s:%s" % (b.id, change),
                      "headline": ws.get("name") and (("SCI" if ws.get("is_sci") else ws["name"])),
                      "text": what, "at": _iso_utc(at)})
    return items


def _sales_participant_q(db: Session, user: User):
    from app.models.scheduling_models import AppointmentParticipant, SalesAppointment
    return (db.query(SalesAppointment)
            .join(AppointmentParticipant, AppointmentParticipant.appointment_id == SalesAppointment.id)
            .filter(AppointmentParticipant.user_id == user.id))


def _sales_json(a) -> Dict[str, Any]:
    minutes = None
    if getattr(a, "ends_at", None) and a.starts_at:
        minutes = int((a.ends_at - a.starts_at).total_seconds() // 60)
    return {"title": a.title or "Sales appointment", "starts_at": _iso_utc(a.starts_at),
            "minutes": minutes, "status": a.status, "where": None}


def _sales_updates(db: Session, user: User, since: datetime) -> List[Dict[str, Any]]:
    from app.models.scheduling_models import SalesAppointment
    items = []
    q = _sales_participant_q(db, user).filter(
        (SalesAppointment.created_at >= since) | (SalesAppointment.cancelled_at >= since)
        | (SalesAppointment.rescheduled_at >= since))
    for a in q.limit(200).all():
        if a.cancelled_at and a.cancelled_at >= since:
            change, what, at = "cancelled", "Appointment cancelled.", a.cancelled_at
        elif a.rescheduled_at and a.rescheduled_at >= since:
            change, what, at = "updated", "Appointment updated.", a.rescheduled_at
        else:
            change, what, at = "booked", "Appointment booked.", a.created_at
        items.append({**_sales_json(a), "kind": "sales_appointment", "change": change,
                      "id": "sales:%s:%s" % (a.id, change), "headline": "Sales Workspace",
                      "text": what, "at": _iso_utc(at)})
    return items


def _mention_updates(db: Session, user: User, access, since: datetime) -> List[Dict[str, Any]]:
    from app.models.team_app_models import TeamChatMessage, TeamChatRoom
    from app.services import team_chat
    if not access.chat:
        return []
    rows = (db.query(TeamChatMessage).filter(TeamChatMessage.created_at >= since,
                                             TeamChatMessage.mentions.isnot(None),
                                             TeamChatMessage.sender_id != user.id)
            .order_by(TeamChatMessage.created_at.desc()).limit(300).all())
    items = []
    for m in rows:
        try:
            if user.id not in json.loads(m.mentions or "[]"):
                continue
            room = team_chat.load_room(db, access, user, m.room_id)   # 404 = no longer readable
        except (HTTPException, ValueError):
            continue
        title = team_chat.room_title(db, room, user.id)["name"]
        sender = db.query(User).filter(User.id == m.sender_id).first()
        items.append({"kind": "mention", "id": "mention:%s" % m.id, "room_id": room.id,
                      "room_kind": room.kind, "headline": title,
                      "text": "%s mentioned you." % ((sender.full_name or "Someone") if sender else "Someone"),
                      "preview": (m.body or "")[:120], "at": _iso_utc(m.created_at)})
    return items


def _run(name: str, fn, sources: List[Dict[str, Any]], items: List[Dict[str, Any]]) -> None:
    try:
        items.extend(fn())
        sources.append({"key": name, "available": True})
    except Exception:  # one source failing never hides the others
        log.exception("activity source %s failed", name)
        sources.append({"key": name, "available": False})


def updates(db: Session, user: User, access, host_slug: Optional[str]) -> Dict[str, Any]:
    since = datetime.utcnow() - timedelta(days=UPDATES_DAYS)
    items: List[Dict[str, Any]] = []
    sources: List[Dict[str, Any]] = []
    wss: Dict[str, Dict[str, Any]] = {}

    def bookings():
        wss.update(_workspaces(db, user, host_slug))
        return _booking_updates(db, user, wss, since)

    _run("workspace_bookings", bookings, sources, items)
    _run("sales_appointments", lambda: _sales_updates(db, user, since), sources, items)
    _run("chat_mentions", lambda: _mention_updates(db, user, access, since), sources, items)
    items.sort(key=lambda x: x.get("at") or "", reverse=True)
    return {"items": items[:MAX_UPDATES], "sources": sources, "days": UPDATES_DAYS,
            "time_zone": user_tz(db, user), "chat": access.chat}


def _day_bounds(tz_name: str, day: date) -> Tuple[datetime, datetime, datetime, datetime]:
    """(utc_start, utc_end, local_start, local_end) - naive datetimes."""
    zi = ZoneInfo(tz_name)
    ls = datetime.combine(day, dtime(0, 0))
    le = ls + timedelta(days=1)
    us = ls.replace(tzinfo=zi).astimezone(timezone.utc).replace(tzinfo=None)
    ue = le.replace(tzinfo=zi).astimezone(timezone.utc).replace(tzinfo=None)
    return us, ue, ls, le


def upcoming(db: Session, user: User, day_s: Optional[str], host_slug: Optional[str]) -> Dict[str, Any]:
    tz = user_tz(db, user)
    try:
        day = date.fromisoformat(day_s[:10]) if day_s else datetime.now(ZoneInfo(tz)).date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Use a date like 2026-11-16.")
    us, ue, ls, le = _day_bounds(tz, day)
    items: List[Dict[str, Any]] = []
    sources: List[Dict[str, Any]] = []

    def bookings():
        from app.services import booking_time
        wss = _workspaces(db, user, host_slug)
        out = []
        # Bookings hold wall-clock time in their advisor's zone; compare with a
        # day of margin, then keep the ones whose own local date is this day.
        rows = (_booking_rows(db, list(wss))
                .filter(BookingLink.status.in_(ACTIVE),
                        BookingLink.booked_time >= ls - timedelta(days=1),
                        BookingLink.booked_time < le + timedelta(days=1))
                .order_by(BookingLink.booked_time).limit(400).all())
        for b, lead in rows:
            wire = booking_time.wire(db, b)
            try:
                local_day = datetime.fromisoformat(wire).astimezone(ZoneInfo(tz)).date()
            except (TypeError, ValueError):
                continue
            if local_day != day:
                continue
            ws = wss.get(lead.organization_id, {})
            out.append({**_booking_json(db, b, lead, ws), "kind": "booking", "id": "booking:%s" % b.id,
                        "headline": ("SCI" if ws.get("is_sci") else ws.get("name"))})
        return out

    def sales():
        from app.models.scheduling_models import APPT_SCHEDULED, SalesAppointment
        return [{**_sales_json(a), "kind": "sales_appointment", "id": "sales:%s" % a.id,
                 "headline": "Sales Workspace"}
                for a in _sales_participant_q(db, user)
                .filter(SalesAppointment.status == APPT_SCHEDULED,
                        SalesAppointment.starts_at >= us, SalesAppointment.starts_at < ue)
                .order_by(SalesAppointment.starts_at).limit(200).all()]

    _run("workspace_bookings", bookings, sources, items)
    _run("sales_appointments", sales, sources, items)
    items.sort(key=lambda x: _sort_key(x.get("starts_at")))
    return {"date": day.isoformat(), "time_zone": tz, "items": items, "sources": sources}


def _sort_key(wire: Optional[str]) -> float:
    try:
        return datetime.fromisoformat((wire or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0
