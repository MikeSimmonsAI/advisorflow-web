"""
Tests for app/services/email_service.py
"""

from unittest.mock import patch
import pytest

from app.services.email_service import render_email, send_email_to_lead, send_email_batch
from app.models.models import Lead, LeadStatus, MessageTrack, EmailMessage
from app.services.template_service import upsert_template


def test_render_email_uses_hardcoded_default_when_no_override(db_session, sample_lead, sample_advisor):
    rendered = render_email(db_session, MessageTrack.PRE_NEED_LOCK_PRICE, sample_lead, sample_advisor, "https://booking.example/abc")
    assert "Jane" in rendered["subject"] or "Jane" in rendered["body_html"]
    assert "https://booking.example/abc" in rendered["body_html"]


def test_render_email_uses_org_override_when_one_exists(db_session, sample_org, sample_lead, sample_advisor):
    upsert_template(
        db_session, sample_org.id, MessageTrack.PRE_NEED_LOCK_PRICE, "email",
        "<p>Custom body for {first_name}</p>", sample_advisor.id,
        email_subject_template="Custom subject for {first_name}",
    )
    rendered = render_email(db_session, MessageTrack.PRE_NEED_LOCK_PRICE, sample_lead, sample_advisor, "https://booking.example/xyz")
    assert "Custom subject for Jane" == rendered["subject"]
    assert "Custom body for Jane" in rendered["body_html"]


def test_render_email_falls_back_to_nurture_template_for_unknown_track(db_session, sample_lead, sample_advisor):
    rendered = render_email(db_session, MessageTrack.NEEDS_REVIEW, sample_lead, sample_advisor, "https://booking.example/abc")
    # NEEDS_REVIEW has no dedicated template, should fall back to EMAIL_ONLY_NURTURE default
    assert rendered["subject"] != ""
    assert rendered["body_html"] != ""


def test_send_email_to_lead_raises_without_email_address(db_session, sample_org, sample_advisor):
    lead_no_email = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                          first_name="No", last_name="Email", contact_channel="email_only")
    db_session.add(lead_no_email)
    db_session.commit()

    with pytest.raises(ValueError, match="no email"):
        send_email_to_lead(db_session, sample_advisor, lead_no_email)


@patch("app.services.email_service.send_email_via_provider")
def test_send_email_to_lead_creates_email_message_record(mock_send, db_session, sample_org, sample_advisor):
    mock_send.return_value = {"success": True, "provider_message_id": "sg123", "error": None}

    email_lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                       first_name="Noah", last_name="Frey", email="noah@example.com",
                       contact_channel="email_only", message_track=MessageTrack.EMAIL_ONLY_NURTURE)
    db_session.add(email_lead)
    db_session.commit()

    msg = send_email_to_lead(db_session, sample_advisor, email_lead)
    assert msg.status == "sent"
    assert msg.provider_message_id == "sg123"
    assert "Noah" in msg.subject or "Noah" in msg.body_html

    fetched = db_session.query(EmailMessage).filter(EmailMessage.id == msg.id).first()
    assert fetched is not None


@patch("app.services.email_service.send_email_via_provider")
def test_send_email_to_lead_marks_failed_status_on_provider_failure(mock_send, db_session, sample_org, sample_advisor):
    mock_send.return_value = {"success": False, "provider_message_id": None, "error": "rate limited"}

    email_lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                       first_name="Fail", last_name="Case", email="fail@example.com",
                       contact_channel="email_only")
    db_session.add(email_lead)
    db_session.commit()

    msg = send_email_to_lead(db_session, sample_advisor, email_lead)
    assert msg.status == "failed"


@patch("app.services.email_service.send_email_via_provider")
def test_send_email_batch_skips_leads_without_email(mock_send, db_session, sample_org, sample_advisor):
    mock_send.return_value = {"success": True, "provider_message_id": "x", "error": None}

    has_email = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                      first_name="Has", last_name="Email", email="has@example.com", contact_channel="email_only")
    no_email = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                     first_name="No", last_name="Email", contact_channel="email_only")
    db_session.add_all([has_email, no_email])
    db_session.commit()

    result = send_email_batch(db_session, sample_advisor, [has_email, no_email])
    assert result["sent_count"] == 1
    assert result["skipped_count"] == 1


