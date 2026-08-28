"""
Communications provider registry.

    EvoSys Communications
        |
        +-- SMS      -> TwilioSmsProvider
        |
        +-- Voice    -> RetellVoiceProvider
                        (future: TwilioVoiceProvider, same interface)

Same registry shape as `calendar_providers` and `meeting_providers`:
`_OVERRIDES` test seam, `register_provider` / `reset_providers`, lazy vendor
imports so a missing dependency cannot break app start-up, and a resolver that
returns a usable object rather than None.

WHY VOICE RESOLUTION TAKES A CONFIG ROW, NOT A KEY. The org's
`VoiceAgentConfig` names the provider, the agent and the from-number together.
Passing the row keeps those three from drifting apart, and means adding a
second Retell agent is a row — not a code change. No agent id or phone number
may appear as a literal in application logic.
"""

from __future__ import annotations

import logging
import os
from typing import Callable, Dict, Optional, Tuple

from sqlalchemy.orm import Session

from app.services.comms.base import SmsProvider, VoiceProvider

log = logging.getLogger(__name__)

PROVIDER_TWILIO = "twilio"
PROVIDER_RETELL = "retell"

# Test seam, exactly as the other two registries do it. A gate registers a fake
# under a real key and the code under test never learns the difference.
_SMS_OVERRIDES: Dict[str, Callable[..., SmsProvider]] = {}
_VOICE_OVERRIDES: Dict[str, Callable[..., VoiceProvider]] = {}


def register_sms_provider(key: str, factory: Callable[..., SmsProvider]) -> None:
    _SMS_OVERRIDES[key] = factory


def register_voice_provider(key: str, factory: Callable[..., VoiceProvider]) -> None:
    _VOICE_OVERRIDES[key] = factory


def reset_providers() -> None:
    """Drop every override. Gates call this in teardown so one test's fake
    cannot silently satisfy the next test's assertion."""
    _SMS_OVERRIDES.clear()
    _VOICE_OVERRIDES.clear()


# ── SMS ──────────────────────────────────────────────────────────────────────

def get_sms_provider(db: Session, advisor, key: str = PROVIDER_TWILIO) -> SmsProvider:
    """Resolve the SMS provider for an advisor.

    Twilio is the only implementation and is not going anywhere — this exists
    so the control plane addresses SMS and voice the same way, not to invite a
    rewrite of a working system.
    """
    if key in _SMS_OVERRIDES:
        return _SMS_OVERRIDES[key](db, advisor)
    from app.services.comms.sms.twilio import TwilioSmsProvider   # lazy
    return TwilioSmsProvider(db, advisor)


# ── Voice ────────────────────────────────────────────────────────────────────

def resolve_api_key(config=None) -> Optional[str]:
    """The provider credential, per-org override first, then the platform key.

    Order matters and is deliberate: an organization that brings its own
    provider account must not silently fall back to the platform's, because
    that would place their calls on our bill and our agent. NULL on the row
    means "use the platform key", which is the normal case today.

    Never logged, never returned to a client, never put in an error message.
    """
    if config is not None and getattr(config, "api_key_encrypted", None):
        from app.utils.crypto import decrypt_value
        try:
            got = decrypt_value(config.api_key_encrypted)
            if got:
                return got
        except Exception as exc:                                  # pragma: no cover
            log.error("voice config %s: api key will not decrypt: %s",
                      getattr(config, "id", "?"), exc)
            return None
    return (os.environ.get("RETELL_API_KEY") or "").strip() or None


def get_voice_provider(db: Session, config) -> VoiceProvider:
    """Build the voice provider named by an org's `VoiceAgentConfig` row."""
    key = (getattr(config, "provider", None) or PROVIDER_RETELL).strip()
    if key in _VOICE_OVERRIDES:
        return _VOICE_OVERRIDES[key](db, config)
    if key == PROVIDER_RETELL:
        from app.services.comms.voice.retell import RetellVoiceProvider  # lazy
        return RetellVoiceProvider(api_key=resolve_api_key(config))
    raise ValueError("Unknown voice provider %r" % key)


def voice_provider_for_key(key: str, api_key: Optional[str] = None) -> VoiceProvider:
    """Build a provider from a bare key — used by the webhook receiver, which
    has a stored call row naming its provider but no config in hand yet."""
    if key in _VOICE_OVERRIDES:
        return _VOICE_OVERRIDES[key](None, None)
    if key == PROVIDER_RETELL:
        from app.services.comms.voice.retell import RetellVoiceProvider  # lazy
        return RetellVoiceProvider(api_key=api_key if api_key is not None
                                   else resolve_api_key(None))
    raise ValueError("Unknown voice provider %r" % key)


def active_voice_config(db: Session, organization_id: str,
                        use_case: str = "file_check"):
    """The org's active voice agent for a use case, or None.

    None is a legitimate, expected answer — an org that has not been configured
    for voice yet. Callers treat it as "not eligible", never as an error.
    """
    from app.models.models import VoiceAgentConfig
    return (
        db.query(VoiceAgentConfig)
        .filter(
            VoiceAgentConfig.organization_id == organization_id,
            VoiceAgentConfig.use_case == use_case,
            VoiceAgentConfig.is_active.is_(True),
        )
        .order_by(VoiceAgentConfig.created_at.desc())
        .first()
    )
