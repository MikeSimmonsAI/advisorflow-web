"""MANAGED CLIENT ACQUISITION (Issue #452) - two surfaces.

/acquisition/*        the CLIENT's portal (Divine Shine): interested prospects,
                      appointments, outcomes, business profile, feedback.
                      Requires the `client_acquisition` feature AND that the
                      acting workspace lists it explicitly with a cleaning (or
                      neutral) identity. No discovery, scoring, evidence,
                      practice data or automation controls are exposed here.
/acquisition-ops/*    OPERATIONS: full pipeline with evidence, practice
                      cohort, pause/resume, workers, audit trail. God, or a
                      login the owner granted operator access for THAT client
                      (AcquisitionOperator) - never God Mode itself.
/god/acquisition/*    setup and operator grants. God only.

SENDS NOTHING and calls no provider or AI model (P1).
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db, require_god, require_not_observation, require_tenant_user
from app.models.models import Organization, User
from app.routers.audit_log_router import log_action
from app.services import lead_scope
from app.services.acquisition import common as C
from app.services.acquisition import engine as E
from app.services.acquisition import booking as B
from app.services.acquisition import outreach as O
from app.services.acquisition import outcomes as OC
from app.services.acquisition import calendar_setup as CAL
from app.limiter import limiter
from app.services.entitlements import require_feature
from app.services.platform_owner import require_tenant_context

router = APIRouter(prefix="/acquisition", tags=["acquisition"],
                   dependencies=[Depends(require_feature(C.FEATURE))])
god_router = APIRouter(prefix="/god/acquisition", tags=["acquisition"])
ops_router = APIRouter(prefix="/acquisition-ops", tags=["acquisition"])
# The prospect's walkthrough page: no login, the unguessable link is the authorization.
public_router = APIRouter(prefix="/acquisition-public", tags=["acquisition"])
_WRITE = [Depends(require_not_observation)]

from app.services.acquisition.admins import require_acq_master  # noqa: E402,F401  God or EvoSys Acquisition admin


def ctx_dep(request: Request, db: Session = Depends(get_db),
            user: User = Depends(require_tenant_context),
            _t: User = Depends(require_tenant_user)) -> C.Ctx:
    from app.services.platform_owner import is_platform_pseudo_org
    org_id = lead_scope.active_workspace_org_id(user, db, request)
    if not org_id or is_platform_pseudo_org(org_id):
        raise HTTPException(409, "No business is selected. Select a workspace first.")
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not C.acquisition_identity(org):
        raise HTTPException(403, "This workspace does not have the acquisition program.")
    return C.Ctx(db, user, org, lead_scope.is_manager_here(user, db, request), request)


def _visible_lead_ids(ctx: C.Ctx):
    """The owner of the business sees all of it; anyone else in the workspace
    sees only the leads lead scope already gives them."""
    if ctx.manager:
        return None
    from app.models.models import Lead
    q = lead_scope.authorized_lead_query(ctx.db, ctx.user, Lead.id, request=ctx.request)
    return [r[0] for r in q.filter(Lead.organization_id == ctx.org_id).all()]


def _audit(db, org_id, user, action, ttype, tid, details=None):
    try:
        log_action(db, org_id, user.id, action, ttype, tid, details=details)
    except Exception:  # noqa: BLE001 - an audit hiccup must not undo the change
        pass


# ═════════════════════════════ CLIENT PORTAL ═════════════════════════════
@router.get("/status")
def status(ctx: C.Ctx = Depends(ctx_dep)):
    p = E.get_profile(ctx.db, ctx.org_id)
    prog = E.progress(ctx.db, ctx.org_id)
    return {"organization_id": ctx.org_id, "pilot_day": prog["day"], "organization_name": ctx.org.name,
            "profile": E.profile_json(ctx.org, p, full=False),
            "running": bool(p and not p.paused and p.pilot_started_at),
            "pilot_started_at": C.iso(p.pilot_started_at) if p else None,
            "can_manage": ctx.manager, "segments": C.SEGMENTS,
            "me": {"id": ctx.user.id, "name": ctx.user.full_name or ctx.user.email}}


@router.get("/dashboard")
def dashboard(ctx: C.Ctx = Depends(ctx_dep)):
    """Booked appointments only. Nothing about research, outreach or interest
    that has not become an appointment (owner decision 2026-11-03)."""
    ids = _visible_lead_ids(ctx)
    items = E.list_client(ctx.db, ctx.org_id, ids)
    counts = E.customer_counts(ctx.db, ctx.org_id)
    now = C.iso(datetime.utcnow())

    def waiting_on_outcome(x):
        if x["stage"] == C.BOOKED:
            t = (x["appointment"] or {}).get("time")
            return bool(t) and t < now
        return x["stage"] in (C.HELD, C.QUOTED)
    by_segment: dict = {}
    for x in items:
        k = x.get("segment") or "other"
        by_segment[k] = by_segment.get(k, 0) + 1
    return {
        "counts": counts,
        "won_monthly_value": counts["won_monthly_value"],
        "upcoming": E.upcoming(ctx.db, ctx.org_id, ids)[:5],
        "needs_outcome": [x for x in items if waiting_on_outcome(x)][:10],
        # Kept EMPTY for the portal build still in production until #474 ships;
        # pre-booking interest is never sent to the customer.
        "new_interest": [],
        "by_segment": [{"segment": k, "label": C.SEGMENT_LABEL.get(k, "Other"), "count": v}
                       for k, v in sorted(by_segment.items(), key=lambda kv: -kv[1])],
    }


@router.get("/prospects")
def client_prospects(stage: Optional[str] = Query(None), ctx: C.Ctx = Depends(ctx_dep)):
    items = E.list_client(ctx.db, ctx.org_id, _visible_lead_ids(ctx))
    if stage:
        if stage not in C.CLIENT_STAGES:
            raise HTTPException(422, "Unknown stage")
        items = [x for x in items if x["stage"] == stage]
    return {"items": items, "stages": [s for s in C.STAGES if s["key"] in C.CLIENT_STAGES]}


@router.get("/prospects/{opp_id}")
def client_prospect(opp_id: str, ctx: C.Ctx = Depends(ctx_dep)):
    opp = E.load_opp(ctx.db, ctx.org_id, opp_id, client=True)
    items = [x for x in E.list_client(ctx.db, ctx.org_id, _visible_lead_ids(ctx)) if x["id"] == opp.id]
    if not items:
        raise HTTPException(404, "Appointment not found")
    return dict(items[0], history=OC.customer_history(ctx.db, opp), contract=OC.contract_json(opp),
                open_followups=[f for f in OC.list_followups(ctx.db, [ctx.org_id], "open")
                                if f["opportunity_id"] == opp.id])


@router.get("/appointments")
def client_appointments(ctx: C.Ctx = Depends(ctx_dep)):
    ids = _visible_lead_ids(ctx)
    items = [x for x in E.list_client(ctx.db, ctx.org_id, ids) if x["appointment"]]
    return {"upcoming": E.upcoming(ctx.db, ctx.org_id, ids),
            "all": sorted(items, key=lambda x: x["appointment"]["time"] or "", reverse=True)}


@router.post("/prospects/{opp_id}/outcome", dependencies=_WRITE)
def client_outcome(opp_id: str, payload: dict = Body(...), ctx: C.Ctx = Depends(ctx_dep)):
    if _visible_lead_ids(ctx) is not None:
        C.require_manager(ctx)
    opp = E.load_opp(ctx.db, ctx.org_id, opp_id, client=True)
    E.client_outcome(ctx.db, opp, ctx.user, payload)
    _audit(ctx.db, ctx.org_id, ctx.user, "acquisition_outcome", "acquisition_opportunity", opp.id,
           {"outcome": payload.get("outcome")})
    return client_prospect(opp_id, ctx)


@router.get("/report")
def client_report(period: str = Query("month"), start: Optional[str] = Query(None), end: Optional[str] = Query(None),
                  ctx: C.Ctx = Depends(ctx_dep)):
    a, b = OC.period_range(period, start, end)
    return dict(OC.client_report(ctx.db, ctx.org_id, a, b), period=period, period_label=OC.PERIODS.get(period),
                start=C.iso(a), end=C.iso(b))


@router.get("/calendar")
def client_calendar(ctx: C.Ctx = Depends(ctx_dep)):
    """Where EvoSys books this client's appointments - read-only for the client,
    plus whether the signed-in person is that calendar and has connected it."""
    cfg = CAL.config(ctx.db, ctx.org)
    mine = bool(cfg["booking_user"] and cfg["booking_user"]["id"] == ctx.user.id)
    cfg.pop("candidates", None)
    return dict(cfg, is_my_calendar=mine)


@router.get("/followups")
def client_followups(ctx: C.Ctx = Depends(ctx_dep)):
    return {"items": OC.list_followups(ctx.db, [ctx.org_id]),
            "reasons": [{"key": k, "label": l} for k, l in OC.FOLLOWUP_REASONS]}


@router.post("/followups", dependencies=_WRITE)
def client_request_followup(payload: dict = Body(...), ctx: C.Ctx = Depends(ctx_dep)):
    f, created = OC.request_followup(ctx.db, ctx.org_id, ctx.user, payload)
    _audit(ctx.db, ctx.org_id, ctx.user, "acquisition_followup_request", "acquisition_followup", f.id,
           {"reason": f.reason, "created": created})
    return {"id": f.id, "created": created, "status": f.status,
            "message": "EvoSys has your request." if created else "You already asked about this - we added your note."}


@router.get("/profile")
def get_profile(ctx: C.Ctx = Depends(ctx_dep)):
    return E.profile_json(ctx.org, E.get_profile(ctx.db, ctx.org_id), full=False)


@router.put("/profile", dependencies=_WRITE)
def put_profile(payload: dict = Body(...), ctx: C.Ctx = Depends(ctx_dep)):
    C.require_manager(ctx)
    p = E.update_profile(ctx.db, ctx.org_id, payload, owner=False)
    _audit(ctx.db, ctx.org_id, ctx.user, "acquisition_profile_update", "acquisition_profile", p.id,
           {"fields": sorted(payload.keys())})
    return E.profile_json(ctx.org, p, full=False)


@router.get("/feedback")
def get_feedback(ctx: C.Ctx = Depends(ctx_dep)):
    return {"items": E.feedback_list(ctx.db, ctx.org_id)}


@router.post("/feedback", dependencies=_WRITE)
def post_feedback(payload: dict = Body(...), ctx: C.Ctx = Depends(ctx_dep)):
    fb = E.add_feedback(ctx.db, ctx.org_id, ctx.user, payload)
    return {"id": fb.id, "ok": True}


# ═══════════════════════════ SETUP (GOD ONLY) ═══════════════════════════
def _org(db: Session, org_id: str) -> Organization:
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if org is None:
        raise HTTPException(404, "Organization not found")
    return org


def _client_org(db: Session, org_id: str) -> Organization:
    org = _org(db, org_id)
    if not C.acquisition_identity(org):
        raise HTTPException(409, "The acquisition program is not enabled for this organization")
    return org


def _clients(db: Session, org_ids=None):
    out = []
    for o in E.enabled_orgs(db):
        if org_ids is not None and o.id not in org_ids:
            continue
        p = E.get_profile(db, o.id)
        from app.services.acquisition import hub as _HB
        out.append({"organization_id": o.id, "name": o.name, "industry": o.industry,
                    "is_test": _HB.is_test_org(o.name),
                    "identity_ok": C.identity_ok(o), "configured": p is not None,
                    "paused": bool(p.paused) if p else True,
                    "pilot_started_at": C.iso(p.pilot_started_at) if p else None})
    return out


@god_router.get("/clients")
def god_clients(db: Session = Depends(get_db), god: User = Depends(require_acq_master)):
    return {"items": _clients(db), "limit": C.PILOT_ORG_LIMIT}


@god_router.post("/{org_id}/enable")
def god_enable(org_id: str, db: Session = Depends(get_db), god: User = Depends(require_acq_master)):
    org = _org(db, org_id)
    out = E.enable(db, org, god)
    _audit(db, org.id, god, "acquisition_enable", "organization", org.id, out)
    return out


@god_router.get("/{org_id}/operators")
def god_operators(org_id: str, db: Session = Depends(get_db), god: User = Depends(require_acq_master)):
    org = _client_org(db, org_id)
    return {"items": E.operators(db, org.id)}


@god_router.post("/{org_id}/operators")
def god_grant(org_id: str, payload: dict = Body(...), db: Session = Depends(get_db),
              god: User = Depends(require_acq_master)):
    org = _client_org(db, org_id)
    out = E.grant_operator(db, org, payload.get("email"), god, payload.get("permissions"))
    _audit(db, org.id, god, "acquisition_operator_grant", "user", out["user_id"])
    return out


@god_router.put("/{org_id}/operators/{user_id}")
def god_operator_permissions(org_id: str, user_id: str, payload: dict = Body(...), db: Session = Depends(get_db),
                             god: User = Depends(require_acq_master)):
    org = _client_org(db, org_id)
    out = E.set_operator_permissions(db, org, user_id, payload.get("permissions"), god)
    _audit(db, org.id, god, "acquisition_operator_permissions", "user", user_id, {"permissions": out["permissions"]})
    return out


@god_router.get("/admins")
def acq_admins(db: Session = Depends(get_db), god: User = Depends(require_god)):
    from app.services.acquisition import admins as AD
    return {"items": AD.admins(db)}


@god_router.post("/admins")
def acq_admin_grant(payload: dict = Body(...), db: Session = Depends(get_db), god: User = Depends(require_god)):
    """Global delegation: only God makes an EvoSys Acquisition admin."""
    from app.services.acquisition import admins as AD
    out = AD.grant(db, payload.get("email"), granted_by=god.id)
    _audit(db, None, god, "acquisition_admin_" + out["action"], "user", out.get("user_id") or "", {"email": out["email"]})
    return out


@god_router.delete("/admins/{user_id}")
def acq_admin_revoke(user_id: str, db: Session = Depends(get_db), god: User = Depends(require_god)):
    from app.services.acquisition import admins as AD
    out = AD.revoke(db, user_id)
    _audit(db, None, god, "acquisition_admin_revoked", "user", user_id)
    return out


# ── HARVEY AI WORKFORCE (#452) ───────────────────────────────────────────────
@god_router.get("/workforce")
def workforce_status(db: Session = Depends(get_db), me: User = Depends(require_acq_master)):
    from app.services.acquisition import workforce as WF
    out = WF.health(db)
    out["recent_jobs"] = WF.recent_jobs(db)
    return out


@god_router.post("/workforce/{action}")
def workforce_control(action: str, payload: dict = Body(default={}), db: Session = Depends(get_db),
                      me: User = Depends(require_acq_master)):
    """start / pause / resume / stop / emergency-stop / practice-test - each a real action."""
    from app.services.acquisition import workforce as WF
    action = action.replace("-", "_")
    if action == "practice_test":
        s = WF.state(db)
        if s.status == "emergency_stopped":
            raise HTTPException(409, "The workforce is emergency-stopped; Start it first")
        summary = WF.run_pass(db, practice_only=True, force=True)
        _audit(db, None, me, "acquisition_workforce_practice_test", "acquisition_workforce", "global", summary)
        return {"summary": summary, "health": WF.health(db)}
    if action == "limits":
        WF.set_limits(db, me, payload or {})
        return WF.health(db)
    if action == "ai_policy":
        from app.services.acquisition import ai_worker as AI
        AI.set_policy(db, me, payload or {})
        _audit(db, None, me, "acquisition_workforce_ai_policy", "acquisition_workforce", "global",
               {k: v for k, v in (payload or {}).items() if k != "reset_breaker"})
        return WF.health(db)
    WF.control(db, me, action, (payload or {}).get("reason"), (payload or {}).get("mode"))
    return WF.health(db)


@god_router.get("/workforce/research")
def workforce_research(bucket: Optional[str] = None, reason: Optional[str] = None,
                       db: Session = Depends(get_db), me: User = Depends(require_acq_master)):
    """The exact master-pool records behind each research count, with reasons."""
    from app.services.acquisition import workforce as WF
    out = WF.research_breakdown(db, with_rows=True)
    rows = out["rows"]
    if bucket:
        rows = [r for r in rows if r["bucket"] == bucket]
    if reason:
        rows = [r for r in rows if r["reason_code"] == reason]
    out["rows"] = rows
    return out


@god_router.post("/workforce/research/{prospect_id}/website")
def workforce_set_website(prospect_id: str, payload: dict = Body(...), db: Session = Depends(get_db),
                          me: User = Depends(require_acq_master)):
    from app.services.acquisition import workforce as WF
    out = WF.set_pool_website(db, me, prospect_id, payload.get("website") or "")
    _audit(db, None, me, "acquisition_pool_website_set", "lead_intel_prospect", prospect_id,
           {"website": out["website"], "confidence": out["confidence"]})
    return out


@god_router.put("/workforce/clients/{org_id}")
def workforce_client(org_id: str, payload: dict = Body(...), db: Session = Depends(get_db),
                     me: User = Depends(require_acq_master)):
    from app.services.acquisition import workforce as WF
    row = WF.set_client(db, me, org_id, bool(payload.get("enabled")), payload.get("daily_job_limit"))
    return {"organization_id": row.organization_id, "enabled": row.enabled, "daily_job_limit": row.daily_job_limit}


@god_router.get("/worker-permissions")
def god_worker_permissions(god: User = Depends(require_acq_master)):
    return {"permissions": [{"key": k, "label": l, "hint": h} for k, l, h in C.WORKER_PERMISSIONS],
            "presets": C.WORKER_PRESETS}


@god_router.delete("/{org_id}/operators/{user_id}")
def god_revoke(org_id: str, user_id: str, db: Session = Depends(get_db), god: User = Depends(require_acq_master)):
    org = _client_org(db, org_id)
    E.revoke_operator(db, org, user_id, god)
    _audit(db, org.id, god, "acquisition_operator_revoke", "user", user_id)
    return {"ok": True}


# ═══════════════════ MASTER ACQUISITION / OWNER DISTRIBUTION ═══════════════════
# Reuses Lead Intelligence master pool. These are NOT file-import endpoints:
# the canonical /intake importer (#470) owns upload, map, review and commit.
# Assigning a qualified master-owned prospect creates a tenant-scoped Lead and
# AcquisitionOpportunity using the EXISTING acquisition engine; sends nothing.
class MasterAssignmentIn(BaseModel):
    prospect_ids: list[str] = Field(..., min_length=1, max_length=20)
    destination_org_id: str
    segment: Optional[str] = None      # empty = each prospect's own segment


@god_router.get("/master")
def master_overview(db: Session = Depends(get_db), god: User = Depends(require_acq_master)):
    from app.models.lead_intel_models import LeadIntelProspect
    from app.services import qualification as Q
    base = db.query(LeadIntelProspect)
    return {
        "clients": _clients(db),
        "template": "cleaning_acquisition",
        "max_clients": C.PILOT_ORG_LIMIT,
        "prospects_total": base.count(),
        "unassigned_qualified": base.filter(
            LeadIntelProspect.routed_org_id.is_(None),
            LeadIntelProspect.stage == "qualified",
            LeadIntelProspect.bucket.in_((Q.READY, Q.REVIEW))).count(),
        "importer": "/intake",
        "prospect_source": "/god/lead-intelligence/prospects",
        "assignment_mode": "exclusive_owner_decision",
        "outreach_channels": ["email"],
        "note": "No other tenant's customer records enter the master prospect pool.",
    }


@god_router.post("/master/assign/preview")
def master_assignment_preview(body: MasterAssignmentIn, db: Session = Depends(get_db),
                              god: User = Depends(require_acq_master)):
    from app.models.lead_intel_models import LeadIntelProspect
    from app.services.acquisition import distribution as D
    org = _client_org(db, body.destination_org_id)
    rows = {p.id: p for p in db.query(LeadIntelProspect).filter(
        LeadIntelProspect.id.in_(body.prospect_ids)).all()}
    decisions = [
        D.assess(db, rows[pid], org, body.segment or "") if pid in rows else
        {"prospect_id": pid, "eligible": False, "refused_because": ["not_found"]}
        for pid in dict.fromkeys(body.prospect_ids)
    ]
    return {"destination_org_id": org.id, "decisions": decisions,
            "assigned": 0, "sent": 0, "preview_only": True}


@god_router.post("/master/assign")
def master_assignment(body: MasterAssignmentIn, db: Session = Depends(get_db),
                      god: User = Depends(require_acq_master)):
    from app.services.acquisition import distribution as D
    org = _client_org(db, body.destination_org_id)
    out = D.assign_many(db, body.prospect_ids, org, god, body.segment or "")
    _audit(db, org.id, god, "acquisition_master_assignment", "organization", org.id,
           {"assigned": out["assigned"], "refused": out["refused"]})
    return out


@god_router.get("/master/prospects")
def master_prospects(status: str = Query("available"), limit: int = Query(200, ge=1, le=500),
                     queue: str = Query(""), search: str = Query(""),
                     db: Session = Depends(get_db), god: User = Depends(require_acq_master)):
    from app.services.acquisition import distribution as D
    from app.services.acquisition import hub as HUB
    if status not in ("available", "assigned", "all"):
        raise HTTPException(422, "status is available, assigned or all")
    if queue and queue not in HUB.QUEUE_LABEL:
        raise HTTPException(422, "Unknown research queue")
    return D.master_rows(db, status, limit, queue, search)


@god_router.get("/hub")
def hub_overview(db: Session = Depends(get_db), god: User = Depends(require_acq_master)):
    from app.services.acquisition import hub as HUB
    return HUB.overview(db)


@god_router.post("/master/{prospect_id}/contact")
def master_contact(prospect_id: str, payload: dict = Body(...), db: Session = Depends(get_db),
                   god: User = Depends(require_acq_master)):
    from app.models.lead_intel_models import LeadIntelProspect
    from app.services.acquisition import hub as HUB
    p = db.query(LeadIntelProspect).filter(LeadIntelProspect.id == prospect_id).first()
    if p is None:
        raise HTTPException(404, "Prospect not found")
    out = HUB.record_contact(db, p, god, payload or {})
    _audit(db, p.routed_org_id or "org-god-platform", god, "acquisition_contact_research", "lead_intel_prospect",
           p.id, {"verified": out["readiness"]["email_status"] == "verified", "pushed": out["pushed_to_client"]})
    return out


def _base_url(request: Request) -> str:
    origin = request.headers.get("origin") or ""
    return origin if origin.startswith("https://") or origin.startswith("http://localhost") else ""


@god_router.post("/clients")
def provision_client(request: Request, payload: dict = Body(...), db: Session = Depends(get_db),
                     god: User = Depends(require_acq_master)):
    """Add ONE cleaning company (direct, or converting a Sales opportunity)."""
    from app.services.acquisition import onboarding as OB
    out = OB.onboard(db, god, payload or {}, _base_url(request))
    if out.get("organization_id") and out["status"] in ("created", "existing"):
        _audit(db, out["organization_id"], god, "acquisition_client_onboarded", "organization", out["organization_id"],
               {"status": out["status"], "source": out.get("source"),
                "invited": bool(out.get("invitation") and out["invitation"].get("status") == "invitation_pending")})
    return out


@god_router.post("/clients/batch")
def provision_clients_batch(request: Request, payload: dict = Body(...), db: Session = Depends(get_db),
                            god: User = Depends(require_acq_master)):
    from app.services.acquisition import onboarding as OB
    out = OB.batch(db, god, payload.get("items") or [], payload.get("defaults") or {}, _base_url(request))
    for r in out["results"]:
        if r.get("status") == "created":
            _audit(db, r["organization_id"], god, "acquisition_client_onboarded", "organization",
                   r["organization_id"], {"status": "created", "source": r.get("source"), "batch": True})
    return out


@god_router.get("/followups")
def god_followups(status: Optional[str] = Query("open"), db: Session = Depends(get_db),
                  user: User = Depends(require_acq_master)):
    return {"items": OC.list_followups(db, None, status or None)}


@god_router.get("/report")
def god_report(period: str = Query("month"), start: Optional[str] = Query(None), end: Optional[str] = Query(None),
               db: Session = Depends(get_db), user: User = Depends(require_acq_master)):
    a, b = OC.period_range(period, start, end)
    return dict(OC.all_clients_report(db, a, b), period=period, period_label=OC.PERIODS.get(period),
                start=C.iso(a), end=C.iso(b))


@god_router.get("/directory")
def client_directory(db: Session = Depends(get_db), god: User = Depends(require_acq_master)):
    from app.services.acquisition import onboarding as OB
    return OB.directory(db)


@god_router.get("/sales-candidates")
def sales_candidates(db: Session = Depends(get_db), god: User = Depends(require_acq_master)):
    from app.services.acquisition import onboarding as OB
    return OB.sales_candidates(db)


@god_router.get("/onboarding-options")
def onboarding_options(god: User = Depends(require_acq_master)):
    from app.services.acquisition import onboarding as OB
    return {"services": [{"key": k, "label": l} for k, l, _ in OB.SERVICES],
            "categories": [{"key": k, "label": l, "services": [{"key": sk, "label": sl, "segment": sg} for sk, sl, sg in svcs]}
                           for k, l, svcs in C.SERVICE_CATEGORIES],
            "segments": C.SEGMENTS,
            "offers": [{"key": k, "label": l} for k, l in C.OFFER_LABEL.items()]}


@god_router.post("/clients/{org_id}/invite")
def invite_client_admin(org_id: str, request: Request, payload: dict = Body(...), db: Session = Depends(get_db),
                        god: User = Depends(require_acq_master)):
    from app.services.acquisition import onboarding as OB
    org = _client_org(db, org_id)
    out = OB.invite(db, org, god, payload.get("email") or "", payload.get("name"), _base_url(request))
    db.commit()
    _audit(db, org.id, god, "acquisition_client_invited", "user", out.get("user_id"),
           {"status": out["status"], "resent": out.get("resent")})
    return out


@god_router.post("/master/{prospect_id}/release")
def master_release(prospect_id: str, payload: dict = Body(...), db: Session = Depends(get_db),
                   god: User = Depends(require_acq_master)):
    from app.services.acquisition import distribution as D
    out = D.release(db, prospect_id, god, payload.get("reason") or "")
    if out.get("released_from"):
        _audit(db, out["released_from"], god, "acquisition_master_release", "lead_intel_prospect", prospect_id,
               {"reason": (payload.get("reason") or "")[:200]})
    return out


# ═══════════════════════ OPERATIONS (GOD OR OPERATOR) ════════════════════
class Op:
    def __init__(self, db, user, org, is_god, perms=None):
        self.db, self.user, self.org, self.org_id, self.is_god = db, user, org, org.id, is_god
        self.perms = set(C.WORKER_PERMISSION_KEYS) if is_god else set(perms or ())

    def can(self, key: str) -> bool:
        return self.is_god or key in self.perms


def _is_god(user) -> bool:
    return getattr(user, "role", None) == "god_admin"



# ── #452 Phase 5: what each operations call needs ─────────────────────────────
# One table, checked for EVERY /acquisition-ops/{org}/... call by a non-owner.
# A call missing from this table is refused (fails closed).
import re as _re  # noqa: E402
OWNER = "__owner__"
_PERM_RULES = [
    ("POST", r"/prospects/[^/]+/confirm-won$", OWNER),
    ("PUT", r"/calendar$", OWNER),
    ("GET", r"/(overview|prospects|calendar|discovery/runs)$", "view_queue"),
    ("GET", r"/followups$", "followups"),
    ("POST", r"/followups/[^/]+$", "followups"),
    ("GET", r"/(report|feedback)$", "view_results"),
    ("POST", r"/prospects$", "research"),
    ("POST", r"/prospects/[^/]+/stage$", "__stage__"),
    ("POST", r"/prospects/[^/]+/evidence$", "research"),
    ("PUT", r"/prospects/[^/]+/location$", "research"),
    ("POST", r"/discovery/plan$", "research"),
    ("POST", r"/prospects/[^/]+/email/preview$", "draft_outreach"),
    ("POST", r"/prospects/[^/]+/email/send$", "send_outreach"),
    ("GET", r"/drafts$", "draft_outreach"),
    ("POST", r"/drafts/[^/]+/reject$", "draft_outreach"),
    ("POST", r"/prospects/[^/]+/(booking-link|booking-link/cancel|book|reschedule|cancel-appointment)$", "schedule"),
    ("GET", r"/prospects/[^/]+/slots$", "schedule"),
    ("PUT", r"/profile$", "client_setup"),
    ("POST", r"/(pause|resume)$", "client_setup"),
]


def _needed(method: str, path: str, org_id: str) -> Optional[str]:
    tail = path.split("/acquisition-ops/" + org_id, 1)[-1]
    for m, pat, perm in _PERM_RULES:
        if m == method and _re.search(pat, tail):
            return perm
    return None


def op_dep(org_id: str, request: Request, db: Session = Depends(get_db),
           user: User = Depends(get_current_user)) -> Op:
    from app.services.acquisition import admins as AD
    # Master = the global owner OR an EvoSys Acquisition admin (program grant).
    god = AD.is_master(db, user)
    if not god and not E.is_operator(db, user, org_id):
        # Same answer for "no such org" and "not yours": ids are not probed.
        raise HTTPException(403, "You don't have acquisition operator access for this client.")
    org = _client_org(db, org_id)
    if god:
        return Op(db, user, org, True)
    perms = E.operator_perms(db, user, org_id)
    need = _needed(request.method, request.url.path, org_id)
    if need is None or need == OWNER:
        raise HTTPException(403, "Only the EvoSys owner can do that.")
    if need == "__stage__":
        if not ({"research", "review_replies"} & perms):
            raise HTTPException(403, "Your access for this client doesn't include moving prospects.")
    elif need not in perms:
        label = dict((k, l) for k, l, _ in C.WORKER_PERMISSIONS).get(need, need)
        raise HTTPException(403, "Your access for this client doesn't include: %s." % label)
    if request.method != "GET":
        # Every worker action is on the audit trail.
        _audit(db, org_id, user, "acquisition_operator_action", "acquisition_ops", org_id,
               {"method": request.method, "path": request.url.path[-120:]})
    return Op(db, user, org, False, perms)


@ops_router.get("/clients")
def ops_clients(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # WHO IS SIGNED IN, stated by the server (#452 owner-access fix): the shell
    # shows this verbatim so an operator session can never pass for the owner's.
    from app.services.acquisition import admins as AD
    access = AD.access_label(db, user)
    me = {"email": user.email, "name": user.full_name or user.email, "access": access}
    if access in ("owner", "admin"):
        # `is_god` keeps its old meaning for the shell: MASTER workspace access.
        return {"items": _clients(db), "is_god": True, "master": True, "global_owner": access == "owner", "me": me}
    ids = E.operator_org_ids(db, user)
    if not ids:
        raise HTTPException(403, "You don't have acquisition operator access.")
    return {"items": _clients(db, set(ids)), "is_god": False, "master": False, "global_owner": False, "me": me}


@ops_router.get("/my-queue")
def ops_my_queue(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The worker's own queue across ONLY the clients they're authorized for,
    filtered by what they may do there. Never the master pool."""
    from app.services.acquisition import admins as AD
    god = AD.is_master(db, user)
    ids = [o.id for o in E.enabled_orgs(db)] if god else E.operator_org_ids(db, user)
    if not ids:
        raise HTTPException(403, "You don't have acquisition operator access.")
    from app.models.acquisition_models import AcquisitionOpportunity as AO
    research = (C.DISCOVERED, C.RESEARCHED, C.QUALIFIED, C.CONTACT_VERIFIED)
    out = []
    for org in db.query(Organization).filter(Organization.id.in_(ids)).all():
        perms = set(C.WORKER_PERMISSION_KEYS) if god else E.operator_perms(db, user, org.id)
        if "view_queue" not in perms:
            continue
        rows = E.list_owner(db, org.id)
        pick = lambda st: [{"id": x["id"], "business_name": x["business_name"], "segment_label": x["segment_label"],  # noqa: E731
                            "city": x["city"], "stage": x["stage"], "stage_label": x["stage_label"],
                            "is_practice": x["is_practice"]} for x in rows if x["stage"] in st]
        item = {"organization_id": org.id, "name": org.name, "permissions": sorted(perms),
                "to_research": pick(research) if "research" in perms else None,
                "outreach_ready": pick((C.OUTREACH_READY,)) if ({"draft_outreach", "send_outreach"} & perms) else None,
                "replies": pick((C.REPLIED,)) if "review_replies" in perms else None,
                "to_schedule": pick((C.INTERESTED,)) if "schedule" in perms else None,
                "followups": (OC.list_followups(db, [org.id], "open") if "followups" in perms else None)}
        out.append(item)
    tot = lambda k: sum(len(i[k]) for i in out if i[k] is not None)  # noqa: E731
    return {"is_god": god, "clients": out,
            "totals": {"to_research": tot("to_research"), "outreach_ready": tot("outreach_ready"),
                       "replies": tot("replies"), "to_schedule": tot("to_schedule"), "followups": tot("followups"),
                       "authorized_clients": len(out)}}


