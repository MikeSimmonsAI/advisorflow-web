"""RUN EVIDENCE — what an AI employee has actually run, from persisted rows.

CANONICAL SOURCE: `ai_employee_runs` (one bounded execution, written by
runtime._start_run / _finish_run), joined to `ai_work_items` only for the
stored job label. Nothing here is a second store, a counter, or a guess.

WHAT IS NEVER PRODUCED: a percentage, an ETA, a stage name that is not stored,
or an "active" state from `switched_on`, `may_run`, assignment or eligibility.
A status string we do not recognise is "unknown".

The pure half (normalise / redact / shape / lineage) imports no ORM so it is
testable without sqlalchemy; the query half imports models lazily.
Every query takes `organization_id` from the caller's workspace and filters on
it in SQL; a row of another organization is indistinguishable from a missing one.
"""
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

# Defined ONCE. Source statuses written today are running / completed / failed;
# the rest are accepted only if a writer records them explicitly.
STATE_MAP = {
    "queued": "queued", "pending": "queued",
    "accepted": "accepted",
    "running": "running",
    "paused": "paused",
    "blocked": "blocked",
    "completed": "completed", "complete": "completed", "succeeded": "completed",
    "failed": "failed", "error": "failed",
    "cancelled": "cancelled", "canceled": "cancelled",
    "skipped": "skipped",
    "stale": "stale", "hung": "stale",
}
TERMINAL_STATES = frozenset({"completed", "failed", "cancelled", "skipped"})
ACTIVE_STATES = frozenset({"queued", "accepted", "running"})

# A run whose only evidence is "started, never ended" is called stale only
# after this long. runtime.DEFAULT_RUN_SECONDS is 120; 15 minutes is far past
# any bounded run, so crossing it means the writer died without finishing.
STALE_AFTER = timedelta(minutes=15)

SUMMARY_MAX = 280
LIST_LIMIT_DEFAULT, LIST_LIMIT_MAX = 50, 200

_SECRET_RE = re.compile(
    r"(sk|ghp|gho|pk|rk|xox[bap])[-_][A-Za-z0-9_-]{8,}"
    r"|bearer\s+[A-Za-z0-9._-]{12,}"
    r"|authorization\s*[:=]\s*(?:bearer\s+)?\S+"
    r"|(api[_-]?key|secret|token|password)\s*[:=]\s*\S+", re.I)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"(?<!\d)\+?\d[\d\s().-]{8,}\d(?!\d)")


def normalize_state(raw: Any) -> str:
    k = raw.strip().lower() if isinstance(raw, str) else ""
    return STATE_MAP.get(k, "unknown")


def is_terminal(state: str) -> bool:
    return state in TERMINAL_STATES


def redact(text: Any, limit: int = SUMMARY_MAX) -> Optional[str]:
    """Credentials, emails and phone numbers out; bounded length."""
    if not isinstance(text, str) or not text.strip():
        return None
    out = _SECRET_RE.sub("[redacted]", text)
    out = _EMAIL_RE.sub("[redacted]", out)
    out = _PHONE_RE.sub("[redacted]", out)
    out = out.strip()
    return out[:limit] if out else None


def _naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is not None and dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _iso(dt: Optional[datetime]) -> Optional[str]:
    dt = _naive_utc(dt)
    return None if dt is None else dt.isoformat() + "Z"   # DB stores naive UTC


def shape_run(run: Any, *, job_label: Optional[str] = None,
              now: Optional[datetime] = None,
              superseded_by: Optional[str] = None) -> Dict[str, Any]:
    """One row -> one contract object. Absent facts are None, never invented."""
    now = _naive_utc(now) or datetime.utcnow()
    state = normalize_state(getattr(run, "status", None))
    started = _naive_utc(getattr(run, "started_at", None))
    ended = _naive_utc(getattr(run, "ended_at", None))

    if is_terminal(state):
        superseded_by = None      # terminal history is never rewritten
    stale = False
    if (state == "running" and ended is None and started is not None
            and now - started > STALE_AFTER):
        stale = True
    if state == "stale":
        stale = True
    # A newer run on the same work item exists, so this one can no longer be
    # the live one. Reported as stale evidence, not as an invented outcome.
    if superseded_by and state in ACTIVE_STATES:
        stale = True

    out: Dict[str, Any] = {
        "run_id": run.id,
        "employee_id": run.employee_id,
        "work_item_id": getattr(run, "work_item_id", None),
        "task_label": redact(job_label, 120),
        "state": state,
        "source_state": getattr(run, "status", None),
        "terminal": is_terminal(state),
        "stale": stale,
        "stale_after_seconds": (int(STALE_AFTER.total_seconds())
                                if stale else None),
        "superseded_by": superseded_by,
        "mode": getattr(run, "mode", None),
        "simulated": getattr(run, "mode", None) == "simulation",
        "trigger": getattr(run, "trigger", None),
        "started_at": _iso(started),
        "updated_at": _iso(ended or started),
        "ended_at": _iso(ended),
        "evidence": {"kind": "run_record", "source": "ai_employee_runs"},
        "checkpoint_summary": redact(getattr(run, "summary", None)),
        "ended_because": redact(getattr(run, "abort_reason", None), 120),
        "failure_summary": redact(getattr(run, "error", None), 160),
        "iterations": getattr(run, "iterations", None),
        "tool_calls": getattr(run, "tool_calls", None),
    }
    if state == "blocked":
        out["blocked_reason"] = out["ended_because"]
    return out


