"""SCI Command phone screens: priority inbox, family view, real slots,
scheduling through the appointment engine, assign; test contacts excluded."""
from datetime import datetime
from unittest.mock import patch

from app.services.programs import appointments as A
from app.services.programs import responses as R
from tests.test_outreach_program import _h, _lead_for, program  # noqa: F401 (fixture)

SLOTS = [datetime(2030, 1, 8, 10, 0), datetime(2030, 1, 8, 14, 0)]


def test_home_puts_families_first_and_hides_test_contacts(program, sample_advisor, client):
    db, org = program["db"], program["org"]
    hot = _lead_for(db, org, sample_advisor, "L001", first="Lester")
    low = _lead_for(db, org, sample_advisor, "L002", phone="2145550102", first="Preston", email="p@example.com")
    test = _lead_for(db, org, sample_advisor, "L003", phone="2145550103", first="Synthetic", email="s@example.com")
    test.is_test = True
    db.commit()
    with patch("app.services.programs.responses._deliver_staff_alert", return_value=(True, None)):
        R.on_inbound(db, low, "I'm driving with Do Not Disturb on. I'll see your message when I get where I'm going.", "sms")
        R.on_inbound(db, hot, "Can I schedule a visit this week?", "sms")
        R.on_inbound(db, test, "Can I schedule a visit?", "sms")
    r = client.get("/program/command", headers=_h(db, program["admin"])).json()
    names = [i["name"] for i in r["inbox"]]
    assert names[0] == "Lester Testcase" and "Synthetic Testcase" not in names
    assert r["counts"]["needs_attention"] == 1 and r["counts"]["replies_today"] == 1
    assert r["owner_alert"] is None                       # owner-only: a manager never sees it
    s = client.get("/program/command?q=lester", headers=_h(db, program["admin"])).json()
    assert [i["name"] for i in s["inbox"]] == ["Lester Testcase"]


def test_family_view_slots_schedule_and_assign(program, sample_advisor, client, monkeypatch):
    db, org, prog = program["db"], program["org"], program["prog"]
    prog.booking_user_id = sample_advisor.id
    db.commit()
    lead = _lead_for(db, org, sample_advisor, "L001", first="Lester")
    with patch("app.services.programs.responses._deliver_staff_alert", return_value=(True, None)):
        R.on_inbound(db, lead, "Would really like to receive the guide, any questions I would contact you?", "sms")
    h = _h(db, program["admin"])
    f = client.get("/program/command/family/%s" % lead.id, headers=h).json()
    assert f["name"] == "Lester Testcase" and f["thread"][-1]["who"] == "family"
    assert "Send planning guide" in f["suggestion"]["steps"]
    monkeypatch.setattr(A, "available_slots", lambda db, prog, **kw: (list(SLOTS), None))
    sl = client.get("/program/command/slots?lead_id=%s" % lead.id, headers=h).json()
    assert [s["value"] for s in sl["slots"]] == [s.isoformat() for s in SLOTS]
    with patch.object(A, "book", return_value={"ok": True, "status": "confirmed", "booking_id": "b1"}) as bk, \
         patch.object(A, "notify_team_booked") as note:
        r = client.post("/program/command/schedule", headers=h,
                        json={"lead_id": lead.id, "slot": SLOTS[0].isoformat(), "kind": "visit"}).json()
    assert r["status"] == "confirmed" and bk.call_args.args[3] == SLOTS[0] and note.call_count == 1
    a = client.post("/program/command/assign", headers=h, json={"lead_id": lead.id, "user_id": sample_advisor.id})
    assert a.status_code == 200 and a.json()["assigned_to_id"] == sample_advisor.id
