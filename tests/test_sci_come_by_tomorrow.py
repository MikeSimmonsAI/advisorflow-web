"""Henry, Oct 22: "I can come by tomorrow if that is ok with you" got a
"see you tomorrow" hand-off - no time, no link, nothing booked. A later-day
visit is a yes: the family gets two real times that day plus the booking link."""
from datetime import date, datetime

import pytest

from app.services.programs import ai_setter as S, appointments as A
from tests.test_outreach_program import _lead_for, program  # noqa: F401 (fixture)
from tests.test_sci_appointment_setter import _Fake, _d, _ai_turns, _run, setter  # noqa: F401

TODAY = date(2030, 1, 7)          # a Monday
DAY = [datetime(2030, 1, 8, 9, 0), datetime(2030, 1, 8, 14, 0)]


@pytest.mark.parametrize("body", ["Thanks for your text I can come by tomorrow if that is ok with you ",
                                  "I could stop by Friday", "We can come in tomorrow", "I'll swing by Thursday",
                                  "Can we visit tomorrow?"])
def test_a_later_day_visit_is_recognised(body):
    assert S.future_visit_day(body, TODAY) is not None


@pytest.mark.parametrize("body", ["I'll run by there in about 15 minutes", "I can stop by today",
                                  "On my way, I'll come by this afternoon", "I need to cancel my visit tomorrow",
                                  "I'll talk to my wife tomorrow", "Thanks", "Can you come by my house?"])
def test_not_a_later_day_visit(body):
    d = S.future_visit_day(body, TODAY)
    assert d is None or "house" in body       # a home-visit ask is screened by decline_context, not here


def _setup(setter, sample_advisor, monkeypatch, link="https://parkhill.evosyspro.live/abc"):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    seen = {}

    def on_day(db_, prog, d, want=2, **k):
        seen["day"] = d
        return list(DAY), None
    monkeypatch.setattr(A, "slots_on_day", on_day)
    monkeypatch.setattr(A, "_today_central", lambda: datetime(2030, 1, 7, 14, 28))
    monkeypatch.setattr(S, "booking_link_line", lambda db_, lead_: ("Or pick another time here: %s" % link) if link else "")
    return db, lead, seen


def test_henry_handoff_becomes_times_tomorrow_plus_link(setter, sample_advisor, monkeypatch):
    db, lead, seen = _setup(setter, sample_advisor, monkeypatch)
    fake = _Fake([_d("handoff", "That's great, Henry. I'll be ready to meet with you when you come by tomorrow.")])
    resp, conv = _run(db, lead, "Thanks for your text I can come by tomorrow if that is ok with you ", fake)
    text = resp.suggested_reply or _ai_turns(db, conv)[-1].text
    assert seen["day"] == date(2030, 1, 8)
    assert conv.state == "offered" and conv.booked_time is None
    assert A.describe(DAY[0]) in text and A.describe(DAY[1]) in text
    assert "tomorrow" in text and "https://parkhill.evosyspro.live/abc" in text
    assert "ready to meet with you" not in text


def test_no_link_when_links_are_off_still_offers_times(setter, sample_advisor, monkeypatch):
    db, lead, _ = _setup(setter, sample_advisor, monkeypatch, link="")
    fake = _Fake([_d("handoff", "See you tomorrow!")])
    resp, conv = _run(db, lead, "I can come by tomorrow", fake)
    text = resp.suggested_reply or _ai_turns(db, conv)[-1].text
    assert conv.state == "offered" and "http" not in text and A.describe(DAY[0]) in text


def test_arriving_today_still_hands_off(setter, sample_advisor, monkeypatch):
    db, lead, _ = _setup(setter, sample_advisor, monkeypatch)
    fake = _Fake([_d("handoff", "Glad you're coming - the team will be ready for you.")])
    _, conv = _run(db, lead, "I'll run by there in about 15 minutes", fake)
    assert conv.state == "human"


def test_every_offer_by_text_carries_the_booking_link(setter, sample_advisor, monkeypatch):
    db, lead, _ = _setup(setter, sample_advisor, monkeypatch)
    resp, conv = _run(db, lead, "Can I come in?", _Fake([_d("offer_times", "Would {TIMES} work?")]))
    text = resp.suggested_reply or _ai_turns(db, conv)[-1].text
    assert "Or pick another time here: https://parkhill.evosyspro.live/abc" in text


