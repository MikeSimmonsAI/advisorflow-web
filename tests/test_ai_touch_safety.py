"""AI TOUCH SAFETY — a failed generation never becomes a customer email.

THE PRODUCTION INCIDENT THIS CLOSES. On 2026-09-17 at 10:00 the
advisorflow-ai-conversation cron found 25 due conversations and logged 25
identical pairs:

    generate_touch_email error: The api_key client option must be set ...
    _send_touch error: Resend send failed: No sending address is configured ...

then exited 1. What the second line hides is what the first line had already
decided: `generate_touch_email` caught the OpenAI failure and returned a
hand-written English sentence, and `_send_touch` went on to send it.

Twenty-five families were one working Resend key away from receiving

    "Hi {first_name}, I wanted to follow up regarding your {appt_label}.
     I'd love to connect at your convenience."

from their funeral home, over their advisor's name. The only thing that
stopped it was the sender refusing for want of a verified from-address. That
is luck, not design.

NOTHING IN THIS FILE SENDS. Every provider is a mock and every assertion is
that it was not called.
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.models.models import (EmailMessage, Lead, Organization,
                               PipelineConversation, User)
from app.services import ai_conversation_service as acs


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    """Preflight satisfied by default, so each test exercises the behaviour it
    is about rather than the preflight."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("RESEND_API_KEY", "test-key-not-real")


def _lead(db, org, advisor, **kw):
    lead = Lead(organization_id=org.id, assigned_to_id=advisor.id,
                first_name=kw.pop("first_name", "Pat"), last_name="Family",
                email=kw.pop("email", "family@example.com"),
                phone="12145557000", status="new", **kw)
    db.add(lead); db.commit(); return lead


def _conv(db, org, lead, advisor, **kw):
    conv = PipelineConversation(
        organization_id=org.id, lead_id=lead.id, advisor_id=advisor.id,
        stage=kw.pop("stage", "outreach_sent"), paused=False, flagged=False,
        touch_number=kw.pop("touch_number", 0),
        started_at=datetime.utcnow() - timedelta(days=1),
        next_send_at=kw.pop("next_send_at", datetime.utcnow() - timedelta(minutes=5)),
        **kw)
    db.add(conv); db.commit(); return conv


# ═══════════════════════════════════════════════════════════════════════════
# A FAILED GENERATION SENDS NOTHING
# ═══════════════════════════════════════════════════════════════════════════

def test_a_generation_failure_sends_no_email(db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor)
    conv = _conv(db_session, sample_org, lead, sample_advisor)

    with patch.object(acs, "generate_touch_email", return_value={
            "subject": "Following up, Pat",
            "body": "Hi Pat, I wanted to follow up regarding your appointment.",
            "should_stop": False, "escalate": False,
            "source": "fallback", "error_kind": "AuthenticationError",
            "generation_failed": True, "touch_number": 0}):
        with patch.object(acs, "_send_email_resend") as sender:
            result = acs._send_touch(db_session, lead, sample_advisor, conv, 0)

    sender.assert_not_called()
    assert result["success"] is False
    assert result.get("generation_failed") is True
    assert "refusing to send a generic message" in result["error"]


def test_the_refusal_names_the_underlying_failure(db_session, sample_org,
                                                  sample_advisor):
    """An operator needs to know it was a dead API key, not a rate limit."""
    lead = _lead(db_session, sample_org, sample_advisor)
    conv = _conv(db_session, sample_org, lead, sample_advisor)
    with patch.object(acs, "generate_touch_email", return_value={
            "subject": "x", "body": "y", "should_stop": False, "escalate": False,
            "source": "fallback", "error_kind": "RateLimitError",
            "generation_failed": True}):
        with patch.object(acs, "_send_email_resend"):
            result = acs._send_touch(db_session, lead, sample_advisor, conv, 0)
    assert "RateLimitError" in result["error"]


def test_a_refused_touch_writes_no_email_row_and_advances_no_counter(
        db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor)
    conv = _conv(db_session, sample_org, lead, sample_advisor)
    before = (conv.touch_number, conv.messages_sent, conv.ai_responses_sent,
              conv.next_send_at, lead.status)

    with patch.object(acs, "generate_touch_email", return_value={
            "subject": "x", "body": "y", "should_stop": False, "escalate": False,
            "source": "fallback", "generation_failed": True}):
        with patch.object(acs, "_send_email_resend") as sender:
            acs.process_scheduled_touches(db_session)

    sender.assert_not_called()
    db_session.expire_all()
    conv = db_session.query(PipelineConversation).filter(
        PipelineConversation.id == conv.id).one()
    lead = db_session.query(Lead).filter(Lead.id == lead.id).one()
    assert (conv.touch_number, conv.messages_sent, conv.ai_responses_sent,
            conv.next_send_at, lead.status) == before
    assert db_session.query(EmailMessage).filter(
        EmailMessage.lead_id == lead.id).count() == 0


