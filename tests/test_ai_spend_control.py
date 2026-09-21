"""AI SPEND CONTROL — what the gateway guarantees, proven without a provider.

THE PRODUCTION FINDING. With nobody using EvoSys, `_ai_conversation_loop` asked
OpenAI for a message every two minutes because conversations stayed due. The
earlier circuit breaker protected the web server when credit ran out; it did
nothing about spending once credit returned.

WHAT THIS FILE HOLDS TRUE
  * Default configuration: ZERO background provider calls.
  * AI_BACKGROUND_AUTOMATION_ENABLED=false: ZERO provider calls, no send, no
    conversation advanced, no row changed - however many conversations are due
    and however many passes run.
  * Manual AI, asked for by a signed-in user, still works with background off.
  * A provider error stops the batch - one request, not one per lead.
  * An unapproved model is refused; gpt-6-astra cannot be selected.
  * Per-pass, per-hour and per-day caps stop further requests.
  * No module outside app/services/ai_gateway.py talks to OpenAI directly.

NOTHING HERE REACHES A PROVIDER. Every client is a fake, and an autouse fixture
makes the gateway's real client explode if anything asks for it.
"""
import ast
import asyncio
import logging
import os
import re
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import event

from app.models.models import EmailMessage, Lead, PipelineConversation
from app.services import ai_conversation_service as acs
from app.services import ai_gateway as gw

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── fakes ───────────────────────────────────────────────────────────────────

class RateLimitError(Exception):
    """Same class NAME as the SDK's; the gateway classifies by name."""


class FakeClient:
    def __init__(self, content='{"subject": "S", "body": "A real body."}',
                 exc=None):
        self.calls = []
        self._content = content
        self._exc = exc
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        if self._exc is not None:
            raise self._exc
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self._content))],
            usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7))


@pytest.fixture(autouse=True)
def _no_real_provider(monkeypatch):
    """If any code path reaches for the gateway's real OpenAI client, fail."""
    def _explode(mode):
        raise AssertionError("a real provider client was requested (%s)" % mode)
    monkeypatch.setattr(gw, "_client", _explode)
    for name in ("AI_BACKGROUND_AUTOMATION_ENABLED", "AI_MANUAL_ACTIONS_ENABLED",
                 "AI_BG_MAX_CALLS_PER_PASS", "AI_BG_MAX_CALLS_PER_HOUR",
                 "AI_BG_MAX_CALLS_PER_DAY"):
        monkeypatch.delenv(name, raising=False)
    for cap in gw.CAPABILITY_MODELS:
        monkeypatch.delenv("AI_MODEL_" + cap.upper(), raising=False)
    # Preflight satisfied, so a disabled pass is disabled by the SWITCH.
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("RESEND_API_KEY", "test-key-not-real")


def _lead(db, org, advisor, i=0):
    lead = Lead(organization_id=org.id, assigned_to_id=advisor.id,
                first_name="Fam%d" % i, last_name="Family",
                email="f%d@example.com" % i, phone="1214555%04d" % i,
                status="new")
    db.add(lead); db.commit(); return lead


def _due(db, org, advisor, n):
    convs = []
    for i in range(n):
        lead = _lead(db, org, advisor, i)
        conv = PipelineConversation(
            organization_id=org.id, lead_id=lead.id, advisor_id=advisor.id,
            stage="outreach_sent", paused=False, flagged=False, touch_number=1,
            started_at=datetime.utcnow() - timedelta(days=2),
            next_send_at=datetime.utcnow() - timedelta(minutes=5))
        db.add(conv); db.commit(); convs.append(conv)
    return convs


def _snapshot(db):
    db.expire_all()
    return sorted(
        (c.id, c.touch_number, c.next_send_at, c.stage, c.messages_sent,
         c.ai_responses_sent)
        for c in db.query(PipelineConversation).all())


def _count_selects(db, table):
    seen = []

    def _before(conn, cursor, statement, params, context, executemany):
        if table in statement.lower():
            seen.append(statement)
    event.listen(db.bind, "before_cursor_execute", _before)
    return seen, lambda: event.remove(db.bind, "before_cursor_execute", _before)


# ═══════════════════════════════════════════════════════════════════════════
# 1. THE MASTER SWITCH — OFF BY DEFAULT
# ═══════════════════════════════════════════════════════════════════════════

def test_background_ai_is_off_when_the_variable_is_unset():
    assert "AI_BACKGROUND_AUTOMATION_ENABLED" not in os.environ
    assert gw.background_enabled() is False


