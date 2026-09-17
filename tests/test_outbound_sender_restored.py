"""
SS10 — the five repaired senders, exercised end to end with both switches on.

RESTORING CODE IS NOT ENABLING OUTREACH. Every switch in this file is turned
on by monkeypatch for the duration of one test and the provider is always a
mock. The deployment ships with every switch unset and every organization's
list NULL, which two tests at the bottom assert.

What these prove is that when Mike does turn a source on, the path behind it
is correct: compliance runs before the provider, the row is written, the
attribution is right, a provider failure does not become a success, and the
refusals still refuse.
"""

import json
import uuid
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.models.models import (
    BookingFollowup, BookingLink, EmailMessage, Lead, Message, Organization,
    PipelineConversation, Reply, User,
)
from app.services import outbound_email_gate as gate
from app.services import send_source
from app.services.auth_service import create_access_token, hash_password


def _lead(db_session, org, advisor, *, phone=None, email="fam@example.com",
          status="new"):
    lead = Lead(organization_id=org.id, assigned_to_id=getattr(advisor, "id", None),
                first_name="Send", last_name="Back", phone=phone, phone_raw=phone,
                email=email, status=status)
    db_session.add(lead)
    db_session.commit()
    return lead


def _both_on(monkeypatch, db_session, org, *sources):
    """Deployment AND organization, for exactly these sources."""
    for s in gate.GATED_SOURCES:
        monkeypatch.delenv(gate._ENV_BY_SOURCE[s], raising=False)
    for s in sources:
        monkeypatch.setenv(gate._ENV_BY_SOURCE[s], "true")
    org.outbound_email_sources = json.dumps(
        [s for s in sources if s != gate.STAFF_ESCALATION])
    db_session.commit()


def _ok():
    return {"success": True, "provider_message_id": "em_live", "error": None}


def _rejected():
    return {"success": False, "provider_message_id": None,
            "error": "domain not verified"}


# ═══════════════════════════════════════════════════════════════════════════
# The composed sender
# ═══════════════════════════════════════════════════════════════════════════

def test_with_both_switches_on_the_email_is_sent_and_logged(
        db_session, sample_org, sample_advisor, monkeypatch):
    _both_on(monkeypatch, db_session, sample_org, send_source.BULK_AI)
    lead = _lead(db_session, sample_org, sample_advisor)

    with patch("app.services.email_service.send_email_via_provider",
               return_value=_ok()) as provider:
        row = gate.send_lead_email(
            db_session, lead, advisor=sample_advisor,
            subject="Following up", body_html="<p>Hello</p>",
            send_source=send_source.BULK_AI, actor_user_id=sample_advisor.id)

    provider.assert_called_once()
    assert row.status == "sent"
    assert row.send_source == send_source.BULK_AI
    assert row.sent_by_user_id == sample_advisor.id
    assert db_session.query(EmailMessage).filter(
        EmailMessage.lead_id == lead.id).count() == 1


def test_a_provider_rejection_raises_with_its_own_words_and_records_failure(
        db_session, sample_org, sample_advisor, monkeypatch):
    _both_on(monkeypatch, db_session, sample_org, send_source.BULK_AI)
    lead = _lead(db_session, sample_org, sample_advisor)

    with patch("app.services.email_service.send_email_via_provider",
               return_value=_rejected()):
        with pytest.raises(RuntimeError, match="domain not verified"):
            gate.send_lead_email(
                db_session, lead, advisor=sample_advisor,
                subject="S", body_html="<p>B</p>",
                send_source=send_source.BULK_AI)

    row = db_session.query(EmailMessage).filter(
        EmailMessage.lead_id == lead.id).one()
    assert row.status == "failed", "a rejected send must not be recorded as sent"


def test_compliance_refuses_before_the_provider_even_with_both_switches_on(
        db_session, sample_org, sample_advisor, monkeypatch):
    _both_on(monkeypatch, db_session, sample_org, send_source.BULK_AI)
    lead = _lead(db_session, sample_org, sample_advisor, status="dnc")
    with patch("app.services.email_service.send_email_via_provider") as provider:
        with pytest.raises(ValueError, match="DNC"):
            gate.send_lead_email(db_session, lead, advisor=sample_advisor,
                                 subject="S", body_html="<p>B</p>",
                                 send_source=send_source.BULK_AI)
    provider.assert_not_called()
    assert db_session.query(EmailMessage).count() == 0


