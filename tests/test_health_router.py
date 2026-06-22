"""
Tests for the advisor system health monitor.

The endpoint is scoped to the logged-in advisor and reports connection flags from
that advisor's User record only. It stays read-only and does not create scheduler
tracking for cadence runs.
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


@pytest.mark.parametrize(
    "twilio_sid,google_connected,microsoft_connected",
    [
        (None, False, False),
        ("AC_test", False, False),
        (None, True, False),
        (None, False, True),
        ("AC_test", True, False),
        ("AC_test", False, True),
        (None, True, True),
        ("AC_test", True, True),
    ],
)
def test_advisor_health_status_reflects_each_connection_combination(
    db_session,
    sample_advisor,
    twilio_sid,
    google_connected,
    microsoft_connected,
):
    sample_advisor.twilio_account_sid = twilio_sid
    sample_advisor.google_calendar_connected = google_connected
    sample_advisor.microsoft_365_connected = microsoft_connected
    db_session.commit()

    client = _make_test_client(db_session, sample_advisor)
    response = client.get("/health/advisor-status")

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "twilio_connected": bool(twilio_sid),
        "google_calendar_connected": google_connected,
        "microsoft_365_connected": microsoft_connected,
        "last_cadence_run": None,
    }


def test_advisor_health_status_is_scoped_to_logged_in_user(db_session, sample_advisor, second_advisor):
    sample_advisor.twilio_account_sid = None
    sample_advisor.google_calendar_connected = False
    sample_advisor.microsoft_365_connected = False

    second_advisor.twilio_account_sid = "AC_other"
    second_advisor.google_calendar_connected = True
    second_advisor.microsoft_365_connected = True
    db_session.commit()

    client = _make_test_client(db_session, sample_advisor)
    response = client.get("/health/advisor-status")

    assert response.status_code == 200
    assert response.json() == {
        "twilio_connected": False,
        "google_calendar_connected": False,
        "microsoft_365_connected": False,
        "last_cadence_run": None,
    }
