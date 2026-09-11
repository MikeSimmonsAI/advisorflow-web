"""DEFENSIBLE METRICS - every number says where it came from.

THREE RULES, AND THEY ARE THE WHOLE MODULE.

1. UNKNOWN IS NOT ZERO. A rate with no denominator is not 0%; a cost with no
   provider figure is not $0.00; an appointment-completion count with no
   authoritative completion record is not "none completed". Each of those
   zeros reads as failure on a screen, and a manager acts on it. Every value
   here is either a number with a calculation attached or an explicit UNKNOWN
   with a sentence saying why.

2. NO FABRICATED REVENUE. Section 3 says it and T6's own performance ledger
   already said it: this platform does not attribute revenue to an AI
   employee. Outcomes are counted; money is reported by the systems that own
   it. `revenue` is present in every payload and is always UNKNOWN with the
   reason, because a missing key reads as an oversight and invites somebody to
   fill it in.

3. DO NOT REWARD AN EMPLOYEE FOR AN OUTCOME IT DID NOT PRODUCE. A cancelled
   objective is not a success. A failed message is not a conversation. A
   cancelled appointment is not a completed outcome. Each of those is enforced
   by the shape of the query rather than by a note asking a caller to be
   careful - the numerators here are built from the states that mean the thing
   happened, and the states that mean it was undone are counted separately.

WHERE THE NUMBERS COME FROM. T6's `ai_performance_entries` is the source for
outcome counts, for the reason T6's own module gives: a ledger that only
increments survives a queue being cleared, reassigned or re-run, and a
re-count does not. Operational counts come from T7's own rows. Nothing here
re-derives an outcome the ledger already recorded, so T9's numbers and the
customer's own screens cannot disagree.
"""

import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.ai_operations_models import AIConversationThread
from app.models.models import BookingLink, Lead
from app.services.ai_operations import constants as O
from app.services.workforce import performance as wf_performance
from app.services.workforce_intelligence import collect
from app.services.workforce_intelligence import constants as C
from app.services.workforce_intelligence.scope import Scope

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Value:
    """One number, with everything needed to argue with it.

    `evidence` is the section 5 label. `calculation` is the sentence a reader
    gets when they ask "how was that worked out" - written out rather than
    implied, because a metric whose derivation lives only in code is a metric
    nobody can check.
    """

    key: str
    label: str
    value: Optional[float]
    unit: str = "count"
    evidence: str = C.EV_METRIC
    calculation: str = ""
    numerator: Optional[int] = None
    denominator: Optional[int] = None
    note: Optional[str] = None

    @property
    def known(self) -> bool:
        return self.value is not None

    def as_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["known"] = self.known
        return out


def fact(key: str, label: str, value: int, calculation: str) -> Value:
    """A count of rows that exist. The strongest thing T9 can say."""
    return Value(key=key, label=label, value=int(value), unit="count",
                 evidence=C.EV_FACT, calculation=calculation)


def unknown(key: str, label: str, why: str, unit: str = "count") -> Value:
    """Absent, and saying so. NEVER a zero standing in for a missing answer."""
    return Value(key=key, label=label, value=None, unit=unit,
                 evidence=C.EV_UNKNOWN, calculation="", note=why)


def rate(key: str, label: str, numerator: int, denominator: int, *,
         calculation: str, minimum: int = 0) -> Value:
    """A ratio, or UNKNOWN when the denominator cannot support one.

    TWO WAYS TO BE UNKNOWN, and they are different sentences. A denominator of
    zero means nothing has happened yet. A denominator below `minimum` means
    something has happened but not enough of it to divide by - "a 50% decline"
    over four events is two events and one of them, and publishing it as a
    percentage is how a screen manufactures a crisis.
    """
    denominator = int(denominator or 0)
    numerator = int(numerator or 0)
    if denominator <= 0:
        return Value(key=key, label=label, value=None, unit="rate",
                     evidence=C.EV_UNKNOWN, calculation=calculation,
                     numerator=numerator, denominator=0,
                     note="Nothing to divide by yet. This is absent, not zero.")
    if minimum and denominator < minimum:
        return Value(key=key, label=label, value=None, unit="rate",
                     evidence=C.EV_UNKNOWN, calculation=calculation,
                     numerator=numerator, denominator=denominator,
                     note=("Too few events to state a rate (%d, minimum %d). "
                           "This is absent, not zero." % (denominator, minimum)))
    return Value(key=key, label=label,
                 value=round(float(numerator) / float(denominator), 4),
                 unit="rate", evidence=C.EV_METRIC, calculation=calculation,
                 numerator=numerator, denominator=denominator)


