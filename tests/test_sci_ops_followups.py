"""SCI ops (Oct 20): follow-ups. After the setter answers (or promises the
mailed guide) and the family goes quiet, ONE gentle contextual check-in two
days later; each delivered follow-up schedules the next three days later until
MAX_FOLLOWUPS; any family reply cancels what was pending; opted-out, handed
over and booked families never get one. "Not right now" / "not interested"
stay opt-outs."""
import json
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from app.models.program_models import ProgramConversation, ProgramConversationTurn
from app.services.programs import ai_setter, appointments as A
from app.services.programs import responses as R
from app.services.programs.reply_rules import OPT_OUT
from tests.test_outreach_program import _lead_for, program  # noqa: F401 (fixture)
from tests.test_sci_appointment_setter import _Fake, _d, _run, setter  # noqa: F401 (fixture)

ANSWER = "Good question - the visit takes about an hour and there's no pressure. What else can I help with?"


def _quiet_family(db, org, advisor, first="Cy", source="L001", phone="2145550101"):
    """A test contact: every delivery is simulated, so follow-ups are scheduled."""
    lead = _lead_for(db, org, advisor, source, phone=phone, first=first)
    lead.is_test = True
    db.commit()
    return lead


def _sweep(db, now, decisions):
    fu = _Fake(decisions)
    with patch.object(ai_setter, "_get_client", return_value=fu):
        out = ai_setter.followup_sweep(db, now=now)
    return out, fu


def _ai_texts(db, conv):
    return [t.text for t in db.query(ProgramConversationTurn).filter_by(conversation_id=conv.id, role="ai")
            .order_by(ProgramConversationTurn.created_at).all()]


# ── (a) one contextual follow-up after an answer ─────────────────────────────

