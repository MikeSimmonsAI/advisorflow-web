"""A BACKGROUND JOB MUST NEVER BE ABLE TO TAKE THE WEB SERVER OFFLINE.

THE INCIDENT. God Mode → Workspaces showed "Unable to reach the server" as
part of what looked like broader instability. The frontend was not wrong: the
backend genuinely was not answering, over and over. Render's event log for the
single web instance, one morning:

    10:30  Instance failed: health check  connection refused
    10:35  Instance failed: health check  connection refused
    10:40  Instance failed: Exited with status 143
    10:48  Instance failed: HTTP health check timed out after 5 seconds
    10:51  Instance failed: Exited with status 143
    11:27  Instance failed: health check  connection refused
    11:38  Instance failed: HTTP health check timed out after 5 seconds
    11:54  Instance failed: HTTP health check timed out after 5 seconds

— memory steady, 299 requests in twelve hours, no deploy. And in the
application log, the moment the health checks stopped being answered:

    12:05:32  GET /health 200
    12:05:37  generate_touch_email error (RateLimitError): 429 ... credit_balance_exhausted
    12:05:37  _send_touch refused for lead e5d8...
    12:05:39  generate_touch_email error (RateLimitError) ...
    12:05:41  ...one every ~1.5s, no /health lines at all...

THE CAUSE. `_ai_conversation_loop` is an `async def` on the web server's event
loop, and it called the synchronous `process_scheduled_touches` directly. That
walks every due conversation and asks OpenAI to write each touch. With the
OpenAI account out of credit, every call came back 429 after the SDK's own
retries, and a failed touch does not advance `next_send_at`, so the whole due
set was retried every two minutes. A synchronous call inside a coroutine does
not yield: for the length of each pass uvicorn could not accept a connection,
Render's five-second health check timed out, the instance was killed (143),
refused connections while it rebooted, came back — and thirty seconds later
started the same pass.

THE FIXES, BOTH GENERAL:

  1. Every background loop runs its pass through `app.main._off_loop`, i.e. in
     a worker thread. The event loop stays free however slow a provider is.
  2. A provider-level failure (no credit, bad key, provider unreachable) stops
     the pass after ONE call instead of one per conversation, leaving every
     remaining conversation due and untouched for the next pass.

NOTHING IN THIS FILE SENDS. Every provider is a mock.
"""
import ast
import asyncio
import os
import subprocess
import time
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from app.models.models import Lead, PipelineConversation
from app.services import ai_conversation_service as acs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(ROOT, "app", "main.py")


def _read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as fh:
        return fh.read()


# ── 1. the mechanism, executed ──────────────────────────────────────────────

def _measure_responsiveness(blocking_call, block_seconds=0.6):
    """How many times a 10ms heartbeat got to run while `blocking_call` ran.

    The heartbeat stands in for everything else the event loop serves — HTTP
    requests, and Render's /health probe. If it cannot run, neither can they.
    """
    beats = {"n": 0}

    async def heartbeat(stop):
        while not stop.is_set():
            beats["n"] += 1
            await asyncio.sleep(0.01)

    async def scenario():
        stop = asyncio.Event()
        hb = asyncio.create_task(heartbeat(stop))
        await asyncio.sleep(0.02)          # let it start
        before = beats["n"]
        await blocking_call(lambda: time.sleep(block_seconds))
        during = beats["n"] - before
        stop.set()
        await hb
        return during

    # A PRIVATE LOOP, NOT asyncio.run(). asyncio.run() ends by setting the
    # thread's current event loop to None, and later test modules that call
    # `asyncio.get_event_loop().run_until_complete(...)` then fail for a reason
    # that has nothing to do with them — which is exactly what the first
    # version of this file did to tests/test_god_job_runs.py in a full run.
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(scenario())
    finally:
        loop.close()


def test_a_pass_run_through_off_loop_leaves_the_server_responsive():
    """THE FIX. A 0.6-second synchronous pass, and the heartbeat keeps beating
    through it — the web server would keep answering /health."""
    from app.main import _off_loop

    async def via_off_loop(fn):
        await _off_loop(fn)

    during = _measure_responsiveness(via_off_loop)
    assert during >= 20, (
        "the event loop only ran %d times during a 0.6s background pass — a "
        "pass run through _off_loop must not block it" % during)