def _discovery_runs(db: Session, org_id: str):
    from app.models.acquisition_models import AcquisitionDiscoveryRun
    return db.query(AcquisitionDiscoveryRun).filter(AcquisitionDiscoveryRun.organization_id == org_id).all()


def _workers(db: Session, org_id: str):
    """The Harvey workforce's real state for THIS client (#452): every value
    comes from the durable job queue; nothing is a decorative health light."""
    from app.models.acquisition_models import AcquisitionWorkerJob as J, AcquisitionWorkforceClient as WC
    from app.services.acquisition import workforce as WF
    st = WF.state(db)
    row = db.query(WC).filter(WC.organization_id == org_id).first()
    on = bool(row and row.enabled)
    bl = WF.blockers(db)
    out = []
    for w in WF.WORKERS:
        q = db.query(J).filter(J.worker == w["key"], J.organization_id == org_id)
        last = q.filter(J.status == "done").order_by(J.finished_at.desc()).first()
        if st.status != "running":
            state = "stopped"
        elif not on:
            state = "client_off"
        else:
            state = "running"
        out.append({"key": w["key"], "label": w["label"], "state": state,
                    "runs": q.filter(J.status == "done").count(), "errors": q.filter(J.status == "dead").count(),
                    "blocked": q.filter(J.status == "blocked").count(), "queued": q.filter(J.status == "queued").count(),
                    "last_run": C.iso(last.finished_at) if last else None, "blockers": bl.get(w["key"], [])[:3],
                    "workforce": st.status, "mode": st.mode})
    return out


