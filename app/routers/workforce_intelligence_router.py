"""AI WORKFORCE COMMAND - the management surface, and only the management surface.

THREE ROUTERS, THREE AUTHORITIES, ONE SET OF ANSWERS.

    `router` (/ai-workforce-intelligence) is the CUSTOMER surface. Every route
    answers for the caller's OWN workspace and NONE OF THEM ACCEPTS AN
    ORGANIZATION ID, so the tenant boundary is a property of the signatures
    rather than of a filter each route has to remember - the same property T7's
    router relies on.

    `brand_router` (/executive/ai-workforce) is the WHITE-LABEL surface. It
    uses `require_brand_executive`, which already proves the caller holds a
    brand_executive membership for exactly one platform, and it is bounded to
    that platform's customers. It is not a root: there is no route here that
    reaches another brand, and no parameter that could.

    `god_router` (/god/ai-workforce-intelligence) EXTENDS GOD MODE. It grants
    nobody new authority - `require_god` is the same dependency every other
    platform-control route uses - and it carries the two things that are
    genuinely platform-level: a named customer's view, asked for by somebody
    above them, and T9's own health.

WHAT IS DELIBERATELY NOT HERE.

    No route that makes an AI employee do anything. Outbound work is initiated
    by the workforce engine and the follow-up worker, both of which run the
    whole gate chain. A management route that triggered a send would bypass
    the objective, the cadence and the attempt ceiling while looking like a
    feature.

    No route that changes authority, entitlement, channels, voice, live
    sending, billing, readiness or activation. Those belong to T6, T7, T8 and
    T2, and `actions.perform` refuses any action not in T9's vocabulary -
    which contains none of them.

    No T10 screens. `/executive-summary` publishes the CONTRACT T10 will read.

EVERY MUTATION CARRIES `require_not_observation` as well as the tenant gate.
An executive observing a customer read-only must not be able to acknowledge
that customer's exceptions, decide their reviews or pause their employees.
"""

import logging
from typing import List, Optional

from fastapi import (APIRouter, Body, Depends, HTTPException, Query, Request,
                     Response)
from sqlalchemy.orm import Session

from app.deps import (get_db, require_brand_executive, require_god,
                      require_not_observation, require_tenant_or_observer,
                      require_tenant_user)
from app.models.models import User
from app.services.workforce_intelligence import actions as t9_actions
from app.services.workforce_intelligence import attention as t9_attention
from app.services.workforce_intelligence import coaching as t9_coaching
from app.services.workforce_intelligence import collect
from app.services.workforce_intelligence import command as t9_command
from app.services.workforce_intelligence import constants as C
from app.services.workforce_intelligence import cost as t9_cost
from app.services.workforce_intelligence import executive as t9_executive
from app.services.workforce_intelligence import findings as t9_findings
from app.services.workforce_intelligence import incidents as t9_incidents
from app.services.workforce_intelligence import metrics as t9_metrics
from app.services.workforce_intelligence import observability as t9_obs
from app.services.workforce_intelligence import quality as t9_quality
from app.services.workforce_intelligence import reconciliation as t9_rec
from app.services.workforce_intelligence import review as t9_review
from app.services.workforce_intelligence import scope as t9_scope
from app.services.workforce_intelligence import scorecards as t9_scorecards

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/ai-workforce-intelligence",
                   tags=["ai-workforce-intelligence"])
brand_router = APIRouter(prefix="/executive/ai-workforce",
                         tags=["ai-workforce-intelligence"])
god_router = APIRouter(prefix="/god/ai-workforce-intelligence", tags=["god"])


def _scope(request: Request, user: User, db: Session):
    """The caller's own workspace, or a refusal with the right status code.

    409 FOR "NO WORKSPACE SELECTED" AND 403 FOR "NOT YOURS". A brand
    salesperson who has not chosen a customer has made a mistake they can fix;
    a caller reaching for another tenant has not, and the two must not read
    the same.
    """
    try:
        return t9_scope.for_organization(db, user, request)
    except t9_scope.ScopeRefused as exc:
        status = 409 if exc.code == "no_workspace_selected" else 403
        raise HTTPException(status_code=status, detail=exc.message)


