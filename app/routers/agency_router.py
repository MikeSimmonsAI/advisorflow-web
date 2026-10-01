"""INSURANCE AGENCY COMMAND (Max Life Command) — /agency/*

Implements /tmp agency API contract. Every route:
  * requires the `insurance_agency` feature (router dependency) and a tenant
    context; reads/writes are scoped to the ACTING workspace org;
  * resolves foreign ids to 404 (ids are always looked up inside the org filter);
  * audits writes via audit_log_router.log_action;
  * never contacts a provider - "simulate send" records an agency_copilot_events
    row with simulated=True and nothing else.
"""
import json
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.deps import get_db, require_not_observation, require_tenant_user
from app.models.agency_models import (AgencyAgentProfile, AgencyApplication, AgencyApplicationDocument,
                                      AgencyApplicationEvent, AgencyApplicationRequirement,
                                      AgencyAppointment, AgencyAssignment, AgencyCopilotEvent,
                                      AgencyPolicy, AgencyProspectProfile, AgencyRecruit,
                                      AgencyRecruitMilestone, AgencyRecruitStageEvent, AgencyTask)
from app.models.models import Lead, Message, Organization, Reply, User
from app.routers.audit_log_router import log_action
from app.services import lead_scope
from app.services.agency import applications as appsvc
from app.services.agency import distribution as dist
from app.services.agency import grounding
from app.services.agency import intelligence as intel
from app.services.agency import queries as Q
from app.services.agency.brief import build_brief
from app.services.agency.common import (ALL_FACTORS, config_dict, get_config, iso, jdump, jload,
                                        lead_is_demo, lead_name, now, paginate, profile_for, ref,
                                        user_names)
from app.services.agency.copilot import analyze
from app.services.entitlements import require_feature
from app.services.platform_owner import require_tenant_context

router = APIRouter(prefix="/agency", tags=["agency"],
                   dependencies=[Depends(require_feature("insurance_agency"))])

_WRITE = [Depends(require_not_observation)]


def ctx_dep(request: Request, db: Session = Depends(get_db),
            user: User = Depends(require_tenant_context),
            _t: User = Depends(require_tenant_user)) -> Q.Ctx:
    from app.services.platform_owner import is_platform_pseudo_org
    org_id = lead_scope.active_workspace_org_id(user, db, request)
    if not org_id or is_platform_pseudo_org(org_id):
        raise HTTPException(409, "No customer organization is selected. Select a workspace first.")
    return Q.Ctx(db, user, org_id, lead_scope.is_manager_here(user, db, request), request)


def _manager(ctx: Q.Ctx):
    if not ctx.manager:
        raise HTTPException(403, "Manager access required")


def _audit(ctx: Q.Ctx, action, ttype, tid, details=None):
    log_action(ctx.db, ctx.org_id, ctx.user.id, action, ttype, tid, details=details, commit=False)


def _lead(ctx: Q.Ctx, lead_id: str) -> Lead:
    lead = lead_scope.load_lead_in_scope(ctx.db, ctx.user, lead_id, ctx.request)
    if lead.organization_id != ctx.org_id:
        raise HTTPException(404, "Lead not found")
    return lead


def _org_user(ctx: Q.Ctx, user_id: str) -> User:
    u = ctx.db.query(User).filter(User.id == user_id, User.organization_id == ctx.org_id).first()
    if u is None:
        raise HTTPException(404, "Agent not found")
    return u


def _work_owner(ctx: Q.Ctx, user_id: Optional[str]) -> Optional[str]:
    """Who a new piece of work (appointment, application, task) is for. An
    advisor creates work for THEMSELF; handing it to a colleague is a manager's
    call (same rule as recruits and lead assignment)."""
    if not user_id:
        return None
    u = _org_user(ctx, user_id)
    if not ctx.manager and u.id != ctx.user.id:
        raise HTTPException(403, "Only a manager can create work for another agent")
    return u.id


def _get(ctx: Q.Ctx, model, id_, own_col=None, label="Record"):
    q = ctx.db.query(model).filter(model.id == id_, model.organization_id == ctx.org_id)
    if own_col is not None and not ctx.manager:
        q = q.filter(own_col == ctx.user.id)
    obj = q.first()
    if obj is None:
        raise HTTPException(404, "%s not found" % label)
    return obj


def _parse_dt(v, field):
    if v in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        raise HTTPException(422, "%s must be ISO-8601" % field)


def _parse_date(v, field):
    if v in (None, ""):
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        raise HTTPException(422, "%s must be YYYY-MM-DD" % field)


# ── conversation (merged, newest last) ──────────────────────────────────────

def _conversation(ctx: Q.Ctx, lead_id: str) -> List[Dict[str, Any]]:
    db = ctx.db
    out = []
    for m in db.query(Message).filter(Message.lead_id == lead_id):
        out.append({"id": m.id, "direction": "outbound", "channel": "sms", "body": m.body,
                    "at": iso(m.sent_at), "simulated": False, "provenance": "messages"})
    for r in db.query(Reply).filter(Reply.lead_id == lead_id):
        out.append({"id": r.id, "direction": "inbound", "channel": r.source or "sms", "body": r.body,
                    "at": iso(r.received_at), "simulated": False, "provenance": "replies"})
    for e in db.query(AgencyCopilotEvent).filter(AgencyCopilotEvent.organization_id == ctx.org_id,
                                                 AgencyCopilotEvent.lead_id == lead_id,
                                                 AgencyCopilotEvent.kind == "simulated_send"):
        out.append({"id": e.id, "direction": "outbound", "channel": "simulated", "body": e.body,
                    "at": iso(e.created_at), "simulated": True, "provenance": "agency_copilot_events"})
    out.sort(key=lambda x: (x["at"] or "", x["id"]))
    return out


