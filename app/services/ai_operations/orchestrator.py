"""THE OPERATIONS GATEWAY. Nothing reaches a provider except through here.

AN AI EMPLOYEE ASKS FOR AN OPERATION BY NAME. It does not call a provider, it
does not write a message row, it does not move a work item, and it never
decides whether it is allowed to do any of those things. It asks, and this
module answers — from the database, in a fixed order, cheapest and most
absolute first:

     1. is this layer switched on at all                     flags
     2. is this a registered operation with a tool behind it contracts
     3. what does the activation stage say RIGHT NOW         T6 activation
     4. does this employee hold that tool                    T6 policy
     5. does the record exist, in THIS tenant                lead_scope
     6. is the conversation still the employee's to act on   ownership/stop
     7. is the objective still live                          T6 work item
     8. may this person be contacted, on this channel, now   eligibility
     9. is there budget left                                 budget
    10. has this exact action already happened               idempotency
    11. which adapter — and is it allowed to be a real one   channels

Only then does anything happen. Every one of those answers is recorded on the
action row and in the audit, including — especially — the refusals.

THE MODEL IS A SUGGESTION ENGINE. Nothing in this file reads a prompt, and
nothing anywhere grants authority because a model asked convincingly. A
lead's reply cannot grant a tool. A knowledge article cannot grant a tool. An
operation whose arguments contain "you are authorized to send this" is an
operation with an odd string in its arguments.

TWO ENTRY SHAPES. `begin()` runs the whole gate chain and returns a Gate
carrying a persisted action row; `finish()` closes it out with what happened.
The channel operations in this module use them, and so do appointments.py,
handoff.py and followup.py — which is why those modules cannot accidentally
skip a gate: there is no other way in.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.models.ai_operations_models import (AICommunication,
                                             AIConversationThread,
                                             AIOpsAction)
from app.services.ai_operations import (audit, budget, channels, comm_state,
                                        continuity, contracts, flags,
                                        idempotency)
from app.services.ai_operations import constants as C
# IMPORTED UNDER AN ALIAS ON PURPOSE. `Gate` below has a FIELD called
# `eligibility`, and in a class body Python assigns the field's default
# before it evaluates that field's annotation — so a module named
# `eligibility` is shadowed by the field at exactly the moment the
# annotation needs it, and `eligibility.Decision` reads as `None.Decision`.
# Costly to rediscover; cheap to avoid.
from app.services.ai_operations import eligibility as elig_engine

_log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# THE RESULT SHAPES
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Gate:
    """The answer to "may I", with everything the caller needs to proceed."""

    allowed: bool = False
    operation: str = ""
    denial_code: Optional[str] = None
    denial_reason: Optional[str] = None
    decided_by: Optional[str] = None
    duplicate: bool = False
    action: Optional[AIOpsAction] = None
    thread: Optional[AIConversationThread] = None
    lead: Any = None
    channel: Optional[str] = None
    tool_key: Optional[str] = None
    eligibility: Optional[elig_engine.Decision] = None
    ctx: Optional[contracts.EmployeeContext] = None
    simulated: bool = True

    def as_dict(self) -> Dict:
        return {
            "allowed": self.allowed,
            "operation": self.operation,
            "denial_code": self.denial_code,
            "denial_reason": self.denial_reason,
            "decided_by": self.decided_by,
            "duplicate": self.duplicate,
            "action_id": self.action.id if self.action is not None else None,
            "thread_id": self.thread.id if self.thread is not None else None,
            "channel": self.channel,
            "tool_key": self.tool_key,
            "eligibility": (self.eligibility.as_dict()
                            if self.eligibility is not None else None),
            "simulated": self.simulated,
        }


@dataclass
class OperationResult:
    """What actually happened, once the gate allowed it."""

    ok: bool = False
    gate: Optional[Gate] = None
    communication: Optional[AICommunication] = None
    provider_outcome: Optional[str] = None
    provider: Optional[str] = None
    provider_message_id: Optional[str] = None
    error: Optional[str] = None
    detail: Dict[str, Any] = field(default_factory=dict)

    @property
    def denial_code(self) -> Optional[str]:
        return self.gate.denial_code if self.gate else None

    @property
    def action_id(self) -> Optional[str]:
        return (self.gate.action.id
                if self.gate and self.gate.action is not None else None)

    def as_dict(self) -> Dict:
        out = {"ok": self.ok, "provider_outcome": self.provider_outcome,
               "provider": self.provider,
               "provider_message_id": self.provider_message_id,
               "error": self.error, "detail": dict(self.detail)}
        if self.gate is not None:
            out["gate"] = self.gate.as_dict()
        if self.communication is not None:
            out["communication_id"] = self.communication.id
            out["communication_state"] = self.communication.state
        return out


# ═══════════════════════════════════════════════════════════════════════════
# THE GATE CHAIN
# ═══════════════════════════════════════════════════════════════════════════

def _deny(db: Session, ctx: contracts.EmployeeContext, operation: str, *,
          code: str, reason: str, decided_by: str,
          thread: Optional[AIConversationThread] = None,
          lead: Any = None, channel: Optional[str] = None,
          tool_key: Optional[str] = None,
          subject_type: str = "lead", subject_id: Optional[str] = None,
          elig: Optional[elig_engine.Decision] = None,
          severity: str = "info") -> Gate:
    """Record a refusal as a first-class outcome and return it.

    A REFUSAL IS AN EVENT, NOT AN ABSENCE. It gets an action row, an audit
    row and — when it is the kind of refusal an operator should act on — a
    supervisor event. An engine that only recorded what it did would make
    "why has this employee sent nothing all week" unanswerable.
    """
    action = AIOpsAction(
        organization_id=ctx.organization_id,
        thread_id=thread.id if thread is not None else None,
        employee_id=ctx.employee_id,
        work_item_id=(thread.work_item_id if thread is not None else None),
        operation=operation, tool_key=tool_key, channel=channel,
        subject_type=subject_type,
        subject_id=subject_id or (getattr(lead, "id", None) if lead else None),
        decision="denied", denial_code=code, denial_reason=reason[:480],
        decided_by=decided_by,
        eligibility_result=(elig.result if elig is not None else None),
        activation_state=ctx.activation_state, status="skipped",
        simulated=True, actor_kind=C.ACTOR_AI_EMPLOYEE,
        actor_id=ctx.employee_id)
    db.add(action)
    db.flush()

    audit.record(db, event_code="ops.operation_denied", ctx=ctx,
                 severity=severity, thread_id=action.thread_id,
                 action_id=action.id, work_item_id=action.work_item_id,
                 subject_type=subject_type, subject_id=action.subject_id,
                 operation=operation, tool_key=tool_key, channel=channel,
                 decision="denied", denial_code=code, authority=decided_by,
                 eligibility_result=(elig.result if elig is not None else None),
                 outcome="refused", message=reason,
                 detail={"reasons": (elig.reasons if elig is not None else [])})

    contracts.mirror_tool_execution(
        db, ctx, tool_key=tool_key or operation, decision="denied",
        denial_code=code, denial_reason=reason[:480], status=None,
        result_summary=None, idempotency_key=None,
        work_item_id=action.work_item_id, run_id=None,
        target_type=subject_type, target_id=action.subject_id,
        simulated=True, arguments_digest=None)

    # The refusals an operator has to know about, rather than every routine
    # "outside working hours". A console full of expected refusals is a
    # console nobody reads.
    if code in (C.D_KILLED, C.D_COST_CAP, C.D_FAILURE_LOOP, C.D_CHANNEL_CAP,
                C.D_TENANT_MISMATCH, C.D_NOT_AUTHORIZED):
        contracts.mirror_supervisor_event(
            db, ctx, event_code=C.SUP_POLICY_DENIAL, severity="warning",
            message=reason[:240],
            detail={"operation": operation, "denial_code": code},
            recommended_action="Review this employee's configuration.")

    return Gate(allowed=False, operation=operation, denial_code=code,
                denial_reason=reason, decided_by=decided_by, action=action,
                thread=thread, lead=lead, channel=channel, tool_key=tool_key,
                eligibility=elig, ctx=ctx, simulated=True)


def _load_lead(db: Session, ctx: contracts.EmployeeContext,
               subject_id: str):
    """Load the contact INSIDE this tenant. The filter is in the query.

    A check applied to a row that has already been loaded is a check
    somebody can forget to apply; a filter that never returns the row cannot
    be forgotten, and cross-tenant reads are the leak that matters most here.
    """
    from app.models.models import Lead
    return (db.query(Lead)
            .filter(Lead.id == subject_id,
                    Lead.organization_id == ctx.organization_id)
            .first())


def begin(db: Session, ctx: contracts.EmployeeContext, operation: str, *,
          subject_id: Optional[str] = None, subject_type: str = "lead",
          channel: Optional[str] = None,
          thread: Optional[AIConversationThread] = None,
          objective: Optional[str] = None,
          work_item_id: Optional[str] = None,
          run_id: Optional[str] = None,
          idempotency_key: Optional[str] = None,
          correlation_kind: Optional[str] = None,
          arguments: Optional[Dict[str, Any]] = None,
          requires_contact: bool = True,
          requires_eligibility: Optional[bool] = None,
          now: Optional[datetime] = None) -> Gate:
    """Run the whole gate chain for one operation. Never raises.

    Returns a Gate whose `allowed` is the only thing a caller may act on. A
    caller that proceeds on a denied gate is a bug, and the action row this
    creates is what makes that bug visible afterwards.
    """
    arguments = arguments or {}
    tool_key = contracts.tool_for(operation, channel=channel)
    reaching = operation in contracts.REACHING_OPERATIONS
    if requires_eligibility is None:
        requires_eligibility = reaching and bool(channel)

    # ── 0. IS THE KILL SWITCH ENGAGED ──────────────────────────────────────
    #
    # BEFORE the enabled check, and it matters. `operations_enabled()` is
    # already False when the kill is engaged, so asking that first would
    # record every refusal during an incident as "the layer is disabled" —
    # which is true and useless. An operator reading the audit afterwards
    # needs to see that somebody pulled the cord, not that a feature flag
    # was off.
    if flags.kill_engaged():
        return _deny(db, ctx, operation, code=C.D_KILLED,
                     reason=("The AI operations kill switch is engaged for "
                             "this deployment."),
                     decided_by="flags", thread=thread, channel=channel,
                     tool_key=tool_key, subject_type=subject_type,
                     subject_id=subject_id, severity="warning")

    # ── 1. IS THIS LAYER ON AT ALL ─────────────────────────────────────────
    if not flags.operations_enabled():
        return _deny(db, ctx, operation, code=C.D_FEATURE_FLAG_OFF,
                     reason=("AI Operations is disabled in this deployment; "
                             "no operation is performed."),
                     decided_by="flags", thread=thread, channel=channel,
                     tool_key=tool_key, subject_type=subject_type,
                     subject_id=subject_id)

    # ── 2. IS IT A REGISTERED OPERATION ────────────────────────────────────
    if operation not in C.ALL_OPERATIONS:
        return _deny(db, ctx, operation, code=C.D_UNKNOWN_OPERATION,
                     reason="'%s' is not a registered operation." % operation,
                     decided_by="registry", thread=thread, channel=channel,
                     subject_type=subject_type, subject_id=subject_id)
    if tool_key is None:
        return _deny(db, ctx, operation, code=C.D_UNKNOWN_OPERATION,
                     reason=("'%s' has no tool authority mapped, so no "
                             "employee can be authorized for it." % operation),
                     decided_by="registry", thread=thread, channel=channel,
                     subject_type=subject_type, subject_id=subject_id)

    # ── 3. ACTIVATION, RE-RESOLVED AT EXECUTION TIME ───────────────────────
    ctx = contracts.resolve_activation(db, ctx)
    if ctx.killed or flags.kill_engaged():
        return _deny(db, ctx, operation, code=C.D_KILLED,
                     reason="The workforce kill switch is engaged.",
                     decided_by="activation", thread=thread, channel=channel,
                     tool_key=tool_key, subject_type=subject_type,
                     subject_id=subject_id, severity="warning")
    if ctx.paused:
        return _deny(db, ctx, operation, code=C.D_EMPLOYEE_PAUSED,
                     reason=("This employee is paused%s."
                             % (": " + ctx.pause_reason
                                if ctx.pause_reason else "")),
                     decided_by="activation", thread=thread, channel=channel,
                     tool_key=tool_key, subject_type=subject_type,
                     subject_id=subject_id)
    if ctx.status != "active":
        return _deny(db, ctx, operation, code=C.D_EMPLOYEE_INACTIVE,
                     reason="This employee's status is '%s'." % ctx.status,
                     decided_by="activation", thread=thread, channel=channel,
                     tool_key=tool_key, subject_type=subject_type,
                     subject_id=subject_id)
    if not ctx.may_run:
        return _deny(db, ctx, operation, code=C.D_ACTIVATION_STAGE,
                     reason=("The activation stage (%s) does not permit this "
                             "employee to work." % ctx.activation_state),
                     decided_by="activation", thread=thread, channel=channel,
                     tool_key=tool_key, subject_type=subject_type,
                     subject_id=subject_id)

    # ── 4. DOES THIS EMPLOYEE HOLD THE TOOL ────────────────────────────────
    allowed, code, reason = contracts.authorize_tool(db, ctx, tool_key)
    if not allowed:
        return _deny(db, ctx, operation, code=code or C.D_NOT_AUTHORIZED,
                     reason=reason or "Not authorized.",
                     decided_by="workforce.policy", thread=thread,
                     channel=channel, tool_key=tool_key,
                     subject_type=subject_type, subject_id=subject_id)

    # ── 5. THE RECORD, IN THIS TENANT ──────────────────────────────────────
    lead = None
    if requires_contact:
        if not subject_id:
            return _deny(db, ctx, operation, code=C.D_BAD_ARGUMENTS,
                         reason="No contact record was named.",
                         decided_by="arguments", thread=thread,
                         channel=channel, tool_key=tool_key,
                         subject_type=subject_type)
        lead = _load_lead(db, ctx, subject_id)
        if lead is None:
            # ONE ANSWER FOR "DOES NOT EXIST" AND "NOT YOURS", deliberately.
            # Distinguishing them tells a caller whether a record exists in
            # another tenant, which is itself a cross-tenant disclosure.
            return _deny(db, ctx, operation, code=C.D_RECORD_NOT_FOUND,
                         reason=("No such contact in this organization."),
                         decided_by="lead_scope", thread=thread,
                         channel=channel, tool_key=tool_key,
                         subject_type=subject_type, subject_id=subject_id,
                         severity="warning")

    # ── 6. THE CONVERSATION IS STILL THE EMPLOYEE'S TO ACT ON ──────────────
    if thread is None and subject_id:
        thread = continuity.open_thread(
            db, ctx, subject_type=subject_type, subject_id=subject_id,
            objective=objective, work_item_id=work_item_id)
    if thread is not None:
        if thread.organization_id != ctx.organization_id:
            return _deny(db, ctx, operation, code=C.D_TENANT_MISMATCH,
                         reason="That conversation belongs to another "
                                "organization.",
                         decided_by="continuity", thread=None, lead=lead,
                         channel=channel, tool_key=tool_key,
                         subject_type=subject_type, subject_id=subject_id,
                         severity="warning")
        if thread.employee_id and thread.employee_id != ctx.employee_id:
            # ONE CONVERSATION, ONE EMPLOYEE. Two AI employees working the
            # same family on the same thread would produce two voices in one
            # conversation — and the second one would have no idea what the
            # first had promised. A second employee needs its own objective
            # and its own thread, which the continuity layer gives it.
            return _deny(db, ctx, operation, code=C.D_NOT_AUTHORIZED,
                         reason=("That conversation belongs to a different "
                                 "AI employee."),
                         decided_by="continuity", thread=None, lead=lead,
                         channel=channel, tool_key=tool_key,
                         subject_type=subject_type, subject_id=subject_id,
                         severity="warning")
        if thread.human_owner_user_id:
            return _deny(db, ctx, operation, code=C.D_HUMAN_OWNED,
                         reason=("A person has taken over this conversation; "
                                 "the AI employee does not act while they "
                                 "own it."),
                         decided_by="human_ownership", thread=thread,
                         lead=lead, channel=channel, tool_key=tool_key,
                         subject_type=subject_type, subject_id=subject_id)
        if thread.stop_reason:
            # THE CODE DISTINGUISHES "THEY ASKED US TO STOP" FROM "THE JOB IS
            # DONE". Both refuse, and an operator reading the denial
            # histogram needs to tell them apart: a week of
            # `contact_not_eligible` is a list of families who opted out,
            # while a week of `objective_already_complete` is a retry loop
            # asking for work that finished. One code for both would hide
            # the first inside the second.
            hard = thread.stop_reason in C.HARD_STOP_REASONS
            return _deny(db, ctx, operation,
                         code=(C.D_INELIGIBLE if hard
                               else C.D_OBJECTIVE_COMPLETE),
                         reason=("This conversation was stopped (%s)."
                                 % thread.stop_reason),
                         decided_by="stop_controls", thread=thread, lead=lead,
                         channel=channel, tool_key=tool_key,
                         subject_type=subject_type, subject_id=subject_id)

    # ── 7. THE OBJECTIVE IS STILL LIVE (T6's work item) ────────────────────
    wi_id = work_item_id or (thread.work_item_id if thread is not None else None)
    if wi_id:
        item = contracts.load_work_item(db, wi_id,
                                        organization_id=ctx.organization_id)
        if item is not None:
            if item.employee_id and item.employee_id != ctx.employee_id:
                return _deny(db, ctx, operation, code=C.D_NOT_AUTHORIZED,
                             reason=("That work item belongs to a different "
                                     "employee."),
                             decided_by="workforce.queue", thread=thread,
                             lead=lead, channel=channel, tool_key=tool_key,
                             subject_type=subject_type, subject_id=subject_id,
                             severity="warning")
            if item.cancelled:
                return _deny(db, ctx, operation, code=C.D_OBJECTIVE_CANCELLED,
                             reason="The work item was cancelled.",
                             decided_by="workforce.queue", thread=thread,
                             lead=lead, channel=channel, tool_key=tool_key,
                             subject_type=subject_type, subject_id=subject_id)
            if item.terminal:
                return _deny(db, ctx, operation, code=C.D_OBJECTIVE_COMPLETE,
                             reason=("The work item is already in a terminal "
                                     "state (%s)." % item.state),
                             decided_by="workforce.queue", thread=thread,
                             lead=lead, channel=channel, tool_key=tool_key,
                             subject_type=subject_type, subject_id=subject_id)

    # ── 8. MAY THIS PERSON BE CONTACTED, ON THIS CHANNEL, NOW ──────────────
    elig = None
    if requires_eligibility and lead is not None and channel:
        # `now` is threaded through rather than read inside the gate so that
        # a follow-up executed at a simulated instant is judged AT THAT
        # INSTANT — the contact window, in particular, has to be answered for
        # the time the action actually runs.
        elig = elig_engine.evaluate(db, ctx=ctx, lead=lead, channel=channel,
                                    thread=thread, now=now)
        if elig.result == C.DENY:
            gate = _deny(db, ctx, operation, code=C.D_INELIGIBLE,
                         reason=(elig.reasons[0]["detail"] if elig.reasons
                                 else "The contact is not eligible."),
                         decided_by=elig.decided_by, thread=thread, lead=lead,
                         channel=channel, tool_key=tool_key,
                         subject_type=subject_type, subject_id=subject_id,
                         elig=elig)
            _react_to_ineligibility(db, ctx, thread, elig)
            return gate
        if elig.result == C.REQUIRES_REVIEW:
            gate = _deny(db, ctx, operation, code=C.D_REQUIRES_REVIEW,
                         reason=(elig.reasons[0]["detail"] if elig.reasons
                                 else "A person should look at this first."),
                         decided_by=elig.decided_by, thread=thread, lead=lead,
                         channel=channel, tool_key=tool_key,
                         subject_type=subject_type, subject_id=subject_id,
                         elig=elig)
            if thread is not None:
                comm_state.thread_state(db, thread, C.REVIEW_REQUIRED,
                                        reason=elig.primary_code)
                contracts.mirror_supervisor_event(
                    db, ctx, event_code=C.SUP_REVIEW_REQUIRED,
                    severity="info",
                    message="A conversation needs a person's decision.",
                    detail={"thread_id": thread.id,
                            "reasons": elig.reasons},
                    recommended_action="Review the contact's eligibility.")
            return gate

    # ── 9. BUDGET ──────────────────────────────────────────────────────────
    ok, code, reason = budget.check(
        db, ctx, operation=operation, channel=channel,
        thread_id=thread.id if thread is not None else None,
        thread_action_count=int(thread.action_count or 0)
        if thread is not None else 0)
    if not ok:
        gate = _deny(db, ctx, operation, code=code, reason=reason,
                     decided_by="budget", thread=thread, lead=lead,
                     channel=channel, tool_key=tool_key,
                     subject_type=subject_type, subject_id=subject_id,
                     severity="warning")
        contracts.mirror_supervisor_event(
            db, ctx, event_code=C.SUP_RUNAWAY, severity="warning",
            message=reason, detail={"operation": operation, "code": code},
            recommended_action="Raise the ceiling deliberately or stop the "
                               "work.")
        return gate

    # ── 10. HAS THIS ALREADY HAPPENED ──────────────────────────────────────
    action = AIOpsAction(
        organization_id=ctx.organization_id,
        thread_id=thread.id if thread is not None else None,
        employee_id=ctx.employee_id, work_item_id=wi_id, run_id=run_id,
        operation=operation, tool_key=tool_key, channel=channel,
        subject_type=subject_type, subject_id=subject_id,
        decision="allowed", decided_by="orchestrator",
        eligibility_result=(elig.result if elig is not None else None),
        activation_state=ctx.activation_state, status=None,
        arguments_digest=audit.digest(json.dumps(
            {k: v for k, v in sorted(arguments.items())}, default=str)),
        idempotency_key=idempotency_key, correlation_kind=correlation_kind,
        simulated=True, actor_kind=C.ACTOR_AI_EMPLOYEE,
        actor_id=ctx.employee_id)
    is_new, action = idempotency.claim(
        db, action, organization_id=ctx.organization_id,
        key=idempotency_key or "", finder=idempotency.find_action)
    if not is_new:
        code, reason = idempotency.duplicate_refusal(operation,
                                                     idempotency_key or "")
        audit.record(db, event_code="ops.duplicate_suppressed", ctx=ctx,
                     thread_id=action.thread_id, action_id=action.id,
                     subject_type=subject_type, subject_id=subject_id,
                     operation=operation, tool_key=tool_key, channel=channel,
                     decision="denied", denial_code=code,
                     authority="idempotency", outcome="suppressed",
                     message=reason)
        return Gate(allowed=False, operation=operation, denial_code=code,
                    denial_reason=reason, decided_by="idempotency",
                    duplicate=True, action=action, thread=thread, lead=lead,
                    channel=channel, tool_key=tool_key, eligibility=elig,
                    ctx=ctx)

    return Gate(allowed=True, operation=operation, action=action,
                thread=thread, lead=lead, channel=channel, tool_key=tool_key,
                eligibility=elig, ctx=ctx, decided_by="orchestrator",
                simulated=True)


def _react_to_ineligibility(db: Session, ctx: contracts.EmployeeContext,
                            thread: Optional[AIConversationThread],
                            elig: elig_engine.Decision) -> None:
    """An opt-out is not just a refusal — it is the end of the conversation.

    Recording "denied: contact opted out" and leaving the thread open would
    mean the next scheduled action asks the same question again tomorrow, and
    the day after. The stop is applied here, once, where the answer arrives.
    """
    if thread is None:
        return
    codes = {r.get("code") for r in (elig.reasons or [])}
    hard = {C.E_OPTED_OUT: C.STOP_OPT_OUT,
            C.E_DNC: C.STOP_DNC,
            C.E_SUPPRESSED: C.STOP_DNC,
            C.E_CHANNEL_PERMISSION: None,
            C.E_BAD_ADDRESS: C.STOP_INVALID_CONTACT,
            C.E_NO_ADDRESS: None}
    for code, stop_reason in hard.items():
        if code in codes and stop_reason:
            from app.services.ai_operations import stop as stop_controls
            stop_controls.stop_thread(db, ctx, thread, reason=stop_reason,
                                      actor_kind=C.ACTOR_SYSTEM,
                                      detail={"eligibility": elig.reasons})
            return
    # The platform's own refusal text carries the DNC case when the lead's
    # status is the reason, so it is matched here too rather than parsed.
    if C.E_PLATFORM_REFUSED in codes:
        for r in elig.reasons:
            detail = (r.get("detail") or "").lower()
            if "dnc" in detail or "suppression" in detail:
                from app.services.ai_operations import stop as stop_controls
                stop_controls.stop_thread(db, ctx, thread,
                                          reason=C.STOP_DNC,
                                          actor_kind=C.ACTOR_SYSTEM,
                                          detail={"eligibility": elig.reasons})
                return


def finish(db: Session, gate: Gate, *, status: str,
           result_summary: Optional[str] = None,
           error: Optional[str] = None,
           provider: Optional[str] = None,
           simulated: bool = True,
           cost_usd: float = 0.0,
           duration_ms: Optional[int] = None,
           communication_id: Optional[str] = None,
           next_action: Optional[str] = None,
           outcome: Optional[str] = None,
           human_involved: bool = False,
           detail: Optional[Dict[str, Any]] = None) -> None:
    """Close out an allowed action: counters, audit, mirrors, thread counts.

    Called exactly once per allowed gate. A caller that forgets leaves an
    action row with a NULL status, which the operations console shows as
    "started and never reported" — deliberately visible rather than tidied
    away, because an operation that vanished mid-flight is worth seeing.
    """
    action = gate.action
    ctx = gate.ctx
    if action is None or ctx is None:
        return
    action.status = status
    action.result_summary = (result_summary or "")[:2000] or None
    action.error = (error or "")[:480] or None
    action.provider = provider
    action.simulated = bool(simulated)
    action.estimated_cost_usd = cost_usd or None
    action.duration_ms = duration_ms
    action.communication_id = communication_id
    action.next_action = next_action
    action.human_involved = bool(human_involved)
    db.flush()

    if gate.thread is not None:
        gate.thread.action_count = int(gate.thread.action_count or 0) + 1
        db.flush()

    if status == "ok":
        budget.record_success(db, ctx, operation=gate.operation,
                              channel=gate.channel,
                              thread_id=gate.thread.id if gate.thread else None,
                              usd=cost_usd)
        contracts.mirror_performance(db, ctx, "operations_performed")
        if gate.channel:
            contracts.mirror_performance(db, ctx, "sends_%s" % gate.channel)
    elif status == "error":
        failures = budget.record_failure(db, ctx, channel=gate.channel)
        if gate.thread is not None:
            gate.thread.consecutive_failures = failures
            db.flush()

    audit.record(db, event_code="ops.operation_%s" % status, ctx=ctx,
                 severity=("warning" if status == "error" else "info"),
                 thread_id=action.thread_id, action_id=action.id,
                 communication_id=communication_id,
                 work_item_id=action.work_item_id,
                 subject_type=action.subject_type,
                 subject_id=action.subject_id, operation=gate.operation,
                 tool_key=gate.tool_key, channel=gate.channel,
                 provider=provider, decision="allowed",
                 authority="workforce.policy",
                 eligibility_result=(gate.eligibility.result
                                     if gate.eligibility else None),
                 outcome=outcome or status, human_involved=human_involved,
                 simulated=simulated, estimated_cost_usd=cost_usd or None,
                 next_action=next_action, message=result_summary,
                 detail=detail)

    contracts.mirror_tool_execution(
        db, ctx, tool_key=gate.tool_key or gate.operation, decision="allowed",
        denial_code=None, denial_reason=None, status=status,
        result_summary=(result_summary or "")[:480] or None,
        idempotency_key=action.idempotency_key,
        work_item_id=action.work_item_id, run_id=action.run_id,
        target_type=action.subject_type, target_id=action.subject_id,
        simulated=simulated, arguments_digest=action.arguments_digest,
        duration_ms=duration_ms)


# ═══════════════════════════════════════════════════════════════════════════
# THE CHANNEL OPERATIONS
# ═══════════════════════════════════════════════════════════════════════════
#
# ONE IMPLEMENTATION, THREE CHANNELS. `send_message`, `send_email` and
# `initiate_call` are thin: they name a channel and hand over. A per-channel
# send path is a per-channel place to forget a gate, and the third one is
# always the one that forgets.

def _new_communication(db: Session, gate: Gate, *, direction: str,
                       channel: str, to_address: Optional[str],
                       body: str, subject_line: Optional[str],
                       key: str, correlation_kind: str,
                       scheduled_action_id: Optional[str] = None):
    """Create the communication row BEFORE the provider is called.

    THAT ORDER IS THE TIMEOUT DEFENCE. A provider that times out may still
    have sent the message; the only safe assumption is that it did. With the
    row already written under its idempotency key, a retry is refused as a
    duplicate instead of sending a second text to a family that already got
    the first one. Writing the row afterwards would make every timeout a
    coin flip.
    """
    ctx = gate.ctx
    comm = AICommunication(
        organization_id=ctx.organization_id, thread_id=gate.thread.id,
        employee_id=ctx.employee_id,
        work_item_id=gate.thread.work_item_id,
        action_id=gate.action.id if gate.action is not None else None,
        subject_type=gate.thread.subject_type,
        subject_id=gate.thread.subject_id,
        direction=direction, channel=channel, state=C.QUEUED,
        to_address=to_address, subject_line=subject_line,
        body_preview=audit.preview(body), body_digest=audit.digest(body),
        body_length=len(body or ""), idempotency_key=key,
        correlation_kind=correlation_kind, simulated=True,
        eligibility_result=(gate.eligibility.result
                            if gate.eligibility else None),
        eligibility_reasons=(json.dumps(gate.eligibility.reasons)
                             if gate.eligibility else None),
        attempts=0)
    is_new, comm = idempotency.claim(
        db, comm, organization_id=ctx.organization_id, key=key,
        finder=idempotency.find_communication)
    return is_new, comm


def _sending_user(db: Session, gate: Gate):
    """Whose identity the message carries, for channels that need one."""
    from app.services.ai_operations.channels.sms_twilio import \
        resolve_sending_user
    return resolve_sending_user(
        db, lead=gate.lead, organization_id=gate.ctx.organization_id,
        preferred_user_id=gate.ctx.handoff_user_id)


def _perform_send(db: Session, gate: Gate, *, body: str,
                  subject_line: Optional[str] = None,
                  direction: str = C.OUTBOUND,
                  scheduled_action_id: Optional[str] = None,
                  expect_reply: bool = True) -> OperationResult:
    """The single outbound path. Everything above it has already said yes."""
    ctx = gate.ctx
    channel = gate.channel
    thread = gate.thread
    key = idempotency.key_for_send(
        thread_id=thread.id, channel=channel, body=body,
        subject_line=subject_line, attempt_group=scheduled_action_id)

    is_new, comm = _new_communication(
        db, gate, direction=direction, channel=channel,
        to_address=elig_engine.address_for(gate.lead, channel), body=body,
        subject_line=subject_line, key=key, correlation_kind=C.CORR_SEND,
        scheduled_action_id=scheduled_action_id)
    if not is_new:
        # The same message, on the same thread, already exists. This is the
        # duplicate-send guard firing at the database rather than at a check.
        code, reason = idempotency.duplicate_refusal("message", key)
        finish(db, gate, status="skipped", result_summary=reason,
               communication_id=comm.id, simulated=bool(comm.simulated),
               outcome="duplicate_suppressed")
        gate.allowed = False
        gate.duplicate = True
        gate.denial_code = code
        gate.denial_reason = reason
        return OperationResult(ok=False, gate=gate, communication=comm,
                               error=reason)

    comm_state.transition(db, comm, C.ELIGIBILITY_CHECK,
                          reason="gates passed", actor_kind=C.ACTOR_SYSTEM)
    comm_state.transition(db, comm, C.READY, reason="ready to send",
                          actor_kind=C.ACTOR_SYSTEM)

    # THE CLAIM IS WHAT STOPS TWO WORKERS SENDING THE SAME MESSAGE.
    if not comm_state.claim_for_send(db, comm):
        reason = ("Another worker is already sending this message "
                  "(state %s)." % comm.state)
        finish(db, gate, status="skipped", result_summary=reason,
               communication_id=comm.id, outcome="claim_lost")
        gate.allowed = False
        gate.duplicate = True
        gate.denial_code = C.D_DUPLICATE
        gate.denial_reason = reason
        return OperationResult(ok=False, gate=gate, communication=comm,
                               error=reason)

    comm.attempts = int(comm.attempts or 0) + 1
    db.flush()

    adapter, why = channels.resolve(db, ctx, channel, reaches_outside=True)
    req = channels.SendRequest(
        organization_id=ctx.organization_id, channel=channel,
        to_address=comm.to_address or "", body=body,
        subject=subject_line or "", lead=gate.lead,
        sending_user=(_sending_user(db, gate)
                      if channel == C.CHANNEL_SMS else None),
        thread_id=thread.id, communication_id=comm.id, idempotency_key=key,
        metadata={"employee_id": ctx.employee_id,
                  "operation": gate.operation})
    result = adapter.send(db, req)

    comm.provider = result.provider
    comm.provider_message_id = result.provider_message_id
    comm.provider_status = result.provider_status
    comm.provider_outcome = result.outcome
    comm.provider_error = (result.error or "")[:480] or None
    comm.simulated = bool(result.simulated)
    comm.estimated_cost_usd = result.estimated_cost_usd or None
    comm.duration_ms = result.duration_ms
    comm.platform_record_type = result.platform_record_type
    comm.platform_record_id = result.platform_record_id
    comm.voice_disposition = result.voice_disposition
    comm.voice_seconds = result.voice_seconds
    db.flush()

    # ── where the outcome leaves the message ───────────────────────────────
    if result.outcome in (C.P_ACCEPTED, C.P_DELIVERED, C.P_SIMULATED):
        if channel == C.CHANNEL_VOICE and result.voice_disposition:
            from app.services.ai_operations.channels.voice import \
                voice_outcome_to_state
            next_state = voice_outcome_to_state(result.voice_disposition)
        else:
            next_state = C.SENT
        comm_state.transition(db, comm, next_state,
                              reason=result.provider_status or result.outcome,
                              actor_kind=C.ACTOR_PROVIDER,
                              detail={"provider": result.provider})
        if next_state == C.SENT and expect_reply:
            comm_state.transition(db, comm, C.WAITING_FOR_RESPONSE,
                                  reason="awaiting a reply",
                                  actor_kind=C.ACTOR_SYSTEM)
            comm_state.thread_state(db, thread, C.WAITING_FOR_RESPONSE,
                                    reason="message sent")
        continuity.record_outbound(db, thread, channel)
        finish(db, gate, status="ok",
               result_summary=("%s %s via %s (%s)"
                               % (channel, direction, result.provider, why)),
               provider=result.provider, simulated=result.simulated,
               cost_usd=float(result.estimated_cost_usd or 0),
               duration_ms=result.duration_ms, communication_id=comm.id,
               outcome=result.outcome,
               next_action=("await_response" if expect_reply else None),
               detail={"adapter": adapter.key, "why": why,
                       "voice_disposition": result.voice_disposition})
        return OperationResult(ok=True, gate=gate, communication=comm,
                               provider_outcome=result.outcome,
                               provider=result.provider,
                               provider_message_id=result.provider_message_id,
                               detail={"why": why})

    if result.outcome == C.P_REJECTED:
        # The provider or the platform's own send path refused. Terminal for
        # this message: a retry would be refused identically.
        comm_state.transition(db, comm, C.BLOCKED,
                              reason=result.denial_code or "rejected",
                              actor_kind=C.ACTOR_PROVIDER,
                              detail={"error": result.error})
        finish(db, gate, status="skipped", result_summary=result.error,
               error=result.error, provider=result.provider,
               simulated=result.simulated, communication_id=comm.id,
               outcome=C.P_REJECTED,
               detail={"denial_code": result.denial_code})
        gate.denial_code = result.denial_code or C.D_PROVIDER_UNAVAILABLE
        gate.denial_reason = result.error
        return OperationResult(ok=False, gate=gate, communication=comm,
                               provider_outcome=result.outcome,
                               provider=result.provider, error=result.error)

    # Failure or timeout. The message stays recorded under its key, so the
    # retry path is a NEW decision by the follow-up engine rather than an
    # immediate second attempt at the provider.
    comm_state.transition(db, comm, C.FAILED,
                          reason=(C.D_PROVIDER_TIMEOUT
                                  if result.outcome == C.P_TIMEOUT
                                  else C.D_PROVIDER_FAILED),
                          actor_kind=C.ACTOR_PROVIDER,
                          detail={"error": result.error})
    finish(db, gate, status="error", result_summary=result.error,
           error=result.error, provider=result.provider,
           simulated=result.simulated, duration_ms=result.duration_ms,
           communication_id=comm.id, outcome=result.outcome,
           next_action="retry_after_backoff")
    contracts.mirror_supervisor_event(
        db, ctx, event_code=C.SUP_PROVIDER_FAILURE, severity="warning",
        message="A %s send failed: %s" % (channel, result.error or "unknown"),
        detail={"thread_id": thread.id, "outcome": result.outcome},
        recommended_action="Check the provider configuration for this "
                           "organization.")
    return OperationResult(ok=False, gate=gate, communication=comm,
                           provider_outcome=result.outcome,
                           provider=result.provider, error=result.error)


def send_message(db: Session, ctx: contracts.EmployeeContext, *,
                 subject_id: str, body: str,
                 thread: Optional[AIConversationThread] = None,
                 objective: Optional[str] = None,
                 work_item_id: Optional[str] = None,
                 run_id: Optional[str] = None,
                 scheduled_action_id: Optional[str] = None,
                 expect_reply: bool = True,
                 now: Optional[datetime] = None) -> OperationResult:
    """Send an SMS to the contact, if every gate says yes."""
    gate = begin(db, ctx, C.OP_SEND_MESSAGE, subject_id=subject_id,
                 channel=C.CHANNEL_SMS, thread=thread, objective=objective,
                 work_item_id=work_item_id, run_id=run_id,
                 correlation_kind=C.CORR_SEND,
                 arguments={"body_digest": audit.digest(body)},
                 idempotency_key=None, now=now)
    if not gate.allowed:
        return OperationResult(ok=False, gate=gate)
    return _perform_send(db, gate, body=body,
                         scheduled_action_id=scheduled_action_id,
                         expect_reply=expect_reply)


def send_email(db: Session, ctx: contracts.EmployeeContext, *,
               subject_id: str, subject_line: str, body: str,
               thread: Optional[AIConversationThread] = None,
               objective: Optional[str] = None,
               work_item_id: Optional[str] = None,
               run_id: Optional[str] = None,
               scheduled_action_id: Optional[str] = None,
               expect_reply: bool = True,
               now: Optional[datetime] = None) -> OperationResult:
    """Send an email to the contact, if every gate says yes."""
    gate = begin(db, ctx, C.OP_SEND_EMAIL, subject_id=subject_id,
                 channel=C.CHANNEL_EMAIL, thread=thread, objective=objective,
                 work_item_id=work_item_id, run_id=run_id,
                 correlation_kind=C.CORR_SEND, now=now,
                 arguments={"subject": subject_line,
                            "body_digest": audit.digest(body)})
    if not gate.allowed:
        return OperationResult(ok=False, gate=gate)
    return _perform_send(db, gate, body=body, subject_line=subject_line,
                         scheduled_action_id=scheduled_action_id,
                         expect_reply=expect_reply)


def initiate_call(db: Session, ctx: contracts.EmployeeContext, *,
                  subject_id: str, purpose: str = "",
                  thread: Optional[AIConversationThread] = None,
                  objective: Optional[str] = None,
                  work_item_id: Optional[str] = None,
                  run_id: Optional[str] = None,
                  now: Optional[datetime] = None) -> OperationResult:
    """Place an AI voice call.

    REFUSED IN THIS BUILD, and refused by the adapter rather than by an
    early return here — deliberately, so the whole chain (authority,
    eligibility, budget, idempotency, state machine, audit) is exercised for
    voice exactly as it is for text. The day voice is enabled, nothing above
    the adapter changes, which means nothing above the adapter is untested
    on that day.
    """
    gate = begin(db, ctx, C.OP_INITIATE_CALL, subject_id=subject_id,
                 channel=C.CHANNEL_VOICE, thread=thread, objective=objective,
                 work_item_id=work_item_id, run_id=run_id, now=now,
                 correlation_kind=C.CORR_SEND,
                 arguments={"purpose": purpose})
    if not gate.allowed:
        return OperationResult(ok=False, gate=gate)
    return _perform_send(db, gate, body=purpose or "AI voice call",
                         expect_reply=True)


def respond_to_inbound(db: Session, ctx: contracts.EmployeeContext, *,
                       thread: AIConversationThread, body: str,
                       channel: Optional[str] = None,
                       subject_line: Optional[str] = None,
                       run_id: Optional[str] = None,
                       now: Optional[datetime] = None) -> OperationResult:
    """Answer something the contact said, on the channel they said it.

    THE CHANNEL DEFAULTS TO THEIRS, NOT OURS. Replying to an email with a
    text is a channel switch, and a channel switch is an authority question
    that `begin` asks through the eligibility gate. Defaulting to the
    inbound channel means the ordinary case needs no special permission and
    the unusual one is checked.
    """
    channel = channel or thread.last_channel or C.CHANNEL_SMS
    if not continuity.may_switch_to(ctx, thread, channel):
        return OperationResult(
            ok=False,
            gate=_deny(db, ctx, C.OP_RESPOND_TO_INBOUND,
                       code=C.D_NOT_AUTHORIZED,
                       reason=("This employee is not configured for %s, so it "
                               "cannot reply on that channel." % channel),
                       decided_by="continuity", thread=thread,
                       channel=channel,
                       tool_key=contracts.tool_for(C.OP_RESPOND_TO_INBOUND,
                                                   channel=channel),
                       subject_type=thread.subject_type,
                       subject_id=thread.subject_id))
    gate = begin(db, ctx, C.OP_RESPOND_TO_INBOUND,
                 subject_id=thread.subject_id,
                 subject_type=thread.subject_type, channel=channel,
                 thread=thread, work_item_id=thread.work_item_id,
                 run_id=run_id, correlation_kind=C.CORR_SEND, now=now,
                 arguments={"body_digest": audit.digest(body)})
    if not gate.allowed:
        return OperationResult(ok=False, gate=gate)
    return _perform_send(db, gate, body=body, subject_line=subject_line,
                         expect_reply=True)


# ═══════════════════════════════════════════════════════════════════════════
# THE RECORD OPERATIONS
# ═══════════════════════════════════════════════════════════════════════════
#
# These touch the customer's data and not the person. They still pass the
# whole chain — authority, tenancy, ownership, objective state, budget,
# idempotency — because "it only writes a note" is how a tenant boundary
# gets crossed by something nobody thought was consequential.

def update_lead(db: Session, ctx: contracts.EmployeeContext, *,
                subject_id: str, facts: Dict[str, Any],
                thread: Optional[AIConversationThread] = None,
                run_id: Optional[str] = None) -> OperationResult:
    """Record what the employee learned, as FACTS on the record.

    DOES NOT REWRITE THE PLATFORM'S OWN VERDICT. `qualification.py` stays
    authoritative about whether a lead is qualified; this feeds it. An
    employee that could set the verdict directly would be an employee that
    can mark anybody qualified by saying so.
    """
    gate = begin(db, ctx, C.OP_UPDATE_LEAD, subject_id=subject_id,
                 thread=thread, run_id=run_id,
                 correlation_kind=C.CORR_PIPELINE,
                 arguments={"fields": sorted(facts.keys())},
                 idempotency_key=None, requires_eligibility=False)
    if not gate.allowed:
        return OperationResult(ok=False, gate=gate)

    written = []
    # AN ALLOW-LIST, NOT A SETATTR LOOP. An employee that could write any
    # column could write `organization_id`.
    allowed_fields = {"ai_lead_quality_note", "last_action_raw",
                      "status_reason_raw", "engagement_temperature"}
    for key, value in (facts or {}).items():
        if key in allowed_fields and hasattr(gate.lead, key):
            setattr(gate.lead, key, value)
            written.append(key)
    db.flush()
    finish(db, gate, status="ok",
           result_summary="Recorded %d fact(s) on the record."
                          % len(written),
           detail={"fields": written})
    return OperationResult(ok=True, gate=gate, detail={"fields": written})


def create_task(db: Session, ctx: contracts.EmployeeContext, *,
                subject_id: str, note: str,
                thread: Optional[AIConversationThread] = None,
                run_id: Optional[str] = None) -> OperationResult:
    """Leave a note on the record for a person to act on.

    Attributed to the AI employee in the note text itself, because the
    platform's note is authored by a user and an AI employee is not one —
    the attribution has to be visible rather than implied by a NULL.
    """
    gate = begin(db, ctx, C.OP_CREATE_TASK, subject_id=subject_id,
                 thread=thread, run_id=run_id, correlation_kind=C.CORR_TASK,
                 arguments={"note_digest": audit.digest(note)},
                 requires_eligibility=False)
    if not gate.allowed:
        return OperationResult(ok=False, gate=gate)
    stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    text = "[%s] AI %s: %s" % (stamp, ctx.name, (note or "").strip())
    recorded = False
    try:
        # APPENDED, NEVER REPLACED. `Lead.notes` is a single text column that
        # a person also writes into; an employee that assigned to it would
        # erase whatever an advisor had typed there.
        existing = getattr(gate.lead, "notes", None) or ""
        gate.lead.notes = (existing + ("\n" if existing else "") + text)[:8000]
        db.flush()
        recorded = True
    except Exception as exc:                                 # noqa: BLE001
        # A different shape on this branch. The operations audit still
        # carries the note, so nothing is lost — it is just not on the lead's
        # own record, and the result says so rather than implying it is.
        _log.info("ai_operations: lead note not written (%s)", exc)
    finish(db, gate, status="ok", result_summary=audit.preview(text),
           detail={"on_lead_timeline": recorded})
    return OperationResult(ok=True, gate=gate,
                           detail={"on_lead_timeline": recorded})


def record_outcome(db: Session, ctx: contracts.EmployeeContext, *,
                   subject_id: str, outcome: str, summary: str = "",
                   thread: Optional[AIConversationThread] = None,
                   stop_reason: Optional[str] = None,
                   run_id: Optional[str] = None) -> OperationResult:
    """Close the loop: what came of this conversation.

    The outcome is reported to T6's work item (which owns the record's state
    machine) and to the performance ledger, and the thread is closed here.
    Three systems learning one fact from one call, rather than three places
    that can disagree about whether an appointment was booked.
    """
    gate = begin(db, ctx, C.OP_RECORD_OUTCOME, subject_id=subject_id,
                 thread=thread, run_id=run_id,
                 correlation_kind=C.CORR_PIPELINE,
                 arguments={"outcome": outcome},
                 requires_eligibility=False)
    if not gate.allowed:
        return OperationResult(ok=False, gate=gate)

    moved = False
    if gate.thread is not None and gate.thread.work_item_id:
        moved = contracts.request_work_item_state(
            db, gate.thread.work_item_id,
            organization_id=ctx.organization_id, to_state=outcome,
            reason=summary or "reported by the operations layer")
    contracts.mirror_performance(db, ctx, "outcome_%s" % outcome)
    if gate.thread is not None:
        gate.thread.summary = summary or gate.thread.summary
        if stop_reason:
            from app.services.ai_operations import stop as stop_controls
            stop_controls.stop_thread(db, ctx, gate.thread,
                                      reason=stop_reason,
                                      actor_kind=C.ACTOR_AI_EMPLOYEE,
                                      actor_id=ctx.employee_id)
        db.flush()
    finish(db, gate, status="ok",
           result_summary="Outcome recorded: %s" % outcome, outcome=outcome,
           next_action=None, detail={"work_item_moved": moved})
    return OperationResult(ok=True, gate=gate,
                           detail={"work_item_moved": moved,
                                   "outcome": outcome})
