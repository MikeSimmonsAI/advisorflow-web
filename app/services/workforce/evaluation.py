"""THE EVALUATION HARNESS — repeatable, scored, and stored.

WHAT MAKES THIS DIFFERENT FROM THE TEST SUITE. pytest answers "did anything
break". This answers "how does the engine behave, across which dimensions, and
is that better or worse than last time" — and it writes the answer down, so a
result is a row an operator can open in God Mode months later rather than a
line in somebody's terminal that scrolled away.

Section 39 asks for measurable results across a named list of dimensions. The
dimensions here ARE that list, and the scenarios behind them live in
simulator.py so that the same cases can be run by pytest, by this harness, and
by an operator from a screen without three copies drifting apart.

IT DOES NOT GRADE ITSELF GENEROUSLY. A scenario that raises is a failure. A
scenario that cannot arrange its world is a failure. There is no "skipped"
bucket, because a skipped security case reads as a passing one at a glance and
that is exactly how a suite stops meaning anything.

"UNIT TESTS PASSED" IS NOT A SAFETY ARGUMENT — section 39 says so explicitly.
What is a safety argument is the SECURITY suite below coming back clean: every
adversarial dimension, run against the real gateway, with a scripted planner
that genuinely tries to do the wrong thing.
"""

import json
import logging
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.workforce_models import AIEvaluationResult, AIEvaluationRun
from app.services.workforce import simulator

_log = logging.getLogger(__name__)

# The suites an operator can run. A suite is a named set of dimensions, so a
# "did I break security" question is one button rather than a list somebody
# has to remember.
SUITES = {
    "full": {
        "label": "Everything",
        "dimensions": None,
        "why": "Every scenario in the catalogue.",
    },
    "security": {
        "label": "Security and authority",
        "dimensions": ["tenant_isolation", "prompt_injection", "tool_authority",
                       "kill_switch", "activation", "entitlement", "audit"],
        "why": "The suite Codex will attack. Tenancy, injection, authority, "
               "the kill switch, activation staging and the audit trail.",
    },
    "safety": {
        "label": "Contact safety",
        "dimensions": ["eligibility", "opt_out", "calendar", "handoff",
                       "uncertainty"],
        "why": "Whether a real person could be contacted, booked or ignored "
               "when they should not have been.",
    },
    "reliability": {
        "label": "Reliability",
        "dimensions": ["state_machine", "idempotency", "concurrency",
                       "runaway", "provider_failure", "supervisor"],
        "why": "Duplicate work, races, runaway loops, provider outages and "
               "whether an operator can see any of it.",
    },
    "capability": {
        "label": "Capability",
        "dimensions": ["knowledge", "channel_continuity", "shadow", "voice"],
        "why": "Knowledge grounding, one conversation across channels, shadow "
               "mode, and the voice architecture.",
    },
}

# Dimensions where ANY failure is a stop-the-line result rather than a
# regression to look at on Monday. Named here so the report can say so.
CRITICAL_DIMENSIONS = frozenset({"tenant_isolation", "prompt_injection",
                                 "tool_authority", "eligibility", "opt_out",
                                 "kill_switch", "activation", "calendar"})


def run(db: Session, *, suite_key: str = "full",
        environment: Optional[str] = None, commit_ref: Optional[str] = None,
        triggered_by: Optional[str] = None,
        persist: bool = True) -> Dict:
    """Run a suite and return a scored report. Optionally store it.

    `persist=False` is for pytest, which has its own reporting and does not
    need a row per run. Everything else stores, because an evaluation nobody
    can look up afterwards is an evaluation that has to be re-run to be cited.
    """
    suite = SUITES.get(suite_key)
    if suite is None:
        raise ValueError("Unknown evaluation suite: %s" % suite_key)

    started = datetime.utcnow()
    report = simulator.run_all(db, dimensions=suite["dimensions"])

    critical_failures = [r for r in report["failures"]
                         if r["dimension"] in CRITICAL_DIMENSIONS]
    scored = {
        "suite": suite_key,
        "suite_label": suite["label"],
        "why": suite["why"],
        "started_at": started.isoformat(),
        "ended_at": datetime.utcnow().isoformat(),
        "total": report["total"],
        "passed": report["passed"],
        "failed": report["failed"],
        "pass_rate": (round(report["passed"] / report["total"], 4)
                      if report["total"] else None),
        "by_dimension": [
            {"dimension": dim,
             "passed": slot["passed"], "failed": slot["failed"],
             "critical": dim in CRITICAL_DIMENSIONS,
             "pass_rate": (round(slot["passed"]
                                 / (slot["passed"] + slot["failed"]), 4)
                           if (slot["passed"] + slot["failed"]) else None)}
            for dim, slot in sorted(report["by_dimension"].items())
        ],
        "critical_failures": [
            {"key": r["key"], "dimension": r["dimension"],
             "expected": r["expected"], "actual": r["actual"]}
            for r in critical_failures
        ],
        "failures": [
            {"key": r["key"], "dimension": r["dimension"],
             "expected": r["expected"], "actual": r["actual"],
             "detail": r.get("detail")}
            for r in report["failures"]
        ],
        "verdict": _verdict(report, critical_failures),
        "results": report["results"],
    }

    if persist:
        try:
            scored["run_id"] = _store(db, scored, environment=environment,
                                      commit_ref=commit_ref,
                                      triggered_by=triggered_by)
        except Exception:                                    # noqa: BLE001
            # A STORAGE FAILURE MUST NOT CHANGE THE VERDICT. The evaluation
            # already ran; losing the row is a reporting gap, and swallowing
            # the result to match would be much worse.
            _log.exception("workforce evaluation: could not store the run")
            scored["run_id"] = None
            scored["storage_error"] = True
    return scored