def test_the_actor_and_the_family_facing_advisor_are_recorded_separately(
        db_session, sample_org, sample_advisor, second_advisor, monkeypatch):
    """Advisor Two owns the lead; Advisor One pressed send."""
    _both_on(monkeypatch, db_session, sample_org, send_source.BULK_AI)
    lead = _lead(db_session, sample_org, second_advisor)

    with patch("app.services.email_service.send_email_via_provider",
               return_value=_ok()):
        row = gate.send_lead_email(
            db_session, lead, advisor=second_advisor,
            subject="S", body_html="<p>B</p>",
            send_source=send_source.BULK_AI, actor_user_id=sample_advisor.id)

    assert row.sender_id == second_advisor.id
    assert row.sent_by_user_id == sample_advisor.id


# ═══════════════════════════════════════════════════════════════════════════
# Site A — bulk AI email
# ═══════════════════════════════════════════════════════════════════════════

def _fake_generate(db, lead, advisor, tone="warm", ai_direction=None,
                   relationship_type=None):
    return {"reply": "Drafted body", "subject": "Drafted subject",
            "should_stop": False, "reason": "", "source": "ai",
            "error_kind": None, "booking_url": ""}


def test_bulk_ai_email_sends_and_records_when_enabled(
        client, auth_headers, db_session, sample_org, sample_advisor, monkeypatch):
    _both_on(monkeypatch, db_session, sample_org, send_source.BULK_AI)
    lead = _lead(db_session, sample_org, sample_advisor)

    with patch("app.routers.ai_conversation_router.generate_auto_reply", _fake_generate), \
         patch("app.services.email_service.send_email_via_provider",
               return_value=_ok()) as provider:
        response = client.post("/ai-conversation/generate-batch", headers=auth_headers,
                               json={"lead_ids": [lead.id], "auto_send": True,
                                     "channel": "email"})

    assert response.status_code == 200
    body = response.json()
    assert body["sent"] == 1 and body["errors"] == 0
    provider.assert_called_once()

    row = db_session.query(EmailMessage).filter(EmailMessage.lead_id == lead.id).one()
    assert row.send_source == send_source.BULK_AI
    assert row.sent_by_user_id == sample_advisor.id
    assert "Drafted body" in row.body_html


def test_bulk_ai_email_still_blocks_a_dnc_lead_when_enabled(
        client, auth_headers, db_session, sample_org, sample_advisor, monkeypatch):
    _both_on(monkeypatch, db_session, sample_org, send_source.BULK_AI)
    lead = _lead(db_session, sample_org, sample_advisor, status="dnc")

    with patch("app.routers.ai_conversation_router.generate_auto_reply", _fake_generate), \
         patch("app.services.email_service.send_email_via_provider") as provider:
        response = client.post("/ai-conversation/generate-batch", headers=auth_headers,
                               json={"lead_ids": [lead.id], "auto_send": True,
                                     "channel": "email"})

    provider.assert_not_called()
    body = response.json()
    assert body["sent"] == 0 and body["skipped"] == 1
    assert body["results"][0]["action"] == "blocked"


def test_bulk_ai_email_sends_nothing_while_the_org_switch_is_off(
        client, auth_headers, db_session, sample_org, sample_advisor, monkeypatch):
    for s in gate.GATED_SOURCES:
        monkeypatch.setenv(gate._ENV_BY_SOURCE[s], "true")   # deployment ON
    sample_org.outbound_email_sources = None                  # customer OFF
    db_session.commit()
    lead = _lead(db_session, sample_org, sample_advisor)

    with patch("app.routers.ai_conversation_router.generate_auto_reply", _fake_generate), \
         patch("app.services.email_service.send_email_via_provider") as provider:
        response = client.post("/ai-conversation/generate-batch", headers=auth_headers,
                               json={"lead_ids": [lead.id], "auto_send": True,
                                     "channel": "email"})

    provider.assert_not_called()
    assert response.json()["sent"] == 0
    assert db_session.query(EmailMessage).count() == 0


# ═══════════════════════════════════════════════════════════════════════════
# Site D — pipeline auto-reply
# ═══════════════════════════════════════════════════════════════════════════

def _analysis(**over):
    base = {"reply": "Happy to help", "confidence": 99, "should_stop": False,
            "stop_reason": "", "intent": "interested", "stage": "ai_responding",
            "include_booking_link": False}
    base.update(over)
    return base