REVENUE_NOTE = ("AdvisorFlow does not attribute revenue to an AI employee. "
                "Outcomes are counted here; money is reported by the systems "
                "that own it. This is unknown, not zero.")


def revenue_value() -> Value:
    """Always UNKNOWN. Present on purpose so nobody adds it by accident."""
    return unknown("revenue", "Revenue attributed", REVENUE_NOTE, unit="usd")


# ---------------------------------------------------------------------------
# THE AUTHORITATIVE APPOINTMENT QUESTION
# ---------------------------------------------------------------------------
#
# WHAT THE PLATFORM ACTUALLY KNOWS ABOUT AN APPOINTMENT AN AI BOOKED.
#
# T7 books through `booking_links`, whose `status` runs pending -> booked ->
# confirmed -> expired -> cancelled, and records the booking's id on the
# conversation as `appointment_ref`. That is the whole authoritative record,
# and it is enough to say BOOKED and CANCELLED with confidence.
#
# It is NOT enough to say COMPLETED. There is no attendance on a booking link
# - no attended flag, no no-show, no completion timestamp. The brief asks for
# "appointments completed where authoritative", and here it is not, so this
# returns UNKNOWN rather than counting confirmed bookings as completed ones.
# Counting them would mean a customer whose families never turned up saw a
# perfect completion rate.


def appointment_rollup(db: Session, scope: Scope, since: datetime,
                       employee_id: Optional[str] = None
                       ) -> Dict[str, Dict[str, int]]:
    """employee_id -> {booking status: count}, for the whole scope, in one query.

    ONE STATEMENT FOR EVERY EMPLOYEE, deliberately. Asking per employee is the
    N+1 section 23 forbids, and the per-employee form of this question is
    asked by every scorecard on the page.
    """
    q = (db.query(AIConversationThread.employee_id, BookingLink.status,
                  func.count(BookingLink.id))
         .select_from(AIConversationThread)
         .join(BookingLink,
               BookingLink.id == AIConversationThread.appointment_ref)
         .join(Lead, Lead.id == BookingLink.lead_id)
         .filter(AIConversationThread.appointment_ref.isnot(None),
                 AIConversationThread.created_at >= since,
                 # THE SECOND TENANT FILTER IS NOT REDUNDANT. The thread's
                 # organization is scoped by `scope.apply` below; this asserts
                 # the BOOKING belongs to the same tenant, so a mis-set
                 # appointment_ref pointing at another customer's booking
                 # contributes to nobody's numbers instead of to the wrong
                 # ones.
                 Lead.organization_id == AIConversationThread.organization_id))
    q = scope.apply(q, AIConversationThread.organization_id)
    if employee_id:
        q = q.filter(AIConversationThread.employee_id == employee_id)
    out: Dict[str, Dict[str, int]] = {}
    for emp, status, n in q.group_by(AIConversationThread.employee_id,
                                     BookingLink.status).all():
        out.setdefault(emp or "", {})[status or "unknown"] = int(n or 0)
    return out


