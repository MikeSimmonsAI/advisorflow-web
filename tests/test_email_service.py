"""
Tests for app/services/email_service.py
"""

from unittest.mock import patch
import pytest

from app.services.email_service import render_email, send_email_to_lead, send_email_batch
from app.models.models import Lead, MessageTrack, EmailMessage
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
def test_send_email_to_lead_does_not_route_through_microsoft_graph(mock_provider, mock_graph, db_session, sample_org, sample_advisor):
    """Per-advisor Microsoft 365 sending is NO LONGER the outbound path, and
    this test asserts the current behaviour rather than the old one.

    send_email_to_lead says why in its own comments: Graph hit anti-spam quota
    limits (WASCL RefuseQuota) during bulk sends, so outbound moved to Resend
    on the organization's verified domain. The old version of this test
    asserted Graph WAS called when an advisor had Microsoft connected, which
    has not been true since that change - it was testing a removed route.

    Kept rather than deleted because the guarantee still matters: a connected
    Microsoft account must not quietly divert a family's mail to a second
    provider with different deliverability and a different From address."""
    mock_provider.return_value = {"success": True, "provider_message_id": "rs1", "error": None}
    sample_advisor.microsoft_365_connected = True
    sample_advisor.microsoft_email_address = "mike@restland.com"
    db_session.commit()

    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="Test", last_name="Lead", email="lead@example.com", contact_channel="email_only")
    db_session.add(lead)
    db_session.commit()

    send_email_to_lead(db_session, sample_advisor, lead)

    mock_provider.assert_called_once()
    mock_graph.assert_not_called()


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
# Direct-send compliance. send_email_to_lead now runs the SAME
# check_compliance_preflight(channel="email") the auto-send queue runs, so a
# family that opted out cannot be emailed by hand from Lead Detail either.
# In every case below the provider must never be called and no EmailMessage
# row may be written.
# ---------------------------------------------------------------------------

def _email_lead(db_session, org, advisor, **kw):
    lead = Lead(organization_id=org.id, assigned_to_id=advisor.id,
                first_name="Gate", last_name="Test",
                email=kw.pop("email", "gate@example.com"),
                contact_channel="email_only", **kw)
    db_session.add(lead)
    db_session.commit()
    return lead


@patch("app.services.email_service.send_email_via_provider")
def test_send_email_to_lead_blocks_a_dnc_lead(mock_provider, db_session, sample_org, sample_advisor):
    """THE GAP THIS CLOSES: a family who replied STOP to a text was still
    emailable by hand. DNC is channel-agnostic."""
    lead = _email_lead(db_session, sample_org, sample_advisor, status="dnc")

    with pytest.raises(ValueError, match="DNC"):
        send_email_to_lead(db_session, sample_advisor, lead)

    mock_provider.assert_not_called()
    assert db_session.query(EmailMessage).filter(EmailMessage.lead_id == lead.id).count() == 0


@patch("app.services.email_service.send_email_via_provider")
def test_send_email_to_lead_blocks_an_explicit_email_opt_out(mock_provider, db_session, sample_org, sample_advisor):
    """allow_email is the platform's email permission of record, imported from
    the source system. It was stored and never consulted by this path, which
    made it a note rather than a compliance record."""
    lead = _email_lead(db_session, sample_org, sample_advisor)
    lead.allow_email = False
    db_session.commit()

    with pytest.raises(ValueError, match="opted out of email"):
        send_email_to_lead(db_session, sample_advisor, lead)

    mock_provider.assert_not_called()


@patch("app.services.email_service.send_email_via_provider")
def test_send_email_to_lead_sends_when_allow_email_is_null(mock_provider, db_session, sample_org, sample_advisor):
    """NULL means the source system never stated a preference. Most imported
    rows are NULL, so reading it as an opt-out would stop nearly all email.
    This is the guard against over-blocking."""
    mock_provider.return_value = {"success": True, "provider_message_id": "rs2", "error": None}
    lead = _email_lead(db_session, sample_org, sample_advisor)
    assert lead.allow_email is None

    send_email_to_lead(db_session, sample_advisor, lead)

    mock_provider.assert_called_once()


@patch("app.services.email_service.send_email_via_provider")
def test_send_email_to_lead_still_blocks_a_flagged_bad_address(mock_provider, db_session, sample_org, sample_advisor):
    """Pre-existing behaviour, now enforced through the shared gate rather
    than an inline check - it must not have been lost in the move."""
    lead = _email_lead(db_session, sample_org, sample_advisor, email="unknow@unknown")
    lead.manual_flag = "bad_email"
    db_session.commit()

    with pytest.raises(ValueError, match="unusable"):
        send_email_to_lead(db_session, sample_advisor, lead)

    mock_provider.assert_not_called()


@patch("app.services.email_service.send_email_via_provider")
def test_send_email_to_lead_is_not_blocked_by_a_suppressed_phone(mock_provider, db_session, sample_org, sample_advisor):
    """THE CROSS-CHANNEL RULE THAT MUST NOT EXIST. suppression_entries holds
    phone numbers. A family who asked not to be texted has said nothing about
    email, and inventing that prohibition would stop permitted mail."""
    from app.models.models import SuppressionEntry
    mock_provider.return_value = {"success": True, "provider_message_id": "rs3", "error": None}
    lead = _email_lead(db_session, sample_org, sample_advisor, phone="12145557777")
    db_session.add(SuppressionEntry(organization_id=sample_org.id, phone="12145557777",
                                    reason="Texted STOP"))
    db_session.commit()

    send_email_to_lead(db_session, sample_advisor, lead)

    mock_provider.assert_called_once()
