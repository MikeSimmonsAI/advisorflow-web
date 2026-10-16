"""SCI appointment setter: the five real conversations (Oscar, Larry, Preston,
Lester, Marion) and every guard - opt-out, auto-reply, human takeover, unsafe
text, AI failure, taken slot, duplicate booking, staging booking through the
real confirmation path, and SCI never reaching the generic auto-reply.

The AI is a scripted fake here (its WORDING is exercised live in staging);
these tests prove what the code around it does with each decision.
"""
import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.models.models import BookingLink, Message
from app.models.program_models import ProgramConversation, ProgramConversationTurn, ProgramResponse
from app.services.programs import ai_setter, appointments as A
from app.services.programs import responses as R
from app.services.programs.reply_rules import AUTO_REPLY, classify
from tests.test_outreach_program import _lead_for, program  # noqa: F401 (fixture)


class _Fake:
    """chat.completions.create returning scripted JSON decisions in order."""

    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.calls = []
        self.chat = MagicMock()
        self.chat.completions.create.side_effect = self._create

    def _create(self, **kw):
        self.calls.append(kw)
        d = self.decisions.pop(0)
        r = MagicMock()
        r.choices = [MagicMock()]
        r.choices[0].message.content = json.dumps(d)
        r.usage = None
        return r


def _d(action, reply="", **kw):
    base = {"intent": "x", "action": action, "reply": reply, "slot_choice": 0, "requested_time": "",
            "appointment_kind": "", "service_type": "", "callback_window": "", "reason": "test"}
    base.update(kw)
    return base


SLOTS = [datetime(2030, 1, 8, 10, 0), datetime(2030, 1, 9, 14, 0)]


@pytest.fixture
def setter(program, sample_advisor, monkeypatch):
    db, org, prog = program["db"], program["org"], program["prog"]
    prog.booking_user_id = sample_advisor.id
    prog.ai_setter_mode = "draft"
    prog.alert_phone, prog.alert_email = "+18435550000", "owner@example.com"
    prog.staff_sms_alerts_enabled = True
    db.commit()
    monkeypatch.setenv("AI_BACKGROUND_AUTOMATION_ENABLED", "true")
    monkeypatch.setattr(A, "available_slots", lambda db, prog, **kw: (list(SLOTS), None))
    monkeypatch.setattr(A, "still_free", lambda db, prog, slot, *a, **k: slot in SLOTS)
    from app.services import ai_gateway
    ai_gateway._reset_for_tests()
    return program


def _run(db, lead, text, fake, mode=None):
    with patch.object(ai_setter, "_get_client", return_value=fake), \
         patch("app.services.programs.responses._deliver_staff_alert", return_value=(True, None)):
        resp = R.on_inbound(db, lead, text, "sms")
    conv = ai_setter.conversation_for(db, lead, create=False)
    return resp, conv


def _ai_turns(db, conv):
    return [t for t in db.query(ProgramConversationTurn).filter_by(conversation_id=conv.id, role="ai")
            .order_by(ProgramConversationTurn.created_at).all()]


# ── auto-reply detection (Preston) ──────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "I'm driving with Do Not Disturb While Driving turned on. I'll see your message when I get where I'm going.",
    "Auto-reply: I am currently driving.",
    "I'm out of the office until Monday.",
])
def test_auto_replies_are_recognised(text):
    assert classify(text)["class"] == AUTO_REPLY


@pytest.mark.parametrize("text", ["Can you call me later today?", "I'll call you", "I'm on a house right now"])
def test_people_are_not_auto_replies(text):
    assert classify(text)["class"] != AUTO_REPLY


