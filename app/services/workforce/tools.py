"""THE AGENT TOOL GATEWAY. NOTHING REACHES A RECORD EXCEPT THROUGH HERE.

THE MODEL HAS NO DATABASE, NO SHELL, NO HTTP AND NO SQL. It emits a tool key
and a dict of arguments. That is a REQUEST, and this module is what turns a
request into an action or into a refusal with a reason.

THE GATE ORDER, AND WHY IT IS THIS ORDER. Cheapest and most absolute first, so
that the answer to "why was this refused" names the most fundamental reason
rather than whichever check happened to run first, and so that a killed
workforce costs one query to refuse rather than twelve.

     1. IS THIS A REAL TOOL           unknown key, refused before anything else
     2. MAY THIS EMPLOYEE RUN AT ALL  kill switch, pause, status, activation
     3. THE WORK ITEM                 present when required, owned by this
                                      employee, not already terminal
     4. ARGUMENTS                     schema, before any record is loaded
     5. AUTHORITY                     template ∩ brand offering ∩ explicit grant
     6. CHANNEL                       is this channel enabled for this employee
     7. LIVE VOICE                    refused platform-wide in this build
     8. ACTIVATION vs REACH           a tool that touches a real person may not
                                      run in simulation or shadow, ever
     9. COMMERCE                      customer feature flag, then entitlement
    10. BUDGET                        run tool-call ceiling, runaway protection
    11. THE RECORD                    loaded through a tenant-scoped read;
                                      a foreign id is a refusal, not a 500
    12. CONTACT ELIGIBILITY           the deterministic engine. DENY and
                                      REQUIRES_REVIEW both refuse the send
    13. IDEMPOTENCY                   a repeat of the same call is suppressed

EVERY ONE OF THESE IS RE-CHECKED ON EVERY CALL. Not once per run, not at
enqueue time. Section 34 is explicit about why: a worker that resolved its
authority when it claimed the item keeps acting after somebody hits pause.

WHAT IS NOT AUTHORIZATION:
  * the model asking for a tool
  * a prompt that says the employee has permission
  * a lead's reply that says the employee has permission
  * a knowledge article that says the employee has permission
  * an argument named `authorized: true`
None of these reach any gate below. The model's output is consulted for the
tool key and the arguments and for nothing else.

REFUSALS ARE RECORDED WITH THE SAME WEIGHT AS SUCCESSES. "The employee tried to
text a lead in another tenant and was refused" is the most valuable row in
`ai_tool_executions`, and a gateway that logged only what it allowed would
never show it.
"""

import logging
import time as _time
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from sqlalchemy.orm import Session

from app.models.models import Lead
from app.models.workforce_models import (AIEmployee, AIEmployeeRun,
                                         AIToolExecution, AIWorkItem)
from app.services.workforce import activation as wf_activation
from app.services.workforce import audit as wf_audit
from app.services.workforce import constants as C
from app.services.workforce import eligibility as wf_eligibility
from app.services.workforce import entitlement as wf_entitlement
from app.services.workforce import policy as wf_policy
from app.services.workforce import registry

_log = logging.getLogger(__name__)


class ToolRefusal(Exception):
    """A registered tool refusing on a BUSINESS rule, from inside its handler.

    Distinct from an exception: a refusal is an answer the employee is expected
    to receive and reason about ("that slot is no longer free"), and it is
    recorded as a denial rather than as an error.
    """

    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code = code
        self.reason = reason