def test_a_persons_text_can_carry_the_booking_link(setter, sample_advisor, client, monkeypatch):
    from app.models.models import Message
    from tests.test_sci_live_email import _h
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    monkeypatch.setattr("app.services.programs.identity.program_booking_url", lambda db_, l: "https://parkhill.evosyspro.live/abc")
    sent = {}

    def fake_send(db_, adv, lead_, text, *a, **k):
        sent["text"] = text
        m = Message(lead_id=lead_.id, sender_id=adv.id, body=text, send_source=k.get("send_source"),
                    twilio_sid="SMb", sent_at=datetime.utcnow())
        db_.add(m)
        db_.commit()
        return m
    monkeypatch.setattr("app.services.sms_service.send_sms", fake_send)
    r = client.post("/program/command/reply", headers=_h(db, setter["admin"]),
                    json={"lead_id": lead.id, "channel": "sms", "idempotency_key": "b1",
                          "text": "Henry, pick a time that works for you here: {BOOKING}"})
    assert r.status_code == 200, r.text
    assert sent["text"] == "Henry, pick a time that works for you here: https://parkhill.evosyspro.live/abc"


# ── hard rule: a yes to coming in never goes out without real times ──────────

def test_a_plain_yes_handed_off_gets_times(setter, sample_advisor, monkeypatch):
    db, lead, _ = _setup(setter, sample_advisor, monkeypatch)
    resp, conv = _run(db, lead, "yes I'll come in", _Fake([_d("handoff", "Great, someone will reach out.")]))
    text = resp.suggested_reply or _ai_turns(db, conv)[-1].text
    assert conv.state == "offered" and A.describe(SLOTS_ANY[0]) in text


def test_an_empty_promise_answer_gets_times(setter, sample_advisor, monkeypatch):
    db, lead, _ = _setup(setter, sample_advisor, monkeypatch)
    resp, conv = _run(db, lead, "Sounds good", _Fake([_d("answer", "Great, looking forward to meeting you!")]))
    text = resp.suggested_reply or _ai_turns(db, conv)[-1].text
    assert conv.state == "offered" and "looking forward to meeting you" not in text


def test_upset_family_still_goes_to_a_person(setter, sample_advisor, monkeypatch):
    db, lead, _ = _setup(setter, sample_advisor, monkeypatch)
    _, conv = _run(db, lead, "I want to come in and talk to a manager about how I was treated",
                   _Fake([_d("handoff", "I'm sorry - I'll reach out to you personally.",
                             understanding={"meaning": "complaint", "mood": "upset"})]))
    assert conv.state == "human"


def test_a_no_with_a_goodbye_is_not_turned_into_times(setter, sample_advisor, monkeypatch):
    db, lead, _ = _setup(setter, sample_advisor, monkeypatch)
    _, conv = _run(db, lead, "No thanks, we don't need anything", _Fake([_d("close", "Understood. Take care, see you around.")]))
    assert conv.state != "offered"


SLOTS_ANY = None


@pytest.fixture(autouse=True)
def _slots_any():
    global SLOTS_ANY
    from tests.test_sci_appointment_setter import SLOTS
    SLOTS_ANY = SLOTS


# ── stuck families: yes to coming in, an hour later still no time ────────────

def _stuck(db, org, sample_advisor, said, minutes_ago, sent=None, src="L001", phone="2145550101"):
    from datetime import timedelta
    from app.models.models import Message
    from app.models.program_models import ProgramConversationTurn
    lead = _lead_for(db, org, sample_advisor, src, phone=phone)
    conv = S.conversation_for(db, lead, create=True)
    at = datetime.utcnow() - timedelta(minutes=minutes_ago)
    conv.state, conv.last_family_at = "human", at
    db.add(ProgramConversationTurn(conversation_id=conv.id, organization_id=org.id, lead_id=lead.id,
                                   role="family", text=said, created_at=at))
    if sent:
        db.add(Message(lead_id=lead.id, sender_id=sample_advisor.id, body=sent, sent_at=at + timedelta(minutes=2)))
    db.commit()
    return lead


def test_stuck_families_are_found(setter, sample_advisor):
    from app.services.programs import stuck_visits as SV
    db, org = setter["db"], setter["org"]
    henry = _stuck(db, org, sample_advisor, "I can come by tomorrow if that is ok with you", 70)
    _stuck(db, org, sample_advisor, "I can come by tomorrow", 20, src="L003", phone="2145550103")    # not an hour yet
    _stuck(db, org, sample_advisor, "We can come in Friday", 90, sent="Pick a time: https://x.example/b",
           src="L004", phone="2145550104")
    _stuck(db, org, sample_advisor, "Thanks, not interested", 90, src="L002", phone="2145550199")
    found = SV.find(db, org.id)
    assert [f["lead_id"] for f in found] == [henry.id]


