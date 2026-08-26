"""
Sales appointment → video meeting orchestration.

THE RULE, SAME AS CALENDAR SYNC: the AdvisorFlow appointment is the source of
truth and must survive anything Zoom does. Every function here runs AFTER the
booking has committed, never raises, and records failures on the row instead of
propagating them.

A provider failure produces an appointment with no video link and a visible
reason. It never produces a failed booking, and it never produces a fake link.

ORDERING MATTERS
----------------
Video is provisioned BEFORE calendar sync and BEFORE the prospect invitation,
because both of those need the join URL in their body. `ensure_meeting` is
therefore called first in the booking path, and the calendar/invite steps read
`appointment.meeting_url`, which this module populates.

HOST URL HANDLING
-----------------
Zoom's start_url starts the meeting AS the host — anyone holding it can
impersonate the host. It is encrypted with the same Fernet key as OAuth tokens,
stored only in `host_url_encrypted`, and returned by exactly one endpoint, to
the appointment's own participants. It is never in an email, an .ics, a calendar
body, or a prospect-facing response.
"""
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models.meeting_models import (
    AppointmentMeeting,
    MEET_PENDING, MEET_CREATED, MEET_UPDATED, MEET_CANCELLED, MEET_FAILED,
    MEET_NOT_REQUIRED,
)
from app.models.scheduling_models import SalesAppointment, MeetingType

log = logging.getLogger(__name__)


def _agenda(appt: SalesAppointment) -> str:
    """What ATTENDEES see in Zoom. Never `appt.notes` — those are internal, and
    a Zoom agenda is visible to every participant including the prospect."""
    bits = []
    if appt.prospect_company:
        bits.append("With %s" % appt.prospect_company)
    elif appt.prospect_name:
        bits.append("With %s" % appt.prospect_name)
    bits.append("Scheduled in AdvisorFlow.")
    return "\n".join(bits)