def test_manual_ai_is_on_when_its_variable_is_unset():
    assert gw.manual_enabled() is True


@pytest.mark.parametrize("value", ["", "  ", "enabled", "tru", "maybe", "2", "False", "0", "off"])
def test_nothing_but_an_explicit_true_turns_background_on(monkeypatch, value):
    monkeypatch.setenv("AI_BACKGROUND_AUTOMATION_ENABLED", value)
    assert gw.background_enabled() is False


@pytest.mark.parametrize("value", ["true", "TRUE", " yes ", "1", "on"])
def test_explicit_true_turns_background_on(monkeypatch, value):
    monkeypatch.setenv("AI_BACKGROUND_AUTOMATION_ENABLED", value)
    assert gw.background_enabled() is True


def test_default_configuration_makes_zero_background_provider_calls():
    fake = FakeClient()
    with pytest.raises(gw.AIDisabled):
        gw.chat_completion(feature="t", capability="touch_email",
                           mode=gw.BACKGROUND, client=fake,
                           messages=[{"role": "user", "content": "x"}])
    assert fake.calls == []


def test_explicit_false_makes_zero_background_provider_calls(monkeypatch):
    monkeypatch.setenv("AI_BACKGROUND_AUTOMATION_ENABLED", "false")
    fake = FakeClient()
    for cap in gw.CAPABILITY_MODELS:
        with pytest.raises(gw.AIDisabled):
            gw.chat_completion(feature="t", capability=cap, mode=gw.BACKGROUND,
                               client=fake, messages=[])
    assert fake.calls == []
    assert gw.config_report()["counters"]["background_calls_attempted_this_process"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# 2. THE SCHEDULED LOOP WITH CONVERSATIONS DUE — NOTHING HAPPENS
# ═══════════════════════════════════════════════════════════════════════════

def test_due_conversations_burn_no_calls_and_change_nothing_when_disabled(
        db_session, sample_org, sample_advisor, monkeypatch, caplog):
    monkeypatch.setenv("AI_BACKGROUND_AUTOMATION_ENABLED", "false")
    _due(db_session, sample_org, sample_advisor, 12)
    before = _snapshot(db_session)
    fake = FakeClient()
    monkeypatch.setattr(acs, "_get_client", lambda: fake)
    selects, stop = _count_selects(db_session, "pipeline_conversations")
    caplog.set_level(logging.WARNING, logger="ai_gateway")
    try:
        with patch.object(acs, "_send_email_resend") as sender:
            # Ten passes = twenty minutes of the production loop.
            results = [acs.process_scheduled_touches(db_session,
                                                     org_id=sample_org.id)
                       for _ in range(10)]
    finally:
        stop()

    assert fake.calls == [], "no provider request"
    sender.assert_not_called()
    assert selects == [], "the due set is not even queried"
    assert all(r["disabled"] is True and r["processed"] == 0 for r in results)
    assert _snapshot(db_session) == before, "no conversation advanced"
    assert db_session.query(EmailMessage).count() == 0
    disabled_lines = [r for r in caplog.records
                      if "background AI is DISABLED" in r.getMessage()]
    assert len(disabled_lines) == 1, "one line per process, not one per pass"


def test_the_production_loop_skips_the_whole_pass_when_disabled(monkeypatch):
    """Drive `_ai_conversation_loop` itself for two intervals."""
    import app.main as main
    import app.services.job_run_service as jrs
    from contextlib import asynccontextmanager

    ledger = []

    @asynccontextmanager
    async def fake_record(job_name, db_factory=None):
        m = {}
        yield m
        ledger.append((job_name, dict(m)))

    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) > 2:           # startup delay + two 120 s intervals
            raise asyncio.CancelledError

    process = MagicMock()
    monkeypatch.setattr(jrs, "record_job_run", fake_record)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr("app.routers.ai_conversation_router.process_scheduled_touches",
                        process)
    off_loop = MagicMock()
    monkeypatch.setattr(main, "_off_loop", off_loop)

    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(asyncio.CancelledError):
            loop.run_until_complete(main._ai_conversation_loop())
    finally:
        loop.close()

    assert sleeps[:3] == [30, 120, 120]
    process.assert_not_called()
    off_loop.assert_not_called()
    assert len(ledger) == 2
    for name, metrics in ledger:
        assert name == "ai_conversation_loop"
        assert metrics == {"disabled": True, "provider_calls": 0}


