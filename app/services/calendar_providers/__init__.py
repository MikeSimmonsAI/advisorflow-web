"""
The provider registry.

The orchestrator never names a vendor. It asks this module for "the provider
for this user" and gets back something that satisfies the CalendarProvider
interface. That is what makes the .ics fallback a routing decision instead of a
special case scattered through the booking code.

RESOLUTION ORDER
----------------
1. An explicitly requested provider, if the caller named one.
2. A live CalendarConnection row for this user, preferring Microsoft.
3. A stored refresh token on the user, preferring Microsoft.
4. The .ics email fallback.

Step 4 is why this function never returns None. Every internal participant gets
SOME delivery mechanism, so no one is silently dropped from a meeting because
they never connected a calendar.

IMPORTS ARE LAZY, deliberately. Google's client libraries and httpx are only
touched when a provider of that kind is actually constructed, so a broken or
missing vendor dependency degrades one provider instead of taking down the app
at import time.
"""
import logging
from typing import Optional

from app.services.calendar_providers.base import (
    CalendarProvider, EventPayload, SyncResult, BusyInterval,
)

log = logging.getLogger(__name__)

PROVIDER_MICROSOFT = "microsoft"
PROVIDER_GOOGLE = "google"
PROVIDER_ICS = "ics"

# Preference order when a user has more than one option available. Microsoft
# first because it is the first production provider and the one the team
# actually runs on.
PREFERENCE = (PROVIDER_MICROSOFT, PROVIDER_GOOGLE)

# Test seam. A test registers a fake here and every call site — orchestrator,
# router, availability read — picks it up, WITHOUT any production code carrying
# an `if testing:` branch. Production code that knows it is being tested is
# production code that is not being tested.
_OVERRIDES = {}


def register_provider(key: str, factory) -> None:
    """Install a factory for `key`. `factory(user, connection, org)` must return
    a CalendarProvider. Used by tests to inject a fake."""
    _OVERRIDES[key] = factory


def reset_providers() -> None:
    """Remove every override. A test that registers a fake MUST call this in
    teardown, or it leaks into every test that runs after it."""
    _OVERRIDES.clear()


def _build(key: str, user, connection=None, org=None) -> Optional[CalendarProvider]:
    if key in _OVERRIDES:
        return _OVERRIDES[key](user, connection, org)
    try:
        if key == PROVIDER_MICROSOFT:
            from app.services.calendar_providers.microsoft import MicrosoftCalendarProvider
            return MicrosoftCalendarProvider(user, connection, org)
        if key == PROVIDER_GOOGLE:
            from app.services.calendar_providers.google import GoogleCalendarProvider
            return GoogleCalendarProvider(user, connection, org)
        if key == PROVIDER_ICS:
            from app.services.calendar_providers.ics import IcsEmailProvider
            return IcsEmailProvider(user, connection, org)
    except Exception:
        # A vendor library that will not import must not take the app with it.
        # Returning None lets the caller fall through to the next option, and
        # ultimately to .ics, which depends on nothing but our own email path.
        log.exception("calendar provider %s failed to load", key)
        return None
    return None


def _live_connections(db, user) -> dict:
    """{provider_key: CalendarConnection} for connections that are actually
    usable — connected AND holding a calendar-capable grant.

    `calendar_scope_ok` is checked, not just `is_connected`. A Microsoft user
    who connected before calendar permission was requested has a live token
    that email works with and calendar does not; treating that as connected is
    how a booking ends up reporting success while writing to nothing.
    """
    if db is None or user is None:
        return {}
    try:
        from app.models.calendar_models import CalendarConnection
        rows = (db.query(CalendarConnection)
                .filter(CalendarConnection.user_id == user.id,
                        CalendarConnection.is_connected.is_(True))
                .all())
    except Exception:
        log.exception("could not read calendar connections for user %s",
                      getattr(user, "id", "?"))
        return {}
    return {r.provider: r for r in rows if r.calendar_scope_ok}


def _has_token(user, key: str) -> bool:
    field = ("microsoft_oauth_refresh_token_encrypted" if key == PROVIDER_MICROSOFT
             else "google_oauth_refresh_token_encrypted")
    return bool(getattr(user, field, None))


def resolve_provider_key(db, user, prefer: str = None) -> str:
    """Which provider this user's events should go through. Never returns None
    — the .ics fallback is always the final answer."""
    if prefer in (PROVIDER_MICROSOFT, PROVIDER_GOOGLE, PROVIDER_ICS):
        return prefer

    live = _live_connections(db, user)
    for key in PREFERENCE:
        if key in live:
            return key

    # No connection row, but a token exists — this is a user connected before
    # the connections table existed. Treat the token as the source of truth
    # rather than forcing a pointless reconnect.
    for key in PREFERENCE:
        if _has_token(user, key):
            return key

    return PROVIDER_ICS


def get_provider(db, user, org=None, prefer: str = None) -> CalendarProvider:
    """The provider for this user. ALWAYS returns something usable.

    If the chosen provider cannot be constructed or reports itself unready, this
    falls back to .ics rather than returning a broken object or None. A caller
    should never have to ask "did I get a provider?" — only "did the operation
    succeed?", which SyncResult already answers.
    """
    key = resolve_provider_key(db, user, prefer)
    connection = _live_connections(db, user).get(key)

    provider = _build(key, user, connection, org)
    if provider is not None:
        ready, _reason = provider.is_ready()
        if ready:
            return _stamp(provider, key)

    if key != PROVIDER_ICS:
        fallback = _build(PROVIDER_ICS, user, None, org)
        if fallback is not None:
            return _stamp(fallback, PROVIDER_ICS)

    # Last resort: the base class. Every call on it returns a clean
    # 'unimplemented' failure, so the appointment still survives.
    return _stamp(CalendarProvider(user, None, org), PROVIDER_ICS)


def _stamp(provider, key: str):
    """Record which REGISTRY key this object was resolved as.

    Not the same as the class's own `key`. Callers persist this value and pass
    it back later as `prefer` when cancelling, so it must be something the
    registry can resolve — and it must reflect a fallback that actually
    happened. A provider that reported itself unready and silently became .ics
    must be stored as .ics, or a later cancellation would go looking for the
    event in a calendar that never received it.
    """
    try:
        provider.resolved_key = key
    except Exception:
        pass
    return provider


def is_external_calendar(key: str) -> bool:
    """True for providers we can READ availability from. The .ics fallback
    delivers invitations but has no calendar to read, and conflating the two is
    how a user with no connection appears to be free at every hour of the day
    with the same confidence as one whose calendar we actually checked."""
    return key in (PROVIDER_MICROSOFT, PROVIDER_GOOGLE)


__all__ = [
    "CalendarProvider", "EventPayload", "SyncResult", "BusyInterval",
    "PROVIDER_MICROSOFT", "PROVIDER_GOOGLE", "PROVIDER_ICS", "PREFERENCE",
    "get_provider", "resolve_provider_key", "is_external_calendar",
    "register_provider", "reset_providers",
]
