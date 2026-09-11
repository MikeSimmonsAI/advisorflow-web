"""THE NEXT ACTION — scheduled now, RE-DECIDED at the moment it runs.

THE ONE SENTENCE THAT MATTERS: A SCHEDULE CREATED WHILE ELIGIBLE DOES NOT
GUARANTEE ELIGIBILITY WHEN THE TIME ARRIVES. A follow-up queued last night is
refused this morning if a STOP arrived at 2am, if a person took the
conversation over at 8, if the employee was paused at 9, or if the customer's
SMS feature was switched off in between. The stored row is an INTENT. The
gate chain runs again, in full, before anything happens.

NO RECURSIVE AI LOOP. An action cannot schedule itself: every scheduled
action is created by an operation that already passed the objective's action
ceiling, and the worker that executes one does not get to enqueue another
without passing it again. That, plus `max_attempts` per action and the
consecutive-failure ceiling, is what stops "follow up in an hour" becoming a
machine that texts somebody forever.

CLAIMING IS A LEASE. `claim_token` plus `lock_expires_at`: a worker that dies
mid-execution releases the action by failing to renew, and a worker whose
lease expired cannot write its result over the one that took over. The claim
is a conditional UPDATE, so two workers racing for the same action produce
one winner at the database rather than two executions.

WHERE THIS RUNS. Nothing in this module is wired into an always-on loop by
this change. `run_due_actions` is called by the operations worker entrypoint
(app/jobs/run_ai_operations_worker.py), which exits immediately unless AI
Operations is explicitly enabled — so a deploy starts no new background work.
"""

import json
import logging
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.ai_operations_models import (AIConversationThread,
                                             AIScheduledAction)
from app.services.ai_operations import audit, comm_state
from app.services.ai_operations import constants as C
from app.services.ai_operations import (contracts, flags, idempotency,
                                        orchestrator)

_log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# SCHEDULING
# ═══════════════════════════════════════════════════════════════════════════

def schedule(db: Session, ctx: contracts.EmployeeContext, *,
             thread: AIConversationThread, operation: str,
             when: datetime, channel: Optional[str] = None,
             payload: Optional[Dict[str, Any]] = None,
             reason: str = "", run_id: Optional[str] = None,
             max_attempts: int = 3):
    """Record what should happen next, and when.

    Every field the executing worker needs is stored — the operation, the
    channel, the reason, the attempt budget and the objective — because the
    worker is a different process on a different day and can assume nothing
    about why this was queued.
    """
    when = when or (datetime.utcnow() + timedelta(hours=1))
    key = idempotency.key_for_followup(
        thread_id=thread.id, operation=operation,
        scheduled_for=when.replace(second=0, microsecond=0).isoformat())

    gate = orchestrator.begin(
        db, ctx, C.OP_SCHEDULE_FOLLOWUP, subject_id=thread.subject_id,
        subject_type=thread.subject_type, thread=thread,
        work_item_id=thread.work_item_id, run_id=run_id,
        idempotency_key=key, correlation_kind=C.CORR_FOLLOWUP,
        arguments={"operation": operation, "when": when.isoformat(),
                   "channel": channel},
        requires_eligibility=False)
    if not gate.allowed:
        return orchestrator.OperationResult(ok=False, gate=gate)

    action = AIScheduledAction(
        organization_id=ctx.organization_id, thread_id=thread.id,
        employee_id=ctx.employee_id, work_item_id=thread.work_item_id,
        subject_type=thread.subject_type, subject_id=thread.subject_id,
        operation=operation, channel=channel,
        payload=json.dumps(audit.redact(payload or {})),
        reason=(reason or "")[:240] or None, status="pending",
        scheduled_for=when, max_attempts=max(1, int(max_attempts)),
        idempotency_key=key, created_by_kind=C.ACTOR_AI_EMPLOYEE,
        created_by_id=ctx.employee_id)
    is_new, action = idempotency.claim(
        db, action, organization_id=ctx.organization_id, key=key,
        finder=idempotency.find_scheduled)

    thread.next_action_at = when
    comm_state.thread_state(db, thread, C.FOLLOWUP_SCHEDULED,
                            reason=reason or operation)
    db.flush()

    orchestrator.finish(
        db, gate, status="ok" if is_new else "skipped",
        result_summary=("Scheduled %s for %s." % (operation, when.isoformat())
                        if is_new else
                        "An identical follow-up was already scheduled."),
        next_action=operation,
        detail={"scheduled_action_id": action.id, "duplicate": not is_new})
    return orchestrator.OperationResult(
        ok=True, gate=gate,
        detail={"scheduled_action_id": action.id, "when": when,
                "duplicate": not is_new})


def backoff_for(attempt: int) -> timedelta:
    """How long to wait before retrying a failed action."""
    idx = min(max(0, attempt - 1), len(C.RETRY_BACKOFF_SECONDS) - 1)
    return timedelta(seconds=C.RETRY_BACKOFF_SECONDS[idx])


# ═══════════════════════════════════════════════════════════════════════════
# EXECUTION
# ═══════════════════════════════════════════════════════════════════════════

