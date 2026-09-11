"""THE AI WORKFORCE EXCEPTION VIEW - and the door through to T5 Support.

TWO KINDS OF PROBLEM, AND ONLY ONE OF THEM BELONGS TO THIS PLATFORM'S
CUSTOMERS.

    A WORKFORCE EXCEPTION is something wrong with an AI employee's work: a
    conversation that cannot proceed, a channel refusing, a record failing
    repeatedly, a handoff nobody took. The customer's own manager fixes these,
    usually by changing something they configured.

    A PLATFORM SUPPORT ISSUE is something wrong with AdvisorFlow: a provider
    integration that is broken for everyone on a brand, a credential that has
    expired, a defect. The customer cannot fix these and should not be asked
    to try.

T9 SHOWS THE FIRST AND HANDS OVER THE SECOND. Section 11 is explicit: do not
create a competing support or ticket system. So there is no ticket table here,
no queue, no assignment, no SLA. `escalate` opens a real T5 ticket through T5's
own `create_ticket`, with the exception's evidence attached, and from that
moment T5 owns it - including the entitlement, the queue and the clock, all of
which T5 resolves and snapshots itself.

WHAT AN EXCEPTION VIEW IS FOR THAT THE QUEUE IS NOT. The Needs Attention queue
answers "what should I do next". This answers "what happened here" - one
exception, in full, with its timeline, the states it moved through, the policy
decisions taken, the tools tried, the provider's answer, who owns it now and
what has been done about it. A manager opens this when the queue item was not
self-explanatory, which is most of the interesting ones.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.ai_operations_models import (AICommunication,
                                             AIConversationThread,
                                             AIOpsAction, AIOpsAuditEntry)
from app.models.models import Organization
from app.models.workforce_intelligence_models import (AIAttentionItem,
                                                      AIManagementAction)
from app.models.workforce_models import AIEmployee, AIWorkItemEvent
from app.services.workforce_intelligence import attention as t9_attention
from app.services.workforce_intelligence import constants as C
from app.services.workforce_intelligence.scope import Scope

_log = logging.getLogger(__name__)

# THE EXCEPTION KINDS THAT ARE USUALLY OURS, NOT THE CUSTOMER'S. A provider
# failing for every employee on a brand is a platform problem; the same code
# for one employee is usually that employee's configuration. The distinction
# is a RECOMMENDATION on the payload, never an automatic escalation - opening
# somebody's support ticket for them is a decision, and it is theirs.
PLATFORM_SUSPECT_KINDS = frozenset({C.A_PROVIDER_FAILURE,
                                    C.A_INBOUND_UNROUTABLE})


def listing(db: Session, scope: Scope, *, limit: int = 100,
            severities: Optional[List[str]] = None,
            now: Optional[datetime] = None) -> Dict[str, Any]:
    """Every open exception, worst first, with its remediation state.

    Built from the same attention rows the queue reads, deliberately. An
    exceptions screen with its own detection would be a second opinion about
    the same workforce, and the first time the two disagreed nobody would know
    which to believe.
    """
    now = now or datetime.utcnow()
    data = t9_attention.queue(db, scope, severities=severities, limit=limit,
                              now=now)
    for item in data["items"]:
        item["platform_suspected"] = item["kind"] in PLATFORM_SUSPECT_KINDS
        item["remediation"] = _remediation_state(db, scope, item["id"])
    data["note"] = (
        "Exceptions about the AdvisorFlow platform itself are escalated to "
        "support rather than handled here. AdvisorFlow does not keep a second "
        "ticket system.")
    return data


def _remediation_state(db: Session, scope: Scope,
                       item_id: str) -> Dict[str, Any]:
    """What has been DONE about this, from the management-action ledger."""
    rows = (scope.apply(db.query(AIManagementAction),
                        AIManagementAction.organization_id)
            .filter(AIManagementAction.origin_kind == "attention_item",
                    AIManagementAction.origin_id == item_id)
            .order_by(AIManagementAction.requested_at.desc()).limit(20).all())
    return {
        "actions_taken": [{
            "action": r.action, "outcome": r.outcome,
            "refusal_code": r.refusal_code,
            "performed_by": r.authority_path,
            "requested_by": r.requested_by,
            "at": r.requested_at.isoformat() if r.requested_at else None,
        } for r in rows],
        "owned": bool(rows),
    }


def detail(db: Session, scope: Scope, item_id: str, *,
           now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """One exception, in full. Every field section 11 asks an incident to expose."""
    now = now or datetime.utcnow()
    row = t9_attention.get(db, scope, item_id)
    if row is None:
        return None

    employee = None
    if row.employee_id:
        employee = (scope.apply(db.query(AIEmployee),
                                AIEmployee.organization_id)
                    .filter(AIEmployee.id == row.employee_id).first())
    org = (db.query(Organization)
           .filter(Organization.id == row.organization_id).first())

    thread = None
    if row.objective_ref:
        thread = (scope.apply(db.query(AIConversationThread),
                              AIConversationThread.organization_id)
                  .filter(AIConversationThread.id == row.objective_ref)
                  .first())

    since = now - timedelta(days=7)
    audit_q = scope.apply(db.query(AIOpsAuditEntry),
                          AIOpsAuditEntry.organization_id).filter(
        AIOpsAuditEntry.created_at >= since)
    if row.employee_id:
        audit_q = audit_q.filter(AIOpsAuditEntry.employee_id
                                 == row.employee_id)
    if thread is not None:
        audit_q = audit_q.filter(AIOpsAuditEntry.thread_id == thread.id)
    audit = audit_q.order_by(AIOpsAuditEntry.created_at.desc()).limit(60).all()

    actions_q = scope.apply(db.query(AIOpsAction),
                            AIOpsAction.organization_id).filter(
        AIOpsAction.created_at >= since)
    if row.employee_id:
        actions_q = actions_q.filter(AIOpsAction.employee_id
                                     == row.employee_id)
    actions = (actions_q.order_by(AIOpsAction.created_at.desc())
               .limit(40).all())

    comms = []
    if thread is not None:
        comms = (scope.apply(db.query(AICommunication),
                             AICommunication.organization_id)
                 .filter(AICommunication.thread_id == thread.id)
                 .order_by(AICommunication.created_at.desc())
                 .limit(30).all())

    transitions = []
    if row.source_kind == "ai_work_items" and row.source_id:
        transitions = (db.query(AIWorkItemEvent)
                       .filter(AIWorkItemEvent.work_item_id == row.source_id,
                               AIWorkItemEvent.organization_id
                               == row.organization_id)
                       .order_by(AIWorkItemEvent.created_at.asc())
                       .limit(80).all())

    rendered = t9_attention._render(
        row, now, employee_names={row.employee_id or "":
                                  getattr(employee, "name", None) or ""},
        org_names={str(row.organization_id): getattr(org, "name", None) or ""})

    return {
        "exception": rendered,
        "severity": row.severity,
        "organization": {"id": row.organization_id,
                         "name": getattr(org, "name", None)},
        "employee": ({"id": employee.id, "name": employee.name,
                      "job_role": employee.job_role,
                      "status": employee.status,
                      "activation_state": employee.activation_state}
                     if employee is not None else None),
        "deployment_id": row.deployment_id,
        "objective": ({"thread_id": thread.id, "state": thread.state,
                       "state_reason": thread.state_reason,
                       "objective": thread.objective,
                       "stop_reason": thread.stop_reason}
                      if thread is not None else None),
        "contact": {"type": row.subject_type, "id": row.subject_id},
        "timeline": [{
            "at": e.created_at.isoformat() if e.created_at else None,
            "from": e.from_state, "to": e.to_state, "reason": e.reason,
            "actor_kind": e.actor_kind,
        } for e in transitions],
        "policy_decisions": [{
            "at": a.created_at.isoformat() if a.created_at else None,
            "operation": a.operation, "decision": a.decision,
            "denial_code": a.denial_code, "denial_reason": a.denial_reason,
            "eligibility_result": a.eligibility_result,
            "activation_state": a.activation_state,
        } for a in actions if a.decision == "denied"][:30],
        "actions": [{
            "at": a.created_at.isoformat() if a.created_at else None,
            "operation": a.operation, "tool_key": a.tool_key,
            "channel": a.channel, "decision": a.decision,
            "status": a.status, "provider": a.provider,
            "simulated": bool(a.simulated), "error": a.error,
        } for a in actions][:40],
        "provider_results": [{
            "at": c.created_at.isoformat() if c.created_at else None,
            "channel": c.channel, "state": c.state,
            "provider": c.provider, "provider_outcome": c.provider_outcome,
            "provider_error": c.provider_error,
            "simulated": bool(c.simulated), "attempts": int(c.attempts or 0),
        } for c in comms],
        "audit": [{
            "at": e.created_at.isoformat() if e.created_at else None,
            "event": e.event_code, "severity": e.severity,
            "actor_kind": e.actor_kind, "decision": e.decision,
            "denial_code": e.denial_code, "authority": e.authority,
            "message": e.message,
        } for e in audit],
        "human_ownership": ({"user_id": thread.human_owner_user_id,
                             "since": (thread.human_owned_at.isoformat()
                                       if thread.human_owned_at else None),
                             "reason": thread.human_owner_reason}
                            if thread is not None else None),
        "remediation": _remediation_state(db, scope, row.id),
        "platform_suspected": row.kind in PLATFORM_SUSPECT_KINDS,
        "support": {
            "system": "AdvisorFlow Support",
            "note": ("If this is a problem with AdvisorFlow rather than with "
                     "this employee's setup, escalate it - it opens a real "
                     "support ticket with this evidence attached."),
        },
    }


def escalate(db: Session, scope: Scope, item_id: str, *, user,
             note: Optional[str] = None) -> Dict[str, Any]:
    """Hand one exception to T5 Support, with its evidence attached.

    T5 OWNS IT FROM HERE. This function does not decide severity, queue,
    entitlement or a response time - `create_ticket` resolves all of those
    from the customer's own commercial arrangement and snapshots them onto the
    ticket, which is exactly why T9 must not compute its own version. What T9
    contributes is the EVIDENCE: the exception, its recommended action, and
    the authoritative row behind it.

    IT RETURNS THE TICKET RATHER THAN A CONFIRMATION, so the caller can link
    to it. An escalation the person cannot then find is an escalation they
    will repeat.
    """
    row = t9_attention.get(db, scope, item_id)
    if row is None:
        raise LookupError("That exception is not in this workspace.")
    org = (db.query(Organization)
           .filter(Organization.id == row.organization_id).first())
    if org is None:
        raise LookupError("That workspace no longer exists.")

    try:
        evidence = json.loads(row.evidence or "{}")
    except (ValueError, TypeError):
        evidence = {}

    body_lines = [
        row.why or "",
        "",
        "Raised from AI Workforce Command.",
        "Exception: %s" % C.ATTENTION_LABELS.get(row.kind, row.kind),
        "First seen: %s" % (row.first_seen_at.isoformat()
                            if row.first_seen_at else "unknown"),
        "Affects: %d record(s)" % int(row.rolled_up_count or 1),
        "Authoritative source: %s %s" % (row.source_kind or "-",
                                         row.source_id or ""),
    ]
    if row.employee_id:
        body_lines.append("AI employee: %s" % row.employee_id)
    if evidence:
        body_lines.append("")
        body_lines.append("Evidence: %s" % json.dumps(evidence, default=str))
    if note:
        body_lines.extend(["", "From the person escalating:", note])

    from app.services import support_tickets
    ticket = support_tickets.create_ticket(
        db, org=org, user=user,
        subject=("AI Workforce: %s" % (row.title or row.kind))[:200],
        body="\n".join(body_lines),
        ai_summary=(row.why or "")[:2000] or None,
        ai_recommendation=(row.recommended_action or None),
        # THE SIGNATURE IS T9'S KIND, so support's own correlation groups
        # every customer reporting the same workforce exception together
        # rather than treating each as unique.
        signature="ai_workforce.%s" % row.kind)

    _record(db, row, action=C.M_ESCALATE, user=user,
            authority_path=C.ACTION_AUTHORITY[C.M_ESCALATE],
            detail={"ticket_id": getattr(ticket, "id", None),
                    "ticket_number": getattr(ticket, "ticket_number", None)})
    row.state = C.ATTENTION_STATE_ACK
    row.acknowledged_at = datetime.utcnow()
    row.acknowledged_by = getattr(user, "id", None)
    db.flush()
    return {
        "escalated": True,
        "ticket_id": getattr(ticket, "id", None),
        "ticket_number": getattr(ticket, "ticket_number", None),
        "note": ("Support owns this now. The exception stays on your list, "
                 "acknowledged, until the underlying condition clears."),
    }


def _record(db: Session, row: AIAttentionItem, *, action: str, user,
            authority_path: str, detail: Dict[str, Any]) -> None:
    db.add(AIManagementAction(
        organization_id=row.organization_id, platform_id=row.platform_id,
        employee_id=row.employee_id, deployment_id=row.deployment_id,
        action=action, target_kind="attention_item", target_id=row.id,
        authority_path=authority_path, outcome=C.OUTCOME_PERFORMED,
        detail=json.dumps(detail, default=str)[:4000],
        origin_kind="attention_item", origin_id=row.id,
        requested_by=getattr(user, "id", None),
        requested_at=datetime.utcnow()))
