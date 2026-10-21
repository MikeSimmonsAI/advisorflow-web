"""EVOSYS PRO ROOFING PROGRAM - /roofing-ops/*

Every route:
  * requires the `roofing_program` feature (router dependency) AND that the
    acting workspace lists it EXPLICITLY (a legacy NULL allow-list is not a
    roofing company - see common.explicitly_enabled);
  * resolves the acting workspace the way every tenant route does and filters
    on its organization id first; homeowners come through lead scope, so a
    salesperson sees their own book and an owner sees the company;
  * resolves another company's ids to 404;
  * SENDS NOTHING. Replies go out through the existing /communications/send
    (every consent / DNC / quiet-hour gate applies there); follow-up runs
    through the existing cadence engine behind the per-workspace text switch.

The public landing page is the browser route /roofing; its demo request goes
through the existing public inquiry endpoint, not this router.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.deps import get_db, require_god, require_not_observation, require_tenant_user
from app.models.models import CadenceState, Lead, Organization, User
from app.models.roofing_models import RoofingOpportunity
from app.models.work_models import LeadNote
from app.routers.audit_log_router import log_action
from app.services import lead_scope
from app.services.entitlements import require_feature
from app.services.platform_owner import require_tenant_context
from app.services.roofing import booking as B
from app.services.roofing import common as C
from app.services.roofing import inbox as I
from app.services.roofing import journey as J
from app.services.roofing import metrics as M
from app.services.roofing import setup as S

router = APIRouter(prefix="/roofing-ops", tags=["roofing"],
                   dependencies=[Depends(require_feature(C.FEATURE))])
god_router = APIRouter(prefix="/god/roofing", tags=["roofing"])
_WRITE = [Depends(require_not_observation)]


def ctx_dep(request: Request, db: Session = Depends(get_db),
            user: User = Depends(require_tenant_context),
            _t: User = Depends(require_tenant_user)) -> C.Ctx:
    from app.services.platform_owner import is_platform_pseudo_org
    org_id = lead_scope.active_workspace_org_id(user, db, request)
    if not org_id or is_platform_pseudo_org(org_id):
        raise HTTPException(409, "No roofing company is selected. Select a workspace first.")
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not C.explicitly_enabled(org):
        raise HTTPException(403, "This workspace does not have the Roofing program.")
    return C.Ctx(db, user, org, lead_scope.is_manager_here(user, db, request), request)


def _audit(ctx: C.Ctx, action: str, ttype: str, tid: str, details=None):
    try:
        log_action(ctx.db, ctx.org_id, ctx.user.id, action, ttype, tid, details=details, commit=False)
    except Exception:  # noqa: BLE001 - an audit hiccup must not undo the user's change
        pass


def _lead(ctx: C.Ctx, lead_id: str) -> Lead:
    lead = lead_scope.load_lead_in_scope(ctx.db, ctx.user, lead_id, ctx.request)
    if lead.organization_id != ctx.org_id:
        raise HTTPException(404, "Lead not found")
    return lead


def _scoped_leads(ctx: C.Ctx) -> List[Lead]:
    return (lead_scope.authorized_lead_query(ctx.db, ctx.user, request=ctx.request)
            .filter(Lead.organization_id == ctx.org_id).all())


def _team(db: Session, org_id: str) -> List[dict]:
    return [{"id": u.id, "name": u.full_name or u.email, "role": u.role, "email": u.email}
            for u in db.query(User).filter(User.organization_id == org_id, User.is_active == True)  # noqa: E712
            .order_by(User.full_name.asc()).all()]


def _profile_json(org: Organization, p) -> dict:
    return {
        "company_name": (p.company_name if p else None) or org.brand_name or org.name,
        "company_name_override": p.company_name if p else None,
        "logo_url": (p.logo_url if p else None) or org.brand_logo_url,
        "color_primary": (p.color_primary if p else None) or "#0b1f3a",
        "color_accent": (p.color_accent if p else None) or "#f97316",
        "service_areas": C.jload(p.service_areas, []) if p else [],
        "inspection_types": C.jload(p.inspection_types, C.DEFAULT_INSPECTION_TYPES) if p else C.DEFAULT_INSPECTION_TYPES,
        "inspection_duration_minutes": B.duration_for(p),
        "default_salesperson_id": p.default_salesperson_id if p else None,
        "notify_user_ids": C.jload(p.notify_user_ids, []) if p else [],
        "business_phone": (p.business_phone if p else None) or org.org_phone,
        "business_email": (p.business_email if p else None) or org.support_email,
        "configured": p is not None,
    }


# ── status / configuration ─────────────────────────────────────────────────
@router.get("/status")
def status(ctx: C.Ctx = Depends(ctx_dep)):
    from app.services.cadence_service import org_sending_enabled
    p = S.get_profile(ctx.db, ctx.org_id)
    return {"enabled": True, "configured": p is not None, "is_demo": bool(ctx.org.is_demo),
            "organization_id": ctx.org_id, "organization_name": ctx.org.name,
            "profile": _profile_json(ctx.org, p), "can_manage": ctx.manager,
            "me": {"id": ctx.user.id, "name": ctx.user.full_name or ctx.user.email},
            "stages": C.STAGES, "fields": C.FIELDS, "texts_on": org_sending_enabled(ctx.db, ctx.org_id),
            "team": _team(ctx.db, ctx.org_id) if ctx.manager else []}


@router.post("/setup", dependencies=_WRITE)
def setup_here(ctx: C.Ctx = Depends(ctx_dep)):
    C.require_manager(ctx)
    out = S.setup(ctx.db, ctx.org, ctx.user.id, grant_features=False)
    _audit(ctx, "roofing.setup", "organization", ctx.org_id, out)
    ctx.db.commit()
    return out


class ProfileIn(BaseModel):
    company_name: Optional[str] = Field(None, max_length=120)
    logo_url: Optional[str] = Field(None, max_length=500)
    color_primary: Optional[str] = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")
    color_accent: Optional[str] = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")
    service_areas: Optional[List[str]] = None
    inspection_types: Optional[List[str]] = None
    inspection_duration_minutes: Optional[int] = Field(None, ge=15, le=480)
    default_salesperson_id: Optional[str] = None
    notify_user_ids: Optional[List[str]] = None
    business_phone: Optional[str] = Field(None, max_length=40)
    business_email: Optional[str] = Field(None, max_length=200)


@router.put("/profile", dependencies=_WRITE)
def save_profile(body: ProfileIn, ctx: C.Ctx = Depends(ctx_dep)):
    C.require_manager(ctx)
    p = S.ensure_profile(ctx.db, ctx.org)
    data = body.model_dump(exclude_unset=True) if hasattr(body, "model_dump") else body.dict(exclude_unset=True)
    team = {u["id"] for u in _team(ctx.db, ctx.org_id)}
    if data.get("default_salesperson_id") and data["default_salesperson_id"] not in team:
        raise HTTPException(404, "Salesperson not found in this company")
    if "notify_user_ids" in data:
        data["notify_user_ids"] = [u for u in (data["notify_user_ids"] or []) if u in team]
    for k in ("service_areas", "inspection_types"):
        if k in data:
            data[k] = [s.strip()[:80] for s in (data[k] or []) if s and s.strip()][:50]
    for k, v in data.items():
        if k in ("service_areas", "inspection_types", "notify_user_ids"):
            v = C.jdump(v or [])
        elif isinstance(v, str):
            v = v.strip() or None
        setattr(p, k, v)
    _audit(ctx, "roofing.profile.update", "roofing_profile", p.id, {"fields": sorted(data)})
    ctx.db.commit()
    return _profile_json(ctx.org, p)


@router.get("/text-switch")
def text_switch(ctx: C.Ctx = Depends(ctx_dep)):
    from app.services.cadence_service import org_sending_enabled
    return {"on": org_sending_enabled(ctx.db, ctx.org_id), "can_change": ctx.user.role == "god_admin"}


class SwitchIn(BaseModel):
    on: bool


@router.post("/text-switch", dependencies=_WRITE)
def set_text_switch(body: SwitchIn, ctx: C.Ctx = Depends(ctx_dep)):
    from app.services.cadence_service import set_org_sending
    if ctx.user.role != "god_admin":
        raise HTTPException(403, "Only the platform owner can switch automated texts on or off.")
    return {"on": set_org_sending(ctx.db, ctx.org_id, body.on, by=ctx.user.id), "can_change": True}


# ── dashboard ──────────────────────────────────────────────────────────────
@router.get("/dashboard")
def dashboard(start: Optional[str] = None, end: Optional[str] = None,
              owner: Optional[str] = Query(None, description="me | <user id>; owners only for ids"),
              ctx: C.Ctx = Depends(ctx_dep)):
    d0, d1 = M.parse_range(start, end)
    leads = _scoped_leads(ctx)
    if owner == "me":
        leads = [l for l in leads if l.assigned_to_id == ctx.user.id]
    elif owner and ctx.manager:
        leads = [l for l in leads if l.assigned_to_id == owner]
    out = M.compute(ctx.db, ctx.org, leads, d0, d1)
    out["scope"] = "company" if ctx.manager and not owner else "salesperson"
    return out


# ── qualification & outreach ───────────────────────────────────────────────
@router.get("/qualification")
def qualification_summary(ctx: C.Ctx = Depends(ctx_dep)):
    """READY / REVIEW / EXCLUDED from the platform's own qualification engine."""
    from app.services import qualification as Q
    out = {}
    for ch in (Q.CHANNEL_SMS, Q.CHANNEL_EMAIL):
        s = Q.qualify_leads(ctx.db, ctx.user, channel=ch, request=ctx.request)
        out[ch] = s
    enrolled = (ctx.db.query(CadenceState).join(Lead, Lead.id == CadenceState.lead_id)
                .filter(Lead.organization_id == ctx.org_id).count())
    from app.services.cadence_service import org_sending_enabled
    return {"channels": out, "in_follow_up": enrolled, "texts_on": org_sending_enabled(ctx.db, ctx.org_id)}


