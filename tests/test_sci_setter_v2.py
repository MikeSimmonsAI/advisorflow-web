"""Setter v2: family memory across days, drafts never shown as received,
validation with one correction, confidence, follow-up lifecycle, mail task,
operational limits instead of an 8-turn cut-off."""
import json
from datetime import datetime, timedelta
from unittest.mock import patch

from app.models.program_models import ProgramAlert, ProgramConversationTurn
from app.services.programs import ai_setter, appointments as A
from tests.test_sci_appointment_setter import SLOTS, _Fake, _ai_turns, _d, _run, setter  # noqa: F401
from tests.test_outreach_program import _lead_for, program  # noqa: F401


def test_memory_persists_and_merges(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001", first="Ada")
    fake = _Fake([
        _d("answer", "That makes sense. Is cremation what you're leaning toward?",
           memory_update={"wants": ["plan ahead for herself"], "service_interests": ["cremation"],
                          "participants": ["daughter Lisa"], "readiness": "considering"}),
        _d("answer", "Of course, Lisa is welcome to join. Would a visit or a phone call be easier for you both?",
           memory_update={"service_interests": ["Cremation", "niche"], "questions_answered": ["can my daughter come"]}),
    ])
    _run(db, lead, "I want to get my own plans in order, probably cremation. My daughter Lisa helps me.", fake)
    _, conv = _run(db, lead, "Can my daughter come too?", fake)
    mem = ai_setter.memory_of(conv)
    assert mem["service_interests"] == ["cremation", "niche"]          # merged, no duplicate
    assert mem["participants"] == ["daughter Lisa"] and mem["readiness"] == "considering"
    sent_ctx = json.loads(fake.calls[-1]["messages"][1]["content"])
    assert sent_ctx["family_memory"]["participants"] == ["daughter Lisa"]   # the model sees it next time


def test_drafts_are_never_presented_as_received(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001", first="Bo")         # draft mode, real (not test) family
    fake = _Fake([_d("answer", "Glad to help. What would be most useful to know first?"),
                  _d("answer", "Sure thing. Would a short call help?")])
    _run(db, lead, "What is this about?", fake)
    _run(db, lead, "ok tell me more", fake)
    ctx = json.loads(fake.calls[-1]["messages"][1]["content"])
    saw = [m["text"] for m in ctx["conversation_family_actually_saw"]]
    assert "Glad to help. What would be most useful to know first?" not in saw
    assert ctx["replies_written_but_NOT_received_by_the_family"] == ["Glad to help. What would be most useful to know first?"]


def test_a_written_clock_time_is_corrected_once(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    fake = _Fake([_d("offer_times", "How about Tuesday at 3pm?"),
                  _d("offer_times", "Would {TIMES} work for a short visit?")])
    _, conv = _run(db, lead, "I'd like to come in", fake)
    assert "clock time" in fake.calls[-1]["messages"][1]["content"]
    assert A.describe(SLOTS[0]) in _ai_turns(db, conv)[-1].text and conv.state == "offered"


def test_low_confidence_goes_to_a_person_with_the_draft(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    fake = _Fake([_d("answer", "I'm not sure, could you tell me more?", confidence=0.2)])
    resp, conv = _run(db, lead, "what about the thing with my brother's lot from 1987", fake)
    assert conv.state == "human" and resp.suggested_reply.startswith("I'm not sure")
    assert _ai_turns(db, conv)[-1].delivery == "blocked"


def test_follow_up_later_and_contextual_follow_up(setter, sample_advisor):
    db, org, prog = setter["db"], setter["org"], setter["prog"]
    lead = _lead_for(db, org, sample_advisor, "L001", first="Cy")
    soon = (A._today_central().date() + timedelta(days=10)).isoformat()
    fake = _Fake([_d("follow_up_later", "Of course. I'll check back after the holidays.",
                     follow_up_date=soon, follow_up_about="they wanted to wait until after the holidays",
                     memory_update={"concerns": ["busy season"]})])
    _, conv = _run(db, lead, "Maybe after the holidays", fake)
    assert conv.state == "waiting_future" and conv.next_followup_at.date().isoformat() == soon
    # not due yet: nothing happens
    fu = _Fake([_d("answer", "Hi Cy, checking back as promised. Would a short call this month be helpful?")])
    with patch.object(ai_setter, "_get_client", return_value=fu):
        assert ai_setter.followup_sweep(db)["sent"] == 0 and fu.calls == []
        out = ai_setter.followup_sweep(db, now=conv.next_followup_at + timedelta(minutes=1))
    assert out["drafted"] == 1                                             # draft mode
    ctx = json.loads(fu.calls[0]["messages"][1]["content"])
    assert "after the holidays" in ctx["task"] and ctx["family_memory"]["concerns"] == ["busy season"]
    db.refresh(conv)
    assert conv.followups_sent == 1 and conv.next_followup_at is None
    with patch.object(ai_setter, "_get_client", return_value=fu):
        assert ai_setter.followup_sweep(db, now=datetime.utcnow() + timedelta(days=30))["sent"] == 0


def test_no_follow_up_after_the_family_wrote_back(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001", first="Di")
    lead.is_test = True                                                   # delivered (simulated), so a follow-up is scheduled
    db.commit()
    fake = _Fake([_d("offer_times", "Would {TIMES} work?"),
                  _d("answer", "Thanks, Di. Which works better for you?")])
    _, conv = _run(db, lead, "I could come in", fake)
    assert conv.state == "offered" and conv.next_followup_at is not None
    _, conv = _run(db, lead, "Let me check with my husband", fake)
    assert conv.next_followup_at is None or conv.next_followup_at > datetime.utcnow()


def test_mail_request_becomes_a_task_never_a_claim(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001", first="Lawrence")
    fake = _Fake([_d("mail_guide", "We've mailed the guide to you."),
                  _d("mail_guide", "I'll have a printed guide arranged for you. Is the address we have for you current?")])
    _, conv = _run(db, lead, "Can you mail me the guide to my house?", fake)
    assert "mailed" in fake.calls[-1]["messages"][1]["content"]           # corrected once
    assert conv.state == "info_requested" and ai_setter.memory_of(conv)["mail_request"]["requested"]
    task = db.query(ProgramAlert).filter_by(kind="task", reason="mail_guide").one()
    assert "Lawrence" in task.message


def test_close_politely_and_no_follow_up(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    fake = _Fake([_d("close", "Understood, thank you for letting me know. We're here if anything changes.")])
    _, conv = _run(db, lead, "I think we're all set for now, but thank you", fake)
    assert conv.state == "closed" and conv.next_followup_at is None


def test_daily_limit_replaces_the_turn_cap(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    lead.is_test = True                                                   # simulated deliveries count
    db.commit()
    replies = [_d("answer", "Reply number %d, what else can I help with?" % i) for i in range(ai_setter.MAX_AI_PER_DAY)]
    fake = _Fake(replies)
    conv = None
    for i in range(ai_setter.MAX_AI_PER_DAY):
        _, conv = _run(db, lead, "question %d" % i, fake)
    assert conv.state != "human"                                          # 6 replies is fine
    _, conv = _run(db, lead, "one more", _Fake([]))
    assert conv.state == "human" and "today" in conv.handoff_reason
