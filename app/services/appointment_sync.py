"""
Appointment → external calendar orchestration.

THE RULE THIS FILE EXISTS TO ENFORCE
------------------------------------
The AdvisorFlow appointment is the source of truth, and it must survive
anything a calendar provider does. Sync runs AFTER the booking transaction has
committed, never inside it. A meeting that is agreed, saved and blocking
everyone's time is not un-booked because Microsoft returned a 500.

Concretely: every function here can be called on an appointment that already
exists, is idempotent, and reports per-participant outcomes instead of raising.

PER PARTICIPANT, NOT PER APPOINTMENT
------------------------------------
Each internal attendee gets their own event on their own calendar under their
own OAuth grant. That is why sync state lives on AppointmentParticipant. Blake
on Microsoft, Michael on Google and Mike on nothing at all is a normal, fully
successful booking: three different code paths, one meeting, and PARTIAL
SUCCESS IS A REAL OUTCOME rather than a failure to hide.

WHAT THE PROSPECT NEVER SEES
----------------------------
`appointment.notes` is internal. It is not put in any event body, any .ics
DESCRIPTION, or any invitation email. The prospect-facing text is built
separately in `_prospect_body`, and internal events are built from
`_internal_body`. Two functions, so no future edit can leak one into the other
by editing a shared string.

NO PROVIDER-SENT INVITATIONS
----------------------------
Internal calendar events are created with NO attendee list. Each participant
already gets their own copy, and the prospect gets AdvisorFlow's own branded
invitation with the secure confirmation link. Letting Graph or Google also mail
the attendee list would send a second, competing invitation with a different
accept/decline mechanism pointing at a calendar we do not control.
"""
import logging
from datetime import datetime
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.calendar_models import (
    AppointmentSyncLog, CalendarConnection,
    PROVIDER_ICS,
    SYNC_NOT_CONNECTED, SYNC_PENDING, SYNC_SYNCED, SYNC_FAILED,
    SYNC_RETRYING, SYNC_REAUTH, SYNC_ICS_SENT, SYNC_NEEDS_ATTENTION,
)
from app.models.scheduling_models import (
    SalesAppointment, AppointmentParticipant, APPT_CANCELLED,
)
from app.models.models import User
from app.services.calendar_providers.base import EventPayload

log = logging.getLogger(__name__)

# Attempts before a failure stops being "retrying" and becomes "failed", which
# is what surfaces it to a human. Low on purpose: a machine that retries
# forever is a machine that never tells anyone something is wrong.
MAX_SYNC_ATTEMPTS = 3


# ── event text ──────────────────────────────────────────────────────────────

def _internal_body(appt: SalesAppointment) -> str:
    """What an INTERNAL participant sees on their own calendar.

    Includes the prospect's identity and the meeting logistics — this is the
    salesperson's own calendar and they need to know who they are meeting. Does
    NOT include `appt.notes`: internal notes are for the workspace, and a
    calendar event is a document that gets forwarded, screen-shared and synced
    to phones.
    """
    lines = []
    if appt.prospect_name or appt.prospect_company:
        who = appt.prospect_name or ""
        if appt.prospect_company:
            who = ("%s (%s)" % (who, appt.prospect_company)).strip()
        lines.append("With: " + who)
    if appt.prospect_email:
        lines.append("Email: " + appt.prospect_email)
    if appt.prospect_phone:
        lines.append("Phone: " + appt.prospect_phone)
    if appt.location:
        lines.append("Location: " + appt.location)
    lines.append("")
    lines.append("Scheduled in AdvisorFlow.")
    return "\n".join(lines).strip()


def _internal_subject(appt: SalesAppointment) -> str:
    base = appt.title or "Sales meeting"
    who = appt.prospect_company or appt.prospect_name
    if who and who.lower() not in base.lower():
        return "%s — %s" % (base, who)
    return base