# ── prospects ───────────────────────────────────────────────────────────────

@router.get("/prospects")
def list_prospects(q: Optional[str] = None, status: Optional[str] = None, intent: Optional[str] = None,
                   agent_id: Optional[str] = None, unassigned: Optional[bool] = None,
                   need: Optional[str] = None, assignment: Optional[str] = None,
                   uncontacted: Optional[bool] = None, state: Optional[str] = None,
                   page: int = 1, per_page: int = Query(50, le=200), ctx: Q.Ctx = Depends(ctx_dep)):
    return Q.page_prospects(ctx, page=page, per_page=per_page, q=q, status=status, intent=intent,
                            agent_id=agent_id, unassigned=unassigned, need=need,
                            assignment=assignment, uncontacted=uncontacted, state=state)


@router.get("/conversations")
def list_conversations(q: Optional[str] = None, awaiting_reply: Optional[bool] = None,
                       page: int = 1, per_page: int = Query(50, le=200), ctx: Q.Ctx = Depends(ctx_dep)):
    """Prospects with a conversation in the acting workspace (Message / Reply /
    SIMULATED copilot sends), newest activity first. Read-only; the thread and
    the copilot live on the prospect record."""
    return Q.page_conversations(ctx, page=page, per_page=per_page, q=q, awaiting_reply=awaiting_reply)


_PROFILE_LIST = ("financial_goals", "stated_concerns", "need_categories")
_PROFILE_BOOL = ("retirement_interest", "business_owner_interest", "living_benefits_interest")


def _profile_dict(p: Optional[AgencyProspectProfile]) -> Dict[str, Any]:
    if p is None:
        return {"household": None, "preferred_contact": None, "financial_goals": [], "stated_concerns": [],
                "need_categories": [], "retirement_interest": None, "business_owner_interest": None,
                "living_benefits_interest": None, "intent_level": None,
                "automation_paused": False, "human_takeover": False}
    d = {"household": jload(p.household), "preferred_contact": p.preferred_contact,
         "intent_level": p.intent_level, "automation_paused": bool(p.automation_paused),
         "human_takeover": bool(p.human_takeover)}
    for k in _PROFILE_LIST:
        d[k] = jload(getattr(p, k), [])
    for k in _PROFILE_BOOL:
        d[k] = getattr(p, k)
    return d


@router.get("/prospects/{lead_id}")
def get_prospect(lead_id: str, ctx: Q.Ctx = Depends(ctx_dep)):
    lead = _lead(ctx, lead_id)
    db = ctx.db
    p = profile_for(db, ctx.org_id, lead.id)
    base = [r for r in Q.list_prospects(ctx, lead_id=lead.id) if r["id"] == lead.id]
    out = dict(base[0]) if base else {"id": lead.id, "name": lead_name(lead)}
    out["profile"] = _profile_dict(p)
    consent = []
    if lead.sms_consent_timestamp or lead.sms_consent:
        consent.append({"type": "sms", "granted": bool(lead.sms_consent), "at": iso(lead.sms_consent_timestamp),
                        "source": lead.sms_consent_source, "text": lead.sms_consent_text})
    out["provenance"] = {"source": lead.source, "source_detail": getattr(lead, "source_detail", None),
                         "page": p.page if p else None, "utm": jload(p.utm) if p else None,
                         "submitted_at": iso(lead.created_at), "consent_events": consent}
    names = user_names(db, [a.agent_user_id for a in db.query(AgencyAssignment).filter(
        AgencyAssignment.organization_id == ctx.org_id, AgencyAssignment.lead_id == lead.id)])
    hist = (db.query(AgencyAssignment).filter(AgencyAssignment.organization_id == ctx.org_id,
                                              AgencyAssignment.lead_id == lead.id)
            .order_by(AgencyAssignment.created_at, AgencyAssignment.attempt).all())
    out["assignment_history"] = [dist.assignment_row(a, names) for a in hist]
    out["conversation"] = _conversation(ctx, lead.id)
    out["applications"] = Q.list_applications(ctx, lead_id=lead.id)
    out["policies"] = Q.list_policies(ctx, lead_id=lead.id)
    out["appointments"] = Q.list_appointments(ctx, lead_id=lead.id)
    out["tasks"] = [Q.task_row(t) for t in db.query(AgencyTask).filter(
        AgencyTask.organization_id == ctx.org_id, AgencyTask.lead_id == lead.id)
        .order_by(AgencyTask.created_at)]
    return out


class ProfileIn(BaseModel):
    household: Optional[Dict[str, Any]] = None
    preferred_contact: Optional[str] = None
    financial_goals: Optional[List[str]] = None
    stated_concerns: Optional[List[str]] = None
    need_categories: Optional[List[str]] = None
    retirement_interest: Optional[bool] = None
    business_owner_interest: Optional[bool] = None
    living_benefits_interest: Optional[bool] = None
    intent_level: Optional[str] = None


@router.patch("/prospects/{lead_id}/profile", dependencies=_WRITE)
def patch_profile(lead_id: str, body: ProfileIn, ctx: Q.Ctx = Depends(ctx_dep)):
    lead = _lead(ctx, lead_id)
    data = body.dict(exclude_unset=True)
    if "intent_level" in data and data["intent_level"] not in (None, "high", "medium", "low"):
        raise HTTPException(422, "intent_level must be high, medium, low or null")
    p = profile_for(ctx.db, ctx.org_id, lead.id)
    if p is None:
        p = AgencyProspectProfile(organization_id=ctx.org_id, lead_id=lead.id,
                                  is_demo=bool(lead.is_test))
        ctx.db.add(p)
    before = _profile_dict(p) if p.id else None
    for k, v in data.items():
        setattr(p, k, jdump(v) if k in _PROFILE_LIST + ("household",) else v)
    ctx.db.flush()
    _audit(ctx, "agency.profile.updated", "lead", lead.id, {"fields": sorted(data)})
    ctx.db.commit()
    return _profile_dict(p)


