"""THE T6 BOUNDARY. Every workforce concept enters this layer through here.

WHY A BOUNDARY MODULE EXISTS AT ALL. T6 — the AI Workforce engine — is being
built in its own worktree at the same time as this one. Its tables, its tool
registry, its activation resolver and its authority policy are the contracts
this layer must consume, and the one thing this layer must NOT do is grow a
second opinion about any of them. A competing `AIEmployee`, a second tool
registry or a private activation resolver would be the architectural failure
the brief names explicitly, and it would be discovered as a security bug: two
authority answers means one of them is the one nobody checks.

So there is exactly one import surface, it is this file, and everything below
it in this package depends on the plain objects this file returns rather than
on T6's modules directly. Two consequences follow, and both are deliberate:

    1. WHEN T6 IS PRESENT, THIS FILE DELEGATES AND ADDS NOTHING. Authority is
       answered by `workforce.policy`, activation by `workforce.activation`,
       contact eligibility by `workforce.eligibility` where it exists, and
       every consequential call is mirrored into `ai_tool_executions` so T6's
       own audit sees the operations this layer performed.

    2. WHEN T6 IS NOT ON THIS BRANCH, THIS FILE STILL ANSWERS — SAFELY. The
       operations layer can be proven end to end against an EmployeeContext
       that was DECLARED rather than loaded, which is what the synthetic
       profiles and the evaluation harness use. A declared context is marked
       as such, and `source == "declared"` FORCES THE SIMULATED ADAPTER no
       matter what any flag says. That is the property that makes this
       fallback safe rather than a hole: the fallback cannot reach a person,
       so the worst a wrong declaration can do is produce a wrong simulation.

WHAT THIS FILE NEVER DOES. It never writes to a T6 table that would change
T6's own state machine — transitions go through T6's queue module when it
exists and are otherwise recorded on this layer's thread and skipped. It
never invents a tool key. It never widens an authority: every path here can
only narrow or refuse.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

from app.services.ai_operations import constants as C

_log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# WHAT OF T6 IS ACTUALLY HERE
# ═══════════════════════════════════════════════════════════════════════════
#
# Probed once at import, reported honestly, and never assumed. A missing
# module is a normal state on a branch cut from main before T6 merged, not an
# error — and it must never be a reason to fail open.

def _try(path: str):
    try:
        mod = __import__(path, fromlist=["*"])
        return mod
    except Exception as exc:                                 # noqa: BLE001
        _log.debug("ai_operations: %s unavailable (%s)", path, exc)
        return None


_wf_constants = _try("app.services.workforce.constants")
_wf_registry = _try("app.services.workforce.registry")
_wf_policy = _try("app.services.workforce.policy")
_wf_activation = _try("app.services.workforce.activation")
_wf_eligibility = _try("app.services.workforce.eligibility")
_wf_queue = _try("app.services.workforce.queue")
_wf_models = _try("app.models.workforce_models")

T6_PRESENT = _wf_models is not None and _wf_policy is not None


def availability() -> Dict[str, bool]:
    """Which T6 contracts this deployment actually has. Shown in the report
    and on the operations console, because "T7 is consuming T6" is a claim
    that should be checkable rather than asserted."""
    return {
        "models": _wf_models is not None,
        "constants": _wf_constants is not None,
        "registry": _wf_registry is not None,
        "policy": _wf_policy is not None,
        "activation": _wf_activation is not None,
        "eligibility": _wf_eligibility is not None,
        "queue": _wf_queue is not None,
    }


# ═══════════════════════════════════════════════════════════════════════════
# OPERATION → TOOL AUTHORITY
# ═══════════════════════════════════════════════════════════════════════════
#
# An operation is performed only if the employee holds the T6 TOOL whose
# authority it consumes. This mapping is the whole reason an operations layer
# cannot invent authority for itself: there is no operation whose permission
# is answered by this package.
#
# An operation missing from this map is UNAUTHORIZED BY CONSTRUCTION — the
# lookup returns None and the orchestrator refuses. Adding an operation and
# forgetting the mapping therefore fails closed, which is the direction a
# mistake should fail in.
TOOL_FOR_OPERATION: Dict[str, str] = {
    C.OP_SEND_MESSAGE: "conversation.send_sms",
    C.OP_SEND_EMAIL: "conversation.send_email",
    C.OP_INITIATE_CALL: "conversation.place_call",
    # A reply to an inbound message is a send on the channel it answers; the
    # orchestrator resolves the channel first and looks the tool up again, so
    # this entry is the SMS default only for argument validation.
    C.OP_RESPOND_TO_INBOUND: "conversation.send_sms",
    C.OP_SCHEDULE_FOLLOWUP: "employee.wait_for_response",
    C.OP_BOOK_APPOINTMENT: "appointment.book",
    C.OP_RESCHEDULE_APPOINTMENT: "appointment.reschedule",
    # Cancelling is a reschedule-class authority; there is no separate T6
    # tool, and inventing one here would be exactly the competing-registry
    # mistake. An employee that may not reschedule may not cancel.
    C.OP_CANCEL_APPOINTMENT: "appointment.reschedule",
    C.OP_TRANSFER_TO_HUMAN: "handoff.create",
    C.OP_UPDATE_LEAD: "lead.update_qualification",
    C.OP_CREATE_TASK: "lead.add_note",
    C.OP_RECORD_OUTCOME: "lead.add_note",
}

# Which channel each operation reaches on, where it reaches one at all.
CHANNEL_FOR_OPERATION: Dict[str, Optional[str]] = {
    C.OP_SEND_MESSAGE: C.CHANNEL_SMS,
    C.OP_SEND_EMAIL: C.CHANNEL_EMAIL,
    C.OP_INITIATE_CALL: C.CHANNEL_VOICE,
    C.OP_RESPOND_TO_INBOUND: None,      # resolved from the inbound event
    C.OP_SCHEDULE_FOLLOWUP: None,
    C.OP_BOOK_APPOINTMENT: None,
    C.OP_RESCHEDULE_APPOINTMENT: None,
    C.OP_CANCEL_APPOINTMENT: None,
    C.OP_TRANSFER_TO_HUMAN: None,
    C.OP_UPDATE_LEAD: None,
    C.OP_CREATE_TASK: None,
    C.OP_RECORD_OUTCOME: None,
}

# Operations that touch a real person or a real provider. These are the ones
# the activation stage gates and the ones the simulated adapter stands in for.
REACHING_OPERATIONS = frozenset({
    C.OP_SEND_MESSAGE, C.OP_SEND_EMAIL, C.OP_INITIATE_CALL,
    C.OP_RESPOND_TO_INBOUND, C.OP_BOOK_APPOINTMENT,
    C.OP_RESCHEDULE_APPOINTMENT, C.OP_CANCEL_APPOINTMENT,
})


def tool_for(operation: str, *, channel: Optional[str] = None) -> Optional[str]:
    """The T6 tool key an operation consumes, or None if it has no mapping."""
    if operation == C.OP_RESPOND_TO_INBOUND:
        if channel == C.CHANNEL_EMAIL:
            return "conversation.send_email"
        if channel == C.CHANNEL_VOICE:
            return "conversation.place_call"
        return "conversation.send_sms"
    return TOOL_FOR_OPERATION.get(operation)


# ═══════════════════════════════════════════════════════════════════════════
# THE EMPLOYEE CONTEXT — the only shape the rest of this package knows
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class EmployeeContext:
    """Everything the operations layer needs to know about the actor.

    Built from T6's rows when T6 is present, and DECLARED when it is not.
    `source` is carried rather than inferred because it changes behaviour:
    a declared context may never resolve a live adapter, and the audit says
    which kind of context authorized every action.
    """

    employee_id: str
    organization_id: str
    name: str = "AI Employee"
    job_role: str = ""
    template_key: str = ""
    platform_id: Optional[str] = None

    # Authority, already intersected by T6 (template ∩ brand ∩ grant).
    tool_keys: Set[str] = field(default_factory=set)
    channels: Set[str] = field(default_factory=set)

    # Activation, resolved most-restrictive-wins across four scopes.
    activation_state: str = "off"
    killed: bool = False
    may_run: bool = False
    may_execute: bool = False

    status: str = "draft"
    paused: bool = False
    pause_reason: Optional[str] = None

    handoff_user_id: Optional[str] = None
    handoff_queue: Optional[str] = None

    timezone: str = C.DEFAULT_WINDOW_TIMEZONE
    operating_hours: Dict[str, Any] = field(default_factory=dict)

    max_actions: int = C.DEFAULT_MAX_ACTIONS_PER_OBJECTIVE
    max_attempts: int = C.DEFAULT_MAX_ATTEMPTS_PER_COMM
    channel_daily_cap: int = C.DEFAULT_CHANNEL_DAILY_CAP
    cost_ceiling_usd: float = C.DEFAULT_COST_CEILING_USD
    daily_cap: Optional[int] = None

    escalations: List[str] = field(default_factory=list)
    source: str = "declared"            # "t6" | "declared"
    activation_chain: List[Dict] = field(default_factory=list)

    # ── derived answers ────────────────────────────────────────────────────

    @property
    def is_declared(self) -> bool:
        return self.source != "t6"

    def holds_tool(self, tool_key: Optional[str]) -> bool:
        if not tool_key:
            return False
        return tool_key in self.tool_keys

    def channel_allowed(self, channel: Optional[str]) -> bool:
        if not channel:
            return True
        return channel in self.channels

    def as_dict(self) -> Dict:
        return {
            "employee_id": self.employee_id,
            "organization_id": self.organization_id,
            "name": self.name,
            "job_role": self.job_role,
            "template_key": self.template_key,
            "platform_id": self.platform_id,
            "channels": sorted(self.channels),
            "tool_keys": sorted(self.tool_keys),
            "activation_state": self.activation_state,
            "killed": self.killed,
            "may_run": self.may_run,
            "may_execute": self.may_execute,
            "status": self.status,
            "paused": self.paused,
            "pause_reason": self.pause_reason,
            "handoff_user_id": self.handoff_user_id,
            "handoff_queue": self.handoff_queue,
            "timezone": self.timezone,
            "source": self.source,
            "activation_chain": list(self.activation_chain),
        }


def declare_employee_context(**kwargs) -> EmployeeContext:
    """Build a context WITHOUT T6. Simulation and evaluation only.

    THE SAFETY PROPERTY. `source` stays "declared", and `channels.resolve`
    refuses to hand a declared context anything but the simulated adapter.
    Nothing reachable from an HTTP route constructs one of these: the router
    loads contexts by employee id through `load_employee_context`, which
    returns None when T6 is absent, and a None context is a refusal.
    """
    kwargs.setdefault("source", "declared")
    ctx = EmployeeContext(**kwargs)
    # A declared context may claim any stage it likes for the purpose of
    # exercising the gates; `may_execute` is still computed rather than taken
    # on trust, so a declaration cannot assert its way past the stage rules.
    executing = frozenset({"controlled", "active"})
    ctx.killed = bool(ctx.killed)
    ctx.may_run = (not ctx.killed) and ctx.activation_state != "off" \
        and not ctx.paused and ctx.status == "active"
    ctx.may_execute = ctx.may_run and ctx.activation_state in executing
    return ctx


# ── IN-PROCESS CONTEXT PROVIDERS (simulation and evaluation only) ──────────
#
# A DECLARED CONTEXT HAS NOWHERE TO BE LOADED FROM. It exists in the memory
# of whatever built it, which is fine while a scenario holds the object — and
# useless to the follow-up worker, which comes back later with only an
# employee id and correctly refuses to act without an authority answer.
#
# A provider closes that gap for synthetic runs WITHOUT weakening production:
# nothing registers one in a served process, a provider can only ever return
# a context marked `declared`, and a declared context is forced onto the
# simulated adapter by `channels.resolve` no matter what it claims. So the
# worst a rogue provider could do is produce a more thorough simulation.
_CONTEXT_PROVIDERS: List = []


def _from_providers(db: Session, employee_id: str,
                    organization_id: Optional[str]
                    ) -> Optional[EmployeeContext]:
    """Ask the registered providers. The tenancy check is applied HERE.

    A provider that returns a context for another organization is ignored
    rather than trusted: the caller asked about one tenant, and a synthetic
    provider is not an exception to that.
    """
    for provider in list(_CONTEXT_PROVIDERS):
        try:
            ctx = provider(db, employee_id, organization_id)
        except Exception as exc:                             # noqa: BLE001
            _log.info("ai_operations: context provider failed (%s)", exc)
            continue
        if ctx is None:
            continue
        if organization_id and ctx.organization_id != organization_id:
            continue
        if ctx.source == "t6":
            # A provider may not claim to be T6. That claim is what unlocks
            # live adapters, and only a real T6 row may make it.
            ctx.source = "declared"
        return ctx
    return None


def register_context_provider(provider) -> None:
    """Add an in-process resolver for declared employee contexts."""
    if provider not in _CONTEXT_PROVIDERS:
        _CONTEXT_PROVIDERS.append(provider)


def clear_context_providers() -> None:
    """Forget every registered provider. Tests share a process; a provider
    left behind by one test is a mystery in the next."""
    _CONTEXT_PROVIDERS.clear()


def load_employee_context(db: Session, employee_id: str, *,
                          organization_id: Optional[str] = None
                          ) -> Optional[EmployeeContext]:
    """Load the actor from T6's own rows. None when T6 cannot answer.

    NONE IS A REFUSAL, NOT AN INVITATION. Every caller treats a None context
    as "this employee cannot act", which is the correct reading both when the
    employee does not exist and when the engine that owns employees is not
    deployed.
    """
    if not employee_id:
        return None
    if not T6_PRESENT:
        return _from_providers(db, employee_id, organization_id)
    try:
        AIEmployee = getattr(_wf_models, "AIEmployee")
        q = db.query(AIEmployee).filter(AIEmployee.id == employee_id)
        # THE TENANCY FILTER IS APPLIED IN THE QUERY, NOT AFTERWARDS. A check
        # made on a row already loaded is a check somebody can forget; a
        # filter that never returns the row cannot be forgotten.
        if organization_id:
            q = q.filter(AIEmployee.organization_id == organization_id)
        emp = q.first()
        if emp is None:
            return _from_providers(db, employee_id, organization_id)

        pol = _wf_policy.resolve(db, emp)
        act = (_wf_activation.resolve(db, employee=emp)
               if _wf_activation is not None else None)

        ctx = EmployeeContext(
            employee_id=emp.id,
            organization_id=emp.organization_id,
            name=emp.name or "AI Employee",
            job_role=emp.job_role or "",
            template_key=getattr(pol, "template_key", "") or "",
            platform_id=getattr(emp, "platform_id", None),
            tool_keys=set(getattr(pol, "tool_keys", set()) or set()),
            channels=set(getattr(pol, "channels", set()) or set()),
            status=(emp.status or "draft"),
            paused=getattr(emp, "paused_at", None) is not None,
            pause_reason=getattr(emp, "pause_reason", None),
            handoff_user_id=getattr(emp, "handoff_user_id", None),
            handoff_queue=getattr(emp, "handoff_queue", None),
            timezone=getattr(pol, "timezone", None) or C.DEFAULT_WINDOW_TIMEZONE,
            operating_hours=dict(getattr(pol, "operating_hours", {}) or {}),
            escalations=list(getattr(pol, "escalations", []) or []),
            source="t6",
        )
        if act is not None:
            ctx.activation_state = act.state
            ctx.killed = bool(act.killed)
            ctx.may_run = bool(act.may_run)
            ctx.may_execute = bool(act.may_execute)
            ctx.daily_cap = act.daily_cap
            ctx.activation_chain = list(act.chain or [])
        # A CONFIGURED CAP MAY ONLY NARROW. The ceilings in constants are the
        # runaway protection and configuration does not negotiate with them.
        emp_cap = getattr(emp, "daily_work_cap", None)
        if emp_cap:
            ctx.channel_daily_cap = min(int(emp_cap), ctx.channel_daily_cap)
        return ctx
    except Exception as exc:                                 # noqa: BLE001
        # A BROKEN T6 READ IS A REFUSAL. Returning a permissive context here
        # would mean a partially-migrated database granted authority.
        _log.warning("ai_operations: could not load employee %s (%s)",
                     employee_id, exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# AUTHORITY
# ═══════════════════════════════════════════════════════════════════════════

def authorize_tool(db: Session, ctx: EmployeeContext, tool_key: Optional[str]
                   ) -> Tuple[bool, Optional[str], Optional[str]]:
    """(allowed, denial_code, reason). NEVER raises, NEVER fails open.

    T6's `policy.may_use_tool` is the authority when it is present, and its
    refusal codes are returned verbatim so an operator sees one vocabulary.
    Without T6 the answer comes from the context's own intersected tool list,
    which for a declared context is whatever the profile declared — and a
    declared context cannot reach anybody, so an over-broad declaration
    produces an over-broad SIMULATION and nothing else.
    """
    if not tool_key:
        return False, C.D_UNKNOWN_OPERATION, (
            "This operation has no registered tool authority.")

    if ctx.source == "t6" and _wf_policy is not None and _wf_models is not None:
        try:
            AIEmployee = getattr(_wf_models, "AIEmployee")
            emp = (db.query(AIEmployee)
                   .filter(AIEmployee.id == ctx.employee_id,
                           AIEmployee.organization_id == ctx.organization_id)
                   .first())
            if emp is None:
                return False, C.D_RECORD_NOT_FOUND, "The employee no longer exists."
            pol = _wf_policy.resolve(db, emp)
            allowed, code, reason = _wf_policy.may_use_tool(pol, tool_key)
            return bool(allowed), code, reason
        except Exception as exc:                             # noqa: BLE001
            _log.warning("ai_operations: T6 authority check failed (%s)", exc)
            return False, C.D_NOT_AUTHORIZED, (
                "The workforce authority engine could not answer, so the "
                "action is refused.")

    if not ctx.holds_tool(tool_key):
        return False, C.D_NOT_AUTHORIZED, (
            "'%s' has not been granted to this employee." % tool_key)
    return True, None, None


def resolve_activation(db: Session, ctx: EmployeeContext) -> EmployeeContext:
    """Re-resolve activation AT EXECUTION TIME and return an updated context.

    Called before every consequential operation rather than once per run.
    A worker that resolved activation when it claimed its work would keep
    sending for the rest of that run after somebody hit the kill switch,
    which is precisely the failure a kill switch exists to prevent.
    """
    if ctx.source != "t6" or _wf_activation is None or _wf_models is None:
        return ctx
    try:
        AIEmployee = getattr(_wf_models, "AIEmployee")
        emp = (db.query(AIEmployee)
               .filter(AIEmployee.id == ctx.employee_id,
                       AIEmployee.organization_id == ctx.organization_id)
               .first())
        if emp is None:
            ctx.may_run = False
            ctx.may_execute = False
            ctx.status = "missing"
            return ctx
        act = _wf_activation.resolve(db, employee=emp)
        ctx.activation_state = act.state
        ctx.killed = bool(act.killed)
        ctx.may_run = bool(act.may_run)
        ctx.may_execute = bool(act.may_execute)
        ctx.daily_cap = act.daily_cap
        ctx.activation_chain = list(act.chain or [])
        ctx.status = emp.status or ctx.status
        ctx.paused = getattr(emp, "paused_at", None) is not None
        ctx.pause_reason = getattr(emp, "pause_reason", None)
    except Exception as exc:                                 # noqa: BLE001
        _log.warning("ai_operations: activation re-resolve failed (%s)", exc)
        ctx.may_execute = False
    return ctx


# ═══════════════════════════════════════════════════════════════════════════
# MIRRORING INTO T6'S OWN RECORDS (best effort, never load-bearing)
# ═══════════════════════════════════════════════════════════════════════════
#
# Everything below writes into T6's tables when they exist so that T6's
# Supervisor, performance ledger and tool-execution audit see what this layer
# did. Every one of them is wrapped: a failure to mirror must never fail the
# operation that already happened, and must never be the reason an audit row
# in THIS layer is missing — this layer's own audit is written first.

def mirror_tool_execution(db: Session, ctx: EmployeeContext, *, tool_key: str,
                          decision: str, denial_code: Optional[str],
                          denial_reason: Optional[str], status: Optional[str],
                          result_summary: Optional[str],
                          idempotency_key: Optional[str],
                          work_item_id: Optional[str], run_id: Optional[str],
                          target_type: Optional[str], target_id: Optional[str],
                          simulated: bool, arguments_digest: Optional[str],
                          duration_ms: Optional[int] = None) -> Optional[str]:
    """Write the attempt into `ai_tool_executions`. Refusals included.

    A refused call is mirrored with the same weight as a successful one: "the
    employee tried to email a lead in another tenant and was refused" is the
    single most valuable line in that table.
    """
    if not T6_PRESENT:
        return None
    try:
        AIToolExecution = getattr(_wf_models, "AIToolExecution")
        row = AIToolExecution(
            run_id=run_id, work_item_id=work_item_id,
            organization_id=ctx.organization_id, employee_id=ctx.employee_id,
            tool_key=tool_key, sequence=0,
            arguments=None, arguments_digest=arguments_digest,
            decision=decision, denial_code=denial_code,
            denial_reason=denial_reason, status=status,
            result_summary=result_summary, error=None,
            idempotency_key=idempotency_key, target_type=target_type,
            target_id=target_id, duration_ms=duration_ms,
            simulated=bool(simulated))
        db.add(row)
        db.flush()
        return row.id
    except Exception as exc:                                 # noqa: BLE001
        _log.info("ai_operations: tool-execution mirror skipped (%s)", exc)
        return None


def mirror_handoff(db: Session, ctx: EmployeeContext, *, subject_type: str,
                   subject_id: str, reason_code: str, summary: str,
                   known_facts: Optional[List] = None,
                   open_questions: Optional[List] = None,
                   recommended_action: Optional[str] = None,
                   priority: str = "normal",
                   work_item_id: Optional[str] = None,
                   conversation_ref: Optional[str] = None,
                   appointment_ref: Optional[str] = None) -> Optional[str]:
    """Create the T6 handoff row so it lands in the queue people already use."""
    if not T6_PRESENT:
        return None
    try:
        import json
        AIHandoff = getattr(_wf_models, "AIHandoff")
        row = AIHandoff(
            organization_id=ctx.organization_id, employee_id=ctx.employee_id,
            work_item_id=work_item_id, subject_type=subject_type,
            subject_id=subject_id, reason_code=reason_code, priority=priority,
            summary=summary or "",
            known_facts=json.dumps(list(known_facts or [])),
            open_questions=json.dumps(list(open_questions or [])),
            recommended_action=recommended_action,
            conversation_ref=conversation_ref,
            appointment_ref=appointment_ref,
            assigned_to_user_id=ctx.handoff_user_id,
            assigned_queue=ctx.handoff_queue, status="open")
        db.add(row)
        db.flush()
        return row.id
    except Exception as exc:                                 # noqa: BLE001
        _log.info("ai_operations: handoff mirror skipped (%s)", exc)
        return None


def mirror_performance(db: Session, ctx: EmployeeContext, metric_key: str,
                       increment: int = 1) -> None:
    """Increment T6's day-grained performance ledger."""
    if not T6_PRESENT:
        return
    try:
        AIPerformanceEntry = getattr(_wf_models, "AIPerformanceEntry")
        day = datetime.utcnow().strftime("%Y-%m-%d")
        row = (db.query(AIPerformanceEntry)
               .filter(AIPerformanceEntry.organization_id == ctx.organization_id,
                       AIPerformanceEntry.employee_id == ctx.employee_id,
                       AIPerformanceEntry.metric_date == day,
                       AIPerformanceEntry.metric_key == metric_key)
               .first())
        if row is None:
            row = AIPerformanceEntry(
                organization_id=ctx.organization_id,
                employee_id=ctx.employee_id, metric_date=day,
                metric_key=metric_key, value=0)
            db.add(row)
            db.flush()
        row.value = int(row.value or 0) + int(increment)
        db.flush()
    except Exception as exc:                                 # noqa: BLE001
        _log.info("ai_operations: performance mirror skipped (%s)", exc)


