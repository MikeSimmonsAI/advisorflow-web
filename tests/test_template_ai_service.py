"""
Tests for the AI template writer endpoints (POST /templates/ai/generate,
POST /templates/ai/rewrite).

These mock the OpenAI client directly rather than hitting the real API -
same idea as test_draft_reply_router.py and test_ai_analysis_service.py,
just applied to template_ai_service instead.
"""

import json
from types import SimpleNamespace

import app.services.template_ai_service as template_ai_service


def _fake_openai_response(payload: dict) -> SimpleNamespace:
    """Builds a minimal object shaped like the OpenAI chat completion response."""
    message = SimpleNamespace(content=json.dumps(payload))
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


class _FakeChatCompletions:
    def __init__(self, payload, exc=None):
        self._payload = payload
        self._exc = exc

    def create(self, **kwargs):
        if self._exc:
            raise self._exc
        return _fake_openai_response(self._payload)


class _FakeChat:
    def __init__(self, payload, exc=None):
        self.completions = _FakeChatCompletions(payload, exc)


class _FakeOpenAIClient:
    def __init__(self, payload=None, exc=None):
        self.chat = _FakeChat(payload, exc)


def _install_fake_client(monkeypatch, payload=None, exc=None):
    template_ai_service._client = _FakeOpenAIClient(payload, exc)
    monkeypatch.setattr(template_ai_service, "_get_client", lambda: template_ai_service._client)


def test_generate_sms_template_returns_body_only(client, admin_auth_headers, monkeypatch):
    _install_fake_client(monkeypatch, payload={"body_template": "Hi {first_name}, this is {advisor_name}."})

    response = client.post("/templates/ai/generate", headers=admin_auth_headers, json={
        "message_track": "pre_need_lock_price",
        "channel": "sms",
    })

    assert response.status_code == 200
    body = response.json()
    assert body["body_template"] == "Hi {first_name}, this is {advisor_name}."
    assert "subject_template" not in body


def test_generate_email_template_requires_and_returns_subject(client, admin_auth_headers, monkeypatch):
    _install_fake_client(monkeypatch, payload={
        "subject_template": "Let's talk, {first_name}",
        "body_template": "<p>Hi {first_name}</p>",
    })

    response = client.post("/templates/ai/generate", headers=admin_auth_headers, json={
        "message_track": "at_need_support",
        "channel": "email",
    })

    assert response.status_code == 200
    body = response.json()
    assert body["subject_template"] == "Let's talk, {first_name}"
    assert body["body_template"] == "<p>Hi {first_name}</p>"


def test_generate_email_template_missing_subject_in_ai_response_returns_502(client, admin_auth_headers, monkeypatch):
    _install_fake_client(monkeypatch, payload={"body_template": "<p>Hi {first_name}</p>"})

    response = client.post("/templates/ai/generate", headers=admin_auth_headers, json={
        "message_track": "at_need_support",
        "channel": "email",
    })

    assert response.status_code == 502


def test_generate_template_invalid_track_returns_400(client, admin_auth_headers, monkeypatch):
    _install_fake_client(monkeypatch, payload={"body_template": "irrelevant"})

    response = client.post("/templates/ai/generate", headers=admin_auth_headers, json={
        "message_track": "not_a_real_track",
        "channel": "sms",
    })

    assert response.status_code == 400


def test_generate_template_openai_failure_returns_502_not_silent_fallback(client, admin_auth_headers, monkeypatch):
    """
    Unlike draft-reply, template generation has no safe fallback text to
    silently substitute - a failure must surface as an error so the admin
    knows to retry, not get a no-op 200 back.
    """
    _install_fake_client(monkeypatch, exc=RuntimeError("simulated OpenAI outage"))

    response = client.post("/templates/ai/generate", headers=admin_auth_headers, json={
        "message_track": "pre_need_lock_price",
        "channel": "sms",
    })

    assert response.status_code == 502
    assert "AI request failed" in response.json()["detail"]


