"""ORGANIZATION CONTROL CENTER + BLUEPRINTS — the payload tells the truth.

Pins:
  1. A fresh organization reads as unconfigured (Needs Setup / Not Ready /
     Not Required) — nothing is green that has not been set up.
  2. Adding a location flips Primary Location; adding an admin flips Users;
     stored Twilio / email columns flip SMS / Email; a live AI deployment
     flips AI Automation. Each from the real row.
  3. Blueprint selection is derived from industry / allow-list (energy,
     wholesale, core), never from an org id; feature tiers evaluate against
     the real entitlement allow-list.
  4. God-only; unknown org 404; the payload never contains another org's rows.
"""

import itertools
import json

import pytest

from app.models.models import Organization, Platform, User, Lead
from app.models.location_models import Location
from app.services import org_blueprints as ob
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


def _h(db, u):
    return {"Authorization": "Bearer " + create_access_token(u, db)}


def _user(db, role="advisor", org_id=None, **kw):
    u = User(organization_id=org_id,
             email=kw.pop("email", None) or "occ%d@example.com" % next(_SEQ),
             password_hash=hash_password("x"), full_name=kw.pop("full_name", "Person"),
             role=role, must_change_password=kw.pop("must_change_password", False),
             is_active=True, **kw)
    db.add(u)
    db.commit()
    return u


def _org(db, plat, industry="generic", features=None, name=None):
    o = Organization(name=name or "Org %d" % next(_SEQ), slug="occ-%d" % next(_SEQ),
                     plan="trial", platform_id=plat.id, industry=industry,
                     enabled_features=json.dumps(features if features is not None else []))
    db.add(o)
    db.commit()
    return o


@pytest.fixture()
def world(db_session):
    plat = Platform(name="Brand %d" % next(_SEQ), slug="brand-occ-%d" % next(_SEQ))
    db_session.add(plat)
    db_session.commit()
    god = _user(db_session, role="god_admin")
    return dict(plat=plat, god=god)


def _cc(client, db, world, org):
    r = client.get("/god/customers/%s/control-center" % org.id, headers=_h(db, world["god"]))
    assert r.status_code == 200, r.text
    return r.json()


def _items(payload):
    return {i["key"]: i for i in payload["readiness"]["items"]}


# ── 1. fresh org ────────────────────────────────────────────────────────────

def test_fresh_org_is_not_configured(client, db_session, world):
    org = _org(db_session, world["plat"])
    p = _cc(client, db_session, world, org)
    it = _items(p)
    assert list(it) == list(ob.ACTIVATION_ITEMS)
    assert it["company"]["status"] == ob.CONFIGURED       # name/slug/brand are real
    assert it["primary_location"]["status"] == ob.NEEDS_SETUP
    assert it["users"]["status"] == ob.NEEDS_SETUP
    # Core blueprint: optional items whose feature is off are Not Required.
    for k in ("booking", "sms", "email", "ai_automation"):
        assert it[k]["status"] == ob.NOT_REQUIRED, (k, it[k])
    assert p["readiness"]["complete"] == 1
    assert p["readiness"]["total"] == 3
    assert p["people"] == [] and p["locations"] == []
    assert p["header"]["primary_contact"] is None
    assert p["header"]["timezone"] is None
    m = p["metrics"]
    assert m["leads"]["total"] == 0 and m["deals"]["total"] is None
    assert m["trends"] is None
    # Incomplete items are actionable.
    assert it["primary_location"]["action"]["tab"] == "locations"
    assert it["users"]["action"]["tab"] == "people"


def test_required_feature_off_is_not_ready(client, db_session, world):
    org = _org(db_session, world["plat"], industry="energy")
    it = _items(_cc(client, db_session, world, org))
    # Energy requires SMS and email; neither feature is enabled.
    assert it["sms"]["status"] == ob.NOT_READY
    assert it["email"]["status"] == ob.NOT_READY
    assert it["sms"]["action"]["tab"] == "entitlements"


# ── 2. real rows flip real items ───────────────────────────────────────────

def test_adding_a_location_flips_primary_location(client, db_session, world):
    org = _org(db_session, world["plat"])
    h = _h(db_session, world["god"])
    r = client.post("/god/customers/%s/locations" % org.id,
                    json={"name": "HQ", "address_line1": "1 Main", "city": "Dallas",
                          "state": "TX", "timezone": "America/Chicago"}, headers=h)
    assert r.status_code == 201, r.text
    p = _cc(client, db_session, world, org)
    assert _items(p)["primary_location"]["status"] == ob.CONFIGURED
    assert p["header"]["timezone"] == "America/Chicago"
    assert p["locations"][0]["booking_route"] == "Default booking route"
    assert p["essentials"]["primary_location"]["name"] == "HQ"


def test_location_without_timezone_is_partial(client, db_session, world):
    org = _org(db_session, world["plat"])
    client.post("/god/customers/%s/locations" % org.id, json={"name": "Somewhere"},
                headers=_h(db_session, world["god"]))
    assert _items(_cc(client, db_session, world, org))["primary_location"]["status"] == ob.PARTIAL


