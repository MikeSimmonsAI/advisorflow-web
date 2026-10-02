"""EVERY PARAMETERLESS WRITE ROUTE, EMPTY BODY, ADMIN AND ADVISOR - NO 5xx.

Sessions are not ended mid-sweep (/auth/* is skipped: logout would revoke the
token and turn the rest into 401s). Machine-authenticated integration routes
answer 401, which is correct.

An empty or partial body is what a stale screen, a double-submit of a half
filled form or a client bug sends. The right answer is a 4xx naming the
problem; a 500 is a crash. Outbound is braked (OUTBOUND_EMERGENCY_STOP) and
routes that reach the network or the public internet are skipped.
"""
import os

import pytest
from fastapi.testclient import TestClient

from app.models.models import Lead, Organization, User
from app.services.auth_service import create_access_token, hash_password

SKIP_PREFIXES = ("/docs", "/redoc", "/openapi", "/email-tracking", "/public", "/site-intake",
                 "/webhooks", "/twilio", "/oauth", "/auth/microsoft", "/auth/google",
                 "/calendar/oauth", "/stream", "/events/stream", "/sse", "/sms/webhook",
                 "/voice/webhook", "/voice/status", "/voice/recording", "/voice/inbound",
                 "/voice/twiml", "/billing/webhook", "/auth/", "/mobile/auth", "/sessions")
# The legacy /crm/contacts writer (contacts_router) inserts columns
# (created_by_id, full_name) that only some deployments' crm_contacts has; the
# test schema has the ORM shape. The app uses /crm-native/contacts. Not swept;
# see the handoff (dead-code candidate).
LEGACY_SHAPE = {("POST", "/crm/contacts")}
SKIP_CONTAINS = ("poll-inbox", "system-check", "/sync", "verify", "/test-connection", "/push/test")


def _routes():
    from app.main import app
    out = set()
    for r in app.routes:
        path = getattr(r, "path", "")
        for m in (getattr(r, "methods", None) or set()) & {"POST", "PUT", "PATCH", "DELETE"}:
            if "{" in path or path.startswith(SKIP_PREFIXES) or any(c in path for c in SKIP_CONTAINS):
                continue
            if (m, path) not in LEGACY_SHAPE:
                out.add((m, path))
    return sorted(out)


def _user(db, org, email, role):
    u = User(organization_id=org.id, email=email, password_hash=hash_password("SweepPass123!"),
             full_name=email.split("@")[0], role=role, must_change_password=False, is_active=True)
    db.add(u)
    db.commit()
    return u


@pytest.mark.parametrize("industry", ["insurance", "wholesale_real_estate"])
def test_no_write_route_500s_on_empty_body(db_session, industry, monkeypatch):
    # The brake is installed at app startup, which a bare TestClient does not
    # run; install it here (a pass-through whenever the variable is unset) so
    # no request in this sweep can reach Twilio, Resend, Graph or Retell even
    # on a machine with open network.
    monkeypatch.setenv("OUTBOUND_EMERGENCY_STOP", "1")
    from app.services import outbound_brake
    outbound_brake.install()
    from app.main import app
    from app.deps import get_db
    org = Organization(name="WSweep %s" % industry, slug="wsweep-%s" % industry.replace("_", "-"),
                       plan="standard", industry=industry)
    db_session.add(org)
    db_session.commit()
    admin = _user(db_session, org, "wadmin-%s@sweep.test" % industry, "org_admin")
    adv = _user(db_session, org, "wadv-%s@sweep.test" % industry, "advisor")
    db_session.add(Lead(organization_id=org.id, assigned_to_id=adv.id, first_name="Sweep",
                        last_name="Test", status="new", is_test=True))
    db_session.commit()

    def _db():
        yield db_session
    app.dependency_overrides[get_db] = _db
    client = TestClient(app, raise_server_exceptions=False)
    failures = []
    try:
        for who in (admin, adv):
            h = {"Authorization": "Bearer %s" % create_access_token(who, db_session)}
            for m, path in _routes():
                try:
                    r = client.request(m, path, json={}, headers=h)
                except Exception as exc:                         # noqa: BLE001
                    failures.append((who.role, m, path, "raised %s" % type(exc).__name__))
                    db_session.rollback()
                    continue
                if r.status_code >= 500 and r.status_code != 503:
                    failures.append((who.role, m, path, r.status_code, r.text[:160]))
                    db_session.rollback()
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert not failures, "\n".join(map(str, failures))
