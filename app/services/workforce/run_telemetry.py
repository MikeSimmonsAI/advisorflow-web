"""Run telemetry — the run row's OWN transaction, never the caller's.

WHY THIS EXISTS. `runtime.execute` never commits: the caller owns the
business transaction (work item transitions, tool mutations, counters). So a
heartbeat written through the caller's session is invisible to every other
session until the caller commits, which can be after the run is over. This
module writes the run-evidence row (`ai_employee_runs`) through a short-lived,
independent session so `GET /workforce/runs` can see a live run.

TRANSACTION MAP
    caller session (`db`)   work items, tools, eligibility, performance
                            counters, audit. Committed/rolled back by the
                            caller. THIS MODULE NEVER TOUCHES IT.
    telemetry session       one per call: INSERT (start) or one guarded UPDATE
                            (heartbeat/finalize), commit, close. It can only
                            ever contain the run row.

THE CALLER NEVER WRITES THE RUN ROW. When the row is created here, the runtime
carries a transient `RunHandle` (not an ORM object) and the counters live on
it; they are persisted by the next telemetry write. That is what avoids a row
lock held by the caller's open transaction blocking the telemetry UPDATE (and
vice versa).

GUARDS (every UPDATE)
    WHERE id = :run_id AND organization_id = :org AND status = 'running'
          AND ended_at IS NULL
  so a terminal row is never revived or overwritten by a late heartbeat or a
  second finalize, and a run id from another org matches nothing.

FAILURE POLICY. Nothing here raises. A failed heartbeat is logged and
reported as False; the work is never retried and no success is implied. A
failed start returns None and the runtime falls back to the old in-caller-
session row (heartbeats then become visible only when the caller commits —
reported as `independent=False`, and the UI reads that as unknown liveness,
never as "working").

LOCK BOUNDS. Postgres: `SET LOCAL lock_timeout`. SQLite: a single writer, so
while the caller holds an open write transaction the telemetry write waits at
most `SQLITE_BUSY_MS` and then reports failure rather than hanging the run.
`start` also fails (-> fallback) when the caller's uncommitted rows (a new
work item or employee) are not visible to this session (FK).
"""

import logging
from datetime import datetime
from typing import Callable, Optional

from app.services.workforce import run_evidence

_log = logging.getLogger(__name__)

STAGE_MAX = 80
LOCK_TIMEOUT_MS = 1500
SQLITE_BUSY_MS = 300
SQLITE_DEFAULT_BUSY_MS = 5000

# Overridable seam (tests / future scheduler). Default resolves app.deps lazily
# so importing this module has no database side effects.
_session_factory: Optional[Callable] = None


def _new_session():
    if _session_factory is not None:
        return _session_factory()
    from app.deps import SessionLocal
    return SessionLocal()


def _model():
    from app.models.workforce_models import AIEmployeeRun
    return AIEmployeeRun


def _now() -> datetime:
    return datetime.utcnow()


class RunHandle:
    """Transient, non-ORM view of a run owned by the telemetry transaction."""

    independent = True

    def __init__(self, run_id: str, organization_id: str, started_at: datetime):
        self.id = run_id
        self.organization_id = organization_id
        self.started_at = started_at
        self.status = "running"
        self.ended_at = None
        self.last_heartbeat_at = started_at
        self.current_stage = "started"
        self.iterations = 0
        self.tool_calls = 0
        self.denied_tool_calls = 0
        self.provider = None
        self.model_name = None
        self.prompt_tokens = None
        self.completion_tokens = None


def _bound_waits(s) -> Optional[str]:
    """Bound lock waits for this transaction. Returns the dialect name."""
    try:
        name = s.get_bind().dialect.name
    except Exception:                                        # noqa: BLE001
        return None
    from sqlalchemy import text
    if name == "postgresql":
        s.execute(text("SET LOCAL lock_timeout = %d" % LOCK_TIMEOUT_MS))
    elif name == "sqlite":
        s.execute(text("PRAGMA busy_timeout = %d" % SQLITE_BUSY_MS))
    return name


def _restore_waits(s, name) -> None:
    if name == "sqlite":
        try:
            from sqlalchemy import text
            s.execute(text("PRAGMA busy_timeout = %d" % SQLITE_DEFAULT_BUSY_MS))
        except Exception:                                    # noqa: BLE001
            pass