def _spend(db: Session, org_id: str):
    runs = _discovery_runs(db, org_id)
    calls = sum((r.search_requests or 0) + (r.detail_requests or 0) for r in runs)
    known = [r.cost_usd for r in runs if r.cost_usd is not None]
    return {"provider_calls": calls, "ai_calls": 0, "usd": round(sum(known), 4),
            "unpriced_runs": sum(1 for r in runs if r.cost_usd is None and ((r.search_requests or 0) + (r.detail_requests or 0))),
            "note": "Metered Google Places requests from discovery runs. No AI calls are made by this program yet."}


@ops_router.get("/{org_id}/overview")
def ops_overview(op: Op = Depends(op_dep)):
    db, org = op.db, op.org
    p = E.get_profile(db, org.id)
    from app.services.cadence_service import org_sending_enabled
    return {
        "organization_id": org.id, "organization_name": org.name, "is_god": op.is_god, "master": op.is_god,
        "service_category": org.industry, "service_category_label": C.SERVICE_CATEGORY_LABEL.get(org.industry or "", org.industry),
        "me": {"id": op.user.id, "name": op.user.full_name or op.user.email},
        "profile": E.profile_json(org, p, full=True),
        "funnel": E.funnel(db, org.id, include_practice=True),
        "client_funnel": E.funnel(db, org.id, include_practice=False),
        "progress": E.progress(db, org.id),
        "targets": {"appointments_low": 3, "appointments_high": 5, "pilot_days": 30},
        "stages": C.STAGES, "segments": C.SEGMENTS, "evidence_kinds": list(C.EVIDENCE_KINDS),
        "workers": _workers(db, org.id),
        "sending": {"workspace_texts_on": org_sending_enabled(db, org.id),
                    "outbound_email_source_registered": True, "email_enabled": O.enabled(),
                    "from": O.SEND_FROM, "daily_cap": O.daily_cap(), "sent_last_24h": O.sent_today(db, org.id),
                    "test_recipients": O.test_recipients(),
                    "note": ("Email only, one prospect at a time, sent by a person pressing Send. "
                             "No texts or calls.")},
        "spend": _spend(db, org.id),
        "recent_events": E.events(db, org.id, 25),
        "operators": E.operators(db, org.id) if op.is_god else [],
    }


