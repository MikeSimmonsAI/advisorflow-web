"""
SCHED-05 — calendar_provider fail-closed resolution

The fix: when a user has MULTIPLE external calendar providers connected
(e.g., both Microsoft and Google) but has NOT explicitly set
`calendar_provider` on their account or organization, `resolve_provider_key`
must NOT silently pick by PREFERENCE order. It must return PROVIDER_ICS so
the booking surfaces as calendar_unavailable rather than writing to whichever
calendar happens to be listed first.

Why this matters: the maintenance router's god_maintenance_router.py documents
the exact failure mode — a booking written before the provider fix went to
Outlook because Microsoft was first in the PREFERENCE tuple, even though the
advisor connected Google. Cancelling it through the Google client 404'd and
reported "already deleted" while the event sat on the real Outlook calendar.

These tests prove the fixed `resolve_provider_key` does NOT exhibit that
failure.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.calendar_providers import (
    PROVIDER_GOOGLE,
    PROVIDER_ICS,
    PROVIDER_MICROSOFT,
    resolve_provider_key,
)


# ─── helpers ──────────────────────────────────────────────────────────────────

def _user(calendar_provider=None, org_id="org-1"):
    u = MagicMock()
    u.id = "user-test"
    u.calendar_provider = calendar_provider
    u.organization_id = org_id
    u.microsoft_oauth_refresh_token_encrypted = None
    u.google_oauth_refresh_token_encrypted = None
    return u


def _connection(provider):
    c = MagicMock()
    c.provider = provider
    c.calendar_scope_ok = True
    c.is_connected = True
    return c


# ─── SCHED-05: the core fail-closed assertion ─────────────────────────────────

class TestFailClosedWhenUnconfigured:

    def test_both_connected_unconfigured_returns_ics(self):
        """
        THE SCHED-05 PROOF: if Microsoft and Google are BOTH connected and
        calendar_provider is null, resolve_provider_key must return ICS, not
        silently pick Microsoft via PREFERENCE order.
        """
        user = _user(calendar_provider=None)
        live = {
            PROVIDER_MICROSOFT: _connection(PROVIDER_MICROSOFT),
            PROVIDER_GOOGLE:    _connection(PROVIDER_GOOGLE),
        }

        with patch("app.services.calendar_providers._live_connections",
                   return_value=live), \
             patch("app.services.calendar_providers.configured_provider_key",
                   return_value=(None, None)):
            result = resolve_provider_key(None, user)

        assert result == PROVIDER_ICS, (
            "Two external providers connected, none configured — must fail closed "
            "to ICS, not silently pick %r via PREFERENCE order" % PROVIDER_MICROSOFT
        )

    def test_both_connected_unconfigured_does_not_return_microsoft(self):
        """Complementary assertion: the old silent winner must not be returned."""
        user = _user(calendar_provider=None)
        live = {
            PROVIDER_MICROSOFT: _connection(PROVIDER_MICROSOFT),
            PROVIDER_GOOGLE:    _connection(PROVIDER_GOOGLE),
        }

        with patch("app.services.calendar_providers._live_connections",
                   return_value=live), \
             patch("app.services.calendar_providers.configured_provider_key",
                   return_value=(None, None)):
            result = resolve_provider_key(None, user)

        assert result != PROVIDER_MICROSOFT, (
            "Microsoft must not win silently when both providers are connected "
            "but calendar_provider is unset"
        )

    def test_both_connected_unconfigured_does_not_return_google(self):
        """Google must not win silently either."""
        user = _user(calendar_provider=None)
        live = {
            PROVIDER_MICROSOFT: _connection(PROVIDER_MICROSOFT),
            PROVIDER_GOOGLE:    _connection(PROVIDER_GOOGLE),
        }

        with patch("app.services.calendar_providers._live_connections",
                   return_value=live), \
             patch("app.services.calendar_providers.configured_provider_key",
                   return_value=(None, None)):
            result = resolve_provider_key(None, user)

        assert result != PROVIDER_GOOGLE, (
            "Google must not win silently when both providers are connected "
            "but calendar_provider is unset"
        )


# ─── single-provider cases (should still work, no ambiguity) ──────────────────

class TestSingleProviderUnconfigured:

    def test_only_microsoft_connected_returns_microsoft(self):
        user = _user(calendar_provider=None)
        live = {PROVIDER_MICROSOFT: _connection(PROVIDER_MICROSOFT)}

        with patch("app.services.calendar_providers._live_connections",
                   return_value=live), \
             patch("app.services.calendar_providers.configured_provider_key",
                   return_value=(None, None)):
            result = resolve_provider_key(None, user)

        assert result == PROVIDER_MICROSOFT

    def test_only_google_connected_returns_google(self):
        user = _user(calendar_provider=None)
        live = {PROVIDER_GOOGLE: _connection(PROVIDER_GOOGLE)}

        with patch("app.services.calendar_providers._live_connections",
                   return_value=live), \
             patch("app.services.calendar_providers.configured_provider_key",
                   return_value=(None, None)):
            result = resolve_provider_key(None, user)

        assert result == PROVIDER_GOOGLE

    def test_no_providers_connected_returns_ics(self):
        user = _user(calendar_provider=None)

        with patch("app.services.calendar_providers._live_connections",
                   return_value={}), \
             patch("app.services.calendar_providers.configured_provider_key",
                   return_value=(None, None)):
            result = resolve_provider_key(None, user)

        assert result == PROVIDER_ICS


# ─── explicit configuration always wins ───────────────────────────────────────

class TestExplicitConfigurationWins:

    def test_configured_microsoft_wins_even_when_google_connected(self):
        """
        If the advisor explicitly chose Microsoft, return Microsoft — even if
        Google is the only live connection. The configuration is the decision;
        the registry handles the fallback if the configured provider is broken.
        """
        user = _user(calendar_provider="microsoft")
        live = {PROVIDER_GOOGLE: _connection(PROVIDER_GOOGLE)}

        with patch("app.services.calendar_providers._live_connections",
                   return_value=live), \
             patch("app.services.calendar_providers.configured_provider_key",
                   return_value=(PROVIDER_MICROSOFT, "advisor")):
            result = resolve_provider_key(None, user)

        assert result == PROVIDER_MICROSOFT

    def test_configured_google_wins_even_when_microsoft_connected(self):
        user = _user(calendar_provider="google")
        live = {PROVIDER_MICROSOFT: _connection(PROVIDER_MICROSOFT)}

        with patch("app.services.calendar_providers._live_connections",
                   return_value=live), \
             patch("app.services.calendar_providers.configured_provider_key",
                   return_value=(PROVIDER_GOOGLE, "advisor")):
            result = resolve_provider_key(None, user)

        assert result == PROVIDER_GOOGLE

    def test_configured_microsoft_wins_when_both_connected(self):
        """Explicit choice beats fail-closed: both connected + configured → use configured."""
        user = _user(calendar_provider="microsoft")
        live = {
            PROVIDER_MICROSOFT: _connection(PROVIDER_MICROSOFT),
            PROVIDER_GOOGLE:    _connection(PROVIDER_GOOGLE),
        }

        with patch("app.services.calendar_providers._live_connections",
                   return_value=live), \
             patch("app.services.calendar_providers.configured_provider_key",
                   return_value=(PROVIDER_MICROSOFT, "advisor")):
            result = resolve_provider_key(None, user)

        assert result == PROVIDER_MICROSOFT

    def test_org_level_configuration_is_respected(self):
        """Organization-level `calendar_provider` counts as configured."""
        user = _user(calendar_provider=None)  # not on the user row
        live = {
            PROVIDER_MICROSOFT: _connection(PROVIDER_MICROSOFT),
            PROVIDER_GOOGLE:    _connection(PROVIDER_GOOGLE),
        }

        # org has it set → configured_provider_key returns (google, "organization")
        with patch("app.services.calendar_providers._live_connections",
                   return_value=live), \
             patch("app.services.calendar_providers.configured_provider_key",
                   return_value=(PROVIDER_GOOGLE, "organization")):
            result = resolve_provider_key(None, user)

        assert result == PROVIDER_GOOGLE


# ─── prefer= override (used by cancellation path) ─────────────────────────────

class TestPreferOverride:

    def test_prefer_microsoft_bypasses_fail_closed(self):
        """
        The `prefer` argument is used by cancellation to replay the key that
        was stored at booking time. It must bypass everything — the stored key
        IS the decision.
        """
        user = _user(calendar_provider=None)
        live = {
            PROVIDER_MICROSOFT: _connection(PROVIDER_MICROSOFT),
            PROVIDER_GOOGLE:    _connection(PROVIDER_GOOGLE),
        }

        with patch("app.services.calendar_providers._live_connections",
                   return_value=live), \
             patch("app.services.calendar_providers.configured_provider_key",
                   return_value=(None, None)):
            result = resolve_provider_key(None, user, prefer=PROVIDER_MICROSOFT)

        assert result == PROVIDER_MICROSOFT

    def test_prefer_google_bypasses_fail_closed(self):
        user = _user(calendar_provider=None)
        live = {
            PROVIDER_MICROSOFT: _connection(PROVIDER_MICROSOFT),
            PROVIDER_GOOGLE:    _connection(PROVIDER_GOOGLE),
        }

        with patch("app.services.calendar_providers._live_connections",
                   return_value=live), \
             patch("app.services.calendar_providers.configured_provider_key",
                   return_value=(None, None)):
            result = resolve_provider_key(None, user, prefer=PROVIDER_GOOGLE)

        assert result == PROVIDER_GOOGLE

    def test_prefer_ics_bypasses_everything(self):
        user = _user(calendar_provider="microsoft")

        with patch("app.services.calendar_providers._live_connections",
                   return_value={}), \
             patch("app.services.calendar_providers.configured_provider_key",
                   return_value=(PROVIDER_MICROSOFT, "advisor")):
            result = resolve_provider_key(None, user, prefer=PROVIDER_ICS)

        assert result == PROVIDER_ICS
