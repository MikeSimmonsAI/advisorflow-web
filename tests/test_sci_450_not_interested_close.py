"""SCI #450: a clear rejection is terminal, not an AI/human handoff.

Regression for the October 31 George Allen email reply. Synthetic lead only.
"""
from unittest.mock import patch

import pytest

from app.models.models import CadenceState
from app.models.program_models import ProgramAlert, ProgramConversation
from app.services.programs import ai_setter, reply_rules, responses
from tests.test_outreach_program import _lead_for, program  # noqa: F401


@pytest.mark.parametrize("words", [
    "No longer interested.",
    "I'm no longer interested.",
    "I am no longer interested, thank you.",
    "Not interested.",
    "Not interested anymore.",
])
def test_clear_rejections_close_without_staff_handoff(words):
    classified = reply_rules.classify(words)
    assert classified["class"] == reply_rules.OPT_OUT
    assert reply_rules.intents(words, classified["class"]) == [
        reply_rules.NOT_INTERESTED, reply_rules.I_OPT_OUT
    ]
    assert reply_rules.alert_plan(classified["class"]) is None
    assert reply_rules.opt_out_needs_person(words) is False


def test_no_longer_interested_email_stops_journey_without_handoff_or_reply(program, sample_advisor):
    db, org, prog = program["db"], program["org"], program["prog"]
    prog.ai_setter_mode = ai_setter.AUTO
    db.commit()

    lead = _lead_for(db, org, sample_advisor, "L001")
    with patch("app.services.programs.ai_setter.decide") as ai_decide, \
         patch("app.services.programs.ai_setter._handoff") as handoff, \
         patch("app.services.email_service.send_email_via_provider") as email_send:
        resp = responses.on_inbound(db, lead, "No longer interested.", "email")
        ai_decide.assert_not_called()
        handoff.assert_not_called()
        email_send.assert_not_called()

    db.refresh(lead)
    assert resp.response_class == reply_rules.OPT_OUT
    assert resp.handling_status == "closed"
    assert lead.allow_email is False

    st = db.query(CadenceState).filter_by(lead_id=lead.id).one()
    assert str(getattr(st.status, "value", st.status)) == "stopped_dnc"

    conv = db.query(ProgramConversation).filter_by(lead_id=lead.id).one()
    assert conv.state == "stopped"
    assert conv.next_followup_at is None
    assert db.query(ProgramAlert).filter_by(response_id=resp.id).count() == 0
