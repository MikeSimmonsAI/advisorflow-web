"""THE CHANNEL CONTRACT — one interface, many providers, no leakage.

WHY PROVIDER-NEUTRAL IS NOT ARCHITECTURE ASTRONOMY HERE. The AI employee asks
for an OPERATION ("send this message to this person"); it does not know what
Twilio is, and it must not, because the day the platform changes SMS provider
is the day every employee that knew would have to be rewritten. An adapter is
the only place a provider's name, credential, status vocabulary or error
shape is allowed to appear.

THE ADAPTER IS A CONVENIENCE, NEVER A GATE. Nothing in this package relies on
an adapter to refuse anything. Authority, eligibility, activation stage,
caps and idempotency are all answered by the orchestrator BEFORE an adapter
is resolved, and the simulated adapter is what a refusal falls back to rather
than what enforces it. An adapter that was the last line of defence would be
a defence one swapped implementation removes.

EVERY ADAPTER REPORTS THE SAME FIVE THINGS: what happened (a normalized
outcome, never the provider's own word), who it was, what reference it gave
back, how long it took, and what it is estimated to have cost. A caller that
had to branch on which adapter answered would not be provider-neutral.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.services.ai_operations import constants as C


@dataclass
class SendRequest:
    """One outbound attempt, described without naming a provider."""

    organization_id: str
    channel: str
    to_address: str
    body: str = ""
    subject: str = ""
    from_address: Optional[str] = None
    lead: Any = None
    sending_user: Any = None
    thread_id: Optional[str] = None
    communication_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    # Voice only.
    purpose: Optional[str] = None
    max_seconds: int = C.DEFAULT_MAX_VOICE_SECONDS
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SendResult:
    """What the adapter did, in this layer's vocabulary rather than the
    provider's."""

    outcome: str = C.P_SIMULATED
    provider: str = "simulated"
    provider_message_id: Optional[str] = None
    provider_status: Optional[str] = None
    error: Optional[str] = None
    denial_code: Optional[str] = None
    duration_ms: Optional[int] = None
    simulated: bool = True
    estimated_cost_usd: float = 0.0
    platform_record_type: Optional[str] = None
    platform_record_id: Optional[str] = None
    voice_disposition: Optional[str] = None
    voice_seconds: Optional[int] = None
    detail: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.outcome in (C.P_ACCEPTED, C.P_DELIVERED, C.P_SIMULATED)

    def as_dict(self) -> Dict:
        return {
            "outcome": self.outcome,
            "provider": self.provider,
            "provider_message_id": self.provider_message_id,
            "provider_status": self.provider_status,
            "error": self.error,
            "denial_code": self.denial_code,
            "duration_ms": self.duration_ms,
            "simulated": self.simulated,
            "estimated_cost_usd": self.estimated_cost_usd,
            "voice_disposition": self.voice_disposition,
            "voice_seconds": self.voice_seconds,
        }


class ChannelAdapter:
    """The interface. Subclasses implement `send` and nothing else.

    `reaches_outside` is declared rather than inferred, and the orchestrator
    reads it to decide whether the activation stage permits this adapter at
    all. A new adapter that forgets to declare it gets the safe default
    (True) and is therefore refused outside the executing stages, which is
    the direction a forgotten declaration should fail in.
    """

    key: str = "base"
    channel: str = ""
    reaches_outside: bool = True

    def send(self, db: Session, req: SendRequest) -> SendResult:
        raise NotImplementedError

    def describe(self) -> Dict:
        return {"key": self.key, "channel": self.channel,
                "reaches_outside": self.reaches_outside}


class AdapterUnavailable(Exception):
    """No adapter could be resolved for a channel. Always a refusal, never a
    fallback to some other channel: a customer who configured email and not
    SMS did not thereby agree to be texted."""

    def __init__(self, channel: str, reason: str):
        self.channel = channel
        self.reason = reason
        super().__init__(reason)
