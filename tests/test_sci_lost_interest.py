"""SCI: replies that lose interest, defer, or ask in Spanish - regression tests
for the audited fixes (opt-out carrying a request, held replies and agreed
follow-up dates, auto-replies on a person's conversation, draft mode and the
SLA reminder, "who is this?", Spanish / misspelt words, day parts, "I'm in",
and follow-ups further out than 90 days).

Opt-outs are never loosened here: every opt-out stays closed, stopped and
silent towards the family; at most ONE staff alert asks a person to decide.
"""
import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.models.models import CadenceState, Reply
from app.models.program_models import ProgramAlert, ProgramConversationTurn, ProgramSourceRecord
from app.services.programs import ai_setter, appointments as A
from app.services.programs import responses as R
from app.services.programs.reply_rules import (
    ACTIVE, BAD_DATA, HOT, OPT_OUT, OPT_OUT_ASK_LABEL, classify, opt_out_needs_person,
)
from tests.test_alert_dedupe import OWNER_EMAIL, OWNER_PHONE, _per_recipient, _reply, wired  # noqa: F401
from tests.test_outreach_program import _lead_for, program  # noqa: F401 (fixture)
from tests.test_sci_appointment_setter import _d, _Fake, _run, setter  # noqa: F401 (fixture)


def _inbound(db, lead, text, sid, *, upstream=None, now=None, fake=None):
    """A stored inbound reply with the platform's upstream label, then the
    program's handling - as the webhook does - with a scripted AI."""
    r = Reply(lead_id=lead.id, body=text, twilio_sid=sid)
    db.add(r)
    db.commit()
    fake = fake if fake is not None else _Fake([])
    with patch.object(ai_setter, "_get_client", return_value=fake):
        return R.on_inbound(db, lead, text, "sms", reply_id=r.id, reply_classification=upstream, now=now)


def _external(db, lead):
    return db.query(ProgramAlert).filter(ProgramAlert.lead_id == lead.id,
                                         ProgramAlert.channel.in_(("sms", "email"))).all()


# A ─ an opt-out that carries a request reaches a person, never a text ────────

ASKS = [
    ("Don't text me, call me at 214-555-1234", None),
    ("Not interested in the guide, can you just call me?", None),
    ("No thanks, we already have plots but we need funeral arrangements", None),
    ("Not right now", "not_interested"),
    ("Not interested right now but maybe next year", None),
    ("I'm not interested in cremation, we want burial", None),
    ("No estoy interesado, pero llámeme el próximo año", None),
]


