# -*- coding: utf-8 -*-
"""S16: Max Life DEMO provisioning from God mode (POST/GET /god/demo/maxlife)."""
import uuid

import pytest

from agency_support import h, mount as mount_agency, org as make_org, user as make_user  # noqa: F401
from app.models.agency_models import (AgencyAgentProfile, AgencyApplication, AgencyAppointment,
                                      AgencyPolicy, AgencyProspectProfile, AgencyRecruit, AgencyTask)
from app.models.models import AuditLogEntry, EmailMessage, Lead, Message, Organization, User
from app.services.agency import demo_seed as ds


def _mount():
    from app.main import app
    from app.routers.god_demo_router import router
    if not any(getattr(r, "path", "").startswith("/god/demo/maxlife") for r in app.routes):
        app.include_router(router)
    mount_agency()


@pytest.fixture()
def god(db_session):
    _mount()
    u = User(email="god-%s@platform.test" % uuid.uuid4().hex[:6], password_hash="x", full_name="God",
             role="god_admin", is_active=True, must_change_password=False)
    db_session.add(u)
    db_session.commit()
    return u


def _orgs(db):
    return db.query(Organization).filter(Organization.slug == ds.SLUG).all()


def test_god_only(client, db_session, god):
    o = make_org(db_session, "Real Customer")
    for role in ("org_admin", "advisor"):
        u = make_user(db_session, o, role, role)
        assert client.get("/god/demo/maxlife", headers=h(db_session, u)).status_code == 403
        assert client.post("/god/demo/maxlife", headers=h(db_session, u), json={}).status_code == 403
    assert _orgs(db_session) == []
    assert client.get("/god/demo/maxlife", headers=h(db_session, god)).json()["exists"] is False


def test_provision_idempotent_all_demo_nothing_sent(client, db_session, god):
    hg = h(db_session, god)
    r = client.post("/god/demo/maxlife", headers=hg, json={})
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["organization_created"] is True
    oid = rep["organization_id"]
    org = db_session.query(Organization).filter(Organization.id == oid).one()
    assert org.is_demo is True and org.industry == "insurance" and "(DEMO)" in org.name
    c = rep["counts"]
    assert c["agents"] == 4 and c["prospects"] == 5 and c["applications"] == 1 and c["recruits"] == 3
    assert rep["status"]["insurance_agency_enabled"] is True
    assert {"leads", "insurance_agency"} <= set(rep["status"]["features"])
    assert "sms" not in rep["status"]["features"] and "email" not in rep["status"]["features"]

    # second call adds nothing
    r2 = client.post("/god/demo/maxlife", headers=hg, json={"organization_id": oid})
    assert r2.status_code == 200, r2.text
    assert r2.json()["organization_created"] is False
    assert all(v == 0 for v in r2.json()["added"].values()), r2.json()["added"]
    r3 = client.post("/god/demo/maxlife", headers=hg, json={})
    assert r3.json()["organization_id"] == oid and r3.json()["counts"] == r2.json()["counts"]
    assert len(_orgs(db_session)) == 1

    # every record is DEMO
    db_session.expire_all()
    leads = db_session.query(Lead).filter(Lead.organization_id == oid).all()
    assert leads and all(l.is_test for l in leads)
    assert all(l.email.endswith("@example.com") for l in leads)
    assert all(l.phone.startswith("+1214555010") for l in leads)
    assert not any(l.sms_consent for l in leads)
    for model in (AgencyAgentProfile, AgencyProspectProfile, AgencyApplication, AgencyPolicy,
                  AgencyAppointment, AgencyTask, AgencyRecruit):
        rows = db_session.query(model).filter(model.organization_id == oid).all()
        assert rows and all(x.is_demo for x in rows), model.__name__
    users = db_session.query(User).filter(User.organization_id == oid).all()
    assert len(users) == 4
    assert all(u.email.endswith("@example.com") and u.full_name.startswith("DEMO") for u in users)
    assert all(u.must_change_password for u in users)   # random, unusable password

    # nothing sent
    assert db_session.query(Message).filter(Message.lead_id.in_([l.id for l in leads])).count() == 0
    assert db_session.query(EmailMessage).count() == 0

    # audited
    acts = {a.action for a in db_session.query(AuditLogEntry).filter(AuditLogEntry.organization_id == oid)}
    assert {"god.demo.maxlife.org_created", "god.demo.maxlife.seeded"} <= acts

    st = client.get("/god/demo/maxlife", headers=hg).json()
    assert st["exists"] and st["organization_id"] == oid and st["counts"]["prospects"] == 5


