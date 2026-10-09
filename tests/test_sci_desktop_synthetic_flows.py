"""Synthetic end-to-end flows run by scripts/sci_desktop_verify.ps1 (and plain pytest).

Fully local: in-memory SQLite from conftest, Twilio blocked by the autouse
no_real_twilio_calls fixture, no network, no credentials. Covers the
login -> workspace authorization -> suppression path with real routes, plus
the booking-cancel persistence/duplicate/handoff path.

NOT YET EXECUTED: written in an environment where pip/npm installs were
blocked, so these have never run. Assertions are grounded in route contracts
read from source:
  * auth_router.login: bad credentials -> 401 "Incorrect email or password";
    success -> TokenResponse(access_token, role, full_name, organization_id).
  * OAuth2PasswordBearer: missing/invalid token -> 401.
  * compliance_router suppression: POST -> 201 SuppressionOut, list ->
    {"stats", "entries"}, phone normalised to 11 digits "1XXXXXXXXXX",
    invalid phone -> 422, rows scoped to the caller's organization.
  * calendar_router.cancel_booking: lead-scoped lookup, then
    calendar_service.cancel_calendar_event sets status="cancelled".

Already covered elsewhere (not duplicated here): admin/permanent-DNC/delete
(test_compliance_router.py), cross-org booking-cancel denial
(test_calendar_router.py), replay/terminal/cancel-twice on the sales calendar
(test_calendar_scheduling_attack.py), public intake persistence, repeat
submission, refusal, brand isolation and no-send (test_site_intake.py,
test_intake_capture.py, test_universal_intake_api.py).
"""
import pytest

from app.models.models import BookingLink, Organization, User
from app.services.auth_service import hash_password

PW = "TestPass123!"


@pytest.fixture()
def other_org_user(db_session):
    org = Organization(name="Synthetic Other Org", slug="synthetic-other", plan="standard", industry="funeral")
    db_session.add(org)
    db_session.commit()
    u = User(organization_id=org.id, email="other@synthetic.test", password_hash=hash_password(PW),
             full_name="Other Advisor", role="advisor", must_change_password=False)
    db_session.add(u)
    db_session.commit()
    return u


def _login(client, email, password=PW):
    return client.post("/auth/login", data={"username": email, "password": password})


def _hdr(client, email):
    r = _login(client, email)
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer %s" % r.json()["access_token"]}


def test_login_issues_a_token_that_authorizes_a_protected_route(client, sample_advisor):
    r = _login(client, sample_advisor.email)
    assert r.status_code == 200
    body = r.json()
    assert body["organization_id"] == sample_advisor.organization_id
    assert body["role"] == "advisor"
    h = {"Authorization": "Bearer %s" % body["access_token"]}
    assert client.get("/compliance/suppression-list", headers=h).status_code == 200


def test_wrong_password_and_unknown_user_are_refused_identically(client, sample_advisor):
    bad_pw = _login(client, sample_advisor.email, "wrong-password")
    unknown = _login(client, "nobody@synthetic.test")
    assert bad_pw.status_code == 401 and unknown.status_code == 401
    assert bad_pw.json()["detail"] == unknown.json()["detail"] == "Incorrect email or password"
    assert "access_token" not in bad_pw.json()


def test_protected_route_requires_a_valid_token(client):
    assert client.get("/compliance/suppression-list").status_code == 401
    bad = {"Authorization": "Bearer not-a-real-token"}
    assert client.get("/compliance/suppression-list", headers=bad).status_code == 401


def test_suppression_add_is_idempotent_and_normalised(client, sample_advisor):
    h = _hdr(client, sample_advisor.email)
    a = client.post("/compliance/suppression-list",
                    json={"phone": "(214) 555-0142", "reason": "synthetic opt-out"}, headers=h)
    b = client.post("/compliance/suppression-list",
                    json={"phone": "2145550142", "reason": "synthetic opt-out"}, headers=h)
    assert a.status_code == 201 and b.status_code == 201
    assert a.json()["id"] == b.json()["id"]
    assert a.json()["phone"] == "12145550142"
    entries = client.get("/compliance/suppression-list", headers=h).json()["entries"]
    assert [e["phone"] for e in entries] == ["12145550142"]


def test_suppression_rejects_an_invalid_phone_and_stores_nothing(client, sample_advisor):
    h = _hdr(client, sample_advisor.email)
    r = client.post("/compliance/suppression-list", json={"phone": "12345678", "reason": "x"}, headers=h)
    assert r.status_code == 422
    assert client.get("/compliance/suppression-list", headers=h).json()["entries"] == []


def test_suppression_does_not_cross_workspaces(client, sample_advisor, other_org_user):
    ha = _hdr(client, sample_advisor.email)
    hb = _hdr(client, other_org_user.email)
    created = client.post("/compliance/suppression-list",
                          json={"phone": "2145550143", "reason": "org A only"}, headers=ha).json()
    assert client.get("/compliance/suppression-list", headers=hb).json()["entries"] == []
    # Org B adding the same number gets its own row, not org A's.
    own = client.post("/compliance/suppression-list",
                      json={"phone": "2145550143", "reason": "org B"}, headers=hb)
    assert own.status_code == 201 and own.json()["id"] != created["id"]
    assert len(client.get("/compliance/suppression-list", headers=ha).json()["entries"]) == 1


# -- Appointment / handoff: persistence, duplicate request, no customer send --
# test_calendar_router.py covers cross-org denial but mocks the cancel service,
# so the persisted state and a repeat request were never asserted.

def test_cancel_booking_persists_and_a_repeat_request_is_safe(
        client, db_session, auth_headers, sample_lead, sample_advisor, monkeypatch):
    from app.services import appointment_flow_service
    handoffs = []
    # The customer-message handoff is observed, not executed: nothing is sent.
    monkeypatch.setattr(appointment_flow_service, "on_booking_cancelled",
                        lambda db, lead, user, booking: handoffs.append(booking.id))
    b = BookingLink(lead_id=sample_lead.id, user_id=sample_advisor.id, status="pending")
    db_session.add(b)
    db_session.commit()
    first = client.post("/calendar/cancel-booking/%s" % b.id, headers=auth_headers)
    second = client.post("/calendar/cancel-booking/%s" % b.id, headers=auth_headers)
    assert first.status_code == 200 and first.json()["success"] is True
    assert second.status_code == 200
    db_session.refresh(b)
    assert b.status == "cancelled"
    assert handoffs and set(handoffs) == {b.id}
