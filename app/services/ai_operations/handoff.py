"""GIVING THE WORK TO A PERSON, WITH EVERYTHING THEY NEED.

A HANDOFF IS A PACKAGE, NOT A FLAG. "This one needs a human" attached to a
lead id is a request to go and read a forty-message transcript. What a
salesperson actually needs, in the first fifteen seconds, is: who this is,
what they want, what has already been said, what they asked that nobody
answered, whether anything is booked, why it came to them, and what to do
next. Those are columns rather than prose so the queue screen can show them
and a test can assert that a handoff arrived carrying a recommended action.

A HANDOFF IS NOT OWNERSHIP. Creating one asks for a person; it does not
produce one. Until somebody accepts it, the conversation is still the
employee's — which is why an unclaimed handoff is a supervisor signal rather
than a silent stall. `stop.take_over` is the act that transfers ownership,
and it is the thing the AI stops on.

SENTIMENT AND INTENT ARE REPORTED, NEVER ASSERTED. Anything derived from what
the contact said is labelled as derived from untrusted content. A handoff
that told a salesperson "this customer is angry" as a fact, on the strength
of a classifier, would be a handoff that misleads them before they have read
a word.
"""

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.ai_operations_models import AIConversationThread
from app.services.ai_operations import audit, comm_state, continuity
from app.services.ai_operations import constants as C
from app.services.ai_operations import contracts, orchestrator

_log = logging.getLogger(__name__)


def build_package(db: Session, ctx: contracts.EmployeeContext,
                  thread: AIConversationThread, *, reason_code: str,
                  summary: str, recommended_action: Optional[str] = None,
                  known_facts: Optional[List[str]] = None,
                  open_questions: Optional[List[str]] = None,
                  priority: str = "normal") -> Dict[str, Any]:
    """Everything a person needs, assembled from what actually happened."""
    lead = None
    try:
        from app.models.models import Lead
        lead = (db.query(Lead)
                .filter(Lead.id == thread.subject_id,
                        Lead.organization_id == thread.organization_id)
                .first())
    except Exception as exc:                                 # noqa: BLE001
        _log.info("ai_operations: handoff could not load the contact (%s)",
                  exc)

    history = continuity.history(db, thread, limit=25)
    inbound = [h for h in history if h.get("direction") == C.INBOUND]
    return {
        "thread_id": thread.id,
        "organization_id": thread.organization_id,
        "employee_id": ctx.employee_id,
        "employee_name": ctx.name,
        "work_item_id": thread.work_item_id,
        "subject_type": thread.subject_type,
        "subject_id": thread.subject_id,
        "contact": {
            "name": " ".join([getattr(lead, "first_name", "") or "",
                              getattr(lead, "last_name", "") or ""]).strip()
            or None,
            "tier": getattr(lead, "tier", None),
            "status": getattr(getattr(lead, "status", None), "value",
                              getattr(lead, "status", None)),
            "assigned_to_id": getattr(lead, "assigned_to_id", None),
        } if lead is not None else None,
        "objective": thread.objective,
        "reason_code": reason_code,
        "reason_label": _reason_label(reason_code),
        "priority": priority,
        "summary": summary,
        "known_facts": list(known_facts or []),
        "open_questions": list(open_questions or []),
        "recommended_action": recommended_action,
        "channels_used": continuity.channels_used(thread),
        "message_count": {
            "outbound": int(thread.outbound_count or 0),
            "inbound": int(thread.inbound_count or 0),
        },
        "last_inbound_at": thread.last_inbound_at,
        "last_outbound_at": thread.last_outbound_at,
        "appointment_ref": thread.appointment_ref,
        # REPORTED, NOT KNOWN — see the module header.
        "reported_sentiment": {
            "source": "classification of the contact's own words",
            "trusted": False,
            "latest": (inbound[-1].get("state") if inbound else None),
        },
        "history": history,
        "urgency": ("high" if priority in ("high", "urgent")
                    else "normal"),
    }


def _reason_label(code: str) -> str:
    """The human sentence for a handoff reason.

    Read from T6's catalogue when it is deployed, so the two layers name the
    same reason the same way, with a local copy of the same strings as the
    fallback rather than an invented second vocabulary.
    """
    try:
        from app.services.workforce import constants as wf
        label = (wf.HANDOFF_REASONS or {}).get(code)
        if label:
            return label
    except Exception:                                        # noqa: BLE001
        pass
    return {
        "human_requested": "The person asked to speak to someone",
        "high_value": "High-value opportunity",
        "policy_requires_human": "Policy requires a person for this step",
        "employee_uncertain": "The AI employee was not confident enough",
        "unsupported_request": "Outside this employee's job",
        "complaint": "The person complained",
        "legal_or_compliance": "A legal or compliance concern was raised",
        "hostile": "The conversation became hostile",
        "billing_or_payment": "A payment or billing question",
        "tool_failure": "A tool or provider failed repeatedly",
        "qualification_threshold": "They met the qualification threshold",
        "appointment_requires_human": "The appointment needs a person",
        "manual_takeover": "A person took it over",
    }.get(code, code.replace("_", " ").capitalize())


