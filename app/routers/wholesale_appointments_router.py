"""/wholesale/appointments - field appointments (build list #8-#10).

    GET    /wholesale/appointments?when=today|upcoming|past|all&day=&rep=&property=
    GET    /wholesale/appointments/reps         who this person may give an appointment to
    POST   /wholesale/appointments              book one
    PATCH  /wholesale/appointments/{id}         move, reassign, cancel, mark done / no-show, notes

Visibility and who-may-change rules: app/services/wholesale_appointments.py.
Nothing here sends a message or calls anyone.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, require_not_observation, require_tenant_user
from app.models.models import User
from app.services import wholesale_appointments as A
from app.services import wholesale_service as svc
from app.services.entitlements import require_feature

router = APIRouter(prefix="/wholesale/appointments", tags=["wholesale-appointments"],
                   dependencies=[Depends(require_feature("wholesale_real_estate"))])


class BookIn(BaseModel):
    starts_at: str
    duration_minutes: int = 60
    assigned_to_id: Optional[str] = None
    evosense_property_id: Optional[str] = None
    property_id: Optional[str] = None
    deal_id: Optional[str] = None
    lead_id: Optional[str] = None
    address: Optional[str] = None
    title: Optional[str] = None
    notes: Optional[str] = None
    source: Optional[str] = None
    source_ref: Optional[str] = None


class ChangeIn(BaseModel):
    starts_at: Optional[str] = None
    duration_minutes: Optional[int] = None
    assigned_to_id: Optional[str] = None     # "" = unassign
    address: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[str] = None


@router.get("")
def list_appointments(when: str = Query("today"), day: Optional[str] = None, rep: Optional[str] = None,
                      property: Optional[str] = None, include_test: bool = False,
                      db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    return A.listing(db, svc.read_org_id(db, user), user, when=when, day=day, rep_id=rep,
                     property_id=property, include_test=include_test)


@router.get("/reps")
def list_reps(db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    return {"items": A.reps(db, svc.read_org_id(db, user), user)}


@router.post("", dependencies=[Depends(require_not_observation)])
def book(body: BookIn, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    org_id = svc.write_org_id(db, user)
    return A.appt_json(db, A.book(db, org_id, user, body.model_dump()))


@router.patch("/{appt_id}", dependencies=[Depends(require_not_observation)])
def change(appt_id: str, body: ChangeIn, db: Session = Depends(get_db),
           user: User = Depends(require_tenant_user)):
    org_id = svc.write_org_id(db, user)
    data = body.model_dump(exclude_unset=True)
    return A.appt_json(db, A.change(db, org_id, user, appt_id, data))
