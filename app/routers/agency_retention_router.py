"""INSURANCE RETENTION WORKSPACE API — /agency/retention/* (Issue #499).

Same gate as the rest of the Insurance program (`insurance_agency` feature +
a selected customer workspace), plus the workspace must have the retention
workspace switched on (agency_retention_settings.enabled). Scope rules live in
app/services/agency/retention.py; this file only parses, authorizes the verb,
audits and commits.

Nothing here contacts a provider. Demo-only verbs (simulate reply, run next
step now, reset) refuse any organization that is not flagged is_demo.
"""
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.deps import get_db, require_not_observation, require_tenant_user
from app.models.agency_models import AgencyAppointment
from app.models.agency_retention_models import (AgencyRenewal, AgencyRenewalEvent, AgencyRenewalWorkflow,
                                                AgencyRetentionStaff)
from app.models.models import Organization, User
from app.routers.audit_log_router import log_action
from app.services import lead_scope
from app.services.agency import retention as R
from app.services.entitlements import require_feature
from app.services.platform_owner import require_tenant_context

router = APIRouter(prefix="/agency/retention", tags=["agency-retention"],
                   dependencies=[Depends(require_feature("insurance_agency"))])
_W = [Depends(require_not_observation)]


def ctx_dep(request: Request, preview_as: Optional[str] = Query(None), db: Session = Depends(get_db),
            user: User = Depends(require_tenant_context), _t: User = Depends(require_tenant_user)) -> R.RCtx:
    from app.services.platform_owner import is_platform_pseudo_org
    org_id = lead_scope.active_workspace_org_id(user, db, request)
    if not org_id or is_platform_pseudo_org(org_id):
        raise HTTPException(409, "No customer organization is selected. Select a workspace first.")
    org = db.query(Organization).filter(Organization.id == org_id).first()
    settings = R.get_settings(db, org_id)
    if org is None or settings is None or not settings.enabled:
        raise HTTPException(404, "The retention workspace is not set up for this agency.")
    role = lead_scope.effective_role(user, db, request) or user.role
    ctx = R.RCtx(db, user, user, role, org, settings, request)
    if preview_as and preview_as != user.id:
        # Owner-only, demo-only, read-only. The server computes the previewed
        # person's real scope; it never widens the caller's.
        if not ctx.is_owner or not ctx.is_demo:
            raise HTTPException(403, "Preview as a staff member is available to agency owners in a demo workspace.")
        target = db.query(User).filter(User.id == preview_as).first()
        if target is None or not R.is_org_staff(db, org_id, target):
            raise HTTPException(404, "Staff member not found")
        trole = target.role
        try:
            from app.services import workspace_access
            trole = workspace_access.workspace_role(target, db, org_id) or target.role
        except Exception:
            pass
        ctx = R.RCtx(db, user, target, trole, org, settings, request, preview=True)
    return ctx


def _audit(ctx: R.RCtx, action: str, ttype: str, tid: str, details=None):
    log_action(ctx.db, ctx.org_id, ctx.actor.id, action, ttype, tid, details=details, commit=False)


def _demo_only(ctx: R.RCtx):
    if not ctx.is_demo:
        raise HTTPException(403, "Simulation tools are only available in a demo workspace.")


def _dt(v: Optional[str], field: str) -> Optional[datetime]:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(422, "%s must be ISO-8601" % field)
    if d.tzinfo is not None:
        from datetime import timezone
        d = d.astimezone(timezone.utc).replace(tzinfo=None)
    return d


def route_guard(request: Request, ctx: R.RCtx = Depends(ctx_dep)) -> None:
    """EVERY retention route: the (module, action) it needs, from one map
    (app/services/agency/access.ROUTES), checked against the viewer's effective
    access. A route missing from the map is refused for everyone - new routes
    cannot slip in unguarded. Writes are refused in preview."""
    from app.services.agency import access as AX
    matched, mod, act = AX.route_permission(request.url.path, request.method)
    if not matched:
        raise HTTPException(403, "This action isn't mapped to a permission.")
    if request.method.upper() not in ("GET", "HEAD") and ctx.preview:
        raise HTTPException(403, "Preview is read-only. Return to your own view to make changes.")
    if mod is not None and not ctx.can(mod, act):
        raise HTTPException(403, "Your access doesn't include %s: %s." % (
            AX.MODULE_LABELS.get(mod, mod), AX.ACTION_LABELS.get((mod, act), act)))


router.dependencies.append(Depends(route_guard))


# ───────────────────────────── workspace / today ─────────────────────────────

@router.get("/workspace")
def workspace(ctx: R.RCtx = Depends(ctx_dep)):
    s = R.settings_dict(ctx.settings, ctx.org)
    t = R.team(ctx)   # computed once
    staff = []
    if ctx.is_owner and not ctx.preview and ctx.is_demo:
        staff = [{"user_id": p["user_id"], "name": p["name"], "title": p["title"]} for p in t["people"]
                 if p["title"] in ("manager", "producer", "service", "retention")]
    locs = t["locations"]
    agents = []
    if ctx.manager_plus:
        agents = [{"user_id": p["user_id"], "name": p["name"]} for p in t["people"] if p["title"] not in ("owner",)]
    return {"settings": s, "organization": {"id": ctx.org.id, "name": ctx.org.name},
            "viewer": R.viewer_dict(ctx), "locations": locs, "agents": agents, "preview_options": staff,
            "products": [{"key": k, "label": R.PRODUCT_LABELS[k]} for k in R.PRODUCTS]}


@router.get("/today")
def today(location_id: Optional[str] = None, agent_id: Optional[str] = None, days: int = Query(30, ge=1, le=120),
          ctx: R.RCtx = Depends(ctx_dep)):
    if ctx.kind == "agent" and agent_id is None:
        agent_id = None  # renewals_q already limits an agent to their own + claimable work
    return R.today_view(ctx, location_id, agent_id, days)


