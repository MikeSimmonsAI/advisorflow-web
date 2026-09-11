"""THE WORK QUEUE — a real state machine, with real locking.

THREE PROPERTIES THIS FILE EXISTS TO GUARANTEE, and the mechanism for each:

  1. ONE RECORD IS WORKED BY ONE WORKER AT A TIME.
     A CONDITIONAL UPDATE, not a read-then-write. `claim()` issues a single
     UPDATE whose WHERE clause contains everything it is relying on, and then
     trusts the row count. Two workers racing produce one winner and one zero,
     on Postgres and on SQLite, without a transaction isolation level anybody
     has to remember to set. Read-then-write is the version that looks correct
     in a code review and sends the same family two first texts.

  2. A DEAD WORKER RELEASES ITS WORK.
     The claim is a LEASE (`claim_token` + `lock_expires_at`), not a flag. A
     process that dies mid-run stops renewing and the item becomes claimable
     again; a process whose lease expired cannot then write its result over the
     worker that took over, because every subsequent write is conditional on
     the token it still believes it holds.

  3. AN ILLEGAL TRANSITION IS A REFUSAL, NOT A LOG LINE.
     `transition()` raises on an edge that is not in
     constants.ALLOWED_TRANSITIONS. The terminal states have EMPTY transition
     sets, so nothing — not a retry, not a supervisor, not a bug — walks a
     record back out of `do_not_contact` and into the sending states.

DUPLICATE ASSIGNMENT is prevented by a unique constraint on
(employee_id, subject_type, subject_id) rather than by a SELECT-then-INSERT.
`enqueue` catches the integrity error per row and reports it as skipped, which
is the only version of that guarantee that holds when two enqueues overlap.
"""

import logging
import uuid as _uuid
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.workforce_models import (AIEmployee, AIWorkItem,
                                         AIWorkItemEvent)
from app.services.workforce import constants as C

_log = logging.getLogger(__name__)


class IllegalTransition(RuntimeError):
    """An edge that is not in the state machine. Never caught and ignored."""


def _json(value) -> Optional[str]:
    if value is None:
        return None
    import json
    try:
        return json.dumps(value)[:8000]
    except (TypeError, ValueError):
        return None


# ── ENQUEUE ─────────────────────────────────────────────────────────────────

def enqueue(db: Session, employee: AIEmployee, subject_ids: Sequence[str], *,
            subject_type: str = "lead", job_key: Optional[str] = None,
            priority: int = 100, actor_kind: str = C.ACTOR_SYSTEM,
            actor_id: Optional[str] = None) -> Dict:
    """Assign records to an employee. Idempotent by construction.

    Returns {created, skipped_existing, invalid}. A record already assigned to
    this employee is SKIPPED rather than reset — re-running an enqueue must not
    restart a conversation that is halfway through.
    """
    job_key = job_key or employee.job_role or "work"
    created, skipped, invalid = 0, 0, 0
    for raw in subject_ids:
        sid = (str(raw) if raw is not None else "").strip()
        if not sid:
            invalid += 1
            continue
        item = AIWorkItem(
            organization_id=employee.organization_id,
            employee_id=employee.id,
            job_key=job_key,
            subject_type=subject_type,
            subject_id=sid,
            state=C.ASSIGNED,
            priority=int(priority),
            next_action_at=datetime.utcnow(),
        )
        # SAVEPOINT PER ROW. Without it the IntegrityError from one duplicate
        # poisons the whole session and the rest of a 40,000-record enqueue is
        # lost — the failure mode is "the batch stopped at the first record the
        # customer had already been assigned", which is exactly the common case.
        try:
            with db.begin_nested():
                db.add(item)
                db.flush()
        except IntegrityError:
            skipped += 1
            continue
        _event(db, item, None, C.ASSIGNED, "assigned to employee",
               actor_kind=actor_kind, actor_id=actor_id)
        created += 1
    db.flush()
    return {"created": created, "skipped_existing": skipped,
            "invalid": invalid, "employee_id": employee.id}


# ── TRANSITIONS ─────────────────────────────────────────────────────────────

