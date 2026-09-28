"""
SS10 - bulk AI compose on the Leads page (POST /ai-conversation/generate-batch).

Every provider is mocked. Switches are turned on by monkeypatch for one test at
a time and nothing may reach a real email or SMS provider.

What is pinned here:
  * a fallback / failed generation is never sent and never offered as a draft
  * `channel` is honoured: auto-send on anything but email is refused (422)
    rather than silently emailed
  * an outbound switch that is off is reported as `disabled`, not as an error
  * drafts are returned to the client and reported as not persisted
"""

import json
from unittest.mock import patch

import pytest

from app.models.models import EmailMessage, Lead, Message
from app.services import outbound_email_gate as gate
from app.services import send_source

URL = "/ai-conversation/generate-batch"
GEN = "app.routers.ai_conversation_router.generate_auto_reply"
EMAIL_PROVIDER = "app.services.email_service.send_email_via_provider"
SMS_SEND = "app.services.sms_service.send_sms"


def _lead(db_session, org, advisor, *, email="fam@example.com", phone="+15555550100",
          status="new", first="Bulk"):
    lead = Lead(organization_id=org.id, assigned_to_id=advisor.id,
                first_name=first, last_name="Compose", phone=phone, phone_raw=phone,
                email=email, status=status)
    db_session.add(lead)
    db_session.commit()
    return lead


def _gen(reply="Drafted body", source="ai", error_kind=None):
    def fake(db, lead, advisor, tone="warm", ai_direction=None,
             relationship_type=None, actor=None):
        return {"reply": reply, "subject": "Drafted subject", "should_stop": False,
                "reason": "", "source": source, "error_kind": error_kind,
                "booking_url": ""}
    return fake


def _all_off(monkeypatch):
    for s in gate.GATED_SOURCES:
        monkeypatch.delenv(gate._ENV_BY_SOURCE[s], raising=False)


def _bulk_ai_on(monkeypatch, db_session, org):
    _all_off(monkeypatch)
    monkeypatch.setenv(gate._ENV_BY_SOURCE[send_source.BULK_AI], "true")
    org.outbound_email_sources = json.dumps([send_source.BULK_AI])
    db_session.commit()


def _ok():
    return {"success": True, "provider_message_id": "em_test", "error": None}


# ── fallback is never sent ─────────────────────────────────────────────────

def test_fallback_is_never_sent_even_with_every_switch_on(
        client, auth_headers, db_session, sample_org, sample_advisor, monkeypatch):
    _bulk_ai_on(monkeypatch, db_session, sample_org)
    lead = _lead(db_session, sample_org, sample_advisor)

    with patch(GEN, _gen(reply="Canned fallback sentence", source="fallback",
                         error_kind="APITimeoutError")), \
         patch(EMAIL_PROVIDER, return_value=_ok()) as provider:
        r = client.post(URL, headers=auth_headers,
                        json={"lead_ids": [lead.id], "auto_send": True, "channel": "email"})

    assert r.status_code == 200
    body = r.json()
    provider.assert_not_called()
    assert body["sent"] == 0 and body["errors"] == 0
    assert body["skipped_fallback"] == 1
    res = body["results"][0]
    assert res["action"] == "skipped_fallback"
    assert res["error_kind"] == "APITimeoutError"
    assert res["reply"] == ""
    assert db_session.query(EmailMessage).count() == 0


def test_fallback_is_not_offered_as_a_draft(
        client, auth_headers, db_session, sample_org, sample_advisor, monkeypatch):
    _all_off(monkeypatch)
    lead = _lead(db_session, sample_org, sample_advisor)

    with patch(GEN, _gen(reply="Canned fallback sentence", source="fallback",
                         error_kind="RateLimitError")):
        r = client.post(URL, headers=auth_headers,
                        json={"lead_ids": [lead.id], "auto_send": False, "channel": "sms"})

    body = r.json()
    assert body["drafted"] == 0 and body["skipped_fallback"] == 1
    assert "Canned fallback sentence" not in json.dumps(body)


def test_empty_generation_is_treated_as_a_failure(
        client, auth_headers, db_session, sample_org, sample_advisor, monkeypatch):
    _bulk_ai_on(monkeypatch, db_session, sample_org)
    lead = _lead(db_session, sample_org, sample_advisor)

    with patch(GEN, _gen(reply="   ")), patch(EMAIL_PROVIDER) as provider:
        body = client.post(URL, headers=auth_headers,
                           json={"lead_ids": [lead.id], "auto_send": True,
                                 "channel": "email"}).json()

    provider.assert_not_called()
    assert body["skipped_fallback"] == 1
    assert body["results"][0]["error_kind"] == "empty_generation"


# ── channel is honoured ────────────────────────────────────────────────────

@pytest.mark.parametrize("channel", ["sms", "both", "auto"])
def test_auto_send_on_a_non_email_channel_is_refused_not_emailed(
        channel, client, auth_headers, db_session, sample_org, sample_advisor, monkeypatch):
    _bulk_ai_on(monkeypatch, db_session, sample_org)
    lead = _lead(db_session, sample_org, sample_advisor)   # has email AND phone

    with patch(GEN, _gen()), \
         patch(EMAIL_PROVIDER, return_value=_ok()) as provider, \
         patch(SMS_SEND) as sms:
        r = client.post(URL, headers=auth_headers,
                        json={"lead_ids": [lead.id], "auto_send": True, "channel": channel})

    assert r.status_code == 422
    assert "email only" in r.json()["detail"]
    assert "Nothing was sent" in r.json()["detail"]
    provider.assert_not_called()
    sms.assert_not_called()
    assert db_session.query(EmailMessage).count() == 0
    assert db_session.query(Message).count() == 0


