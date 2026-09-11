"""THE RUN LOOP — bounded, observable, and unable to run away.

THERE IS NO OPEN-ENDED AUTONOMOUS LOOP IN THIS ENGINE. Section 14 asks for
every execution to declare its objective, tool set, authority, work item,
iteration limit, time limit and terminal conditions before it starts. That is
literally what `execute` does: it opens an `ai_employee_runs` row carrying all
of them, and the ceilings are enforced by the gateway and by this loop rather
than by the model choosing to stop.

WHAT ONE RUN IS. One employee, one work item, one objective, a handful of
turns. It ends when the work item reaches a terminal state, when the employee
decides to wait, when a ceiling is hit, or when something fails too many times.
It does not end because the model said it was finished — a model that says it
is finished without having moved the record leaves the item where it was, and
the run is recorded as producing nothing, which is exactly what happened.

THE FOUR CEILINGS, all of them hard:
    iterations          policy.max_iterations, clamped to a code ceiling
    tool calls          policy.max_tool_calls, enforced in tools.authorize
    consecutive errors  DEFAULT_MAX_CONSECUTIVE_FAILURES, then Needs Review
    wall clock          DEFAULT_RUN_SECONDS

A REFUSAL IS AN OBSERVATION, NOT A CRASH. When the gateway refuses, the reason
goes back into the next turn's context and the loop continues — which is what a
person would do on being told "that time is taken". What it may NOT do is
retry the identical call: `_seen` records every (tool, arguments) pair and a
repeat is stopped here, before the gateway, so a planner in a rut costs one
turn rather than the whole budget.
"""

import json
import logging
import time as _time
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.models import Lead
from app.models.workforce_models import AIEmployee, AIEmployeeRun, AIWorkItem
from app.services.workforce import activation as wf_activation
from app.services.workforce import audit as wf_audit
from app.services.workforce import constants as C
from app.services.workforce import eligibility as wf_eligibility
from app.services.workforce import knowledge as wf_knowledge
from app.services.workforce import memory as wf_memory
from app.services.workforce import model_router
from app.services.workforce import performance
from app.services.workforce import policy as wf_policy
from app.services.workforce import queue as wf_queue
from app.services.workforce import tools as wf_tools

_log = logging.getLogger(__name__)


def _lead_for(db: Session, item: AIWorkItem) -> Optional[Lead]:
    if item.subject_type != "lead":
        return None
    return (db.query(Lead)
            .filter(Lead.id == item.subject_id,
                    Lead.organization_id == item.organization_id).first())


def _start_run(db: Session, employee: AIEmployee, item: Optional[AIWorkItem],
               *, objective: str, mode: str, trigger: str) -> AIEmployeeRun:
    run = AIEmployeeRun(
        organization_id=employee.organization_id, employee_id=employee.id,
        work_item_id=getattr(item, "id", None), objective=objective[:2000],
        trigger=trigger, mode=mode, status="running",
        model_capability=model_router.PLAN)
    db.add(run)
    db.flush()
    performance.bump(db, employee, "runs")
    return run


def _finish_run(db: Session, run: AIEmployeeRun, status: str, *,
                summary: str = "", abort_reason: Optional[str] = None,
                error: Optional[str] = None, started: float = 0.0) -> None:
    run.status = status
    run.ended_at = datetime.utcnow()
    run.duration_ms = int((_time.time() - started) * 1000) if started else None
    run.summary = (summary or "")[:4000] or None
    run.abort_reason = (abort_reason or "")[:255] or None
    run.error = (error or "")[:255] or None
    db.flush()


