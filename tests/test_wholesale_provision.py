# -*- coding: utf-8 -*-
"""LANE-E-BOOKABOOST (#272 P2/P7): God-only "Provision Wholesale Workspace".
Dry run writes nothing; apply is idempotent; outbound and paid start paused;
EvoSys's own Wholesale org and other brands' orgs are refused; nobody is added
unless named; the new workspace opens Wholesale and sees none of EvoSys's data.
All records synthetic."""
import itertools
import json
import uuid

import pytest

from app.models.evosense_models import EvoSenseControl
from app.models.models import AuditLogEntry, Organization, Platform, User
from app.models.sales_models import Membership
from app.models.wholesale_models import WholesaleSettings
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)
URL = "/god/access-setup/wholesale-workspace"
CONFIRM = "SET UP WHOLESALE"


def _platform(db, slug, name):
    p = db.query(Platform).filter(Platform.slug == slug).first()
    if p is None:
        p = Platform(id="plt-%s" % slug, name=name, slug=slug, is_active=True)
        db.add(p)
        db.commit()
    return p


def _user(db, org, email, role="org_admin"):
    u = User(organization_id=org.id if org else None, email=email, full_name=email.split("@")[0], role=role,
             password_hash=hash_password("Pass12345!"), is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _h(db, u):
    return {"Authorization": "Bearer %s" % create_access_token(u, db)}


@pytest.fixture()
def p(db_session):
    db = db_session
    bab = _platform(db, "bookaboost", "BookaBoost")
    evp = _platform(db, "evosyspro", "EvoSys Pro")
    evo = Organization(name="EVO Integrated Solutions LLC", slug="evo-integrated-solutions-llc", plan="trial",
                       industry="generic", is_active=True, platform_id=evp.id,
                       enabled_features=json.dumps(["leads", "users", "wholesale_real_estate"]))
    other = Organization(name="Other Brand Customer (TEST)", slug="other-brand-%s" % uuid.uuid4().hex[:4], plan="trial",
                         industry="generic", is_active=True, platform_id=evp.id, enabled_features=json.dumps(["leads"]))
    legacy = Organization(name="Legacy Open (TEST)", slug="legacy-%s" % uuid.uuid4().hex[:4], plan="trial",
                          industry="generic", is_active=True, platform_id=bab.id, enabled_features=None)
    db.add_all([evo, other, legacy])
    db.commit()
    god = _user(db, None, "god-%d@example.com" % next(_SEQ), role="god_admin")
    owner = _user(db, None, "bab-owner-%d@example.com" % next(_SEQ))
    operator = _user(db, evo, "evo-operator-%d@example.com" % next(_SEQ))   # EvoSys person: never added implicitly
    db.add(Membership(user_id=operator.id, scope_type="customer_org", scope_id=evo.id, role="org_admin", is_active=True))
    db.commit()
    name = "BookaBoost Wholesale TEST %s" % uuid.uuid4().hex[:4]
    return dict(db=db, bab=bab, evp=evp, evo=evo, other=other, legacy=legacy, god=god, owner=owner,
                operator=operator, name=name)


def _org_named(db, name):
    return db.query(Organization).filter(Organization.name == name).all()


def test_only_god_can_plan_or_apply(client, p):
    db = p["db"]
    for u in (p["owner"], p["operator"]):
        assert client.get(URL, params={"name": p["name"], "brand": "bookaboost"}, headers=_h(db, u)).status_code in (401, 403)
        assert client.post(URL, json={"name": p["name"], "brand": "bookaboost", "confirm": CONFIRM},
                           headers=_h(db, u)).status_code in (401, 403)
    assert _org_named(db, p["name"]) == []


def test_dry_run_writes_nothing_and_needs_confirm(client, p):
    db = p["db"]
    r = client.get(URL, params={"name": p["name"], "brand": "bookaboost"}, headers=_h(db, p["god"]))
    assert r.status_code == 200, r.text[:300]
    plan = r.json()
    assert plan["ready"] is True and plan["organization"]["action"] == "create"
    assert "wholesale_workspace" in plan["features"]["to_add"]
    assert all(plan["evosense_controls"]["paused"].values())
    assert _org_named(db, p["name"]) == []
    r = client.post(URL, json={"name": p["name"], "brand": "bookaboost"}, headers=_h(db, p["god"]))
    assert r.status_code == 422
    assert _org_named(db, p["name"]) == []


def test_apply_creates_isolated_workspace_with_outbound_paused_and_is_idempotent(client, p):
    db, god = p["db"], p["god"]
    body = {"name": p["name"], "brand": "bookaboost", "emails": [p["owner"].email], "confirm": CONFIRM}
    r = client.post(URL, json=body, headers=_h(db, god))
    assert r.status_code == 200, r.text[:300]
    out = r.json()
    assert out["organization_created"] is True and out["brand"] == p["bab"].id
    org = db.query(Organization).filter(Organization.id == out["organization_id"]).one()
    feats = json.loads(org.enabled_features)
    assert {"wholesale_real_estate", "wholesale_workspace", "leads"} <= set(feats)
    ctl = db.query(EvoSenseControl).filter(EvoSenseControl.organization_id == org.id).one()
    for k in ("paused_sms", "paused_email", "paused_voice", "paused_ai_replies", "paused_paid_data", "paused_discovery"):
        assert getattr(ctl, k) is True, k
    assert db.query(WholesaleSettings).filter(WholesaleSettings.organization_id == org.id).count() == 1
    # Only the named login was added; the EvoSys operator was not.
    members = {m.user_id for m in db.query(Membership).filter(Membership.scope_type == "customer_org",
                                                                Membership.scope_id == org.id,
                                                                Membership.is_active.is_(True)).all()}
    assert members == {p["owner"].id}
    assert db.query(AuditLogEntry).filter(AuditLogEntry.target_id == org.id,
                                          AuditLogEntry.action == "wholesale_workspace_provisioned").count() == 1
    # Second run: same org, nothing new, no new audit row, no duplicate org.
    audit_before = db.query(AuditLogEntry).filter(AuditLogEntry.target_id == org.id).count()
    r2 = client.post(URL, json=body, headers=_h(db, god))
    assert r2.status_code == 200, r2.text[:300]
    o2 = r2.json()
    assert o2["organization_id"] == org.id and o2["organization_created"] is False
    assert o2["features_added"] == [] and o2["evosense_controls_created"] is False
    assert o2["logins"][0]["action"] == "unchanged"
    assert len(_org_named(db, p["name"])) == 1
    assert db.query(AuditLogEntry).filter(AuditLogEntry.target_id == org.id).count() == audit_before


def test_existing_controls_are_left_exactly_as_they_are(client, p):
    db, god = p["db"], p["god"]
    org = Organization(name=p["name"], slug="bab-existing-%s" % uuid.uuid4().hex[:4], plan="trial", industry="generic",
                       is_active=True, platform_id=p["bab"].id, enabled_features=json.dumps(["leads", "users"]))
    db.add(org)
    db.commit()
    db.add(EvoSenseControl(organization_id=org.id, paused_sms=False, paused_email=True))
    db.commit()
    r = client.post(URL, json={"organization_id": org.id, "brand": "bookaboost", "confirm": CONFIRM}, headers=_h(db, god))
    assert r.status_code == 200, r.text[:300]
    assert r.json()["organization_created"] is False and r.json()["evosense_controls_created"] is False
    db.expire_all()
    ctl = db.query(EvoSenseControl).filter(EvoSenseControl.organization_id == org.id).one()
    assert ctl.paused_sms is False and ctl.paused_email is True
    assert "leads" in json.loads(db.query(Organization).get(org.id).enabled_features)     # kept


def test_refusals(client, p):
    db, god = p["db"], p["god"]
    h = _h(db, god)
    cases = [
        ({"organization_id": p["evo"].id, "brand": "evosyspro"}, "EvoSys"),          # built-in host: never
        ({"organization_id": p["other"].id, "brand": "bookaboost"}, "another brand"),   # wrong brand
        ({"organization_id": p["legacy"].id, "brand": "bookaboost"}, "legacy"),        # NULL list not narrowed
        ({"name": p["name"], "brand": "nope"}, "No brand"),
        ({"name": p["name"], "brand": "bookaboost", "emails": ["nobody@example.com"]}, "No login"),
    ]
    for body, needle in cases:
        r = client.post(URL, json={**body, "confirm": CONFIRM}, headers=h)
        assert r.status_code == 409, (body, r.status_code, r.text[:200])
        assert needle in r.json()["detail"], (needle, r.json()["detail"])
    db.expire_all()
    assert json.loads(db.query(Organization).get(p["evo"].id).enabled_features) == ["leads", "users", "wholesale_real_estate"]
    assert db.query(Organization).get(p["legacy"].id).enabled_features is None
    assert _org_named(db, p["name"]) == []


def test_provisioned_workspace_opens_wholesale_and_sees_no_evosys_data(client, p):
    db, god = p["db"], p["god"]
    out = client.post(URL, json={"name": p["name"], "brand": "bookaboost", "emails": [p["owner"].email],
                                 "confirm": CONFIRM}, headers=_h(db, god)).json()
    org_id = out["organization_id"]
    # Name the owner for Wholesale (the separate, explicit seat step) and look.
    assert client.post("/god/access-setup/wholesale/grant", json={"email": p["owner"].email},
                       headers=_h(db, god)).status_code == 200
    c = client.get("/auth/my-contexts", headers=_h(db, p["owner"])).json()
    assert {x["organization_id"]: x["experience"] for x in c["workspace_contexts"]} == {org_id: "wholesale"}
    h = {**_h(db, p["owner"]), "X-Workspace-Id": org_id}
    r = client.get("/wholesale/buyers", headers=h, params={"include_test": True})
    assert r.status_code == 200 and r.json()["buyers"] == []
