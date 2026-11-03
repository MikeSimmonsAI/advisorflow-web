# -*- coding: utf-8 -*-
"""#452 owner decision 2026-11-03: EvoSys sells BOOKED APPOINTMENTS, not leads.

Internal allocation of a master-pool prospect to a cleaning client never shows
the client anything. The client's own login sees an opportunity only once a
real appointment is booked for them, and its login can't reach any general
lead / CRM / report API that would expose EvoSys's working records."""
from datetime import datetime, timedelta

from app.models.acquisition_models import AcquisitionOpportunity
from app.models.models import BookingLink
from tests.test_acquisition_master import _assign
from tests.test_acquisition_pilot import _add, _h, _move, _pool_business, pilot  # noqa: F401


def _book(p, opp_id):
    db = p["db"]
    o = db.query(AcquisitionOpportunity).get(opp_id)
    bl = BookingLink(lead_id=o.lead_id, user_id=p["god"].id, status="booked",
                     booked_time=datetime.utcnow() + timedelta(days=2), appt_label="Walkthrough", appt_duration=45,
                     calendar_event_id="evt-test")
    db.add(bl)
    db.commit()
    o.booking_link_id = bl.id
    db.commit()
    assert _move(p, opp_id, "appointment_booked").status_code == 200


def test_internal_allocation_is_invisible_to_the_customer(pilot):
    p = pilot
    item = _pool_business(p["db"], industry="medical_dental")
    out = _assign(p, p["shine"], [item.id])
    opp = out["results"][0]["opportunity_id"]
    h = _h(p["db"], p["owner"], p["shine"].id)                      # the cleaning company's own login
    assert p["c"].get("/acquisition/prospects", headers=h).json()["items"] == []
    assert p["c"].get("/acquisition/appointments", headers=h).json()["all"] == []
    assert p["c"].get("/acquisition/prospects/%s" % opp, headers=h).status_code == 404
    d = p["c"].get("/acquisition/dashboard", headers=h).json()
    assert d["counts"]["delivered"] == 0 and "in_research" not in d["counts"] and d["new_interest"] == []
    # interest is still EvoSys-internal
    _move(p, opp, "interested", note="Asked for a walkthrough")
    assert p["c"].get("/acquisition/prospects", headers=h).json()["items"] == []
    assert p["c"].post("/acquisition/prospects/%s/outcome" % opp, json={"outcome": "lost"},
                       headers=h).status_code == 404


def test_booked_appointment_is_delivered_without_evosys_internals(pilot):
    p = pilot
    opp = _add(p, email="owner@oaklawn.example")
    _move(p, opp, "interested", note="Wants Tuesday")
    _book(p, opp)
    h = _h(p["db"], p["owner"], p["shine"].id)
    items = p["c"].get("/acquisition/prospects", headers=h).json()["items"]
    assert [x["id"] for x in items] == [opp]
    row = items[0]
    for secret in ("email", "interest_note", "lead_id", "source", "source_url", "evidence", "score"):
        assert secret not in row
    assert row["appointment"]["time"] and row["booked_at"]
    d = p["c"].get("/acquisition/dashboard", headers=h).json()
    assert d["counts"]["delivered"] == 1 and d["counts"]["upcoming"] == 1
    assert d["by_segment"] == [{"segment": "medical_dental", "label": d["by_segment"][0]["label"], "count": 1}]


def test_practice_and_unbooked_late_stages_never_reach_the_customer(pilot):
    p = pilot
    prac = _add(p, is_practice=True)
    _move(p, prac, "interested", note="practice")
    _book(p, prac)
    lost = _add(p)
    _move(p, lost, "interested", note="maybe")
    _move(p, lost, "lost", reason="Went with another vendor")        # lost BEFORE any appointment
    h = _h(p["db"], p["owner"], p["shine"].id)
    assert p["c"].get("/acquisition/prospects", headers=h).json()["items"] == []


def test_customer_login_cannot_reach_general_lead_apis(pilot):
    p = pilot
    item = _pool_business(p["db"], industry="office")
    out = _assign(p, p["shine"], [item.id])
    lead_id = p["db"].query(AcquisitionOpportunity).get(out["results"][0]["opportunity_id"]).lead_id
    h = _h(p["db"], p["owner"], p["shine"].id)
    for path in ("/leads", "/leads/%s" % lead_id, "/pipeline", "/acquisition-ops/clients",
                 "/acquisition-ops/%s/prospects" % p["shine"].id, "/god/acquisition/hub"):
        r = p["c"].get(path, headers=h)
        assert r.status_code in (403, 404), (path, r.status_code)
        assert lead_id not in r.text and item.name not in r.text
    # what the portal needs still works
    assert p["c"].get("/acquisition/status", headers=h).status_code == 200
    assert p["c"].get("/acquisition/profile", headers=h).status_code == 200


def test_owner_still_sees_the_internal_allocation(pilot):
    p = pilot
    item = _pool_business(p["db"], industry="office")
    out = _assign(p, p["shine"], [item.id])
    opp = out["results"][0]["opportunity_id"]
    g = _h(p["db"], p["god"])
    items = p["c"].get("/acquisition-ops/%s/prospects" % p["shine"].id, headers=g).json()["items"]
    assert opp in [x["id"] for x in items]


def test_a_real_appointment_cannot_be_marked_booked_by_hand(pilot):
    p = pilot
    opp = _add(p)
    _move(p, opp, "interested", note="Wants a walkthrough")
    r = _move(p, opp, "appointment_booked")
    assert r.status_code == 409 and "calendar" in r.text
    h = _h(p["db"], p["owner"], p["shine"].id)
    assert p["c"].get("/acquisition/prospects", headers=h).json()["items"] == []
