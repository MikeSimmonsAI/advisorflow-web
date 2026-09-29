"""END-TO-END ORGANIZATION PROVISIONING — through the EXISTING endpoints only.

Brand → Organization → Vertical Blueprint → Core Required → Vertical Required
→ Optional → Workspace → User → Role → Effective Entitlements → Activation
Readiness → Login / Access, for a new WHOLESALE org and a new ENERGY org, in
the isolated test DB.

Every step is an HTTP call an operator makes today:
  POST /god/customers                     create org (+ primary location)
  GET  /god/customers/{id}/blueprint      evaluate; returns apply_keys
  PUT  /god/customers/{id}/features       apply blueprint features (audited)
  POST /god/customers/{id}/users          add admin → one-time setup link
  POST /auth/staff-activation/accept      the person sets a password
  POST /auth/login                        they sign in
  GET  /god/customers/{id}/control-center readiness reflects all of it
  POST /god/customers/{id}/activate       go live

NO SENDS. SMS / MMS / email senders are replaced with recorders that fail the
test if anything calls them; the provisioning path must not send anything.
Test data lives in the per-test in-memory DB and is discarded with it; the
audit rows each step wrote are asserted before that happens.
"""

import itertools
from urllib.parse import parse_qs, urlparse

import pytest

from app.models.models import AuditLogEntry, Lead, Platform, User
from app.services import entitlements
from app.services import org_blueprints as ob
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)
PASSWORD = "Str0ng-Test-Passphrase!"


def _h(db, u):
    return {"Authorization": "Bearer " + create_access_token(u, db)}


@pytest.fixture()
def sends(monkeypatch):
    """Record (and refuse) every outbound send path."""
    calls = []

    def _rec(name):
        def _f(*a, **k):
            calls.append(name)
            raise AssertionError("provisioning attempted to send: %s" % name)
        return _f

    from app.services import email_service, sms_service
    for mod, names in ((sms_service, ("send_sms", "send_mms", "send_batch")),
                       (email_service, ("send_email", "send_email_via_provider",
                                        "send_email_to_lead", "send_email_batch"))):
        for n in names:
            if hasattr(mod, n):
                monkeypatch.setattr(mod, n, _rec(n))
    return calls


@pytest.fixture()
def world(db_session):
    brand = Platform(name="E2E Brand %d" % next(_SEQ), slug="e2e-brand-%d" % next(_SEQ))
    db_session.add(brand)
    db_session.commit()
    god = User(organization_id=None, email="e2e-god-%d@example.com" % next(_SEQ),
               password_hash=hash_password("x"), full_name="Platform Owner",
               role="god_admin", must_change_password=False, is_active=True)
    db_session.add(god)
    db_session.commit()
    return dict(brand=brand, god=god)


def _items(payload):
    return {i["key"]: i for i in payload["readiness"]["items"]}


