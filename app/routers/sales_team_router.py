"""MY TEAM + SALES ONBOARDING — the EvoSys Pro sales team, run from Back Office.

Every rule lives in app/services/sales_team.py and sales_onboarding.py; this
file is the HTTP surface. Each route re-checks the caller on the server.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models.models import User
from app.models.sales_team_models import SalesOnboarding, SalesOnboardingDocument
from app.services import sales_onboarding as onb
from app.services import sales_team as team

router = APIRouter(prefix="/sales-team", tags=["sales-team"])
_NO_STORE = {"Cache-Control": "no-store, private"}


def _brand(db: Session):
    b = team.program_brand(db)
    if b is None:
        raise HTTPException(status_code=503, detail="The EvoSys Pro sales organization is not set up.")
    return b


@router.get("/me")
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    brand = team.program_brand(db)
    rights = team.actor_rights(db, user)
    rec = onb.record(db, user.id, brand.id) if brand else None
    return {"rights": rights,
            "onboarding": (onb.checklist(db, rec, with_file_names=True) if rec else None),
            "approved": bool(rec and rec.status == "approved")}


@router.get("/roster")
def roster(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return Response(content=__import__("json").dumps(team.roster(db, user)),
                    media_type="application/json", headers=_NO_STORE)


class InviteIn(BaseModel):
    email: str
    full_name: str
    seat: str
    manager_id: Optional[str] = None
    base_url: Optional[str] = None


@router.post("/invite")
def invite(body: InviteIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return team.invite(db, user, email=body.email, full_name=body.full_name, seat_key=body.seat,
                       manager_id=body.manager_id, base_url=body.base_url)


@router.post("/members/{user_id}/suspend")
def suspend(user_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    team.set_suspended(db, user, user_id, True)
    return {"ok": True}


@router.post("/members/{user_id}/reactivate")
def reactivate(user_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    team.set_suspended(db, user, user_id, False)
    return {"ok": True}


class MoveIn(BaseModel):
    manager_id: str


@router.post("/members/{user_id}/move")
def move(user_id: str, body: MoveIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    team.move(db, user, user_id, body.manager_id)
    return {"ok": True}


class DepartIn(BaseModel):
    departed_at: Optional[str] = None
    note: Optional[str] = None


@router.post("/members/{user_id}/depart")
def depart(user_id: str, body: DepartIn, user: User = Depends(get_current_user),
           db: Session = Depends(get_db)):
    when = None
    if body.departed_at:
        try:
            when = datetime.fromisoformat(body.departed_at[:19])
        except ValueError:
            raise HTTPException(status_code=422, detail="Departure date must be a date.")
    team.record_departure(db, user, user_id, when, body.note)
    return {"ok": True}


# ── onboarding: the person's own ─────────────────────────────────────────────

def _own(db: Session, user: User) -> SalesOnboarding:
    brand = _brand(db)
    rec = onb.record(db, user.id, brand.id)
    if rec is None:
        if team.seat(db, user, brand) in (None, team.SEAT_GOD, team.SEAT_OWNER):
            raise HTTPException(status_code=404, detail="You don't have a sales onboarding to complete.")
        rec = onb.open_for(db, user, brand.id)
        db.commit()
    return rec


@router.get("/onboarding/me")
def my_onboarding(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rec = _own(db, user)
    return Response(content=__import__("json").dumps(
        {"application": onb.application(rec), "checklist": onb.checklist(db, rec, with_file_names=True),
         "fields": onb.APPLICATION_LABELS}),
        media_type="application/json", headers=_NO_STORE)


@router.put("/onboarding/me/application")
def save_application(body: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rec = _own(db, user)
    onb.save_application(db, rec, body or {})
    db.commit()
    return {"application": onb.application(rec), "checklist": onb.checklist(db, rec, with_file_names=True)}


@router.post("/onboarding/me/documents/{kind}")
async def upload_document(kind: str, file: UploadFile = File(...), user: User = Depends(get_current_user),
                          db: Session = Depends(get_db)):
    rec = _own(db, user)
    await onb.upload(db, rec, kind, file, user)
    db.commit()
    return {"checklist": onb.checklist(db, rec, with_file_names=True)}


@router.post("/onboarding/me/submit")
def submit(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rec = _own(db, user)
    onb.submit(db, rec)
    db.commit()
    return {"checklist": onb.checklist(db, rec, with_file_names=True)}


# ── onboarding: owners and God ───────────────────────────────────────────────

def _admin_record(db: Session, user: User, onboarding_id: str) -> SalesOnboarding:
    if not team.actor_rights(db, user)["can_approve"]:
        raise HTTPException(status_code=403, detail="Only an owner can do that.")
    rec = db.query(SalesOnboarding).filter(SalesOnboarding.id == onboarding_id).first()
    if rec is None or rec.brand_sales_org_id != _brand(db).id:
        raise HTTPException(status_code=404, detail="Onboarding not found.")
    return rec


@router.get("/onboarding/{onboarding_id}")
def onboarding_detail(onboarding_id: str, user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    rec = _admin_record(db, user, onboarding_id)
    return Response(content=__import__("json").dumps(
        {"application": onb.application(rec), "checklist": onb.checklist(db, rec, with_file_names=True)}),
        media_type="application/json", headers=_NO_STORE)


@router.post("/onboarding/{onboarding_id}/approve")
def approve(onboarding_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rec = _admin_record(db, user, onboarding_id)
    onb.approve(db, rec, user)
    db.commit()
    return {"ok": True}


class SendBackIn(BaseModel):
    note: Optional[str] = None


@router.post("/onboarding/{onboarding_id}/send-back")
def send_back(onboarding_id: str, body: SendBackIn, user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    rec = _admin_record(db, user, onboarding_id)
    onb.send_back(db, rec, user, body.note or "")
    db.commit()
    return {"ok": True}


@router.get("/onboarding/{onboarding_id}/documents/{document_id}")
def download(onboarding_id: str, document_id: str, inline: bool = False,
             user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rec = _admin_record(db, user, onboarding_id)
    doc = (db.query(SalesOnboardingDocument)
           .filter(SalesOnboardingDocument.id == document_id,
                   SalesOnboardingDocument.onboarding_id == rec.id).first())
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    data = onb.read_document(db, doc, rec, user, "viewed" if inline else "downloaded")
    db.commit()
    disp = "inline" if inline else "attachment"
    return Response(content=data, media_type=doc.content_type,
                    headers={**_NO_STORE, "Content-Disposition": '%s; filename="%s"' % (disp, doc.filename),
                             "X-Content-Type-Options": "nosniff"})


# ── unclaimed commission: owners and God only ────────────────────────────────

@router.get("/unclaimed")
def unclaimed(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Commission no one was paid because the seat was empty or the person had
    left the company, plus refunds with commission still standing. EvoSys keeps
    unclaimed money; it never raises anyone else's pay."""
    if not team.actor_rights(db, user)["is_admin"]:
        raise HTTPException(status_code=403, detail="Only the owners can see unclaimed commission.")
    from app.services import sales_program_comp
    return sales_program_comp.unclaimed_report(db)