def test_preston_driving_autoreply_gets_no_answer_no_page_and_cadence_continues(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    fake = _Fake([])
    with patch("app.services.programs.responses._deliver_staff_alert") as staff:
        with patch.object(ai_setter, "_get_client", return_value=fake):
            resp = R.on_inbound(db, lead, "I'm driving with Do Not Disturb on. I'll see your message when I get where I'm going.", "sms")
    conv = ai_setter.conversation_for(db, lead, create=False)
    assert resp.response_class == AUTO_REPLY and not resp.cadence_paused
    assert staff.call_count == 0 and fake.calls == []                 # nobody paged, no AI, no reply
    assert conv.state == "waiting" and conv.next_followup_at is not None
    assert not _ai_turns(db, conv)
    from app.services.programs import spacing
    assert not spacing.family_replied_since(db, lead.id, datetime.utcnow() - timedelta(days=1))


# ── Lester: wants the guide, has questions -> guide, then times, then booked ─

def test_lester_guide_then_offer_then_booked(setter, sample_advisor, monkeypatch):
    monkeypatch.setenv("SCI_SMS_LINKS", "on")                       # links in texts switched back on
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001", first="Lester")
    lead.is_test = True
    db.commit()
    fake = _Fake([
        _d("send_guide", "Happy to. Here is the planning guide, and I'm glad to answer any questions."),
        _d("offer_times", "Would a short visit help? I have {TIMES}.", appointment_kind="visit"),
        _d("book", "", slot_choice=2),
    ])
    _run(db, lead, "Would really like this to receive guide, any questions I would contact you. Is this possible?", fake)
    _run(db, lead, "Yes, I'd like to talk about it in person.", fake)
    _, conv = _run(db, lead, "Wednesday works for me.", fake)
    texts = [t.text for t in _ai_turns(db, conv)]
    assert "planning-guide" in texts[0] or "http" in texts[0]
    assert A.describe(SLOTS[0]) in texts[1] and A.describe(SLOTS[1]) in texts[1]
    assert conv.state == "booked" and conv.booked_time == SLOTS[1]
    assert all(t.delivery == "simulated" for t in _ai_turns(db, conv))   # synthetic: nothing left
    assert "SIMULATED" in _ai_turns(db, conv)[-1].reason                  # never claimed as a real booking
    assert db.query(BookingLink).filter_by(lead_id=lead.id).count() == 0  # production: calendar untouched


# ── Oscar: "I'll call you" -> respect it, offer a callback time ─────────────

def test_oscar_callback_request_hands_to_person(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001", first="Oscar")
    fake = _Fake([
        _d("answer", "Of course, Oscar. Call whenever it suits you. Or if it's easier, I can call you - what time is good?"),
        _d("callback", "Perfect, I'll call you tomorrow afternoon.", callback_window="tomorrow afternoon"),
    ])
    _run(db, lead, "I'll call you", fake)
    resp, conv = _run(db, lead, "Tomorrow afternoon is fine, have her call me", fake)
    assert conv.state == "callback_requested" and conv.callback_window == "tomorrow afternoon"
    assert resp.suggested_reply.startswith("Perfect")          # draft mode: a person sends it
    assert [t.delivery for t in _ai_turns(db, conv)] == ["draft", "draft"]


# ── Larry: busy, reconnect later ─────────────────────────────────────────────

def test_larry_busy_gets_a_gentle_answer_not_times(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001", first="Larry")
    fake = _Fake([_d("answer", "No problem at all, Larry. When is a better time to reconnect?")])
    resp, conv = _run(db, lead, "Will get back with you later, I'm on a house", fake)
    assert conv.state == "ai_active" and not A.offered(conv)
    assert "better time" in resp.suggested_reply


# ── Marion: existing plan -> plan review, asks which kind, no urgency ────────

def test_marion_existing_plan_offer_review_and_unsafe_text_is_held(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001", first="Marion")
    fake = _Fake([
        _d("answer", "Thank you, Marion. Would you like to review your prearrangement file or your flower care service?"),
        _d("offer_times", "Prices go up next month, so act now - {TIMES}?"),
        _d("offer_times", "Prices go up next month, so act now - {TIMES}?"),   # told once, does it again
    ])
    _run(db, lead, "I already have a plan with you", fake)
    resp, conv = _run(db, lead, "The prearrangement file", fake)
    held = _ai_turns(db, conv)[-1]
    assert held.delivery == "blocked" and "pressure" in held.reason       # fabricated urgency never goes out
    assert conv.state == "human"
    assert "broke these rules" in fake.calls[-1]["messages"][1]["content"]  # it was given one chance to fix it


# ── guards ───────────────────────────────────────────────────────────────────

def test_opt_out_stops_everything(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    fake = _Fake([])
    _, conv = _run(db, lead, "STOP", fake)
    assert conv.state == "stopped" and fake.calls == [] and not _ai_turns(db, conv)


def test_ai_failure_hands_off_and_sends_nothing(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    broken = MagicMock()
    broken.chat.completions.create.side_effect = RuntimeError("provider down")
    _, conv = _run(db, lead, "What does the guide include?", broken)
    assert conv.state == "human" and not _ai_turns(db, conv)


def test_a_person_texting_takes_the_conversation_over(setter, sample_advisor):
    from app.services import send_source as ss
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    fake = _Fake([_d("answer", "Glad to help - what questions do you have?")])
    _run(db, lead, "What does the guide include?", fake)
    db.add(Message(lead_id=lead.id, sender_id=sample_advisor.id, body="Kerry here", send_source=ss.MANUAL,
                   sent_at=datetime.utcnow() + timedelta(seconds=5)))
    db.commit()
    fake2 = _Fake([])
    _, conv = _run(db, lead, "Thanks Kerry", fake2)
    assert fake2.calls == [] and conv.state == "human"


def test_auto_mode_sends_from_the_program_line_with_its_own_source(setter, sample_advisor, monkeypatch):
    from app.services import send_source as ss
    db, org, prog = setter["db"], setter["org"], setter["prog"]
    prog.ai_setter_mode = "auto"
    db.commit()
    lead = _lead_for(db, org, sample_advisor, "L001")
    monkeypatch.setattr("app.services.contact_hours.check", lambda lead, now=None: {"permitted": True})
    sent = []

    def fake_send(db_, adv, lead_, text, **kw):
        sent.append((text, kw.get("send_source")))
        m = Message(lead_id=lead_.id, sender_id=adv.id, body=text, send_source=kw.get("send_source"))
        db_.add(m)
        db_.flush()
        return m
    with patch("app.services.sms_service.send_sms", side_effect=fake_send):
        _, conv = _run(db, lead, "What does the guide include?", _Fake([_d("answer", "It covers your options and wishes. Any questions?")]))
    assert sent == [("It covers your options and wishes. Any questions?", ss.SCI_SETTER)]
    assert _ai_turns(db, conv)[0].delivery == "sent"


def test_auto_mode_outside_hours_holds_then_sweep_sends(setter, sample_advisor, monkeypatch):
    db, org, prog = setter["db"], setter["org"], setter["prog"]
    prog.ai_setter_mode = "auto"
    db.commit()
    lead = _lead_for(db, org, sample_advisor, "L001")
    allowed = {"v": False}
    monkeypatch.setattr("app.services.contact_hours.check", lambda lead, now=None: {"permitted": allowed["v"]})
    monkeypatch.setattr("app.services.contact_hours.next_permitted_utc", lambda lead, now=None: datetime.utcnow())
    _, conv = _run(db, lead, "What does the guide include?", _Fake([_d("answer", "Happy to explain. Any questions?")]))
    assert _ai_turns(db, conv)[0].delivery == "held"
    allowed["v"] = True
    with patch("app.services.sms_service.send_sms", return_value=MagicMock(id="m1")) as s:
        assert ai_setter.sweep(db)["sent"] == 1
    assert s.call_count == 1 and _ai_turns(db, conv)[0].delivery == "sent"


def test_taken_time_is_never_booked_and_new_times_are_offered(setter, sample_advisor, monkeypatch):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    lead.is_test = True
    db.commit()
    fake = _Fake([_d("offer_times", "Would {TIMES} work?"), _d("book", "", slot_choice=1)])
    _run(db, lead, "Can I come in this week?", fake)
    monkeypatch.setattr(A, "still_free", lambda db, prog, slot, *a, **k: False)
    _, conv = _run(db, lead, "The first one", fake)
    assert conv.state == "offered" and conv.booked_time is None
    assert "just filled" in _ai_turns(db, conv)[-1].text


# ── staging: a real booking through the booking page's own confirm path ─────

def test_staging_books_for_real_once_and_pages_nobody_for_a_test_contact(setter, sample_advisor, monkeypatch):
    db, org = setter["db"], setter["org"]
    monkeypatch.setenv("APP_ENV", "staging")
    lead = _lead_for(db, org, sample_advisor, "L001")
    lead.is_test = True
    lead.phone = None
    db.commit()
    fake = _Fake([_d("offer_times", "Would {TIMES} work for a visit?", appointment_kind="visit"),
                  _d("book", "", slot_choice=1), _d("book", "", slot_choice=1)])
    with patch("app.services.programs.responses._deliver_staff_alert", return_value=(True, None)) as staff:
        _run(db, lead, "I'd like to come in", fake)
        _, conv = _run(db, lead, "First one please", fake)
        assert conv.state == "booked" and conv.booked_time == SLOTS[0]
        link = db.query(BookingLink).filter_by(lead_id=lead.id).one()
        assert link.status in ("booked", "confirmed") and link.booked_time == SLOTS[0]
        # the same choice again never makes a second booking
        res = A.book(db, setter["prog"], lead, SLOTS[0], "visit")
        assert res["status"] == "confirmed" and db.query(BookingLink).filter_by(lead_id=lead.id).count() == 1
    assert staff.call_count == 0                                        # synthetic: team not paged


def test_generic_auto_reply_never_answers_an_sci_family(setter, sample_advisor, monkeypatch):
    from app.services import pipeline_service
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    monkeypatch.setattr("app.services.sms_programs.is_sci_org", lambda db, org_id: True)
    assert pipeline_service.process_inbound_reply(db, lead, sample_advisor, MagicMock())["action"] == "sci_program"


def test_simulator_runs_the_setter_as_the_persons_manual_action(setter, client, monkeypatch):
    from tests.test_outreach_program import _h
    db = setter["db"]
    monkeypatch.setenv("AI_BACKGROUND_AUTOMATION_ENABLED", "false")      # production: background AI off
    setter["prog"].ai_setter_mode = "off"
    db.commit()
    fake = _Fake([_d("offer_times", "Would {TIMES} work for a call?", appointment_kind="phone")])
    with patch.object(ai_setter, "_get_client", return_value=fake):
        r = client.post("/program/setter/simulate", headers=_h(db, setter["admin"]),
                        json={"persona": "lester", "first_name": "Lester", "text": "Can we talk this week?"}).json()
    assert r["result"]["action"] == "offer_times" and len(r["conversation"]["offered"]) == 2
    assert r["conversation"]["turns"][-1]["delivery"] == "simulated"
    assert setter["prog"].ai_setter_mode == "off"                       # program setting untouched


def test_never_books_a_time_the_family_did_not_choose(setter, sample_advisor):
    """Live run, Oct 14: Marion answered "The prearrangement file" and the AI
    tried to book the first time. The family's words must pick the time."""
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001", first="Marion")
    lead.is_test = True
    db.commit()
    fake = _Fake([_d("answer", "Thank you, Marion. Would you like to review your prearrangement file or your flower care service?"),
                  _d("offer_times", "Would {TIMES} work for a review?"), _d("book", "", slot_choice=1)])
    _run(db, lead, "I already have a plan with you", fake)
    _run(db, lead, "The prearrangement file", fake)
    _, conv = _run(db, lead, "Sounds good to me", fake)
    assert conv.state == "offered" and conv.booked_time is None
    assert "Which works better" in _ai_turns(db, conv)[-1].text


def test_two_questions_are_rewritten_to_one(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    fake = _Fake([_d("answer", "Would you like a call? Or a visit?"),
                  _d("answer", "Would a short call or a visit suit you better?")])
    resp, conv = _run(db, lead, "Tell me more", fake)
    assert resp.suggested_reply == "Would a short call or a visit suit you better?" and len(fake.calls) == 2


def test_a_malformed_ai_answer_gets_one_correction(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001", first="Oscar")
    fake = _Fake([_d(""), _d("answer", "Of course, Oscar - call whenever suits you. Would you like me to call you instead?")])
    resp, conv = _run(db, lead, "I'll call you", fake)
    assert len(fake.calls) == 2 and conv.state == "ai_active" and resp.suggested_reply.startswith("Of course")


def test_asking_for_a_day_offers_times_that_day(setter, sample_advisor, monkeypatch):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    lead.is_test = True
    db.commit()
    day = [datetime(2030, 1, 11, 10, 0), datetime(2030, 1, 11, 15, 0)]
    monkeypatch.setattr(A, "slots_on_day", lambda db, prog, d, want=2, **k: (day, None))
    fake = _Fake([_d("offer_times", "Would {TIMES} work?"),
                  _d("book", "", requested_time="2030-01-11T09:00")])
    _run(db, lead, "Can I come in?", fake)
    _, conv = _run(db, lead, "Can you do Friday?", fake)
    t = _ai_turns(db, conv)[-1].text
    assert conv.state == "offered" and conv.booked_time is None
    assert A.describe(day[0]) in t and A.describe(day[1]) in t and "filled" not in t


def test_the_afternoon_one_books_the_afternoon_time(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    lead.is_test = True
    db.commit()
    fake = _Fake([_d("offer_times", "Would {TIMES} work?"), _d("book", "", slot_choice=0)])
    _run(db, lead, "Can I come in?", fake)
    _, conv = _run(db, lead, "The afternoon one", fake)
    assert conv.state == "booked" and conv.booked_time == SLOTS[1]          # SLOTS[1] is 2 PM


def test_a_named_day_shapes_the_offer_even_when_the_ai_just_offers(setter, sample_advisor, monkeypatch):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    seen = {}

    def on_day(db, prog, d, want=2, **k):
        seen["day"] = d
        return [datetime(2030, 1, 11, 10, 0), datetime(2030, 1, 11, 15, 0)], None
    monkeypatch.setattr(A, "slots_on_day", on_day)
    resp, conv = _run(db, lead, "A visit would help. Can you do Friday?", _Fake([_d("offer_times", "Would {TIMES} work?")]))
    assert seen["day"].weekday() == 4 and "January 11" in resp.suggested_reply


def test_with_sms_links_off_a_guide_request_becomes_a_visit_invitation(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    fake = _Fake([_d("send_guide", "Happy to. Here is the planning guide.")])
    out, conv = _run(db, lead, "Can you send me the guide?", fake)
    texts = [t.text for t in _ai_turns(db, conv)]
    assert "http" not in texts[0] and "email" not in texts[0].lower()
    assert "in person" in texts[0] and conv.state == "offered" and len(A.offered(conv)) == 2   # invited in, real times
