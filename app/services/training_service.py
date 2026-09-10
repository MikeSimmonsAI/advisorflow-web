"""ASSIGNING TRAINING, AND KNOWING WHO IS READY.

WHO MAY ASSIGN

Only God. Not an executive, not a sales manager, not an org admin. Training
assignment is a root provisioning function for the same reason granting Demo
Suite access is: both are statements about what a person is expected to be able
to do on this platform, and there is exactly one root authority here.

That is a deliberate limitation of this first version and it is stated rather
than hidden. Delegating "a sales manager may assign the salesperson path to
their own team" is a real and reasonable thing to want; it needs a scoped
delegation model of its own and inventing one in passing is how a second
authority layer gets built by accident.

A PRACTICE STEP IS NOT COMPLETABLE BY CLICKING DONE

A step carrying `practice_scenario` requires that the person has actually run
that scenario to completion in the Demo Suite. `complete_step` checks the demo
session before it will mark it, and refuses with the reason.

Without that, the Demo Presenter path is a reading exercise that produces
somebody who has read about running a demo — which is precisely the outcome the
whole project exists to avoid.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.demo_suite_models import SESSION_COMPLETE, DemoSession
from app.models.models import User
from app.models.training_models import (TRAINING_COMPLETE, TRAINING_IN_PROGRESS,
                                        TRAINING_NOT_STARTED,
                                        TrainingAssignment,
                                        TrainingStepProgress)
from app.services import training_catalog as catalog

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.utcnow()


# ─────────────────────────────────────────────────────────────────────────────
# ASSIGNMENT
# ─────────────────────────────────────────────────────────────────────────────

def assign(db: Session, *, user: User, path_key: str, actor: User,
           due_at: Optional[datetime] = None, note: Optional[str] = None,
           commit: bool = True) -> TrainingAssignment:
    """Give one person one path. IDEMPOTENT — reactivates rather than duplicates.

    Re-assigning a path somebody already holds returns the existing row with
    its history intact. Two operators assigning the same path must not produce
    two competing progress records, which is the same lesson
    `grant_workspace_membership` learned about invitations.
    """
    path = catalog.path_or_404(path_key)
    row = (db.query(TrainingAssignment)
           .filter(TrainingAssignment.user_id == user.id,
                   TrainingAssignment.path_key == path_key)
           .first())
    if row is None:
        row = TrainingAssignment(
            user_id=user.id, path_key=path_key,
            status=TRAINING_NOT_STARTED, assigned_by=actor.id,
            assigned_at=_now(), due_at=due_at, note=note, is_active=True)
        db.add(row)
    else:
        row.is_active = True
        row.revoked_at = None
        row.revoked_by = None
        row.assigned_by = actor.id
        if due_at is not None:
            row.due_at = due_at
        if note:
            row.note = note
    if commit:
        db.commit()
        db.refresh(row)
    else:
        db.flush()
    log.info("AUDIT: training assigned path=%s user=%s by=%s",
             path["key"], user.email, getattr(actor, "email", "?"))
    return row


def revoke(db: Session, *, user: User, path_key: str, actor: User,
           commit: bool = True) -> Dict[str, Any]:
    """Un-assign. DEACTIVATES, never deletes.

    Erasing the record of a completed path is erasing the answer to "was this
    person ever signed off to present?" — which is exactly the question a
    revocation makes somebody ask.
    """
    row = (db.query(TrainingAssignment)
           .filter(TrainingAssignment.user_id == user.id,
                   TrainingAssignment.path_key == path_key,
                   TrainingAssignment.is_active.is_(True))
           .first())
    if row is None:
        return {"status": "not_assigned"}
    row.is_active = False
    row.revoked_at = _now()
    row.revoked_by = actor.id
    if commit:
        db.commit()
    else:
        db.flush()
    log.info("AUDIT: training revoked path=%s user=%s by=%s",
             path_key, user.email, getattr(actor, "email", "?"))
    return {"status": "revoked", "assignment_id": row.id}


# ─────────────────────────────────────────────────────────────────────────────
# PROGRESS
# ─────────────────────────────────────────────────────────────────────────────

def _assignment(db: Session, user: User, path_key: str,
                required: bool = True) -> Optional[TrainingAssignment]:
    row = (db.query(TrainingAssignment)
           .filter(TrainingAssignment.user_id == user.id,
                   TrainingAssignment.path_key == path_key,
                   TrainingAssignment.is_active.is_(True))
           .first())
    if row is None and required:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This training path has not been assigned to you.")
    return row


def _completed_keys(db: Session, assignment: TrainingAssignment) -> List[str]:
    return [r.step_key for r in
            db.query(TrainingStepProgress)
            .filter(TrainingStepProgress.assignment_id == assignment.id).all()]


def _practice_satisfied(db: Session, user: User, scenario_key: str) -> bool:
    """Has this person actually run that scenario to completion, anywhere?

    ANY environment, deliberately. A presenter who practised on the EvoSys Pro
    demonstration has practised; requiring them to do it again per brand would
    be busywork, because the scenario is the same scenario and the skill is the
    same skill.
    """
    return (db.query(DemoSession)
            .filter(DemoSession.user_id == user.id,
                    DemoSession.scenario_key == scenario_key,
                    DemoSession.status == SESSION_COMPLETE)
            .first()) is not None


def complete_step(db: Session, *, user: User, path_key: str, step_key: str,
                  commit: bool = True) -> Dict[str, Any]:
    path = catalog.path_or_404(path_key)
    step = catalog.find_step(path, step_key)
    if step is None:
        raise HTTPException(status_code=404,
                            detail="No such step in this training path.")
    assignment = _assignment(db, user, path_key)

    practised = False
    scenario_key = step.get("practice_scenario")
    if scenario_key:
        if not _practice_satisfied(db, user, scenario_key):
            # REFUSED, WITH THE REASON AND THE WAY OUT. A 403 that only says no
            # sends the person to ask somebody; naming the scenario sends them
            # to do it.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This step is completed by practising, not by reading. "
                       "Run the '%s' scenario through to the end in the Demo "
                       "Suite and this will complete itself."
                       % scenario_key.replace("_", " "))
        practised = True

    existing = (db.query(TrainingStepProgress)
                .filter(TrainingStepProgress.assignment_id == assignment.id,
                        TrainingStepProgress.step_key == step_key)
                .first())
    if existing is None:
        db.add(TrainingStepProgress(
            assignment_id=assignment.id, step_key=step_key,
            completed_at=_now(), practised=practised))
        db.flush()

    _recompute(db, assignment, path)
    if commit:
        db.commit()
    return progress(db, user, path_key)


def uncomplete_step(db: Session, *, user: User, path_key: str, step_key: str,
                    commit: bool = True) -> Dict[str, Any]:
    """Let somebody un-tick a step they ticked by accident.

    Worth having because the alternative is a person who marked a step done
    without reading it and now has no way to be honest about it.
    """
    path = catalog.path_or_404(path_key)
    assignment = _assignment(db, user, path_key)
    (db.query(TrainingStepProgress)
     .filter(TrainingStepProgress.assignment_id == assignment.id,
             TrainingStepProgress.step_key == step_key)
     .delete(synchronize_session=False))
    db.flush()
    _recompute(db, assignment, path)
    if commit:
        db.commit()
    return progress(db, user, path_key)


def _recompute(db: Session, assignment: TrainingAssignment,
               path: Dict[str, Any]) -> None:
    """Derive the assignment's status from its steps. Never set directly.

    A status column that is written by hand in three places is a status column
    that will eventually say Complete for a path with an unfinished step.
    """
    done = set(_completed_keys(db, assignment))
    keys = {s["key"] for s in path["steps"]}
    now = _now()
    if not done:
        assignment.status = TRAINING_NOT_STARTED
        assignment.started_at = None
        assignment.completed_at = None
    elif done >= keys:
        assignment.status = TRAINING_COMPLETE
        assignment.started_at = assignment.started_at or now
        assignment.completed_at = assignment.completed_at or now
    else:
        assignment.status = TRAINING_IN_PROGRESS
        assignment.started_at = assignment.started_at or now
        assignment.completed_at = None


# ─────────────────────────────────────────────────────────────────────────────
# READING
# ─────────────────────────────────────────────────────────────────────────────

def progress(db: Session, user: User, path_key: str) -> Dict[str, Any]:
    """One path, with the person's position in it and every step's body."""
    path = catalog.path_or_404(path_key)
    assignment = _assignment(db, user, path_key)
    done = set(_completed_keys(db, assignment))
    steps = []
    for i, s in enumerate(path["steps"]):
        scenario_key = s.get("practice_scenario")
        steps.append({
            **s, "index": i, "done": s["key"] in done,
            "requires_practice": bool(scenario_key),
            "practice_available": (
                _practice_satisfied(db, user, scenario_key)
                if scenario_key else True),
        })
    return {
        "path": {k: path[k] for k in
                 ("key", "name", "audience", "summary", "why",
                  "requires_demo")},
        "audience_label": catalog.AUDIENCE_LABELS.get(path["audience"]),
        "steps": steps,
        "assignment": _assignment_payload(assignment, path, done),
    }


def _assignment_payload(assignment: TrainingAssignment, path: Dict[str, Any],
                        done: set) -> Dict[str, Any]:
    total = len(path["steps"])
    now = _now()
    return {
        "id": assignment.id,
        "status": assignment.status,
        "completed_steps": len(done),
        "total_steps": total,
        "assigned_at": (assignment.assigned_at.isoformat()
                        if assignment.assigned_at else None),
        "due_at": assignment.due_at.isoformat() if assignment.due_at else None,
        # DERIVED, NEVER STORED. A stored overdue flag is a flag somebody has
        # to remember to set, and it is wrong from the first midnight onwards.
        "overdue": bool(assignment.due_at
                        and assignment.status != TRAINING_COMPLETE
                        and assignment.due_at < now),
        "completed_at": (assignment.completed_at.isoformat()
                         if assignment.completed_at else None),
        "note": assignment.note,
    }


def my_training(db: Session, user: User) -> Dict[str, Any]:
    """Everything assigned to the caller, plus what exists but is not.

    The unassigned list is shown deliberately. Somebody who wants to learn the
    manager path should be able to see it exists and ask, rather than being
    unable to tell the difference between "not for you" and "not built".
    """
    rows = (db.query(TrainingAssignment)
            .filter(TrainingAssignment.user_id == user.id,
                    TrainingAssignment.is_active.is_(True)).all())
    by_key = {r.path_key: r for r in rows}
    assigned, available = [], []
    for entry in catalog.catalogue():
        row = by_key.get(entry["key"])
        if row is None:
            available.append(entry)
            continue
        path = catalog.get_path(entry["key"])
        done = set(_completed_keys(db, row))
        assigned.append({**entry,
                         "assignment": _assignment_payload(row, path, done)})
    assigned.sort(key=lambda a: (a["assignment"]["status"] == TRAINING_COMPLETE,
                                 a["name"]))
    return {
        "assigned": assigned,
        "available": available,
        "complete_count": sum(1 for a in assigned
                              if a["assignment"]["status"] == TRAINING_COMPLETE),
        "assigned_count": len(assigned),
    }


def for_user(db: Session, user: User) -> List[Dict[str, Any]]:
    """A person's training, as the access footprint reports it.

    Compact on purpose: God Mode is looking at a whole person, and a wall of
    per-step detail there would bury the thing being asked, which is whether
    this person is ready.
    """
    rows = (db.query(TrainingAssignment)
            .filter(TrainingAssignment.user_id == user.id).all())
    out = []
    for r in rows:
        path = catalog.get_path(r.path_key)
        if path is None:
            # A row whose path is no longer in the catalogue is inert rather
            # than dangerous — dropped here exactly as `grants_for` drops an
            # unregistered capability.
            continue
        done = set(_completed_keys(db, r))
        out.append({
            "path_key": r.path_key,
            "name": path["name"],
            "audience_label": catalog.AUDIENCE_LABELS.get(path["audience"]),
            "status": r.status if r.is_active else "revoked",
            "is_active": bool(r.is_active),
            "completed_steps": len(done),
            "total_steps": len(path["steps"]),
            "assigned_at": (r.assigned_at.isoformat()
                            if r.assigned_at else None),
            "due_at": r.due_at.isoformat() if r.due_at else None,
            "completed_at": (r.completed_at.isoformat()
                             if r.completed_at else None),
        })
    out.sort(key=lambda a: a["name"])
    return out


def readiness_report(db: Session, path_key: Optional[str] = None
                     ) -> Dict[str, Any]:
    """Who has been assigned what, and where they have stopped.

    Names the step people are stuck on rather than averaging a percentage —
    "everybody stalls on what-not-to-promise" is a finding; "the team averages
    62%" is not.
    """
    q = db.query(TrainingAssignment).filter(
        TrainingAssignment.is_active.is_(True))
    if path_key:
        catalog.path_or_404(path_key)
        q = q.filter(TrainingAssignment.path_key == path_key)
    rows = q.all()
    users = {u.id: u for u in db.query(User).filter(
        User.id.in_([r.user_id for r in rows])).all()} if rows else {}

    people = []
    stuck: Dict[str, int] = {}
    for r in rows:
        path = catalog.get_path(r.path_key)
        if path is None:
            continue
        u = users.get(r.user_id)
        done = set(_completed_keys(db, r))
        next_step = next((s for s in path["steps"] if s["key"] not in done),
                         None)
        if next_step is not None and done:
            stuck[next_step["title"]] = stuck.get(next_step["title"], 0) + 1
        people.append({
            "user_id": r.user_id,
            "name": u.full_name if u else None,
            "email": u.email if u else None,
            "path_key": r.path_key, "path_name": path["name"],
            "status": r.status,
            "completed_steps": len(done), "total_steps": len(path["steps"]),
            "next_step": next_step["title"] if next_step else None,
            "due_at": r.due_at.isoformat() if r.due_at else None,
            "overdue": bool(r.due_at and r.status != TRAINING_COMPLETE
                            and r.due_at < _now()),
        })
    people.sort(key=lambda p: (p["status"] == TRAINING_COMPLETE,
                               p["name"] or ""))
    return {
        "people": people,
        "assigned": len(people),
        "complete": sum(1 for p in people if p["status"] == TRAINING_COMPLETE),
        "overdue": sum(1 for p in people if p["overdue"]),
        "stuck_on": sorted(({"step": k, "people": v} for k, v in stuck.items()),
                           key=lambda x: -x["people"]),
    }