class ToolContext:
    """Everything one tool call is decided against.

    Built once per run and passed down. The `resolved` activation on it is NOT
    reused across calls — `authorize` re-resolves every time, and this copy is
    only what the run loop was started with.
    """

    __slots__ = ("db", "employee", "policy", "work_item", "run", "claim_token",
                 "actor_user_id", "now", "sequence", "mode", "trigger",
                 "_shadow_sink")

    def __init__(self, db: Session, employee: AIEmployee, *,
                 policy: Optional[wf_policy.EffectivePolicy] = None,
                 work_item: Optional[AIWorkItem] = None,
                 run: Optional[AIEmployeeRun] = None,
                 claim_token: Optional[str] = None,
                 actor_user_id: Optional[str] = None,
                 now: Optional[datetime] = None,
                 mode: str = C.SIMULATION, trigger: str = "manual"):
        self.db = db
        self.employee = employee
        self.policy = policy or wf_policy.resolve(db, employee)
        self.work_item = work_item
        self.run = run
        self.claim_token = claim_token
        self.actor_user_id = actor_user_id
        self.now = now or datetime.utcnow()
        self.sequence = 0
        self.mode = mode
        self.trigger = trigger
        self._shadow_sink = []

    @property
    def organization_id(self) -> str:
        return self.employee.organization_id

    def next_sequence(self) -> int:
        self.sequence += 1
        return self.sequence


class Authorization:
    """The gateway's verdict, before anything is executed."""

    __slots__ = ("allowed", "code", "reason", "spec", "resolved", "eligibility",
                 "commerce", "idempotency_key", "lead", "simulated")

    def __init__(self, allowed, code=None, reason=None, spec=None,
                 resolved=None, eligibility=None, commerce=None,
                 idempotency_key=None, lead=None, simulated=True):
        self.allowed = allowed
        self.code = code
        self.reason = reason
        self.spec = spec
        self.resolved = resolved
        self.eligibility = eligibility
        self.commerce = commerce
        self.idempotency_key = idempotency_key
        self.lead = lead
        self.simulated = simulated


class ToolResult:
    __slots__ = ("ok", "decision", "denial_code", "denial_reason", "data",
                 "simulated", "execution_id", "tool_key", "terminal_outcome")

    def __init__(self, ok, decision, tool_key, denial_code=None,
                 denial_reason=None, data=None, simulated=True,
                 execution_id=None, terminal_outcome=None):
        self.ok = ok
        self.decision = decision
        self.tool_key = tool_key
        self.denial_code = denial_code
        self.denial_reason = denial_reason
        self.data = data if data is not None else {}
        self.simulated = simulated
        self.execution_id = execution_id
        self.terminal_outcome = terminal_outcome

    def as_dict(self) -> Dict:
        return {"ok": self.ok, "tool": self.tool_key, "decision": self.decision,
                "denial_code": self.denial_code,
                "denial_reason": self.denial_reason, "data": self.data,
                "simulated": self.simulated,
                "execution_id": self.execution_id}


# ── ARGUMENT VALIDATION ─────────────────────────────────────────────────────

_TYPE_CHECKS = {
    "str": lambda v: isinstance(v, str),
    "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "bool": lambda v: isinstance(v, bool),
    "dict": lambda v: isinstance(v, dict),
    "list": lambda v: isinstance(v, (list, tuple)),
}


def validate_arguments(spec: registry.ToolSpec, args: Optional[Dict]
                       ) -> Optional[str]:
    """Returns an error string, or None when the arguments are usable.

    UNKNOWN ARGUMENTS ARE REFUSED, not ignored. A model that invents
    `override: true` or `organization_id: <someone else's>` must get a refusal
    naming the argument rather than have it silently dropped — a silently
    dropped argument is a model that believes it succeeded at something it did
    not do, and the next turn reasons from that belief.
    """
    args = args if isinstance(args, dict) else {}
    for name, (kind, required) in spec.schema.items():
        if name not in args or args[name] is None:
            if required:
                return "missing required argument '%s'" % name
            continue
        check = _TYPE_CHECKS.get(kind)
        if check is not None and not check(args[name]):
            return "argument '%s' must be %s" % (name, kind)
    extra = sorted(set(args) - set(spec.schema))
    if extra:
        return "unexpected argument(s): %s" % ", ".join(extra)
    return None


def _idempotency_key(spec: registry.ToolSpec, ctx: ToolContext,
                     args: Dict) -> Optional[str]:
    """Stable across retries of the SAME intent, different for a new one.

    Keyed on the work item as well as the arguments, so a second appointment
    for the same lead on a DIFFERENT day is a new action while a retry of the
    same booking is suppressed.
    """
    if not spec.idempotent_on:
        return None
    parts = {k: args.get(k) for k in spec.idempotent_on}
    parts["_tool"] = spec.key
    parts["_work_item"] = getattr(ctx.work_item, "id", None)
    return wf_audit.digest(parts)