def transfer_to_human(db: Session, ctx: contracts.EmployeeContext, *,
                      thread: AIConversationThread, reason_code: str,
                      summary: str, recommended_action: Optional[str] = None,
                      known_facts: Optional[List[str]] = None,
                      open_questions: Optional[List[str]] = None,
                      priority: str = "normal",
                      run_id: Optional[str] = None):
    """The operation. Goes through the gate chain like everything else."""
    gate = orchestrator.begin(
        db, ctx, C.OP_TRANSFER_TO_HUMAN, subject_id=thread.subject_id,
        subject_type=thread.subject_type, thread=thread,
        work_item_id=thread.work_item_id, run_id=run_id,
        correlation_kind=C.CORR_HANDOFF,
        arguments={"reason_code": reason_code, "priority": priority},
        requires_eligibility=False)
    if not gate.allowed:
        return orchestrator.OperationResult(ok=False, gate=gate)

    package = build_package(db, ctx, thread, reason_code=reason_code,
                            summary=summary,
                            recommended_action=recommended_action,
                            known_facts=known_facts,
                            open_questions=open_questions, priority=priority)

    handoff_id = contracts.mirror_handoff(
        db, ctx, subject_type=thread.subject_type,
        subject_id=thread.subject_id, reason_code=reason_code,
        summary=summary, known_facts=known_facts,
        open_questions=open_questions,
        recommended_action=recommended_action, priority=priority,
        work_item_id=thread.work_item_id, conversation_ref=thread.id,
        appointment_ref=thread.appointment_ref)

    thread.handoff_ref = handoff_id or thread.handoff_ref or thread.id
    comm_state.thread_state(db, thread, C.HANDOFF_REQUIRED,
                            reason=reason_code)
    thread.next_action_at = None
    db.flush()

    # The AI stops here and waits. It does NOT keep working the record while
    # a handoff is open: competing with the person you just asked for help is
    # the behaviour that makes a workforce untrustworthy.
    from app.services.ai_operations import stop as stop_controls
    stop_controls.cancel_scheduled(db, thread,
                                   reason="handed off to a person")

    contracts.mirror_supervisor_event(
        db, ctx, event_code=C.SUP_HANDOFF_CREATED,
        severity=("warning" if priority in ("high", "urgent") else "info"),
        message="A conversation was handed to a person: %s"
                % _reason_label(reason_code),
        detail={"thread_id": thread.id, "handoff_id": handoff_id,
                "priority": priority},
        recommended_action=recommended_action)
    contracts.mirror_performance(db, ctx, "handoffs_created")

    orchestrator.finish(
        db, gate, status="ok",
        result_summary="Handed to a person: %s" % _reason_label(reason_code),
        outcome="human_handoff", next_action="human",
        detail={"handoff_id": handoff_id,
                "assigned_to_user_id": ctx.handoff_user_id,
                "assigned_queue": ctx.handoff_queue,
                "package_keys": sorted(package.keys())})
    return orchestrator.OperationResult(
        ok=True, gate=gate,
        detail={"handoff_id": handoff_id, "package": package})


def open_handoffs(db: Session, organization_id: str) -> List[Dict[str, Any]]:
    """Conversations waiting for a person, oldest first.

    Read from this layer's threads rather than from T6's handoff table, so
    the console still answers on a deployment where T6 has not merged. When
    T6 IS deployed, `handoff_ref` points at its row and its queue screen is
    the authoritative one.
    """
    rows = (db.query(AIConversationThread)
            .filter(AIConversationThread.organization_id == organization_id,
                    AIConversationThread.state == C.HANDOFF_REQUIRED,
                    AIConversationThread.human_owner_user_id.is_(None))
            .order_by(AIConversationThread.updated_at.asc())
            .all())
    return [{
        "thread_id": t.id,
        "subject_type": t.subject_type,
        "subject_id": t.subject_id,
        "employee_id": t.employee_id,
        "objective": t.objective,
        "handoff_ref": t.handoff_ref,
        "reason": t.state_reason,
        "reason_label": _reason_label(t.state_reason or ""),
        "waiting_since": t.updated_at,
        "last_inbound_at": t.last_inbound_at,
    } for t in rows]