def test_an_inbound_reply_triggers_no_ai_and_no_change_when_disabled(
        db_session, sample_org, sample_advisor, monkeypatch):
    conv = _due(db_session, sample_org, sample_advisor, 1)[0]
    lead = db_session.query(Lead).get(conv.lead_id)
    before = _snapshot(db_session)
    fake = FakeClient()
    monkeypatch.setattr(acs, "_get_client", lambda: fake)
    with patch.object(acs, "_send_email_resend") as sender:
        out = acs.handle_inbound_reply(db_session, lead, sample_advisor, "Yes please")
    assert out == {"action": "ai_disabled"}
    assert fake.calls == []
    sender.assert_not_called()
    assert _snapshot(db_session) == before


def test_pipeline_inbound_reply_is_inert_when_disabled(
        db_session, sample_org, sample_advisor):
    from app.services import pipeline_service
    lead = _lead(db_session, sample_org, sample_advisor)
    n = db_session.query(PipelineConversation).count()
    out = pipeline_service.process_inbound_reply(db_session, lead,
                                                 sample_advisor, MagicMock())
    assert out == {"action": "ai_disabled"}
    assert db_session.query(PipelineConversation).count() == n


def test_post_appointment_followups_do_nothing_when_disabled(db_session):
    from app.services import post_appointment_service as pas
    selects, stop = _count_selects(db_session, "booking")
    try:
        assert pas.check_and_send_followups(db_session) == 0
    finally:
        stop()
    assert selects == []


def test_auto_send_inbound_reply_is_skipped_when_disabled(monkeypatch):
    from app.routers import auto_send_router as asr
    monkeypatch.setattr(asr, "_compliance_block_reason", lambda *a, **k: None)
    advisor = SimpleNamespace(auto_send_phase="auto", organization_id="o", id="a")
    assert asr.handle_inbound_for_auto_send(MagicMock(), MagicMock(), advisor,
                                            "what time?") == "skipped"


def test_the_public_concierge_makes_no_provider_call_when_disabled(client):
    r = client.post("/concierge/chat",
                    json={"messages": [{"role": "user", "content": "hi"}]})
    # _no_real_provider would have raised inside the handler, and the handler
    # turns every exception into this same 500 - so assert the counter too.
    assert r.status_code == 500
    assert gw.config_report()["counters"]["refused"].get("disabled", 0) >= 1
    assert gw.config_report()["counters"]["background_calls_attempted_this_process"] == 0


def test_the_voice_realtime_session_is_refused_when_disabled():
    with pytest.raises(gw.AIDisabled):
        gw.admit_realtime(feature="voice", mode=gw.BACKGROUND)


# ═══════════════════════════════════════════════════════════════════════════
# 3. MANUAL AI STILL WORKS, SEPARATELY
# ═══════════════════════════════════════════════════════════════════════════

def test_manual_generation_works_while_background_is_off(monkeypatch):
    monkeypatch.setenv("AI_BACKGROUND_AUTOMATION_ENABLED", "false")
    fake = FakeClient(content="ok")
    resp = gw.chat_completion(feature="compose", capability="draft_reply",
                              mode=gw.MANUAL, actor="user-1", client=fake,
                              messages=[{"role": "user", "content": "x"}])
    assert resp.choices[0].message.content == "ok"
    assert len(fake.calls) == 1
    assert fake.calls[0]["model"] == "gpt-4o-mini"


def test_the_compose_preview_generates_for_a_signed_in_user(
        db_session, sample_org, sample_advisor, monkeypatch):
    lead = _lead(db_session, sample_org, sample_advisor)
    fake = FakeClient(content='{"subject": "Hello", "body": "A real body."}')
    monkeypatch.setattr(acs, "_get_client", lambda: fake)
    out = acs.generate_auto_reply(db_session, lead, sample_advisor,
                                  actor=sample_advisor.id)
    assert out["source"] == "ai"
    assert out["reply"] == "A real body."
    assert len(fake.calls) == 1 and fake.calls[0]["model"] == "gpt-4o"


def test_the_same_preview_without_a_user_is_background_and_refused(
        db_session, sample_org, sample_advisor, monkeypatch):
    lead = _lead(db_session, sample_org, sample_advisor)
    fake = FakeClient()
    monkeypatch.setattr(acs, "_get_client", lambda: fake)
    out = acs.generate_auto_reply(db_session, lead, sample_advisor)
    assert out["source"] == "fallback"
    assert out["error_kind"] == "AIDisabled"
    assert fake.calls == []