def test_the_old_shape_starves_the_server_which_is_the_incident():
    """THE CONTROL. The same pass called directly from a coroutine, the way
    the AI loop used to call process_scheduled_touches. The heartbeat does not
    run at all — and neither did Render's health check."""
    async def directly(fn):
        fn()

    during = _measure_responsiveness(directly)
    assert during <= 1, (
        "a synchronous call inside a coroutine let the loop run %d times; if "
        "this ever passes the control is broken, not the bug fixed" % during)


# ── 2. every loop uses it ───────────────────────────────────────────────────

BACKGROUND_LOOPS = (
    "_support_intelligence_loop", "_review_request_loop",
    "_ai_conversation_loop", "_cadence_loop",
    "_session_cleanup_loop", "_sales_reminder_loop",
)

# The synchronous work each loop exists to do. None of these may be called in
# the body of an `async def` — only inside a plain `def` handed to _off_loop,
# or passed to _off_loop by name.
BLOCKING_WORK = {
    "process_scheduled_touches", "run_due_cadences", "run_review_request_cron",
    "purge_dead_sessions", "process_due", "run_daily_intelligence",
}


def _called_name(call):
    f = call.func
    return f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)


def _direct_calls(async_fn):
    """Calls made in this coroutine's own body, NOT inside a nested sync def."""
    found = []

    def walk(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.Lambda)):
                continue           # a nested sync def runs in the worker thread
            if isinstance(child, ast.Call):
                found.append(child)
            walk(child)

    walk(async_fn)
    return found


@pytest.fixture(scope="module")
def loops():
    tree = ast.parse(_read(MAIN))
    return {n.name: n for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef) and n.name in BACKGROUND_LOOPS}


def test_every_background_loop_is_accounted_for(loops):
    assert set(loops) == set(BACKGROUND_LOOPS), (
        "a background loop was added or renamed; add it here so it is held to "
        "the same rule")


@pytest.mark.parametrize("name", BACKGROUND_LOOPS)
def test_no_loop_runs_its_blocking_pass_on_the_event_loop(loops, name):
    """The rule, per loop: the synchronous work is never called directly in
    the coroutine body. A new loop written the old way fails here, by name,
    before it can take the web server down in production."""
    fn = loops[name]
    offenders = [c for c in _direct_calls(fn) if _called_name(c) in BLOCKING_WORK]
    assert not offenders, (
        "%s calls %s directly on the event loop — run it through _off_loop"
        % (name, ", ".join(sorted({_called_name(c) for c in offenders}))))

    awaited_off_loop = any(
        isinstance(n, ast.Await) and isinstance(n.value, ast.Call)
        and _called_name(n.value) == "_off_loop"
        for n in ast.walk(fn))
    assert awaited_off_loop, "%s never awaits _off_loop" % name


# ── 3. one provider failure, not one per conversation ───────────────────────

