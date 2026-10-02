"""EVERY PARAMETERLESS GET, EVERY KIND OF USER, AN EMPTY AND A SEEDED WORKSPACE.

The commonest production 500 on this platform has been a screen's GET
assuming data the workspace does not have yet (no location, no campaign, no
settings row) or a role the code did not expect. This walks every GET route
without path parameters as each kind of signed-in user - org admin and
advisor in an insurance, a wholesale and an energy workspace, plus the owner -
and fails on any 5xx except 503 (an integration honestly reporting it is not
configured). 4xx is fine: refusal is a correct answer.
"""
import os

import pytest
from fastapi.testclient import TestClient

from app.models.models import Lead, Organization, User
from app.services.auth_service import create_access_token, hash_password

# Routes that deliberately reach the network or stream; never part of a sweep.
SKIP_PREFIXES = ("/docs", "/redoc", "/openapi", "/email-tracking", "/public", "/site-intake",
                 "/webhooks", "/twilio", "/oauth", "/auth/microsoft", "/auth/google",
                 "/calendar/oauth", "/stream", "/events/stream", "/sse")


# Tables production creates with raw DDL in app/auto_migrate.py (no ORM model),
# so the test schema (create_all) does not have them. Not a production 500.
RAW_DDL_ONLY = {"/case-file/summary/org", "/crm/connections"}


def _get_routes():
    from app.main import app
    out = set()
    for r in app.routes:
        methods = getattr(r, "methods", None) or set()
        path = getattr(r, "path", "")
        if ("GET" in methods and "{" not in path and not path.startswith(SKIP_PREFIXES)
                and path not in RAW_DDL_ONLY):
            out.add(path)
    return sorted(out)


def _user(db, org, email, role):
    u = User(organization_id=org.id if org else None, email=email,
             password_hash=hash_password("SweepPass123!"), full_name=email.split("@")[0],
             role=role, must_change_password=False, is_active=True)
    db.add(u)
    db.commit()
    return u


@pytest.mark.parametrize("industry", ["insurance", "wholesale_real_estate", "energy"])
def test_no_get_route_500s_for_any_user(db_session, industry):
    from app.main import app
    from app.deps import get_db
    from app.services.tier_config_service import seed_default_tier_definitions
    org = Organization(name="Sweep %s" % industry, slug="sweep-%s" % industry.replace("_", "-"),
                       plan="standard", industry=industry)
    db_session.add(org)
    db_session.commit()
    try:
        seed_default_tier_definitions(db_session, org.id, industry=industry)
    except Exception:                                    # noqa: BLE001
        db_session.rollback()
    admin = _user(db_session, org, "admin-%s@sweep.test" % industry, "org_admin")
    adv = _user(db_session, org, "adv-%s@sweep.test" % industry, "advisor")
    god = _user(db_session, None, "owner-%s@sweep.test" % industry, "god_admin")
    db_session.add(Lead(organization_id=org.id, assigned_to_id=adv.id, first_name="Sweep",
                        last_name="Test", status="new", is_test=True))
    db_session.commit()

    def _db():
        yield db_session
    app.dependency_overrides[get_db] = _db
    client = TestClient(app, raise_server_exceptions=False)
    failures = []
    try:
        for who in (admin, adv, god):
            h = {"Authorization": "Bearer %s" % create_access_token(who, db_session)}
            if who is god:
                h["X-Org-Override"] = org.id
            for path in _get_routes():
                r = client.get(path, headers=h)
                if r.status_code >= 500 and r.status_code != 503:   # 503 = honestly not configured
                    failures.append("%s %s -> %s %s" % (who.role, path, r.status_code, r.text[:160]))
                    db_session.rollback()
    finally:
        app.dependency_overrides.clear()
    if os.environ.get("SWEEP_LOG"):
        with open(os.environ["SWEEP_LOG"], "a") as fh:
            fh.write("".join("[%s] %s\n" % (industry, f) for f in failures))
    assert not failures, "\n" + "\n".join(failures)


def test_no_get_route_500s_on_an_unknown_id(db_session):
    """Every GET with path parameters, given ids that do not exist: the answer
    is a 4xx, never a crash."""
    import re
    from app.main import app
    from app.deps import get_db
    org = Organization(name="Sweep ids", slug="sweep-ids", plan="standard", industry="insurance")
    db_session.add(org)
    db_session.commit()
    admin = _user(db_session, org, "admin-ids@sweep.test", "org_admin")
    god = _user(db_session, None, "owner-ids@sweep.test", "god_admin")

    def _db():
        yield db_session
    app.dependency_overrides[get_db] = _db
    client = TestClient(app, raise_server_exceptions=False)
    paths = sorted({r.path for r in app.routes
                    if "GET" in (getattr(r, "methods", None) or set()) and "{" in getattr(r, "path", "")
                    and not r.path.startswith(SKIP_PREFIXES + ("/case-file/", "/crm/connections"))})
    failures = []
    try:
        for who in (admin, god):
            h = {"Authorization": "Bearer %s" % create_access_token(who, db_session)}
            for path in paths:
                url = re.sub(r"\{[^}]+\}", "00000000-0000-0000-0000-000000000000", path)
                r = client.get(url, headers=h)
                if r.status_code >= 500 and r.status_code != 503:
                    failures.append("%s %s -> %s %s" % (who.role, path, r.status_code, r.text[:160]))
                    db_session.rollback()
    finally:
        app.dependency_overrides.clear()
    if os.environ.get("SWEEP_LOG"):
        with open(os.environ["SWEEP_LOG"], "a") as fh:
            fh.write("".join("[ids] %s\n" % f for f in failures))
    assert not failures, "\n" + "\n".join(failures)
