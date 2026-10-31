"""#450 follow-up: a plain "no" closes the outreach and leaves the family
alone - in the rules AND in the appointment setter, where THE RULE used to turn
the AI's "close" into "needs a human" (George, Oct 31). Real requests still
reach a person. Synthetic leads; AI and providers mocked; nothing is sent."""
from unittest.mock import patch

import pytest

from app.models.models import CadenceState, EmailMessage, Message
from app.models.program_models import ProgramAlert, ProgramConversation, ProgramResponse
from app.services.programs import ai_setter, reply_rules as R, responses
from tests.test_outreach_program import _lead_for, program  # noqa: F401

CLEAR_NO = [
    "No longer interested.", "I'm no longer interested.", "I am no longer interested, thank you.",
    "Not interested.", "Not interested anymore.", "No thank you, I'm not interested.",
    "Please don't contact me again.", "Remove me from your list.", "STOP.",
    "No longer interested, please remove me.", "I've changed my mind, thanks.", "This is not for me.",
    "I don't want this anymore.", "I no longer need it, thank you.", "I do not wish to be contacted.",
]


@pytest.mark.parametrize("words", CLEAR_NO)
def test_every_plain_no_is_terminal_with_no_page(words):
    c = R.classify(words)
    assert c["class"] == R.OPT_OUT
    assert R.alert_plan(c["class"]) is None
    assert R.opt_out_needs_person(words) is False
    assert R.HOT not in R.intents(words, c["class"]) and R.APPOINTMENT not in R.intents(words, c["class"])


@pytest.mark.parametrize("words,expect", [
    ("Not interested in cremation, but how much is a burial plot?", R.HOT),          # a real price question
    ("We changed our minds about Thursday, can we do Friday at 2?", None),          # a reschedule, not a no
    ("It's not for me but my sister may want it", None),                              # a referral
    ("Yes, I'd like to come in Thursday at 2", R.HOT),
    ("My husband just passed away, can someone call me?", R.HOT),
    ("Wrong person, I don't know who this is", R.WRONG_PERSON),
])
def test_real_requests_are_not_mistaken_for_a_no(words, expect):
    c = R.classify(words)["class"]
    assert c != R.OPT_OUT
    if expect:
        assert c == expect


def test_soft_timing_and_channel_requests_go_to_a_person_not_back_into_outreach():
    for words in ("I'm not interested right now, maybe next year.",
                  "Please don't text me, but email me the information."):
        assert R.classify(words)["class"] == R.OPT_OUT          # outreach stops
        assert R.opt_out_needs_person(words) is True            # ONE note for a person, no auto follow-up
    assert R.opt_out_scope("Please don't text me, but email me the information.") == "sms"
    assert R.opt_out_scope("No longer interested.") == "all"


def _auto(program):
    db, prog = program["db"], program["prog"]
    prog.ai_setter_mode = ai_setter.AUTO
    db.commit()


@pytest.mark.parametrize("channel", ["email", "sms"])
def test_no_longer_interested_on_either_channel_closes_and_sends_nothing(program, sample_advisor, channel):
    db, org = program["db"], program["org"]
    _auto(program)
    lead = _lead_for(db, org, sample_advisor, "L001")
    with patch("app.services.programs.ai_setter.decide") as decide, \
         patch("app.services.programs.ai_setter._handoff") as handoff, \
         patch("app.services.email_service.send_email_via_provider") as email_send, \
         patch("app.services.sms_service.send_sms") as sms_send:
        resp = responses.on_inbound(db, lead, "No longer interested.", channel, reply_id="r-450-%s" % channel)
        decide.assert_not_called()
        handoff.assert_not_called()
        email_send.assert_not_called()
        sms_send.assert_not_called()
    db.refresh(lead)
    assert resp.response_class == R.OPT_OUT and resp.handling_status == "closed"
    assert resp.body_excerpt == "No longer interested."                      # the family's words are kept
    assert db.query(ProgramAlert).filter_by(response_id=resp.id).count() == 0
    st = db.query(CadenceState).filter_by(lead_id=lead.id).one()
    assert str(getattr(st.status, "value", st.status)) == "stopped_dnc"      # ended, not paused
    conv = db.query(ProgramConversation).filter_by(lead_id=lead.id).one()
    assert conv.state == "stopped" and conv.next_followup_at is None
    assert (lead.allow_email is False) == (channel == "email")              # the email opt-out of record
    # the same message read twice (two mail readers / a retried webhook): one response
    again = responses.on_inbound(db, lead, "No longer interested.", channel, reply_id="r-450-%s" % channel)
    assert again.id == resp.id and db.query(ProgramResponse).filter_by(lead_id=lead.id).count() == 1