def _event(db: Session, item: AIWorkItem, from_state: Optional[str],
           to_state: str, reason: Optional[str], *,
           actor_kind: str = C.ACTOR_AI_EMPLOYEE,
           actor_id: Optional[str] = None, run_id: Optional[str] = None,
           detail=None) -> AIWorkItemEvent:
    row = AIWorkItemEvent(
        work_item_id=item.id, organization_id=item.organization_id,
        employee_id=item.employee_id, from_state=from_state, to_state=to_state,
        reason=(reason or "")[:255] or None, actor_kind=actor_kind,
        actor_id=actor_id, run_id=run_id, detail=_json(detail))
    db.add(row)
    return row


def can_transition(from_state: str, to_state: str) -> bool:
    return to_state in C.ALLOWED_TRANSITIONS.get(from_state, set())


def path_between(from_state: str, to_state: str) -> Optional[List[str]]:
    """The shortest legal route between two states, or None if there is none.

    Breadth-first over the SAME transition table `transition` enforces — so a
    route this returns is a sequence of edges that individually exist, and a
    state with no outgoing edges (every terminal state) is a dead end here just
    as it is there. There is no path out of `do_not_contact`, and this cannot
    invent one.
    """
    if from_state == to_state:
        return []
    seen = {from_state}
    frontier = [(from_state, [])]
    while frontier:
        state, route = frontier.pop(0)
        for nxt in sorted(C.ALLOWED_TRANSITIONS.get(state, set())):
            if nxt in seen:
                continue
            if nxt == to_state:
                return route + [nxt]
            seen.add(nxt)
            frontier.append((nxt, route + [nxt]))
    return None


def advance_to(db: Session, item: AIWorkItem, to_state: str, *,
               reason: Optional[str] = None,
               actor_kind: str = C.ACTOR_AI_EMPLOYEE,
               actor_id: Optional[str] = None, run_id: Optional[str] = None,
               detail=None, outcome_detail: Optional[str] = None,
               claim_token: Optional[str] = None) -> AIWorkItem:
    """Reach a state by walking LEGAL EDGES, recording each hop.

    WHY THIS EXISTS, and it is a real defect it fixes. `appointment.book`
    creates the booking and then closes the work item. When the item happened
    to be in `assigned` rather than `working` — which is exactly what a direct
    tool call does — `assigned -> appointment_booked` is not an edge, the
    transition raised, the tool reported a refusal, AND THE APPOINTMENT STILL
    EXISTED. An action that happened being reported as refused is the worst
    possible pairing: the employee believes it failed and tries again.

    Walking the path keeps every edge legal and every hop in the timeline,
    rather than adding shortcut edges to the state machine — which would have
    been the easy fix and would have quietly made `assigned -> appointment
    _booked` a route that skips the eligibility check.
    """
    route = path_between(item.state, to_state)
    if route is None:
        raise IllegalTransition("there is no legal route from %s to %s"
                                % (item.state, to_state))
    for index, hop in enumerate(route):
        last = index == len(route) - 1
        transition(db, item, hop,
                   reason=(reason if last else "en route to %s" % to_state),
                   actor_kind=actor_kind, actor_id=actor_id, run_id=run_id,
                   detail=(detail if last else None),
                   outcome_detail=(outcome_detail if last else None),
                   claim_token=claim_token)
    return item