def _payload_for(appt: SalesAppointment, user: User,
                 sequence: int = 0, organizer=None) -> EventPayload:
    """One participant's event. Attendees deliberately empty — see module docs."""
    org_email = getattr(organizer, "email", None) if organizer else None
    org_name = getattr(organizer, "full_name", None) if organizer else None
    return EventPayload(
        subject=_internal_subject(appt),
        starts_at=appt.starts_at,
        ends_at=appt.ends_at,
        timezone=appt.timezone or "UTC",
        body_text=_internal_body(appt),
        location=appt.location,
        meeting_url=appt.meeting_url,
        attendees=[],                      # never let a provider mail anyone
        advisorflow_appointment_id=appt.id,
        sequence=sequence,
        recipient_email=getattr(user, "email", None),
        recipient_name=getattr(user, "full_name", None),
        organizer_email=org_email,
        organizer_name=org_name,
    )


# ── recording ───────────────────────────────────────────────────────────────

def _log(db: Session, appt_id: str, user_id: Optional[str], provider: str,
         action: str, result, status: str, attempt: int, now: datetime) -> None:
    """Append one attempt. Never raises — a logging failure must not turn a
    successful sync into a failed one."""
    try:
        db.add(AppointmentSyncLog(
            appointment_id=appt_id, user_id=user_id, provider=provider or "",
            action=action, status=status, ok=bool(result.ok),
            external_event_id=result.external_event_id,
            error_code=result.error_code,
            error_message=(result.error_message or "")[:2000] or None,
            attempt=attempt, occurred_at=now,
        ))
    except Exception:
        log.exception("could not write sync log for appointment %s", appt_id)


def _apply_result(part: AppointmentParticipant, provider_key: str, result,
                  now: datetime) -> str:
    """Write the outcome onto the participant row and return the status."""
    part.external_calendar_provider = provider_key
    part.sync_last_attempt = now
    part.sync_attempts = (part.sync_attempts or 0) + 1

    if result.ok:
        part.external_event_id = result.external_event_id or part.external_event_id
        part.external_synced_at = now
        part.sync_error = None
        # The .ics path is honestly labelled as its own state. Calling it
        # 'synced' would claim a calendar connection that does not exist, and
        # the UI would stop offering to connect one.
        status = SYNC_ICS_SENT if provider_key == PROVIDER_ICS else SYNC_SYNCED
        if provider_key == PROVIDER_ICS:
            part.ics_sent_at = now
        part.sync_status = status
        return status

    part.sync_error = (result.error_message or result.error_code or "")[:2000]
    if result.needs_reauth:
        # Only the user can fix this, so retrying is pointless and marking it
        # 'retrying' would hide it behind a spinner forever.
        status = SYNC_REAUTH
    elif (part.sync_attempts or 0) < MAX_SYNC_ATTEMPTS:
        status = SYNC_RETRYING
    else:
        status = SYNC_FAILED
    part.sync_status = status
    return status


# ── one participant ─────────────────────────────────────────────────────────

def _sync_participant(db: Session, appt: SalesAppointment,
                      part: AppointmentParticipant, user: User,
                      org=None, organizer=None,
                      now: Optional[datetime] = None) -> dict:
    """Create or update ONE participant's calendar event.

    IDEMPOTENT ON `external_event_id`. If the row already carries an event id
    this updates that event; otherwise it creates one. That is what makes a
    retry safe: running this twice moves an event, it does not produce two.
    """
    now = now or datetime.utcnow()
    from app.services import calendar_providers as reg

    provider = reg.get_provider(db, user, org=org)
    # The REGISTRY key, not the provider class's own name. This is what gets
    # stored on the participant row and handed back as `prefer` when
    # cancelling, so it must be something the registry can resolve — and it
    # must reflect any fallback to .ics that actually happened.
    key = getattr(provider, "resolved_key", None) or PROVIDER_ICS

    ready, why = provider.is_ready()
    if not ready:
        # Not connected and not sendable. This is a state, not a crash: the
        # participant stays on the appointment and still blocks their time.
        part.external_calendar_provider = key
        part.sync_status = SYNC_NOT_CONNECTED
        part.sync_error = why
        part.sync_last_attempt = now
        return {"user_id": user.id, "provider": key, "status": SYNC_NOT_CONNECTED,
                "ok": False, "error": why}

    # A reschedule must raise the iCalendar SEQUENCE or the recipient's mail
    # client discards the update as a duplicate. Providers that version events
    # themselves ignore this.
    payload = _payload_for(appt, user, sequence=(appt.rescheduled_count or 0),
                           organizer=organizer)

    if part.external_event_id:
        action = "update"
        result = provider.update_event(part.external_event_id, payload)
    else:
        action = "create"
        result = provider.create_event(payload)

    status = _apply_result(part, key, result, now)
    _log(db, appt.id, user.id, key, action, result, status,
         part.sync_attempts or 1, now)
    return {"user_id": user.id, "provider": key, "status": status,
            "ok": bool(result.ok), "error": result.error_code,
            "needs_reauth": bool(result.needs_reauth)}


