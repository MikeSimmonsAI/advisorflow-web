"""/wholesale/appointments - field appointments (build list #8-#10).

    GET    /wholesale/appointments?when=today|upcoming|past|all&day=&rep=&property=
    GET    /wholesale/appointments/reps         who this person may give an appointment to
    POST   /wholesale/appointments              book one
    PATCH  /wholesale/appointments/{id}         move, reassign, cancel, mark done / no-show, notes
    GET    /wholesale/appointments/{id}/visit   the visit record: interview, repairs, photos (#12, #13)
    PUT    /wholesale/appointments/{id}/visit   save interview and / or repairs
    POST   /wholesale/appointments/{id}/photos  one walkthrough photo (multipart: file, room, caption)
    GET    /wholesale/appointments/{id}/photos/{photo_id}
    DELETE /wholesale/appointments/{id}/photos/{photo_id}

Visibility and who-may-change rules: app/services/wholesale_appointments.py.
Nothing here sends a message or calls anyone.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, require_not_observation, require_tenant_user
from app.models.models import User
from app.services import wholesale_appointments as A
from app.services import wholesale_field_visits as V
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

class VisitIn(BaseModel):
    interview: Optional[dict] = None
    repairs: Optional[dict] = None


@router.get("/{appt_id}/visit")
def get_visit(appt_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    return V.get_visit(db, svc.read_org_id(db, user), user, appt_id)


@router.put("/{appt_id}/visit", dependencies=[Depends(require_not_observation)])
def save_visit(appt_id: str, body: VisitIn, db: Session = Depends(get_db),
               user: User = Depends(require_tenant_user)):
    return V.save_visit(db, svc.write_org_id(db, user), user, appt_id, body.model_dump())


@router.post("/{appt_id}/photos", dependencies=[Depends(require_not_observation)])
async def add_photo(appt_id: str, file: UploadFile = File(...), room: Optional[str] = Form(None),
                    caption: Optional[str] = Form(None), db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_user)):
    return await V.add_photo(db, svc.write_org_id(db, user), user, appt_id, file, room, caption)


@router.get("/{appt_id}/photos/{photo_id}")
def photo(appt_id: str, photo_id: str, db: Session = Depends(get_db),
          user: User = Depends(require_tenant_user)):
    data, ctype = V.photo_bytes(db, svc.read_org_id(db, user), user, appt_id, photo_id)
    return Response(content=data, media_type=ctype, headers={"Cache-Control": "private, max-age=3600"})


@router.delete("/{appt_id}/photos/{photo_id}", dependencies=[Depends(require_not_observation)])
def delete_photo(appt_id: str, photo_id: str, db: Session = Depends(get_db),
                 user: User = Depends(require_tenant_user)):
    V.remove_photo(db, svc.write_org_id(db, user), user, appt_id, photo_id)
    return {"ok": True}
