"""RECONCILIATION - two systems disagreeing, surfaced and never silently fixed.

WHAT THIS MODULE IS FOR. T6, T7 and T8 each hold part of the truth about one
AI employee: whether it may run, what it has been doing, and whether anybody is
paying for it. Each is authoritative about its own part and none of them can
see the others' contradictions. A deployment that says ACTIVE while the engine
says OFF is invisible to both - each one is internally consistent.

T9 is the only layer that reads all three, so it is the only layer that can
notice. That is the whole justification for this file existing, and it is also
the reason it must not act.

WHY NOT JUST FIX IT. Because every contradiction here is between two systems
that each have an owner, an audit trail and a control path, and a management
layer that quietly wrote to one of them would be a second control plane with
no record - the exact thing the mission forbids from its first paragraph.
Worse, the "obvious" repair is usually wrong in one direction: switching the
engine on to match the deployment would start outreach nobody approved;
switching the deployment off to match the engine would stop a customer's
workforce because a flag drifted.

SO EVERY FINDING NAMES BOTH SIDES AND THE SYSTEM THAT OWNS THE REMEDY. The
statement quotes what each side says, `remediation_owner` names which layer's
control path fixes it, and nothing in this file writes to any table outside
T9's own `ai_reconciliation_findings`.

NONE OF THESE SHOULD EVER FIRE. Each is a check for something the three layers
are built to prevent. That is precisely why they are checked rather than
assumed - a guarantee nobody verifies is a guarantee that has already stopped
holding somewhere.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.ai_deployment_models import AIEmployeeDeployment
from app.models.ai_operations_models import (AIConversationThread,
                                             AIOpsCounter)
from app.models.models import BookingLink, Lead, Organization
from app.models.workforce_intelligence_models import AIReconciliationFinding
from app.models.workforce_models import (AIEmployee, AIHandoff,
                                         AIPerformanceEntry, AIWorkItem)
from app.services.ai_operations import constants as O
from app.services.workforce import constants as W
from app.services.workforce_intelligence import collect
from app.services.workforce_intelligence import constants as C
from app.services.workforce_intelligence.scope import Scope

_log = logging.getLogger(__name__)


@dataclass
class Contradiction:
    check_key: str
    organization_id: str
    contradiction_key: str
    statement: str
    severity: str = C.SEV_HIGH
    employee_id: Optional[str] = None
    left_source: Optional[str] = None
    left_id: Optional[str] = None
    left_value: Optional[str] = None
    right_source: Optional[str] = None
    right_id: Optional[str] = None
    right_value: Optional[str] = None
    remediation_hint: Optional[str] = None

    @property
    def remediation_owner(self) -> str:
        return C.REMEDIATION_OWNER.get(self.check_key, "unknown")

    def as_dict(self) -> Dict[str, Any]:
        return {
            "check": self.check_key,
            "statement": self.statement,
            "severity": self.severity,
            "organization_id": self.organization_id,
            "employee_id": self.employee_id,
            "left": {"source": self.left_source, "id": self.left_id,
                     "value": self.left_value},
            "right": {"source": self.right_source, "id": self.right_id,
                      "value": self.right_value},
            "remediation_owner": self.remediation_owner,
            "remediation_hint": self.remediation_hint,
            "evidence_class": C.EV_FACT,
            "t9_action": ("None. This is surfaced here and fixed through the "
                          "system that owns it."),
        }


def check(db: Session, scope: Scope, *,
          now: Optional[datetime] = None) -> List[Contradiction]:
    """Run every check over one scope. A failing check does not stop the rest."""
    now = now or datetime.utcnow()
    out: List[Contradiction] = []
    for fn in _CHECKS:
        try:
            out.extend(fn(db, scope, now))
        except Exception:                                    # noqa: BLE001
            _log.exception("t9 reconciliation: check %s failed",
                           getattr(fn, "__name__", "?"))
    return out


def _deployment_vs_engine(db, scope, now) -> List[Contradiction]:
    """T8 says the employee is working; T6 says it is switched off.

    THE DANGEROUS ONE, and in the safe direction. A customer's screen reads
    T8's state and says "Working"; the engine reads T6's activation and
    refuses everything. Nothing gets sent, nobody is harmed, and the customer
    is told their AI employee is working while it is doing nothing at all.
    """
    rows = collect.deployments(db, scope, include_retired=False)
    if not rows:
        return []
    emp_ids = [d.employee_id for d in rows if d.employee_id]
    engines: Dict[str, Any] = {}
    if emp_ids:
        q = scope.apply(db.query(AIEmployee.id, AIEmployee.activation_state,
                                 AIEmployee.status, AIEmployee.paused_at),
                        AIEmployee.organization_id)
        engines = {row[0]: row for row in q.filter(
            AIEmployee.id.in_(emp_ids)).all()}
    out = []
    for dep in rows:
        if dep.state not in C.DEPLOY_LIVE_STATES:
            continue
        engine = engines.get(dep.employee_id or "")
        if engine is None:
            out.append(Contradiction(
                check_key=C.RC_DEPLOYMENT_WITHOUT_EMPLOYEE,
                organization_id=str(dep.organization_id),
                contradiction_key="no-actor:%s" % dep.id,
                statement=("A deployment is in a live state but has no AI "
                           "employee behind it, so there is nothing to run."),
                severity=C.SEV_CRITICAL, employee_id=dep.employee_id,
                left_source="ai_employee_deployments", left_id=dep.id,
                left_value=dep.state,
                right_source="ai_employees", right_id=dep.employee_id,
                right_value="missing",
                remediation_hint=("Provision the actor through the deployment "
                                  "lifecycle, or stand the deployment down.")))
            continue
        activation = engine[1] or "off"
        if activation == "off":
            out.append(Contradiction(
                check_key=C.RC_DEPLOYMENT_ACTIVE_ENGINE_OFF,
                organization_id=str(dep.organization_id),
                contradiction_key="active-off:%s" % dep.id,
                statement=("The deployment says this employee is working and "
                           "the engine says its activation stage is off. "
                           "Nothing is running."),
                severity=C.SEV_CRITICAL, employee_id=dep.employee_id,
                left_source="ai_employee_deployments", left_id=dep.id,
                left_value=dep.state,
                right_source="ai_employees", right_id=dep.employee_id,
                right_value=activation,
                remediation_hint=("Use the deployment's own start or stand-"
                                  "down path so both sides move together. Do "
                                  "not set the activation stage directly.")))
        if dep.commercial_state not in C.DEPLOY_COMMERCIALLY_LIVE:
            out.append(Contradiction(
                check_key=C.RC_EMPLOYEE_ACTIVE_NO_ENTITLEMENT,
                organization_id=str(dep.organization_id),
                contradiction_key="no-entitlement:%s" % dep.id,
                statement=("This employee is in a live state while its "
                           "commercial state is '%s'." % dep.commercial_state),
                severity=C.SEV_CRITICAL, employee_id=dep.employee_id,
                left_source="ai_employee_deployments", left_id=dep.id,
                left_value=dep.state,
                right_source="ai_employee_deployments.commercial_state",
                right_id=dep.id, right_value=dep.commercial_state,
                remediation_hint=("Re-check entitlement through the commerce "
                                  "path. T9 does not change entitlement.")))
    return out


def _work_for_retired(db, scope, now) -> List[Contradiction]:
    """An objective still running for an employee that was retired."""
    retired = [d for d in collect.deployments(db, scope)
               if d.state == C.DEPLOY_RETIRED and d.employee_id]
    if not retired:
        return []
    ids = [d.employee_id for d in retired]
    q = scope.apply(
        db.query(AIWorkItem.organization_id, AIWorkItem.employee_id,
                 func.count(AIWorkItem.id)),
        AIWorkItem.organization_id).filter(
            AIWorkItem.employee_id.in_(ids),
            AIWorkItem.terminal_at.is_(None),
            ~AIWorkItem.state.in_(list(W.TERMINAL_STATES)))
    by_employee = {emp: (org, int(n)) for org, emp, n in q.group_by(
        AIWorkItem.organization_id, AIWorkItem.employee_id).all()}
    out = []
    for dep in retired:
        slot = by_employee.get(dep.employee_id)
        if not slot:
            continue
        org, count = slot
        out.append(Contradiction(
            check_key=C.RC_WORK_FOR_RETIRED, organization_id=str(org),
            contradiction_key="retired-work:%s" % dep.id,
            statement=("%d work items are still open for an employee that was "
                       "retired." % count),
            severity=C.SEV_HIGH, employee_id=dep.employee_id,
            left_source="ai_employee_deployments", left_id=dep.id,
            left_value=C.DEPLOY_RETIRED,
            right_source="ai_work_items", right_value="%d open" % count,
            remediation_hint=("Close or reassign the queue through the work "
                              "queue, not from here.")))
    return out


def _ledger_vs_records(db, scope, now) -> List[Contradiction]:
    """A counted outcome with no underlying record to point at.

    THE LEDGER IS AUTHORITATIVE FOR COUNTS and the record tables are
    authoritative for the things themselves, so a ledger figure that exceeds
    what the records can account for is not a number to correct - it is a
    question about which of the two stopped being written. Reported, never
    reconciled: silently adjusting a counter would destroy the only
    append-only history this platform has.
    """
    out = []
    handoff_counts = {emp: int(n) for emp, n in scope.apply(
        db.query(AIHandoff.employee_id, func.count(AIHandoff.id)),
        AIHandoff.organization_id).group_by(AIHandoff.employee_id).all()}
    ledger = {}
    q = scope.apply(
        db.query(AIPerformanceEntry.organization_id,
                 AIPerformanceEntry.employee_id,
                 AIPerformanceEntry.metric_key,
                 func.sum(AIPerformanceEntry.value)),
        AIPerformanceEntry.organization_id).filter(
            AIPerformanceEntry.metric_key.in_(("handoffs", "appointments")))
    for org, emp, key, total in q.group_by(
            AIPerformanceEntry.organization_id,
            AIPerformanceEntry.employee_id,
            AIPerformanceEntry.metric_key).all():
        ledger.setdefault(emp or "", {"org": str(org)})[key] = int(total or 0)

    booking_counts: Dict[str, int] = {}
    bq = (db.query(AIConversationThread.employee_id,
                   func.count(BookingLink.id))
          .select_from(AIConversationThread)
          .join(BookingLink,
                BookingLink.id == AIConversationThread.appointment_ref)
          .join(Lead, Lead.id == BookingLink.lead_id)
          .filter(Lead.organization_id == AIConversationThread.organization_id))
    bq = scope.apply(bq, AIConversationThread.organization_id)
    for emp, n in bq.group_by(AIConversationThread.employee_id).all():
        booking_counts[emp or ""] = int(n or 0)

    for emp_id, slot in ledger.items():
        org = slot["org"]
        counted = int(slot.get("handoffs", 0))
        actual = handoff_counts.get(emp_id, 0)
        if counted > actual:
            out.append(Contradiction(
                check_key=C.RC_HANDOFF_WITHOUT_RECORD, organization_id=org,
                contradiction_key="handoff-count:%s" % (emp_id or "unknown"),
                statement=("The ledger counts %d handoffs for this employee "
                           "and %d handoff records exist."
                           % (counted, actual)),
                severity=C.SEV_NORMAL, employee_id=emp_id or None,
                left_source="ai_performance_entries", left_value=str(counted),
                right_source="ai_handoffs", right_value=str(actual),
                remediation_hint=("Check whether handoff creation is writing "
                                  "both the record and the counter.")))
        counted_appt = int(slot.get("appointments", 0))
        actual_appt = booking_counts.get(emp_id, 0)
        if counted_appt > actual_appt:
            out.append(Contradiction(
                check_key=C.RC_APPOINTMENT_WITHOUT_RECORD,
                organization_id=org,
                contradiction_key="appointment-count:%s" % (emp_id
                                                            or "unknown"),
                statement=("The ledger counts %d appointments for this "
                           "employee and %d bookings are referenced by its "
                           "conversations." % (counted_appt, actual_appt)),
                severity=C.SEV_NORMAL, employee_id=emp_id or None,
                left_source="ai_performance_entries",
                left_value=str(counted_appt),
                right_source="booking_links", right_value=str(actual_appt),
                remediation_hint=("Check whether the booking path records the "
                                  "appointment reference on the "
                                  "conversation.")))
    return out


def _success_after_cancellation(db, scope, now) -> List[Contradiction]:
    """A conversation still claiming an appointment whose booking was cancelled.

    SECTION 18 ASKS FOR THIS TO BE PROVEN: a cancelled appointment must not be
    counted as a completed outcome. `metrics` keeps it out of the standing
    count; this check is the other half, catching the case where the
    conversation's own state still says APPOINTMENT BOOKED after the booking
    went away.
    """
    q = (db.query(AIConversationThread.organization_id,
                  AIConversationThread.id,
                  AIConversationThread.employee_id,
                  BookingLink.id, BookingLink.status)
         .select_from(AIConversationThread)
         .join(BookingLink,
               BookingLink.id == AIConversationThread.appointment_ref)
         .join(Lead, Lead.id == BookingLink.lead_id)
         .filter(Lead.organization_id == AIConversationThread.organization_id,
                 AIConversationThread.state == O.APPOINTMENT_BOOKED,
                 BookingLink.status == "cancelled"))
    q = scope.apply(q, AIConversationThread.organization_id)
    return [Contradiction(
        check_key=C.RC_SUCCESS_AFTER_CANCEL, organization_id=str(org),
        contradiction_key="cancelled-success:%s" % thread_id,
        statement=("This conversation is still recorded as having booked an "
                   "appointment, and the booking has been cancelled."),
        severity=C.SEV_NORMAL, employee_id=emp_id,
        left_source="ai_conversation_threads", left_id=thread_id,
        left_value=O.APPOINTMENT_BOOKED,
        right_source="booking_links", right_id=booking_id,
        right_value=status,
        remediation_hint=("The cancellation path should move the conversation "
                          "on. Counting stays correct either way - the "
                          "standing-appointment figure already excludes "
                          "cancelled bookings."))
        for org, thread_id, emp_id, booking_id, status
        in q.limit(200).all()]


def _cost_without_usage(db, scope, now) -> List[Contradiction]:
    """Money recorded against nothing that happened."""
    q = scope.apply(
        db.query(AIOpsCounter.organization_id, AIOpsCounter.scope_type,
                 AIOpsCounter.scope_id, AIOpsCounter.metric_key,
                 AIOpsCounter.metric_date, AIOpsCounter.value,
                 AIOpsCounter.value_usd, AIOpsCounter.id),
        AIOpsCounter.organization_id).filter(
            AIOpsCounter.value_usd > 0, AIOpsCounter.value <= 0)
    return [Contradiction(
        check_key=C.RC_COST_WITHOUT_USAGE, organization_id=str(org),
        contradiction_key="cost-no-usage:%s" % row_id,
        statement=("An estimated cost of $%s is recorded for '%s' on %s with "
                   "a usage count of zero." % (usd, key, day)),
        severity=C.SEV_NORMAL,
        employee_id=(sid if stype == "employee" else None),
        left_source="ai_ops_counters.value_usd", left_id=row_id,
        left_value=str(usd),
        right_source="ai_ops_counters.value", right_id=row_id,
        right_value=str(value),
        remediation_hint=("Check the counter increment path - a cost written "
                          "without its unit is a cost nobody can explain."))
        for (org, stype, sid, key, day, value, usd, row_id)
        in q.limit(200).all()]


def _invalid_scope(db, scope, now) -> List[Contradiction]:
    """Work and conversations pointing at employees that are not there.

    A work-queue item whose employee does not exist IN THE SAME ORGANIZATION
    is the shape a cross-tenant reference would take, so this check is a
    tenant assertion as much as a data one. The comparison is deliberately
    against employees loaded through the same scope: an employee that exists
    in another customer's workspace is, correctly, not found.
    """
    employees = {e.id for e in collect.employees(db, scope)}
    out: List[Contradiction] = []

    q = scope.apply(
        db.query(AIWorkItem.organization_id, AIWorkItem.employee_id,
                 func.count(AIWorkItem.id)),
        AIWorkItem.organization_id).filter(AIWorkItem.terminal_at.is_(None))
    for org, emp_id, n in q.group_by(AIWorkItem.organization_id,
                                     AIWorkItem.employee_id).all():
        if emp_id and emp_id in employees:
            continue
        out.append(Contradiction(
            check_key=C.RC_QUEUE_ITEM_INVALID_SCOPE, organization_id=str(org),
            contradiction_key="orphan-work:%s" % (emp_id or "null"),
            statement=("%d open work items name an employee that does not "
                       "exist in this workspace." % int(n)),
            severity=C.SEV_CRITICAL, employee_id=emp_id,
            left_source="ai_work_items", left_value=str(emp_id),
            right_source="ai_employees", right_value="not found in this tenant",
            remediation_hint=("Close these items through the work queue. An "
                              "item pointing outside its tenant must never be "
                              "worked.")))

    tq = scope.apply(
        db.query(AIConversationThread.organization_id,
                 AIConversationThread.employee_id,
                 func.count(AIConversationThread.id)),
        AIConversationThread.organization_id).filter(
            AIConversationThread.status == "open",
            AIConversationThread.employee_id.isnot(None))
    for org, emp_id, n in tq.group_by(
            AIConversationThread.organization_id,
            AIConversationThread.employee_id).all():
        if emp_id in employees:
            continue
        out.append(Contradiction(
            check_key=C.RC_THREAD_EMPLOYEE_MISSING, organization_id=str(org),
            contradiction_key="orphan-thread:%s" % emp_id,
            statement=("%d open conversations name an employee that does not "
                       "exist in this workspace." % int(n)),
            severity=C.SEV_CRITICAL, employee_id=emp_id,
            left_source="ai_conversation_threads", left_value=str(emp_id),
            right_source="ai_employees", right_value="not found in this tenant",
            remediation_hint=("Stop these conversations through the "
                              "operations layer.")))
    return out


_CHECKS = (
    _deployment_vs_engine,
    _work_for_retired,
    _ledger_vs_records,
    _success_after_cancellation,
    _cost_without_usage,
    _invalid_scope,
)


# ---------------------------------------------------------------------------
# PERSISTENCE
# ---------------------------------------------------------------------------


def refresh(db: Session, scope: Scope, *,
            now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.utcnow()
    found = check(db, scope, now=now)
    platforms = {}
    org_ids = {f.organization_id for f in found if f.organization_id}
    if org_ids:
        platforms = {str(row[0]): row[1] for row in
                     db.query(Organization.id, Organization.platform_id)
                     .filter(Organization.id.in_(list(org_ids))).all()}
    seen: Dict[str, set] = {}
    for f in found:
        if not scope.covers(f.organization_id) and not scope._all_organizations:
            continue
        seen.setdefault(f.organization_id, set()).add(f.contradiction_key)
        row = (db.query(AIReconciliationFinding)
               .filter(AIReconciliationFinding.organization_id
                       == f.organization_id,
                       AIReconciliationFinding.contradiction_key
                       == f.contradiction_key).first())
        if row is None:
            row = AIReconciliationFinding(
                organization_id=f.organization_id,
                contradiction_key=f.contradiction_key, first_seen_at=now)
            db.add(row)
        elif row.state == "cleared":
            row.state = "open"
            row.cleared_at = None
        row.platform_id = platforms.get(f.organization_id)
        row.employee_id = f.employee_id
        row.check_key = f.check_key
        row.severity = f.severity
        row.statement = f.statement[:255]
        row.left_source, row.left_id = f.left_source, f.left_id
        row.left_value = (f.left_value or "")[:255] or None
        row.right_source, row.right_id = f.right_source, f.right_id
        row.right_value = (f.right_value or "")[:255] or None
        row.remediation_owner = f.remediation_owner
        row.remediation_hint = (f.remediation_hint or "")[:255] or None
        row.last_seen_at = now

    cleared = 0
    q = scope.apply(db.query(AIReconciliationFinding),
                    AIReconciliationFinding.organization_id).filter(
        AIReconciliationFinding.state.in_(("open", "acknowledged")))
    for row in q.limit(5000).all():
        if row.contradiction_key in seen.get(str(row.organization_id), set()):
            continue
        row.state = "cleared"
        row.cleared_at = now
        cleared += 1
    db.flush()
    return {"found": len(found), "cleared": cleared,
            "generated_at": now.isoformat()}


def listing(db: Session, scope: Scope, *, limit: int = 200) -> Dict[str, Any]:
    q = scope.apply(db.query(AIReconciliationFinding),
                    AIReconciliationFinding.organization_id).filter(
        AIReconciliationFinding.state.in_(("open", "acknowledged")))
    rows = (q.order_by(AIReconciliationFinding.severity.asc(),
                       AIReconciliationFinding.first_seen_at.asc())
            .limit(limit).all())
    return {
        "contradictions": [{
            "id": r.id, "check": r.check_key, "statement": r.statement,
            "severity": r.severity, "organization_id": r.organization_id,
            "employee_id": r.employee_id,
            "left": {"source": r.left_source, "id": r.left_id,
                     "value": r.left_value},
            "right": {"source": r.right_source, "id": r.right_id,
                      "value": r.right_value},
            "remediation_owner": r.remediation_owner,
            "remediation_hint": r.remediation_hint,
            "first_seen_at": (r.first_seen_at.isoformat()
                              if r.first_seen_at else None),
            "state": r.state,
            "evidence_class": C.EV_FACT,
        } for r in rows],
        "total": len(rows),
        "note": ("These are disagreements between systems. AdvisorFlow does "
                 "not repair commercial or authority state from this screen - "
                 "each one names the system that owns the fix."),
    }


def acknowledge(db: Session, scope: Scope, finding_id: str, *, user):
    row = (scope.apply(db.query(AIReconciliationFinding),
                       AIReconciliationFinding.organization_id)
           .filter(AIReconciliationFinding.id == finding_id).first())
    if row is None:
        return None
    row.state = "acknowledged"
    row.acknowledged_at = datetime.utcnow()
    row.acknowledged_by = getattr(user, "id", None)
    db.flush()
    return row