def test_a_manual_call_must_name_the_user_who_asked():
    fake = FakeClient()
    with pytest.raises(gw.AIRefused):
        gw.chat_completion(feature="x", capability="draft_reply",
                           mode=gw.MANUAL, actor=None, client=fake, messages=[])
    assert fake.calls == []


def test_the_manual_switch_turns_manual_ai_off(monkeypatch):
    monkeypatch.setenv("AI_MANUAL_ACTIONS_ENABLED", "false")
    fake = FakeClient()
    with pytest.raises(gw.AIDisabled):
        gw.chat_completion(feature="x", capability="draft_reply",
                           mode=gw.MANUAL, actor="u", client=fake, messages=[])
    assert fake.calls == []


def test_manual_calls_do_not_spend_the_background_budget(monkeypatch):
    monkeypatch.setenv("AI_BG_MAX_CALLS_PER_HOUR", "1")
    fake = FakeClient(content="ok")
    for _ in range(5):
        gw.chat_completion(feature="x", capability="draft_reply",
                           mode=gw.MANUAL, actor="u", client=fake, messages=[])
    assert len(fake.calls) == 5


# ═══════════════════════════════════════════════════════════════════════════
# 4. A PROVIDER ERROR STOPS THE BATCH
# ═══════════════════════════════════════════════════════════════════════════

def test_a_provider_error_stops_the_batch_after_one_request(
        db_session, sample_org, sample_advisor, monkeypatch):
    monkeypatch.setenv("AI_BACKGROUND_AUTOMATION_ENABLED", "true")
    _due(db_session, sample_org, sample_advisor, 6)
    before = _snapshot(db_session)
    fake = FakeClient(exc=RateLimitError("insufficient_quota"))
    monkeypatch.setattr(acs, "_get_client", lambda: fake)
    with patch.object(acs, "_send_email_resend") as sender:
        out = acs.process_scheduled_touches(db_session, org_id=sample_org.id)
    assert len(fake.calls) == 1, "one request, not one per lead"
    sender.assert_not_called()
    assert out["halted"] is True
    assert _snapshot(db_session) == before, "the rest stay due, untouched"


def test_after_a_provider_error_the_breaker_refuses_the_next_pass(
        db_session, sample_org, sample_advisor, monkeypatch):
    monkeypatch.setenv("AI_BACKGROUND_AUTOMATION_ENABLED", "true")
    _due(db_session, sample_org, sample_advisor, 3)
    fake = FakeClient(exc=RateLimitError("insufficient_quota"))
    monkeypatch.setattr(acs, "_get_client", lambda: fake)
    with patch.object(acs, "_send_email_resend"):
        acs.process_scheduled_touches(db_session, org_id=sample_org.id)
        second = acs.process_scheduled_touches(db_session, org_id=sample_org.id)
    assert len(fake.calls) == 1, "the breaker made the second pass free"
    assert second["halted"] is True
    assert gw.config_report()["counters"]["breaker_open_for_s"] > 0


def test_background_requests_are_made_with_sdk_retries_off(monkeypatch):
    monkeypatch.undo()                   # the real _client, never called out
    gw._reset_for_tests()
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    assert gw._client(gw.BACKGROUND).max_retries == 0
    gw._reset_for_tests()


# ═══════════════════════════════════════════════════════════════════════════
# 5. MODELS ARE PINNED; GPT-6-ASTRA CANNOT BE SELECTED
# ═══════════════════════════════════════════════════════════════════════════

def test_every_capability_resolves_to_an_approved_model():
    for cap, model in gw.CAPABILITY_MODELS.items():
        assert gw.resolve_model(cap) == model
        assert model in gw.APPROVED_MODELS


def test_an_unknown_capability_is_refused_not_defaulted():
    with pytest.raises(gw.ModelNotApproved):
        gw.resolve_model("something_new")


def test_an_env_override_to_an_unapproved_model_is_refused(monkeypatch, caplog):
    monkeypatch.setenv("AI_MODEL_TOUCH_EMAIL", "gpt-4.1")
    monkeypatch.setenv("AI_BACKGROUND_AUTOMATION_ENABLED", "true")
    fake = FakeClient()
    caplog.set_level(logging.ERROR, logger="ai_gateway")
    with pytest.raises(gw.ModelNotApproved):
        gw.chat_completion(feature="t", capability="touch_email",
                           mode=gw.BACKGROUND, client=fake, messages=[])
    assert fake.calls == []
    assert any("not an approved model" in r.getMessage() for r in caplog.records)