@ops_router.get("/{org_id}/prospects")
def ops_prospects(stage: Optional[str] = Query(None), practice: Optional[bool] = Query(None),
                  op: Op = Depends(op_dep)):
    if stage and stage not in C.STAGE_KEYS:
        raise HTTPException(422, "Unknown stage")
    return {"items": E.list_owner(op.db, op.org_id, stage, practice)}


@ops_router.get("/{org_id}/followups")
def ops_followups(status: Optional[str] = Query(None), op: Op = Depends(op_dep)):
    return {"items": OC.list_followups(op.db, [op.org_id], status)}


@ops_router.post("/{org_id}/followups/{fid}", dependencies=_WRITE)
def ops_update_followup(fid: str, payload: dict = Body(...), op: Op = Depends(op_dep)):
    f = OC.update_followup(op.db, op.org_id, fid, op.user, payload)
    _audit(op.db, op.org_id, op.user, "acquisition_followup_update", "acquisition_followup", f.id, {"status": f.status})
    return next(x for x in OC.list_followups(op.db, [op.org_id]) if x["id"] == f.id)


@ops_router.post("/{org_id}/prospects/{opp_id}/confirm-won", dependencies=_WRITE)
def ops_confirm_won(opp_id: str, op: Op = Depends(op_dep)):
    opp = OC.confirm_won(op.db, op.org_id, opp_id, op.user)
    _audit(op.db, op.org_id, op.user, "acquisition_won_confirmed", "acquisition_opportunity", opp.id)
    return OC.contract_json(opp)