@pytest.fixture()
def _configured(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("RESEND_API_KEY", "test-key-not-real")


def _due_set(db, org, advisor, n=3):
    convs = []
    for i in range(n):
        lead = Lead(organization_id=org.id, assigned_to_id=advisor.id,
                    first_name="Fam%d" % i, last_name="Family",
                    email="fam%d@example.com" % i, phone="1214555%04d" % i,
                    status="new")
        db.add(lead)
        db.commit()
        conv = PipelineConversation(
            organization_id=org.id, lead_id=lead.id, advisor_id=advisor.id,
            stage="outreach_sent", paused=False, flagged=False, touch_number=0,
            started_at=datetime.utcnow() - timedelta(days=1),
            next_send_at=datetime.utcnow() - timedelta(minutes=5))
        db.add(conv)
        db.commit()
        convs.append(conv)
    return convs


def _failure(kind, provider_level):
    return {"subject": "x", "body": "y", "should_stop": False, "escalate": False,
            "source": "fallback", "error_kind": kind, "generation_failed": True,
            "provider_unavailable": provider_level, "touch_number": 0}


def test_a_provider_failure_stops_the_pass_after_one_call(
        _configured, db_session, sample_org, sample_advisor):
    """THE AMPLIFIER, SHUT. An OpenAI account with no credit answers every
    lead the same way; asking once per conversation is what made the pass
    long enough to starve the health check."""
    convs = _due_set(db_session, sample_org, sample_advisor, n=3)
    before = {c.id: c.next_send_at for c in convs}

    with patch.object(acs, "generate_touch_email",
                      return_value=_failure("RateLimitError", True)) as gen:
        with patch.object(acs, "_send_email_resend") as sender:
            result = acs.process_scheduled_touches(db_session, org_id=sample_org.id)

    assert gen.call_count == 1, \
        "the provider was asked %d times in one pass" % gen.call_count
    sender.assert_not_called()
    assert result.get("halted") is True
    assert result["sent"] == 0 and result["errors"] == 1

    # Nothing skipped, nothing advanced: every conversation is still due, so
    # the next pass sends them when the provider is back.
    db_session.expire_all()
    for c in db_session.query(PipelineConversation).filter(
            PipelineConversation.id.in_(list(before))).all():
        assert c.next_send_at == before[c.id], \
            "a halted pass moved conversation %s" % c.id
        assert c.touch_number == 0


def test_a_per_lead_failure_does_not_stop_the_pass(
        _configured, db_session, sample_org, sample_advisor):
    """One lead's bad response says nothing about the next — the pass goes on,
    exactly as it did before this change."""
    _due_set(db_session, sample_org, sample_advisor, n=3)
    with patch.object(acs, "generate_touch_email",
                      return_value=_failure("JSONDecodeError", False)) as gen:
        with patch.object(acs, "_send_email_resend"):
            result = acs.process_scheduled_touches(db_session, org_id=sample_org.id)
    assert gen.call_count == 3
    assert not result.get("halted")
    assert result["errors"] == 3


def test_the_provider_level_list_names_the_failures_that_are_about_the_account():
    for kind in ("RateLimitError", "AuthenticationError", "PermissionDeniedError",
                 "APIConnectionError", "APITimeoutError"):
        assert kind in acs.PROVIDER_LEVEL_ERRORS, kind
    for kind in ("JSONDecodeError", "KeyError", "ValueError"):
        assert kind not in acs.PROVIDER_LEVEL_ERRORS, kind


def test_the_real_generator_marks_a_quota_failure_as_provider_level(
        _configured, db_session, sample_org, sample_advisor):
    """End to end through `generate_touch_email`, with the OpenAI client
    replaced by one that raises the SDK's own RateLimitError class name."""
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="Pat", last_name="Family", email="pat@example.com",
                phone="12145557000", status="new")
    db_session.add(lead)
    db_session.commit()

    class RateLimitError(Exception):
        pass

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(**_kw):
                    raise RateLimitError("insufficient_quota")

    with patch.object(acs, "_get_client", return_value=_Client()):
        out = acs.generate_touch_email(db_session, lead, sample_advisor, 0)
    assert out["generation_failed"] is True
    assert out["provider_unavailable"] is True
    assert out["error_kind"] == "RateLimitError"


# ── 4. the browser's message is honest about which failure it was ───────────

def test_an_answered_request_is_never_called_unreachable():
    """Runs the real classifier under node. See tests/frontend/httpErrors.test.mjs."""
    script = os.path.join(ROOT, "tests", "frontend", "httpErrors.test.mjs")
    try:
        proc = subprocess.run(["node", script], capture_output=True, text=True,
                              cwd=ROOT, timeout=120)
    except (FileNotFoundError, OSError):
        pytest.skip("node is not available on this machine")
    assert proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")


def test_a_request_with_no_answer_is_marked_as_one():
    """The only place 'Unable to reach the server' may be produced, and it no
    longer blames the reader's connection. It carries `kind = 'network'` and
    deliberately NO status — auth/workspaceGuard.js reads a missing status as
    'no refusal occurred', which is exactly true."""
    import re
    client_js = _read(ROOT, "frontend", "src", "api", "client.js")
    block = client_js[client_js.index("} catch (networkErr) {"):]
    block = block[:block.index("if (res.status === 401)")]
    # The comment above the throw quotes the old wording to explain why it
    # went; only the code is checked.
    block = re.sub(r"^\s*//.*$", "", block, flags=re.M)
    assert "err.kind = 'network'" in block
    assert "err.status" not in block, \
        "a transport failure now claims an HTTP status; the workspace guard " \
        "would read it as a refusal"
    assert "check your connection" not in block, \
        "the message is back to blaming the reader's connection"
    assert "httpErrorKind" in client_js and "fallbackHttpMessage" in client_js, \
        "answered errors are no longer classified by api/httpErrors.js"
