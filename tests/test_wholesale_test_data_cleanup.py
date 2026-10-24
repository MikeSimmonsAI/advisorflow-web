"""Wholesale test-data cleanup: only rows flagged test go, every dependent row
is counted and backed up first, evidence is kept, and a plan that would touch
a real record is refused."""
import itertools
import json

import pytest

from app.models.evosense_models import EvoSenseProperty, EvoSenseSignal
from app.models.models import Lead, User
from app.models.wholesale_models import (WholesaleBuyer, WholesaleDeal, WholesaleDocument, WholesaleEvent,
                                         WholesaleProperty)
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)
BASE = "/god/customers/cleanup/wholesale"


@pytest.fixture
def god(db_session):
    u = User(organization_id=None, email="owner-wc%d@evosyspro.live" % next(_SEQ), password_hash=hash_password("x"),
             full_name="Owner", role="god_admin", must_change_password=False)
    db_session.add(u)
    db_session.commit()
    return u


def _h(db, u):
    return {"Authorization": "Bearer " + create_access_token(u, db)}


@pytest.fixture
def world(db_session, sample_org):
    org = sample_org.id
    add = db_session.add
    smoke = WholesaleProperty(organization_id=org, street_address="100 Smoketest Street", is_test=True,
                              test_note="ZZTEST production smoke test")
    keep = WholesaleProperty(organization_id=org, street_address="2904 Chambley Ln", is_test=True,
                             test_note="Mike's live /sell test submission - kept as production verification evidence")
    practice = WholesaleProperty(organization_id=org, street_address="1234 Practice Ln", is_test=True,
                                 test_note="Practice deal for trying e-signature")
    real = WholesaleProperty(organization_id=org, street_address="4509 Aspen Way", is_test=False)
    for x in (smoke, keep, practice, real):
        add(x)
    db_session.flush()
    lead = Lead(organization_id=org, first_name="ZZTEST", last_name="Seller", is_test=True, phone="+12145550101")
    add(lead)
    db_session.flush()
    d1 = WholesaleDeal(organization_id=org, property_id=smoke.id, seller_lead_id=lead.id, is_test=True)
    d2 = WholesaleDeal(organization_id=org, property_id=keep.id, is_test=True)
    d3 = WholesaleDeal(organization_id=org, property_id=practice.id, is_test=True)
    d4 = WholesaleDeal(organization_id=org, property_id=real.id, is_test=False)
    for x in (d1, d2, d3, d4):
        add(x)
    db_session.flush()
    add(WholesaleDocument(organization_id=org, deal_id=d3.id, doc_type="purchase_contract", status="signed"))
    add(WholesaleEvent(organization_id=org, property_id=smoke.id, deal_id=d1.id, action="deal.created"))
    add(WholesaleEvent(organization_id=org, property_id=real.id, deal_id=d4.id, action="deal.created"))
    buyer = WholesaleBuyer(organization_id=org, company_name="ZZTEST Standing Check", is_test=True)
    add(buyer)
    ep = EvoSenseProperty(organization_id=org, street_address="7700 ZZTEST Acceptance Ln", is_test=True,
                          address_key="7700 zztest acceptance ln|75201")
    add(ep)
    db_session.flush()
    add(EvoSenseSignal(organization_id=org, property_id=ep.id, signal_type="VACANT", source="manual"))
    db_session.commit()
    return dict(smoke=smoke, keep=keep, practice=practice, real=real, lead=lead, buyer=buyer, ep=ep, d4=d4)