def due_actions(db: Session, *, now: Optional[datetime] = None,
                limit: int = 50,
                organization_id: Optional[str] = None
                ) -> List[AIScheduledAction]:
    """Actions whose time has come and whose lease is free."""
    now = now or datetime.utcnow()
    q = (db.query(AIScheduledAction)
         .filter(AIScheduledAction.status.in_(("pending", "claimed")),
                 AIScheduledAction.scheduled_for <= now))
    if organization_id:
        q = q.filter(AIScheduledAction.organization_id == organization_id)
    rows = q.order_by(AIScheduledAction.scheduled_for.asc()).limit(limit).all()
    return [r for r in rows
            if r.status == "pending"
            or (r.lock_expires_at is not None and r.lock_expires_at <= now)]


def claim(db: Session, action: AIScheduledAction, *,
          now: Optional[datetime] = None) -> Optional[str]:
    """Take the lease. Returns the token, or None if another worker has it.

    THE UPDATE IS THE LOCK — a WHERE clause on the state the row must still
    be in, so the loser's UPDATE matches nothing and it learns it lost
    without a SELECT-then-UPDATE gap to lose in.
    """
    now = now or datetime.utcnow()
    token = secrets.token_urlsafe(12)
    updated = (db.query(AIScheduledAction)
               .filter(AIScheduledAction.id == action.id,
                       AIScheduledAction.status.in_(("pending", "claimed")),
                       ((AIScheduledAction.lock_expires_at.is_(None))
                        | (AIScheduledAction.lock_expires_at <= now)))
               .update({AIScheduledAction.status: "claimed",
                        AIScheduledAction.claim_token: token,
                        AIScheduledAction.claimed_at: now,
                        AIScheduledAction.lock_expires_at:
                            now + timedelta(seconds=C.ACTION_LEASE_SECONDS),
                        AIScheduledAction.updated_at: now},
                       synchronize_session=False))
    db.flush()
    if not updated:
        return None
    db.refresh(action)
    return token


def _settle(db: Session, action: AIScheduledAction, token: str, *,
            status: str, detail: str,
            result_action_id: Optional[str] = None) -> bool:
    """Write the outcome, but only if this worker still holds the lease."""
    updated = (db.query(AIScheduledAction)
               .filter(AIScheduledAction.id == action.id,
                       AIScheduledAction.claim_token == token)
               .update({AIScheduledAction.status: status,
                        AIScheduledAction.status_detail: (detail or "")[:240],
                        AIScheduledAction.executed_at: datetime.utcnow(),
                        AIScheduledAction.result_action_id: result_action_id,
                        AIScheduledAction.updated_at: datetime.utcnow()},
                       synchronize_session=False))
    db.flush()
    if not updated:
        _log.info("ai_operations: lease lost before settling action %s",
                  action.id)
    return bool(updated)


def execute(db: Session, action: AIScheduledAction, *,
            now: Optional[datetime] = None) -> Dict[str, Any]:
    """Run one due action, re-deciding everything first.

    Returns a small dict describing what happened — executed, skipped,
    cancelled or failed — rather than raising, because this is called in a
    loop over many actions and one bad row must not stop the rest.
    """
    now = now or datetime.utcnow()
    outcome: Dict[str, Any] = {"action_id": action.id, "status": "skipped"}

    if not flags.operations_enabled():
        return {**outcome, "reason": C.D_FEATURE_FLAG_OFF}

    token = claim(db, action, now=now)
    if token is None:
        return {**outcome, "reason": "claimed by another worker"}

    thread = (db.query(AIConversationThread)
              .filter(AIConversationThread.id == action.thread_id).first())
    if thread is None:
        _settle(db, action, token, status="cancelled",
                detail="the conversation no longer exists")
        return {**outcome, "status": "cancelled", "reason": "no thread"}

    # ── the cheap refusals, before an employee context is even loaded ──────
    if thread.stop_reason or thread.status == "closed":
        _settle(db, action, token, status="cancelled",
                detail="conversation stopped: %s" % (thread.stop_reason or ""))
        return {**outcome, "status": "cancelled",
                "reason": thread.stop_reason or "closed"}
    if thread.human_owner_user_id:
        _settle(db, action, token, status="cancelled",
                detail="a person owns this conversation")
        return {**outcome, "status": "cancelled", "reason": C.D_HUMAN_OWNED}

    ctx = contracts.load_employee_context(
        db, action.employee_id or "", organization_id=action.organization_id)
    if ctx is None:
        # FAIL SAFE. Without an authority answer there is no authority, so
        # the action waits rather than running on assumptions.
        _settle(db, action, token, status="skipped",
                detail="the employee's authority could not be resolved")
        audit.record(db, event_code="ops.followup_unresolvable",
                     organization_id=action.organization_id,
                     actor_kind=C.ACTOR_SYSTEM, thread_id=thread.id,
                     subject_type=action.subject_type,
                     subject_id=action.subject_id,
                     operation=action.operation, severity="warning",
                     message=("A scheduled action could not run: the "
                              "employee's authority could not be resolved."))
        return {**outcome, "reason": "no employee context"}

    payload = {}
    try:
        payload = json.loads(action.payload or "{}")
    except (TypeError, ValueError):
        payload = {}

    action.attempt = int(action.attempt or 0) + 1
    db.flush()

    # ── the operation itself, through the full gate chain ─────────────────
    result = _dispatch(db, ctx, thread, action, payload, now=now)

    if result is None:
        _settle(db, action, token, status="failed",
                detail="unsupported scheduled operation %s" % action.operation)
        return {**outcome, "status": "failed", "reason": "unsupported"}

    if result.ok:
        _settle(db, action, token, status="executed", detail="ok",
                result_action_id=result.action_id)
        return {**outcome, "status": "executed",
                "result": result.as_dict()}

    # A REFUSAL IS NOT A RETRY. If a gate said no, trying again in five
    # minutes asks the same gate the same question; only a genuine provider
    # failure earns a retry, and only within the attempt budget.
    retryable = (result.provider_outcome in (C.P_FAILED, C.P_TIMEOUT))
    if retryable and action.attempt < int(action.max_attempts or 1):
        action.status = "pending"
        action.claim_token = None
        action.lock_expires_at = None
        action.scheduled_for = now + backoff_for(action.attempt)
        action.status_detail = ("provider %s; retrying at %s"
                                % (result.provider_outcome,
                                   action.scheduled_for.isoformat()))[:240]
        action.updated_at = now
        thread.next_action_at = action.scheduled_for
        db.flush()
        return {**outcome, "status": "retry_scheduled",
                "when": action.scheduled_for}

    _settle(db, action, token,
            status=("failed" if retryable else "skipped"),
            detail=(result.error or result.denial_code or "refused"),
            result_action_id=result.action_id)
    return {**outcome,
            "status": ("failed" if retryable else "skipped"),
            "reason": result.denial_code or result.error}


