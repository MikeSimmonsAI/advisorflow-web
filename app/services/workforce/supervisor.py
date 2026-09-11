"""THE AI SUPERVISOR — it watches, it reports, it recommends. It does not rule.

WHAT IT IS NOT. It is not another chatbot, and it is NOT a second root. Section
21 and section 1 together: the Supervisor observes workforce operations and
surfaces what needs attention, and anything it wants DONE goes back through the
same registered tools with the same authority checks as any other actor. There
is no privileged path out of this module. It writes rows in
`ai_supervisor_events`; a person or a gated tool call is what acts on them.

That restraint is not modesty. A supervisor with its own authority is a second
control plane, and the mission's first non-negotiable is that God Mode is the
only root. Giving an observer the power to pause a customer's workforce
"because it looked wrong" would mean an AI could take a customer's outreach
offline with nobody having decided that.

WHAT AN OPERATOR ACTUALLY NEEDS TO SEE, and therefore what `overview` returns:
who is running, what is queued, what is waiting, what needs review, what
failed, what was refused and why, what it cost, and which employees are
producing outcomes. All of it is COUNTED from the same tables the customer's
own screens read, so the owner's view and the customer's view cannot disagree.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.workforce_models import (AIEmployee, AIEmployeeRun, AIHandoff,
                                         AISupervisorEvent, AIToolExecution,
                                         AIWorkItem)
from app.services.workforce import constants as C

_log = logging.getLogger(__name__)

SEVERITIES = ("info", "warning", "critical")

# A run that stopped because it hit a ceiling is normal once and a signal when
# it keeps happening. These are the thresholds the pass uses; they are
# deliberately unremarkable numbers rather than tuned ones, because a threshold
# nobody can explain is a threshold nobody trusts.
STALLED_AFTER_MINUTES = 60
FAILURE_STREAK = 3
DENIAL_SPIKE = 25


def raise_event(db: Session, *, event_code: str, message: str,
                severity: str = "info", employee: Optional[AIEmployee] = None,
                organization_id: Optional[str] = None,
                platform_id: Optional[str] = None,
                detail: Optional[Dict] = None,
                recommended_action: Optional[str] = None) -> AISupervisorEvent:
    """Record something an operator should know. Never raises.

    Deduplicated within the hour on (scope, code): a stalled queue produces one
    event per hour rather than one per pass, because a supervisor screen that
    scrolls is a supervisor screen nobody reads.
    """
    if severity not in SEVERITIES:
        severity = "info"
    org_id = organization_id or getattr(employee, "organization_id", None)
    plat_id = platform_id or getattr(employee, "platform_id", None)
    try:
        cutoff = datetime.utcnow() - timedelta(hours=1)
        existing = (db.query(AISupervisorEvent)
                    .filter(AISupervisorEvent.event_code == event_code,
                            AISupervisorEvent.organization_id == org_id,
                            AISupervisorEvent.employee_id ==
                            getattr(employee, "id", None),
                            AISupervisorEvent.created_at >= cutoff)
                    .first())
        if existing is not None:
            existing.message = message[:255]
            existing.detail = json.dumps(detail or {})[:4000]
            return existing
        row = AISupervisorEvent(
            organization_id=org_id, platform_id=plat_id,
            employee_id=getattr(employee, "id", None), severity=severity,
            event_code=event_code, message=message[:255],
            detail=json.dumps(detail or {})[:4000],
            recommended_action=(recommended_action or "")[:255] or None)
        db.add(row)
        db.flush()
        _log.log(logging.WARNING if severity != "info" else logging.INFO,
                 "workforce supervisor [%s] %s org=%s employee=%s",
                 severity, event_code, org_id, getattr(employee, "id", None))
        return row
    except Exception:                                        # noqa: BLE001
        _log.exception("workforce supervisor: could not record event %s",
                       event_code)
        return None


def acknowledge(db: Session, event: AISupervisorEvent, user) -> AISupervisorEvent:
    event.acknowledged_at = datetime.utcnow()
    event.acknowledged_by = getattr(user, "id", None)
    db.flush()
    return event


# ── THE PERIODIC PASS ───────────────────────────────────────────────────────

def run_pass(db: Session, *, organization_id: Optional[str] = None,
             now: Optional[datetime] = None) -> Dict:
    """One supervision sweep. Safe to run repeatedly; changes no employee state.

    The ONE mutation it performs is releasing expired claim leases, and that is
    housekeeping rather than authority: the lease has already expired, and
    leaving the row claimed hides a dead worker instead of surfacing it.
    """
    from app.services.workforce import queue as wf_queue
    now = now or datetime.utcnow()
    findings: List[Dict] = []

    released = wf_queue.reclaim_expired(db, now=now)
    if released:
        findings.append({"code": "leases_released", "count": released})

    stalled = wf_queue.stalled(db, organization_id=organization_id,
                               older_than_minutes=STALLED_AFTER_MINUTES)
    if stalled:
        by_employee: Dict[str, int] = {}
        for item in stalled:
            by_employee[item.employee_id] = by_employee.get(item.employee_id, 0) + 1
        for emp_id, count in by_employee.items():
            emp = db.query(AIEmployee).filter(AIEmployee.id == emp_id).first()
            raise_event(db, employee=emp, severity="warning",
                        event_code="work_stalled",
                        message="%d record(s) have been due for over an hour."
                                % count,
                        detail={"count": count},
                        recommended_action="Check whether this employee is "
                                           "running and whether its provider "
                                           "is reachable.")
        findings.append({"code": "work_stalled", "count": len(stalled)})

    # REPEATED FAILURES ON ONE RECORD. Section 35 asks for an employee to be
    # paused on repeated failures; pausing is a decision, so this REPORTS it
    # with the recommendation instead of taking the customer's workforce down.
    q = db.query(AIWorkItem).filter(
        AIWorkItem.consecutive_failures >= FAILURE_STREAK)
    if organization_id:
        q = q.filter(AIWorkItem.organization_id == organization_id)
    failing = q.limit(200).all()
    if failing:
        for emp_id in {i.employee_id for i in failing}:
            emp = db.query(AIEmployee).filter(AIEmployee.id == emp_id).first()
            raise_event(db, employee=emp, severity="critical",
                        event_code="repeated_failures",
                        message="Records are failing repeatedly for this "
                                "employee.",
                        detail={"count": sum(1 for i in failing
                                             if i.employee_id == emp_id)},
                        recommended_action="Pause this employee and review the "
                                           "most recent tool failures.")
        findings.append({"code": "repeated_failures", "count": len(failing)})

    # A SPIKE OF REFUSALS is usually configuration, not attack — a channel
    # switched off, a feature removed, an entitlement lapsed — and it is
    # exactly the thing that otherwise looks like "the AI stopped working".
    since = now - timedelta(hours=24)
    dq = (db.query(AIToolExecution.employee_id, AIToolExecution.denial_code,
                   func.count(AIToolExecution.id))
          .filter(AIToolExecution.decision == "denied",
                  AIToolExecution.created_at >= since))
    if organization_id:
        dq = dq.filter(AIToolExecution.organization_id == organization_id)
    for emp_id, code, count in dq.group_by(AIToolExecution.employee_id,
                                           AIToolExecution.denial_code).all():
        if int(count) < DENIAL_SPIKE:
            continue
        emp = db.query(AIEmployee).filter(AIEmployee.id == emp_id).first()
        raise_event(db, employee=emp, severity="warning",
                    event_code="denial_spike",
                    message="%d refusals in 24h, all '%s'." % (count, code),
                    detail={"denial_code": code, "count": int(count)},
                    recommended_action="Check this employee's channels, "
                                       "feature flags and entitlement.")
        findings.append({"code": "denial_spike", "denial_code": code,
                         "count": int(count)})

    hq = db.query(AIHandoff).filter(AIHandoff.status == "open",
                                    AIHandoff.assigned_to_user_id.is_(None),
                                    AIHandoff.assigned_queue.is_(None))
    if organization_id:
        hq = hq.filter(AIHandoff.organization_id == organization_id)
    unrouted = hq.count()
    if unrouted:
        findings.append({"code": "handoffs_unrouted", "count": unrouted})

    return {"ran_at": now.isoformat(), "findings": findings,
            "organization_id": organization_id}


# ── THE VIEW ────────────────────────────────────────────────────────────────

def overview(db: Session, *, organization_id: Optional[str] = None,
             platform_id: Optional[str] = None, days: int = 7,
             now: Optional[datetime] = None) -> Dict:
    """Everything section 21 asks the Supervisor to surface, in one payload."""
    now = now or datetime.utcnow()
    since = now - timedelta(days=max(1, int(days)))

    emp_q = db.query(AIEmployee)
    if organization_id:
        emp_q = emp_q.filter(AIEmployee.organization_id == organization_id)
    if platform_id:
        emp_q = emp_q.filter(AIEmployee.platform_id == platform_id)
    employees = emp_q.all()
    emp_ids = [e.id for e in employees]

    def _scope(q, model):
        if organization_id:
            q = q.filter(model.organization_id == organization_id)
        elif emp_ids:
            q = q.filter(model.employee_id.in_(emp_ids))
        return q

    item_q = _scope(db.query(AIWorkItem.state, func.count(AIWorkItem.id)),
                    AIWorkItem)
    by_state = {s: 0 for s in C.ALL_STATES}
    for state, n in item_q.group_by(AIWorkItem.state).all():
        by_state[state] = int(n)

    run_q = _scope(db.query(AIEmployeeRun), AIEmployeeRun).filter(
        AIEmployeeRun.started_at >= since)
    runs = run_q.all()
    run_status: Dict[str, int] = {}
    cost = 0.0
    for r in runs:
        run_status[r.status] = run_status.get(r.status, 0) + 1
        if r.estimated_cost_usd:
            cost += float(r.estimated_cost_usd)

    exec_q = _scope(db.query(AIToolExecution), AIToolExecution).filter(
        AIToolExecution.created_at >= since)
    allowed = exec_q.filter(AIToolExecution.decision == "allowed").count()
    denied = exec_q.filter(AIToolExecution.decision == "denied").count()
    errors = exec_q.filter(AIToolExecution.status == "error").count()
    denial_rows = (exec_q.filter(AIToolExecution.decision == "denied")
                   .with_entities(AIToolExecution.denial_code,
                                  func.count(AIToolExecution.id))
                   .group_by(AIToolExecution.denial_code).all())

    hand_q = _scope(db.query(AIHandoff), AIHandoff)
    events_q = db.query(AISupervisorEvent).filter(
        AISupervisorEvent.created_at >= since)
    if organization_id:
        events_q = events_q.filter(
            AISupervisorEvent.organization_id == organization_id)

    from app.services.workforce import activation as wf_activation
    return {
        "generated_at": now.isoformat(),
        "window_days": int(days),
        "employees": {
            "total": len(employees),
            "active": sum(1 for e in employees if e.status == "active"),
            "paused": sum(1 for e in employees if e.paused_at is not None),
            "by_stage": _count(e.activation_state for e in employees),
            "by_role": _count(e.job_role for e in employees),
        },
        "work": {
            "by_state": by_state,
            "groups": [{"key": k, "label": label,
                        "count": sum(by_state.get(s, 0) for s in states)}
                       for k, label, states in C.QUEUE_GROUPS],
            "total": sum(by_state.values()),
        },
        "runs": {"total": len(runs), "by_status": run_status,
                 "estimated_cost_usd": round(cost, 4)},
        "tools": {
            "allowed": allowed, "denied": denied, "errors": errors,
            "top_denials": sorted(
                [{"code": c or "unknown", "count": int(n)}
                 for c, n in denial_rows],
                key=lambda d: -d["count"])[:12],
        },
        "handoffs": {
            "open": hand_q.filter(AIHandoff.status == "open").count(),
            "accepted": hand_q.filter(AIHandoff.status == "accepted").count(),
            "unrouted": hand_q.filter(
                AIHandoff.status == "open",
                AIHandoff.assigned_to_user_id.is_(None),
                AIHandoff.assigned_queue.is_(None)).count(),
        },
        "events": [
            {"id": e.id, "severity": e.severity, "code": e.event_code,
             "message": e.message, "employee_id": e.employee_id,
             "recommended_action": e.recommended_action,
             "acknowledged": e.acknowledged_at is not None,
             "created_at": e.created_at.isoformat() if e.created_at else None}
            for e in events_q.order_by(AISupervisorEvent.created_at.desc())
            .limit(50).all()
        ],
        "activation": (wf_activation.resolve(
            db, organization_id=organization_id, platform_id=platform_id
        ).as_dict() if organization_id or platform_id else
            wf_activation.scope_report(db, wf_activation.SCOPE_PLATFORM, "")),
    }


def _count(values) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for v in values:
        key = v or "unknown"
        out[key] = out.get(key, 0) + 1
    return out
