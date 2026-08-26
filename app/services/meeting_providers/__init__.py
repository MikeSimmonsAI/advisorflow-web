"""
The video meeting provider registry.

The orchestrator never names Zoom. It asks for "the provider for this brand"
and gets something satisfying the MeetingProvider interface. That is what makes
"add Teams later" a new class plus a vocabulary entry, rather than a rewrite of
the appointment system.

RESOLUTION IS PER BRAND, and that is the multi-brand requirement made
structural: a BookaBoost meeting resolves BookaBoost's Zoom config, or none.
There is no global "the Zoom account" anywhere in this layer.

Returns None when a brand has no usable provider — deliberately unlike the
calendar registry, which always returns something. There is no meaningful
fallback for video: if we cannot create a Zoom room, the honest outcome is an
appointment with no video link and a visible reason, not a pretend meeting.
"""
import logging
from typing import Optional

from app.services.meeting_providers.base import (
    MeetingProvider, MeetingRequest, MeetingResult,
)

log = logging.getLogger(__name__)

PROVIDER_ZOOM = "zoom"

# What is actually implemented. Teams and Google Meet are named in the model
# vocabulary so the data shape is stable, but they are NOT listed here — a
# meeting type asking for one resolves to nothing rather than silently
# producing a Zoom room under another provider's name.
IMPLEMENTED = (PROVIDER_ZOOM,)

DEFAULT_PROVIDER = PROVIDER_ZOOM

# Test seam. A test registers a fake here and every call site picks it up, with
# no `if testing:` branch in production code.
_OVERRIDES = {}


def register_provider(key: str, factory) -> None:
    """Install a factory for `key`. `factory(config)` returns a MeetingProvider."""
    _OVERRIDES[key] = factory


def reset_providers() -> None:
    """Remove every override. A test that registers a fake MUST call this in
    teardown or it leaks into every test that follows."""
    _OVERRIDES.clear()


def _build(key: str, config=None) -> Optional[MeetingProvider]:
    if key in _OVERRIDES:
        return _OVERRIDES[key](config)
    try:
        if key == PROVIDER_ZOOM:
            from app.services.meeting_providers.zoom import ZoomProvider
            return ZoomProvider(config)
    except Exception:
        # A vendor library that will not import must not take the app with it.
        log.exception("meeting provider %s failed to load", key)
        return None
    return None


def brand_config(db, brand_sales_org_id: str, provider: str):
    """The brand's config row for a provider, or None (env-var fallback)."""
    if db is None or not brand_sales_org_id:
        return None
    try:
        from app.models.meeting_models import MeetingProviderConfig
        return (db.query(MeetingProviderConfig)
                .filter(MeetingProviderConfig.brand_sales_org_id == brand_sales_org_id,
                        MeetingProviderConfig.provider == provider,
                        MeetingProviderConfig.is_active.is_(True))
                .first())
    except Exception:
        log.exception("could not read meeting provider config for brand %s",
                      brand_sales_org_id)
        return None


def resolve_provider_key(meeting_type=None) -> Optional[str]:
    """Which provider a meeting type wants, or None for no video.

    `requires_video` is the gate. A type that has not asked for video never
    gets a room — that is what stops an internal pipeline review from burning
    a concurrent-meeting slot and cluttering the host's Zoom account.
    """
    if meeting_type is None:
        return None
    if not getattr(meeting_type, "requires_video", False):
        return None
    key = getattr(meeting_type, "video_provider", None) or DEFAULT_PROVIDER
    if key not in IMPLEMENTED:
        # Named but not built. Returning None is the honest answer; inventing a
        # Zoom room for a type that asked for Teams would be worse.
        log.warning("meeting type asked for unimplemented provider %r", key)
        return None
    return key


def get_provider(db, brand_sales_org_id: str, key: str = None,
                 meeting_type=None) -> Optional[MeetingProvider]:
    """The provider for this brand, or None if there is no usable one.

    None is a legitimate, expected answer. Callers must handle it as "this
    appointment has no video meeting" rather than as an error.
    """
    key = key or resolve_provider_key(meeting_type)
    if not key:
        return None
    provider = _build(key, brand_config(db, brand_sales_org_id, key))
    if provider is None:
        return None
    ready, _reason = provider.is_ready()
    if not ready:
        # Not configured for this brand. The caller records the reason on the
        # appointment so the UI can say WHY there is no link.
        return provider   # returned anyway so the caller can read is_ready()
    return provider


__all__ = [
    "MeetingProvider", "MeetingRequest", "MeetingResult",
    "PROVIDER_ZOOM", "IMPLEMENTED", "DEFAULT_PROVIDER",
    "get_provider", "resolve_provider_key", "brand_config",
    "register_provider", "reset_providers",
]
