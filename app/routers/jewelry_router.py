"""EVOSYS GOLD & JEWELRY PROGRAM - /jewelry-ops/*

Every route:
  * requires the `jewelry_program` feature (router dependency) AND that the
    acting workspace lists it EXPLICITLY (a legacy NULL allow-list is not a
    gold & jewelry business - common.explicitly_enabled);
  * resolves the acting workspace the way every tenant route does and filters
    on its organization id first; customers come through lead scope, so a
    staff member sees their own book and an owner/manager sees the business;
  * resolves another business's ids to 404;
  * writes are refused for view-only logins (require_not_observation) and
    audited (audit_log_entries);
  * SENDS NOTHING. Campaign sending is locked (services/jewelry/campaigns),
    appointments are written so the platform's reminder and survey jobs skip
    them, and the AI receptionist runs in test mode only.
"""
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.deps import get_db, require_god, require_not_observation, require_tenant_user
from app.models.jewelry_models import JewelryItemPhoto
from app.models.models import Organization, User
from app.models.work_models import LeadTask
from app.routers.audit_log_router import log_action
from app.services import lead_scope
from app.services.entitlements import require_feature
from app.services.jewelry import ai as AI
from app.services.jewelry import appointments as AP
from app.services.jewelry import campaigns as CA
from app.services.jewelry import common as C
from app.services.jewelry import customers as CU
from app.services.jewelry import inventory as INV
from app.services.jewelry import metrics as M
from app.services.jewelry import setup as S
from app.services.jewelry import team as T
from app.services.platform_owner import require_tenant_context

router = APIRouter(prefix="/jewelry-ops", tags=["jewelry"],
                   dependencies=[Depends(require_feature(C.FEATURE))])
god_router = APIRouter(prefix="/god/jewelry", tags=["jewelry"])
_WRITE = [Depends(require_not_observation)]
OWNER_ROLES = ("org_admin", "super_admin", "god_admin")


def ctx_dep(request: Request, db: Session = Depends(get_db),
            user: User = Depends(require_tenant_context),
            _t: User = Depends(require_tenant_user)) -> C.Ctx:
    from app.services.platform_owner import is_platform_pseudo_org
    org_id = lead_scope.active_workspace_org_id(user, db, request)
    if not org_id or is_platform_pseudo_org(org_id):
        raise HTTPException(409, "No gold & jewelry business is selected. Select a workspace first.")
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not C.explicitly_enabled(org):
        raise HTTPException(403, "This workspace does not have the Gold & Jewelry program.")
    role = lead_scope.effective_role(user, db, request)
    if role in ("spectator", "viewer") and request.method not in ("GET", "HEAD", "OPTIONS"):
        raise HTTPException(403, "This login is view-only in this workspace.")
    return C.Ctx(db, user, org, lead_scope.is_manager_here(user, db, request), role in OWNER_ROLES, request)


def _audit(ctx: C.Ctx, action: str, ttype: str, tid: str, details=None):
    try:
        log_action(ctx.db, ctx.org_id, ctx.user.id, action, ttype, tid, details=details, commit=False)
    except Exception:  # noqa: BLE001 - an audit hiccup must not undo the user's change
        pass


def _dump(body: BaseModel) -> Dict[str, Any]:
    return body.model_dump(exclude_unset=True) if hasattr(body, "model_dump") else body.dict(exclude_unset=True)


def _profile(ctx: C.Ctx):
    return S.ensure_profile(ctx.db, ctx.org)


def _business_name(ctx: C.Ctx) -> str:
    p = S.get_profile(ctx.db, ctx.org_id)
    return (p.business_name if p else None) or ctx.org.name


def _profile_json(ctx: C.Ctx, p) -> Dict[str, Any]:
    return {"business_name": (p.business_name if p else None) or ctx.org.name,
            "business_name_override": p.business_name if p else None,
            "street_address": p.street_address if p else None, "city_state_zip": p.city_state_zip if p else None,
            "business_phone": p.business_phone if p else None, "business_email": p.business_email if p else None,
            "business_hours": C.jload(p.business_hours, {}) if p else {},
            "hours_confirmed": bool(p.hours_confirmed) if p else False,
            "services": C.jload(p.services, []) if p else [],
            "appointment_types": AP.types_for(p), "appointment_minutes": (p.appointment_minutes if p else 30) or 30,
            "approved_answers": C.jload(p.approved_answers, []) if p else [],
            "ai_mode": p.ai_mode if p else "test", "ai_paused": bool(p.ai_paused) if p else False,
            "ai_paused_at": C.iso(p.ai_paused_at) if p else None}


