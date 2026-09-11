"""WHAT AN OPERATOR — AND LATER T9 — NEEDS TO SEE.

THIS IS THE DATA, NOT THE PRODUCT. T9 is the Workforce Intelligence product
and T10 is the Executive Command Center; neither is built here. What is built
here is the clean operational read they will consume, and the small amount of
it the T7 console needs to be usable on its own.

THE SHAPE IS DELIBERATE: counts first, then the specific rows behind each
count, each carrying enough context to act on without a second query.
"Fourteen conversations are blocked" is not information; "fourteen, and here
are the four reasons, and here are the twelve waiting on the same missing
feature flag" is.

EVERY QUERY IS TENANT-SCOPED BY ARGUMENT. There is no function here that
answers across organizations, and the platform-wide view a God screen needs
is composed by asking per organization — which keeps the accidental
cross-tenant read impossible rather than merely unlikely.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.ai_operations_models import (AICommunication,
                                             AIConversationThread,
                                             AIInboundEvent, AIOpsAction,
                                             AIOpsAuditEntry,
                                             AIScheduledAction)
from app.services.ai_operations import budget, channels
from app.services.ai_operations import constants as C
from app.services.ai_operations import contracts, flags, handoff

_log = logging.getLogger(__name__)


def overview(db: Session, organization_id: str) -> Dict[str, Any]:
    """The headline numbers for one customer's AI operations."""
    threads = (db.query(AIConversationThread.state,
                        func.count(AIConversationThread.id))
               .filter(AIConversationThread.organization_id == organization_id,
                       AIConversationThread.status == "open")
               .group_by(AIConversationThread.state).all())
    by_state = {state: int(count) for state, count in threads}

    grouped: Dict[str, int] = {}
    for key, _label, states in C.COMM_GROUPS:
        grouped[key] = sum(by_state.get(s, 0) for s in states)

    since = datetime.utcnow() - timedelta(days=1)
    denials = (db.query(AIOpsAction.denial_code,
                        func.count(AIOpsAction.id))
               .filter(AIOpsAction.organization_id == organization_id,
                       AIOpsAction.decision == "denied",
                       AIOpsAction.created_at >= since)
               .group_by(AIOpsAction.denial_code).all())

    return {
        "organization_id": organization_id,
        "generated_at": datetime.utcnow(),
        "dark_launch": flags.state(),
        "providers": channels.describe(db),
        "workforce_contracts": contracts.availability(),
        "open_threads": sum(by_state.values()),
        "threads_by_state": by_state,
        "threads_by_group": grouped,
        "handoffs_waiting": len(handoff.open_handoffs(db, organization_id)),
        "scheduled_pending": (
            db.query(func.count(AIScheduledAction.id))
            .filter(AIScheduledAction.organization_id == organization_id,
                    AIScheduledAction.status == "pending").scalar() or 0),
        "unrouted_inbound_24h": (
            db.query(func.count(AIInboundEvent.id))
            .filter(AIInboundEvent.organization_id == organization_id,
                    AIInboundEvent.routed.is_(False),
                    AIInboundEvent.created_at >= since).scalar() or 0),
        "denials_24h": [{"code": code or "unknown", "count": int(n)}
                        for code, n in denials],
        "budget": budget.report(db, organization_id),
    }


def active_objectives(db: Session, organization_id: str, *,
                      employee_id: Optional[str] = None,
                      group: Optional[str] = None,
                      limit: int = 100) -> List[Dict[str, Any]]:
    """The conversations in flight, newest activity first."""
    q = (db.query(AIConversationThread)
         .filter(AIConversationThread.organization_id == organization_id,
                 AIConversationThread.status == "open"))
    if employee_id:
        q = q.filter(AIConversationThread.employee_id == employee_id)
    if group:
        states = next((s for key, _l, s in C.COMM_GROUPS if key == group),
                      None)
        if states:
            q = q.filter(AIConversationThread.state.in_(list(states)))
    rows = (q.order_by(AIConversationThread.updated_at.desc())
            .limit(limit).all())
    return [{
        "thread_id": t.id,
        "employee_id": t.employee_id,
        "subject_type": t.subject_type,
        "subject_id": t.subject_id,
        "objective": t.objective,
        "state": t.state,
        "state_reason": t.state_reason,
        "last_channel": t.last_channel,
        "outbound": int(t.outbound_count or 0),
        "inbound": int(t.inbound_count or 0),
        "actions": int(t.action_count or 0),
        "next_action_at": t.next_action_at,
        "last_inbound_at": t.last_inbound_at,
        "last_outbound_at": t.last_outbound_at,
        "human_owner_user_id": t.human_owner_user_id,
        "appointment_ref": t.appointment_ref,
        "updated_at": t.updated_at,
    } for t in rows]


