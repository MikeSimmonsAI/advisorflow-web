"""A SCHEDULED TOUCH YIELDS TO WHAT THE CUSTOMER SAID.

The AI cadence loop (process_scheduled_touches) sent the next scripted touch
on the cadence clock whatever had happened since. A reply set the
conversation's stage to "replied", which the loop does not exclude - so
"call me next week" got the next email on the old schedule, "stop" was
answered with a pitch, and an unanswered question was talked over.

Now conversation intelligence decides (ci.decide_outreach). Nothing here
sends: _send_touch is replaced and every assertion is about whether it was
called and where the schedule moved.
"""
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from app.models.models import Lead, Message, PipelineConversation, Reply
from app.services import ai_conversation_service as acs


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("RESEND_API_KEY", "test-key-not-real")
    monkeypatch.setenv("AI_BACKGROUND_AUTOMATION_ENABLED", "true")


def _setup(db, org, advisor):
    lead = Lead(organization_id=org.id, assigned_to_id=advisor.id, first_name="Pat", last_name="Family",
                email="family@example.com", phone="12145557100", status="replied")
    db.add(lead); db.commit()
    now = datetime.utcnow()
    db.add(Message(lead_id=lead.id, sender_id=advisor.id, body="Hi Pat, checking in about your plan.",
                   sent_at=now - timedelta(days=2), send_source="ai_conversation"))
    db.commit()
    conv = PipelineConversation(organization_id=org.id, lead_id=lead.id, advisor_id=advisor.id,
                                stage="replied", paused=False, flagged=False, touch_number=1,
                                started_at=now - timedelta(days=3),
                                next_send_at=now - timedelta(minutes=5))
    db.add(conv); db.commit()
    return lead, conv, now


def _inbound(db, lead, text, at):
    db.add(Reply(lead_id=lead.id, body=text, source="sms", received_at=at)); db.commit()


def _ack(db, lead, advisor, at, source="manual"):
    db.add(Message(lead_id=lead.id, sender_id=advisor.id, body="Sounds good - talk then.",
                   sent_at=at, send_source=source)); db.commit()


def _run(db):
    with patch.object(acs, "_send_touch", return_value={"success": True}) as send:
        acs._process_scheduled_touches(db)
    return send


def test_no_reply_the_touch_goes_out(db_session, sample_org, sample_advisor):
    lead, conv, now = _setup(db_session, sample_org, sample_advisor)
    send = _run(db_session)
    assert send.call_count == 1


def test_call_me_next_week_moves_the_touch_to_that_day(db_session, sample_org, sample_advisor):
    lead, conv, now = _setup(db_session, sample_org, sample_advisor)
    _inbound(db_session, lead, "Busy this week - call me next week", now - timedelta(hours=3))
    _ack(db_session, lead, sample_advisor, now - timedelta(hours=2))
    send = _run(db_session)
    send.assert_not_called()
    db_session.refresh(conv)
    assert conv.next_send_at is not None and conv.next_send_at > now + timedelta(hours=12)
    assert conv.stage != "stopped"


def test_an_unanswered_message_is_not_talked_over(db_session, sample_org, sample_advisor):
    lead, conv, now = _setup(db_session, sample_org, sample_advisor)
    _inbound(db_session, lead, "What would the monthly cost be?", now - timedelta(hours=1))
    send = _run(db_session)
    send.assert_not_called()
    db_session.refresh(conv)
    assert conv.next_send_at > now + timedelta(hours=20)       # looked at again tomorrow


def test_stop_ends_the_sequence(db_session, sample_org, sample_advisor):
    lead, conv, now = _setup(db_session, sample_org, sample_advisor)
    _inbound(db_session, lead, "STOP", now - timedelta(hours=1))
    send = _run(db_session)
    send.assert_not_called()
    db_session.refresh(conv)
    assert conv.stage == "stopped" and conv.next_send_at is None


def test_unreadable_conversation_sends_nothing(db_session, sample_org, sample_advisor):
    lead, conv, now = _setup(db_session, sample_org, sample_advisor)
    with patch("app.services.conversation_intel.build_context", side_effect=RuntimeError("boom")):
        send = _run(db_session)
    send.assert_not_called()


def test_not_now_without_a_date_holds_even_after_we_answered(db_session, sample_org, sample_advisor):
    lead, conv, now = _setup(db_session, sample_org, sample_advisor)
    _inbound(db_session, lead, "Not right now, maybe later", now - timedelta(hours=3))
    # Answered by the AI (not a person), so nothing else holds the touch.
    _ack(db_session, lead, sample_advisor, now - timedelta(hours=2), source="ai_conversation")
    send = _run(db_session)
    send.assert_not_called()
