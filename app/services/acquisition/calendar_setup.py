"""#452 Phase 3 - each cleaning client's appointment destination, and EvoSys
booking onto it.

REAL AVAILABILITY ONLY. Times come from the platform slot engine
(calendar_router.compute_day_slots) on the client's chosen booking user's
connected Microsoft/Google calendar, then narrowed by this client's own rules
(booking windows, minimum notice). Nothing is invented; with no connected
calendar no time is offered.

NO FALSE SYNC. An appointment is booked only after the provider confirms the
calendar event. A failed write books nothing, records the attempt and the
provider's error, and can be retried - never a second event for one booking.

RESCHEDULE keeps the appointment's identity: the same booking row and calendar
event are moved (provider update), and the move is recorded.

The prospect's email is never written into the client's calendar event: EvoSys
owns that conversation.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.acquisition_models import AcquisitionOpportunity
from app.models.models import BookingLink, Lead, Organization, User
from app.services.acquisition import booking as B
from app.services.acquisition import common as C
from app.services.acquisition import engine as E

WORK_TZ = "America/Chicago"          # the slot engine's working time zone
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
CONFIRMATION = ("calendar_only", "calendar_and_email")


def _p(db: Session, org_id: str):
    p = E.get_profile(db, org_id)
    if p is None:
        raise HTTPException(409, "Run setup for this client first")
    return p


def _windows(p) -> Dict[str, List[List[str]]]:
    w = C.jload(getattr(p, "booking_windows", None), {}) or {}
    return w if isinstance(w, dict) else {}


def config(db: Session, org: Organization) -> Dict[str, Any]:
    p = E.get_profile(db, org.id)
    users = (db.query(User).filter(User.organization_id == org.id, User.is_active.isnot(False))
             .order_by(User.created_at.asc()).all())
    booker = next((u for u in users if p and u.id == p.booking_user_id), None)
    connected = B.calendar_connected(db, booker)
    return {
        "booking_user": ({"id": booker.id, "name": booker.full_name or booker.email, "email": booker.email,
                          "calendar_connected": connected} if booker else None),
        "candidates": [{"id": u.id, "name": u.full_name or u.email, "email": u.email,
                        "calendar_connected": B.calendar_connected(db, u),
                        "signed_in": u.last_login_at is not None} for u in users],
        "timezone": (p.timezone if p else None) or WORK_TZ,
        "slot_engine_timezone": WORK_TZ,
        "appointment_minutes": (p.appointment_minutes if p else None) or C.DEFAULT_APPOINTMENT_MINUTES,
        "min_notice_hours": getattr(p, "min_notice_hours", None),
        "buffer_minutes": getattr(p, "buffer_minutes", None),
        "booking_windows": _windows(p) if p else {},
        "meeting_location": getattr(p, "meeting_location", None),
        "booking_contact": getattr(p, "booking_contact", None),
        "reschedule_rules": getattr(p, "reschedule_rules", None),
        "confirmation_pref": getattr(p, "confirmation_pref", None) or "calendar_only",
        "ready": bool(booker and connected),
        "status": ("ready" if booker and connected else "calendar_not_connected" if booker else "no_booking_user"),
        "status_label": ("Ready - EvoSys can book real times" if booker and connected else
                         "%s hasn't connected a Microsoft or Google calendar yet" % (booker.full_name or booker.email)
                         if booker else "Choose which of the client's logins receives appointments"),
    }


def _hhmm(v: str) -> str:
    try:
        return datetime.strptime(str(v).strip(), "%H:%M").strftime("%H:%M")
    except ValueError:
        raise HTTPException(422, "Times must look like 09:00")


def update(db: Session, org: Organization, actor: User, data: Dict[str, Any]) -> Dict[str, Any]:
    p = _p(db, org.id)
    if "booking_user_id" in data:
        uid = data.get("booking_user_id") or None
        if uid:
            u = db.query(User).filter(User.id == uid, User.organization_id == org.id,
                                      User.is_active.isnot(False)).first()
            if u is None:
                raise HTTPException(422, "Appointments can only go to one of this client's own logins")
        p.booking_user_id = uid
    if "timezone" in data and data["timezone"]:
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(str(data["timezone"]))
        except Exception:  # noqa: BLE001
            raise HTTPException(422, "Unknown time zone")
        p.timezone = str(data["timezone"])
    if data.get("appointment_minutes") not in (None, ""):
        m = int(data["appointment_minutes"])
        if not 15 <= m <= 240:
            raise HTTPException(422, "Meeting length must be 15-240 minutes")
        p.appointment_minutes = m
    for key, lo, hi in (("min_notice_hours", 0, 336), ("buffer_minutes", 0, 120)):
        if key in data:
            v = data.get(key)
            if v in (None, ""):
                setattr(p, key, None)
            else:
                v = int(v)
                if not lo <= v <= hi:
                    raise HTTPException(422, "%s must be between %d and %d" % (key.replace("_", " "), lo, hi))
                setattr(p, key, v)
    if "booking_windows" in data:
        w = data.get("booking_windows") or {}
        if not isinstance(w, dict) or any(k not in DAYS for k in w):
            raise HTTPException(422, "Booking windows are per weekday (mon..sun)")
        clean = {}
        for d, ranges in w.items():
            out = []
            for r in ranges or []:
                a, b = _hhmm(r[0]), _hhmm(r[1])
                if a >= b:
                    raise HTTPException(422, "Each window must end after it starts")
                out.append([a, b])
            if out:
                clean[d] = out
        p.booking_windows = C.jdump(clean) if clean else None
    for key, n in (("meeting_location", 500), ("booking_contact", 200), ("reschedule_rules", 1000)):
        if key in data:
            setattr(p, key, (str(data.get(key) or "").strip()[:n]) or None)
    if "confirmation_pref" in data:
        if data["confirmation_pref"] not in CONFIRMATION:
            raise HTTPException(422, "Unknown confirmation preference")
        p.confirmation_pref = data["confirmation_pref"]
    E._event(db, org.id, None, "calendar_setup", actor.id, note="Booking settings updated")
    db.commit()
    return config(db, org)


# ── availability ───────────────────────────────────────────────────────────
def filter_slots(p, slots: List[str], now: Optional[datetime] = None) -> List[str]:
    """Narrow the slot engine's real free times by the client's own rules."""
    from zoneinfo import ZoneInfo
    now_local = (now or datetime.utcnow()).replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(WORK_TZ)).replace(tzinfo=None)
    notice = getattr(p, "min_notice_hours", None)
    windows = _windows(p)
    out = []
    for s in slots:
        t = datetime.fromisoformat(s[:19])
        if notice and t < now_local + timedelta(hours=notice):
            continue
        if windows:
            day = DAYS[t.weekday()]
            hm = t.strftime("%H:%M")
            if not any(a <= hm < b for a, b in windows.get(day, [])):
                continue
        out.append(s)
    return out


def available(db: Session, org: Organization, opp: AcquisitionOpportunity, actor: User, day: str) -> Dict[str, Any]:
    from datetime import date as date_cls
    booker = B.booker_for(db, org, opp, actor)
    try:
        target = date_cls.fromisoformat(day)
    except ValueError:
        raise HTTPException(400, "Use a YYYY-MM-DD date")
    from app.routers.calendar_router import compute_day_slots
    raw = compute_day_slots(db, booker, target, program_visit=True, minutes=60)
    p = _p(db, org.id)
    res = {"date": day, "slots": filter_slots(p, raw.get("slots", [])), "timezone": WORK_TZ,
           "calendar_of": booker.full_name or booker.email}
    if raw.get("reason"):
        res["reason"] = raw["reason"]
    return res


# ── booking by EvoSys ──────────────────────────────────────────────────────
def _write(db: Session, booker: User, org: Organization, link: BookingLink, lead: Lead, opp, start: datetime,
           minutes: int, note: str) -> Dict[str, Any]:
    from app.services import tenant_scheduling as TS
    f = C.jload(lead.custom_fields, {}) or {}
    company = f.get("company") or "Prospect"
    # Phone only - the prospect's email stays with EvoSys.
    lead_view = Lead(first_name=company, last_name="", phone=lead.phone, email=None)
    p = E.get_profile(db, org.id)
    where = (getattr(p, "meeting_location", None) if p else None)
    body = "Booked by EvoSys Pro. %s%s" % (note or "", (" Location: %s" % where) if where else "")
    return TS._push_to_calendar(db, booker, org, link, lead_view, B.LABEL, start, minutes, WORK_TZ, body)


def book(db: Session, org: Organization, opp: AcquisitionOpportunity, actor: User, data: Dict[str, Any]) -> Dict[str, Any]:
    """EvoSys books an appointment it agreed with the prospect onto the client's calendar."""
    if not data.get("confirmed_with_prospect"):
        raise HTTPException(422, "Confirm the prospect agreed to this time")
    if opp.stage != C.INTERESTED:
        raise HTTPException(409, "Only an interested prospect can be booked" if opp.stage != C.BOOKED
                            else "This appointment is already booked - reschedule it instead")
    lead = db.query(Lead).filter(Lead.id == opp.lead_id, Lead.organization_id == org.id).first()
    if lead is None or lead.status == "dnc":
        raise HTTPException(409, "This prospect can't be booked")
    try:
        start = datetime.fromisoformat(str(data.get("slot"))[:19])
    except (TypeError, ValueError):
        raise HTTPException(400, "Pick a time from the list")
    free = available(db, org, opp, actor, start.date().isoformat())
    if start.strftime("%Y-%m-%dT%H:%M:00") not in free["slots"]:
        raise HTTPException(409, "That time isn't free on the client's calendar any more. Pick another.")
    booker = B.booker_for(db, org, opp, actor)
    p = _p(db, org.id)
    minutes = int(p.appointment_minutes or C.DEFAULT_APPOINTMENT_MINUTES)
    # One booking row per attempt-chain: reuse an unsynced row from a failed try.
    link = (db.query(BookingLink).filter(BookingLink.lead_id == lead.id, BookingLink.status == "sync_failed")
            .order_by(BookingLink.created_at.desc()).first())
    if link is None:
        link = BookingLink(lead_id=lead.id, user_id=booker.id, status="pending",
                           token="aq" + secrets.token_urlsafe(18), appt_label=B.LABEL, appt_duration=minutes,
                           expires_at=datetime.utcnow() + timedelta(days=B.LINK_DAYS),
                           confirmation_sent=True, reminder_24hr_sent=True, reminder_1hr_sent=True)
        db.add(link)
        db.flush()
    link.user_id, link.appt_duration = booker.id, minutes
    sync = _write(db, booker, org, link, lead, opp, start, minutes, (data.get("note") or "").strip()[:300])
    if not sync.get("ok"):
        link.status = "sync_failed"
        E._event(db, org.id, opp.id, "calendar_failed", actor.id,
                 note="Calendar write failed (%s) - nothing booked; retry" % str(sync.get("error"))[:120])
        db.commit()
        raise HTTPException(502, "The client's calendar didn't accept the event (%s). Nothing was booked - "
                                 "retry or pick another time." % str(sync.get("error"))[:120])
    link.status = "booked"
    link.booked_time = start
    link.review_request_sent_at = datetime.utcnow()
    opp.booking_link_id = link.id
    E._move(db, opp, C.BOOKED, actor.id, "Booked by EvoSys for %s" % start.strftime("%a %b %d, %I:%M %p"))
    # Any open self-booking links for this prospect are withdrawn - one appointment only.
    for other in db.query(BookingLink).filter(BookingLink.lead_id == lead.id, BookingLink.status == "pending",
                                              BookingLink.id != link.id).all():
        other.status = "cancelled"
    db.commit()
    return {"status": "booked", "booked_time": start.strftime("%Y-%m-%dT%H:%M:00"), "minutes": minutes,
            "calendar_synced": True, "calendar_event_id": bool(link.calendar_event_id)}