def communications(db: Session, organization_id: str, *,
                   thread_id: Optional[str] = None,
                   state: Optional[str] = None,
                   limit: int = 100) -> List[Dict[str, Any]]:
    q = (db.query(AICommunication)
         .filter(AICommunication.organization_id == organization_id))
    if thread_id:
        q = q.filter(AICommunication.thread_id == thread_id)
    if state:
        q = q.filter(AICommunication.state == state)
    rows = q.order_by(AICommunication.created_at.desc()).limit(limit).all()
    return [{
        "communication_id": c.id,
        "thread_id": c.thread_id,
        "direction": c.direction,
        "channel": c.channel,
        "state": c.state,
        "state_reason": c.state_reason,
        "preview": c.body_preview,
        "provider": c.provider,
        "provider_outcome": c.provider_outcome,
        "simulated": bool(c.simulated),
        "attempts": int(c.attempts or 0),
        "eligibility_result": c.eligibility_result,
        "denial_code": c.denial_code,
        "sent_at": c.sent_at,
        "delivered_at": c.delivered_at,
        "responded_at": c.responded_at,
        "created_at": c.created_at,
    } for c in rows]


def scheduled(db: Session, organization_id: str, *,
              limit: int = 100) -> List[Dict[str, Any]]:
    rows = (db.query(AIScheduledAction)
            .filter(AIScheduledAction.organization_id == organization_id,
                    AIScheduledAction.status.in_(("pending", "claimed")))
            .order_by(AIScheduledAction.scheduled_for.asc())
            .limit(limit).all())
    return [{
        "scheduled_action_id": a.id,
        "thread_id": a.thread_id,
        "employee_id": a.employee_id,
        "operation": a.operation,
        "channel": a.channel,
        "reason": a.reason,
        "status": a.status,
        "scheduled_for": a.scheduled_for,
        "attempt": int(a.attempt or 0),
        "max_attempts": int(a.max_attempts or 0),
        "subject_id": a.subject_id,
    } for a in rows]


def blocked_work(db: Session, organization_id: str, *,
                 limit: int = 100) -> List[Dict[str, Any]]:
    """Everything stuck, and WHY — the console's most-used screen.

    Blocked and review-required are listed together because to an operator
    they are one question ("what needs me?") even though to the engine they
    are two different refusals.
    """
    rows = (db.query(AIConversationThread)
            .filter(AIConversationThread.organization_id == organization_id,
                    AIConversationThread.state.in_(
                        (C.BLOCKED, C.REVIEW_REQUIRED, C.FAILED,
                         C.HANDOFF_REQUIRED)))
            .order_by(AIConversationThread.updated_at.desc())
            .limit(limit).all())
    return [{
        "thread_id": t.id,
        "state": t.state,
        "reason": t.state_reason,
        "subject_id": t.subject_id,
        "employee_id": t.employee_id,
        "since": t.updated_at,
        "consecutive_failures": int(t.consecutive_failures or 0),
    } for t in rows]