def _provision(client, db, world, *, name, industry, blueprint):
    h = _h(db, world["god"])

    # Brand → Organization (+ primary location = the workspace's first site)
    r = client.post("/god/customers", headers=h, json={
        "name": name, "platform_id": world["brand"].id, "industry": industry,
        "plan": "trial",
        "primary_location": {"name": "Head Office", "address_line1": "1 Test Way",
                             "city": "Dallas", "state": "TX",
                             "timezone": "America/Chicago"}})
    assert r.status_code == 201, r.text
    org_id = r.json()["customer"]["id"]
    assert r.json()["launch"]["invitation_sent"] is False
    base = "/god/customers/%s" % org_id

    # Fresh org: nothing enabled, nobody can sign in.
    cc = client.get(base + "/control-center", headers=h).json()
    assert cc["enabled_tools"]["enabled_count"] == 0
    assert _items(cc)["users"]["status"] == ob.NEEDS_SETUP
    assert _items(cc)["primary_location"]["status"] == ob.CONFIGURED
    assert cc["readiness"]["can_activate"] is False

    # Vertical Blueprint → Core Required / Vertical Required evaluated
    bp = client.get(base + "/blueprint?key=" + blueprint, headers=h).json()
    assert bp["blueprint"]["key"] == blueprint
    missing = set(bp["features"]["missing_required"])
    assert set(ob.CORE_REQUIRED) <= missing
    apply_keys = bp["features"]["apply_keys"]

    # Apply the blueprint through the existing, audited features endpoint.
    r = client.put(base + "/features", headers=h, json={"enabled": apply_keys})
    assert r.status_code == 200, r.text
    assert r.json()["dependency_gaps"] == []

    bp = client.get(base + "/blueprint", headers=h).json()
    assert bp["blueprint"]["key"] == blueprint              # now auto-selected
    assert bp["features"]["missing_required"] == []
    for tier in ("core_required", "vertical_required"):
        assert all(f["status"] == ob.CONFIGURED for f in bp["features"][tier])
    # Optional stays off — not counted, not enabled.
    assert all(not f["enabled"] for f in bp["features"]["optional"])

    # User → Role (org_admin) with a one-time setup link. Nothing is sent.
    email = "admin-%d@e2e.example.com" % next(_SEQ)
    r = client.post(base + "/users", headers=h, json={
        "email": email, "full_name": "Org Admin", "role": "org_admin"})
    assert r.status_code == 201, r.text
    setup_url = r.json()["setup_url"]
    assert setup_url
    token = parse_qs(urlparse(setup_url).query)["token"][0]

    cc = client.get(base + "/control-center", headers=h).json()
    person = next(p for p in cc["people"] if p["email"] == email)
    assert person["role"] == "org_admin"
    assert person["invitation"]["status"] == "pending"
    assert _items(cc)["users"]["status"] == ob.PARTIAL

    # Login / Access
    r = client.post("/auth/staff-activation/accept",
                    json={"token": token, "new_password": PASSWORD})
    assert r.status_code == 200, r.text
    r = client.post("/auth/login", data={"username": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    user_token = r.json()["access_token"]

    cc = client.get(base + "/control-center", headers=h).json()
    assert _items(cc)["users"]["status"] == ob.CONFIGURED
    person = next(p for p in cc["people"] if p["email"] == email)
    assert person["invitation"]["status"] == "accepted"
    assert person["last_login_at"]

    # Effective entitlements: every applied key is effective for the org.
    from app.models.models import Organization
    org = db.query(Organization).filter(Organization.id == org_id).first()
    db.refresh(org)
    for k in apply_keys:
        assert entitlements.org_has_feature(org, k)

    # Activation readiness → activate
    assert cc["readiness"]["blockers"] == []
    r = client.post(base + "/activate", headers=h, json={"acknowledge_warnings": True})
    assert r.status_code == 200, r.text

    # Audit evidence of every step.
    actions = {a for (a,) in db.query(AuditLogEntry.action)
               .filter(AuditLogEntry.organization_id == org_id).all()}
    assert {"customer.created", "customer.location_created", "customer.features_set",
            "customer.user_added", "customer.activated"} <= actions

    return dict(org_id=org_id, email=email,
                user_headers={"Authorization": "Bearer " + user_token},
                apply_keys=apply_keys)


def test_provision_wholesale_and_energy_end_to_end(client, db_session, world, sends):
    ws = _provision(client, db_session, world, name="E2E Wholesale Co",
                    industry="wholesale_real_estate", blueprint="wholesale_real_estate")
    en = _provision(client, db_session, world, name="E2E Energy Co",
                    industry="energy", blueprint="energy")

    assert "wholesale_real_estate" in ws["apply_keys"]
    assert "wholesale_real_estate" not in en["apply_keys"]

    # Access follows entitlement: the wholesale admin reaches the wholesale
    # module; the energy admin is refused it (402) — it was never granted.
    r = client.get("/wholesale/settings", headers=ws["user_headers"])
    assert r.status_code == 200, r.text
    r = client.get("/wholesale/settings", headers=en["user_headers"])
    assert r.status_code == 402, r.text
    assert client.get("/leads/", headers=en["user_headers"]).status_code == 200

    # Tenant isolation: neither admin sees the other's lead; neither can
    # reach God endpoints; control-center payloads do not mix.
    ws_admin = db_session.query(User).filter(User.email == ws["email"]).first()
    lead = Lead(organization_id=ws["org_id"], assigned_to_id=ws_admin.id,
                first_name="Iso", last_name="Lation", phone="+15555550199")
    db_session.add(lead)
    db_session.commit()
    assert client.get("/leads/%s" % lead.id, headers=en["user_headers"]).status_code == 404
    assert client.get("/leads/%s" % lead.id, headers=ws["user_headers"]).status_code == 200
    r = client.get("/god/customers/%s/control-center" % ws["org_id"],
                   headers=en["user_headers"])
    assert r.status_code in (401, 403)

    god_h = _h(db_session, world["god"])
    cc_en = client.get("/god/customers/%s/control-center" % en["org_id"], headers=god_h).json()
    assert ws["email"] not in {p["email"] for p in cc_en["people"]}
    assert cc_en["metrics"]["leads"]["total"] == 0
    cc_ws = client.get("/god/customers/%s/control-center" % ws["org_id"], headers=god_h).json()
    assert cc_ws["metrics"]["leads"]["total"] == 1
    assert cc_ws["metrics"]["deals"]["total"] == 0
    assert cc_en["metrics"]["deals"]["total"] is None

    # Required comms are honestly not configured yet — no Twilio, no sender.
    for cc in (cc_en, cc_ws):
        it = _items(cc)
        assert it["sms"]["status"] == ob.NEEDS_SETUP
        assert it["email"]["status"] == ob.NEEDS_SETUP

    assert sends == []