def test_an_env_override_to_an_approved_model_is_honoured(monkeypatch):
    monkeypatch.setenv("AI_MODEL_TOUCH_EMAIL", "gpt-4o-mini")
    assert gw.resolve_model("touch_email") == "gpt-4o-mini"


def test_a_caller_cannot_pass_a_model():
    with pytest.raises(TypeError):
        gw.chat_completion(feature="t", capability="draft_reply", mode=gw.MANUAL,
                           actor="u", client=FakeClient(), messages=[],
                           model="gpt-6-astra")


def test_gpt_6_astra_is_not_approved_anywhere():
    assert "gpt-6-astra" not in gw.APPROVED_MODELS
    assert "gpt-6-astra" not in gw.CAPABILITY_MODELS.values()
    assert not any("astra" in m or m.startswith("gpt-6") for m in gw.APPROVED_MODELS)


def test_gpt_6_astra_cannot_be_selected_through_any_env_override(monkeypatch):
    monkeypatch.setenv("AI_BACKGROUND_AUTOMATION_ENABLED", "true")
    fake = FakeClient()
    for cap in gw.CAPABILITY_MODELS:
        monkeypatch.setenv("AI_MODEL_" + cap.upper(), "gpt-6-astra")
        with pytest.raises(gw.ModelNotApproved):
            gw.chat_completion(feature="t", capability=cap, mode=gw.MANUAL,
                               actor="u", client=fake, messages=[])
    assert fake.calls == []
    assert all(v.startswith("REFUSED")
               for v in gw.config_report()["capability_models"].values())


def test_the_workforce_provider_is_unavailable_with_an_unapproved_model(monkeypatch):
    from app.services.workforce.model_router import OpenAIProvider
    monkeypatch.setenv("AI_WORKFORCE_LLM_ENABLED", "true")
    for model in ("gpt-6-astra", "gpt-4.1-mini"):
        monkeypatch.setenv("AI_WORKFORCE_OPENAI_MODEL", model)
        assert OpenAIProvider().available() is False


def test_no_source_file_names_gpt_6_or_astra_outside_the_gateway():
    pat = re.compile(r"gpt-6|\bastra\b", re.IGNORECASE)
    hits = []
    for base in ("app", "frontend/src", "scripts"):
        for dp, dn, fn in os.walk(os.path.join(REPO, base)):
            dn[:] = [d for d in dn if d not in ("node_modules", "dist", "__pycache__")]
            for f in fn:
                if not f.endswith((".py", ".js", ".jsx", ".ts", ".tsx", ".json",
                                   ".yaml", ".yml", ".toml", ".env", ".cfg")):
                    continue
                p = os.path.join(dp, f)
                if p.endswith(os.path.join("services", "ai_gateway.py")):
                    continue
                try:
                    src = open(p, encoding="utf-8", errors="ignore").read()
                except OSError:
                    continue
                if pat.search(src):
                    hits.append(p)
    assert hits == []


# ═══════════════════════════════════════════════════════════════════════════
# 6. SPEND CAPS
# ═══════════════════════════════════════════════════════════════════════════

def _bg(fake):
    return gw.chat_completion(feature="t", capability="reply_classification",
                              mode=gw.BACKGROUND, client=fake, messages=[])


def test_the_per_pass_cap_stops_further_requests(monkeypatch):
    monkeypatch.setenv("AI_BACKGROUND_AUTOMATION_ENABLED", "true")
    monkeypatch.setenv("AI_BG_MAX_CALLS_PER_PASS", "2")
    fake = FakeClient(content="ok")
    with gw.background_pass("t"):
        _bg(fake); _bg(fake)
        with pytest.raises(gw.SpendLimitReached):
            _bg(fake)
    assert len(fake.calls) == 2


def test_the_per_hour_cap_stops_further_requests(monkeypatch):
    monkeypatch.setenv("AI_BACKGROUND_AUTOMATION_ENABLED", "true")
    monkeypatch.setenv("AI_BG_MAX_CALLS_PER_HOUR", "3")
    fake = FakeClient(content="ok")
    for _ in range(3):
        _bg(fake)
    with pytest.raises(gw.SpendLimitReached):
        _bg(fake)
    assert len(fake.calls) == 3