class EnrollIn(BaseModel):
    lead_ids: Optional[List[str]] = None


@router.post("/outreach/enroll", dependencies=_WRITE)
def enroll(body: EnrollIn = Body(default=EnrollIn()), ctx: C.Ctx = Depends(ctx_dep)):
    """Start the follow-up sequence for homeowners the qualification engine
    calls READY to text. Enrolment schedules touches; it sends nothing itself -
    the cadence runner does, and only while this workspace's text switch is on."""
    C.require_manager(ctx)
    from app.services import qualification as Q
    from app.services.cadence_service import org_sending_enabled, start_cadence
    q = lead_scope.authorized_lead_query(ctx.db, ctx.user, request=ctx.request).filter(
        Lead.organization_id == ctx.org_id)
    if body.lead_ids is not None:
        lead_scope.assert_leads_in_scope(ctx.db, ctx.user, list(body.lead_ids), request=ctx.request)
        q = q.filter(Lead.id.in_(list(body.lead_ids)))
    leads = q.limit(Q.MAX_QUALIFY_ROWS).all()
    qctx = Q.QualificationContext(ctx.db, leads, ctx.org_id, Q.org_rules(ctx.db, ctx.org_id))
    qctx.suppressed_phones = Q.load_suppressed_phones(ctx.db, ctx.org_id)
    already = {r[0] for r in ctx.db.query(CadenceState.lead_id).filter(
        CadenceState.lead_id.in_([l.id for l in leads] or [""])).all()}
    enrolled, skipped = 0, {}

    def skip(why):
        skipped[why] = skipped.get(why, 0) + 1

    for l in leads:
        if l.id in already:
            skip("Already in follow-up")
            continue
        d = Q.qualify_one(l, Q.CHANNEL_SMS, qctx)
        if d["bucket"] != Q.READY:
            r = (d.get("reasons") or [{}])[0]
            skip(r.get("detail") or r.get("code") or d["bucket"])
            continue
        if start_cadence(ctx.db, l) is None:
            skip("Not eligible for an automated sequence (status, duplicate, email-only or test record)")
            continue
        enrolled += 1
    _audit(ctx, "roofing.outreach.enroll", "organization", ctx.org_id, {"enrolled": enrolled})
    ctx.db.commit()
    on = org_sending_enabled(ctx.db, ctx.org_id)
    return {"enrolled": enrolled, "considered": len(leads),
            "skipped": [{"reason": k, "count": v} for k, v in sorted(skipped.items(), key=lambda kv: -kv[1])],
            "texts_on": on,
            "note": None if on else ("Automated texting is OFF for this workspace, so nothing will send until "
                                     "the platform owner switches it on. Emails and manual replies still follow "
                                     "their own rules.")}


