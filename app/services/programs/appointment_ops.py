"""Staff actions on a program appointment (Issue #507, Restland).

  attendance(completed | no_show)  - drives the survey (completed only)
  cancel                           - calendar event removed, pending messages
                                     cancelled, one acknowledgement per channel
  reschedule(new slot)             - re-checked against the rules, calendar
                                     moved, old messages superseded, one
                                     "updated" notice per channel
  calendar_reassigned              - after "Assign to": the event moves to the
                                     new person's calendar; the family is NOT
                                     messaged (who attends is an internal
                                     decision; their next message names the
                                     new advisor)

Every action is scoped to the program, audited, and sends only through
appointment_notifications (off until switched on).
"""
from datetime import datetime, timedelta
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.services.programs import appointment_notifications as AN
from app.services.programs import booking_policy as BP


class OpError(Exception):
    pass


def _audit(db, prog, actor, action, booking, before, after):
    from app.routers.audit_log_router import log_action
    log_action(db, prog.organization_id, getattr(actor, "id", None), action=action, target_type="booking_link",
               target_id=booking.id, before=before, after=after, commit=False)


def attendance(db: Session, prog, booking, status: str, actor, *, ended_at: Optional[datetime] = None,
               now: Optional[datetime] = None) -> Dict:
    now = now or datetime.utcnow()
    if status not in ("completed", "no_show"):
        raise OpError("Choose completed or no-show.")
    if (booking.status or "") not in ("booked", "confirmed", "completed", "no_show"):
        raise OpError("This appointment is %s." % booking.status)
    start = AN._start_utc(booking)
    if start > now:
        raise OpError("The visit hasn't happened yet.")
    before = {"status": booking.status,
              "completed_at": booking.completed_at.isoformat() if booking.completed_at else None}
    booking.status = status
    booking.attendance_recorded_at, booking.attendance_recorded_by = now, getattr(actor, "id", None)
    if status == "completed":
        end = ended_at or (start + timedelta(minutes=int(booking.appt_duration or 45)))
        booking.completed_at = min(end, now)
    else:
        booking.completed_at = None
    # Any reminder still pending can never go now.
    from app.models.reset_models import AppointmentNotification as _AN
    for r in (db.query(_AN).filter(_AN.booking_id == booking.id, _AN.status.in_(AN.OPEN),
                                   _AN.purpose != "survey").all()):
        r.status, r.reason = "skipped", "visit already happened"
    survey = {}
    if status == "completed":
        survey = AN.schedule_survey(db, booking, now=now)
    else:
        AN._cancel_survey(db, booking)
    _audit(db, prog, actor, "program.appointment_attendance", booking, before,
           {"status": status, "completed_at": booking.completed_at.isoformat() if booking.completed_at else None})
    db.commit()
    return {"ok": True, "status": status, "survey": survey}


def _move_calendar(db, booking, old_user_id: Optional[str] = None) -> Dict:
    """Delete the old event (from its owner's calendar) and create one at the
    booking's current time on the current owner's calendar. Best-effort and
    reported; never blocks the change itself."""
    from app.models.models import User
    out = {"deleted": None, "created": None}
    owner = db.query(User).filter(User.id == (old_user_id or booking.user_id)).first()
    if booking.calendar_event_id and owner is not None and getattr(owner, "google_calendar_connected", False):
        try:
            from app.services.calendar_service import _get_calendar_service, booking_calendar_id
            _get_calendar_service(owner).events().delete(
                calendarId=booking_calendar_id(owner),
                eventId=booking.calendar_event_id).execute()
            out["deleted"] = True
        except Exception as e:                           # noqa: BLE001
            out["deleted"] = "failed: %s" % str(e)[:120]
        booking.calendar_event_id = None
    me = db.query(User).filter(User.id == booking.user_id).first()
    if me is not None and getattr(me, "google_calendar_connected", False) and (booking.status or "") in AN.LIVE:
        try:
            from app.services.calendar_service import create_calendar_event_for_booking
            bt = booking.booked_time.replace(tzinfo=None) if getattr(booking.booked_time, "tzinfo", None) \
                else booking.booked_time
            r = create_calendar_event_for_booking(db, booking, bt, int(booking.appt_duration or 45))
            out["created"] = bool(r.get("success")) or r.get("error")
        except Exception as e:                           # noqa: BLE001
            out["created"] = "failed: %s" % str(e)[:120]
    return out