def test_answer_schedules_one_follow_up_in_two_days(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _quiet_family(db, org, sample_advisor)
    before = datetime.utcnow()
    _, conv = _run(db, lead, "How long does a visit take?", _Fake([_d("answer", ANSWER)]))
    assert conv.state == "ai_active"
    assert conv.followup_reason == ai_setter.ANSWER_FOLLOWUP_REASON
    due = conv.next_followup_at
    assert before + timedelta(days=2) - timedelta(minutes=1) <= due <= datetime.utcnow() + timedelta(days=2)
    # not due yet: nothing happens, the AI is not even asked
    out, fu = _sweep(db, due - timedelta(minutes=5), [])
    assert out == {"sent": 0, "drafted": 0, "skipped": 0} and fu.calls == []
    out, fu = _sweep(db, due + timedelta(minutes=1),
                     [_d("answer", "Hi Cy, just checking in - any other questions I can help with?")])
    assert out["sent"] == 1
    ctx = json.loads(fu.calls[0]["messages"][1]["content"])
    assert "answered their question" in ctx["task"]                     # contextual, not a new intro
    db.refresh(conv)
    assert conv.followups_sent == 1 and conv.state == "ai_active"


def test_mail_guide_schedules_one_follow_up_in_two_days(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _quiet_family(db, org, sample_advisor, first="Lawrence")
    now = datetime.utcnow()
    _, conv = _run(db, lead, "Can you mail me the guide?",
                   _Fake([_d("mail_guide", "I'll have a printed guide arranged for you. Is the address we have current?")]))
    assert conv.state == "info_requested"
    assert conv.followup_reason == ai_setter.MAIL_FOLLOWUP_REASON
    assert abs((conv.next_followup_at - (now + ai_setter.ANSWER_FOLLOWUP)).total_seconds()) < 120


def test_draft_mode_real_family_gets_no_follow_up(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")                  # real family, draft mode: nothing sent
    _, conv = _run(db, lead, "How long does a visit take?", _Fake([_d("answer", ANSWER)]))
    assert conv.next_followup_at is None


def test_opted_out_family_never_gets_the_answer_follow_up(setter, sample_advisor, monkeypatch):
    db, org = setter["db"], setter["org"]
    lead = _quiet_family(db, org, sample_advisor)
    monkeypatch.setattr(A, "_opted_out", lambda db, lead: True)
    _, conv = _run(db, lead, "How long does a visit take?", _Fake([_d("answer", ANSWER)]))
    assert conv.next_followup_at is None and conv.followup_reason is None


def test_handed_over_or_booked_conversation_gets_no_answer_follow_up(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _quiet_family(db, org, sample_advisor)
    _, conv = _run(db, lead, "Can someone call me?",
                   _Fake([_d("handoff", "Of course - I'll reach out to you personally.", reason="wants a person")]))
    assert conv.state == "human" and conv.next_followup_at is None
    # a scheduled answer follow-up on a conversation that was then booked / taken never fires
    for state in ("booked", "human", "stopped"):
        conv.state = state
        conv.followup_reason = ai_setter.ANSWER_FOLLOWUP_REASON
        conv.next_followup_at = datetime.utcnow() - timedelta(minutes=1)
        db.commit()
        out, fu = _sweep(db, datetime.utcnow(), [])
        assert fu.calls == [] and out["sent"] == 0, state


def test_ai_active_without_the_setters_reason_is_never_swept(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _quiet_family(db, org, sample_advisor)
    conv = ProgramConversation(organization_id=org.id, lead_id=lead.id, state="ai_active",
                               followup_reason="mail the printed planning guide",
                               next_followup_at=datetime.utcnow() - timedelta(hours=1))
    db.add(conv)
    db.commit()
    out, fu = _sweep(db, datetime.utcnow(), [])
    assert fu.calls == [] and out["sent"] == 0
    db.refresh(conv)
    assert conv.next_followup_at is not None                          # untouched


# ── (b) MAX_FOLLOWUPS is reachable, then it stops ────────────────────────────

def test_follow_ups_continue_three_days_apart_up_to_the_max(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _quiet_family(db, org, sample_advisor)
    _, conv = _run(db, lead, "How long does a visit take?", _Fake([_d("answer", ANSWER)]))
    t1 = conv.next_followup_at + timedelta(minutes=1)
    out, _ = _sweep(db, t1, [_d("answer", "Hi Cy, just checking in - any other questions?")])
    assert out["sent"] == 1
    db.refresh(conv)
    assert conv.followups_sent == 1 and conv.next_followup_at == t1 + ai_setter.NEXT_FOLLOWUP
    t2 = conv.next_followup_at + timedelta(minutes=1)
    out, _ = _sweep(db, t2, [_d("answer", "Hi Cy, one last check - we're here whenever you're ready.")])
    assert out["sent"] == 1
    db.refresh(conv)
    assert conv.followups_sent == ai_setter.MAX_FOLLOWUPS == 2 and conv.next_followup_at is None
    out, fu = _sweep(db, t2 + timedelta(days=30), [])
    assert out["sent"] == 0 and fu.calls == []
    assert len(_ai_texts(db, conv)) == 3                               # the answer + two follow-ups, no more


def test_offered_times_follow_up_also_reaches_the_second(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _quiet_family(db, org, sample_advisor)
    _, conv = _run(db, lead, "I could come in", _Fake([_d("offer_times", "Would {TIMES} work?")]))
    assert conv.state == "offered" and conv.next_followup_at is not None
    for n, text in ((1, "Just checking in - would either time suit you?"),
                    (2, "One last check - happy to find another day if those don't work.")):
        out, _ = _sweep(db, conv.next_followup_at + timedelta(minutes=1), [_d("answer", text)])
        assert out["sent"] == 1, n
        db.refresh(conv)
        assert conv.followups_sent == n
    assert conv.next_followup_at is None


def test_a_follow_up_the_ai_declines_does_not_reschedule(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _quiet_family(db, org, sample_advisor)
    _, conv = _run(db, lead, "How long does a visit take?", _Fake([_d("answer", ANSWER)]))
    out, _ = _sweep(db, conv.next_followup_at + timedelta(minutes=1), [_d("wait", "", reason="not helpful")])
    assert out["skipped"] == 1
    db.refresh(conv)
    assert conv.next_followup_at is None


def test_follow_up_held_for_contact_hours_reschedules_three_days_out(setter, sample_advisor, monkeypatch):
    from app.services import contact_hours
    db, org, prog = setter["db"], setter["org"], setter["prog"]
    prog.ai_setter_mode = "auto"
    db.commit()
    lead = _lead_for(db, org, sample_advisor, "L001", first="Hal")      # real family, auto mode
    monkeypatch.setattr(contact_hours, "check", lambda lead, now=None: {"permitted": False})
    monkeypatch.setattr(contact_hours, "next_permitted_utc", lambda lead, now=None: (now or datetime.utcnow())
                        + timedelta(hours=8))
    _, conv = _run(db, lead, "How long does a visit take?", _Fake([_d("answer", ANSWER)]))
    assert conv.state == "ai_active"
    assert conv.next_followup_at > datetime.utcnow() + timedelta(days=1)   # the 2-day check-in, not the hours retry
    t1 = conv.next_followup_at + timedelta(minutes=1)
    out, _ = _sweep(db, t1, [_d("answer", "Hi Hal, just checking in - any other questions?")])
    db.refresh(conv)
    assert out["drafted"] == 1 and conv.followups_sent == 1               # held for hours (not counted as sent)
    assert conv.next_followup_at == t1 + ai_setter.NEXT_FOLLOWUP          # never re-fired at the next open hour


# ── (c) any family reply cancels what is pending ─────────────────────────────

def test_a_family_reply_cancels_the_pending_answer_follow_up(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _quiet_family(db, org, sample_advisor)
    _, conv = _run(db, lead, "How long does a visit take?", _Fake([_d("answer", ANSWER)]))
    assert conv.next_followup_at is not None
    _, conv = _run(db, lead, "ok thanks", _Fake([_d("wait", "", reason="acknowledgement")]))
    assert conv.next_followup_at is None and conv.followup_reason is None
    out, fu = _sweep(db, datetime.utcnow() + timedelta(days=10), [])
    assert out["sent"] == 0 and fu.calls == []


def test_a_family_reply_after_scheduling_is_never_followed_up(setter, sample_advisor):
    """The sweep's own backstop: the family wrote after our last message."""
    db, org = setter["db"], setter["org"]
    lead = _quiet_family(db, org, sample_advisor)
    _, conv = _run(db, lead, "How long does a visit take?", _Fake([_d("answer", ANSWER)]))
    conv.last_family_at = conv.last_ai_at + timedelta(minutes=5)
    db.commit()
    out, fu = _sweep(db, conv.next_followup_at + timedelta(minutes=1), [])
    assert fu.calls == [] and out["skipped"] == 1


@pytest.mark.parametrize("text,upstream", [("Not right now", "not_interested"), ("Not interested", None)])
def test_not_right_now_and_not_interested_stay_opt_outs(setter, sample_advisor, text, upstream):
    db, org = setter["db"], setter["org"]
    lead = _quiet_family(db, org, sample_advisor)
    _, conv = _run(db, lead, "How long does a visit take?", _Fake([_d("answer", ANSWER)]))
    assert conv.next_followup_at is not None
    fake = _Fake([])
    with patch.object(ai_setter, "_get_client", return_value=fake), \
         patch("app.services.programs.responses._deliver_staff_alert", return_value=(True, None)):
        resp = R.on_inbound(db, lead, text, "sms", reply_classification=upstream)   # as the webhook labels it
    db.refresh(conv)
    assert resp.response_class == OPT_OUT and conv.state == "stopped" and fake.calls == []
    out, fu = _sweep(db, datetime.utcnow() + timedelta(days=10), [])
    assert out["sent"] == 0 and fu.calls == []


# ── independent-review follow-ups: re-checked at send time ───────────────────

def test_no_follow_up_after_a_booking_outside_the_ai(setter, sample_advisor):
    from app.models.models import BookingLink
    db, org = setter["db"], setter["org"]
    lead = _quiet_family(db, org, sample_advisor)
    _, conv = _run(db, lead, "How long does a visit take?", _Fake([_d("answer", ANSWER)]))
    db.add(BookingLink(lead_id=lead.id, user_id=sample_advisor.id, status="booked", token="tok-bk-%s" % lead.id,
                       booked_time=datetime.utcnow() + timedelta(days=4)))
    db.commit()
    out, fu = _sweep(db, conv.next_followup_at + timedelta(minutes=1), [])
    assert out["sent"] == 0 and fu.calls == []


def test_no_follow_up_after_a_person_texted_by_hand(setter, sample_advisor):
    from app.models.models import Message
    from app.services import send_source as ss
    db, org = setter["db"], setter["org"]
    lead = _quiet_family(db, org, sample_advisor)
    _, conv = _run(db, lead, "How long does a visit take?", _Fake([_d("answer", ANSWER)]))
    db.add(Message(lead_id=lead.id, sender_id=sample_advisor.id, body="Hi, it's Kerry",
                   send_source=ss.MANUAL, sent_at=datetime.utcnow() + timedelta(seconds=5)))
    db.commit()
    out, fu = _sweep(db, conv.next_followup_at + timedelta(minutes=1), [])
    assert out["sent"] == 0 and fu.calls == []