def appointment_facts(db: Session, scope: Scope, since: datetime,
                      employee_id: Optional[str] = None) -> Dict[str, Value]:
    """Booked, cancelled, and the one that is honestly unknown.

    ATTRIBUTION IS THROUGH THE CONVERSATION, not through the booking. A
    booking link has no employee on it; the conversation that produced it
    does, and joining that way is what stops a booking a PERSON made being
    counted as an AI employee's outcome.
    """
    rollup = appointment_rollup(db, scope, since, employee_id=employee_id)
    by_status: Dict[str, int] = {}
    for slot in rollup.values():
        for status, n in slot.items():
            by_status[status] = by_status.get(status, 0) + int(n or 0)

    live = by_status.get("booked", 0) + by_status.get("confirmed", 0)
    cancelled = by_status.get("cancelled", 0)
    expired = by_status.get("expired", 0)

    return {
        "appointments_standing": fact(
            "appointments_standing", "Appointments still standing", live,
            "booking_links.status IN ('booked','confirmed') for bookings "
            "referenced by this employee's conversations"),
        "appointments_cancelled": fact(
            "appointments_cancelled", "Appointments cancelled", cancelled,
            "booking_links.status = 'cancelled' for bookings referenced by "
            "this employee's conversations"),
        "appointments_expired": fact(
            "appointments_expired", "Appointment links that expired", expired,
            "booking_links.status = 'expired' for bookings referenced by "
            "this employee's conversations"),
        "appointments_completed": unknown(
            "appointments_completed", "Appointments completed",
            "AdvisorFlow records that an appointment was booked and whether "
            "it was cancelled. Whether the person attended is not recorded on "
            "a booking, so this is unknown rather than zero."),
    }


# ---------------------------------------------------------------------------
# THE METRIC SET
# ---------------------------------------------------------------------------


def employee_metrics(db: Session, scope: Scope, *,
                     window_key: str = C.DEFAULT_WINDOW,
                     now: Optional[datetime] = None,
                     thresholds: Optional[Dict] = None) -> Dict[str, Dict]:
    """Every metric, for every employee in scope, in a bounded number of queries.

    Seven grouped statements and one per-employee dictionary walk, whatever
    the employee count. Section 23's N+1 prohibition is satisfied by
    construction rather than by a caching layer papering over a loop.
    """
    now = now or datetime.utcnow()
    thresholds = thresholds or C.thresholds()
    since = now - timedelta(days=C.window_days(window_key))
    since_day = since.strftime("%Y-%m-%d")
    minimum = int(thresholds["minimum_denominator"])

    ledger = collect.performance_rollup(db, scope, since_day)
    runs = collect.run_rollup(db, scope, since)
    tools = collect.tool_rollup(db, scope, since)
    ops = collect.ops_action_rollup(db, scope, since)
    comms = collect.communication_rollup(db, scope, since)
    handoffs = collect.handoff_rollup(db, scope)
    work = collect.work_counts_by_employee_state(db, scope)
    threads = collect.thread_counts_by_employee_state(db, scope)
    appointments = appointment_rollup(db, scope, since)

    employee_ids = set(ledger) | set(runs) | set(tools) | set(ops) \
        | set(comms) | set(handoffs) | set(appointments)
    employee_ids |= {emp for emp, _state in work}
    employee_ids |= {emp for emp, _state in threads}
    employee_ids.discard("")

    out: Dict[str, Dict] = {}
    for emp_id in employee_ids:
        out[emp_id] = _one_employee(
            emp_id, minimum=minimum,
            ledger=ledger.get(emp_id, {}), runs=runs.get(emp_id, {}),
            tools=tools.get(emp_id, {}), ops=ops.get(emp_id, {}),
            comms=comms.get(emp_id, {}), handoffs=handoffs.get(emp_id, {}),
            appointments=appointments.get(emp_id, {}),
            work={state: n for (e, state), n in work.items() if e == emp_id},
            threads={state: n for (e, state), n in threads.items()
                     if e == emp_id},
            window_key=window_key)
    return out


# The provider verdicts that mean a message actually left the building. A
# FAILED, REJECTED or TIMED-OUT send is not a conversation, and section 18
# asks for that to be proven rather than promised - so the numerator is built
# from this set rather than from a row count.
_DELIVERED_OUTCOMES = (O.P_ACCEPTED, O.P_DELIVERED)

# The T7 states that mean a person actually replied. `response_received` and
# `processing` are the two; `delivered` is the provider talking, not the
# person, and counting it as a response is how a response rate becomes a
# delivery rate wearing the wrong label.
_RESPONDED_STATES = (O.RESPONSE_RECEIVED, O.PROCESSING)