def cancel(db: Session, prog, booking, actor, *, acknowledge: bool = True, now: Optional[datetime] = None) -> Dict:
    now = now or datetime.utcnow()
    if (booking.status or "") not in AN.LIVE:
        raise OpError("Only a booked appointment can be cancelled.")
    before = {"status": booking.status}
    cal = _move_calendar_cancel(db, booking)
    booking.status, booking.cancelled_at = "cancelled", now
    from app.models.models import Lead
    lead = db.query(Lead).filter(Lead.id == booking.lead_id).first()
    if lead is not None and (lead.status or "") == "booked":
        lead.status = "new"           # free to be helped again; history keeps the cancellation
    res = AN.on_cancelled(db, booking, now=now, acknowledge=acknowledge)
    _audit(db, prog, actor, "program.appointment_cancel", booking, before, {"status": "cancelled"})
    db.commit()
    AN.run(db, prog.organization_id, now=now, booking_id=booking.id)
    return {"ok": True, "calendar": cal, "messages": res}


def _move_calendar_cancel(db, booking) -> Optional[str]:
    from app.models.models import User
    owner = db.query(User).filter(User.id == booking.user_id).first()
    if not booking.calendar_event_id or owner is None or not getattr(owner, "google_calendar_connected", False):
        return None
    try:
        from app.services.calendar_service import _get_calendar_service, booking_calendar_id
        _get_calendar_service(owner).events().delete(
            calendarId=booking_calendar_id(owner),
            eventId=booking.calendar_event_id).execute()
        booking.calendar_event_id = None
        return "deleted"
    except Exception as e:                               # noqa: BLE001
        return "failed: %s" % str(e)[:120]


def reschedule(db: Session, prog, booking, slot: datetime, actor, *, busy_reader=None,
               now: Optional[datetime] = None) -> Dict:
    from app.models.models import User
    now = now or datetime.utcnow()
    policy = BP.for_program(prog)
    if policy is None:
        raise OpError("This program has no appointment rules.")
    if (booking.status or "") not in AN.LIVE:
        raise OpError("Only a booked appointment can be moved.")
    owner = db.query(User).filter(User.id == booking.user_id).first()
    try:
        db.query(User).filter(User.id == booking.user_id).with_for_update().first()
    except Exception:                                    # noqa: BLE001
        pass
    if not BP.is_open(db, owner, slot, policy, exclude_booking_id=booking.id, busy_reader=busy_reader):
        raise OpError("That time isn't open.")
    before = {"booked_time": booking.booked_time.isoformat() if booking.booked_time else None}
    booking.booked_time, booking.rescheduled_at = slot, now
    booking.appt_duration = policy["duration"]
    cal = _move_calendar(db, booking)
    booking.status = "booked"
    res = AN.on_booked(db, booking, event="rescheduled", now=now)
    _audit(db, prog, actor, "program.appointment_reschedule", booking, before, {"booked_time": slot.isoformat()})
    db.commit()
    AN.run(db, prog.organization_id, now=now, booking_id=booking.id)
    return {"ok": True, "calendar": cal, "messages": res}


def calendar_reassigned(db: Session, booking, old_user_id: str) -> Dict:
    """After Assign to: move the event; the family is not messaged."""
    out = _move_calendar(db, booking, old_user_id=old_user_id)
    db.commit()
    return out


def pending_attendance(db: Session, organization_id: str, now: Optional[datetime] = None):
    """Visits that have ended with no attendance recorded - staff work, never
    an assumed visit."""
    from app.models.models import BookingLink, Lead
    now = now or datetime.utcnow()
    local_now = AN._local(now)
    rows = (db.query(BookingLink).join(Lead, Lead.id == BookingLink.lead_id)
            .filter(Lead.organization_id == organization_id, BookingLink.status.in_(AN.LIVE),
                    BookingLink.booked_time.isnot(None), BookingLink.booked_time < local_now,
                    Lead.is_test.isnot(True)).order_by(BookingLink.booked_time.desc()).limit(200).all())
    return [b for b in rows if b.booked_time + timedelta(minutes=int(b.appt_duration or 45)) <= local_now]