@router.get("/renewals")
def renewals(view: str = "all", location_id: Optional[str] = None, agent_id: Optional[str] = None,
             product: Optional[str] = None, days: int = Query(30, ge=1, le=365), ctx: R.RCtx = Depends(ctx_dep)):
    if view not in ("all", "due", "attention", "booked", "confirmed", "lost", "mine", "unassigned"):
        raise HTTPException(422, "Unknown view")
    return R.list_renewals(ctx, view, location_id, agent_id, product, days)


@router.get("/renewals/{renewal_id}")
def renewal_detail(renewal_id: str, ctx: R.RCtx = Depends(ctx_dep)):
    r = R.get_renewal(ctx, renewal_id)
    return R.renewal_rows(ctx, [r])[0]


class AssignIn(BaseModel):
    agent_id: Optional[str] = None
    reason: str = Field("Manager reassignment", max_length=300)
    expected_version: Optional[int] = None
    move_review: bool = False


@router.post("/renewals/{renewal_id}/assign", dependencies=_W)
def assign(renewal_id: str, body: AssignIn, ctx: R.RCtx = Depends(ctx_dep)):
    ctx.require_write()   # module/action enforced by route_guard
    r = R.get_renewal(ctx, renewal_id)
    kind = "assigned" if r.assigned_user_id is None else "reassigned"
    R.assign(ctx, r, body.agent_id, body.reason, body.expected_version, kind=kind, move_review=body.move_review)
    _audit(ctx, "agency.retention.%s" % kind, "agency_renewal", r.id, {"to": body.agent_id, "reason": body.reason})
    ctx.db.commit()
    return R.renewal_rows(ctx, [r])[0]


class BulkAssignIn(BaseModel):
    renewal_ids: List[str]
    agent_id: str
    reason: str = "Bulk assignment"


@router.post("/renewals/bulk-assign", dependencies=_W)
def bulk_assign(body: BulkAssignIn, ctx: R.RCtx = Depends(ctx_dep)):
    ctx.require_write()   # module/action enforced by route_guard
    done, conflicts = [], []
    for rid in body.renewal_ids[:500]:
        r = R.get_renewal(ctx, rid)
        try:
            R.assign(ctx, r, body.agent_id, body.reason, None,
                     kind="assigned" if r.assigned_user_id is None else "reassigned")
            done.append(rid)
        except HTTPException as e:
            if e.status_code != 409:
                raise
            conflicts.append(rid)
    _audit(ctx, "agency.retention.bulk_assign", "organization", ctx.org_id, {"count": len(done), "to": body.agent_id})
    ctx.db.commit()
    return {"assigned": done, "conflicts": conflicts}


class ClaimIn(BaseModel):
    expected_version: Optional[int] = None


@router.post("/renewals/{renewal_id}/claim", dependencies=_W)
def claim(renewal_id: str, body: ClaimIn = Body(default=ClaimIn()), ctx: R.RCtx = Depends(ctx_dep)):
    ctx.require_write()
    r = R.get_renewal(ctx, renewal_id)
    if r.assigned_user_id is not None:
        raise HTTPException(409, "Already assigned to someone")
    R.assign(ctx, r, ctx.user.id, "Claimed from shared queue", body.expected_version, kind="claimed")
    _audit(ctx, "agency.retention.claimed", "agency_renewal", r.id)
    ctx.db.commit()
    return R.renewal_rows(ctx, [r])[0]


@router.post("/renewals/auto-route", dependencies=_W)
def auto_route(body: Dict[str, Any] = Body(default={}), ctx: R.RCtx = Depends(ctx_dep)):
    res = R.auto_route(ctx, body.get("renewal_ids"))
    _audit(ctx, "agency.retention.auto_route", "organization", ctx.org_id,
           {"assigned": len(res["assigned"]), "unrouted": len(res["unrouted"])})
    ctx.db.commit()
    return res


class BookIn(BaseModel):
    starts_at: str
    agent_id: Optional[str] = None
    medium: str = "in_person"
    notes: Optional[str] = Field(None, max_length=1000)


@router.post("/renewals/{renewal_id}/book-review", dependencies=_W, status_code=201)
def book(renewal_id: str, body: BookIn, ctx: R.RCtx = Depends(ctx_dep)):
    ctx.require_write()
    r = R.get_renewal(ctx, renewal_id)
    if r.outcome:
        raise HTTPException(409, "This renewal already has an outcome")
    if body.medium not in ("in_person", "phone", "video"):
        raise HTTPException(422, "medium must be in_person, phone or video")
    starts = _dt(body.starts_at, "starts_at")
    if starts < R.utcnow() - timedelta(minutes=5):
        raise HTTPException(422, "Pick a time in the future")
    appt = R.book_review(ctx, r, starts, body.agent_id, body.medium, body.notes)
    _audit(ctx, "agency.retention.review_booked", "agency_appointment", appt.id, {"renewal_id": r.id})
    ctx.db.commit()
    return {"appointment_id": appt.id, "renewal": R.renewal_rows(ctx, [r])[0]}


class OutcomeIn(BaseModel):
    outcome: str
    note: Optional[str] = Field(None, max_length=1000)


@router.post("/renewals/{renewal_id}/outcome", dependencies=_W)
def outcome(renewal_id: str, body: OutcomeIn, ctx: R.RCtx = Depends(ctx_dep)):
    ctx.require_write()
    r = R.get_renewal(ctx, renewal_id)
    R.record_outcome(ctx, r, body.outcome, body.note)
    _audit(ctx, "agency.retention.outcome", "agency_renewal", r.id, {"outcome": body.outcome})
    ctx.db.commit()
    return R.renewal_rows(ctx, [r])[0]


class NoteIn(BaseModel):
    body: str = Field(..., min_length=1, max_length=2000)
    resolve: bool = False
    next_action: Optional[str] = Field(None, max_length=200)
    next_action_at: Optional[str] = None