def _window(window: Optional[str]) -> str:
    return window if window in C.WINDOWS else C.DEFAULT_WINDOW


def _no_store(response: Response) -> None:
    """Management answers are never cached by a browser or a proxy.

    A stale overview served from a browser cache would defeat the whole
    freshness contract: the payload would carry a `computed_at` from this
    morning and the page would present it as current.
    """
    response.headers["Cache-Control"] = "no-store"


# ═══════════════════════════════════════════════════════════════════════════
# THE CUSTOMER SURFACE - read
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/overview")
def overview(request: Request, response: Response,
             window: Optional[str] = Query(None),
             force: bool = Query(False),
             db: Session = Depends(get_db),
             user: User = Depends(require_tenant_or_observer)):
    """The command centre. Needs Attention first, then everything else."""
    _no_store(response)
    return t9_command.cached_overview(db, _scope(request, user, db),
                                      window_key=_window(window), force=force)


@router.get("/attention")
def attention_queue(request: Request, response: Response,
                    kind: Optional[List[str]] = Query(None),
                    severity: Optional[List[str]] = Query(None),
                    employee_id: Optional[str] = Query(None),
                    state: Optional[List[str]] = Query(None),
                    limit: int = Query(200, ge=1, le=500),
                    db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_or_observer)):
    _no_store(response)
    return t9_attention.queue(db, _scope(request, user, db), kinds=kind,
                              severities=severity, employee_id=employee_id,
                              states=state, limit=limit)


@router.get("/exceptions")
def exceptions(request: Request, response: Response,
               severity: Optional[List[str]] = Query(None),
               limit: int = Query(100, ge=1, le=300),
               db: Session = Depends(get_db),
               user: User = Depends(require_tenant_or_observer)):
    _no_store(response)
    return t9_incidents.listing(db, _scope(request, user, db),
                                severities=severity, limit=limit)


@router.get("/exceptions/{item_id}")
def exception_detail(item_id: str, request: Request, response: Response,
                     db: Session = Depends(get_db),
                     user: User = Depends(require_tenant_or_observer)):
    _no_store(response)
    data = t9_incidents.detail(db, _scope(request, user, db), item_id)
    if data is None:
        # 404 rather than 403 for another tenant's exception, deliberately:
        # telling a caller that a record exists somewhere else is itself a
        # disclosure.
        raise HTTPException(status_code=404, detail="Exception not found.")
    return data


@router.get("/performance")
def performance(request: Request, response: Response,
                window: Optional[str] = Query(None),
                db: Session = Depends(get_db),
                user: User = Depends(require_tenant_or_observer)):
    _no_store(response)
    scope = _scope(request, user, db)
    window_key = _window(window)
    th = C.thresholds()
    per_employee = t9_metrics.employee_metrics(db, scope,
                                               window_key=window_key,
                                               thresholds=th)
    return {
        "window": window_key,
        "totals": t9_metrics.totals(per_employee,
                                    minimum=int(th["minimum_denominator"]),
                                    window_key=window_key),
        "by_employee": per_employee,
        "labels": t9_metrics.ledger_vocabulary(),
    }


@router.get("/scorecards")
def scorecards(request: Request, response: Response,
               window: Optional[str] = Query(None),
               db: Session = Depends(get_db),
               user: User = Depends(require_tenant_or_observer)):
    _no_store(response)
    return t9_scorecards.build(db, _scope(request, user, db),
                               window_key=_window(window))


@router.get("/scorecards/{employee_id}")
def scorecard(employee_id: str, request: Request, response: Response,
              window: Optional[str] = Query(None),
              db: Session = Depends(get_db),
              user: User = Depends(require_tenant_or_observer)):
    _no_store(response)
    data = t9_scorecards.for_employee(db, _scope(request, user, db),
                                      employee_id,
                                      window_key=_window(window))
    if data is None:
        raise HTTPException(status_code=404, detail="AI employee not found.")
    return data


@router.get("/quality")
def quality(request: Request, response: Response,
            window: Optional[str] = Query(None),
            db: Session = Depends(get_db),
            user: User = Depends(require_tenant_or_observer)):
    _no_store(response)
    return t9_quality.evaluate(db, _scope(request, user, db),
                               window_key=_window(window))