def unrouted_inbound(db: Session, organization_id: Optional[str] = None, *,
                     limit: int = 50) -> List[Dict[str, Any]]:
    """Inbound messages nobody could place.

    Deliberately answerable with `organization_id=None`, and it is the only
    function here that is: an event whose tenant could not be established has
    no organization to scope it to, and hiding those from every view is how
    they stay invisible. The router restricts this view to God Mode.
    """
    q = db.query(AIInboundEvent).filter(AIInboundEvent.routed.is_(False))
    if organization_id:
        q = q.filter(AIInboundEvent.organization_id == organization_id)
    rows = q.order_by(AIInboundEvent.created_at.desc()).limit(limit).all()
    return [{
        "event_id": e.id,
        "organization_id": e.organization_id,
        "provider": e.provider,
        "channel": e.channel,
        "reason": e.route_reason,
        "preview": e.body_preview,
        "received_at": e.created_at,
        "is_opt_out": bool(e.is_opt_out),
    } for e in rows]


def activity(db: Session, organization_id: str, *,
             thread_id: Optional[str] = None,
             employee_id: Optional[str] = None,
             limit: int = 100) -> List[Dict[str, Any]]:
    """The audit trail, rendered for people.

    Every consequential action, allowed or refused, with the twelve answers
    the audit table exists to hold.
    """
    q = (db.query(AIOpsAuditEntry)
         .filter(AIOpsAuditEntry.organization_id == organization_id))
    if thread_id:
        q = q.filter(AIOpsAuditEntry.thread_id == thread_id)
    if employee_id:
        q = q.filter(AIOpsAuditEntry.employee_id == employee_id)
    rows = q.order_by(AIOpsAuditEntry.created_at.desc()).limit(limit).all()
    return [{
        "id": r.id,
        "at": r.created_at,
        "event": r.event_code,
        "severity": r.severity,
        "actor_kind": r.actor_kind,
        "actor_id": r.actor_id,
        "employee_id": r.employee_id,
        "thread_id": r.thread_id,
        "subject_id": r.subject_id,
        "operation": r.operation,
        "tool_key": r.tool_key,
        "channel": r.channel,
        "provider": r.provider,
        "decision": r.decision,
        "denial_code": r.denial_code,
        "authority": r.authority,
        "activation_state": r.activation_state,
        "eligibility_result": r.eligibility_result,
        "state_from": r.state_from,
        "state_to": r.state_to,
        "outcome": r.outcome,
        "human_involved": bool(r.human_involved),
        "simulated": bool(r.simulated),
        "estimated_cost_usd": (float(r.estimated_cost_usd)
                               if r.estimated_cost_usd is not None else None),
        "next_action": r.next_action,
        "message": r.message,
    } for r in rows]


def employee_status(db: Session, organization_id: str,
                    employee_id: str) -> Dict[str, Any]:
    """One employee's operational state, for the console's detail view."""
    ctx = contracts.load_employee_context(db, employee_id,
                                          organization_id=organization_id)
    open_threads = (db.query(func.count(AIConversationThread.id))
                    .filter(AIConversationThread.organization_id
                            == organization_id,
                            AIConversationThread.employee_id == employee_id,
                            AIConversationThread.status == "open")
                    .scalar() or 0)
    return {
        "employee_id": employee_id,
        "context": ctx.as_dict() if ctx is not None else None,
        "context_available": ctx is not None,
        "open_threads": int(open_threads),
        "budget": budget.report(db, organization_id, employee_id=employee_id),
        "scheduled": len([a for a in scheduled(db, organization_id)
                          if a["employee_id"] == employee_id]),
    }


def supervisor_payload(db: Session, organization_id: str) -> Dict[str, Any]:
    """THE CONTRACT T9 WILL CONSUME. One call, everything operational.

    Kept as one function deliberately: the intelligence product should read a
    documented shape rather than assembling six queries of its own and
    inventing a seventh that scopes wrongly.
    """
    return {
        "overview": overview(db, organization_id),
        "active": active_objectives(db, organization_id, limit=50),
        "waiting": active_objectives(db, organization_id, group="waiting",
                                     limit=50),
        "scheduled": scheduled(db, organization_id, limit=50),
        "blocked": blocked_work(db, organization_id, limit=50),
        "handoffs": handoff.open_handoffs(db, organization_id),
        "recent_activity": activity(db, organization_id, limit=50),
    }
