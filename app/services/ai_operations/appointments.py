"""APPOINTMENTS — THE CALENDAR AUTHORITY IS THE PLATFORM'S, NEVER THE MODEL'S.

THREE RULES, AND EVERY LINE BELOW SERVES ONE OF THEM.

    NEVER INVENT AVAILABILITY. Openings come from
    `tenant_scheduling.availability`, which reads the advisor's working
    pattern, their blocks, the bookings already on the books and their
    external calendar — and which FAILS CLOSED when it cannot read that
    calendar. An employee may only offer a time that function returned, and
    `book` re-checks that the requested time is still among them at the
    moment of booking. A time an employee "remembers" from five minutes ago
    is not an opening.

    NEVER BUILD A SECOND CALENDAR. No slot table, no parallel booking
    model, no availability arithmetic of this layer's own. `BookingLink` is
    the booking, `tenant_scheduling.book` is the way one is made, and this
    module's whole contribution is the authority chain in front of it and
    the record of what happened behind it.

    NEVER BOOK TWICE. The idempotency key is (subject, start time, thread),
    deliberately not including the employee: two employees booking the same
    family into the same slot is one duplicate appointment for that family,
    whichever of them asked first.

WHAT HAPPENS IN A DARK LAUNCH. When the resolved provider is simulated — the
default everywhere in this build — an appointment is RECORDED and no
`BookingLink` is written, no calendar event is pushed and no confirmation is
sent to anybody. A simulation that wrote a real booking into a real
customer's calendar would not be a simulation.
"""

import logging
from datetime import date as date_cls
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.ai_operations_models import AIConversationThread
from app.services.ai_operations import audit, channels, comm_state
from app.services.ai_operations import constants as C
from app.services.ai_operations import contracts, idempotency, orchestrator

_log = logging.getLogger(__name__)


def _advisor_for(db: Session, ctx: contracts.EmployeeContext, lead):
    """Whose calendar. The lead's advisor, then the employee's handoff owner.

    NOT "any advisor in the organization". Booking somebody into a calendar
    that is not theirs produces an appointment nobody attends.
    """
    from app.models.models import User
    for candidate in (getattr(lead, "assigned_to_id", None),
                      ctx.handoff_user_id):
        if not candidate:
            continue
        user = (db.query(User)
                .filter(User.id == candidate,
                        User.organization_id == ctx.organization_id).first())
        if user is not None:
            return user
    return None


def list_availability(db: Session, ctx: contracts.EmployeeContext, *,
                      subject_id: str, days_ahead: int = 7,
                      duration_minutes: Optional[int] = None,
                      appointment_type: Optional[str] = None,
                      now_utc: Optional[datetime] = None) -> Dict[str, Any]:
    """The openings this employee is allowed to offer. Read-only.

    Returns the platform's own answer verbatim, including its refusals: an
    unreadable calendar comes back as `availability_status =
    calendar_unavailable` with a sentence, and the employee says that rather
    than offering a time it cannot stand behind.
    """
    from app.models.models import Lead, Organization
    lead = (db.query(Lead)
            .filter(Lead.id == subject_id,
                    Lead.organization_id == ctx.organization_id).first())
    if lead is None:
        return {"availability_status": "not_found", "success": False,
                "slots": [], "reason": "No such contact in this organization."}
    advisor = _advisor_for(db, ctx, lead)
    if advisor is None:
        return {"availability_status": "no_advisor", "success": False,
                "slots": [],
                "reason": ("No advisor is assigned to this contact, so there "
                           "is no calendar to offer times from.")}
    org = (db.query(Organization)
           .filter(Organization.id == ctx.organization_id).first())

    from app.services import tenant_scheduling
    today = (now_utc or datetime.utcnow()).date()
    try:
        # `cred` is unused by this function — it is part of the voice
        # bridge's signature, not of the availability computation. Passing
        # None keeps this layer out of the integration-credential business
        # while still asking the platform's own engine the question.
        return tenant_scheduling.availability(
            db, None, advisor, org, today,
            today + timedelta(days=max(1, min(int(days_ahead), 14))),
            duration_minutes, None, appointment_type, now_utc=now_utc)
    except Exception as exc:                                 # noqa: BLE001
        _log.info("ai_operations: availability unavailable (%s)", exc)
        return {"availability_status": "calendar_unavailable",
                "success": False, "slots": [],
                "reason": ("The calendar could not be read, so no times can "
                           "be offered right now.")}


