"""T9 WATCHING ITSELF - because silent failure looks exactly like a quiet day.

THE FAILURE MODE THIS PREVENTS. An aggregation pass raises, the exception is
caught and logged, the read model keeps yesterday's payload or stays empty,
and the command centre renders zeros. Zero stalled objectives. Zero
exceptions. Nothing needs you. A manager reads that as good news and closes
the tab, and the workforce with fourteen problems stays broken for a week.

There is no way to tell those zeros from real ones by looking at them, which
is why section 24 asks for T9 itself to be observable and why every pass
writes a row here whether it succeeded or not. The read models then carry the
failure forward as STALENESS rather than as absence, so the screen says "this
has not been computed since 06:12" instead of "everything is fine".

WHAT IS TRACKED, from section 24's own list: aggregation failures, stale read
models, failed supervisor runs, evaluation failures, queue generation
failures, tenant-scope refusals and reconciliation contradictions. The
scope-refusal counter is the interesting one - a pass that discarded a signal
because it fell outside its scope has found either a bug or an attack, and
either way it must not be silent.
"""

import json
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.workforce_intelligence_models import (AIIntelligenceReadModel,
                                                      AIIntelligenceRun)
from app.services.workforce_intelligence import constants as C
from app.services.workforce_intelligence.scope import Scope

_log = logging.getLogger(__name__)


@contextmanager
def pass_run(db: Session, scope: Scope, pass_key: str):
    """Wrap one pass so it cannot fail invisibly.

    THE ROW IS WRITTEN EVEN WHEN THE PASS RAISES, and the exception is
    re-raised rather than swallowed. Swallowing here would reintroduce exactly
    the failure this module exists to make visible - the caller decides
    whether to continue, and the record exists either way.
    """
    started = datetime.utcnow()
    row = AIIntelligenceRun(
        scope_type=scope.scope_type, scope_id=scope.scope_id or "",
        pass_key=pass_key, status=C.RUN_OK, started_at=started)
    db.add(row)
    try:
        db.flush()
    except Exception:                                        # noqa: BLE001
        _log.exception("t9 observability: could not open a run record")

    stats: Dict[str, Any] = {}
    try:
        yield stats
    except Exception as exc:                                 # noqa: BLE001
        row.status = C.RUN_FAILED
        row.error = str(exc)[:255]
        _finish(db, row, started, stats)
        raise
    else:
        row.status = (C.RUN_PARTIAL if stats.get("partial") else C.RUN_OK)
        _finish(db, row, started, stats)


def _finish(db: Session, row: AIIntelligenceRun, started: datetime,
            stats: Dict[str, Any]) -> None:
    ended = datetime.utcnow()
    row.ended_at = ended
    row.duration_ms = int((ended - started).total_seconds() * 1000)
    row.organizations_scanned = int(stats.get("organizations", 0) or 0)
    row.items_written = int(stats.get("written", 0) or 0)
    row.items_cleared = int(stats.get("cleared", 0) or 0)
    row.findings_written = int(stats.get("findings", 0) or 0)
    row.contradictions_found = int(stats.get("contradictions", 0) or 0)
    row.scope_refusals = int(stats.get("scope_refusals", 0) or 0)
    try:
        row.detail = json.dumps(stats, default=str)[:4000]
        db.flush()
    except Exception:                                        # noqa: BLE001
        _log.exception("t9 observability: could not close a run record")


