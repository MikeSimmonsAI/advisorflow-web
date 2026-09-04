"""
Tests for app/services/sms_service.py

The actual Twilio API call (client.messages.create) is mocked throughout -
these tests verify OUR logic (template rendering, DNC blocking, booking
link creation, batch skip logic), not Twilio's API itself, which would
require real credentials and would actually send messages.
"""

from unittest.mock import patch, MagicMock
import pytest

from app.services.sms_service import render_template, create_booking_link, send_sms, send_batch
from app.models.models import Lead, LeadStatus, BookingLink


def test_render_template_substitutes_all_placeholders(sample_lead, sample_advisor):
    template = "Hi {first_name}, this is {advisor_name}. Book here: {booking_link} or call {advisor_cell}"
    result = render_template(template, sample_lead, sample_advisor, "https://booking.example/abc123")
    assert "Jane" in result  # sample_lead.first_name
    assert "Advisor One" in result  # sample_advisor.full_name
    assert "https://booking.example/abc123" in result
    assert sample_advisor.twilio_phone_number in result
    assert "{first_name}" not in result  # no placeholders left unfilled
    assert "{advisor_name}" not in result


def test_render_template_falls_back_to_there_when_no_first_name(db_session, sample_org, sample_advisor):
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id, last_name="Doe", phone="12145559999")
    template = "Hi {first_name}!"
    result = render_template(template, lead, sample_advisor, "")
    assert "Hi there!" == result


def test_create_booking_link_persists_and_returns_token(db_session, sample_lead, sample_advisor):
    booking = create_booking_link(db_session, sample_lead, sample_advisor)
    assert booking.id is not None
    assert booking.token is not None
    assert booking.lead_id == sample_lead.id
    assert booking.user_id == sample_advisor.id
    assert booking.status == "pending"

    # actually persisted, not just returned in memory
    fetched = db_session.query(BookingLink).filter(BookingLink.id == booking.id).first()
    assert fetched is not None


def test_send_sms_blocks_dnc_leads(db_session, sample_org, sample_advisor):
    dnc_lead = Lead(
        organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
        first_name="Blocked", last_name="Lead", phone="12145550000", status=LeadStatus.DNC,
    )
    db_session.add(dnc_lead)
    db_session.commit()

    with pytest.raises(ValueError, match="DNC"):
        send_sms(db_session, sample_advisor, dnc_lead, "Hi {first_name}")


def test_send_sms_blocks_suppressed_numbers_even_when_status_is_not_dnc(db_session, sample_org, sample_advisor):
    """
    REAL ENFORCEMENT GAP FIXED: confirmed by testing that send_sms
    previously only checked Lead.status == "dnc", never the actual
    SuppressionEntry table. A number sitting in the suppression list
    with a lead whose status was still "new" (e.g. because the
    phone-format mismatch in compliance_router.py prevented the status
    update from ever matching) would have sailed straight through.
    """
    from app.models.models import SuppressionEntry
    lead = Lead(
        organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
        first_name="Suppressed", last_name="NotMarkedDNC", phone="12145559876", status=LeadStatus.NEW,
    )
    db_session.add(lead)
    db_session.commit()
    assert lead.status == LeadStatus.NEW  # confirms this is exactly the gap scenario

    suppression = SuppressionEntry(organization_id=sample_org.id, phone="12145559876", reason="Manually suppressed")
    db_session.add(suppression)
    db_session.commit()

    with pytest.raises(ValueError, match="suppression"):
        send_sms(db_session, sample_advisor, lead, "Hi {first_name}")


@patch("app.services.sms_service._resolve_twilio_creds")
def test_send_sms_creates_message_record_on_success(mock_get_client, db_session, sample_lead, sample_advisor):
    mock_twilio_message = MagicMock(sid="SM123", status="queued",
                                    error_code=None, error_message=None)
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_twilio_message
    mock_get_client.return_value = (mock_client, "+12145551111", None)

    message = send_sms(db_session, sample_advisor, sample_lead, "Hi {first_name}, lock in your price!")

    assert message.twilio_sid == "SM123"
    assert message.twilio_status == "queued"
    assert "Jane" in message.body
    db_session.refresh(sample_lead)
    assert sample_lead.status == "sent"


