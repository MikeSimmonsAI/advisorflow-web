"""TRAINING — the person doing it, and the owner assigning it.

    /training/*        the LEARNER. Any authenticated user, reading and
                       progressing what has been assigned TO THEM. Nobody can
                       read or complete somebody else's training here — the
                       subject of every route is `current_user`, never a
                       user id in the path.

    /god/training/*    the OWNER. Assigning, un-assigning, and reading the
                       readiness report.

WHY THE LEARNER ROUTES TAKE NO USER ID

Because then there is no id to get wrong. A route shaped
`/training/{user_id}/complete` needs a guard proving the caller is that user,
and that guard is one refactor away from being dropped. A route that can only
ever mean "me" cannot be pointed at anybody else at all.
"""
# NO `from __future__ import annotations` IN A ROUTER MODULE — see the note in
# god_access_router.py. Under a `@limiter.limit` wrapper FastAPI cannot resolve
# a stringified body annotation and the app refuses to import.
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db, require_god
from app.limiter import limiter
from app.models.models import User
from app.services import training_catalog as catalog
from app.services import training_service

log = logging.getLogger(__name__)

router = APIRouter(prefix="/training", tags=["training"])
god_router = APIRouter(prefix="/god/training", tags=["training (owner)"])

WRITE_LIMIT = "60/minute"


class StepIn(BaseModel):
    step: str


class AssignIn(BaseModel):
    user_id: str
    path_key: str
    due_at: Optional[str] = None
    note: Optional[str] = None


class RevokeIn(BaseModel):
    user_id: str
    path_key: str


# ─────────────────────────────────────────────────────────────────────────────
# LEARNER
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/me")
def my_training(db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    """What has been assigned to the caller, and what exists but has not been.

    The unassigned list is shown deliberately: somebody who wants to learn the
    manager path should be able to see it exists and ask for it, rather than
    being unable to tell "not for you" from "not built".
    """
    return training_service.my_training(db, user)


@router.get("/paths/{path_key}")
def path(path_key: str, db: Session = Depends(get_db),
         user: User = Depends(get_current_user)):
    """One path in full, with the caller's position in it.

    404s for a path that has not been assigned to them. That is a deliberate
    choice over showing it read-only: training the person has not been asked to
    do is not their next task, and a screen that lets somebody work through an
    unassigned path produces completion records nobody asked for.
    """
    return training_service.progress(db, user, path_key)


@router.post("/paths/{path_key}/complete")
@limiter.limit(WRITE_LIMIT)
def complete_step(request: Request, path_key: str, body: StepIn,
                  db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    """Mark one step done.

    A step that carries a practice scenario is REFUSED unless that scenario has
    actually been completed in the Demo Suite. Reading about how to run a demo
    produces somebody who has read about running a demo, and the entire point
    of this is to stop the platform owner having to attend sales meetings.
    """
    return training_service.complete_step(db, user=user, path_key=path_key,
                                          step_key=body.step)


@router.post("/paths/{path_key}/uncomplete")
@limiter.limit(WRITE_LIMIT)
def uncomplete_step(request: Request, path_key: str, body: StepIn,
                    db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    """Un-tick a step. Worth having: the alternative is somebody who ticked a
    step without reading it and has no way to be honest about it."""
    return training_service.uncomplete_step(db, user=user, path_key=path_key,
                                            step_key=body.step)


@router.get("/catalogue")
def catalogue(_: User = Depends(get_current_user)):
    """Every path that exists, without the step bodies. Not gated: knowing
    what training exists reveals nothing and helps somebody ask for it."""
    return {"paths": catalog.catalogue()}


# ─────────────────────────────────────────────────────────────────────────────
# OWNER
# ─────────────────────────────────────────────────────────────────────────────

@god_router.post("/assign")
@limiter.limit(WRITE_LIMIT)
def assign(request: Request, body: AssignIn, db: Session = Depends(get_db),
           god: User = Depends(require_god)):
    """Assign one path to one person. Idempotent."""
    target = db.query(User).filter(User.id == body.user_id).first()
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    due_at = None
    if body.due_at:
        from datetime import datetime
        try:
            due_at = datetime.fromisoformat(
                body.due_at.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="due_at must be an ISO-8601 date or timestamp.")
    row = training_service.assign(db, user=target, path_key=body.path_key,
                                  actor=god, due_at=due_at, note=body.note)
    return {"status": "assigned", "assignment_id": row.id,
            "path_key": row.path_key, "user_id": target.id}


@god_router.post("/revoke")
@limiter.limit(WRITE_LIMIT)
def revoke(request: Request, body: RevokeIn, db: Session = Depends(get_db),
           god: User = Depends(require_god)):
    """Un-assign a path. Deactivates; any completion already recorded is kept."""
    target = db.query(User).filter(User.id == body.user_id).first()
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    return training_service.revoke(db, user=target, path_key=body.path_key,
                                   actor=god)


@god_router.get("/readiness")
def readiness(path_key: Optional[str] = Query(None),
              db: Session = Depends(get_db),
              _god: User = Depends(require_god)):
    """Who has been assigned what, and where they have stopped.

    Names the step people are stuck on rather than averaging a percentage:
    "everybody stalls on what-not-to-promise" is a finding, "the team averages
    62%" is not.
    """
    return training_service.readiness_report(db, path_key)


@god_router.get("/paths")
def paths(_god: User = Depends(require_god)):
    """The catalogue with step bodies, so the owner can see what they are
    assigning before they assign it."""
    return {"paths": catalog.catalogue(include_steps=True)}
