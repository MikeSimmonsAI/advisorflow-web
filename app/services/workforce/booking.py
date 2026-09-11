"""APPOINTMENT SAFETY — the calendar is the authority, never the model.

THE REQUIRED FLOW, AND THE ONE FAILURE IT EXISTS TO PREVENT.

    read authorized availability
      -> choose a slot THAT READ ACTUALLY RETURNED
        -> book through the platform's booking authority
          -> report only what that authority confirmed

The failure is a model that says "Thursday at two is free" because Thursday at
two sounds plausible, and then writes a BookingLink for it. A grieving family
is told they have an appointment that the advisor's calendar knows nothing
about. Section 45 is written about exactly this, and every guard below exists
to make it impossible rather than unlikely.

WHY THIS FILE REUSES `tenant_scheduling` RATHER THAN COMPUTING SLOTS.
`app/services/tenant_scheduling.py` is the platform's existing answer for a
machine-driven caller — the Retell voice bridge — and it already does the four
hard things correctly: the advisor's own working pattern and buffers, their
availability blocks, existing BookingLink conflicts, and an external calendar
read THAT FAILS CLOSED. Its own comment says why that last one matters: "saying
'9am is open' would be a guess spoken aloud to a grieving family." An AI
employee is the same kind of caller, so it gets the same engine.

THE RE-CHECK AT BOOKING TIME IS NOT OPTIONAL. The slots a run read may be
minutes old. `book_slot` re-reads availability for the exact window and refuses
if the slot is no longer in it, so a retry storm or a slow conversation cannot
double-book. The gateway's idempotency key (lead + start time) is the second
layer; this is the first.

CONFIRMATION MESSAGING DELIBERATELY DOES NOT FIRE FROM HERE.
`tenant_scheduling.book` calls `on_booking_confirmed`, which sends a real SMS
or email. That is right for the voice bridge and wrong here: an outbound
message from an AI employee must go through the tool gateway so that contact
eligibility, channel policy, activation stage and the daily cap all apply to
it. So this books the appointment and returns `confirmation_pending: True`, and
the employee sends the confirmation with `conversation.send_sms` like any other
message. One send path, one set of gates.
"""

import logging
from datetime import date as date_cls
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.models import BookingLink, Lead, Organization, User

_log = logging.getLogger(__name__)

DEFAULT_DAYS_AHEAD = 10
MAX_DAYS_AHEAD = 21


def _org(db: Session, organization_id: str) -> Optional[Organization]:
    return (db.query(Organization)
            .filter(Organization.id == organization_id).first())


def resolve_advisor(db: Session, lead: Lead, employee) -> Optional[User]:
    """Whose calendar is this employee booking into?

    Order: the employee's configured booking owner, then the record's assigned
    advisor, then the person who created the employee. Every candidate is
    re-checked against the employee's organization — a booking written into
    another tenant's calendar is a leak with a meeting attached.
    """
    from app.services.workforce import policy as wf_policy
    cfg = wf_policy.json_obj(getattr(employee, "config", None))
    candidates = [cfg.get("booking_owner"), getattr(lead, "assigned_to_id", None),
                  getattr(employee, "created_by", None)]
    for uid in candidates:
        if not uid:
            continue
        user = db.query(User).filter(User.id == uid).first()
        if user is None:
            continue
        if str(user.organization_id) != str(employee.organization_id):
            continue
        return user
    return None