# ── status / configuration ─────────────────────────────────────────────────
@router.get("/status")
def status(ctx: C.Ctx = Depends(ctx_dep)):
    from app.services.cadence_service import org_sending_enabled
    p = S.get_profile(ctx.db, ctx.org_id)
    role = lead_scope.effective_role(ctx.user, ctx.db, ctx.request)
    return {"enabled": True, "configured": p is not None, "is_demo": bool(ctx.org.is_demo),
            "organization_id": ctx.org_id, "organization_name": ctx.org.name,
            "profile": _profile_json(ctx, p), "can_manage": ctx.manager, "is_owner": ctx.owner,
            "role": role, "role_label": "Platform owner" if role == "god_admin" else T.ROLE_LABEL.get(role, "Staff"),
            "me": {"id": ctx.user.id, "name": ctx.user.full_name or ctx.user.email},
            "segments": C.SEGMENTS, "follow_up_statuses": [{"key": k, "label": C.FOLLOW_UP_LABEL[k]} for k in C.FOLLOW_UP_STATUSES],
            "item_categories": [{"key": k, "label": C.ITEM_CATEGORY_LABEL[k]} for k in C.ITEM_CATEGORIES],
            "campaign_kinds": C.CAMPAIGN_KINDS, "appointment_statuses": C.APPOINTMENT_STATUSES,
            "texts_on": org_sending_enabled(ctx.db, ctx.org_id),
            "connections": {"pos": False, "messaging": False, "ai_live": False},
            "team": T.members(ctx.db, ctx.org_id) if ctx.manager else [],
            "today": C.local_now(ctx.org).isoformat(timespec="minutes")}


class HoursIn(BaseModel):
    mon: Optional[str] = None
    tue: Optional[str] = None
    wed: Optional[str] = None
    thu: Optional[str] = None
    fri: Optional[str] = None
    sat: Optional[str] = None
    sun: Optional[str] = None


class AnswerIn(BaseModel):
    q: str = Field(..., max_length=300)
    a: str = Field(..., max_length=1000)


class ProfileIn(BaseModel):
    business_name: Optional[str] = Field(None, max_length=160)
    street_address: Optional[str] = Field(None, max_length=200)
    city_state_zip: Optional[str] = Field(None, max_length=120)
    business_phone: Optional[str] = Field(None, max_length=40)
    business_email: Optional[str] = Field(None, max_length=200)
    business_hours: Optional[HoursIn] = None
    hours_confirmed: Optional[bool] = None
    services: Optional[List[str]] = None
    appointment_types: Optional[List[str]] = None
    appointment_minutes: Optional[int] = Field(None, ge=10, le=480)
    approved_answers: Optional[List[AnswerIn]] = None


@router.put("/profile", dependencies=_WRITE)
def save_profile(body: ProfileIn, ctx: C.Ctx = Depends(ctx_dep)):
    C.require_owner(ctx)
    p = _profile(ctx)
    data = _dump(body)
    for k in ("business_name", "street_address", "city_state_zip", "business_phone", "business_email"):
        if k in data:
            setattr(p, k, (data[k] or "").strip() or None)
    if "business_hours" in data:
        hours = {k: (v or "").strip()[:40] for k, v in (data["business_hours"] or {}).items() if v and v.strip()}
        p.business_hours = C.jdump(hours)
    if "hours_confirmed" in data:
        p.hours_confirmed = bool(data["hours_confirmed"])
    if "services" in data:
        p.services = C.jdump(C.clean_list(data["services"], limit=40, size=80))
    if "appointment_types" in data:
        types = C.clean_list(data["appointment_types"], limit=20, size=60)
        if not types:
            raise HTTPException(400, "Keep at least one appointment type")
        p.appointment_types = C.jdump(types)
    if "appointment_minutes" in data and data["appointment_minutes"]:
        p.appointment_minutes = int(data["appointment_minutes"])
    if "approved_answers" in data:
        p.approved_answers = C.jdump([{"q": a["q"].strip(), "a": a["a"].strip()}
                                      for a in (data["approved_answers"] or []) if a["q"].strip() and a["a"].strip()][:60])
    _audit(ctx, "jewelry.profile.update", "jewelry_profile", p.id, {"fields": sorted(data)})
    ctx.db.commit()
    return _profile_json(ctx, p)


@router.get("/text-switch")
def text_switch(ctx: C.Ctx = Depends(ctx_dep)):
    from app.services.cadence_service import org_sending_enabled
    return {"on": org_sending_enabled(ctx.db, ctx.org_id), "can_change": False,
            "detail": "Automated texting stays off for this program until messaging is connected and the owner authorizes it."}


@router.get("/team")
def team(ctx: C.Ctx = Depends(ctx_dep)):
    C.require_manager(ctx)
    return {"members": T.members(ctx.db, ctx.org_id),
            "roles": [{"role": "org_admin", "label": "Owner / Admin",
                       "can": "Everything: settings, team, campaign approval, reports, exports"},
                      {"role": "manager", "label": "Manager",
                       "can": "All customers, campaigns, appointments, reports and staff oversight"},
                      {"role": "advisor", "label": "Staff",
                       "can": "Their assigned customers and appointments, notes and follow-ups"},
                      {"role": "spectator", "label": "View only", "can": "Read only"}],
            "invite_detail": "Staff are invited through EvoSys workspace access. No accounts are created automatically."}


