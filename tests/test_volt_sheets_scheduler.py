"""VOLT Google Sheets worker SCHEDULING (#630).

THE DEFECT. PR #620 registered `volt_sheets_loop` (JobName, LOOP_JOB_NAMES and
the app.main loop table) but never gave it an owner in
app/service_role.SCHEDULER_OWNER. The scheduler fails closed, so NO process
started it: production logged "These scheduled tasks have no declared owner and
will run nowhere: volt_sheets_loop" at every boot and job_runs held zero rows
for it, while the connection checks (which run in the request path) passed.

These tests pin the repair: the backend - and only the backend - starts the
loop, the loop really drains the outbox into the existing rows, and everything
the worker promised in #620 (switch, tenant, lease, lock, retries, monitoring,
no PII in logs, crash recovery, reconciliation) still holds when it is driven
by the scheduler rather than called by hand.
"""
import asyncio
import logging
from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import sessionmaker

from app import main as M
from app import service_role as sr
from app.models.job_models import JobName, LOOP_JOB_NAMES
from app.models.volt_leads_models import VoltLead, VoltLeadSheetOutbox
from app.services.volt import sheets_export as X
from app.services.volt import sheets_outbox as SO
from tests.test_volt_leads import enable, post
from tests.test_volt_recruiting import volt  # noqa: F401  (fixture)
from tests.test_volt_sheets_export import gs, outbox, run  # noqa: F401  (fixture)


class _Stop(Exception):
    pass