def test_the_per_day_cap_stops_further_requests(monkeypatch):
    monkeypatch.setenv("AI_BACKGROUND_AUTOMATION_ENABLED", "true")
    monkeypatch.setenv("AI_BG_MAX_CALLS_PER_DAY", "2")
    fake = FakeClient(content="ok")
    _bg(fake); _bg(fake)
    with pytest.raises(gw.SpendLimitReached):
        _bg(fake)
    assert len(fake.calls) == 2


def test_the_per_pass_cap_halts_the_scheduled_loop(
        db_session, sample_org, sample_advisor, monkeypatch):
    monkeypatch.setenv("AI_BACKGROUND_AUTOMATION_ENABLED", "true")
    monkeypatch.setenv("AI_BG_MAX_CALLS_PER_PASS", "2")
    _due(db_session, sample_org, sample_advisor, 5)
    fake = FakeClient()
    monkeypatch.setattr(acs, "_get_client", lambda: fake)
    with patch.object(acs, "_send_email_resend") as sender:
        out = acs.process_scheduled_touches(db_session, org_id=sample_org.id)
    assert len(fake.calls) == 2
    assert sender.call_count == 2
    assert out["sent"] == 2 and out["halted"] is True
    still_due = db_session.query(PipelineConversation).filter(
        PipelineConversation.touch_number == 1).count()
    assert still_due == 3


# ═══════════════════════════════════════════════════════════════════════════
# 7. ONE DOOR — AND WHAT IT LOGS
# ═══════════════════════════════════════════════════════════════════════════

def test_no_module_outside_the_gateway_talks_to_openai_directly():
    offenders = []
    app_dir = os.path.join(REPO, "app")
    for dp, dn, fn in os.walk(app_dir):
        dn[:] = [d for d in dn if d != "__pycache__"]
        for f in fn:
            if not f.endswith(".py"):
                continue
            p = os.path.join(dp, f)
            if p.endswith(os.path.join("services", "ai_gateway.py")):
                continue
            try:
                tree = ast.parse(open(p, encoding="utf-8-sig").read())
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr in (
                        "completions", "responses", "embeddings"):
                    if node.attr == "completions" or isinstance(node.value, ast.Call):
                        offenders.append((p, node.lineno, node.attr))
                if isinstance(node, ast.Call):
                    name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                    if name in ("OpenAI", "AsyncOpenAI"):
                        offenders.append((p, node.lineno, name))
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("openai"):
                    offenders.append((p, node.lineno, "from openai"))
                if isinstance(node, ast.Import) and any(
                        a.name.split(".")[0] == "openai" for a in node.names):
                    offenders.append((p, node.lineno, "import openai"))
    assert offenders == []


def test_a_request_is_logged_with_its_metadata_and_nothing_sensitive(
        monkeypatch, caplog):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key-SECRET-should-never-appear")
    caplog.set_level(logging.INFO, logger="ai_gateway")
    fake = FakeClient(content="the completion text")
    gw.chat_completion(feature="compose.preview", capability="draft_reply",
                       mode=gw.MANUAL, actor="u", org_id="org-9", client=fake,
                       messages=[{"role": "user", "content": "PRIVATE PROMPT"}])
    line = next(r.getMessage() for r in caplog.records
                if r.getMessage().startswith("ai_call"))
    for part in ("feature=compose.preview", "capability=draft_reply",
                 "mode=manual", "org=org-9", "model=gpt-4o-mini", "corr=",
                 "tokens_in=11", "tokens_out=7", "status=ok"):
        assert part in line
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "SECRET" not in text
    assert "PRIVATE PROMPT" not in text
    assert "the completion text" not in text


def test_the_config_report_names_models_but_never_the_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key-SECRET-should-never-appear")
    monkeypatch.setenv("AI_MODEL_TOUCH_EMAIL", "gpt-6-astra")
    monkeypatch.setenv("SOME_MODEL_API_KEY", "fake-another-SECRET")
    report = gw.config_report()
    blob = repr(report)
    assert "SECRET" not in blob
    assert report["openai_key_present"] is True
    assert report["background_enabled"] is False
    assert report["model_environment"]["AI_MODEL_TOUCH_EMAIL"] == "gpt-6-astra"
    assert report["capability_models"]["touch_email"].startswith("REFUSED")


def test_the_god_endpoint_is_registered():
    from app.main import app
    assert any(getattr(r, "path", "") == "/god/ai-spend-control" for r in app.routes)
