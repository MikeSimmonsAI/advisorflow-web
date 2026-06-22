"""
Tests for the advisor system health monitor.

Rebuilt alongside health_router.py per Mike's explicit feedback that the
original version only showed a checkmark/X with no reason and no way to
act on it. These tests cover the new per-integration `integrations` list
(status + reason + settings_path), not just the legacy flat booleans kept
for backward compatibility.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.deps import get_current_user, get_db
from app.routers.health_router import router as health_router


def _make_test_client(db_session, user):
    app = FastAPI()
    app.include_router(health_router)

    def _override_get_db():
        yield db_session

    def _override_get_current_user():
        return user

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user
    return TestClient(app)


def _integration(body, key):
    return next(item for item in body["integrations"] if item["key"] == key)


# --- Twilio: requires all three fields (account_sid, auth_token, phone_number) ---

def test_twilio_disconnected_when_nothing_configured(db_session, sample_advisor):
    sample_advisor.twilio_account_sid = None
    sample_advisor.twilio_auth_token_encrypted = None
    sample_advisor.twilio_phone_number = None
    db_session.commit()

    client = _make_test_client(db_session, sample_advisor)
    body = client.get("/health/advisor-status").json()

    twilio = _integration(body, "twilio")
    assert twilio["connected"] is False
    assert "account SID" in twilio["reason"]
    assert "auth token" in twilio["reason"]
    assert "phone number" in twilio["reason"]
    assert twilio["settings_path"] == "/settings#twilio"
    assert body["twilio_connected"] is False


@pytest.mark.parametrize(
    "sid,token,number,expected_connected",
    [
        (None, None, None, False),
        ("ACxxx", None, None, False),
        ("ACxxx", "enc_token", None, False),
        (None, "enc_token", "+12145551234", False),
        ("ACxxx", None, "+12145551234", False),
        ("ACxxx", "enc_token", "+12145551234", True),
    ],
)
def test_twilio_connected_requires_all_three_fields(db_session, sample_advisor, sid, token, number, expected_connected):
    """
    Regression coverage for the fix: the original check only looked at
    twilio_account_sid. A real send also needs the auth token (used by
    get_twilio_client) and the phone number (used as the from_ field) -
    missing any one of the three means sends fail, so all three must be
    checked, not just the SID.
    """
    sample_advisor.twilio_account_sid = sid
    sample_advisor.twilio_auth_token_encrypted = token
    sample_advisor.twilio_phone_number = number
    db_session.commit()

    client = _make_test_client(db_session, sample_advisor)
    body = client.get("/health/advisor-status").json()

    twilio = _integration(body, "twilio")
    assert twilio["connected"] is expected_connected
    if not expected_connected:
        assert twilio["reason"] is not None
    else:
        assert twilio["reason"] is None


# --- Google Calendar ---

def test_google_calendar_disconnected_has_reason(db_session, sample_advisor):
    sample_advisor.google_calendar_connected = False
    db_session.commit()

    client = _make_test_client(db_session, sample_advisor)
    body = client.get("/health/advisor-status").json()

    google = _integration(body, "google_calendar")
    assert google["connected"] is False
    assert google["reason"]
    assert google["settings_path"] == "/settings#google"


def test_google_calendar_connected_has_no_reason(db_session, sample_advisor):
    sample_advisor.google_calendar_connected = True
    db_session.commit()

    client = _make_test_client(db_session, sample_advisor)
    body = client.get("/health/advisor-status").json()

    google = _integration(body, "google_calendar")
    assert google["connected"] is True
    assert google["reason"] is None


# --- Microsoft 365 ---

def test_microsoft_365_disconnected_has_reason_and_settings_path(db_session, sample_advisor):
    sample_advisor.microsoft_365_connected = False
    db_session.commit()

    client = _make_test_client(db_session, sample_advisor)
    body = client.get("/health/advisor-status").json()

    microsoft = _integration(body, "microsoft_365")
    assert microsoft["connected"] is False
    assert microsoft["reason"]
    assert microsoft["settings_path"] == "/settings#microsoft"


def test_microsoft_365_connected_reflected_correctly(db_session, sample_advisor):
    sample_advisor.microsoft_365_connected = True
    db_session.commit()

    client = _make_test_client(db_session, sample_advisor)
    body = client.get("/health/advisor-status").json()

    microsoft = _integration(body, "microsoft_365")
    assert microsoft["connected"] is True
    assert body["microsoft_365_connected"] is True


# --- AI features (org-wide env var, not per-advisor) ---

def test_ai_features_disconnected_when_no_key_set(db_session, sample_advisor, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    client = _make_test_client(db_session, sample_advisor)
    body = client.get("/health/advisor-status").json()

    ai = _integration(body, "ai_features")
    assert ai["connected"] is False
    assert ai["reason"]
    assert ai["settings_path"] == "/system-health"


def test_ai_features_connected_when_key_present(db_session, sample_advisor, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")

    client = _make_test_client(db_session, sample_advisor)
    body = client.get("/health/advisor-status").json()

    ai = _integration(body, "ai_features")
    assert ai["connected"] is True
    assert ai["reason"] is None


# --- Scoping and legacy boolean fields ---

def test_advisor_health_status_is_scoped_to_logged_in_user(db_session, sample_advisor, second_advisor):
    sample_advisor.twilio_account_sid = None
    sample_advisor.twilio_auth_token_encrypted = None
    sample_advisor.twilio_phone_number = None
    sample_advisor.google_calendar_connected = False
    sample_advisor.microsoft_365_connected = False

    second_advisor.twilio_account_sid = "AC_other"
    second_advisor.twilio_auth_token_encrypted = "enc_other"
    second_advisor.twilio_phone_number = "+12145559999"
    second_advisor.google_calendar_connected = True
    second_advisor.microsoft_365_connected = True
    db_session.commit()

    client = _make_test_client(db_session, sample_advisor)
    body = client.get("/health/advisor-status").json()

    assert body["twilio_connected"] is False
    assert body["google_calendar_connected"] is False
    assert body["microsoft_365_connected"] is False


def test_legacy_flat_booleans_still_present_for_backward_compatibility(db_session, sample_advisor):
    """Kept alongside the new integrations list so anything still reading the flat fields directly doesn't break."""
    client = _make_test_client(db_session, sample_advisor)
    body = client.get("/health/advisor-status").json()

    assert "twilio_connected" in body
    assert "google_calendar_connected" in body
    assert "microsoft_365_connected" in body
    assert "last_cadence_run" in body
    assert "integrations" in body
    assert len(body["integrations"]) == 4
