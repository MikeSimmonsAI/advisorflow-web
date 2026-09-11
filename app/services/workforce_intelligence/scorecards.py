"""EMPLOYEE SCORECARDS - explainable, and never comparing unlike jobs.

THE ONE THING THIS MODULE REFUSES TO DO.

There is no universal productivity score. A Receptionist answers people who
call in; a Reactivation Specialist reopens conversations with people who went
cold. Putting both on one 0-100 scale would require deciding how many answered
calls equal one revived lead, and nobody can decide that, so the number would
be arbitrary - and arbitrary numbers get acted on. Section 4 forbids it in as
many words, and the refusal is structural here: `benchmark` groups by job_role
and will not produce a cross-role comparison at all.

WHAT A SCORECARD ACTUALLY COMPARES, in order of how much it is worth:

    THIS PERIOD vs THE PREVIOUS PERIOD - the same employee, the same length of
    time, adjacent and non-overlapping. Always available, always meaningful.

    THIS PERIOD vs THIS EMPLOYEE'S OWN BASELINE - the mean daily rate over
    the periods before those. This is what catches a slow decline that a
    single period-over-period comparison reads as noise.

    THIS EMPLOYEE vs OTHERS DOING THE SAME JOB - only inside the same tenant,
    only with enough employees to be meaningless to reverse, and never across
    a brand or a platform. Section 4 permits same-job benchmarking "where
    tenant/privacy boundaries permit", and inside one customer's own workspace
    is the only place they do.

EVERY COMPARISON CARRIES ITS DENOMINATORS. A change is reported as "9 against
14 last period", not as "-36%", with the percentage alongside. A percentage
on its own is how two events and one of them becomes a crisis.
"""

import logging
import statistics
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.services.workforce import performance as wf_performance
from app.services.workforce_intelligence import collect
from app.services.workforce_intelligence import constants as C
from app.services.workforce_intelligence import metrics as M
from app.services.workforce_intelligence.scope import Scope

_log = logging.getLogger(__name__)

# The ledger metrics a trend is worth stating for. Deliberately the OUTCOME
# ones plus the two that explain them - a trend in "records assigned" is a
# statement about how much work somebody loaded, not about the employee.
TREND_METRICS = ("appointments", "qualified", "handoffs", "responses",
                 "messages_sent", "opt_outs", "records_eligible",
                 "policy_blocks", "provider_failures")

# The smallest group in which a same-job comparison is published. Below this,
# "the median of your Reactivation Specialists" is one other employee, and
# naming a median that is really one person's number is a privacy leak dressed
# as a statistic - even inside one tenant, where a manager can see both.
MIN_BENCHMARK_GROUP = 3