def test_refuses_non_demo_org(client, db_session, god):
    real = make_org(db_session, "Real Agency")
    before = db_session.query(Lead).filter(Lead.organization_id == real.id).count()
    r = client.post("/god/demo/maxlife", headers=h(db_session, god), json={"organization_id": real.id})
    assert r.status_code == 409
    db_session.expire_all()
    assert db_session.query(Lead).filter(Lead.organization_id == real.id).count() == before
    assert db_session.query(AgencyAgentProfile).filter(
        AgencyAgentProfile.organization_id == real.id).count() == 0
    # an is_demo org that this endpoint did not create is refused too
    other_demo = make_org(db_session, "Brand Demo Workspace")
    other_demo.is_demo = True
    db_session.commit()
    assert client.post("/god/demo/maxlife", headers=h(db_session, god),
                       json={"organization_id": other_demo.id}).status_code == 409
    # the slug held by a non-demo org is never converted
    real.slug = ds.SLUG
    db_session.commit()
    assert client.post("/god/demo/maxlife", headers=h(db_session, god), json={}).status_code == 409
    db_session.expire_all()
    assert db_session.get(Organization, real.id).is_demo is False
    assert client.post("/god/demo/maxlife", headers=h(db_session, god),
                       json={"organization_id": "nope"}).status_code == 404


def test_agency_endpoints_work_for_demo_org(client, db_session, god):
    oid = client.post("/god/demo/maxlife", headers=h(db_session, god), json={}).json()["organization_id"]
    # the god owner views it through the existing enter-customer override
    hg = dict(h(db_session, god), **{"X-Org-Override": oid})
    s = client.get("/agency/summary", headers=hg)
    assert s.status_code == 200, s.text
    p = client.get("/agency/prospects", headers=hg)
    assert p.status_code == 200 and p.json()["total"] == 5
    a = client.get("/agency/agents", headers=hg)
    assert a.status_code == 200 and len(a.json()["items"]) == 4
    # the demo org admin (entitlement path for a customer user) is entitled too
    owner = db_session.query(User).filter(User.email == "demo.owner@example.com").one()
    # unusable until deliberately reset: the must-change gate blocks it first
    assert client.get("/agency/summary", headers=h(db_session, owner)).status_code == 403
    owner.must_change_password = False   # simulate a deliberate reset by the owner
    db_session.commit()
    r = client.get("/agency/summary", headers=h(db_session, owner))
    assert r.status_code == 200, r.text
    # and a real customer cannot see the demo prospects
    real = make_org(db_session, "Real Agency 2")
    mgr = make_user(db_session, real, "org_admin", "realmgr")
    assert client.get("/agency/prospects", headers=h(db_session, mgr)).json()["total"] == 0


def test_seed_function_refuses_non_demo_and_email_collision(db_session):
    real = make_org(db_session, "Real")
    with pytest.raises(ds.DemoSeedError):
        ds.seed_maxlife_demo(db_session, organization=real)
    db_session.rollback()
    # a demo email already homed in another org is never adopted
    db_session.add(User(organization_id=real.id, email="demo.owner@example.com", password_hash="x",
                        full_name="Someone", role="org_admin", is_active=True))
    db_session.commit()
    with pytest.raises(ds.DemoSeedError):
        ds.seed_maxlife_demo(db_session)
