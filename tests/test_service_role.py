"""SERVICE ROLES — one owner per scheduled task, and nothing runs by accident.

THE DEFECT, MEASURED IN PRODUCTION. `advisorflow-backend` and
`advisorflow-voice` run the same `app.main:app`, and the startup handler
started five background schedulers unconditionally. Both ran all five. Over 24
hours the `job_runs` ledger recorded:

    ai_conversation_loop        1,436   (720 configured)
    review_request_loop            96   ( 48 configured)
    cadence_loop                   48   ( 24 configured)
    support_intelligence_loop       9   (  4 configured)

Exactly 2x, every one, visible as pairs of runs 1-2 seconds apart.

Two processes a second apart reading `pipeline_conversations WHERE
next_send_at <= now` and sending from it is a double-send waiting for the gap
between read and write to widen. So the tests below are about correctness, not
tidiness.
"""

import ast
import inspect

import pytest

from app import service_role as sr
from app.models.job_models import JobName, LOOP_JOB_NAMES


@pytest.fixture(autouse=True)
def _clean_role(monkeypatch):
    monkeypatch.delenv(sr.ENV_VAR, raising=False)


# ── one owner, never two ────────────────────────────────────────────────────

def test_every_loop_has_exactly_one_owner():
    for name in LOOP_JOB_NAMES:
        assert name in sr.SCHEDULER_OWNER, "%s has no declared owner" % name
        assert isinstance(sr.SCHEDULER_OWNER[name], str)


def test_no_scheduled_task_is_owned_by_two_roles():
    """The whole point. A dict cannot express two owners, and that is the
    design - but a future refactor to a list could, so this says so."""
    for name, owner in sr.SCHEDULER_OWNER.items():
        assert isinstance(owner, str), "%s has multiple owners" % name
        assert owner in sr.KNOWN_ROLES


def test_the_ownership_table_covers_the_declared_loops_and_nothing_else():
    assert set(sr.SCHEDULER_OWNER) == set(LOOP_JOB_NAMES)
    assert sr.UNASSIGNED == ()


@pytest.mark.parametrize("name", list(LOOP_JOB_NAMES))
def test_exactly_one_role_starts_each_loop(name, monkeypatch):
    starters = []
    for role in sr.KNOWN_ROLES:
        monkeypatch.setenv(sr.ENV_VAR, role)
        if sr.owns(name):
            starters.append(role)
    assert len(starters) == 1, "%s is started by %s" % (name, starters)


# ── the backend owns the platform schedulers ────────────────────────────────

def test_backend_starts_the_platform_schedulers(monkeypatch):
    monkeypatch.setenv(sr.ENV_VAR, sr.ROLE_BACKEND)
    plan = sr.startup_plan()
    assert plan["known"] is True
    assert set(plan["start"]) == set(LOOP_JOB_NAMES)
    assert plan["skip"] == []


# ── voice runs voice things, and nothing else ───────────────────────────────

def test_voice_starts_no_platform_scheduler(monkeypatch):
    """Holding a Twilio media stream open has nothing to do with the cadence
    engine, the AI conversation loop, review-request SMS, the support brief or
    the session sweep."""
    monkeypatch.setenv(sr.ENV_VAR, sr.ROLE_VOICE)
    plan = sr.startup_plan()
    assert plan["known"] is True
    assert plan["start"] == []
    assert set(plan["skip"]) == set(LOOP_JOB_NAMES)


@pytest.mark.parametrize("name", list(LOOP_JOB_NAMES))
def test_voice_owns_nothing(name, monkeypatch):
    monkeypatch.setenv(sr.ENV_VAR, sr.ROLE_VOICE)
    assert sr.owns(name) is False


# ── cron-owned work is not also started by a web service ────────────────────

def test_a_cron_container_starts_no_in_process_loop(monkeypatch):
    monkeypatch.setenv(sr.ENV_VAR, sr.ROLE_JOB)
    assert sr.startup_plan()["start"] == []