def _verdict(report: Dict, critical_failures: List[Dict]) -> str:
    if critical_failures:
        return "BLOCKED — a critical dimension failed"
    if report["failed"]:
        return "PASSED WITH FAILURES — no critical dimension failed"
    return "PASSED"


def _store(db: Session, scored: Dict, *, environment=None, commit_ref=None,
           triggered_by=None) -> str:
    run_row = AIEvaluationRun(
        suite_key=scored["suite"], environment=environment,
        commit_ref=commit_ref, triggered_by=triggered_by,
        started_at=datetime.fromisoformat(scored["started_at"]),
        ended_at=datetime.fromisoformat(scored["ended_at"]),
        total=scored["total"], passed=scored["passed"],
        failed=scored["failed"],
        summary=json.dumps({"by_dimension": scored["by_dimension"],
                            "verdict": scored["verdict"],
                            "pass_rate": scored["pass_rate"]})[:8000])
    db.add(run_row)
    db.flush()
    for result in scored["results"]:
        db.add(AIEvaluationResult(
            run_id=run_row.id, case_key=result["key"],
            dimension=result["dimension"],
            expected=str(result.get("expected"))[:2000],
            actual=str(result.get("actual"))[:2000],
            passed=bool(result["passed"]),
            detail=json.dumps(result.get("detail") or {}, default=str)[:4000]))
    db.flush()
    return run_row.id


def history(db: Session, *, suite_key: Optional[str] = None,
            limit: int = 25) -> List[Dict]:
    q = db.query(AIEvaluationRun)
    if suite_key:
        q = q.filter(AIEvaluationRun.suite_key == suite_key)
    rows = q.order_by(AIEvaluationRun.started_at.desc()).limit(limit).all()
    out = []
    for row in rows:
        try:
            summary = json.loads(row.summary or "{}")
        except (ValueError, TypeError):
            summary = {}
        out.append({
            "id": row.id, "suite": row.suite_key,
            "environment": row.environment, "commit": row.commit_ref,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "total": row.total, "passed": row.passed, "failed": row.failed,
            "verdict": summary.get("verdict"),
            "pass_rate": summary.get("pass_rate"),
        })
    return out


def detail(db: Session, run_id: str) -> Dict:
    row = (db.query(AIEvaluationRun)
           .filter(AIEvaluationRun.id == run_id).first())
    if row is None:
        return {}
    results = (db.query(AIEvaluationResult)
               .filter(AIEvaluationResult.run_id == run_id)
               .order_by(AIEvaluationResult.dimension.asc(),
                         AIEvaluationResult.case_key.asc()).all())
    try:
        summary = json.loads(row.summary or "{}")
    except (ValueError, TypeError):
        summary = {}
    return {
        "id": row.id, "suite": row.suite_key, "environment": row.environment,
        "commit": row.commit_ref, "total": row.total, "passed": row.passed,
        "failed": row.failed, "summary": summary,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "ended_at": row.ended_at.isoformat() if row.ended_at else None,
        "results": [
            {"case": r.case_key, "dimension": r.dimension,
             "passed": r.passed, "expected": r.expected, "actual": r.actual}
            for r in results
        ],
    }


def describe_suites() -> List[Dict]:
    return [{"key": k, "label": v["label"], "why": v["why"],
             "dimensions": v["dimensions"] or list(simulator.DIMENSIONS),
             "case_count": len([s for s in simulator.SCENARIOS
                                if v["dimensions"] is None
                                or s["dimension"] in v["dimensions"]])}
            for k, v in SUITES.items()]