def _slot_is_offered(availability: Dict[str, Any], starts_at: str) -> bool:
    """Was this exact time actually returned by the availability engine?

    THE ANTI-HALLUCINATION CHECK. A model that has been told the openings can
    still produce a time that was never in the list — a minute out, a day
    out, or invented wholesale. Comparing against the engine's own strings
    makes "never state a time you were not given" enforced rather than
    instructed.
    """
    wanted = (starts_at or "").strip()
    for slot in (availability.get("slots") or []):
        if str(slot.get("starts_at", "")).strip() == wanted:
            return True
    return False


def book_appointment(db: Session, ctx: contracts.EmployeeContext, *,
                     thread: AIConversationThread, starts_at: str,
                     duration_minutes: Optional[int] = None,
                     appointment_type: Optional[str] = None,
                     notes: Optional[str] = None,
                     run_id: Optional[str] = None,
                     now_utc: Optional[datetime] = None):
    """Book a time that the calendar authority actually offered."""
    subject_id = thread.subject_id
    key = idempotency.key_for_booking(thread_id=thread.id,
                                      subject_id=subject_id,
                                      start_at=starts_at)
    gate = orchestrator.begin(
        db, ctx, C.OP_BOOK_APPOINTMENT, subject_id=subject_id,
        subject_type=thread.subject_type, thread=thread,
        work_item_id=thread.work_item_id, run_id=run_id,
        idempotency_key=key, correlation_kind=C.CORR_BOOKING,
        arguments={"starts_at": starts_at,
                   "duration_minutes": duration_minutes},
        requires_eligibility=False)
    if not gate.allowed:
        return orchestrator.OperationResult(ok=False, gate=gate)

    availability = list_availability(
        db, ctx, subject_id=subject_id, days_ahead=14,
        duration_minutes=duration_minutes, appointment_type=appointment_type,
        now_utc=now_utc)
    if not availability.get("success"):
        orchestrator.finish(
            db, gate, status="skipped",
            result_summary=availability.get("reason") or "No availability.",
            outcome=C.D_NO_AVAILABILITY)
        gate.denial_code = C.D_NO_AVAILABILITY
        gate.denial_reason = availability.get("reason")
        return orchestrator.OperationResult(ok=False, gate=gate,
                                            error=availability.get("reason"))
    if not _slot_is_offered(availability, starts_at):
        reason = ("%s was never offered by the calendar, so it cannot be "
                  "booked." % starts_at)
        orchestrator.finish(db, gate, status="skipped", result_summary=reason,
                            outcome=C.D_SLOT_NOT_OFFERED)
        gate.denial_code = C.D_SLOT_NOT_OFFERED
        gate.denial_reason = reason
        contracts.mirror_supervisor_event(
            db, ctx, event_code=C.SUP_POLICY_DENIAL, severity="warning",
            message="An employee tried to book a time the calendar never "
                    "offered.",
            detail={"thread_id": thread.id, "starts_at": starts_at},
            recommended_action="Check the employee's prompt and the "
                               "availability it was given.")
        return orchestrator.OperationResult(ok=False, gate=gate, error=reason)

    adapter, why = channels.resolve(db, ctx, C.CHANNEL_SMS,
                                    reaches_outside=True)
    simulated = not adapter.reaches_outside or bool(
        getattr(adapter, "key", "").startswith("simulated"))

    if simulated:
        # A SIMULATED BOOKING WRITES NO BOOKING. The appointment is recorded
        # here, the thread moves, the outcome is reported — and the
        # customer's real calendar is untouched, which is the whole point of
        # a dark launch.
        ref = "sim_booking_%s" % (audit.digest("%s|%s" % (thread.id,
                                                          starts_at)) or "0")
        thread.appointment_ref = ref
        comm_state.thread_state(db, thread, C.APPOINTMENT_BOOKED,
                                reason="appointment booked (simulated)")
        db.flush()
        contracts.mirror_performance(db, ctx, "appointments_booked")
        contracts.mirror_supervisor_event(
            db, ctx, event_code=C.SUP_APPOINTMENT_BOOKED, severity="info",
            message="An appointment was booked (simulated).",
            detail={"thread_id": thread.id, "starts_at": starts_at})
        orchestrator.finish(
            db, gate, status="ok",
            result_summary="Appointment recorded for %s (simulated; nothing "
                           "was written to a real calendar)." % starts_at,
            simulated=True, outcome=C.APPOINTMENT_BOOKED,
            next_action="confirmation",
            detail={"appointment_ref": ref, "why": why,
                    "slot_offered": True})
        return orchestrator.OperationResult(
            ok=True, gate=gate,
            detail={"appointment_ref": ref, "simulated": True,
                    "starts_at": starts_at})

    # ── live booking, through the platform's own booking authority ─────────
    from app.models.models import Lead, Organization
    lead = (db.query(Lead)
            .filter(Lead.id == subject_id,
                    Lead.organization_id == ctx.organization_id).first())
    advisor = _advisor_for(db, ctx, lead)
    org = (db.query(Organization)
           .filter(Organization.id == ctx.organization_id).first())
    cred = _booking_credential(db, ctx)
    if cred is None:
        # NO SECOND BOOKING PATH. `tenant_scheduling.book` is the function
        # that re-validates against the live calendar, mints the token the
        # booking page understands, pushes the calendar event and sends the
        # confirmations. Reimplementing a thinner version of it here would be
        # a second booking path that sends no confirmation, and the family
        # would simply never hear that they are booked. Refuse and ask a
        # person instead.
        reason = ("Live booking needs the organization's scheduling bridge, "
                  "which is not configured; a person should book this.")
        orchestrator.finish(db, gate, status="skipped", result_summary=reason,
                            outcome=C.D_BOOKING_AUTHORITY, simulated=False)
        gate.denial_code = C.D_BOOKING_AUTHORITY
        gate.denial_reason = reason
        return orchestrator.OperationResult(ok=False, gate=gate, error=reason)

    from app.services import tenant_scheduling
    try:
        starts_utc = datetime.fromisoformat(
            starts_at.replace("Z", "").replace("+00:00", ""))
        result = tenant_scheduling.book(
            db, cred, advisor, org, starts_utc, external_ref="aiops_%s" % key,
            duration_minutes=duration_minutes,
            appointment_type=appointment_type, lead_id=subject_id,
            notes=notes, now_utc=now_utc)
    except Exception as exc:                                 # noqa: BLE001
        reason = str(getattr(exc, "detail", exc))[:300]
        orchestrator.finish(db, gate, status="error", result_summary=reason,
                            error=reason, simulated=False,
                            outcome=C.D_BOOKING_AUTHORITY)
        gate.denial_code = C.D_BOOKING_AUTHORITY
        gate.denial_reason = reason
        return orchestrator.OperationResult(ok=False, gate=gate, error=reason)

    thread.appointment_ref = result.get("booking_id")
    comm_state.thread_state(db, thread, C.APPOINTMENT_BOOKED,
                            reason="appointment booked")
    db.flush()
    contracts.mirror_performance(db, ctx, "appointments_booked")
    contracts.mirror_supervisor_event(
        db, ctx, event_code=C.SUP_APPOINTMENT_BOOKED, severity="info",
        message="An appointment was booked.",
        detail={"thread_id": thread.id, "booking_id": result.get("booking_id"),
                "replay": bool(result.get("idempotent_replay"))})
    orchestrator.finish(
        db, gate, status="ok",
        result_summary="Booked: %s" % (result.get("label") or starts_at),
        simulated=False, outcome=C.APPOINTMENT_BOOKED,
        next_action="confirmation",
        detail={"booking_id": result.get("booking_id"),
                "calendar_synced": result.get("calendar_synced"),
                "confirmation_sent": result.get("confirmation_sent"),
                "idempotent_replay": result.get("idempotent_replay")})
    return orchestrator.OperationResult(ok=True, gate=gate, detail=result)