@router.post("/renewals/{renewal_id}/note", dependencies=_W)
def note(renewal_id: str, body: NoteIn, ctx: R.RCtx = Depends(ctx_dep)):
    ctx.require_write()
    r = R.get_renewal(ctx, renewal_id)
    R.add_event(ctx, ctx.org_id, r.lead_id, "note", renewal_id=r.id, body=body.body, actor=ctx.actor.id,
                is_demo=r.is_demo)
    if body.resolve:
        R.resolve_attention(r)
        for ev in ctx.db.query(AgencyRenewalEvent).filter(AgencyRenewalEvent.renewal_id == r.id,
                                                          AgencyRenewalEvent.kind == "reply",
                                                          AgencyRenewalEvent.handled.is_(False)).all():
            ev.handled = True
        if r.workflow_status == "needs_attention":
            r.workflow_status = "in_outreach"
    if body.next_action is not None:
        r.next_action = body.next_action or None
        r.next_action_at = _dt(body.next_action_at, "next_action_at")
    r.last_activity_at = R.utcnow()
    ctx.db.commit()
    return R.renewal_rows(ctx, [r])[0]


@router.get("/renewals/{renewal_id}/draft")
def draft(renewal_id: str, language: Optional[str] = None, ctx: R.RCtx = Depends(ctx_dep)):
    r = R.get_renewal(ctx, renewal_id)
    row = R.renewal_rows(ctx, [r])[0]
    wf = ctx.db.query(AgencyRenewalWorkflow).filter(AgencyRenewalWorkflow.organization_id == ctx.org_id).first()
    tmp = wf or AgencyRenewalWorkflow(template=R.DEFAULT_TEMPLATE, template_es=R.DEFAULT_TEMPLATE_ES)
    return {"draft": R.render_template(tmp, row, ctx, language),
            "fields_used": ["first name", "product", "renewal date", "agency name", "assigned agent"],
            "note": "Draft only - nothing is sent. No prices, quotes or coverage statements are generated."}


# ───────────────────────────── demo simulation ─────────────────────────────

class ReplyIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=1000)


@router.post("/renewals/{renewal_id}/simulate-reply", dependencies=_W)
def simulate_reply(renewal_id: str, body: ReplyIn, ctx: R.RCtx = Depends(ctx_dep)):
    _demo_only(ctx)
    ctx.require_write()
    r = R.get_renewal(ctx, renewal_id)
    ev = R.record_reply(ctx.db, ctx.org_id, ctx.settings, r, body.text, simulated=True, actor_id=ctx.actor.id)
    _audit(ctx, "agency.retention.simulated_reply", "agency_renewal", r.id)
    ctx.db.commit()
    return {"event_id": ev.id if ev else None, "renewal": R.renewal_rows(ctx, [r])[0]}


@router.post("/renewals/{renewal_id}/run-next-step", dependencies=_W)
def run_next_step(renewal_id: str, ctx: R.RCtx = Depends(ctx_dep)):
    _demo_only(ctx)
    ctx.require_write()   # module/action enforced by route_guard
    r = R.get_renewal(ctx, renewal_id)
    stats = R.run_due(ctx.db, ctx.org_id, ctx.settings, renewal_ids=[r.id], force_now=True, actor_id=ctx.actor.id)
    ctx.db.commit()
    return {"stats": stats, "renewal": R.renewal_rows(ctx, [r])[0]}


# ───────────────────────────── workflows ─────────────────────────────

class WorkflowIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    kind: str = "renewal_outreach"
    offsets: List[int] = Field(default_factory=lambda: list(R.DEFAULT_OFFSETS))
    channel: str = "sms"
    template: Optional[str] = Field(None, max_length=1000)
    template_es: Optional[str] = Field(None, max_length=1000)
    window_days: int = Field(60, ge=1, le=180)
    products: Optional[List[str]] = None
    location_ids: Optional[List[str]] = None


def _wf_dict(w: AgencyRenewalWorkflow, ctx: R.RCtx) -> Dict[str, Any]:
    from app.models.agency_retention_models import AgencyRenewalEnrollment as E
    counts = {}
    for st in ("active", "needs_human", "paused", "completed", "stopped"):
        counts[st] = ctx.db.query(E).filter(E.workflow_id == w.id, E.state == st).count()
    return {"id": w.id, "name": w.name, "kind": w.kind, "offsets": R.jl(w.offsets, []), "channel": w.channel,
            "template": w.template, "template_es": w.template_es, "window_days": w.window_days,
            "products": R.jl(w.products, None), "location_ids": R.jl(w.location_ids, None), "state": w.state,
            "delivery_mode": w.delivery_mode, "last_run_at": R.iso(w.last_run_at), "enrollments": counts,
            "is_demo": bool(w.is_demo)}


def _wf(ctx: R.RCtx, wid: str) -> AgencyRenewalWorkflow:
    w = ctx.db.query(AgencyRenewalWorkflow).filter(AgencyRenewalWorkflow.id == wid,
                                                   AgencyRenewalWorkflow.organization_id == ctx.org_id).first()
    if w is None:
        raise HTTPException(404, "Workflow not found")
    return w


def _validate_wf(body: WorkflowIn, ctx: R.RCtx):
    if body.channel not in ("sms", "email"):
        raise HTTPException(422, "channel must be sms or email")
    if not body.offsets or any(o < 0 or o > 180 for o in body.offsets):
        raise HTTPException(422, "offsets must be days before renewal between 0 and 180")
    for lid in body.location_ids or []:
        R.location_in_scope(ctx, lid)
    for p in body.products or []:
        if p not in R.PRODUCTS:
            raise HTTPException(422, "Unknown product %s" % p)