@router.get("/costs")
def costs(request: Request, response: Response,
          window: Optional[str] = Query(None),
          db: Session = Depends(get_db),
          user: User = Depends(require_tenant_or_observer)):
    _no_store(response)
    return t9_cost.report(db, _scope(request, user, db),
                          window_key=_window(window))


@router.get("/findings")
def findings(request: Request, response: Response,
             limit: int = Query(100, ge=1, le=300),
             db: Session = Depends(get_db),
             user: User = Depends(require_tenant_or_observer)):
    _no_store(response)
    return t9_findings.listing(db, _scope(request, user, db), limit=limit)


@router.get("/recommendations")
def recommendations(request: Request, response: Response,
                    window: Optional[str] = Query(None),
                    db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_or_observer)):
    _no_store(response)
    return t9_coaching.recommendations(db, _scope(request, user, db),
                                       window_key=_window(window))


@router.get("/reconciliation")
def reconciliation(request: Request, response: Response,
                   db: Session = Depends(get_db),
                   user: User = Depends(require_tenant_or_observer)):
    _no_store(response)
    return t9_rec.listing(db, _scope(request, user, db))


@router.get("/review")
def review_queue(request: Request, response: Response,
                 limit: int = Query(200, ge=1, le=500),
                 db: Session = Depends(get_db),
                 user: User = Depends(require_tenant_or_observer)):
    _no_store(response)
    return t9_review.queue(db, _scope(request, user, db), limit=limit)