# ── leads ──────────────────────────────────────────────────────────────────
@router.get("/leads")
def list_leads(stage: Optional[str] = None, owner: Optional[str] = None, q: Optional[str] = None,
               page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
               ctx: C.Ctx = Depends(ctx_dep)):
    leads = _scoped_leads(ctx)
    opps = J.sync(ctx.db, ctx.org_id, leads) if leads else {}
    names = C.user_names(ctx.db, ctx.org_id)
    rows = []
    needle = (q or "").strip().lower()
    for l in leads:
        o = opps[l.id]
        if stage and o.stage != stage:
            continue
        if owner == "me" and l.assigned_to_id != ctx.user.id:
            continue
        if owner == "unassigned" and l.assigned_to_id:
            continue
        if owner and owner not in ("me", "unassigned") and l.assigned_to_id != owner:
            continue
        if needle and needle not in " ".join(filter(None, [C.lead_name(l), l.phone, l.email,
                                                            l.street_address, l.city])).lower():
            continue
        f = C.lead_fields(l)
        rows.append({"lead_id": l.id, "name": C.lead_name(l), "phone": l.phone, "email": l.email,
                     "address": C.address(l), "stage": o.stage, "stage_label": C.STAGE_LABEL[o.stage],
                     "owner_id": l.assigned_to_id, "owner_name": names.get(l.assigned_to_id),
                     "source": l.source_detail or l.source, "status": l.status, "is_test": bool(l.is_test),
                     "created_at": C.iso(l.created_at), **f})
    rows.sort(key=lambda r: r["created_at"] or "", reverse=True)
    total = len(rows)
    s = (page - 1) * page_size
    return {"items": rows[s:s + page_size], "total": total, "page": page, "page_size": page_size}