def _booking_credential(db: Session, ctx: contracts.EmployeeContext):
    """The organization's scheduling bridge credential, if it has one."""
    try:
        from app.models.integration_models import IntegrationCredential
        return (db.query(IntegrationCredential)
                .filter(IntegrationCredential.organization_id
                        == ctx.organization_id,
                        IntegrationCredential.is_active.is_(True))
                .first())
    except Exception as exc:                                 # noqa: BLE001
        _log.info("ai_operations: no scheduling credential available (%s)",
                  exc)
        return None


def reschedule_appointment(db: Session, ctx: contracts.EmployeeContext, *,
                           thread: AIConversationThread, starts_at: str,
                           duration_minutes: Optional[int] = None,
                           run_id: Optional[str] = None):
    """Move an appointment this conversation already owns.

    A RESCHEDULE IS A CANCEL AND A BOOK, and it is written that way rather
    than as an update, because the second half can fail: a time that was free
    when the family asked can be taken by the time the write happens. Doing
    it in that order would leave them with nothing. So the new time is booked
    FIRST and the old booking is released only once the new one exists.
    """
    if not thread.appointment_ref:
        return orchestrator.OperationResult(
            ok=False,
            gate=orchestrator._deny(
                db, ctx, C.OP_RESCHEDULE_APPOINTMENT,
                code=C.D_RECORD_NOT_FOUND,
                reason="This conversation has no appointment to move.",
                decided_by="appointments", thread=thread,
                subject_type=thread.subject_type,
                subject_id=thread.subject_id,
                tool_key=contracts.tool_for(C.OP_RESCHEDULE_APPOINTMENT)))

    previous_ref = thread.appointment_ref
    # Booking the new time re-runs every gate, including the slot-was-offered
    # check. Only if it succeeds is the old one released.
    thread.appointment_ref = None
    db.flush()
    result = book_appointment(db, ctx, thread=thread, starts_at=starts_at,
                              duration_minutes=duration_minutes,
                              run_id=run_id)
    if not result.ok:
        thread.appointment_ref = previous_ref
        db.flush()
        return result

    released = _release_booking(db, ctx, previous_ref)
    audit.record(db, event_code="ops.appointment_rescheduled", ctx=ctx,
                 thread_id=thread.id, subject_type=thread.subject_type,
                 subject_id=thread.subject_id,
                 operation=C.OP_RESCHEDULE_APPOINTMENT,
                 outcome="rescheduled",
                 message="Appointment moved to %s." % starts_at,
                 detail={"previous_ref": previous_ref,
                         "previous_released": released,
                         "new_ref": thread.appointment_ref})
    return result