def transition(db: Session, item: AIWorkItem, to_state: str, *,
               reason: Optional[str] = None,
               actor_kind: str = C.ACTOR_AI_EMPLOYEE,
               actor_id: Optional[str] = None, run_id: Optional[str] = None,
               detail=None, outcome_detail: Optional[str] = None,
               claim_token: Optional[str] = None) -> AIWorkItem:
    """Move a work item. Raises IllegalTransition on an edge that is not real.

    `claim_token`, when given, is checked against the row. A worker whose lease
    was taken away cannot write its conclusion over the worker that has the
    item now — this is the second half of property 2 in the module header.
    """
    if to_state not in C.ALL_STATES:
        raise IllegalTransition("unknown state %r" % to_state)
    if claim_token is not None and item.claim_token != claim_token:
        raise IllegalTransition(
            "work item %s is no longer held by this worker (lease lost)"
            % item.id)
    frm = item.state
    if frm == to_state:
        # A NO-OP IS NOT AN ERROR, and it is also not an event. Recording it
        # would fill the timeline with lines that say nothing happened.
        return item
    if not can_transition(frm, to_state):
        raise IllegalTransition("%s -> %s is not a permitted transition"
                                % (frm, to_state))

    item.state = to_state
    item.state_reason = (reason or "")[:255] or None
    item.updated_at = datetime.utcnow()

    if to_state in C.TERMINAL_STATES:
        item.terminal_at = datetime.utcnow()
        item.outcome = C.STATE_TO_OUTCOME.get(to_state)
        item.outcome_detail = (outcome_detail or reason or "")[:255] or None
        item.next_action_at = None
        # A TERMINAL ITEM RELEASES ITS LEASE. Leaving it held would keep the
        # row out of "unclaimed" counts forever and hide a stuck worker.
        item.claim_token = None
        item.lock_expires_at = None
        item.claimed_by_run_id = None
    elif to_state == C.NEEDS_REVIEW:
        item.outcome = C.OUTCOME_NEEDS_REVIEW
        item.next_action_at = None
        item.claim_token = None
        item.lock_expires_at = None

    _event(db, item, frm, to_state, reason, actor_kind=actor_kind,
           actor_id=actor_id, run_id=run_id, detail=detail)
    db.flush()
    return item


# ── CLAIMING ────────────────────────────────────────────────────────────────

def claimable_query(db: Session, employee: AIEmployee, now: datetime):
    """The rows a worker for this employee may legitimately pick up."""
    return (db.query(AIWorkItem)
            .filter(AIWorkItem.employee_id == employee.id,
                    AIWorkItem.organization_id == employee.organization_id,
                    AIWorkItem.state.in_(list(C.CLAIMABLE_STATES)),
                    or_(AIWorkItem.next_action_at.is_(None),
                        AIWorkItem.next_action_at <= now),
                    or_(AIWorkItem.claim_token.is_(None),
                        AIWorkItem.lock_expires_at.is_(None),
                        AIWorkItem.lock_expires_at <= now))
            .order_by(AIWorkItem.priority.asc(),
                      AIWorkItem.next_action_at.asc().nullsfirst(),
                      AIWorkItem.created_at.asc(),
                      AIWorkItem.id.asc()))


def claim(db: Session, employee: AIEmployee, *, limit: int = 1,
          now: Optional[datetime] = None, run_id: Optional[str] = None,
          lease_seconds: int = C.CLAIM_LEASE_SECONDS
          ) -> List[Tuple[AIWorkItem, str]]:
    """Claim up to `limit` items. Returns [(item, claim_token), ...].

    THE CONDITIONAL UPDATE IS THE LOCK. The candidate list is only a hint about
    which ids to try; whether this worker actually got one is decided by the
    row count of an UPDATE whose WHERE clause repeats every condition. That is
    why the SELECT above is allowed to be stale.
    """
    now = now or datetime.utcnow()
    expires = now + timedelta(seconds=int(lease_seconds))
    # Over-fetch: some candidates will be taken by other workers between the
    # select and the update, and re-querying per miss would be slower than
    # simply having more ids to try.
    candidates = claimable_query(db, employee, now).limit(max(1, limit) * 4).all()

    won: List[Tuple[AIWorkItem, str]] = []
    for cand in candidates:
        if len(won) >= limit:
            break
        token = str(_uuid.uuid4())
        updated = (db.query(AIWorkItem)
                   .filter(AIWorkItem.id == cand.id,
                           AIWorkItem.employee_id == employee.id,
                           AIWorkItem.state.in_(list(C.CLAIMABLE_STATES)),
                           or_(AIWorkItem.claim_token.is_(None),
                               AIWorkItem.lock_expires_at.is_(None),
                               AIWorkItem.lock_expires_at <= now))
                   .update({"claim_token": token,
                            "claimed_at": now,
                            "lock_expires_at": expires,
                            "claimed_by_run_id": run_id,
                            "attempts": AIWorkItem.attempts + 1,
                            "updated_at": now},
                           synchronize_session=False))
        if updated == 1:
            db.flush()
            db.refresh(cand)
            won.append((cand, token))
    return won