def health(db: Session, scope: Scope, *,
           now: Optional[datetime] = None) -> Dict[str, Any]:
    """Is T9 itself working? Answered from its own rows, not from a heartbeat.

    A HEARTBEAT PROVES A PROCESS IS ALIVE. This proves the passes RAN and what
    they did, which is the question that matters: a scheduler that fires a
    pass that raises every time produces a healthy heartbeat and no
    intelligence at all.
    """
    now = now or datetime.utcnow()
    since = now - timedelta(days=1)
    rows = (db.query(AIIntelligenceRun)
            .filter(AIIntelligenceRun.scope_type == scope.scope_type,
                    AIIntelligenceRun.scope_id == (scope.scope_id or ""),
                    AIIntelligenceRun.started_at >= since)
            .order_by(AIIntelligenceRun.started_at.desc())
            .limit(200).all())

    by_pass: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        slot = by_pass.setdefault(row.pass_key, {
            "runs": 0, "failed": 0, "partial": 0, "last_status": None,
            "last_run_at": None, "last_error": None, "scope_refusals": 0})
        slot["runs"] += 1
        if row.status == C.RUN_FAILED:
            slot["failed"] += 1
        if row.status == C.RUN_PARTIAL:
            slot["partial"] += 1
        slot["scope_refusals"] += int(row.scope_refusals or 0)
        if slot["last_run_at"] is None:
            slot["last_status"] = row.status
            slot["last_run_at"] = (row.started_at.isoformat()
                                   if row.started_at else None)
            slot["last_error"] = row.error

    stale_models = _stale_read_models(db, scope, now=now)
    never_ran = [p for p in C.PASSES if p not in by_pass]
    failing = [p for p, slot in by_pass.items() if slot["failed"]]
    refusals = sum(slot["scope_refusals"] for slot in by_pass.values())

    return {
        "generated_at": now.isoformat(),
        "scope": scope.as_dict(),
        "passes": by_pass,
        "passes_never_run_in_24h": never_ran,
        "passes_with_failures": failing,
        "stale_read_models": stale_models,
        "scope_refusals_24h": refusals,
        "healthy": not failing and not stale_models and not never_ran,
        # THE SENTENCE A SCREEN SHOWS ABOVE THE NUMBERS. It names the
        # consequence rather than the condition, because "aggregate pass
        # failed" tells a manager nothing they can act on.
        "statement": _statement(failing, never_ran, stale_models, refusals),
    }


def _statement(failing: List[str], never_ran: List[str],
               stale: List[Dict], refusals: int) -> str:
    if refusals:
        return ("%d signals were discarded for falling outside their scope. "
                "That is either a defect or an attempt, and either way it "
                "needs looking at." % refusals)
    if failing:
        return ("Some of this page has not been recomputed because %s failed. "
                "What you see below may be out of date - it is not "
                "necessarily quiet." % ", ".join(sorted(failing)))
    if never_ran:
        return ("%s has not run in the last 24 hours, so anything it produces "
                "is missing rather than empty." % ", ".join(sorted(never_ran)))
    if stale:
        return ("%d view%s on this page were computed some time ago and have "
                "not been refreshed since." % (len(stale),
                                               "" if len(stale) == 1 else "s"))
    return "Everything on this page was computed recently."


def _stale_read_models(db: Session, scope: Scope, *,
                       now: datetime) -> List[Dict[str, Any]]:
    cutoff = now - timedelta(seconds=C.STALE_SECONDS)
    rows = (db.query(AIIntelligenceReadModel)
            .filter(AIIntelligenceReadModel.scope_type == scope.scope_type,
                    AIIntelligenceReadModel.scope_id == (scope.scope_id or ""),
                    AIIntelligenceReadModel.computed_at < cutoff)
            .limit(50).all())
    return [{"view": r.view_key, "window": r.window_key,
             "computed_at": (r.computed_at.isoformat()
                             if r.computed_at else None),
             "last_error": r.last_error}
            for r in rows]


def recent_runs(db: Session, *, limit: int = 100) -> List[Dict[str, Any]]:
    """Platform-wide pass history, for the God diagnostics screen."""
    rows = (db.query(AIIntelligenceRun)
            .order_by(AIIntelligenceRun.started_at.desc()).limit(limit).all())
    return [{
        "id": r.id, "pass": r.pass_key, "scope_type": r.scope_type,
        "scope_id": r.scope_id, "status": r.status,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "duration_ms": r.duration_ms,
        "organizations": r.organizations_scanned,
        "written": r.items_written, "cleared": r.items_cleared,
        "findings": r.findings_written,
        "contradictions": r.contradictions_found,
        "scope_refusals": r.scope_refusals,
        "error": r.error,
    } for r in rows]


def failure_counts(db: Session, *, hours: int = 24) -> Dict[str, int]:
    since = datetime.utcnow() - timedelta(hours=hours)
    q = (db.query(AIIntelligenceRun.pass_key,
                  func.count(AIIntelligenceRun.id))
         .filter(AIIntelligenceRun.started_at >= since,
                 AIIntelligenceRun.status == C.RUN_FAILED)
         .group_by(AIIntelligenceRun.pass_key))
    return {row[0]: int(row[1]) for row in q.all()}