# ── public entry points ─────────────────────────────────────────────────────

def _participants(db: Session, appt: SalesAppointment):
    """(participant, user) pairs. A participant whose user row is gone is
    skipped rather than crashing the whole sync for everyone else."""
    parts = (db.query(AppointmentParticipant)
             .filter(AppointmentParticipant.appointment_id == appt.id)
             .all())
    out = []
    for p in parts:
        u = db.query(User).filter(User.id == p.user_id).first()
        if u is not None:
            out.append((p, u))
    return out


def sync_appointment(db: Session, appt: SalesAppointment, org=None,
                     organizer=None, now: Optional[datetime] = None,
                     commit: bool = True) -> dict:
    """Push an appointment to every participant's calendar.

    CALL THIS AFTER THE BOOKING HAS COMMITTED. It never raises, so a caller
    cannot accidentally roll back a saved meeting on a provider error, and it
    reports every participant's outcome separately because partial success is a
    real and acceptable result.
    """
    now = now or datetime.utcnow()
    results = []
    for part, user in _participants(db, appt):
        try:
            results.append(_sync_participant(db, appt, part, user, org=org,
                                             organizer=organizer, now=now))
        except Exception as e:
            # A provider is contractually forbidden from raising, but this
            # boundary assumes one eventually will. One participant's failure
            # must not abandon the rest of the room.
            log.exception("participant sync blew up (appt=%s user=%s)", appt.id, user.id)
            part.sync_status = SYNC_FAILED
            part.sync_error = str(e)[:2000]
            part.sync_last_attempt = now
            part.sync_attempts = (part.sync_attempts or 0) + 1
            results.append({"user_id": user.id, "provider": None,
                            "status": SYNC_FAILED, "ok": False, "error": "exception"})
    if commit:
        try:
            db.commit()
        except Exception:
            log.exception("could not commit sync state for appointment %s", appt.id)
            db.rollback()
    return summarize(results)


def summarize(results: List[dict]) -> dict:
    """Roll per-participant outcomes into something a UI can render honestly.

    `needs_attention` is the one a manager acts on. It counts reauth and hard
    failures — NOT `not_connected`, which is a legitimate choice and routes to
    the email fallback rather than to a problem anyone has to fix.
    """
    synced = [r for r in results if r.get("status") == SYNC_SYNCED]
    emailed = [r for r in results if r.get("status") == SYNC_ICS_SENT]
    unconn = [r for r in results if r.get("status") == SYNC_NOT_CONNECTED]
    attention = [r for r in results if r.get("status") in SYNC_NEEDS_ATTENTION]
    return {
        "total": len(results),
        "synced": len(synced),
        "ics_sent": len(emailed),
        "not_connected": len(unconn),
        "needs_attention": len(attention),
        # True only when nobody needs a human. An all-.ics meeting is a success.
        "all_ok": len(attention) == 0,
        "partial": bool(attention) and bool(synced or emailed),
        "results": results,
    }


def resync_appointment(db: Session, appt: SalesAppointment, org=None,
                       organizer=None, now: Optional[datetime] = None,
                       commit: bool = True) -> dict:
    """After a reschedule. Same call — `sync_appointment` already updates in
    place when an event id exists, which is exactly what a moved meeting needs.

    Kept as a named function so call sites read as what they mean, and so the
    reschedule path has somewhere to grow if it ever diverges.
    """
    return sync_appointment(db, appt, org=org, organizer=organizer,
                            now=now, commit=commit)


