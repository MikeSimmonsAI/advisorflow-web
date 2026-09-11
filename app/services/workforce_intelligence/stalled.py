"""WORK THAT IS NOT PROGRESSING - detected deterministically, never guessed.

EVERY DETECTOR IN THIS FILE IS A QUERY WITH A THRESHOLD. Not a model, not a
heuristic, not a score. A manager who asks "why is this on my list" gets the
row and the comparison that put it there, and a test can arrange the row and
assert the item appears. That is the whole design: section 6 asks for
deterministic detection, and determinism means reproducible, not merely
consistent.

THRESHOLDS ARE DETECTION SETTINGS, NOT PROMISES. Section 6's other half: do
not invent customer-specific SLA promises. "Has not moved for an hour" is an
operator's own filter; "will be answered within an hour" is a commitment to a
customer, and nothing here makes one. Every sentence this module produces says
what HAS happened - "waiting since 09:12", "three attempts, no progress" - and
never what should happen by when.

WHAT A DETECTOR RETURNS IS A SIGNAL, NOT AN ALERT. A signal is a fact with a
deduplication key attached. Turning signals into the queue - rolling forty
stalled objectives up into the one provider outage that caused them, deciding
severity, ordering - is `attention.py`'s job, and keeping it separate is what
makes it possible to test the detection without testing the presentation.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.ai_operations_models import (AICommunication,
                                             AIConversationThread,
                                             AIScheduledAction)
from app.models.models import BookingLink, Lead
from app.models.workforce_models import AIHandoff, AIWorkItem
from app.services.ai_operations import constants as O
from app.services.workforce import constants as W
from app.services.workforce_intelligence import collect
from app.services.workforce_intelligence import constants as C
from app.services.workforce_intelligence.scope import Scope

_log = logging.getLogger(__name__)


@dataclass
class Signal:
    """One detected condition, with everything the queue needs to render it."""

    kind: str
    organization_id: str
    dedup_key: str
    title: str
    why: str
    severity: str = C.SEV_NORMAL
    employee_id: Optional[str] = None
    deployment_id: Optional[str] = None
    subject_type: Optional[str] = None
    subject_id: Optional[str] = None
    objective_ref: Optional[str] = None
    source_kind: Optional[str] = None
    source_id: Optional[str] = None
    recommended_action: Optional[str] = None
    evidence: Dict[str, Any] = field(default_factory=dict)
    drilldown: Dict[str, Any] = field(default_factory=dict)
    count: int = 1
    oldest_at: Optional[datetime] = None


def _ago(then: Optional[datetime], now: datetime) -> str:
    """How long ago, in words a person reads without converting units."""
    if then is None:
        return "an unknown time ago"
    seconds = max(0, int((now - then).total_seconds()))
    if seconds < 90:
        return "just now"
    minutes = seconds // 60
    if minutes < 90:
        return "%d minutes ago" % minutes
    hours = minutes // 60
    if hours < 48:
        return "%d hours ago" % hours
    return "%d days ago" % (hours // 24)


def detect(db: Session, scope: Scope, *,
           thresholds: Optional[Dict] = None,
           now: Optional[datetime] = None,
           limit_per_detector: int = 200) -> List[Signal]:
    """Run every detector over one scope. Never raises for one detector.

    A DETECTOR THAT FAILS MUST NOT EMPTY THE QUEUE. Section 24: silent failure
    must not look like zero activity. Each detector is wrapped, and a failure
    is logged and counted rather than allowed to take the whole pass down -
    the alternative is one bad query turning a workforce with fourteen
    problems into a screen that says everything is fine.
    """
    now = now or datetime.utcnow()
    th = thresholds or C.thresholds()
    signals: List[Signal] = []
    for detector in _DETECTORS:
        try:
            signals.extend(detector(db, scope, th, now, limit_per_detector))
        except Exception:                                    # noqa: BLE001
            _log.exception("t9 stalled: detector %s failed for scope %s/%s",
                           getattr(detector, "__name__", "?"),
                           scope.scope_type, scope.scope_id)
    return signals


# ---------------------------------------------------------------------------
# THE DETECTORS
# ---------------------------------------------------------------------------


def _stalled_work(db, scope, th, now, limit) -> List[Signal]:
    """Claimable work that was due and did not move.

    THE DEFINITION IS T6'S OWN, deliberately. `queue.stalled` already answers
    "due for action and untouched" and the engine's supervisor already uses
    it; re-deriving it here with a slightly different comparison is how two
    screens end up disagreeing about the same forty records. This adds scope
    and grouping, and changes nothing about what stalled means.
    """
    cutoff = now - timedelta(minutes=int(th["stalled_work_minutes"]))
    q = scope.apply(
        db.query(AIWorkItem.organization_id, AIWorkItem.employee_id,
                 func.count(AIWorkItem.id), func.min(AIWorkItem.next_action_at)),
        AIWorkItem.organization_id).filter(
            AIWorkItem.state.in_(list(W.CLAIMABLE_STATES)),
            AIWorkItem.next_action_at.isnot(None),
            AIWorkItem.next_action_at <= cutoff)
    out = []
    for org_id, emp_id, n, oldest in q.group_by(
            AIWorkItem.organization_id, AIWorkItem.employee_id
    ).limit(limit).all():
        out.append(Signal(
            kind=C.A_OBJECTIVE_STUCK, organization_id=str(org_id),
            employee_id=emp_id,
            dedup_key="stuck:work:%s" % (emp_id or "unassigned"),
            title="%d record%s have not moved" % (int(n),
                                                  "" if n == 1 else "s"),
            why=("These records were due for action %s and nothing has "
                 "happened to them since." % _ago(oldest, now)),
            severity=C.SEV_HIGH if int(n) > 10 else C.SEV_NORMAL,
            source_kind="ai_work_items", count=int(n), oldest_at=oldest,
            recommended_action=("Check whether this employee is running and "
                                "whether its channels are reachable."),
            evidence={"records": int(n), "oldest_due_at": _iso(oldest),
                      "threshold_minutes": int(th["stalled_work_minutes"])},
            drilldown={"view": "work", "employee_id": emp_id,
                       "states": sorted(W.CLAIMABLE_STATES)}))
    return out


def _no_permissible_next_action(db, scope, th, now, limit) -> List[Signal]:
    """Active work with nowhere to go.

    A work item in a claimable state with NO next action scheduled and no
    claim on it is not waiting for anything - nothing will ever pick it up.
    This is the condition the brief calls "objective active but no permissible
    next action", and it is invisible on every other screen precisely because
    the record looks healthy: its state is fine, it simply has no future.
    """
    q = scope.apply(
        db.query(AIWorkItem.organization_id, AIWorkItem.employee_id,
                 func.count(AIWorkItem.id), func.min(AIWorkItem.updated_at)),
        AIWorkItem.organization_id).filter(
            AIWorkItem.state.in_(list(W.CLAIMABLE_STATES)),
            AIWorkItem.next_action_at.is_(None),
            AIWorkItem.claim_token.is_(None),
            AIWorkItem.terminal_at.is_(None),
            AIWorkItem.updated_at <= now - timedelta(
                minutes=int(th["stalled_work_minutes"])))
    out = []
    for org_id, emp_id, n, oldest in q.group_by(
            AIWorkItem.organization_id, AIWorkItem.employee_id
    ).limit(limit).all():
        out.append(Signal(
            kind=C.A_OBJECTIVE_BLOCKED, organization_id=str(org_id),
            employee_id=emp_id,
            dedup_key="blocked:no-next-action:%s" % (emp_id or "unassigned"),
            title="%d record%s cannot proceed" % (int(n),
                                                  "" if n == 1 else "s"),
            why=("These records are still open but nothing is scheduled to "
                 "happen to them and nobody has claimed them. The oldest was "
                 "last touched %s." % _ago(oldest, now)),
            severity=C.SEV_HIGH, source_kind="ai_work_items", count=int(n),
            oldest_at=oldest,
            recommended_action=("Open one of these records to see which gate "
                                "stopped it, then fix the cause or close the "
                                "records."),
            evidence={"records": int(n), "oldest_touched_at": _iso(oldest)},
            drilldown={"view": "work", "employee_id": emp_id,
                       "filter": "no_next_action"}))
    return out


def _waiting_without_followup(db, scope, th, now, limit) -> List[Signal]:
    """Someone was messaged, never replied, and nothing will chase them."""
    cutoff = now - timedelta(hours=int(th["waiting_without_followup_hours"]))
    q = scope.apply(
        db.query(AIConversationThread.organization_id,
                 AIConversationThread.employee_id,
                 func.count(AIConversationThread.id),
                 func.min(AIConversationThread.last_outbound_at)),
        AIConversationThread.organization_id).filter(
            AIConversationThread.status == "open",
            AIConversationThread.state == O.WAITING_FOR_RESPONSE,
            AIConversationThread.next_action_at.is_(None),
            AIConversationThread.last_outbound_at.isnot(None),
            AIConversationThread.last_outbound_at <= cutoff)
    out = []
    for org_id, emp_id, n, oldest in q.group_by(
            AIConversationThread.organization_id,
            AIConversationThread.employee_id).limit(limit).all():
        out.append(Signal(
            kind=C.A_STALE_FOLLOWUP, organization_id=str(org_id),
            employee_id=emp_id,
            dedup_key="stale:no-followup:%s" % (emp_id or "unassigned"),
            title="%d conversation%s waiting with no follow-up planned"
                  % (int(n), "" if n == 1 else "s"),
            why=("The last message went out %s and no follow-up is "
                 "scheduled." % _ago(oldest, now)),
            severity=C.SEV_NORMAL, source_kind="ai_conversation_threads",
            count=int(n), oldest_at=oldest,
            recommended_action=("Decide whether these should be followed up, "
                               "handed to a person, or closed."),
            evidence={"conversations": int(n),
                      "oldest_outbound_at": _iso(oldest),
                      "threshold_hours":
                          int(th["waiting_without_followup_hours"])},
            drilldown={"view": "conversations", "employee_id": emp_id,
                       "state": O.WAITING_FOR_RESPONSE}))
    return out


def _overdue_scheduled(db, scope, th, now, limit) -> List[Signal]:
    """A follow-up whose time has passed and which has not run."""
    cutoff = now - timedelta(minutes=int(th["overdue_followup_minutes"]))
    q = scope.apply(
        db.query(AIScheduledAction.organization_id,
                 AIScheduledAction.employee_id,
                 func.count(AIScheduledAction.id),
                 func.min(AIScheduledAction.scheduled_for)),
        AIScheduledAction.organization_id).filter(
            AIScheduledAction.status == "pending",
            AIScheduledAction.scheduled_for <= cutoff)
    out = []
    for org_id, emp_id, n, oldest in q.group_by(
            AIScheduledAction.organization_id,
            AIScheduledAction.employee_id).limit(limit).all():
        out.append(Signal(
            kind=C.A_STALE_FOLLOWUP, organization_id=str(org_id),
            employee_id=emp_id,
            dedup_key="stale:overdue-scheduled:%s" % (emp_id or "unassigned"),
            title="%d scheduled follow-up%s did not run"
                  % (int(n), "" if n == 1 else "s"),
            why=("The earliest was due %s and is still pending."
                 % _ago(oldest, now)),
            severity=C.SEV_HIGH, source_kind="ai_scheduled_actions",
            count=int(n), oldest_at=oldest,
            recommended_action=("Check whether the follow-up worker is "
                                "running for this workspace."),
            evidence={"actions": int(n), "earliest_due_at": _iso(oldest)},
            drilldown={"view": "scheduled", "employee_id": emp_id}))
    return out


def _retry_without_progress(db, scope, th, now, limit) -> List[Signal]:
    """The same action attempted again and again, with the state unchanged."""
    attempts = int(th["retry_without_progress"])
    q = scope.apply(
        db.query(AIScheduledAction.organization_id,
                 AIScheduledAction.employee_id,
                 AIScheduledAction.operation,
                 func.count(AIScheduledAction.id),
                 func.max(AIScheduledAction.attempt)),
        AIScheduledAction.organization_id).filter(
            AIScheduledAction.status.in_(("pending", "claimed")),
            AIScheduledAction.attempt >= attempts)
    out = []
    for org_id, emp_id, operation, n, worst in q.group_by(
            AIScheduledAction.organization_id, AIScheduledAction.employee_id,
            AIScheduledAction.operation).limit(limit).all():
        out.append(Signal(
            kind=C.A_RETRY_LOOP, organization_id=str(org_id),
            employee_id=emp_id,
            dedup_key="retry:%s:%s" % (emp_id or "unassigned",
                                       operation or "unknown"),
            title="'%s' is retrying without succeeding" % (operation or "an "
                                                           "action"),
            why=("%d action%s have reached %d attempts without the work "
                 "moving on." % (int(n), "" if n == 1 else "s", int(worst))),
            severity=C.SEV_HIGH, source_kind="ai_scheduled_actions",
            count=int(n),
            recommended_action=("Look at the last failure on one of these "
                                "actions before letting it retry again."),
            evidence={"actions": int(n), "max_attempt": int(worst),
                      "operation": operation,
                      "threshold_attempts": attempts},
            drilldown={"view": "scheduled", "employee_id": emp_id,
                       "operation": operation}))
    return out


def _repeated_failures(db, scope, th, now, limit) -> List[Signal]:
    """One record failing over and over for the same employee."""
    streak = int(th["failure_streak"])
    q = scope.apply(
        db.query(AIWorkItem.organization_id, AIWorkItem.employee_id,
                 func.count(AIWorkItem.id),
                 func.max(AIWorkItem.consecutive_failures)),
        AIWorkItem.organization_id).filter(
            AIWorkItem.consecutive_failures >= streak,
            AIWorkItem.terminal_at.is_(None))
    out = []
    for org_id, emp_id, n, worst in q.group_by(
            AIWorkItem.organization_id, AIWorkItem.employee_id
    ).limit(limit).all():
        out.append(Signal(
            kind=C.A_REPEATED_FAILURE, organization_id=str(org_id),
            employee_id=emp_id,
            dedup_key="repeat-fail:%s" % (emp_id or "unassigned"),
            title="%d record%s failing repeatedly" % (int(n),
                                                      "" if n == 1 else "s"),
            why=("The worst has failed %d times in a row. Repeated failure on "
                 "the same record is usually one cause, not many."
                 % int(worst)),
            severity=C.SEV_CRITICAL, source_kind="ai_work_items", count=int(n),
            recommended_action=("Review the most recent tool failures for "
                                "this employee, and pause it if the cause is "
                                "not obvious."),
            evidence={"records": int(n), "worst_streak": int(worst),
                      "threshold": streak},
            drilldown={"view": "work", "employee_id": emp_id,
                       "filter": "failing"}))
    return out


def _provider_failures(db, scope, th, now, limit) -> List[Signal]:
    """A channel that keeps refusing, timing out or erroring.

    THIS IS THE ROOT THAT SWALLOWS THE OTHERS. Section 2's "do not create 50
    alerts from one root problem" is mostly this condition: a provider going
    down stalls every objective that needed it, and forty stalled-work items
    with no explanation is a worse screen than one item saying which channel
    stopped working.
    """
    threshold = int(th["provider_failure_24h"])
    since = now - timedelta(hours=24)
    q = scope.apply(
        db.query(AICommunication.organization_id, AICommunication.employee_id,
                 AICommunication.channel, AICommunication.provider,
                 AICommunication.provider_outcome,
                 func.count(AICommunication.id),
                 func.max(AICommunication.created_at)),
        AICommunication.organization_id).filter(
            AICommunication.created_at >= since,
            AICommunication.provider_outcome.in_(
                (O.P_FAILED, O.P_TIMEOUT, O.P_REJECTED)))
    buckets: Dict[Any, Dict] = {}
    for (org_id, emp_id, channel, provider, outcome, n, last) in q.group_by(
            AICommunication.organization_id, AICommunication.employee_id,
            AICommunication.channel, AICommunication.provider,
            AICommunication.provider_outcome).limit(limit * 4).all():
        key = (str(org_id), emp_id or "", channel or "unknown",
               provider or "unknown")
        slot = buckets.setdefault(key, {"count": 0, "outcomes": {},
                                        "last": None})
        slot["count"] += int(n or 0)
        slot["outcomes"][outcome or "unknown"] = int(n or 0)
        if last and (slot["last"] is None or last > slot["last"]):
            slot["last"] = last
    out = []
    for (org_id, emp_id, channel, provider), slot in buckets.items():
        if slot["count"] < threshold:
            continue
        out.append(Signal(
            kind=C.A_PROVIDER_FAILURE, organization_id=org_id,
            employee_id=emp_id or None,
            dedup_key="provider:%s:%s:%s" % (emp_id or "any", channel,
                                             provider),
            title="%s is failing" % (channel.upper() if channel != "unknown"
                                     else "A channel"),
            why=("%d %s message%s failed, timed out or were rejected in the "
                 "last 24 hours. The most recent was %s."
                 % (slot["count"], channel,
                    "" if slot["count"] == 1 else "s",
                    _ago(slot["last"], now))),
            severity=C.SEV_CRITICAL, source_kind="ai_communications",
            count=slot["count"], oldest_at=slot["last"],
            recommended_action=("Check this channel's provider configuration "
                                "and credentials before anything else on this "
                                "list."),
            evidence={"channel": channel, "provider": provider,
                      "failures_24h": slot["count"],
                      "by_outcome": slot["outcomes"],
                      "threshold": threshold},
            drilldown={"view": "communications", "employee_id": emp_id or None,
                       "channel": channel}))
    return out


def _unaccepted_handoffs(db, scope, th, now, limit) -> List[Signal]:
    """A person was asked for and nobody has come.

    TWO SIGNALS FROM ONE TABLE, because they have different remedies. A
    handoff nobody has ACCEPTED needs somebody to pick it up; a handoff with
    nowhere to GO needs the employee's routing fixed, and telling a manager to
    "pick this up" when there is no queue to pick it up from is advice they
    cannot act on.
    """
    cutoff = now - timedelta(hours=int(th["handoff_unaccepted_hours"]))
    rows = (scope.apply(db.query(AIHandoff), AIHandoff.organization_id)
            .filter(AIHandoff.status == "open",
                    AIHandoff.created_at <= cutoff)
            .order_by(AIHandoff.created_at.asc()).limit(limit).all())
    out: List[Signal] = []
    for row in rows:
        unrouted = (row.assigned_to_user_id is None
                    and not (row.assigned_queue or "").strip())
        kind = C.A_HANDOFF_UNROUTED if unrouted else C.A_HANDOFF_WAITING
        urgent = (row.priority or "") in ("urgent", "high")
        out.append(Signal(
            kind=kind, organization_id=str(row.organization_id),
            employee_id=row.employee_id,
            dedup_key="handoff:%s:%s" % ("unrouted" if unrouted else "waiting",
                                         row.id),
            title=("A handoff has nobody to go to" if unrouted
                   else "A handoff is waiting to be picked up"),
            why=("Raised %s for '%s'. %s"
                 % (_ago(row.created_at, now), row.reason_code or "a handoff",
                    "No person or team is assigned to it." if unrouted
                    else "Nobody has accepted it yet.")),
            severity=(C.SEV_CRITICAL if unrouted or urgent else C.SEV_HIGH),
            subject_type=row.subject_type, subject_id=row.subject_id,
            source_kind="ai_handoffs", source_id=row.id,
            oldest_at=row.created_at,
            recommended_action=("Set a handoff destination for this employee."
                                if unrouted else "Accept it, or reassign it."),
            evidence={"reason_code": row.reason_code,
                      "priority": row.priority,
                      "raised_at": _iso(row.created_at),
                      "threshold_hours":
                          int(th["handoff_unaccepted_hours"])},
            drilldown={"view": "handoffs", "handoff_id": row.id}))
    return out


def _customer_waiting_for_human(db, scope, th, now, limit) -> List[Signal]:
    """A conversation where the AI has stopped and a person has not started."""
    rows = (scope.apply(db.query(AIConversationThread),
                        AIConversationThread.organization_id)
            .filter(AIConversationThread.status == "open",
                    AIConversationThread.state == O.HANDOFF_REQUIRED,
                    AIConversationThread.human_owner_user_id.is_(None))
            .order_by(AIConversationThread.updated_at.asc())
            .limit(limit).all())
    return [Signal(
        kind=C.A_CUSTOMER_WAITING, organization_id=str(t.organization_id),
        employee_id=t.employee_id,
        dedup_key="waiting-human:%s" % t.id,
        title="Someone is waiting for a person",
        why=("This conversation needs a person and nobody owns it. It has "
             "been waiting since %s." % _ago(t.updated_at, now)),
        severity=C.SEV_CRITICAL, subject_type=t.subject_type,
        subject_id=t.subject_id, objective_ref=t.id,
        source_kind="ai_conversation_threads", source_id=t.id,
        oldest_at=t.updated_at,
        recommended_action="Take this conversation over, or assign it.",
        evidence={"state": t.state, "reason": t.state_reason,
                  "waiting_since": _iso(t.updated_at)},
        drilldown={"view": "conversation", "thread_id": t.id})
        for t in rows]


def _blocked_too_long(db, scope, th, now, limit) -> List[Signal]:
    """Conversations sitting in blocked, failed or review for too long."""
    cutoff = now - timedelta(hours=int(th["blocked_hours"]))
    q = scope.apply(
        db.query(AIConversationThread.organization_id,
                 AIConversationThread.employee_id,
                 AIConversationThread.state,
                 AIConversationThread.state_reason,
                 func.count(AIConversationThread.id),
                 func.min(AIConversationThread.updated_at)),
        AIConversationThread.organization_id).filter(
            AIConversationThread.status == "open",
            AIConversationThread.state.in_((O.BLOCKED, O.FAILED)),
            AIConversationThread.updated_at <= cutoff)
    out = []
    for org_id, emp_id, state, reason, n, oldest in q.group_by(
            AIConversationThread.organization_id,
            AIConversationThread.employee_id, AIConversationThread.state,
            AIConversationThread.state_reason).limit(limit).all():
        out.append(Signal(
            kind=C.A_OBJECTIVE_BLOCKED, organization_id=str(org_id),
            employee_id=emp_id,
            dedup_key="blocked:%s:%s:%s" % (emp_id or "unassigned", state,
                                            reason or "no-reason"),
            title="%d conversation%s blocked" % (int(n),
                                                 "" if n == 1 else "s"),
            why=("Blocked as '%s'%s. The oldest has been blocked since %s."
                 % (state, (" - %s" % reason) if reason else "",
                    _ago(oldest, now))),
            severity=C.SEV_HIGH, source_kind="ai_conversation_threads",
            count=int(n), oldest_at=oldest,
            recommended_action=("Fix the cause named here, or close these "
                                "conversations."),
            evidence={"conversations": int(n), "state": state,
                      "reason": reason, "oldest_at": _iso(oldest),
                      "threshold_hours": int(th["blocked_hours"])},
            drilldown={"view": "conversations", "employee_id": emp_id,
                       "state": state}))
    return out


def _review_required(db, scope, th, now, limit) -> List[Signal]:
    """Work and conversations a person has to look at.

    COMPLIANCE IS SEPARATED FROM ORDINARY REVIEW. To the engine both are
    "review required"; to a manager one of them is a legal question and the
    other is an ambiguous reply, and mixing them means the legal one is
    halfway down a list of thirty.
    """
    out: List[Signal] = []
    wq = scope.apply(
        db.query(AIWorkItem.organization_id, AIWorkItem.employee_id,
                 func.count(AIWorkItem.id), func.min(AIWorkItem.updated_at)),
        AIWorkItem.organization_id).filter(
            AIWorkItem.state == W.NEEDS_REVIEW)
    for org_id, emp_id, n, oldest in wq.group_by(
            AIWorkItem.organization_id, AIWorkItem.employee_id
    ).limit(limit).all():
        out.append(Signal(
            kind=C.A_WORK_REVIEW, organization_id=str(org_id),
            employee_id=emp_id,
            dedup_key="review:work:%s" % (emp_id or "unassigned"),
            title="%d record%s waiting for review" % (int(n),
                                                      "" if n == 1 else "s"),
            why=("The oldest has been waiting since %s." % _ago(oldest, now)),
            severity=C.SEV_HIGH, source_kind="ai_work_items", count=int(n),
            oldest_at=oldest,
            recommended_action="Open the review queue and clear these.",
            evidence={"records": int(n), "oldest_at": _iso(oldest)},
            drilldown={"view": "review", "employee_id": emp_id,
                       "source": C.REVIEW_SOURCE_WORK_ITEM}))

    tq = scope.apply(
        db.query(AIConversationThread.organization_id,
                 AIConversationThread.employee_id,
                 AIConversationThread.state_reason,
                 func.count(AIConversationThread.id),
                 func.min(AIConversationThread.updated_at)),
        AIConversationThread.organization_id).filter(
            AIConversationThread.status == "open",
            AIConversationThread.state == O.REVIEW_REQUIRED)
    # The refusal reasons that are a COMPLIANCE question rather than an
    # ordinary ambiguity. Named from T7's own eligibility vocabulary so a
    # renamed reason does not silently downgrade a legal review to a normal
    # one.
    compliance_reasons = {O.E_CONSENT_UNKNOWN, O.E_POLICY_REVIEW,
                          O.E_PLATFORM_REFUSED, O.E_DNC, O.E_OPTED_OUT}
    for org_id, emp_id, reason, n, oldest in tq.group_by(
            AIConversationThread.organization_id,
            AIConversationThread.employee_id,
            AIConversationThread.state_reason).limit(limit).all():
        is_compliance = (reason or "") in compliance_reasons
        out.append(Signal(
            kind=(C.A_COMPLIANCE_REVIEW if is_compliance else C.A_WORK_REVIEW),
            organization_id=str(org_id), employee_id=emp_id,
            dedup_key="review:conversation:%s:%s" % (emp_id or "unassigned",
                                                     reason or "unspecified"),
            title=("%d conversation%s need a compliance decision" if is_compliance
                   else "%d conversation%s waiting for review")
                  % (int(n), "" if n == 1 else "s"),
            why=("Held as '%s'. The oldest has been waiting since %s."
                 % (reason or "review required", _ago(oldest, now))),
            severity=(C.SEV_CRITICAL if is_compliance else C.SEV_HIGH),
            source_kind="ai_conversation_threads", count=int(n),
            oldest_at=oldest,
            recommended_action=("Decide these yourself - the engine will not "
                                "proceed without a person."),
            evidence={"conversations": int(n), "reason": reason,
                      "oldest_at": _iso(oldest),
                      "compliance": is_compliance},
            drilldown={"view": "review", "employee_id": emp_id,
                       "source": C.REVIEW_SOURCE_THREAD, "reason": reason}))
    return out


def _appointment_problems(db, scope, th, now, limit) -> List[Signal]:
    """A conversation that booked something, where the booking did not stick."""
    q = (db.query(AIConversationThread.organization_id,
                  AIConversationThread.employee_id,
                  AIConversationThread.id, AIConversationThread.subject_id,
                  BookingLink.status, BookingLink.id,
                  AIConversationThread.updated_at)
         .select_from(AIConversationThread)
         .join(BookingLink,
               BookingLink.id == AIConversationThread.appointment_ref)
         .join(Lead, Lead.id == BookingLink.lead_id)
         .filter(AIConversationThread.appointment_ref.isnot(None),
                 Lead.organization_id == AIConversationThread.organization_id,
                 BookingLink.status.in_(("pending", "expired", "cancelled"))))
    q = scope.apply(q, AIConversationThread.organization_id)
    out = []
    for (org_id, emp_id, thread_id, subject_id, status, booking_id,
         updated) in q.order_by(
             AIConversationThread.updated_at.desc()).limit(limit).all():
        out.append(Signal(
            kind=C.A_APPOINTMENT_PROBLEM, organization_id=str(org_id),
            employee_id=emp_id, subject_type="lead", subject_id=subject_id,
            objective_ref=thread_id,
            dedup_key="appointment:%s" % booking_id,
            title="An appointment did not complete",
            why=("The conversation recorded an appointment but the booking is "
                 "'%s'. Last activity %s." % (status, _ago(updated, now))),
            severity=(C.SEV_HIGH if status != "cancelled" else C.SEV_NORMAL),
            source_kind="booking_links", source_id=booking_id,
            oldest_at=updated,
            recommended_action=("Confirm with the person whether the "
                                "appointment stands."),
            evidence={"booking_status": status, "booking_id": booking_id},
            drilldown={"view": "conversation", "thread_id": thread_id}))
    return out


def _deployment_conditions(db, scope, th, now, limit) -> List[Signal]:
    """Everything the DEPLOYMENT record knows that the engine does not.

    Four conditions from one scan, because they share a row and splitting them
    would mean four queries over the same table:

        suspended            - somebody or something stopped this employee
        paused with work     - it is off and its queue is not empty
        entitlement mismatch - it is live and the commercial state is not
        readiness regression - it is live and no longer passes its own checks

    THE LAST TWO ARE NOT REPAIRED HERE, and section 9 and section 19 both say
    why: changing an entitlement or a readiness verdict from a management
    screen is exactly the second control plane the mission forbids.
    """
    deployments = collect.deployments(db, scope, include_retired=False,
                                      limit=limit)
    if not deployments:
        return []
    emp_ids = [d.employee_id for d in deployments if d.employee_id]
    open_work: Dict[str, int] = {}
    if emp_ids:
        wq = scope.apply(
            db.query(AIWorkItem.employee_id, func.count(AIWorkItem.id)),
            AIWorkItem.organization_id).filter(
                AIWorkItem.employee_id.in_(emp_ids),
                AIWorkItem.terminal_at.is_(None),
                ~AIWorkItem.state.in_(list(W.TERMINAL_STATES)))
        open_work = {emp: int(n or 0)
                     for emp, n in wq.group_by(AIWorkItem.employee_id).all()}

    out: List[Signal] = []
    for dep in deployments:
        waiting = open_work.get(dep.employee_id or "", 0)
        name = dep.display_name or "An AI employee"
        if dep.state == C.DEPLOY_SUSPENDED:
            out.append(Signal(
                kind=C.A_EMPLOYEE_SUSPENDED,
                organization_id=str(dep.organization_id),
                employee_id=dep.employee_id, deployment_id=dep.id,
                dedup_key="suspended:%s" % dep.id,
                title="%s has been stopped" % name,
                why=(dep.suspend_reason
                     or "This employee is suspended and is not working."),
                severity=C.SEV_CRITICAL, source_kind="ai_employee_deployments",
                source_id=dep.id, count=max(1, waiting),
                oldest_at=dep.suspended_at,
                recommended_action=("Resolve the account standing behind the "
                                    "suspension, then restart the employee."),
                evidence={"state": dep.state,
                          "suspend_reason": dep.suspend_reason,
                          "commercial_state": dep.commercial_state,
                          "work_waiting": waiting},
                drilldown={"view": "employee",
                           "deployment_id": dep.id}))
        elif dep.state == C.DEPLOY_PAUSED and waiting:
            out.append(Signal(
                kind=C.A_EMPLOYEE_PAUSED_WITH_WORK,
                organization_id=str(dep.organization_id),
                employee_id=dep.employee_id, deployment_id=dep.id,
                dedup_key="paused-with-work:%s" % dep.id,
                title="%s is paused and has %d record%s waiting"
                      % (name, waiting, "" if waiting == 1 else "s"),
                why=("Paused %s. Nothing in its queue will move until it is "
                     "resumed." % _ago(dep.paused_at, now)),
                severity=C.SEV_NORMAL, source_kind="ai_employee_deployments",
                source_id=dep.id, count=waiting, oldest_at=dep.paused_at,
                recommended_action="Resume it, or reassign its work.",
                evidence={"state": dep.state, "work_waiting": waiting,
                          "paused_at": _iso(dep.paused_at)},
                drilldown={"view": "employee", "deployment_id": dep.id}))

        if dep.state in C.DEPLOY_LIVE_STATES:
            if dep.commercial_state not in C.DEPLOY_COMMERCIALLY_LIVE:
                out.append(Signal(
                    kind=C.A_ENTITLEMENT_MISMATCH,
                    organization_id=str(dep.organization_id),
                    employee_id=dep.employee_id, deployment_id=dep.id,
                    dedup_key="entitlement:%s" % dep.id,
                    title="%s is working without a live entitlement" % name,
                    why=("Its operational state is '%s' and its commercial "
                         "state is '%s'. These should not disagree."
                         % (dep.state, dep.commercial_state)),
                    severity=C.SEV_CRITICAL,
                    source_kind="ai_employee_deployments", source_id=dep.id,
                    count=max(1, waiting),
                    recommended_action=("Check this customer's subscription "
                                        "and add-ons. Nothing here changes "
                                        "entitlement."),
                    evidence={"state": dep.state,
                              "commercial_state": dep.commercial_state,
                              "commercial_detail": dep.commercial_detail,
                              "checked_at": _iso(dep.commercial_checked_at)},
                    drilldown={"view": "employee", "deployment_id": dep.id}))
            if dep.readiness_state in (C.READINESS_NOT_READY,
                                       C.READINESS_REVIEW):
                out.append(Signal(
                    kind=C.A_READINESS_REGRESSION,
                    organization_id=str(dep.organization_id),
                    employee_id=dep.employee_id, deployment_id=dep.id,
                    dedup_key="readiness:%s" % dep.id,
                    title="%s no longer passes its own checks" % name,
                    why=("It is live, and its last readiness check came back "
                         "'%s'." % dep.readiness_state),
                    severity=C.SEV_HIGH,
                    source_kind="ai_employee_deployments", source_id=dep.id,
                    oldest_at=dep.readiness_checked_at,
                    recommended_action=("Open its setup and fix the checks "
                                        "that are failing."),
                    evidence={"readiness_state": dep.readiness_state,
                              "checked_at": _iso(dep.readiness_checked_at)},
                    drilldown={"view": "employee", "deployment_id": dep.id}))
        elif (dep.employee_id is None
              and dep.state not in (C.DEPLOY_RETIRED,)
              and dep.readiness_state == C.READINESS_NOT_READY):
            out.append(Signal(
                kind=C.A_EMPLOYEE_CONFIG,
                organization_id=str(dep.organization_id),
                deployment_id=dep.id,
                dedup_key="config:%s" % dep.id,
                title="%s is not set up yet" % name,
                why="It has been selected but is not ready to start working.",
                severity=C.SEV_NORMAL, source_kind="ai_employee_deployments",
                source_id=dep.id,
                recommended_action="Finish its setup.",
                evidence={"state": dep.state,
                          "readiness_state": dep.readiness_state},
                drilldown={"view": "employee", "deployment_id": dep.id}))
    return out


def _unrouted_inbound(db, scope, th, now, limit) -> List[Signal]:
    """Replies that arrived and could not be matched to a conversation."""
    since = now - timedelta(hours=24)
    rows = collect.unrouted_inbound(db, scope, since, limit=limit)
    if not rows:
        return []
    by_org: Dict[str, List] = {}
    for row in rows:
        by_org.setdefault(str(row.organization_id), []).append(row)
    out = []
    for org_id, group in by_org.items():
        oldest = min((r.created_at for r in group if r.created_at),
                     default=None)
        out.append(Signal(
            kind=C.A_INBOUND_UNROUTABLE, organization_id=org_id,
            dedup_key="inbound-unrouted:%s" % org_id,
            title="%d repl%s could not be matched to anyone"
                  % (len(group), "y" if len(group) == 1 else "ies"),
            why=("These arrived in the last 24 hours and no conversation, "
                 "record or person could be found for them. The earliest was "
                 "%s." % _ago(oldest, now)),
            severity=C.SEV_HIGH, source_kind="ai_inbound_events",
            count=len(group), oldest_at=oldest,
            recommended_action=("Look at these directly - somebody replied "
                                "and nobody saw it."),
            evidence={"events": len(group),
                      "reasons": sorted({r.route_reason or "unknown"
                                         for r in group})},
            drilldown={"view": "inbound"}))
    return out


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


_DETECTORS = (
    _provider_failures,          # the root that swallows several others
    _deployment_conditions,
    _customer_waiting_for_human,
    _unaccepted_handoffs,
    _review_required,
    _repeated_failures,
    _retry_without_progress,
    _no_permissible_next_action,
    _stalled_work,
    _blocked_too_long,
    _waiting_without_followup,
    _overdue_scheduled,
    _appointment_problems,
    _unrouted_inbound,
)
