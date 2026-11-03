# -*- coding: utf-8 -*-
"""#452 Phase 4: outcomes, won contracts, follow-up requests, period reports.
Synthetic data only. Nothing here sends a message."""
from app.models.acquisition_models import AcquisitionFollowUp
from app.models.models import EmailMessage
from tests.test_acquisition_delivery_boundary import _book
from tests.test_acquisition_pilot import _add, _h, _move, pilot  # noqa: F401


def _delivered(p):
    opp = _add(p)
    _move(p, opp, "interested", note="Wants a walkthrough")
    _book(p, opp)
    return opp


def test_outcome_sequence_won_contract_history_and_confirmation(pilot):
    p = pilot
    opp = _delivered(p)
    h = _h(p["db"], p["owner"], p["shine"].id)
    post = lambda body: p["c"].post("/acquisition/prospects/%s/outcome" % opp, json=body, headers=h)  # noqa: E731
    assert post({"outcome": "appointment_held"}).status_code == 200
    assert post({"outcome": "quote_issued", "quote_amount": 2400, "monthly_value": 2400}).status_code == 200
    assert post({"outcome": "won", "monthly_value": 2200, "contract_type": "bogus"}).status_code == 422
    r = post({"outcome": "won", "monthly_value": 2200, "contract_type": "recurring", "service_frequency": "3x weekly",
              "contract_value": 26400, "contract_start": "2026-12-01"})
    assert r.status_code == 200
    d = p["c"].get("/acquisition/prospects/%s" % opp, headers=h).json()
    assert [e["label"] for e in d["history"] if e["kind"] == "stage"] == [
        "Appointment booked", "Appointment held", "Quote issued", "Won"]
    assert all(e["at"] for e in d["history"])
    assert d["contract"]["contract_type"] == "recurring" and d["contract"]["confirmed"] is False
    rep = p["c"].get("/acquisition/report?period=all", headers=h).json()
    assert rep["reported_monthly_value"] == 2200 and rep["confirmed_monthly_value"] == 0
    assert rep["appointments_booked"] == 1 and rep["visits_held"] == 1 and rep["contracts_won"] == 1
    # EvoSys confirms; the customer can't
    assert p["c"].post("/acquisition-ops/%s/prospects/%s/confirm-won" % (p["shine"].id, opp),
                       headers=h).status_code == 403
    g = _h(p["db"], p["god"])
    assert p["c"].post("/acquisition-ops/%s/prospects/%s/confirm-won" % (p["shine"].id, opp),
                       headers=g).json()["confirmed"] is True
    rep = p["c"].get("/acquisition/report?period=all", headers=h).json()
    assert rep["confirmed_monthly_value"] == 2200


def test_followup_request_reaches_evosys_once_and_sends_nothing(pilot):
    p = pilot
    opp = _delivered(p)
    h = _h(p["db"], p["owner"], p["shine"].id)
    body = {"opportunity_id": opp, "reason": "reschedule", "note": "Owner is out Tuesday"}
    first = p["c"].post("/acquisition/followups", json=body, headers=h).json()
    again = p["c"].post("/acquisition/followups", json=dict(body, note="Thursday works"), headers=h).json()
    assert first["created"] is True and again["created"] is False and again["id"] == first["id"]
    assert p["db"].query(AcquisitionFollowUp).count() == 1
    assert p["c"].post("/acquisition/followups", json={"reason": "nope"}, headers=h).status_code == 422
    assert p["c"].post("/acquisition/followups", json={"reason": "other"}, headers=h).status_code == 422
    g = _h(p["db"], p["god"])
    q = p["c"].get("/god/acquisition/followups?status=open", headers=g).json()["items"]
    assert len(q) == 1 and q[0]["client"] == p["shine"].name and q[0]["business_name"]
    assert "Thursday works" in q[0]["note"]
    # closing needs a note, then the customer sees the status
    url = "/acquisition-ops/%s/followups/%s" % (p["shine"].id, first["id"])
    assert p["c"].post(url, json={"status": "done"}, headers=g).status_code == 422
    assert p["c"].post(url, json={"status": "done", "resolution_note": "Moved to Thursday 10am"},
                       headers=g).json()["status"] == "done"
    mine = p["c"].get("/acquisition/followups", headers=h).json()["items"]
    assert mine[0]["status"] == "done" and mine[0]["resolution_note"] == "Moved to Thursday 10am"
    assert p["db"].query(EmailMessage).count() == 0


def test_followups_and_reports_stay_inside_their_client(pilot, db_session):
    import json
    from app.services.acquisition import engine as E
    p = pilot
    opp = _delivered(p)
    p["other"].enabled_features = json.dumps(list(E.CLIENT_FEATURES))
    db_session.commit()
    other = _h(p["db"], p["outsider"], p["other"].id)
    assert p["c"].post("/acquisition/followups", json={"opportunity_id": opp, "reason": "missing_info"},
                       headers=other).status_code == 404
    assert p["c"].get("/acquisition/followups", headers=other).json()["items"] == []
    assert p["c"].get("/acquisition/report?period=all", headers=other).json()["appointments_booked"] == 0
    for path in ("/god/acquisition/followups", "/god/acquisition/report"):
        assert p["c"].get(path, headers=other).status_code == 403


def test_period_filters(pilot):
    p = pilot
    _delivered(p)
    h = _h(p["db"], p["owner"], p["shine"].id)
    for per in ("7d", "month", "90d", "all"):
        assert p["c"].get("/acquisition/report?period=%s" % per, headers=h).json()["appointments_booked"] == 1
    assert p["c"].get("/acquisition/report?period=last_month", headers=h).json()["appointments_booked"] == 0
    assert p["c"].get("/acquisition/report?period=custom", headers=h).status_code == 422
    assert p["c"].get("/acquisition/report?period=custom&start=2020-01-01&end=2020-01-31",
                      headers=h).json()["appointments_booked"] == 0
    g = _h(p["db"], p["god"])
    allr = p["c"].get("/god/acquisition/report?period=all", headers=g).json()
    assert allr["totals"]["appointments_booked"] == 1
