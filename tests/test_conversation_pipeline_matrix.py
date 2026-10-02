"""EVERYDAY REPLIES STILL GET ANSWERED - and the ones that need a person don't.

Drives the real pipeline (process_inbound_reply -> Conversation Intelligence
gate -> quality gate -> SMS send with a mocked Twilio) for a lead WITHOUT an
SMS-consent flag who texted in, the commonest production shape. Written after
the gate briefly held every such reply (fixed in 7475dbc) and after an
over-strict "asking back is not answering" rule held a good price reply.
"""
from unittest.mock import MagicMock, patch
from app.models.models import Reply, PipelineConversation
from tests.test_outbound_source_switches import _enable, _lead, _analysis


import pytest

EXPECT = {
    "Yes please": "auto_sent", "What does a 20 year term cost?": "auto_sent", "ok": "auto_sent",
    "Thursday at 3 works": "auto_sent", "I'm driving, text me later": "auto_sent",
    "Not interested": "held", "Who is this?": "flagged", "Can I talk to a real person?": "flagged",
    "My wife and I want coverage for our 2 kids": "auto_sent", "When does my contract end?": "flagged",
    "We're moving next month": "auto_sent", "We'd like to preplan my funeral": "auto_sent",
}

CASES = [
    ("insurance", "Yes please", "Happy to help - does Tuesday work?"),
    ("insurance", "What does a 20 year term cost?", "Rates depend on age and health; can I ask your age?"),
    ("insurance", "ok", "Great - what's a good time to talk?"),
    ("insurance", "Thursday at 3 works", "Perfect, Thursday at 3 it is. Talk then!"),
    ("insurance", "I'm driving, text me later", "No problem - I'll follow up later."),
    ("insurance", "Not interested", "Understood, thanks."),
    ("insurance", "Who is this?", "This is Maya with the agency."),
    ("insurance", "Can I talk to a real person?", "Sure, someone will call you."),
    ("insurance", "My wife and I want coverage for our 2 kids", "Wonderful - how old are you both?"),
    ("energy", "When does my contract end?", "Let me check that for you."),
    ("energy", "We're moving next month", "Congrats! Where are you moving to?"),
    ("funeral", "We'd like to preplan my funeral", "We'd be glad to help - when is a good time to visit?"),
]

@pytest.mark.parametrize("case", range(12))
def test_everyday_reply(case, db_session, sample_org, sample_advisor, monkeypatch, ai_background_on):
    from app.services import pipeline_service
    _enable(monkeypatch)
    i = case
    ind, body, ai = CASES[case]
    sample_org.industry = ind
    db_session.commit()
    lead = _lead(db_session, sample_org, sample_advisor, phone="1214555%04d" % (7000 + i))
    p = PipelineConversation(organization_id=sample_org.id, lead_id=lead.id, advisor_id=sample_advisor.id,
                             stage="replied", auto_respond=True, confidence_threshold=50)
    db_session.add(p); db_session.commit()
    r = Reply(lead_id=lead.id, body=body, source="sms"); db_session.add(r); db_session.commit()
    tw = MagicMock(); tw.messages.create.return_value = MagicMock(sid="SM%d" % i, status="queued", error_code=None, error_message=None)
    with patch.object(pipeline_service, "analyze_and_respond", return_value=_analysis(reply=ai)), \
         patch("app.services.sms_service._resolve_twilio_creds", return_value=(tw, "+19998887777", None)):
        res = pipeline_service.process_inbound_reply(db_session, lead, sample_advisor, r)
    assert res.get("action") == EXPECT[body], (body, res)
    if EXPECT[body] == "auto_sent":
        tw.messages.create.assert_called_once()
    else:
        tw.messages.create.assert_not_called()