def _next_action(lead: Lead, opp: RoofingOpportunity, sig: J.Signals, cadence) -> Dict[str, str]:
    reps = sig.replies.get(lead.id, [])
    unread = [r for r in reps if r.reviewed_at is None]
    b = sig.booked(lead.id)
    if lead.status == "dnc" or sig.opted_out(lead.id):
        return {"key": "none", "label": "Opted out - do not contact"}
    if opp.stage == C.WON:
        return {"key": "none", "label": "Job won"}
    if opp.stage == C.LOST:
        return {"key": "reopen", "label": "Closed lost: %s" % (opp.lost_reason or "no reason")}
    if opp.stage == C.ESTIMATE_SENT:
        return {"key": "follow_up_estimate", "label": "Follow up on the estimate, then mark Won or Lost"}
    if opp.stage == C.INSPECTED:
        return {"key": "send_estimate", "label": "Send the estimate"}
    if b:
        return {"key": "prepare", "label": "Inspection %s - confirm with the homeowner" %
                b.booked_time.strftime("%a %b %d, %I:%M %p").replace(" 0", " ")}
    if unread:
        return {"key": "reply", "label": "Read and answer the new reply"}
    if opp.stage == C.OFFERED:
        return {"key": "await_pick", "label": "Waiting for the homeowner to pick a time"}
    if opp.stage == C.ENGAGED:
        return {"key": "offer", "label": "Offer inspection times"}
    if cadence is not None and cadence.status == "active":
        return {"key": "await_reply", "label": "Follow-up running - touch %d sent" % (cadence.current_touch_number or 0)}
    if opp.stage == C.CONTACTED:
        return {"key": "await_reply", "label": "Waiting on a reply"}
    if opp.stage == C.QUALIFIED:
        return {"key": "enroll", "label": "Ready to start follow-up"}
    return {"key": "review", "label": "Review why this homeowner isn't ready to contact"}