@ops_router.get("/{org_id}/calendar")
def ops_calendar(op: Op = Depends(op_dep)):
    return CAL.config(op.db, op.org)


@ops_router.put("/{org_id}/calendar", dependencies=_WRITE)
def ops_calendar_update(payload: dict = Body(...), op: Op = Depends(op_dep)):
    if not op.is_god:
        raise HTTPException(403, "Only the EvoSys owner changes a client's booking setup")
    out = CAL.update(op.db, op.org, op.user, payload)
    _audit(op.db, op.org_id, op.user, "acquisition_calendar_setup", "acquisition_profile", op.org_id,
           {"fields": sorted(payload.keys())})
    return out


@ops_router.get("/{org_id}/prospects/{opp_id}/slots")
def ops_slots(opp_id: str, day: str = Query(...), op: Op = Depends(op_dep)):
    return CAL.available(op.db, op.org, E.load_opp(op.db, op.org_id, opp_id), op.user, day)


@ops_router.post("/{org_id}/prospects/{opp_id}/book", dependencies=_WRITE)
def ops_book(opp_id: str, payload: dict = Body(...), op: Op = Depends(op_dep)):
    out = CAL.book(op.db, op.org, E.load_opp(op.db, op.org_id, opp_id), op.user, payload)
    _audit(op.db, op.org_id, op.user, "acquisition_booked", "acquisition_opportunity", opp_id,
           {"time": out["booked_time"]})
    return out


