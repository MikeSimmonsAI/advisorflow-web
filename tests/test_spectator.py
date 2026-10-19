"""SPECTATOR (Oct 19): Richard-style view-only SCI login. Reads what a manager
reads; every write - including a hand-made API call - is refused centrally."""
import pytest

from app.models.models import Organization, User
from app.services import spectator
from app.services.workspace_access import grant_workspace_membership
from tests.test_outreach_program import _h, _lead_for, hash_password, program  # noqa: F401 (fixture)


def _login(db, email, *, org_id=None, role="advisor"):
    u = User(organization_id=org_id, email=email, password_hash=hash_password("Pass12345!"),
             full_name=email.split("@")[0].title(), role=role, is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


@pytest.fixture
def spect(program):
    db, org = program["db"], program["org"]
    u = _login(db, "richard.view@example.com")
    grant_workspace_membership(db, u.id, org.id, role="spectator", check_capacity=False)
    return dict(program, user=u, h=dict(_h(db, u), **{"X-Workspace-Id": org.id}))


def test_spectator_sees_the_sci_screens(client, spect, sample_advisor):
    db, org, h = spect["db"], spect["org"], spect["h"]
    _lead_for(db, org, sample_advisor, "L001")
    for path in ("/program/me", "/program/responses", "/program/records", "/program/command/consent",
                 "/program/command/analytics?range=today"):
        r = client.get(path, headers=h)
        assert r.status_code == 200, (path, r.status_code, r.text[:200])
    assert len(client.get("/program/records", headers=h).json()["items"]) >= 1
    lead = db.query(User).first() and _lead_for(db, org, sample_advisor, "L002", phone="2145550102")
    for path in ("/program/command", "/program/command/pipeline", "/program/command/search?q=Ollie",
                 "/program/command/family/%s" % lead.id, "/program/command/analytics/journey/%s" % lead.id,
                 "/program/status"):
        r = client.get(path, headers=h)
        assert r.status_code == 200, (path, r.status_code, r.text[:200])
    assert client.get("/program/status", headers=h).json()["active"] is True


@pytest.mark.parametrize("method,path,body", [
    ("post", "/program/command/restart-texts", {"lead_id": "x", "from_touch": 1}),
    ("patch", "/program/settings", {"customer_identity_locked": False}),
    ("post", "/program/alerts/test-sms", {"message": "hi"}),
    ("post", "/program/records/x/hold", {"on_hold": True}),
    ("post", "/program/setter/eval", {"scenario_id": "s001"}),
    ("post", "/sms/send", {"lead_id": "x", "body": "hello"}),
    ("post", "/admin/users", {"email": "a@b.c"}),
    ("delete", "/leads/x", None),
    ("post", "/program/records/x/location", {"location_id": "y"}),
])
def test_spectator_cannot_write_even_by_direct_api_call(client, spect, method, path, body):
    kw = {"headers": spect["h"]}
    if body is not None:
        kw["json"] = body
    r = getattr(client, method)(path, **kw)
    assert r.status_code == 403 and "View-only" in r.json()["detail"]


def test_spectator_cannot_export_or_reach_providers(client, spect):
    for path in ("/program/aliases.csv", "/program/alerts/sms-check", "/leads/export"):
        r = client.get(path, headers=spect["h"])
        assert r.status_code == 403, path


def test_spectator_writes_change_nothing(client, spect, sample_advisor):
    from app.models.program_models import ProgramSourceRecord
    db, org, h = spect["db"], spect["org"], spect["h"]
    _lead_for(db, org, sample_advisor, "L001")
    rec = db.query(ProgramSourceRecord).filter_by(organization_id=org.id, source_lead_id="L001").one()
    before = rec.on_hold
    assert client.post("/program/records/%s/hold" % rec.id, headers=h, json={"on_hold": True}).status_code == 403
    db.refresh(rec)
    assert rec.on_hold == before


def test_spectator_may_manage_only_their_own_session(spect):
    u = spect["user"]
    db = spect["db"]
    for p in ("/auth/logout", "/auth/refresh", "/auth/change-password", "/auth/staff-activation/accept"):
        assert spectator.refusal(db, u, p, "POST") is None, p
    assert spectator.refusal(db, u, "/auth/anything-else", "POST") is not None


def test_spectator_is_not_an_owner_admin_or_cross_tenant(client, spect):
    db, h = spect["db"], spect["h"]
    other = Organization(name="Other Co", slug="other-co")
    db.add(other)
    db.commit()
    assert client.get("/admin/users", headers=h).status_code == 403
    assert client.get("/god/users", headers=h).status_code == 403
    r = client.get("/program/records", headers=dict(h, **{"X-Workspace-Id": other.id}))
    assert r.status_code in (403, 404)


def test_fence_never_narrows_existing_access(spect, program):
    db, org = program["db"], program["org"]
    # Kerry-style: org_admin of SCI - not a spectator
    kerry = _login(db, "kerry.admin@example.com")
    grant_workspace_membership(db, kerry.id, org.id, role="org_admin", check_capacity=False)
    assert spectator.refusal(db, kerry, "/program/settings", "PATCH") is None
    # a person who is a spectator here but works elsewhere keeps their work
    both = _login(db, "both@example.com")
    home = Organization(name="Home Co", slug="home-co")
    db.add(home)
    db.commit()
    grant_workspace_membership(db, both.id, org.id, role="spectator", check_capacity=False)
    grant_workspace_membership(db, both.id, home.id, role="manager", check_capacity=False)
    assert spectator.refusal(db, both, "/program/settings", "PATCH") is None
    # an ordinary advisor of another tenant is untouched
    adv = _login(db, "adv@example.com", org_id=home.id)
    assert spectator.refusal(db, adv, "/leads/x", "DELETE") is None


def test_spectator_role_is_offered_by_manage_access(program):
    from app.services import access_management
    d = access_management.directory(program["db"])
    assert {"key": "spectator", "label": "Spectator (view only)"} in d["workspace_roles"]
