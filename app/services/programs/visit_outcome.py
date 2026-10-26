"""What happened at a program family's visit (#405) - recorded by SCI staff on
the Appointments screen, and the ONLY thing that makes a visit eligible for a
satisfaction survey (app/services/survey_dispatch.py).

  completed          the family came; after the visit ENDS + the buffer, and
                     with survey sending switched on, ONE survey may go
  no_show            never surveyed
  cancelled          never surveyed
  follow_up_needed   never surveyed (a person is still working it)

Stored on LeadOutcome (the existing outcome system), one row per visit,
linked to the family, the booking, the program location, the staff member
who set it and when. The booking itself (BookingLink.status, its time, its
calendar event) is never changed here, and neither is the family's pipeline
status.

RULES
  * the booking must belong to a family of THIS program's organization;
  * an outcome can be set only once the visit has STARTED, and "completed"
    only once it has ENDED - nobody can mark a visit before it happens;
  * after a survey went out for the visit, the outcome is locked (changing it
    could not take the survey back).
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, Iterable, Optional

from sqlalchemy.orm import Session

VALUES = ("completed", "no_show", "cancelled", "follow_up_needed")
LABELS = {"completed": "Completed", "no_show": "No-show", "cancelled": "Cancelled",
          "follow_up_needed": "Follow-up needed"}


class OutcomeRefused(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def _times(db: Session, booking):
    from datetime import timedelta
    from app.services import booking_time as bkt
    from app.services.survey_dispatch import DEFAULT_MINUTES
    if booking.booked_time is None:
        return None, None
    start = bkt.to_utc(booking.booked_time, bkt.zone_of_user(db, booking.user_id))
    return start, start + timedelta(minutes=int(booking.appt_duration or DEFAULT_MINUTES))


def _latest(db: Session, booking_id: str):
    """The row holding this visit's attendance - the same rule the survey
    sender uses (survey_dispatch.latest_attendance_row)."""
    from app.services.survey_dispatch import latest_attendance_row
    return latest_attendance_row(db, booking_id)


def _survey_followup(db: Session, booking_id: str):
    from app.models.models import BookingFollowup
    return (db.query(BookingFollowup).filter(BookingFollowup.booking_link_id == booking_id)
            .order_by(BookingFollowup.created_at.desc()).first())


def survey_note(db: Session, booking, attendance: Optional[str], now: Optional[datetime] = None) -> Optional[str]:
    """One plain line for staff about this visit's survey."""
    now = now or datetime.utcnow()
    _, end = _times(db, booking)
    return _note(_survey_followup(db, booking.id), booking, attendance, end, now)


def record(db: Session, prog, booking_id: str, attendance: str, user, *,
           expected_location_id: Optional[str] = None, now: Optional[datetime] = None) -> Dict:
    """Set what happened at one visit. Raises OutcomeRefused with a plain reason."""
    from app.models.models import BookingLink, Lead, LeadOutcome
    from app.services.programs.identity import source_record_for_lead
    now = now or datetime.utcnow()
    if attendance not in VALUES:
        raise OutcomeRefused("Choose Completed, No-show, Cancelled or Follow-up needed.")
    hit = (db.query(BookingLink, Lead).join(Lead, Lead.id == BookingLink.lead_id)
           .filter(BookingLink.id == booking_id, Lead.organization_id == prog.organization_id).first())
    if hit is None:
        raise OutcomeRefused("Appointment not found.", 404)        # other org: indistinguishable from missing
    booking, lead = hit
    # One writer per visit at a time, and the survey claim (which sets
    # review_request_sent_at on this same row) serialises with it.
    try:
        db.query(BookingLink).filter(BookingLink.id == booking.id).with_for_update().first()
    except Exception:                                    # noqa: BLE001 - SQLite: writers are serialised anyway
        pass
    db.refresh(booking)
    rec = source_record_for_lead(db, lead)
    location_id = getattr(rec, "location_id", None)
    if expected_location_id and expected_location_id != location_id:
        raise OutcomeRefused("This appointment belongs to a different location.", 409)
    if (booking.status or "") not in ("booked", "confirmed", "completed"):
        raise OutcomeRefused("This appointment is %s - there is no visit to record." % (booking.status or "not booked"))
    start, end = _times(db, booking)
    if start is None:
        raise OutcomeRefused("This appointment has no time.")
    if start > now:
        raise OutcomeRefused("This visit hasn't happened yet.")
    if attendance == "completed" and end > now:
        raise OutcomeRefused("You can mark it Completed once the visit is over.")
    fu = _survey_followup(db, booking.id)
    row = _latest(db, booking.id)
    if row is not None and row.attendance == attendance:
        db.rollback()                                                  # release the row lock
        return _out(db, booking, row, now, unchanged=True)            # a double click changes nothing
    if booking.review_request_sent_at is not None or (fu is not None and (fu.state is not None or fu.survey_link_sent)):
        db.rollback()
        raise OutcomeRefused("A survey already went out for this visit, so its outcome is locked.", 409)
    if row is None:
        row = LeadOutcome(lead_id=lead.id, recorded_by_id=user.id, booking_link_id=booking.id,
                          appointment_date=booking.booked_time)
        db.add(row)
    row.attendance = attendance
    row.location_id = location_id
    row.attendance_set_by = user.id
    row.attendance_set_at = now
    db.commit()
    try:
        from app.routers.audit_log_router import log_action
        log_action(db, prog.organization_id, user.id, "program.visit_outcome", "booking_link", booking.id,
                   details={"lead_id": lead.id, "attendance": attendance, "location_id": location_id})
    except Exception:                                    # noqa: BLE001 - the outcome is saved either way
        db.rollback()
    return _out(db, booking, row, now)