@router.get("/review/{source_kind}/{source_id}")
def review_detail(source_kind: str, source_id: str, request: Request,
                  response: Response, db: Session = Depends(get_db),
                  user: User = Depends(require_tenant_or_observer)):
    _no_store(response)
    data = t9_review.detail(db, _scope(request, user, db), source_kind,
                            source_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Review item not found.")
    return data


@router.get("/handoffs")
def handoffs(request: Request, response: Response,
             db: Session = Depends(get_db),
             user: User = Depends(require_tenant_or_observer)):
    """Handoffs as a MANAGEMENT view: who is waiting, and for how long."""
    _no_store(response)
    scope = _scope(request, user, db)
    from app.services.workforce import handoff as wf_handoff
    rows = collect.handoffs(db, scope, statuses=("open", "accepted"))
    return {"handoffs": [wf_handoff.as_dict(r) for r in rows],
            "open": sum(1 for r in rows if r.status == "open"),
            "accepted": sum(1 for r in rows if r.status == "accepted")}


@router.get("/executive-summary")
def executive_summary(request: Request, response: Response,
                      window: Optional[str] = Query(None),
                      force: bool = Query(False),
                      db: Session = Depends(get_db),
                      user: User = Depends(require_tenant_or_observer)):
    """The T10 contract, for this workspace."""
    _no_store(response)
    return t9_executive.cached_snapshot(db, _scope(request, user, db),
                                        window_key=_window(window),
                                        force=force)


@router.get("/contract")
def contract(user: User = Depends(require_tenant_or_observer)):
    """What a consumer of the executive read may rely on."""
    return t9_executive.describe_contract()


@router.get("/thresholds")
def thresholds(user: User = Depends(require_tenant_or_observer)):
    """The detection settings, so a screen's filters are not a second copy."""
    return {"thresholds": C.thresholds(),
            "labels": dict(C.THRESHOLD_LABELS),
            "note": ("These are detection settings for this screen. They are "
                     "not commitments to anybody about response times.")}


@router.get("/self-check")
def self_check(request: Request, response: Response,
               db: Session = Depends(get_db),
               user: User = Depends(require_tenant_or_observer)):
    """Whether the intelligence on this page was actually computed."""
    _no_store(response)
    return t9_obs.health(db, _scope(request, user, db))


# ═══════════════════════════════════════════════════════════════════════════
# THE CUSTOMER SURFACE - management actions
# ═══════════════════════════════════════════════════════════════════════════
#
# EVERY ONE OF THESE GOES THROUGH `actions.perform`, which refuses anything
# outside T9's vocabulary and delegates everything inside it to the system
# that owns the state. There is no route below that writes to an employee, a
# deployment, a conversation or an entitlement directly.


def _perform(db, scope, *, action, user, target_id=None, reason=None,
             payload=None, origin_kind=None, origin_id=None):
    try:
        return t9_actions.perform(db, scope, action=action, user=user,
                                  target_id=target_id, reason=reason,
                                  payload=payload, origin_kind=origin_kind,
                                  origin_id=origin_id)
    except t9_actions.ActionRefused as exc:
        status = {
            C.R_RECORD_NOT_FOUND: 404,
            C.R_TENANT_MISMATCH: 404,
            C.R_NOT_AUTHORIZED: 403,
            C.R_OBSERVATION_MODE: 403,
            C.R_UNKNOWN_ACTION: 400,
            C.R_NOT_PERMITTED_BY_T9: 400,
        }.get(exc.code, 409)
        raise HTTPException(status_code=status,
                            detail={"code": exc.code,
                                    "message": exc.message})


@router.post("/refresh")
def refresh(request: Request, window: Optional[str] = Query(None),
            db: Session = Depends(get_db),
            user: User = Depends(require_tenant_user),
            _guard: User = Depends(require_not_observation)):
    """Recompute this workspace's intelligence now.

    A READ THAT WRITES, which is why it is a POST and why it carries the
    mutation guard: it stores read models and attention rows. It starts no AI
    work of any kind.
    """
    scope = _scope(request, user, db)
    result = t9_command.refresh(db, scope, window_key=_window(window))
    db.commit()
    return result


@router.post("/attention/{item_id}/acknowledge")
def acknowledge_item(item_id: str, request: Request,
                     payload: dict = Body(default={}),
                     db: Session = Depends(get_db),
                     user: User = Depends(require_tenant_user),
                     _guard: User = Depends(require_not_observation)):
    scope = _scope(request, user, db)
    out = _perform(db, scope, action=C.M_ACKNOWLEDGE_EXCEPTION, user=user,
                   target_id=item_id, reason=payload.get("note"))
    db.commit()
    return out


@router.post("/attention/{item_id}/resolve")
def resolve_item(item_id: str, request: Request,
                 payload: dict = Body(default={}),
                 db: Session = Depends(get_db),
                 user: User = Depends(require_tenant_user),
                 _guard: User = Depends(require_not_observation)):
    scope = _scope(request, user, db)
    out = _perform(db, scope, action=C.M_RESOLVE_ATTENTION, user=user,
                   target_id=item_id, reason=payload.get("note"))
    db.commit()
    return out


@router.post("/exceptions/{item_id}/escalate")
def escalate_exception(item_id: str, request: Request,
                       payload: dict = Body(default={}),
                       db: Session = Depends(get_db),
                       user: User = Depends(require_tenant_user),
                       _guard: User = Depends(require_not_observation)):
    """Hand this to AdvisorFlow Support. Opens a real ticket; keeps no second one."""
    scope = _scope(request, user, db)
    out = _perform(db, scope, action=C.M_ESCALATE, user=user,
                   target_id=item_id, reason=payload.get("note"))
    db.commit()
    return out


@router.post("/findings/{finding_id}/acknowledge")
def acknowledge_finding(finding_id: str, request: Request,
                        db: Session = Depends(get_db),
                        user: User = Depends(require_tenant_user),
                        _guard: User = Depends(require_not_observation)):
    scope = _scope(request, user, db)
    out = _perform(db, scope, action=C.M_ACKNOWLEDGE_FINDING, user=user,
                   target_id=finding_id)
    db.commit()
    return out


@router.post("/reconciliation/{finding_id}/acknowledge")
def acknowledge_contradiction(finding_id: str, request: Request,
                              db: Session = Depends(get_db),
                              user: User = Depends(require_tenant_user),
                              _guard: User = Depends(require_not_observation)):
    scope = _scope(request, user, db)
    out = _perform(db, scope, action=C.M_ACKNOWLEDGE_CONTRADICTION, user=user,
                   target_id=finding_id)
    db.commit()
    return out


@router.post("/review/{source_kind}/{source_id}/decide")
def decide_review(source_kind: str, source_id: str, request: Request,
                  payload: dict = Body(...),
                  db: Session = Depends(get_db),
                  user: User = Depends(require_tenant_user),
                  _guard: User = Depends(require_not_observation)):
    """Record a reviewer's decision. Recording is not acting."""
    scope = _scope(request, user, db)
    out = _perform(db, scope, action=C.M_RECORD_REVIEW, user=user,
                   target_id=source_id, reason=payload.get("note"),
                   payload={"source_kind": source_kind,
                            "source_id": source_id,
                            "decision": payload.get("decision")})
    db.commit()
    return out


@router.post("/employees/{deployment_id}/pause")
def pause_employee(deployment_id: str, request: Request,
                   payload: dict = Body(default={}),
                   db: Session = Depends(get_db),
                   user: User = Depends(require_tenant_user),
                   _guard: User = Depends(require_not_observation)):
    """Pause through T8's own lifecycle, with T8's own checks and audit."""
    scope = _scope(request, user, db)
    out = _perform(db, scope, action=C.M_PAUSE_EMPLOYEE, user=user,
                   target_id=deployment_id, reason=payload.get("reason"),
                   origin_kind=payload.get("origin_kind"),
                   origin_id=payload.get("origin_id"))
    db.commit()
    return out


@router.post("/employees/{deployment_id}/resume")
def resume_employee(deployment_id: str, request: Request,
                    payload: dict = Body(default={}),
                    db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_user),
                    _guard: User = Depends(require_not_observation)):
    scope = _scope(request, user, db)
    out = _perform(db, scope, action=C.M_RESUME_EMPLOYEE, user=user,
                   target_id=deployment_id, reason=payload.get("reason"))
    db.commit()
    return out


