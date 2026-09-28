"""P6: automatic exception triggers - AI handoffs, missing disposition data - the
hourly pass, and the My Work count. Genuine human-action conditions only; no
duplicates, no reopening what a person just closed, no sandbox noise from the
automatic pass, nothing across organizations, nothing sent to anyone."""
import json
from datetime import datetime, timedelta

import pytest

from app.models.evosense_models import EvoSenseHandoff, EvoSenseProperty
from app.models.models import Message, Organization, User, UserCapabilityGrant
from app.models.wholesale_models import (WholesaleDeal, WholesaleEvent, WholesaleSellerProfile,
                                         WholesaleWorkException)
from app.services import wholesale_exceptions as EX
from app.services.auth_service import create_access_token, hash_password


def ok(r):
    assert r.status_code in (200, 201), "%s %s" % (r.status_code, r.text[:400])
    return r.json()


def _user(db, org, role, email, grant=False):
    u = User(organization_id=org.id, email=email, password_hash=hash_password("Pass12345!"),
             full_name=email.split("@")[0], role=role, must_change_password=False)
    db.add(u)
    db.commit()
    if grant:
        db.add(UserCapabilityGrant(user_id=u.id, organization_id=org.id, scope_type="customer_org",
                                   scope_id=org.id, capability="exception_queue_work", is_active=True))
        db.commit()
    return u


def _h(db, u):
    return {"Authorization": "Bearer %s" % create_access_token(u, db)}


@pytest.fixture
def admin(db_session, sample_org):
    return _user(db_session, sample_org, "org_admin", "owner@trig.test")


def _seller_deal(client, db, admin, street, is_test=False):
    prop = ok(client.post("/wholesale/properties", headers=_h(db, admin),
                          json={"street_address": street, "city": "Dallas", "state": "TX",
                                "zip_code": "75215", "is_test": is_test}))
    ok(client.post("/wholesale/properties/%s/seller" % prop["id"], headers=_h(db, admin),
                   json={"first_name": "Sal", "last_name": "Seller", "phone": "2145550177"}))
    return prop


def _open(db, kind):
    return (db.query(WholesaleWorkException)
            .filter(WholesaleWorkException.kind == kind,
                    WholesaleWorkException.status.in_(("open", "assigned", "needs_more_info", "escalated")))
            .all())


def test_ai_handoff_raises_one_exception_and_nothing_is_sent(client, db_session, admin, sample_org):
    prop = _seller_deal(client, db_session, admin, "1 Angry Ln")
    prof = db_session.query(WholesaleSellerProfile).filter(
        WholesaleSellerProfile.property_id == prop["id"]).one()
    prof.needs_human, prof.needs_human_reason = True, "Message reads as a legal threat"
    db_session.commit()
    before = db_session.query(Message).count()
    for _ in range(3):
        EX.sweep(db_session, sample_org.id)
        db_session.commit()
    rows = _open(db_session, "ai_exception")
    assert len(rows) == 1
    assert rows[0].subject_type == "lead" and "legal threat" in rows[0].detail
    assert rows[0].source == "sweep:ai_needs_human"
    assert db_session.query(Message).count() == before
    assert db_session.query(WholesaleEvent).filter(WholesaleEvent.action == "exception.raised").count() >= 1


def test_only_stale_evosense_handoffs_become_exceptions(db_session, sample_org):
    fresh_p = EvoSenseProperty(organization_id=sample_org.id, street_address="2 Fresh St")
    old_p = EvoSenseProperty(organization_id=sample_org.id, street_address="3 Stale St")
    db_session.add_all([fresh_p, old_p])
    db_session.flush()
    db_session.add_all([
        EvoSenseHandoff(organization_id=sample_org.id, property_id=fresh_p.id,
                        reasons=json.dumps([{"code": "asked_question", "label": "Seller asked a question"}])),
        EvoSenseHandoff(organization_id=sample_org.id, property_id=old_p.id, next_action="Call the owner",
                        reasons=json.dumps([{"code": "price", "label": "Seller named a price"}]),
                        created_at=datetime.utcnow() - timedelta(hours=EX.HANDOFF_STALE_HOURS + 2)),
    ])
    db_session.commit()
    made = EX.sweep(db_session, sample_org.id)
    db_session.commit()
    assert made.get("ai_exception") == 1
    ex = _open(db_session, "ai_exception")[0]
    assert ex.subject_id == old_p.id and "Seller named a price" in ex.detail and "Call the owner" in ex.detail


