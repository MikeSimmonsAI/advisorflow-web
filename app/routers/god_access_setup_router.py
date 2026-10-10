"""God-only: set up the SCI workspace for a login, and name who may use EvoSys
Wholesale. Every write is behind require_god; every SCI step is dry-run first."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, require_god
from app.models.models import User
from app.services import product_access, sci_provision

router = APIRouter(prefix="/god/access-setup", tags=["god"])


class SciIn(BaseModel):
    email: str
    confirm: str = ""


@router.get("/sci")
def sci_plan(email: str = Query(""), db: Session = Depends(get_db), _god: User = Depends(require_god)):
    """What setting up SCI for this login would do. Writes nothing."""
    return sci_provision.plan(db, email)


@router.post("/sci")
def sci_apply(body: SciIn, db: Session = Depends(get_db), god: User = Depends(require_god)):
    if (body.confirm or "").strip().upper() != "SET UP SCI":
        raise HTTPException(status_code=422, detail='Type SET UP SCI to confirm.')
    try:
        return sci_provision.apply(db, god, body.email)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


class EmailIn(BaseModel):
    email: str


@router.get("/wholesale")
def wholesale_access(db: Session = Depends(get_db), _god: User = Depends(require_god)):
    return {"lock_on": product_access.lock_on(db), "holders": product_access.holders(db),
            "rule": "God mode always; otherwise only the logins listed here once the lock is on."}


@router.post("/wholesale/grant")
def wholesale_grant(body: EmailIn, db: Session = Depends(get_db), god: User = Depends(require_god)):
    try:
        out = product_access.grant(db, body.email, granted_by=god.id)
    except (ValueError, LookupError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {**out, "holders": product_access.holders(db)}


@router.post("/wholesale/revoke")
def wholesale_revoke(body: EmailIn, db: Session = Depends(get_db), _god: User = Depends(require_god)):
    try:
        out = product_access.revoke(db, body.email)
    except (ValueError, LookupError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {**out, "holders": product_access.holders(db)}
