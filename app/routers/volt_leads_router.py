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
from app.services.volt import lead_sources as S
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


@public_router.post("/{slug}/leads/hooks/{source_id}")
@limiter.limit("120/minute")
async def lead_hook(request: Request, slug: str, source_id: str, db: Session = Depends(get_db)):
    """Lead-source webhook (signed custom webhook or Google Ads lead form).
    Verified before anything is stored; see app/services/volt/lead_sources.py."""
    prog = C.program_by_slug(db, slug)
    raw = await request.body()
    try:
        out = S.receive_webhook(db, prog.organization_id, source_id, raw, request.headers)
    except Exception:
        db.commit()        # keep the rejected/duplicate counters
        raise
    db.commit()
    return out



@ops_router.get("/{org_id}/leads")
def ops_leads(org_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return L.listing(db, user, org_id, dict(request.query_params))


@ops_router.get("/{org_id}/leads/summary")
def ops_leads_summary(org_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Campaign card: counts and last activity only."""
    return L.summary(db, user, org_id)


@ops_router.get("/{org_id}/leads/report")
def ops_leads_report(org_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return L.report(db, user, org_id, L.is_test_param(request.query_params))


@ops_router.get("/{org_id}/leads/followup")
def ops_leads_followup(org_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return L.followup(db, user, org_id, L.is_test_param(request.query_params))


@ops_router.post("/{org_id}/leads/preview")
@limiter.limit("30/minute")
def ops_leads_preview(request: Request, org_id: str, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    """Signed-in website preview: writes a TEST lead only (works with intake off)."""
    out = L.preview_submit(db, user, org_id, body)
    db.commit()
    return out


@ops_router.post("/{org_id}/leads/test-data/clear")
def ops_leads_clear_test(org_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    out = L.clear_test(db, user, org_id)
    db.commit()
    return out


@ops_router.get("/{org_id}/leads/sources")
def ops_sources(org_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return S.listing(db, user, org_id)


@ops_router.post("/{org_id}/leads/sources")
def ops_sources_create(org_id: str, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    out = S.create(db, user, org_id, body)
    db.commit()
    return out


@ops_router.patch("/{org_id}/leads/sources/{source_id}")
def ops_sources_update(org_id: str, source_id: str, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    out = S.update(db, user, org_id, source_id, body)
    db.commit()
    return out


@ops_router.get("/{org_id}/leads/sources/{source_id}/deliveries")
def ops_sources_deliveries(org_id: str, source_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return S.deliveries(db, user, org_id, source_id)


@ops_router.post("/{org_id}/leads/sources/{source_id}/import")
def ops_sources_import(org_id: str, source_id: str, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    out = S.import_csv(db, user, org_id, source_id, body)
    db.commit()
    return out


@ops_router.post("/{org_id}/leads/deliveries/{delivery_id}/retry")
def ops_delivery_retry(org_id: str, delivery_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    out = S.retry(db, user, org_id, delivery_id)
    db.commit()
    return out


@ops_router.get("/{org_id}/leads/settings")
def ops_leads_settings(org_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    L.require_admin(db, user, org_id)
    out = L.settings_dict(L.settings_row(db, org_id))
    out["grants"] = L.grants(db, org_id)
    out["candidates"] = L.candidates(db, org_id)
    out["can_intake"] = C.staff_access(db, user, org_id) == "owner"
    out["can_make_manager"] = out["can_intake"]
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
    out = L.set_grant(db, org_id, user, body)
    db.commit()
    return {"grants": out}


@ops_router.get("/{org_id}/leads/agents")
def ops_leads_agents(org_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if L.access(db, user, org_id) != "all":
        return {"agents": [{"id": user.id, "name": user.full_name or user.email}]}
    return {"agents": L.assignable(db, org_id)}


@ops_router.get("/{org_id}/leads/{lead_id}")
def ops_lead(org_id: str, lead_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return L.detail(db, user, org_id, lead_id, L.is_test_param(request.query_params))


@ops_router.patch("/{org_id}/leads/{lead_id}")
def ops_lead_update(org_id: str, lead_id: str, request: Request, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    out = L.update(db, user, org_id, lead_id, body, L.is_test_param(request.query_params) or L.is_test_param(body))
    db.commit()
    return out


@ops_router.post("/{org_id}/leads/{lead_id}/assign")
def ops_lead_assign(org_id: str, lead_id: str, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    out = L.assign(db, user, org_id, lead_id, body)
    db.commit()
    return out
