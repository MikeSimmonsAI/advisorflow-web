from datetime import datetime
# -*- coding: utf-8 -*-
"""Rapid cleaning-company onboarding: the five-of-fifteen scenario, idempotency,
recovery, invitations, isolation. Synthetic companies on example.test only."""
import json
import uuid

import pytest

from app.models.acquisition_models import AcquisitionOpportunity, AcquisitionProfile
from app.models.models import EmailMessage, Message, Organization, Platform, User
from app.models.sales_models import BrandSalesOrg, Opportunity
from tests.test_acquisition_pilot import _h, _pool_business, pilot  # noqa: F401


@pytest.fixture()
def sales(pilot):
    p = pilot
    db = p["db"]
    plat = db.query(Platform).filter(Platform.slug == "evosyspro").first()
    if plat is None:
        plat = Platform(name="EvoSys Pro", slug="evosyspro")
        db.add(plat)
        db.commit()
    bso = BrandSalesOrg(platform_id=plat.id, name="EvoSys Pro Sales Test", slug="evo-sales-%s" % uuid.uuid4().hex[:6])
    db.add(bso)
    db.commit()
    opps = []
    for i in range(15):
        o = Opportunity(brand_sales_org_id=bso.id, company_name="Sparkle Test Cleaning %02d" % i,
                        contact_name="Owner %02d" % i, email="owner%02d@sparkle%02d.example.test" % (i, i),
                        phone="214-555-01%02d" % i, industry="Cleaning Services",
                        source="Test campaign | TEST-CLEAN-%03d" % i,
                        stage="contacted" if i < 5 else "prospect")
        db.add(o)
        opps.append(o)
    db.commit()
    return dict(p, opps=opps, bso=bso)


def _batch(s, items, defaults=None):
    r = s["c"].post("/god/acquisition/clients/batch", json={"items": items, "defaults": defaults or {}},
                    headers=_h(s["db"], s["god"]))
    assert r.status_code == 200, r.text
    return r.json()


def test_five_of_fifteen_become_five_private_paused_workspaces(sales):
    s = sales
    db = s["db"]
    cands = s["c"].get("/god/acquisition/sales-candidates", headers=_h(db, s["god"])).json()["items"]
    mine = [c for c in cands if c["company"].startswith("Sparkle Test")]
    assert len(mine) == 15 and sum(c["status"] == "interested" for c in mine) == 5
    picked = [{"sales_opportunity_id": o.id} for o in s["opps"][:5]]
    before_emails, before_sms = db.query(EmailMessage).count(), db.query(Message).count()
    out = _batch(s, picked, {"services": ["office", "medical"], "offer_type": "free_pilot"})
    assert out["created"] == 5 and out["failed"] == 0 and out["sent"] == 0
    ids = [r["organization_id"] for r in out["results"]]
    assert len(set(ids)) == 5
    names = {r["name"] for r in out["results"]}
    assert names == {"Sparkle Test Cleaning %02d" % i for i in range(5)}
    profiles = db.query(AcquisitionProfile).filter(AcquisitionProfile.organization_id.in_(ids)).all()
    assert len(profiles) == 5 and all(p.paused and p.pilot_started_at is None for p in profiles)
    assert all(json.loads(p.service_area) == [] for p in profiles)           # territory NOT guessed
    assert all(set(json.loads(p.segments)) == {"office", "medical_dental"} for p in profiles)
    for o in s["opps"][:5]:
        db.refresh(o)
        assert o.customer_organization_id in ids and o.stage == "onboarding" and o.status == "won"
    for o in s["opps"][5:]:
        db.refresh(o)
        assert o.customer_organization_id is None and o.stage == "prospect"       # untouched
    # retry the same batch: nothing new
    again = _batch(s, picked)
    assert again["created"] == 0 and again["existing"] == 5
    assert db.query(Organization).filter(Organization.name.like("Sparkle Test Cleaning%")).count() == 5
    assert db.query(EmailMessage).count() == before_emails and db.query(Message).count() == before_sms
    # owner sees all five; each has its own empty pipeline
    for oid in ids:
        ov = s["c"].get("/acquisition-ops/%s/overview" % oid, headers=_h(db, s["god"])).json()
        assert ov["organization_id"] == oid and ov["funnel"]["total"] == 0 and ov["profile"]["paused"] is True
    d = s["c"].get("/god/acquisition/directory", headers=_h(db, s["god"])).json()["items"]
    row = next(x for x in d if x["organization_id"] == ids[0])
    assert row["territory_confirmed"] is False and row["status"] == "onboarding_incomplete"


def test_assignment_needs_territory_and_stays_with_one_client(sales):
    s = sales
    db = s["db"]
    out = _batch(s, [{"sales_opportunity_id": o.id} for o in s["opps"][:3]], {"services": ["medical"]})
    a, b, c = [r["organization_id"] for r in out["results"]]
    item = _pool_business(db, industry="medical_dental", city="Plano", state="TX")
    g = _h(db, s["god"])
    pv = s["c"].post("/god/acquisition/master/assign/preview", json={"destination_org_id": a, "prospect_ids": [item.id]},
                     headers=g).json()
    assert "outside_client_service_area" in pv["decisions"][0]["refused_because"]   # unknown territory blocks
    s["c"].put("/acquisition-ops/%s/profile" % a, json={"service_area": ["Plano"], "service_states": ["TX"]}, headers=g)
    s["c"].put("/acquisition-ops/%s/profile" % b, json={"service_area": ["Fort Worth"], "service_states": ["TX"]}, headers=g)
    r = s["c"].post("/god/acquisition/master/assign", json={"destination_org_id": a, "prospect_ids": [item.id]},
                    headers=g).json()
    assert r["assigned"] == 1 and r["sent"] == 0
    assert len(s["c"].get("/acquisition-ops/%s/prospects" % a, headers=g).json()["items"]) == 1
    for other in (b, c):
        assert s["c"].get("/acquisition-ops/%s/prospects" % other, headers=g).json()["items"] == []
    assert db.query(AcquisitionOpportunity).filter(AcquisitionOpportunity.source == "master_pool:" + item.id).count() == 1