def _load_lead(ctx: ToolContext, lead_id: Optional[str]) -> Optional[Lead]:
    """Load a lead INSIDE this employee's tenant, or return None.

    THE TENANT FILTER IS IN THE QUERY, not in a check afterwards. A foreign id
    therefore returns no row at all, and the caller reports `record_not_found`
    — which is deliberately the same answer as an id that does not exist, so
    the gateway cannot be used to enumerate another customer's leads one id at
    a time. app/deps.py uses 404-not-403 for the same reason.
    """
    if not lead_id:
        return None
    return (ctx.db.query(Lead)
            .filter(Lead.id == lead_id,
                    Lead.organization_id == ctx.organization_id)
            .first())


# ── THE GATE ────────────────────────────────────────────────────────────────

def authorize(ctx: ToolContext, tool_key: str,
              args: Optional[Dict] = None) -> Authorization:
    """Answer "may this employee do this, right now" and nothing else.

    Separated from `invoke` so it can be tested on its own and so the run loop
    can ask before it commits to a plan. It performs NO mutation.
    """
    args = dict(args or {})
    db, employee = ctx.db, ctx.employee

    # ── 1. IS THIS A REAL TOOL ──────────────────────────────────────────────
    spec = registry.tool(tool_key)
    if spec is None:
        return Authorization(False, C.DENY_UNKNOWN_TOOL,
                             "'%s' is not a registered platform tool."
                             % tool_key)

    # ── 2. MAY THIS EMPLOYEE RUN AT ALL ─────────────────────────────────────
    #
    # Re-resolved on EVERY call. This is the kill switch and the pause, and
    # they are worth one query per tool call.
    resolved = wf_activation.resolve(db, employee=employee)
    if resolved.killed:
        return Authorization(False, C.DENY_KILLED,
                             "The kill switch is engaged at %s scope."
                             % (resolved.killed_by_scope or "an enclosing"),
                             spec=spec, resolved=resolved)
    if employee.paused_at is not None:
        return Authorization(False, C.DENY_EMPLOYEE_PAUSED,
                             "%s is paused%s." % (employee.name,
                                                  (": " + employee.pause_reason)
                                                  if employee.pause_reason else ""),
                             spec=spec, resolved=resolved)
    if (employee.status or "") != "active":
        return Authorization(False, C.DENY_EMPLOYEE_INACTIVE,
                             "%s is not active (status: %s)."
                             % (employee.name, employee.status),
                             spec=spec, resolved=resolved)
    if not resolved.may_run:
        return Authorization(False, C.DENY_ACTIVATION_STAGE,
                             "The workforce is switched off for this employee "
                             "(effective stage: %s)." % resolved.state,
                             spec=spec, resolved=resolved)

    simulated = resolved.state not in C.EXECUTING_STAGES

    # ── 3. THE WORK ITEM ────────────────────────────────────────────────────
    if spec.requires_work_item:
        item = ctx.work_item
        if item is None:
            return Authorization(False, C.DENY_WORK_ITEM_MISMATCH,
                                 "'%s' may only be used while working an "
                                 "assigned record." % spec.label,
                                 spec=spec, resolved=resolved)
        if str(item.employee_id) != str(employee.id) or \
                str(item.organization_id) != str(employee.organization_id):
            return Authorization(False, C.DENY_WORK_ITEM_MISMATCH,
                                 "That work item does not belong to this "
                                 "employee.", spec=spec, resolved=resolved)
        if item.state in C.TERMINAL_STATES:
            return Authorization(False, C.DENY_BUSINESS_RULE,
                                 "This record is already closed as '%s'."
                                 % item.state, spec=spec, resolved=resolved)
        # A LOST LEASE IS A REFUSAL. Another worker owns this record now.
        if ctx.claim_token is not None and item.claim_token != ctx.claim_token:
            return Authorization(False, C.DENY_WORK_ITEM_MISMATCH,
                                 "This record is being worked by another run.",
                                 spec=spec, resolved=resolved)

    # ── 4. ARGUMENTS ────────────────────────────────────────────────────────
    err = validate_arguments(spec, args)
    if err:
        return Authorization(False, C.DENY_BAD_ARGUMENTS, err, spec=spec,
                             resolved=resolved)

    # ── 5. AUTHORITY ────────────────────────────────────────────────────────
    ok, code, reason = wf_policy.may_use_tool(ctx.policy, tool_key)
    if not ok:
        return Authorization(False, code, reason, spec=spec, resolved=resolved)

    # ── 6. CHANNEL ──────────────────────────────────────────────────────────
    if spec.channel and not wf_policy.channel_enabled(ctx.policy, spec.channel):
        return Authorization(False, C.DENY_CHANNEL_OFF,
                             "%s is not an enabled channel for %s."
                             % (spec.channel, employee.name),
                             spec=spec, resolved=resolved)

    # ── 7. LIVE VOICE ───────────────────────────────────────────────────────
    #
    # BEFORE any provider is resolved and before eligibility is consulted, so
    # that no configuration of any customer, brand or employee can produce a
    # call in this build. The voice ARCHITECTURE is exercised by the simulator
    # against a fake adapter; the live action is withheld here.
    if spec.channel == C.CHANNEL_VOICE and spec.reaches_outside:
        from app.services.workforce import outbound as _outbound
        simulating_voice = (resolved.state == C.SIMULATION
                            and _outbound.is_fully_simulated())
        if not wf_activation.live_voice_enabled() and not simulating_voice:
            return Authorization(False, C.DENY_LIVE_VOICE_DISABLED,
                                 "AI voice calling is disabled platform-wide "
                                 "in this deployment.",
                                 spec=spec, resolved=resolved)

    # ── 8. ACTIVATION vs REACH ──────────────────────────────────────────────
    #
    # THE SINGLE MOST IMPORTANT LINE IN THE DARK LAUNCH. A tool that can touch
    # a real person or a real provider is refused unless the resolved stage is
    # CONTROLLED or ACTIVE. It does not matter which adapter is installed; a
    # fake adapter is a convenience, and this is the thing standing between the
    # engine and a real send.
    if spec.reaches_outside and resolved.state not in C.EXECUTING_STAGES:
        # THE ONE EXCEPTION, AND IT REACHES NOBODY EITHER.
        #
        # In the SIMULATION stage a reach-shaped tool is permitted ONLY while
        # every channel is served by a simulated adapter. That is what lets
        # the simulator drive the real send path — the real eligibility check,
        # the real touch counting, the real state machine — instead of testing
        # a different program from the one that ships (section 37).
        #
        # `is_fully_simulated()` is all-or-nothing on purpose: one live adapter
        # left installed and this is False. The real adapters ALSO refuse below
        # CONTROLLED on their own, so a wrong answer here still cannot send.
        # SHADOW is deliberately NOT included: shadow observes real events, and
        # a "simulated send" against a real customer's live conversation would
        # write a message row nobody asked for.
        from app.services.workforce import outbound as _outbound
        if not (resolved.state == C.SIMULATION
                and _outbound.is_fully_simulated()):
            return Authorization(False, C.DENY_ACTIVATION_STAGE,
                                 "'%s' reaches a real recipient and the "
                                 "effective activation stage is '%s'."
                                 % (spec.label, resolved.state),
                                 spec=spec, resolved=resolved)

    # ── 9. COMMERCE ─────────────────────────────────────────────────────────
    commerce = wf_entitlement.check(
        db, ctx.organization_id,
        entitlement_key=ctx.policy.entitlement_key,
        feature_key=(spec.required_feature or ctx.policy.required_feature),
        reaches_outside=spec.reaches_outside,
        activation_state=resolved.state)
    if not commerce["allowed"]:
        return Authorization(False, commerce["denial_code"],
                             commerce["denial_reason"], spec=spec,
                             resolved=resolved, commerce=commerce)

    # ── 10. BUDGET ──────────────────────────────────────────────────────────
    if ctx.run is not None:
        if int(ctx.run.tool_calls or 0) >= int(ctx.policy.max_tool_calls):
            return Authorization(False, C.DENY_RUN_BUDGET,
                                 "This run has used its %d tool calls."
                                 % ctx.policy.max_tool_calls,
                                 spec=spec, resolved=resolved, commerce=commerce)

    # ── 11. THE RECORD ──────────────────────────────────────────────────────
    lead = None
    if "lead_id" in spec.schema:
        lead_id = args.get("lead_id")
        if lead_id:
            lead = _load_lead(ctx, lead_id)
            if lead is None:
                # Same answer for "does not exist" and "belongs to somebody
                # else" — see _load_lead.
                return Authorization(False, C.DENY_RECORD_NOT_FOUND,
                                     "No such record in this organization.",
                                     spec=spec, resolved=resolved,
                                     commerce=commerce)
            # THE RECORD MUST BE THE ONE THIS WORK ITEM IS ABOUT.
            #
            # Without this, an employee legitimately working lead A could be
            # persuaded — by a prompt, by a reply, by a malformed plan — to
            # text lead B, who is in the same tenant and therefore passes every
            # tenancy check above. Section 40 lists tool argument substitution
            # as an attack and this is the answer to it.
            if ctx.work_item is not None and spec.mutating:
                if str(ctx.work_item.subject_id) != str(lead.id):
                    return Authorization(
                        False, C.DENY_NO_AUTHORITY,
                        "This employee is working a different record.",
                        spec=spec, resolved=resolved, commerce=commerce)

    # ── 12. CONTACT ELIGIBILITY ─────────────────────────────────────────────
    elig = None
    if spec.requires_eligibility:
        if lead is None:
            return Authorization(False, C.DENY_BAD_ARGUMENTS,
                                 "A contact tool needs a record to contact.",
                                 spec=spec, resolved=resolved, commerce=commerce)
        elig = wf_eligibility.evaluate_and_record(
            db, employee=employee, lead=lead,
            channel=spec.requires_eligibility, pol=ctx.policy,
            work_item_id=getattr(ctx.work_item, "id", None), now=ctx.now,
            daily_cap=resolved.daily_cap)
        if elig.result == C.REQUIRES_REVIEW:
            # NOT AN ALLOW. Section 12 names this failure explicitly.
            return Authorization(False, C.DENY_ELIGIBILITY_REVIEW,
                                 _first_reason(elig, "This contact needs a "
                                                     "person to review it."),
                                 spec=spec, resolved=resolved,
                                 eligibility=elig, commerce=commerce, lead=lead)
        if elig.result != C.ALLOW:
            return Authorization(False, C.DENY_INELIGIBLE_CONTACT,
                                 _first_reason(elig, "This contact may not be "
                                                     "contacted on that channel."),
                                 spec=spec, resolved=resolved,
                                 eligibility=elig, commerce=commerce, lead=lead)

    # ── 13. IDEMPOTENCY ─────────────────────────────────────────────────────
    key = _idempotency_key(spec, ctx, args)
    if key:
        prior = (db.query(AIToolExecution)
                 .filter(AIToolExecution.employee_id == employee.id,
                         AIToolExecution.idempotency_key == key)
                 .first())
        if prior is not None:
            return Authorization(False, C.DENY_DUPLICATE,
                                 "This exact action was already performed "
                                 "(execution %s)." % prior.id,
                                 spec=spec, resolved=resolved,
                                 eligibility=elig, commerce=commerce,
                                 idempotency_key=key, lead=lead)

    return Authorization(True, spec=spec, resolved=resolved, eligibility=elig,
                         commerce=commerce, idempotency_key=key, lead=lead,
                         simulated=simulated)


