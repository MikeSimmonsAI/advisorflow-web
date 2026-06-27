"""
Tests for app/routers/auto_send_router.py - the Phase 1 review queue.
Every endpoint here either reads candidates or, when it sends, goes
through the exact same DNC/suppression-checked send_exact_sms used
elsewhere. The single most important property across these tests:
nothing ever sends without an explicit advisor action on a real,
pending candidate.
"""

from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from app.models.models import Lead, AutoSendCandidate, AutoSendCandidateStatus


def _candidate(db_session, sample_org, sample_advisor, body="Sounds good, see you at 2pm!", phone="12145559800"):
    import uuid
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="Candidate", last_name="Lead", phone=phone)
    db_session.add(lead)
    db_session.flush()
    candidate = AutoSendCandidate(
        reply_id=f"fake-reply-{uuid.uuid4()}", lead_id=lead.id, advisor_id=sample_advisor.id,
        ai_drafted_body=body, eligibility_reasoning="Simple scheduling question.",
        classification_confidence="high",
    )
    db_session.add(candidate)
    db_session.commit()
    return lead, candidate


# ---------------------------------------------------------------------------
# Listing and counts
# ---------------------------------------------------------------------------

def test_list_queue_requires_auth(client):
    response = client.get("/auto-send/queue")
    assert response.status_code == 401


def test_list_queue_returns_only_pending_candidates(client, db_session, sample_org, sample_advisor, auth_headers):
    lead, pending = _candidate(db_session, sample_org, sample_advisor)
    _, resolved = _candidate(db_session, sample_org, sample_advisor, phone="12145559801")
    resolved.status = AutoSendCandidateStatus.CONFIRMED
    db_session.commit()

    response = client.get("/auto-send/queue", headers=auth_headers)

    assert response.status_code == 200
    ids = [c["candidate_id"] for c in response.json()]
    assert pending.id in ids
    assert resolved.id not in ids


def test_list_queue_scoped_to_calling_advisor_only(client, db_session, sample_org, sample_advisor, second_advisor, auth_headers):
    _, other_candidate = _candidate(db_session, sample_org, second_advisor, phone="12145559802")

    response = client.get("/auto-send/queue", headers=auth_headers)

    ids = [c["candidate_id"] for c in response.json()]
    assert other_candidate.id not in ids


def test_queue_counts_reflects_real_pending_count(client, db_session, sample_org, sample_advisor, auth_headers):
    _candidate(db_session, sample_org, sample_advisor, phone="12145559803")
    _candidate(db_session, sample_org, sample_advisor, phone="12145559804")

    response = client.get("/auto-send/queue/counts", headers=auth_headers)

    assert response.json()["pending_count"] == 2


# ---------------------------------------------------------------------------
# Confirm - sends the AI draft exactly as-is
# ---------------------------------------------------------------------------

