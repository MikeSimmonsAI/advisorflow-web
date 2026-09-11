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


@dataclass
class ExternalEventState:
    """What a provider event looks like RIGHT NOW, for drift detection.

    Read-only and deliberately narrow. It carries the time (the only field
    whose divergence is a scheduling commitment), the provider's own version
    marker, and whether the event still claims to belong to a specific
    AdvisorFlow appointment — nothing else.

    NOTE WHAT IS ABSENT: no attendee list, no body. A reconciliation pass runs
    over every participant's calendar, so anything this class could hold is
    something the reconciler would end up logging about people who never
    consented to that. The same privacy rule that keeps `BusyInterval`
    interval-only applies here, for the same reason.

    `exists=False` is the DELETED case and is a normal answer, not an error.
    """
    exists: bool
    starts_at: Optional[datetime] = None      # naive UTC
    ends_at: Optional[datetime] = None        # naive UTC
    etag: Optional[str] = None                # Graph changeKey / Google etag
    is_cancelled: bool = False
    # The appointment id the event itself claims, read back from wherever the
    # provider was told to keep it. Lets the reconciler prove an event is ours
    # before it writes — an id we merely stored is not proof, because a stale
    # or mis-copied id could point at a stranger's event.
    claimed_appointment_id: Optional[str] = None
    subject: Optional[str] = None
    location: Optional[str] = None


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

    def supports_read_back(self) -> bool:
        """Can this provider be asked what one event currently says?

        The .ics fallback cannot: it sends an email and has no calendar to
        query. That is not a failure to work around — it means drift detection
        genuinely does not apply to that delivery path, and the reconciler
        skips it rather than inventing a conflict out of its own blindness.
        """
        return False

    def get_event(self, external_event_id: str) -> Tuple[Optional["ExternalEventState"],
                                                         Optional[SyncResult]]:
        """Read ONE event back, for drift detection. Returns (state, error).

        A missing event returns `ExternalEventState(exists=False)` with NO
        error — deletion is information, not a fault, and returning it as a
        failure would make "somebody deleted this" indistinguishable from
        "Microsoft was down", which are opposite situations: one needs healing,
        the other needs leaving alone until the provider comes back.
        """
        return None, SyncResult.failure("unimplemented", "get_event not implemented")
