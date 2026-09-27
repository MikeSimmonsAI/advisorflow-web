"""NEEDS YOU, the Morning Command Center and the seller lifecycle view.

  * NEEDS YOU holds only work a person must do, each with WHY and evidence;
    a routine inquiry, a nurture timer or an opted-out seller never appears;
  * test records stay out unless asked for;
  * nothing crosses tenants;
  * the lifecycle is a view over existing states - never stored, never a
    fourth status system.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.wholesale_models import (WholesaleDeal, WholesaleSellerProfile,
                                         WholesaleSettings)
from app.services import wholesale_command as WC

from test_wholesale_seller_sms_program import (KEY_B, _headers, _user, form, post,  # noqa: F401
                                               world)


@pytest.fixture()
def cc(client, world):
    db, o = world["db"], world["orgs"]
    for org in (o["evo"], o["other"]):
        s = db.query(WholesaleSettings).filter_by(organization_id=org.id).one()
        s.ai_qualification_enabled = False
    db.commit()
    post(client, form(submission_id="cc1"))
    h = _headers(db, _user(db, o["evo"]))
    deal = db.query(WholesaleDeal).one()
    return {"db": db, "o": o, "h": h, "deal": deal}


def _center(client, h, **params):
    r = client.get("/wholesale/command-center", headers=h, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_a_routine_inquiry_is_not_needs_you(client, cc):
    body = _center(client, cc["h"])
    assert body["needs_you"]["count"] == 0
    for section in ("new_qualified", "seller_replies", "appointments", "awaiting_contact_data",
                    "nurture", "provider_problems", "pipeline_movement"):
        assert section in body


def test_a_seller_asking_for_an_offer_is_needs_you_with_why_and_evidence(client, cc):
    r = client.post("/wholesale/deals/%s/seller-reply" % cc["deal"].id, headers=cc["h"],
                    json={"message": "Can you make me an offer? I'd take 150k."})
    assert r.status_code == 200
    items = _center(client, cc["h"])["needs_you"]["items"]
    top = items[0]
    assert top["kind"] == "seller_request" and "offer" in top["title"].lower()
    assert "person decides" in top["why"]
    assert any("make me an offer" in e for e in top["evidence"])
    assert top["link"] == "/wholesale/deals/%s" % cc["deal"].id


def test_a_verified_request_leaves_needs_you(client, cc):
    client.post("/wholesale/deals/%s/seller-reply" % cc["deal"].id, headers=cc["h"],
                json={"message": "Can you make me an offer?"})
    item = _center(client, cc["h"])["needs_you"]["items"][0]
    prof = item["profile_id"]
    for fid in item["fact_ids"]:
        client.post("/wholesale/sellers/%s/facts/%s/verify" % (prof, fid), headers=cc["h"])
    assert all(i["kind"] != "seller_request" for i in _center(client, cc["h"])["needs_you"]["items"])


def test_an_opted_out_seller_never_appears(client, cc):
    client.post("/wholesale/deals/%s/seller-reply" % cc["deal"].id, headers=cc["h"],
                json={"message": "make me an offer"})
    client.post("/wholesale/deals/%s/seller-reply" % cc["deal"].id, headers=cc["h"],
                json={"message": "STOP"})
    assert _center(client, cc["h"])["needs_you"]["count"] == 0


def test_nurture_due_is_listed_not_escalated(client, cc):
    db = cc["db"]
    p = db.query(WholesaleSellerProfile).one()
    p.nurture_until = datetime.utcnow() - timedelta(days=1)
    p.nurture_reason = "Not now"
    db.commit()
    body = _center(client, cc["h"])
    assert body["nurture"]["due"] == 1 and body["needs_you"]["count"] == 0


def test_test_records_stay_out_unless_asked_for(client, cc):
    db = cc["db"]
    cc["deal"].is_test = True
    db.query(WholesaleSellerProfile).one().is_test = True
    db.commit()
    client.post("/wholesale/deals/%s/seller-reply" % cc["deal"].id, headers=cc["h"],
                json={"message": "Can you make me an offer?"})
    assert _center(client, cc["h"])["needs_you"]["count"] == 0
    assert _center(client, cc["h"], include_test=True)["needs_you"]["count"] == 1


def test_another_workspace_sees_nothing_of_this_one(client, cc):
    client.post("/wholesale/deals/%s/seller-reply" % cc["deal"].id, headers=cc["h"],
                json={"message": "Can you make me an offer?"})
    theirs = _headers(cc["db"], _user(cc["db"], cc["o"]["other"]))
    body = _center(client, theirs, include_test=True)
    assert body["needs_you"]["count"] == 0 and body["seller_replies"]["count"] == 0
    r = client.get("/wholesale/needs-you", headers=theirs)
    assert r.status_code == 200 and r.json()["count"] == 0


# ── the lifecycle is a view ─────────────────────────────────────────────────

def _L(**kw):
    return WC.seller_lifecycle(**kw)["stage"]


def test_lifecycle_reads_existing_states():
    lead = SimpleNamespace(status="new")
    assert _L() == WC.DISCOVERED
    assert _L(lead=lead) == WC.OWNER_IDENTIFIED
    assert _L(lead=lead, contactability="CONTACT_DATA_FOUND") == WC.CONTACTABILITY_PENDING
    assert _L(lead=lead, contactability="CONTACTABLE_SMS") == WC.CONTACTABLE
    assert _L(lead=lead, contactability="CONTACTABLE_SMS",
              deal=SimpleNamespace(stage="ready_for_outreach")) == WC.OUTREACH_ELIGIBLE
    assert _L(lead=lead, engagement_status="active") == WC.ENGAGED
    assert _L(lead=lead, profile=SimpleNamespace(qualification_status="NEEDS_MORE_INFORMATION")) \
        == WC.QUALIFYING
    assert _L(lead=lead, profile=SimpleNamespace(qualification_status="QUALIFIED")) == WC.QUALIFIED
    assert _L(lead=lead, deal=SimpleNamespace(stage="analysis")) == WC.OPPORTUNITY
    assert _L(lead=lead, profile=SimpleNamespace(qualification_status="NURTURE")) == WC.NURTURE
    assert _L(lead=SimpleNamespace(status="dnc"), deal=SimpleNamespace(stage="analysis")) \
        == WC.DO_NOT_CONTACT
    assert _L(lead=lead, engagement_status="stopped") == WC.UNREACHABLE
    assert _L(lead=lead, profile=SimpleNamespace(qualification_status="HUMAN_REVIEW")) \
        == WC.REVIEW_REQUIRED
    life = WC.seller_lifecycle(lead=lead, contactability="CONTACTABLE_SMS")
    assert life["derived_from"]["contactability"] == "CONTACTABLE_SMS"


def test_seller_intelligence_carries_the_lifecycle(client, cc):
    p = cc["db"].query(WholesaleSellerProfile).one()
    body = client.get("/wholesale/sellers/%s/intelligence" % p.id, headers=cc["h"]).json()
    assert body["lifecycle"]["stage"] in WC.LIFECYCLE
    assert body["lifecycle"]["derived_from"]["qualification"] == body["qualification"]["status"]