def _first_reason(elig, fallback: str) -> str:
    for r in (elig.reasons or []):
        label = r.get("label") or r.get("code")
        if label:
            detail = r.get("detail")
            return "%s%s" % (label, (" — %s" % detail) if detail else "")
    return fallback


# ── DISPATCH ────────────────────────────────────────────────────────────────

_HANDLERS: Dict[str, Callable] = {}


def register(tool_key: str):
    """Decorator used by tool_impls to bind an implementation to a key.

    A registered tool with no handler is a configuration error rather than a
    silent no-op: `invoke` refuses with `unknown_tool` and logs it, so a tool
    added to the registry and never implemented fails loudly the first time an
    employee asks for it.
    """
    def _wrap(fn):
        _HANDLERS[tool_key] = fn
        return fn
    return _wrap


def _ensure_handlers_loaded() -> None:
    if not _HANDLERS:
        from app.services.workforce import tool_impls  # noqa: F401
        _ = tool_impls


def _record(ctx: ToolContext, tool_key: str, args: Dict, *, decision: str,
            denial_code=None, denial_reason=None, status=None,
            result_summary=None, error=None, idempotency_key=None,
            target_type=None, target_id=None, duration_ms=None,
            simulated=True) -> Optional[str]:
    """Write the AI-native audit row. Refusals included, always."""
    import json
    redacted = wf_audit.redact(args)
    row = AIToolExecution(
        run_id=getattr(ctx.run, "id", None),
        work_item_id=getattr(ctx.work_item, "id", None),
        organization_id=ctx.organization_id,
        employee_id=ctx.employee.id,
        tool_key=tool_key,
        sequence=ctx.next_sequence(),
        arguments=json.dumps(redacted)[:8000],
        arguments_digest=wf_audit.digest(args),
        decision=decision,
        denial_code=denial_code,
        denial_reason=(denial_reason or "")[:255] or None,
        status=status,
        result_summary=(json.dumps(result_summary)[:4000]
                        if result_summary is not None else None),
        error=(error or "")[:255] or None,
        # A DENIED CALL CARRIES NO IDEMPOTENCY KEY.
        #
        # Writing one would mean a refusal permanently blocks the same action
        # from ever being retried after the refusal's cause is fixed — an
        # employee refused for being outside working hours could never send
        # that message at nine the next morning.
        idempotency_key=(idempotency_key if decision == "allowed" else None),
        target_type=target_type,
        target_id=target_id,
        duration_ms=duration_ms,
        simulated=bool(simulated),
    )
    ctx.db.add(row)
    try:
        ctx.db.flush()
    except Exception:                                        # noqa: BLE001
        # A UNIQUE VIOLATION HERE IS THE IDEMPOTENCY GUARD FIRING AT THE
        # DATABASE, which is where it has to hold under concurrency: two
        # workers that both passed gate 13 race, and exactly one row survives.
        ctx.db.rollback()
        _log.warning("workforce: tool execution row rejected (likely a "
                     "concurrent duplicate) for %s/%s", ctx.employee.id,
                     tool_key)
        return None
    return row.id