@router.get("/leads/{lead_id}")
def lead_workspace(lead_id: str, ctx: C.Ctx = Depends(ctx_dep)):
    """Everything for one homeowner in one place (the thread itself is the
    existing GET /communications/thread/{lead_id})."""
    from app.models.conversation_models import ConversationState
    from app.models.models import CadenceTouchLog
    from app.services import qualification as Q
    lead = _lead(ctx, lead_id)
    sig = J.Signals(ctx.db, [lead.id])
    opp = J.sync(ctx.db, ctx.org_id, [lead], sig=sig)[lead.id]
    cad = ctx.db.query(CadenceState).filter(CadenceState.lead_id == lead.id).first()
    logs = (ctx.db.query(CadenceTouchLog).filter(CadenceTouchLog.lead_id == lead.id)
            .order_by(CadenceTouchLog.created_at.desc()).limit(30).all())
    st = (ctx.db.query(ConversationState).filter(ConversationState.organization_id == ctx.org_id,
                                                ConversationState.lead_id == lead.id).first())
    qctx = Q.QualificationContext(ctx.db, [lead], ctx.org_id, Q.org_rules(ctx.db, ctx.org_id))
    qctx.suppressed_phones = Q.load_suppressed_phones(ctx.db, ctx.org_id)
    qual = {ch: Q.qualify_one(lead, ch, qctx) for ch in (Q.CHANNEL_SMS, Q.CHANNEL_EMAIL)}
    names = C.user_names(ctx.db, ctx.org_id)
    notes = (ctx.db.query(LeadNote).filter(LeadNote.organization_id == ctx.org_id, LeadNote.lead_id == lead.id)
             .order_by(LeadNote.created_at.desc()).limit(50).all())
    b = sig.booked(lead.id)
    return {
        "lead": {"id": lead.id, "name": C.lead_name(lead), "first_name": lead.first_name,
                 "last_name": lead.last_name, "phone": lead.phone, "email": lead.email,
                 "address": C.address(lead), "street_address": lead.street_address, "city": lead.city,
                 "state": lead.state, "zip_code": lead.zip_code, "source": lead.source,
                 "source_detail": lead.source_detail, "status": lead.status, "is_test": bool(lead.is_test),
                 "owner_id": lead.assigned_to_id, "owner_name": names.get(lead.assigned_to_id),
                 "sms_consent": bool(lead.sms_consent), "created_at": C.iso(lead.created_at)},
        "roofing": C.lead_fields(lead),
        "opportunity": {"stage": opp.stage, "stage_label": C.STAGE_LABEL[opp.stage],
                        "stage_entered_at": C.iso(opp.stage_entered_at), "estimate_amount": opp.estimate_amount,
                        "won_amount": opp.won_amount, "lost_reason": opp.lost_reason,
                        "inspection_type": opp.inspection_type},
        "history": J.history(ctx.db, ctx.org_id, lead.id),
        "inspection": ({"booking_id": b.id, "at_local": b.booked_time.isoformat(), "label": b.appt_label,
                        "duration_minutes": b.appt_duration, "salesperson_id": b.user_id,
                        "salesperson_name": names.get(b.user_id), "status": b.status} if b else None),
        "follow_up": ({"status": cad.status, "touch": cad.current_touch_number or 0,
                       "next_due_at": C.iso(cad.next_touch_due_at),
                       "last_sent_at": C.iso(cad.last_touch_sent_at)} if cad else None),
        "touches": [{"touch": t.touch_number, "attempt": t.attempt_seq, "channel": t.channel,
                     "outcome": t.outcome, "reason": t.reason, "at": C.iso(t.attempted_at or t.created_at),
                     "preview": t.body_preview} for t in logs],
        "touch_count": len(sig.outbound.get(lead.id, [])),
        "conversation": ({"mode": st.mode, "priority": st.priority, "intent": st.current_intent,
                          "needs_human_reason": st.needs_human_reason,
                          "has_open_question": bool(st.has_open_question)} if st else None),
        "qualification": {ch: {"bucket": d["bucket"], "priority": d["priority"], "score": d["score"],
                               "reasons": d["reasons"][:6]} for ch, d in qual.items()},
        "next_action": _next_action(lead, opp, sig, cad),
        "notes": [{"id": n.id, "body": n.body, "author": names.get(n.author_user_id), "at": C.iso(n.created_at)}
                  for n in notes],
    }