def _request(appt: SalesAppointment, mt: Optional[MeetingType]):
    from app.services.meeting_providers import MeetingRequest
    duration = int((appt.ends_at - appt.starts_at).total_seconds() // 60)
    topic = appt.title or (mt.name if mt else "Sales meeting")
    who = appt.prospect_company or appt.prospect_name
    if who and who.lower() not in topic.lower():
        topic = "%s — %s" % (topic, who)
    return MeetingRequest(
        topic=topic,
        starts_at=appt.starts_at,
        duration_minutes=max(duration, 1),
        timezone=appt.timezone or "UTC",
        agenda=_agenda(appt),
        advisorflow_appointment_id=appt.id,
    )


def _store_host_url(row: AppointmentMeeting, host_url: Optional[str]) -> None:
    if not host_url:
        return
    try:
        from app.utils.crypto import encrypt_value
        row.host_url_encrypted = encrypt_value(host_url)
    except Exception:
        # Losing the host URL is survivable — the host can start from their own
        # Zoom account. Storing it in plaintext because encryption failed is not.
        log.exception("could not encrypt Zoom host url; storing nothing")
        row.host_url_encrypted = None


def get_meeting_row(db: Session, appointment_id: str) -> Optional[AppointmentMeeting]:
    return (db.query(AppointmentMeeting)
            .filter(AppointmentMeeting.appointment_id == appointment_id)
            .first())


def _meeting_type(db: Session, appt: SalesAppointment) -> Optional[MeetingType]:
    if not appt.meeting_type_id:
        return None
    return db.query(MeetingType).filter(MeetingType.id == appt.meeting_type_id).first()


def ensure_meeting(db: Session, appt: SalesAppointment,
                   now: Optional[datetime] = None,
                   commit: bool = True) -> dict:
    """Create or update the video meeting for an appointment.

    IDEMPOTENT. With a stored provider_meeting_id it updates; without one it
    creates. Running it twice moves a meeting, it does not make two — which is
    what makes it safe to call from both booking and reschedule.

    Returns a small report. Never raises.
    """
    now = now or datetime.utcnow()
    from app.services import meeting_providers as reg

    mt = _meeting_type(db, appt)
    key = reg.resolve_provider_key(mt)
    row = get_meeting_row(db, appt.id)

    # ── the meeting type does not want video ────────────────────────────────
    if not key:
        # If a room exists from before the type changed, withdraw it rather
        # than leaving an orphan nobody will ever join.
        if row is not None and row.provider_meeting_id and row.status != MEET_CANCELLED:
            return cancel_meeting(db, appt, now=now, commit=commit,
                                  reason="meeting type no longer requires video")
        if row is None:
            row = AppointmentMeeting(appointment_id=appt.id,
                                     brand_sales_org_id=appt.brand_sales_org_id,
                                     provider="none", status=MEET_NOT_REQUIRED)
            db.add(row)
        else:
            row.status = MEET_NOT_REQUIRED
        if commit:
            _safe_commit(db, appt.id)
        return {"ok": True, "provider": None, "status": MEET_NOT_REQUIRED,
                "join_url": None, "reason": "not_required"}

    provider = reg.get_provider(db, appt.brand_sales_org_id, key=key)
    if row is None:
        row = AppointmentMeeting(appointment_id=appt.id,
                                 brand_sales_org_id=appt.brand_sales_org_id,
                                 provider=key, status=MEET_PENDING)
        db.add(row)
        db.flush()
    row.provider = key
    row.brand_sales_org_id = appt.brand_sales_org_id

    if provider is None:
        return _fail(db, row, appt, "not_configured",
                     "No %s provider is available for this brand." % key,
                     now, commit)

    ready, why = provider.is_ready()
    if not ready:
        # Not configured for this brand. Recorded so the UI can say WHY there is
        # no link, instead of showing a mysteriously missing button.
        return _fail(db, row, appt, "not_configured", why, now, commit)

    req = _request(appt, mt)
    row.attempts = (row.attempts or 0) + 1

    if row.provider_meeting_id:
        result = provider.update_meeting(row.provider_meeting_id, req)
        action = MEET_UPDATED
    else:
        result = provider.create_meeting(req)
        action = MEET_CREATED

    if not result.ok:
        return _fail(db, row, appt, result.error_code, result.error_message, now, commit)

    row.provider_meeting_id = result.provider_meeting_id or row.provider_meeting_id
    if result.join_url:
        row.join_url = result.join_url
    if result.passcode:
        row.passcode = result.passcode
    if result.dial_in_info:
        row.dial_in_info = result.dial_in_info
    _store_host_url(row, result.host_url)
    row.status = action
    row.provider_error = None
    row.last_synced_at = now
    row.cancelled_at = None

    # THE APPOINTMENT'S OWN meeting_url IS THE ATTENDEE LINK.
    # Calendar sync and the prospect invitation both read it, so writing it here
    # is what makes the join link appear in Outlook, Google and the customer's
    # email without any of those knowing Zoom exists.
    if row.join_url:
        appt.meeting_url = row.join_url
        appt.meeting_provider = key

    _event(db, appt, "meeting_created" if action == MEET_CREATED else "meeting_updated",
           "Zoom meeting %s" % ("created" if action == MEET_CREATED else "updated"),
           None, now)

    if commit:
        _safe_commit(db, appt.id)
    return {"ok": True, "provider": key, "status": row.status,
            "join_url": row.join_url, "reason": None}


def _fail(db: Session, row: AppointmentMeeting, appt: SalesAppointment,
          code: Optional[str], message: Optional[str],
          now: datetime, commit: bool) -> dict:
    """Record a provider failure. The appointment itself is never touched."""
    row.status = MEET_FAILED
    row.provider_error = (message or code or "Unknown provider error")[:2000]
    row.last_synced_at = row.last_synced_at   # unchanged: this was not a success
    _event(db, appt, "meeting_failed", "Video meeting could not be created",
           row.provider_error, now)
    if commit:
        _safe_commit(db, appt.id)
    return {"ok": False, "provider": row.provider, "status": MEET_FAILED,
            "join_url": row.join_url, "reason": code,
            "error": row.provider_error}


def _safe_commit(db: Session, appt_id: str) -> None:
    try:
        db.commit()
    except Exception:
        log.exception("could not commit meeting state for appointment %s", appt_id)
        db.rollback()


def _event(db: Session, appt: SalesAppointment, event_type: str,
           summary: str, detail, now: datetime) -> None:
    """Write to the OPPORTUNITY timeline. Never raises — a logging failure must
    not turn a successful meeting into a failed one."""
    if not appt.opportunity_id:
        return
    try:
        from app.models.sales_models import OpportunityEvent
        db.add(OpportunityEvent(
            opportunity_id=appt.opportunity_id, event_type=event_type,
            summary=summary, detail=detail, actor_user_id=None,
            occurred_at=now))
    except Exception:
        log.exception("could not write meeting event for appointment %s", appt.id)


def cancel_meeting(db: Session, appt: SalesAppointment,
                   now: Optional[datetime] = None, commit: bool = True,
                   reason: str = None) -> dict:
    """Withdraw the video meeting.

    A meeting nobody cancelled is a room the prospect can still walk into after
    the deal is dead. Failures are recorded loudly rather than swallowed, and
    the provider_meeting_id is KEPT on failure so a retry can finish the job.
    """
    now = now or datetime.utcnow()
    from app.services import meeting_providers as reg

    row = get_meeting_row(db, appt.id)
    if row is None or not row.provider_meeting_id:
        # Nothing was ever provisioned. A clean no-op, not a failure.
        return {"ok": True, "provider": None, "status": MEET_NOT_REQUIRED,
                "reason": "nothing_to_cancel"}

    provider = reg.get_provider(db, appt.brand_sales_org_id, key=row.provider)
    if provider is None:
        return _fail(db, row, appt, "not_configured",
                     "No %s provider available to cancel the meeting." % row.provider,
                     now, commit)

    result = provider.cancel_meeting(row.provider_meeting_id)
    row.attempts = (row.attempts or 0) + 1
    if not result.ok:
        return _fail(db, row, appt, result.error_code, result.error_message, now, commit)

    row.status = MEET_CANCELLED
    row.cancelled_at = now
    row.last_synced_at = now
    row.provider_error = None
    # The room is gone. Keeping the id would make a later retry try to update
    # something that no longer exists; keeping the join_url would leave a dead
    # link on screen that looks joinable.
    row.provider_meeting_id = None
    row.join_url = None
    row.host_url_encrypted = None
    row.passcode = None
    if appt.meeting_provider == row.provider:
        appt.meeting_url = None

    _event(db, appt, "meeting_cancelled", "Zoom meeting cancelled", reason, now)
    if commit:
        _safe_commit(db, appt.id)
    return {"ok": True, "provider": row.provider, "status": MEET_CANCELLED,
            "reason": reason}


def host_url_for(db: Session, appointment_id: str) -> Optional[str]:
    """Decrypt the host URL. The ONLY place this value is read.

    Callers must already have proven the requester is a participant on the
    appointment — this function performs no authorization of its own, and its
    result must never reach a prospect-facing response.
    """
    row = get_meeting_row(db, appointment_id)
    if row is None or not row.host_url_encrypted:
        return None
    try:
        from app.utils.crypto import decrypt_value
        return decrypt_value(row.host_url_encrypted)
    except Exception:
        log.exception("could not decrypt host url for appointment %s", appointment_id)
        return None


def meeting_out(row: Optional[AppointmentMeeting]) -> dict:
    """Serialize for the sales UI.

    HOST URL IS ABSENT BY CONSTRUCTION. There is no field for it here and no
    branch that adds one, so no future edit to a caller can accidentally include
    it — the same reason BusyInterval has nowhere to put a meeting subject.
    """
    from app.models.meeting_models import MEET_LABELS, MEET_NEEDS_ATTENTION
    if row is None:
        return {"has_meeting": False, "provider": None, "status": None,
                "join_url": None, "label": None, "needs_attention": False}
    return {
        "has_meeting": bool(row.join_url),
        "provider": row.provider,
        "status": row.status,
        "label": MEET_LABELS.get(row.status, row.status),
        "join_url": row.join_url,
        "passcode": row.passcode,
        "needs_attention": row.status in MEET_NEEDS_ATTENTION,
        "provider_error": row.provider_error,
        "last_synced_at": row.last_synced_at,
    }
