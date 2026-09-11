"""AUTHORITATIVE READS - T6, T7 and T8's own rows, scoped, never widened.

WHY A COLLECTION LAYER AND NOT QUERIES WHERE THEY ARE NEEDED.

Two reasons, and the second one is the one that actually bites.

The first is section 23. A command centre that asks "how many objectives are
active" per employee, on a customer with forty employees, is forty queries
before the page has drawn a single number - and then the scorecard screen asks
again, per employee, per metric. Every function here returns a GROUPED answer
for the whole scope in one statement, keyed by employee, so a caller that
needs per-employee numbers reads a dict instead of issuing a query in a loop.

The second is that a query written at its call site is a query whose tenant
filter was written at its call site. Every statement in this file goes through
`scope.apply`, and a reviewer checking tenant isolation reads one file rather
than auditing every screen. The functions take a Scope and have no
`organization_id` parameter at all, so there is nothing to pass wrongly.

NOTHING HERE INTERPRETS. These functions return counts, rows and timestamps.
What any of it MEANS is `metrics`, `findings` and `scorecards`, and keeping
that boundary is what makes it possible to say which numbers are facts.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.ai_deployment_models import AIEmployeeDeployment
from app.models.ai_operations_models import (AICommunication,
                                             AIConversationThread,
                                             AIInboundEvent, AIOpsAction,
                                             AIOpsCounter, AIScheduledAction)
from app.models.workforce_models import (AIEmployee, AIEmployeeRun, AIHandoff,
                                         AIPerformanceEntry,
                                         AISupervisorEvent, AIToolExecution,
                                         AIWorkItem)
from app.services.ai_operations import constants as O
from app.services.workforce import constants as W
from app.services.workforce_intelligence import constants as C
from app.services.workforce_intelligence.scope import Scope

_log = logging.getLogger(__name__)


def since_for(window_key: str, now: Optional[datetime] = None) -> datetime:
    now = now or datetime.utcnow()
    return now - timedelta(days=C.window_days(window_key))


# ---------------------------------------------------------------------------
# THE ACTORS
# ---------------------------------------------------------------------------


def employees(db: Session, scope: Scope, *,
              employee_ids: Optional[Sequence[str]] = None,
              limit: int = 2000) -> List[AIEmployee]:
    q = scope.apply(db.query(AIEmployee), AIEmployee.organization_id)
    if employee_ids:
        q = q.filter(AIEmployee.id.in_(list(employee_ids)))
    return q.order_by(AIEmployee.name.asc()).limit(limit).all()


def employee_index(db: Session, scope: Scope,
                   **kw) -> Dict[str, AIEmployee]:
    return {e.id: e for e in employees(db, scope, **kw)}


def deployments(db: Session, scope: Scope, *,
                include_retired: bool = True,
                limit: int = 2000) -> List[AIEmployeeDeployment]:
    q = scope.apply(db.query(AIEmployeeDeployment),
                    AIEmployeeDeployment.organization_id)
    if not include_retired:
        q = q.filter(AIEmployeeDeployment.state != C.DEPLOY_RETIRED)
    return q.order_by(AIEmployeeDeployment.created_at.asc()).limit(limit).all()


def deployment_by_employee(db: Session, scope: Scope
                           ) -> Dict[str, AIEmployeeDeployment]:
    """The deployment that owns each actor, for joining without a per-row query.

    An employee with no deployment row is possible and is not an error: T6 can
    create an actor directly, and T8's rows begin at `selected` before an
    actor exists at all. Callers treat a miss as "no commercial arrangement
    recorded", never as zero.
    """
    out: Dict[str, AIEmployeeDeployment] = {}
    for dep in deployments(db, scope):
        if dep.employee_id:
            out[dep.employee_id] = dep
    return out


# ---------------------------------------------------------------------------
# WORK (T6)
# ---------------------------------------------------------------------------


def work_counts_by_state(db: Session, scope: Scope) -> Dict[str, int]:
    q = scope.apply(db.query(AIWorkItem.state, func.count(AIWorkItem.id)),
                    AIWorkItem.organization_id)
    # Every state T6 declares, zeroed, so a screen renders the same rows on a
    # quiet day as on a busy one. An absent key and a zero are different
    # things to a reader, and only one of them is true here.
    out = {s: 0 for s in W.ALL_STATES}
    for state, n in q.group_by(AIWorkItem.state).all():
        out[state or "unknown"] = int(n)
    return out


def work_counts_by_employee_state(db: Session, scope: Scope
                                  ) -> Dict[Tuple[str, str], int]:
    """(employee_id, state) -> count, for the whole scope, in one statement."""
    q = scope.apply(
        db.query(AIWorkItem.employee_id, AIWorkItem.state,
                 func.count(AIWorkItem.id)),
        AIWorkItem.organization_id)
    return {(emp or "", state or "unknown"): int(n)
            for emp, state, n in q.group_by(AIWorkItem.employee_id,
                                            AIWorkItem.state).all()}


def open_work_items(db: Session, scope: Scope, *,
                    states: Optional[Sequence[str]] = None,
                    employee_id: Optional[str] = None,
                    limit: int = 500) -> List[AIWorkItem]:
    q = scope.apply(db.query(AIWorkItem), AIWorkItem.organization_id)
    if states:
        q = q.filter(AIWorkItem.state.in_(list(states)))
    if employee_id:
        q = q.filter(AIWorkItem.employee_id == employee_id)
    return q.order_by(AIWorkItem.updated_at.desc()).limit(limit).all()


def runs(db: Session, scope: Scope, since: datetime, *,
         limit: int = 5000) -> List[AIEmployeeRun]:
    q = scope.apply(db.query(AIEmployeeRun), AIEmployeeRun.organization_id)
    return (q.filter(AIEmployeeRun.started_at >= since)
            .order_by(AIEmployeeRun.started_at.desc()).limit(limit).all())


def run_rollup(db: Session, scope: Scope, since: datetime) -> Dict[str, Dict]:
    """Per-employee run totals. Cost is SUMMED, never averaged into existence.

    `cost_rows` counts the runs that actually carried an estimate. A caller
    comparing it with `total` is the only way to know whether a cost figure
    covers the whole period or a fraction of it - which is the difference
    between a number and a guess, and section 10 says the guess must not be
    made.
    """
    q = scope.apply(
        db.query(AIEmployeeRun.employee_id,
                 AIEmployeeRun.status,
                 func.count(AIEmployeeRun.id),
                 func.sum(AIEmployeeRun.iterations),
                 func.sum(AIEmployeeRun.tool_calls),
                 func.sum(AIEmployeeRun.denied_tool_calls),
                 func.sum(AIEmployeeRun.estimated_cost_usd),
                 func.count(AIEmployeeRun.estimated_cost_usd),
                 func.sum(AIEmployeeRun.duration_ms)),
        AIEmployeeRun.organization_id).filter(
            AIEmployeeRun.started_at >= since)
    out: Dict[str, Dict] = {}
    for (emp, status, n, iters, calls, denied, cost, cost_rows,
         duration) in q.group_by(AIEmployeeRun.employee_id,
                                 AIEmployeeRun.status).all():
        slot = out.setdefault(emp or "", {
            "total": 0, "by_status": {}, "iterations": 0, "tool_calls": 0,
            "denied_tool_calls": 0, "cost_usd": 0.0, "cost_rows": 0,
            "duration_ms": 0})
        slot["total"] += int(n or 0)
        slot["by_status"][status or "unknown"] = int(n or 0)
        slot["iterations"] += int(iters or 0)
        slot["tool_calls"] += int(calls or 0)
        slot["denied_tool_calls"] += int(denied or 0)
        slot["cost_usd"] += float(cost or 0)
        slot["cost_rows"] += int(cost_rows or 0)
        slot["duration_ms"] += int(duration or 0)
    return out


def tool_rollup(db: Session, scope: Scope, since: datetime) -> Dict[str, Dict]:
    """Per-employee tool outcomes and the refusal codes behind them."""
    q = scope.apply(
        db.query(AIToolExecution.employee_id, AIToolExecution.decision,
                 AIToolExecution.status, AIToolExecution.denial_code,
                 func.count(AIToolExecution.id)),
        AIToolExecution.organization_id).filter(
            AIToolExecution.created_at >= since)
    out: Dict[str, Dict] = {}
    for emp, decision, status, denial, n in q.group_by(
            AIToolExecution.employee_id, AIToolExecution.decision,
            AIToolExecution.status, AIToolExecution.denial_code).all():
        slot = out.setdefault(emp or "", {"allowed": 0, "denied": 0,
                                          "errors": 0, "denials": {}})
        count = int(n or 0)
        if decision == "denied":
            slot["denied"] += count
            key = denial or "unknown"
            slot["denials"][key] = slot["denials"].get(key, 0) + count
        else:
            slot["allowed"] += count
        if status == "error":
            slot["errors"] += count
    return out


def handoffs(db: Session, scope: Scope, *,
             statuses: Sequence[str] = ("open", "accepted"),
             limit: int = 500) -> List[AIHandoff]:
    q = scope.apply(db.query(AIHandoff), AIHandoff.organization_id)
    if statuses:
        q = q.filter(AIHandoff.status.in_(list(statuses)))
    return q.order_by(AIHandoff.created_at.asc()).limit(limit).all()


def handoff_rollup(db: Session, scope: Scope) -> Dict[str, Dict[str, int]]:
    q = scope.apply(
        db.query(AIHandoff.employee_id, AIHandoff.status,
                 func.count(AIHandoff.id)),
        AIHandoff.organization_id)
    out: Dict[str, Dict[str, int]] = {}
    for emp, status, n in q.group_by(AIHandoff.employee_id,
                                     AIHandoff.status).all():
        out.setdefault(emp or "", {})[status or "unknown"] = int(n or 0)
    return out


def performance_rollup(db: Session, scope: Scope, since_day: str
                       ) -> Dict[str, Dict[str, int]]:
    """The T6 performance ledger, per employee, per metric, in one statement.

    THE LEDGER IS THE SOURCE, not a re-count of work items. T6's own module
    says why: a queue that was cleared, reassigned or re-run changes a
    re-counted number retrospectively, and the ledger only increments. T9
    reading anything else would mean the two screens disagreed about last
    month.
    """
    q = scope.apply(
        db.query(AIPerformanceEntry.employee_id,
                 AIPerformanceEntry.metric_key,
                 func.sum(AIPerformanceEntry.value)),
        AIPerformanceEntry.organization_id).filter(
            AIPerformanceEntry.metric_date >= since_day)
    out: Dict[str, Dict[str, int]] = {}
    for emp, key, total in q.group_by(AIPerformanceEntry.employee_id,
                                      AIPerformanceEntry.metric_key).all():
        out.setdefault(emp or "", {})[key] = int(total or 0)
    return out


def performance_rollup_window(db: Session, scope: Scope, from_day: str,
                              to_day: str) -> Dict[str, Dict[str, int]]:
    """The ledger, bounded on BOTH sides. The period-comparison primitive.

    WHY THE LEDGER AND NOT A RE-QUERY OF EVENTS for trend. `metric_date` is a
    plain YYYY-MM-DD string, so "the seven days before the seven days before
    now" is a range comparison rather than a second pass over event tables
    with a fabricated upper bound. It is also the only source in the platform
    that cannot change retrospectively, which is what a trend needs: a
    comparison whose earlier half moves is not a comparison.

    The upper bound is EXCLUSIVE, so two adjacent windows cannot both count
    the same day - which is how a period comparison quietly reports growth
    that is one day counted twice.
    """
    q = scope.apply(
        db.query(AIPerformanceEntry.employee_id,
                 AIPerformanceEntry.metric_key,
                 func.sum(AIPerformanceEntry.value)),
        AIPerformanceEntry.organization_id).filter(
            AIPerformanceEntry.metric_date >= from_day,
            AIPerformanceEntry.metric_date < to_day)
    out: Dict[str, Dict[str, int]] = {}
    for emp, key, total in q.group_by(AIPerformanceEntry.employee_id,
                                      AIPerformanceEntry.metric_key).all():
        out.setdefault(emp or "", {})[key] = int(total or 0)
    return out


def performance_series(db: Session, scope: Scope, *, metric_key: str,
                       since_day: str,
                       employee_id: Optional[str] = None
                       ) -> List[Dict[str, Any]]:
    q = scope.apply(
        db.query(AIPerformanceEntry.metric_date,
                 func.sum(AIPerformanceEntry.value)),
        AIPerformanceEntry.organization_id).filter(
            AIPerformanceEntry.metric_key == metric_key,
            AIPerformanceEntry.metric_date >= since_day)
    if employee_id:
        q = q.filter(AIPerformanceEntry.employee_id == employee_id)
    rows = q.group_by(AIPerformanceEntry.metric_date).all()
    return [{"date": d, "value": int(v or 0)}
            for d, v in sorted(rows, key=lambda r: r[0])]


def supervisor_events(db: Session, scope: Scope, since: datetime, *,
                      limit: int = 200) -> List[AISupervisorEvent]:
    q = scope.apply(db.query(AISupervisorEvent),
                    AISupervisorEvent.organization_id)
    return (q.filter(AISupervisorEvent.created_at >= since)
            .order_by(AISupervisorEvent.created_at.desc()).limit(limit).all())


# ---------------------------------------------------------------------------
# OPERATIONS (T7)
# ---------------------------------------------------------------------------


def thread_counts_by_state(db: Session, scope: Scope, *,
                           open_only: bool = True) -> Dict[str, int]:
    q = scope.apply(
        db.query(AIConversationThread.state,
                 func.count(AIConversationThread.id)),
        AIConversationThread.organization_id)
    if open_only:
        q = q.filter(AIConversationThread.status == "open")
    out = {s: 0 for s in O.ALL_COMM_STATES}
    for state, n in q.group_by(AIConversationThread.state).all():
        out[state or "unknown"] = int(n or 0)
    return out


def thread_counts_by_employee_state(db: Session, scope: Scope, *,
                                    open_only: bool = True
                                    ) -> Dict[Tuple[str, str], int]:
    q = scope.apply(
        db.query(AIConversationThread.employee_id,
                 AIConversationThread.state,
                 func.count(AIConversationThread.id)),
        AIConversationThread.organization_id)
    if open_only:
        q = q.filter(AIConversationThread.status == "open")
    return {(emp or "", state or "unknown"): int(n or 0)
            for emp, state, n in q.group_by(
                AIConversationThread.employee_id,
                AIConversationThread.state).all()}


def threads(db: Session, scope: Scope, *,
            states: Optional[Sequence[str]] = None,
            employee_id: Optional[str] = None,
            open_only: bool = True,
            limit: int = 500) -> List[AIConversationThread]:
    q = scope.apply(db.query(AIConversationThread),
                    AIConversationThread.organization_id)
    if open_only:
        q = q.filter(AIConversationThread.status == "open")
    if states:
        q = q.filter(AIConversationThread.state.in_(list(states)))
    if employee_id:
        q = q.filter(AIConversationThread.employee_id == employee_id)
    return (q.order_by(AIConversationThread.updated_at.desc())
            .limit(limit).all())


def ops_action_rollup(db: Session, scope: Scope, since: datetime
                      ) -> Dict[str, Dict]:
    """Per-employee operational decisions: allowed, refused, and why.

    THIS IS WHERE "the AI stopped working" IS ANSWERED. A refusal carries a
    code, and one code repeated is almost always configuration rather than
    anything the engine did wrong - a channel switched off, a feature removed,
    an entitlement lapsed.
    """
    q = scope.apply(
        db.query(AIOpsAction.employee_id, AIOpsAction.decision,
                 AIOpsAction.denial_code, AIOpsAction.status,
                 AIOpsAction.operation, func.count(AIOpsAction.id)),
        AIOpsAction.organization_id).filter(AIOpsAction.created_at >= since)
    out: Dict[str, Dict] = {}
    for emp, decision, denial, status, operation, n in q.group_by(
            AIOpsAction.employee_id, AIOpsAction.decision,
            AIOpsAction.denial_code, AIOpsAction.status,
            AIOpsAction.operation).all():
        slot = out.setdefault(emp or "", {"allowed": 0, "denied": 0,
                                          "errors": 0, "denials": {},
                                          "operations": {}})
        count = int(n or 0)
        if decision == "denied":
            slot["denied"] += count
            key = denial or "unknown"
            slot["denials"][key] = slot["denials"].get(key, 0) + count
        else:
            slot["allowed"] += count
        if status == "error":
            slot["errors"] += count
        if operation:
            slot["operations"][operation] = (
                slot["operations"].get(operation, 0) + count)
    return out


def communication_rollup(db: Session, scope: Scope, since: datetime
                         ) -> Dict[str, Dict]:
    """Per-employee message outcomes, by channel, direction and provider verdict.

    A FAILED MESSAGE IS NOT A CONVERSATION, and this is the shape that keeps
    that true: the provider outcome is carried through rather than collapsed
    into a send count, so `metrics` can count what was accepted and leave what
    was rejected out of the numerator.
    """
    q = scope.apply(
        db.query(AICommunication.employee_id, AICommunication.direction,
                 AICommunication.channel, AICommunication.state,
                 AICommunication.provider_outcome,
                 AICommunication.simulated,
                 func.count(AICommunication.id),
                 func.sum(AICommunication.voice_seconds),
                 func.sum(AICommunication.estimated_cost_usd),
                 func.count(AICommunication.estimated_cost_usd)),
        AICommunication.organization_id).filter(
            AICommunication.created_at >= since)
    out: Dict[str, Dict] = {}
    for (emp, direction, channel, state, outcome, simulated, n, secs, cost,
         cost_rows) in q.group_by(
            AICommunication.employee_id, AICommunication.direction,
            AICommunication.channel, AICommunication.state,
            AICommunication.provider_outcome,
            AICommunication.simulated).all():
        slot = out.setdefault(emp or "", {
            "outbound": 0, "inbound": 0, "by_channel": {}, "by_state": {},
            "by_provider_outcome": {}, "simulated": 0, "real": 0,
            "voice_seconds": 0, "cost_usd": 0.0, "cost_rows": 0})
        count = int(n or 0)
        if direction == O.INBOUND:
            slot["inbound"] += count
        else:
            slot["outbound"] += count
        slot["by_channel"][channel or "unknown"] = (
            slot["by_channel"].get(channel or "unknown", 0) + count)
        slot["by_state"][state or "unknown"] = (
            slot["by_state"].get(state or "unknown", 0) + count)
        slot["by_provider_outcome"][outcome or "unknown"] = (
            slot["by_provider_outcome"].get(outcome or "unknown", 0) + count)
        slot["simulated" if simulated else "real"] += count
        slot["voice_seconds"] += int(secs or 0)
        slot["cost_usd"] += float(cost or 0)
        slot["cost_rows"] += int(cost_rows or 0)
    return out


def scheduled_actions(db: Session, scope: Scope, *,
                      statuses: Sequence[str] = ("pending", "claimed"),
                      due_before: Optional[datetime] = None,
                      limit: int = 500) -> List[AIScheduledAction]:
    q = scope.apply(db.query(AIScheduledAction),
                    AIScheduledAction.organization_id)
    if statuses:
        q = q.filter(AIScheduledAction.status.in_(list(statuses)))
    if due_before is not None:
        q = q.filter(AIScheduledAction.scheduled_for <= due_before)
    return (q.order_by(AIScheduledAction.scheduled_for.asc())
            .limit(limit).all())


def unrouted_inbound(db: Session, scope: Scope, since: datetime, *,
                     limit: int = 200) -> List[AIInboundEvent]:
    """Replies that could not be placed.

    SCOPED LIKE EVERYTHING ELSE HERE. T7's own `unrouted_inbound` is
    deliberately answerable with no organization at all, because an event
    whose tenant could not be established has none - but that view is God
    Mode's, and a customer-scoped management screen must not inherit it. A
    manager sees the unrouted events that DID resolve to their organization;
    the ones that resolved to nobody stay on the platform view.
    """
    q = scope.apply(db.query(AIInboundEvent), AIInboundEvent.organization_id)
    return (q.filter(AIInboundEvent.routed.is_(False),
                     AIInboundEvent.created_at >= since)
            .order_by(AIInboundEvent.created_at.desc()).limit(limit).all())


def counter_rollup(db: Session, scope: Scope, since_day: str
                   ) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """T7's operational counters: (scope_type, scope_id) -> metric totals.

    Both the count and the money are summed, because T7 records them on the
    same row and separating them here would make a cost figure that no single
    table agreed with.
    """
    q = scope.apply(
        db.query(AIOpsCounter.scope_type, AIOpsCounter.scope_id,
                 AIOpsCounter.metric_key,
                 func.sum(AIOpsCounter.value),
                 func.sum(AIOpsCounter.value_usd),
                 func.count(AIOpsCounter.id)),
        AIOpsCounter.organization_id).filter(
            AIOpsCounter.metric_date >= since_day)
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for (stype, sid, key, value, usd, rows) in q.group_by(
            AIOpsCounter.scope_type, AIOpsCounter.scope_id,
            AIOpsCounter.metric_key).all():
        slot = out.setdefault((stype or "", sid or ""),
                              {"metrics": {}, "usd": {}, "rows": 0})
        slot["metrics"][key] = int(value or 0)
        slot["usd"][key] = float(usd or 0)
        slot["rows"] += int(rows or 0)
    return out


# ---------------------------------------------------------------------------
# FRESHNESS
# ---------------------------------------------------------------------------


# The authoritative event tables whose newest row decides whether a computed
# answer is still current. Each entry is (label, model, timestamp column).
#
# WHY A LIST AND NOT ONE TABLE. Because "nothing has happened" is only true if
# nothing happened ANYWHERE the management view reads from. A watermark taken
# from work items alone would report a workforce as quiet while its
# conversations were moving, and the screen would say "up to date" over a
# number that was not.
_WATERMARK_SOURCES = (
    ("work_items", AIWorkItem, AIWorkItem.updated_at),
    ("runs", AIEmployeeRun, AIEmployeeRun.started_at),
    ("tool_executions", AIToolExecution, AIToolExecution.created_at),
    ("threads", AIConversationThread, AIConversationThread.updated_at),
    ("communications", AICommunication, AICommunication.created_at),
    ("ops_actions", AIOpsAction, AIOpsAction.created_at),
    ("handoffs", AIHandoff, AIHandoff.created_at),
    ("deployments", AIEmployeeDeployment, AIEmployeeDeployment.updated_at),
)


def watermark(db: Session, scope: Scope) -> Optional[datetime]:
    """The newest authoritative event this scope can see.

    Compared against a read model's `source_watermark`, this is what separates
    "computed a while ago and still correct" from "the aggregation has not
    run". Section 13 asks a screen to be able to say which, and a single
    `computed_at` cannot.
    """
    newest: Optional[datetime] = None
    for _label, model, column in _WATERMARK_SOURCES:
        try:
            q = scope.apply(db.query(func.max(column)),
                            model.organization_id)
            value = q.scalar()
        except Exception:                                    # noqa: BLE001
            # A table that is not present on this branch must not take the
            # whole freshness answer down with it - the read model simply
            # reports what it could see.
            _log.debug("t9 watermark: source %s unavailable", _label)
            continue
        if value is not None and (newest is None or value > newest):
            newest = value
    return newest


def counts_snapshot(db: Session, scope: Scope) -> Dict[str, int]:
    """The three headline counts, cheaply, for a header that must not lie."""
    return {
        "employees": int(scope.apply(db.query(func.count(AIEmployee.id)),
                                     AIEmployee.organization_id).scalar() or 0),
        "deployments": int(scope.apply(
            db.query(func.count(AIEmployeeDeployment.id)),
            AIEmployeeDeployment.organization_id).filter(
                AIEmployeeDeployment.state != C.DEPLOY_RETIRED).scalar() or 0),
        "open_threads": int(scope.apply(
            db.query(func.count(AIConversationThread.id)),
            AIConversationThread.organization_id).filter(
                AIConversationThread.status == "open").scalar() or 0),
    }
