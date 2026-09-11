"""COST AND RUNAWAY INTELLIGENCE - and the cost that is honestly unknown.

WHAT THIS PLATFORM ACTUALLY KNOWS ABOUT MONEY.

T7 records an ESTIMATE on each communication and each action, computed from
per-unit constants in `ai_operations/budget.py` that an operator sets by
environment variable. T6 records an estimate per run. Neither is a provider
invoice. Twilio's real per-segment price for this account, this month, at this
volume is not in this database, and no amount of arithmetic here will produce
it.

SO EVERY FIGURE IN THIS MODULE IS LABELLED, and the label is part of the
payload rather than a footnote on the screen:

    measured    a count of things that happened - messages, calls, actions,
                model invocations. A fact.
    estimated   the platform's own estimate, with the rate that produced it.
    unknown     no provider cost is available. NOT zero, and never rendered
                as a currency figure.

Section 10 says "do not invent dollar costs when provider cost is
unavailable". The temptation is to show the estimate as if it were the bill,
because an estimate looks like an answer and "unknown" looks like a gap. But a
customer who is told their AI workforce cost $412 last month will believe it,
and will be wrong by whatever the real rates are.

WHAT RUNAWAY DETECTION IS FOR. Not billing. A runaway is a SAFETY condition -
an employee that has started doing something repeatedly is either broken or
being provoked, and the useful signal is "this is unusual for this employee"
rather than "this is expensive". So the comparisons here are against ceilings
the platform configured and against the employee's own recent normal, neither
of which needs a price to be meaningful.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.ai_operations_models import AIOpsCounter
from app.services.ai_operations import budget as t7_budget
from app.services.ai_operations import constants as O
from app.services.workforce import constants as W
from app.services.workforce_intelligence import collect
from app.services.workforce_intelligence import constants as C
from app.services.workforce_intelligence.scope import Scope

_log = logging.getLogger(__name__)

MEASURED = "measured"
ESTIMATED = "estimated"
UNKNOWN = "unknown"

# WHY THERE IS NO "ACTUAL". There is no code path in this platform that reads a
# provider invoice, so an "actual" label would have nothing to attach to. When
# one exists, it goes here and the payload gains a fourth label - it does not
# quietly replace `estimated`, because a screen that changed meaning without
# changing shape is how a comparison across months becomes nonsense.

COST_NOTE = (
    "These are AdvisorFlow's own estimates, computed from configured per-unit "
    "rates. They are not provider invoices, and no provider cost is available "
    "to this platform. Treat them as a size, not as a bill.")


def _rates() -> Dict[str, float]:
    """The per-unit rates the estimate was computed from, quoted back.

    A cost figure whose rate is not shown is a number nobody can check. These
    are read from T7's module rather than re-declared, so a rate change moves
    both the estimate and the explanation at once.
    """
    return {
        "sms_usd": float(getattr(t7_budget, "_COST_SMS", 0.0)),
        "email_usd": float(getattr(t7_budget, "_COST_EMAIL", 0.0)),
        "voice_per_minute_usd": float(
            getattr(t7_budget, "_COST_VOICE_MINUTE", 0.0)),
        "model_call_usd": float(getattr(t7_budget, "_COST_MODEL_CALL", 0.0)),
    }


def usage(db: Session, scope: Scope, *, window_key: str = C.DEFAULT_WINDOW,
          now: Optional[datetime] = None) -> Dict[str, Any]:
    """What was consumed, what it is estimated to have cost, and what is not known.

    The counters are T7's own `ai_ops_counters` rows, which the orchestrator
    increments as it works. Reading them rather than re-counting communications
    is the same choice T9 makes about the performance ledger, for the same
    reason: a counter that only increments cannot be changed retrospectively
    by somebody clearing a queue.
    """
    now = now or datetime.utcnow()
    since = now - timedelta(days=C.window_days(window_key))
    since_day = since.strftime("%Y-%m-%d")

    counters = collect.counter_rollup(db, scope, since_day)
    by_employee: Dict[str, Dict[str, Any]] = {}
    org_totals: Dict[str, int] = {}
    estimated_total = 0.0
    estimate_rows = 0

    for (scope_type, scope_id), slot in counters.items():
        metrics = slot["metrics"]
        usd = slot["usd"]
        if scope_type == t7_budget.SCOPE_EMPLOYEE:
            emp = by_employee.setdefault(scope_id, {"metrics": {}, "usd": 0.0,
                                                    "usd_rows": 0})
            for key, value in metrics.items():
                emp["metrics"][key] = emp["metrics"].get(key, 0) + int(value)
            emp["usd"] += sum(float(v or 0) for v in usd.values())
            emp["usd_rows"] += int(slot["rows"])
        elif scope_type == t7_budget.SCOPE_ORG:
            for key, value in metrics.items():
                org_totals[key] = org_totals.get(key, 0) + int(value)
            estimated_total += sum(float(v or 0) for v in usd.values())
            estimate_rows += int(slot["rows"])

    runs = collect.run_rollup(db, scope, since)
    model_cost = sum(float(r.get("cost_usd") or 0) for r in runs.values())
    model_cost_rows = sum(int(r.get("cost_rows") or 0) for r in runs.values())
    model_runs = sum(int(r.get("total") or 0) for r in runs.values())

    comms = collect.communication_rollup(db, scope, since)
    voice_seconds = sum(int(c.get("voice_seconds") or 0)
                        for c in comms.values())

    return {
        "window": window_key,
        "generated_at": now.isoformat(),
        "rates": _rates(),
        "note": COST_NOTE,
        "measured": {
            "actions": org_totals.get(t7_budget.METRIC_ACTIONS, 0),
            "messages_sms": org_totals.get(t7_budget.METRIC_SENDS_SMS, 0),
            "messages_email": org_totals.get(t7_budget.METRIC_SENDS_EMAIL, 0),
            "calls": org_totals.get(t7_budget.METRIC_CALLS, 0),
            "model_calls": org_totals.get(t7_budget.METRIC_MODEL_CALLS, 0),
            "runs": model_runs,
            "voice_seconds": voice_seconds,
            "label": MEASURED,
        },
        "estimated_cost": {
            "operations_usd": round(estimated_total, 4),
            "model_usd": round(model_cost, 4),
            "total_usd": round(estimated_total + model_cost, 4),
            "label": ESTIMATED,
            # HOW MUCH OF THE PERIOD THE ESTIMATE ACTUALLY COVERS. Runs that
            # carried no cost estimate are not zero-cost runs; they are runs
            # whose cost was never recorded, and a total that hides that is a
            # total that reads as complete.
            "runs_with_an_estimate": model_cost_rows,
            "runs_total": model_runs,
            "coverage_note": (
                "%d of %d runs carried a cost estimate. The rest are not "
                "zero - their cost was not recorded."
                % (model_cost_rows, model_runs)) if model_runs else None,
        },
        "provider_cost": {
            "value": None,
            "label": UNKNOWN,
            "note": ("No provider invoice or per-account rate is available to "
                     "AdvisorFlow. This is unknown, not zero."),
        },
        "by_employee": by_employee,
        "ceilings": {
            "cost_ceiling_usd": float(O.DEFAULT_COST_CEILING_USD),
            "actions_per_objective": int(O.DEFAULT_MAX_ACTIONS_PER_OBJECTIVE),
            "channel_daily_cap": int(O.DEFAULT_CHANNEL_DAILY_CAP),
            "voice_daily_cap": int(O.DEFAULT_VOICE_DAILY_CAP),
            "max_voice_seconds": int(O.DEFAULT_MAX_VOICE_SECONDS),
            "max_iterations": int(W.DEFAULT_MAX_ITERATIONS),
            "max_tool_calls": int(W.DEFAULT_MAX_TOOL_CALLS),
        },
    }


# ---------------------------------------------------------------------------
# RUNAWAY CONDITIONS
# ---------------------------------------------------------------------------


def _condition(code: str, severity: str, employee_id, organization_id, *,
               current, limit, unit, reason, action, trend=None,
               evidence=None) -> Dict[str, Any]:
    """One runaway condition, in the shape section 10 asks for.

    CURRENT / TREND / LIMIT / REASON / AFFECTED / RECOMMENDED ACTION, every
    time, so a screen can render any condition without knowing which one it
    is - and so a condition cannot be added that quietly omits its limit.
    """
    fraction = None
    if limit:
        try:
            fraction = round(float(current) / float(limit), 4)
        except (TypeError, ZeroDivisionError):
            fraction = None
    return {
        "code": code,
        "severity": severity,
        "organization_id": organization_id,
        "employee_id": employee_id,
        "current": current,
        "trend": trend,
        "limit": limit,
        "fraction_of_limit": fraction,
        "unit": unit,
        "reason": reason,
        "recommended_action": action,
        "evidence": evidence or {},
    }


def runaway(db: Session, scope: Scope, *,
            thresholds: Optional[Dict] = None,
            now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Consumption that is unusual, approaching a ceiling, or yielding nothing.

    NOT ONE OF THESE NEEDS A PRICE. Each compares a measured count against a
    configured ceiling or against the employee's own outcomes, so the whole
    detection works on a deployment where no cost is known at all - which is
    this deployment.
    """
    now = now or datetime.utcnow()
    th = thresholds or C.thresholds()
    warn_at = float(th["budget_warning_fraction"])
    today = now.strftime("%Y-%m-%d")
    out: List[Dict[str, Any]] = []

    # -- approaching a configured daily ceiling -----------------------------
    q = scope.apply(
        db.query(AIOpsCounter.organization_id, AIOpsCounter.scope_type,
                 AIOpsCounter.scope_id, AIOpsCounter.metric_key,
                 func.sum(AIOpsCounter.value),
                 func.sum(AIOpsCounter.value_usd)),
        AIOpsCounter.organization_id).filter(
            AIOpsCounter.metric_date == today)
    ceilings = {
        t7_budget.METRIC_SENDS_SMS: (int(O.DEFAULT_CHANNEL_DAILY_CAP),
                                     "messages"),
        t7_budget.METRIC_SENDS_EMAIL: (int(O.DEFAULT_CHANNEL_DAILY_CAP),
                                       "messages"),
        t7_budget.METRIC_CALLS: (int(O.DEFAULT_VOICE_DAILY_CAP), "calls"),
        t7_budget.METRIC_ACTIONS: (int(O.MAX_ACTIONS_CEILING), "actions"),
    }
    for (org_id, scope_type, scope_id, key, value, usd) in q.group_by(
            AIOpsCounter.organization_id, AIOpsCounter.scope_type,
            AIOpsCounter.scope_id, AIOpsCounter.metric_key).all():
        limit, unit = ceilings.get(key, (None, "events"))
        value = int(value or 0)
        if not limit or not value:
            continue
        if value < limit * warn_at:
            continue
        out.append(_condition(
            "approaching_daily_limit",
            C.SEV_CRITICAL if value >= limit else C.SEV_HIGH,
            scope_id if scope_type == t7_budget.SCOPE_EMPLOYEE else None,
            str(org_id), current=value, limit=limit, unit=unit,
            reason=("%s today is %d against a configured ceiling of %d."
                    % (key, value, limit)),
            action=("Nothing will be sent past the ceiling. Decide whether to "
                    "raise it deliberately or to reduce what is queued."),
            evidence={"metric": key, "counter_scope": scope_type,
                      "estimated_cost_usd": float(usd or 0),
                      "estimate_note": COST_NOTE}))

    # -- the cost CEILING, which is enforced even though the price is an
    #    estimate. An estimate that stops work is honest about being an
    #    estimate; an estimate presented as a bill is not.
    for (org_id, scope_type, scope_id, key, value, usd) in q.group_by(
            AIOpsCounter.organization_id, AIOpsCounter.scope_type,
            AIOpsCounter.scope_id, AIOpsCounter.metric_key).all():
        spend = float(usd or 0)
        ceiling = float(O.DEFAULT_COST_CEILING_USD)
        if spend < ceiling * warn_at:
            continue
        out.append(_condition(
            "approaching_cost_ceiling",
            C.SEV_CRITICAL if spend >= ceiling else C.SEV_HIGH,
            scope_id if scope_type == t7_budget.SCOPE_EMPLOYEE else None,
            str(org_id), current=round(spend, 4), limit=ceiling,
            unit="usd_estimated",
            reason=("Estimated spend today is about $%.2f against a ceiling of "
                    "$%.2f. This is AdvisorFlow's estimate, not an invoice."
                    % (spend, ceiling)),
            action=("Check what this employee is doing before raising the "
                    "ceiling."),
            evidence={"metric": key, "label": ESTIMATED,
                      "rates": _rates(), "note": COST_NOTE}))

    out.extend(_expensive_low_yield(db, scope, th, now))
    out.extend(_voice_conditions(db, scope, th, now))
    out.extend(_duplicate_suppression(db, scope, th, now))
    out.extend(_actions_per_objective(db, scope, th, now))
    return out


