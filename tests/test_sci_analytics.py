"""SCI Live performance: every number equals its drill-down, touches are
separated, tests / forecasts / opt-outs never enter results, and a family's
journey shows its whole recorded history."""
from datetime import datetime, timedelta
from unittest.mock import patch

from app.models.models import BookingLink, CadenceState, CadenceTouchLog, Message
from app.services.programs import responses as R
from tests.test_outreach_program import _h, _lead_for, program  # noqa: F401 (fixture)


def _touch(db, lead, n, *, status="delivered", code=None, at=None, outcome="sent"):
    st = db.query(CadenceState).filter_by(lead_id=lead.id).one()
    at = at or datetime.utcnow() - timedelta(minutes=30)
    m = Message(lead_id=lead.id, sender_id=lead.assigned_to_id, body="Touch %s for %s" % (n, lead.first_name),
                twilio_sid="SM%s%s" % (lead.id[:6], n), twilio_status=status, delivery_status=status,
                error_code=code, sent_at=at, send_source="cadence")
    db.add(m)
    db.flush()
    db.add(CadenceTouchLog(cadence_state_id=st.id, lead_id=lead.id, organization_id=lead.organization_id,
                           touch_number=n, attempt_seq=1, channel="sms", outcome=outcome, attempted_at=at,
                           message_id=m.id, body_preview=m.body))
    db.commit()


def _seed(program, advisor):
    db, org = program["db"], program["org"]
    a = _lead_for(db, org, advisor, "L001", first="Lester")
    b = _lead_for(db, org, advisor, "L002", phone="2145550102", first="Marion", email="m@example.com")
    t = _lead_for(db, org, advisor, "L003", phone="2145550103", first="Synthetic", email="s@example.com")
    t.is_test = True
    db.commit()
    _touch(db, a, 1, at=datetime.utcnow() - timedelta(days=3))
    _touch(db, a, 2)
    _touch(db, b, 1, status="undelivered", code="30007")
    _touch(db, t, 1)
    with patch("app.services.programs.responses._deliver_staff_alert", return_value=(True, None)):
        R.on_inbound(db, a, "Can I come by on Tuesday?", "sms")
        R.on_inbound(db, t, "Can I schedule a visit?", "sms")
    return db, a, b, t


def test_every_number_matches_its_drill_down(program, sample_advisor, client):
    db, a, b, t = _seed(program, sample_advisor)
    h = _h(db, program["admin"])
    s = client.get("/program/command/analytics?range=7d", headers=h).json()
    m = {k: v["value"] for k, v in s["metrics"].items()}
    assert m["attempts"] == 3 and m["families_contacted"] == 2          # test family excluded
    assert m["new_families"] == 2 and m["followup_families"] == 1
    assert m["delivered"] == 2 and m["failed"] == 1 and m["replies"] == 1 and m["repliers"] == 1
    assert m["loaded"] == 2 and m["appointments"] == 0
    assert s["rates"]["avg_touches_per_family"] == 1.5
    for k, v in m.items():
        d = client.get("/program/command/analytics/records?metric=%s&range=7d" % k, headers=h).json()
        assert d["count"] == v == len(d["items"]), k
        assert all(i["name"] != "Synthetic Testcase" for i in d["items"])
    fail = client.get("/program/command/analytics/records?metric=failed&range=7d", headers=h).json()
    assert fail["items"][0]["error"] == "Carrier filtered (link)" and fail["items"][0]["name"] == "Marion Testcase"


def test_touches_are_separated_and_today_differs_from_the_week(program, sample_advisor, client):
    db, a, b, t = _seed(program, sample_advisor)
    h = _h(db, program["admin"])
    s = client.get("/program/command/analytics?range=7d", headers=h).json()
    by = {x["touch"]: x for x in s["touches"]}
    assert by["1"]["attempts"] == 2 and by["2"]["attempts"] == 1 and by["3"]["attempts"] == 0
    assert by["2"]["replies"] == 1 and by["1"]["replies"] == 0           # reply follows touch 2
    d = client.get("/program/command/analytics/records?metric=attempts&range=7d&touch=2", headers=h).json()
    assert d["count"] == by["2"]["attempts"]
    today = client.get("/program/command/analytics?range=today", headers=h).json()
    assert today["metrics"]["new_families"]["value"] == 1 and today["metrics"]["followup_families"]["value"] == 1
    assert today["metrics"]["attempts"]["all_time"] == 3
    assert s["projections"]["is_projection"] is True


def test_opted_out_and_test_bookings_are_not_appointment_results(program, sample_advisor, client):
    db, a, b, t = _seed(program, sample_advisor)
    for lead in (a, b, t):
        db.add(BookingLink(token="tok-%s" % lead.id[:8], lead_id=lead.id, user_id=sample_advisor.id,
                           status="booked", booked_time=datetime.utcnow() + timedelta(days=2),
                           calendar_event_id="ev-%s" % lead.id[:6], created_at=datetime.utcnow()))
    b.status = "dnc"
    db.commit()
    s = client.get("/program/command/analytics?range=today", headers=_h(db, program["admin"])).json()
    assert s["metrics"]["appointments"]["value"] == 1                    # only Lester


def test_journey_shows_the_complete_history(program, sample_advisor, client):
    db, a, b, t = _seed(program, sample_advisor)
    j = client.get("/program/command/analytics/journey/%s" % a.id, headers=_h(db, program["admin"])).json()
    kinds = [e["kind"] for e in j["events"]]
    assert kinds.count("touch") == 2 and "reply" in kinds and "imported" in kinds
    assert j["touches_sent"] == 2 and j["stage"] == "In conversation"
    jb = client.get("/program/command/analytics/journey/%s" % b.id, headers=_h(db, program["admin"])).json()
    failed = [e for e in jb["events"] if e["kind"] == "touch"][0]
    assert failed["result"] == "failed" and failed["error"] == "Carrier filtered (link)"


def test_drill_down_rejects_unknown_filters(program, sample_advisor, client):
    db = program["db"]
    h = _h(db, program["admin"])
    assert client.get("/program/command/analytics/records?metric=nope", headers=h).status_code == 400
    assert client.get("/program/command/analytics/records?metric=attempts&touch=9", headers=h).status_code == 400
