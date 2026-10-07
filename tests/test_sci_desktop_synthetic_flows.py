"""Synthetic end-to-end flows run by scripts/sci_desktop_verify.ps1 (and plain pytest).

Fully local: in-memory SQLite from conftest, Twilio blocked by the autouse
no_real_twilio_calls fixture, no network, no credentials. Covers the
login -> workspace authorization -> suppression path with real routes.
Intake, inbound-email routing and appointments are covered by the existing
suites the desktop script runs alongside (test_universal_intake_api,
test_inbound_mailbox, test_reply_triage_actions, test_pipeline_appointments_cap).

NOT YET EXECUTED: written in an environment where pip/npm installs were
blocked, so these have never run. First desktop run may need small fixes.
"""
import pytest

from app.models.models import Organization, User
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


def test_login_succeeds_and_wrong_password_is_refused(client, sample_advisor):
    assert _login(client, sample_advisor.email).status_code == 200
    assert _login(client, sample_advisor.email, "wrong-password").status_code in (400, 401)


def test_protected_route_requires_a_token(client):
    assert client.get("/compliance/suppression-list").status_code in (401, 403)


def test_suppression_add_is_idempotent_and_listed(client, sample_advisor):
    h = _hdr(client, sample_advisor.email)
    body = {"phone": "2145550142", "reason": "synthetic opt-out"}
    a = client.post("/compliance/suppression-list", json=body, headers=h)
    b = client.post("/compliance/suppression-list", json=body, headers=h)
    assert a.status_code == 201 and b.status_code == 201
    assert a.json()["id"] == b.json()["id"]
    listed = client.get("/compliance/suppression-list", headers=h).json()["entries"]
    assert len(listed) == 1


def test_suppression_does_not_cross_workspaces(client, sample_advisor, other_org_user):
    ha = _hdr(client, sample_advisor.email)
    hb = _hdr(client, other_org_user.email)
    client.post("/compliance/suppression-list", json={"phone": "2145550143", "reason": "org A only"}, headers=ha)
    assert client.get("/compliance/suppression-list", headers=hb).json()["entries"] == []