def _expensive_low_yield(db, scope, th, now) -> List[Dict[str, Any]]:
    """An employee doing a great deal and producing nothing.

    THE COMPARISON IS ACTIONS AGAINST OUTCOMES, not money against outcomes,
    and that is deliberate. It works with no price at all, and it is the
    question a manager actually has: is this employee earning the work it is
    doing? An employee with four hundred actions and no appointments, no
    qualifications and no handoffs is not expensive because of a rate card -
    it is expensive because it is achieving nothing.
    """
    since = now - timedelta(days=7)
    since_day = since.strftime("%Y-%m-%d")
    ops = collect.ops_action_rollup(db, scope, since)
    ledger = collect.performance_rollup(db, scope, since_day)
    employees = {e.id: e for e in collect.employees(db, scope)}
    minimum = int(th["minimum_denominator"])

    out = []
    for emp_id, slot in ops.items():
        allowed = int(slot.get("allowed", 0) or 0)
        if allowed < max(minimum, 20):
            continue
        led = ledger.get(emp_id, {})
        outcomes = (int(led.get("appointments", 0) or 0)
                    + int(led.get("qualified", 0) or 0)
                    + int(led.get("handoffs", 0) or 0))
        if outcomes:
            continue
        emp = employees.get(emp_id)
        out.append(_condition(
            "work_without_outcomes", C.SEV_HIGH, emp_id,
            str(getattr(emp, "organization_id", "") or ""),
            current=allowed, limit=None, unit="actions",
            reason=("%s has taken %d actions in the last 7 days and produced "
                    "no appointments, qualifications or handoffs."
                    % (getattr(emp, "name", None) or "This employee", allowed)),
            action=("Look at what it is doing before it does more of it. This "
                    "is usually a configuration problem, not a model one."),
            evidence={"actions_7d": allowed, "outcomes_7d": 0,
                      "ledger": {k: int(v) for k, v in led.items()}}))
    return out


