"""
Tests for the message preview/confirm-send flow in leads_router.py -
the "AI drafts a message per lead, advisor reviews/edits, THEN it
sends" workflow Mike specifically asked for, replacing silent
auto-send.
"""

from unittest.mock import patch, MagicMock
from app.models.models import Lead, LeadStatus, LeadTier, MessageTrack, CadenceState


def test_preview_messages_requires_auth(client):
    response = client.post("/leads/preview-messages", json={"lead_ids": ["x"]})
    assert response.status_code == 401


def test_preview_messages_returns_draft_for_normal_lead(client, auth_headers, db_session, sample_lead):
    sample_lead.message_track = MessageTrack.PRE_NEED_LOCK_PRICE
    sample_lead.phone = "12145559999"
    db_session.commit()

    response = client.post("/leads/preview-messages", json={"lead_ids": [sample_lead.id]}, headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["lead_id"] == sample_lead.id
    assert data[0]["draft_message"] != ""
    assert data[0]["skip_reason"] is None
    assert "Jane" in data[0]["draft_message"]  # sample_lead's first name, substituted in


def test_preview_messages_does_not_actually_send_anything(client, auth_headers, db_session, sample_lead):
    """Critical: calling preview must never create a Message record or change lead status."""
    from app.models.models import Message
    sample_lead.phone = "12145559999"
    db_session.commit()

    client.post("/leads/preview-messages", json={"lead_ids": [sample_lead.id]}, headers=auth_headers)

    message_count = db_session.query(Message).filter(Message.lead_id == sample_lead.id).count()
    assert message_count == 0
    db_session.refresh(sample_lead)
    assert sample_lead.status == LeadStatus.NEW  # unchanged


def test_preview_messages_flags_dnc_lead_with_skip_reason(client, auth_headers, db_session, sample_org, sample_advisor):
    dnc_lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                     first_name="DNC", last_name="Lead", phone="12145550001", status=LeadStatus.DNC)
    db_session.add(dnc_lead)
    db_session.commit()

    response = client.post("/leads/preview-messages", json={"lead_ids": [dnc_lead.id]}, headers=auth_headers)
    data = response.json()
    assert data[0]["skip_reason"] is not None
    assert "DNC" in data[0]["skip_reason"]
    assert data[0]["draft_message"] == ""


def test_preview_messages_flags_no_phone_with_skip_reason(client, auth_headers, db_session, sample_org, sample_advisor):
    no_phone_lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                          first_name="No", last_name="Phone", phone=None, email="x@x.com")
    db_session.add(no_phone_lead)
    db_session.commit()

    response = client.post("/leads/preview-messages", json={"lead_ids": [no_phone_lead.id]}, headers=auth_headers)
    data = response.json()
    assert data[0]["skip_reason"] is not None
    assert "phone" in data[0]["skip_reason"].lower()


def test_preview_messages_silently_skips_leads_from_other_orgs(client, auth_headers, db_session):
    """A lead ID from a different org should not appear in the results at all (not even with a skip_reason)."""
    from app.models.models import Organization, User
    from app.services.auth_service import hash_password
    other_org = Organization(name="Other", slug="other-preview-org", plan="trial")
    db_session.add(other_org)
    db_session.commit()
    other_advisor = User(organization_id=other_org.id, email="otherpreview@test.com",
                          password_hash=hash_password("x"), full_name="Other", role="advisor")
    db_session.add(other_advisor)
    db_session.commit()
    foreign_lead = Lead(organization_id=other_org.id, assigned_to_id=other_advisor.id,
                         first_name="Foreign", last_name="Lead", phone="12145550002")
    db_session.add(foreign_lead)
    db_session.commit()

    response = client.post("/leads/preview-messages", json={"lead_ids": [foreign_lead.id]}, headers=auth_headers)
    assert response.json() == []


@patch("app.services.sms_service.get_twilio_client")
def test_confirm_send_batch_sends_edited_message_text(mock_get_client, client, auth_headers, db_session, sample_lead):
    mock_client = MagicMock()
    mock_client.messages.create.return_value = MagicMock(sid="SM_confirm_test", status="queued")
    mock_get_client.return_value = mock_client
    sample_lead.phone = "12145559999"
    db_session.commit()

    response = client.post("/leads/confirm-send-batch", json={
        "items": [{"lead_id": sample_lead.id, "message": "Custom edited message, hand-typed by advisor"}],
        "include_booking_link": False,
    }, headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["sent_count"] == 1

    from app.models.models import Message
    sent_message = db_session.query(Message).filter(Message.lead_id == sample_lead.id).first()
    assert "Custom edited message" in sent_message.body


@patch("app.services.sms_service.get_twilio_client")
def test_confirm_send_batch_starts_cadence_after_sending(mock_get_client, client, auth_headers, db_session, sample_lead):
    """
    Confirms the real gap fix: leads that go through the new
    preview-then-confirm flow must actually enter the 9-touch cadence
    afterward, not sit at status=NEW with no follow-up scheduled.
    """
    mock_client = MagicMock()
    mock_client.messages.create.return_value = MagicMock(sid="SM_cadence_test", status="queued")
    mock_get_client.return_value = mock_client
    sample_lead.phone = "12145559999"
    db_session.commit()

    client.post("/leads/confirm-send-batch", json={
        "items": [{"lead_id": sample_lead.id, "message": "Hi {first_name}"}],
    }, headers=auth_headers)

    cadence = db_session.query(CadenceState).filter(CadenceState.lead_id == sample_lead.id).first()
    assert cadence is not None
    assert cadence.current_touch_number == 0  # touch 1 was the manual send, not yet counted by the cadence engine


def test_confirm_send_batch_reports_not_found_for_foreign_lead(client, auth_headers):
    response = client.post("/leads/confirm-send-batch", json={
        "items": [{"lead_id": "does-not-exist", "message": "test"}],
    }, headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["skipped_count"] == 1
    assert response.json()["skipped"][0]["reason"] == "not_found"