@router.get("/activity")
def activity(limit: int = Query(100, ge=1, le=500), ctx: C.Ctx = Depends(ctx_dep)):
    C.require_manager(ctx)
    from app.models.models import AuditLogEntry
    names = {m["id"]: m["name"] for m in T.members(ctx.db, ctx.org_id)}
    rows = (ctx.db.query(AuditLogEntry).filter(AuditLogEntry.organization_id == ctx.org_id,
                                               AuditLogEntry.action.like("jewelry.%"))
            .order_by(AuditLogEntry.created_at.desc()).limit(limit).all())
    missing = {r.actor_user_id for r in rows if r.actor_user_id and r.actor_user_id not in names}
    if missing:
        for u in ctx.db.query(User).filter(User.id.in_(list(missing))).all():
            names[u.id] = "Platform owner" if u.role == "god_admin" else (u.full_name or u.email)
    return {"entries": [{"id": r.id, "action": r.action, "target_type": r.target_type, "target_id": r.target_id,
                         "actor": names.get(r.actor_user_id or "", "System"), "at": C.iso(r.created_at)} for r in rows]}


# ── dashboard / reports ────────────────────────────────────────────────────
@router.get("/dashboard")
def dashboard(period: Optional[str] = None, ctx: C.Ctx = Depends(ctx_dep)):
    return M.compute(ctx, period)


@router.get("/reports")
def reports(period: Optional[str] = None, ctx: C.Ctx = Depends(ctx_dep)):
    return M.report(ctx, period)


# ── customers ──────────────────────────────────────────────────────────────
class CustomerIn(BaseModel):
    first_name: Optional[str] = Field(None, max_length=80)
    last_name: Optional[str] = Field(None, max_length=80)
    phone: Optional[str] = Field(None, max_length=40)
    email: Optional[str] = Field(None, max_length=200)
    street_address: Optional[str] = Field(None, max_length=200)
    city: Optional[str] = Field(None, max_length=80)
    state: Optional[str] = Field(None, max_length=20)
    zip_code: Optional[str] = Field(None, max_length=12)
    segments: Optional[List[str]] = None
    interests: Optional[List[str]] = None
    follow_up: Optional[str] = None
    last_visit: Optional[str] = None
    assigned_to_id: Optional[str] = None
    allow_sms: Optional[bool] = None
    allow_email: Optional[bool] = None
    permission_source: Optional[str] = Field(None, max_length=200)
    notes: Optional[str] = Field(None, max_length=4000)
    allow_duplicate: Optional[bool] = None


def _one(ctx: C.Ctx, lead) -> Dict[str, Any]:
    act = CU.activity_index(ctx, [lead.id])
    return CU.customer_json(lead, act[lead.id], CU.team_names(ctx))


@router.get("/customers")
def customers(q: Optional[str] = None, segment: Optional[str] = None, follow_up: Optional[str] = None,
              interest: Optional[str] = None, limit: int = Query(200, ge=1, le=500),
              offset: int = Query(0, ge=0), ctx: C.Ctx = Depends(ctx_dep)):
    return CU.list_customers(ctx, q, segment, follow_up, interest, limit, offset)


@router.post("/customers", dependencies=_WRITE, status_code=201)
def customer_create(body: CustomerIn, ctx: C.Ctx = Depends(ctx_dep)):
    data = _dump(body)
    if data.get("allow_duplicate") and not ctx.manager:
        data.pop("allow_duplicate")
    lead = CU.create(ctx, data)
    _audit(ctx, "jewelry.customer.create", "lead", lead.id, {"source": "manual"})
    ctx.db.commit()
    return _one(ctx, lead)


@router.get("/customers/export")
def customer_export(ctx: C.Ctx = Depends(ctx_dep)):
    body = CU.export_csv(ctx)
    _audit(ctx, "jewelry.customer.export", "organization", ctx.org_id, {"bytes": len(body)})
    ctx.db.commit()
    stamp = datetime.utcnow().strftime("%Y%m%d")
    return Response(content=body, media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="customers-%s.csv"' % stamp,
                             "Cache-Control": "no-store"})


@router.post("/customers/import/preview", dependencies=_WRITE)
async def customer_import_preview(file: UploadFile = File(...), mapping: Optional[str] = Form(None),
                                  ctx: C.Ctx = Depends(ctx_dep)):
    C.require_manager(ctx)
    data = await file.read()
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(400, "That file is larger than 8 MB")
    header, body = CU.parse_file(file.filename, data)
    m = _mapping(mapping)
    out = CU.analyse(ctx, header, body, m)
    out["rows"] = out["rows"][:500]
    out["preview_limited"] = len(body) > 500
    out["filename"] = file.filename
    return out


