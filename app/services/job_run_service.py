"""
Job-run recorder (GOD-10).

Usage — wrap any background loop body in record_job_run():

    async with record_job_run("cadence_loop", db_factory=SessionLocal) as metrics:
        result = run_due_cadences(db)
        metrics["sent"]      = result.get("sent", 0)
        metrics["completed"] = result.get("completed", 0)
        metrics["errors"]    = result.get("errors", 0)

The context manager:
  1. Opens its own DB session (never borrows the loop's session — isolation).
  2. Inserts a JobRun row with status='running'.
  3. Yields a mutable dict the caller can populate with loop-specific counts.
  4. On exit — regardless of exception — updates the row to 'success'/'error',
     sets finished_at and duration_ms, writes metrics.
  5. Closes the session. Any DB error inside record_job_run() is swallowed and
     logged so a metrics-write failure never crashes the loop it is measuring.

SAFETY: error messages are truncated to 512 chars. Never log credentials,
message bodies, OAuth tokens, or stack frames — only the exception type and
a short human-readable summary.
"""

import logging
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

log = logging.getLogger("job_run_service")

_MAX_ERR = 512  # truncation ceiling for error_summary


# A body that isolates failures (one tenant's error must not stop the others)
# never raises, so the row closed 'success' however many tenants failed. Such a
# body sets metrics[FAILED_KEY] = "<what failed>" and the run closes 'error'
# with that summary - isolation, not suppression. The key never reaches the
# stored metrics.
FAILED_KEY = "_failed"


def _close_status(caught_exc, metrics):
    failed = metrics.pop(FAILED_KEY, None) if isinstance(metrics, dict) else None
    if caught_exc:
        return "error", _safe_err(caught_exc)
    if failed:
        return "error", str(failed)[:500]
    return "success", None


def _safe_err(exc: Exception) -> str:
    """Convert an exception to a safe, truncated string — no secrets."""
    raw = f"{type(exc).__name__}: {exc}"
    return raw[:_MAX_ERR]


