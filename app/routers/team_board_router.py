"""/program/team-board - the handoff sheet's work inside EvoSys (owner, Nov 25).

The owner's logins (the program's managers) see everyone's work and the
numbers, and can filter by person and reassign. A team member sees only the
work assigned to them, and only once the owner opens the board to the team.
Every route re-decides that on every call (services/programs/team_board.py).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, require_tenant_user
from app.models.models import User
from app.routers.program_router import _program, _sees_program
from app.services.programs import restland_sheet as RS
from app.services.programs import team_board as TB

router = APIRouter(prefix="/program/team-board", tags=["team-board"])


def _nostore(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, private"


def _ctx(db: Session, user: User):
    prog = _program(db, user, managers_only=False)
    try:
        who = TB.viewer(db, prog, user, _sees_program(db, user, prog.organization_id))
    except TB.BoardError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    return prog, who


def _manager(who) -> None:
    if not who["manager"]:
        raise HTTPException(status_code=403, detail="Only the owner can do that.")


@router.get("/me")
def me(response: Response, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    _nostore(response)
    prog, who = _ctx(db, user)
    return {"name": who["name"] or user.full_name, "manager": who["manager"], "live_for_team": TB.is_live(prog),
            "choices": {"outcomes": list(TB.OUTCOMES), "sold": list(RS.SOLD_CHOICES),
                        "what_happened": list(RS.WHAT_HAPPENED), "times": list(RS.TIME_CHOICES),
                        "how": list(RS.HOW_PICK)}}


@router.get("/work")
def work(response: Response, person: Optional[str] = Query(None), db: Session = Depends(get_db),
         user: User = Depends(require_tenant_user)):
    """Appointments and follow-ups. A team member always gets only their own;
    the owner gets everyone's, or one person's with ?person=."""
    _nostore(response)
    prog, who = _ctx(db, user)
    data = TB.work(db, prog, person=person if who["manager"] else who["name"])
    data["summary"] = TB.summary(db, prog, data)
    return data


@router.get("/summary")
def summary(response: Response, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    _nostore(response)
    prog, who = _ctx(db, user)
    data = TB.work(db, prog, person=None if who["manager"] else who["name"])
    return TB.summary(db, prog, data)


class EditIn(BaseModel):
    changes: Dict[str, Any]


def _save(db, user, tab, row_id, body: EditIn):
    prog, who = _ctx(db, user)
    try:
        return TB.save(db, prog, who, user.id, tab, row_id, body.changes)
    except TB.BoardError as e:
        raise HTTPException(status_code=e.status, detail=str(e))


@router.patch("/appointments/{row_id}")
def edit_appointment(row_id: str, body: EditIn, db: Session = Depends(get_db),
                     user: User = Depends(require_tenant_user)):
    """Outcome, Sold?, What Was Sold, Team Notes, Call Link / Details (owner: Assigned To)."""
    return _save(db, user, RS.APPOINTMENTS, row_id, body)


@router.patch("/follow-ups/{row_id}")
def edit_follow_up(row_id: str, body: EditIn, db: Session = Depends(get_db),
                   user: User = Depends(require_tenant_user)):
    """Done?, Team Notes, Followed Up On, What Happened, and booking (Appointment
    Date + Time, How, Home Address) - owner: Assigned To."""
    return _save(db, user, RS.FOLLOW_UPS, row_id, body)


@router.get("/team")
def team(response: Response, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    _nostore(response)
    prog, who = _ctx(db, user)
    _manager(who)
    return {"team": TB.team(db, prog), "live_for_team": TB.is_live(prog)}
