"""
Provider-neutral communications interfaces.
======================================================================

EvoSys Pro is the communications control plane. Providers execute; they do not
decide. Concretely, everything below is deliberately ignorant of:

  * which lead is being contacted and why
  * whether that contact is allowed — eligibility and suppression are decided
    upstream, in EvoSys, for every channel and every provider alike
  * what the outcome means to the business

A provider's whole job is: take an already-authorised instruction, execute it
on a vendor, and describe what happened in EvoSys's vocabulary.

SHAPE COPIED ON PURPOSE. `app/services/calendar_providers/` and
`app/services/meeting_providers/` already solved this exact problem twice —
base class + never-raising result dataclass + `_OVERRIDES` test seam +
`register_provider`/`reset_providers`/`get_*`. A third dialect would be a third
thing to learn, so this mirrors them.

THE RULE THAT MATTERS: a provider NEVER raises into its caller. Vendor failures
come back as a result object with `ok=False`, because the caller is usually
mid-transaction with a database row it must leave clean and recoverable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional, Tuple


# ── Results ──────────────────────────────────────────────────────────────────

@dataclass
class SmsResult:
    ok: bool
    provider_message_id: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None

    @classmethod
    def failure(cls, code: str, message: str) -> "SmsResult":
        return cls(ok=False, error_code=code, error_message=message)


@dataclass
class VoiceCallResult:
    """`ok=True` means the provider ACCEPTED the call, not that anyone
    answered. Everything after that arrives asynchronously as webhooks."""
    ok: bool
    provider_call_id: Optional[str] = None
    provider_status: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None

    @classmethod
    def failure(cls, code: str, message: str) -> "VoiceCallResult":
        return cls(ok=False, error_code=code, error_message=message)


# ── Requests ─────────────────────────────────────────────────────────────────

@dataclass
class VoiceCallRequest:
    """What EvoSys hands a voice provider to place one call.

    `metadata` is CORRELATION ONLY. It travels to the vendor and returns on
    every webhook, which makes it ideal for finding our row — and useless as
    authorization, because anything that round-tripped through a third party
    can be forged. Ownership is always re-derived from our own stored record.

    `dynamic_variables` are the conversational values the agent speaks with.
    Keep to what the agent actually uses: these land in a vendor's prompt and
    logs, so nothing sensitive goes here that the conversation doesn't need.
    """
    to_number: str
    from_number: str
    agent_id: str
    metadata: Dict[str, str] = field(default_factory=dict)
    dynamic_variables: Dict[str, str] = field(default_factory=dict)


@dataclass
class SmsRequest:
    to_number: str
    body: str
    lead_id: Optional[str] = None


# ── Normalised inbound event ─────────────────────────────────────────────────

# The provider-neutral event vocabulary. Retell's names, Twilio's names and any
# future vendor's collapse into these before anything downstream sees them, so
# business logic never learns a vendor's spelling.
EVENT_STARTED = "call_started"
EVENT_ENDED = "call_ended"
EVENT_ANALYZED = "call_analyzed"
EVENT_TRANSCRIPT = "transcript_updated"
EVENT_TRANSFER_STARTED = "transfer_started"
EVENT_TRANSFER_BRIDGED = "transfer_bridged"
EVENT_TRANSFER_CANCELLED = "transfer_cancelled"
EVENT_TRANSFER_ENDED = "transfer_ended"
EVENT_UNKNOWN = "unknown"

TRANSFER_EVENTS = {
    EVENT_TRANSFER_STARTED: "started",
    EVENT_TRANSFER_BRIDGED: "bridged",
    EVENT_TRANSFER_CANCELLED: "cancelled",
    EVENT_TRANSFER_ENDED: "ended",
}


@dataclass
class VoiceEvent:
    """One lifecycle event, in EvoSys's vocabulary rather than a vendor's."""
    kind: str
    provider_call_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    provider_status: Optional[str] = None
    disconnect_reason: Optional[str] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    duration_seconds: Optional[int] = None

    transcript: Optional[str] = None
    recording_url: Optional[str] = None
    summary: Optional[str] = None
    analysis: Optional[Dict[str, Any]] = None

    transfer_destination: Optional[str] = None

    # Business outcomes the post-call analysis asserted. All optional: absent
    # means "the analysis did not say", which is NOT the same as False.
    reached_person: Optional[bool] = None
    voicemail: Optional[bool] = None
    opted_out: Optional[bool] = None
    callback_requested: Optional[bool] = None
    callback_at: Optional[datetime] = None
    appointment_booked: Optional[bool] = None
    wrong_number: Optional[bool] = None
    interested: Optional[bool] = None

    raw: Dict[str, Any] = field(default_factory=dict)


# ── Provider interfaces ──────────────────────────────────────────────────────

class SmsProvider:
    """Send a text message. Deliberately tiny.

    This exists so voice and SMS are addressed the same way by the control
    plane — NOT to re-abstract Twilio SMS, which already works and whose
    per-account credential resolution and fail-closed webhook validation are
    authoritative. The Twilio implementation wraps the existing service; it
    does not reimplement it.
    """

    key: str = "base"

    def is_ready(self) -> Tuple[bool, Optional[str]]:
        raise NotImplementedError

    def send(self, req: SmsRequest) -> SmsResult:
        raise NotImplementedError


class VoiceProvider:
    """Place a call and interpret that vendor's webhooks.

    Four methods, chosen as the minimum the File Check agent needs. Anything a
    second provider would do differently belongs behind these; anything only
    one vendor has does not belong in this interface at all.
    """

    key: str = "base"

    def is_ready(self) -> Tuple[bool, Optional[str]]:
        """(usable?, why not). Never raises — a missing API key is a normal
        reportable state, not an exception."""
        raise NotImplementedError

    def start_call(self, req: VoiceCallRequest) -> VoiceCallResult:
        raise NotImplementedError

    def verify_webhook(self, raw_body: bytes, signature: str,
                       now: Optional[datetime] = None) -> bool:
        """Authenticate a webhook. MUST receive the RAW body bytes.

        Returns a bool rather than raising, so the router owns the single
        403-and-stop path and a verification bug can never be mistaken for a
        routing error.
        """
        raise NotImplementedError

    def parse_event(self, payload: Dict[str, Any]) -> VoiceEvent:
        """Vendor payload → `VoiceEvent`. Never raises; an unrecognised shape
        becomes `EVENT_UNKNOWN`, which the router ignores without mutating."""
        raise NotImplementedError