def available_slots(db: Session, *, employee, lead: Lead,
                    advisor: Optional[User] = None,
                    days_ahead: int = DEFAULT_DAYS_AHEAD,
                    duration_minutes: Optional[int] = None,
                    now: Optional[datetime] = None) -> Dict:
    """Real openings, or an honest refusal. NEVER a guess.

    Returns the platform's own availability payload plus `slots` as a list of
    `{starts_at, label}`. `starts_at` is the value that must be passed back to
    `book_slot` verbatim; an employee that alters it is booking a time nobody
    offered, and `book_slot` re-checks and refuses.
    """
    advisor = advisor or resolve_advisor(db, lead, employee)
    if advisor is None:
        return {"availability_status": "no_advisor", "slots": [],
                "reason": "No advisor is configured to take this appointment."}
    org = _org(db, employee.organization_id)
    if org is None:
        return {"availability_status": "no_organization", "slots": [],
                "reason": "The organization could not be resolved."}

    days = max(1, min(int(days_ahead or DEFAULT_DAYS_AHEAD), MAX_DAYS_AHEAD))
    now = now or datetime.utcnow()
    start = now.date()
    end = start + timedelta(days=days - 1)

    from app.services import tenant_scheduling
    from app.services.workforce import policy as wf_policy
    cfg = wf_policy.json_obj(getattr(employee, "config", None))
    try:
        payload = tenant_scheduling.availability(
            db, None, advisor, org, start, end,
            duration_minutes or cfg.get("appointment_duration_minutes")
            or getattr(advisor, "appt_duration_minutes", None),
            getattr(advisor, "booking_timezone", None),
            cfg.get("appointment_type"), now_utc=now)
    except Exception as exc:                                 # noqa: BLE001
        # FAIL CLOSED. Anything that goes wrong reading the calendar produces
        # NO SLOTS, never a default grid. That includes the case where the
        # platform's availability signature changes underneath this call: the
        # wrong behaviour would be to fall back to a locally computed set of
        # office hours, because that is the second availability engine this
        # module exists to avoid.
        _log.exception("workforce booking: availability read failed")
        return {"availability_status": "calendar_unavailable", "slots": [],
                "reason": "The calendar could not be read, so no times can be "
                          "offered right now.",
                "error_class": type(exc).__name__}

    slots: List[Dict] = []
    for s in (payload.get("slots") or []):
        starts_at = s.get("starts_at")
        if not starts_at:
            continue
        slots.append({"starts_at": starts_at,
                      "label": s.get("label") or s.get("starts_at_local"),
                      "duration_minutes": s.get("duration_minutes")})
    return {
        "availability_status": payload.get("availability_status"),
        "advisor_id": advisor.id,
        "advisor_name": advisor.full_name,
        "appointment_type": payload.get("appointment_type"),
        "timezone": payload.get("timezone"),
        "duration_minutes": payload.get("duration_minutes"),
        "slot_count": len(slots),
        "slots": slots,
        "reason": payload.get("reason"),
    }


def _parse_starts_at(raw: str) -> Optional[datetime]:
    """Accept exactly what availability emitted: naive UTC ISO with a Z."""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1]
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt.replace(tzinfo=None)


def book_slot(db: Session, *, employee, lead: Lead, starts_at: str,
              advisor: Optional[User] = None, appt_label: Optional[str] = None,
              now: Optional[datetime] = None) -> Dict:
    """Book a slot the calendar is STILL offering. Raises ValueError otherwise.

    The re-read is the guard. Between the employee reading availability and
    asking for a time, the advisor's own calendar may have changed, the front
    desk may have taken the hour, or a retry of an earlier attempt may already
    have booked it.
    """
    now = now or datetime.utcnow()
    advisor = advisor or resolve_advisor(db, lead, employee)
    if advisor is None:
        raise ValueError("No advisor is configured to take this appointment.")
    org = _org(db, employee.organization_id)
    if org is None:
        raise ValueError("The organization could not be resolved.")

    wanted = _parse_starts_at(starts_at)
    if wanted is None:
        raise ValueError("That start time is not in the format the calendar "
                         "offered (naive UTC ISO 8601 with a trailing Z).")
    if wanted <= now:
        raise ValueError("That time is already in the past.")

    # ── RE-READ. The employee may only take a slot still on offer. ──────────
    days = max(1, min((wanted.date() - now.date()).days + 1, MAX_DAYS_AHEAD))
    fresh = available_slots(db, employee=employee, lead=lead, advisor=advisor,
                            days_ahead=days, now=now)
    if fresh.get("availability_status") != "ok":
        raise ValueError(fresh.get("reason")
                         or "The calendar could not confirm that time.")
    offered = {_parse_starts_at(s["starts_at"]) for s in fresh["slots"]}
    if wanted not in offered:
        raise ValueError("That time is no longer available. Offer another "
                         "opening from the current list.")

    # ── A SECOND BOOKING FOR THE SAME RECORD IS NOT A SECOND APPOINTMENT ────
    #
    # The gateway's idempotency key covers a retry of the same call. This
    # covers the different shape: two DIFFERENT runs, minutes apart, each
    # deciding to book. The record already has a live appointment, so the
    # answer is the existing one rather than a new row.
    existing = (db.query(BookingLink)
                .filter(BookingLink.lead_id == lead.id,
                        BookingLink.status.in_(("booked", "confirmed")),
                        BookingLink.booked_time.isnot(None))
                .order_by(BookingLink.booked_time.asc()).first())
    if existing is not None:
        return {"booking_id": existing.id, "already_booked": True,
                "booked_time": existing.booked_time.isoformat()
                if existing.booked_time else None,
                "advisor_id": existing.user_id,
                "confirmation_pending": False,
                "message": "This record already has an appointment."}

    from app.services import tenant_scheduling
    from app.services.sms_service import _encode_booking_token

    work_tz = tenant_scheduling.Settings(advisor).timezone
    start_local = tenant_scheduling._to_local(wanted, work_tz)
    duration = int(fresh.get("duration_minutes")
                   or tenant_scheduling.DEFAULT_DURATION_MINUTES)
    label = appt_label or fresh.get("appointment_type") \
        or tenant_scheduling.FALLBACK_APPOINTMENT_TYPE

    booking = BookingLink(
        lead_id=lead.id,
        user_id=advisor.id,
        status="booked",
        token=_encode_booking_token(lead, advisor),
        # NAIVE LOCAL WALL TIME, matching every existing tenant reader — see
        # the tenant_scheduling module docstring. Writing UTC here would put
        # the appointment on the booking page at the wrong hour.
        booked_time=start_local,
        appt_label=label,
        appt_duration=duration,
        confirmation_sent=False,
    )
    db.add(booking)
    lead.status = "booked"
    db.flush()

    # SIDE EFFECTS THAT CANNOT UN-BOOK THE MEETING. Each is best effort and
    # reported; none of them may raise out of here, because the appointment is
    # already real by this point.
    calendar_ok = False
    try:
        result = tenant_scheduling._push_to_calendar(
            db, advisor, org, booking, lead, label, start_local, duration,
            work_tz, None)
        calendar_ok = bool((result or {}).get("ok"))
    except Exception:                                        # noqa: BLE001
        _log.exception("workforce booking: calendar push failed for %s",
                       booking.id)
    try:
        tenant_scheduling._make_case_file(db, booking, lead, advisor, start_local)
    except Exception:                                        # noqa: BLE001
        _log.exception("workforce booking: case file creation failed for %s",
                       booking.id)

    return {
        "booking_id": booking.id,
        "already_booked": False,
        "advisor_id": advisor.id,
        "advisor_name": advisor.full_name,
        "appointment_type": label,
        "duration_minutes": duration,
        "starts_at": wanted.replace(microsecond=0).isoformat() + "Z",
        "starts_at_local": start_local.replace(microsecond=0).isoformat(),
        "timezone": work_tz,
        "calendar_synced": calendar_ok,
        # See the module header: the confirmation is a MESSAGE, and messages
        # from an AI employee go through the gateway.
        "confirmation_pending": True,
        "message": "Booked. Send the confirmation through the messaging tool.",
    }


