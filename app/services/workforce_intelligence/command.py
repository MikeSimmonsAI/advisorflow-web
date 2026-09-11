"""THE WORKFORCE COMMAND CENTER - one call, and Needs Attention is first.

WHAT THIS IS AND IS NOT. It is the operational management surface for ONE
customer's AI workforce: who is working, what is queued, what is waiting, what
is stuck, what needs a person, what is costing effort, what changed. It is not
T10's Executive Command Center, which is about a whole business rather than
about its AI employees, and nothing here tries to be.

THE SHAPE IS THE ARGUMENT. `attention` comes first in this payload because it
comes first on the screen, and it comes first on the screen because a manager
opening this page has one question: what needs me. Charts answer "how are we
doing", which is a different question asked less often and never urgently.
Section 21 says do not bury Needs Attention and do not build a wall of charts,
and the ordering here is where that is decided rather than in the CSS.

ONE CALL ON PURPOSE. A header rendered from one answer and a list rendered
from another, taken a moment apart, is how a customer ends up asking why the
count disagrees with the rows. T8's own `workforce_summary` makes the same
choice for the same reason.

EVERY NUMBER CARRIES ITS AGE. The payload's `freshness` block says when this
was computed, whether anything has happened since, and whether the last
computation failed - so a screen can never present a stale figure as live.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.services.ai_deployment import activation as t8_activation
from app.services.ai_operations import flags as t7_flags
from app.services.workforce import activation as t6_activation
from app.services.workforce_intelligence import attention as t9_attention
from app.services.workforce_intelligence import collect
from app.services.workforce_intelligence import constants as C
from app.services.workforce_intelligence import cost as t9_cost
from app.services.workforce_intelligence import findings as t9_findings
from app.services.workforce_intelligence import metrics as t9_metrics
from app.services.workforce_intelligence import observability as t9_obs
from app.services.workforce_intelligence import readmodel as t9_readmodel
from app.services.workforce_intelligence import reconciliation as t9_rec
from app.services.workforce_intelligence import review as t9_review
from app.services.workforce_intelligence import scorecards as t9_scorecards
from app.services.workforce_intelligence.scope import Scope

_log = logging.getLogger(__name__)


def refresh(db: Session, scope: Scope, *,
            thresholds: Optional[Dict] = None,
            window_key: str = C.DEFAULT_WINDOW,
            now: Optional[datetime] = None) -> Dict[str, Any]:
    """Run every pass for one scope, each recorded whether it works or not.

    ONE PASS FAILING DOES NOT STOP THE OTHERS, and the result says which
    failed rather than returning a partial answer that looks complete. The
    attention queue is refreshed first because it is the one a manager acts
    on; findings and reconciliation are worth having and are not urgent.
    """
    now = now or datetime.utcnow()
    results: Dict[str, Any] = {}
    failures: List[str] = []

    for pass_key, fn in (
        (C.P_ATTENTION, lambda: t9_attention.refresh(
            db, scope, thresholds=thresholds, now=now)),
        (C.P_FINDINGS, lambda: t9_findings.refresh(
            db, scope, window_key=window_key, thresholds=thresholds, now=now)),
        (C.P_RECONCILE, lambda: t9_rec.refresh(db, scope, now=now)),
    ):
        try:
            with t9_obs.pass_run(db, scope, pass_key) as stats:
                outcome = fn()
                results[pass_key] = outcome
                stats.update({k: v for k, v in outcome.items()
                              if isinstance(v, (int, float))})
        except Exception as exc:                             # noqa: BLE001
            failures.append(pass_key)
            results[pass_key] = {"failed": True, "error": str(exc)}
            _log.exception("t9 command: pass %s failed for %s/%s", pass_key,
                           scope.scope_type, scope.scope_id)

    # A REFRESH INVALIDATES THE MATERIALISED VIEWS IT FEEDS. Leaving them in
    # place would mean a manager who pressed refresh still saw the previous
    # numbers, which is worse than not offering the button.
    t9_readmodel.invalidate(db, scope)
    return {"refreshed_at": now.isoformat(), "passes": results,
            "failed_passes": failures,
            "complete": not failures}


def overview(db: Session, scope: Scope, *,
             window_key: str = C.DEFAULT_WINDOW,
             thresholds: Optional[Dict] = None,
             now: Optional[datetime] = None,
             include_scorecards: bool = True) -> Dict[str, Any]:
    """Everything the command centre renders, in the order it renders it."""
    now = now or datetime.utcnow()
    th = thresholds or C.thresholds()

    attention = t9_attention.queue(db, scope, now=now)
    per_employee = t9_metrics.employee_metrics(db, scope,
                                               window_key=window_key,
                                               now=now, thresholds=th)
    totals = t9_metrics.totals(per_employee,
                               minimum=int(th["minimum_denominator"]),
                               window_key=window_key)
    work = collect.work_counts_by_state(db, scope)
    threads = collect.thread_counts_by_state(db, scope)
    deployments = collect.deployments(db, scope, include_retired=False)
    employees = collect.employees(db, scope)
    reviews = t9_review.queue(db, scope, limit=50, now=now)
    contradictions = t9_rec.listing(db, scope, limit=50)
    findings = t9_findings.listing(db, scope, limit=50)
    costs = t9_cost.report(db, scope, window_key=window_key, thresholds=th,
                           now=now)
    handoffs = collect.handoff_rollup(db, scope)

    scorecards = None
    if include_scorecards:
        scorecards = t9_scorecards.build(db, scope, window_key=window_key,
                                         now=now, thresholds=th)

    return {
        "generated_at": now.isoformat(),
        "scope": scope.as_dict(),
        "window": window_key,
        # FIRST, ON PURPOSE.
        "attention": attention,
        "headline": attention["headline"],
        "workforce": _workforce_block(employees, deployments, work, threads),
        "waiting": _waiting_block(work, threads, reviews, handoffs),
        "outcomes": totals,
        "findings": findings,
        "reconciliation": contradictions,
        "costs": costs,
        "scorecards": scorecards,
        "employees": _employee_rows(employees, deployments, per_employee,
                                    attention),
        "platform_state": platform_state(db),
        "thresholds": th,
        "threshold_labels": dict(C.THRESHOLD_LABELS),
        "read_only": scope.read_only,
        "self_check": t9_obs.health(db, scope, now=now),
    }


def _workforce_block(employees, deployments, work, threads) -> Dict[str, Any]:
    live = [d for d in deployments if d.state in C.DEPLOY_LIVE_STATES]
    paused = [d for d in deployments if d.state == C.DEPLOY_PAUSED]
    suspended = [d for d in deployments if d.state == C.DEPLOY_SUSPENDED]
    return {
        "employees": len(employees),
        "deployments": len(deployments),
        "working": len(live),
        "paused": len(paused),
        "stopped": len(suspended),
        "by_activation_stage": _count(e.activation_state for e in employees),
        "by_job": _count(e.job_role for e in employees),
        "by_deployment_state": _count(d.state for d in deployments),
        "work_by_state": work,
        "conversations_by_state": threads,
        # THE SENTENCE BEFORE THE NUMBERS, because "why is nothing happening"
        # is the question a manager asks first and a badge does not answer.
        "statement": _workforce_statement(deployments, live, suspended),
    }


def _workforce_statement(deployments, live, suspended) -> str:
    if not deployments:
        return "You have not hired any AI employees yet."
    if suspended and not live:
        return ("%d of your AI employees are stopped because of their account "
                "standing." % len(suspended))
    if not live:
        return ("You have %d AI employee%s set up. None of them is working."
                % (len(deployments), "" if len(deployments) == 1 else "s"))
    return ("%d of your %d AI employees %s working."
            % (len(live), len(deployments),
               "is" if len(live) == 1 else "are"))


def _waiting_block(work, threads, reviews, handoffs) -> Dict[str, Any]:
    """What is queued, waiting, or sitting on somebody's desk.

    ONE PLACE FOR "WHAT IS OUTSTANDING", collected from three layers that each
    hold part of it. A manager does not distinguish between a work item
    waiting and a conversation waiting; they see one backlog.
    """
    open_handoffs = sum(int(v.get("open", 0) or 0) for v in handoffs.values())
    accepted = sum(int(v.get("accepted", 0) or 0) for v in handoffs.values())
    return {
        "work_queued": sum(int(work.get(s, 0) or 0)
                           for s in C.WORK_CLAIMABLE_STATES),
        "conversations_waiting": int(threads.get(C.THREAD_WAITING, 0) or 0),
        "conversations_blocked": (int(threads.get(C.THREAD_BLOCKED, 0) or 0)
                                  + int(threads.get(C.THREAD_FAILED, 0) or 0)),
        "review_required": reviews["undecided"],
        "review_total": reviews["total"],
        "handoffs_open": open_handoffs,
        "handoffs_accepted": accepted,
        "needs_a_person": (reviews["undecided"] + open_handoffs
                           + int(threads.get(C.THREAD_HANDOFF, 0) or 0)),
    }


def _employee_rows(employees, deployments, per_employee,
                   attention) -> List[Dict[str, Any]]:
    """The team list, with each employee's own share of the attention queue."""
    by_employee = {d.employee_id: d for d in deployments if d.employee_id}
    attention_counts: Dict[str, int] = {}
    worst: Dict[str, str] = {}
    for item in attention["items"]:
        emp = item.get("employee_id")
        if not emp:
            continue
        attention_counts[emp] = attention_counts.get(emp, 0) + 1
        if emp not in worst:
            worst[emp] = item["severity"]

    rows = []
    for emp in employees:
        dep = by_employee.get(emp.id)
        values = (per_employee.get(emp.id) or {}).get("values", {})
        rows.append({
            "employee_id": emp.id,
            "name": emp.name,
            "job_role": emp.job_role,
            "status": emp.status,
            "activation_state": emp.activation_state,
            "paused": emp.paused_at is not None,
            "deployment_id": getattr(dep, "id", None),
            "deployment_state": getattr(dep, "state", None),
            "commercial_state": getattr(dep, "commercial_state", None),
            "readiness_state": getattr(dep, "readiness_state", None),
            "working": bool(dep is not None
                            and dep.state in C.DEPLOY_LIVE_STATES),
            "attention_items": attention_counts.get(emp.id, 0),
            "worst_severity": worst.get(emp.id),
            "appointments": (values.get("appointments_booked")
                             or {}).get("value"),
            "qualified": (values.get("qualified") or {}).get("value"),
            "handoffs": (values.get("handoffs") or {}).get("value"),
            "messages_sent": (values.get("messages_sent") or {}).get("value"),
            "response_rate": (values.get("response_rate") or {}).get("value"),
            "response_rate_note": (values.get("response_rate")
                                   or {}).get("note"),
        })
    rows.sort(key=lambda r: (-(r["attention_items"] or 0), r["name"] or ""))
    return rows