@patch("app.services.sms_service._resolve_twilio_creds")
def test_send_sms_requesting_a_booking_link_sends_no_url_and_mints_no_token(mock_get_client, db_session, sample_lead, sample_advisor):
    """SMS carries no URL under the current A2P campaign.

    sms_content_policy.LINKS_ALLOWED is False because campaign CO3YNIF is
    registered has_embedded_links=false - a URL in the body is filtered by the
    carrier as error 30007, so the message simply does not arrive. This test
    used to assert the opposite ("the link is in the body"), which stopped
    being true when the policy landed.

    The second half matters as much as the first: no booking token is created
    either. A token minted and never delivered is a dead row and a misleading
    audit trail - it reads as "a link was issued to this family" for a message
    that carried none."""
    mock_client = MagicMock()
    mock_client.messages.create.return_value = MagicMock(sid="SM456", status="queued",
                                                    error_code=None, error_message=None)
    mock_get_client.return_value = (mock_client, "+12145551111", None)

    message = send_sms(db_session, sample_advisor, sample_lead, "Book here: {booking_link}", include_booking_link=True)

    assert "http" not in message.body
    assert message.booking_link_id is None
    # The opt-out sentence is still guaranteed exactly once.
    assert message.body.lower().count("reply stop") == 1


@patch("app.services.sms_service._resolve_twilio_creds")
def test_send_sms_skips_booking_link_when_not_requested(mock_get_client, db_session, sample_lead, sample_advisor):
    mock_client = MagicMock()
    mock_client.messages.create.return_value = MagicMock(sid="SM789", status="queued",
                                                    error_code=None, error_message=None)
    mock_get_client.return_value = (mock_client, "+12145551111", None)

    message = send_sms(db_session, sample_advisor, sample_lead, "Hi {first_name}", include_booking_link=False)
    assert message.booking_link_id is None


@patch("app.services.sms_service._resolve_twilio_creds")
def test_send_batch_skips_dnc_and_duplicate_leads(mock_get_client, db_session, sample_org, sample_advisor):
    mock_client = MagicMock()
    mock_client.messages.create.return_value = MagicMock(sid="SM999", status="queued",
                                                    error_code=None, error_message=None)
    mock_get_client.return_value = (mock_client, "+12145551111", None)

    good_lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                      first_name="Good", last_name="Lead", phone="12145551111", status=LeadStatus.NEW)
    dnc_lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                     first_name="Bad", last_name="Lead", phone="12145552222", status=LeadStatus.DNC)
    dup_lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                     first_name="Dup", last_name="Lead", phone="12145553333", status=LeadStatus.NEW, is_duplicate=True)
    db_session.add_all([good_lead, dnc_lead, dup_lead])
    db_session.commit()

    result = send_batch(db_session, sample_advisor, [good_lead, dnc_lead, dup_lead], "Hi {first_name}")
    assert result["sent_count"] == 1
    assert result["skipped_count"] == 2
    # sent_ids holds the created Message IDs, not Lead IDs - just confirm one was recorded
    assert len(result["sent_ids"]) == 1
    assert dnc_lead.id in result["skipped_ids"]
    assert dup_lead.id in result["skipped_ids"]


def test_get_twilio_client_raises_clear_error_when_unconfigured(db_session, sample_org):
    """The precondition is now explicit. sample_org carries fake org-level
    Twilio credentials (that is the production model), so this test clears
    them to describe the organization it is actually about: one that has an
    advisor with a sending number and no account behind it."""
    from app.services.sms_service import get_twilio_client
    from app.models.models import User
    from app.services.auth_service import hash_password

    sample_org.org_twilio_account_sid = None
    sample_org.org_twilio_auth_token_encrypted = None
    db_session.commit()

    unconfigured_advisor = User(
        organization_id=sample_org.id, email="noconfig@test.com",
        password_hash=hash_password("x"), full_name="No Config", role="advisor",
        twilio_phone_number="+12145559999",
        must_change_password=False,
    )
    db_session.add(unconfigured_advisor)
    db_session.commit()

    with pytest.raises(ValueError, match="no Twilio credentials"):
        get_twilio_client(unconfigured_advisor)
