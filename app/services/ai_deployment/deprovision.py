"""RETIREMENT AND DEPROVISIONING - stopping without erasing.

SECTION 12 IS THE MOST DANGEROUS PART OF T8 AND THE SHORTEST TO STATE: when an
arrangement ends, the work stops and the record survives.

WHAT RETIREMENT DOES

    stops future work        the T6 employee is disabled, its stage is `off`,
                             and its queued items are paused. The next tool
                             call is refused at EXECUTION time, which is the
                             only timing that stops a run already in flight.
    prevents new jobs        no enqueue, no schedule, no activation. The
                             deployment is in a terminal state with no edge out
                             of it.
    drains the queue safely  queued work is PAUSED, not deleted. A paused work
                             item keeps its history, its eligibility answer and
                             its timeline; a deleted one takes the explanation
                             of what happened to that family with it.
    cancels schedules        T7's pending follow-ups are marked cancelled so
                             nothing fires later against an employee nobody is
                             paying for. An orphan schedule is the specific
                             failure this section names.
    leaves communications    every message that was sent stays sent, every
                             audit row stays, and the conversation threads stay
                             readable. Retiring an employee is not a reason to
                             lose the record of what it said to somebody.

WHAT RETIREMENT NEVER DOES

    It never deletes a work item, a communication, an audit row, a handoff, a
    performance entry or a deployment event. `retire` writes no DELETE at all,
    and the test suite asserts the counts are unchanged across it.

    It never touches T2. Removing an add-on is a commercial act with its own
    authority and its own refusals - `catalog_purchase.remove_recurring_addon`
    owns it. Retiring an employee here and cancelling the money are two
    decisions, and collapsing them would mean a customer tidying up their
    workforce screen cancelled a subscription.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.ai_deployment_models import AIEmployeeDeployment
from app.models.models import User
from app.services.ai_deployment import commerce
from app.services.ai_deployment import constants as D
from app.services.ai_deployment import lifecycle

_log = logging.getLogger(__name__)


def retire(db: Session, deployment: AIEmployeeDeployment, *,
           actor: Optional[User] = None, reason: str = "",
           expected_state: Optional[str] = None) -> Dict[str, Any]:
    """Give this AI employee back. Everything it did stays.

    THE ORDER MATTERS. The engine is stopped BEFORE the state moves, so there
    is no window in which the deployment reads as retired while its actor is
    still able to take another tool call. If the transition then fails as
    stale, the worst outcome is an employee that is stopped and whose row still
    says it was running - which is the safe direction for that pair to
    disagree in.
    """
    if deployment.state == D.RETIRED:
        return {"state": D.RETIRED, "already_retired": True}

    stopped = lifecycle.stop_operational_work(
        db, deployment, reason="retired: %s" % (reason or "no reason given"),
        disable_actor=True)

    before = _history_counts(db, deployment)
    lifecycle.transition(
        db, deployment, D.RETIRED,
        reason=reason or "Retired by an operator.",
        actor_kind=D.ACTOR_HUMAN, actor_id=getattr(actor, "id", None),
        detail={"stopped": stopped, "history_at_retirement": before},
        expected_state=expected_state)

    return {"state": deployment.state, "stopped": stopped,
            "history_preserved": before,
            "note": ("Everything this employee did is still here. Its "
                     "conversations, its audit trail and its results are "
                     "unchanged.")}


def _history_counts(db: Session, deployment: AIEmployeeDeployment
                    ) -> Dict[str, int]:
    """What exists for this employee right now, counted so it can be compared.

    Recorded ON the retirement event, which is what makes "history was
    preserved" checkable afterwards rather than merely asserted in a docstring.
    """
    out = {"work_items": 0, "tool_executions": 0, "handoffs": 0,
           "communications": 0, "scheduled_actions": 0}
    if not deployment.employee_id:
        return out
    emp_id = deployment.employee_id
    org_id = deployment.organization_id
    try:
        from app.models.workforce_models import (AIHandoff, AIToolExecution,
                                                 AIWorkItem)
        out["work_items"] = (db.query(AIWorkItem)
                             .filter(AIWorkItem.employee_id == emp_id,
                                     AIWorkItem.organization_id == org_id)
                             .count())
        out["tool_executions"] = (db.query(AIToolExecution)
                                  .filter(AIToolExecution.employee_id == emp_id,
                                          AIToolExecution.organization_id
                                          == org_id).count())
        out["handoffs"] = (db.query(AIHandoff)
                           .filter(AIHandoff.employee_id == emp_id,
                                   AIHandoff.organization_id == org_id)
                           .count())
    except Exception as exc:                                  # noqa: BLE001
        _log.info("ai_deployment: workforce history count skipped (%s)", exc)
    try:
        from app.models.ai_operations_models import (AICommunication,
                                                     AIScheduledAction)
        out["communications"] = (db.query(AICommunication)
                                 .filter(AICommunication.organization_id
                                         == org_id,
                                         AICommunication.employee_id == emp_id)
                                 .count())
        out["scheduled_actions"] = (db.query(AIScheduledAction)
                                    .filter(AIScheduledAction.organization_id
                                            == org_id,
                                            AIScheduledAction.employee_id
                                            == emp_id).count())
    except Exception as exc:                                  # noqa: BLE001
        _log.info("ai_deployment: operations history count skipped (%s)", exc)
    return out


# ---------------------------------------------------------------------------
# THE SWEEP - finding what should have stopped and did not
# ---------------------------------------------------------------------------

def orphan_scan(db: Session, *, organization_id: Optional[str] = None
                ) -> Dict[str, Any]:
    """Find employees still able to work that no deployment entitles.

    THIS IS A REPORT, NOT A REPAIR, unless the caller asks for one. Three
    things it looks for, each of which is a way section 12 can be violated by
    something OUTSIDE this package:

      * an `ai_employees` row with no deployment at all, not switched off. T6
        can create employees directly - its own router does - and one created
        that way is not entitled by anything T8 knows about.
      * a deployment in a live state whose commercial standing is not live.
      * a scheduled action pointing at an employee whose deployment is retired
        or suspended.

    An empty result is the expected one and the interesting number is how it
    got to be non-zero.
    """
    from app.models.workforce_models import AIEmployee

    findings: Dict[str, List[Dict[str, Any]]] = {
        "employees_without_deployment": [],
        "live_without_entitlement": [],
        "orphan_scheduled_actions": [],
    }

    dep_q = db.query(AIEmployeeDeployment)
    emp_q = db.query(AIEmployee)
    if organization_id:
        dep_q = dep_q.filter(
            AIEmployeeDeployment.organization_id == organization_id)
        emp_q = emp_q.filter(AIEmployee.organization_id == organization_id)
    deployments = dep_q.all()
    by_employee = {d.employee_id: d for d in deployments if d.employee_id}

    from app.services.workforce import activation as wf_activation
    for emp in emp_q.all():
        dep = by_employee.get(emp.id)
        if dep is not None:
            continue
        resolved = wf_activation.resolve(db, employee=emp)
        if resolved.may_run:
            findings["employees_without_deployment"].append({
                "employee_id": emp.id,
                "organization_id": emp.organization_id,
                "stage": resolved.state,
                "detail": ("This AI employee can run and no deployment record "
                           "entitles it."),
            })

    for dep in deployments:
        if dep.state not in D.LIVE_STATES:
            continue
        from app.models.models import Organization
        org = (db.query(Organization)
               .filter(Organization.id == dep.organization_id).first())
        offer = commerce.resolve_offer(db, org, dep.template_key) \
            if org is not None else None
        if offer is None or not offer.is_live:
            findings["live_without_entitlement"].append({
                "deployment_id": dep.id,
                "organization_id": dep.organization_id,
                "state": dep.state,
                "detail": (getattr(offer, "detail", None)
                           or "No live commercial arrangement."),
            })

    try:
        from app.models.ai_operations_models import AIScheduledAction
        sched_q = db.query(AIScheduledAction).filter(
            AIScheduledAction.status.in_(("pending", "claimed")))
        if organization_id:
            sched_q = sched_q.filter(
                AIScheduledAction.organization_id == organization_id)
        dead = {d.employee_id for d in deployments
                if d.employee_id and d.state in (D.RETIRED, D.SUSPENDED)}
        for row in sched_q.all():
            if row.employee_id and row.employee_id in dead:
                findings["orphan_scheduled_actions"].append({
                    "scheduled_action_id": row.id,
                    "employee_id": row.employee_id,
                    "organization_id": row.organization_id,
                    "scheduled_for": (row.scheduled_for.isoformat()
                                      if row.scheduled_for else None),
                })
    except Exception as exc:                                  # noqa: BLE001
        _log.info("ai_deployment: orphan schedule scan skipped (%s)", exc)

    total = sum(len(v) for v in findings.values())
    return {
        "organization_id": organization_id,
        "clean": total == 0,
        "findings": findings,
        "total": total,
        "checked_at": datetime.utcnow().isoformat(),
    }


def repair_orphans(db: Session, *, organization_id: Optional[str] = None,
                   actor: Optional[User] = None) -> Dict[str, Any]:
    """Stop what the scan found. STOPPING ONLY - nothing is started or deleted.

    Every repair here moves something towards OFF. That is what makes it safe
    to expose as a button and safe to run on a schedule: the worst outcome of
    an over-eager repair is an employee that needs switching on again by a
    person, which is a conversation, and the worst outcome of not running it is
    an AI employee contacting families on an arrangement that ended.
    """
    scan = orphan_scan(db, organization_id=organization_id)
    stopped = {"employees": 0, "deployments": 0, "scheduled_actions": 0}

    from app.models.workforce_models import AIEmployee
    from app.services.workforce import service as wf_service
    for item in scan["findings"]["employees_without_deployment"]:
        emp = (db.query(AIEmployee)
               .filter(AIEmployee.id == item["employee_id"]).first())
        if emp is None:
            continue
        wf_service.disable(db, emp, actor=actor,
                           reason="no deployment record entitles this employee")
        stopped["employees"] += 1

    for item in scan["findings"]["live_without_entitlement"]:
        dep = (db.query(AIEmployeeDeployment)
               .filter(AIEmployeeDeployment.id == item["deployment_id"])
               .first())
        if dep is None:
            continue
        commerce.reconcile_deployment(
            db, dep, reason="no live commercial arrangement",
            actor_kind=D.ACTOR_SYSTEM)
        stopped["deployments"] += 1

    for item in scan["findings"]["orphan_scheduled_actions"]:
        dep = next((d for d in lifecycle.list_for_org(
            db, item["organization_id"])
            if d.employee_id == item["employee_id"]), None)
        if dep is None:
            continue
        stopped["scheduled_actions"] += lifecycle.cancel_scheduled_actions(
            db, dep, reason="deployment no longer entitled")

    db.flush()
    return {"before": scan, "stopped": stopped,
            "after": orphan_scan(db, organization_id=organization_id)}