@router.post("/conversations/{thread_id}/takeover")
def takeover(thread_id: str, request: Request,
             payload: dict = Body(default={}),
             db: Session = Depends(get_db),
             user: User = Depends(require_tenant_user),
             _guard: User = Depends(require_not_observation)):
    scope = _scope(request, user, db)
    out = _perform(db, scope, action=C.M_REQUEST_TAKEOVER, user=user,
                   target_id=thread_id, reason=payload.get("note"),
                   payload=payload)
    db.commit()
    return out


@router.post("/conversations/{thread_id}/release")
def release(thread_id: str, request: Request,
            payload: dict = Body(default={}),
            db: Session = Depends(get_db),
            user: User = Depends(require_tenant_user),
            _guard: User = Depends(require_not_observation)):
    scope = _scope(request, user, db)
    out = _perform(db, scope, action=C.M_RELEASE_TAKEOVER, user=user,
                   target_id=thread_id, reason=payload.get("note"),
                   payload=payload)
    db.commit()
    return out


@router.post("/conversations/{thread_id}/cancel")
def cancel_objective(thread_id: str, request: Request,
                     payload: dict = Body(default={}),
                     db: Session = Depends(get_db),
                     user: User = Depends(require_tenant_user),
                     _guard: User = Depends(require_not_observation)):
    """Stop work on one objective. A cancelled objective is not a success."""
    scope = _scope(request, user, db)
    out = _perform(db, scope, action=C.M_CANCEL_OBJECTIVE, user=user,
                   target_id=thread_id, reason=payload.get("reason"))
    db.commit()
    return out


@router.post("/handoffs/{handoff_id}/accept")
def accept_handoff(handoff_id: str, request: Request,
                   db: Session = Depends(get_db),
                   user: User = Depends(require_tenant_user),
                   _guard: User = Depends(require_not_observation)):
    scope = _scope(request, user, db)
    out = _perform(db, scope, action=C.M_ACCEPT_HANDOFF, user=user,
                   target_id=handoff_id)
    db.commit()
    return out


@router.post("/handoffs/{handoff_id}/resolve")
def resolve_handoff(handoff_id: str, request: Request,
                    payload: dict = Body(default={}),
                    db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_user),
                    _guard: User = Depends(require_not_observation)):
    scope = _scope(request, user, db)
    out = _perform(db, scope, action=C.M_RESOLVE_HANDOFF, user=user,
                   target_id=handoff_id, reason=payload.get("note"))
    db.commit()
    return out


