# -*- coding: utf-8 -*-
"""#452 master pool -> cleaning tenants: exclusivity, release, outcomes. Synthetic data."""
from app.models.acquisition_models import AcquisitionOpportunity
from app.models.models import EmailMessage, Lead
from tests.test_acquisition_pilot import _h, _pool_business, pilot  # noqa: F401


def _enable_other(p):
    assert p["c"].post("/god/acquisition/%s/enable" % p["other"].id, headers=_h(p["db"], p["god"])).status_code == 200


def _assign(p, org, ids, segment=None):
    body = {"destination_org_id": org.id, "prospect_ids": ids}
    if segment:
        body["segment"] = segment
    r = p["c"].post("/god/acquisition/master/assign", json=body, headers=_h(p["db"], p["god"]))
    assert r.status_code == 200, r.text
    return r.json()


def test_one_business_one_client_and_segment_from_the_pool(pilot):
    p = pilot
    _enable_other(p)
    item = _pool_business(p["db"], industry="medical_dental")
    first = _assign(p, p["shine"], [item.id])                     # no segment given: uses the pool's
    assert first["assigned"] == 1
    second = _assign(p, p["other"], [item.id])
    assert second["assigned"] == 0 and "master_already_routed" in second["results"][0]["reasons"]
    assert p["db"].query(AcquisitionOpportunity).filter(
        AcquisitionOpportunity.source == "master_pool:" + item.id).count() == 1
    unknown = _pool_business(p["db"], industry="bakery")
    out = _assign(p, p["shine"], [unknown.id])
    assert out["assigned"] == 0 and "choose_a_segment" in out["results"][0]["reasons"]


def test_master_list_shows_who_when_and_the_real_outcome(pilot):
    p = pilot
    item = _pool_business(p["db"], industry="medical_dental")
    free = _pool_business(p["db"], industry="office", name="Free Office")
    _assign(p, p["shine"], [item.id])
    r = p["c"].get("/god/acquisition/master/prospects?status=assigned", headers=_h(p["db"], p["god"])).json()
    row = next(x for x in r["items"] if x["id"] == item.id)
    assert row["assigned_to"] == p["shine"].name and row["assigned_by"] and row["assigned_at"]
    oc = row["client_outcome"]
    assert oc["stage"] == "discovered" and oc["outreach_started"] is False and oc["appointment"] is False
    avail = p["c"].get("/god/acquisition/master/prospects", headers=_h(p["db"], p["god"])).json()
    ids = [x["id"] for x in avail["items"]]
    assert free.id in ids and item.id not in ids
    for u in (p["owner"], p["outsider"]):
        assert p["c"].get("/god/acquisition/master/prospects", headers=_h(p["db"], u)).status_code == 403


def test_release_before_contact_then_reassign_keeps_history(pilot):
    p = pilot
    _enable_other(p)
    item = _pool_business(p["db"], industry="medical_dental")
    out = _assign(p, p["shine"], [item.id])
    opp_id = out["results"][0]["opportunity_id"]
    assert p["c"].post("/god/acquisition/master/%s/release" % item.id, json={},
                       headers=_h(p["db"], p["god"])).status_code == 422          # reason required
    r = p["c"].post("/god/acquisition/master/%s/release" % item.id, json={"reason": "Better fit elsewhere"},
                    headers=_h(p["db"], p["god"]))
    assert r.status_code == 200 and r.json()["released_from"] == p["shine"].id
    opp = p["db"].query(AcquisitionOpportunity).filter(AcquisitionOpportunity.id == opp_id).one()
    assert opp.stage == "disqualified" and opp.disqualified_reason.startswith("Released")   # kept, not deleted
    assert _assign(p, p["other"], [item.id])["assigned"] == 1
    # the first client can't get it back silently (its closed copy still holds the business)
    assert p["c"].post("/god/acquisition/master/%s/release" % item.id, json={"reason": "x"},
                       headers=_h(p["db"], p["god"])).status_code == 200
    again = _assign(p, p["shine"], [item.id])
    assert again["assigned"] == 0 and "already_in_client_pipeline" in again["results"][0]["reasons"]


def test_release_refused_once_the_client_has_emailed(pilot):
    p = pilot
    item = _pool_business(p["db"], industry="medical_dental")
    out = _assign(p, p["shine"], [item.id])
    opp = p["db"].query(AcquisitionOpportunity).filter(
        AcquisitionOpportunity.id == out["results"][0]["opportunity_id"]).one()
    p["db"].add(EmailMessage(lead_id=opp.lead_id, sender_id=p["god"].id, subject="Hello", body_html="x", status="sent"))
    p["db"].commit()
    r = p["c"].post("/god/acquisition/master/%s/release" % item.id, json={"reason": "x"},
                    headers=_h(p["db"], p["god"]))
    assert r.status_code == 409
    p["db"].refresh(item)
    assert item.routed_org_id == p["shine"].id


def test_assignment_sends_nothing_and_starts_no_clock(pilot):
    p = pilot
    before = p["db"].query(EmailMessage).count()
    items = [_pool_business(p["db"], industry="medical_dental") for _ in range(3)]
    out = _assign(p, p["shine"], [i.id for i in items])
    assert out["assigned"] == 3 and out["sent"] == 0 and out["channel"] == "email_only"
    assert p["db"].query(EmailMessage).count() == before
    ov = p["c"].get("/acquisition-ops/%s/overview" % p["shine"].id, headers=_h(p["db"], p["god"])).json()
    assert ov["profile"]["paused"] is True and ov["profile"]["pilot_started_at"] is None
    assert all(l.contact_channel == "email_only" for l in p["db"].query(Lead).filter(
        Lead.organization_id == p["shine"].id).all())
