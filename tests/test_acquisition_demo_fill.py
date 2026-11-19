# -*- coding: utf-8 -*-
"""Owner-requested example data for a demo company, and the client's own
availability. Sends nothing."""
from app.models.models import EmailMessage
from tests.test_acquisition_pilot import _h, pilot  # noqa: F401


def test_demo_fill_builds_every_tool_and_runs_once(pilot):
    p = pilot
    g = _h(p["db"], p["god"])
    r = p["c"].put("/acquisition-ops/%s/service-ops" % p["shine"].id, json={"ops_enabled": True}, headers=g)
    assert r.status_code == 200, r.text
    out = p["c"].post("/god/acquisition/clients/%s/demo-fill" % p["shine"].id, headers=g)
    assert out.status_code == 200, out.text
    j = out.json()
    assert j["status"] == "filled", j
    assert not j["skipped"], j["skipped"]
    h = _h(p["db"], p["owner"], p["shine"].id)
    d = p["c"].get("/acquisition/dashboard", headers=h).json()
    assert d["counts"]["upcoming"] == 3 and d["counts"]["won"] == 2 and d["won_monthly_value"] == 2400
    for path, key in (("/service-ops/customers", "items"), ("/service-ops/jobs", "items"), ("/service-ops/schedules", "items"),
                      ("/service-ops/estimates", "items"), ("/service-ops/invoices", "items"), ("/service-ops/team", "items")):
        rows = p["c"].get(path, headers=h).json()[key]
        assert rows, path
    assert p["c"].post("/god/acquisition/clients/%s/demo-fill" % p["shine"].id, headers=g).json()["status"] == "already_filled"
    assert p["db"].query(EmailMessage).count() == 0
    # not for the client
    assert p["c"].post("/god/acquisition/clients/%s/demo-fill" % p["shine"].id, headers=h).status_code in (401, 403)


def test_client_sets_own_availability_but_not_the_calendar_owner(pilot):
    p = pilot
    h = _h(p["db"], p["owner"], p["shine"].id)
    r = p["c"].put("/acquisition/availability", json={"booking_windows": {"mon": [["09:00", "12:00"]], "wed": [["13:00", "17:00"]]},
                                                       "appointment_minutes": 45, "min_notice_hours": 24,
                                                       "booking_user_id": "someone-else"}, headers=h)
    assert r.status_code == 200, r.text
    c = p["c"].get("/acquisition/calendar", headers=h).json()
    assert c["booking_windows"]["mon"] == [["09:00", "12:00"]] and c["appointment_minutes"] == 45
    assert c["booking_user"] is None or c["booking_user"]["id"] != "someone-else"
    bad = p["c"].put("/acquisition/availability", json={"booking_windows": {"mon": [["12:00", "09:00"]]}}, headers=h)
    assert bad.status_code == 422