@router.get("/prospects/{lead_id}/brief")
def prospect_brief(lead_id: str, assist: Optional[str] = Query(None, pattern="^(ai|rules)$"),
                   ctx: Q.Ctx = Depends(ctx_dep)):
    """Rules always compute the brief. `assist=ai` (a person asked) lets the
    model REPHRASE the narrative from these facts only; the verifier rejects
    anything new and the rules text stands (generated_by stays "rules")."""
    lead = _lead(ctx, lead_id)
    p = profile_for(ctx.db, ctx.org_id, lead.id)
    rec = dist.recommend(ctx.db, ctx.org_id, lead) if not lead.assigned_to_id else None
    out = build_brief(lead, p, _conversation(ctx, lead.id), rec)
    out["is_demo"] = lead_is_demo(lead, p)
    return grounding.enhance_brief(out, assist == "ai", ctx.user.id, ctx.org_id)


# ── distribution ────────────────────────────────────────────────────────────

@router.get("/distribution/config")
def get_distribution_config(ctx: Q.Ctx = Depends(ctx_dep)):
    cfg = get_config(ctx.db, ctx.org_id)
    ctx.db.commit()
    return config_dict(cfg)


class ConfigIn(BaseModel):
    acceptance_timeout_minutes: Optional[int] = Field(None, ge=1, le=10080)
    escalation_user_id: Optional[str] = None
    max_active_per_agent: Optional[int] = Field(None, ge=1, le=10000)
    factors_enabled: Optional[List[str]] = None
    stalled_days: Optional[int] = Field(None, ge=1, le=365)
    response_target_minutes: Optional[int] = Field(None, ge=1, le=10080)
    review_window_days: Optional[int] = Field(None, ge=0, le=366)
    workload_alert_pct: Optional[int] = Field(None, ge=1, le=1000)
    recruit_stages: Optional[List[str]] = None


@router.put("/distribution/config", dependencies=_WRITE)
def put_distribution_config(body: ConfigIn, ctx: Q.Ctx = Depends(ctx_dep)):
    _manager(ctx)
    cfg = get_config(ctx.db, ctx.org_id)
    data = body.dict(exclude_unset=True)
    if data.get("escalation_user_id"):
        _org_user(ctx, data["escalation_user_id"])
    if "factors_enabled" in data:
        bad = [f for f in data["factors_enabled"] or [] if f not in ALL_FACTORS]
        if bad:
            raise HTTPException(422, "Unknown factor(s): %s" % ", ".join(bad))
    if "recruit_stages" in data and not data["recruit_stages"]:
        raise HTTPException(422, "recruit_stages cannot be empty")
    before = config_dict(cfg)
    for k, v in data.items():
        setattr(cfg, k, jdump(v) if k in ("factors_enabled", "recruit_stages") else v)
    ctx.db.flush()
    log_action(ctx.db, ctx.org_id, ctx.user.id, "agency.distribution_config.updated",
               "agency_distribution_config", cfg.id, before=before, after=config_dict(cfg), commit=False)
    ctx.db.commit()
    return config_dict(cfg)


@router.get("/prospects/{lead_id}/recommendation")
def recommendation(lead_id: str, ctx: Q.Ctx = Depends(ctx_dep)):
    lead = _lead(ctx, lead_id)
    out = dist.recommend(ctx.db, ctx.org_id, lead)
    ctx.db.commit()
    return out


class AssignIn(BaseModel):
    agent_id: str
    note: Optional[str] = None


@router.post("/prospects/{lead_id}/assign", dependencies=_WRITE)
def assign(lead_id: str, body: AssignIn, ctx: Q.Ctx = Depends(ctx_dep)):
    _manager(ctx)
    lead = _lead(ctx, lead_id)
    agent = _org_user(ctx, body.agent_id)
    rec = dist.recommend(ctx.db, ctx.org_id, lead)
    cand = next((c for c in rec["candidates"] if c["agent_id"] == agent.id), None)
    if cand is None:
        raise HTTPException(409, "This user has no agency agent profile")
    a = dist.offer(ctx.db, ctx.org_id, lead, agent, ctx.user.id, body.note, cand["reasons"],
                   is_demo=bool(lead.is_test))
    ctx.db.commit()
    out = dist.assignment_row(a, user_names(ctx.db, [agent.id]), {lead.id: lead_name(lead)})
    out["blockers_at_offer"] = cand["blockers"]
    return out


def _assignment(ctx: Q.Ctx, aid: str) -> AgencyAssignment:
    a = ctx.db.query(AgencyAssignment).filter(AgencyAssignment.id == aid,
                                              AgencyAssignment.organization_id == ctx.org_id).first()
    if a is None or (not ctx.manager and a.agent_user_id != ctx.user.id):
        raise HTTPException(404, "Assignment not found")
    if a.state != "offered":
        raise HTTPException(409, "Assignment is %s, not offered" % a.state)
    return a


@router.post("/assignments/{aid}/accept", dependencies=_WRITE)
def accept(aid: str, ctx: Q.Ctx = Depends(ctx_dep)):
    a = _assignment(ctx, aid)
    if a.expires_at and a.expires_at < now():
        raise HTTPException(409, "Offer expired")
    dist.accept(ctx.db, ctx.org_id, a, ctx.user.id)
    ctx.db.commit()
    return dist.assignment_row(a, user_names(ctx.db, [a.agent_user_id]))


class DeclineIn(BaseModel):
    reason: Optional[str] = None


