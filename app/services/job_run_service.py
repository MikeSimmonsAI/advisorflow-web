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
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

log = logging.getLogger("job_run_service")

_MAX_ERR = 512  # truncation ceiling for error_summary


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
        status = "error" if caught_exc else "success"
        err_str = _safe_err(caught_exc) if caught_exc else None

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