def test_direct_add_duplicate_name_and_conversion_duplicate_check(sales):
    s = sales
    g = _h(s["db"], s["god"])
    r = s["c"].post("/god/acquisition/clients", json={"name": "Lucy Test Cleaning Co", "cities": "Irving, Dallas",
                                                      "services": ["office"]}, headers=g).json()
    assert r["status"] == "created" and r["paused"] is True and r["invitation"] is None
    again = s["c"].post("/god/acquisition/clients", json={"name": "lucy test cleaning co"}, headers=g).json()
    assert again["status"] == "existing" and again["organization_id"] == r["organization_id"]
    # converting a Sales record whose company already has a workspace offers the existing one
    o = s["opps"][7]
    o.company_name = "Lucy Test Cleaning Co"
    s["db"].commit()
    dup = s["c"].post("/god/acquisition/clients", json={"sales_opportunity_id": o.id}, headers=g).json()
    assert dup["status"] == "duplicate" and dup["organization_id"] == r["organization_id"]
    s["db"].refresh(o)
    assert o.customer_organization_id is None and o.stage == "prospect"
    prof = s["db"].query(AcquisitionProfile).filter(AcquisitionProfile.organization_id == r["organization_id"]).one()
    assert json.loads(prof.service_area) == ["Irving", "Dallas"]


def test_interrupted_provisioning_recovers_without_duplicates(sales, monkeypatch):
    from app.services.acquisition import engine as E
    s = sales
    real = E.enable
    calls = {"n": 0}

    def flaky(db, org, actor):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated crash after the organization was created")
        return real(db, org, actor)
    monkeypatch.setattr(E, "enable", flaky)
    o = s["opps"][0]
    first = _batch(s, [{"sales_opportunity_id": o.id}])
    assert first["failed"] == 1
    second = _batch(s, [{"sales_opportunity_id": o.id}])
    assert second["results"][0]["status"] in ("existing", "created") and second["failed"] == 0
    s["db"].refresh(o)
    assert s["db"].query(Organization).filter(Organization.id == o.customer_organization_id).count() == 1
    assert s["db"].query(AcquisitionProfile).filter(
        AcquisitionProfile.organization_id == o.customer_organization_id).count() == 1


def test_invitations_new_resend_and_other_workspace(sales):
    s = sales
    db = s["db"]
    g = _h(db, s["god"])
    r = s["c"].post("/god/acquisition/clients", json={"name": "Invite Test Cleaning", "contact_name": "Ina Admin",
                                                      "invite_email": "ina@invite.example.test"}, headers=g).json()
    inv = r["invitation"]
    assert inv["status"] == "invitation_pending" and "/activate?token=" in inv["setup_url"] and not inv["resent"]
    u = db.query(User).filter(User.email == "ina@invite.example.test").one()
    assert u.organization_id == r["organization_id"] and u.role == "org_admin" and u.must_change_password
    again = s["c"].post("/god/acquisition/clients/%s/invite" % r["organization_id"],
                        json={"email": "ina@invite.example.test"}, headers=g).json()
    assert again["resent"] is True and db.query(User).filter(User.email == "ina@invite.example.test").count() == 1
    clash = s["c"].post("/god/acquisition/clients/%s/invite" % r["organization_id"],
                        json={"email": s["outsider"].email}, headers=g)
    assert clash.status_code == 409
    god_clash = s["c"].post("/god/acquisition/clients/%s/invite" % r["organization_id"],
                            json={"email": s["god"].email}, headers=g)
    assert god_clash.status_code == 409
    d = s["c"].get("/god/acquisition/directory", headers=g).json()["items"]
    assert next(x for x in d if x["organization_id"] == r["organization_id"])["invitation"]["status"] == "sent"
    # Resending never leaves two working links for one person.
    from app.models.staff_models import StaffActivation
    from app.services import staff_activation as SA
    rows = db.query(StaffActivation).filter(StaffActivation.user_id == u.id).all()
    assert sum(1 for x in rows if x.is_usable()) == 1
    # Accepted link, never signed in -> "accepted"; first sign-in -> "active".
    live = next(x for x in rows if x.is_usable())
    live.accepted_at = datetime.utcnow(); db.commit()
    st = lambda: next(x for x in s["c"].get("/god/acquisition/directory", headers=g).json()["items"]
                      if x["organization_id"] == r["organization_id"])["invitation"]["status"]
    assert st() == "accepted"
    u.last_login_at = datetime.utcnow(); db.commit()
    assert st() == "active"
    assert db.query(EmailMessage).count() == 0


def test_onboarding_is_owner_only(sales):
    s = sales
    for u in (s["owner"], s["outsider"]):
        h = _h(s["db"], u)
        assert s["c"].post("/god/acquisition/clients", json={"name": "X Co"}, headers=h).status_code == 403
        assert s["c"].post("/god/acquisition/clients/batch", json={"items": [{"name": "X"}]}, headers=h).status_code == 403
        assert s["c"].get("/god/acquisition/directory", headers=h).status_code == 403
        assert s["c"].get("/god/acquisition/sales-candidates", headers=h).status_code == 403
