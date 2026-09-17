"""
Reminders for a SalesAppointment, as a first-class lifecycle.

WHY THIS IS NOT `appointment_reminder_cron`
-------------------------------------------
That module exists and it operates on `BookingLink` - the customer-tenant
model a funeral-home advisor books a family into. It has nothing to do with
SalesAppointment, it was never wired to one, and it is currently an orphan: no
Render service runs it and nothing imports it. Extending it would mean teaching
one module two unrelated domains, on the customer-tenant side of a boundary the
sales models are explicit about not crossing.

It is also built on two booleans, `reminder_24hr_sent` and `reminder_1hr_sent`,
and that shape cannot answer the questions this needs to answer:

    Was the 24-hour reminder skipped because the meeting was booked ninety
    minutes out, or has the job simply not run yet?      - a boolean cannot say

    Why did it fail?                                     - a boolean cannot say

    The meeting moved. Is the reminder owed again?       - a boolean says "sent"
                                                           and stays wrong

And most importantly: a boolean is written AFTER the send. Two overlapping runs
both read False and both send. The claim has to be the atomic act, which is what
UNIQUE(appointment_id, kind, target_starts_at) makes it - the second run's
INSERT fails at the database rather than producing a second email.

WHAT SCHEDULES THIS
-------------------
Nothing new. `process_due` is a plain function over a Session; the existing
background-loop registry in app/service_role.py owns which process runs it, the
same way it owns every other scheduled job on the platform. There is no second
scheduler here and there must not be one.

DELIVERY IS GATED
-----------------
Every send goes through `outbound_email_gate.gate_staff_email`, which is off by
default. In this build a due reminder is therefore CLAIMED, attempted, and
recorded as `failed` with the gate's own reason - which is the honest state. The
claim still happens, so turning the gate on later does not produce a backlog of
reminders for meetings that have already happened.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.scheduling_models import (
    AppointmentReminder, SalesAppointment, MeetingType,
    APPT_SCHEDULED, DEFAULT_TIMEZONE,
    REMINDER_24H, REMINDER_1H, REMINDER_KINDS, REMINDER_LEAD_MINUTES,
    REMINDER_PENDING, REMINDER_SENT, REMINDER_FAILED, REMINDER_SKIPPED,
    REMINDER_SUPPRESSED, REMINDER_SETTLED,
)
from app.services import availability as av

log = logging.getLogger(__name__)

# How long before the meeting a reminder may still be sent. A "24-hour reminder"
# delivered eleven hours late is not a 24-hour reminder; it is a confusing second
# message. Past the window it is recorded as skipped rather than sent.
LATE_TOLERANCE_MINUTES = {REMINDER_24H: 6 * 60, REMINDER_1H: 30}

# DO NOT STACK TWO MESSAGES ON A CUSTOMER.
#
# The 24-hour and one-hour reminders are 23 hours apart by construction and can
# never crowd each other. The message they CAN crowd is the booking
# confirmation, which goes out the moment the form is submitted.
#
# A meeting booked 24 hours and ten minutes ahead would send "you're booked" and
# then "your meeting is tomorrow" within ten minutes of each other, saying the
# same thing twice to somebody who has just this second read it. So the rule is
# measured from the BOOKING, not between the two reminders: a 24-hour reminder
# that would land within this of the confirmation is suppressed, and the
# one-hour message - which is the genuinely useful one - still goes.
MIN_GAP_FROM_BOOKING_MINUTES = 90


def _rows(db: Session, appointment_id: str) -> List[AppointmentReminder]:
    return (db.query(AppointmentReminder)
            .filter(AppointmentReminder.appointment_id == appointment_id)
            .all())


def schedule_for(db: Session, appt: SalesAppointment,
                 now: Optional[datetime] = None) -> dict:
    """Work out what this meeting's reminders are, and claim the ones that are
    already decided.

    Called at booking and again after a reschedule. IDEMPOTENT: rows are keyed
    on (appointment, kind, target time), so calling it twice for the same
    meeting time changes nothing.

    A reminder whose moment has already passed at booking time is written as
    SKIPPED immediately rather than left absent. A gap in this table would be
    indistinguishable from a job that never ran.
    """
    now = now or datetime.utcnow()
    target = appt.starts_at
    plan = {}

    # Settle anything claimed against a DIFFERENT meeting time. The meeting
    # moved; a reminder owed for where it used to be is not owed any more.
    for row in _rows(db, appt.id):
        if row.target_starts_at != target and row.status == REMINDER_PENDING:
            row.status = REMINDER_SUPPRESSED
            row.detail = "The meeting was rescheduled; this reminder is no longer owed."

    cancelled = appt.status != APPT_SCHEDULED

    for kind in (REMINDER_24H, REMINDER_1H):
        due_at = target - timedelta(minutes=REMINDER_LEAD_MINUTES[kind])
        status = REMINDER_PENDING
        detail = None

        if cancelled:
            status = REMINDER_SUPPRESSED
            detail = "The meeting is not scheduled."
        elif due_at <= now:
            # Booked closer than this reminder's lead time. It never had a
            # moment, which is different from having missed one.
            status = REMINDER_SKIPPED
            detail = ("The meeting was booked less than %d minutes ahead, so this "
                      "reminder had no moment to be sent."
                      % REMINDER_LEAD_MINUTES[kind])
        elif (due_at - now) < timedelta(minutes=MIN_GAP_FROM_BOOKING_MINUTES):
            # It would arrive on the heels of the confirmation email, saying the
            # same thing to somebody who has just read it.
            status = REMINDER_SUPPRESSED
            detail = ("Would arrive within %d minutes of the booking "
                      "confirmation; only the later message is sent."
                      % MIN_GAP_FROM_BOOKING_MINUTES)

        row = _claim(db, appt, kind, target, due_at, status, detail)
        plan[kind] = {"status": row.status if row else "already_claimed",
                      "scheduled_for": due_at, "detail": detail}

    db.flush()
    return {"ok": True, "plan": plan, "target_starts_at": target}


def _claim(db: Session, appt: SalesAppointment, kind: str, target: datetime,
           due_at: datetime, status: str,
           detail: Optional[str]) -> Optional[AppointmentReminder]:
    """Insert the row, or find the one that already exists.

    The INSERT is the claim. A unique-violation here is not an error - it means
    another worker got there first, which is exactly the outcome the constraint
    exists to produce.
    """
    existing = (db.query(AppointmentReminder)
                .filter(AppointmentReminder.appointment_id == appt.id,
                        AppointmentReminder.kind == kind,
                        AppointmentReminder.target_starts_at == target)
                .first())
    if existing is not None:
        return existing
    row = AppointmentReminder(appointment_id=appt.id, kind=kind, status=status,
                              target_starts_at=target, scheduled_for=due_at,
                              detail=detail)
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return (db.query(AppointmentReminder)
                .filter(AppointmentReminder.appointment_id == appt.id,
                        AppointmentReminder.kind == kind,
                        AppointmentReminder.target_starts_at == target)
                .first())
    return row


def cancel_for(db: Session, appt: SalesAppointment,
               reason: str = "The meeting was cancelled.") -> int:
    """Suppress every reminder still owed. A cancelled meeting sends nothing."""
    count = 0
    for row in _rows(db, appt.id):
        if row.status == REMINDER_PENDING:
            row.status = REMINDER_SUPPRESSED
            row.detail = reason
            count += 1
    db.flush()
    return count


def due_reminders(db: Session, now: Optional[datetime] = None,
                  limit: int = 100) -> List[AppointmentReminder]:
    """Everything owed right now, oldest first.

    Bounded by LATE_TOLERANCE_MINUTES at the far end: a reminder the scheduler
    missed by half a day is not sent late, it is settled as skipped by
    `process_due`. Sending it anyway would put "your meeting is tomorrow" in
    front of somebody two hours before it starts.
    """
    now = now or datetime.utcnow()
    return (db.query(AppointmentReminder)
            .filter(AppointmentReminder.status == REMINDER_PENDING,
                    AppointmentReminder.scheduled_for.isnot(None),
                    AppointmentReminder.scheduled_for <= now)
            .order_by(AppointmentReminder.scheduled_for.asc())
            .limit(limit).all())


def process_due(db: Session, now: Optional[datetime] = None,
                limit: int = 100, send: bool = True) -> dict:
    """Send what is owed. Idempotent, and safe to run twice.

    `send=False` runs the whole decision path and records what WOULD have gone,
    which is what the tests use and what an operator can use to inspect the
    queue without touching a mail provider.
    """
    now = now or datetime.utcnow()
    report = {"examined": 0, "sent": 0, "skipped": 0, "failed": 0,
              "suppressed": 0, "errors": []}

    for row in due_reminders(db, now=now, limit=limit):
        report["examined"] += 1
        appt = (db.query(SalesAppointment)
                .filter(SalesAppointment.id == row.appointment_id).first())

        if appt is None or appt.status != APPT_SCHEDULED:
            row.status = REMINDER_SUPPRESSED
            row.detail = "The meeting is no longer scheduled."
            report["suppressed"] += 1
            continue

        if appt.starts_at != row.target_starts_at:
            # Rescheduled since this was claimed. The new time has its own rows.
            row.status = REMINDER_SUPPRESSED
            row.detail = "The meeting was rescheduled after this was scheduled."
            report["suppressed"] += 1
            continue

        if appt.starts_at <= now:
            row.status = REMINDER_SKIPPED
            row.detail = "The meeting had already started."
            report["skipped"] += 1
            continue

        late_by = now - row.scheduled_for
        if late_by > timedelta(minutes=LATE_TOLERANCE_MINUTES.get(row.kind, 60)):
            row.status = REMINDER_SKIPPED
            row.detail = ("Missed its window by %d minutes; sending it now would "
                          "be a confusing second message rather than a reminder."
                          % int(late_by.total_seconds() // 60))
            report["skipped"] += 1
            continue

        if not appt.prospect_email:
            row.status = REMINDER_SKIPPED
            row.detail = "No prospect email address."
            report["skipped"] += 1
            continue

        row.attempts = (row.attempts or 0) + 1
        row.attempted_at = now
        row.recipient = appt.prospect_email

        if not send:
            row.status = REMINDER_SENT
            row.sent_at = now
            row.detail = "Simulated: delivery not attempted."
            report["sent"] += 1
            continue

        try:
            _deliver(db, appt, row)
            row.status = REMINDER_SENT
            row.sent_at = now
            row.detail = None
            report["sent"] += 1
        except Exception as exc:                                 # noqa: BLE001
            # LEFT AS FAILED, NOT RETURNED TO PENDING. A reminder that keeps
            # failing must not be retried forever into a window it has already
            # left; the row records what happened and an operator decides.
            row.status = REMINDER_FAILED
            row.detail = str(exc)[:300]
            report["failed"] += 1
            report["errors"].append({"reminder_id": row.id,
                                     "error": str(exc)[:200]})

    db.commit()
    return report


def _deliver(db: Session, appt: SalesAppointment, row: AppointmentReminder) -> None:
    """One reminder email, through the platform's own gated sender.

    The gate is off by default, so in this build this raises EmailSendDisabled
    and the row records that as the reason - which is the true state, and is
    reported as such rather than as a success.
    """
    from app.services import outbound_email_gate
    from app.services.email_service import send_email_via_provider
    from app.services.appointment_invites import brand_identity, _SendingOrg

    ident = brand_identity(db, appt)
    hours = 24 if row.kind == REMINDER_24H else 1
    subject, body = render(db, appt, hours)

    outbound_email_gate.gate_staff_email(appt.prospect_email,
                                         purpose="appointment reminder")
    identity = _SendingOrg(ident.get("from_email"), from_name=ident.get("name"))
    result = send_email_via_provider(
        to_email=appt.prospect_email, subject=subject, body_html=body,
        org=identity, message_type="appointment_reminder",
        template_id="sales.appointment_reminder")
    if not result.get("success"):
        raise RuntimeError(result.get("error") or "The mail provider refused it.")


def render(db: Session, appt: SalesAppointment, hours: int) -> tuple:
    """Subject and body. Branded, and in the PROSPECT's timezone when known.

    A reminder that states the time in the seller's timezone is a reminder that
    makes the customer do arithmetic under time pressure. The meeting's own zone
    is stated alongside it so the two can never be confused.

    THE JOIN LINK IS THE REAL ONE OR THERE IS NO BUTTON. A dead call-to-action
    at the moment somebody is trying to join is worse than a line of text
    telling them to check their confirmation email.
    """
    from html import escape
    from app.services.appointment_invites import brand_identity
    ident = brand_identity(db, appt)
    brand = ident.get("name") or "Your meeting"

    meeting_tz = appt.timezone or DEFAULT_TIMEZONE
    local = av.utc_to_local(appt.starts_at, meeting_tz)
    when = "%s (%s)" % (local.strftime("%A, %B %d at %I:%M %p"), meeting_tz)
    if appt.prospect_timezone and appt.prospect_timezone != meeting_tz:
        theirs = av.utc_to_local(appt.starts_at, appt.prospect_timezone)
        when = ("%s (%s) — %s your time"
                % (local.strftime("%A, %B %d at %I:%M %p"), meeting_tz,
                   theirs.strftime("%I:%M %p")))

    lead = "tomorrow" if hours == 24 else "in one hour"
    subject = "Reminder: your %s %s" % (appt.title.split(" · ")[0], lead)

    cta = ("<p><a href='%s' style='display:inline-block;padding:10px 18px;"
           "background:#1565c0;color:#fff;text-decoration:none;border-radius:6px'>"
           "Join the meeting</a></p>" % escape(appt.meeting_url)
           if appt.meeting_url else
           "<p>The join details are in your confirmation email.</p>")

    body = ("<p>Hi %s,</p><p>A reminder that your meeting with %s is %s.</p>"
            "<p><strong>%s</strong></p>%s"
            % (escape((appt.prospect_name or "there").split(" ")[0]),
               escape(brand), lead, escape(when), cta))
    return subject, body


def state_for(db: Session, appointment_id: str) -> List[dict]:
    """Every reminder this meeting has, for an operations view."""
    return [{"kind": r.kind, "status": r.status,
             "scheduled_for": r.scheduled_for, "sent_at": r.sent_at,
             "target_starts_at": r.target_starts_at,
             "attempts": r.attempts, "detail": r.detail,
             "recipient": r.recipient}
            for r in sorted(_rows(db, appointment_id),
                            key=lambda x: (x.scheduled_for or datetime.min))]