def mirror_supervisor_event(db: Session, ctx: Optional[EmployeeContext], *,
                            event_code: str, message: str,
                            severity: str = "info",
                            detail: Optional[Dict] = None,
                            recommended_action: Optional[str] = None,
                            organization_id: Optional[str] = None) -> Optional[str]:
    """Tell the Supervisor. It observes and recommends; it never acts."""
    if not T6_PRESENT:
        return None
    try:
        import json
        AISupervisorEvent = getattr(_wf_models, "AISupervisorEvent")
        row = AISupervisorEvent(
            organization_id=(organization_id
                             or (ctx.organization_id if ctx else None)),
            platform_id=(ctx.platform_id if ctx else None),
            employee_id=(ctx.employee_id if ctx else None),
            severity=severity, event_code=event_code, message=message or "",
            detail=json.dumps(detail or {}),
            recommended_action=recommended_action)
        db.add(row)
        db.flush()
        return row.id
    except Exception as exc:                                 # noqa: BLE001
        _log.info("ai_operations: supervisor mirror skipped (%s)", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# THE WORK ITEM (T6 owns it; this layer reads it and asks it to move)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class WorkItemView:
    """A read-only view of a T6 work item, or a synthetic stand-in."""
    work_item_id: Optional[str]
    organization_id: str
    employee_id: Optional[str]
    subject_type: str = "lead"
    subject_id: str = ""
    state: str = ""
    cancelled: bool = False
    terminal: bool = False
    touches: int = 0
    source: str = "declared"


def load_work_item(db: Session, work_item_id: Optional[str], *,
                   organization_id: str) -> Optional[WorkItemView]:
    """Read a T6 work item inside one tenant. None when it cannot be read."""
    if not work_item_id or not T6_PRESENT:
        return None
    try:
        AIWorkItem = getattr(_wf_models, "AIWorkItem")
        row = (db.query(AIWorkItem)
               .filter(AIWorkItem.id == work_item_id,
                       AIWorkItem.organization_id == organization_id)
               .first())
        if row is None:
            return None
        terminal_states = frozenset(
            getattr(_wf_constants, "TERMINAL_STATES", frozenset()))
        return WorkItemView(
            work_item_id=row.id, organization_id=row.organization_id,
            employee_id=row.employee_id, subject_type=row.subject_type,
            subject_id=row.subject_id, state=row.state or "",
            cancelled=(row.state or "") in ("paused", "failed")
            and (row.state_reason or "") == "cancelled",
            terminal=(row.state or "") in terminal_states,
            touches=int(row.touches or 0), source="t6")
    except Exception as exc:                                 # noqa: BLE001
        _log.info("ai_operations: work item read skipped (%s)", exc)
        return None


def request_work_item_state(db: Session, work_item_id: Optional[str], *,
                            organization_id: str, to_state: str,
                            reason: Optional[str] = None) -> bool:
    """Ask T6 to move a work item. Returns whether it moved.

    THE TRANSITION BELONGS TO T6. This layer asks; it does not write the
    column itself, and a refusal is honoured rather than worked around — a
    record that reached `do_not_contact` must not be dragged back out by an
    operations-layer update.
    """
    if not work_item_id or not T6_PRESENT:
        return False
    try:
        if _wf_queue is not None and hasattr(_wf_queue, "transition"):
            return bool(_wf_queue.transition(
                db, work_item_id=work_item_id,
                organization_id=organization_id, to_state=to_state,
                reason=reason))
    except Exception as exc:                                 # noqa: BLE001
        _log.info("ai_operations: work item transition refused (%s)", exc)
    return False


# ═══════════════════════════════════════════════════════════════════════════
# ELIGIBILITY DELEGATION
# ═══════════════════════════════════════════════════════════════════════════

def delegate_eligibility(db: Session, ctx: EmployeeContext, *, lead,
                         channel: str) -> Optional[Tuple[str, List[Dict]]]:
    """Ask T6's eligibility engine, when it exists.

    Returns (result, reasons) or None when T6 cannot answer. The caller does
    NOT treat None as ALLOW: this layer runs its own full gate chain in every
    case and merges T6's answer as an additional, never a substitute, source
    of refusal. Two engines that can each say DENY and neither of which can
    say ALLOW on the other's behalf is the only safe way to have two.
    """
    if _wf_eligibility is None:
        return None
    for fn_name in ("evaluate", "check", "decide"):
        fn = getattr(_wf_eligibility, fn_name, None)
        if fn is None:
            continue
        try:
            out = fn(db, lead=lead, channel=channel, employee_id=ctx.employee_id)
        except TypeError:
            try:
                out = fn(db, lead, channel)
            except Exception as exc:                         # noqa: BLE001
                _log.info("ai_operations: T6 eligibility call failed (%s)", exc)
                return None
        except Exception as exc:                             # noqa: BLE001
            _log.info("ai_operations: T6 eligibility call failed (%s)", exc)
            return None
        result = getattr(out, "result", None) or (
            out.get("result") if isinstance(out, dict) else None)
        reasons = getattr(out, "reasons", None) or (
            out.get("reasons") if isinstance(out, dict) else None)
        if result in C.ELIGIBILITY_RESULTS:
            return result, list(reasons or [])
        return None
    return None