def _dispatch(db: Session, ctx: contracts.EmployeeContext,
              thread: AIConversationThread, action: AIScheduledAction,
              payload: Dict[str, Any], *, now: Optional[datetime] = None):
    """Turn a scheduled row back into an operation call.

    DELIBERATELY A SMALL, CLOSED SET. A scheduled action can only be one of
    the operations listed here; a row naming anything else is refused rather
    than reflected into a function call, because "call whatever this string
    says" is how a database row becomes code execution.
    """
    body = payload.get("body") or payload.get("message") or ""
    subject_line = payload.get("subject") or payload.get("subject_line") or ""
    if action.operation == C.OP_SEND_MESSAGE:
        return orchestrator.send_message(
            db, ctx, subject_id=thread.subject_id, body=body, thread=thread,
            scheduled_action_id=action.id, now=now)
    if action.operation == C.OP_SEND_EMAIL:
        return orchestrator.send_email(
            db, ctx, subject_id=thread.subject_id, subject_line=subject_line,
            body=body, thread=thread, scheduled_action_id=action.id, now=now)
    if action.operation == C.OP_INITIATE_CALL:
        return orchestrator.initiate_call(
            db, ctx, subject_id=thread.subject_id,
            purpose=payload.get("purpose") or "", thread=thread, now=now)
    if action.operation == C.OP_TRANSFER_TO_HUMAN:
        from app.services.ai_operations import handoff
        return handoff.transfer_to_human(
            db, ctx, thread=thread,
            reason_code=payload.get("reason_code") or "policy_requires_human",
            summary=payload.get("summary") or "Scheduled handoff.",
            recommended_action=payload.get("recommended_action"))
    if action.operation == C.OP_RECORD_OUTCOME:
        return orchestrator.record_outcome(
            db, ctx, subject_id=thread.subject_id,
            outcome=payload.get("outcome") or "exhausted",
            summary=payload.get("summary") or "", thread=thread,
            stop_reason=payload.get("stop_reason"))
    return None


def run_due_actions(db: Session, *, now: Optional[datetime] = None,
                    limit: int = 50,
                    organization_id: Optional[str] = None) -> Dict[str, Any]:
    """One pass over everything that is due. Returns a summary.

    Every action is executed inside its own try/except: one row that raises
    must not stop the other forty-nine, and the row that raised is left
    claimed until its lease expires, which is exactly the behaviour a stuck
    row should have.
    """
    now = now or datetime.utcnow()
    summary = {"seen": 0, "executed": 0, "skipped": 0, "cancelled": 0,
               "failed": 0, "retry_scheduled": 0, "errors": []}
    if not flags.operations_enabled():
        summary["disabled"] = True
        return summary

    for action in due_actions(db, now=now, limit=limit,
                              organization_id=organization_id):
        summary["seen"] += 1
        try:
            result = execute(db, action, now=now)
            status = result.get("status", "skipped")
            summary[status] = summary.get(status, 0) + 1
        except Exception as exc:                             # noqa: BLE001
            db.rollback()
            summary["failed"] += 1
            summary["errors"].append("%s: %s" % (action.id,
                                                 str(exc)[:200]))
            _log.exception("ai_operations: scheduled action %s raised",
                           action.id)
    return summary
