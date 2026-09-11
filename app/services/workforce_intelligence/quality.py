"""QUALITY - graded on what happened, never on how it sounded.

SECTION 7 IN ONE SENTENCE: do not grade merely on whether language sounded
pleasant. Every dimension below is answered by a row that exists or does not -
a state, a provider verdict, an eligibility result, a handoff's contents, a
booking's status. Not one of them reads a message body, and not one of them
asks a model what it thinks of a message body.

WHY THAT MATTERS MORE THAN IT SOUNDS. A quality score built from tone is a
model grading a model, and it has two properties that make it useless: it
moves when the grader changes rather than when the work changes, and nobody
can argue with it. A customer shown "quality 72%" with no row behind it will
either ignore it or escalate it, and both are worse than no number.

THE VIOLATIONS ARE THE POINT, NOT THE PERCENTAGE. Three of these dimensions -
policy compliance, stop-condition compliance, and whether a person's request
was respected - are checks for things that MUST NOT HAVE HAPPENED. When one
fires it names the exact rows, because "97% compliant" is not a finding and
"this message went out after the person said stop, here it is" is.

QUALITY EVALUATION MUST NEVER GRANT AUTHORITY. Section 7 again. Nothing in
this module writes to an employee, a policy, a channel or an entitlement. A
bad verdict can recommend review or a pause, and `coaching.py` refuses to
carry any consequence outside `QUALITY_ALLOWED_CONSEQUENCES`.

WHERE A DIMENSION IS NOT MEASURABLE IT SAYS SO. An employee whose
configuration declares no required information cannot be graded on collecting
it, and the answer is UNMEASURED rather than 100% - a dimension that scores
perfectly because nothing was asked of it is how a composite score becomes
meaningless.
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.ai_operations_models import (AICommunication,
                                             AIConversationThread,
                                             AIInboundEvent)
from app.models.models import BookingLink, Lead
from app.models.workforce_models import AIHandoff, AIToolExecution, AIWorkItem
from app.services.ai_operations import constants as O
from app.services.workforce import constants as W
from app.services.workforce_intelligence import collect
from app.services.workforce_intelligence import constants as C
from app.services.workforce_intelligence.scope import Scope

_log = logging.getLogger(__name__)

UNMEASURED = "unmeasured"


@dataclass
class Dimension:
    """One graded dimension, with its denominator and its exceptions named."""

    key: str
    label: str
    measured: bool
    passed: int = 0
    total: int = 0
    calculation: str = ""
    note: Optional[str] = None
    violations: List[Dict[str, Any]] = field(default_factory=list)
    critical: bool = False

    @property
    def rate(self) -> Optional[float]:
        if not self.measured or self.total <= 0:
            return None
        return round(float(self.passed) / float(self.total), 4)

    def as_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["rate"] = self.rate
        out["status"] = (UNMEASURED if not self.measured
                         else ("clean" if not self.violations else "exceptions"))
        out["evidence"] = C.EV_METRIC if self.measured else C.EV_UNKNOWN
        return out


def _unmeasured(key: str, why: str) -> Dimension:
    return Dimension(key=key, label=C.QUALITY_LABELS.get(key, key),
                     measured=False, note=why,
                     critical=key in C.QUALITY_CRITICAL)


def _measured(key: str, passed: int, total: int, calculation: str,
              violations=None) -> Dimension:
    return Dimension(key=key, label=C.QUALITY_LABELS.get(key, key),
                     measured=True, passed=int(passed), total=int(total),
                     calculation=calculation, violations=list(violations or []),
                     critical=key in C.QUALITY_CRITICAL)


# THE WEIGHTS. Section 4: if a composite score exists, every component and
# weighting must be explainable and deterministic. No mystery AI score.
#
# These are published in the payload alongside the score, they are the same
# for every employee and every customer, and the arithmetic is a weighted mean
# over the dimensions that were actually MEASURED - an unmeasured dimension
# contributes neither its weight nor a default, so an employee is never
# rewarded for a question nobody asked it.
WEIGHTS = {
    C.Q_POLICY_COMPLIANCE: 3.0,
    C.Q_STOP_COMPLIANCE: 3.0,
    C.Q_REQUEST_RESPECTED: 3.0,
    C.Q_OBJECTIVE_COMPLETION: 2.0,
    C.Q_HANDOFF_CORRECTNESS: 2.0,
    C.Q_APPOINTMENT_BEHAVIOUR: 2.0,
    C.Q_TOOL_CORRECTNESS: 1.5,
    C.Q_ESCALATION: 1.5,
    C.Q_REQUIRED_INFORMATION: 1.0,
    C.Q_QUALIFICATION_COMPLETENESS: 1.0,
    C.Q_FACTUAL_GROUNDING: 1.0,
}

SCORE_EXPLANATION = (
    "A weighted mean of the dimensions that could be measured for this "
    "employee. Dimensions that could not be measured are excluded entirely "
    "rather than counted as perfect. The weights are fixed, identical for "
    "every employee and every customer, and published with the score.")


# ---------------------------------------------------------------------------
# THE EVIDENCE GATHER - one pass over the window, shared by every dimension
# ---------------------------------------------------------------------------


def _gather(db: Session, scope: Scope, since: datetime) -> Dict[str, Any]:
    """Everything every dimension needs, in a fixed number of statements.

    Gathered once and sliced per employee, for the reason `collect` exists: a
    dimension that fetched its own rows would be a query per dimension per
    employee, and there are eleven dimensions.
    """
    # Terminal work outcomes, per employee.
    outcomes = {}
    q = scope.apply(
        db.query(AIWorkItem.employee_id, AIWorkItem.outcome,
                 func.count(AIWorkItem.id)),
        AIWorkItem.organization_id).filter(
            AIWorkItem.terminal_at.isnot(None),
            AIWorkItem.terminal_at >= since)
    for emp, outcome, n in q.group_by(AIWorkItem.employee_id,
                                      AIWorkItem.outcome).all():
        outcomes.setdefault(emp or "", {})[outcome or "unknown"] = int(n or 0)

    # Tool executions that were ALLOWED - a refusal is the gateway working,
    # not the employee failing, so refusals are excluded from this denominator
    # entirely rather than counted as failures.
    tools = {}
    tq = scope.apply(
        db.query(AIToolExecution.employee_id, AIToolExecution.status,
                 func.count(AIToolExecution.id)),
        AIToolExecution.organization_id).filter(
            AIToolExecution.decision == "allowed",
            AIToolExecution.created_at >= since)
    for emp, status, n in tq.group_by(AIToolExecution.employee_id,
                                      AIToolExecution.status).all():
        tools.setdefault(emp or "", {})[status or "unknown"] = int(n or 0)

    # POLICY VIOLATIONS. A communication that REACHED a sent state while its
    # recorded eligibility verdict was DENY. The gate chain should make this
    # impossible; that is exactly why it is checked rather than assumed.
    violations = []
    vq = scope.apply(
        db.query(AICommunication.id, AICommunication.employee_id,
                 AICommunication.thread_id, AICommunication.channel,
                 AICommunication.eligibility_result, AICommunication.state,
                 AICommunication.created_at),
        AICommunication.organization_id).filter(
            AICommunication.created_at >= since,
            AICommunication.direction == O.OUTBOUND,
            AICommunication.eligibility_result == O.DENY,
            AICommunication.state.in_((O.SENT, O.DELIVERED,
                                       O.WAITING_FOR_RESPONSE)))
    for row in vq.limit(200).all():
        violations.append({"communication_id": row[0], "employee_id": row[1],
                           "thread_id": row[2], "channel": row[3],
                           "eligibility": row[4], "state": row[5],
                           "at": row[6].isoformat() if row[6] else None})

    # Outbound sends per employee, the denominator for policy compliance.
    sends = {}
    sq = scope.apply(
        db.query(AICommunication.employee_id, func.count(AICommunication.id)),
        AICommunication.organization_id).filter(
            AICommunication.created_at >= since,
            AICommunication.direction == O.OUTBOUND,
            AICommunication.state.in_((O.SENT, O.DELIVERED,
                                       O.WAITING_FOR_RESPONSE,
                                       O.RESPONSE_RECEIVED, O.COMPLETED)))
    for emp, n in sq.group_by(AICommunication.employee_id).all():
        sends[emp or ""] = int(n or 0)

    # STOP-CONDITION VIOLATIONS. An outbound message created AFTER the
    # conversation it belongs to was stopped. Two tables, one join, and no
    # interpretation: the message's timestamp is later than the stop's.
    stop_violations = []
    stopped = (scope.apply(db.query(AIConversationThread.id,
                                    AIConversationThread.employee_id,
                                    AIConversationThread.stopped_at,
                                    AIConversationThread.stop_reason),
                           AIConversationThread.organization_id)
               .filter(AIConversationThread.stopped_at.isnot(None),
                       AIConversationThread.stopped_at >= since)
               .limit(2000).all())
    stopped_index = {row[0]: row for row in stopped}
    if stopped_index:
        late = (scope.apply(db.query(AICommunication.id,
                                     AICommunication.thread_id,
                                     AICommunication.employee_id,
                                     AICommunication.created_at),
                            AICommunication.organization_id)
                .filter(AICommunication.thread_id.in_(list(stopped_index)),
                        AICommunication.direction == O.OUTBOUND,
                        AICommunication.state.in_((O.SENT, O.DELIVERED)))
                .limit(2000).all())
        for comm_id, thread_id, emp_id, created in late:
            row = stopped_index.get(thread_id)
            if row is None or created is None or row[2] is None:
                continue
            if created > row[2]:
                stop_violations.append({
                    "communication_id": comm_id, "thread_id": thread_id,
                    "employee_id": emp_id,
                    "stopped_at": row[2].isoformat(),
                    "sent_at": created.isoformat(),
                    "stop_reason": row[3]})

    # OPT-OUT VIOLATIONS. Somebody said stop, and something went out after.
    opt_out_violations = []
    opt_outs = (scope.apply(db.query(AIInboundEvent.thread_id,
                                     AIInboundEvent.employee_id,
                                     func.min(AIInboundEvent.created_at)),
                            AIInboundEvent.organization_id)
                .filter(AIInboundEvent.is_opt_out.is_(True),
                        AIInboundEvent.created_at >= since,
                        AIInboundEvent.thread_id.isnot(None))
                .group_by(AIInboundEvent.thread_id,
                          AIInboundEvent.employee_id)
                .limit(2000).all())
    opt_index = {row[0]: row for row in opt_outs}
    if opt_index:
        after = (scope.apply(db.query(AICommunication.id,
                                      AICommunication.thread_id,
                                      AICommunication.employee_id,
                                      AICommunication.created_at),
                             AICommunication.organization_id)
                 .filter(AICommunication.thread_id.in_(list(opt_index)),
                         AICommunication.direction == O.OUTBOUND,
                         AICommunication.state.in_((O.SENT, O.DELIVERED)))
                 .limit(2000).all())
        for comm_id, thread_id, emp_id, created in after:
            row = opt_index.get(thread_id)
            if row is None or created is None or row[2] is None:
                continue
            if created > row[2]:
                opt_out_violations.append({
                    "communication_id": comm_id, "thread_id": thread_id,
                    "employee_id": emp_id,
                    "opted_out_at": row[2].isoformat(),
                    "sent_at": created.isoformat()})

    handoffs = (scope.apply(db.query(AIHandoff), AIHandoff.organization_id)
                .filter(AIHandoff.created_at >= since).limit(1000).all())

    bookings = {}
    bq = (db.query(AIConversationThread.employee_id, BookingLink.status,
                   BookingLink.id, AIConversationThread.id)
          .select_from(AIConversationThread)
          .join(BookingLink,
                BookingLink.id == AIConversationThread.appointment_ref)
          .join(Lead, Lead.id == BookingLink.lead_id)
          .filter(Lead.organization_id == AIConversationThread.organization_id,
                  AIConversationThread.created_at >= since))
    bq = scope.apply(bq, AIConversationThread.organization_id)
    for emp, status, booking_id, thread_id in bq.limit(2000).all():
        bookings.setdefault(emp or "", []).append(
            {"status": status, "booking_id": booking_id,
             "thread_id": thread_id})

    escalation_rows = (
        scope.apply(db.query(AIConversationThread.id,
                             AIConversationThread.employee_id,
                             AIConversationThread.state,
                             AIConversationThread.stop_reason,
                             AIConversationThread.handoff_ref),
                    AIConversationThread.organization_id)
        .filter(AIConversationThread.created_at >= since)
        .limit(3000).all())

    return {"outcomes": outcomes, "tools": tools, "sends": sends,
            "policy_violations": violations,
            "stop_violations": stop_violations,
            "opt_out_violations": opt_out_violations,
            "handoffs": handoffs, "bookings": bookings,
            "threads": escalation_rows}


# The terminal outcomes that mean the employee finished its job. `handoff` is
# here on purpose: handing a person over when a person was needed IS the
# objective completing correctly for a job whose objective is to reach a
# person. `exhausted`, `bad_contact` and `needs_review` are not failures of
# conduct either - but they are not completions, so they sit in the
# denominator and not the numerator.
_COMPLETED_OUTCOMES = (W.OUTCOME_APPOINTMENT, W.OUTCOME_QUALIFIED,
                       W.OUTCOME_HANDOFF)
# Outcomes that are a correct STOP rather than a completion, and are therefore
# excluded from the denominator entirely. Counting an opt-out as a failed
# objective would grade an employee down for obeying somebody.
_CORRECT_STOPS = (W.OUTCOME_DNC, W.OUTCOME_NOT_INTERESTED)


def _config_of(deployment) -> Dict[str, Any]:
    try:
        return json.loads(getattr(deployment, "config", None) or "{}") or {}
    except (ValueError, TypeError):
        return {}


def _dimensions_for(employee_id: str, gathered: Dict[str, Any],
                    deployment) -> Dict[str, Dimension]:
    out: Dict[str, Dimension] = {}
    config = _config_of(deployment)

    # -- objective completion ---------------------------------------------
    oc = gathered["outcomes"].get(employee_id, {})
    denominator = sum(n for outcome, n in oc.items()
                      if outcome not in _CORRECT_STOPS)
    numerator = sum(n for outcome, n in oc.items()
                    if outcome in _COMPLETED_OUTCOMES)
    if denominator:
        out[C.Q_OBJECTIVE_COMPLETION] = _measured(
            C.Q_OBJECTIVE_COMPLETION, numerator, denominator,
            "work items reaching an outcome of appointment, qualified or "
            "handoff, over all finished work items excluding opt-outs and "
            "not-interested")
    else:
        out[C.Q_OBJECTIVE_COMPLETION] = _unmeasured(
            C.Q_OBJECTIVE_COMPLETION,
            "No work has finished in this period, so there is nothing to "
            "grade. This is unmeasured, not zero.")

    # -- tool correctness --------------------------------------------------
    tools = gathered["tools"].get(employee_id, {})
    total_tools = sum(tools.values())
    if total_tools:
        out[C.Q_TOOL_CORRECTNESS] = _measured(
            C.Q_TOOL_CORRECTNESS, int(tools.get("ok", 0)), total_tools,
            "allowed tool executions with status 'ok' over all allowed tool "
            "executions; refusals are excluded because a refusal is the "
            "gateway working, not the employee failing")
    else:
        out[C.Q_TOOL_CORRECTNESS] = _unmeasured(
            C.Q_TOOL_CORRECTNESS, "This employee has taken no actions yet.")

    # -- policy compliance -------------------------------------------------
    sends = int(gathered["sends"].get(employee_id, 0))
    breaches = [v for v in gathered["policy_violations"]
                if v["employee_id"] == employee_id]
    if sends or breaches:
        out[C.Q_POLICY_COMPLIANCE] = _measured(
            C.Q_POLICY_COMPLIANCE, max(0, sends - len(breaches)),
            max(sends, len(breaches)),
            "outbound messages that reached a sent state while their recorded "
            "eligibility verdict was DENY - this should be impossible, and is "
            "checked rather than assumed",
            violations=breaches)
    else:
        out[C.Q_POLICY_COMPLIANCE] = _unmeasured(
            C.Q_POLICY_COMPLIANCE, "Nothing has been sent yet.")

    # -- stop-condition compliance ----------------------------------------
    stops = [v for v in gathered["stop_violations"]
             if v["employee_id"] == employee_id]
    if sends or stops:
        out[C.Q_STOP_COMPLIANCE] = _measured(
            C.Q_STOP_COMPLIANCE, max(0, sends - len(stops)),
            max(sends, len(stops)),
            "outbound messages created after the conversation they belong to "
            "was stopped",
            violations=stops)
    else:
        out[C.Q_STOP_COMPLIANCE] = _unmeasured(
            C.Q_STOP_COMPLIANCE, "Nothing has been sent yet.")

    # -- the person's own request -----------------------------------------
    opted = [v for v in gathered["opt_out_violations"]
             if v["employee_id"] == employee_id]
    if sends or opted:
        out[C.Q_REQUEST_RESPECTED] = _measured(
            C.Q_REQUEST_RESPECTED, max(0, sends - len(opted)),
            max(sends, len(opted)),
            "outbound messages sent after the person opted out",
            violations=opted)
    else:
        out[C.Q_REQUEST_RESPECTED] = _unmeasured(
            C.Q_REQUEST_RESPECTED, "Nothing has been sent yet.")

    # -- handoffs ----------------------------------------------------------
    mine = [h for h in gathered["handoffs"] if h.employee_id == employee_id]
    if mine:
        good = []
        bad = []
        for h in mine:
            routed = bool(h.assigned_to_user_id
                          or (h.assigned_queue or "").strip())
            briefed = bool((h.summary or "").strip())
            (good if (routed and briefed) else bad).append(h)
        out[C.Q_HANDOFF_CORRECTNESS] = _measured(
            C.Q_HANDOFF_CORRECTNESS, len(good), len(mine),
            "handoffs that named a destination AND carried a briefing, over "
            "all handoffs raised",
            violations=[{"handoff_id": h.id, "reason_code": h.reason_code,
                         "routed": bool(h.assigned_to_user_id
                                        or h.assigned_queue),
                         "has_summary": bool((h.summary or "").strip())}
                        for h in bad[:50]])
    else:
        out[C.Q_HANDOFF_CORRECTNESS] = _unmeasured(
            C.Q_HANDOFF_CORRECTNESS, "This employee has handed nothing over.")

    # -- appointments ------------------------------------------------------
    books = gathered["bookings"].get(employee_id, [])
    if books:
        seen: Dict[str, int] = {}
        for b in books:
            seen[b["booking_id"]] = seen.get(b["booking_id"], 0) + 1
        doubled = [bid for bid, n in seen.items() if n > 1]
        kept = [b for b in books if b["status"] in ("booked", "confirmed")]
        out[C.Q_APPOINTMENT_BEHAVIOUR] = _measured(
            C.Q_APPOINTMENT_BEHAVIOUR, len(kept), len(books),
            "bookings still standing over all bookings this employee "
            "produced; a booking referenced by more than one conversation is "
            "listed as an exception",
            violations=([{"booking_id": bid,
                          "why": "referenced by more than one conversation"}
                         for bid in doubled[:50]]
                        + [{"booking_id": b["booking_id"],
                            "why": "booking is %s" % b["status"]}
                           for b in books
                           if b["status"] not in ("booked", "confirmed")][:50]))
    else:
        out[C.Q_APPOINTMENT_BEHAVIOUR] = _unmeasured(
            C.Q_APPOINTMENT_BEHAVIOUR, "This employee has booked nothing.")

    # -- escalation --------------------------------------------------------
    # THE STOP REASONS THAT SHOULD ALWAYS HAVE REACHED A PERSON. Read from
    # T7's own vocabulary rather than spelled out, so a renamed reason cannot
    # quietly drop out of the check.
    must_escalate = {O.STOP_POLICY_VIOLATION, O.STOP_HUMAN_TAKEOVER,
                     O.STOP_CONTACT_REQUESTED}
    relevant = [t for t in gathered["threads"]
                if t[1] == employee_id and (t[3] or "") in must_escalate]
    if relevant:
        escalated = [t for t in relevant
                     if t[4] or t[2] in (O.HANDOFF_REQUIRED, O.HUMAN_OWNED)]
        out[C.Q_ESCALATION] = _measured(
            C.Q_ESCALATION, len(escalated), len(relevant),
            "conversations that stopped for a reason requiring a person, and "
            "actually reached one",
            violations=[{"thread_id": t[0], "stop_reason": t[3],
                         "state": t[2]}
                        for t in relevant if t not in escalated][:50])
    else:
        out[C.Q_ESCALATION] = _unmeasured(
            C.Q_ESCALATION,
            "Nothing has happened that required an escalation.")

    # -- what the customer asked it to collect -----------------------------
    required = [r for r in (config.get("required_information") or []) if r]
    if required and mine:
        complete = []
        for h in mine:
            try:
                facts = json.loads(h.known_facts or "[]")
            except (ValueError, TypeError):
                facts = []
            complete.append(len(facts) >= len(required))
        out[C.Q_REQUIRED_INFORMATION] = _measured(
            C.Q_REQUIRED_INFORMATION, sum(1 for c in complete if c),
            len(complete),
            "handoffs carrying at least as many recorded facts as this "
            "employee's configuration declares it must collect (%d)"
            % len(required))
    else:
        out[C.Q_REQUIRED_INFORMATION] = _unmeasured(
            C.Q_REQUIRED_INFORMATION,
            ("This employee's configuration does not declare information it "
             "must collect, so there is nothing to grade against."
             if not required else
             "This employee has handed nothing over yet."))

    questions = [q for q in (config.get("qualification_questions") or []) if q]
    qualified = int(oc.get(W.OUTCOME_QUALIFIED, 0))
    if questions and denominator:
        out[C.Q_QUALIFICATION_COMPLETENESS] = _measured(
            C.Q_QUALIFICATION_COMPLETENESS, qualified, denominator,
            "work items reaching the 'qualified' outcome, over finished work; "
            "this employee's configuration declares %d qualification "
            "question(s)" % len(questions))
    else:
        out[C.Q_QUALIFICATION_COMPLETENESS] = _unmeasured(
            C.Q_QUALIFICATION_COMPLETENESS,
            ("This employee's configuration declares no qualification "
             "questions." if not questions else
             "No work has finished in this period."))

    # -- factual grounding -------------------------------------------------
    #
    # HONESTLY UNMEASURED AT THE EMPLOYEE LEVEL, and saying so is the point.
    # Whether an answer was grounded in the customer's own information is a
    # property of the answer, and this platform does not store message bodies
    # in a form that could be checked against a source. T6's evaluation
    # harness DOES test grounding, against scripted cases, and the suite
    # result is carried alongside rather than folded in - a platform-level
    # pass is not evidence about this employee's conversations.
    out[C.Q_FACTUAL_GROUNDING] = _unmeasured(
        C.Q_FACTUAL_GROUNDING,
        "Grounding is tested by the platform's evaluation suite against "
        "scripted cases, not per employee against live conversations. "
        "Unmeasured here rather than assumed.")
    return out


def _score(dimensions: Dict[str, Dimension]) -> Dict[str, Any]:
    """The composite, with every component and weight shown.

    NO MYSTERY AI SCORE. The components, the weights, the arithmetic and the
    exclusions are all in the payload. A reader can reproduce this number with
    a calculator, which is the only property that makes a composite score
    worth having.
    """
    components = []
    weighted_sum = 0.0
    weight_total = 0.0
    for key, dim in dimensions.items():
        weight = WEIGHTS.get(key, 1.0)
        rate = dim.rate
        components.append({
            "dimension": key, "label": dim.label, "weight": weight,
            "rate": rate, "measured": dim.measured,
            "included": rate is not None,
            "why_excluded": None if rate is not None else dim.note,
        })
        if rate is None:
            continue
        weighted_sum += rate * weight
        weight_total += weight
    score = (round(weighted_sum / weight_total, 4) if weight_total else None)
    critical_failures = [k for k, d in dimensions.items()
                         if d.critical and d.violations]
    return {
        "score": score,
        "score_evidence": C.EV_METRIC if score is not None else C.EV_UNKNOWN,
        "explanation": SCORE_EXPLANATION,
        "components": components,
        "weight_total_used": weight_total,
        "critical_failures": critical_failures,
        "verdict": ("BLOCKED - a critical dimension has exceptions"
                    if critical_failures else
                    ("Not enough measured yet to score"
                     if score is None else "Measured")),
        "note": (None if score is not None else
                 "No dimension could be measured for this employee yet. This "
                 "is unmeasured, not a score of zero."),
    }


def evaluate(db: Session, scope: Scope, *,
             window_key: str = C.DEFAULT_WINDOW,
             now: Optional[datetime] = None) -> Dict[str, Any]:
    """Quality for every employee in scope, plus the scope's own exceptions.

    LIKE IS COMPARED WITH LIKE OR NOT AT ALL. There is no cross-employee
    quality ranking here and there deliberately is not one: a receptionist and
    a reactivation specialist do not share a scale, and section 4 says so. The
    scope-level numbers are COUNTS of exceptions, not an average of scores.
    """
    now = now or datetime.utcnow()
    since = now - timedelta(days=C.window_days(window_key))
    gathered = _gather(db, scope, since)
    employees = collect.employees(db, scope)
    deployments = collect.deployment_by_employee(db, scope)

    per_employee: Dict[str, Any] = {}
    for emp in employees:
        dims = _dimensions_for(emp.id, gathered, deployments.get(emp.id))
        per_employee[emp.id] = {
            "employee_id": emp.id,
            "name": emp.name,
            "job_role": emp.job_role,
            "dimensions": {k: d.as_dict() for k, d in dims.items()},
            "score": _score(dims),
        }

    all_violations = (gathered["policy_violations"]
                      + gathered["stop_violations"]
                      + gathered["opt_out_violations"])
    return {
        "window": window_key,
        "generated_at": now.isoformat(),
        "weights": dict(WEIGHTS),
        "score_explanation": SCORE_EXPLANATION,
        "dimension_labels": dict(C.QUALITY_LABELS),
        "critical_dimensions": sorted(C.QUALITY_CRITICAL),
        "employees": per_employee,
        "exceptions": {
            "policy": gathered["policy_violations"],
            "stop_condition": gathered["stop_violations"],
            "opt_out": gathered["opt_out_violations"],
            "total": len(all_violations),
        },
        "platform_evaluation": _platform_suite(db),
    }


def _platform_suite(db: Session) -> Dict[str, Any]:
    """The last platform evaluation run, quoted and not merged.

    T6's harness grades the ENGINE against scripted cases. That is a different
    claim from "this customer's employees behaved well", and presenting the
    two as one number would let a clean platform suite cover for a customer
    whose employees are producing exceptions. So it is carried alongside,
    labelled, and clearly about the platform.
    """
    try:
        from app.services.workforce import evaluation as wf_evaluation
        history = wf_evaluation.history(db, limit=1)
    except Exception:                                        # noqa: BLE001
        _log.debug("t9 quality: platform evaluation history unavailable")
        return {"available": False,
                "note": "The platform evaluation harness is not available."}
    if not history:
        return {"available": False,
                "note": ("The platform evaluation suite has not been run on "
                         "this deployment. Unknown, not passing.")}
    row = history[0]
    return {
        "available": True,
        "scope": "platform",
        "note": ("This grades the AdvisorFlow engine against scripted cases. "
                 "It is not evidence about any one customer's employees."),
        "suite": row.get("suite"),
        "verdict": row.get("verdict"),
        "pass_rate": row.get("pass_rate"),
        "passed": row.get("passed"),
        "failed": row.get("failed"),
        "run_at": row.get("started_at"),
    }