@router.get("/actions")
def action_history(request: Request, response: Response,
                   limit: int = Query(100, ge=1, le=300),
                   db: Session = Depends(get_db),
                   user: User = Depends(require_tenant_or_observer)):
    """What has been done from this surface, and which system performed it."""
    _no_store(response)
    return {"actions": t9_actions.history(db, _scope(request, user, db),
                                          limit=limit),
            "catalogue": {a: C.ACTION_AUTHORITY[a]
                          for a in C.MANAGEMENT_ACTIONS}}


# ═══════════════════════════════════════════════════════════════════════════
# THE WHITE-LABEL SURFACE
# ═══════════════════════════════════════════════════════════════════════════
#
# A BRAND EXECUTIVE SEES THEIR BRAND'S CUSTOMERS AND NOBODY ELSE'S. The
# authority is `require_brand_executive`, which resolves exactly one platform;
# the scope is bounded by a subquery over that platform's organizations. There
# is no parameter on any route below that could widen it, and a brand owner is
# not a root - nothing here reaches platform-wide state.


def _brand_scope(db: Session, bundle, request: Request):
    user, _membership, platform = bundle
    try:
        return t9_scope.for_brand(db, user, platform.id, request)
    except t9_scope.ScopeRefused as exc:
        raise HTTPException(status_code=403, detail=exc.message)


@brand_router.get("/summary")
def brand_summary(request: Request, response: Response,
                  window: Optional[str] = Query(None),
                  db: Session = Depends(get_db),
                  bundle=Depends(require_brand_executive)):
    """The executive read across this brand's customers."""
    _no_store(response)
    return t9_executive.cached_snapshot(db, _brand_scope(db, bundle, request),
                                        window_key=_window(window))


@brand_router.get("/attention")
def brand_attention(request: Request, response: Response,
                    severity: Optional[List[str]] = Query(None),
                    limit: int = Query(200, ge=1, le=500),
                    db: Session = Depends(get_db),
                    bundle=Depends(require_brand_executive)):
    _no_store(response)
    return t9_attention.queue(db, _brand_scope(db, bundle, request),
                              severities=severity, limit=limit)


@brand_router.get("/reconciliation")
def brand_reconciliation(request: Request, response: Response,
                         db: Session = Depends(get_db),
                         bundle=Depends(require_brand_executive)):
    _no_store(response)
    return t9_rec.listing(db, _brand_scope(db, bundle, request))


@brand_router.get("/contract")
def brand_contract(bundle=Depends(require_brand_executive)):
    return t9_executive.describe_contract()


# ═══════════════════════════════════════════════════════════════════════════
# GOD MODE
# ═══════════════════════════════════════════════════════════════════════════
#
# THE OWNER'S VIEW OF ONE CUSTOMER IS THE CUSTOMER'S OWN VIEW. Same function,
# same payload, same numbers - what differs is who may ask. Building a second
# shape for the owner is how a support call becomes "your screen says
# something different from mine".


def _god_org_scope(db: Session, user: User, organization_id: str,
                   request: Request):
    try:
        return t9_scope.for_organization_as_operator(db, user,
                                                     organization_id, request)
    except t9_scope.ScopeRefused as exc:
        raise HTTPException(status_code=403, detail=exc.message)


@god_router.get("/organizations/{organization_id}/overview")
def god_overview(organization_id: str, request: Request, response: Response,
                 window: Optional[str] = Query(None),
                 force: bool = Query(False),
                 db: Session = Depends(get_db),
                 user: User = Depends(require_god)):
    _no_store(response)
    scope = _god_org_scope(db, user, organization_id, request)
    return t9_command.cached_overview(db, scope, window_key=_window(window),
                                      force=force)


@god_router.get("/organizations/{organization_id}/executive-summary")
def god_executive(organization_id: str, request: Request, response: Response,
                  window: Optional[str] = Query(None),
                  db: Session = Depends(get_db),
                  user: User = Depends(require_god)):
    _no_store(response)
    scope = _god_org_scope(db, user, organization_id, request)
    return t9_executive.snapshot(db, scope, window_key=_window(window))


