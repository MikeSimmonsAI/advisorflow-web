"""SCI customer replies: purchased-plan acknowledgement and calendar-failure safety.

All fixture conversations are synthetic. No real email, SMS or appointments.
"""
from app.models.program_models import ProgramConversationTurn
from app.services.programs import ai_setter, appointments as A
from tests.test_sci_appointment_setter import _Fake, _d, _run, _ai_turns, setter  # noqa: F401
from tests.test_outreach_program import _lead_for, program  # noqa: F401


def test_prior_purchase_statement_and_negation():
    yes = [
        "We purchased a plan from you last year, around December.",
        "We have a funeral plan with you already.",
        "I bought a plot from your cemetery.",
        "Our family paid for a plan with you.",
    ]
    for statement in yes:
        assert ai_setter.existing_plan_purchased_with_us(statement), statement
    for statement in (
        "We purchased a plan from another funeral home last year.",
        "We never purchased a plan from you.",
        "We didn't purchase a plan from you.",
        "We have not purchased a plan from you.",
    ):
        assert not ai_setter.existing_plan_purchased_with_us(statement), statement


def test_customer_purchase_not_forced_into_unrequested_appointment(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001", first="Glenn")
    fake = _Fake([
        _d("offer_times", "Thank you. Would {TIMES} work for a planning visit?"),
        _d("answer", "Thank you for reminding me you purchased a plan with us last December. "
           "Is there anything about your existing arrangements I can help you review?"),
    ])
    body = "Hi Kerry,\n\nWe purchased a plan from you last year, around December.\n\nThanks,\nGlenn"
    resp, conv = _run(db, lead, body, fake)
    assert len(fake.calls) == 2        # forced correction, not an appointment push
    assert "EXISTING CUSTOMER" in fake.calls[1]["messages"][1]["content"]
    assert "purchased a plan" in (resp.suggested_reply or "").lower()
    assert "{TIMES}" not in (resp.suggested_reply or "")
    assert _ai_turns(db, conv)[-1].action == "answer"
    assert conv.state != "offered"


def test_previous_plan_is_not_mistaken_for_opt_out_or_decline(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001", first="Glenn")
    fake = _Fake([
        _d("close", "Okay, we'll take you off the list."),
        _d("answer", "I understand you already purchased a plan with us last December. "
           "Is there anything about your existing arrangements you'd like help with?"),
    ])
    _, conv = _run(db, lead, "We purchased a plan from you last December.", fake)
    assert len(fake.calls) == 2
    assert conv.state != "closed" and _ai_turns(db, conv)[-1].action == "answer"


def test_explicit_customer_request_for_review_may_use_real_times(setter, sample_advisor):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001", first="Glenn")
    fake = _Fake([
        _d("offer_times", "I understand you already purchased a plan with us. "
           "Would {TIMES} work to go over your existing arrangements?")
    ])
    _, conv = _run(db, lead,
                   "We purchased a plan from you last December. Could we meet to review it?", fake)
    assert len(fake.calls) == 1
    assert conv.state == "offered"
    assert _ai_turns(db, conv)[-1].action == "offer_times"


def test_calendar_has_no_slots_holds_reply_instead_of_sending_canned_filler(
        setter, sample_advisor, monkeypatch):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001", first="Glenn")
    monkeypatch.setattr(A, "available_slots", lambda *a, **k: ([], "advisor calendar unavailable"))
    fake = _Fake([
        _d("offer_times", "I understand you already purchased a plan with us. "
           "Would {TIMES} work to review it?")
    ])
    resp, conv = _run(db, lead,
                      "We purchased a plan from you last December. Could we meet to review it?", fake)
    assert conv.state == "human"
    turns = db.query(ProgramConversationTurn).filter_by(conversation_id=conv.id).all()
    assert not [t for t in turns if t.role == "ai" and t.delivery in ("sent", "held", "draft")]
    assert any(t.role == "system" and "generic automatic reply suppressed" in (t.reason or "")
               for t in turns)
    assert not resp.suggested_reply  # no unusable {TIMES} draft offered as family-ready