def ensure_eligibility(db: Session, employee: AIEmployee, item: AIWorkItem, *,
                       pol=None, claim_token: Optional[str] = None,
                       now: Optional[datetime] = None,
                       run_id: Optional[str] = None) -> Dict:
    """Decide the item's eligibility and move it accordingly.

    RUN BEFORE EVERY WORKING TURN, not once at assignment. A suppression added
    last night outranks an assignment made last week, and the only way that is
    true is if the check happens on the way in each time.

    The channel question is "is there ANY enabled channel this person may be
    contacted on". A record with a dead email and a good phone is eligible;
    a record denied on every channel is not, and the REASON that closes it is
    the strongest one — an opt-out closes the item as DO_NOT_CONTACT, a missing
    address as BAD_CONTACT, anything else as NEEDS_REVIEW.
    """
    now = now or datetime.utcnow()
    pol = pol or wf_policy.resolve(db, employee)

    # ASSIGNED -> ELIGIBILITY_PENDING FIRST, ALWAYS.
    #
    # `assigned` has no edge to `eligible`, on purpose: an item cannot be
    # declared eligible without the check having visibly started, so the
    # timeline reads "assigned -> checking -> eligible" rather than jumping.
    # This is also what gives the deny branches below their edges — every
    # terminal reason is reachable from `eligibility_pending` and only two of
    # them are reachable from `assigned`.
    if item.state == C.ASSIGNED:
        wf_queue.transition(db, item, C.ELIGIBILITY_PENDING,
                            reason="checking whether this person may be "
                                   "contacted",
                            actor_kind=C.ACTOR_SYSTEM, run_id=run_id,
                            claim_token=claim_token)

    lead = _lead_for(db, item)
    if lead is None:
        wf_queue.transition(db, item, C.NEEDS_REVIEW,
                            reason="the assigned record could not be read",
                            actor_kind=C.ACTOR_SYSTEM, run_id=run_id,
                            claim_token=claim_token)
        return {"result": C.DENY, "state": item.state,
                "reason": "record_not_found"}

    results = {}
    for channel in sorted(pol.channels):
        verdict = wf_eligibility.evaluate_and_record(
            db, employee=employee, lead=lead, channel=channel, pol=pol,
            work_item_id=item.id, now=now, enforce_hours=False,
            enforce_frequency=False)
        results[channel] = verdict

    allowed = [ch for ch, v in results.items() if v.result == C.ALLOW]
    review = [ch for ch, v in results.items() if v.result == C.REQUIRES_REVIEW]

    item.eligibility_checked_at = now
    item.eligibility_state = (C.ALLOW if allowed else
                              (C.REQUIRES_REVIEW if review else C.DENY))
    item.eligibility_reasons = json.dumps(
        {ch: v.reasons for ch, v in results.items()})[:8000]

    if allowed:
        performance.bump(db, employee, "records_eligible", now=now)
        if item.state in (C.ASSIGNED, C.ELIGIBILITY_PENDING):
            wf_queue.transition(db, item, C.ELIGIBLE,
                                reason="eligible on %s" % ", ".join(allowed),
                                actor_kind=C.ACTOR_SYSTEM, run_id=run_id,
                                claim_token=claim_token)
        return {"result": C.ALLOW, "channels": allowed, "state": item.state}

    if review:
        performance.bump(db, employee, "records_review", now=now)
        wf_queue.transition(db, item, C.NEEDS_REVIEW,
                            reason="a person must review this contact",
                            actor_kind=C.ACTOR_SYSTEM, run_id=run_id,
                            claim_token=claim_token)
        return {"result": C.REQUIRES_REVIEW, "state": item.state}

    performance.bump(db, employee, "records_denied", now=now)
    codes = {r.get("code") for v in results.values() for r in v.reasons}
    if {"dnc", "opted_out", "suppressed", "record_is_dnc"} & codes:
        target, why = C.DO_NOT_CONTACT, "the platform records an opt-out"
    elif {"missing_phone", "missing_email", "invalid_phone", "invalid_email",
          "flagged_bad_email", "no_address_for_channel"} & codes:
        target, why = C.BAD_CONTACT, "no usable way to reach this person"
    elif "record_says_not_interested" in codes:
        target, why = C.NOT_INTERESTED, "the record is already marked declined"
    else:
        # AN UNRECOGNISED DENIAL IS A REVIEW, NOT A CLOSURE. Closing a record
        # on a reason nobody has read is how a whole cohort silently disappears
        # from a queue.
        target, why = C.NEEDS_REVIEW, "not contactable — reason needs a person"
    wf_queue.transition(db, item, target, reason=why,
                        actor_kind=C.ACTOR_SYSTEM, run_id=run_id,
                        claim_token=claim_token)
    # ONLY `records_denied` IS COUNTED HERE, and the outcome counter is
    # deliberately NOT bumped.
    #
    # THE BUG THIS FIXES, exactly as it read. A dormant database contains
    # people who opted out years ago. Closing those items as DO_NOT_CONTACT and
    # counting each one as an `opt_outs` made the Performance panel report an
    # opt-out rate of 75% — against twelve messages, from nine opt-outs the
    # employee had nothing to do with. A customer reading that would reasonably
    # conclude the AI was driving their database away.
    #
    # `opt_outs` now means "somebody opted out in response to this employee",
    # which is the number an operator actually needs. Records that arrived
    # already unreachable are `records_denied`, and the eligibility report
    # breaks those down by reason.
    return {"result": C.DENY, "state": item.state, "codes": sorted(codes)}


