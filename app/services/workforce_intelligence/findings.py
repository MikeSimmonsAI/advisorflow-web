"""SUPERVISOR INTELLIGENCE - where T9 stops reporting and starts saying things.

THE LINE THIS MODULE WALKS. Section 5 asks for findings like "appointment
conversion has declined relative to this employee's baseline" - which is an
interpretation, not a row. It also says, twice, that the model may EXPLAIN
evidence and may never CREATE it, and that interpretation must never be
presented as database fact.

So a finding here is built from four separated parts, and they are separate in
the schema rather than separate by convention:

    FACTS           rows that exist, each with the table and the count
    METRICS         numbers computed from those rows, each with its formula
    INTERPRETATION  the sentence that reads the numbers, LABELLED as a reading
    UNKNOWNS        what would change the conclusion and is not knowable here

A renderer shows the labels. A reader who disagrees with the interpretation
still has the facts, and can reach the rows.

NO MODEL RUNS IN THIS FILE. Every interpretation is generated from a template
filled with computed numbers, which makes it deterministic, testable and
reproducible - the same inputs produce the same sentence, in a unit test as in
production. That is a deliberate choice over generated prose: a finding that
reads slightly differently each time it is computed is a finding nobody can
diff, and section 18 requires that prompt injection cannot change a score or
remove an incident. Text that is assembled rather than generated cannot be
talked out of its own numbers.

CONFIDENCE IS ABOUT THE EVIDENCE, NOT ABOUT THE MODEL. `high` means the
denominators are large enough that the comparison is not noise; `low` means it
is worth looking at and could easily be nothing. It is derived from sample
size, deterministically, and is absent where there is nothing to be confident
about.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.models import Organization
from app.models.workforce_intelligence_models import AISupervisorFinding
from app.services.workforce import constants as W
from app.services.workforce import performance as wf_performance
from app.services.workforce_intelligence import collect
from app.services.workforce_intelligence import constants as C
from app.services.workforce_intelligence import scorecards as t9_scorecards
from app.services.workforce_intelligence.scope import Scope

_log = logging.getLogger(__name__)

CONF_HIGH = "high"
CONF_MEDIUM = "medium"
CONF_LOW = "low"


@dataclass
class Finding:
    code: str
    organization_id: str
    headline: str
    severity: str = C.SEV_INFO
    employee_id: Optional[str] = None
    facts: List[Dict[str, Any]] = field(default_factory=list)
    metrics: List[Dict[str, Any]] = field(default_factory=list)
    interpretation: Optional[str] = None
    unknowns: List[str] = field(default_factory=list)
    confidence: Optional[str] = None
    why_it_matters: Optional[str] = None
    recommended_action: Optional[str] = None
    affected_scope: Optional[str] = None
    drilldown: Dict[str, Any] = field(default_factory=dict)
    finding_key: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "headline": self.headline,
            "severity": self.severity,
            "employee_id": self.employee_id,
            "organization_id": self.organization_id,
            # THE FOUR CLASSES, EACH LABELLED, EVERY TIME.
            "fact": {"class": C.EV_FACT, "items": self.facts},
            "metric": {"class": C.EV_METRIC, "items": self.metrics},
            "interpretation": {"class": C.EV_INTERPRETATION,
                               "text": self.interpretation,
                               "note": ("This is a reading of the numbers "
                                        "above, not a record in the "
                                        "database.")},
            "unknown": {"class": C.EV_UNKNOWN, "items": self.unknowns},
            "recommendation": {"class": C.EV_RECOMMENDATION,
                               "text": self.recommended_action,
                               "note": ("A suggestion for a person. Nothing "
                                        "acts on this automatically.")},
            "confidence": self.confidence,
            "why_it_matters": self.why_it_matters,
            "affected_scope": self.affected_scope,
            "drilldown": self.drilldown,
        }


def _fact(statement: str, table: str, count=None, **extra) -> Dict[str, Any]:
    out = {"statement": statement, "source_table": table, "count": count}
    out.update(extra)
    return out


def _metric(key: str, value, calculation: str, **extra) -> Dict[str, Any]:
    out = {"key": key, "value": value, "calculation": calculation}
    out.update(extra)
    return out


def _confidence(denominator: int, minimum: int) -> str:
    """How much weight the numbers can carry. Derived, never asserted."""
    if denominator >= minimum * 10:
        return CONF_HIGH
    if denominator >= minimum * 3:
        return CONF_MEDIUM
    return CONF_LOW


# ---------------------------------------------------------------------------
# THE GENERATORS
# ---------------------------------------------------------------------------


def generate(db: Session, scope: Scope, *,
             window_key: str = C.DEFAULT_WINDOW,
             thresholds: Optional[Dict] = None,
             now: Optional[datetime] = None) -> List[Finding]:
    """Every finding for one scope. One generator failing does not stop the rest."""
    now = now or datetime.utcnow()
    th = thresholds or C.thresholds()
    cards = t9_scorecards.build(db, scope, window_key=window_key, now=now,
                                thresholds=th)
    employees = {e.id: e for e in collect.employees(db, scope)}
    out: List[Finding] = []
    for gen in _GENERATORS:
        try:
            out.extend(gen(db, scope, cards, employees, th, now, window_key))
        except Exception:                                    # noqa: BLE001
            _log.exception("t9 findings: generator %s failed",
                           getattr(gen, "__name__", "?"))
    for f in out:
        if not f.finding_key:
            f.finding_key = "%s:%s" % (f.code, f.employee_id or "scope")
    return out


def _eligible_backlog(db, scope, cards, employees, th, now, window_key):
    """Work that is ready to go and is not going.

    THE MOST USEFUL FINDING ON THE LIST, and the least clever. "427 eligible
    reactivation records are waiting" is a number a manager can act on in a
    minute, and it is invisible on a dashboard of rates.
    """
    counts = collect.work_counts_by_employee_state(db, scope)
    by_employee: Dict[str, int] = {}
    for (emp_id, state), n in counts.items():
        if state in (W.ELIGIBLE, W.ASSIGNED, W.ELIGIBILITY_PENDING):
            by_employee[emp_id] = by_employee.get(emp_id, 0) + int(n)
    out = []
    for emp_id, waiting in by_employee.items():
        if waiting < max(25, int(th["minimum_denominator"]) * 2):
            continue
        emp = employees.get(emp_id)
        if emp is None:
            continue
        live = (emp.activation_state or "") not in ("off", "")
        out.append(Finding(
            code=C.F_ELIGIBLE_BACKLOG, organization_id=str(emp.organization_id),
            employee_id=emp_id,
            headline="%d record%s are waiting for %s"
                     % (waiting, "" if waiting == 1 else "s", emp.name),
            severity=C.SEV_HIGH if waiting > 200 else C.SEV_NORMAL,
            facts=[_fact("%d work items are assigned, pending eligibility or "
                         "eligible and none of them has been worked yet."
                         % waiting, "ai_work_items", waiting),
                   _fact("This employee's activation stage is '%s'."
                         % (emp.activation_state or "off"), "ai_employees")],
            metrics=[_metric("records_waiting", waiting,
                             "count of work items in assigned, "
                             "eligibility_pending or eligible")],
            interpretation=(
                "There is a queue and nothing is draining it."
                if live else
                "The queue exists but this employee is switched off, so "
                "nothing will happen to it until somebody starts it."),
            unknowns=["Whether these records should be worked at all is a "
                      "business decision this platform does not hold."],
            confidence=CONF_HIGH,
            why_it_matters=("Records sitting in a queue are the work this "
                            "employee was hired to do."),
            recommended_action=("Start this employee." if not live else
                                "Check whether its channels and entitlement "
                                "still permit it to work."),
            affected_scope="%d records" % waiting,
            drilldown={"view": "work", "employee_id": emp_id}))
    return out


def _review_backlog(db, scope, cards, employees, th, now, window_key):
    counts = collect.thread_counts_by_employee_state(db, scope)
    waiting = sum(int(n) for (emp, state), n in counts.items()
                  if state == C.THREAD_REVIEW)
    if waiting < 1:
        return []
    org_ids = collect_org_ids(scope)
    return [Finding(
        code=C.F_REVIEW_BACKLOG, organization_id=org_ids,
        headline="%d conversation%s require human review"
                 % (waiting, "" if waiting == 1 else "s"),
        severity=C.SEV_HIGH if waiting > 5 else C.SEV_NORMAL,
        facts=[_fact("%d open conversations are in state 'review_required'."
                     % waiting, "ai_conversation_threads", waiting)],
        metrics=[_metric("conversations_awaiting_review", waiting,
                         "count of open threads in review_required")],
        interpretation=("These will not move without a person, whatever else "
                        "is configured."),
        unknowns=[],
        confidence=CONF_HIGH,
        why_it_matters="Somebody is waiting on the other end of each of these.",
        recommended_action="Open the review queue and clear them.",
        affected_scope="%d conversations" % waiting,
        drilldown={"view": "review"})]


def collect_org_ids(scope: Scope) -> str:
    """The organization a scope-level finding belongs to.

    A finding needs an organization because `ai_supervisor_findings` is
    tenant-scoped, and it is tenant-scoped because a finding that belonged to
    no tenant would be readable by everyone. For a single-organization scope
    that is the organization; for a brand or platform scope there is no single
    answer, which is why brand-level passes iterate organizations rather than
    asking once.
    """
    if scope.organization_ids and len(scope.organization_ids) == 1:
        return scope.organization_ids[0]
    return ""


def _conversion_decline(db, scope, cards, employees, th, now, window_key):
    """An employee doing worse than it used to, stated with both numbers."""
    delta_threshold = float(th["baseline_delta_fraction"])
    minimum = int(th["minimum_denominator"])
    out = []
    for card in cards["cards"]:
        trend = (card.get("trends") or {}).get("appointments") or {}
        current, previous = int(trend.get("current", 0)), int(
            trend.get("previous", 0))
        if previous < minimum:
            continue
        fraction = trend.get("change_fraction")
        if fraction is None or fraction > -delta_threshold:
            continue
        emp = employees.get(card["employee_id"])
        out.append(Finding(
            code=C.F_CONVERSION_DECLINE,
            organization_id=str(getattr(emp, "organization_id", "") or ""),
            employee_id=card["employee_id"],
            headline="%s booked fewer appointments than last period"
                     % card["name"],
            severity=C.SEV_NORMAL,
            facts=[_fact("%d appointments this period against %d in the "
                         "previous period of the same length."
                         % (current, previous), "ai_performance_entries",
                         current, previous=previous),
                   _fact("%d messages were sent this period."
                         % int((card.get("trends") or {})
                               .get("messages_sent", {}).get("current", 0)),
                         "ai_performance_entries")],
            metrics=[_metric("appointments_change_fraction", fraction,
                             "(this period - previous period) / previous "
                             "period, from the day-grained performance "
                             "ledger"),
                     _metric("baseline_daily", trend.get("baseline_daily"),
                             "mean appointments per day over the four "
                             "periods before the previous one")],
            interpretation=("Fewer appointments, from a comparison of two "
                            "adjacent periods of equal length. This is a "
                            "reading of the counts, not a cause."),
            unknowns=["Whether the records worked this period were comparable "
                      "to last period's is not knowable from these counts.",
                      "Seasonality is not modelled by this platform."],
            confidence=_confidence(previous, minimum),
            why_it_matters=("Appointments are what this employee is measured "
                            "on."),
            recommended_action=("Compare this employee's message volume and "
                                "response rate for the two periods before "
                                "changing anything."),
            affected_scope=card["name"],
            drilldown={"view": "employee", "employee_id": card["employee_id"]}))
    return out


def _response_decline(db, scope, cards, employees, th, now, window_key):
    delta_threshold = float(th["baseline_delta_fraction"])
    minimum = int(th["minimum_denominator"])
    out = []
    for card in cards["cards"]:
        responses = (card.get("trends") or {}).get("responses") or {}
        sent = (card.get("trends") or {}).get("messages_sent") or {}
        cur_sent, prev_sent = int(sent.get("current", 0)), int(
            sent.get("previous", 0))
        if prev_sent < minimum or cur_sent < minimum:
            continue
        cur_rate = float(responses.get("current", 0)) / cur_sent
        prev_rate = float(responses.get("previous", 0)) / prev_sent
        if prev_rate <= 0:
            continue
        drift = round((cur_rate - prev_rate) / prev_rate, 4)
        if drift > -delta_threshold:
            continue
        emp = employees.get(card["employee_id"])
        out.append(Finding(
            code=C.F_RESPONSE_DECLINE,
            organization_id=str(getattr(emp, "organization_id", "") or ""),
            employee_id=card["employee_id"],
            headline="Fewer people are replying to %s" % card["name"],
            severity=C.SEV_NORMAL,
            facts=[_fact("%d replies from %d messages this period; %d from %d "
                         "last period."
                         % (int(responses.get("current", 0)), cur_sent,
                            int(responses.get("previous", 0)), prev_sent),
                         "ai_performance_entries")],
            metrics=[_metric("response_rate_current", round(cur_rate, 4),
                             "responses / messages sent, this period"),
                     _metric("response_rate_previous", round(prev_rate, 4),
                             "responses / messages sent, previous period"),
                     _metric("change_fraction", drift,
                             "(current rate - previous rate) / previous rate")],
            interpretation=("The reply rate has fallen by more than the "
                            "reporting threshold of %d%%."
                            % int(delta_threshold * 100)),
            unknowns=["Message content is not stored in a form this platform "
                      "can compare across periods, so whether the wording "
                      "changed is unknown."],
            confidence=_confidence(prev_sent, minimum),
            why_it_matters=("A falling reply rate usually shows up before a "
                            "falling appointment count."),
            recommended_action=("Check whether this employee's audience or "
                                "channel changed between the two periods."),
            affected_scope=card["name"],
            drilldown={"view": "employee", "employee_id": card["employee_id"]}))
    return out


def _provider_failures_up(db, scope, cards, employees, th, now, window_key):
    """Provider trouble, stated as a comparison rather than as a count."""
    minimum = int(th["minimum_denominator"])
    today = collect.communication_rollup(db, scope, now - timedelta(days=1))
    prior = collect.communication_rollup(db, scope, now - timedelta(days=8))
    out = []
    for emp_id, slot in today.items():
        failures = sum(int(n or 0) for outcome, n
                       in (slot.get("by_provider_outcome") or {}).items()
                       if outcome in ("failed", "timeout", "rejected"))
        if failures < max(3, minimum // 2):
            continue
        week = prior.get(emp_id, {})
        week_failures = sum(int(n or 0) for outcome, n
                            in (week.get("by_provider_outcome") or {}).items()
                            if outcome in ("failed", "timeout", "rejected"))
        # The previous SEVEN days excluding today, expressed per day, so a
        # one-day count is compared against a one-day normal.
        daily_normal = round(max(0, week_failures - failures) / 7.0, 2)
        if daily_normal and failures < daily_normal * 2:
            continue
        emp = employees.get(emp_id)
        out.append(Finding(
            code=C.F_PROVIDER_FAILURES_UP,
            organization_id=str(getattr(emp, "organization_id", "") or ""),
            employee_id=emp_id,
            headline="Provider failures are up for %s"
                     % (getattr(emp, "name", None) or "an AI employee"),
            severity=C.SEV_HIGH,
            facts=[_fact("%d messages failed, timed out or were rejected in "
                         "the last 24 hours." % failures,
                         "ai_communications", failures),
                   _fact("The seven days before that averaged %.2f a day."
                         % daily_normal, "ai_communications")],
            metrics=[_metric("failures_24h", failures,
                             "communications with provider_outcome in failed, "
                             "timeout or rejected, last 24 hours"),
                     _metric("daily_normal_prior_7d", daily_normal,
                             "same count over the previous seven days, "
                             "divided by seven")],
            interpretation=("Today is materially worse than this employee's "
                            "own recent normal."),
            unknowns=["Whether the provider is at fault, or the numbers being "
                      "dialled are, is not distinguishable from these rows."],
            confidence=(CONF_HIGH if daily_normal else CONF_LOW),
            why_it_matters=("A failing channel stops work silently - the "
                            "objectives just stop moving."),
            recommended_action=("Check this channel's provider configuration "
                                "before looking at anything else."),
            affected_scope=getattr(emp, "name", None) or emp_id,
            drilldown={"view": "communications", "employee_id": emp_id}))
    return out


def _denial_concentration(db, scope, cards, employees, th, now, window_key):
    """Many refusals, all for one reason - which is almost always configuration."""
    threshold = int(th["denial_spike_24h"])
    ops = collect.ops_action_rollup(db, scope, now - timedelta(days=1))
    out = []
    for emp_id, slot in ops.items():
        denials = slot.get("denials") or {}
        if not denials:
            continue
        code, count = max(denials.items(), key=lambda kv: kv[1])
        total = sum(denials.values())
        if count < threshold:
            continue
        emp = employees.get(emp_id)
        share = round(float(count) / float(total), 4) if total else None
        out.append(Finding(
            code=C.F_DENIAL_CONCENTRATION,
            organization_id=str(getattr(emp, "organization_id", "") or ""),
            employee_id=emp_id,
            headline="%s is being refused for one reason"
                     % (getattr(emp, "name", None) or "An AI employee"),
            severity=C.SEV_HIGH,
            facts=[_fact("%d of %d refusals in the last 24 hours carry the "
                         "code '%s'." % (count, total, code),
                         "ai_ops_actions", count)],
            metrics=[_metric("top_denial_share", share,
                             "refusals with the most common code / all "
                             "refusals, last 24 hours"),
                     _metric("top_denial_count", count,
                             "refusals carrying '%s' in 24 hours" % code)],
            interpretation=("A single repeated refusal code is usually a "
                            "configuration change rather than anything the "
                            "engine did - a channel switched off, a feature "
                            "removed, an entitlement lapsed."),
            unknowns=[],
            confidence=CONF_HIGH,
            why_it_matters=("From the outside this looks like the AI has "
                            "stopped working, with no error anywhere."),
            recommended_action=("Check this employee's channels, feature "
                                "flags and entitlement against the code "
                                "above."),
            affected_scope=getattr(emp, "name", None) or emp_id,
            drilldown={"view": "activity", "employee_id": emp_id,
                       "denial_code": code}))
    return out


def _voice_without_yield(db, scope, cards, employees, th, now, window_key):
    """Voice minutes up, outcomes not - section 5's own example, verbatim."""
    minimum = int(th["minimum_denominator"])
    since = now - timedelta(days=C.window_days(window_key))
    comms = collect.communication_rollup(db, scope, since)
    ledger = collect.performance_rollup(db, scope,
                                        since.strftime("%Y-%m-%d"))
    out = []
    for emp_id, slot in comms.items():
        calls = int((slot.get("by_channel") or {}).get("voice", 0) or 0)
        if calls < max(5, minimum // 2):
            continue
        led = ledger.get(emp_id, {})
        outcomes = (int(led.get("appointments", 0) or 0)
                    + int(led.get("qualified", 0) or 0))
        messages = int(led.get("messages_sent", 0) or 0)
        if outcomes and calls and (outcomes / calls) >= 0.05:
            continue
        emp = employees.get(emp_id)
        out.append(Finding(
            code=C.F_VOICE_WITHOUT_YIELD,
            organization_id=str(getattr(emp, "organization_id", "") or ""),
            employee_id=emp_id,
            headline="Voice is being used without matching results",
            severity=C.SEV_NORMAL,
            facts=[_fact("%d voice communications and %d seconds of call time "
                         "in this period."
                         % (calls, int(slot.get("voice_seconds", 0) or 0)),
                         "ai_communications", calls),
                   _fact("%d appointments and qualifications were recorded in "
                         "the same period, across all channels." % outcomes,
                         "ai_performance_entries", outcomes)],
            metrics=[_metric("outcomes_per_call",
                             round(outcomes / calls, 4) if calls else None,
                             "appointments plus qualifications / voice "
                             "communications"),
                     _metric("messages_sent", messages,
                             "ai_performance_entries.messages_sent")],
            interpretation=("Voice is the most expensive channel this "
                            "platform has and is producing the fewest "
                            "measurable outcomes for this employee."),
            unknowns=["Whether a call produced a result recorded somewhere "
                      "other than this platform is unknown.",
                      "No provider cost is available, so the comparison is "
                      "of effort rather than of money."],
            confidence=_confidence(calls, minimum),
            why_it_matters=("Voice consumes far more per contact than "
                            "messaging."),
            recommended_action=("Compare this employee's messaging results "
                                "against its call results before continuing "
                                "with voice."),
            affected_scope=getattr(emp, "name", None) or emp_id,
            drilldown={"view": "costs", "employee_id": emp_id}))
    return out


def _no_outcomes(db, scope, cards, employees, th, now, window_key):
    """A live employee that has produced nothing at all."""
    minimum = int(th["minimum_denominator"])
    out = []
    for card in cards["cards"]:
        deployment = card.get("deployment") or {}
        if not deployment.get("live"):
            continue
        values = card.get("outcomes") or {}
        produced = sum(int((values.get(k) or {}).get("value") or 0)
                       for k in ("appointments_booked", "qualified",
                                 "handoffs"))
        attempted = int((values.get("messages_sent") or {}).get("value") or 0)
        if produced or attempted < max(20, minimum * 2):
            continue
        emp = employees.get(card["employee_id"])
        out.append(Finding(
            code=C.F_NO_OUTCOMES,
            organization_id=str(getattr(emp, "organization_id", "") or ""),
            employee_id=card["employee_id"],
            headline="%s has produced no outcomes this period" % card["name"],
            severity=C.SEV_HIGH,
            facts=[_fact("%d messages were sent and no appointments, "
                         "qualifications or handoffs were recorded."
                         % attempted, "ai_performance_entries", attempted),
                   _fact("Its deployment state is '%s'."
                         % deployment.get("state"),
                         "ai_employee_deployments")],
            metrics=[_metric("messages_sent", attempted,
                             "ai_performance_entries.messages_sent"),
                     _metric("outcomes", 0,
                             "appointments + qualified + handoffs")],
            interpretation=("It is working and producing nothing. That is "
                            "usually configuration - the wrong audience, the "
                            "wrong question, or nowhere to hand people to."),
            unknowns=["Whether these contacts were reachable at all is "
                      "answered by the eligibility record, not by this "
                      "count."],
            confidence=_confidence(attempted, minimum),
            why_it_matters="This employee is costing effort and returning none.",
            recommended_action=("Read one of its conversations end to end "
                                "before changing its settings."),
            affected_scope=card["name"],
            drilldown={"view": "employee", "employee_id": card["employee_id"]}))
    return out


def _idle_employee(db, scope, cards, employees, th, now, window_key):
    """Switched on, entitled, and doing nothing at all."""
    idle_hours = int(th["idle_employee_hours"])
    counts = collect.work_counts_by_employee_state(db, scope)
    out = []
    for card in cards["cards"]:
        deployment = card.get("deployment") or {}
        if not deployment.get("live"):
            continue
        emp_id = card["employee_id"]
        open_work = sum(int(n) for (e, state), n in counts.items()
                        if e == emp_id and state not in C.WORK_TERMINAL_STATES)
        runs = int((card.get("outcomes") or {}).get("runs", {}).get("value")
                   or 0)
        if open_work or runs:
            continue
        emp = employees.get(emp_id)
        out.append(Finding(
            code=C.F_IDLE_EMPLOYEE,
            organization_id=str(getattr(emp, "organization_id", "") or ""),
            employee_id=emp_id,
            headline="%s is switched on and has nothing to do" % card["name"],
            severity=C.SEV_NORMAL,
            facts=[_fact("No open work items and no runs in this period.",
                         "ai_work_items", 0),
                   _fact("Its deployment state is '%s' and its commercial "
                         "state is '%s'." % (deployment.get("state"),
                                             deployment.get(
                                                 "commercial_state")),
                         "ai_employee_deployments")],
            metrics=[_metric("open_work_items", 0, "non-terminal work items"),
                     _metric("runs_in_window", runs, "ai_employee_runs")],
            interpretation=("Nothing has been given to it. An employee with "
                            "no queue is not underperforming - it is "
                            "unstaffed."),
            unknowns=["Whether there are records that SHOULD be assigned to "
                      "it is a business decision this platform does not "
                      "hold."],
            confidence=CONF_HIGH,
            why_it_matters=("This is being paid for and is not being used."),
            recommended_action=("Give it an audience, or pause it until there "
                                "is one."),
            affected_scope=card["name"],
            drilldown={"view": "employee", "employee_id": emp_id},
            finding_key="%s:%s:%dh" % (C.F_IDLE_EMPLOYEE, emp_id, idle_hours)))
    return out


def _handoff_latency(db, scope, cards, employees, th, now, window_key):
    waiting_hours = int(th["handoff_unaccepted_hours"])
    rows = collect.handoffs(db, scope, statuses=("open",), limit=500)
    if not rows:
        return []
    stale = [h for h in rows
             if h.created_at and (now - h.created_at).total_seconds()
             > waiting_hours * 3600]
    if not stale:
        return []
    oldest = min(h.created_at for h in stale)
    hours = round((now - oldest).total_seconds() / 3600.0, 1)
    org = str(stale[0].organization_id)
    return [Finding(
        code=C.F_HANDOFF_LATENCY, organization_id=org,
        headline="%d handoff%s have been waiting for a person"
                 % (len(stale), "" if len(stale) == 1 else "s"),
        severity=C.SEV_HIGH,
        facts=[_fact("%d open handoffs are older than %d hours."
                     % (len(stale), waiting_hours), "ai_handoffs", len(stale)),
               _fact("The oldest has been waiting %.1f hours." % hours,
                     "ai_handoffs")],
        metrics=[_metric("open_handoffs_over_threshold", len(stale),
                         "open handoffs older than the configured %d hours"
                         % waiting_hours),
                 _metric("oldest_wait_hours", hours,
                         "now minus the oldest open handoff's created_at")],
        interpretation=("The AI did its job and stopped. Nobody has picked "
                        "these up."),
        unknowns=["Whether somebody contacted these people outside "
                  "AdvisorFlow is not knowable here."],
        confidence=CONF_HIGH,
        why_it_matters=("Each of these is a person who asked for someone and "
                        "has not heard back."),
        recommended_action=("Assign these, or set a handoff destination so "
                            "the next ones route automatically."),
        affected_scope="%d handoffs" % len(stale),
        drilldown={"view": "handoffs"})]


_GENERATORS = (
    _eligible_backlog,
    _review_backlog,
    _conversion_decline,
    _response_decline,
    _provider_failures_up,
    _denial_concentration,
    _voice_without_yield,
    _no_outcomes,
    _idle_employee,
    _handoff_latency,
)


# ---------------------------------------------------------------------------
# PERSISTENCE
# ---------------------------------------------------------------------------


def refresh(db: Session, scope: Scope, *,
            window_key: str = C.DEFAULT_WINDOW,
            thresholds: Optional[Dict] = None,
            now: Optional[datetime] = None) -> Dict[str, Any]:
    """Recompute findings for a scope and reconcile them with what is stored.

    The same upsert-and-clear shape as the attention queue, for the same
    reason: a finding that stopped being true must stop being displayed, and
    an acknowledged finding must stay acknowledged while it is still true.
    """
    now = now or datetime.utcnow()
    generated = generate(db, scope, window_key=window_key,
                         thresholds=thresholds, now=now)
    platform_of = _platforms(db, {f.organization_id for f in generated
                                  if f.organization_id})
    seen: Dict[str, set] = {}
    written = 0
    for f in generated:
        if not f.organization_id:
            # A finding with no organization cannot be stored tenant-safely,
            # so it is returned to the caller and not persisted. Storing it
            # against a guessed organization would be worse than not storing
            # it at all.
            continue
        if not scope.covers(f.organization_id) and not scope._all_organizations:
            _log.warning("t9 findings: finding outside scope discarded "
                         "(org=%s)", f.organization_id)
            continue
        seen.setdefault(f.organization_id, set()).add(f.finding_key)
        _store(db, f, platform_id=platform_of.get(f.organization_id),
               window_key=window_key, now=now)
        written += 1

    cleared = 0
    q = scope.apply(db.query(AISupervisorFinding),
                    AISupervisorFinding.organization_id).filter(
        AISupervisorFinding.state.in_((C.FINDING_STATE_OPEN,
                                       C.FINDING_STATE_ACK)))
    for row in q.limit(5000).all():
        if row.finding_key in seen.get(str(row.organization_id), set()):
            continue
        row.state = C.FINDING_STATE_CLEARED
        cleared += 1
    db.flush()
    return {"generated": len(generated), "written": written,
            "cleared": cleared, "generated_at": now.isoformat()}


def _platforms(db: Session, org_ids) -> Dict[str, Optional[str]]:
    wanted = sorted({str(i) for i in org_ids if i})
    if not wanted:
        return {}
    return {str(row[0]): row[1]
            for row in db.query(Organization.id, Organization.platform_id)
            .filter(Organization.id.in_(wanted)).all()}


def _store(db: Session, f: Finding, *, platform_id, window_key: str,
           now: datetime) -> AISupervisorFinding:
    row = (db.query(AISupervisorFinding)
           .filter(AISupervisorFinding.organization_id == f.organization_id,
                   AISupervisorFinding.finding_key == f.finding_key)
           .first())
    if row is None:
        row = AISupervisorFinding(organization_id=f.organization_id,
                                  finding_key=f.finding_key)
        db.add(row)
    elif row.state == C.FINDING_STATE_CLEARED:
        row.state = C.FINDING_STATE_OPEN
    row.platform_id = platform_id
    row.employee_id = f.employee_id
    row.code = f.code
    row.severity = f.severity
    row.headline = (f.headline or "")[:255]
    # THE FOUR PARTS GO INTO FOUR COLUMNS. Not one blob - a renderer cannot
    # then print an interpretation where a fact belongs, and a test can assert
    # that `facts` contains no sentence the database did not support.
    row.facts = json.dumps(f.facts, default=str)[:8000]
    row.metrics = json.dumps(f.metrics, default=str)[:8000]
    row.interpretation = f.interpretation
    row.unknowns = json.dumps(f.unknowns, default=str)[:4000]
    row.confidence = f.confidence
    row.why_it_matters = (f.why_it_matters or "")[:255] or None
    row.recommended_action = (f.recommended_action or "")[:255] or None
    row.affected_scope = (f.affected_scope or "")[:255] or None
    row.drilldown = json.dumps(f.drilldown, default=str)[:2000]
    row.window_key = window_key
    row.computed_at = now
    return row


def listing(db: Session, scope: Scope, *,
            states: Optional[List[str]] = None,
            limit: int = 100) -> Dict[str, Any]:
    q = scope.apply(db.query(AISupervisorFinding),
                    AISupervisorFinding.organization_id).filter(
        AISupervisorFinding.state.in_(
            list(states or (C.FINDING_STATE_OPEN, C.FINDING_STATE_ACK))))
    rows = (q.order_by(AISupervisorFinding.computed_at.desc())
            .limit(limit).all())
    return {"findings": [_render(r) for r in rows], "total": len(rows)}


def _render(row: AISupervisorFinding) -> Dict[str, Any]:
    def _load(raw, default):
        try:
            value = json.loads(raw or "null")
        except (ValueError, TypeError):
            return default
        return value if value is not None else default

    return {
        "id": row.id,
        "code": row.code,
        "headline": row.headline,
        "severity": row.severity,
        "severity_label": C.SEVERITY_LABELS.get(row.severity, row.severity),
        "employee_id": row.employee_id,
        "organization_id": row.organization_id,
        "fact": {"class": C.EV_FACT, "items": _load(row.facts, [])},
        "metric": {"class": C.EV_METRIC, "items": _load(row.metrics, [])},
        "interpretation": {
            "class": C.EV_INTERPRETATION, "text": row.interpretation,
            "note": ("This is a reading of the numbers above, not a record in "
                     "the database."),
        },
        "unknown": {"class": C.EV_UNKNOWN, "items": _load(row.unknowns, [])},
        "recommendation": {
            "class": C.EV_RECOMMENDATION, "text": row.recommended_action,
            "note": "A suggestion for a person. Nothing acts on this on its own.",
        },
        "confidence": row.confidence,
        "why_it_matters": row.why_it_matters,
        "affected_scope": row.affected_scope,
        "drilldown": _load(row.drilldown, {}),
        "window": row.window_key,
        "computed_at": (row.computed_at.isoformat()
                        if row.computed_at else None),
        "state": row.state,
        "acknowledged_at": (row.acknowledged_at.isoformat()
                            if row.acknowledged_at else None),
    }


def get(db: Session, scope: Scope, finding_id: str):
    return (scope.apply(db.query(AISupervisorFinding),
                        AISupervisorFinding.organization_id)
            .filter(AISupervisorFinding.id == finding_id).first())


def acknowledge(db: Session, scope: Scope, finding_id: str, *, user):
    row = get(db, scope, finding_id)
    if row is None:
        return None
    row.state = C.FINDING_STATE_ACK
    row.acknowledged_at = datetime.utcnow()
    row.acknowledged_by = getattr(user, "id", None)
    db.flush()
    return row