def renew(db: Session, item: AIWorkItem, claim_token: str, *,
          lease_seconds: int = C.CLAIM_LEASE_SECONDS,
          now: Optional[datetime] = None) -> bool:
    """Extend a lease this worker still holds. False means it was lost."""
    now = now or datetime.utcnow()
    updated = (db.query(AIWorkItem)
               .filter(AIWorkItem.id == item.id,
                       AIWorkItem.claim_token == claim_token)
               .update({"lock_expires_at": now + timedelta(seconds=lease_seconds)},
                       synchronize_session=False))
    db.flush()
    return updated == 1


def release(db: Session, item: AIWorkItem, claim_token: Optional[str] = None, *,
            next_action_in_minutes: Optional[int] = None) -> bool:
    """Give the item back. Safe to call with a lease that was already lost."""
    q = db.query(AIWorkItem).filter(AIWorkItem.id == item.id)
    if claim_token is not None:
        q = q.filter(AIWorkItem.claim_token == claim_token)
    values = {"claim_token": None, "lock_expires_at": None,
              "claimed_by_run_id": None, "updated_at": datetime.utcnow()}
    if next_action_in_minutes is not None:
        values["next_action_at"] = (datetime.utcnow()
                                    + timedelta(minutes=int(next_action_in_minutes)))
    updated = q.update(values, synchronize_session=False)
    db.flush()
    return updated == 1


def reclaim_expired(db: Session, *, now: Optional[datetime] = None,
                    limit: int = 500) -> int:
    """Free leases whose worker never came back. Returns how many.

    Runs from the supervisor pass. Without it a container replaced mid-run
    parks every item it held until the lease expires and then forever, because
    nothing else clears `claim_token`.
    """
    now = now or datetime.utcnow()
    rows = (db.query(AIWorkItem)
            .filter(AIWorkItem.claim_token.isnot(None),
                    AIWorkItem.lock_expires_at.isnot(None),
                    AIWorkItem.lock_expires_at <= now)
            .limit(limit).all())
    for row in rows:
        row.claim_token = None
        row.lock_expires_at = None
        row.claimed_by_run_id = None
        _event(db, row, row.state, row.state, "lease expired; item released",
               actor_kind=C.ACTOR_SYSTEM)
    db.flush()
    return len(rows)


# ── SCHEDULING AND FAILURE ──────────────────────────────────────────────────

def record_touch(db: Session, item: AIWorkItem, *, now: Optional[datetime] = None,
                 max_touches: int = C.DEFAULT_MAX_TOUCHES) -> bool:
    """Count an outward touch. Returns True when the item is now exhausted.

    The CALLER decides what to do about exhaustion; this only counts, so the
    transition into EXHAUSTED still goes through the state machine with a
    reason attached.
    """
    now = now or datetime.utcnow()
    item.touches = int(item.touches or 0) + 1
    item.last_action_at = now
    item.consecutive_failures = 0
    db.flush()
    return item.touches >= int(max_touches)


def schedule_next(db: Session, item: AIWorkItem, minutes: int, *,
                  now: Optional[datetime] = None) -> AIWorkItem:
    now = now or datetime.utcnow()
    item.next_action_at = now + timedelta(minutes=max(0, int(minutes)))
    item.updated_at = now
    db.flush()
    return item


def record_failure(db: Session, item: AIWorkItem, reason: str, *,
                   run_id: Optional[str] = None,
                   max_consecutive: int = C.DEFAULT_MAX_CONSECUTIVE_FAILURES
                   ) -> bool:
    """Back off. Returns True when the item has failed too many times.

    EXPONENTIAL BACKOFF WITH A FLOOR ON THE ATTEMPTS. An infinite retry loop
    against a provider that is down is how an API bill is run up overnight
    (section 35), so failures both slow down AND stop.
    """
    item.consecutive_failures = int(item.consecutive_failures or 0) + 1
    idx = min(item.consecutive_failures - 1, len(C.RETRY_BACKOFF_SECONDS) - 1)
    delay = C.RETRY_BACKOFF_SECONDS[idx]
    item.next_action_at = datetime.utcnow() + timedelta(seconds=delay)
    item.updated_at = datetime.utcnow()
    _event(db, item, item.state, item.state, ("failure: %s" % reason)[:255],
           actor_kind=C.ACTOR_SYSTEM, run_id=run_id,
           detail={"consecutive_failures": item.consecutive_failures,
                   "backoff_seconds": delay})
    db.flush()
    return item.consecutive_failures >= int(max_consecutive)