@god_router.post("/organizations/{organization_id}/refresh")
def god_refresh(organization_id: str, request: Request,
                window: Optional[str] = Query(None),
                db: Session = Depends(get_db),
                user: User = Depends(require_god)):
    scope = _god_org_scope(db, user, organization_id, request)
    result = t9_command.refresh(db, scope, window_key=_window(window))
    db.commit()
    return result


@god_router.get("/platform")
def god_platform(request: Request, response: Response,
                 window: Optional[str] = Query(None),
                 db: Session = Depends(get_db),
                 user: User = Depends(require_god)):
    """Platform-wide workforce state. The only scope with no tenant filter.

    `scope.for_platform` refuses anybody who is not a god_admin, which is the
    same answer `require_god` already gave - two independent checks for the
    one place in this package where the organization filter is absent.
    """
    _no_store(response)
    scope = t9_scope.for_platform(db, user)
    return t9_executive.snapshot(db, scope, window_key=_window(window))


@god_router.get("/platform/attention")
def god_platform_attention(response: Response,
                           severity: Optional[List[str]] = Query(None),
                           limit: int = Query(200, ge=1, le=500),
                           db: Session = Depends(get_db),
                           user: User = Depends(require_god)):
    _no_store(response)
    return t9_attention.queue(db, t9_scope.for_platform(db, user),
                              severities=severity, limit=limit)


@god_router.get("/platform/reconciliation")
def god_platform_reconciliation(response: Response,
                                db: Session = Depends(get_db),
                                user: User = Depends(require_god)):
    _no_store(response)
    return t9_rec.listing(db, t9_scope.for_platform(db, user), limit=500)


@god_router.get("/runs")
def god_runs(limit: int = Query(100, ge=1, le=500),
             db: Session = Depends(get_db),
             user: User = Depends(require_god)):
    """Whether T9's own passes ran, and what they could not do."""
    return {"runs": t9_obs.recent_runs(db, limit=limit),
            "failures_24h": t9_obs.failure_counts(db, hours=24),
            "note": ("A pass that failed leaves the last good numbers in "
                     "place and marks them stale. It does not render as a "
                     "quiet day.")}


@god_router.get("/vocabulary")
def god_vocabulary(user: User = Depends(require_god)):
    """Every name this layer can produce, so a test can assert on the list."""
    return {
        "attention_kinds": list(C.ATTENTION_KINDS),
        "attention_labels": dict(C.ATTENTION_LABELS),
        "finding_codes": list(C.FINDING_CODES),
        "quality_dimensions": list(C.QUALITY_DIMENSIONS),
        "reconciliation_checks": list(C.RECONCILIATION_CHECKS),
        "management_actions": {a: C.ACTION_AUTHORITY[a]
                               for a in C.MANAGEMENT_ACTIONS},
        "evidence_classes": list(C.EVIDENCE_CLASSES),
        "severities": list(C.SEVERITIES),
        "windows": dict(C.WINDOWS),
        "passes": list(C.PASSES),
        "view_keys": list(C.VIEW_KEYS),
    }


@god_router.post("/proofs/run")
def god_run_proof(which: str = Query("all"),
                  db: Session = Depends(get_db),
                  user: User = Depends(require_god)):
    """Run the synthetic management proofs. Demonstration tenants only.

    GOD ONLY, AND A POST, because it writes synthetic history. It enables
    nothing: no AI operation is switched on, no live adapter is reachable, and
    the report's `real_outreach` figure is a query over non-simulated
    communications rather than an assurance.
    """
    from app.services.workforce_intelligence import proof as t9_proof
    try:
        report = t9_proof.run(db, which=which)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.commit()
    return report


@god_router.get("/proofs")
def god_list_proofs(user: User = Depends(require_god)):
    from app.services.workforce_intelligence import proof as t9_proof
    return {"proofs": [{"key": k, "label": v["label"]}
                       for k, v in t9_proof.SCENARIOS.items()]}