@pytest.mark.parametrize("text,upstream", ASKS)
def test_opt_out_with_a_request_stays_opt_out_and_pages_once(wired, setter, sample_advisor, text, upstream):
    db, org, sent = wired["db"], wired["org"], wired["sent"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    fake = _Fake([])
    with patch("app.services.sms_service.send_sms") as family_text:
        resp = _inbound(db, lead, text, "SM_A1", upstream=upstream, fake=fake)
    # Still an opt-out in every way.
    assert resp.response_class == OPT_OUT
    assert resp.handling_status == "closed" and resp.closed_at is not None
    st = db.query(CadenceState).filter_by(lead_id=lead.id).one()
    assert str(getattr(st.status, "value", st.status)) == "stopped_dnc"
    conv = ai_setter.conversation_for(db, lead, create=False)
    assert conv.state == "stopped"
    assert fake.calls == [] and family_text.call_count == 0           # no AI, no text to the family
    assert not [t for t in db.query(ProgramConversationTurn).filter_by(conversation_id=conv.id, role="ai")]
    # ONE staff alert: one key, each recipient once, plain wording.
    rows = _external(db, lead)
    assert rows and {r.dedupe_key for r in rows} == {"reply:%s" % resp.reply_id}
    assert {r.kind for r in rows} == {"opt_out_request"}
    assert all(v == 1 for v in _per_recipient(sent).values())
    owner_email = [b for ch, to, b in sent if ch == "email" and to == OWNER_EMAIL]
    assert owner_email and "Opted out of texts but asked for something" in owner_email[0]
    owner_sms = [b for ch, to, b in sent if ch == "sms" and to == OWNER_PHONE]
    assert owner_sms and "No Texts" in owner_sms[0] and "214-555" not in owner_sms[0]
    assert OPT_OUT_ASK_LABEL in resp.recommended_action
    # A retry of the same message pages nobody again.
    n = len(sent)
    R.on_inbound(db, lead, text, "sms", reply_id=resp.reply_id, reply_classification=upstream)
    assert len(sent) == n


@pytest.mark.parametrize("text", ["STOP", "unsubscribe", "Remove me", "Leave me alone",
                                  "Stop texting me right now", "Please don't call me", "Not interested",
                                  "No estoy interesado", "No me manden mensajes"])
def test_bare_opt_out_pages_nobody(wired, setter, sample_advisor, text):
    db, org, sent = wired["db"], wired["org"], wired["sent"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    resp = _inbound(db, lead, text, "SM_A2")
    assert resp.response_class == OPT_OUT and resp.handling_status == "closed"
    assert sent == [] and _external(db, lead) == []
    assert ai_setter.conversation_for(db, lead, create=False).state == "stopped"


@pytest.mark.parametrize("text,needs", [
    ("Don't text me, call me at 214-555-1234", True), ("Not right now", True), ("send it by mail instead", True),
    ("STOP", False), ("unsubscribe", False), ("remove me", False), ("leave me alone", False),
    ("Don't call me", False), ("Do not email me", False), ("Stop sending me texts", False),
    ("Don't send me anything", False), ("Stop texting me right now", False), ("No estoy interesado", False),
])
def test_opt_out_request_detection(text, needs):
    assert opt_out_needs_person(text) is needs


# 4 ─ a reply held for contact hours never moves an agreed later date ─────────

def _auto_closed_hours(setter, monkeypatch, nxt):
    db, prog = setter["db"], setter["prog"]
    prog.ai_setter_mode = "auto"
    db.commit()
    monkeypatch.setattr("app.services.contact_hours.check", lambda lead, now=None: {"permitted": False})
    monkeypatch.setattr("app.services.contact_hours.next_permitted_utc", lambda lead, now=None: nxt)


def test_held_reply_keeps_the_agreed_follow_up_date(setter, sample_advisor, monkeypatch):
    db, org = setter["db"], setter["org"]
    nxt = datetime.utcnow() + timedelta(hours=10)
    _auto_closed_hours(setter, monkeypatch, nxt)
    lead = _lead_for(db, org, sample_advisor, "L001")
    day = (A._today_central().date() + timedelta(days=35)).isoformat()
    fake = _Fake([_d("follow_up_later", "Of course - I'll check back with you next month.",
                     follow_up_date=day, follow_up_about="check in after the holidays")])
    resp, conv = _run(db, lead, "We're busy now, can you check back next month?", fake)
    turns = db.query(ProgramConversationTurn).filter_by(conversation_id=conv.id, role="ai").all()
    # THE RULE (Oct 22): "check back next month" is not an appointment - a
    # person takes it; nothing is held to go out later.
    assert [t.delivery for t in turns] == ["blocked"]
    assert conv.state == "human" and resp.suggested_reply.startswith("Of course")


def test_held_reply_with_no_date_still_waits_for_open_hours(setter, sample_advisor, monkeypatch):
    db, org = setter["db"], setter["org"]
    nxt = datetime(2031, 1, 6, 14, 30)
    _auto_closed_hours(setter, monkeypatch, nxt)
    lead = _lead_for(db, org, sample_advisor, "L001")
    _, conv = _run(db, lead, "What does the guide include?",
                   _Fake([_d("offer_times", "It covers your options. Would {TIMES} work to go over it?")]))
    assert conv.next_followup_at is not None and conv.next_followup_at >= datetime.utcnow()   # never before it goes out


# 5 ─ an auto-reply never takes a conversation away from a person ─────────────

AUTO = "I'm driving with Do Not Disturb on. I'll see your message when I get where I'm going."


@pytest.mark.parametrize("state", ["human", "callback_requested", "offered", "booked", "waiting_future", "stopped"])
def test_auto_reply_leaves_a_non_ai_conversation_alone(setter, sample_advisor, state):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    conv = ai_setter.conversation_for(db, lead)
    agreed = datetime(2031, 2, 1, 15, 0)
    conv.state, conv.next_followup_at = state, agreed
    db.commit()
    fake = _Fake([])
    _, conv = _run(db, lead, AUTO, fake)
    assert conv.state == state and conv.next_followup_at == agreed and fake.calls == []


def test_auto_reply_on_an_ai_conversation_waits(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    _, conv = _run(db, lead, AUTO, _Fake([]))
    assert conv.state == "waiting" and conv.next_followup_at is not None


# 6 ─ a draft is not "the AI is talking": the SLA reminder still goes ─────────

def _sla_pages(sent):
    return [b for ch, to, b in sent if ch == "sms" and to == OWNER_PHONE and "Still Waiting" in b]


def test_draft_mode_does_not_silence_the_sla_reminder(wired, setter, sample_advisor):
    db, org, sent = wired["db"], wired["org"], wired["sent"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    t0 = datetime.utcnow() - timedelta(hours=1)
    fake = _Fake([_d("offer_times", "I'd be glad to. Would {TIMES} work for a visit?")])
    resp = _inbound(db, lead, "Can I come in to visit and see the plots?", "SM_6A", now=t0, fake=fake)
    assert resp.response_class == HOT
    conv = ai_setter.conversation_for(db, lead, create=False)
    assert [t.delivery for t in db.query(ProgramConversationTurn).filter_by(conversation_id=conv.id, role="ai")] \
        == ["draft"] and conv.last_ai_at is not None
    R.sla_sweep(db, now=t0 + timedelta(minutes=20))
    assert len(_sla_pages(sent)) == 1


def test_a_delivered_ai_answer_still_silences_the_sla_reminder(wired, setter, sample_advisor):
    db, org, sent = wired["db"], wired["org"], wired["sent"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    t0 = datetime.utcnow() - timedelta(hours=1)
    fake = _Fake([_d("offer_times", "I'd be glad to. Would {TIMES} work for a visit?")])
    _inbound(db, lead, "Can I come in to visit and see the plots?", "SM_6B", now=t0, fake=fake)
    conv = ai_setter.conversation_for(db, lead, create=False)
    for t in db.query(ProgramConversationTurn).filter_by(conversation_id=conv.id, role="ai"):
        t.delivery = "sent"                                         # as AUTO mode would have done
    db.commit()
    R.sla_sweep(db, now=t0 + timedelta(minutes=20))
    assert _sla_pages(sent) == []


# 7 ─ "who is this?" is a question, not a wrong number ───────────────────────

@pytest.mark.parametrize("text", ["Who is this?", "who's this", "Who are you?", "What is this about?", "Quién es?"])
def test_who_is_this_is_active_not_bad_data(text):
    r = classify(text, "wrong_number")
    assert r["class"] == ACTIVE and r["reasons"] == ["asked who we are"]


@pytest.mark.parametrize("text", ["Wrong number, who is this?", "Who is this? You have the wrong person",
                                  "I don't know who this is", "Sorry, wrong number"])
def test_explicit_wrong_number_stays_bad_data(text):
    assert classify(text, "wrong_number")["class"] != ACTIVE


def test_who_is_this_does_not_flag_the_record(wired, sample_advisor):
    db, org = wired["db"], wired["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    resp = _inbound(db, lead, "Who is this?", "SM_7", upstream="wrong_number")
    assert resp.response_class == ACTIVE
    rec = db.query(ProgramSourceRecord).filter_by(lead_id=lead.id).one()
    assert "WRONG NUMBER / ADDRESS (reply)" not in json.loads(rec.data_note_flags or "[]")


# 11 ─ Spanish and misspellings ─────────────────────────────────────────────────

@pytest.mark.parametrize("text", ["Quiero una cita", "Me interesa", "Estoy interesada", "Llámeme por favor",
                                  "Quiero ver los terrenos", "I'm intrested", "very intersted", "interessted"])
def test_spanish_and_typo_interest_is_hot(text):
    assert classify(text)["class"] == HOT


@pytest.mark.parametrize("text", ["No me manden mensajes", "Ya no me escriban", "No estoy interesado",
                                  "No estamos interesados", "Bórrame", "Dejen de escribir", "No me interesa",
                                  "No me llamen"])
def test_spanish_opt_out(text):
    assert classify(text)["class"] == OPT_OUT


def test_spanish_not_interested_with_a_request_pages_but_stays_opt_out():
    text = "No estoy interesado, pero llámeme el próximo año"
    assert classify(text)["class"] == OPT_OUT and opt_out_needs_person(text)
    assert not opt_out_needs_person("No estoy interesado")


# 12 ─ a part of the day that contradicts the slot is not a pick ──────────────

FRI_10, FRI_14, SAT_10 = datetime(2030, 1, 11, 10, 0), datetime(2030, 1, 11, 14, 0), datetime(2030, 1, 12, 10, 0)


@pytest.mark.parametrize("text,slot,picked", [
    ("Friday morning would be better", FRI_14, False),
    ("Friday morning would be better", FRI_10, True),
    ("Friday morning would be better", SAT_10, False),
    ("Friday afternoon works", FRI_10, False),
    ("Friday afternoon works", FRI_14, True),
    ("Friday evening is best", FRI_14, True),
    ("Good morning! Friday works", FRI_14, True),
    ("Friday morning at 2 works", FRI_14, True),                    # their own clock hour wins
    ("el viernes por la mañana", FRI_14, False),
    ("mejor en la tarde", FRI_10, False),
])
def test_day_part_contradiction(text, slot, picked):
    assert ai_setter.choice_evidence(text, slot, None) is picked


def test_friday_morning_never_books_friday_afternoon(setter, sample_advisor, monkeypatch):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    lead.is_test = True
    db.commit()
    monkeypatch.setattr(A, "available_slots", lambda db, prog, **kw: ([FRI_14, SAT_10], None))
    fake = _Fake([_d("offer_times", "Would {TIMES} work?"), _d("book", "", slot_choice=1)])
    _run(db, lead, "I'd like to come in", fake)
    _, conv = _run(db, lead, "Friday morning would be better", fake)
    assert conv.state != "booked" and conv.booked_time is None


# 13 ─ "I'm in" is agreement only at the end of the message ──────────────────

@pytest.mark.parametrize("text", ["I'm in the hospital", "I'm in Houston this week", "I am in a meeting"])
def test_im_in_a_place_is_not_interest(text):
    assert "interested" not in classify(text)["reasons"]


@pytest.mark.parametrize("text", ["I'm in", "I'm in!", "Count me in, I'm in.", "I'm in 👍", "I'm in for Saturday",
                                  "I’m in"])
def test_im_in_agreement_is_interest(text):
    r = classify(text)
    assert r["class"] == HOT and ("interested" in r["reasons"] or "picked or suggested a time" in r["reasons"])


# 14 ─ "next year" is clamped to 90 days, not reset to 14 ───────────────────

def test_follow_up_beyond_90_days_is_clamped_to_90():
    today = A._today_central().date()
    now = datetime.utcnow()
    far = ai_setter._parse_follow_up((today + timedelta(days=200)).isoformat(), now)
    assert far.date() == today + timedelta(days=90) and far.hour == 15
    mid = ai_setter._parse_follow_up((today + timedelta(days=35)).isoformat(), now)
    assert mid.date() == today + timedelta(days=35)
    assert ai_setter._parse_follow_up("soon", now).date() == today + timedelta(days=14)
    assert ai_setter._parse_follow_up((today - timedelta(days=3)).isoformat(), now).date() == today + timedelta(days=14)


# --- independent-review follow-ups (Oct 20) --------------------------------
from app.services.programs import reply_rules as _RR


@pytest.mark.parametrize("text", ["leave me alone, no citas", "no me interesa una cita", "no quiero una cita",
                                  "no gracias, no más citas", "Don't ever call me", "do not text or call me",
                                  "nunca me llame"])
def test_review_opt_outs_stay_opt_out(text):
    assert _RR.classify(text)["class"] == "opt_out", text


@pytest.mark.parametrize("text", ["leave me alone right now", "Not interested. Do not call me later either.",
                                  "not interested but thank you", "no thanks, not at this time but thank you",
                                  "please do not email me or call me"])
def test_review_bare_opt_outs_page_nobody(text):
    assert _RR.opt_out_needs_person(text) is False, text


def test_opt_out_request_page_is_never_merged_away():
    from app.services.programs import alert_ledger as L
    assert L.TIER["opt_out_request"] > max(v for k, v in L.TIER.items() if k != "opt_out_request")