def test_pipeline_email_fallback_sends_and_advances_counters_only_then(
        db_session, sample_org, sample_advisor, monkeypatch):
    from app.services import pipeline_service
    _both_on(monkeypatch, db_session, sample_org, send_source.PIPELINE_AUTO_REPLY)
    sample_advisor.microsoft_365_connected = True
    lead = _lead(db_session, sample_org, sample_advisor)   # no phone -> email branch
    conv = PipelineConversation(organization_id=sample_org.id, lead_id=lead.id,
                                advisor_id=sample_advisor.id, stage="replied",
                                auto_respond=True, confidence_threshold=50)
    reply = Reply(lead_id=lead.id, body="yes")
    db_session.add_all([conv, reply])
    db_session.commit()

    with patch.object(pipeline_service, "analyze_and_respond", return_value=_analysis()), \
         patch("app.services.email_service.send_email_via_provider",
               return_value=_ok()) as provider:
        result = pipeline_service.process_inbound_reply(
            db_session, lead, sample_advisor, reply)

    provider.assert_called_once()
    assert result["action"] == "auto_sent" and result["channel"] == "email"

    db_session.refresh(conv)
    assert conv.ai_responses_sent == 1
    assert conv.messages_sent == 1
    assert conv.last_outbound_at is not None

    row = db_session.query(EmailMessage).filter(EmailMessage.lead_id == lead.id).one()
    assert row.send_source == send_source.PIPELINE_AUTO_REPLY
    assert row.sent_by_user_id is None, "a webhook has no authenticated human"


def test_pipeline_email_provider_failure_advances_nothing(
        db_session, sample_org, sample_advisor, monkeypatch):
    from app.services import pipeline_service
    _both_on(monkeypatch, db_session, sample_org, send_source.PIPELINE_AUTO_REPLY)
    sample_advisor.microsoft_365_connected = True
    lead = _lead(db_session, sample_org, sample_advisor)
    conv = PipelineConversation(organization_id=sample_org.id, lead_id=lead.id,
                                advisor_id=sample_advisor.id, stage="replied",
                                auto_respond=True, confidence_threshold=50)
    reply = Reply(lead_id=lead.id, body="yes")
    db_session.add_all([conv, reply])
    db_session.commit()

    with patch.object(pipeline_service, "analyze_and_respond", return_value=_analysis()), \
         patch("app.services.email_service.send_email_via_provider",
               return_value=_rejected()):
        result = pipeline_service.process_inbound_reply(
            db_session, lead, sample_advisor, reply)

    assert result["action"] == "error"
    db_session.refresh(conv)
    assert (conv.ai_responses_sent or 0) == 0
    assert conv.last_outbound_at is None


# ═══════════════════════════════════════════════════════════════════════════
# Site E — post-appointment follow-up
# ═══════════════════════════════════════════════════════════════════════════

def test_post_appointment_email_sends_and_records_the_followup(
        db_session, sample_org, sample_advisor, monkeypatch):
    from app.services.post_appointment_service import check_and_send_followups
    _both_on(monkeypatch, db_session, sample_org, send_source.APPOINTMENT_FOLLOWUP)
    sample_advisor.microsoft_365_connected = True
    lead = _lead(db_session, sample_org, sample_advisor)
    booking = BookingLink(lead_id=lead.id, user_id=sample_advisor.id, status="booked",
                          booked_time=datetime.utcnow() - timedelta(minutes=20))
    db_session.add(booking)
    db_session.commit()

    with patch("app.services.email_service.send_email_via_provider",
               return_value=_ok()) as provider:
        sent = check_and_send_followups(db_session)

    provider.assert_called_once()
    assert sent == 1
    followup = db_session.query(BookingFollowup).filter(
        BookingFollowup.booking_link_id == booking.id).one()
    assert followup.thank_you_sent is True
    assert followup.channel == "email"

    row = db_session.query(EmailMessage).filter(EmailMessage.lead_id == lead.id).one()
    assert row.send_source == send_source.APPOINTMENT_FOLLOWUP