@ops_router.post("/{org_id}/prospects/{opp_id}/reschedule", dependencies=_WRITE)
def ops_reschedule(opp_id: str, payload: dict = Body(...), op: Op = Depends(op_dep)):
    out = CAL.reschedule(op.db, op.org, E.load_opp(op.db, op.org_id, opp_id), op.user, payload)
    _audit(op.db, op.org_id, op.user, "acquisition_rescheduled", "acquisition_opportunity", opp_id,
           {"time": out["booked_time"]})
    return out


@ops_router.get("/{org_id}/report")
def ops_report(period: str = Query("month"), start: Optional[str] = Query(None), end: Optional[str] = Query(None),
               op: Op = Depends(op_dep)):
    a, b = OC.period_range(period, start, end)
    return dict(OC.client_report(op.db, op.org_id, a, b), period=period, start=C.iso(a), end=C.iso(b))


@ops_router.post("/{org_id}/prospects", dependencies=_WRITE)
def ops_add_prospect(payload: dict = Body(...), op: Op = Depends(op_dep)):
    opp = E.add_prospect(op.db, op.org, op.user, payload)
    _audit(op.db, op.org_id, op.user, "acquisition_prospect_add", "acquisition_opportunity", opp.id,
           {"practice": bool(opp.is_practice)})
    return {"id": opp.id}


