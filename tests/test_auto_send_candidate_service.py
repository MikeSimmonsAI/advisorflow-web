"""
Tests for app/services/auto_send_candidate_service.py - the gate
deciding whether an advisor's auto_send_phase even allows the
eligibility brain to be consulted at all. The single most important
property this file guarantees: an advisor on the default "off" phase
gets ZERO candidate rows created and ZERO API calls spent, no matter
what the reply says.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.models.models import Lead, Reply, ReplyClassification, AutoSendCandidate
from app.services.auto_send_candidate_service import maybe_create_candidate


def _lead_and_reply(db_session, sample_org, sample_advisor, body="What time works for you?", classification=ReplyClassification.QUESTION, phone="12145559700"):
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="Candidate", last_name="Test", phone=phone)
    db_session.add(lead)
    db_session.flush()
    reply = Reply(lead_id=lead.id, body=body, classification=classification)
    db_session.add(reply)
    db_session.commit()
    return lead, reply


# ---------------------------------------------------------------------------
# The single most important property: "off" (the default) must mean
# zero candidate rows, zero API calls, no matter what.
# ---------------------------------------------------------------------------

def test_default_off_phase_creates_no_candidate_and_makes_no_api_call(db_session, sample_org, sample_advisor):
    lead, reply = _lead_and_reply(db_session, sample_org, sample_advisor)
    assert sample_advisor.auto_send_phase == "off"

    with patch("app.services.auto_send_eligibility_service.check_auto_send_eligibility") as mock_check:
        result = maybe_create_candidate(db_session, reply, lead)

    assert result is None
    mock_check.assert_not_called()
    assert db_session.query(AutoSendCandidate).count() == 0


def test_explicit_off_phase_also_creates_nothing(db_session, sample_org, sample_advisor):
    sample_advisor.auto_send_phase = "off"
    db_session.commit()
    lead, reply = _lead_and_reply(db_session, sample_org, sample_advisor)

    result = maybe_create_candidate(db_session, reply, lead)

    assert result is None


def test_lead_with_no_assigned_advisor_creates_nothing(db_session, sample_org):
    lead = Lead(organization_id=sample_org.id, assigned_to_id=None,
                first_name="Unassigned", last_name="Lead", phone="12145559701")
    db_session.add(lead)
    db_session.flush()
    reply = Reply(lead_id=lead.id, body="What time?", classification=ReplyClassification.QUESTION)
    db_session.add(reply)
    db_session.commit()

    result = maybe_create_candidate(db_session, reply, lead)

    assert result is None


# ---------------------------------------------------------------------------
# Candidate phase - the actual eligibility check runs.
# ---------------------------------------------------------------------------

def test_candidate_phase_with_eligible_reply_creates_a_real_row(db_session, sample_org, sample_advisor):
    sample_advisor.auto_send_phase = "candidate"
    db_session.commit()
    lead, reply = _lead_and_reply(db_session, sample_org, sample_advisor)
    db_session.add(Reply(lead_id=lead.id, body="Hi", received_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1), classification=ReplyClassification.NEUTRAL))
    db_session.commit()

    with patch("app.services.auto_send_eligibility_service.check_auto_send_eligibility") as mock_check:
        mock_check.return_value = {"eligible": True, "confidence": "high", "reasoning": "Simple scheduling question."}

        result = maybe_create_candidate(db_session, reply, lead)

    assert result is not None
    assert result.reply_id == reply.id
    assert result.advisor_id == sample_advisor.id
    assert result.eligibility_reasoning == "Simple scheduling question."
    assert db_session.query(AutoSendCandidate).count() == 1


def test_candidate_phase_with_ineligible_reply_creates_nothing(db_session, sample_org, sample_advisor):
    sample_advisor.auto_send_phase = "candidate"
    db_session.commit()
    lead, reply = _lead_and_reply(db_session, sample_org, sample_advisor, body="Why hasn't anyone called my mother", classification=ReplyClassification.QUESTION)

    with patch("app.services.auto_send_eligibility_service.check_auto_send_eligibility") as mock_check:
        mock_check.return_value = {"eligible": False, "confidence": "high", "reasoning": "Emotional content."}

        result = maybe_create_candidate(db_session, reply, lead)

    assert result is None
    assert db_session.query(AutoSendCandidate).count() == 0


def test_auto_phase_also_runs_the_eligibility_check(db_session, sample_org, sample_advisor):
    """Phase 2 (auto) uses the exact same eligibility gate as Phase 1 - this function doesn't distinguish between them, the distinction is what happens AFTER a candidate is created."""
    sample_advisor.auto_send_phase = "auto"
    db_session.commit()
    lead, reply = _lead_and_reply(db_session, sample_org, sample_advisor)

    with patch("app.services.auto_send_eligibility_service.check_auto_send_eligibility") as mock_check:
        mock_check.return_value = {"eligible": True, "confidence": "high", "reasoning": "Fine."}
        result = maybe_create_candidate(db_session, reply, lead)

    mock_check.assert_called_once()
    assert result is not None