def invoke(ctx: ToolContext, tool_key: str,
           args: Optional[Dict] = None) -> ToolResult:
    """Authorize, then execute, then record. The only way in.

    A REFUSAL IS A RESULT, NOT AN EXCEPTION. The run loop is expected to hand
    the refusal back to the model as an observation so it can choose something
    else — which is exactly what a person would do — rather than crash the run.
    """
    args = dict(args or {})
    started = _time.time()
    auth = authorize(ctx, tool_key, args)

    if not auth.allowed:
        exec_id = _record(ctx, tool_key, args, decision="denied",
                          denial_code=auth.code, denial_reason=auth.reason,
                          idempotency_key=auth.idempotency_key,
                          simulated=auth.simulated)
        if ctx.run is not None:
            ctx.run.denied_tool_calls = int(ctx.run.denied_tool_calls or 0) + 1
        wf_audit.safe_log(logging.INFO,
                          "workforce tool DENIED employee=%s tool=%s code=%s",
                          ctx.employee.id, tool_key, auth.code)
        return ToolResult(False, "denied", tool_key, denial_code=auth.code,
                          denial_reason=auth.reason, simulated=auth.simulated,
                          execution_id=exec_id)

    _ensure_handlers_loaded()
    handler = _HANDLERS.get(tool_key)
    if handler is None:
        exec_id = _record(ctx, tool_key, args, decision="denied",
                          denial_code=C.DENY_UNKNOWN_TOOL,
                          denial_reason="No implementation is bound to '%s'."
                                        % tool_key, simulated=auth.simulated)
        _log.error("workforce: registered tool %r has no handler", tool_key)
        return ToolResult(False, "denied", tool_key,
                          denial_code=C.DENY_UNKNOWN_TOOL,
                          denial_reason="This tool is not available.",
                          simulated=auth.simulated, execution_id=exec_id)

    if ctx.run is not None:
        ctx.run.tool_calls = int(ctx.run.tool_calls or 0) + 1

    try:
        data = handler(ctx, args, auth) or {}
    except ToolRefusal as refusal:
        exec_id = _record(ctx, tool_key, args, decision="denied",
                          denial_code=refusal.code,
                          denial_reason=refusal.reason,
                          simulated=auth.simulated)
        return ToolResult(False, "denied", tool_key, denial_code=refusal.code,
                          denial_reason=refusal.reason,
                          simulated=auth.simulated, execution_id=exec_id)
    except Exception as exc:                                 # noqa: BLE001
        # AN IMPLEMENTATION FAULT IS NOT A REFUSAL AND IS NOT A SUCCESS.
        # It is recorded as an error, the run loop counts it towards the
        # consecutive-failure ceiling, and the message the employee receives
        # says nothing about internals.
        _log.exception("workforce: tool %s raised", tool_key)
        exec_id = _record(ctx, tool_key, args, decision="allowed",
                          status="error", error="%s: %s" % (type(exc).__name__,
                                                            exc),
                          idempotency_key=None, simulated=auth.simulated)
        return ToolResult(False, "allowed", tool_key,
                          denial_code=None,
                          denial_reason="That step failed and was not applied.",
                          simulated=auth.simulated, execution_id=exec_id)

    duration = int((_time.time() - started) * 1000)
    exec_id = _record(ctx, tool_key, args, decision="allowed", status="ok",
                      result_summary=_summarize(data),
                      idempotency_key=auth.idempotency_key,
                      target_type=data.get("_target_type"),
                      target_id=data.get("_target_id"),
                      duration_ms=duration, simulated=auth.simulated)
    data.pop("_target_type", None)
    data.pop("_target_id", None)
    return ToolResult(True, "allowed", tool_key, data=data,
                      simulated=auth.simulated, execution_id=exec_id,
                      terminal_outcome=auth.spec.terminal_outcome)