def cancel_appointment(db: Session, ctx: contracts.EmployeeContext, *,
                       thread: AIConversationThread, reason: str = "",
                       run_id: Optional[str] = None):
    """Cancel an appointment this conversation owns, where authorized."""
    gate = orchestrator.begin(
        db, ctx, C.OP_CANCEL_APPOINTMENT, subject_id=thread.subject_id,
        subject_type=thread.subject_type, thread=thread,
        work_item_id=thread.work_item_id, run_id=run_id,
        correlation_kind=C.CORR_BOOKING, arguments={"reason": reason},
        requires_eligibility=False)
    if not gate.allowed:
        return orchestrator.OperationResult(ok=False, gate=gate)
    if not thread.appointment_ref:
        orchestrator.finish(db, gate, status="skipped",
                            result_summary="No appointment to cancel.")
        return orchestrator.OperationResult(ok=False, gate=gate,
                                            error="No appointment to cancel.")
    released = _release_booking(db, ctx, thread.appointment_ref)
    thread.appointment_ref = None
    db.flush()
    orchestrator.finish(db, gate, status="ok",
                        result_summary="Appointment cancelled.",
                        outcome="cancelled",
                        detail={"released": released, "reason": reason})
    return orchestrator.OperationResult(ok=True, gate=gate,
                                        detail={"released": released})


def _release_booking(db: Session, ctx: contracts.EmployeeContext,
                     booking_ref: Optional[str]) -> bool:
    """Free a booking slot. Simulated references release nothing."""
    if not booking_ref or booking_ref.startswith("sim_booking_"):
        return False
    try:
        from app.models.models import BookingLink, Lead
        booking = (db.query(BookingLink)
                   .join(Lead, Lead.id == BookingLink.lead_id)
                   .filter(BookingLink.id == booking_ref,
                           Lead.organization_id == ctx.organization_id)
                   .first())
        if booking is None:
            return False
        booking.status = "cancelled"
        db.flush()
        try:
            from app.services.calendar_service import cancel_calendar_event
            cancel_calendar_event(db, booking)
        except Exception as exc:                             # noqa: BLE001
            _log.info("ai_operations: calendar event not cancelled (%s)", exc)
        return True
    except Exception as exc:                                 # noqa: BLE001
        _log.warning("ai_operations: booking release failed (%s)", exc)
        return False


def offered_times(availability: Dict[str, Any], limit: int = 3
                  ) -> List[Dict[str, str]]:
    """The two or three openings an employee may put in a message.

    Bounded deliberately: a list of twelve times is not an offer, it is a
    spreadsheet, and the family has to reply to one of them.
    """
    return [{"starts_at": s.get("starts_at"), "label": s.get("label")}
            for s in (availability.get("slots") or [])[:max(1, limit)]]