def _mapping(raw: Optional[str]):
    if not raw:
        return None
    try:
        m = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "Column mapping isn't valid")
    if not isinstance(m, dict):
        raise HTTPException(400, "Column mapping isn't valid")
    return {k: (int(v) if v not in (None, "", -1, "-1") else None) for k, v in m.items() if k in CU.HEADER_MAP}


@router.post("/customers/import/confirm", dependencies=_WRITE)
async def customer_import_confirm(file: UploadFile = File(...), mapping: Optional[str] = Form(None),
                                  ctx: C.Ctx = Depends(ctx_dep)):
    C.require_manager(ctx)
    data = await file.read()
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(400, "That file is larger than 8 MB")
    header, body = CU.parse_file(file.filename, data)
    analysis = CU.analyse(ctx, header, body, _mapping(mapping))
    out = CU.commit_import(ctx, analysis, file.filename)
    _audit(ctx, "jewelry.customer.import", "organization", ctx.org_id,
           {"file": file.filename, "created": out["created"], "batch": out["batch_id"]})
    ctx.db.commit()
    return out


@router.get("/customers/{lead_id}")
def customer_get(lead_id: str, ctx: C.Ctx = Depends(ctx_dep)):
    return _one(ctx, CU.load(ctx, lead_id))


@router.patch("/customers/{lead_id}", dependencies=_WRITE)
def customer_update(lead_id: str, body: CustomerIn, ctx: C.Ctx = Depends(ctx_dep)):
    lead = CU.load(ctx, lead_id)
    data = _dump(body)
    data.pop("allow_duplicate", None)
    changed = CU.apply_fields(ctx, lead, data, creating=False)
    if ("phone" in data or "email" in data) and CU.find_duplicate(ctx, lead.phone, lead.email, exclude_id=lead.id):
        ctx.db.rollback()
        raise HTTPException(409, "Another customer already has this phone or email.")
    _audit(ctx, "jewelry.customer.update", "lead", lead.id, {"fields": changed})
    ctx.db.commit()
    return _one(ctx, lead)


@router.get("/customers/{lead_id}/history")
def customer_history(lead_id: str, ctx: C.Ctx = Depends(ctx_dep)):
    return CU.history(ctx, CU.load(ctx, lead_id))


class NoteIn(BaseModel):
    body: str = Field(..., max_length=4000)


@router.post("/customers/{lead_id}/notes", dependencies=_WRITE, status_code=201)
def customer_note(lead_id: str, body: NoteIn, ctx: C.Ctx = Depends(ctx_dep)):
    lead = CU.load(ctx, lead_id)
    n = CU.add_note(ctx, lead, body.body)
    _audit(ctx, "jewelry.customer.note", "lead", lead.id)
    ctx.db.commit()
    return {"id": n.id}


class FollowUpIn(BaseModel):
    title: str = Field(..., max_length=200)
    due_at: Optional[str] = None
    assigned_to_id: Optional[str] = None


