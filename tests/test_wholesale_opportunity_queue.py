"""Opportunity readiness, the "act first" queue, contact investigations and the
acquisition funnel (app/services/evosense/readiness.py).

The rules:
  * every step is read from existing records - nothing is scored, guessed or
    filled in, and missing data is named;
  * a property that passed its search's threshold with no phone or email
    becomes a contact investigation a person owns (not a dead-end status);
  * adding a contact closes it; a wrong number reopens it at once;
  * the queue puts the most advanced opportunity first;
  * nothing here looks anything up or sends anything.
"""
import json

import pytest

from app.models.evosense_models import EvoSenseMessage, EvoSenseProperty, EvoSenseStrategy
from app.models.wholesale_models import WholesaleWorkException
from app.services import wholesale_exceptions as WX
from app.services.evosense import common as C
from app.services.evosense import evaluate as EV
from app.services.evosense import ingest as IN
from app.services.evosense import providers as PV
from app.services.evosense import readiness as RD
from app.services.evosense import strategy as ST

from tests.test_wholesale_source_to_deal import county_records


@pytest.fixture()
def strategy(db_session, sample_org):
    clean, problems = ST.validate(ST.starter_values("behind_on_taxes"))
    assert not problems, problems
    s = EvoSenseStrategy(organization_id=sample_org.id, is_test=False)
    ST.apply(s, clean)
    db_session.add(s)
    db_session.flush()
    ST.transition(s, "activate")
    db_session.commit()
    return s


def _house(db, org, strategy, street="4410 FICTION GROVE DR", apn="9990001", **owner_over):
    recs = json.loads(json.dumps(county_records()))
    for r in recs:
        rec = r["record"]
        rec["street_address"] = street
        rec["parcel_apn"] = apn
        rec["source_reference"] = rec["source_reference"].replace("9990001", apn)
        if rec.get("owner"):
            rec["owner"].update(owner_over)
        for sg in rec.get("signals") or []:
            if sg.get("ref"):
                sg["ref"] = sg["ref"] + apn
        IN.ingest(db, org, PV.PROVIDERS[r["provider"]], r["capability"], rec, strategy_id=strategy.id)
    db.commit()
    p = db.query(EvoSenseProperty).filter_by(organization_id=org, parcel_apn=apn).one()
    EV.rescore(db, p, strategy)
    db.commit()
    return p


def _low(db, org, strategy):
    p = EvoSenseProperty(organization_id=org, street_address="12 QUIET LN", city="Fort Worth", state="TX",
                         zip_code="76105", county="Tarrant", opportunity_score=20, status=C.S_LOW,
                         best_strategy_id=strategy.id)
    db.add(p)
    db.commit()
    return p


def test_readiness_names_each_step_and_the_next_action(db_session, sample_org, strategy):
    db, org = db_session, sample_org.id
    p = _house(db, org, strategy)
    r = RD.for_property(db, p, strategy)
    steps = {s["key"]: s for s in r["steps"]}
    assert steps["evidence"]["state"] == RD.DONE and steps["evidence"]["threshold"] == 45
    assert any(s["type"] == "TAX_DELINQUENT" and "tarrant_tax_roll" in s["sources"]
               for s in steps["evidence"]["signals"])
    # Missing facts are named, never filled in.
    assert any("Equity" in m for m in steps["evidence"]["missing"])
    assert any("Vacancy" in m for m in steps["evidence"]["missing"])
    assert steps["owner"]["state"] == RD.DONE and steps["owner"]["owner"]["name"] == "ROBIN & PAT ETAL SAMPLE"
    assert steps["contact"]["state"] == RD.TODO
    assert any("Door-knock" in w for w in steps["contact"]["ways"])
    assert r["next"]["step"] == "contact" and r["next"]["action"] == "Find a way to reach the owner"


def test_sweep_opens_one_investigation_and_only_for_properties_worth_working(
        db_session, sample_org, strategy):
    db, org = db_session, sample_org.id
    p = _house(db, org, strategy)
    low = _low(db, org, strategy)
    made = WX.sweep(db, org, include_test=False)
    db.commit()
    assert made.get("verify_contact") == 1
    ex = db.query(WholesaleWorkException).filter_by(kind="verify_contact").one()
    assert ex.subject_id == p.id and ex.priority == RD.investigation_priority(p.opportunity_score)
    assert "Owner of record: ROBIN & PAT ETAL SAMPLE" in ex.detail and "mailing 1 EXAMPLE CT" in ex.detail
    assert WX.sweep(db, org, include_test=False).get("verify_contact") is None    # no duplicate
    assert db.query(WholesaleWorkException).filter_by(subject_id=low.id).count() == 0
    # The queue item shows the score so the person sees why it is first.
    sub = WX._subject(db, org, ex)
    assert sub["opportunity_score"] == p.opportunity_score and sub["property_id"] == p.id


def test_title_flags_on_low_scoring_houses_stay_out_of_the_queue(db_session, sample_org, strategy):
    db, org = db_session, sample_org.id
    # The tax roll's cut-off name alone (no TAD record) leaves a flagged owner.
    rec = json.loads(json.dumps(county_records()[0]["record"]))
    rec["signals"] = []                                      # nothing that scores
    rec["source_reference"] = "TCTAX:LOWFLAG"
    rec["parcel_apn"] = "LOWFLAG"
    IN.ingest(db, org, PV.PROVIDERS["tarrant_tax_roll"], "TAX", rec, strategy_id=strategy.id)
    db.commit()
    p = db.query(EvoSenseProperty).filter_by(parcel_apn="LOWFLAG").one()
    EV.rescore(db, p, strategy)
    db.commit()
    assert (p.opportunity_score or 0) < 45
    WX.sweep(db, org, include_test=False)
    assert db.query(WholesaleWorkException).filter_by(kind="title_ownership_anomaly").count() == 0