@router.get("/workflows")
def workflows(ctx: R.RCtx = Depends(ctx_dep)):
    rows = ctx.db.query(AgencyRenewalWorkflow).filter(AgencyRenewalWorkflow.organization_id == ctx.org_id).all()
    return {"items": [_wf_dict(w, ctx) for w in rows]}


@router.post("/workflows", dependencies=_W, status_code=201)
def create_workflow(body: WorkflowIn, ctx: R.RCtx = Depends(ctx_dep)):
    ctx.require_write()   # module/action enforced by route_guard
    _validate_wf(body, ctx)
    w = AgencyRenewalWorkflow(organization_id=ctx.org_id, name=body.name, kind=body.kind,
                              offsets=R.jd(sorted(set(body.offsets), reverse=True)), channel=body.channel,
                              template=body.template or R.DEFAULT_TEMPLATE,
                              template_es=body.template_es or R.DEFAULT_TEMPLATE_ES, window_days=body.window_days,
                              products=R.jd(body.products), location_ids=R.jd(body.location_ids),
                              created_by=ctx.actor.id, is_demo=ctx.is_demo)
    ctx.db.add(w)
    ctx.db.flush()
    _audit(ctx, "agency.retention.workflow_created", "agency_renewal_workflow", w.id, body.dict())
    ctx.db.commit()
    return _wf_dict(w, ctx)


@router.patch("/workflows/{wid}", dependencies=_W)
def update_workflow(wid: str, body: WorkflowIn, ctx: R.RCtx = Depends(ctx_dep)):
    ctx.require_write()   # module/action enforced by route_guard
    _validate_wf(body, ctx)
    w = _wf(ctx, wid)
    before = _wf_dict(w, ctx)
    w.name, w.kind, w.channel, w.window_days = body.name, body.kind, body.channel, body.window_days
    w.offsets = R.jd(sorted(set(body.offsets), reverse=True))
    w.template = body.template or w.template
    w.template_es = body.template_es or w.template_es
    w.products, w.location_ids = R.jd(body.products), R.jd(body.location_ids)
    _audit(ctx, "agency.retention.workflow_updated", "agency_renewal_workflow", w.id,
           {"before": {k: before[k] for k in ("offsets", "channel", "window_days", "products")}, "after": body.dict()})
    ctx.db.commit()
    return _wf_dict(w, ctx)


@router.get("/workflows/{wid}/preview")
def preview(wid: str, ctx: R.RCtx = Depends(ctx_dep)):
    return R.preview_workflow(ctx, _wf(ctx, wid))


@router.post("/workflows/{wid}/start", dependencies=_W)
def start(wid: str, ctx: R.RCtx = Depends(ctx_dep)):
    ctx.require_write()   # module/action enforced by route_guard
    w = _wf(ctx, wid)
    res = R.start_workflow(ctx, w)
    _audit(ctx, "agency.retention.workflow_started", "agency_renewal_workflow", w.id, res)
    ctx.db.commit()
    return {**res, "workflow": _wf_dict(w, ctx)}


@router.post("/workflows/{wid}/pause", dependencies=_W)
def pause(wid: str, ctx: R.RCtx = Depends(ctx_dep)):
    ctx.require_write()   # module/action enforced by route_guard
    w = _wf(ctx, wid)
    w.state = "paused"
    _audit(ctx, "agency.retention.workflow_paused", "agency_renewal_workflow", w.id)
    ctx.db.commit()
    return _wf_dict(w, ctx)


@router.post("/workflows/{wid}/resume", dependencies=_W)
def resume(wid: str, ctx: R.RCtx = Depends(ctx_dep)):
    ctx.require_write()   # module/action enforced by route_guard
    w = _wf(ctx, wid)
    if w.state == "draft":
        raise HTTPException(409, "Start the workflow first")
    w.state = "running"
    _audit(ctx, "agency.retention.workflow_resumed", "agency_renewal_workflow", w.id)
    ctx.db.commit()
    return _wf_dict(w, ctx)


@router.post("/workflows/{wid}/run-due", dependencies=_W)
def run_due(wid: str, ctx: R.RCtx = Depends(ctx_dep)):
    """Process steps that are due now (quiet hours respected). Idempotent."""
    ctx.require_write()   # module/action enforced by route_guard
    w = _wf(ctx, wid)
    vis = [x.id for x in R.renewals_q(ctx).all()]
    stats = R.run_due(ctx.db, ctx.org_id, ctx.settings, workflow_id=w.id, renewal_ids=vis, actor_id=ctx.actor.id)
    _audit(ctx, "agency.retention.workflow_run", "agency_renewal_workflow", w.id, stats)
    ctx.db.commit()
    return stats


# ───────────────────────────── clients / conversations / appointments / team ─────────────────────────────

@router.get("/clients")
def clients(search: Optional[str] = None, location_id: Optional[str] = None, ctx: R.RCtx = Depends(ctx_dep)):
    return R.list_clients(ctx, search, location_id)


@router.get("/clients/{lead_id}")
def client(lead_id: str, ctx: R.RCtx = Depends(ctx_dep)):
    return R.client_detail(ctx, R.get_client_lead(ctx, lead_id))


@router.get("/clients/{lead_id}/summary")
def client_summary(lead_id: str, ctx: R.RCtx = Depends(ctx_dep)):
    return R.summarize_client(ctx, R.get_client_lead(ctx, lead_id))


@router.get("/conversations")
def convs(open_only: bool = False, ctx: R.RCtx = Depends(ctx_dep)):
    return R.conversations(ctx, open_only)


@router.get("/appointments")
def appts(start: Optional[str] = None, days: int = Query(7, ge=1, le=60), location_id: Optional[str] = None,
          agent_id: Optional[str] = None, ctx: R.RCtx = Depends(ctx_dep)):
    if start:
        s = datetime.combine(date.fromisoformat(start[:10]), datetime.min.time())
    else:
        s = datetime.combine(ctx.today(), datetime.min.time())
    from datetime import timezone
    s = s.replace(tzinfo=ctx.tz).astimezone(timezone.utc).replace(tzinfo=None)
    return {"items": R.list_appointments(ctx, s, s + timedelta(days=days), location_id, agent_id)}