def _build_context(db: Session, employee: AIEmployee, item: AIWorkItem,
                   lead: Optional[Lead], objective: str,
                   observations: List[Dict]) -> Dict:
    history: List[Dict] = []
    knowledge_hits: List[Dict] = []
    if lead is not None:
        from app.models.models import EmailMessage, Message, Reply
        for m in (db.query(Message).filter(Message.lead_id == lead.id)
                  .order_by(Message.sent_at.desc()).limit(10).all()):
            history.append({"direction": "outbound", "channel": "sms",
                            "at": m.sent_at.isoformat() if m.sent_at else None,
                            "body": m.body})
        for e in (db.query(EmailMessage)
                  .filter(EmailMessage.lead_id == lead.id)
                  .order_by(EmailMessage.sent_at.desc()).limit(10).all()):
            history.append({"direction": "outbound", "channel": "email",
                            "at": e.sent_at.isoformat() if e.sent_at else None,
                            "body": e.body_html})
        for r in (db.query(Reply).filter(Reply.lead_id == lead.id)
                  .order_by(Reply.received_at.desc()).limit(10).all()):
            history.append({"direction": "inbound", "channel": r.source,
                            "at": (r.received_at.isoformat()
                                   if r.received_at else None),
                            "body": r.body})
        history.sort(key=lambda d: d.get("at") or "")

    ctx = wf_memory.build_context(db, employee=employee, lead=lead,
                                  work_item=item, objective=objective,
                                  history=history, knowledge=knowledge_hits)
    from app.models.models import Organization
    org = (db.query(Organization)
           .filter(Organization.id == employee.organization_id).first())
    if org is not None:
        ctx["facts"]["business_name"] = org.brand_name or org.name
    ctx["observations"] = observations[-8:]
    return ctx


