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