@patch("app.services.sms_service.get_twilio_client")
def test_confirm_sends_the_exact_drafted_body(mock_get_client, client, db_session, sample_org, sample_advisor, auth_headers):
    lead, candidate = _candidate(db_session, sample_org, sample_advisor, body="See you Tuesday at 2pm!")
    mock_client = MagicMock()
    mock_client.messages.create.return_value = MagicMock(sid="SM_confirm", status="queued")
    mock_get_client.return_value = mock_client

    response = client.post(f"/auto-send/queue/{candidate.id}/confirm", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["status"] == "confirmed"
    mock_client.messages.create.assert_called_once()
    assert mock_client.messages.create.call_args.kwargs["body"] == "See you Tuesday at 2pm!"

    db_session.refresh(candidate)
    assert candidate.status == AutoSendCandidateStatus.CONFIRMED
    assert candidate.final_sent_body == "See you Tuesday at 2pm!"
    assert candidate.message_id is not None


def test_confirm_rejects_an_already_resolved_candidate(client, db_session, sample_org, sample_advisor, auth_headers):
    lead, candidate = _candidate(db_session, sample_org, sample_advisor)
    candidate.status = AutoSendCandidateStatus.OVERRIDDEN
    db_session.commit()

    response = client.post(f"/auto-send/queue/{candidate.id}/confirm", headers=auth_headers)

    assert response.status_code == 400


def test_confirm_404s_for_another_advisors_candidate(client, db_session, sample_org, second_advisor, auth_headers):
    lead, candidate = _candidate(db_session, sample_org, second_advisor, phone="12145559805")

    response = client.post(f"/auto-send/queue/{candidate.id}/confirm", headers=auth_headers)

    assert response.status_code == 404


@patch("app.services.compliance_service.is_phone_suppressed")
def test_confirm_respects_the_suppression_list(mock_suppressed, client, db_session, sample_org, sample_advisor, auth_headers):
    """Confirms the real safety check is genuinely wired in, not bypassed by this queue."""
    mock_suppressed.return_value = True
    lead, candidate = _candidate(db_session, sample_org, sample_advisor)

    response = client.post(f"/auto-send/queue/{candidate.id}/confirm", headers=auth_headers)

    assert response.status_code == 400
    assert "suppression" in response.json()["detail"]
    db_session.refresh(candidate)
    assert candidate.status == AutoSendCandidateStatus.PENDING  # must NOT have been marked resolved


# ---------------------------------------------------------------------------
# Edit and send
# ---------------------------------------------------------------------------

@patch("app.services.sms_service.get_twilio_client")
def test_edit_and_send_uses_the_edited_body_not_the_original_draft(mock_get_client, client, db_session, sample_org, sample_advisor, auth_headers):
    lead, candidate = _candidate(db_session, sample_org, sample_advisor, body="Original AI draft.")
    mock_client = MagicMock()
    mock_client.messages.create.return_value = MagicMock(sid="SM_edit", status="queued")
    mock_get_client.return_value = mock_client

    response = client.post(f"/auto-send/queue/{candidate.id}/edit-and-send", json={"body": "My edited version."}, headers=auth_headers)

    assert response.status_code == 200
    assert mock_client.messages.create.call_args.kwargs["body"] == "My edited version."

    db_session.refresh(candidate)
    assert candidate.status == AutoSendCandidateStatus.EDITED_SENT
    assert candidate.final_sent_body == "My edited version."
    assert candidate.ai_drafted_body == "Original AI draft."  # original preserved for comparison


def test_edit_and_send_rejects_empty_body(client, db_session, sample_org, sample_advisor, auth_headers):
    lead, candidate = _candidate(db_session, sample_org, sample_advisor)

    response = client.post(f"/auto-send/queue/{candidate.id}/edit-and-send", json={"body": "   "}, headers=auth_headers)

    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Override - declines the draft, sends nothing
# ---------------------------------------------------------------------------

def test_override_sends_nothing_at_all(client, db_session, sample_org, sample_advisor, auth_headers):
    lead, candidate = _candidate(db_session, sample_org, sample_advisor)

    with patch("app.services.sms_service.get_twilio_client") as mock_get_client:
        response = client.post(f"/auto-send/queue/{candidate.id}/override", headers=auth_headers)

        mock_get_client.assert_not_called()

    assert response.status_code == 200
    db_session.refresh(candidate)
    assert candidate.status == AutoSendCandidateStatus.OVERRIDDEN
    assert candidate.message_id is None


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

def test_history_only_shows_resolved_candidates(client, db_session, sample_org, sample_advisor, auth_headers):
    lead, pending = _candidate(db_session, sample_org, sample_advisor, phone="12145559806")
    _, resolved = _candidate(db_session, sample_org, sample_advisor, phone="12145559807")
    resolved.status = AutoSendCandidateStatus.OVERRIDDEN
    resolved.resolved_at = datetime.now(timezone.utc)
    db_session.commit()

    response = client.get("/auto-send/history", headers=auth_headers)

    ids = [c["candidate_id"] for c in response.json()]
    assert resolved.id in ids
    assert pending.id not in ids
