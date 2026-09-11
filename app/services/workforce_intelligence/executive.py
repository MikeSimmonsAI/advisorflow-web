"""THE T10 CONTRACT - a stable read of the AI workforce, for a layer above.

WHY THIS EXISTS AS ITS OWN MODULE. T10 is the Executive / Owner Command
Center. It will want to say something about the AI workforce alongside
everything else a business does, and the wrong way for it to get that is to
query `ai_work_items` and `ai_conversation_threads` itself. That would make
T10 a second consumer of T6/T7/T8's table shapes, and the day one of those
changes there would be two places to fix and only one of them would have
tests.

So T9 publishes ONE function with ONE documented shape, and T10 calls it.
`snapshot` is that function. Its keys are the brief's own list - status, active
employees, active objectives, outcomes, appointments, handoffs, human
attention required, quality, cost, trend, major risks, major opportunities,
critical exceptions, last updated - and they do not change without a version
change.

THE FOUR CLASSES SURVIVE THE JOURNEY. Section 16 requires this contract to
distinguish FACT, METRIC, INTERPRETATION and UNKNOWN, and the requirement is
not decorative: an executive dashboard is exactly where an estimate becomes a
board slide. Every value here carries its class, `revenue` is unknown and says
so, and provider cost is unknown and says so.

NO T10 UI IS BUILT HERE, and no screen in this file. What is built is the read
a screen can be built from.

TENANCY IS THE CALLER'S SCOPE, unchanged. `snapshot` takes a Scope like
everything else in this package, so an executive asking about their brand gets
their brand and nothing wider - the contract adds no authority of its own.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.services.workforce_intelligence import attention as t9_attention
from app.services.workforce_intelligence import collect
from app.services.workforce_intelligence import constants as C
from app.services.workforce_intelligence import cost as t9_cost
from app.services.workforce_intelligence import findings as t9_findings
from app.services.workforce_intelligence import metrics as t9_metrics
from app.services.workforce_intelligence import quality as t9_quality
from app.services.workforce_intelligence import readmodel as t9_readmodel
from app.services.workforce_intelligence import reconciliation as t9_rec
from app.services.workforce_intelligence import review as t9_review
from app.services.workforce_intelligence import scorecards as t9_scorecards
from app.services.workforce_intelligence.scope import Scope

_log = logging.getLogger(__name__)

# THE CONTRACT VERSION. T10 should assert on it. A consumer that pins a
# version and gets a different one has been told, rather than discovering it
# from a missing key three screens deep.
CONTRACT_VERSION = "t9.executive.v1"

CONTRACT_KEYS = (
    "contract_version", "generated_at", "scope", "window",
    "ai_workforce_status", "active_employees", "active_objectives",
    "outcomes", "appointments", "handoffs", "human_attention_required",
    "quality", "cost", "trend", "major_risks", "major_opportunities",
    "critical_exceptions", "last_updated",
)


def _classed(value, evidence: str, note: Optional[str] = None,
             calculation: Optional[str] = None) -> Dict[str, Any]:
    """Every value in this contract carries its class. No bare numbers."""
    return {"value": value, "class": evidence, "note": note,
            "calculation": calculation}


def snapshot(db: Session, scope: Scope, *,
             window_key: str = C.DEFAULT_WINDOW,
             thresholds: Optional[Dict] = None,
             now: Optional[datetime] = None) -> Dict[str, Any]:
    """The whole executive read, in one call, with every value labelled."""
    now = now or datetime.utcnow()
    th = thresholds or C.thresholds()

    per_employee = t9_metrics.employee_metrics(db, scope,
                                               window_key=window_key,
                                               now=now, thresholds=th)
    totals = t9_metrics.totals(per_employee,
                               minimum=int(th["minimum_denominator"]),
                               window_key=window_key)
    values = totals["values"]

    deployments = collect.deployments(db, scope, include_retired=False)
    employees = collect.employees(db, scope)
    live = [d for d in deployments if d.state in C.DEPLOY_LIVE_STATES]
    work = collect.work_counts_by_state(db, scope)
    threads = collect.thread_counts_by_state(db, scope)
    attention = t9_attention.queue(db, scope, now=now, limit=200)
    reviews = t9_review.queue(db, scope, limit=200, now=now)
    graded = t9_quality.evaluate(db, scope, window_key=window_key, now=now)
    costs = t9_cost.report(db, scope, window_key=window_key, thresholds=th,
                           now=now)
    stored_findings = t9_findings.listing(db, scope, limit=100)["findings"]
    contradictions = t9_rec.listing(db, scope, limit=100)["contradictions"]
    cards = t9_scorecards.build(db, scope, window_key=window_key, now=now,
                                thresholds=th)

    mark = collect.watermark(db, scope)
    open_work = sum(int(n or 0) for state, n in work.items()
                    if state not in C.WORK_TERMINAL_STATES)

    return {
        "contract_version": CONTRACT_VERSION,
        "generated_at": now.isoformat(),
        "scope": scope.as_dict(),
        "window": window_key,

        "ai_workforce_status": {
            "statement": _status_statement(deployments, live, attention),
            "employees": _classed(len(employees), C.EV_FACT,
                                  calculation="rows in ai_employees"),
            "deployments": _classed(len(deployments), C.EV_FACT,
                                    calculation="non-retired deployments"),
            "working": _classed(len(live), C.EV_FACT,
                                calculation="deployments in a live state"),
            "paused": _classed(
                sum(1 for d in deployments if d.state == C.DEPLOY_PAUSED),
                C.EV_FACT),
            "stopped": _classed(
                sum(1 for d in deployments if d.state == C.DEPLOY_SUSPENDED),
                C.EV_FACT),
        },

        "active_employees": [{
            "employee_id": d.employee_id,
            "name": d.display_name,
            "state": d.state,
            "commercial_state": d.commercial_state,
            "readiness_state": d.readiness_state,
        } for d in live],

        "active_objectives": {
            "open_work_items": _classed(open_work, C.EV_FACT,
                                        calculation="non-terminal work items"),
            "open_conversations": _classed(
                sum(int(n or 0) for n in threads.values()), C.EV_FACT,
                calculation="open ai_conversation_threads"),
            "waiting_for_response": _classed(
                int(threads.get(C.THREAD_WAITING, 0) or 0), C.EV_FACT),
            "blocked": _classed(
                int(threads.get(C.THREAD_BLOCKED, 0) or 0)
                + int(threads.get(C.THREAD_FAILED, 0) or 0), C.EV_FACT),
        },

        "outcomes": {
            key: _classed(entry.get("value"),
                          entry.get("evidence", C.EV_METRIC),
                          note=entry.get("note"),
                          calculation=entry.get("calculation"))
            for key, entry in values.items()
        },

        "appointments": {
            "booked": _classed(
                (values.get("appointments_booked") or {}).get("value"),
                C.EV_FACT, calculation="ai_performance_entries.appointments"),
            "standing": _classed(
                (values.get("appointments_standing") or {}).get("value"),
                C.EV_FACT),
            "cancelled": _classed(
                (values.get("appointments_cancelled") or {}).get("value"),
                C.EV_FACT),
            # THE HONEST ONE. Attendance is not recorded on a booking, so a
            # completion figure would be an invention - and this is precisely
            # the number that ends up on a board slide.
            "completed": _classed(
                None, C.EV_UNKNOWN,
                note=("Attendance is not recorded against a booking in "
                      "AdvisorFlow. Unknown, not zero.")),
        },

        "handoffs": {
            "raised": _classed((values.get("handoffs") or {}).get("value"),
                               C.EV_FACT),
            "open": _classed((values.get("handoffs_open") or {}).get("value"),
                             C.EV_FACT),
            "accepted": _classed(
                (values.get("handoffs_accepted") or {}).get("value"),
                C.EV_FACT),
        },

        "human_attention_required": {
            "total": _classed(attention["total"], C.EV_FACT),
            "critical": _classed(
                attention["by_severity"].get(C.SEV_CRITICAL, 0), C.EV_FACT),
            "awaiting_review": _classed(reviews["undecided"], C.EV_FACT),
            "headline": attention["headline"],
        },

        "quality": _quality_block(graded),
        "cost": _cost_block(costs),
        "trend": _trend_block(cards),
        "major_risks": _risks(attention, contradictions, graded),
        "major_opportunities": _opportunities(stored_findings, cards),
        "critical_exceptions": [i for i in attention["items"]
                                if i["severity"] == C.SEV_CRITICAL][:25],
        "last_updated": {
            "computed_at": now.isoformat(),
            "newest_source_event": mark.isoformat() if mark else None,
            "note": ("`newest_source_event` is the most recent authoritative "
                     "record this scope can see. When it is older than "
                     "`computed_at`, nothing has happened since these numbers "
                     "were worked out."),
        },
    }


def _status_statement(deployments, live, attention) -> str:
    if not deployments:
        return "No AI employees have been hired."
    if not live:
        return ("%d AI employees are set up and none of them is working."
                % len(deployments))
    critical = attention["by_severity"].get(C.SEV_CRITICAL, 0)
    if critical:
        return ("%d of %d AI employees are working, and %d thing%s cannot "
                "wait." % (len(live), len(deployments), critical,
                           "" if critical == 1 else "s"))
    return ("%d of %d AI employees are working."
            % (len(live), len(deployments)))


def _quality_block(graded: Dict[str, Any]) -> Dict[str, Any]:
    """Quality for an executive: exceptions counted, scores NOT averaged.

    THERE IS NO WORKFORCE QUALITY SCORE and there deliberately is not one.
    Averaging a receptionist's score with a reactivation specialist's produces
    a number that moves when the mix of employees changes rather than when the
    work changes - and an executive dashboard is the last place that belongs.
    What is reported instead is the count of employees with critical
    exceptions, which is a fact and is actionable.
    """
    employees = graded.get("employees") or {}
    with_critical = []
    measured = 0
    for emp_id, slot in employees.items():
        score = (slot.get("score") or {})
        if score.get("score") is not None:
            measured += 1
        if score.get("critical_failures"):
            with_critical.append({"employee_id": emp_id,
                                  "name": slot.get("name"),
                                  "dimensions": score["critical_failures"]})
    exceptions = graded.get("exceptions") or {}
    return {
        "employees_measured": _classed(measured, C.EV_FACT),
        "employees_with_critical_exceptions": _classed(len(with_critical),
                                                       C.EV_FACT),
        "critical_exception_detail": with_critical[:25],
        "policy_exceptions": _classed(len(exceptions.get("policy") or []),
                                      C.EV_FACT),
        "stop_condition_exceptions": _classed(
            len(exceptions.get("stop_condition") or []), C.EV_FACT),
        "opt_out_exceptions": _classed(len(exceptions.get("opt_out") or []),
                                       C.EV_FACT),
        "workforce_score": _classed(
            None, C.EV_UNKNOWN,
            note=("AdvisorFlow does not publish a single quality score across "
                  "different jobs. A receptionist and a reactivation "
                  "specialist are not on one scale.")),
        "platform_evaluation": graded.get("platform_evaluation"),
    }


def _cost_block(costs: Dict[str, Any]) -> Dict[str, Any]:
    usage = costs.get("usage") or {}
    measured = usage.get("measured") or {}
    estimated = usage.get("estimated_cost") or {}
    return {
        "measured": {key: _classed(value, C.EV_FACT)
                     for key, value in measured.items() if key != "label"},
        "estimated_usd": _classed(
            estimated.get("total_usd"), C.EV_METRIC,
            note=usage.get("note"),
            calculation="platform per-unit rate estimates, not an invoice"),
        "estimate_coverage": estimated.get("coverage_note"),
        "provider_cost_usd": _classed(
            None, C.EV_UNKNOWN,
            note=("No provider invoice or per-account rate is available to "
                  "AdvisorFlow. Unknown, not zero.")),
        "cost_per_outcome": _classed(
            None, C.EV_UNKNOWN,
            note=("Requires a provider cost, which this platform does not "
                  "have. Effort per outcome is reported instead, on the "
                  "employee scorecards.")),
        "runaway_conditions": _classed(costs.get("condition_count"),
                                       C.EV_FACT),
        "worst_condition_severity": costs.get("worst_severity"),
    }


def _trend_block(cards: Dict[str, Any]) -> Dict[str, Any]:
    """Direction of travel, summed across employees rather than averaged."""
    totals: Dict[str, Dict[str, int]] = {}
    for card in cards.get("cards") or []:
        for key, trend in (card.get("trends") or {}).items():
            slot = totals.setdefault(key, {"current": 0, "previous": 0})
            slot["current"] += int(trend.get("current", 0) or 0)
            slot["previous"] += int(trend.get("previous", 0) or 0)
    out: Dict[str, Any] = {}
    for key, slot in totals.items():
        change = slot["current"] - slot["previous"]
        fraction = (round(change / slot["previous"], 4)
                    if slot["previous"] else None)
        out[key] = {
            "current": _classed(slot["current"], C.EV_FACT),
            "previous": _classed(slot["previous"], C.EV_FACT),
            "change": _classed(change, C.EV_METRIC,
                               calculation="this period minus previous period"),
            "change_fraction": _classed(
                fraction, C.EV_METRIC if fraction is not None else C.EV_UNKNOWN,
                note=(None if fraction is not None else
                      "Nothing in the previous period to compare against."),
                calculation="change / previous period"),
        }
    return {"period": cards.get("period"), "metrics": out,
            "note": ("Totals are summed across employees and the change is "
                     "computed once. Averaging per-employee percentages would "
                     "let a small new employee move the headline.")}


def _risks(attention: Dict[str, Any], contradictions: List[Dict],
           graded: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The things an owner would want raised, each with its evidence.

    A RISK IS AN INTERPRETATION and is labelled as one. The underlying counts
    are facts and travel with it, so an executive summary never asserts
    something the rows do not support.
    """
    out: List[Dict[str, Any]] = []
    critical = [i for i in attention["items"]
                if i["severity"] == C.SEV_CRITICAL]
    if critical:
        out.append({
            "risk": "Work is stopped or unattended",
            "class": C.EV_INTERPRETATION,
            "evidence": {"critical_items": len(critical),
                         "kinds": sorted({i["kind"] for i in critical}),
                         "class": C.EV_FACT},
            "recommended_action": "Open Needs Attention.",
        })
    exceptions = graded.get("exceptions") or {}
    breaches = (len(exceptions.get("policy") or [])
                + len(exceptions.get("stop_condition") or [])
                + len(exceptions.get("opt_out") or []))
    if breaches:
        out.append({
            "risk": "Messages were sent that should not have been",
            "class": C.EV_INTERPRETATION,
            "evidence": {"exceptions": breaches, "class": C.EV_FACT,
                         "detail": {
                             "policy": len(exceptions.get("policy") or []),
                             "after_stop":
                                 len(exceptions.get("stop_condition") or []),
                             "after_opt_out":
                                 len(exceptions.get("opt_out") or [])}},
            "recommended_action": ("Review these before anything else. This "
                                   "is the one category with a compliance "
                                   "consequence."),
        })
    if contradictions:
        out.append({
            "risk": "Systems disagree about an AI employee",
            "class": C.EV_INTERPRETATION,
            "evidence": {"contradictions": len(contradictions),
                         "checks": sorted({c["check"]
                                           for c in contradictions}),
                         "class": C.EV_FACT},
            "recommended_action": ("Each names the system that owns the fix. "
                                   "AdvisorFlow does not repair these "
                                   "automatically."),
        })
    return out


