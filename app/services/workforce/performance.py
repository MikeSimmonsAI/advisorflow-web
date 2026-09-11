"""THE PERFORMANCE LEDGER — what actually happened, and nothing invented.

A DAY-GRAINED COUNTER RATHER THAN A QUERY OVER WORK ITEMS. Counting
appointments by re-querying the queue works until a work item is deleted,
reassigned, re-run or re-enqueued — at which point last month's number quietly
changes and nobody can say why. A ledger that only increments is the version an
operator can trust, and it is the only version that survives a customer
clearing their queue.

NO REVENUE. NO ROI. NO ATTRIBUTION.

Section 27 is explicit: "Do not fake ROI. Do not invent revenue attribution
without authoritative opportunity/payment data." This engine does not own
opportunity value and does not own payments, so it counts OUTCOMES and stops
there. An appointment booked is a fact. "£4,200 of pipeline generated" would be
a number this module made up, and a made-up number on a customer's dashboard is
worse than an empty one — they will act on it.

RATES ARE COMPUTED AT READ TIME from the counters, never stored. A stored rate
is a number that has to be recomputed every time either side of it changes, and
the version that is never recomputed is the one somebody screenshots.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.workforce_models import (AIEmployee, AIPerformanceEntry,
                                         AIWorkItem)
from app.services.workforce import constants as C

_log = logging.getLogger(__name__)

# THE VOCABULARY. Every metric an employee can produce, with the words a
# customer reads. A key that is not here is not counted — which stops the
# ledger filling with one-off strings nobody can chart.
METRICS = {
    "records_assigned": "Records assigned",
    "records_eligible": "Records eligible",
    "records_denied": "Records not contactable",
    "records_review": "Records sent for review",
    "attempts": "Contact attempts",
    "messages_sent": "Messages sent",
    "responses": "Responses received",
    "qualified": "Qualified",
    "appointments": "Appointments booked",
    "handoffs": "Handed to a person",
    "not_interested": "Not interested",
    "opt_outs": "Opt-outs recorded",
    "bad_contact": "Bad contact details",
    "exhausted": "Exhausted",
    "tool_failures": "Tool failures",
    "provider_failures": "Provider failures",
    "policy_blocks": "Blocked by policy",
    "runs": "Runs",
    "runs_aborted": "Runs stopped by a limit",
}

ALL_METRIC_KEYS = tuple(sorted(METRICS))

# Which outcome increments which counter when a work item reaches a terminal
# state. One table rather than a branch per call site.
OUTCOME_METRIC = {
    C.OUTCOME_APPOINTMENT: "appointments",
    C.OUTCOME_QUALIFIED: "qualified",
    C.OUTCOME_HANDOFF: "handoffs",
    C.OUTCOME_NOT_INTERESTED: "not_interested",
    C.OUTCOME_DNC: "opt_outs",
    C.OUTCOME_BAD_CONTACT: "bad_contact",
    C.OUTCOME_EXHAUSTED: "exhausted",
    C.OUTCOME_NEEDS_REVIEW: "records_review",
}


def _today(now: Optional[datetime] = None) -> str:
    return (now or datetime.utcnow()).strftime("%Y-%m-%d")


def bump(db: Session, employee: AIEmployee, metric_key: str, *, value: int = 1,
         now: Optional[datetime] = None) -> None:
    """Increment one counter for one day. Never raises.

    A LEDGER WRITE MUST NOT BE ABLE TO FAIL AN OPERATION. The action it counts
    has already happened; losing the count is a reporting gap, and refusing the
    action over it would be a far worse trade. Both the constraint race and the
    unexpected-error path are swallowed with a log line.
    """
    if metric_key not in METRICS:
        _log.warning("workforce performance: unknown metric %r ignored",
                     metric_key)
        return
    day = _today(now)
    try:
        row = (db.query(AIPerformanceEntry)
               .filter(AIPerformanceEntry.organization_id == employee.organization_id,
                       AIPerformanceEntry.employee_id == employee.id,
                       AIPerformanceEntry.metric_date == day,
                       AIPerformanceEntry.metric_key == metric_key)
               .first())
        if row is None:
            row = AIPerformanceEntry(
                organization_id=employee.organization_id,
                employee_id=employee.id, metric_date=day,
                metric_key=metric_key, value=0)
            db.add(row)
            try:
                with db.begin_nested():
                    db.flush()
            except IntegrityError:
                # Another worker created it first. Re-read and carry on — this
                # is the normal outcome under concurrency, not an error.
                db.rollback()
                row = (db.query(AIPerformanceEntry)
                       .filter(AIPerformanceEntry.organization_id == employee.organization_id,
                               AIPerformanceEntry.employee_id == employee.id,
                               AIPerformanceEntry.metric_date == day,
                               AIPerformanceEntry.metric_key == metric_key)
                       .first())
                if row is None:
                    return
        row.value = int(row.value or 0) + int(value)
        row.updated_at = datetime.utcnow()
        db.flush()
    except Exception:                                        # noqa: BLE001
        _log.exception("workforce performance: could not record %s", metric_key)


def bump_outcome(db: Session, employee: AIEmployee, outcome: Optional[str],
                 now: Optional[datetime] = None) -> None:
    key = OUTCOME_METRIC.get(outcome or "")
    if key:
        bump(db, employee, key, now=now)


def totals(db: Session, *, organization_id: str,
           employee_id: Optional[str] = None, days: int = 30,
           now: Optional[datetime] = None) -> Dict[str, int]:
    since = ((now or datetime.utcnow()) - timedelta(days=max(1, int(days)))
             ).strftime("%Y-%m-%d")
    q = (db.query(AIPerformanceEntry.metric_key,
                  func.sum(AIPerformanceEntry.value))
         .filter(AIPerformanceEntry.organization_id == organization_id,
                 AIPerformanceEntry.metric_date >= since))
    if employee_id:
        q = q.filter(AIPerformanceEntry.employee_id == employee_id)
    out = {k: 0 for k in ALL_METRIC_KEYS}
    for key, total in q.group_by(AIPerformanceEntry.metric_key).all():
        out[key] = int(total or 0)
    return out


def daily_series(db: Session, *, organization_id: str,
                 employee_id: Optional[str] = None,
                 metric_key: str = "appointments", days: int = 30,
                 now: Optional[datetime] = None) -> List[Dict]:
    since = ((now or datetime.utcnow()) - timedelta(days=max(1, int(days)))
             ).strftime("%Y-%m-%d")
    q = (db.query(AIPerformanceEntry.metric_date,
                  func.sum(AIPerformanceEntry.value))
         .filter(AIPerformanceEntry.organization_id == organization_id,
                 AIPerformanceEntry.metric_key == metric_key,
                 AIPerformanceEntry.metric_date >= since))
    if employee_id:
        q = q.filter(AIPerformanceEntry.employee_id == employee_id)
    rows = q.group_by(AIPerformanceEntry.metric_date).all()
    return [{"date": d, "value": int(v or 0)}
            for d, v in sorted(rows, key=lambda r: r[0])]


def _rate(numerator: int, denominator: int) -> Optional[float]:
    """A rate with no denominator is NOT zero. It is unknown.

    Reporting 0% response rate for an employee that has sent nothing is a
    number that reads as failure. None reads as "nothing to say yet", which is
    the truth.
    """
    if not denominator:
        return None
    return round(float(numerator) / float(denominator), 4)


def report(db: Session, *, organization_id: str,
           employee_id: Optional[str] = None, days: int = 30,
           now: Optional[datetime] = None) -> Dict:
    """The Performance panel. Counts, derived rates, and durations.

    `average_touches` and the two time-to-X figures are read from the WORK
    ITEMS rather than the ledger, because they are properties of a record's
    life rather than events to count.
    """
    counts = totals(db, organization_id=organization_id,
                    employee_id=employee_id, days=days, now=now)

    q = db.query(AIWorkItem).filter(
        AIWorkItem.organization_id == organization_id)
    if employee_id:
        q = q.filter(AIWorkItem.employee_id == employee_id)

    touch_avg = (db.query(func.avg(AIWorkItem.touches))
                 .filter(AIWorkItem.organization_id == organization_id,
                         *( [AIWorkItem.employee_id == employee_id]
                            if employee_id else []),
                         AIWorkItem.touches > 0).scalar())

    terminal = q.filter(AIWorkItem.terminal_at.isnot(None)).count()
    open_items = q.filter(~AIWorkItem.state.in_(list(C.TERMINAL_STATES))).count()

    return {
        "window_days": int(days),
        "counts": counts,
        "labels": dict(METRICS),
        "rates": {
            "response_rate": _rate(counts["responses"], counts["messages_sent"]),
            "appointment_rate": _rate(counts["appointments"],
                                      counts["records_eligible"]),
            "qualification_rate": _rate(counts["qualified"],
                                        counts["records_eligible"]),
            "handoff_rate": _rate(counts["handoffs"], counts["records_assigned"]),
            "opt_out_rate": _rate(counts["opt_outs"], counts["messages_sent"]),
            "eligibility_rate": _rate(counts["records_eligible"],
                                      counts["records_assigned"]),
        },
        "average_touches": round(float(touch_avg), 2) if touch_avg else None,
        "work_items_closed": terminal,
        "work_items_open": open_items,
        # Named so nobody has to wonder whether a money figure is missing by
        # accident. It is missing on purpose.
        "revenue": None,
        "revenue_note": ("AdvisorFlow does not attribute revenue to an AI "
                         "employee. Outcomes are counted; money is reported by "
                         "the systems that own it."),
    }


def leaderboard(db: Session, organization_id: str, *, days: int = 30
                ) -> List[Dict]:
    """Per-employee summary for the team screen."""
    employees = (db.query(AIEmployee)
                 .filter(AIEmployee.organization_id == organization_id).all())
    out = []
    for emp in employees:
        counts = totals(db, organization_id=organization_id,
                        employee_id=emp.id, days=days)
        out.append({
            "employee_id": emp.id, "name": emp.name, "job_role": emp.job_role,
            "status": emp.status, "activation_state": emp.activation_state,
            "appointments": counts["appointments"],
            "qualified": counts["qualified"],
            "handoffs": counts["handoffs"],
            "messages_sent": counts["messages_sent"],
            "responses": counts["responses"],
        })
    return sorted(out, key=lambda d: (-d["appointments"], d["name"]))