def _out(db: Session, booking, row, now, unchanged: bool = False) -> Dict:
    return {"booking_id": booking.id, "attendance": row.attendance,
            "label": LABELS.get(row.attendance or "", None), "unchanged": unchanged,
            "set_at": (row.attendance_set_at or row.updated_at or row.created_at).isoformat() + "Z"
            if (row.attendance_set_at or row.updated_at or row.created_at) else None,
            "survey_note": survey_note(db, booking, row.attendance, now)}


def for_bookings(db: Session, bookings: Iterable, now: Optional[datetime] = None) -> Dict[str, Dict]:
    """{booking_id: {attendance, label, started, ended, can_record, survey_note}} for a list.
    Two queries for the whole list, not three per row."""
    from sqlalchemy import func as _f
    from app.models.models import BookingFollowup, LeadOutcome
    now = now or datetime.utcnow()
    bookings = list(bookings)
    ids = [b.id for b in bookings]
    att_of, fu_of = {}, {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for r in (db.query(LeadOutcome).filter(LeadOutcome.booking_link_id.in_(chunk), LeadOutcome.attendance.isnot(None))
                  .order_by(_f.coalesce(LeadOutcome.attendance_set_at, LeadOutcome.updated_at,
                                        LeadOutcome.created_at).desc(), LeadOutcome.id.desc()).all()):
            att_of.setdefault(r.booking_link_id, r.attendance)      # first = newest
        for f in (db.query(BookingFollowup).filter(BookingFollowup.booking_link_id.in_(chunk))
                  .order_by(BookingFollowup.created_at.desc()).all()):
            fu_of.setdefault(f.booking_link_id, f)
    out = {}
    for b in bookings:
        att = att_of.get(b.id)
        start, end = _times(db, b)
        fu = fu_of.get(b.id)
        locked = b.review_request_sent_at is not None or (
            fu is not None and (fu.state is not None or bool(fu.survey_link_sent)))
        out[b.id] = {"attendance": att, "attendance_label": LABELS.get(att or "", None),
                     "started": bool(start and start <= now), "ended": bool(end and end <= now),
                     "can_record": bool(start and start <= now and (b.status or "") in ("booked", "confirmed", "completed")
                                        and not locked),
                     "survey_note": _note(fu, b, att, end, now)}
    return out


def _note(fu, booking, attendance, end, now) -> Optional[str]:
    from app.services import survey_dispatch as sd
    if fu is not None:
        if fu.state == sd.STATE_SENT or (fu.state is None and fu.survey_link_sent):
            return "Survey sent"
        if fu.state in (sd.STATE_HELD, sd.STATE_FAILED):
            return "Survey not sent - see Surveys"
        return "Survey in progress"
    if attendance != "completed":
        return None
    if not sd.sending_enabled():
        return "Surveys are switched off"
    if end is not None and end + sd.BUFFER > now:
        return "Survey after the visit ends"
    return "Survey queued"
