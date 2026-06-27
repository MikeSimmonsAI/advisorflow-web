"""
Tests for app/services/feature_flags_service.py - the generic per-advisor
feature toggle system. Mike's explicit ask was for the "bulletproof"
option: named toggles validated against a real registry, never
arbitrary freeform text that could silently fail with a typo.

KNOWN_FEATURE_FLAGS is empty in production right now (no lightweight
feature exists yet that needs this kind of gate) - these tests use a
monkeypatched registry to exercise the actual validation logic without
needing a real flag to exist first.
"""

import pytest
from app.services import feature_flags_service as ffs


class _FakeUser:
    def __init__(self, feature_flags=None):
        self.feature_flags = feature_flags


def test_get_enabled_flags_empty_when_none(monkeypatch):
    user = _FakeUser(feature_flags=None)
    assert ffs.get_enabled_flags(user) == set()


def test_get_enabled_flags_parses_comma_separated_string():
    user = _FakeUser(feature_flags="early_access_reports,beta_dashboard")
    assert ffs.get_enabled_flags(user) == {"early_access_reports", "beta_dashboard"}


def test_get_enabled_flags_strips_whitespace_and_ignores_blank_entries():
    user = _FakeUser(feature_flags=" early_access_reports , , beta_dashboard ")
    assert ffs.get_enabled_flags(user) == {"early_access_reports", "beta_dashboard"}


def test_set_feature_flags_rejects_unknown_flag_name(monkeypatch):
    monkeypatch.setitem(ffs.KNOWN_FEATURE_FLAGS, "early_access_reports", "Early access to the new Reports layout")
    user = _FakeUser()

    with pytest.raises(ValueError, match="not_a_real_flag"):
        ffs.set_feature_flags(user, ["early_access_reports", "not_a_real_flag"])


def test_set_feature_flags_accepts_known_flag_and_persists_as_string(monkeypatch):
    monkeypatch.setitem(ffs.KNOWN_FEATURE_FLAGS, "early_access_reports", "Early access to the new Reports layout")
    user = _FakeUser()

    ffs.set_feature_flags(user, ["early_access_reports"])

    assert user.feature_flags == "early_access_reports"


def test_set_feature_flags_with_empty_list_clears_to_none(monkeypatch):
    monkeypatch.setitem(ffs.KNOWN_FEATURE_FLAGS, "early_access_reports", "desc")
    user = _FakeUser(feature_flags="early_access_reports")

    ffs.set_feature_flags(user, [])

    assert user.feature_flags is None


def test_has_feature_flag_true_when_enabled_and_known(monkeypatch):
    monkeypatch.setitem(ffs.KNOWN_FEATURE_FLAGS, "early_access_reports", "desc")
    user = _FakeUser(feature_flags="early_access_reports")

    assert ffs.has_feature_flag(user, "early_access_reports") is True


def test_has_feature_flag_false_when_not_enabled(monkeypatch):
    monkeypatch.setitem(ffs.KNOWN_FEATURE_FLAGS, "early_access_reports", "desc")
    user = _FakeUser(feature_flags=None)

    assert ffs.has_feature_flag(user, "early_access_reports") is False


def test_has_feature_flag_false_even_if_stored_when_flag_removed_from_registry(monkeypatch):
    """
    The actual 'bulletproof' guarantee: even if a user's stored string
    somehow contains a flag name that's no longer in the registry (e.g.
    a flag was retired), has_feature_flag must never trust stored data
    blindly - it checks against the live registry every time.
    """
    user = _FakeUser(feature_flags="some_retired_flag")
    assert ffs.has_feature_flag(user, "some_retired_flag") is False