def _voice_conditions(db, scope, th, now) -> List[Dict[str, Any]]:
    """Voice minutes, and whether they bought anything.

    VOICE IS DARK IN THIS BUILD and its daily cap is zero, so in production
    today this detector finds nothing - which is correct and is not a reason
    to leave it out. The architecture is real, the simulator exercises it, and
    a cost surface that only grows a voice detector on the day voice is
    switched on is a surface nobody has tested by then.
    """
    since = now - timedelta(days=7)
    comms = collect.communication_rollup(db, scope, since)
    ledger = collect.performance_rollup(db, scope,
                                        since.strftime("%Y-%m-%d"))
    employees = {e.id: e for e in collect.employees(db, scope)}
    out = []
    for emp_id, slot in comms.items():
        seconds = int(slot.get("voice_seconds", 0) or 0)
        if not seconds:
            continue
        calls = int((slot.get("by_channel") or {}).get(O.CHANNEL_VOICE, 0) or 0)
        average = round(seconds / calls, 1) if calls else None
        emp = employees.get(emp_id)
        led = ledger.get(emp_id, {})
        outcomes = (int(led.get("appointments", 0) or 0)
                    + int(led.get("qualified", 0) or 0))
        if average is not None and average > int(O.DEFAULT_MAX_VOICE_SECONDS):
            out.append(_condition(
                "voice_calls_running_long", C.SEV_HIGH, emp_id,
                str(getattr(emp, "organization_id", "") or ""),
                current=average, limit=int(O.DEFAULT_MAX_VOICE_SECONDS),
                unit="seconds_per_call",
                reason=("Calls are averaging %.0f seconds against a configured "
                        "maximum of %d." % (average,
                                            O.DEFAULT_MAX_VOICE_SECONDS)),
                action="Listen to one before the next batch goes out.",
                evidence={"calls": calls, "total_seconds": seconds}))
        if calls >= max(5, int(th["minimum_denominator"])) and not outcomes:
            out.append(_condition(
                "voice_without_outcomes", C.SEV_HIGH, emp_id,
                str(getattr(emp, "organization_id", "") or ""),
                current=calls, limit=None, unit="calls",
                reason=("%d calls in the last 7 days produced no appointments "
                        "or qualifications." % calls),
                action=("Compare this against the same employee's messaging "
                        "results before continuing with voice."),
                evidence={"calls": calls, "total_seconds": seconds,
                          "outcomes": outcomes}))
    return out