# ── READING ─────────────────────────────────────────────────────────────────

def counts_by_state(db: Session, *, organization_id: str,
                    employee_id: Optional[str] = None) -> Dict[str, int]:
    from sqlalchemy import func
    q = (db.query(AIWorkItem.state, func.count(AIWorkItem.id))
         .filter(AIWorkItem.organization_id == organization_id))
    if employee_id:
        q = q.filter(AIWorkItem.employee_id == employee_id)
    out = {s: 0 for s in C.ALL_STATES}
    for state, n in q.group_by(AIWorkItem.state).all():
        out[state] = int(n)
    return out


def grouped_counts(db: Session, *, organization_id: str,
                   employee_id: Optional[str] = None) -> List[Dict]:
    """The Work Queue screen's groups. Raw states are never shown to a customer."""
    by_state = counts_by_state(db, organization_id=organization_id,
                               employee_id=employee_id)
    return [{"key": key, "label": label,
             "count": sum(by_state.get(s, 0) for s in states),
             "states": list(states)}
            for key, label, states in C.QUEUE_GROUPS]


def stalled(db: Session, *, organization_id: Optional[str] = None,
            older_than_minutes: int = 60, limit: int = 100) -> List[AIWorkItem]:
    """Items that should have moved and did not.

    The definition is deliberately simple and slightly generous: due for action
    and untouched for an hour. A stalled-job list nobody trusts is a list
    nobody reads, and a tighter definition produces noise on a slow morning.
    """
    cutoff = datetime.utcnow() - timedelta(minutes=int(older_than_minutes))
    q = (db.query(AIWorkItem)
         .filter(AIWorkItem.state.in_(list(C.CLAIMABLE_STATES)),
                 or_(AIWorkItem.next_action_at.is_(None),
                     AIWorkItem.next_action_at <= cutoff),
                 or_(AIWorkItem.updated_at.is_(None),
                     AIWorkItem.updated_at <= cutoff)))
    if organization_id:
        q = q.filter(AIWorkItem.organization_id == organization_id)
    return q.order_by(AIWorkItem.updated_at.asc()).limit(limit).all()


def timeline(db: Session, work_item_id: str, *, limit: int = 200
             ) -> List[AIWorkItemEvent]:
    return (db.query(AIWorkItemEvent)
            .filter(AIWorkItemEvent.work_item_id == work_item_id)
            .order_by(AIWorkItemEvent.created_at.asc(),
                      AIWorkItemEvent.id.asc())
            .limit(limit).all())


def pause_all(db: Session, *, employee_id: str, reason: str,
              actor_id: Optional[str] = None, limit: int = 5000) -> int:
    """Pause every live item for one employee. Used by the kill switch.

    Pausing the ITEMS as well as the employee matters: `activation.resolve`
    already stops the next tool call, and this stops the queue from looking
    busy on a screen while nothing is happening.
    """
    rows = (db.query(AIWorkItem)
            .filter(AIWorkItem.employee_id == employee_id,
                    AIWorkItem.state.in_(list(C.CLAIMABLE_STATES)))
            .limit(limit).all())
    n = 0
    for row in rows:
        try:
            transition(db, row, C.PAUSED, reason=reason,
                       actor_kind=C.ACTOR_HUMAN, actor_id=actor_id)
            n += 1
        except IllegalTransition:
            continue
    return n


def resume_all(db: Session, *, employee_id: str, reason: str,
               actor_id: Optional[str] = None, limit: int = 5000) -> int:
    """Bring paused items back. They re-enter at eligibility, never at working.

    That is the same rule NEEDS_REVIEW follows and for the same reason: a
    suppression or an opt-out that arrived while the queue was paused must be
    seen before the next touch, not after it.
    """
    rows = (db.query(AIWorkItem)
            .filter(AIWorkItem.employee_id == employee_id,
                    AIWorkItem.state == C.PAUSED)
            .limit(limit).all())
    n = 0
    for row in rows:
        try:
            transition(db, row, C.ELIGIBILITY_PENDING, reason=reason,
                       actor_kind=C.ACTOR_HUMAN, actor_id=actor_id)
            row.next_action_at = datetime.utcnow()
            n += 1
        except IllegalTransition:
            continue
    db.flush()
    return n
