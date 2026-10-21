"""Inspection scheduling on REAL capacity, through the platform's own engine.

Openings come from `tenant_scheduling.availability` - the salesperson's own
working days and hours, buffer, daily cap, availability blocks, existing
bookings and their connected calendar (read fail-closed: if the calendar
cannot be read, there are NO openings, never a guess).

Booking re-asks that same engine for the chosen day at the moment of writing
and refuses unless the exact start time is still open, so a slot that was
taken while someone was looking cannot be double-booked. The inspection is a
BookingLink (the platform's appointment record), pushed to the salesperson's
connected calendar when there is one, and the team gets ONE in-app alert per
person per booking.

NOTHING HERE TEXTS THE HOMEOWNER unless the caller explicitly asks for the
existing confirmation flow (send_confirmation=True), which applies every
existing send gate.
"""
import logging
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.models import BookingLink, Lead, Notification, NotificationType, User
from app.models.roofing_models import RoofingOpportunity, RoofingProfile
from app.services import tenant_scheduling as TS
from app.services.roofing import common as C
from app.services.roofing import journey as J

log = logging.getLogger(__name__)


def salesperson_for(db: Session, ctx: C.Ctx, lead: Lead, profile: Optional[RoofingProfile],
                    requested: Optional[str] = None) -> User:
    for uid in (requested, lead.assigned_to_id, profile.default_salesperson_id if profile else None):
        if not uid:
            continue
        u = db.query(User).filter(User.id == uid, User.organization_id == ctx.org_id,
                                  User.is_active == True).first()  # noqa: E712
        if u:
            return u
        if uid == requested:
            raise HTTPException(404, "Salesperson not found in this company")
    if ctx.user.organization_id == ctx.org_id:
        return ctx.user
    raise HTTPException(409, "Assign this homeowner to a salesperson before scheduling.")


def duration_for(profile: Optional[RoofingProfile]) -> int:
    d = int(getattr(profile, "inspection_duration_minutes", None) or C.DEFAULT_DURATION)
    return max(15, min(d, 480))


def openings(db: Session, ctx: C.Ctx, lead: Lead, profile: Optional[RoofingProfile],
             date_from: Optional[str], days: int, inspection_type: Optional[str],
             salesperson_id: Optional[str] = None, now: Optional[datetime] = None) -> Dict:
    sp = salesperson_for(db, ctx, lead, profile, salesperson_id)
    try:
        d0 = date.fromisoformat(date_from) if date_from else datetime.utcnow().date()
    except ValueError:
        raise HTTPException(400, "date_from must be YYYY-MM-DD")
    days = max(1, min(int(days or 7), TS.MAX_RANGE_DAYS))
    out = TS.availability(db, None, sp, ctx.org, d0, d0 + timedelta(days=days - 1),
                          duration_for(profile), None, inspection_type, now_utc=now)
    out["salesperson_id"] = sp.id
    out["salesperson_name"] = sp.full_name or sp.email
    return out


def _parse_utc(raw: str) -> datetime:
    try:
        s = raw.strip().replace("Z", "")
        dt = datetime.fromisoformat(s)
    except (AttributeError, ValueError):
        raise HTTPException(400, "starts_at must be an ISO time like 2026-10-22T15:00:00Z")
    if dt.tzinfo is not None:
        from datetime import timezone
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.replace(second=0, microsecond=0)


def notify_team(db: Session, ctx: C.Ctx, lead: Lead, profile: Optional[RoofingProfile],
                salesperson: User, booking: BookingLink, label: str, when_label: str) -> List[str]:
    """ONE actionable alert per person per booking (deduplicated on the link)."""
    link = "/program/roofing/homeowners/%s?" % lead.id        # opens the homeowner record
    ids = [salesperson.id] + [u for u in C.jload(getattr(profile, "notify_user_ids", None), []) if isinstance(u, str)]
    valid = {u.id for u in db.query(User).filter(User.organization_id == ctx.org_id,
                                                 User.id.in_(ids)).all()}
    sent = []
    msg = "Inspection booked: %s - %s%s. %s with %s." % (
        C.lead_name(lead), when_label, (" at " + C.address(lead)) if C.address(lead) else "",
        label, salesperson.full_name or salesperson.email)
    for uid in dict.fromkeys(ids):
        if uid not in valid:
            continue
        dup = (db.query(Notification).filter(Notification.user_id == uid, Notification.lead_id == lead.id,
                                             Notification.type == NotificationType.BOOKING_CONFIRMED,
                                             Notification.link == link + "booking=" + booking.id).first())
        if dup:
            continue
        db.add(Notification(user_id=uid, lead_id=lead.id, type=NotificationType.BOOKING_CONFIRMED,
                            message=msg[:900], link=link + "booking=" + booking.id))
        sent.append(uid)
    return sent


