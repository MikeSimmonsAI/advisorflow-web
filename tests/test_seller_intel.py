"""Seller intelligence: facts with provenance, Seller Intent, qualification
outcomes and nurture - through the real endpoints, with the deterministic
readers (AI qualification off) so every expectation is exact.
"""
import json

import pytest

from app.models.models import Lead, SuppressionEntry
from app.models.sms_consent_models import SmsConsentRecord
from app.models.wholesale_models import (WholesaleDeal, WholesaleSellerFact,
                                         WholesaleSellerProfile, WholesaleSettings)
from app.services import wholesale_seller_intel as SI

from test_wholesale_seller_sms_program import (_headers, _user, form, post,  # noqa: F401
                                               world)


@pytest.fixture()
def seller(client, world):
    db, o = world["db"], world["orgs"]
    s = db.query(WholesaleSettings).filter_by(organization_id=o["evo"].id).one()
    s.ai_qualification_enabled = False
    db.commit()
    post(client, form(submission_id="si", sms_consent=True))
    h = _headers(db, _user(db, o["evo"]))
    profile = db.query(WholesaleSellerProfile).one()
    deal = db.query(WholesaleDeal).one()
    return {"db": db, "h": h, "profile": profile, "deal": deal, "orgs": o}


def _reply(client, seller, text):
    r = client.post("/wholesale/deals/%s/seller-reply" % seller["deal"].id, headers=seller["h"],
                    json={"message": text})
    assert r.status_code == 200, r.text
    return r.json()


def _intel(client, seller):
    r = client.get("/wholesale/sellers/%s/intelligence" % seller["profile"].id, headers=seller["h"])
    assert r.status_code == 200, r.text
    return r.json()


def test_the_inquiry_form_is_recorded_as_seller_stated_facts(client, seller):
    intel = _intel(client, seller)
    facts = {f["fact_type"]: f for f in intel["facts"]}
    assert facts["willing_to_sell"]["extracted_by"] == "form"
    assert facts["timeline"]["value"] == "90_days" and facts["timeline"]["quote"].startswith("timeline")
    assert all(f["truth_state"] == "seller_stated" and not f["verified"] for f in intel["facts"])
    assert all(f["message_ref"] == "form:si" for f in intel["facts"])
    # interest + a timeline inside the window: qualified, with what is still unknown named
    q = intel["qualification"]
    assert q["status"] == SI.QUALIFIED and "decision_maker" in q["known_unknowns"]
    assert intel["contactability"]["state"] == "CONTACTABLE_OTHER"   # program off: a person calls


def test_a_reply_becomes_facts_that_quote_the_message(client, seller):
    out = _reply(client, seller, "Can you make me an offer? I would take 150k and close asap.")
    assert out["outcome"] == "WANTS_OFFER"
    facts = {f["fact_type"]: f for f in _intel(client, seller)["facts"]}
    assert facts["offer_request"]["quote"].startswith("Can you make me an offer")
    assert facts["asking_price"]["value"].startswith("150000")
    assert facts["asking_price"]["message_ref"].startswith("reply:")
    # the older form timeline was superseded, not deleted
    db = seller["db"]
    old = db.query(WholesaleSellerFact).filter_by(fact_type="timeline", superseded=True).count()
    assert old == 1
    assert _intel(client, seller)["seller_intent"]["value"] >= 50


def test_not_now_nurtures_and_the_deal_is_not_dead(client, seller):
    out = _reply(client, seller, "Not right now, maybe next year.")
    assert out["qualification_outcome"]["status"] == SI.NURTURE
    db = seller["db"]
    db.expire_all()
    p = db.query(WholesaleSellerProfile).one()
    assert p.nurture_until is not None and p.nurture_reason
    assert db.query(WholesaleDeal).one().stage != "dead"


def test_stop_disqualifies_suppresses_and_withdraws_consent(client, seller):
    out = _reply(client, seller, "STOP")
    assert out["qualification_outcome"]["status"] == SI.DISQUALIFIED
    db = seller["db"]
    db.expire_all()
    assert db.query(Lead).one().status == "dnc"
    assert db.query(SuppressionEntry).filter_by(phone="12145550123").count() == 1
    assert db.query(SmsConsentRecord).one().status == "opted_out"
    # and nurture cannot put them back into play
    r = client.post("/wholesale/sellers/%s/nurture" % seller["profile"].id, headers=seller["h"],
                    json={"days": 30})
    assert r.status_code == 409


def test_an_ownership_complication_goes_to_a_person(client, seller):
    out = _reply(client, seller, "My brother and I inherited it, the estate is still in probate.")
    assert out["qualification_outcome"]["status"] == SI.HUMAN_REVIEW
    assert "ownership" in " ".join(out["qualification_outcome"]["reasons"]).lower()


def test_a_long_timeline_nurtures_rather_than_rejects(client, seller):
    db = seller["db"]
    r = client.patch("/wholesale/sellers/%s" % seller["profile"].id, headers=seller["h"],
                     json={"timeline": "no_rush"})
    assert r.status_code == 200
    q = _intel(client, seller)["qualification"]
    assert q["status"] == SI.NURTURE and "beyond" in q["reasons"][0]
    # an operator's entry is a human-entered fact
    f = db.query(WholesaleSellerFact).filter_by(fact_type="timeline", superseded=False).one()
    assert f.truth_state == "human_entered" and f.extracted_by == "person"


def test_missing_criteria_mean_more_information_not_rejection(client, seller):
    db, o = seller["db"], seller["orgs"]
    r = client.patch("/wholesale/settings", headers=seller["h"], json={
        "qualification_criteria": {"require": ["selling_interest", "timeline", "decision_maker",
                                               "asking_price"]}})
    assert r.status_code == 200, r.text
    q = _intel(client, seller)["qualification"]
    assert q["status"] == SI.NEEDS_MORE_INFORMATION
    assert set(q["missing"]) == {"decision_maker", "asking_price"}
    bad = client.patch("/wholesale/settings", headers=seller["h"],
                       json={"qualification_criteria": {"require": ["vibes"]}})
    assert bad.status_code == 400


def test_only_a_person_verifies_a_fact(client, seller):
    fid = _intel(client, seller)["facts"][0]["id"]
    r = client.post("/wholesale/sellers/%s/facts/%s/verify" % (seller["profile"].id, fid),
                    headers=seller["h"])
    assert r.status_code == 200
    f = [x for x in r.json()["facts"] if x["id"] == fid][0]
    assert f["verified"] is True and f["truth_state"] == "seller_stated"


def test_a_returning_seller_leaves_nurture(client, seller):
    db = seller["db"]
    client.post("/wholesale/sellers/%s/nurture" % seller["profile"].id, headers=seller["h"],
                json={"days": 90, "reason": "after the holidays"})
    db.expire_all()
    assert db.query(WholesaleSellerProfile).one().nurture_until is not None
    post(client, form(submission_id="si-2", timeline="asap"))
    db.expire_all()
    p = db.query(WholesaleSellerProfile).one()
    assert p.nurture_until is None and p.qualification_status == SI.QUALIFIED


def test_seller_intent_is_not_the_property_score(client, seller):
    db = seller["db"]
    p = db.query(WholesaleSellerProfile).one()
    detail = json.loads(p.seller_intent_detail)
    assert detail["version"].startswith("seller_intent/")
    assert all("opportunity" not in f["label"].lower() for f in detail["factors"])
