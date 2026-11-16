"""VOLT consumer leads (DEV-4). Mounted from app/routers/volt_router.py, so the
public routes sit under /volt/public/{slug}/leads* and the staff routes under
/volt/ops/{org_id}/leads* behind the same VOLT guard as every recruiting
staff route. All authorization is in app/services/volt/leads.py."""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.limiter import limiter
from app.models.models import User
from app.services.volt import common as C
from app.services.volt import lead_sources as S
from app.services.volt import sheets_outbox as SO
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


@ops_router.get("/{org_id}/leads/export.csv")
def ops_leads_csv(org_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Consumer Leads CSV: exactly the leads this person may see (agents: their
    own), with the same filters as the inbox plus a date range. UTF-8 with a
    byte-order mark (opens correctly in Excel), formula-safe cells, audited."""
    from fastapi.responses import Response
    params = dict(request.query_params)
    body, count = L.csv_export(db, user, org_id, params)
    C.audit(db, org_id, user, "export", "consumer_leads", "lead_csv_export", new=str(count),
            reason=",".join("%s=%s" % (k, v) for k, v in sorted(params.items()) if k != "q")[:500] or None)
    db.commit()
    name = "consumer-leads%s.csv" % ("-TEST-DATA" if L.is_test_param(params) else "")
    return Response(content=body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="%s"' % name, "Cache-Control": "no-store"})


@ops_router.get("/{org_id}/sheets/status")
def ops_sheets_status(org_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    SO.require_operator(db, user, org_id)
    return SO.status(db, org_id)


def _sheets_business(business: str) -> str:
    if business not in SO.BUSINESSES:
        raise HTTPException(status_code=404, detail="Unknown business.")
    return business


def _sheets_org(org_id: str) -> None:
    if SO.approved_org() != org_id:
        raise HTTPException(status_code=409, detail="Google Sheets export is not approved for this organization.")


@ops_router.post("/{org_id}/sheets/{business}/retry")
def ops_sheets_retry(org_id: str, business: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    SO.require_operator(db, user, org_id)
    _sheets_org(org_id)
    n = SO.retry_failed(db, org_id, _sheets_business(business))
    C.audit(db, org_id, user, "google_sheets", business, "retry_failed", new=str(n))
    db.commit()
    return {"requeued": n}


@ops_router.post("/{org_id}/sheets/{business}/reconcile")
def ops_sheets_reconcile(org_id: str, business: str, body: Dict[str, Any] = Body(default={}),
                         db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Re-queue records whose Sheet row may be behind. full=true (every real
    record - the first backfill) is the owner's decision only."""
    SO.require_operator(db, user, org_id)
    _sheets_org(org_id)
    full = body.get("full") is True
    if full and C.staff_access(db, user, org_id) != "owner":
        raise HTTPException(status_code=403, detail="Only the owner can copy every existing record into the Sheet.")
    n = SO.reconcile(db, org_id, _sheets_business(business), full=full)
    C.audit(db, org_id, user, "google_sheets", business, "reconcile_full" if full else "reconcile", new=str(n))
    db.commit()
    return {"queued": n, "full": full}


@ops_router.post("/{org_id}/sheets/{business}/verify")
def ops_sheets_verify(org_id: str, business: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    SO.require_operator(db, user, org_id)
    _sheets_org(org_id)
    out = SO.verify_now(db, org_id, _sheets_business(business))
    C.audit(db, org_id, user, "google_sheets", business, "verify", new="ok" if out["ok"] else out["category"])
    db.commit()
    return out


@ops_router.post("/{org_id}/sheets/{business}/pause")
def ops_sheets_pause(org_id: str, business: str, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    SO.require_operator(db, user, org_id)
    _sheets_org(org_id)
    paused = body.get("paused") is True
    SO.set_paused(db, org_id, _sheets_business(business), paused)
    C.audit(db, org_id, user, "google_sheets", business, "paused" if paused else "resumed")
    db.commit()
    return SO.status(db, org_id)


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
    out = L.settings_dict(L.settings_row(db, org_id), db)
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


@ops_router.put("/{org_id}/leads/settings/collector")
def ops_leads_collector(org_id: str, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    """Owner only: the legal company that collects the information (#619)."""
    L.require_owner(db, user, org_id)
    out = L.set_collector(db, org_id, user, body)
    db.commit()
    return out


@ops_router.put("/{org_id}/leads/grants/{member_id}/license")
def ops_leads_license(org_id: str, member_id: str, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    """Owner only: record an agent's verified health insurance license (#619)."""
    L.require_owner(db, user, org_id)
    out = L.set_license(db, org_id, user, member_id, body)
    db.commit()
    return out


@ops_router.post("/{org_id}/leads/{lead_id}/opt-out")
def ops_lead_opt_out(org_id: str, lead_id: str, body: Dict[str, Any] = Body(default={}), db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    out = L.record_opt_out(db, user, org_id, lead_id, body or {})
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