def test_the_run_reports_it_as_an_error_not_a_success(
        db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor)
    _conv(db_session, sample_org, lead, sample_advisor)
    with patch.object(acs, "generate_touch_email", return_value={
            "subject": "x", "body": "y", "should_stop": False, "escalate": False,
            "source": "fallback", "generation_failed": True}):
        with patch.object(acs, "_send_email_resend"):
            result = acs.process_scheduled_touches(db_session)
    assert result["sent"] == 0
    assert result["errors"] == 1


def test_there_is_no_approved_fallback_template_in_this_build():
    """Setting one is a product decision - a template a human wrote, reviewed
    and signed off - not a default and not an environment switch."""
    assert acs.APPROVED_FALLBACK_TEMPLATE is None


def test_a_successful_generation_still_sends(db_session, sample_org,
                                             sample_advisor):
    """The refusal must not have broken the working path."""
    lead = _lead(db_session, sample_org, sample_advisor)
    conv = _conv(db_session, sample_org, lead, sample_advisor)
    with patch.object(acs, "generate_touch_email", return_value={
            "subject": "A real subject", "body": "A real body.",
            "should_stop": False, "escalate": False, "source": "ai"}):
        with patch.object(acs, "_send_email_resend") as sender:
            result = acs._send_touch(db_session, lead, sample_advisor, conv, 0)
    sender.assert_called_once()
    assert result["success"] is True


def test_the_compose_preview_still_shows_a_human_the_fallback(
        db_session, sample_org, sample_advisor):
    """generate_touch_email still RETURNS a body on failure, because the
    compose preview shows it to a person who then decides. A human reading a
    draft is not the same act as a machine mailing a family."""
    lead = _lead(db_session, sample_org, sample_advisor)
    with patch.object(acs, "_get_client", side_effect=RuntimeError("no api key")):
        out = acs.generate_touch_email(db_session, lead, sample_advisor, 0)
    assert out["source"] == "fallback"
    assert out["generation_failed"] is True
    assert out["body"], "the preview needs something to show"
    assert out["error_kind"] == "RuntimeError"


# ═══════════════════════════════════════════════════════════════════════════
# PREFLIGHT — ONE FAILURE, NOT TWENTY-FIVE
# ═══════════════════════════════════════════════════════════════════════════

def test_missing_configuration_is_reported_once_and_touches_nothing(
        db_session, sample_org, sample_advisor, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    for i in range(25):
        lead = _lead(db_session, sample_org, sample_advisor,
                     first_name="Fam%d" % i, email="f%d@example.com" % i)
        _conv(db_session, sample_org, lead, sample_advisor)

    with patch.object(acs, "generate_touch_email") as gen:
        with patch.object(acs, "_send_email_resend") as sender:
            result = acs.process_scheduled_touches(db_session)

    # Not one record was looked at, and no provider was approached.
    gen.assert_not_called()
    sender.assert_not_called()
    assert result["aborted"] is True
    assert result["processed"] == 0
    assert "OPENAI_API_KEY" in result["error"]
    assert "OPENAI_API_KEY" in result["missing_config"]


def test_the_abort_makes_the_cron_exit_non_zero(db_session, sample_org,
                                                sample_advisor, monkeypatch):
    """A green run that did nothing is how this went unnoticed for so long:
    every run with no work due looked identical to a broken one."""
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    result = acs.process_scheduled_touches(db_session)
    # This is the expression app/jobs/run_ai_conversation_job.py exits on.
    assert (result.get("errors", 0) > 0 or "error" in result)


def test_a_configured_service_does_not_abort(db_session, sample_org,
                                             sample_advisor):
    result = acs.process_scheduled_touches(db_session)
    assert result.get("aborted") is not True


def test_preflight_names_every_missing_variable(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    check = acs.preflight()
    assert check["ok"] is False
    assert set(check["missing"]) == {"OPENAI_API_KEY", "RESEND_API_KEY"}
    assert "generate the message" in check["reason"]
    assert "deliver the message" in check["reason"]


def test_preflight_treats_blank_as_absent(monkeypatch):
    """An empty dashboard field is not configuration."""
    monkeypatch.setenv("OPENAI_API_KEY", "   ")
    assert "OPENAI_API_KEY" in acs.preflight()["missing"]


def test_preflight_reaches_no_database_and_no_provider():
    import ast, inspect
    tree = ast.parse(inspect.getsource(acs.preflight))
    names = {getattr(n.func, "id", None) or getattr(n.func, "attr", None)
             for n in ast.walk(tree) if isinstance(n, ast.Call)}
    for forbidden in ("query", "commit", "send_email_via_provider",
                      "_send_email_resend", "_get_client"):
        assert forbidden not in names