class ApptStatusIn(BaseModel):
    status: str


@router.post("/appointments/{appt_id}/status", dependencies=_W)
def appt_status(appt_id: str, body: ApptStatusIn, ctx: R.RCtx = Depends(ctx_dep)):
    ctx.require_write()
    if body.status not in ("completed", "cancelled", "no_show", "confirmed"):
        raise HTTPException(422, "status must be completed, cancelled, no_show or confirmed")
    a = ctx.db.query(AgencyAppointment).filter(AgencyAppointment.id == appt_id,
                                               AgencyAppointment.organization_id == ctx.org_id).first()
    if a is None or (not ctx.manager_plus and a.agent_user_id != ctx.user.id):
        raise HTTPException(404, "Appointment not found")
    a.status = body.status
    r = ctx.db.query(AgencyRenewal).filter(AgencyRenewal.review_appointment_id == a.id).first()
    if r is not None:
        if body.status == "completed":
            r.workflow_status = "reviewed"
            r.next_action, r.next_action_at = "Record renewal outcome", R.utcnow()
        elif body.status in ("cancelled", "no_show"):
            r.workflow_status = "needs_attention"
            r.attention_reason = "follow_up_overdue"
            r.attention_detail = "Review %s" % body.status.replace("_", " ")
        R.add_event(ctx, ctx.org_id, r.lead_id, "review_" + body.status, renewal_id=r.id, actor=ctx.actor.id,
                    is_demo=r.is_demo, detail={"appointment_id": a.id,
                                               "note": "Appointment status does not change the renewal outcome."})
    _audit(ctx, "agency.retention.appointment_status", "agency_appointment", a.id, {"status": body.status})
    ctx.db.commit()
    return {"id": a.id, "status": a.status}


@router.get("/team")
def team(ctx: R.RCtx = Depends(ctx_dep)):
    return R.team(ctx)


class StaffIn(BaseModel):
    available: Optional[bool] = None
    review_capacity: Optional[int] = Field(None, ge=0, le=500)
    languages: Optional[List[str]] = None


@router.patch("/team/{user_id}", dependencies=_W)
def update_staff(user_id: str, body: StaffIn, ctx: R.RCtx = Depends(ctx_dep)):
    ctx.require_write()   # module/action enforced by route_guard
    u = R.org_user(ctx, user_id)
    if ctx.kind == "manager":
        mine = set(ctx.loc_ids)
        from app.models.location_models import UserLocation
        locs = {x[0] for x in ctx.db.query(UserLocation.location_id).filter(UserLocation.user_id == u.id).all()}
        if not (locs & mine):
            raise HTTPException(404, "Staff member not found")
    s = ctx.db.query(AgencyRetentionStaff).filter(AgencyRetentionStaff.organization_id == ctx.org_id,
                                                  AgencyRetentionStaff.user_id == u.id).first()
    if s is None:
        s = AgencyRetentionStaff(organization_id=ctx.org_id, user_id=u.id, is_demo=ctx.is_demo)
        ctx.db.add(s)
    if body.available is not None:
        s.available = body.available
    if body.review_capacity is not None:
        s.review_capacity = body.review_capacity
    if body.languages is not None:
        s.languages = R.jd([l[:5] for l in body.languages][:6])
    _audit(ctx, "agency.retention.staff_updated", "user", u.id, body.dict(exclude_none=True))
    ctx.db.commit()
    return {"ok": True}


# ───────────────────────────── assistant / reports / import / settings ─────────────────────────────

class AskIn(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)


@router.post("/ask")
def ask(body: AskIn, ctx: R.RCtx = Depends(ctx_dep)):
    return R.ask(ctx, body.question)


@router.get("/reports")
def reports(start: Optional[str] = None, end: Optional[str] = None, location_id: Optional[str] = None,
            agent_id: Optional[str] = None, product: Optional[str] = None, ctx: R.RCtx = Depends(ctx_dep)):
    t = ctx.today()
    s = date.fromisoformat(start) if start else t - timedelta(days=30)
    e = date.fromisoformat(end) if end else t + timedelta(days=60)
    if ctx.kind == "agent":
        agent_id = ctx.user.id
    return R.report(ctx, s, e, location_id, agent_id, product)


class ImportIn(BaseModel):
    csv_text: str = Field(..., max_length=2_000_000)
    filename: str = "import.csv"
    mapping: Optional[Dict[str, str]] = None


@router.post("/import/preview", dependencies=_W)
def import_preview(body: ImportIn, ctx: R.RCtx = Depends(ctx_dep)):
    return R.import_preview(ctx, body.csv_text, body.mapping, body.filename)


@router.post("/import/commit", dependencies=_W)
def import_commit(body: ImportIn, ctx: R.RCtx = Depends(ctx_dep)):
    res = R.import_commit(ctx, body.csv_text, body.mapping, body.filename)
    _audit(ctx, "agency.retention.import", "organization", ctx.org_id, {k: res[k] for k in ("created", "updated", "skipped_rows_with_errors")})
    ctx.db.commit()
    return res


class SettingsIn(BaseModel):
    display_name: Optional[str] = Field(None, max_length=120)
    tagline: Optional[str] = Field(None, max_length=160)
    default_offsets: Optional[List[int]] = None
    send_window_start: Optional[str] = None
    send_window_end: Optional[str] = None
    shared_queue: Optional[bool] = None
    routing_mode: Optional[str] = None
    review_minutes: Optional[int] = Field(None, ge=10, le=180)


