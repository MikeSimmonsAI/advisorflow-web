"""MANAGED CLIENT ACQUISITION (Issue #452) - two surfaces.

/acquisition/*        the CLIENT's portal (Divine Shine): interested prospects,
                      appointments, outcomes, business profile, feedback.
                      Requires the `client_acquisition` feature AND that the
                      acting workspace lists it explicitly with a cleaning (or
                      neutral) identity. No discovery, scoring, evidence,
                      practice data or automation controls are exposed here.
/god/acquisition/*    the PLATFORM OWNER's operations: setup, full pipeline
                      with evidence, practice cohort, pause/resume, workers,
                      audit trail. God only.

SENDS NOTHING and calls no provider or AI model (P1).
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.deps import get_db, require_god, require_not_observation, require_tenant_user
from app.models.models import Organization, User
from app.routers.audit_log_router import log_action
from app.services import lead_scope
from app.services.acquisition import common as C
from app.services.acquisition import engine as E
from app.services.entitlements import require_feature
from app.services.platform_owner import require_tenant_context

router = APIRouter(prefix="/acquisition", tags=["acquisition"],
                   dependencies=[Depends(require_feature(C.FEATURE))])
god_router = APIRouter(prefix="/god/acquisition", tags=["acquisition"])
_WRITE = [Depends(require_not_observation)]


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
    return {"organization_id": ctx.org_id, "organization_name": ctx.org.name,
            "profile": E.profile_json(ctx.org, p, full=False),
            "running": bool(p and not p.paused and p.pilot_started_at),
            "pilot_started_at": C.iso(p.pilot_started_at) if p else None,
            "can_manage": ctx.manager, "segments": C.SEGMENTS,
            "me": {"id": ctx.user.id, "name": ctx.user.full_name or ctx.user.email}}


@router.get("/dashboard")
def dashboard(ctx: C.Ctx = Depends(ctx_dep)):
    ids = _visible_lead_ids(ctx)
    items = E.list_client(ctx.db, ctx.org_id, ids)
    f = E.funnel(ctx.db, ctx.org_id, include_practice=False)
    now = C.iso(datetime.utcnow())

    def waiting_on_outcome(x):
        # A booked appointment needs an outcome once its time has passed;
        # held and quoted prospects always wait on the client's next step.
        if x["stage"] == C.BOOKED:
            t = (x["appointment"] or {}).get("time")
            return bool(t) and t < now
        return x["stage"] in (C.HELD, C.QUOTED)
    return {
        "counts": {k: f[k] for k in ("interested", "booked", "held", "no_show", "quoted", "won", "lost")},
        "won_monthly_value": f["won_monthly_value"],
        "upcoming": E.upcoming(ctx.db, ctx.org_id, ids)[:5],
        "needs_outcome": [x for x in items if waiting_on_outcome(x)][:10],
        "new_interest": [x for x in items if x["stage"] == C.INTERESTED][:10],
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
        raise HTTPException(404, "Prospect not found")
    return items[0]


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


# ════════════════════════════ OWNER OPERATIONS ═══════════════════════════
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


@god_router.get("/clients")
def god_clients(db: Session = Depends(get_db), god: User = Depends(require_god)):
    out = []
    for o in E.enabled_orgs(db):
        p = E.get_profile(db, o.id)
        out.append({"organization_id": o.id, "name": o.name, "industry": o.industry,
                    "identity_ok": C.identity_ok(o), "configured": p is not None,
                    "paused": bool(p.paused) if p else True,
                    "pilot_started_at": C.iso(p.pilot_started_at) if p else None})
    return {"items": out, "limit": C.PILOT_ORG_LIMIT}


@god_router.post("/{org_id}/enable")
def god_enable(org_id: str, db: Session = Depends(get_db), god: User = Depends(require_god)):
    org = _org(db, org_id)
    out = E.enable(db, org, god)
    _audit(db, org.id, god, "acquisition_enable", "organization", org.id, out)
    return out


@god_router.get("/{org_id}/overview")
def god_overview(org_id: str, db: Session = Depends(get_db), god: User = Depends(require_god)):
    org = _client_org(db, org_id)
    p = E.get_profile(db, org.id)
    from app.services.cadence_service import org_sending_enabled
    return {
        "organization_id": org.id, "organization_name": org.name,
        "profile": E.profile_json(org, p, full=True),
        "funnel": E.funnel(db, org.id, include_practice=True),
        "client_funnel": E.funnel(db, org.id, include_practice=False),
        "stages": C.STAGES, "segments": C.SEGMENTS, "evidence_kinds": list(C.EVIDENCE_KINDS),
        "workers": [dict(w, state="not_built", runs=0, errors=0) for w in C.WORKERS],
        "sending": {"workspace_texts_on": org_sending_enabled(db, org.id),
                    "outbound_email_source_registered": False,
                    "note": "No outbound path exists for this program yet (P3). Nothing can be sent."},
        "spend": {"provider_calls": 0, "ai_calls": 0, "usd": 0.0,
                  "note": "No provider or AI calls are made by this program yet."},
        "recent_events": E.events(db, org.id, 25),
        "team": [{"id": u.id, "name": u.full_name or u.email, "role": u.role}
                 for u in db.query(User).filter(User.organization_id == org.id, User.is_active == True).all()],  # noqa: E712
    }


@god_router.get("/{org_id}/prospects")
def god_prospects(org_id: str, stage: Optional[str] = Query(None), practice: Optional[bool] = Query(None),
                  db: Session = Depends(get_db), god: User = Depends(require_god)):
    org = _client_org(db, org_id)
    if stage and stage not in C.STAGE_KEYS:
        raise HTTPException(422, "Unknown stage")
    return {"items": E.list_owner(db, org.id, stage, practice)}


@god_router.post("/{org_id}/prospects")
def god_add_prospect(org_id: str, payload: dict = Body(...), db: Session = Depends(get_db),
                     god: User = Depends(require_god)):
    org = _client_org(db, org_id)
    opp = E.add_prospect(db, org, god, payload)
    _audit(db, org.id, god, "acquisition_prospect_add", "acquisition_opportunity", opp.id,
           {"practice": bool(opp.is_practice)})
    return {"id": opp.id}


@god_router.post("/{org_id}/prospects/{opp_id}/stage")
def god_move(org_id: str, opp_id: str, payload: dict = Body(...), db: Session = Depends(get_db),
             god: User = Depends(require_god)):
    org = _client_org(db, org_id)
    opp = E.load_opp(db, org.id, opp_id)
    E.owner_move(db, opp, god, payload)
    return {"id": opp.id, "stage": opp.stage}


@god_router.post("/{org_id}/prospects/{opp_id}/evidence")
def god_evidence(org_id: str, opp_id: str, payload: dict = Body(...), db: Session = Depends(get_db),
                 god: User = Depends(require_god)):
    org = _client_org(db, org_id)
    opp = E.load_opp(db, org.id, opp_id)
    e = E.add_evidence(db, org.id, opp, god, payload)
    return {"id": e.id}


@god_router.put("/{org_id}/profile")
def god_profile(org_id: str, payload: dict = Body(...), db: Session = Depends(get_db),
                god: User = Depends(require_god)):
    org = _client_org(db, org_id)
    p = E.update_profile(db, org.id, payload, owner=True)
    return E.profile_json(org, p, full=True)


@god_router.post("/{org_id}/pause")
def god_pause(org_id: str, payload: dict = Body(default={}), db: Session = Depends(get_db),
              god: User = Depends(require_god)):
    org = _client_org(db, org_id)
    p = E.set_paused(db, org.id, god, True, payload.get("reason"))
    _audit(db, org.id, god, "acquisition_pause", "acquisition_profile", p.id, {"reason": p.paused_reason})
    return {"paused": True, "reason": p.paused_reason}


@god_router.post("/{org_id}/resume")
def god_resume(org_id: str, db: Session = Depends(get_db), god: User = Depends(require_god)):
    org = _client_org(db, org_id)
    p = E.set_paused(db, org.id, god, False, None)
    _audit(db, org.id, god, "acquisition_resume", "acquisition_profile", p.id)
    return {"paused": False,
            "note": "Unpaused. No worker exists yet, so nothing runs and nothing is sent."}


@god_router.get("/{org_id}/feedback")
def god_feedback(org_id: str, db: Session = Depends(get_db), god: User = Depends(require_god)):
    org = _client_org(db, org_id)
    return {"items": E.feedback_list(db, org.id, 200)}