# ---------------------------------------------------------------------------
# First-reply detection - must correctly identify whether prior context exists.
# ---------------------------------------------------------------------------

def test_first_ever_reply_is_correctly_flagged_as_first(db_session, sample_org, sample_advisor):
    sample_advisor.auto_send_phase = "candidate"
    db_session.commit()
    lead, reply = _lead_and_reply(db_session, sample_org, sample_advisor)

    with patch("app.services.auto_send_eligibility_service.check_auto_send_eligibility") as mock_check:
        mock_check.return_value = {"eligible": True, "confidence": "high", "reasoning": "x"}
        maybe_create_candidate(db_session, reply, lead)

    assert mock_check.call_args.kwargs["is_first_reply"] is True


def test_reply_with_prior_history_is_correctly_flagged_as_not_first(db_session, sample_org, sample_advisor):
    sample_advisor.auto_send_phase = "candidate"
    db_session.commit()
    lead, reply = _lead_and_reply(db_session, sample_org, sample_advisor)
    earlier_reply = Reply(lead_id=lead.id, body="Hi there", received_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2), classification=ReplyClassification.NEUTRAL)
    db_session.add(earlier_reply)
    db_session.commit()

    with patch("app.services.auto_send_eligibility_service.check_auto_send_eligibility") as mock_check:
        mock_check.return_value = {"eligible": True, "confidence": "high", "reasoning": "x"}
        maybe_create_candidate(db_session, reply, lead)

    assert mock_check.call_args.kwargs["is_first_reply"] is False


# ---------------------------------------------------------------------------
# Failure handling - any exception must result in no candidate, never a crash.
# ---------------------------------------------------------------------------

def test_eligibility_check_raising_an_exception_creates_no_candidate(db_session, sample_org, sample_advisor):
    sample_advisor.auto_send_phase = "candidate"
    db_session.commit()
    lead, reply = _lead_and_reply(db_session, sample_org, sample_advisor)

    with patch("app.services.auto_send_eligibility_service.check_auto_send_eligibility") as mock_check:
        mock_check.side_effect = Exception("Unexpected failure")

        result = maybe_create_candidate(db_session, reply, lead)

    assert result is None
    assert db_session.query(AutoSendCandidate).count() == 0


def test_reply_with_no_classification_creates_nothing(db_session, sample_org, sample_advisor):
    """
    Reply.classification has a column-level default (NEUTRAL) that
    overrides an explicit None on commit - confirmed by direct
    investigation, not assumed. The only way this function ever sees
    classification=None in practice is an in-memory Reply object that
    hasn't been through a real commit/refresh cycle - constructed
    directly here to test that real defensive branch.
    """
    sample_advisor.auto_send_phase = "candidate"
    db_session.commit()
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="NoClass", last_name="Lead", phone="12145559702")
    db_session.add(lead)
    db_session.commit()
    reply = Reply(lead_id=lead.id, body="hi")
    reply.classification = None  # force it after construction, bypassing the column default that fires on assignment-via-constructor too

    with patch("app.services.auto_send_eligibility_service.check_auto_send_eligibility") as mock_check:
        result = maybe_create_candidate(db_session, reply, lead)

    assert result is None
    mock_check.assert_not_called()


# ---------------------------------------------------------------------------
# AI drafting - confirms the candidate actually gets a real drafted
# response, reusing draft_reply_service, not left empty.
# ---------------------------------------------------------------------------

def test_eligible_candidate_gets_a_real_drafted_reply(db_session, sample_org, sample_advisor):
    sample_advisor.auto_send_phase = "candidate"
    db_session.commit()
    lead, reply = _lead_and_reply(db_session, sample_org, sample_advisor)

    with patch("app.services.auto_send_eligibility_service.check_auto_send_eligibility") as mock_check:
        mock_check.return_value = {"eligible": True, "confidence": "high", "reasoning": "x"}

        result = maybe_create_candidate(db_session, reply, lead)

    assert result is not None
    # No OPENAI_API_KEY in the test environment, so draft_reply's own
    # internal fallback fires - the real, important thing being
    # confirmed here is that ai_drafted_body is NOT left empty, not
    # which exact fallback text it contains.
    assert result.ai_drafted_body != ""
    assert isinstance(result.ai_drafted_body, str)


def test_drafting_failure_still_creates_candidate_with_empty_draft_not_a_crash(db_session, sample_org, sample_advisor):
    """A failure in drafting must never block candidate creation - the advisor can write the reply themselves in the review queue."""
    sample_advisor.auto_send_phase = "candidate"
    db_session.commit()
    lead, reply = _lead_and_reply(db_session, sample_org, sample_advisor)

    with patch("app.services.auto_send_eligibility_service.check_auto_send_eligibility") as mock_check, \
         patch("app.services.draft_reply_service.draft_reply") as mock_draft:
        mock_check.return_value = {"eligible": True, "confidence": "high", "reasoning": "x"}
        mock_draft.side_effect = Exception("Drafting blew up unexpectedly")

        result = maybe_create_candidate(db_session, reply, lead)

    assert result is not None
    assert result.ai_drafted_body == ""