@router.patch("/settings", dependencies=_W)
def update_settings(body: SettingsIn, ctx: R.RCtx = Depends(ctx_dep)):
    ctx.require_write()   # settings:manage enforced by route_guard
    s = ctx.settings
    import re
    for f in ("send_window_start", "send_window_end"):
        v = getattr(body, f)
        if v is not None and not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", v):
            raise HTTPException(422, "%s must be HH:MM" % f)
    if body.routing_mode is not None and body.routing_mode not in ("least_loaded", "round_robin"):
        raise HTTPException(422, "routing_mode must be least_loaded or round_robin")
    before = R.settings_dict(s, ctx.org)
    for f, v in body.dict(exclude_none=True).items():
        setattr(s, f, R.jd(v) if f == "default_offsets" else v)
    _audit(ctx, "agency.retention.settings_updated", "organization", ctx.org_id,
           {"before": {k: before.get(k) for k in body.dict(exclude_none=True)}, "after": body.dict(exclude_none=True)})
    ctx.db.commit()
    return R.settings_dict(s, ctx.org)


@router.post("/demo/reset", dependencies=_W)
def demo_reset(ctx: R.RCtx = Depends(ctx_dep)):
    """Restore the synthetic demo to its starting state. Demo workspaces only."""
    _demo_only(ctx)
    ctx.require_owner()
    from app.services.agency import retention_demo as D
    if not D.is_vg_demo_org(ctx.org):
        raise HTTPException(403, "Only the seeded retention demo workspace can be reset.")
    report = D.reset_demo(ctx.db, ctx.org)
    _audit(ctx, "agency.retention.demo_reset", "organization", ctx.org_id, report)
    ctx.db.commit()
    return report


# ═════════════════════════════ ME / DASHBOARD (#523) ═════════════════════════════

@router.get("/me")
def me(ctx: R.RCtx = Depends(ctx_dep)):
    """Who I am here: profile, scope, permissions, landing dashboard, visible
    navigation. A new login's first signed-in visit records acceptance."""
    from app.services.agency import team_access as TA
    if ctx.configured and not ctx.preview:
        TA.confirm_new_login(ctx.db, ctx.org_id, ctx.user)
        ctx = R.RCtx(ctx.db, ctx.actor, ctx.user, ctx.role_name, ctx.org, ctx.settings, ctx.request)
    v = R.viewer_dict(ctx)
    locs = {l["id"]: l["name"] for l in R.team(ctx)["locations"]} if ctx.can("today", "view") or ctx.can("reports", "view") else {}
    v["location_names"] = [locs.get(i) for i in (v["location_ids"] or []) if locs.get(i)]
    v["prefs"] = R.jl(ctx.member.prefs_json, {}) if ctx.member is not None else {}
    v["nav"] = R.nav_for(ctx)
    return v


class PrefsIn(BaseModel):
    landing: Optional[str] = None
    compact: Optional[bool] = None


@router.patch("/me/prefs", dependencies=_W)
def my_prefs(body: PrefsIn, ctx: R.RCtx = Depends(ctx_dep)):
    ctx.require_write()
    if ctx.member is None:
        raise HTTPException(409, "Preferences are saved for invited members. Owners use their account settings.")
    prefs = R.jl(ctx.member.prefs_json, {}) or {}
    allowed = {n["key"] for n in R.nav_for(ctx)}
    if body.landing is not None:
        if body.landing and body.landing not in allowed:
            raise HTTPException(422, "Pick a page you have access to.")
        prefs["landing"] = body.landing or None
    if body.compact is not None:
        prefs["compact"] = bool(body.compact)
    ctx.member.prefs_json = R.jd(prefs)
    ctx.db.commit()
    return {"prefs": prefs}


@router.get("/dashboard")
def dashboard(location_id: Optional[str] = None, ctx: R.RCtx = Depends(ctx_dep)):
    return R.dashboard_for(ctx, location_id)


class EscalateIn(BaseModel):
    to_user_id: Optional[str] = None
    note: str = Field(..., min_length=1, max_length=1000)


@router.post("/renewals/{renewal_id}/escalate", dependencies=_W)
def escalate(renewal_id: str, body: EscalateIn, ctx: R.RCtx = Depends(ctx_dep)):
    """Hand a client question to an authorized producer. Records the request;
    reassigns only if the caller may assign."""
    ctx.require_write()
    r = R.get_renewal(ctx, renewal_id)
    row = R.escalate(ctx, r, body.to_user_id, body.note)
    _audit(ctx, "agency.retention.escalated", "agency_renewal", r.id, {"to": body.to_user_id})
    ctx.db.commit()
    return row


@router.get("/renewals/{renewal_id}/availability")
def availability(renewal_id: str, day: Optional[str] = None, agent_id: Optional[str] = None,
                 ctx: R.RCtx = Depends(ctx_dep)):
    r = R.get_renewal(ctx, renewal_id)
    return R.availability(ctx, r, day, agent_id)


# ═════════════════════════════ TEAM & ACCESS (#523) ═════════════════════════════

def _ta():
    from app.services.agency import team_access as TA
    return TA


def _run(fn, *a, **kw):
    TA = _ta()
    try:
        return fn(*a, **kw)
    except TA.AccessError as e:
        detail = {"message": str(e), **e.extra} if e.extra else str(e)
        raise HTTPException(e.status, detail)


def _base_url(request: Request) -> str:
    import os
    origin = (request.headers.get("origin") or "").rstrip("/")
    from app.main import ALLOWED_ORIGINS
    if origin and origin in ALLOWED_ORIGINS:
        return origin
    return (os.environ.get("APP_BASE_URL") or "").rstrip("/")


def _people_scope(ctx: R.RCtx):
    """People a viewer may list: agency-wide, or those sharing a location or team."""
    if ctx.scope == "agency":
        return None
    from app.models.location_models import UserLocation
    ids = {r[0] for r in ctx.db.query(UserLocation.user_id).filter(
        UserLocation.organization_id == ctx.org_id, UserLocation.location_id.in_(ctx.loc_ids or ["-"])).all()}
    return ids | set(ctx.team_mates) | {ctx.user.id}