def test_missing_disposition_data_is_one_item_per_deal_and_stays_current(client, db_session, admin,
                                                                        sample_org):
    prop = _seller_deal(client, db_session, admin, "4 Dispo Dr")
    deal = db_session.query(WholesaleDeal).filter(WholesaleDeal.id == prop["deal"]["id"]).one()
    deal.stage = "disposition"
    db_session.commit()
    EX.sweep(db_session, sample_org.id)
    db_session.commit()
    rows = _open(db_session, "missing_disposition_data")
    assert len(rows) == 1 and rows[0].subject_type == "deal"
    assert "seller contract price" in rows[0].detail and "asking price for buyers" in rows[0].detail
    item = next(i for i in ok(client.get("/wholesale/exceptions?scope=all", headers=_h(db_session, admin)))["items"]
                if i["kind"] == "missing_disposition_data")
    assert item["subject"]["label"].startswith("4 Dispo Dr") and item["subject"]["stage"] == "disposition"
    # Part of it gets filled in: still one item, and its list is current.
    deal.contract_price, deal.buyer_room_asking_price = 150000, 185000
    db_session.commit()
    EX.sweep(db_session, sample_org.id)
    db_session.commit()
    rows = _open(db_session, "missing_disposition_data")
    assert len(rows) == 1
    assert "seller contract price" not in rows[0].detail and "closing deadline" in rows[0].detail
    # What is still missing is exactly what the item lists.
    assert EX.missing_disposition_items(deal) == ["signed seller contract recorded", "closing deadline"]


def test_a_deal_outside_disposition_is_left_alone(client, db_session, admin, sample_org):
    _seller_deal(client, db_session, admin, "5 Early Ave")
    EX.sweep(db_session, sample_org.id)
    db_session.commit()
    assert _open(db_session, "missing_disposition_data") == []


def test_a_closed_exception_is_not_reopened_by_the_next_sweep(client, db_session, admin, sample_org):
    prop = _seller_deal(client, db_session, admin, "6 Resolved Rd")
    prof = db_session.query(WholesaleSellerProfile).filter(
        WholesaleSellerProfile.property_id == prop["id"]).one()
    prof.needs_human = True
    db_session.commit()
    EX.sweep(db_session, sample_org.id)
    db_session.commit()
    ex = _open(db_session, "ai_exception")[0]
    ok(client.post("/wholesale/exceptions/%s/resolve" % ex.id, headers=_h(db_session, admin),
                   json={"outcome": "unable_to_verify", "note": "Left a voicemail; no answer"}))
    made = EX.sweep(db_session, sample_org.id)
    db_session.commit()
    assert "ai_exception" not in made and _open(db_session, "ai_exception") == []
    # After the cool-down a still-true condition may come back once.
    closed = db_session.query(WholesaleWorkException).filter(WholesaleWorkException.id == ex.id).one()
    closed.resolved_at = datetime.utcnow() - timedelta(days=EX.COOLDOWN_DAYS + 1)
    db_session.commit()
    assert EX.sweep(db_session, sample_org.id).get("ai_exception") == 1