class LeadPatch(BaseModel):
    roof_issue: Optional[str] = Field(None, max_length=300)
    service_type: Optional[str] = None
    inspection_interest: Optional[str] = None
    urgency: Optional[str] = None
    roof_age: Optional[float] = Field(None, ge=0, le=150)
    insurance_claim: Optional[str] = Field(None, max_length=200)
    owner_id: Optional[str] = None
    estimate_amount: Optional[float] = Field(None, ge=0)
    won_amount: Optional[float] = Field(None, ge=0)
    inspection_type: Optional[str] = Field(None, max_length=120)


@router.patch("/leads/{lead_id}", dependencies=_WRITE)
def patch_lead(lead_id: str, body: LeadPatch, ctx: C.Ctx = Depends(ctx_dep)):
    lead = _lead(ctx, lead_id)
    data = body.model_dump(exclude_unset=True) if hasattr(body, "model_dump") else body.dict(exclude_unset=True)
    cf = C.jload(lead.custom_fields, {})
    cf = cf if isinstance(cf, dict) else {}
    for k in C.FIELD_KEYS:
        if k not in data:
            continue
        v = data[k]
        if k in C.CHOICES and v not in (None, "") and v not in C.CHOICES[k]:
            raise HTTPException(400, "%s must be one of %s" % (k, ", ".join(C.CHOICES[k])))
        if v in (None, ""):
            cf.pop(k, None)
        else:
            cf[k] = v
    lead.custom_fields = C.jdump(cf)
    if "owner_id" in data:
        C.require_manager(ctx)
        if data["owner_id"]:
            u = ctx.db.query(User).filter(User.id == data["owner_id"], User.organization_id == ctx.org_id).first()
            if u is None:
                raise HTTPException(404, "Salesperson not found in this company")
        lead.assigned_to_id = data["owner_id"] or None
    opp = J.ensure(ctx.db, ctx.org_id, [lead])[lead.id]
    for k in ("estimate_amount", "won_amount", "inspection_type"):
        if k in data:
            setattr(opp, k, data[k])
    _audit(ctx, "roofing.lead.update", "lead", lead.id, {"fields": sorted(data)})
    ctx.db.commit()
    return lead_workspace(lead_id, ctx)


class StageIn(BaseModel):
    stage: str
    reason: Optional[str] = Field(None, max_length=300)


@router.post("/leads/{lead_id}/stage", dependencies=_WRITE)
def move_stage(lead_id: str, body: StageIn, ctx: C.Ctx = Depends(ctx_dep)):
    lead = _lead(ctx, lead_id)
    opp = J.ensure(ctx.db, ctx.org_id, [lead])[lead.id]
    J.set_stage(ctx.db, opp, body.stage, ctx.user.id, body.reason)
    _audit(ctx, "roofing.stage", "lead", lead.id, {"to": body.stage})
    ctx.db.commit()
    return {"lead_id": lead.id, "stage": opp.stage, "stage_label": C.STAGE_LABEL[opp.stage],
            "lost_reason": opp.lost_reason}


class NoteIn(BaseModel):
    body: str = Field(..., min_length=1, max_length=4000)


@router.post("/leads/{lead_id}/notes", dependencies=_WRITE)
def add_note(lead_id: str, body: NoteIn, ctx: C.Ctx = Depends(ctx_dep)):
    lead = _lead(ctx, lead_id)
    n = LeadNote(organization_id=ctx.org_id, lead_id=lead.id, author_user_id=ctx.user.id, body=body.body.strip())
    ctx.db.add(n)
    ctx.db.commit()
    return {"id": n.id, "body": n.body, "author": ctx.user.full_name or ctx.user.email, "at": C.iso(n.created_at)}