@router.post("/assignments/{aid}/decline", dependencies=_WRITE)
def decline(aid: str, body: DeclineIn = Body(default=DeclineIn()), ctx: Q.Ctx = Depends(ctx_dep)):
    a = _assignment(ctx, aid)
    nxt = dist.decline(ctx.db, ctx.org_id, a, ctx.user.id, body.reason)
    ctx.db.commit()
    names = user_names(ctx.db, [a.agent_user_id, nxt.agent_user_id if nxt else None])
    return {"declined": dist.assignment_row(a, names),
            "next": dist.assignment_row(nxt, names) if nxt else None}


@router.post("/assignments/sweep", dependencies=_WRITE)
def sweep(ctx: Q.Ctx = Depends(ctx_dep)):
    _manager(ctx)
    out = dist.sweep(ctx.db, ctx.org_id, ctx.user.id)
    ctx.db.commit()
    return out


@router.get("/assignments")
def list_assignments(state: Optional[str] = None, agent_id: Optional[str] = None,
                     page: int = 1, per_page: int = Query(50, le=200), ctx: Q.Ctx = Depends(ctx_dep)):
    q = ctx.own(ctx.db.query(AgencyAssignment).filter(AgencyAssignment.organization_id == ctx.org_id),
                AgencyAssignment.agent_user_id)
    if state:
        q = q.filter(AgencyAssignment.state == state)
    if agent_id:
        q = q.filter(AgencyAssignment.agent_user_id == agent_id)
    rows = q.order_by(AgencyAssignment.created_at.desc()).all()
    names = user_names(ctx.db, [a.agent_user_id for a in rows])
    lnames = Q._lead_names(ctx.db, ctx.org_id, [a.lead_id for a in rows])
    return paginate([dist.assignment_row(a, names, lnames) for a in rows], page, per_page)


# ── agents ──────────────────────────────────────────────────────────────────

@router.get("/agents")
def list_agents(with_capacity: Optional[bool] = None, page: int = 1, per_page: int = Query(50, le=200),
                ctx: Q.Ctx = Depends(ctx_dep)):
    return paginate(Q.list_agents(ctx, with_capacity=with_capacity), page, per_page)


class AgentProfileIn(BaseModel):
    jurisdictions: Optional[List[str]] = None
    specializations: Optional[List[str]] = None
    available: Optional[bool] = None
    active: Optional[bool] = None
    max_active: Optional[int] = Field(None, ge=1, le=10000)


@router.put("/agents/{user_id}/profile", dependencies=_WRITE)
def put_agent_profile(user_id: str, body: AgentProfileIn, ctx: Q.Ctx = Depends(ctx_dep)):
    _manager(ctx)
    u = _org_user(ctx, user_id)
    p = ctx.db.query(AgencyAgentProfile).filter(AgencyAgentProfile.organization_id == ctx.org_id,
                                                AgencyAgentProfile.user_id == u.id).first()
    if p is None:
        p = AgencyAgentProfile(organization_id=ctx.org_id, user_id=u.id)
        ctx.db.add(p)
    data = body.dict(exclude_unset=True)
    for k, v in data.items():
        if k == "jurisdictions":
            v = sorted({s.strip().upper() for s in v or [] if s.strip()})
        setattr(p, k, jdump(v) if k in ("jurisdictions", "specializations") else v)
    ctx.db.flush()
    _audit(ctx, "agency.agent_profile.updated", "user", u.id, data)
    ctx.db.commit()
    return next(a for a in Q.list_agents(ctx) if a["user_id"] == u.id)


# ── copilot ─────────────────────────────────────────────────────────────────

@router.get("/prospects/{lead_id}/copilot")
def copilot(lead_id: str, assist: Optional[str] = Query(None, pattern="^(ai|rules)$"),
            ctx: Q.Ctx = Depends(ctx_dep)):
    lead = _lead(ctx, lead_id)
    p = profile_for(ctx.db, ctx.org_id, lead.id)
    out = analyze(_conversation(ctx, lead.id), lead_name(lead))
    out = grounding.enhance_copilot(out, assist == "ai", ctx.user.id, ctx.org_id, lead_name(lead))
    out["automation_paused"] = bool(p.automation_paused) if p else False
    out["human_takeover"] = bool(p.human_takeover) if p else False
    return out


class SimSendIn(BaseModel):
    body: str = Field(..., min_length=1, max_length=2000)


def _ensure_profile(ctx, lead):
    p = profile_for(ctx.db, ctx.org_id, lead.id)
    if p is None:
        p = AgencyProspectProfile(organization_id=ctx.org_id, lead_id=lead.id, is_demo=bool(lead.is_test))
        ctx.db.add(p)
    return p


def _event(ctx, lead, kind, body=None):
    e = AgencyCopilotEvent(organization_id=ctx.org_id, lead_id=lead.id, kind=kind, body=body,
                           simulated=True, by_user_id=ctx.user.id, is_demo=bool(lead.is_test),
                           created_at=now())
    ctx.db.add(e)
    ctx.db.flush()
    return e


@router.post("/prospects/{lead_id}/copilot/simulate-send", dependencies=_WRITE)
def simulate_send(lead_id: str, body: SimSendIn, ctx: Q.Ctx = Depends(ctx_dep)):
    """Records a SIMULATED outbound event. No Message row, no provider call."""
    lead = _lead(ctx, lead_id)
    e = _event(ctx, lead, "simulated_send", body.body)
    _audit(ctx, "agency.copilot.simulated_send", "lead", lead.id, {"event_id": e.id, "simulated": True})
    ctx.db.commit()
    return {"id": e.id, "kind": e.kind, "body": e.body, "simulated": True, "provider_called": False,
            "at": iso(e.created_at), "is_demo": bool(e.is_demo),
            "note": "Simulated send - nothing was sent to the prospect."}


class AutomationIn(BaseModel):
    paused: bool