def test_users_partial_then_configured(client, db_session, world):
    org = _org(db_session, world["plat"])
    h = _h(db_session, world["god"])
    r = client.post("/god/customers/%s/users" % org.id,
                    json={"email": "admin%d@example.com" % next(_SEQ),
                          "full_name": "Ada Admin", "role": "org_admin"}, headers=h)
    assert r.status_code == 201, r.text
    p = _cc(client, db_session, world, org)
    assert _items(p)["users"]["status"] == ob.PARTIAL        # never signed in
    person = p["people"][0]
    assert person["invitation"]["status"] == "pending"        # a setup link exists
    assert person["access"]["status"] == "active"
    assert person["workspace"] == org.name

    from datetime import datetime
    u = db_session.query(User).filter(User.id == person["id"]).first()
    u.last_login_at = datetime.utcnow()
    db_session.commit()
    p = _cc(client, db_session, world, org)
    assert _items(p)["users"]["status"] == ob.CONFIGURED
    assert p["people"][0]["invitation"]["status"] == "accepted"


def test_sms_and_email_from_stored_columns(client, db_session, world):
    org = _org(db_session, world["plat"], industry="energy",
               features=["leads", "sms", "email"])
    it = _items(_cc(client, db_session, world, org))
    assert it["sms"]["status"] == ob.NEEDS_SETUP
    assert it["email"]["status"] == ob.NEEDS_SETUP

    org.org_twilio_account_sid = "ACfake"
    org.org_twilio_auth_token_encrypted = "enc"
    org.org_twilio_phone_number = "+15555550100"
    org.org_twilio_number_type = "10dlc"
    org.twilio_a2p_campaign_status = "IN_PROGRESS"
    org.from_email = "ops@example.com"
    db_session.commit()
    it = _items(_cc(client, db_session, world, org))
    assert it["sms"]["status"] == ob.PARTIAL                 # campaign not approved
    assert "IN_PROGRESS" in it["sms"]["reason"]
    assert it["email"]["status"] == ob.PARTIAL               # no org key

    org.twilio_a2p_campaign_status = "VERIFIED"
    org.resend_api_key = "re_fake"
    db_session.commit()
    it = _items(_cc(client, db_session, world, org))
    assert it["sms"]["status"] == ob.CONFIGURED
    assert it["sms"]["detail"]["verified_against_provider"] is False
    assert it["email"]["status"] == ob.CONFIGURED


def test_ai_automation_from_deployment_rows(client, db_session, world):
    from app.models.ai_deployment_models import AIEmployeeDeployment
    org = _org(db_session, world["plat"], industry="wholesale_real_estate",
               features=["leads", "wholesale_real_estate", "ai_assist"])
    it = _items(_cc(client, db_session, world, org))
    assert it["ai_automation"]["status"] == ob.NEEDS_SETUP
    d = AIEmployeeDeployment(organization_id=org.id, template_key="t",
                             provisioning_key="pk-%d" % next(_SEQ), state="configuring")
    db_session.add(d)
    db_session.commit()
    assert _items(_cc(client, db_session, world, org))["ai_automation"]["status"] == ob.PARTIAL
    d.state = "active"
    db_session.commit()
    assert _items(_cc(client, db_session, world, org))["ai_automation"]["status"] == ob.CONFIGURED


def test_metrics_are_real_counts_and_scoped(client, db_session, world):
    org = _org(db_session, world["plat"], features=["leads"])
    other = _org(db_session, world["plat"], features=["leads"])
    adv = _user(db_session, org_id=org.id)
    adv2 = _user(db_session, org_id=other.id)
    for _ in range(3):
        db_session.add(Lead(organization_id=org.id, assigned_to_id=adv.id,
                            first_name="A", last_name="B", phone="+1555%07d" % next(_SEQ)))
    for _ in range(5):
        db_session.add(Lead(organization_id=other.id, assigned_to_id=adv2.id,
                            first_name="C", last_name="D", phone="+1555%07d" % next(_SEQ)))
    db_session.commit()
    p = _cc(client, db_session, world, org)
    assert p["metrics"]["leads"]["total"] == 3
    assert p["metrics"]["leads"]["last_30_days"] == 3
    assert {x["id"] for x in p["people"]} == {adv.id}
    assert adv2.id not in json.dumps(p)


def test_recent_activity_is_the_orgs_audit_log(client, db_session, world):
    org = _org(db_session, world["plat"])
    other = _org(db_session, world["plat"])
    h = _h(db_session, world["god"])
    client.post("/god/customers/%s/locations" % org.id, json={"name": "Mine"}, headers=h)
    client.post("/god/customers/%s/locations" % other.id, json={"name": "Theirs"}, headers=h)
    acts = _cc(client, db_session, world, org)["recent_activity"]
    assert any(a["action"] == "customer.location_created" for a in acts)
    assert all("Theirs" not in json.dumps(a) for a in acts)


