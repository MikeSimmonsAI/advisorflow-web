"""THE COMMUNICATION STATE MACHINE — deterministic, auditable, and closed.

WHY A STATE MACHINE AND NOT A STATUS COLUMN. A status column is a field
anybody can set to anything; a state machine is a set of edges, and an edge
that does not exist cannot be taken. The difference shows up exactly once, in
production, when something sets `state = "sent"` on a communication that was
BLOCKED and the send actually goes out. `transition()` refuses instead of
logging and continuing, and the refusal is the feature.

EVERY TRANSITION IS RECORDED. `ai_communication_events` gets a row per move
with the actor that caused it, so "why did this message end up in
review_required" is answerable without reconstruction.

THE CLAIM IS CONDITIONAL, WHICH IS WHAT STOPS DOUBLE SENDS. `claim_for_send`
moves READY → SENDING with a WHERE clause on the current state, and reports
whether it won. Two workers holding the same communication both call it; one
gets True and sends, the other gets False and stops. That is a database
guarantee rather than an application convention, which is the only kind that
survives two processes.
"""

import json
import logging
from datetime import datetime
from typing import Dict, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.ai_operations_models import (AICommunication,
                                             AICommunicationEvent,
                                             AIConversationThread)
from app.services.ai_operations import constants as C

_log = logging.getLogger(__name__)


class IllegalTransition(Exception):
    """A transition that is not in the table. Raised, never swallowed."""

    def __init__(self, from_state: str, to_state: str):
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(
            "A communication cannot move from %r to %r. The legal moves from "
            "%r are: %s." % (from_state, to_state, from_state,
                             ", ".join(sorted(
                                 C.ALLOWED_COMM_TRANSITIONS.get(
                                     from_state, set()))) or "none — it is "
                             "terminal"))


def may_transition(from_state: str, to_state: str) -> bool:
    if from_state == to_state:
        # A no-op move is allowed and does nothing. Providers send the same
        # status twice; that is not an illegal transition, it is a repeat.
        return True
    return to_state in C.ALLOWED_COMM_TRANSITIONS.get(from_state or "", set())


def is_terminal(state: str) -> bool:
    return state in C.TERMINAL_COMM_STATES


def transition(db: Session, comm: AICommunication, to_state: str, *,
               reason: Optional[str] = None,
               actor_kind: str = C.ACTOR_AI_EMPLOYEE,
               actor_id: Optional[str] = None,
               detail: Optional[Dict] = None,
               strict: bool = True) -> bool:
    """Move one communication. Returns whether it moved.

    `strict=True` raises IllegalTransition on an edge that does not exist —
    the right behaviour for engine code, where an illegal move is a defect.
    `strict=False` returns False instead, for the paths where an out-of-order
    PROVIDER event is expected: a "delivered" callback arriving after the
    conversation was already stopped is not a defect in this system, it is
    ordinary network reality, and it must not raise.
    """
    from_state = comm.state or C.QUEUED
    if from_state == to_state:
        return False
    if not may_transition(from_state, to_state):
        if strict:
            raise IllegalTransition(from_state, to_state)
        _log.info("ai_operations: ignoring out-of-order transition %s -> %s "
                  "on communication %s", from_state, to_state, comm.id)
        return False

    comm.state = to_state
    comm.state_reason = reason
    now = datetime.utcnow()
    comm.updated_at = now
    if to_state == C.SENT and comm.sent_at is None:
        comm.sent_at = now
    if to_state == C.DELIVERED and comm.delivered_at is None:
        comm.delivered_at = now
    if to_state == C.RESPONSE_RECEIVED and comm.responded_at is None:
        comm.responded_at = now

    db.add(AICommunicationEvent(
        communication_id=comm.id, organization_id=comm.organization_id,
        from_state=from_state, to_state=to_state, reason=reason,
        actor_kind=actor_kind, actor_id=actor_id,
        detail=json.dumps(detail or {}) if detail else None))
    db.flush()
    return True


def claim_for_send(db: Session, comm: AICommunication) -> bool:
    """READY → SENDING, conditionally. True means this worker owns the send.

    THE UPDATE IS THE LOCK. The WHERE clause names the state the row must
    still be in, so the second worker's UPDATE matches zero rows and it
    learns it lost — without a SELECT-then-UPDATE gap for it to lose in.
    """
    updated = (db.query(AICommunication)
               .filter(AICommunication.id == comm.id,
                       AICommunication.state == C.READY)
               .update({AICommunication.state: C.SENDING,
                        AICommunication.updated_at: datetime.utcnow()},
                       synchronize_session=False))
    db.flush()
    if not updated:
        db.refresh(comm)
        _log.info("ai_operations: send claim lost for communication %s "
                  "(state is now %s)", comm.id, comm.state)
        return False
    db.refresh(comm)
    db.add(AICommunicationEvent(
        communication_id=comm.id, organization_id=comm.organization_id,
        from_state=C.READY, to_state=C.SENDING, reason="claimed by worker",
        actor_kind=C.ACTOR_SYSTEM))
    db.flush()
    return True


def thread_state(db: Session, thread: AIConversationThread, to_state: str, *,
                 reason: Optional[str] = None) -> bool:
    """Move the CONVERSATION's own state, on its own transition table.

    The thread and its communications share a VOCABULARY deliberately — an
    operator filtering the console for "waiting for response" means the same
    thing at both grains — but not a transition table. See the note above
    `ALLOWED_THREAD_TRANSITIONS` for why one table could not serve both.
    """
    from_state = thread.state or C.QUEUED
    if from_state == to_state:
        return False
    if to_state not in C.ALLOWED_THREAD_TRANSITIONS.get(from_state, set()):
        # NOT AN EXCEPTION. A conversation-level move that is not legal is
        # almost always a race — an inbound reply landing while an outbound
        # send is settling — and raising would turn an ordering artefact into
        # a failed operation. It is logged and the state stays where it was,
        # which is the conservative answer.
        _log.info("ai_operations: thread %s cannot move %s -> %s",
                  thread.id, from_state, to_state)
        return False
    thread.state = to_state
    thread.state_reason = reason
    thread.updated_at = datetime.utcnow()
    db.flush()
    return True


def describe(state: str) -> Tuple[str, str]:
    """(group_key, human label) for one state, for the console."""
    for key, label, states in C.COMM_GROUPS:
        if state in states:
            return key, label
    return "other", "Other"