def test_only_flagged_test_rows_go_and_evidence_is_kept(client, db_session, god, world):
    h = _h(db_session, god)
    p = client.post(BASE + "/preview", headers=h, json={})
    assert p.status_code == 200, p.text
    plan = p.json()
    ids = plan["ids"]
    assert ids["wholesale_properties"] == [world["smoke"].id]
    assert world["lead"].id in ids["leads"] and world["buyer"].id in ids["wholesale_buyers"]
    assert world["ep"].id in ids["evosense_properties"] and len(ids["evosense_signals"]) == 1
    assert len(ids["wholesale_events"]) == 1 and len(ids["wholesale_deals"]) == 1
    kept = {k["street_address"] for k in plan["kept"]}
    assert kept == {"2904 Chambley Ln", "1234 Practice Ln"}
    assert plan["refused"] == [] and plan["total"] == sum(plan["counts"].values())
    # the backup holds every column of every row before anything is deleted
    b = client.get(BASE + "/%s/backup" % plan["execution_id"], headers=h).json()
    assert b["manifest"]["backup"]["wholesale_properties"][0]["street_address"] == "100 Smoketest Street"
    assert b["manifest"]["backup"]["leads"][0]["phone"] == "+12145550101"
    bad = client.post(BASE + "/execute", headers=h, json={"execution_id": plan["execution_id"], "confirmation": "yes"})
    assert bad.status_code == 400
    ok = client.post(BASE + "/execute", headers=h, json={"execution_id": plan["execution_id"],
                                                         "confirmation": plan["confirmation_phrase"]})
    assert ok.status_code == 200, ok.text
    assert ok.json()["counts_match"] is True
    db_session.expire_all()
    left = {p.street_address for p in db_session.query(WholesaleProperty).all()}
    assert left == {"2904 Chambley Ln", "1234 Practice Ln", "4509 Aspen Way"}
    assert db_session.query(WholesaleDeal).count() == 3
    assert db_session.query(WholesaleEvent).filter_by(property_id=world["real"].id).count() == 1
    assert db_session.query(EvoSenseProperty).count() == 0 and db_session.query(WholesaleBuyer).count() == 0
    again = client.post(BASE + "/execute", headers=h, json={"execution_id": plan["execution_id"],
                                                            "confirmation": plan["confirmation_phrase"]})
    assert again.status_code == 409                               # a receipt runs once


def test_a_plan_touching_a_real_record_is_refused(client, db_session, god, world):
    world["d4"].assigned_buyer_id = world["buyer"].id             # a REAL deal points at the test buyer
    db_session.commit()
    h = _h(db_session, god)
    plan = client.post(BASE + "/preview", headers=h, json={}).json()
    assert plan["refused"] and "wholesale_deals" in plan["refused"][0]
    r = client.post(BASE + "/execute", headers=h, json={"execution_id": plan["execution_id"],
                                                        "confirmation": plan["confirmation_phrase"]})
    assert r.status_code == 409
    db_session.expire_all()
    assert db_session.query(WholesaleBuyer).count() == 1 and db_session.query(WholesaleDeal).count() == 4


def test_a_changed_set_is_not_deleted(client, db_session, god, world, sample_org):
    h = _h(db_session, god)
    plan = client.post(BASE + "/preview", headers=h, json={}).json()
    db_session.add(WholesaleProperty(organization_id=sample_org.id, street_address="999 New Test", is_test=True))
    db_session.commit()
    r = client.post(BASE + "/execute", headers=h, json={"execution_id": plan["execution_id"],
                                                        "confirmation": plan["confirmation_phrase"]})
    assert r.status_code == 409
    db_session.expire_all()
    assert db_session.query(WholesaleProperty).count() == 5


def test_only_the_platform_owner_may_clean(client, db_session, world, admin_auth_headers):
    assert client.post(BASE + "/preview", headers=admin_auth_headers, json={}).status_code in (401, 403)


def test_sandbox_sources_never_feed_a_real_strategy(db_session, sample_org):
    from app.services.evosense import hunt as HU
    from app.services.evosense import providers as PV
    cfg = PV.config(db_session, sample_org.id, "sandbox_property_records")
    cfg.enabled = True
    db_session.commit()
    real = {p.key for _c, p, _cfg in HU._discovery_routes(db_session, sample_org.id, [], sandbox_allowed=False)}
    test = {p.key for _c, p, _cfg in HU._discovery_routes(db_session, sample_org.id, [], sandbox_allowed=True)}
    assert not any(k.startswith("sandbox") for k in real)
    assert any(k.startswith("sandbox") for k in test)
