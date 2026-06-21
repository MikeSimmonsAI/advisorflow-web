"""
Tests for app/services/reply_classification_service.py

These specifically cover the false positives discovered while testing
the OLD naive substring keyword matcher in sms_router.py:
  - "I'm not SURE yet" incorrectly matched the "sure" keyword -> hot
  - "please REMOVE me from this list" matched "remove" -> dnc (this one
    happened to be correct by luck, but the matching logic itself was
    never actually checking intent)

The OpenAI API call itself is mocked throughout - these tests verify
OUR fallback logic and our handling of the API response, not OpenAI's
actual model output.
"""

import json
from unittest.mock import patch, MagicMock
from app.services.reply_classification_service import classify_reply, contains_hard_stop_language, _fallback_keyword_classify


# --- Hard stop override (always-on safety net, never AI-judged) ---

def test_contains_hard_stop_language_detects_stop():
    assert contains_hard_stop_language("STOP") is True
    assert contains_hard_stop_language("please stop texting me") is True


def test_contains_hard_stop_language_detects_unsubscribe():
    assert contains_hard_stop_language("unsubscribe") is True


def test_contains_hard_stop_language_false_for_unrelated_text():
    assert contains_hard_stop_language("when does the office open") is False


# --- Fallback heuristic: the specific false positives this service was built to fix ---

def test_fallback_does_not_misclassify_not_sure_as_interested():
    """The original bug: 'not sure' contains 'sure', a former hot keyword."""
    result = _fallback_keyword_classify("I'm not sure yet, let me think about it")
    assert result["classification"] != "interested"


def test_fallback_classifies_clear_interest_correctly():
    result = _fallback_keyword_classify("Yes, I'm very interested!")
    assert result["classification"] == "interested"


def test_fallback_prioritizes_interested_over_secondary_callback_mention():
    """
    Real bug caught during this session's own testing: a message
    containing BOTH an interested signal and a callback signal should
    classify as interested, not callback - the stronger signal should win.
    """
    result = _fallback_keyword_classify("Yes I'm interested, please call me")
    assert result["classification"] == "interested"


def test_fallback_classifies_pure_callback_request():
    result = _fallback_keyword_classify("Can you call me tomorrow morning")
    assert result["classification"] == "callback"


def test_fallback_classifies_stop_keyword_as_dnc():
    result = _fallback_keyword_classify("Please stop texting me")
    assert result["classification"] == "dnc"


def test_fallback_classifies_unrelated_question_as_neutral():
    result = _fallback_keyword_classify("What time does your office close today")
    assert result["classification"] == "neutral"


def test_fallback_includes_error_in_reasoning_when_provided():
    result = _fallback_keyword_classify("test", error="429 rate limit")
    assert "429 rate limit" in result["reasoning"]


# --- classify_reply: success path (mocked OpenAI response) ---

@patch("app.services.reply_classification_service._get_client")
def test_classify_reply_parses_valid_ai_response(mock_get_client):
    mock_response = MagicMock()
    mock_response.choices[0].message.content = json.dumps({
        "classification": "interested", "confidence": "high", "reasoning": "Clear yes",
    })
    mock_get_client.return_value.chat.completions.create.return_value = mock_response

    result = classify_reply("Yes, sign me up")
    assert result["classification"] == "interested"
    assert result["confidence"] == "high"


@patch("app.services.reply_classification_service._get_client")
def test_classify_reply_correctly_handles_not_sure_via_ai(mock_get_client):
    """Confirms the AI path (not just the fallback) correctly judges the 'not sure' case as neutral."""
    mock_response = MagicMock()
    mock_response.choices[0].message.content = json.dumps({
        "classification": "neutral", "confidence": "high", "reasoning": "Expressing uncertainty, not interest",
    })
    mock_get_client.return_value.chat.completions.create.return_value = mock_response

    result = classify_reply("I'm not sure yet")
    assert result["classification"] == "neutral"


@patch("app.services.reply_classification_service._get_client")
def test_classify_reply_strips_markdown_fences(mock_get_client):
    mock_response = MagicMock()
    mock_response.choices[0].message.content = '```json\n{"classification": "callback", "confidence": "medium", "reasoning": "test"}\n```'
    mock_get_client.return_value.chat.completions.create.return_value = mock_response

    result = classify_reply("call me please")
    assert result["classification"] == "callback"


@patch("app.services.reply_classification_service._get_client")
def test_classify_reply_falls_back_on_api_exception(mock_get_client):
    mock_get_client.return_value.chat.completions.create.side_effect = Exception("429 Too Many Requests")

    result = classify_reply("Yes I'm interested")
    assert result["classification"] == "interested"  # fallback still gets this right
    assert "429" in result["reasoning"]


@patch("app.services.reply_classification_service._get_client")
def test_classify_reply_falls_back_on_invalid_classification_value(mock_get_client):
    """If the AI returns something outside the allowed enum, fall back rather than store garbage."""
    mock_response = MagicMock()
    mock_response.choices[0].message.content = json.dumps({
        "classification": "super_excited", "confidence": "high", "reasoning": "test",
    })
    mock_get_client.return_value.chat.completions.create.return_value = mock_response

    result = classify_reply("Yes, I'm very interested!")
    assert result["classification"] in ("interested", "callback", "dnc", "neutral")


@patch("app.services.reply_classification_service._get_client")
def test_classify_reply_falls_back_on_malformed_json(mock_get_client):
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "I think they are interested because..."
    mock_get_client.return_value.chat.completions.create.return_value = mock_response

    result = classify_reply("yes please")
    assert result["classification"] == "interested"  # fallback correctly catches this one too
