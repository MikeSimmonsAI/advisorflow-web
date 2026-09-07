"""
Tests for app/services/microsoft_email_service.py

External HTTP calls to Microsoft's OAuth/Graph endpoints are mocked
throughout - these test OUR logic (URL construction, token storage,
error handling), not Microsoft's actual API behavior.
"""

from unittest.mock import patch, MagicMock
import pytest
from app.services.microsoft_email_service import (
    get_microsoft_authorization_url, handle_microsoft_oauth_callback,
    send_email_via_microsoft_graph, _get_fresh_access_token,
)
from app.models.models import User
from app.utils.crypto import decrypt_value


def test_get_authorization_url_is_correctly_encoded():
    """
    Real bug caught and fixed while building this: an earlier version
    used a manual encoding trick that left literal spaces and unescaped
    special characters in the URL (e.g. scope with raw spaces, an
    unescaped redirect_uri). Confirms the fix - the URL must parse back
    to the exact original parameter values via standard URL parsing.
    """
    from urllib.parse import urlparse, parse_qs
    from app.services import microsoft_email_service

    with patch("app.services.microsoft_email_service.MICROSOFT_CLIENT_ID", "test-client"):
        url = get_microsoft_authorization_url("advisor-123")

    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    assert qs["state"] == ["advisor-123"]
    # Asserted against the module constant, not a literal: the scope list is
    # allowed to grow (Calendars.ReadWrite was added for the scheduling sync)
    # and this test is about ENCODING, not about which scopes are requested.
    assert qs["scope"] == [microsoft_email_service.SCOPES]
    assert "offline_access" in qs["scope"][0]  # required or no refresh token gets issued

    # The actual encoding assertions. SCOPES is a space-separated list and the
    # redirect URI contains characters that MUST be escaped; the earlier manual
    # encoding left both raw in the query string, which is what broke the flow.
    assert " " in qs["scope"][0]        # spaces survive the round trip...
    assert " " not in parsed.query      # ...because they were escaped on the way out
    assert qs["redirect_uri"] == [microsoft_email_service.MICROSOFT_REDIRECT_URI]
    for raw_char in (" ", "<", ">"):
        assert raw_char not in parsed.query


def test_get_authorization_url_raises_without_client_id():
    with patch("app.services.microsoft_email_service.MICROSOFT_CLIENT_ID", None):
        with pytest.raises(RuntimeError, match="MICROSOFT_CLIENT_ID"):
            get_microsoft_authorization_url("advisor-123")


@patch("app.services.microsoft_email_service.httpx.get")
@patch("app.services.microsoft_email_service.httpx.post")
def test_oauth_callback_stores_encrypted_refresh_token(mock_post, mock_get, db_session, sample_advisor):
    mock_post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"access_token": "fake_access", "refresh_token": "fake_refresh_token_xyz"},
    )
    mock_post.return_value.raise_for_status = lambda: None
    mock_get.return_value = MagicMock(json=lambda: {"mail": "mike@restland.com"})
    mock_get.return_value.raise_for_status = lambda: None

    with patch("app.services.microsoft_email_service.MICROSOFT_CLIENT_ID", "test-client"), \
         patch("app.services.microsoft_email_service.MICROSOFT_CLIENT_SECRET", "test-secret"):
        handle_microsoft_oauth_callback(db_session, sample_advisor.id, "fake_auth_code")

    db_session.refresh(sample_advisor)
    assert sample_advisor.microsoft_365_connected is True
    assert sample_advisor.microsoft_email_address == "mike@restland.com"
    assert sample_advisor.microsoft_oauth_refresh_token_encrypted is not None
    # confirms it's genuinely encrypted at rest, not stored as plaintext
    assert decrypt_value(sample_advisor.microsoft_oauth_refresh_token_encrypted) == "fake_refresh_token_xyz"


@patch("app.services.microsoft_email_service.httpx.post")
def test_oauth_callback_raises_if_no_refresh_token_returned(mock_post, db_session, sample_advisor):
    """
    If Microsoft doesn't return a refresh_token (e.g. offline_access
    wasn't actually granted), this must fail loudly rather than silently
    storing nothing - a missing refresh token means every future send
    would fail anyway.
    """
    mock_post.return_value = MagicMock(status_code=200, json=lambda: {"access_token": "fake_access"})
    mock_post.return_value.raise_for_status = lambda: None

    with patch("app.services.microsoft_email_service.MICROSOFT_CLIENT_ID", "test-client"), \
         patch("app.services.microsoft_email_service.MICROSOFT_CLIENT_SECRET", "test-secret"):
        with pytest.raises(RuntimeError, match="refresh token"):
            handle_microsoft_oauth_callback(db_session, sample_advisor.id, "fake_auth_code")


def test_get_fresh_access_token_raises_for_unconnected_advisor(sample_advisor):
    assert sample_advisor.microsoft_oauth_refresh_token_encrypted is None
    with pytest.raises(ValueError, match="has not connected Microsoft 365"):
        _get_fresh_access_token(sample_advisor)


@patch("app.services.microsoft_email_service._get_fresh_access_token")
@patch("app.services.microsoft_email_service.httpx.post")
def test_send_email_via_graph_success(mock_post, mock_token, sample_advisor):
    mock_token.return_value = "fake_access_token"
    mock_post.return_value = MagicMock(status_code=202)

    result = send_email_via_microsoft_graph(sample_advisor, "lead@example.com", "Subject", "<p>Body</p>")
    assert result["success"] is True
    assert result["error"] is None


@patch("app.services.microsoft_email_service._get_fresh_access_token")
@patch("app.services.microsoft_email_service.httpx.post")
def test_send_email_via_graph_handles_non_202_response(mock_post, mock_token, sample_advisor):
    mock_token.return_value = "fake_access_token"
    mock_post.return_value = MagicMock(status_code=403, text="Forbidden")

    result = send_email_via_microsoft_graph(sample_advisor, "lead@example.com", "Subject", "<p>Body</p>")
    assert result["success"] is False
    assert "403" in result["error"]


@patch("app.services.microsoft_email_service._get_fresh_access_token")
def test_send_email_via_graph_handles_token_refresh_failure_gracefully(mock_token, sample_advisor):
    mock_token.side_effect = ValueError("Advisor has not connected Microsoft 365.")

    result = send_email_via_microsoft_graph(sample_advisor, "lead@example.com", "Subject", "<p>Body</p>")
    assert result["success"] is False
    assert "not connected" in result["error"]