def _drive_loop_once(gs, monkeypatch, *, claim=True):
    """Run the REAL app.main._volt_sheets_loop for exactly one iteration
    against the test database: startup delay, one pass, then stop at the
    15-second sleep. Returns the sleeps the loop asked for."""
    factory = sessionmaker(bind=gs["db"].get_bind())
    monkeypatch.setattr("app.deps.SessionLocal", factory)
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) > 1:              # the first is the startup delay
            raise _Stop()

    async def fake_claim(job_name, interval):
        assert job_name == JobName.VOLT_SHEETS and interval == 15
        return claim
    monkeypatch.setattr(M.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(M, "_pass_claimed", fake_claim)
    with pytest.raises(_Stop):
        asyncio.run(M._volt_sheets_loop())
    gs["db"].expire_all()
    return sleeps


@pytest.fixture(autouse=True)
def _no_role(monkeypatch):
    monkeypatch.delenv(sr.ENV_VAR, raising=False)


# 1-2. ── who starts it ──────────────────────────────────────────────────────

def test_the_sheets_loop_has_one_owner_the_backend():
    assert JobName.VOLT_SHEETS in LOOP_JOB_NAMES
    assert sr.SCHEDULER_OWNER[JobName.VOLT_SHEETS] == sr.ROLE_BACKEND
    assert JobName.VOLT_SHEETS not in sr.UNASSIGNED


def test_backend_starts_it_and_nothing_else_does(monkeypatch):
    monkeypatch.setenv(sr.ENV_VAR, "backend")
    assert JobName.VOLT_SHEETS in sr.startup_plan()["start"]
    assert sr.owns(JobName.VOLT_SHEETS)
    for role in ("voice", "job", "", "nonsense"):
        monkeypatch.setenv(sr.ENV_VAR, role)
        assert JobName.VOLT_SHEETS in sr.startup_plan()["skip"], role
        assert not sr.owns(JobName.VOLT_SHEETS), role


def test_backend_startup_no_longer_warns_about_an_ownerless_loop(monkeypatch, caplog):
    monkeypatch.setenv(sr.ENV_VAR, "backend")
    with caplog.at_level(logging.INFO, logger="app.service_role"):
        plan = sr.log_startup_plan()
    assert plan["unassigned"] == []
    assert "volt_sheets_loop" not in " ".join(r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING)


def test_app_main_starts_a_loop_for_every_job_the_backend_owns():
    """The startup handler builds tasks from service_role's plan through its
    own name -> coroutine table; an owned name with no coroutine would be
    logged and skipped. Read the table from the source so this cannot drift."""
    import inspect
    src = inspect.getsource(M)
    table = src[src.index("_loop_factories = {"):]
    table = table[:table.index("}")]
    assert "JobName.VOLT_SHEETS:" in table and "_volt_sheets_loop" in table


# 3. ── the switch ───────────────────────────────────────────────────────────

def test_switched_off_the_scheduled_loop_processes_nothing(gs, monkeypatch):
    enable(gs)
    post(gs)
    monkeypatch.setenv("VOLT_SHEETS_SYNC_ENABLED", "off")
    called = []
    monkeypatch.setattr(SO, "sync_pass", lambda *a, **k: called.append(1) or {})
    _drive_loop_once(gs, monkeypatch)
    assert called == [] and gs["books"]["consumer"].data() == []
    assert outbox(gs, "consumer")[0].status == "pending"         # kept for later, not lost


def test_unapproved_tenant_processes_nothing(gs, monkeypatch):
    enable(gs)
    post(gs)
    monkeypatch.setenv("VOLT_SHEETS_ORG_ID", "")
    _drive_loop_once(gs, monkeypatch)
    assert gs["books"]["consumer"].data() == []


# 5-6. ── the scheduled loop really delivers, into the existing row ──────────

def test_scheduled_loop_writes_a_new_lead_then_updates_the_same_row(gs, monkeypatch):
    from app.models.job_models import JobRun
    enable(gs)
    post(gs)
    assert outbox(gs, "consumer")[0].status == "pending"         # queued with the save
    sleeps = _drive_loop_once(gs, monkeypatch)
    assert sleeps == [50, 15]                                    # startup delay, then the 15 s cadence
    book = gs["books"]["consumer"]
    lid = gs["db"].query(VoltLead).one().id
    assert len(book.data()) == 1 and book.by_id(lid)[9] == "New"
    assert outbox(gs, "consumer")[0].status == "synced"
    runs = gs["db"].query(JobRun).filter(JobRun.job_name == JobName.VOLT_SHEETS).all()
    assert len(runs) == 1 and runs[0].status == "success"        # visible in the ledger and the panels
    lead = gs["db"].get(VoltLead, lid)
    lead.status = "contacted"
    gs["db"].commit()
    _drive_loop_once(gs, monkeypatch)
    assert len(book.data()) == 1 and book.by_id(lid)[9] == "Contacted"


# 4. ── never two at once ────────────────────────────────────────────────────

def test_a_held_lease_means_this_instance_skips_the_pass(gs, monkeypatch):
    enable(gs)
    post(gs)
    _drive_loop_once(gs, monkeypatch, claim=False)
    assert gs["books"]["consumer"].data() == []
    assert outbox(gs, "consumer")[0].status == "pending"


def test_the_lease_admits_one_instance_per_interval(gs):
    from app.services.job_run_service import claim_pass
    factory = sessionmaker(bind=gs["db"].get_bind())
    assert claim_pass(JobName.VOLT_SHEETS, 15, factory) is True
    assert claim_pass(JobName.VOLT_SHEETS, 15, factory) is False  # a second instance, same interval


def test_a_held_advisory_lock_means_the_pass_writes_nothing(gs, monkeypatch):
    enable(gs)
    post(gs)
    monkeypatch.setattr(SO, "_lock", lambda db: False)
    assert run(gs)["skipped"] == "another worker is running"
    assert gs["books"]["consumer"].data() == [] and outbox(gs, "consumer")[0].status == "pending"


# 9-11. ── retries, monitoring, logs - driven by the scheduler ───────────────

def test_transient_failure_retries_and_permanent_failure_is_reported(gs, monkeypatch, caplog):
    enable(gs)
    post(gs, phone="214-555-0199", email="jane@example.test")
    book = gs["books"]["consumer"]
    book.fail = [503]
    with caplog.at_level(logging.DEBUG):
        _drive_loop_once(gs, monkeypatch)
    row = outbox(gs, "consumer")[0]
    assert row.status == "failed" and row.last_error == "google_unavailable" and row.attempts == 1
    row.next_retry_at = datetime.utcnow() - timedelta(seconds=1)  # backoff elapsed
    gs["db"].commit()
    _drive_loop_once(gs, monkeypatch)
    assert outbox(gs, "consumer")[0].status == "synced" and len(book.data()) == 1
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "214-555-0199" not in text and "jane@example.test" not in text
    # A permanent problem (lost permission) is named on the status panel.
    gs["db"].get(VoltLead, outbox(gs, "consumer")[0].record_id).status = "qualified"
    gs["db"].commit()
    book.fail = [403]
    _drive_loop_once(gs, monkeypatch)
    st = SO.status(gs["db"], gs["org"].id)
    consumer = next(b for b in st["businesses"] if b["business"] == "consumer")
    assert consumer["last_error_category"] == "permission"


# 8. ── the two businesses stay apart ────────────────────────────────────────

def test_consumer_and_recruiting_go_to_their_own_sheets(gs, monkeypatch):
    enable(gs)
    post(gs)
    _drive_loop_once(gs, monkeypatch)
    assert len(gs["books"]["consumer"].data()) == 1
    assert gs["books"]["recruiting"].data() == []
    assert {r.business for r in outbox(gs)} == {"consumer"}


# 12-13. ── restart recovery and reconciliation through the scheduler ────────

def test_restart_picks_up_an_abandoned_claim(gs, monkeypatch):
    enable(gs)
    post(gs)
    r = outbox(gs, "consumer")[0]
    r.status, r.claim_token, r.claimed_version = "processing", "killed-process", r.version
    r.claimed_at = datetime.utcnow() - SO.CLAIM_TTL - timedelta(seconds=1)
    gs["db"].commit()
    _drive_loop_once(gs, monkeypatch)                            # a fresh process after a deploy
    assert outbox(gs, "consumer")[0].status == "synced" and len(gs["books"]["consumer"].data()) == 1


def test_first_scheduled_pass_reconciles_a_change_the_queue_missed(gs, monkeypatch):
    enable(gs)
    post(gs)
    _drive_loop_once(gs, monkeypatch)
    lid = gs["db"].query(VoltLead).one().id
    # A change that never reached the queue (e.g. written while the listener
    # was not registered): the row says synced, the record is newer.
    gs["db"].query(VoltLead).filter(VoltLead.id == lid).update(
        {"status": "enrolled", "updated_at": datetime.utcnow() + timedelta(seconds=5)}, synchronize_session=False)
    gs["db"].query(VoltLeadSheetOutbox).update({"status": "synced"}, synchronize_session=False)
    gs["db"].commit()
    # A new process reconciles on its FIRST pass - even on a machine that
    # booted moments ago (time.monotonic() is time since boot; the loop used
    # to start its timers at 0 and so waited up to 30 min on a fresh host).
    import time as _t
    monkeypatch.setattr(_t, "monotonic", lambda: 100.0)
    _drive_loop_once(gs, monkeypatch)
    assert gs["books"]["consumer"].by_id(lid)[9] == "Enrolled"
    assert len(gs["books"]["consumer"].data()) == 1