def apply_lineage(shaped: List[Dict[str, Any]]) -> None:
    """Mark non-terminal runs superseded by a later run on the same work item.

    Only recorded facts are used: same employee, same stored work_item_id, a
    strictly later started_at. Terminal runs are never rewritten.
    """
    by_key: Dict[Any, List[Dict[str, Any]]] = {}
    for r in shaped:
        if r.get("work_item_id"):
            by_key.setdefault((r["employee_id"], r["work_item_id"]),
                              []).append(r)
    for runs in by_key.values():
        runs.sort(key=lambda r: r.get("started_at") or "")
        for i, r in enumerate(runs[:-1]):
            later = runs[i + 1]
            if (not r["terminal"] and r["state"] in ACTIVE_STATES
                    and (later.get("started_at") or "")
                    > (r.get("started_at") or "")):
                r["superseded_by"] = later["run_id"]
                r["stale"] = True
                r["stale_after_seconds"] = int(STALE_AFTER.total_seconds())


def split_current_history(shaped: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Newest trusted active run is `current`; everything else is history.

    Terminal, superseded and stale runs are never current.
    """
    items = list(shaped)

    def key(r):
        return r.get("updated_at") or r.get("started_at") or ""

    active = sorted((r for r in items
                     if r["state"] in ACTIVE_STATES and not r["terminal"]
                     and not r["superseded_by"] and not r["stale"]),
                    key=key, reverse=True)
    current = active[0] if active else None
    history = sorted((r for r in items if r is not current),
                     key=key, reverse=True)
    return {"current": current, "history": history}


# ── QUERIES (org-scoped in SQL) ─────────────────────────────────────────────

def clamp_limit(limit: Any) -> int:
    try:
        n = int(limit)
    except (TypeError, ValueError):
        return LIST_LIMIT_DEFAULT
    return max(1, min(n, LIST_LIMIT_MAX))


def _labels(db, organization_id: str, runs) -> Dict[str, str]:
    from app.models.workforce_models import AIWorkItem
    ids = {r.work_item_id for r in runs if r.work_item_id}
    if not ids:
        return {}
    rows = (db.query(AIWorkItem.id, AIWorkItem.job_key)
            .filter(AIWorkItem.organization_id == organization_id,
                    AIWorkItem.id.in_(ids)).all())
    return {i: k for i, k in rows}


def list_runs(db, *, organization_id: str, employee_id: Optional[str] = None,
              limit: Any = None, now: Optional[datetime] = None
              ) -> Dict[str, Any]:
    from app.models.workforce_models import AIEmployeeRun
    n = clamp_limit(limit)
    q = db.query(AIEmployeeRun).filter(
        AIEmployeeRun.organization_id == organization_id)
    if employee_id:
        q = q.filter(AIEmployeeRun.employee_id == employee_id)
    rows = q.order_by(AIEmployeeRun.started_at.desc()).limit(n).all()
    labels = _labels(db, organization_id, rows)
    shaped = [shape_run(r, job_label=labels.get(r.work_item_id), now=now)
              for r in rows]
    apply_lineage(shaped)
    out = split_current_history(shaped)
    out["as_of"] = _iso(now or datetime.utcnow())
    out["truncated"] = len(rows) >= n
    return out


def get_run(db, *, organization_id: str, run_id: str,
            now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """None for missing AND for another organization's run (identical)."""
    from app.models.workforce_models import AIEmployeeRun
    row = (db.query(AIEmployeeRun)
           .filter(AIEmployeeRun.id == run_id,
                   AIEmployeeRun.organization_id == organization_id).first())
    if row is None:
        return None
    labels = _labels(db, organization_id, [row])
    later_id = None
    if row.work_item_id:
        later = (db.query(AIEmployeeRun.id)
                 .filter(AIEmployeeRun.organization_id == organization_id,
                         AIEmployeeRun.employee_id == row.employee_id,
                         AIEmployeeRun.work_item_id == row.work_item_id,
                         AIEmployeeRun.started_at > row.started_at)
                 .order_by(AIEmployeeRun.started_at.asc()).first())
        later_id = later[0] if later else None
    return shape_run(row, job_label=labels.get(row.work_item_id), now=now,
                     superseded_by=later_id)
