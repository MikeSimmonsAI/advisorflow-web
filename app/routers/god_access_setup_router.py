"""God-only: set up the SCI workspace for a login, and name who may use EvoSys
Wholesale. Every write is behind require_god; every SCI step is dry-run first."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, require_god
from app.models.models import User
from app.services import product_access, sci_provision, wholesale_provision

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


class WholesaleWorkspaceIn(BaseModel):
    name: str = ""
    brand: str = ""                 # platforms.slug, e.g. "bookaboost"
    organization_id: Optional[str] = None
    emails: List[str] = []
    role: str = "org_admin"
    industry: str = "generic"
    confirm: str = ""


@router.get("/wholesale-workspace")
def wholesale_workspace_plan(name: str = Query(""), brand: str = Query(""), organization_id: str = Query(""),
                             emails: str = Query(""), role: str = Query("org_admin"),
                             db: Session = Depends(get_db), _god: User = Depends(require_god)):
    """What provisioning this Wholesale workspace would do (LANE-E #272). Writes nothing."""
    return wholesale_provision.plan(db, name=name, brand_slug=brand, org_id=organization_id or None,
                                    emails=[e for e in emails.split(",") if e.strip()], role=role)


@router.post("/wholesale-workspace")
def wholesale_workspace_apply(body: WholesaleWorkspaceIn, db: Session = Depends(get_db),
                              god: User = Depends(require_god)):
    if (body.confirm or "").strip().upper() != wholesale_provision.CONFIRM:
        raise HTTPException(status_code=422, detail="Type %s to confirm." % wholesale_provision.CONFIRM)
    try:
        return wholesale_provision.apply(db, god, name=body.name, brand_slug=body.brand,
                                         org_id=body.organization_id or None, emails=body.emails,
                                         role=body.role, industry=body.industry)
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