def start(*, organization_id: str, employee_id: str, work_item_id,
          objective: str, mode: str, trigger: str, capability: str,
          lease_seconds: Optional[int]) -> Optional[RunHandle]:
    """INSERT the run row in its own transaction and commit. None on failure."""
    s = None
    try:
        s = _new_session()
        t = _now()
        M = _model()
        row = M(organization_id=organization_id, employee_id=employee_id,
                work_item_id=work_item_id, objective=(objective or "")[:2000],
                trigger=trigger, mode=mode, status="running",
                model_capability=capability, started_at=t,
                last_heartbeat_at=t, current_stage="started",
                lease_seconds=int(lease_seconds) if lease_seconds else None)
        s.add(row)
        s.flush()
        run_id = row.id
        s.commit()
        return RunHandle(run_id, organization_id, t)
    except Exception:                                        # noqa: BLE001
        _log.warning("workforce telemetry: run row not created "
                     "(falling back to caller session)", exc_info=True)
        _safe_rollback(s)
        return None
    finally:
        _safe_close(s)


def _counter_values(h: RunHandle) -> dict:
    vals = {"iterations": int(h.iterations or 0),
            "tool_calls": int(h.tool_calls or 0),
            "denied_tool_calls": int(h.denied_tool_calls or 0)}
    for k in ("provider", "model_name", "prompt_tokens", "completion_tokens"):
        v = getattr(h, k, None)
        if v is not None:
            vals[k] = v
    return vals


def _guarded_update(h: RunHandle, values: dict, *, extra_status=None) -> bool:
    s = None
    waits = None
    try:
        s = _new_session()
        waits = _bound_waits(s)
        M = _model()
        q = s.query(M).filter(M.id == h.id,
                              M.organization_id == h.organization_id)
        if extra_status is None:
            q = q.filter(M.status == "running", M.ended_at.is_(None))
        else:
            q = q.filter(M.status == extra_status)
        n = q.update(values, synchronize_session=False)
        s.commit()
        return bool(n)
    except Exception:                                        # noqa: BLE001
        _log.warning("workforce telemetry: write failed for run %s",
                     getattr(h, "id", "?"), exc_info=True)
        _safe_rollback(s)
        return False
    finally:
        if s is not None:
            _restore_waits(s, waits)
        _safe_close(s)


def heartbeat(h: RunHandle, stage: str) -> bool:
    """Refresh liveness + counters. False if terminal, contended or failed."""
    if h is None or h.status != "running" or h.ended_at is not None:
        return False
    t = _now()
    if h.last_heartbeat_at and t < h.last_heartbeat_at:
        t = h.last_heartbeat_at                  # never regress the timestamp
    clean = run_evidence.redact(stage, STAGE_MAX)
    vals = _counter_values(h)
    vals.update(last_heartbeat_at=t, current_stage=clean)
    ok = _guarded_update(h, vals)
    if ok:
        h.last_heartbeat_at, h.current_stage = t, clean
    return ok


def finalize(h: RunHandle, status: str, *, summary: str = "",
             abort_reason: Optional[str] = None, error: Optional[str] = None,
             duration_ms: Optional[int] = None) -> bool:
    """Write the terminal result once. A second call is a no-op (False)."""
    if h is None or h.status != "running" or h.ended_at is not None:
        return False
    t = _now()
    if h.last_heartbeat_at and t < h.last_heartbeat_at:
        t = h.last_heartbeat_at
    vals = _counter_values(h)
    vals.update(status=status, ended_at=t, last_heartbeat_at=t,
                current_stage=status, duration_ms=duration_ms,
                summary=run_evidence.redact(summary or "", 4000),
                abort_reason=run_evidence.redact(abort_reason or "", 255),
                error=run_evidence.redact(error or "", 255))
    ok = _guarded_update(h, vals)
    if ok:
        h.status, h.ended_at, h.last_heartbeat_at = status, t, t
        h.current_stage = status
    return ok


def correct_after_rollback(h: RunHandle) -> bool:
    """The caller's transaction rolled back AFTER this run was finalized as
    completed: the business work did not persist, so 'completed' is false.
    Narrow, explicit supersede: only completed -> failed, same org."""
    if h is None or h.status != "completed":
        return False
    t = _now()
    ok = _guarded_update(
        h, {"status": "failed", "current_stage": "failed",
            "last_heartbeat_at": t,
            "error": "caller transaction rolled back; work not persisted"},
        extra_status="completed")
    if ok:
        h.status = "failed"
    return ok


def _safe_rollback(s) -> None:
    try:
        if s is not None:
            s.rollback()
    except Exception:                                        # noqa: BLE001
        pass


def _safe_close(s) -> None:
    try:
        if s is not None:
            s.close()
    except Exception:                                        # noqa: BLE001
        pass