def execute(db: Session, employee: AIEmployee, item: AIWorkItem, *,
            claim_token: Optional[str] = None, trigger: str = "scheduled",
            actor_user_id: Optional[str] = None,
            now: Optional[datetime] = None,
            max_seconds: int = C.DEFAULT_RUN_SECONDS) -> Dict:
    """ONE BOUNDED RUN against ONE work item."""
    started = _time.time()
    now = now or datetime.utcnow()
    pol = wf_policy.resolve(db, employee)
    resolved = wf_activation.resolve(db, employee=employee)

    if not resolved.may_run:
        return {"ran": False, "reason": "activation", "state": resolved.state,
                "killed": resolved.killed, "work_item_id": item.id}

    objective = (employee.objective
                 or (pol.template.default_objective if pol.template else "")
                 or "work this record toward a legitimate outcome")
    run = _start_run(db, employee, item, objective=objective,
                     mode=resolved.state, trigger=trigger)

    ctx = wf_tools.ToolContext(db, employee, policy=pol, work_item=item,
                               run=run, claim_token=claim_token,
                               actor_user_id=actor_user_id, now=now,
                               mode=resolved.state, trigger=trigger)

    observations: List[Dict] = []
    try:
        elig = ensure_eligibility(db, employee, item, pol=pol,
                                  claim_token=claim_token, now=now,
                                  run_id=run.id)
        if elig["result"] != C.ALLOW:
            _finish_run(db, run, "completed",
                        summary="Closed at eligibility: %s" % elig["result"],
                        started=started)
            return {"ran": True, "run_id": run.id, "work_item_id": item.id,
                    "state": item.state, "outcome": item.outcome,
                    "iterations": 0, "observations": [],
                    "ended_because": "eligibility"}

        if item.state == C.ELIGIBLE:
            wf_queue.transition(db, item, C.WORKING, reason="run started",
                                actor_kind=C.ACTOR_AI_EMPLOYEE,
                                actor_id=employee.id, run_id=run.id,
                                claim_token=claim_token)

        lead = _lead_for(db, item)
        available = wf_tools.available_tools(ctx)
        seen = set()
        consecutive_failures = 0
        ended_because = "iterations"

        for turn in range(int(pol.max_iterations)):
            run.iterations = turn + 1

            if _time.time() - started > max_seconds:
                ended_because = "time_limit"
                break

            # THE PAUSE AND KILL ARE RE-READ EVERY TURN, not only per tool
            # call. A run that is mid-loop when somebody hits pause should stop
            # at the next turn rather than finish its plan.
            live = wf_activation.resolve(db, employee=employee)
            if not live.may_run:
                ended_because = "stopped"
                break

            context = _build_context(db, employee, item, lead, objective,
                                     observations)
            proposal = model_router.plan(model_router.PLAN, context,
                                         tools=available, objective=objective)
            run.provider = proposal.provider
            run.model_name = proposal.model
            if proposal.prompt_tokens:
                run.prompt_tokens = (run.prompt_tokens or 0) + proposal.prompt_tokens
            if proposal.completion_tokens:
                run.completion_tokens = ((run.completion_tokens or 0)
                                         + proposal.completion_tokens)

            if proposal.parse_error:
                # SECTION 41: a malformed decision never becomes an action.
                observations.append({"error": "unreadable decision",
                                     "detail": proposal.parse_error})
                wf_tools.invoke(ctx, "employee.request_review",
                                {"reason": "The planner produced an "
                                           "unreadable decision."})
                ended_because = "parse_error"
                break

            if not proposal.tool:
                wf_tools.invoke(ctx, "employee.request_review",
                                {"reason": proposal.rationale
                                 or "The planner had no next action."})
                ended_because = "no_action"
                break

            signature = (proposal.tool,
                         wf_audit.digest(proposal.arguments))
            if signature in seen:
                # A PLANNER IN A RUT COSTS ONE TURN, NOT THE BUDGET.
                observations.append({"tool": proposal.tool,
                                     "result": "refused: already tried this "
                                               "exact call in this run"})
                consecutive_failures += 1
                if consecutive_failures >= C.DEFAULT_MAX_CONSECUTIVE_FAILURES:
                    wf_tools.invoke(ctx, "employee.request_review",
                                    {"reason": "The planner repeated itself."})
                    ended_because = "repetition"
                    break
                continue
            seen.add(signature)

            result = wf_tools.invoke(ctx, proposal.tool, proposal.arguments)
            # THE OBSERVATION CARRIES THE STRUCTURED RESULT, not only a
            # rendering of it. A planner that is handed "availability_status:
            # ok, 12 slots" as a truncated string cannot then book one of them,
            # and the first version of this loop produced exactly that: an
            # employee that read the calendar, could not see what it had read,
            # and asked again until the repetition guard stopped it.
            observations.append({
                "tool": proposal.tool,
                "ok": result.ok,
                "decision": result.decision,
                "denial_code": result.denial_code,
                "data": result.data if result.ok else None,
                "result": (result.denial_reason if not result.ok
                           else _short(result.data)),
            })

            if result.ok:
                consecutive_failures = 0
                if result.terminal_outcome or item.state in C.TERMINAL_STATES \
                        or item.state == C.NEEDS_REVIEW:
                    ended_because = "terminal"
                    break
                if item.state == C.WAITING_FOR_RESPONSE:
                    ended_because = "waiting"
                    break
            else:
                if result.denial_code in (C.DENY_KILLED,
                                          C.DENY_EMPLOYEE_PAUSED,
                                          C.DENY_ACTIVATION_STAGE):
                    ended_because = "stopped"
                    break
                if result.denial_code == C.DENY_RUN_BUDGET:
                    ended_because = "tool_budget"
                    break
                performance.bump(db, employee, "policy_blocks", now=now)
                consecutive_failures += 1
                if consecutive_failures >= C.DEFAULT_MAX_CONSECUTIVE_FAILURES:
                    wf_tools.invoke(ctx, "employee.request_review",
                                    {"reason": "Too many refused or failed "
                                               "steps in one run."})
                    ended_because = "failures"
                    break

        if ended_because in ("iterations", "time_limit", "tool_budget"):
            performance.bump(db, employee, "runs_aborted", now=now)

        _finish_run(db, run, "completed",
                    summary="%d turn(s); ended: %s; state: %s"
                            % (run.iterations, ended_because, item.state),
                    abort_reason=(ended_because
                                  if ended_because in ("iterations",
                                                       "time_limit",
                                                       "tool_budget",
                                                       "failures",
                                                       "repetition") else None),
                    started=started)
        return {"ran": True, "run_id": run.id, "work_item_id": item.id,
                "state": item.state, "outcome": item.outcome,
                "iterations": run.iterations, "tool_calls": run.tool_calls,
                "denied_tool_calls": run.denied_tool_calls,
                "observations": observations, "ended_because": ended_because,
                "mode": resolved.state}

    except Exception as exc:                                 # noqa: BLE001
        _log.exception("workforce runtime: run failed for item %s", item.id)
        _finish_run(db, run, "failed", error="%s: %s" % (type(exc).__name__,
                                                         exc),
                    started=started)
        try:
            exhausted = wf_queue.record_failure(db, item, str(exc)[:120],
                                                run_id=run.id)
            if exhausted:
                wf_queue.transition(db, item, C.NEEDS_REVIEW,
                                    reason="repeated failures",
                                    actor_kind=C.ACTOR_SYSTEM, run_id=run.id)
        except Exception:                                    # noqa: BLE001
            _log.exception("workforce runtime: could not record the failure")
        return {"ran": True, "run_id": run.id, "work_item_id": item.id,
                "state": item.state, "error": str(exc)[:200],
                "ended_because": "error"}