@router.post("/prospects/{lead_id}/automation", dependencies=_WRITE)
def automation(lead_id: str, body: AutomationIn, ctx: Q.Ctx = Depends(ctx_dep)):
    lead = _lead(ctx, lead_id)
    p = _ensure_profile(ctx, lead)
    p.automation_paused = body.paused
    _event(ctx, lead, "automation_paused" if body.paused else "automation_resumed")
    _audit(ctx, "agency.automation.%s" % ("paused" if body.paused else "resumed"), "lead", lead.id)
    ctx.db.commit()
    return {"automation_paused": body.paused}


@router.post("/prospects/{lead_id}/takeover", dependencies=_WRITE)
def takeover(lead_id: str, ctx: Q.Ctx = Depends(ctx_dep)):
    lead = _lead(ctx, lead_id)
    p = _ensure_profile(ctx, lead)
    p.human_takeover = True
    p.automation_paused = True
    p.takeover_at = now()
    p.takeover_by = ctx.user.id
    _event(ctx, lead, "takeover")
    _audit(ctx, "agency.takeover", "lead", lead.id)
    ctx.db.commit()
    return {"human_takeover": True, "automation_paused": True, "by": ctx.user.id, "at": iso(p.takeover_at)}


class TaskIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    kind: str = "follow_up"
    due_at: Optional[str] = None
    assigned_user_id: Optional[str] = None


@router.post("/prospects/{lead_id}/tasks", dependencies=_WRITE)
def create_prospect_task(lead_id: str, body: TaskIn, ctx: Q.Ctx = Depends(ctx_dep)):
    lead = _lead(ctx, lead_id)
    assignee = _work_owner(ctx, body.assigned_user_id) or (lead.assigned_to_id or ctx.user.id)
    t = AgencyTask(organization_id=ctx.org_id, kind=body.kind, title=body.title,
                   due_at=_parse_dt(body.due_at, "due_at"), lead_id=lead.id, assigned_user_id=assignee,
                   created_by=ctx.user.id, is_demo=bool(lead.is_test))
    ctx.db.add(t)
    ctx.db.flush()
    _audit(ctx, "agency.task.created", "agency_task", t.id, {"lead_id": lead.id})
    ctx.db.commit()
    return Q.task_row(t)


# ── appointments ────────────────────────────────────────────────────────────

APPT_STATUSES = ("pending", "confirmed", "completed", "missed", "cancelled")


@router.get("/appointments")
def list_appointments(range: Optional[str] = None, status: Optional[str] = None,
                      agent_id: Optional[str] = None, needs_confirmation: Optional[bool] = None,
                      page: int = 1, per_page: int = Query(50, le=200), ctx: Q.Ctx = Depends(ctx_dep)):
    return paginate(Q.list_appointments(ctx, range_=range, status=status, agent_id=agent_id,
                                        needs_confirmation=needs_confirmation), page, per_page)


class ApptIn(BaseModel):
    prospect_id: str
    agent_id: str
    type: Optional[str] = None
    medium: Optional[str] = None
    starts_at: str
    notes: Optional[str] = None


@router.post("/appointments", status_code=201, dependencies=_WRITE)
def create_appointment(body: ApptIn, ctx: Q.Ctx = Depends(ctx_dep)):
    lead = _lead(ctx, body.prospect_id)
    agent = _org_user(ctx, _work_owner(ctx, body.agent_id))
    a = AgencyAppointment(organization_id=ctx.org_id, lead_id=lead.id, agent_user_id=agent.id,
                          type=body.type, medium=body.medium, starts_at=_parse_dt(body.starts_at, "starts_at"),
                          status="pending", notes=body.notes, is_demo=bool(lead.is_test))
    ctx.db.add(a)
    ctx.db.flush()
    _audit(ctx, "agency.appointment.created", "agency_appointment", a.id, {"lead_id": lead.id})
    ctx.db.commit()
    return next(r for r in Q.list_appointments(ctx, lead_id=lead.id) if r["id"] == a.id)