# ── inbox ──────────────────────────────────────────────────────────────────
@router.get("/inbox")
def inbox(bucket: Optional[str] = None, q: Optional[str] = None, ctx: C.Ctx = Depends(ctx_dep)):
    if bucket and bucket not in I.RANK:
        raise HTTPException(400, "Unknown inbox bucket")
    return I.build(ctx.db, ctx, bucket=bucket, q=q)


# ── inspections ────────────────────────────────────────────────────────────
@router.get("/leads/{lead_id}/openings")
def lead_openings(lead_id: str, date_from: Optional[str] = None, days: int = Query(7, ge=1, le=21),
                  inspection_type: Optional[str] = None, salesperson_id: Optional[str] = None,
                  ctx: C.Ctx = Depends(ctx_dep)):
    lead = _lead(ctx, lead_id)
    p = S.get_profile(ctx.db, ctx.org_id)
    out = B.openings(ctx.db, ctx, lead, p, date_from, days, inspection_type, salesperson_id)
    sp = ctx.db.query(User).filter(User.id == out["salesperson_id"]).first()
    company = _profile_json(ctx.org, p)["company_name"]
    out["offer_text"] = B.offer_text(lead, sp, out.get("slots") or [], company)
    return out


class BookIn(BaseModel):
    starts_at: str
    inspection_type: Optional[str] = None
    salesperson_id: Optional[str] = None
    notes: Optional[str] = Field(None, max_length=2000)
    send_confirmation: bool = False


@router.post("/leads/{lead_id}/book", dependencies=_WRITE)
def book_inspection(lead_id: str, body: BookIn, ctx: C.Ctx = Depends(ctx_dep)):
    lead = _lead(ctx, lead_id)
    p = S.get_profile(ctx.db, ctx.org_id)
    out = B.book(ctx.db, ctx, lead, p, body.starts_at, body.inspection_type, body.salesperson_id,
                 body.notes, send_confirmation=body.send_confirmation)
    _audit(ctx, "roofing.inspection.book", "lead", lead.id, {"booking_id": out["booking_id"]})
    ctx.db.commit()
    return out


# ── pipeline ───────────────────────────────────────────────────────────────
@router.get("/pipeline")
def pipeline(owner: Optional[str] = None, q: Optional[str] = None, ctx: C.Ctx = Depends(ctx_dep)):
    leads = _scoped_leads(ctx)
    if owner == "me":
        leads = [l for l in leads if l.assigned_to_id == ctx.user.id]
    elif owner == "unassigned":
        leads = [l for l in leads if not l.assigned_to_id]
    elif owner:
        leads = [l for l in leads if l.assigned_to_id == owner]
    needle = (q or "").strip().lower()
    if needle:
        leads = [l for l in leads if needle in " ".join(filter(None, [C.lead_name(l), l.phone, l.email,
                                                                       l.street_address, l.city])).lower()]
    sig = J.Signals(ctx.db, [l.id for l in leads])
    opps = J.sync(ctx.db, ctx.org_id, leads, sig=sig) if leads else {}
    cads = {}
    for chunk in J._chunks([l.id for l in leads]):
        for c in ctx.db.query(CadenceState).filter(CadenceState.lead_id.in_(chunk)).all():
            cads[c.lead_id] = c
    names = C.user_names(ctx.db, ctx.org_id)
    now = datetime.utcnow()
    cols = {k: [] for k in C.STAGE_KEYS}
    for l in leads:
        o = opps[l.id]
        b = sig.booked(l.id)
        f = C.lead_fields(l)
        cols[o.stage].append({
            "lead_id": l.id, "name": C.lead_name(l), "address": C.address(l),
            "owner_id": l.assigned_to_id, "owner_name": names.get(l.assigned_to_id),
            "roof_issue": f.get("roof_issue"), "service_type": f.get("service_type"), "urgency": f.get("urgency"),
            "days_in_stage": (now - o.stage_entered_at).days if o.stage_entered_at else None,
            "inspection_at": b.booked_time.isoformat() if b else None,
            "estimate_amount": o.estimate_amount, "won_amount": o.won_amount, "lost_reason": o.lost_reason,
            "unread": sum(1 for r in sig.replies.get(l.id, []) if r.reviewed_at is None),
            "next_action": _next_action(l, o, sig, cads.get(l.id))["label"], "is_test": bool(l.is_test)})
    for v in cols.values():
        v.sort(key=lambda c: (-(c["unread"] or 0), c["days_in_stage"] if c["days_in_stage"] is not None else 0))
    est_total = sum(o.estimate_amount or 0 for o in opps.values() if o.stage == C.ESTIMATE_SENT)
    won_total = sum(o.won_amount or 0 for o in opps.values() if o.stage == C.WON)
    return {"columns": [{"key": s["key"], "label": s["label"], "hint": s["hint"], "count": len(cols[s["key"]]),
                         "cards": cols[s["key"]][:150]} for s in C.STAGES],
            "total": len(leads), "open_estimate_value": est_total, "won_value": won_total,
            "team": _team(ctx.db, ctx.org_id) if ctx.manager else [], "can_manage": ctx.manager}