def test_day_slots_lists_every_open_time(setter, sample_advisor, client, monkeypatch):
    from tests.test_sci_live_email import _h
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    times = [datetime(2030, 1, 8, h, 0) for h in (9, 10, 11, 13, 14)]
    monkeypatch.setattr(A, "_today_central", lambda: datetime(2030, 1, 7, 14, 0))
    monkeypatch.setattr("app.routers.calendar_router.compute_day_slots",
                        lambda db_, adv, day, **kw: {"slots": [x.strftime("%Y-%m-%dT%H:%M:00") for x in times]})
    r = client.get("/program/command/day-slots?lead_id=%s&date=2030-01-08" % lead.id, headers=_h(db, setter["admin"]))
    assert r.status_code == 200, r.text
    assert [s["time"] for s in r.json()["slots"]] == ["9:00 AM", "10:00 AM", "11:00 AM", "1:00 PM", "2:00 PM"]


def test_practice_run_has_henrys_conversation():
    from app.services.programs.setter_eval_scenarios import REGRESSIONS
    assert any("come by tomorrow if that is ok" in s["turns"][0]["text"] for s in REGRESSIONS)


def test_stuck_family_pages_the_team_once_a_day(setter, sample_advisor, monkeypatch):
    from app.models.program_models import ProgramAlert
    from app.services.programs import stuck_visits as SV
    db, org = setter["db"], setter["org"]
    monkeypatch.setattr("app.services.sms_programs.sci_org_id", lambda db_: org.id)
    _stuck(db, org, sample_advisor, "I can come by tomorrow if that is ok with you", 70)
    assert SV.alert_all(db) == 1
    assert SV.alert_all(db) == 0                                    # once per family per day
    rows = db.query(ProgramAlert).filter(ProgramAlert.dedupe_key.like("stuck_visit:%")).all()
    assert rows and all("wants to come in" in (r.message or "") for r in rows)


# ── soft no (James, Oct 22: "Close it for now") ─────────────────────────────

@pytest.mark.parametrize("body,soft", [("Close it for now\n\nSent from my iPhone", True),
                                       ("Not right now, maybe later", True), ("Please hold off for now", True),
                                       ("Not now, but I can come in next week", False), ("yes", False),
                                       ("I can come by tomorrow", False), ("what is this", False)])
def test_soft_no_is_recognised(body, soft):
    assert S.is_soft_no(body) is soft


def test_soft_no_sends_nothing_and_hands_to_a_person(setter, sample_advisor, monkeypatch):
    db, lead, _ = _setup(setter, sample_advisor, monkeypatch)
    fake = _Fake([_d("close", "Thank you for letting me know. Take care.")])
    _, conv = _run(db, lead, "Close it for now\n\nSent from my iPhone", fake)
    assert conv.state == "human" and "Call them once and offer a short visit" in conv.handoff_reason
    assert not fake.calls                                       # the AI was never asked - nothing drafted
    assert not [t for t in _ai_turns(db, conv) if t.text]


# ── HUMAN HANDOFF red flag (Mike, Oct 22) ──────────────────────────────────

def test_a_handoff_is_flagged_in_sci_live_until_handled(setter, sample_advisor, client, monkeypatch):
    from tests.test_sci_live_email import _h
    db, lead, _ = _setup(setter, sample_advisor, monkeypatch)
    _run(db, lead, "Close it for now", _Fake([]))
    h = _h(db, setter["admin"])
    home = client.get("/program/command", headers=h).json()
    item = [i for i in home["inbox"] if i["lead_id"] == lead.id][0]
    assert item["human_handoff"] is True and "Soft no" in item["handoff_reason"]
    assert home["filter_counts"]["handoff"] == 1 and home["counts"]["human_handoff"] == 1
    only = client.get("/program/command?filter=handoff", headers=h).json()["inbox"]
    assert [i["lead_id"] for i in only] == [lead.id]
    fam = client.get("/program/command/family/%s" % lead.id, headers=h).json()
    assert fam["human_handoff"] is True
    client.post("/program/responses/%s/mark" % fam["response_id"], headers=h, json={"state": "closed"})
    fam = client.get("/program/command/family/%s" % lead.id, headers=h).json()
    assert fam["human_handoff"] is False                                  # handled: the red flag clears


def test_handoff_alert_text_says_human_handoff():
    from app.services.programs import alert_ledger as L
    assert L.sms_line("handoff", "Henry Simmons", detail="x").startswith("SCI HUMAN HANDOFF | Henry Simmons")