def _duplicate_suppression(db, scope, th, now) -> List[Dict[str, Any]]:
    """A spike of duplicates suppressed - which means something is looping.

    A duplicate refusal is the idempotency guard doing its job, so one is not
    interesting. A cluster of them is: something is asking for the same action
    over and over, and the guard is the only reason it is not happening.
    """
    since = now - timedelta(hours=24)
    ops = collect.ops_action_rollup(db, scope, since)
    employees = {e.id: e for e in collect.employees(db, scope)}
    threshold = int(th["denial_spike_24h"])
    out = []
    for emp_id, slot in ops.items():
        dupes = int((slot.get("denials") or {}).get(O.D_DUPLICATE, 0) or 0)
        if dupes < threshold:
            continue
        emp = employees.get(emp_id)
        out.append(_condition(
            "duplicate_suppression_spike", C.SEV_HIGH, emp_id,
            str(getattr(emp, "organization_id", "") or ""),
            current=dupes, limit=threshold, unit="suppressed_duplicates",
            reason=("%d duplicate actions were suppressed in 24 hours. The "
                    "guard stopped them; something is still asking."
                    % dupes),
            action=("Find what is re-issuing the same action - this is a loop, "
                    "not a coincidence."),
            evidence={"duplicates_24h": dupes,
                      "denial_code": O.D_DUPLICATE}))
    return out