class ApptPatch(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None


@router.patch("/appointments/{appt_id}", dependencies=_WRITE)
def patch_appointment(appt_id: str, body: ApptPatch, ctx: Q.Ctx = Depends(ctx_dep)):
    a = _get(ctx, AgencyAppointment, appt_id, AgencyAppointment.agent_user_id, "Appointment")
    if body.status is not None:
        if body.status not in APPT_STATUSES:
            raise HTTPException(422, "status must be one of %s" % ", ".join(APPT_STATUSES))
        a.status = body.status
    if body.notes is not None:
        a.notes = body.notes
    _audit(ctx, "agency.appointment.updated", "agency_appointment", a.id, body.dict(exclude_unset=True))
    ctx.db.commit()
    return next(r for r in Q.list_appointments(ctx, lead_id=a.lead_id) if r["id"] == a.id)


@router.get("/appointments/{appt_id}")
def get_appointment(appt_id: str, ctx: Q.Ctx = Depends(ctx_dep)):
    a = _get(ctx, AgencyAppointment, appt_id, AgencyAppointment.agent_user_id, "Appointment")
    return next(r for r in Q.list_appointments(ctx, lead_id=a.lead_id) if r["id"] == a.id)


# ── applications ────────────────────────────────────────────────────────────

@router.get("/applications")
def list_applications(status: Optional[str] = None, agent_id: Optional[str] = None,
                      stalled: Optional[bool] = None, open: Optional[bool] = None,
                      page: int = 1, per_page: int = Query(50, le=200), ctx: Q.Ctx = Depends(ctx_dep)):
    return paginate(Q.list_applications(ctx, status=status, agent_id=agent_id, stalled=stalled,
                                        open_only=open), page, per_page)


class AppIn(BaseModel):
    prospect_id: str
    agent_id: str
    carrier: Optional[str] = None
    product_category: Optional[str] = None
    notes: Optional[str] = None


@router.post("/applications", status_code=201, dependencies=_WRITE)
def create_application(body: AppIn, ctx: Q.Ctx = Depends(ctx_dep)):
    lead = _lead(ctx, body.prospect_id)
    agent = _org_user(ctx, _work_owner(ctx, body.agent_id))
    t = now()
    a = AgencyApplication(organization_id=ctx.org_id, lead_id=lead.id, agent_user_id=agent.id,
                          owner_user_id=ctx.user.id, carrier=body.carrier,
                          product_category=body.product_category, status="draft",
                          status_changed_at=t, notes=body.notes, is_demo=bool(lead.is_test), created_at=t)
    ctx.db.add(a)
    ctx.db.flush()
    ctx.db.add(AgencyApplicationEvent(organization_id=ctx.org_id, application_id=a.id, from_status=None,
                                      to_status="draft", at=t, by_user_id=ctx.user.id, note="created"))
    _audit(ctx, "agency.application.created", "agency_application", a.id, {"lead_id": lead.id})
    ctx.db.commit()
    return _application_detail(ctx, a)


def _application_detail(ctx: Q.Ctx, a: AgencyApplication) -> Dict[str, Any]:
    db = ctx.db
    names = user_names(db, [a.agent_user_id])
    lnames = Q._lead_names(db, ctx.org_id, [a.lead_id])
    out = Q.application_row(ctx, a, names, lnames)
    out["notes"] = a.notes
    out["requirements"] = [{"id": r.id, "label": r.label, "status": r.status, "due": iso(r.due)}
                           for r in db.query(AgencyApplicationRequirement).filter(
                               AgencyApplicationRequirement.organization_id == ctx.org_id,
                               AgencyApplicationRequirement.application_id == a.id)
                           .order_by(AgencyApplicationRequirement.created_at)]
    out["documents"] = [{"id": d.id, "name": d.name, "kind": d.kind, "added_at": iso(d.added_at)}
                        for d in db.query(AgencyApplicationDocument).filter(
                            AgencyApplicationDocument.organization_id == ctx.org_id,
                            AgencyApplicationDocument.application_id == a.id)]
    evs = db.query(AgencyApplicationEvent).filter(AgencyApplicationEvent.organization_id == ctx.org_id,
                                                  AgencyApplicationEvent.application_id == a.id
                                                  ).order_by(AgencyApplicationEvent.at).all()
    by = user_names(db, [e.by_user_id for e in evs])
    out["history"] = [{"from": e.from_status, "to": e.to_status, "at": iso(e.at),
                       "by": ref(e.by_user_id, by), "note": e.note} for e in evs]
    out["tasks"] = [Q.task_row(t) for t in db.query(AgencyTask).filter(
        AgencyTask.organization_id == ctx.org_id, AgencyTask.application_id == a.id)]
    out["disclaimer"] = ("Statuses are recorded by the agency. This system does not underwrite, "
                         "bind coverage, or connect to carrier systems.")
    return out


@router.get("/applications/{app_id}")
def get_application(app_id: str, ctx: Q.Ctx = Depends(ctx_dep)):
    return _application_detail(ctx, _get(ctx, AgencyApplication, app_id, AgencyApplication.agent_user_id,
                                          "Application"))


class TransitionIn(BaseModel):
    to: str
    note: Optional[str] = None


@router.post("/applications/{app_id}/transition", dependencies=_WRITE)
def transition_application(app_id: str, body: TransitionIn, ctx: Q.Ctx = Depends(ctx_dep)):
    a = _get(ctx, AgencyApplication, app_id, AgencyApplication.agent_user_id, "Application")
    frm = a.status
    appsvc.transition(ctx.db, a, body.to, ctx.user.id, body.note)
    _audit(ctx, "agency.application.transition", "agency_application", a.id, {"from": frm, "to": body.to})
    ctx.db.commit()
    return _application_detail(ctx, a)


class RequirementIn(BaseModel):
    label: str = Field(..., min_length=1, max_length=300)
    due: Optional[str] = None


@router.post("/applications/{app_id}/requirements", status_code=201, dependencies=_WRITE)
def add_requirement(app_id: str, body: RequirementIn, ctx: Q.Ctx = Depends(ctx_dep)):
    a = _get(ctx, AgencyApplication, app_id, AgencyApplication.agent_user_id, "Application")
    r = AgencyApplicationRequirement(organization_id=ctx.org_id, application_id=a.id, label=body.label,
                                     due=_parse_date(body.due, "due"))
    ctx.db.add(r)
    ctx.db.flush()
    _audit(ctx, "agency.application.requirement_added", "agency_application", a.id, {"label": body.label})
    ctx.db.commit()
    return _application_detail(ctx, a)


class DocumentIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=300)
    kind: Optional[str] = None


@router.post("/applications/{app_id}/documents", status_code=201, dependencies=_WRITE)
def add_document(app_id: str, body: DocumentIn, ctx: Q.Ctx = Depends(ctx_dep)):
    a = _get(ctx, AgencyApplication, app_id, AgencyApplication.agent_user_id, "Application")
    ctx.db.add(AgencyApplicationDocument(organization_id=ctx.org_id, application_id=a.id, name=body.name,
                                         kind=body.kind, added_at=now(), added_by=ctx.user.id))
    _audit(ctx, "agency.application.document_added", "agency_application", a.id, {"name": body.name})
    ctx.db.commit()
    return _application_detail(ctx, a)


class IssueIn(BaseModel):
    policy_number: Optional[str] = None
    carrier: Optional[str] = None
    effective_date: Optional[str] = None
    annual_review_date: Optional[str] = None