def _one_employee(employee_id: str, *, minimum: int, ledger: Dict,
                  runs: Dict, tools: Dict, ops: Dict, comms: Dict,
                  handoffs: Dict, appointments: Dict, work: Dict,
                  threads: Dict, window_key: str) -> Dict[str, Any]:
    """Every metric for one employee, from rollups already in memory.

    NO DATABASE ACCESS IN HERE, on purpose. Everything this needs was fetched
    in grouped form by the caller, so adding a metric can never accidentally
    add a query per employee.
    """
    led = lambda k: int(ledger.get(k, 0) or 0)            # noqa: E731

    messages_sent = led("messages_sent")
    delivered = sum(int(n or 0)
                    for outcome, n in (comms.get("by_provider_outcome") or {}).items()
                    if outcome in _DELIVERED_OUTCOMES)
    outbound = int(comms.get("outbound", 0) or 0)
    inbound = int(comms.get("inbound", 0) or 0)
    responded_threads = sum(int(threads.get(s, 0) or 0)
                            for s in _RESPONDED_STATES)

    assigned = led("records_assigned")
    eligible = led("records_eligible")
    appointments_booked = led("appointments")
    qualified = led("qualified")
    handed_off = led("handoffs")
    opt_outs = led("opt_outs")

    appt_live = (int(appointments.get("booked", 0) or 0)
                 + int(appointments.get("confirmed", 0) or 0))
    appt_cancelled = int(appointments.get("cancelled", 0) or 0)

    # A CANCELLED APPOINTMENT IS NOT A COMPLETED OUTCOME. The ledger counted
    # the booking when it happened and that count is correct - the booking DID
    # happen. What must not happen is treating it as an outcome that still
    # stands, so the "standing" figure is a separate fact and the rate below
    # uses it rather than the booking count.
    values: Dict[str, Value] = {
        "records_assigned": fact("records_assigned", "Records assigned",
                                 assigned,
                                 "ai_performance_entries.records_assigned"),
        "records_eligible": fact("records_eligible", "Records eligible",
                                 eligible,
                                 "ai_performance_entries.records_eligible"),
        "records_denied": fact("records_denied", "Records not contactable",
                               led("records_denied"),
                               "ai_performance_entries.records_denied"),
        "records_review": fact("records_review", "Records sent for review",
                               led("records_review"),
                               "ai_performance_entries.records_review"),
        "messages_sent": fact("messages_sent", "Messages sent", messages_sent,
                              "ai_performance_entries.messages_sent"),
        "messages_delivered": fact(
            "messages_delivered", "Messages the provider accepted", delivered,
            "ai_communications with provider_outcome IN ('accepted',"
            "'delivered')"),
        "outbound_communications": fact(
            "outbound_communications", "Outbound messages attempted", outbound,
            "ai_communications.direction = 'outbound'"),
        "inbound_communications": fact(
            "inbound_communications", "Inbound messages received", inbound,
            "ai_communications.direction = 'inbound'"),
        "responses": fact("responses", "Responses received", led("responses"),
                          "ai_performance_entries.responses"),
        "conversations_responded": fact(
            "conversations_responded", "Conversations that got a reply",
            responded_threads,
            "open ai_conversation_threads in state response_received or "
            "processing"),
        "qualified": fact("qualified", "Qualified", qualified,
                          "ai_performance_entries.qualified"),
        "appointments_booked": fact(
            "appointments_booked", "Appointments booked", appointments_booked,
            "ai_performance_entries.appointments"),
        "appointments_standing": fact(
            "appointments_standing", "Appointments still standing", appt_live,
            "booking_links.status IN ('booked','confirmed') joined through "
            "ai_conversation_threads.appointment_ref"),
        "appointments_cancelled": fact(
            "appointments_cancelled", "Appointments cancelled", appt_cancelled,
            "booking_links.status = 'cancelled' joined through "
            "ai_conversation_threads.appointment_ref"),
        "appointments_completed": unknown(
            "appointments_completed", "Appointments completed",
            "AdvisorFlow records that an appointment was booked and whether it "
            "was cancelled. Attendance is not recorded on a booking, so this "
            "is unknown rather than zero."),
        "handoffs": fact("handoffs", "Handed to a person", handed_off,
                         "ai_performance_entries.handoffs"),
        "handoffs_open": fact("handoffs_open", "Handoffs still open",
                              int(handoffs.get("open", 0) or 0),
                              "ai_handoffs.status = 'open'"),
        "handoffs_accepted": fact("handoffs_accepted", "Handoffs accepted",
                                  int(handoffs.get("accepted", 0) or 0),
                                  "ai_handoffs.status = 'accepted'"),
        "opt_outs": fact("opt_outs", "Opt-outs recorded", opt_outs,
                         "ai_performance_entries.opt_outs"),
        "policy_denials": fact(
            "policy_denials", "Refused by policy",
            int(ops.get("denied", 0) or 0) + int(tools.get("denied", 0) or 0),
            "ai_ops_actions.decision = 'denied' plus "
            "ai_tool_executions.decision = 'denied'"),
        "tool_failures": fact("tool_failures", "Tool failures",
                              int(tools.get("errors", 0) or 0),
                              "ai_tool_executions.status = 'error'"),
        "provider_failures": fact(
            "provider_failures", "Provider failures",
            sum(int(n or 0)
                for outcome, n in (comms.get("by_provider_outcome") or {}).items()
                if outcome in (O.P_FAILED, O.P_TIMEOUT, O.P_REJECTED)),
            "ai_communications with provider_outcome IN ('failed','timeout',"
            "'rejected')"),
        "runs": fact("runs", "Runs", int(runs.get("total", 0) or 0),
                     "ai_employee_runs"),
        "runs_aborted": fact(
            "runs_aborted", "Runs stopped by a limit",
            int((runs.get("by_status") or {}).get("aborted", 0) or 0),
            "ai_employee_runs.status = 'aborted'"),
        "human_interventions": fact(
            "human_interventions", "Times a person stepped in",
            int(threads.get(O.HUMAN_OWNED, 0) or 0)
            + int(handoffs.get("accepted", 0) or 0),
            "conversations in human_owned plus accepted handoffs"),
        "revenue": revenue_value(),
    }

    values["response_rate"] = rate(
        "response_rate", "Response rate", led("responses"), delivered,
        calculation="responses / messages the provider accepted",
        minimum=minimum)
    values["delivery_rate"] = rate(
        "delivery_rate", "Delivery rate", delivered, outbound,
        calculation="messages the provider accepted / outbound attempts",
        minimum=minimum)
    values["appointment_rate"] = rate(
        "appointment_rate", "Appointment rate", appointments_booked, eligible,
        calculation="appointments booked / records eligible", minimum=minimum)
    values["qualification_rate"] = rate(
        "qualification_rate", "Qualification rate", qualified, eligible,
        calculation="qualified / records eligible", minimum=minimum)
    values["handoff_rate"] = rate(
        "handoff_rate", "Handoff rate", handed_off, assigned,
        calculation="handed to a person / records assigned", minimum=minimum)
    values["opt_out_rate"] = rate(
        "opt_out_rate", "Opt-out rate", opt_outs, delivered,
        calculation="opt-outs / messages the provider accepted",
        minimum=minimum)
    values["eligibility_rate"] = rate(
        "eligibility_rate", "Eligibility rate", eligible, assigned,
        calculation="records eligible / records assigned", minimum=minimum)
    values["appointment_cancellation_rate"] = rate(
        "appointment_cancellation_rate", "Appointments later cancelled",
        appt_cancelled, appt_cancelled + appt_live,
        calculation="cancelled bookings / all bookings this employee produced",
        minimum=minimum)

    return {
        "employee_id": employee_id,
        "window": window_key,
        "values": {k: v.as_dict() for k, v in values.items()},
        "work_by_state": work,
        "threads_by_state": threads,
        "denials": dict(ops.get("denials") or {}),
        "tool_denials": dict(tools.get("denials") or {}),
    }


