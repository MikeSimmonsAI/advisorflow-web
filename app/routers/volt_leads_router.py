"""VOLT consumer leads (DEV-4). Mounted from app/routers/volt_router.py, so the
public routes sit under /volt/public/{slug}/leads* and the staff routes under
/volt/ops/{org_id}/leads* behind the same VOLT guard as every recruiting
staff route. All authorization is in app/services/volt/leads.py."""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, Request
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.limiter import limiter
from app.models.models import User
from app.services.volt import common as C
from app.services.volt import leads as L

public_router = APIRouter()
ops_router = APIRouter()

OK = {"ok": True, "message": "Thanks. Your request was received. A licensed agent may contact you."}


@public_router.get("/{slug}/leads/config")
@limiter.limit("120/minute")
def lead_config(request: Request, slug: str, db: Session = Depends(get_db)):
    return L.public_config(db, C.program_by_slug(db, slug).organization_id)


@public_router.post("/{slug}/leads")
@limiter.limit("10/minute")
def lead_submit(request: Request, slug: str, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    prog = C.program_by_slug(db, slug)
    L.submit(db, prog.organization_id, body)
    db.commit()
    return OK



@ops_router.get("/{org_id}/leads")
def ops_leads(org_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return L.listing(db, user, org_id, dict(request.query_params))


@ops_router.get("/{org_id}/leads/report")
def ops_leads_report(org_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return L.report(db, user, org_id)


@ops_router.get("/{org_id}/leads/settings")
def ops_leads_settings(org_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    L.require_owner(db, user, org_id)
    out = L.settings_dict(L.settings_row(db, org_id))
    out["grants"] = L.grants(db, org_id)
    out["assignable"] = L.assignable(db, org_id)
    db.commit()
    return out


@ops_router.put("/{org_id}/leads/settings")
def ops_leads_settings_put(org_id: str, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                           user: User = Depends(get_current_user)):
    L.require_owner(db, user, org_id)
    out = L.set_intake(db, org_id, user, body)
    db.commit()
    return out


@ops_router.post("/{org_id}/leads/grants")
def ops_leads_grant(org_id: str, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    L.require_owner(db, user, org_id)
    out = L.set_grant(db, org_id, user, body)
    db.commit()
    return {"grants": out}


@ops_router.get("/{org_id}/leads/agents")
def ops_leads_agents(org_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if L.access(db, user, org_id) != "all":
        return {"agents": [{"id": user.id, "name": user.full_name or user.email}]}
    return {"agents": L.assignable(db, org_id)}


@ops_router.get("/{org_id}/leads/{lead_id}")
def ops_lead(org_id: str, lead_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return L.detail(db, user, org_id, lead_id)


@ops_router.patch("/{org_id}/leads/{lead_id}")
def ops_lead_update(org_id: str, lead_id: str, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    out = L.update(db, user, org_id, lead_id, body)
    db.commit()
    return out


@ops_router.post("/{org_id}/leads/{lead_id}/assign")
def ops_lead_assign(org_id: str, lead_id: str, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    out = L.assign(db, user, org_id, lead_id, body)
    db.commit()
    return out
