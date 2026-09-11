"""STOPPING — every reason, and the controls that make them real.

THE ASYMMETRY THAT MATTERS. Starting work requires authority, entitlement,
eligibility, budget and an activation stage. Stopping work requires none of
those: anyone who can see the conversation can stop it, an environment
variable can stop everything, and a contact saying "stop" stops it without
anybody's approval. A system where stopping is as hard as starting is a
system that cannot be stopped in an incident.

STOPPING IS IDEMPOTENT AND MONOTONIC. Stopping an already-stopped thread is a
no-op that keeps the ORIGINAL reason, because the first reason is the true
one — a conversation stopped by an opt-out and later marked "objective
complete" by a tidy-up job would lose the fact that a person asked.

WHAT A STOP DOES NOT DO. It does not delete anything, it does not close the
work item behind T6's back, and it does not undo a send that already went
out. It prevents the NEXT one, cancels what was scheduled, and records why.
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.ai_operations_models import (AIConversationThread,
                                             AIHumanOwnership,
                                             AIScheduledAction)
from app.services.ai_operations import audit, comm_state
from app.services.ai_operations import constants as C
from app.services.ai_operations import contracts

_log = logging.getLogger(__name__)


def stop_thread(db: Session, ctx: Optional[contracts.EmployeeContext],
                thread: AIConversationThread, *, reason: str,
                actor_kind: str = C.ACTOR_SYSTEM,
                actor_id: Optional[str] = None,
                detail: Optional[Dict] = None,
                close: bool = True) -> bool:
    """Stop work on one conversation. Returns whether this call stopped it."""
    if thread.stop_reason:
        return False

    thread.stop_reason = reason
    thread.stopped_at = datetime.utcnow()
    thread.stopped_by_kind = actor_kind
    thread.stopped_by_id = actor_id
    thread.next_action_at = None
    comm_state.thread_state(db, thread, C.STOPPED, reason=reason)
    if close:
        thread.status = "closed"
        thread.closed_at = datetime.utcnow()
    db.flush()

    cancelled = cancel_scheduled(db, thread, reason="conversation stopped: %s"
                                                    % reason)

    audit.record(db, event_code="ops.work_stopped", ctx=ctx,
                 organization_id=thread.organization_id,
                 severity=("warning" if reason in C.HARD_STOP_REASONS
                           else "info"),
                 actor_kind=actor_kind, actor_id=actor_id,
                 actor_user_id=(actor_id if actor_kind == C.ACTOR_HUMAN
                                else None),
                 thread_id=thread.id, subject_type=thread.subject_type,
                 subject_id=thread.subject_id, state_to=C.STOPPED,
                 outcome=reason,
                 human_involved=(actor_kind == C.ACTOR_HUMAN),
                 message="Work stopped: %s" % reason,
                 next_action="none",
                 detail={"cancelled_actions": cancelled, **(detail or {})})

    if reason in (C.STOP_OPT_OUT, C.STOP_DNC, C.STOP_CONTACT_REQUESTED):
        contracts.mirror_supervisor_event(
            db, ctx, event_code=C.SUP_OPT_OUT, severity="info",
            organization_id=thread.organization_id,
            message="A contact asked not to be contacted; work stopped.",
            detail={"thread_id": thread.id, "reason": reason})
    return True


def cancel_scheduled(db: Session, thread: AIConversationThread, *,
                     reason: str) -> int:
    """Cancel every pending action on a thread. Returns how many.

    A STALE JOB MUST NOT ACT AFTER A CANCELLATION. Cancelling here is the
    first defence and the follow-up worker's re-evaluation is the second: an
    action that somehow survives cancellation still re-runs the whole gate
    chain, and the stopped thread refuses it there.
    """
    rows = (db.query(AIScheduledAction)
            .filter(AIScheduledAction.thread_id == thread.id,
                    AIScheduledAction.status.in_(("pending", "claimed")))
            .all())
    for row in rows:
        row.status = "cancelled"
        row.status_detail = reason[:240]
        row.updated_at = datetime.utcnow()
    if rows:
        db.flush()
    return len(rows)


# ═══════════════════════════════════════════════════════════════════════════
# HUMAN TAKEOVER
# ═══════════════════════════════════════════════════════════════════════════

def take_over(db: Session, thread: AIConversationThread, *, user_id: str,
              reason_code: str = "manual_takeover",
              note: Optional[str] = None,
              handoff_ref: Optional[str] = None) -> AIHumanOwnership:
    """A person takes this conversation. The AI stops immediately.

    IMMEDIATELY MEANS AT THE NEXT GATE, NOT AT THE NEXT RUN. The ownership is
    written on the thread as well as in its own row, and `orchestrator.begin`
    reads it on every single operation — so a worker that is mid-run when
    somebody takes over is refused on its next action rather than finishing
    its sequence.
    """
    existing = (db.query(AIHumanOwnership)
                .filter(AIHumanOwnership.thread_id == thread.id,
                        AIHumanOwnership.is_active.is_(True))
                .first())
    if existing is not None:
        return existing

    row = AIHumanOwnership(
        organization_id=thread.organization_id, thread_id=thread.id,
        subject_type=thread.subject_type, subject_id=thread.subject_id,
        employee_id=thread.employee_id, handoff_ref=handoff_ref,
        user_id=user_id, reason_code=reason_code, note=note, is_active=True)
    db.add(row)
    thread.human_owner_user_id = user_id
    thread.human_owned_at = datetime.utcnow()
    thread.human_owner_reason = reason_code
    comm_state.thread_state(db, thread, C.HUMAN_OWNED, reason=reason_code)
    db.flush()

    cancelled = cancel_scheduled(db, thread,
                                 reason="a person took the conversation over")

    audit.record(db, event_code="ops.human_takeover",
                 organization_id=thread.organization_id,
                 actor_kind=C.ACTOR_HUMAN, actor_id=user_id,
                 actor_user_id=user_id, thread_id=thread.id,
                 subject_type=thread.subject_type,
                 subject_id=thread.subject_id, state_to=C.HUMAN_OWNED,
                 human_involved=True, outcome="human_owned",
                 message="A person took over this conversation.",
                 next_action="human",
                 detail={"reason_code": reason_code,
                         "cancelled_actions": cancelled})
    audit.human_action(db, organization_id=thread.organization_id,
                       actor_user_id=user_id,
                       action="ai_operations.takeover",
                       target_type="ai_conversation_thread",
                       target_id=thread.id,
                       details={"reason_code": reason_code})
    contracts.mirror_supervisor_event(
        db, None, organization_id=thread.organization_id,
        event_code=C.SUP_HUMAN_TAKEOVER, severity="info",
        message="A person took over an AI conversation.",
        detail={"thread_id": thread.id, "user_id": user_id})
    return row


def release(db: Session, thread: AIConversationThread, *, user_id: str,
            ai_may_resume: bool = False,
            note: Optional[str] = None) -> bool:
    """Hand the conversation back. The AI resumes ONLY if explicitly allowed.

    THE DEFAULT IS NO. A person who took a conversation over and finished
    with it has not thereby re-authorized automated outreach to that family;
    resuming by default is how somebody gets an AI text an hour after a real
    conversation with a human ended.
    """
    row = (db.query(AIHumanOwnership)
           .filter(AIHumanOwnership.thread_id == thread.id,
                   AIHumanOwnership.is_active.is_(True))
           .first())
    if row is None:
        return False
    row.is_active = False
    row.released_at = datetime.utcnow()
    row.released_by = user_id
    row.ai_may_resume = bool(ai_may_resume)
    if note:
        row.note = ((row.note or "") + "\n" + note)[:2000]

    thread.human_owner_user_id = None
    thread.human_owned_at = None
    if ai_may_resume:
        thread.state = C.QUEUED
        thread.state_reason = "returned to the AI employee"
    else:
        thread.stop_reason = thread.stop_reason or C.STOP_HUMAN_TAKEOVER
        thread.stopped_at = thread.stopped_at or datetime.utcnow()
        thread.status = "closed"
        thread.closed_at = thread.closed_at or datetime.utcnow()
        thread.state = C.COMPLETED
    db.flush()

    audit.record(db, event_code="ops.human_released",
                 organization_id=thread.organization_id,
                 actor_kind=C.ACTOR_HUMAN, actor_id=user_id,
                 actor_user_id=user_id, thread_id=thread.id,
                 subject_type=thread.subject_type,
                 subject_id=thread.subject_id, state_to=thread.state,
                 human_involved=True,
                 message=("Returned to the AI employee." if ai_may_resume
                          else "Closed by the person who owned it."),
                 next_action=("ai" if ai_may_resume else "none"))
    audit.human_action(db, organization_id=thread.organization_id,
                       actor_user_id=user_id,
                       action="ai_operations.release",
                       target_type="ai_conversation_thread",
                       target_id=thread.id,
                       details={"ai_may_resume": ai_may_resume})
    return True


# ═══════════════════════════════════════════════════════════════════════════
# OPERATOR CONTROLS
# ═══════════════════════════════════════════════════════════════════════════

def pause_employee_work(db: Session, *, organization_id: str,
                        employee_id: str, user_id: str,
                        reason: str = "paused by an operator") -> int:
    """Stop this employee's scheduled work inside ONE organization.

    WHAT THIS IS NOT: it is not the employee's pause switch. That column
    lives on T6's `ai_employees` row and belongs to T6's God/customer
    screens; writing it from here would be two owners for one flag. This
    cancels the pending ACTIONS, which is the operations layer's own state,
    and it is what an operator wants in the moment — "stop what is queued"
    rather than "reconfigure the employee".
    """
    rows = (db.query(AIScheduledAction)
            .filter(AIScheduledAction.organization_id == organization_id,
                    AIScheduledAction.employee_id == employee_id,
                    AIScheduledAction.status.in_(("pending", "claimed")))
            .all())
    for row in rows:
        row.status = "cancelled"
        row.status_detail = reason[:240]
        row.updated_at = datetime.utcnow()
    db.flush()
    audit.record(db, event_code="ops.employee_work_paused",
                 organization_id=organization_id, actor_kind=C.ACTOR_HUMAN,
                 actor_id=user_id, actor_user_id=user_id,
                 human_involved=True, severity="warning",
                 message="Pending work cancelled for an AI employee.",
                 detail={"employee_id": employee_id,
                         "cancelled_actions": len(rows), "reason": reason})
    audit.human_action(db, organization_id=organization_id,
                       actor_user_id=user_id,
                       action="ai_operations.pause_employee",
                       target_type="ai_employee", target_id=employee_id,
                       details={"cancelled_actions": len(rows)})
    return len(rows)


def stop_all_for_organization(db: Session, *, organization_id: str,
                              user_id: str, reason: str) -> Dict[str, int]:
    """The tenant-level stop. Every open thread, every pending action.

    An operator with authority inside ONE customer can stop that customer's
    entire AI workforce without holding any platform authority — and without
    being able to touch anybody else's. That boundary is the reason this
    takes an organization_id rather than being a global switch with a filter.
    """
    threads: List[AIConversationThread] = (
        db.query(AIConversationThread)
        .filter(AIConversationThread.organization_id == organization_id,
                AIConversationThread.status == "open")
        .all())
    stopped = 0
    for thread in threads:
        if stop_thread(db, None, thread, reason=reason,
                       actor_kind=C.ACTOR_HUMAN, actor_id=user_id):
            stopped += 1
    audit.record(db, event_code="ops.organization_stopped",
                 organization_id=organization_id, actor_kind=C.ACTOR_HUMAN,
                 actor_id=user_id, actor_user_id=user_id, severity="warning",
                 human_involved=True,
                 message="All AI conversations stopped for this "
                         "organization.",
                 detail={"threads_stopped": stopped, "reason": reason})
    return {"threads_stopped": stopped, "threads_seen": len(threads)}


def reasons_catalogue() -> List[Dict[str, str]]:
    """Every stop reason, for the console's filter and for documentation."""
    labels = {
        C.STOP_OPT_OUT: "The contact opted out",
        C.STOP_DNC: "Do not contact",
        C.STOP_OBJECTIVE_COMPLETE: "The objective was completed",
        C.STOP_APPOINTMENT_BOOKED: "An appointment was booked",
        C.STOP_HUMAN_TAKEOVER: "A person took over",
        C.STOP_CONTACT_REQUESTED: "The contact asked us to stop",
        C.STOP_INVALID_CONTACT: "The contact details are unusable",
        C.STOP_EMPLOYEE_DISABLED: "The employee was disabled",
        C.STOP_EMPLOYEE_PAUSED: "The employee was paused",
        C.STOP_OBJECTIVE_CANCELLED: "The objective was cancelled",
        C.STOP_CHANNEL_DISABLED: "The channel was disabled",
        C.STOP_POLICY_VIOLATION: "A policy was violated",
        C.STOP_COST_LIMIT: "A cost or risk limit was reached",
        C.STOP_SUPERVISOR: "The supervisor intervened",
        C.STOP_EXHAUSTED: "Every permitted attempt was used",
        C.STOP_KILL_SWITCH: "The kill switch was engaged",
    }
    return [{"code": code, "label": label,
             "hard": code in C.HARD_STOP_REASONS}
            for code, label in labels.items()]