def test_the_automatic_pass_skips_sandbox_and_caps_each_rule(client, db_session, admin, sample_org,
                                                            monkeypatch):
    prop = _seller_deal(client, db_session, admin, "7 Sandbox Ct", is_test=True)
    prof = db_session.query(WholesaleSellerProfile).filter(
        WholesaleSellerProfile.property_id == prop["id"]).one()
    prof.needs_human = True
    for i in range(4):
        ok(client.post("/wholesale/properties", headers=_h(db_session, admin),
                       json={"street_address": "%d Ownerless St" % (10 + i), "city": "Dallas", "state": "TX",
                             "zip_code": "75215"}))
    db_session.commit()
    monkeypatch.setattr(EX, "AUTO_CAP_PER_KIND", 2)
    report = EX.sweep_all(db_session)
    assert report["failed"] == 0 and report["orgs"] >= 1
    assert _open(db_session, "ai_exception") == []                     # sandbox seller left alone
    assert len(_open(db_session, "verify_owner")) == 2                 # capped per pass
    EX.sweep_all(db_session)
    assert len(_open(db_session, "verify_owner")) == 4                 # the rest arrive next pass
    EX.sweep_all(db_session)
    assert len(_open(db_session, "verify_owner")) == 4                 # and never twice


def test_the_automatic_pass_keeps_tenants_apart(client, db_session, admin, sample_org):
    other = Organization(name="Trigger Other Org", slug="trigger-other", plan="enterprise")
    db_session.add(other)
    db_session.commit()
    other_admin = _user(db_session, other, "org_admin", "owner@trig-other.test")
    ok(client.post("/wholesale/properties", headers=_h(db_session, other_admin),
                   json={"street_address": "8 Theirs St", "city": "Austin", "state": "TX", "zip_code": "78701"}))
    ok(client.post("/wholesale/properties", headers=_h(db_session, admin),
                   json={"street_address": "9 Ours St", "city": "Dallas", "state": "TX", "zip_code": "75215"}))
    EX.sweep_all(db_session)
    mine = ok(client.get("/wholesale/exceptions?scope=all", headers=_h(db_session, admin)))["items"]
    theirs = ok(client.get("/wholesale/exceptions?scope=all", headers=_h(db_session, other_admin)))["items"]
    assert {i["subject"]["label"].split(",")[0] for i in mine} == {"9 Ours St"}
    assert {i["subject"]["label"].split(",")[0] for i in theirs} == {"8 Theirs St"}
    assert {e.organization_id for e in db_session.query(WholesaleWorkException).filter(
        WholesaleWorkException.title.like("%8 Theirs St%")).all()} == {other.id}


def test_my_work_count_is_per_person(client, db_session, admin, sample_org):
    va = _user(db_session, sample_org, "advisor", "va@trig.test", grant=True)
    plain = _user(db_session, sample_org, "advisor", "plain@trig.test")
    ok(client.post("/wholesale/properties", headers=_h(db_session, admin),
                   json={"street_address": "11 Count St", "city": "Dallas", "state": "TX", "zip_code": "75215"}))
    ok(client.post("/wholesale/properties", headers=_h(db_session, admin),
                   json={"street_address": "12 Count St", "city": "Dallas", "state": "TX", "zip_code": "75215"}))
    EX.sweep(db_session, sample_org.id)
    db_session.commit()
    ids = [e.id for e in _open(db_session, "verify_owner")]
    ok(client.post("/wholesale/exceptions/%s/assign" % ids[0], headers=_h(db_session, admin),
                   json={"assigned_to_id": va.id}))
    s_va = ok(client.get("/wholesale/exceptions/summary", headers=_h(db_session, va)))
    assert s_va == {"assigned_to_me": 1, "manager": False}
    s_admin = ok(client.get("/wholesale/exceptions/summary", headers=_h(db_session, admin)))
    assert s_admin["manager"] is True and s_admin["assigned_to_me"] == 0 and s_admin["unassigned"] == 1
    assert client.get("/wholesale/exceptions/summary", headers=_h(db_session, plain)).status_code == 403
    # Escalating hands it back: the VA's count drops, the owner's escalations rise.
    ok(client.post("/wholesale/exceptions/%s/resolve" % ids[0], headers=_h(db_session, va),
                   json={"outcome": "escalate", "note": "Two people claim to own it"}))
    assert ok(client.get("/wholesale/exceptions/summary", headers=_h(db_session, va)))["assigned_to_me"] == 0
    assert ok(client.get("/wholesale/exceptions/summary", headers=_h(db_session, admin)))["escalated"] == 1