def test_post_appointment_still_cannot_replay_history_when_enabled(
        db_session, sample_org, sample_advisor, monkeypatch):
    """Enabling the source must not turn the three-hour window into a backlog."""
    from app.services.post_appointment_service import check_and_send_followups
    _both_on(monkeypatch, db_session, sample_org, send_source.APPOINTMENT_FOLLOWUP)
    sample_advisor.microsoft_365_connected = True
    lead = _lead(db_session, sample_org, sample_advisor)
    stale = BookingLink(lead_id=lead.id, user_id=sample_advisor.id, status="booked",
                        booked_time=datetime.utcnow() - timedelta(hours=11))
    db_session.add(stale)
    db_session.commit()

    with patch("app.services.email_service.send_email_via_provider") as provider:
        sent = check_and_send_followups(db_session)

    provider.assert_not_called()
    assert sent == 0
    assert db_session.query(BookingFollowup).count() == 0


def test_post_appointment_failure_records_not_sent(
        db_session, sample_org, sample_advisor, monkeypatch):
    from app.services.post_appointment_service import check_and_send_followups
    _both_on(monkeypatch, db_session, sample_org, send_source.APPOINTMENT_FOLLOWUP)
    sample_advisor.microsoft_365_connected = True
    lead = _lead(db_session, sample_org, sample_advisor)
    booking = BookingLink(lead_id=lead.id, user_id=sample_advisor.id, status="booked",
                          booked_time=datetime.utcnow() - timedelta(minutes=20))
    db_session.add(booking)
    db_session.commit()

    with patch("app.services.email_service.send_email_via_provider",
               return_value=_rejected()):
        check_and_send_followups(db_session)

    followup = db_session.query(BookingFollowup).filter(
        BookingFollowup.booking_link_id == booking.id).one()
    assert followup.thank_you_sent is False
    assert followup.error


# ═══════════════════════════════════════════════════════════════════════════
# Site C — the staff alert
# ═══════════════════════════════════════════════════════════════════════════

def test_the_staff_alert_sends_when_its_own_switch_is_on(
        db_session, sample_org, sample_advisor, monkeypatch):
    _both_on(monkeypatch, db_session, sample_org, gate.STAFF_ESCALATION)
    with patch("app.services.email_service.send_email_via_provider",
               return_value=_ok()) as provider:
        gate.send_staff_email(db_session, sample_advisor, "advisor@example.com",
                              "Escalated", "<p>body</p>", purpose="voice escalation")
    provider.assert_called_once()
    assert db_session.query(EmailMessage).count() == 0, \
        "an advisor alert is not lead communication history"


def test_the_staff_alert_is_not_governed_by_the_customer_list(
        db_session, sample_org, sample_advisor, monkeypatch):
    """It emails our own advisor, so a customer's entitlement does not apply -
    only the deployment switch does."""
    for s in gate.GATED_SOURCES:
        monkeypatch.delenv(gate._ENV_BY_SOURCE[s], raising=False)
    monkeypatch.setenv(gate._ENV_BY_SOURCE[gate.STAFF_ESCALATION], "true")
    sample_org.outbound_email_sources = None
    db_session.commit()
    with patch("app.services.email_service.send_email_via_provider",
               return_value=_ok()) as provider:
        gate.send_staff_email(db_session, sample_advisor, "advisor@example.com",
                              "Escalated", "<p>b</p>", purpose="voice escalation")
    provider.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════
# The shipped posture
# ═══════════════════════════════════════════════════════════════════════════

def test_this_build_ships_with_every_deployment_switch_off():
    assert not any(gate.enablement_report().values())


def test_a_fresh_customer_has_no_sources(db_session, sample_org):
    assert gate.org_enabled_sources(sample_org) == []


def test_with_the_shipped_posture_every_repaired_path_refuses(
        db_session, sample_org, sample_advisor, monkeypatch):
    for s in gate.GATED_SOURCES:
        monkeypatch.delenv(gate._ENV_BY_SOURCE[s], raising=False)
    lead = _lead(db_session, sample_org, sample_advisor)
    with patch("app.services.email_service.send_email_via_provider") as provider:
        for source in (send_source.BULK_AI, send_source.VOICE_BOOKING_LINK,
                       send_source.PIPELINE_AUTO_REPLY,
                       send_source.APPOINTMENT_FOLLOWUP):
            with pytest.raises(gate.EmailSendDisabled):
                gate.send_lead_email(db_session, lead, advisor=sample_advisor,
                                     subject="S", body_html="<p>B</p>",
                                     send_source=source)
    provider.assert_not_called()