def _actions_per_objective(db, scope, th, now) -> List[Dict[str, Any]]:
    """Conversations spending far more actions than the ceiling allows for."""
    ceiling = int(O.DEFAULT_MAX_ACTIONS_PER_OBJECTIVE)
    warn_at = float(th["budget_warning_fraction"])
    rows = collect.threads(db, scope, open_only=True, limit=500)
    out = []
    for t in rows:
        actions = int(t.action_count or 0)
        if actions < ceiling * warn_at:
            continue
        out.append(_condition(
            "objective_approaching_action_limit",
            C.SEV_CRITICAL if actions >= ceiling else C.SEV_NORMAL,
            t.employee_id, str(t.organization_id),
            current=actions, limit=ceiling, unit="actions",
            reason=("This one conversation has used %d of its %d permitted "
                    "actions." % (actions, ceiling)),
            action=("Decide whether it should be handed to a person rather "
                    "than continuing."),
            evidence={"thread_id": t.id, "state": t.state,
                      "outbound": int(t.outbound_count or 0),
                      "inbound": int(t.inbound_count or 0)}))
    return out


def report(db: Session, scope: Scope, *, window_key: str = C.DEFAULT_WINDOW,
           thresholds: Optional[Dict] = None,
           now: Optional[datetime] = None) -> Dict[str, Any]:
    """The Costs & Usage screen: what was used, what it may have cost, what is
    running away, and the sentence that keeps the estimate honest."""
    now = now or datetime.utcnow()
    conditions = runaway(db, scope, thresholds=thresholds, now=now)
    return {
        "usage": usage(db, scope, window_key=window_key, now=now),
        "conditions": conditions,
        "condition_count": len(conditions),
        "worst_severity": min(
            (c["severity"] for c in conditions),
            key=lambda s: C.SEVERITY_RANK.get(s, 99), default=None),
    }
