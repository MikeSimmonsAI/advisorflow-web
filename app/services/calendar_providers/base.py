"""
The calendar provider interface.

Every provider — Microsoft Graph, Google Calendar, and the fake used in tests —
implements exactly this. The sync orchestrator talks only to this shape, which
is what keeps "add Google" from meaning "fork the scheduling model".

DESIGN RULE: a provider NEVER raises into the orchestrator. It returns a
SyncResult saying what happened. An appointment must survive any provider
failure, and code that can throw from three different vendor SDKs into the
middle of a booking transaction cannot make that promise.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple


@dataclass
class SyncResult:
    """What a provider call did. Never an exception."""
    ok: bool
    external_event_id: Optional[str] = None
    error_code: Optional[str] = None      # 'reauth' | 'scope' | 'http_404' | 'transport' | ...
    error_message: Optional[str] = None

    @property
    def needs_reauth(self) -> bool:
        """The user must reconnect. Retrying on their behalf cannot fix this,
        so it is surfaced differently from a transient failure."""
        return self.error_code in ("reauth", "scope")

    @classmethod
    def failure(cls, code: str, message) -> "SyncResult":
        # Truncated hard: provider errors can carry whole response bodies, and
        # this string is written to a log table and shown in the UI.
        return cls(ok=False, error_code=code, error_message=str(message)[:500])


@dataclass
class EventPayload:
    """What goes on a calendar. Assembled once, sent to whichever provider.

    `body_text` is what the ATTENDEE reads. Internal notes are never put here —
    that is enforced by the caller building this object, and by the fact that
    this class has no field for them.
    """
    subject: str
    starts_at: datetime          # naive UTC
    ends_at: datetime            # naive UTC
    timezone: str                # IANA — the wall clock the meeting was agreed in
    body_text: str = ""
    location: Optional[str] = None
    meeting_url: Optional[str] = None
    attendees: List[Tuple[str, str]] = field(default_factory=list)  # (email, name)
    # Written into the provider event so a later reconciliation can prove an
    # event belongs to a specific AdvisorFlow appointment.
    advisorflow_appointment_id: Optional[str] = None
    # Revision number. Microsoft and Google track their own versions and ignore
    # this; iCalendar does NOT — a re-sent .ics with a SEQUENCE that has not
    # increased is discarded by the recipient's mail client as a duplicate,
    # which is exactly how a reschedule silently fails to reach someone.
    sequence: int = 0
    # Who the invitation is addressed to, and who it is from. Only the .ics
    # fallback needs these; the OAuth providers already know whose calendar
    # they are writing to.
    recipient_email: Optional[str] = None
    recipient_name: Optional[str] = None
    organizer_email: Optional[str] = None
    organizer_name: Optional[str] = None


@dataclass
class BusyInterval:
    """A period the user is unavailable. Interval only — never a subject.

    A colleague booking a meeting needs to know you are busy, not what you are
    doing. Providers happily return subjects and attendee lists; this type has
    nowhere to put them, which is the point.
    """
    starts_at: datetime
    ends_at: datetime
    provider_event_id: Optional[str] = None
    is_all_day: bool = False


class CalendarProvider:
    """Base class. Subclasses override; none of these ever raise."""

    key: str = "base"

    def __init__(self, user, connection=None, org=None):
        self.user = user
        self.connection = connection
        # Only the .ics fallback uses this — it sends mail, and mail is sent
        # from the BRAND's verified domain, not a global default. The OAuth
        # providers write to a calendar and never send anything, so they ignore
        # it. It lives on the base so the registry can construct every provider
        # the same way.
        self.org = org

    def is_ready(self) -> Tuple[bool, Optional[str]]:
        """(usable, reason-if-not). Checked before every operation so a dead
        connection is reported as such rather than discovered as an exception."""
        return False, "Provider not implemented"

    def create_event(self, payload: EventPayload) -> SyncResult:
        return SyncResult.failure("unimplemented", "create_event not implemented")

    def update_event(self, external_event_id: str, payload: EventPayload) -> SyncResult:
        return SyncResult.failure("unimplemented", "update_event not implemented")

    def cancel_event(self, external_event_id: str,
                     payload: Optional[EventPayload] = None) -> SyncResult:
        """`payload` is optional and ignored by the OAuth providers — Graph and
        Google cancel by id alone. The .ics fallback genuinely needs it: an
        iCalendar CANCEL must repeat the event's UID, SEQUENCE and DTSTART or
        the recipient's mail client will not match it to the invitation it is
        meant to withdraw, and the meeting stays on their calendar."""
        return SyncResult.failure("unimplemented", "cancel_event not implemented")

    def get_busy(self, start_utc: datetime, end_utc: datetime) -> Tuple[List[BusyInterval], Optional[SyncResult]]:
        """Busy intervals in the window. Returns ([], failure) on error rather
        than raising — a provider that cannot be read must degrade to 'we know
        of no external commitments', never to a broken availability search."""
        return [], SyncResult.failure("unimplemented", "get_busy not implemented")
