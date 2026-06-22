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


# --- New categories added per Mike's explicit request for a fuller
# reclassification set: not_interested, wrong_number, question ---

def test_fallback_classifies_plain_decline_as_not_interested_not_dnc():
    """
    Regression test for the conflation Mike specifically flagged: a plain
    "not interested" used to fall into the same stop_keywords bucket as a
    real legal opt-out (stop/unsubscribe). It must now be its own
    not_interested category, distinct from dnc.
    """
    result = _fallback_keyword_classify("Not interested, thanks")
    assert result["classification"] == "not_interested"


def test_fallback_classifies_no_thanks_as_not_interested():
    result = _fallback_keyword_classify("No thanks, we already have a plan")
    assert result["classification"] == "not_interested"


def test_fallback_still_classifies_actual_stop_as_dnc():
    """The real legal opt-out language must still hit dnc, unaffected by the not_interested split."""
    result = _fallback_keyword_classify("STOP")
    assert result["classification"] == "dnc"


def test_fallback_classifies_remove_me_as_dnc():
    result = _fallback_keyword_classify("Please remove me from your list")
    assert result["classification"] == "dnc"


def test_contains_hard_stop_language_does_not_trigger_on_plain_not_interested():
    """
    The hard-override DNC check must NOT fire just because someone says
    "not interested" - that's a decline, not a legal opt-out request.
    """
    assert contains_hard_stop_language("Not interested, please don't contact me again") is False


def test_fallback_classifies_wrong_number():
    result = _fallback_keyword_classify("Wrong number, you have the wrong person")
    assert result["classification"] == "wrong_number"


def test_fallback_classifies_who_is_this_as_wrong_number():
    result = _fallback_keyword_classify("Who is this? I don't know you")
    assert result["classification"] == "wrong_number"


def test_fallback_classifies_genuine_question_as_question():
    result = _fallback_keyword_classify("What's the price difference between the two options?")
    assert result["classification"] == "question"


@patch("app.services.reply_classification_service._get_client")
def test_classify_reply_accepts_not_interested_from_ai(mock_get_client):
    mock_response = MagicMock()
    mock_response.choices[0].message.content = json.dumps({
        "classification": "not_interested", "confidence": "high", "reasoning": "Polite decline, no opt-out language.",
    })
    mock_get_client.return_value.chat.completions.create.return_value = mock_response

    result = classify_reply("No thanks, we're all set.")
    assert result["classification"] == "not_interested"


@patch("app.services.reply_classification_service._get_client")
def test_classify_reply_accepts_wrong_number_from_ai(mock_get_client):
    mock_response = MagicMock()
    mock_response.choices[0].message.content = json.dumps({
        "classification": "wrong_number", "confidence": "high", "reasoning": "Recipient says this isn't them.",
    })
    mock_get_client.return_value.chat.completions.create.return_value = mock_response

    result = classify_reply("Sorry, wrong number")
    assert result["classification"] == "wrong_number"


@patch("app.services.reply_classification_service._get_client")
def test_classify_reply_accepts_question_from_ai(mock_get_client):
    mock_response = MagicMock()
    mock_response.choices[0].message.content = json.dumps({
        "classification": "question", "confidence": "medium", "reasoning": "Lead is asking about pricing.",
    })
    mock_get_client.return_value.chat.completions.create.return_value = mock_response

    result = classify_reply("How much does this cost?")
    assert result["classification"] == "question"
