"""HUMAN REVIEW - everything a reviewer needs, and nothing they must not see.

WHAT A REVIEWER IS ACTUALLY DOING. Somewhere in the engine a gate said "a
person has to decide this". The person now has to decide it with less context
than the engine had, from a screen, possibly days later. So the payload here
is built around the eight things section 8 lists - context, timeline,
employee, objective, the relevant communication, the policy decision, why
review is required, and what they are allowed to do - and it is built by
ALLOW-LIST rather than by redaction.

THE ALLOW-LIST IS THE SECURITY BOUNDARY. A payload assembled from named fields
cannot leak a field nobody thought about; a payload assembled from a row with
sensitive keys removed leaks the next sensitive key somebody adds. Section 8
says not to expose hidden model chain-of-thought, and the reason this is done
positively rather than by filtering `reasoning` out is that the filter is a
list that has to be maintained and the allow-list is not.

WHAT IS NOT STORED. The queue is not stored. Items requiring review already
exist authoritatively - a work item in `needs_review`, a thread in
`review_required`, an eligibility verdict of REQUIRES_REVIEW, an unaccepted
handoff - and T9 renders those. What T9 adds is the DECISION, which nothing
else records, and decisions are APPEND-ONLY: a reviewer who changes their mind
adds a row. An audit you can overwrite is a note.

A DECISION IS NOT AN ACTION. Recording "this should be paused" does not pause
anything. Pausing is a management action, it goes through T8's lifecycle with
T8's checks, and it is recorded separately in `ai_management_actions`. Keeping
them apart is what stops a review screen becoming a way to reach into the
engine without passing its gates.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.ai_operations_models import (AICommunication,
                                             AIConversationThread)
from app.models.workforce_intelligence_models import AIReviewDecision
from app.models.workforce_models import (AIEligibilityResult, AIEmployee,
                                         AIHandoff, AIWorkItem,
                                         AIWorkItemEvent)
from app.services.ai_operations import constants as O
from app.services.workforce import constants as W
from app.services.workforce_intelligence import collect
from app.services.workforce_intelligence import constants as C
from app.services.workforce_intelligence.scope import Scope

_log = logging.getLogger(__name__)

# WHAT A REVIEWER MAY DO, per source. An action not in this list is not offered
# and is refused if requested - a screen that offers a button the backend does
# not implement is a support ticket, and a backend that accepts an action the
# screen never offers is a hole.
ALLOWED_ACTIONS = {
    C.REVIEW_SOURCE_WORK_ITEM: (C.RD_APPROVED, C.RD_REJECTED, C.RD_ESCALATED,
                                C.RD_NO_ACTION, C.RD_PAUSE_REQUESTED),
    C.REVIEW_SOURCE_THREAD: (C.RD_APPROVED, C.RD_REJECTED, C.RD_ESCALATED,
                             C.RD_NO_ACTION, C.RD_PAUSE_REQUESTED),
    C.REVIEW_SOURCE_ELIGIBILITY: (C.RD_APPROVED, C.RD_REJECTED,
                                  C.RD_ESCALATED, C.RD_NO_ACTION),
    C.REVIEW_SOURCE_HANDOFF: (C.RD_APPROVED, C.RD_ESCALATED, C.RD_NO_ACTION),
    C.REVIEW_SOURCE_COMMUNICATION: (C.RD_APPROVED, C.RD_REJECTED,
                                    C.RD_NO_ACTION),
    C.REVIEW_SOURCE_SUPERVISOR: (C.RD_NO_ACTION, C.RD_ESCALATED,
                                 C.RD_PAUSE_REQUESTED),
}


def _key(source_kind: str, source_id: str) -> str:
    return "%s:%s" % (source_kind, source_id)


def queue(db: Session, scope: Scope, *, limit: int = 200,
          now: Optional[datetime] = None) -> Dict[str, Any]:
    """Everything waiting for a person, from all four authoritative sources.

    ONE LIST, FOUR SOURCES. To the engine these are four different refusals;
    to a reviewer they are one afternoon's work, and splitting them across
    four screens is how the compliance one gets missed.
    """
    now = now or datetime.utcnow()
    items: List[Dict[str, Any]] = []

    for item in collect.open_work_items(db, scope, states=(W.NEEDS_REVIEW,),
                                        limit=limit):
        items.append({
            "review_key": _key(C.REVIEW_SOURCE_WORK_ITEM, item.id),
            "source_kind": C.REVIEW_SOURCE_WORK_ITEM,
            "source_id": item.id,
            "employee_id": item.employee_id,
            "organization_id": item.organization_id,
            "subject_type": item.subject_type,
            "subject_id": item.subject_id,
            "why_review_is_required": (item.state_reason
                                       or "The engine could not decide this."),
            "waiting_since": (item.updated_at.isoformat()
                              if item.updated_at else None),
            "age_seconds": _age(item.updated_at, now),
            "allowed_actions": list(
                ALLOWED_ACTIONS[C.REVIEW_SOURCE_WORK_ITEM]),
        })

    for thread in collect.threads(db, scope, states=(O.REVIEW_REQUIRED,),
                                  limit=limit):
        items.append({
            "review_key": _key(C.REVIEW_SOURCE_THREAD, thread.id),
            "source_kind": C.REVIEW_SOURCE_THREAD,
            "source_id": thread.id,
            "employee_id": thread.employee_id,
            "organization_id": thread.organization_id,
            "subject_type": thread.subject_type,
            "subject_id": thread.subject_id,
            "why_review_is_required": (thread.state_reason
                                       or "Held for review."),
            "waiting_since": (thread.updated_at.isoformat()
                              if thread.updated_at else None),
            "age_seconds": _age(thread.updated_at, now),
            "allowed_actions": list(ALLOWED_ACTIONS[C.REVIEW_SOURCE_THREAD]),
        })

    eq = scope.apply(db.query(AIEligibilityResult),
                     AIEligibilityResult.organization_id).filter(
        AIEligibilityResult.result == W.REQUIRES_REVIEW,
        AIEligibilityResult.created_at >= now - timedelta(days=30))
    for row in eq.order_by(AIEligibilityResult.created_at.desc()
                           ).limit(limit).all():
        items.append({
            "review_key": _key(C.REVIEW_SOURCE_ELIGIBILITY, row.id),
            "source_kind": C.REVIEW_SOURCE_ELIGIBILITY,
            "source_id": row.id,
            "employee_id": row.employee_id,
            "organization_id": row.organization_id,
            "subject_type": row.subject_type,
            "subject_id": row.subject_id,
            "why_review_is_required": _reasons(row.reasons),
            "waiting_since": (row.created_at.isoformat()
                              if row.created_at else None),
            "age_seconds": _age(row.created_at, now),
            "allowed_actions": list(
                ALLOWED_ACTIONS[C.REVIEW_SOURCE_ELIGIBILITY]),
        })

    for h in collect.handoffs(db, scope, statuses=("open",), limit=limit):
        items.append({
            "review_key": _key(C.REVIEW_SOURCE_HANDOFF, h.id),
            "source_kind": C.REVIEW_SOURCE_HANDOFF,
            "source_id": h.id,
            "employee_id": h.employee_id,
            "organization_id": h.organization_id,
            "subject_type": h.subject_type,
            "subject_id": h.subject_id,
            "why_review_is_required": (h.reason_code
                                       or "A person was asked for."),
            "waiting_since": (h.created_at.isoformat()
                              if h.created_at else None),
            "age_seconds": _age(h.created_at, now),
            "allowed_actions": list(ALLOWED_ACTIONS[C.REVIEW_SOURCE_HANDOFF]),
        })

    decided = _decided_keys(db, scope, [i["review_key"] for i in items])
    for item in items:
        item["already_decided"] = item["review_key"] in decided

    items.sort(key=lambda i: -(i["age_seconds"] or 0))
    return {
        "generated_at": now.isoformat(),
        "items": items[:limit],
        "total": len(items),
        "undecided": sum(1 for i in items if not i["already_decided"]),
        "decision_options": {k: list(v) for k, v in ALLOWED_ACTIONS.items()},
    }


def _age(then: Optional[datetime], now: datetime) -> int:
    if then is None:
        return 0
    return max(0, int((now - then).total_seconds()))


def _reasons(raw: Optional[str]) -> str:
    try:
        parsed = json.loads(raw or "[]")
    except (ValueError, TypeError):
        return "Requires review."
    codes = []
    for entry in parsed if isinstance(parsed, list) else []:
        if isinstance(entry, dict):
            codes.append(entry.get("code") or entry.get("detail") or "")
        else:
            codes.append(str(entry))
    codes = [c for c in codes if c]
    return ", ".join(codes) if codes else "Requires review."


def _decided_keys(db: Session, scope: Scope, keys: List[str]) -> set:
    if not keys:
        return set()
    q = scope.apply(db.query(AIReviewDecision.review_key),
                    AIReviewDecision.organization_id).filter(
        AIReviewDecision.review_key.in_(keys))
    return {row[0] for row in q.all()}


# ---------------------------------------------------------------------------
# ONE ITEM, IN FULL
# ---------------------------------------------------------------------------
#
# THE ALLOW-LISTS. Every field a reviewer sees is named here. Nothing is
# copied wholesale from a row, so a column added to `ai_communications`
# tomorrow does not appear on a review screen by default - which is the
# property that keeps `REVIEW_FORBIDDEN_FIELDS` from ever needing to be
# consulted at runtime.

_COMMUNICATION_FIELDS = ("id", "direction", "channel", "state",
                         "state_reason", "body_preview", "provider",
                         "provider_outcome", "eligibility_result",
                         "denial_code", "simulated", "sent_at",
                         "delivered_at", "responded_at", "created_at")

_TIMELINE_FIELDS = ("from_state", "to_state", "reason", "actor_kind",
                    "created_at")


def detail(db: Session, scope: Scope, source_kind: str,
           source_id: str, *, now: Optional[datetime] = None
           ) -> Optional[Dict[str, Any]]:
    """Everything a reviewer needs about one item, assembled by allow-list."""
    now = now or datetime.utcnow()
    if source_kind not in ALLOWED_ACTIONS:
        return None

    context: Dict[str, Any] = {}
    thread = None
    work_item = None
    employee_id = None
    organization_id = None

    if source_kind == C.REVIEW_SOURCE_WORK_ITEM:
        work_item = (scope.apply(db.query(AIWorkItem),
                                 AIWorkItem.organization_id)
                     .filter(AIWorkItem.id == source_id).first())
        if work_item is None:
            return None
        employee_id = work_item.employee_id
        organization_id = work_item.organization_id
        context = {
            "state": work_item.state, "reason": work_item.state_reason,
            "subject_type": work_item.subject_type,
            "subject_id": work_item.subject_id,
            "touches": int(work_item.touches or 0),
            "attempts": int(work_item.attempts or 0),
            "consecutive_failures": int(work_item.consecutive_failures or 0),
            "eligibility_state": work_item.eligibility_state,
            "eligibility_reasons": _reasons(work_item.eligibility_reasons),
        }
        thread = (scope.apply(db.query(AIConversationThread),
                              AIConversationThread.organization_id)
                  .filter(AIConversationThread.work_item_id == work_item.id)
                  .order_by(AIConversationThread.created_at.desc()).first())
    elif source_kind == C.REVIEW_SOURCE_THREAD:
        thread = (scope.apply(db.query(AIConversationThread),
                              AIConversationThread.organization_id)
                  .filter(AIConversationThread.id == source_id).first())
        if thread is None:
            return None
        employee_id = thread.employee_id
        organization_id = thread.organization_id
    elif source_kind == C.REVIEW_SOURCE_ELIGIBILITY:
        row = (scope.apply(db.query(AIEligibilityResult),
                           AIEligibilityResult.organization_id)
               .filter(AIEligibilityResult.id == source_id).first())
        if row is None:
            return None
        employee_id = row.employee_id
        organization_id = row.organization_id
        context = {"channel": row.channel, "result": row.result,
                   "decided_by": row.decided_by,
                   "policy_version": row.policy_version,
                   "reasons": _reasons(row.reasons),
                   "subject_type": row.subject_type,
                   "subject_id": row.subject_id}
    elif source_kind == C.REVIEW_SOURCE_HANDOFF:
        row = (scope.apply(db.query(AIHandoff), AIHandoff.organization_id)
               .filter(AIHandoff.id == source_id).first())
        if row is None:
            return None
        employee_id = row.employee_id
        organization_id = row.organization_id
        context = {"reason_code": row.reason_code, "priority": row.priority,
                   "summary": row.summary,
                   "recommended_action": row.recommended_action,
                   "assigned_to_user_id": row.assigned_to_user_id,
                   "assigned_queue": row.assigned_queue,
                   "subject_type": row.subject_type,
                   "subject_id": row.subject_id}
        if row.conversation_ref:
            thread = (scope.apply(db.query(AIConversationThread),
                                  AIConversationThread.organization_id)
                      .filter(AIConversationThread.id == row.conversation_ref)
                      .first())
    else:
        return None

    employee = None
    if employee_id:
        employee = (scope.apply(db.query(AIEmployee),
                                AIEmployee.organization_id)
                    .filter(AIEmployee.id == employee_id).first())

    communications: List[Dict[str, Any]] = []
    if thread is not None:
        rows = (scope.apply(db.query(AICommunication),
                            AICommunication.organization_id)
                .filter(AICommunication.thread_id == thread.id)
                .order_by(AICommunication.created_at.asc()).limit(60).all())
        communications = [_pick(r, _COMMUNICATION_FIELDS) for r in rows]

    timeline: List[Dict[str, Any]] = []
    if work_item is not None:
        rows = (db.query(AIWorkItemEvent)
                .filter(AIWorkItemEvent.work_item_id == work_item.id,
                        AIWorkItemEvent.organization_id == organization_id)
                .order_by(AIWorkItemEvent.created_at.asc()).limit(80).all())
        timeline = [_pick(r, _TIMELINE_FIELDS) for r in rows]

    decisions = (scope.apply(db.query(AIReviewDecision),
                             AIReviewDecision.organization_id)
                 .filter(AIReviewDecision.review_key
                         == _key(source_kind, source_id))
                 .order_by(AIReviewDecision.decided_at.desc()).all())

    return {
        "review_key": _key(source_kind, source_id),
        "source_kind": source_kind,
        "source_id": source_id,
        "organization_id": organization_id,
        "employee": ({"id": employee.id, "name": employee.name,
                      "job_role": employee.job_role,
                      "status": employee.status,
                      "activation_state": employee.activation_state}
                     if employee is not None else None),
        "objective": ({"thread_id": thread.id, "objective": thread.objective,
                       "job_key": thread.job_key, "state": thread.state,
                       "state_reason": thread.state_reason,
                       "outbound": int(thread.outbound_count or 0),
                       "inbound": int(thread.inbound_count or 0),
                       "human_owner_user_id": thread.human_owner_user_id}
                      if thread is not None else None),
        "context": context,
        "communications": communications,
        "timeline": timeline,
        "policy_decision": {
            "eligibility": context.get("eligibility_state")
            or context.get("result"),
            "reasons": context.get("eligibility_reasons")
            or context.get("reasons"),
        },
        "why_review_is_required": (context.get("reason")
                                   or context.get("reasons")
                                   or context.get("reason_code")
                                   or "A person has to decide this."),
        "allowed_actions": list(ALLOWED_ACTIONS[source_kind]),
        "previous_decisions": [{
            "decision": d.decision, "note": d.note,
            "decided_by": d.decided_by,
            "decided_at": d.decided_at.isoformat() if d.decided_at else None,
            "effect": d.effect,
        } for d in decisions],
        # NAMED SO THE ABSENCE IS DELIBERATE RATHER THAN OVERLOOKED.
        "model_reasoning": None,
        "model_reasoning_note": (
            "AdvisorFlow does not show a model's internal reasoning. What is "
            "shown above is what the engine recorded and acted on."),
    }


def _pick(row, fields) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for name in fields:
        value = getattr(row, name, None)
        if isinstance(value, datetime):
            value = value.isoformat()
        out[name] = value
    return out


def decide(db: Session, scope: Scope, *, source_kind: str, source_id: str,
           decision: str, user, note: Optional[str] = None,
           effect: Optional[str] = None,
           effect_detail: Optional[Dict] = None) -> Dict[str, Any]:
    """Record what a person decided. Appends; never edits.

    THE TENANT IS RE-RESOLVED FROM THE ITEM, not taken from the request. A
    decision whose organization came from the caller would be a decision
    somebody could file against another customer's record.
    """
    if decision not in ALLOWED_ACTIONS.get(source_kind, ()):
        raise ValueError("That decision is not available for this kind of "
                         "review item.")
    item = detail(db, scope, source_kind, source_id)
    if item is None:
        raise LookupError("That review item is not in this workspace.")
    row = AIReviewDecision(
        organization_id=item["organization_id"],
        employee_id=(item.get("employee") or {}).get("id"),
        review_key=_key(source_kind, source_id),
        source_kind=source_kind, source_id=source_id,
        reason_code=(item.get("context") or {}).get("reason_code"),
        decision=decision, note=(note or None),
        effect=effect,
        effect_detail=(json.dumps(effect_detail, default=str)[:4000]
                       if effect_detail else None),
        decided_by=getattr(user, "id", None),
        decided_at=datetime.utcnow())
    db.add(row)
    db.flush()
    return {
        "recorded": True,
        "decision": decision,
        "review_key": row.review_key,
        "id": row.id,
        # SAYING WHAT A DECISION DID NOT DO IS PART OF THE ANSWER. A reviewer
        # who thinks "pause requested" paused something will not check.
        "effect": effect,
        "note": ("Recorded. A review decision does not change an employee, a "
                 "conversation or an entitlement on its own - those go "
                 "through their own systems and are recorded separately."),
    }
