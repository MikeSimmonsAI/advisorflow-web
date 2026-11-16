"""job_runs retention (audit #650): old rows go, recent and newest-per-job stay."""
from datetime import datetime, timedelta

from app.models.job_models import JobRun
from app.services.job_run_service import purge_old_job_runs

NOW = datetime(2026, 11, 16, 6, 0, 0)


def _run(db, name, days_ago, status="ok"):
    r = JobRun(job_name=name, started_at=NOW - timedelta(days=days_ago),
               finished_at=NOW - timedelta(days=days_ago), status=status)
    db.add(r)
    db.flush()
    return r.id


def test_purges_only_old_finished_rows_and_keeps_newest_per_job(db_session):
    db = db_session
    old_a = [_run(db, "cadence_loop", d) for d in (90, 60, 45)]
    recent_a = _run(db, "cadence_loop", 2)
    retired_older = _run(db, "ai_conversation_cron", 59)
    retired_only = _run(db, "ai_conversation_cron", 58)      # newest of its job
    stuck = _run(db, "email_poller", 70, status="running")
    db.commit()

    n = purge_old_job_runs(db, now=NOW)
    db.commit()

    left = {r.id for r in db.query(JobRun).all()}
    assert n == 4
    assert not (set(old_a) | {retired_older}) & left
    assert {recent_a, retired_only, stuck} <= left


def test_batch_limit_bounds_one_pass(db_session):
    db = db_session
    for d in range(40, 60):
        _run(db, "program_sla_loop", d)
    _run(db, "program_sla_loop", 1)
    db.commit()
    assert purge_old_job_runs(db, now=NOW, batch_limit=5) == 5
    db.commit()
    assert db.query(JobRun).count() == 16


def test_disabled_when_retention_or_batch_is_zero(db_session):
    db = db_session
    _run(db, "x", 100)
    _run(db, "x", 1)
    db.commit()
    assert purge_old_job_runs(db, now=NOW, retain_days=0) == 0
    assert purge_old_job_runs(db, now=NOW, batch_limit=0) == 0