def retry_failed_sync(db: Session, appt: SalesAppointment, org=None,
                      organizer=None, user_id: Optional[str] = None,
                      now: Optional[datetime] = None, commit: bool = True) -> dict:
    """Retry only the participants that need it. Manual, from the UI.

    Resets the attempt counter for the rows being retried: a human pressing
    Retry is new information, and carrying the old count forward would let one
    bad afternoon exhaust the budget permanently.

    A `reauth` row is retried only if the user has since reconnected, which
    `is_ready()` decides — retrying a dead grant just re-fails and re-alerts.
    """
    now = now or datetime.utcnow()
    results = []
    for part, user in _participants(db, appt):
        if user_id and user.id != user_id:
            continue
        if part.sync_status not in SYNC_NEEDS_ATTENTION and not user_id:
            continue
        part.sync_attempts = 0
        try:
            results.append(_sync_participant(db, appt, part, user, org=org,
                                             organizer=organizer, now=now))
        except Exception as e:
            log.exception("retry blew up (appt=%s user=%s)", appt.id, user.id)
            results.append({"user_id": user.id, "provider": None,
                            "status": SYNC_FAILED, "ok": False, "error": str(e)[:200]})
    if commit:
        try:
            db.commit()
        except Exception:
            log.exception("could not commit retry state for appointment %s", appt.id)
            db.rollback()
    return summarize(results)


def cancel_appointment_sync(db: Session, appt: SalesAppointment, org=None,
                            organizer=None, now: Optional[datetime] = None,
                            commit: bool = True) -> dict:
    """Withdraw the meeting from every participant's calendar.

    A cancellation that only changes our own status is the worst outcome of the
    three: everyone still has the meeting, nobody knows it is off, and someone
    dials in. So this runs for every participant that has an event id, and
    failures are recorded loudly rather than swallowed.

    A participant with no event id has nothing to withdraw — that is a clean
    no-op, not a failure.
    """
    now = now or datetime.utcnow()
    from app.services import calendar_providers as reg

    results = []
    for part, user in _participants(db, appt):
        if not part.external_event_id:
            part.sync_status = SYNC_NOT_CONNECTED
            results.append({"user_id": user.id, "provider": part.external_calendar_provider,
                            "status": SYNC_NOT_CONNECTED, "ok": True, "error": None,
                            "nothing_to_cancel": True})
            continue
        try:
            # Prefer the provider the event was actually CREATED with. If the
            # user has since connected a different one, cancelling through the
            # new provider would look for an id that lives in the old calendar.
            prefer = part.external_calendar_provider
            provider = reg.get_provider(db, user, org=org, prefer=prefer)
            # SEQUENCE must exceed the last one sent or an .ics CANCEL is
            # ignored as a duplicate by the recipient's mail client.
            payload = _payload_for(appt, user,
                                   sequence=(appt.rescheduled_count or 0) + 1,
                                   organizer=organizer)
            key = getattr(provider, "resolved_key", None) or prefer or PROVIDER_ICS
            result = provider.cancel_event(part.external_event_id, payload)
            part.sync_last_attempt = now
            part.sync_attempts = (part.sync_attempts or 0) + 1
            if result.ok:
                part.sync_error = None
                part.external_synced_at = now
                # The event is gone. Keeping the id would make a later retry
                # try to update something that no longer exists.
                part.external_event_id = None
                part.sync_status = SYNC_NOT_CONNECTED
                status = SYNC_NOT_CONNECTED
            else:
                part.sync_error = (result.error_message or result.error_code or "")[:2000]
                status = SYNC_REAUTH if result.needs_reauth else SYNC_FAILED
                part.sync_status = status
            _log(db, appt.id, user.id, key, "cancel", result, status,
                 part.sync_attempts or 1, now)
            results.append({"user_id": user.id, "provider": key,
                            "status": status, "ok": bool(result.ok),
                            "error": result.error_code})
        except Exception as e:
            log.exception("cancel sync blew up (appt=%s user=%s)", appt.id, user.id)
            part.sync_status = SYNC_FAILED
            part.sync_error = str(e)[:2000]
            results.append({"user_id": user.id, "provider": None,
                            "status": SYNC_FAILED, "ok": False, "error": "exception"})

    if commit:
        try:
            db.commit()
        except Exception:
            log.exception("could not commit cancel state for appointment %s", appt.id)
            db.rollback()
    return summarize(results)