def test_the_cron_job_names_are_not_in_the_in_process_ownership_table():
    """`ai_conversation_cron` and `cadence_cron` are separate Render services
    running job scripts. If either appeared here, a web process would be
    running the cron's work as well as the cron - which is the exact shape of
    the defect this module exists to remove."""
    from app.models.job_models import CRON_JOB_NAMES
    for cron_name in CRON_JOB_NAMES:
        assert cron_name not in sr.SCHEDULER_OWNER


# ── fail closed ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", [None, "", "   ", "web", "worker", "BACKEND_",
                                   "backendd", "voice2", "unknown", "0"])
def test_an_unrecognised_role_starts_nothing(value, monkeypatch):
    if value is None:
        monkeypatch.delenv(sr.ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(sr.ENV_VAR, value)
    plan = sr.startup_plan()
    assert plan["known"] is False
    assert plan["start"] == []
    assert set(plan["skip"]) == set(LOOP_JOB_NAMES)
    for name in LOOP_JOB_NAMES:
        assert sr.owns(name) is False


def test_an_absent_role_is_not_quietly_treated_as_backend(monkeypatch):
    """The tempting default, and the wrong one. Every loop here can eventually
    text or email a customer; a process that cannot say what it is has no
    business doing that."""
    monkeypatch.delenv(sr.ENV_VAR, raising=False)
    assert sr.current_role() == sr.ROLE_UNKNOWN
    assert sr.owns(JobName.AI_CONVERSATION) is False


def test_the_role_is_case_and_whitespace_tolerant(monkeypatch):
    """A trailing space in a dashboard field must not silently disable every
    scheduler in the platform."""
    monkeypatch.setenv(sr.ENV_VAR, "  Backend \n")
    assert sr.current_role() == sr.ROLE_BACKEND


def test_the_role_is_read_fresh_not_frozen_at_import(monkeypatch):
    monkeypatch.setenv(sr.ENV_VAR, sr.ROLE_BACKEND)
    assert sr.owns(JobName.CADENCE_LOOP) is True
    monkeypatch.setenv(sr.ENV_VAR, sr.ROLE_VOICE)
    assert sr.owns(JobName.CADENCE_LOOP) is False


# ── the startup handler actually uses it ────────────────────────────────────

def test_main_no_longer_creates_loop_tasks_unconditionally():
    """The regression this replaces: five bare `asyncio.create_task(...)`
    calls that ran wherever app.main was imported."""
    import app.main as main
    src = inspect.getsource(main.on_startup)
    tree = ast.parse(inspect.cleandoc(src))
    unconditional = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "attr", None) != "create_task":
            continue
        # A create_task whose argument names a loop function directly, rather
        # than one resolved from the ownership plan.
        arg = node.args[0] if node.args else None
        called = getattr(getattr(arg, "func", None), "id", None)
        if called and called.startswith("_") and called.endswith("_loop"):
            unconditional.append(called)
    assert not unconditional, (
        "app.main starts these regardless of service role: %s" % unconditional)


def test_startup_consults_the_ownership_plan():
    import app.main as main
    src = inspect.getsource(main.on_startup)
    assert "service_role" in src
    assert "log_startup_plan" in src or "startup_plan" in src


def test_every_loop_in_main_has_an_owner_entry():
    """A loop wired into the startup factory map but missing from the table
    would never run anywhere - a scheduler everyone believes is running."""
    import app.main as main
    src = inspect.getsource(main.on_startup)
    for name in LOOP_JOB_NAMES:
        assert name in sr.SCHEDULER_OWNER
    # And the factory map names every owned job.
    for job in sr.SCHEDULER_OWNER:
        const = [k for k, v in vars(JobName).items() if v == job]
        assert const, job
        assert ("JobName.%s" % const[0]) in src, (
            "app.main has no loop wired for %s" % job)


# ── the app still boots under every role ────────────────────────────────────

@pytest.mark.parametrize("role", [None, "backend", "voice", "job", "nonsense"])
def test_the_application_starts_under_any_role(role, monkeypatch, client):
    """Fail-closed must mean 'runs no schedulers', never 'refuses to serve'."""
    if role is None:
        monkeypatch.delenv(sr.ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(sr.ENV_VAR, role)
    assert client.get("/health").status_code == 200