# The metrics that are COUNTS and may be added up across employees. A key that
# is not here is either a rate (which must be recomputed, never averaged) or an
# UNKNOWN (which stays unknown however many employees share it).
SUMMABLE = (
    "records_assigned", "records_eligible", "records_denied", "records_review",
    "messages_sent", "messages_delivered", "outbound_communications",
    "inbound_communications", "responses", "conversations_responded",
    "qualified", "appointments_booked", "appointments_standing",
    "appointments_cancelled", "handoffs", "handoffs_open", "handoffs_accepted",
    "opt_outs", "policy_denials", "tool_failures", "provider_failures",
    "runs", "runs_aborted", "human_interventions",
)

# (rate key, numerator key, denominator key, label, calculation)
DERIVED_RATES = (
    ("response_rate", "responses", "messages_delivered", "Response rate",
     "responses / messages the provider accepted"),
    ("delivery_rate", "messages_delivered", "outbound_communications",
     "Delivery rate", "messages the provider accepted / outbound attempts"),
    ("appointment_rate", "appointments_booked", "records_eligible",
     "Appointment rate", "appointments booked / records eligible"),
    ("qualification_rate", "qualified", "records_eligible",
     "Qualification rate", "qualified / records eligible"),
    ("handoff_rate", "handoffs", "records_assigned", "Handoff rate",
     "handed to a person / records assigned"),
    ("opt_out_rate", "opt_outs", "messages_delivered", "Opt-out rate",
     "opt-outs / messages the provider accepted"),
    ("eligibility_rate", "records_eligible", "records_assigned",
     "Eligibility rate", "records eligible / records assigned"),
)