def book(db: Session, ctx: C.Ctx, lead: Lead, profile: Optional[RoofingProfile], starts_at: str,
         inspection_type: Optional[str], salesperson_id: Optional[str], notes: Optional[str],
         send_confirmation: bool = False, now: Optional[datetime] = None) -> Dict:
    if lead.status == "dnc":
        raise HTTPException(409, "This homeowner opted out. Do not schedule outreach-driven appointments.")
    sp = salesperson_for(db, ctx, lead, profile, salesperson_id)
    start_utc = _parse_utc(starts_at)
    st = TS.Settings(sp)
    day_local = TS._to_local(start_utc, st.timezone).date()
    duration = duration_for(profile)
    label = TS.resolve_appointment_type(ctx.org, inspection_type)
    fresh = TS.availability(db, None, sp, ctx.org, day_local, day_local, duration, None, label, now_utc=now)
    if fresh.get("availability_status") == TS.STATUS_CALENDAR_UNAVAILABLE:
        raise HTTPException(503, fresh.get("reason") or "The calendar can't be read right now.")
    wanted = start_utc.replace(microsecond=0).isoformat() + "Z"
    slot = next((s for s in fresh.get("slots", []) if s["starts_at"] == wanted), None)
    if slot is None:
        raise HTTPException(409, "That time is no longer open. Pick another opening.")

    from app.services.sms_service import _encode_booking_token
    start_local = TS._to_local(start_utc, st.timezone)
    booking = BookingLink(lead_id=lead.id, user_id=sp.id, status="booked",
                          token=_encode_booking_token(lead, sp), booked_time=start_local,
                          appt_label=label, appt_duration=duration)
    db.add(booking)
    lead.status = "booked"
    if not lead.assigned_to_id:
        lead.assigned_to_id = sp.id
    db.flush()
    opp = J.ensure(db, ctx.org_id, [lead])[lead.id]
    opp.booking_link_id = booking.id
    opp.inspection_type = label
    if opp.stage not in C.MANUAL_STAGES or opp.stage == C.LOST:
        J._record(db, opp, C.BOOKED, ctx.user.id, "Inspection booked for %s" % slot["label"],
                  now or datetime.utcnow())
    try:
        from app.models.models import CadenceStatus
        from app.services.cadence_service import stop_cadence_for_lead
        stop_cadence_for_lead(db, lead.id, CadenceStatus.STOPPED_BOOKED)
    except Exception:  # noqa: BLE001 - booking stands even if the cadence row is odd
        log.exception("roofing: could not stop cadence for %s", lead.id)
    if notes:
        from app.models.work_models import LeadNote
        db.add(LeadNote(organization_id=ctx.org_id, lead_id=lead.id, author_user_id=ctx.user.id,
                        body="Inspection notes: " + notes.strip()[:2000]))
    alerted = notify_team(db, ctx, lead, profile, sp, booking, label, slot["label"])
    db.commit()
    db.refresh(booking)

    calendar = {"ok": False}
    try:
        calendar = TS._push_to_calendar(db, sp, ctx.org, booking, lead, label, start_local,
                                        duration, st.timezone, notes) or {"ok": False}
    except Exception as e:  # noqa: BLE001 - recorded, never raised: the booking is real
        log.exception("roofing: calendar push failed for %s", booking.id)
        calendar = {"ok": False, "error": str(e)[:200]}
    confirmation = {"requested": bool(send_confirmation), "sent": False}
    if send_confirmation:
        try:
            from app.services.appointment_flow_service import on_booking_confirmed
            on_booking_confirmed(db, lead, sp, booking)
            confirmation["sent"] = True
        except Exception as e:  # noqa: BLE001
            confirmation["error"] = str(e)[:200]
    db.commit()
    return {"booking_id": booking.id, "lead_id": lead.id, "starts_at": wanted,
            "label": slot["label"], "inspection_type": label, "duration_minutes": duration,
            "salesperson_id": sp.id, "salesperson_name": sp.full_name or sp.email,
            "calendar_synced": bool(calendar.get("ok")), "calendar_detail": calendar.get("error") or calendar.get("reason"),
            "team_alerted": len(alerted), "confirmation": confirmation, "stage": opp.stage}


def offer_text(lead: Lead, salesperson: User, slots: List[dict], company: str) -> Optional[str]:
    """An inspection offer built ONLY from openings the engine returned."""
    if not slots:
        return None
    picks = slots[:3]
    times = [s["label"] for s in picks]
    joined = times[0] if len(times) == 1 else ", ".join(times[:-1]) + " or " + times[-1]
    first = (lead.first_name or "there").strip()
    who = (salesperson.full_name or "").split(" ")[0] or "our inspector"
    return ("Great, %s! I can have %s from %s come take a look at your roof. I have %s open - "
            "which works best for you?" % (first, who, company, joined))