@ops_router.post("/{org_id}/prospects/{opp_id}/stage", dependencies=_WRITE)
def ops_move(opp_id: str, payload: dict = Body(...), op: Op = Depends(op_dep)):
    need = "review_replies" if payload.get("stage") == C.INTERESTED else "research"
    if not op.can(need):
        raise HTTPException(403, "Your access for this client doesn't include that move.")
    opp = E.load_opp(op.db, op.org_id, opp_id)
    E.owner_move(op.db, opp, op.user, payload)
    return {"id": opp.id, "stage": opp.stage}


@ops_router.post("/{org_id}/prospects/{opp_id}/evidence", dependencies=_WRITE)
def ops_evidence(opp_id: str, payload: dict = Body(...), op: Op = Depends(op_dep)):
    opp = E.load_opp(op.db, op.org_id, opp_id)
    e = E.add_evidence(op.db, op.org_id, opp, op.user, payload)
    return {"id": e.id}


@ops_router.put("/{org_id}/prospects/{opp_id}/location", dependencies=_WRITE)
def ops_location(opp_id: str, payload: dict = Body(...), op: Op = Depends(op_dep)):
    opp = E.load_opp(op.db, op.org_id, opp_id)
    return E.set_location(op.db, op.org_id, opp, op.user, payload)


@ops_router.post("/{org_id}/prospects/{opp_id}/email/preview")
def ops_email_preview(opp_id: str, payload: dict = Body(default={}), op: Op = Depends(op_dep)):
    opp = E.load_opp(op.db, op.org_id, opp_id)
    return O.preview(op.db, op.org, opp, payload or {})