@asynccontextmanager
async def record_job_run(
    job_name: str,
    db_factory: Callable[[], Session],
):
    """Async context manager that bookends a background-loop body with a DB row.

    Yields a mutable dict. Populate it with loop-specific metrics (counts,
    org totals, etc.) — whatever already appears in the loop's log statements.
    The dict is stored as-is in job_runs.metrics (JSONB).

    Example
    -------
        async with record_job_run("cadence_loop", db_factory=SessionLocal) as m:
            result = run_due_cadences(db)
            m["sent"] = result.get("sent", 0)
    """
    from app.models.job_models import JobRun

    metrics: dict[str, Any] = {}
    run_id: int | None = None
    started = datetime.now(timezone.utc)
    t0 = time.monotonic()

    # --- open row -------------------------------------------------------
    try:
        db: Session = db_factory()
        try:
            row = JobRun(
                job_name=job_name,
                started_at=started,
                status="running",
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            run_id = row.id
        finally:
            db.close()
    except Exception as exc:
        log.warning("job_run_service: could not open run row for %s: %s", job_name, exc)
        # Still yield so the loop body runs unaffected
        yield metrics
        return

    # --- run caller's body ----------------------------------------------
    caught_exc: Exception | None = None
    try:
        yield metrics
    except Exception as exc:
        caught_exc = exc
        raise
    finally:
        # --- close row --------------------------------------------------
        duration_ms = int((time.monotonic() - t0) * 1000)
        status, err_str = _close_status(caught_exc, metrics)

        try:
            db2: Session = db_factory()
            try:
                db2.query(JobRun).filter(JobRun.id == run_id).update(
                    {
                        "finished_at": datetime.now(timezone.utc),
                        "status": status,
                        "error_summary": err_str,
                        "duration_ms": duration_ms,
                        "metrics": metrics if metrics else None,
                    },
                    synchronize_session=False,
                )
                db2.commit()
            finally:
                db2.close()
        except Exception as close_exc:
            log.warning(
                "job_run_service: could not close run row %s for %s: %s",
                run_id, job_name, close_exc,
            )


@contextmanager
def record_job_run_sync(
    job_name: str,
    db_factory: Callable[[], Session],
):
    """Synchronous twin of record_job_run(), for the Render cron entrypoints.

    WHY A SECOND ONE. The three scripts under app/jobs/ are plain synchronous
    `python app/jobs/x.py` processes launched by Render cron. They cannot use
    an async context manager without an event loop, so for the ledger's whole
    life they simply did not record — they printed a JSON blob to stdout and
    that was all. Render's cron run-history showed the truth; the platform's
    own System Health screen could not, because nothing ever reached job_runs.

    Wrapping each script's body in this makes a cron run as visible as a loop
    iteration, using the same table and the same semantics. It is not a second
    job framework: there is one ledger, one row shape, one recorder — this is
    the sync door into it.

    Same contract as the async version, deliberately: yields a mutable metrics
    dict, always closes the row, and SWALLOWS its own database errors so that a
    metrics failure can never fail the job it is measuring. A cron process that
    exits non-zero because its bookkeeping hiccuped would page somebody about
    the wrong problem.
    """
    from app.models.job_models import JobRun

    metrics: dict[str, Any] = {}
    run_id: int | None = None
    started = datetime.now(timezone.utc)
    t0 = time.monotonic()

    try:
        db: Session = db_factory()
        try:
            row = JobRun(job_name=job_name, started_at=started, status="running")
            db.add(row)
            db.commit()
            db.refresh(row)
            run_id = row.id
        finally:
            db.close()
    except Exception as exc:
        log.warning("job_run_service: could not open run row for %s: %s", job_name, exc)
        yield metrics
        return

    caught_exc: Exception | None = None
    try:
        yield metrics
    except Exception as exc:
        caught_exc = exc
        raise
    finally:
        duration_ms = int((time.monotonic() - t0) * 1000)
        status, err_str = _close_status(caught_exc, metrics)

        try:
            db2: Session = db_factory()
            try:
                db2.query(JobRun).filter(JobRun.id == run_id).update(
                    {
                        "finished_at": datetime.now(timezone.utc),
                        "status": status,
                        "error_summary": err_str,
                        "duration_ms": duration_ms,
                        "metrics": metrics if metrics else None,
                    },
                    synchronize_session=False,
                )
                db2.commit()
            finally:
                db2.close()
        except Exception as close_exc:
            log.warning(
                "job_run_service: could not close run row %s for %s: %s",
                run_id, job_name, close_exc,
            )


def claim_pass(job_name: str, interval_seconds, db_factory) -> bool:
    """One pass of a background loop per interval across EVERY instance.

    Each web instance starts the same loops. With one instance that is fine;
    with two, every reminder sweep, review-request run and AI pass would run
    twice (the data-level guards keep most of that from double-sending, but
    not all of it, and none of it from double-working). The first instance to
    reach a pass takes a lease named for the job that lives ~90% of the
    interval and is never released early; the others find it held and skip.
    If the holder dies the lease simply expires.

    Fails CLOSED (P0, 2026-10-29): if the lease cannot be taken for any
    reason - another holder, the table missing, the database unreachable or its
    connection pool exhausted - this instance does NOT run the pass; the loop
    tries again at its next interval. It used to fail open ("run anyway"),
    which meant that exactly when the database was struggling every loop on
    every instance ran unguarded and at once - the overlapping, memory-heavy
    passes that preceded the Oct 28-29 OOM kills. A skipped pass is retried a
    few minutes later; a doubled or overlapping one cannot be taken back. The
    failure is logged at ERROR so a lease that keeps failing is visible.
    """
    try:
        ttl = max(30, int(float(interval_seconds) * 0.9))
    except (TypeError, ValueError):
        ttl = 60
    try:
        db = db_factory()
    except Exception as exc:                                     # noqa: BLE001
        log.error("claim_pass(%s): no database session (%s); skipping this pass",
                  job_name, type(exc).__name__)
        return False
    try:
        from app.services import action_lease
        return action_lease.acquire(db, "job.pass", job_name, ttl_seconds=ttl) is not None
    except Exception:                                            # noqa: BLE001
        log.error("claim_pass(%s): lease unavailable; skipping this pass", job_name, exc_info=True)
        try:
            db.rollback()
        except Exception:                                        # noqa: BLE001
            pass
        return False
    finally:
        db.close()


# ── ABANDONED RUN ROWS (P0, 2026-10-29) ─────────────────────────────────────
#
# record_job_run commits the 'running' row BEFORE the body and closes it in a
# separate transaction AFTER. A process that is killed in between - an OOM
# kill, a deploy's SIGTERM, a crash - never runs the close, and the row says
# 'running' forever. Production had 33 such rows on 2026-10-29 (19 EvoSense
# hunts, 7 Wholesale sweeps, 6 program SLA, 1 AI conversation), almost all
# opened minutes before a Render `server_failed: oomKilled` event, while the
# ledger reported zero errors.
#
# On backend startup, a loop row still 'running' long after any pass could
# have finished is closed as an error with a plain reason. The cut-off is far
# beyond the slowest pass ever recorded (an EvoSense hunt: 48 minutes) so a
# pass that is genuinely still running on the other instance during a
# zero-downtime deploy is never touched. Only rows of in-process LOOP jobs are
# considered: Render cron jobs are separate processes this one knows nothing
# about. Nothing is deleted; started_at and metrics are left as they were.
ABANDONED_AFTER_SECONDS = 3 * 3600
ABANDONED_SUMMARY = ("abandoned: the process stopped before this pass finished "
                     "(restart, deploy or out-of-memory kill); closed at startup")


def close_abandoned_runs(db_factory, *, older_than_seconds: int = ABANDONED_AFTER_SECONDS,
                         now=None) -> int:
    """Close loop-job rows left 'running' by a process that died. Returns how
    many rows were closed. Never raises."""
    from datetime import timedelta
    from app.models.job_models import JobRun, LOOP_JOB_NAMES
    now = now or datetime.now(timezone.utc)
    # job_runs.started_at is a timezone-less column holding UTC (production
    # session TimeZone is UTC). Compare with a naive UTC value so the cut-off
    # means the same thing whatever the session time zone is.
    cutoff = (now.astimezone(timezone.utc) if now.tzinfo else now).replace(tzinfo=None) \
        - timedelta(seconds=older_than_seconds)
    try:
        db = db_factory()
    except Exception as exc:                                     # noqa: BLE001
        log.warning("close_abandoned_runs: no database session: %s", type(exc).__name__)
        return 0
    try:
        n = (db.query(JobRun)
             .filter(JobRun.status == "running",
                     JobRun.finished_at.is_(None),
                     JobRun.job_name.in_(LOOP_JOB_NAMES),
                     JobRun.started_at < cutoff)
             .update({"status": "error", "finished_at": now,
                      "error_summary": ABANDONED_SUMMARY},
                     synchronize_session=False))
        db.commit()
        if n:
            log.warning("close_abandoned_runs: closed %d abandoned loop run row(s)", n)
        return int(n or 0)
    except Exception as exc:                                     # noqa: BLE001
        try:
            db.rollback()
        except Exception:                                        # noqa: BLE001
            pass
        log.warning("close_abandoned_runs failed: %s", type(exc).__name__)
        return 0
    finally:
        db.close()