@router.get("/access/catalog")
def access_catalog(ctx: R.RCtx = Depends(ctx_dep)):
    from app.services.agency import access as AX
    c = AX.ceiling(ctx.db, ctx.actor, ctx.org_id)
    return {"modules": AX.catalog_json(), "scopes": [{"key": k, "label": AX.SCOPE_LABELS[k]} for k in AX.SCOPES],
            "dashboards": list(AX.DASHBOARDS), "ceiling": {"permissions": c["permissions"],
                                                           "record_scope": c["record_scope"],
                                                           "location_ids": c["location_ids"], "owner": c["owner"]}}


@router.get("/access/people")
def access_people(ctx: R.RCtx = Depends(ctx_dep)):
    TA = _ta()
    TA.ensure_starters(ctx.db, ctx.org_id, ctx.is_demo)
    ctx.db.commit()
    return {"items": TA.people(ctx.db, ctx.org_id, ctx.actor, _people_scope(ctx)),
            "can_manage_access": ctx.can("access", "manage") and not ctx.preview,
            "can_manage_team": ctx.can("team", "manage") and not ctx.preview,
            "can_manage_locations": ctx.can("locations", "manage") and not ctx.preview}


@router.get("/access/people/{user_id}")
def access_person(user_id: str, ctx: R.RCtx = Depends(ctx_dep)):
    scope = _people_scope(ctx)
    if scope is not None and user_id not in scope:
        raise HTTPException(404, "Person not found.")
    return _run(_ta().member_detail, ctx.db, ctx.org_id, ctx.actor, user_id)


@router.get("/access/people/{user_id}/owned-work")
def access_owned(user_id: str, ctx: R.RCtx = Depends(ctx_dep)):
    return _ta().owned_work(ctx.db, ctx.org_id, user_id)


class AccessIn(BaseModel):
    profile_id: Optional[str] = None
    grants: Optional[Dict[str, List[str]]] = None
    denies: Optional[Dict[str, List[str]]] = None
    record_scope: Optional[str] = None
    reports_to_user_id: Optional[str] = None
    location_ids: Optional[List[str]] = None
    team_ids: Optional[List[str]] = None


@router.patch("/access/people/{user_id}", dependencies=_W)
def access_set(user_id: str, body: AccessIn, ctx: R.RCtx = Depends(ctx_dep)):
    ctx.require_write()
    return _run(_ta().set_access, ctx.db, ctx.org_id, ctx.actor, user_id, **body.dict())


class LocsIn(BaseModel):
    location_ids: List[str]


@router.post("/access/people/{user_id}/locations", dependencies=_W)
def access_locations(user_id: str, body: LocsIn, ctx: R.RCtx = Depends(ctx_dep)):
    ctx.require_write()
    return _run(_ta().set_locations, ctx.db, ctx.org_id, ctx.actor, user_id, body.location_ids)


class StatusIn(BaseModel):
    reassign_to: Optional[str] = None
    release_to_queue: bool = False


@router.post("/access/people/{user_id}/{action}", dependencies=_W)
def access_status(user_id: str, action: str, body: StatusIn = Body(default=StatusIn()), ctx: R.RCtx = Depends(ctx_dep)):
    if action not in ("suspend", "reactivate", "remove"):
        raise HTTPException(404, "Not found")
    ctx.require_write()
    return _run(_ta().change_status, ctx.db, ctx.org_id, ctx.actor, user_id, action, body.reassign_to,
                body.release_to_queue)


@router.get("/access/profiles")
def access_profiles(include_archived: bool = False, ctx: R.RCtx = Depends(ctx_dep)):
    TA = _ta()
    TA.ensure_starters(ctx.db, ctx.org_id, ctx.is_demo)
    ctx.db.commit()
    return {"items": TA.profiles(ctx.db, ctx.org_id, include_archived)}


class ProfileIn(BaseModel):
    name: Optional[str] = Field(None, max_length=80)
    description: Optional[str] = Field(None, max_length=600)
    template_key: Optional[str] = None
    permissions: Optional[Dict[str, List[str]]] = None
    record_scope: Optional[str] = None
    dashboard: Optional[str] = None
    aggregate_only: Optional[bool] = None
    apply: Optional[str] = None


@router.post("/access/profiles", dependencies=_W, status_code=201)
def access_profile_create(body: ProfileIn, ctx: R.RCtx = Depends(ctx_dep)):
    ctx.require_write()
    return _run(_ta().create_profile, ctx.db, ctx.org_id, ctx.actor, name=body.name or "",
                description=body.description, template_key=body.template_key, permissions=body.permissions,
                record_scope=body.record_scope or "assigned", dashboard=body.dashboard or "auto",
                aggregate_only=bool(body.aggregate_only))


@router.patch("/access/profiles/{pid}", dependencies=_W)
def access_profile_update(pid: str, body: ProfileIn, ctx: R.RCtx = Depends(ctx_dep)):
    ctx.require_write()
    return _run(_ta().update_profile, ctx.db, ctx.org_id, ctx.actor, pid, name=body.name,
                description=body.description, permissions=body.permissions, record_scope=body.record_scope,
                dashboard=body.dashboard, aggregate_only=body.aggregate_only, apply=body.apply)


class NameIn(BaseModel):
    name: Optional[str] = None
    move_to: Optional[str] = None


@router.post("/access/profiles/{pid}/duplicate", dependencies=_W, status_code=201)
def access_profile_dup(pid: str, body: NameIn = Body(default=NameIn()), ctx: R.RCtx = Depends(ctx_dep)):
    ctx.require_write()
    return _run(_ta().duplicate_profile, ctx.db, ctx.org_id, ctx.actor, pid, body.name)