def test_administration_split_is_structured(client, db_session, world):
    org = _org(db_session, world["plat"])
    a = _cc(client, db_session, world, org)["administration"]
    assert a["self_manageable"] and a["platform_only"]
    assert all("changed_by" in x for x in a["self_manageable"] + a["authorized_admin"]
               + a["platform_only"])
    from app.services import capabilities
    nd = {k for k, c in capabilities.CAPABILITIES.items() if not c.delegable}
    assert not nd & {x["key"] for x in a["authorized_admin"]}


# ── 3. blueprints ───────────────────────────────────────────────────────────

def test_blueprint_selection_is_derived_not_hard_coded(db_session, world):
    assert ob.select_blueprint(_org(db_session, world["plat"], industry="energy"))[0] == "energy"
    assert ob.select_blueprint(_org(db_session, world["plat"],
                                    industry="wholesale_real_estate"))[0] == "wholesale_real_estate"
    assert ob.select_blueprint(_org(db_session, world["plat"], industry="generic",
                                    features=["leads", "wholesale_real_estate"]))[0] \
        == "wholesale_real_estate"
    assert ob.select_blueprint(_org(db_session, world["plat"], industry="roofing"))[0] == "core"
    # A legacy NULL allow-list ("everything") is not read as wholesale.
    legacy = _org(db_session, world["plat"], industry="generic")
    legacy.enabled_features = None
    db_session.commit()
    assert ob.select_blueprint(legacy)[0] == "core"


def test_blueprint_feature_keys_are_registered():
    from app.services import entitlements
    for key in ob.BLUEPRINTS:
        for k in ob.blueprint_feature_keys(key, include_optional=True):
            assert k in entitlements.FEATURES
        entitlements.normalize_keys(ob.blueprint_feature_keys(key))  # would 400 if not


def test_energy_blueprint_endpoint(client, db_session, world):
    org = _org(db_session, world["plat"], industry="energy", features=["leads", "crm"])
    r = client.get("/god/customers/%s/blueprint" % org.id, headers=_h(db_session, world["god"]))
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["blueprint"]["key"] == "energy"
    core = {f["key"]: f["status"] for f in b["features"]["core_required"]}
    assert core["leads"] == ob.CONFIGURED and core["crm"] == ob.CONFIGURED
    assert core["imports"] == ob.NEEDS_SETUP
    vert = {f["key"]: f["status"] for f in b["features"]["vertical_required"]}
    assert vert["sms"] == ob.NEEDS_SETUP
    assert "contacts_imported" in {i["key"] for i in b["vertical_setup"]}
    assert set(b["features"]["missing_required"]) >= {"imports", "sms", "email"}


def test_wholesale_blueprint_and_dependency_gap(client, db_session, world):
    # wholesale_real_estate requires leads; enabled without it is Not Ready.
    org = _org(db_session, world["plat"], industry="wholesale_real_estate",
               features=["wholesale_real_estate"])
    b = client.get("/god/customers/%s/blueprint" % org.id,
                   headers=_h(db_session, world["god"])).json()
    assert b["blueprint"]["key"] == "wholesale_real_estate"
    vert = {f["key"]: f for f in b["features"]["vertical_required"]}
    assert vert["wholesale_real_estate"]["status"] == ob.NOT_READY
    assert vert["wholesale_real_estate"]["missing_dependencies"] == ["leads"]
    extras = {i["key"]: i for i in b["vertical_setup"]}
    assert extras["wholesale_settings"]["status"] == ob.NEEDS_SETUP
    assert extras["buyer_network"]["status"] == ob.NEEDS_SETUP
    p = _cc(client, db_session, world, org)
    assert p["metrics"]["deals"]["total"] == 0          # tracked for wholesale


def test_blueprint_override_and_unknown_key(client, db_session, world):
    org = _org(db_session, world["plat"])
    h = _h(db_session, world["god"])
    r = client.get("/god/customers/%s/blueprint?key=energy" % org.id, headers=h)
    assert r.status_code == 200 and r.json()["blueprint"]["key"] == "energy"
    assert r.json()["blueprint"]["selected"] is False
    assert client.get("/god/customers/%s/blueprint?key=nope" % org.id,
                      headers=h).status_code == 400


# ── 4. permission and 404 ──────────────────────────────────────────────────

@pytest.mark.parametrize("suffix", ["control-center", "blueprint"])
def test_god_only(client, db_session, world, suffix):
    org = _org(db_session, world["plat"])
    admin = _user(db_session, role="org_admin", org_id=org.id)
    sadmin = _user(db_session, role="super_admin", org_id=org.id)
    for u in (admin, sadmin):
        r = client.get("/god/customers/%s/%s" % (org.id, suffix), headers=_h(db_session, u))
        assert r.status_code in (401, 403), (u.role, r.status_code)
    assert client.get("/god/customers/%s/%s" % (org.id, suffix)).status_code in (401, 403)


@pytest.mark.parametrize("suffix", ["control-center", "blueprint"])
def test_unknown_org_404(client, db_session, world, suffix):
    r = client.get("/god/customers/does-not-exist/%s" % suffix,
                   headers=_h(db_session, world["god"]))
    assert r.status_code == 404