def _day(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _delta(current: int, previous: int) -> Dict[str, Any]:
    """The change, with both sides shown and the percentage kept honest."""
    change = int(current) - int(previous)
    if previous:
        fraction = round(float(change) / float(previous), 4)
    else:
        fraction = None
    return {
        "current": int(current),
        "previous": int(previous),
        "change": change,
        "change_fraction": fraction,
        "direction": ("up" if change > 0 else
                      ("down" if change < 0 else "flat")),
        "note": (None if previous else
                 "Nothing in the previous period to compare against. The "
                 "change is unknown, not infinite."),
    }


def _baseline(db: Session, scope: Scope, *, now: datetime, days: int,
              periods: int = 4) -> Dict[str, Dict[str, float]]:
    """Each employee's own normal, as a MEAN DAILY RATE per metric.

    A RATE RATHER THAN A TOTAL, because the baseline covers more days than the
    current period and comparing a 28-day total against a 7-day total would
    report every employee as collapsing. The current period is converted to a
    daily rate for the comparison and both are shown.

    An employee with no history has no baseline, and that is reported as such
    rather than as a baseline of zero - a new employee is not underperforming
    against a past it did not have.
    """
    start = now - timedelta(days=days * (periods + 1))
    end = now - timedelta(days=days)
    rows = collect.performance_rollup_window(db, scope, _day(start), _day(end))
    span = max(1, (end - start).days)
    out: Dict[str, Dict[str, float]] = {}
    for emp_id, totals in rows.items():
        out[emp_id] = {key: round(float(value) / span, 4)
                       for key, value in totals.items()}
    return out


def build(db: Session, scope: Scope, *, window_key: str = C.DEFAULT_WINDOW,
          now: Optional[datetime] = None,
          thresholds: Optional[Dict] = None) -> Dict[str, Any]:
    """A scorecard for every employee in scope, in a bounded query count.

    Five ledger statements and the metric rollups, whatever the employee
    count. The per-employee loop below touches nothing but dictionaries.
    """
    now = now or datetime.utcnow()
    th = thresholds or C.thresholds()
    days = C.window_days(window_key)
    current_from, current_to = now - timedelta(days=days), now
    previous_from, previous_to = now - timedelta(days=days * 2), current_from

    current = collect.performance_rollup_window(
        db, scope, _day(current_from), _day(current_to + timedelta(days=1)))
    previous = collect.performance_rollup_window(
        db, scope, _day(previous_from), _day(previous_to))
    baseline = _baseline(db, scope, now=now, days=days)

    per_metrics = M.employee_metrics(db, scope, window_key=window_key,
                                     now=now, thresholds=th)
    employees = collect.employees(db, scope)
    deployments = collect.deployment_by_employee(db, scope)
    work = collect.work_counts_by_employee_state(db, scope)
    threads = collect.thread_counts_by_employee_state(db, scope)

    cards: List[Dict[str, Any]] = []
    for emp in employees:
        cards.append(_card(
            emp, deployment=deployments.get(emp.id),
            current=current.get(emp.id, {}), previous=previous.get(emp.id, {}),
            baseline=baseline.get(emp.id), days=days,
            values=(per_metrics.get(emp.id) or {}).get("values", {}),
            work={s: n for (e, s), n in work.items() if e == emp.id},
            threads={s: n for (e, s), n in threads.items() if e == emp.id},
            window_key=window_key,
            delta_threshold=float(th["baseline_delta_fraction"]),
            minimum=int(th["minimum_denominator"])))

    return {
        "window": window_key,
        "generated_at": now.isoformat(),
        "period": {"current_from": current_from.isoformat(),
                   "current_to": current_to.isoformat(),
                   "previous_from": previous_from.isoformat(),
                   "previous_to": previous_to.isoformat(),
                   "days": days},
        "metric_labels": M.ledger_vocabulary(),
        "cards": cards,
        "benchmarks": benchmark(cards, minimum_group=MIN_BENCHMARK_GROUP),
        "comparison_policy": (
            "Employees are compared with their own previous period and their "
            "own baseline. Comparisons against other employees are only made "
            "within the same job and the same workspace, and only when there "
            "are at least %d of them. Different jobs are never placed on one "
            "scale." % MIN_BENCHMARK_GROUP),
    }


def _card(emp, *, deployment, current, previous, baseline, days, values, work,
          threads, window_key, delta_threshold, minimum) -> Dict[str, Any]:
    trends = {}
    for key in TREND_METRICS:
        cur = int(current.get(key, 0) or 0)
        prev = int(previous.get(key, 0) or 0)
        entry = _delta(cur, prev)
        entry["label"] = wf_performance.METRICS.get(key, key)
        entry["metric"] = key
        if baseline is not None:
            base_rate = float(baseline.get(key, 0.0) or 0.0)
            cur_rate = round(float(cur) / max(1, days), 4)
            entry["baseline_daily"] = base_rate
            entry["current_daily"] = cur_rate
            if base_rate > 0:
                drift = round((cur_rate - base_rate) / base_rate, 4)
                entry["baseline_change_fraction"] = drift
                entry["off_baseline"] = abs(drift) >= delta_threshold
            else:
                entry["baseline_change_fraction"] = None
                entry["off_baseline"] = False
                entry["baseline_note"] = (
                    "This employee has done none of this before, so there is "
                    "no baseline to compare against.")
        else:
            entry["baseline_note"] = (
                "Not enough history for a baseline yet. Unknown, not zero.")
            entry["off_baseline"] = False
        trends[key] = entry

    open_work = sum(n for state, n in work.items()
                    if state not in C.WORK_TERMINAL_STATES)
    return {
        "employee_id": emp.id,
        "name": emp.name,
        # JOB IS THE COMPARISON KEY and is carried on every card so a renderer
        # cannot group by anything else by accident.
        "job_role": emp.job_role,
        "status": emp.status,
        "activation_state": emp.activation_state,
        "paused": emp.paused_at is not None,
        "deployment": ({
            "id": deployment.id,
            "state": deployment.state,
            "commercial_state": deployment.commercial_state,
            "readiness_state": deployment.readiness_state,
            "live": deployment.state in C.DEPLOY_LIVE_STATES,
        } if deployment is not None else {
            "id": None, "state": None,
            "note": ("No deployment record. This actor was created directly "
                     "rather than hired through the workforce catalogue."),
        }),
        "window": window_key,
        "activity": {
            "open_work_items": open_work,
            "work_by_state": work,
            "conversations_by_state": threads,
        },
        "outcomes": {k: v for k, v in values.items()},
        "trends": trends,
        "efficiency": _efficiency(values, minimum),
        "exceptions": {
            "policy_denials": (values.get("policy_denials") or {}).get("value"),
            "tool_failures": (values.get("tool_failures") or {}).get("value"),
            "provider_failures": (values.get("provider_failures")
                                  or {}).get("value"),
            "runs_aborted": (values.get("runs_aborted") or {}).get("value"),
        },
        "human_involvement": {
            "handoffs": (values.get("handoffs") or {}).get("value"),
            "handoffs_open": (values.get("handoffs_open") or {}).get("value"),
            "interventions": (values.get("human_interventions")
                              or {}).get("value"),
        },
        "revenue": M.revenue_value().as_dict(),
    }


def _efficiency(values: Dict[str, Dict], minimum: int) -> Dict[str, Any]:
    """Work per outcome - the honest version of "is this worth running".

    NOT A COST FIGURE. Actions per outcome needs no price, works on a
    deployment where no provider cost is known, and is the number a manager
    can actually act on: forty messages per appointment is a conversation
    problem, not a billing one.
    """
    def _v(key):
        entry = values.get(key) or {}
        return entry.get("value")

    sent = _v("messages_sent") or 0
    outcomes = sum(int(_v(k) or 0) for k in ("appointments_booked",
                                             "qualified", "handoffs"))
    return {
        "outcomes": outcomes,
        "messages_sent": sent,
        "messages_per_outcome": (
            round(float(sent) / outcomes, 2) if outcomes else None),
        "messages_per_outcome_note": (
            None if outcomes else
            "No outcomes yet in this period, so there is nothing to divide "
            "by. Unknown, not zero."),
        "cost_per_outcome": None,
        "cost_per_outcome_note": (
            "No provider cost is available to this platform, so cost per "
            "outcome is unknown rather than zero. AdvisorFlow's own estimate "
            "is shown on the Costs screen and is labelled as an estimate."),
        "minimum_denominator": minimum,
    }


def benchmark(cards: List[Dict[str, Any]], *,
              minimum_group: int = MIN_BENCHMARK_GROUP) -> Dict[str, Any]:
    """Same job, same workspace, enough of them - or nothing at all.

    THREE REFUSALS, EACH FOR A DIFFERENT REASON:

        Different jobs are never compared. There is no denominator that makes
        a receptionist and a reactivation specialist commensurable, so the
        grouping key is `job_role` and there is no "all employees" bucket.

        Groups smaller than `minimum_group` publish nothing. A median over two
        employees is one employee's number with a statistical hat on.

        Nothing here crosses a tenant. `cards` arrives already scoped, and
        this function receives no database session at all - it cannot reach
        another customer's employees even by mistake, which is a stronger
        guarantee than remembering to filter.
    """
    groups: Dict[str, List[Dict]] = {}
    for card in cards:
        groups.setdefault(card.get("job_role") or "unknown", []).append(card)

    out: Dict[str, Any] = {}
    for job_role, members in groups.items():
        if len(members) < minimum_group:
            out[job_role] = {
                "job_role": job_role,
                "employees": len(members),
                "published": False,
                "why": ("Only %d employee%s in this job. A comparison needs at "
                        "least %d to mean anything."
                        % (len(members), "" if len(members) == 1 else "s",
                           minimum_group)),
            }
            continue
        stats: Dict[str, Any] = {}
        for key in TREND_METRICS:
            series = [int(((m.get("trends") or {}).get(key) or {})
                          .get("current", 0) or 0) for m in members]
            if not any(series):
                stats[key] = {"median": 0, "best": 0, "note": (
                    "Nobody in this job has produced any of this in the "
                    "period.")}
                continue
            stats[key] = {
                "median": statistics.median(series),
                "best": max(series),
                "worst": min(series),
            }
        out[job_role] = {
            "job_role": job_role,
            "employees": len(members),
            "published": True,
            "metrics": stats,
            "scope_note": ("Compared only against other employees doing the "
                           "same job inside this workspace."),
        }
    return out


def for_employee(db: Session, scope: Scope, employee_id: str, *,
                 window_key: str = C.DEFAULT_WINDOW,
                 now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """One employee's scorecard, with the same numbers the team view shows.

    BUILT FROM THE SAME FUNCTION, deliberately. A detail screen with its own
    query is a detail screen that eventually disagrees with the list it was
    opened from, and "the team page says 14 and this page says 12" is a
    support call nobody can resolve without reading both queries.
    """
    built = build(db, scope, window_key=window_key, now=now)
    for card in built["cards"]:
        if card["employee_id"] == employee_id:
            return {
                "window": built["window"],
                "period": built["period"],
                "generated_at": built["generated_at"],
                "card": card,
                "benchmark": built["benchmarks"].get(card.get("job_role")
                                                     or "unknown"),
                "comparison_policy": built["comparison_policy"],
            }
    return None