def test_email_channel_sends_through_the_gate_with_the_provider_mocked(
        client, auth_headers, db_session, sample_org, sample_advisor, monkeypatch):
    _bulk_ai_on(monkeypatch, db_session, sample_org)
    lead = _lead(db_session, sample_org, sample_advisor)

    with patch(GEN, _gen()), patch(EMAIL_PROVIDER, return_value=_ok()) as provider, \
         patch(SMS_SEND) as sms:
        body = client.post(URL, headers=auth_headers,
                           json={"lead_ids": [lead.id], "auto_send": True,
                                 "channel": "email"}).json()

    provider.assert_called_once()
    sms.assert_not_called()
    assert body["sent"] == 1 and body["errors"] == 0 and body["disabled"] == 0
    row = db_session.query(EmailMessage).filter(EmailMessage.lead_id == lead.id).one()
    assert row.send_source == send_source.BULK_AI


# ── gate-off is `disabled`, not an error ───────────────────────────────────

def test_deployment_switch_off_is_reported_as_disabled(
        client, auth_headers, db_session, sample_org, sample_advisor, monkeypatch):
    _all_off(monkeypatch)
    lead = _lead(db_session, sample_org, sample_advisor)

    with patch(GEN, _gen()), patch(EMAIL_PROVIDER) as provider:
        body = client.post(URL, headers=auth_headers,
                           json={"lead_ids": [lead.id], "auto_send": True,
                                 "channel": "email"}).json()

    provider.assert_not_called()
    assert body["sent"] == 0
    assert body["errors"] == 0
    assert body["disabled"] == 1
    res = body["results"][0]
    assert res["action"] == "disabled"
    assert "OUTBOUND_EMAIL_BULK_AI" in res["reason"]


def test_org_switch_off_is_reported_as_disabled(
        client, auth_headers, db_session, sample_org, sample_advisor, monkeypatch):
    _all_off(monkeypatch)
    monkeypatch.setenv(gate._ENV_BY_SOURCE[send_source.BULK_AI], "true")
    sample_org.outbound_email_sources = None
    db_session.commit()
    lead = _lead(db_session, sample_org, sample_advisor)

    with patch(GEN, _gen()), patch(EMAIL_PROVIDER) as provider:
        body = client.post(URL, headers=auth_headers,
                           json={"lead_ids": [lead.id], "auto_send": True,
                                 "channel": "email"}).json()

    provider.assert_not_called()
    assert body["errors"] == 0 and body["disabled"] == 1
    assert "organization" in body["results"][0]["reason"]


def test_a_real_provider_fault_is_still_an_error(
        client, auth_headers, db_session, sample_org, sample_advisor, monkeypatch):
    _bulk_ai_on(monkeypatch, db_session, sample_org)
    lead = _lead(db_session, sample_org, sample_advisor)

    with patch(GEN, _gen()), patch(EMAIL_PROVIDER, side_effect=RuntimeError("boom")):
        body = client.post(URL, headers=auth_headers,
                           json={"lead_ids": [lead.id], "auto_send": True,
                                 "channel": "email"}).json()

    assert body["sent"] == 0 and body["disabled"] == 0 and body["errors"] == 1


def test_dnc_lead_is_blocked_not_disabled(
        client, auth_headers, db_session, sample_org, sample_advisor, monkeypatch):
    _bulk_ai_on(monkeypatch, db_session, sample_org)
    lead = _lead(db_session, sample_org, sample_advisor, status="dnc")

    with patch(GEN, _gen()), patch(EMAIL_PROVIDER) as provider:
        body = client.post(URL, headers=auth_headers,
                           json={"lead_ids": [lead.id], "auto_send": True,
                                 "channel": "email"}).json()

    provider.assert_not_called()
    assert body["skipped"] == 1 and body["disabled"] == 0 and body["errors"] == 0
    assert body["results"][0]["action"] == "blocked"


# ── drafts, not a fictional queue ──────────────────────────────────────────

def test_review_mode_returns_drafts_and_persists_nothing(
        client, auth_headers, db_session, sample_org, sample_advisor, monkeypatch):
    _bulk_ai_on(monkeypatch, db_session, sample_org)
    a = _lead(db_session, sample_org, sample_advisor, first="Ann")
    b = _lead(db_session, sample_org, sample_advisor, first="Bob")

    with patch(GEN, _gen()), patch(EMAIL_PROVIDER) as provider, patch(SMS_SEND) as sms:
        r = client.post(URL, headers=auth_headers,
                        json={"lead_ids": [a.id, b.id], "auto_send": False, "channel": "sms"})

    assert r.status_code == 200
    body = r.json()
    provider.assert_not_called()
    sms.assert_not_called()
    assert body["drafted"] == 2 and body["sent"] == 0
    assert body["persisted"] is False
    assert "queued" not in body, "nothing is queued; the response must not say so"
    assert {x["action"] for x in body["results"]} == {"draft"}
    assert all(x["reply"] == "Drafted body" for x in body["results"])
    assert db_session.query(EmailMessage).count() == 0
    assert db_session.query(Message).count() == 0


def test_no_switch_left_enabled():
    assert not any(gate.enablement_report().values())