def _opportunities(stored_findings: List[Dict],
                   cards: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Where there is unused capacity, stated with the number behind it."""
    out: List[Dict[str, Any]] = []
    for f in stored_findings:
        if f["code"] not in (C.F_ELIGIBLE_BACKLOG, C.F_IDLE_EMPLOYEE):
            continue
        out.append({
            "opportunity": f["headline"],
            "class": C.EV_INTERPRETATION,
            "evidence": {"facts": (f.get("fact") or {}).get("items", []),
                         "metrics": (f.get("metric") or {}).get("items", []),
                         "class": C.EV_FACT},
            "recommended_action": (f.get("recommendation") or {}).get("text"),
            "employee_id": f.get("employee_id"),
        })
    return out[:25]


def cached_snapshot(db: Session, scope: Scope, *,
                    window_key: str = C.DEFAULT_WINDOW,
                    force: bool = False,
                    now: Optional[datetime] = None) -> Dict[str, Any]:
    """The contract, materialised. T10 reads this rather than the tables."""
    now = now or datetime.utcnow()
    return t9_readmodel.get_or_build(
        db, scope, C.V_EXECUTIVE,
        lambda: snapshot(db, scope, window_key=window_key, now=now),
        window_key=window_key, force=force, now=now)


def describe_contract() -> Dict[str, Any]:
    """What T10 can rely on. Published so a consumer can assert on it."""
    return {
        "version": CONTRACT_VERSION,
        "keys": list(CONTRACT_KEYS),
        "value_classes": list(C.EVIDENCE_CLASSES),
        "guarantees": [
            "Every value carries its class: fact, metric, interpretation or "
            "unknown.",
            "Unknown is never rendered as zero.",
            "Revenue attribution is always unknown - AdvisorFlow does not "
            "attribute revenue to an AI employee.",
            "Provider cost is always unknown - no invoice is available to "
            "this platform.",
            "Appointment completion is always unknown - attendance is not "
            "recorded against a booking.",
            "There is no single quality score across different jobs.",
            "Scope is the caller's own. This contract adds no authority.",
        ],
    }