def _dt(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        d = datetime.fromisoformat(raw.replace("Z", ""))
    except ValueError:
        raise HTTPException(400, "Dates must look like 2026-11-12 or 2026-11-12T14:30")
    return d.replace(tzinfo=None)


@router.post("/customers/{lead_id}/follow-ups", dependencies=_WRITE, status_code=201)
def customer_follow_up(lead_id: str, body: FollowUpIn, ctx: C.Ctx = Depends(ctx_dep)):
    lead = CU.load(ctx, lead_id)
    t = CU.add_follow_up(ctx, lead, body.title, _dt(body.due_at), body.assigned_to_id)
    _audit(ctx, "jewelry.followup.create", "lead_task", t.id, {"lead_id": lead.id})
    ctx.db.commit()
    return {"id": t.id}


@router.get("/follow-ups")
def follow_ups(status: str = Query("open"), ctx: C.Ctx = Depends(ctx_dep)):
    from app.models.models import Lead
    q = ctx.db.query(LeadTask, Lead).join(Lead, Lead.id == LeadTask.lead_id) \
        .filter(LeadTask.organization_id == ctx.org_id, Lead.organization_id == ctx.org_id)
    if status != "all":
        q = q.filter(LeadTask.status == status)
    if not ctx.manager:
        q = q.filter(LeadTask.assigned_to_id == ctx.user.id)
    names = CU.team_names(ctx)
    rows = q.order_by(LeadTask.due_at.is_(None), LeadTask.due_at.asc()).limit(300).all()
    now = datetime.utcnow()
    return {"follow_ups": [{"id": t.id, "title": t.title, "due_at": C.iso(t.due_at), "status": t.status,
                            "overdue": bool(t.due_at and t.due_at < now and t.status == "open"),
                            "lead_id": l.id, "customer_name": C.lead_name(l),
                            "assigned_to_name": names.get(t.assigned_to_id or "")} for t, l in rows]}


@router.post("/follow-ups/{task_id}/complete", dependencies=_WRITE)
def follow_up_complete(task_id: str, ctx: C.Ctx = Depends(ctx_dep)):
    t = ctx.db.query(LeadTask).filter(LeadTask.id == task_id, LeadTask.organization_id == ctx.org_id).first()
    if t is None or (not ctx.manager and t.assigned_to_id != ctx.user.id):
        raise HTTPException(404, "Follow-up not found")
    t.status, t.completed_at = "done", datetime.utcnow()
    _audit(ctx, "jewelry.followup.complete", "lead_task", t.id)
    ctx.db.commit()
    return {"ok": True}


class OptOutIn(BaseModel):
    reason: Optional[str] = Field(None, max_length=300)


@router.post("/customers/{lead_id}/opt-out", dependencies=_WRITE)
def customer_opt_out(lead_id: str, body: OptOutIn, ctx: C.Ctx = Depends(ctx_dep)):
    lead = CU.load(ctx, lead_id)
    CU.opt_out(ctx, lead, body.reason or "Customer asked not to be contacted (recorded by staff)")
    _audit(ctx, "jewelry.customer.opt_out", "lead", lead.id)
    ctx.db.commit()
    return _one(ctx, lead)


class TxnIn(BaseModel):
    kind: str = "sale"
    occurred_on: Optional[str] = None
    amount: float
    description: Optional[str] = Field(None, max_length=300)
    campaign_id: Optional[str] = None
    item_id: Optional[str] = None


@router.post("/customers/{lead_id}/transactions", dependencies=_WRITE, status_code=201)
def customer_transaction(lead_id: str, body: TxnIn, ctx: C.Ctx = Depends(ctx_dep)):
    lead = CU.load(ctx, lead_id)
    t = CU.record_transaction(ctx, lead, body.kind, body.occurred_on, body.amount, body.description,
                              body.campaign_id, body.item_id)
    _audit(ctx, "jewelry.transaction.record", "jewelry_transaction", t.id, {"kind": t.kind, "amount": t.amount})
    ctx.db.commit()
    return CU.txn_json(t)


# ── appointments ───────────────────────────────────────────────────────────
@router.get("/appointments")
def appointments(start: Optional[str] = None, end: Optional[str] = None, staff_id: Optional[str] = None,
                 ctx: C.Ctx = Depends(ctx_dep)):
    return {"appointments": AP.list_range(ctx, start, end, staff_id),
            "types": AP.types_for(S.get_profile(ctx.db, ctx.org_id)),
            "staff": T.members(ctx.db, ctx.org_id) if ctx.manager else
                     [{"id": ctx.user.id, "name": ctx.user.full_name or ctx.user.email}],
            "reminders_enabled": False}


class AppointmentIn(BaseModel):
    lead_id: str
    type: str
    starts_at: str
    duration_minutes: Optional[int] = None
    staff_id: Optional[str] = None
    internal_notes: Optional[str] = Field(None, max_length=2000)
    follow_up_days: Optional[int] = Field(None, ge=1, le=90)
    campaign_id: Optional[str] = None


@router.post("/appointments", dependencies=_WRITE, status_code=201)
def appointment_create(body: AppointmentIn, ctx: C.Ctx = Depends(ctx_dep)):
    lead = CU.load(ctx, body.lead_id)
    out = AP.create(ctx, lead, S.get_profile(ctx.db, ctx.org_id), body.type, body.starts_at,
                    body.duration_minutes, body.staff_id, body.internal_notes, body.follow_up_days, body.campaign_id)
    _audit(ctx, "jewelry.appointment.create", "booking_link", out["id"], {"type": body.type})
    ctx.db.commit()
    return out


class AppointmentPatch(BaseModel):
    type: Optional[str] = None
    starts_at: Optional[str] = None
    duration_minutes: Optional[int] = None
    staff_id: Optional[str] = None
    status: Optional[str] = None
    internal_notes: Optional[str] = Field(None, max_length=2000)


@router.patch("/appointments/{appt_id}", dependencies=_WRITE)
def appointment_update(appt_id: str, body: AppointmentPatch, ctx: C.Ctx = Depends(ctx_dep)):
    data = _dump(body)
    out = AP.update(ctx, appt_id, data, S.get_profile(ctx.db, ctx.org_id))
    _audit(ctx, "jewelry.appointment.update", "booking_link", appt_id, {"fields": sorted(data)})
    ctx.db.commit()
    return out


# ── campaigns ──────────────────────────────────────────────────────────────
class CampaignIn(BaseModel):
    name: Optional[str] = Field(None, max_length=160)
    kind: Optional[str] = None
    segments: Optional[List[str]] = None
    channel: Optional[str] = None
    sms_body: Optional[str] = Field(None, max_length=640)
    email_subject: Optional[str] = Field(None, max_length=200)
    email_body: Optional[str] = Field(None, max_length=8000)
    marketing_cost: Optional[float] = None
    item_id: Optional[str] = None


@router.get("/campaigns")
def campaigns(ctx: C.Ctx = Depends(ctx_dep)):
    C.require_manager(ctx)
    from app.models.jewelry_models import JewelryCampaign
    rows = ctx.db.query(JewelryCampaign).filter(JewelryCampaign.organization_id == ctx.org_id) \
        .order_by(JewelryCampaign.updated_at.desc()).all()
    return {"campaigns": [CA.camp_json(c, CA.results(ctx, c)) for c in rows],
            "readiness": CA.send_readiness(ctx)}


@router.post("/campaigns", dependencies=_WRITE, status_code=201)
def campaign_create(body: CampaignIn, ctx: C.Ctx = Depends(ctx_dep)):
    C.require_manager(ctx)
    c = CA.create(ctx, _dump(body), _business_name(ctx))
    _audit(ctx, "jewelry.campaign.create", "jewelry_campaign", c.id, {"kind": c.kind})
    ctx.db.commit()
    return CA.camp_json(c, CA.results(ctx, c))


@router.get("/campaigns/{cid}")
def campaign_get(cid: str, preview_lead_id: Optional[str] = None, ctx: C.Ctx = Depends(ctx_dep)):
    C.require_manager(ctx)
    c = CA._load(ctx, cid)
    first = None
    if preview_lead_id:
        first = CU.load(ctx, preview_lead_id).first_name
    name = _business_name(ctx)
    return dict(CA.camp_json(c, CA.results(ctx, c)), recipients=CA.recipients(ctx, c),
                readiness=CA.send_readiness(ctx, c),
                preview={"sms": CA.render(c.sms_body, first, name, sms=True),
                         "email_subject": CA.render(c.email_subject, first, name),
                         "email_body": CA.render(c.email_body, first, name),
                         "from_name": name})


@router.patch("/campaigns/{cid}", dependencies=_WRITE)
def campaign_update(cid: str, body: CampaignIn, ctx: C.Ctx = Depends(ctx_dep)):
    C.require_manager(ctx)
    c = CA._load(ctx, cid)
    if c.status in ("cancelled", "completed"):
        raise HTTPException(409, "A %s campaign can't be edited" % c.status)
    data = _dump(body)
    data.pop("kind", None) if "kind" in data and not data["kind"] else None
    CA.apply(ctx, c, data)
    _audit(ctx, "jewelry.campaign.update", "jewelry_campaign", c.id, {"fields": sorted(data)})
    ctx.db.commit()
    return CA.camp_json(c, CA.results(ctx, c))


class ScheduleIn(BaseModel):
    scheduled_for: str


@router.post("/campaigns/{cid}/schedule", dependencies=_WRITE)
def campaign_schedule(cid: str, body: ScheduleIn, ctx: C.Ctx = Depends(ctx_dep)):
    C.require_manager(ctx)
    c = CA._load(ctx, cid)
    if c.status not in ("draft", "paused", "scheduled"):
        raise HTTPException(409, "A %s campaign can't be scheduled" % c.status)
    CA.schedule(ctx, c, body.scheduled_for)
    _audit(ctx, "jewelry.campaign.schedule", "jewelry_campaign", c.id, {"for": C.iso(c.scheduled_for)})
    ctx.db.commit()
    return CA.camp_json(c, CA.results(ctx, c))


@router.post("/campaigns/{cid}/{action}", dependencies=_WRITE)
def campaign_action(cid: str, action: str, ctx: C.Ctx = Depends(ctx_dep)):
    C.require_manager(ctx)
    c = CA._load(ctx, cid)
    if action == "send":
        readiness = CA.send_readiness(ctx, c)
        _audit(ctx, "jewelry.campaign.send_refused", "jewelry_campaign", c.id)
        ctx.db.commit()
        raise HTTPException(409, {"message": "Nothing was sent. Campaign sending is locked for this workspace.",
                                  "readiness": readiness})
    if action == "approve":
        C.require_owner(ctx)
        if c.status in ("cancelled", "completed"):
            raise HTTPException(409, "A %s campaign can't be approved" % c.status)
        c.approved_by, c.approved_at = ctx.user.id, datetime.utcnow()
    elif action in ("pause", "resume", "cancel"):
        CA.set_status(c, action)
    else:
        raise HTTPException(404, "Unknown action")
    _audit(ctx, "jewelry.campaign." + action, "jewelry_campaign", c.id)
    ctx.db.commit()
    return CA.camp_json(c, CA.results(ctx, c))


# ── inventory & offers ─────────────────────────────────────────────────────
class ItemIn(BaseModel):
    name: Optional[str] = Field(None, max_length=200)
    category: Optional[str] = None
    status: Optional[str] = None
    description: Optional[str] = Field(None, max_length=4000)
    sku: Optional[str] = Field(None, max_length=80)
    price_note: Optional[str] = Field(None, max_length=120)
    featured: Optional[bool] = None
    interest_tags: Optional[List[str]] = None


@router.get("/items")
def items(category: Optional[str] = None, status: Optional[str] = None, ctx: C.Ctx = Depends(ctx_dep)):
    return {"items": INV.list_items(ctx, category, status), "pos_connected": False}


@router.post("/items", dependencies=_WRITE, status_code=201)
def item_create(body: ItemIn, ctx: C.Ctx = Depends(ctx_dep)):
    C.require_manager(ctx)
    from app.models.jewelry_models import JewelryItem
    data = _dump(body)
    if not (data.get("name") or "").strip():
        raise HTTPException(400, "Give the item a name")
    it = JewelryItem(organization_id=ctx.org_id, name="-", category="jewelry", status="available",
                     created_by=ctx.user.id)
    INV.apply(it, data)
    ctx.db.add(it)
    ctx.db.flush()
    _audit(ctx, "jewelry.item.create", "jewelry_item", it.id)
    ctx.db.commit()
    return INV.item_json(ctx, it, [], 0, len(INV.matching_customers(ctx, it)))


@router.patch("/items/{item_id}", dependencies=_WRITE)
def item_update(item_id: str, body: ItemIn, ctx: C.Ctx = Depends(ctx_dep)):
    C.require_manager(ctx)
    it = INV._load(ctx, item_id)
    data = _dump(body)
    INV.apply(it, data)
    _audit(ctx, "jewelry.item.update", "jewelry_item", it.id, {"fields": sorted(data)})
    ctx.db.commit()
    photos = ctx.db.query(JewelryItemPhoto).filter(JewelryItemPhoto.item_id == it.id,
                                                   JewelryItemPhoto.organization_id == ctx.org_id).all()
    return INV.item_json(ctx, it, photos, len(INV.inquiries(ctx, it)), len(INV.matching_customers(ctx, it)))


@router.post("/items/{item_id}/photos", dependencies=_WRITE, status_code=201)
async def item_photo_add(item_id: str, file: UploadFile = File(...), ctx: C.Ctx = Depends(ctx_dep)):
    C.require_manager(ctx)
    from app.services import wholesale_files as WF
    it = INV._load(ctx, item_id)
    n = ctx.db.query(JewelryItemPhoto).filter(JewelryItemPhoto.item_id == it.id).count()
    if n >= INV.MAX_PHOTOS:
        raise HTTPException(409, "An item can have at most %d photos" % INV.MAX_PHOTOS)
    data, ctype, _name = await WF.read_upload(file, images_only=True)
    stored = WF.put(ctx.org_id, "jewelry-item", data, ctype, db=ctx.db)
    p = JewelryItemPhoto(organization_id=ctx.org_id, item_id=it.id, content_type=ctype, byte_size=len(data),
                         storage_backend=stored.get("storage_backend"), storage_key=stored.get("storage_key"))
    ctx.db.add(p)
    ctx.db.flush()
    _audit(ctx, "jewelry.item.photo_add", "jewelry_item", it.id, {"photo_id": p.id})
    ctx.db.commit()
    return {"id": p.id, "url": "/jewelry-ops/items/%s/photos/%s" % (it.id, p.id)}


@router.get("/items/{item_id}/photos/{photo_id}")
def item_photo_bytes(item_id: str, photo_id: str, ctx: C.Ctx = Depends(ctx_dep)):
    from app.services import wholesale_files as WF
    it = INV._load(ctx, item_id)
    p = ctx.db.query(JewelryItemPhoto).filter(JewelryItemPhoto.id == photo_id, JewelryItemPhoto.item_id == it.id,
                                              JewelryItemPhoto.organization_id == ctx.org_id).first()
    if p is None:
        raise HTTPException(404, "Photo not found")
    data = WF.fetch(p.storage_backend, p.storage_key, db=ctx.db)
    if data is None:
        raise HTTPException(404, "Photo not found")
    return Response(content=data, media_type=p.content_type, headers={"Cache-Control": "private, max-age=300"})


@router.delete("/items/{item_id}/photos/{photo_id}", dependencies=_WRITE)
def item_photo_delete(item_id: str, photo_id: str, ctx: C.Ctx = Depends(ctx_dep)):
    C.require_manager(ctx)
    from app.services import wholesale_files as WF
    it = INV._load(ctx, item_id)
    p = ctx.db.query(JewelryItemPhoto).filter(JewelryItemPhoto.id == photo_id, JewelryItemPhoto.item_id == it.id,
                                              JewelryItemPhoto.organization_id == ctx.org_id).first()
    if p is None:
        raise HTTPException(404, "Photo not found")
    try:
        WF.remove(p.storage_backend, p.storage_key, db=ctx.db)
    except Exception:  # noqa: BLE001 - the record goes even if the bytes were already gone
        pass
    ctx.db.delete(p)
    _audit(ctx, "jewelry.item.photo_delete", "jewelry_item", it.id, {"photo_id": photo_id})
    ctx.db.commit()
    return {"ok": True}


class InterestIn(BaseModel):
    lead_id: str
    note: Optional[str] = Field(None, max_length=300)


@router.post("/items/{item_id}/interest", dependencies=_WRITE, status_code=201)
def item_interest(item_id: str, body: InterestIn, ctx: C.Ctx = Depends(ctx_dep)):
    it = INV._load(ctx, item_id)
    lead = CU.load(ctx, body.lead_id)
    r = INV.log_interest(ctx, it, lead, body.note)
    _audit(ctx, "jewelry.item.inquiry", "jewelry_item", it.id, {"lead_id": lead.id})
    ctx.db.commit()
    return {"id": r.id}


@router.get("/items/{item_id}/inquiries")
def item_inquiries(item_id: str, ctx: C.Ctx = Depends(ctx_dep)):
    it = INV._load(ctx, item_id)
    rows = INV.inquiries(ctx, it)
    if not ctx.manager:
        mine = {l.id for l in CU.scoped_query(ctx).all()}
        rows = [r for r in rows if r["lead_id"] in mine]
    return {"inquiries": rows}


@router.post("/items/{item_id}/promote", dependencies=_WRITE, status_code=201)
def item_promote(item_id: str, ctx: C.Ctx = Depends(ctx_dep)):
    C.require_manager(ctx)
    it = INV._load(ctx, item_id)
    c = CA.from_item(ctx, it, _business_name(ctx))
    _audit(ctx, "jewelry.campaign.create", "jewelry_campaign", c.id, {"from_item": it.id})
    ctx.db.commit()
    return CA.camp_json(c, CA.results(ctx, c))


# ── AI receptionist (test mode) ────────────────────────────────────────────
class AskIn(BaseModel):
    question: str = Field(..., max_length=1000)
    session_id: Optional[str] = Field(None, max_length=40)
    lead_id: Optional[str] = None


@router.post("/ai/ask", dependencies=_WRITE)
def ai_ask(body: AskIn, ctx: C.Ctx = Depends(ctx_dep)):
    lead = CU.load(ctx, body.lead_id) if body.lead_id else None
    out = AI.ask(ctx, _profile(ctx), body.question, body.session_id, lead)
    _audit(ctx, "jewelry.ai.test_question", "jewelry_ai_exchange", out["id"],
           {"needs_human": out["needs_human"], "source": out["source"]})
    ctx.db.commit()
    return out


class OverrideIn(BaseModel):
    answer: str = Field(..., max_length=2000)


@router.post("/ai/exchanges/{ex_id}/override", dependencies=_WRITE)
def ai_override(ex_id: str, body: OverrideIn, ctx: C.Ctx = Depends(ctx_dep)):
    out = AI.override(ctx, ex_id, body.answer)
    _audit(ctx, "jewelry.ai.operator_override", "jewelry_ai_exchange", out["id"])
    ctx.db.commit()
    return out


@router.get("/ai")
def ai_overview(ctx: C.Ctx = Depends(ctx_dep)):
    p = S.get_profile(ctx.db, ctx.org_id)
    return {"mode": p.ai_mode if p else "test", "paused": bool(p.ai_paused) if p else False,
            "live_available": False, "summary": M.ai_summary(ctx), "history": AI.history(ctx, 60)}


class AiModeIn(BaseModel):
    mode: Optional[str] = None
    paused: Optional[bool] = None


@router.post("/ai/controls", dependencies=_WRITE)
def ai_controls(body: AiModeIn, ctx: C.Ctx = Depends(ctx_dep)):
    C.require_manager(ctx)
    p = _profile(ctx)
    if body.mode is not None:
        C.require_owner(ctx)
        if body.mode not in ("off", "test"):
            raise HTTPException(400, "The AI receptionist can be off or in test mode. Live mode is not available yet.")
        p.ai_mode = body.mode
    if body.paused is not None:
        p.ai_paused = bool(body.paused)
        p.ai_paused_by = ctx.user.id if body.paused else None
        p.ai_paused_at = datetime.utcnow() if body.paused else None
    _audit(ctx, "jewelry.ai.controls", "jewelry_profile", p.id, {"mode": body.mode, "paused": body.paused})
    ctx.db.commit()
    return {"mode": p.ai_mode, "paused": bool(p.ai_paused)}


# ── platform owner: turn an organization into a gold & jewelry business ────
class GodSetupIn(BaseModel):
    organization_id: str


@god_router.post("/setup")
def god_setup(body: GodSetupIn, db: Session = Depends(get_db), god: User = Depends(require_god)):
    """Grant the Gold & Jewelry feature set to one organization and configure it.
    Idempotent. Creates no users or customers, switches no texting on, sends nothing."""
    org = db.query(Organization).filter(Organization.id == body.organization_id).first()
    if org is None:
        raise HTTPException(404, "Organization not found.")
    out = S.setup(db, org, god.id, grant_features=True)
    try:
        log_action(db, org.id, god.id, "jewelry.setup", "organization", org.id, details=out, commit=True)
    except Exception:  # noqa: BLE001
        pass
    return out
