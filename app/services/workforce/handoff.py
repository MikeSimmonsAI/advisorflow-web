"""HANDOFF — giving the work to a person, with everything they need.

THE REQUIREMENT, IN ONE SENTENCE FROM SECTION 17: "Do not make the human reread
a 40-message transcript to understand what happened."

So a handoff is not a flag on a record and it is not "the AI gave up". It is a
structured briefing: why, what is known, what is still unknown, what the
recommended next step is, and who it is for. Those are COLUMNS rather than
prose so the queue screen can show them without parsing, and so a test can
assert that a handoff arrived carrying a next action rather than a shrug.

WHO IT GOES TO, in order:
    1. the employee's configured handoff user
    2. the record's assigned advisor
    3. the named queue, if the customer configured one
    4. nobody — and the handoff is still created, marked unassigned, and a
       supervisor event is raised.

The fourth case matters. An unroutable handoff must never be a reason NOT to
hand off: the alternative is an AI employee continuing to work a conversation
that it has already decided needs a person, which is the worst of both.

THE CONVERSATION TRAVELS WITH IT. `conversation_ref` points at the lead, and
the summary is written by the employee at the moment it decides to escalate —
when it still has the context — rather than reconstructed later by whoever
opens the queue.
"""

import json
import logging
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.models import BookingLink, Lead, User
from app.models.workforce_models import AIEmployee, AIHandoff, AIWorkItem
from app.services.workforce import constants as C

_log = logging.getLogger(__name__)

PRIORITIES = ("low", "normal", "high", "urgent")

# Reasons that are always urgent whatever the employee said. A model asking for
# "normal" priority on a complaint is a model being polite about the wrong
# thing.
ALWAYS_URGENT = frozenset({"complaint", "legal_or_compliance", "hostile",
                           "human_requested"})


def _route(db: Session, employee: AIEmployee,
           lead: Optional[Lead]) -> Dict[str, Optional[str]]:
    """Resolve the destination, checking tenancy at every step."""
    org_id = str(employee.organization_id)

    for uid in (employee.handoff_user_id,
                getattr(lead, "assigned_to_id", None)):
        if not uid:
            continue
        user = db.query(User).filter(User.id == uid).first()
        if user is None or not user.is_active:
            continue
        if str(user.organization_id) != org_id:
            # A handoff routed into another tenant would put one customer's
            # conversation in another customer's queue.
            _log.warning("workforce handoff: refused cross-tenant routing to "
                         "user %s from employee %s", uid, employee.id)
            continue
        return {"assigned_to_user_id": user.id,
                "assigned_queue": None,
                "routed_by": "configured" if uid == employee.handoff_user_id
                else "record_owner"}

    if employee.handoff_queue:
        return {"assigned_to_user_id": None,
                "assigned_queue": employee.handoff_queue,
                "routed_by": "queue"}

    return {"assigned_to_user_id": None, "assigned_queue": None,
            "routed_by": "unrouted"}