def test_generate_template_malformed_json_returns_502(client, admin_auth_headers, monkeypatch):
    class _BrokenChatCompletions:
        def create(self, **kwargs):
            return _fake_openai_response_raw("not valid json {{{")

    class _BrokenChat:
        def __init__(self):
            self.completions = _BrokenChatCompletions()

    class _BrokenClient:
        def __init__(self):
            self.chat = _BrokenChat()

    template_ai_service._client = _BrokenClient()
    monkeypatch.setattr(template_ai_service, "_get_client", lambda: template_ai_service._client)

    response = client.post("/templates/ai/generate", headers=admin_auth_headers, json={
        "message_track": "pre_need_lock_price",
        "channel": "sms",
    })

    assert response.status_code == 502


def _fake_openai_response_raw(text: str) -> SimpleNamespace:
    message = SimpleNamespace(content=text)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


def test_rewrite_sms_template_applies_instruction(client, admin_auth_headers, monkeypatch):
    _install_fake_client(monkeypatch, payload={"body_template": "Hey {first_name}! Quick one for you - {booking_link}"})

    response = client.post("/templates/ai/rewrite", headers=admin_auth_headers, json={
        "message_track": "pre_need_lock_price",
        "channel": "sms",
        "current_body": "Hi {first_name}, this is {advisor_name} with Restland.",
        "instruction": "make it shorter and more casual",
    })

    assert response.status_code == 200
    assert response.json()["body_template"] == "Hey {first_name}! Quick one for you - {booking_link}"


def test_rewrite_template_requires_instruction(client, admin_auth_headers, monkeypatch):
    _install_fake_client(monkeypatch, payload={"body_template": "irrelevant"})

    response = client.post("/templates/ai/rewrite", headers=admin_auth_headers, json={
        "message_track": "pre_need_lock_price",
        "channel": "sms",
        "current_body": "Hi {first_name}",
        "instruction": "",
    })

    assert response.status_code == 502
    assert "instruction is required" in response.json()["detail"].lower()


def test_rewrite_email_template_requires_current_subject(client, admin_auth_headers, monkeypatch):
    _install_fake_client(monkeypatch, payload={
        "subject_template": "New subject",
        "body_template": "<p>New body</p>",
    })

    response = client.post("/templates/ai/rewrite", headers=admin_auth_headers, json={
        "message_track": "at_need_support",
        "channel": "email",
        "current_body": "<p>Old body</p>",
        "instruction": "add more urgency",
    })

    assert response.status_code == 400
    assert "current_subject" in response.json()["detail"]


def test_rewrite_email_template_with_subject_succeeds(client, admin_auth_headers, monkeypatch):
    _install_fake_client(monkeypatch, payload={
        "subject_template": "New subject, {first_name}",
        "body_template": "<p>New body with urgency</p>",
    })

    response = client.post("/templates/ai/rewrite", headers=admin_auth_headers, json={
        "message_track": "at_need_support",
        "channel": "email",
        "current_body": "<p>Old body</p>",
        "current_subject": "Old subject",
        "instruction": "add more urgency",
    })

    assert response.status_code == 200
    body = response.json()
    assert body["subject_template"] == "New subject, {first_name}"
    assert body["body_template"] == "<p>New body with urgency</p>"


def test_ai_generate_requires_admin_role(client, auth_headers, monkeypatch):
    """A plain advisor (not org_admin/super_admin) must be blocked from generating templates."""
    _install_fake_client(monkeypatch, payload={"body_template": "irrelevant"})

    response = client.post("/templates/ai/generate", headers=auth_headers, json={
        "message_track": "pre_need_lock_price",
        "channel": "sms",
    })

    assert response.status_code == 403


def test_ai_rewrite_requires_admin_role(client, auth_headers, monkeypatch):
    _install_fake_client(monkeypatch, payload={"body_template": "irrelevant"})

    response = client.post("/templates/ai/rewrite", headers=auth_headers, json={
        "message_track": "pre_need_lock_price",
        "channel": "sms",
        "current_body": "Hi {first_name}",
        "instruction": "make it warmer",
    })

    assert response.status_code == 403
