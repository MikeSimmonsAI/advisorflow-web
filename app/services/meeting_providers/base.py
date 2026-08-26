"""
The video meeting provider interface.

Same contract as the calendar provider layer, for the same reason: a provider
NEVER raises into the caller. It returns a MeetingResult describing what
happened. A booked sales appointment must survive Zoom being down, and code
that can throw a vendor exception into the middle of a booking cannot promise
that.

WHAT A PROVIDER MUST NEVER DO
-----------------------------
Put a host URL anywhere a prospect can reach. `MeetingResult.host_url` exists so
the orchestrator can encrypt and store it; it is excluded from every serializer
above this layer, and no provider should return it in any other field.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple


@dataclass
class MeetingResult:
    """What a provider call did. Never an exception."""
    ok: bool
    provider_meeting_id: Optional[str] = None
    join_url: Optional[str] = None
    # HOST ONLY. Encrypted by the caller before storage, never serialized out.
    host_url: Optional[str] = None
    passcode: Optional[str] = None
    dial_in_info: Optional[str] = None
    error_code: Optional[str] = None   # 'auth' | 'scope' | 'rate_limit' | 'transport' | ...
    error_message: Optional[str] = None

    @property
    def needs_attention(self) -> bool:
        """A credential or permission problem a human must fix. Retrying on
        their behalf cannot help, so it is surfaced differently from a blip."""
        return self.error_code in ("auth", "scope", "not_configured")

    @classmethod
    def failure(cls, code: str, message) -> "MeetingResult":
        # Truncated hard: vendor errors carry whole response bodies, and this
        # string lands in a database column and on a salesperson's screen.
        return cls(ok=False, error_code=code, error_message=str(message)[:500])


@dataclass
class MeetingRequest:
    """What to create. Assembled once, sent to whichever provider.

    `agenda` is what ATTENDEES read. Internal notes have no field here, and
    that absence is the enforcement — a future edit cannot leak them into a
    Zoom agenda by touching a shared string.
    """
    topic: str
    starts_at: datetime          # naive UTC
    duration_minutes: int
    timezone: str = "UTC"        # IANA — the wall clock the meeting was agreed in
    agenda: str = ""
    # Who hosts. None means the provider's configured default host.
    host_identifier: Optional[str] = None
    # Correlation only — written into the provider's own tracking field so a
    # later reconciliation can prove which appointment an orphaned meeting is.
    advisorflow_appointment_id: Optional[str] = None


class MeetingProvider:
    """Base class. Subclasses override; none of these ever raise."""

    key: str = "base"

    def __init__(self, config=None):
        # `config` is a MeetingProviderConfig row or None (env-var fallback).
        self.config = config

    def is_ready(self) -> Tuple[bool, Optional[str]]:
        """(usable, reason-if-not). Checked before every operation so a missing
        credential is reported as such rather than discovered as an exception."""
        return False, "Provider not implemented"

    def create_meeting(self, req: MeetingRequest) -> MeetingResult:
        return MeetingResult.failure("unimplemented", "create_meeting not implemented")

    def update_meeting(self, provider_meeting_id: str, req: MeetingRequest) -> MeetingResult:
        return MeetingResult.failure("unimplemented", "update_meeting not implemented")

    def cancel_meeting(self, provider_meeting_id: str) -> MeetingResult:
        return MeetingResult.failure("unimplemented", "cancel_meeting not implemented")

    def verify(self) -> MeetingResult:
        """A real round-trip proving the credentials work. Used by the
        'Test connection' action — a check that only reads our own database
        proves nothing about whether we can actually create a meeting."""
        return MeetingResult.failure("unimplemented", "verify not implemented")