@router.post("/access/profiles/{pid}/retire", dependencies=_W)
def access_profile_retire(pid: str, body: NameIn = Body(default=NameIn()), ctx: R.RCtx = Depends(ctx_dep)):
    ctx.require_write()
    return _run(_ta().retire_profile, ctx.db, ctx.org_id, ctx.actor, pid, body.move_to)


class PreviewIn(BaseModel):
    profile_id: Optional[str] = None
    grants: Optional[Dict[str, List[str]]] = None
    denies: Optional[Dict[str, List[str]]] = None
    record_scope: Optional[str] = None
    location_ids: Optional[List[str]] = None


@router.post("/access/preview")
def access_preview(body: PreviewIn, ctx: R.RCtx = Depends(ctx_dep)):
    return _run(_ta().preview, ctx.db, ctx.org_id, **body.dict())


@router.get("/access/invitations")
def access_invites(ctx: R.RCtx = Depends(ctx_dep)):
    return {"items": _ta().invites(ctx.db, ctx.org_id)}


class InviteIn(BaseModel):
    email: str = Field(..., max_length=254)
    full_name: Optional[str] = Field(None, max_length=120)
    profile_id: Optional[str] = None
    grants: Optional[Dict[str, List[str]]] = None
    denies: Optional[Dict[str, List[str]]] = None
    record_scope: Optional[str] = None
    location_ids: Optional[List[str]] = None
    team_ids: Optional[List[str]] = None
    reports_to_user_id: Optional[str] = None
    send: bool = False


@router.post("/access/invitations", dependencies=_W, status_code=201)
def access_invite(body: InviteIn, request: Request, ctx: R.RCtx = Depends(ctx_dep)):
    ctx.require_write()
    data = body.dict()
    send = data.pop("send")
    return _run(_ta().invite, ctx.db, ctx.org_id, ctx.actor, base_url=_base_url(request), send=send, **data)


class SendIn(BaseModel):
    send: bool = False


@router.post("/access/invitations/{iid}/resend", dependencies=_W)
def access_resend(iid: str, request: Request, body: SendIn = Body(default=SendIn()), ctx: R.RCtx = Depends(ctx_dep)):
    ctx.require_write()
    return _run(_ta().resend, ctx.db, ctx.org_id, ctx.actor, iid, _base_url(request), body.send)


@router.post("/access/invitations/{iid}/revoke", dependencies=_W)
def access_revoke(iid: str, ctx: R.RCtx = Depends(ctx_dep)):
    ctx.require_write()
    return _run(_ta().revoke, ctx.db, ctx.org_id, ctx.actor, iid)


@router.get("/access/teams")
def access_teams(include_archived: bool = False, ctx: R.RCtx = Depends(ctx_dep)):
    return {"items": _ta().teams(ctx.db, ctx.org_id, include_archived)}


class TeamIn(BaseModel):
    name: Optional[str] = Field(None, max_length=80)
    description: Optional[str] = Field(None, max_length=400)
    lead_user_id: Optional[str] = None
    location_ids: Optional[List[str]] = None
    shared_queue: Optional[bool] = None
    routing_eligible: Optional[bool] = None
    member_ids: Optional[List[str]] = None
    add_member_ids: Optional[List[str]] = None
    remove_member_ids: Optional[List[str]] = None
    archive: bool = False
    restore: bool = False


@router.post("/access/teams", dependencies=_W, status_code=201)
def access_team_create(body: TeamIn, ctx: R.RCtx = Depends(ctx_dep)):
    ctx.require_write()
    return _run(_ta().create_team, ctx.db, ctx.org_id, ctx.actor, body.dict(exclude_none=True))


@router.patch("/access/teams/{tid}", dependencies=_W)
def access_team_update(tid: str, body: TeamIn, ctx: R.RCtx = Depends(ctx_dep)):
    ctx.require_write()
    return _run(_ta().update_team, ctx.db, ctx.org_id, ctx.actor, tid, body.dict(exclude_unset=True))


@router.get("/access/locations")
def access_locs(ctx: R.RCtx = Depends(ctx_dep)):
    items = _ta().locations(ctx.db, ctx.org_id)
    allowed = R.permitted_location_ids(ctx)
    if allowed is not None:
        items = [l for l in items if l["id"] in allowed]
    return {"items": items}


class LocationIn(BaseModel):
    name: Optional[str] = Field(None, max_length=80)
    city: Optional[str] = None
    state: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    timezone: Optional[str] = None
    staff_user_ids: Optional[List[str]] = None
    archive: bool = False
    restore: bool = False
    move_work_to: Optional[str] = None


@router.post("/access/locations", dependencies=_W, status_code=201)
def access_loc_create(body: LocationIn, ctx: R.RCtx = Depends(ctx_dep)):
    ctx.require_write()
    if ctx.scope != "agency":
        raise HTTPException(403, "Only someone with entire-agency access can add a location.")
    return _run(_ta().create_location, ctx.db, ctx.org_id, ctx.actor, body.dict(exclude_none=True))


@router.patch("/access/locations/{lid}", dependencies=_W)
def access_loc_update(lid: str, body: LocationIn, ctx: R.RCtx = Depends(ctx_dep)):
    ctx.require_write()
    return _run(_ta().update_location, ctx.db, ctx.org_id, ctx.actor, lid, body.dict(exclude_unset=True))


@router.get("/access/history")
def access_history(ctx: R.RCtx = Depends(ctx_dep)):
    return {"items": _ta().history(ctx.db, ctx.org_id)}


# ═════════════════════════════ INVITATION ACCEPTANCE (signed-in) ═════════════════════════════
invites_router = APIRouter(prefix="/agency-invites", tags=["agency-invites"])


class TokenIn(BaseModel):
    token: str = Field(..., min_length=8, max_length=200)


@invites_router.post("/preview")
def invite_preview(body: TokenIn, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    return _run(_ta().invite_preview, db, body.token)


@invites_router.post("/accept")
def invite_accept(body: TokenIn, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    return _run(_ta().accept, db, user, body.token)