@router.post("/applications/{app_id}/issue", dependencies=_WRITE)
def issue(app_id: str, body: IssueIn = Body(default=IssueIn()), ctx: Q.Ctx = Depends(ctx_dep)):
    a = _get(ctx, AgencyApplication, app_id, AgencyApplication.agent_user_id, "Application")
    appsvc.mark_issued(ctx.db, a, ctx.user.id, "issued recorded")
    eff = _parse_date(body.effective_date, "effective_date")
    review = _parse_date(body.annual_review_date, "annual_review_date")
    if review is None and eff is not None:
        try:
            review = eff.replace(year=eff.year + 1)
        except ValueError:
            review = eff + timedelta(days=365)
    p = AgencyPolicy(organization_id=ctx.org_id, lead_id=a.lead_id, application_id=a.id,
                     agent_user_id=a.agent_user_id, policy_number=body.policy_number,
                     carrier=body.carrier or a.carrier, product_category=a.product_category,
                     status="in_force" if eff else "pending", effective_date=eff,
                     annual_review_date=review, is_demo=bool(a.is_demo))
    ctx.db.add(p)
    ctx.db.flush()
    a.policy_id = p.id
    lead = ctx.db.query(Lead).filter(Lead.id == a.lead_id, Lead.organization_id == ctx.org_id).first()
    if lead is not None and lead.status not in ("dnc",):
        lead.status = "client"   # client relationship: the same Lead, now a client
    _audit(ctx, "agency.application.issued", "agency_application", a.id, {"policy_id": p.id})
    ctx.db.commit()
    return {"application": _application_detail(ctx, a), "policy": _policy_detail(ctx, p)}


# ── policies ────────────────────────────────────────────────────────────────

@router.get("/policies")
def list_policies(review_due: Optional[bool] = None, status: Optional[str] = None,
                  review_month: Optional[str] = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
                  page: int = 1, per_page: int = Query(50, le=200), ctx: Q.Ctx = Depends(ctx_dep)):
    return paginate(Q.list_policies(ctx, review_due=review_due, status=status, review_month=review_month),
                    page, per_page)


def _policy_detail(ctx, p):
    row = next(r for r in Q.list_policies(ctx, lead_id=p.lead_id) if r["id"] == p.id)
    row["tasks"] = [Q.task_row(t) for t in ctx.db.query(AgencyTask).filter(
        AgencyTask.organization_id == ctx.org_id, AgencyTask.policy_id == p.id).order_by(AgencyTask.created_at)]
    return row


@router.get("/policies/{policy_id}")
def get_policy(policy_id: str, ctx: Q.Ctx = Depends(ctx_dep)):
    return _policy_detail(ctx, _get(ctx, AgencyPolicy, policy_id, AgencyPolicy.agent_user_id, "Policy"))


class ReviewTaskIn(BaseModel):
    title: Optional[str] = None
    kind: str = "annual_review"     # annual_review | beneficiary_review | service
    due_at: Optional[str] = None


@router.post("/policies/{policy_id}/review-task", status_code=201, dependencies=_WRITE)
def review_task(policy_id: str, body: ReviewTaskIn = Body(default=ReviewTaskIn()), ctx: Q.Ctx = Depends(ctx_dep)):
    p = _get(ctx, AgencyPolicy, policy_id, AgencyPolicy.agent_user_id, "Policy")
    if body.kind not in ("annual_review", "beneficiary_review", "service"):
        raise HTTPException(422, "kind must be annual_review, beneficiary_review or service")
    due = _parse_dt(body.due_at, "due_at") or (
        datetime.combine(p.annual_review_date, datetime.min.time()) if p.annual_review_date else None)
    t = AgencyTask(organization_id=ctx.org_id, kind=body.kind,
                   title=body.title or {"annual_review": "Annual policy review",
                                        "beneficiary_review": "Beneficiary review",
                                        "service": "Policy service"}[body.kind],
                   due_at=due, lead_id=p.lead_id, policy_id=p.id, assigned_user_id=p.agent_user_id,
                   created_by=ctx.user.id, is_demo=bool(p.is_demo))
    ctx.db.add(t)
    ctx.db.flush()
    _audit(ctx, "agency.policy.review_task", "agency_policy", p.id, {"task_id": t.id})
    ctx.db.commit()
    return _policy_detail(ctx, p)


# ── recruits ────────────────────────────────────────────────────────────────

@router.get("/recruits")
def list_recruits(stage: Optional[str] = None, near_activation: Optional[bool] = None,
                  page: int = 1, per_page: int = Query(50, le=200), ctx: Q.Ctx = Depends(ctx_dep)):
    return paginate(Q.list_recruits(ctx, stage=stage, near_activation=near_activation), page, per_page)


class RecruitIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    email: Optional[str] = None
    phone: Optional[str] = None
    jurisdiction: Optional[str] = None
    stage: Optional[str] = None
    recruiter_user_id: Optional[str] = None
    exam_status: Optional[str] = None
    training_progress_pct: Optional[int] = Field(None, ge=0, le=100)
    notes: Optional[str] = None


def _recruit_detail(ctx, r):
    row = next(x for x in Q.list_recruits(ctx, recruit_id=r.id) if x["id"] == r.id)
    row["notes"] = r.notes
    row["stages"] = ctx.cfg["recruit_stages"]
    row["milestones"] = [{"id": m.id, "label": m.label, "status": m.status, "due": iso(m.due),
                          "completed_at": iso(m.completed_at)} for m in ctx.db.query(AgencyRecruitMilestone)
                         .filter(AgencyRecruitMilestone.organization_id == ctx.org_id,
                                 AgencyRecruitMilestone.recruit_id == r.id).order_by(AgencyRecruitMilestone.created_at)]
    evs = ctx.db.query(AgencyRecruitStageEvent).filter(AgencyRecruitStageEvent.organization_id == ctx.org_id,
                                                       AgencyRecruitStageEvent.recruit_id == r.id
                                                       ).order_by(AgencyRecruitStageEvent.at).all()
    row["stage_history"] = [{"from": e.from_stage, "to": e.to_stage, "at": iso(e.at), "by": e.by_user_id}
                            for e in evs]
    row["tasks"] = [Q.task_row(t) for t in ctx.db.query(AgencyTask).filter(
        AgencyTask.organization_id == ctx.org_id, AgencyTask.recruit_id == r.id)]
    row["licensing_note"] = "Licensing and exam status are as entered; no government licensing integration."
    return row


