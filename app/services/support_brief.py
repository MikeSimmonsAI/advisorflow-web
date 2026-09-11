"""THE DAILY PLATFORM SUPPORT BRIEF — one document, not a wall of counters.

WHAT IT IS FOR
--------------
God should be able to open one page each morning and know: what broke, what
the platform fixed by itself, what it could not, what is waiting on a human,
what is coming back again, and what to do about it. Everything here exists to
answer that and nothing else.

NOTHING IS INVENTED
-------------------
Two rules, and they are what make the brief worth reading:

  * A RATE COMPUTED FROM ZERO ATTEMPTS IS NOT 100%. `auto_fix_success_rate`
    is NULL when nothing was eligible, and the screen says "nothing eligible"
    rather than showing a perfect score for a day when the fixer never ran.

  * A TREND NEEDS A PREVIOUS PERIOD. `trend_json` is NULL on the first brief
    and on any day whose predecessor was never generated. A baseline of zero
    would render every first day as an infinite improvement.

THE DISTINCTIONS THE BRIEF MUST KEEP
-------------------------------------
    DETECTED        we saw a fault
    AUTO-FIXED      the platform repaired it AND VERIFIED the repair
    HUMAN-FIXED     a person resolved it
    UNRESOLVED      still open
    RECURRING       this signature keeps coming back
    PLATFORM        our defect
    PROVIDER        somebody else's outage
    CUSTOMER CONFIG their setup

Collapsing any two of those produces a number that looks encouraging and
means nothing. In particular, "fixed" is `verified`, never `status == FIXED`:
a repair that ran and was not checked counts as a failure here, deliberately.

SCOPE
-----
One brief per (date, platform). A NULL platform is the whole platform — the
only view that can see a cross-brand incident, which is the view that exists
to notice one.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.models import Platform
from app.models.support_models import (
    Cause, FixStatus, SlaState, SupportDailyBrief, SupportDiagnosticRun,
    SupportFixRun, SupportIncident, SupportIssueSignature, SupportTicket,
    TicketStatus,
)
from app.services import severity as sev, support_sla

log = logging.getLogger(__name__)


def _day_bounds(day: str) -> tuple:
    start = datetime.strptime(day, "%Y-%m-%d")
    return start, start + timedelta(days=1)


def _scoped(query, model, platform_id: Optional[str]):
    return query.filter(model.platform_id == platform_id) if platform_id else query


def generate(db: Session, *, day: Optional[str] = None,
             platform_id: Optional[str] = None,
             now: Optional[datetime] = None) -> SupportDailyBrief:
    """Build (or rebuild) one day's brief. Idempotent by (date, platform).

    REGENERATING OVERWRITES rather than appending, so a brief that ran at
    06:00 and again at 18:00 tells you about the whole day instead of leaving
    two contradictory rows for the same date.
    """
    now = now or datetime.utcnow()
    day = day or (now - timedelta(days=1)).strftime("%Y-%m-%d")
    start, end = _day_bounds(day)

    tickets_opened = _scoped(
        db.query(SupportTicket).filter(SupportTicket.created_at >= start,
                                       SupportTicket.created_at < end),
        SupportTicket, platform_id).all()

    tickets_resolved = _scoped(
        db.query(SupportTicket).filter(SupportTicket.resolved_at >= start,
                                       SupportTicket.resolved_at < end),
        SupportTicket, platform_id).all()

    fix_runs = _scoped(
        db.query(SupportFixRun).filter(SupportFixRun.created_at >= start,
                                       SupportFixRun.created_at < end),
        SupportFixRun, platform_id).all()

    diagnostic_runs = _scoped(
        db.query(SupportDiagnosticRun).filter(
            SupportDiagnosticRun.created_at >= start,
            SupportDiagnosticRun.created_at < end),
        SupportDiagnosticRun, platform_id).all()

    incidents = (db.query(SupportIncident)
                 .filter(SupportIncident.last_observed_at >= start,
                         SupportIncident.last_observed_at < end).all())

    # ── DETECTED. A diagnostic run that FOUND something, plus a ticket that
    #    was raised without one. Counting every diagnostic run would count the
    #    healthy ones; counting only tickets would miss everything Ask AI
    #    resolved before a ticket existed.
    detected_runs = [r for r in diagnostic_runs
                     if r.overall_severity in (sev.ACTION_REQUIRED, sev.ATTENTION)]
    tickets_without_diagnosis = [t for t in tickets_opened if not t.diagnostic_run_id]
    issues_detected = len(detected_runs) + len(tickets_without_diagnosis)

    # ── FIXED. `verified`, never `status`.
    auto_fixed = [f for f in fix_runs if f.verified]
    auto_fix_failed = [f for f in fix_runs if f.status == FixStatus.FAILED]
    awaiting_approval = [f for f in fix_runs
                         if f.status == FixStatus.APPROVAL_REQUIRED]

    # ELIGIBLE means it actually got as far as trying. A proposal waiting for
    # approval never attempted anything and must not drag a success rate down;
    # a refused one never ran either.
    eligible = [f for f in fix_runs
                if f.status in (FixStatus.FIXED, FixStatus.FAILED,
                                FixStatus.ROLLED_BACK)]
    success_rate = (int(round(len(auto_fixed) * 100.0 / len(eligible)))
                    if eligible else None)

    human_fixed = [t for t in tickets_resolved
                   if t.resolution_code != "fixed_automatically"]

    causes = {c: 0 for c in Cause.ALL}
    for ticket in tickets_opened:
        cause = ticket.ai_suspected_cause or Cause.UNKNOWN
        causes[cause] = causes.get(cause, 0) + 1
    for run in detected_runs:
        cause = run.suspected_cause or Cause.UNKNOWN
        causes[cause] = causes.get(cause, 0) + 1

    open_now = _scoped(
        db.query(SupportTicket).filter(
            SupportTicket.status.in_(list(TicketStatus.OPEN))),
        SupportTicket, platform_id).all()
    at_risk = [t for t in open_now if t.sla_state == SlaState.AT_RISK]
    breached = [t for t in open_now if t.sla_state == SlaState.BREACHED]

    first_response_avg = average_first_response_minutes(db, tickets_opened)

    brief = (db.query(SupportDailyBrief)
             .filter(SupportDailyBrief.brief_date == day,
                     SupportDailyBrief.platform_id == platform_id).first())
    if brief is None:
        brief = SupportDailyBrief(brief_date=day, platform_id=platform_id)
        db.add(brief)

    previous = _previous_brief(db, day, platform_id)

    brief.issues_detected = issues_detected
    brief.auto_fixed = len(auto_fixed)
    brief.auto_fix_failed = len(auto_fix_failed)
    brief.human_fixed = len(human_fixed)
    brief.unresolved = len(open_now)
    brief.awaiting_god_approval = len(awaiting_approval)
    brief.customer_configuration_issues = causes.get(Cause.CUSTOMER_CONFIGURATION, 0)
    brief.provider_issues = causes.get(Cause.THIRD_PARTY_PROVIDER, 0)
    brief.platform_defects = causes.get(Cause.PLATFORM_DEFECT, 0)
    brief.multi_org_incidents = sum(1 for i in incidents
                                    if (i.organizations_affected or 0) >= 2)
    brief.sla_at_risk = len(at_risk)
    brief.sla_breached = len(breached)
    brief.tickets_opened = len(tickets_opened)
    brief.tickets_resolved = len(tickets_resolved)
    brief.first_response_minutes_avg = first_response_avg
    brief.auto_fix_success_rate = success_rate
    brief.top_services_json = json.dumps(_top_services(detected_runs, tickets_opened))
    brief.top_signatures_json = json.dumps(_top_signatures(db, start, end,
                                                           platform_id))
    brief.recommended_actions_json = json.dumps(
        _recommended_actions(db, incidents, auto_fix_failed, awaiting_approval,
                             breached))
    brief.trend_json = (json.dumps(_trend(brief, previous))
                        if previous is not None else None)
    brief.generated_at = now
    db.flush()
    return brief


def average_first_response_minutes(db: Session,
                                   tickets: List[SupportTicket]) -> Optional[int]:
    """Mean first-response time, in BUSINESS minutes, for tickets answered.

    PUBLIC, because the God console shows the same figure on its board and the
    two must be the same number computed the same way. A second
    implementation in the router would eventually disagree with the brief
    about how long we take to answer, which is precisely the statistic nobody
    should have two versions of.

    Business minutes rather than wall clock, for the same reason the target
    is: a mean that includes the weekend measures the calendar, not the team.
    Returns None when no ticket raised that day has been answered — an
    average of nothing is not zero.
    """
    answered = [t for t in tickets if t.first_response_at and t.created_at]
    if not answered:
        return None
    total = 0
    for ticket in answered:
        cfg = support_sla.hours_for(db, ticket.platform_id)
        total += support_sla.business_minutes_between(
            ticket.created_at, ticket.first_response_at, cfg,
            continuous=support_sla.is_continuous(ticket.queue, cfg))
    return int(round(total / float(len(answered))))


def _top_services(runs: List[SupportDiagnosticRun],
                  tickets: List[SupportTicket]) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for ticket in tickets:
        service = (ticket.issue_signature or "").split(".", 1)[0]
        if service:
            counts[service] = counts.get(service, 0) + 1
    for run in runs:
        for key in (run.checks_run or "").split(","):
            key = key.strip()
            if key:
                counts[key] = counts.get(key, 0) + 1
    return [{"service": k, "count": v}
            for k, v in sorted(counts.items(), key=lambda kv: -kv[1])[:8]]


def _top_signatures(db: Session, start: datetime, end: datetime,
                    platform_id: Optional[str]) -> List[Dict[str, Any]]:
    rows = (db.query(SupportIssueSignature)
            .filter(SupportIssueSignature.last_seen_at >= start,
                    SupportIssueSignature.last_seen_at < end)
            .order_by(SupportIssueSignature.occurrence_count.desc())
            .limit(8).all())
    return [{"signature": r.signature, "title": r.title,
             "occurrences": r.occurrence_count,
             "organizations": r.organizations_affected,
             "auto_fixed": r.auto_fixed_count,
             "engineering_candidate": bool(r.engineering_candidate)}
            for r in rows]


def _recommended_actions(db: Session, incidents: List[SupportIncident],
                         failed_fixes: List[SupportFixRun],
                         awaiting: List[SupportFixRun],
                         breached: List[SupportTicket]) -> List[Dict[str, Any]]:
    """What God should DO today, in priority order.

    Every entry names a concrete object and a concrete next step. "Review
    support metrics" is not an action; "approve or reject the three prepared
    repairs waiting on you" is.
    """
    actions: List[Dict[str, Any]] = []

    for incident in incidents:
        if (incident.organizations_affected or 0) >= 2 and \
                incident.status == "suspected":
            actions.append({
                "priority": "high",
                "kind": "incident",
                "reference": incident.incident_number,
                "action": "Acknowledge or dismiss this incident",
                "why": incident.impact_summary or "Affects multiple organizations.",
                "recommendation": incident.recommended_remediation,
            })

    if awaiting:
        actions.append({
            "priority": "high",
            "kind": "approval",
            "reference": "%d prepared repair(s)" % len(awaiting),
            "action": "Approve or reject the prepared repairs",
            "why": "Each one is diagnosed and waiting on a decision, so the "
                   "underlying problem is still live.",
            "recommendation": None,
        })

    if breached:
        actions.append({
            "priority": "high",
            "kind": "sla",
            "reference": "%d ticket(s) past target" % len(breached),
            "action": "Respond to the tickets that are past their first-response "
                      "target",
            "why": "A breached first response is a commitment already missed; "
                   "the second-best time to answer is now.",
            "recommendation": None,
        })

    by_action: Dict[str, int] = {}
    for run in failed_fixes:
        by_action[run.action_key] = by_action.get(run.action_key, 0) + 1
    for action_key, count in sorted(by_action.items(), key=lambda kv: -kv[1]):
        if count >= 2:
            actions.append({
                "priority": "medium",
                "kind": "fixer",
                "reference": action_key,
                "action": "Review this repair — it failed %d time(s) today" % count,
                "why": "A repair that fails repeatedly is worse than none, "
                       "because the problem looks handled.",
                "recommendation": None,
            })

    return actions[:10]


def _previous_brief(db: Session, day: str,
                    platform_id: Optional[str]) -> Optional[SupportDailyBrief]:
    return (db.query(SupportDailyBrief)
            .filter(SupportDailyBrief.platform_id == platform_id,
                    SupportDailyBrief.brief_date < day)
            .order_by(SupportDailyBrief.brief_date.desc()).first())


def _trend(current: SupportDailyBrief,
           previous: SupportDailyBrief) -> Dict[str, Any]:
    """Change against the previous generated brief.

    Against the PREVIOUS BRIEF, not against "yesterday": if no brief was
    generated yesterday there is no baseline, and comparing against a day we
    never measured would manufacture a swing out of an outage in the job that
    writes these.
    """
    fields = ("issues_detected", "auto_fixed", "auto_fix_failed", "unresolved",
              "tickets_opened", "tickets_resolved", "sla_breached",
              "platform_defects", "provider_issues")
    out: Dict[str, Any] = {"compared_to": previous.brief_date, "deltas": {}}
    for field in fields:
        now_value = getattr(current, field, None)
        was_value = getattr(previous, field, None)
        if now_value is None or was_value is None:
            continue
        out["deltas"][field] = {"now": now_value, "was": was_value,
                                "change": now_value - was_value}
    return out


def brief_view(brief: SupportDailyBrief) -> Dict[str, Any]:
    def _load(raw, fallback):
        try:
            return json.loads(raw) if raw else fallback
        except (TypeError, ValueError):
            return fallback

    return {
        "brief_date": brief.brief_date,
        "platform_id": brief.platform_id,
        "generated_at": brief.generated_at,
        "detected": brief.issues_detected,
        "auto_fixed": brief.auto_fixed,
        "auto_fix_failed": brief.auto_fix_failed,
        "human_fixed": brief.human_fixed,
        "unresolved": brief.unresolved,
        "awaiting_god_approval": brief.awaiting_god_approval,
        "customer_configuration_issues": brief.customer_configuration_issues,
        "provider_issues": brief.provider_issues,
        "platform_defects": brief.platform_defects,
        "multi_org_incidents": brief.multi_org_incidents,
        "sla_at_risk": brief.sla_at_risk,
        "sla_breached": brief.sla_breached,
        "tickets_opened": brief.tickets_opened,
        "tickets_resolved": brief.tickets_resolved,
        "first_response_minutes_avg": brief.first_response_minutes_avg,
        # NULL is meaningful and is passed through as null rather than as 0.
        # The screen renders "nothing eligible", which is the truth.
        "auto_fix_success_rate": brief.auto_fix_success_rate,
        "top_services": _load(brief.top_services_json, []),
        "top_signatures": _load(brief.top_signatures_json, []),
        "recommended_actions": _load(brief.recommended_actions_json, []),
        "trend": _load(brief.trend_json, None),
        "has_baseline": bool(brief.trend_json),
    }


def latest(db: Session, *, platform_id: Optional[str] = None
           ) -> Optional[SupportDailyBrief]:
    return (db.query(SupportDailyBrief)
            .filter(SupportDailyBrief.platform_id == platform_id)
            .order_by(SupportDailyBrief.brief_date.desc()).first())


def run_daily_intelligence(db: Session, *, day: Optional[str] = None,
                           now: Optional[datetime] = None) -> Dict[str, Any]:
    """The whole nightly pass: refresh, correlate, learn, then write the brief.

    ORDER MATTERS AND IS THE POINT.

      1. Refresh cached SLA states, so a ticket that crossed its target
         overnight is counted as breached rather than as within.
      2. Correlate, so today's incidents exist before the brief counts them.
      3. Scan for learning candidates, so a recurring problem is proposed
         rather than merely tallied.
      4. Generate the platform-wide brief and one per brand.

    Running the brief first would produce a document about yesterday's
    understanding of yesterday.
    """
    from app.services import support_incidents, support_knowledge, support_tickets

    now = now or datetime.utcnow()
    day = day or (now - timedelta(days=1)).strftime("%Y-%m-%d")
    result: Dict[str, Any] = {"day": day}

    try:
        result["sla_refresh"] = support_tickets.refresh_open_sla_states(db, now=now)
    except Exception:                                          # noqa: BLE001
        log.exception("support_brief: SLA refresh failed")
        result["sla_refresh"] = {"error": True}

    try:
        result["correlation"] = support_incidents.correlate(db, now=now)
    except Exception:                                          # noqa: BLE001
        log.exception("support_brief: correlation failed")
        result["correlation"] = {"error": True}

    try:
        result["learning"] = support_knowledge.scan_for_candidates(db)
    except Exception:                                          # noqa: BLE001
        log.exception("support_brief: candidate scan failed")
        result["learning"] = {"error": True}

    briefs = []
    try:
        briefs.append(brief_view(generate(db, day=day, platform_id=None, now=now)))
        for platform in db.query(Platform).all():
            briefs.append(brief_view(generate(db, day=day, platform_id=platform.id,
                                              now=now)))
    except Exception:                                          # noqa: BLE001
        log.exception("support_brief: brief generation failed")
    result["briefs"] = briefs
    db.commit()
    return result