def _summarize(data: Dict) -> Dict:
    """What goes in `result_summary` — counts and ids, never content."""
    out = {}
    for k, v in (data or {}).items():
        if k.startswith("_"):
            continue
        if isinstance(v, (int, float, bool)) or v is None:
            out[k] = v
        elif isinstance(v, str):
            out[k] = v[:120]
        elif isinstance(v, (list, tuple)):
            out[k] = {"count": len(v)}
        elif isinstance(v, dict):
            out[k] = {"keys": sorted(v)[:12]}
    return out


def available_tools(ctx: ToolContext) -> list:
    """What this employee may actually ask for, right now.

    Used to build the model's tool list. It is a CONVENIENCE, not a boundary —
    every call still goes through `authorize`, so a model that asks for
    something absent from this list is refused rather than obeyed. Offering the
    list anyway is what stops a run wasting its whole budget asking for tools
    it was never going to be given.
    """
    out = []
    for key in sorted(ctx.policy.tool_keys):
        spec = registry.tool(key)
        if spec is None:
            continue
        if spec.channel and not wf_policy.channel_enabled(ctx.policy, spec.channel):
            continue
        if spec.channel == C.CHANNEL_VOICE and spec.reaches_outside:
            from app.services.workforce import outbound as _outbound
            if not (wf_activation.live_voice_enabled()
                    or _outbound.is_fully_simulated()):
                continue
        out.append(spec.as_dict())
    return out