@router.get("/team")
def team(ctx: C.Ctx = Depends(ctx_dep)):
    C.require_manager(ctx)
    return {"team": _team(ctx.db, ctx.org_id)}


# ── platform owner ─────────────────────────────────────────────────────────
class GodSetupIn(BaseModel):
    organization_id: str


@god_router.post("/setup")
def god_setup(body: GodSetupIn, db: Session = Depends(get_db), god: User = Depends(require_god)):
    """Grant the roofing feature set to one organization and configure it.
    Idempotent. Creates no users, switches no texting on, sends nothing."""
    org = db.query(Organization).filter(Organization.id == body.organization_id).first()
    if org is None:
        raise HTTPException(404, "Organization not found.")
    return S.setup(db, org, god.id, grant_features=True)


@god_router.post("/demo")
def god_demo(db: Session = Depends(get_db), god: User = Depends(require_god)):
    """Create or refresh the clearly labelled SAMPLE roofing workspace."""
    from app.services.roofing import demo_seed
    return demo_seed.seed(db, by_user_id=god.id)


# ── mission control (the front door) ───────────────────────────────────────
@router.get("/mission")
def mission(lane_limit: int = Query(25, ge=1, le=500), ctx: C.Ctx = Depends(ctx_dep)):
    from app.services.roofing import mission as MC
    return MC.build(ctx.db, ctx, lane_limit=lane_limit)


@router.get("/playbooks")
def playbooks(ctx: C.Ctx = Depends(ctx_dep)):
    from app.services.roofing import mission as MC
    return MC.playbook(ctx.db, ctx)


class TakeoverIn(BaseModel):
    human: bool
    reason: Optional[str] = Field(None, max_length=300)


@router.post("/leads/{lead_id}/takeover", dependencies=_WRITE)
def takeover(lead_id: str, body: TakeoverIn, ctx: C.Ctx = Depends(ctx_dep)):
    """A person takes this conversation from the AI (or hands it back).

    Uses the conversation engine's own mode switch, and pauses / resumes this
    homeowner's follow-up sequence so no automated touch lands while a person
    is talking to them. Resuming never restarts a finished or stopped sequence."""
    from app.services import conversation_intel as ci
    lead = _lead(ctx, lead_id)
    ci.set_mode(ctx.db, lead, "human_active" if body.human else "ai_active", user=ctx.user, reason=body.reason)
    cad = ctx.db.query(CadenceState).filter(CadenceState.lead_id == lead.id).first()
    if cad is not None:
        if body.human and cad.status == "active":
            cad.status = "paused"
        elif not body.human and cad.status == "paused":
            cad.status = "active"
    _audit(ctx, "roofing.takeover" if body.human else "roofing.handback", "lead", lead.id)
    ctx.db.commit()
    return {"lead_id": lead.id, "mode": "human_active" if body.human else "ai_active",
            "follow_up": cad.status if cad is not None else None}