def test_adding_a_contact_closes_the_investigation_and_a_wrong_number_reopens_it(
        client, db_session, sample_org, strategy, admin_auth_headers):
    db, org = db_session, sample_org.id
    p = _house(db, org, strategy)
    WX.sweep(db, org, include_test=False)
    db.commit()
    r = client.post("/wholesale/evosense/properties/%s/contacts" % p.id, headers=admin_auth_headers,
                    json={"kind": "phone", "value": "817-555-0164", "person_name": "Owner"})
    assert r.status_code == 200, r.text
    db.expire_all()
    ex = db.query(WholesaleWorkException).filter_by(kind="verify_contact").one()
    assert ex.status == "complete" and "Phone added by" in ex.outcome_note
    detail = client.get("/wholesale/evosense/properties/%s/readiness" % p.id, headers=admin_auth_headers).json()
    assert detail["next"]["step"] == "conversation"
    contact = [s for s in detail["steps"] if s["key"] == "contact"][0]
    assert contact["state"] == "done" and "5550164" not in json.dumps(detail)      # masked
    cp_id = client.get("/wholesale/evosense/properties/%s" % p.id, headers=admin_auth_headers).json()["contacts"][0]["id"]
    r = client.post("/wholesale/evosense/properties/%s/contacts/%s/wrong-party" % (p.id, cp_id),
                    headers=admin_auth_headers)
    assert r.status_code == 200 and r.json()["investigation_id"]
    db.expire_all()
    open_ = db.query(WholesaleWorkException).filter(WholesaleWorkException.kind == "verify_contact",
                                                    WholesaleWorkException.status == "open").one()
    assert "wrong person" in open_.detail


def test_queue_puts_the_answered_seller_first_and_funnel_shows_where_it_stops(
        client, db_session, sample_org, strategy, admin_auth_headers):
    db, org = db_session, sample_org.id
    first = _house(db, org, strategy, street="1 FIRST ST", apn="1110001")
    second = _house(db, org, strategy, street="2 SECOND ST", apn="2220002",
                    mailing_street="2 OTHER WAY")
    _low(db, org, strategy)
    client.post("/wholesale/evosense/properties/%s/contacts" % second.id, headers=admin_auth_headers,
                json={"kind": "phone", "value": "817-555-0177", "person_name": "Owner"})
    r = client.post("/wholesale/evosense/properties/%s/reply" % second.id, headers=admin_auth_headers,
                    json={"text": "Yes I'd sell. It's vacant since my dad died and needs a new roof. "
                                  "I'd take around 120,000 and could close in 30 days.",
                          "delivery": "manual_entry"})
    assert r.status_code == 200, r.text
    q = client.get("/wholesale/evosense/opportunity-queue", headers=admin_auth_headers).json()
    order = [i["property_id"] for i in q["items"]]
    assert order[:2] == [second.id, first.id], q["by_step"]
    top = q["items"][0]
    assert top["next"]["step"] == "deal"
    said = [s for s in top["steps"] if s["key"] == "qualified"][0]["facts"]
    assert all(f["basis"] == "SELLER STATED" and f["quote"] for f in said)          # their words, quoted
    assert q["total"] == 2                                                           # the low one is not here
    f = client.get("/wholesale/evosense/funnel", headers=admin_auth_headers).json()
    by = {s["key"]: s for s in f["stages"]}
    assert by["discovered"]["count"] == 3 and by["worth_review"]["count"] == 2
    assert by["contact"]["count"] == 1 and by["talked"]["count"] == 1 and by["handoff"]["count"] == 1
    assert by["deals"]["count"] == 0 and by["appointments"]["count"] is None        # unknown, not zero
    assert f["stuck_at"] is not None
    # Nothing was sent to anyone.
    assert db.query(EvoSenseMessage).filter_by(direction="outbound").count() == 0


def test_institutional_owner_gets_no_investigation(db_session, sample_org, strategy):
    db, org = db_session, sample_org.id
    _house(db, org, strategy, street="9 CIVIC ST", apn="3330003", name="CITY OF FORT WORTH",
           name_truncated=False)
    WX.sweep(db, org, include_test=False)
    assert db.query(WholesaleWorkException).filter_by(kind="verify_contact").count() == 0


def test_field_login_cannot_read_the_queue_or_funnel(client, db_session, sample_advisor, admin_auth_headers,
                                                     auth_headers):
    r = client.post("/wholesale/team/members", headers=admin_auth_headers,
                    json={"user_id": sample_advisor.id, "role": "canvasser", "confirm_lock": True})
    assert r.status_code in (200, 201), r.text
    assert client.get("/wholesale/evosense/opportunity-queue", headers=auth_headers).status_code == 403
    assert client.get("/wholesale/evosense/funnel", headers=auth_headers).status_code == 403


@pytest.mark.parametrize("name,expected", [
    ("PNC BANK N A", "corporation"), ("BANK OF THE OZARKS", "corporation"),
    ("AMERICAN AIRLINES FEDERAL CREDIT UNION", "corporation"),
    ("WILMINGTON SAVINGS FUND SOCIETY", "corporation"), ("WACHOVIA BANK NATIONAL ASSOC", "corporation"),
    ("SECRETARY OF VETERANS AFFAIRS", "government"),
    # people stay people
    ("ROBIN SAMPLE", "individual"), ("BANKS ROBERT", "individual"), ("NA THANH", "individual"),
])
def test_lenders_are_companies_not_people(name, expected):
    assert IN.owner_type_of(name) == expected