def _count(values) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for value in values:
        key = value or "unknown"
        out[key] = out.get(key, 0) + 1
    return out


def platform_state(db: Session) -> Dict[str, Any]:
    """The dark-launch switches, quoted verbatim from the layers that own them.

    T9 DOES NOT TURN ANYTHING ON AND CANNOT. This block is read-only and
    exists because "why is nothing sending" has a factual answer, and a
    management screen that could not show it would send people to a log file.
    Every value comes from T6's or T7's own module, so a screen and the engine
    cannot disagree about whether the workforce is switched on.
    """
    try:
        ops = t7_flags.state()
    except Exception:                                        # noqa: BLE001
        ops = {"unavailable": True}
    try:
        capability = t8_activation.operational_capability(db)
    except Exception:                                        # noqa: BLE001
        capability = {"unavailable": True}
    try:
        platform_activation = t6_activation.scope_report(
            db, t6_activation.SCOPE_PLATFORM, t6_activation.PLATFORM_SCOPE_ID)
    except Exception:                                        # noqa: BLE001
        platform_activation = {"unavailable": True}
    return {
        "operations": ops,
        "deployment_capability": capability,
        "platform_activation": platform_activation,
        "note": ("These switches are read here and changed elsewhere. AI "
                 "Workforce Command cannot start the workforce, enable a "
                 "channel, enable voice or switch on live sending."),
    }


def cached_overview(db: Session, scope: Scope, *,
                    window_key: str = C.DEFAULT_WINDOW,
                    thresholds: Optional[Dict] = None,
                    force: bool = False,
                    now: Optional[datetime] = None) -> Dict[str, Any]:
    """The overview, materialised, with its freshness attached.

    The scorecards are deliberately excluded from the cached payload and
    fetched by their own screen: they are the most expensive part and the
    least urgent, and including them would make the page a manager opens
    every ten minutes the slowest thing in the product.
    """
    now = now or datetime.utcnow()
    return t9_readmodel.get_or_build(
        db, scope, C.V_COMMAND_CENTER,
        lambda: overview(db, scope, window_key=window_key,
                         thresholds=thresholds, now=now,
                         include_scorecards=False),
        window_key=window_key, force=force, now=now)