def _short(data) -> str:
    try:
        return json.dumps(data, default=str)[:220]
    except (TypeError, ValueError):
        return str(data)[:220]


def run_employee(db: Session, employee: AIEmployee, *, limit: int = 5,
                 trigger: str = "scheduled", now: Optional[datetime] = None,
                 actor_user_id: Optional[str] = None) -> Dict:
    """Claim up to `limit` items and work them, one bounded run each.

    Claim-then-run rather than select-then-run: see queue.claim for why the
    conditional UPDATE is the lock. A run that fails releases its item with a
    backoff rather than holding the lease until it expires.
    """
    now = now or datetime.utcnow()
    resolved = wf_activation.resolve(db, employee=employee)
    if not resolved.may_run:
        # THE SAME SHAPE AS A REAL RUN, deliberately. A caller that has to
        # branch on which keys are present is a caller that will one day read
        # `ran` off a dict that does not have it.
        return {"employee_id": employee.id, "ran": 0, "claimed": 0,
                "mode": resolved.state,
                "skipped_reason": "activation:%s%s"
                                  % (resolved.state,
                                     " (killed)" if resolved.killed else ""),
                "results": []}

    results = []
    claimed = wf_queue.claim(db, employee, limit=limit, now=now)
    for item, token in claimed:
        outcome = execute(db, employee, item, claim_token=token,
                          trigger=trigger, actor_user_id=actor_user_id,
                          now=now)
        results.append(outcome)
        wf_queue.release(db, item, token)
    return {"employee_id": employee.id, "ran": len(results),
            "claimed": len(claimed), "mode": resolved.state,
            "results": results}


def run_organization(db: Session, organization_id: str, *,
                     per_employee: int = 5, trigger: str = "scheduled",
                     now: Optional[datetime] = None) -> Dict:
    """Every active employee in one customer workspace."""
    employees = (db.query(AIEmployee)
                 .filter(AIEmployee.organization_id == organization_id,
                         AIEmployee.status == "active",
                         AIEmployee.paused_at.is_(None))
                 .all())
    out = []
    for emp in employees:
        out.append(run_employee(db, emp, limit=per_employee, trigger=trigger,
                                now=now))
    return {"organization_id": organization_id,
            "employees": len(employees), "results": out}
