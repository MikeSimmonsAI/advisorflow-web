"""
Tests for app/services/auto_send_eligibility_service.py - the actual
brain of the auto-send queue. This is the highest-stakes logic in the
entire app: it decides whether AI is allowed to send a real message to
a real person with zero human review. Every test here exists to prove
the system defaults to NOT eligible whenever there's any real doubt,
never the other way around.
"""

from unittest.mock import patch, MagicMock

from app.services.auto_send_eligibility_service import check_auto_send_eligibility


def _fake_response(payload):
    import json
    from types import SimpleNamespace
    message = SimpleNamespace(content=json.dumps(payload))
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


# ---------------------------------------------------------------------------
# Rule 1 - hard classification gate, checked BEFORE any AI call at all.
# ---------------------------------------------------------------------------

def test_non_question_classifications_are_hard_excluded_with_no_api_call():
    """interested/hot, callback, dnc, not_interested, wrong_number, neutral must ALL be excluded with zero API calls made."""
    for classification in ("interested", "callback", "dnc", "not_interested", "wrong_number", "neutral"):
        with patch("app.services.auto_send_eligibility_service._get_client") as mock_get_client:
            result = check_auto_send_eligibility("anything", classification, is_first_reply=False)

            assert result["eligible"] is False
            mock_get_client.assert_not_called()


def test_question_classification_does_proceed_to_the_real_check():
    with patch("app.services.auto_send_eligibility_service._get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _fake_response(
            {"eligible": True, "confidence": "high", "reasoning": "Simple scheduling question."}
        )
        mock_get_client.return_value = mock_client

        result = check_auto_send_eligibility("What time works for you?", "question", is_first_reply=False)

        mock_get_client.assert_called_once()
        assert result["eligible"] is True


# ---------------------------------------------------------------------------
# Rule 3 - first-ever reply is hard-excluded, no established context.
# ---------------------------------------------------------------------------

def test_first_reply_is_hard_excluded_with_no_api_call():
    with patch("app.services.auto_send_eligibility_service._get_client") as mock_get_client:
        result = check_auto_send_eligibility("What time works?", "question", is_first_reply=True)

        assert result["eligible"] is False
        assert "first-ever reply" in result["reasoning"]
        mock_get_client.assert_not_called()


# ---------------------------------------------------------------------------
# Rule 4 - confidence must be HIGH, not medium or low, even if eligible=True.
# ---------------------------------------------------------------------------

def test_medium_confidence_is_not_eligible_even_if_ai_says_eligible_true():
    with patch("app.services.auto_send_eligibility_service._get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _fake_response(
            {"eligible": True, "confidence": "medium", "reasoning": "Probably a scheduling question."}
        )
        mock_get_client.return_value = mock_client

        result = check_auto_send_eligibility("Maybe later this week?", "question", is_first_reply=False)

        assert result["eligible"] is False


def test_low_confidence_is_not_eligible():
    with patch("app.services.auto_send_eligibility_service._get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _fake_response(
            {"eligible": True, "confidence": "low", "reasoning": "Unclear."}
        )
        mock_get_client.return_value = mock_client

        result = check_auto_send_eligibility("Is this still a thing?", "question", is_first_reply=False)

        assert result["eligible"] is False


def test_high_confidence_with_eligible_false_stays_not_eligible():
    """High confidence that something is NOT eligible must still mean not eligible - confidence alone never overrides the actual eligible flag."""
    with patch("app.services.auto_send_eligibility_service._get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _fake_response(
            {"eligible": False, "confidence": "high", "reasoning": "Contains emotional content about a family member."}
        )
        mock_get_client.return_value = mock_client

        result = check_auto_send_eligibility("Why hasn't anyone called my mother back, what's going on?", "question", is_first_reply=False)

        assert result["eligible"] is False


# ---------------------------------------------------------------------------
# Failure handling - any error must default to NOT eligible, never proceed.
# ---------------------------------------------------------------------------

def test_api_failure_defaults_to_not_eligible():
    with patch("app.services.auto_send_eligibility_service._get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("API timeout")
        mock_get_client.return_value = mock_client

        result = check_auto_send_eligibility("What time works?", "question", is_first_reply=False)

        assert result["eligible"] is False
        assert "failed" in result["reasoning"].lower()


def test_malformed_json_response_defaults_to_not_eligible():
    with patch("app.services.auto_send_eligibility_service._get_client") as mock_get_client:
        from types import SimpleNamespace
        mock_client = MagicMock()
        message = SimpleNamespace(content="not valid json at all")
        choice = SimpleNamespace(message=message)
        mock_client.chat.completions.create.return_value = SimpleNamespace(choices=[choice])
        mock_get_client.return_value = mock_client

        result = check_auto_send_eligibility("What time works?", "question", is_first_reply=False)

        assert result["eligible"] is False


def test_missing_eligible_field_in_response_defaults_to_not_eligible():
    with patch("app.services.auto_send_eligibility_service._get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _fake_response({"confidence": "high"})
        mock_get_client.return_value = mock_client

        result = check_auto_send_eligibility("What time works?", "question", is_first_reply=False)

        assert result["eligible"] is False


# ---------------------------------------------------------------------------
# Real prompt content check - confirms the actual instruction sent to
# the model matches the agreed rule, not just that the function
# technically calls the API.
# ---------------------------------------------------------------------------

def test_prompt_sent_to_model_includes_the_real_eligibility_rule():
    with patch("app.services.auto_send_eligibility_service._get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _fake_response(
            {"eligible": True, "confidence": "high", "reasoning": "Simple."}
        )
        mock_get_client.return_value = mock_client

        check_auto_send_eligibility("What time works?", "question", is_first_reply=False)

        sent_prompt = mock_client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        assert "ZERO human review" in sent_prompt
        assert "default to NOT eligible" in sent_prompt
        assert "What time works?" in sent_prompt
