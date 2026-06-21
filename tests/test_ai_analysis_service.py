"""
Tests for app/services/ai_analysis_service.py

The OpenAI API call is mocked throughout - these tests verify OUR logic
(prompt construction, JSON parsing, the fallback heuristic, and writing
results back to the Lead record), not OpenAI's actual model output.
"""

import json
from unittest.mock import patch, MagicMock

from app.services.ai_analysis_service import (
    analyze_lead_quality, _fallback_heuristic, analyze_lead, analyze_batch,
)
from app.models.models import Lead, LeadTier


# --- Fallback heuristic tests (pure logic, no mocking needed) ---

def test_fallback_classifies_non_viable_as_dead():
    result = _fallback_heuristic(tier="pre_need", status_reason="New", last_action="Called: Non Viable Lead")
    assert result["quality"] == "dead"


def test_fallback_classifies_scheduled_appt_as_warm():
    result = _fallback_heuristic(tier="pre_need", status_reason="New", last_action="Called: Scheduled Appt.")
    assert result["quality"] == "warm"


def test_fallback_classifies_contract_sold_as_warm_regardless_of_last_action():
    result = _fallback_heuristic(tier="contract_sold", status_reason="Contract Sold", last_action=None)
    assert result["quality"] == "warm"


def test_fallback_classifies_no_answer_as_cold():
    result = _fallback_heuristic(tier="pre_need", status_reason="Attempting Contact", last_action="Called: LM/No Answer")
    assert result["quality"] == "cold"


def test_fallback_classifies_no_history_as_unknown():
    result = _fallback_heuristic(tier="partial", status_reason="New", last_action=None)
    assert result["quality"] == "unknown"


def test_fallback_includes_error_in_reasoning_when_provided():
    result = _fallback_heuristic(tier="pre_need", status_reason=None, last_action=None, error="429 rate limit")
    assert "429 rate limit" in result["reasoning"]


# --- analyze_lead_quality: success path (mocked OpenAI response) ---

@patch("app.services.ai_analysis_service._get_client")
def test_analyze_lead_quality_parses_valid_json_response(mock_get_client):
    mock_response = MagicMock()
    mock_response.choices[0].message.content = json.dumps({
        "quality": "hot",
        "recommended_approach": "Call immediately",
        "reasoning": "Scheduled an appointment recently",
    })
    mock_get_client.return_value.chat.completions.create.return_value = mock_response

    result = analyze_lead_quality(tier="pre_need", status_reason="New", last_action="Called: Scheduled Appt.")
    assert result["quality"] == "hot"
    assert result["recommended_approach"] == "Call immediately"


@patch("app.services.ai_analysis_service._get_client")
def test_analyze_lead_quality_strips_markdown_fences_before_parsing(mock_get_client):
    """Real LLM responses sometimes wrap JSON in ```json fences despite instructions not to."""
    mock_response = MagicMock()
    mock_response.choices[0].message.content = '```json\n{"quality": "warm", "recommended_approach": "Follow up", "reasoning": "test"}\n```'
    mock_get_client.return_value.chat.completions.create.return_value = mock_response

    result = analyze_lead_quality(tier="at_need")
    assert result["quality"] == "warm"


@patch("app.services.ai_analysis_service._get_client")
def test_analyze_lead_quality_falls_back_on_api_exception(mock_get_client):
    mock_get_client.return_value.chat.completions.create.side_effect = Exception("429 Too Many Requests")

    result = analyze_lead_quality(tier="pre_need", status_reason="New", last_action="Called: Scheduled Appt.")
    # Should have fallen through to the heuristic, which classifies this as warm
    assert result["quality"] == "warm"
    assert "429" in result["reasoning"]


@patch("app.services.ai_analysis_service._get_client")
def test_analyze_lead_quality_falls_back_on_malformed_json(mock_get_client):
    """If the model returns text that isn't valid JSON at all, fall back rather than crash."""
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "I think this lead is probably warm because..."
    mock_get_client.return_value.chat.completions.create.return_value = mock_response

    result = analyze_lead_quality(tier="pre_need", last_action="Called: LM/No Answer")
    # Falls back to heuristic since the "response" wasn't parseable JSON
    assert result["quality"] == "cold"


# --- analyze_lead: DB write-back ---

@patch("app.services.ai_analysis_service._get_client")
def test_analyze_lead_writes_result_to_database(mock_get_client, db_session, sample_org, sample_advisor):
    mock_response = MagicMock()
    mock_response.choices[0].message.content = json.dumps({
        "quality": "hot", "recommended_approach": "Call now", "reasoning": "test",
    })
    mock_get_client.return_value.chat.completions.create.return_value = mock_response

    lead = Lead(
        organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
        first_name="Test", last_name="Lead", phone="12145559999",
        tier=LeadTier.PRE_NEED, last_action_raw="Called: Scheduled Appt.",
    )
    db_session.add(lead)
    db_session.commit()

    assert lead.ai_lead_quality_note is None  # confirm starting state

    result = analyze_lead(db_session, lead)
    assert result["quality"] == "hot"

    db_session.refresh(lead)
    assert lead.ai_lead_quality_note is not None
    stored = json.loads(lead.ai_lead_quality_note)
    assert stored["quality"] == "hot"


@patch("app.services.ai_analysis_service._get_client")
def test_analyze_batch_processes_multiple_leads_independently(mock_get_client, db_session, sample_org, sample_advisor):
    mock_response = MagicMock()
    mock_response.choices[0].message.content = json.dumps({
        "quality": "warm", "recommended_approach": "Follow up", "reasoning": "test",
    })
    mock_get_client.return_value.chat.completions.create.return_value = mock_response

    lead1 = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id, first_name="A", last_name="One", phone="12145551111")
    lead2 = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id, first_name="B", last_name="Two", phone="12145552222")
    db_session.add_all([lead1, lead2])
    db_session.commit()

    results = analyze_batch(db_session, [lead1, lead2])
    assert len(results) == 2
    assert lead1.id in results
    assert lead2.id in results
    assert results[lead1.id]["quality"] == "warm"
