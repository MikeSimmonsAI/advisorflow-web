"""SERVICE OPERATIONS (Issue #452 / #506) - the service company's own
operations inside its existing private portal.

/service-ops/*                    the company's staff (role-based; workers see
                                  only their assigned work)
/service-ops/buyer/*              the company's OWN customers (optional buyer
                                  portal), each scoped to its own records
/acquisition-ops/{org}/service-ops  EvoSys switches the feature on per company
/god/acquisition/buyer-network    EvoSys multi-service buyer requests

Same authentication as the rest of EvoSys. The company is the workspace the
caller is standing in; every query filters on it. Nothing here sends email,
texts, charges money or calls an AI model unless a separate switch says so.
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.deps import get_db, require_not_observation, require_tenant_user
from app.models.models import Organization, User
from app.services import lead_scope
from app.services.acquisition import common as AC
from app.services.acquisition.admins import require_acq_master
from app.services.entitlements import require_feature
from app.services.platform_owner import require_tenant_context
from app.services.service_ops import billing as BL
from app.services.service_ops import buyer as BY
from app.services.service_ops import core as K
from app.services.service_ops import insights as IN
from app.services.service_ops import jobs as J
from app.services.service_ops import recurring as R

router = APIRouter(prefix="/service-ops", tags=["service-ops"],
                   dependencies=[Depends(require_feature(AC.FEATURE))])
admin_router = APIRouter(tags=["service-ops"])
_WRITE = [Depends(require_not_observation)]


def _company(request: Request, db: Session, user: User) -> Organization:
    from app.services.platform_owner import is_platform_pseudo_org
    org_id = lead_scope.active_workspace_org_id(user, db, request)
    if not org_id or is_platform_pseudo_org(org_id):
        raise HTTPException(409, "No business is selected. Select a workspace first.")
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if org is None or not AC.acquisition_identity(org):
        raise HTTPException(403, "This workspace does not have the client portal.")
    return org


def ops(request: Request, db: Session = Depends(get_db), user: User = Depends(require_tenant_context),
        _t: User = Depends(require_tenant_user)) -> K.OpsCtx:
    org = _company(request, db, user)
    return K.resolve(db, user, org, is_manager=lead_scope.is_manager_here(user, db, request), request=request)


def buyer(request: Request, db: Session = Depends(get_db), user: User = Depends(require_tenant_context),
          _t: User = Depends(require_tenant_user)) -> BY.BuyerCtx:
    org = _company(request, db, user)
    return BY.resolve_buyer(db, user, org)


# ── who am I here ─────────────────────────────────────────────────────────
@router.get("/me")
def me(request: Request, db: Session = Depends(get_db), user: User = Depends(require_tenant_context),
       _t: User = Depends(require_tenant_user)):
    """What this login can use here. Never errors for a company with the
    feature off - the portal just shows no Service Operations menu."""
    org = _company(request, db, user)
    s = K.settings(db, org.id)
    out = {"ops_enabled": bool(s and s.ops_enabled), "buyer_portal_enabled": bool(s and s.buyer_portal_enabled),
           "advanced_enabled": bool(s and s.advanced_enabled), "ai_assist_enabled": bool(s and s.ai_assist_enabled),
           "role": None, "role_label": None, "perms": [], "is_buyer": False}
    if not out["ops_enabled"]:
        return out
    try:
        ctx = K.resolve(db, user, org, is_manager=lead_scope.is_manager_here(user, db, request))
        out.update(role=ctx.role, role_label=K.ROLE_LABEL.get(ctx.role), perms=sorted(K.PERMS.get(ctx.role, [])),
                   member_id=ctx.member_id, timezone=K.company_tz(db, org.id))
    except HTTPException:
        a = db.query(BY.ServiceBuyerAccess).filter(BY.ServiceBuyerAccess.organization_id == org.id,
                                                   BY.ServiceBuyerAccess.user_id == user.id).first()
        out["is_buyer"] = bool(a and a.status != "revoked" and out["buyer_portal_enabled"])
    return out


@router.get("/settings")
def get_settings(ctx: K.OpsCtx = Depends(ops)):
    return K.settings_json(ctx.settings)


@router.put("/settings", dependencies=_WRITE)
def put_settings(payload: dict = Body(...), ctx: K.OpsCtx = Depends(ops)):
    ctx.need("settings", "change company settings")
    bad = [k for k in payload if k in K.ENTITLEMENT_FLAGS]
    if bad:
        raise HTTPException(403, "Features are switched on by EvoSys")
    return K.settings_json(K.save_settings(ctx.db, ctx.org_id, ctx.user, payload, entitlements=False))


# ── customers & locations ─────────────────────────────────────────────────
@router.get("/customers")
def customers(archived: bool = False, ctx: K.OpsCtx = Depends(ops)):
    return {"items": K.list_customers(ctx, include_archived=archived), "categories": [
        {"key": c, "label": K.CATEGORY_LABEL[c]} for c in K.CATEGORIES]}


@router.post("/customers", dependencies=_WRITE)
def add_customer(payload: dict = Body(...), ctx: K.OpsCtx = Depends(ops)):
    c = K.create_customer(ctx, payload)
    if payload.get("location"):
        K.create_location(ctx, c.id, payload["location"])
    ctx.db.commit()
    return K.customer_json(c)


@router.get("/customers/{cid}")
def customer(cid: str, ctx: K.OpsCtx = Depends(ops)):
    ctx.need("view_all", "see customers")
    c = K.get_customer(ctx.db, ctx.org_id, cid)
    locs = ctx.db.query(K.ServiceLocation).filter(K.ServiceLocation.organization_id == ctx.org_id,
                                                  K.ServiceLocation.customer_id == c.id).all()
    return dict(K.customer_json(c, locations=[K.location_json(ctx, l) for l in locs]),
                jobs=J.list_jobs(ctx, customer_id=c.id, limit=100))


@router.put("/customers/{cid}", dependencies=_WRITE)
def edit_customer(cid: str, payload: dict = Body(...), ctx: K.OpsCtx = Depends(ops)):
    return K.customer_json(K.update_customer(ctx, cid, payload))


@router.post("/customers/{cid}/locations", dependencies=_WRITE)
def add_location(cid: str, payload: dict = Body(...), ctx: K.OpsCtx = Depends(ops)):
    l = K.create_location(ctx, cid, payload)
    ctx.db.commit()
    return K.location_json(ctx, l)


@router.get("/locations")
def locations(ctx: K.OpsCtx = Depends(ops)):
    ctx.need("view_all", "see every location")
    rows = ctx.db.query(K.ServiceLocation).filter(K.ServiceLocation.organization_id == ctx.org_id).all()
    return {"items": [K.location_json(ctx, l) for l in rows]}


@router.get("/locations/{lid}")
def location(lid: str, ctx: K.OpsCtx = Depends(ops)):
    l = K.get_location(ctx.db, ctx.org_id, lid)
    if not (ctx.can("view_all") or K.assigned_to_location(ctx, l)):
        raise HTTPException(404, "Location not found")
    return dict(K.location_json(ctx, l), jobs=J.list_jobs(ctx, location_id=l.id, limit=100))


@router.put("/locations/{lid}", dependencies=_WRITE)
def edit_location(lid: str, payload: dict = Body(...), ctx: K.OpsCtx = Depends(ops)):
    return K.location_json(ctx, K.update_location(ctx, lid, payload))


# ── team ──────────────────────────────────────────────────────────────────
@router.get("/team")
def team(ctx: K.OpsCtx = Depends(ops)):
    return {"items": K.list_team(ctx), "roles": [{"key": r, "label": K.ROLE_LABEL[r]} for r in K.ROLES]}


@router.get("/team/workload")
def team_workload(days: int = Query(14, ge=1, le=60), ctx: K.OpsCtx = Depends(ops)):
    return {"items": J.workload(ctx, days)}


@router.post("/team", dependencies=_WRITE)
def invite(payload: dict = Body(...), ctx: K.OpsCtx = Depends(ops)):
    return K.invite_member(ctx, payload)


@router.put("/team/{mid}", dependencies=_WRITE)
def edit_member(mid: str, payload: dict = Body(...), ctx: K.OpsCtx = Depends(ops)):
    return K.update_member(ctx, mid, payload)


# ── checklists ────────────────────────────────────────────────────────────
@router.get("/checklists")
def checklists(ctx: K.OpsCtx = Depends(ops)):
    rows = ctx.db.query(J.ServiceChecklistTemplate).filter(
        J.ServiceChecklistTemplate.organization_id == ctx.org_id).all()
    return {"items": [J.template_json(t) for t in rows],
            "examples": {k: v for k, v in J.DEFAULT_CHECKLISTS.items()}}


@router.post("/checklists", dependencies=_WRITE)
def add_checklist(payload: dict = Body(...), ctx: K.OpsCtx = Depends(ops)):
    return J.save_template(ctx, payload)


@router.put("/checklists/{tid}", dependencies=_WRITE)
def edit_checklist(tid: str, payload: dict = Body(...), ctx: K.OpsCtx = Depends(ops)):
    return J.save_template(ctx, payload, tid)


# ── won contracts -> jobs ─────────────────────────────────────────────────
@router.get("/won")
def won_contracts(ctx: K.OpsCtx = Depends(ops)):
    """Won Acquisition contracts and whether each already became a job."""
    ctx.need("schedule", "see won contracts")
    from app.services.acquisition import engine as AE
    items = [x for x in AE.list_client(ctx.db, ctx.org_id, None) if x["stage"] == AC.WON]
    conv = {j.opportunity_id: j.id for j in ctx.db.query(J.ServiceJob).filter(
        J.ServiceJob.organization_id == ctx.org_id, J.ServiceJob.opportunity_id.isnot(None))}
    sch = {s.opportunity_id: s.id for s in ctx.db.query(R.ServiceRecurringSchedule).filter(
        R.ServiceRecurringSchedule.organization_id == ctx.org_id, R.ServiceRecurringSchedule.opportunity_id.isnot(None))}
    return {"items": [dict(x, job_id=conv.get(x["id"]), schedule_id=sch.get(x["id"])) for x in items]}


@router.post("/won/{opp_id}/convert", dependencies=_WRITE)
def convert(opp_id: str, payload: dict = Body(default={}), ctx: K.OpsCtx = Depends(ops)):
    return J.convert_opportunity(ctx, opp_id, payload or {})


# ── jobs ──────────────────────────────────────────────────────────────────
def _dt(v: Optional[str]) -> Optional[datetime]:
    if not v:
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", ""))
    except ValueError:
        raise HTTPException(422, "Bad date")


@router.get("/jobs")
def jobs(status: Optional[str] = None, start: Optional[str] = None, end: Optional[str] = None,
         mine: bool = False, customer_id: Optional[str] = None, location_id: Optional[str] = None,
         ctx: K.OpsCtx = Depends(ops)):
    return {"items": J.list_jobs(ctx, status=status, start=_dt(start), end=_dt(end), mine=mine,
                                 customer_id=customer_id, location_id=location_id),
            "statuses": [{"key": s, "label": J.STATUS_LABEL[s]} for s in J.STATUSES]}


@router.post("/jobs", dependencies=_WRITE)
def add_job(payload: dict = Body(...), ctx: K.OpsCtx = Depends(ops)):
    job, created = J.new_job(ctx, payload)
    return dict(J.job_json(ctx, job, full=True), created=created)


@router.get("/jobs/{jid}")
def job(jid: str, ctx: K.OpsCtx = Depends(ops)):
    return J.job_json(ctx, J.view_job(ctx, jid), full=True)


@router.put("/jobs/{jid}", dependencies=_WRITE)
def edit_job(jid: str, payload: dict = Body(...), ctx: K.OpsCtx = Depends(ops)):
    return J.job_json(ctx, J.update_job(ctx, jid, payload), full=True)


@router.post("/jobs/{jid}/assign", dependencies=_WRITE)
def assign(jid: str, payload: dict = Body(...), ctx: K.OpsCtx = Depends(ops)):
    return J.job_json(ctx, J.set_assignment(ctx, jid, payload.get("add") or [], payload.get("remove") or []), full=True)


@router.post("/jobs/{jid}/status", dependencies=_WRITE)
def job_status(jid: str, payload: dict = Body(...), ctx: K.OpsCtx = Depends(ops)):
    return J.job_json(ctx, J.change_status(ctx, jid, payload.get("status"), payload), full=True)


@router.post("/jobs/{jid}/skip", dependencies=_WRITE)
def skip(jid: str, payload: dict = Body(default={}), ctx: K.OpsCtx = Depends(ops)):
    return J.job_json(ctx, R.skip_occurrence(ctx, jid, (payload or {}).get("reason")), full=True)


@router.post("/jobs/{jid}/start", dependencies=_WRITE)
def start(jid: str, ctx: K.OpsCtx = Depends(ops)):
    return J.job_json(ctx, J.start_job(ctx, jid), full=True)


@router.post("/jobs/{jid}/checklist", dependencies=_WRITE)
def checklist_tick(jid: str, payload: dict = Body(...), ctx: K.OpsCtx = Depends(ops)):
    return J.job_json(ctx, J.tick(ctx, jid, payload.get("key"), bool(payload.get("done"))), full=True)


@router.post("/jobs/{jid}/complete", dependencies=_WRITE)
def complete(jid: str, payload: dict = Body(default={}), ctx: K.OpsCtx = Depends(ops)):
    return J.job_json(ctx, J.complete_job(ctx, jid, payload or {}), full=True)


@router.post("/jobs/{jid}/note", dependencies=_WRITE)
def note(jid: str, payload: dict = Body(...), ctx: K.OpsCtx = Depends(ops)):
    return J.job_json(ctx, J.worker_note(ctx, jid, payload.get("kind") or "note", payload.get("body")), full=True)


@router.post("/jobs/{jid}/photos", dependencies=_WRITE)
async def photo(jid: str, file: UploadFile = File(...), caption: Optional[str] = Form(None),
                ctx: K.OpsCtx = Depends(ops)):
    from app.services import wholesale_files as FS
    data, ctype, name = await FS.read_upload(file, images_only=True)
    return J.attachment_json(J.add_attachment(ctx, jid, data, ctype, name, caption))


@router.get("/jobs/{jid}/photos/{aid}")
def photo_file(jid: str, aid: str, ctx: K.OpsCtx = Depends(ops)):
    a, data = J.read_attachment(ctx, jid, aid)
    return Response(content=data, media_type=a.content_type,
                    headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
                             "Content-Disposition": 'inline; filename="photo%s"' % (
                                 {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(a.content_type, ""))})


@router.get("/jobs/{jid}/ai-summary")
def job_ai_summary(jid: str, ctx: K.OpsCtx = Depends(ops)):
    return IN.ai_summary(ctx, jid)


# ── recurring service ─────────────────────────────────────────────────────
@router.get("/schedules")
def schedules(ctx: K.OpsCtx = Depends(ops)):
    return {"items": R.list_schedules(ctx),
            "frequencies": [{"key": f, "label": R.FREQ_LABEL[f]} for f in R.FREQUENCIES]}


@router.post("/schedules", dependencies=_WRITE)
def add_schedule(payload: dict = Body(...), ctx: K.OpsCtx = Depends(ops)):
    return R.schedule_json(ctx.db, R.create_schedule(ctx, payload))


@router.post("/schedules/{sid}/status", dependencies=_WRITE)
def schedule_status(sid: str, payload: dict = Body(...), ctx: K.OpsCtx = Depends(ops)):
    return R.schedule_json(ctx.db, R.set_schedule_status(ctx, sid, payload.get("status"), payload.get("reason")))


# ── estimates, invoices, payments ─────────────────────────────────────────
@router.get("/estimates")
def estimates(ctx: K.OpsCtx = Depends(ops)):
    return {"items": BL.list_estimates(ctx)}


@router.post("/estimates", dependencies=_WRITE)
def add_estimate(payload: dict = Body(...), ctx: K.OpsCtx = Depends(ops)):
    return BL.estimate_json(BL.create_estimate(ctx, payload))


@router.post("/estimates/{eid}/status", dependencies=_WRITE)
def estimate_status(eid: str, payload: dict = Body(...), ctx: K.OpsCtx = Depends(ops)):
    return BL.estimate_json(BL.set_estimate_status(ctx, eid, payload.get("status")))


@router.get("/invoices")
def invoices(status: Optional[str] = None, ctx: K.OpsCtx = Depends(ops)):
    return {"items": BL.list_invoices(ctx, status)}


@router.post("/invoices", dependencies=_WRITE)
def add_invoice(payload: dict = Body(...), ctx: K.OpsCtx = Depends(ops)):
    inv, created = BL.create_invoice(ctx, payload)
    return dict(BL.invoice_json(ctx.db, inv), created=created)


@router.get("/invoices/{iid}")
def invoice(iid: str, ctx: K.OpsCtx = Depends(ops)):
    ctx.need("billing", "see invoices")
    return BL.invoice_json(ctx.db, BL.get_invoice(ctx.db, ctx.org_id, iid))


def _need_advanced(ctx: K.OpsCtx) -> None:
    if not (ctx.settings and ctx.settings.advanced_enabled):
        raise HTTPException(402, "Invoice PDFs are part of Advanced Operations, which isn't switched on")


@router.get("/invoices/{iid}/pdf")
def invoice_pdf(iid: str, ctx: K.OpsCtx = Depends(ops)):
    ctx.need("billing", "download invoices")
    _need_advanced(ctx)
    i = BL.get_invoice(ctx.db, ctx.org_id, iid)
    return Response(content=BL.invoice_pdf(ctx.db, i), media_type="application/pdf",
                    headers={"Content-Disposition": 'inline; filename="%s.pdf"' % i.number,
                             "Cache-Control": "private, no-store"})


@router.post("/invoices/{iid}/issue", dependencies=_WRITE)
def issue(iid: str, ctx: K.OpsCtx = Depends(ops)):
    return BL.invoice_json(ctx.db, BL.issue_invoice(ctx, iid))


@router.post("/invoices/{iid}/void", dependencies=_WRITE)
def void(iid: str, payload: dict = Body(...), ctx: K.OpsCtx = Depends(ops)):
    return BL.invoice_json(ctx.db, BL.void_invoice(ctx, iid, payload.get("reason")))


@router.post("/invoices/{iid}/payments", dependencies=_WRITE)
def pay(iid: str, payload: dict = Body(...), ctx: K.OpsCtx = Depends(ops)):
    BL.report_payment(ctx, iid, payload)
    return BL.invoice_json(ctx.db, BL.get_invoice(ctx.db, ctx.org_id, iid))


@router.post("/invoices/{iid}/payments/{pid}/confirm", dependencies=_WRITE)
def confirm(iid: str, pid: str, payload: dict = Body(default={}), ctx: K.OpsCtx = Depends(ops)):
    return BL.invoice_json(ctx.db, BL.confirm_payment(ctx, iid, pid, (payload or {}).get("confirm", True) is not False))


# ── requests, buyer access, reporting ─────────────────────────────────────
@router.get("/requests")
def requests_(status: Optional[str] = None, ctx: K.OpsCtx = Depends(ops)):
    return {"items": BY.list_requests(ctx, status)}


@router.post("/requests/{rid}", dependencies=_WRITE)
def answer_request(rid: str, payload: dict = Body(...), ctx: K.OpsCtx = Depends(ops)):
    return BY.handle_request(ctx, rid, payload)


@router.get("/buyers")
def buyers(ctx: K.OpsCtx = Depends(ops)):
    return {"items": BY.list_buyers(ctx)}


@router.post("/buyers", dependencies=_WRITE)
def invite_buyer(payload: dict = Body(...), ctx: K.OpsCtx = Depends(ops)):
    return BY.invite_buyer(ctx, payload)


@router.post("/buyers/{aid}/revoke", dependencies=_WRITE)
def revoke_buyer(aid: str, ctx: K.OpsCtx = Depends(ops)):
    return BY.revoke_buyer(ctx, aid)


@router.get("/network-offers")
def offers(ctx: K.OpsCtx = Depends(ops)):
    return {"items": BY.network_offers_for(ctx)}


@router.post("/network-offers/{rid}", dependencies=_WRITE)
def answer_offer(rid: str, payload: dict = Body(...), ctx: K.OpsCtx = Depends(ops)):
    return BY.network_answer(ctx, rid, bool(payload.get("accept")))


@router.get("/report")
def report(days: int = Query(30, ge=1, le=366), ctx: K.OpsCtx = Depends(ops)):
    return IN.report(ctx, days=days)


@router.get("/suggestions")
def suggestions(ctx: K.OpsCtx = Depends(ops)):
    return {"items": IN.suggestions(ctx)}


# ── the company's own customers (optional buyer portal) ───────────────────
@router.get("/buyer/home")
def buyer_home(b: BY.BuyerCtx = Depends(buyer)):
    c = b.db.query(K.ServiceCustomer).filter(K.ServiceCustomer.id == b.customer_id).first()
    s = K.settings(b.db, b.org_id)
    return {"customer": {"id": c.id, "name": c.name}, "company": (s.business_name if s else None) or b.org.name,
            "locations": BY.buyer_locations(b), "upcoming": BY.buyer_jobs(b),
            "can_approve_estimates": b.access.can_approve_estimates}


@router.get("/buyer/history")
def buyer_history(b: BY.BuyerCtx = Depends(buyer)):
    return {"items": BY.buyer_jobs(b, history=True)}


@router.get("/buyer/visits/{jid}")
def buyer_visit(jid: str, b: BY.BuyerCtx = Depends(buyer)):
    return BY.buyer_job_detail(b, jid)


@router.post("/buyer/visits/{jid}/approve", dependencies=_WRITE)
def buyer_approve(jid: str, b: BY.BuyerCtx = Depends(buyer)):
    return BY.buyer_approve_visit(b, jid)


@router.get("/buyer/visits/{jid}/photos/{aid}")
def buyer_photo(jid: str, aid: str, b: BY.BuyerCtx = Depends(buyer)):
    a, data = BY.buyer_photo(b, jid, aid)
    return Response(content=data, media_type=a.content_type,
                    headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/buyer/estimates")
def buyer_estimates(b: BY.BuyerCtx = Depends(buyer)):
    return {"items": BY.buyer_estimates(b)}


@router.post("/buyer/estimates/{eid}", dependencies=_WRITE)
def buyer_decide(eid: str, payload: dict = Body(...), b: BY.BuyerCtx = Depends(buyer)):
    return BY.buyer_decide_estimate(b, eid, bool(payload.get("accept")))


@router.get("/buyer/invoices")
def buyer_invoices(b: BY.BuyerCtx = Depends(buyer)):
    return {"items": BY.buyer_invoices(b)}


@router.get("/buyer/invoices/{iid}/pdf")
def buyer_invoice_pdf(iid: str, b: BY.BuyerCtx = Depends(buyer)):
    return Response(content=BY.buyer_invoice_pdf(b, iid), media_type="application/pdf",
                    headers={"Cache-Control": "private, no-store"})


@router.post("/buyer/invoices/{iid}/payments", dependencies=_WRITE)
def buyer_pay(iid: str, payload: dict = Body(...), b: BY.BuyerCtx = Depends(buyer)):
    return BY.buyer_report_payment(b, iid, payload)


@router.get("/buyer/requests")
def buyer_requests(b: BY.BuyerCtx = Depends(buyer)):
    return {"items": BY.buyer_requests(b), "kinds": [{"key": k, "label": BY.REQUEST_LABEL[k]} for k in BY.REQUEST_KINDS]}


@router.post("/buyer/requests", dependencies=_WRITE)
def buyer_new_request(payload: dict = Body(...), b: BY.BuyerCtx = Depends(buyer)):
    return BY.buyer_request(b, payload)


# ── EvoSys: switch it on per company; the buyer network ───────────────────
@admin_router.get("/acquisition-ops/{org_id}/service-ops")
def admin_get(org_id: str, db: Session = Depends(get_db), me: User = Depends(require_acq_master)):
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if org is None or not AC.acquisition_identity(org):
        raise HTTPException(404, "Company not found")
    return K.settings_json(K.settings(db, org_id))


@admin_router.put("/acquisition-ops/{org_id}/service-ops", dependencies=_WRITE)
def admin_put(org_id: str, payload: dict = Body(...), db: Session = Depends(get_db),
              me: User = Depends(require_acq_master)):
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if org is None or not AC.acquisition_identity(org):
        raise HTTPException(404, "Company not found")
    return K.settings_json(K.save_settings(db, org_id, me, payload, entitlements=True))


@admin_router.get("/god/acquisition/buyer-network")
def network_list(db: Session = Depends(get_db), me: User = Depends(require_acq_master)):
    rows = db.query(BY.ServiceBuyerNetworkRequest).order_by(BY.ServiceBuyerNetworkRequest.created_at.desc()).limit(300)
    names = {o.id: o.name for o in db.query(Organization).all()}
    return {"items": [BY.network_json(r, names.get(r.offered_org_id)) for r in rows]}


@admin_router.post("/god/acquisition/buyer-network", dependencies=_WRITE)
def network_add(payload: dict = Body(...), db: Session = Depends(get_db), me: User = Depends(require_acq_master)):
    return BY.network_json(BY.network_create(db, me, payload))


@admin_router.get("/god/acquisition/buyer-network/{rid}/eligible")
def network_eligible(rid: str, db: Session = Depends(get_db), me: User = Depends(require_acq_master)):
    r = db.query(BY.ServiceBuyerNetworkRequest).filter(BY.ServiceBuyerNetworkRequest.id == rid).first()
    if r is None:
        raise HTTPException(404, "Request not found")
    return {"items": BY.eligible_companies(db, r)}


@admin_router.post("/god/acquisition/buyer-network/{rid}/offer", dependencies=_WRITE)
def network_offer(rid: str, payload: dict = Body(...), db: Session = Depends(get_db),
                  me: User = Depends(require_acq_master)):
    return BY.network_json(BY.network_offer(db, me, rid, payload.get("organization_id")))