@router.post("/recruits", status_code=201, dependencies=_WRITE)
def create_recruit(body: RecruitIn, ctx: Q.Ctx = Depends(ctx_dep)):
    stages = ctx.cfg["recruit_stages"]
    stage = body.stage or stages[0]
    if stage not in stages:
        raise HTTPException(422, "stage must be one of %s" % ", ".join(stages))
    recruiter = _org_user(ctx, body.recruiter_user_id).id if body.recruiter_user_id else ctx.user.id
    if not ctx.manager and recruiter != ctx.user.id:
        raise HTTPException(403, "Advisors can only create recruits they own")
    t = now()
    r = AgencyRecruit(organization_id=ctx.org_id, name=body.name, email=body.email, phone=body.phone,
                      jurisdiction=body.jurisdiction, stage=stage, stage_changed_at=t,
                      recruiter_user_id=recruiter, exam_status=body.exam_status,
                      training_progress_pct=body.training_progress_pct, notes=body.notes, created_at=t)
    ctx.db.add(r)
    ctx.db.flush()
    ctx.db.add(AgencyRecruitStageEvent(organization_id=ctx.org_id, recruit_id=r.id, from_stage=None,
                                       to_stage=stage, at=t, by_user_id=ctx.user.id))
    _audit(ctx, "agency.recruit.created", "agency_recruit", r.id)
    ctx.db.commit()
    return _recruit_detail(ctx, r)


@router.get("/recruits/{rid}")
def get_recruit(rid: str, ctx: Q.Ctx = Depends(ctx_dep)):
    return _recruit_detail(ctx, _get(ctx, AgencyRecruit, rid, AgencyRecruit.recruiter_user_id, "Recruit"))


class RecruitPatch(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    jurisdiction: Optional[str] = None
    exam_status: Optional[str] = None
    training_progress_pct: Optional[int] = Field(None, ge=0, le=100)
    notes: Optional[str] = None


@router.patch("/recruits/{rid}", dependencies=_WRITE)
def patch_recruit(rid: str, body: RecruitPatch, ctx: Q.Ctx = Depends(ctx_dep)):
    r = _get(ctx, AgencyRecruit, rid, AgencyRecruit.recruiter_user_id, "Recruit")
    data = body.dict(exclude_unset=True)
    for k, v in data.items():
        setattr(r, k, v)
    _audit(ctx, "agency.recruit.updated", "agency_recruit", r.id, {"fields": sorted(data)})
    ctx.db.commit()
    return _recruit_detail(ctx, r)


class StageIn(BaseModel):
    to: str


@router.post("/recruits/{rid}/stage", dependencies=_WRITE)
def recruit_stage(rid: str, body: StageIn, ctx: Q.Ctx = Depends(ctx_dep)):
    r = _get(ctx, AgencyRecruit, rid, AgencyRecruit.recruiter_user_id, "Recruit")
    stages = ctx.cfg["recruit_stages"]
    if body.to not in stages:
        raise HTTPException(422, "stage must be one of %s" % ", ".join(stages))
    if body.to == r.stage:
        raise HTTPException(409, "Recruit is already in %s" % r.stage)
    t = now()
    ctx.db.add(AgencyRecruitStageEvent(organization_id=ctx.org_id, recruit_id=r.id, from_stage=r.stage,
                                       to_stage=body.to, at=t, by_user_id=ctx.user.id))
    frm, r.stage, r.stage_changed_at = r.stage, body.to, t
    _audit(ctx, "agency.recruit.stage", "agency_recruit", r.id, {"from": frm, "to": body.to})
    ctx.db.commit()
    return _recruit_detail(ctx, r)


class MilestoneIn(BaseModel):
    label: str = Field(..., min_length=1, max_length=300)
    status: str = "pending"
    due: Optional[str] = None


@router.post("/recruits/{rid}/milestones", status_code=201, dependencies=_WRITE)
def recruit_milestone(rid: str, body: MilestoneIn, ctx: Q.Ctx = Depends(ctx_dep)):
    r = _get(ctx, AgencyRecruit, rid, AgencyRecruit.recruiter_user_id, "Recruit")
    if body.status not in ("pending", "in_progress", "done"):
        raise HTTPException(422, "status must be pending, in_progress or done")
    m = AgencyRecruitMilestone(organization_id=ctx.org_id, recruit_id=r.id, label=body.label,
                               status=body.status, due=_parse_date(body.due, "due"),
                               completed_at=now() if body.status == "done" else None)
    ctx.db.add(m)
    ctx.db.flush()
    _audit(ctx, "agency.recruit.milestone", "agency_recruit", r.id, {"label": body.label, "status": body.status})
    ctx.db.commit()
    return _recruit_detail(ctx, r)


# ── intelligence ────────────────────────────────────────────────────────────

@router.get("/attention")
def attention(ctx: Q.Ctx = Depends(ctx_dep)):
    items = intel.attention(ctx)
    return {"items": items, "total": len(items)}


@router.get("/summary")
def summary(ctx: Q.Ctx = Depends(ctx_dep)):
    out = intel.summary(ctx)
    ctx.db.commit()
    return out


class AskIn(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)


@router.post("/ask")
def ask(body: AskIn, ctx: Q.Ctx = Depends(ctx_dep)):
    return intel.ask(ctx, body.question)
