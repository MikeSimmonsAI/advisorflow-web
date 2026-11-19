# -*- coding: utf-8 -*-
"""Client-filled setup (2026-11-18): the client saves her own details step by
step, may only ADD to the never-contact list, agrees to the offer without
changing it, and finishing shows "ready for review" to EvoSys. A no-show
keeps its reason."""
from tests.test_acquisition_outcomes_followups import _delivered
from tests.test_acquisition_pilot import _h, pilot  # noqa: F401


def test_client_setup_steps_offer_and_ready_for_review(pilot):
    p = pilot
    h = _h(p["db"], p["owner"], p["shine"].id)
    c = p["c"]
    r = c.put("/acquisition/profile", json={"business_name": "Shine Co", "contact_phone": "901-555-0100", "setup_step": "business"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["setup"]["steps"] == ["business"]
    r = c.put("/acquisition/profile", json={"never_contact": ["acmedental.com", "boss@acme.com"], "setup_step": "never"}, headers=h).json()
    assert r["setup"]["never_contact_count"] == 2 and r["setup"]["steps"] == ["business", "never"]
    # adding again never duplicates and never removes
    r = c.put("/acquisition/profile", json={"never_contact": ["acmedental.com"]}, headers=h).json()
    assert r["setup"]["never_contact_count"] == 2
    # can't finish before agreeing to the offer
    assert c.post("/acquisition/setup/complete", headers=h).status_code == 422
    before = r["offer_type"]
    a = c.post("/acquisition/setup/accept-offer", headers=h)
    assert a.status_code == 200 and a.json()["setup"]["offer_accepted_at"] and a.json()["offer_type"] == before
    # the client can't change the offer type
    c.put("/acquisition/profile", json={"offer_type": "growth_pilot_250"}, headers=h)
    assert c.get("/acquisition/profile", headers=h).json()["offer_type"] == before
    done = c.post("/acquisition/setup/complete", headers=h)
    assert done.status_code == 200 and done.json()["setup"]["completed_at"]
    g = _h(p["db"], p["god"])
    items = c.get("/god/acquisition/directory", headers=g).json()["items"]
    me = next(i for i in items if i["organization_id"] == p["shine"].id)
    assert me["client_setup"] == "done" and me["offer_accepted_at"]
    # setup never starts outreach
    ov = c.get("/acquisition-ops/%s/overview" % p["shine"].id, headers=g).json()
    assert ov["profile"]["paused"] is True


def test_outsider_cannot_finish_another_clients_setup(pilot):
    p = pilot
    h = _h(p["db"], p["outsider"], p["shine"].id)
    assert p["c"].post("/acquisition/setup/accept-offer", headers=h).status_code in (402, 403, 404)


def test_no_show_keeps_its_reason(pilot):
    p = pilot
    opp = _delivered(p)
    h = _h(p["db"], p["owner"], p["shine"].id)
    r = p["c"].post("/acquisition/prospects/%s/outcome" % opp, json={"outcome": "no_show", "reason": "They cancelled"}, headers=h)
    assert r.status_code == 200, r.text
    assert "No-show reason: They cancelled" in (r.json().get("outcome_note") or "")