@patch("app.services.microsoft_email_service.send_email_via_microsoft_graph")
@patch("app.services.email_service.send_email_via_provider")
def test_send_email_to_lead_uses_microsoft_365_when_connected(mock_sendgrid, mock_graph, db_session, sample_org, sample_advisor):
    """
    Confirms the real wiring: an advisor with Microsoft 365 connected
    should send through Graph (their real Outlook mailbox), NOT through
    the shared SendGrid sender - the whole point of the integration
    Mike asked for.
    """
    mock_graph.return_value = {"success": True, "provider_message_id": None, "error": None}
    sample_advisor.microsoft_365_connected = True
    sample_advisor.microsoft_email_address = "mike@restland.com"
    db_session.commit()

    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="Test", last_name="Lead", email="lead@example.com", contact_channel="email_only")
    db_session.add(lead)
    db_session.commit()

    send_email_to_lead(db_session, sample_advisor, lead)

    mock_graph.assert_called_once()
    mock_sendgrid.assert_not_called()


@patch("app.services.email_service.send_email_via_provider")
def test_send_email_to_lead_uses_sendgrid_when_microsoft_not_connected(mock_sendgrid, db_session, sample_org, sample_advisor):
    """The default/fallback path for advisors who haven't connected Microsoft 365 yet."""
    mock_sendgrid.return_value = {"success": True, "provider_message_id": "sg1", "error": None}
    assert sample_advisor.microsoft_365_connected is False

    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="Test", last_name="Lead", email="lead2@example.com", contact_channel="email_only")
    db_session.add(lead)
    db_session.commit()

    send_email_to_lead(db_session, sample_advisor, lead)
    mock_sendgrid.assert_called_once()


# ---------------------------------------------------------------------------
# Compliance Preflight wiring - the actual gap this closes: a lead
# correctly marked DNC after a STOP reply via text could previously
# still receive emails, since send_email_to_lead had NO compliance
# check at all. Now it shares the exact same gate every SMS send path
# uses.
# ---------------------------------------------------------------------------

def test_send_email_to_lead_blocks_dnc_status(db_session, sample_org, sample_advisor):
    dnc_lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                     first_name="Blocked", last_name="ByEmail", email="blocked@example.com",
                     status=LeadStatus.DNC, message_track=MessageTrack.EMAIL_ONLY_NURTURE)
    db_session.add(dnc_lead)
    db_session.commit()

    with pytest.raises(ValueError, match="DNC"):
        send_email_to_lead(db_session, sample_advisor, dnc_lead)


def test_send_email_to_lead_blocks_a_lead_suppressed_via_phone_even_though_sending_by_email(db_session, sample_org, sample_advisor):
    """
    The real, specific scenario Mike described: a lead replies STOP on
    text (suppressing their PHONE number), and this same lead - who
    also has an email on file - must still be blocked from email too,
    not just text.
    """
    from app.models.models import SuppressionEntry
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="Both", last_name="Methods", phone="12145559950",
                email="bothmethods@example.com", message_track=MessageTrack.EMAIL_ONLY_NURTURE)
    db_session.add(lead)
    db_session.commit()
    db_session.add(SuppressionEntry(organization_id=sample_org.id, phone="12145559950", reason="Replied STOP"))
    db_session.commit()

    with pytest.raises(ValueError, match="suppression"):
        send_email_to_lead(db_session, sample_advisor, lead)


def test_send_email_to_lead_blocks_an_email_only_dnc_lead_with_no_phone_at_all(db_session, sample_org, sample_advisor):
    """The real, original gap: an email-only lead has no phone to suppress at all, so the OLD check (which only ever looked at phone suppression) would have let this through even if it had existed."""
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="EmailOnly", last_name="DNC", phone=None,
                email="emailonlydnc@example.com", status=LeadStatus.DNC,
                message_track=MessageTrack.EMAIL_ONLY_NURTURE)
    db_session.add(lead)
    db_session.commit()

    with pytest.raises(ValueError, match="DNC"):
        send_email_to_lead(db_session, sample_advisor, lead)