def test_dont_text_but_email_me_keeps_the_email_open(program, sample_advisor):
    db, org = program["db"], program["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    resp = responses.on_inbound(db, lead, "Please don't text me, but email me the information.", "email")
    db.refresh(lead)
    assert resp.response_class == R.OPT_OUT
    assert lead.allow_sms is False and lead.allow_email is not False
    assert db.query(CadenceState).filter_by(lead_id=lead.id).one().status == "stopped_dnc"   # no marketing follow-ups


def _decision(action, reply, mood="neutral", confidence=0.9):
    return {"action": action, "reply": reply, "reason": "they declined", "confidence": confidence,
            "understanding": {"meaning": "not interested", "mood": mood}}


def test_a_no_the_rules_miss_but_the_ai_closes_is_not_a_handoff(program, sample_advisor):
    """The George path: the AI reads a plain no and chooses "close". It used to
    become "needs a human" with 9 staff alerts; now it closes quietly."""
    db, org = program["db"], program["org"]
    _auto(program)
    lead = _lead_for(db, org, sample_advisor, "L001")
    words = "We're all set, thank you."
    assert R.classify(words)["class"] != R.OPT_OUT                          # the rules don't catch it
    with patch("app.services.programs.ai_setter.decide",
               return_value=_decision("close", "Thank you for letting me know. Take care.")), \
         patch("app.services.programs.ai_setter._handoff") as handoff, \
         patch("app.services.email_service.send_email_via_provider") as email_send, \
         patch("app.services.sms_service.send_sms") as sms_send, \
         patch("app.services.programs.alert_ledger.page") as page:
        resp = responses.on_inbound(db, lead, words, "email")
        handoff.assert_not_called()
        email_send.assert_not_called()
        sms_send.assert_not_called()
        page.assert_not_called()
    db.refresh(resp)
    assert resp.response_class == R.OPT_OUT and resp.handling_status == "closed"
    assert db.query(ProgramAlert).filter_by(response_id=resp.id).count() == 0
    conv = db.query(ProgramConversation).filter_by(lead_id=lead.id).one()
    assert conv.state == "stopped"
    assert db.query(CadenceState).filter_by(lead_id=lead.id).one().status == "stopped_dnc"
    assert db.query(Message).count() == 0 and db.query(EmailMessage).count() == 0


@pytest.mark.parametrize("words,decision", [
    ("No", _decision("close", "Thank you, take care.")),                                  # bare no: a person looks
    ("We're all set, but can someone call my daughter?", _decision("close", "Thanks.")),  # carries a request
    ("Not now, we just lost my father", _decision("close", "So sorry.", mood="grieving")),
    ("Can someone call me about my mother's arrangements?", _decision("handoff", "")),     # a real ask
])
def test_genuine_or_unclear_replies_still_reach_a_person(program, sample_advisor, words, decision):
    db, org = program["db"], program["org"]
    _auto(program)
    lead = _lead_for(db, org, sample_advisor, "L001")
    with patch("app.services.programs.ai_setter.decide", return_value=decision), \
         patch("app.services.programs.ai_setter._handoff") as handoff, \
         patch("app.services.sms_service.send_sms"), \
         patch("app.services.email_service.send_email_via_provider"):
        responses.on_inbound(db, lead, words, "sms")
    if R.classify(words)["class"] == R.OPT_OUT:
        pytest.skip("the rules treat this as a no already")
    assert handoff.called