def create(db: Session, *, employee: AIEmployee, lead: Optional[Lead],
           work_item: Optional[AIWorkItem], reason_code: str, summary: str,
           recommended_action: Optional[str] = None,
           known_facts: Optional[List[str]] = None,
           open_questions: Optional[List[str]] = None,
           priority: str = "normal", run_id: Optional[str] = None) -> AIHandoff:
    """Create the briefing. Does NOT transition the work item.

    The transition is the caller's, because the state machine has to record who
    caused it and with what claim token — and because a handoff created for a
    record that is not in a work item (an inbound enquiry, say) is legitimate.
    """
    reason_code = (reason_code or "").strip() or "employee_uncertain"
    if reason_code not in C.HANDOFF_REASONS:
        # An unknown reason is still a handoff. Refusing one because the model
        # invented a reason code would strand the conversation.
        _log.info("workforce handoff: unrecognised reason %r recorded as-is",
                  reason_code)
    priority = (priority or "normal").lower()
    if priority not in PRIORITIES:
        priority = "normal"
    if reason_code in ALWAYS_URGENT:
        priority = "urgent"

    routing = _route(db, employee, lead)

    # The appointment, when there is one, so the person picking this up does
    # not have to go looking for it.
    appointment_ref = None
    if lead is not None:
        booking = (db.query(BookingLink)
                   .filter(BookingLink.lead_id == lead.id,
                           BookingLink.status.in_(("booked", "confirmed")))
                   .order_by(BookingLink.booked_time.desc()).first())
        if booking is not None:
            appointment_ref = booking.id

    row = AIHandoff(
        organization_id=employee.organization_id,
        employee_id=employee.id,
        work_item_id=getattr(work_item, "id", None),
        subject_type="lead",
        subject_id=getattr(lead, "id", "") or "",
        reason_code=reason_code,
        priority=priority,
        summary=(summary or "")[:4000],
        known_facts=json.dumps([str(f)[:300] for f in (known_facts or [])][:25]),
        open_questions=json.dumps([str(q)[:300]
                                   for q in (open_questions or [])][:25]),
        recommended_action=(recommended_action or "")[:255] or None,
        conversation_ref=getattr(lead, "id", None),
        appointment_ref=appointment_ref,
        assigned_to_user_id=routing["assigned_to_user_id"],
        assigned_queue=routing["assigned_queue"],
        status="open",
    )
    db.add(row)
    db.flush()

    if routing["routed_by"] == "unrouted":
        # LOUD, because a handoff nobody owns is a customer waiting on nobody.
        from app.services.workforce import supervisor
        supervisor.raise_event(
            db, employee=employee, severity="warning",
            event_code="handoff_unrouted",
            message="A handoff was created with no person or queue to receive it.",
            detail={"handoff_id": row.id, "reason_code": reason_code},
            recommended_action="Set a handoff recipient for this employee.")

    from app.services.workforce import audit as wf_audit
    wf_audit.write_platform_audit(
        db, employee, action="ai_workforce.handoff_created",
        target_type="lead", target_id=getattr(lead, "id", "") or "",
        details={"handoff_id": row.id, "reason_code": reason_code,
                 "priority": priority, "routed_by": routing["routed_by"]},
        run_id=run_id, work_item_id=getattr(work_item, "id", None))
    return row


def accept(db: Session, handoff: AIHandoff, user: User) -> AIHandoff:
    """A person takes it. Tenancy is checked here, not assumed by the caller."""
    if str(handoff.organization_id) != str(user.organization_id) \
            and getattr(user, "role", None) != "god_admin":
        raise PermissionError("That handoff belongs to another organization.")
    handoff.status = "accepted"
    handoff.accepted_by = user.id
    handoff.accepted_at = datetime.utcnow()
    db.flush()
    return handoff


def resolve(db: Session, handoff: AIHandoff, user: User,
            note: Optional[str] = None) -> AIHandoff:
    if str(handoff.organization_id) != str(user.organization_id) \
            and getattr(user, "role", None) != "god_admin":
        raise PermissionError("That handoff belongs to another organization.")
    handoff.status = "resolved"
    handoff.resolved_at = datetime.utcnow()
    handoff.resolution_note = (note or "")[:2000] or None
    if handoff.accepted_by is None:
        handoff.accepted_by = user.id
        handoff.accepted_at = handoff.accepted_at or datetime.utcnow()
    db.flush()
    return handoff


def as_dict(row: AIHandoff) -> Dict:
    def _load(raw):
        try:
            val = json.loads(raw or "[]")
            return val if isinstance(val, list) else []
        except (ValueError, TypeError):
            return []
    return {
        "id": row.id,
        "employee_id": row.employee_id,
        "work_item_id": row.work_item_id,
        "lead_id": row.subject_id,
        "reason_code": row.reason_code,
        "reason_label": C.HANDOFF_REASONS.get(row.reason_code, row.reason_code),
        "priority": row.priority,
        "summary": row.summary,
        "known_facts": _load(row.known_facts),
        "open_questions": _load(row.open_questions),
        "recommended_action": row.recommended_action,
        "appointment_id": row.appointment_ref,
        "assigned_to_user_id": row.assigned_to_user_id,
        "assigned_queue": row.assigned_queue,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "accepted_at": row.accepted_at.isoformat() if row.accepted_at else None,
        "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
    }


def open_for_org(db: Session, organization_id: str, *,
                 employee_id: Optional[str] = None,
                 limit: int = 200) -> List[AIHandoff]:
    q = (db.query(AIHandoff)
         .filter(AIHandoff.organization_id == organization_id,
                 AIHandoff.status.in_(("open", "accepted"))))
    if employee_id:
        q = q.filter(AIHandoff.employee_id == employee_id)
    order = {"urgent": 0, "high": 1, "normal": 2, "low": 3}
    rows = q.order_by(AIHandoff.created_at.desc()).limit(limit).all()
    return sorted(rows, key=lambda r: (order.get(r.priority, 2),
                                       -(r.created_at.timestamp()
                                         if r.created_at else 0)))