def reschedule(db: Session, org: Organization, opp: AcquisitionOpportunity, actor: User, data: Dict[str, Any]) -> Dict[str, Any]:
    if opp.stage != C.BOOKED or not opp.booking_link_id:
        raise HTTPException(409, "Only an upcoming booked appointment can be rescheduled")
    link = db.query(BookingLink).filter(BookingLink.id == opp.booking_link_id).first()
    if link is None or not link.calendar_event_id:
        raise HTTPException(409, "This appointment has no calendar event to move")
    try:
        start = datetime.fromisoformat(str(data.get("slot"))[:19])
    except (TypeError, ValueError):
        raise HTTPException(400, "Pick a time from the list")
    free = available(db, org, opp, actor, start.date().isoformat())
    if start.strftime("%Y-%m-%dT%H:%M:00") not in free["slots"]:
        raise HTTPException(409, "That time isn't free on the client's calendar")
    booker = db.query(User).filter(User.id == link.user_id).first()
    lead = db.query(Lead).filter(Lead.id == opp.lead_id).first()
    from app.services import calendar_providers as reg
    from app.services.calendar_providers.base import EventPayload
    from app.services.tenant_scheduling import _to_utc
    minutes = int(link.appt_duration or C.DEFAULT_APPOINTMENT_MINUTES)
    f = C.jload(lead.custom_fields, {}) or {}
    st = _to_utc(start, WORK_TZ)
    payload = EventPayload(subject="%s — %s" % (B.LABEL, f.get("company") or "Prospect"),
                           body_text="Rescheduled by EvoSys Pro. Phone: %s" % (lead.phone or "n/a"),
                           starts_at=st, ends_at=st + timedelta(minutes=minutes), timezone=WORK_TZ,
                           location=org.org_address or None, attendees=[])
    try:
        result = reg.get_provider(db, booker, org).update_event(link.calendar_event_id, payload)
        ok, err = bool(getattr(result, "ok", False)), getattr(result, "error_code", None)
    except Exception as e:  # noqa: BLE001
        ok, err = False, str(e)[:120]
    if not ok:
        E._event(db, org.id, opp.id, "calendar_failed", actor.id, note="Reschedule not accepted by the calendar (%s)" % err)
        db.commit()
        raise HTTPException(502, "The calendar didn't accept the new time (%s). The appointment is unchanged." % err)
    old = link.booked_time
    link.booked_time = start
    E._event(db, org.id, opp.id, "rescheduled", actor.id,
             note="Moved from %s to %s%s" % (old.strftime("%a %b %d %I:%M %p") if old else "?",
                                             start.strftime("%a %b %d %I:%M %p"),
                                             (" - " + data["note"].strip()[:200]) if data.get("note") else ""))
    db.commit()
    return {"status": "rescheduled", "booked_time": start.strftime("%Y-%m-%dT%H:%M:00")}