@ops_router.post("/{org_id}/prospects/{opp_id}/email/send", dependencies=_WRITE)
def ops_email_send(opp_id: str, payload: dict = Body(default={}), op: Op = Depends(op_dep)):
    opp = E.load_opp(op.db, op.org_id, opp_id)
    res = O.send(op.db, op.org, opp, op.user, payload or {})
    _audit(op.db, op.org_id, op.user, "acquisition_email_send", "acquisition_opportunity", opp.id,
           {"practice": bool(opp.is_practice), "pilot_started": res.get("pilot_started")})
    if (payload or {}).get("draft_id"):
        # The person who pressed Send approved the workforce's draft (approval history).
        from app.services.acquisition import workforce as WF
        try:
            res["draft"] = WF.decide_draft(op.db, op.org_id, payload["draft_id"], op.user, "sent")
        except HTTPException:
            pass
    return res


@ops_router.get("/{org_id}/drafts")
def ops_drafts(opp_id: Optional[str] = None, status: str = "pending", op: Op = Depends(op_dep)):
    """Messages the AI workforce prepared, waiting for a person."""
    from app.services.acquisition import workforce as WF
    return {"items": WF.drafts_for(op.db, op.org_id, opp_id, status)}


@ops_router.post("/{org_id}/drafts/{draft_id}/reject", dependencies=_WRITE)
def ops_draft_reject(draft_id: str, op: Op = Depends(op_dep)):
    from app.services.acquisition import workforce as WF
    return WF.decide_draft(op.db, op.org_id, draft_id, op.user, "rejected")


@ops_router.post("/{org_id}/prospects/{opp_id}/booking-link", dependencies=_WRITE)
def ops_booking_link(opp_id: str, op: Op = Depends(op_dep)):
    opp = E.load_opp(op.db, op.org_id, opp_id)
    return B.make_link(op.db, op.org, opp, op.user)


@ops_router.post("/{org_id}/prospects/{opp_id}/cancel-appointment", dependencies=_WRITE)
def ops_cancel_appointment(opp_id: str, payload: dict = Body(default={}), op: Op = Depends(op_dep)):
    opp = E.load_opp(op.db, op.org_id, opp_id)
    out = CAL.cancel_booked(op.db, op.org, opp, op.user, payload or {})
    _audit(op.db, op.org_id, op.user, "acquisition_appointment_cancelled", "acquisition_opportunity", opp.id,
           {"reason": (payload or {}).get("reason"), "practice": bool(opp.is_practice)})
    return out


@ops_router.post("/{org_id}/prospects/{opp_id}/booking-link/cancel", dependencies=_WRITE)
def ops_booking_link_cancel(opp_id: str, op: Op = Depends(op_dep)):
    opp = E.load_opp(op.db, op.org_id, opp_id)
    return {"cancelled": B.cancel_links(op.db, op.org, opp, op.user)}


@public_router.get("/walkthrough/{token}")
@limiter.limit("60/minute")
def public_walkthrough(request: Request, token: str, db: Session = Depends(get_db)):
    return B.page(db, token)


@public_router.get("/walkthrough/{token}/slots")
@limiter.limit("60/minute")
def public_walkthrough_slots(request: Request, token: str, date: str = Query(...), db: Session = Depends(get_db)):
    return B.slots(db, token, date)


@public_router.post("/walkthrough/{token}/book")
@limiter.limit("10/minute")
def public_walkthrough_book(request: Request, token: str, payload: dict = Body(...), db: Session = Depends(get_db)):
    return B.book(db, token, payload.get("slot") or "")


@ops_router.put("/{org_id}/profile", dependencies=_WRITE)
def ops_profile(payload: dict = Body(...), op: Op = Depends(op_dep)):
    p = E.update_profile(op.db, op.org_id, payload, owner=True)
    return E.profile_json(op.org, p, full=True)


@ops_router.post("/{org_id}/pause", dependencies=_WRITE)
def ops_pause(payload: dict = Body(default={}), op: Op = Depends(op_dep)):
    p = E.set_paused(op.db, op.org_id, op.user, True, payload.get("reason"))
    _audit(op.db, op.org_id, op.user, "acquisition_pause", "acquisition_profile", p.id, {"reason": p.paused_reason})
    return {"paused": True, "reason": p.paused_reason}


@ops_router.post("/{org_id}/resume", dependencies=_WRITE)
def ops_resume(op: Op = Depends(op_dep)):
    p = E.set_paused(op.db, op.org_id, op.user, False, None)
    _audit(op.db, op.org_id, op.user, "acquisition_resume", "acquisition_profile", p.id)
    return {"paused": False, "note": "Unpaused. No worker exists yet, so nothing runs and nothing is sent."}


@ops_router.get("/{org_id}/feedback")
def ops_feedback(op: Op = Depends(op_dep)):
    return {"items": E.feedback_list(op.db, op.org_id, 200)}


# ═══════════════════════ P2 DISCOVERY (metered, gated) ═══════════════════
@ops_router.post("/{org_id}/discovery/plan")
def ops_discovery_plan(payload: dict = Body(...), op: Op = Depends(op_dep)):
    """What a run would search, cost and be refused for. Makes no calls."""
    from app.services.acquisition import discovery as D
    return D.plan(op.db, op.org, payload.get("segment"), payload.get("city"),
                  payload.get("max_results", D.MAX_RESULTS_PER_RUN), bool(payload.get("is_practice", True)))


@ops_router.get("/{org_id}/discovery/runs")
def ops_discovery_runs(op: Op = Depends(op_dep)):
    from app.services.acquisition import discovery as D
    return {"items": D.runs(op.db, op.org_id), "segments": [s for s in C.SEGMENTS],
            "limits": {"max_results_per_run": D.MAX_RESULTS_PER_RUN, "max_runs_per_day": D.MAX_RUNS_PER_DAY}}


@god_router.post("/{org_id}/discovery/run")
async def god_discovery_run(org_id: str, payload: dict = Body(...), db: Session = Depends(get_db),
                            god: User = Depends(require_god)):
    """Spends provider money, so platform owner only. Contacts nobody."""
    from app.services.acquisition import discovery as D
    org = _client_org(db, org_id)
    out = await D.run(db, org, god, payload.get("segment"), payload.get("city"),
                      payload.get("max_results", D.MAX_RESULTS_PER_RUN), bool(payload.get("is_practice", True)))
    _audit(db, org.id, god, "acquisition_discovery_run", "acquisition_discovery_run", out["id"],
           {"added": out["added"], "cost_usd": out["cost_usd"], "practice": out["is_practice"]})
    return out
