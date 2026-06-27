"""
Tests for app/services/google_contacts_service.py - automatic Google
Contacts sync, per Mike's explicit request: "if I upload a spreadsheet,
those contacts need to be able to go into Google Contacts too," with no
separate review step.

Mocks the actual Google API client (same approach as
test_microsoft_email_service.py mocking Microsoft Graph) rather than
hitting the real People API.
"""

from unittest.mock import MagicMock, patch

from app.models.models import Lead, LeadStatus
from app.services.google_contacts_service import (
    sync_lead_to_google_contacts, sync_leads_to_google_contacts_batch,
)
from app.utils.crypto import encrypt_value


def _connected_advisor(db_session, advisor):
    advisor.google_oauth_refresh_token_encrypted = encrypt_value("fake-refresh-token")
    advisor.google_calendar_connected = True
    db_session.commit()
    return advisor


def _lead(db_session, org, advisor, idx, **kwargs):
    lead = Lead(
        organization_id=org.id, assigned_to_id=advisor.id,
        first_name=f"Contact{idx}", last_name="Sync",
        phone=kwargs.pop("phone", f"1214555{idx:04d}"),
        status=LeadStatus.NEW, **kwargs,
    )
    db_session.add(lead)
    db_session.commit()
    return lead


def test_sync_skips_when_advisor_not_connected(db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor, 1)

    result = sync_lead_to_google_contacts(db_session, lead)

    assert result["success"] is False
    assert "not connected" in result["skipped_reason"]
    assert result["error"] is None


def test_sync_skips_when_lead_has_no_assigned_advisor(db_session, sample_org):
    lead = Lead(organization_id=sample_org.id, assigned_to_id=None,
                first_name="Unassigned", last_name="Lead", phone="12145559999", status=LeadStatus.NEW)
    db_session.add(lead)
    db_session.commit()

    result = sync_lead_to_google_contacts(db_session, lead)

    assert result["success"] is False
    assert "no assigned advisor" in result["skipped_reason"]


def test_sync_skips_when_lead_has_no_phone_or_email(db_session, sample_org, sample_advisor):
    _connected_advisor(db_session, sample_advisor)
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="NoContact", last_name="Info", phone=None, email=None, status=LeadStatus.NEW)
    db_session.add(lead)
    db_session.commit()

    result = sync_lead_to_google_contacts(db_session, lead)

    assert result["success"] is False
    assert "no phone or email" in result["skipped_reason"]


@patch("app.services.google_contacts_service._get_people_service")
def test_sync_creates_contact_and_stores_resource_name(mock_get_service, db_session, sample_org, sample_advisor):
    _connected_advisor(db_session, sample_advisor)
    lead = _lead(db_session, sample_org, sample_advisor, 2)

    mock_service = MagicMock()
    mock_service.people().createContact().execute.return_value = {"resourceName": "people/c123456"}
    mock_get_service.return_value = mock_service

    result = sync_lead_to_google_contacts(db_session, lead)

    assert result["success"] is True
    assert result["contact_resource_name"] == "people/c123456"
    db_session.refresh(lead)
    assert lead.google_contact_resource_name == "people/c123456"


@patch("app.services.google_contacts_service._get_people_service")
def test_sync_is_idempotent_does_not_create_duplicate_contact(mock_get_service, db_session, sample_org, sample_advisor):
    """Already-synced lead must not trigger a second Google API call at all."""
    _connected_advisor(db_session, sample_advisor)
    lead = _lead(db_session, sample_org, sample_advisor, 3)
    lead.google_contact_resource_name = "people/already-synced"
    db_session.commit()

    result = sync_lead_to_google_contacts(db_session, lead)

    assert result["success"] is True
    assert result["skipped_reason"] == "Already synced."
    mock_get_service.assert_not_called()


@patch("app.services.google_contacts_service._get_people_service")
def test_sync_handles_google_api_failure_gracefully(mock_get_service, db_session, sample_org, sample_advisor):
    _connected_advisor(db_session, sample_advisor)
    lead = _lead(db_session, sample_org, sample_advisor, 4)

    mock_service = MagicMock()
    mock_service.people().createContact().execute.side_effect = Exception("Rate limit exceeded")
    mock_get_service.return_value = mock_service

    result = sync_lead_to_google_contacts(db_session, lead)

    assert result["success"] is False
    assert "Rate limit exceeded" in result["error"]
    db_session.refresh(lead)
    assert lead.google_contact_resource_name is None


def test_sync_never_raises_even_when_advisor_lookup_itself_would_fail(db_session, sample_org):
    """A lead with a garbage assigned_to_id must return a clean skip, never an unhandled exception."""
    lead = Lead(organization_id=sample_org.id, assigned_to_id="not-a-real-user-id",
                first_name="Bad", last_name="Assignment", phone="12145558888", status=LeadStatus.NEW)
    db_session.add(lead)
    db_session.commit()

    result = sync_lead_to_google_contacts(db_session, lead)

    assert result["success"] is False
    assert result["skipped_reason"] is not None


# ---------------------------------------------------------------------------
# Batch sync - used after a bulk Excel import
# ---------------------------------------------------------------------------

@patch("app.services.google_contacts_service._get_people_service")
def test_batch_sync_counts_succeeded_skipped_and_failed_correctly(mock_get_service, db_session, sample_org, sample_advisor, second_advisor):
    _connected_advisor(db_session, sample_advisor)
    # second_advisor deliberately NOT connected, to produce a real skip

    connected_lead = _lead(db_session, sample_org, sample_advisor, 5)
    unconnected_lead = _lead(db_session, sample_org, second_advisor, 6)

    mock_service = MagicMock()
    mock_service.people().createContact().execute.return_value = {"resourceName": "people/cbatch"}
    mock_get_service.return_value = mock_service

    result = sync_leads_to_google_contacts_batch(db_session, [connected_lead, unconnected_lead])

    assert result["succeeded"] == 1
    assert result["skipped"] == 1
    assert result["failed"] == 0


def test_batch_sync_one_lead_failing_does_not_stop_the_rest(db_session, sample_org, sample_advisor):
    """Per-item isolation - one bad lead in a batch must not prevent the others from being attempted."""
    _connected_advisor(db_session, sample_advisor)
    good_lead = _lead(db_session, sample_org, sample_advisor, 7)
    bad_lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                     first_name="NoContactInfo", last_name="Lead", phone=None, email=None, status=LeadStatus.NEW)
    db_session.add(bad_lead)
    db_session.commit()

    with patch("app.services.google_contacts_service._get_people_service") as mock_get_service:
        mock_service = MagicMock()
        mock_service.people().createContact().execute.return_value = {"resourceName": "people/cgood"}
        mock_get_service.return_value = mock_service

        result = sync_leads_to_google_contacts_batch(db_session, [bad_lead, good_lead])

    assert result["succeeded"] == 1
    assert result["skipped"] == 1


def test_batch_sync_with_empty_list_returns_clean_zero_counts(db_session):
    result = sync_leads_to_google_contacts_batch(db_session, [])
    assert result == {"succeeded": 0, "skipped": 0, "failed": 0, "errors": []}