def reschedule(db: Session, *, employee, lead: Lead, booking_link_id: str,
               starts_at: str, now: Optional[datetime] = None) -> Dict:
    """Move an existing appointment this employee's record owns.

    The booking is loaded through the LEAD, so a booking id belonging to
    another record — or another tenant — simply does not resolve.
    """
    now = now or datetime.utcnow()
    booking = (db.query(BookingLink)
               .filter(BookingLink.id == booking_link_id,
                       BookingLink.lead_id == lead.id).first())
    if booking is None:
        raise ValueError("No such appointment on this record.")
    advisor = db.query(User).filter(User.id == booking.user_id).first()
    if advisor is None or str(advisor.organization_id) != str(employee.organization_id):
        raise ValueError("The advisor for that appointment could not be resolved.")

    wanted = _parse_starts_at(starts_at)
    if wanted is None or wanted <= now:
        raise ValueError("That is not a valid future time.")

    days = max(1, min((wanted.date() - now.date()).days + 1, MAX_DAYS_AHEAD))
    fresh = available_slots(db, employee=employee, lead=lead, advisor=advisor,
                            days_ahead=days, now=now)
    if fresh.get("availability_status") != "ok":
        raise ValueError(fresh.get("reason")
                         or "The calendar could not confirm that time.")
    if wanted not in {_parse_starts_at(s["starts_at"]) for s in fresh["slots"]}:
        raise ValueError("That time is not available. Offer another opening.")

    from app.services import tenant_scheduling
    work_tz = tenant_scheduling.Settings(advisor).timezone
    previous = booking.booked_time
    booking.booked_time = tenant_scheduling._to_local(wanted, work_tz)
    booking.status = "booked"
    booking.confirmation_sent = False
    db.flush()
    return {"booking_id": booking.id,
            "previous_time": previous.isoformat() if previous else None,
            "starts_at": wanted.replace(microsecond=0).isoformat() + "Z",
            "starts_at_local": booking.booked_time.replace(microsecond=0).isoformat(),
            "timezone": work_tz,
            "confirmation_pending": True}