def totals(per_employee: Dict[str, Dict], *, minimum: int = 0,
           window_key: str = C.DEFAULT_WINDOW) -> Dict[str, Any]:
    """Roll per-employee metrics up to the scope.

    RATES ARE RECOMPUTED, NEVER AVERAGED. The mean of two response rates is
    not the response rate: an employee that sent four messages and one that
    sent four thousand would count equally, and the headline would move
    because somebody switched on a small new employee. Summing the numerators
    and the denominators and dividing once is the only version that means
    anything, and it is the version a customer can check by hand.

    UNKNOWN SURVIVES THE ROLL-UP. A metric that is unknown for every employee
    is unknown for the scope, not zero.
    """
    sums = {key: 0 for key in SUMMABLE}
    known_any = {key: False for key in SUMMABLE}
    for slot in per_employee.values():
        for key in SUMMABLE:
            v = (slot.get("values") or {}).get(key) or {}
            if v.get("value") is None:
                continue
            known_any[key] = True
            sums[key] += int(v["value"])

    values: Dict[str, Dict] = {}
    for key in SUMMABLE:
        label = None
        for slot in per_employee.values():
            label = ((slot.get("values") or {}).get(key) or {}).get("label")
            if label:
                break
        if not known_any[key] and not per_employee:
            values[key] = unknown(
                key, label or key,
                "No AI employees in this workspace have produced this yet."
            ).as_dict()
            continue
        values[key] = fact(key, label or key, sums[key],
                           "sum across every AI employee in scope").as_dict()

    for rate_key, num_key, den_key, label, calc in DERIVED_RATES:
        values[rate_key] = rate(rate_key, label, sums.get(num_key, 0),
                                sums.get(den_key, 0), calculation=calc,
                                minimum=minimum).as_dict()

    values["appointment_cancellation_rate"] = rate(
        "appointment_cancellation_rate", "Appointments later cancelled",
        sums.get("appointments_cancelled", 0),
        sums.get("appointments_cancelled", 0)
        + sums.get("appointments_standing", 0),
        calculation="cancelled bookings / all bookings AI employees produced",
        minimum=minimum).as_dict()
    values["appointments_completed"] = unknown(
        "appointments_completed", "Appointments completed",
        "Attendance is not recorded on a booking. Unknown, not zero."
    ).as_dict()
    values["revenue"] = revenue_value().as_dict()

    return {
        "window": window_key,
        "employee_count": len(per_employee),
        "values": values,
        "summable_keys": list(SUMMABLE),
    }


def ledger_vocabulary() -> Dict[str, str]:
    """T6's own metric labels, so T9 renders one vocabulary and not a second.

    Imported rather than copied for the reason the constants module gives: a
    second spelling of a metric name is a second definition of the metric.
    """
    return dict(wf_performance.METRICS)
