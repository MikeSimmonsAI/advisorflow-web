"""AI OPERATIONS — the operational surface, and only the operational surface.

TWO ROUTERS, TWO AUTHORITIES, ONE ENGINE.

    `router` (/ai-operations) is the CUSTOMER surface: what my AI employees
    are doing right now, what is waiting, what is stuck, and the controls a
    person in that business needs — take a conversation over, hand it back,
    stop it, stop everything. Every route answers for the caller's OWN
    organization and none of them accepts an organization id, so the tenant
    boundary is a property of the signatures rather than of a filter each
    route has to remember.

    `god_router` (/god/ai-operations) EXTENDS GOD MODE. It is not a second
    root and it grants nobody new authority: `require_god` is the same
    dependency every other platform-control route uses. It carries the three
    things that are genuinely platform-level — the dark-launch state, inbound
    events that could not be attributed to any tenant, and the synthetic
    proofs.

WHAT IS DELIBERATELY NOT HERE. No endpoint that makes an AI employee send
something. Outbound work is initiated by the workforce engine and by the
follow-up worker, both of which run the whole gate chain; an HTTP route that
let a person trigger an AI send would be a route that bypasses the objective,
the cadence and the attempt ceiling while looking like a feature.

Nor is T9's Workforce Intelligence dashboard, nor T10's Executive Command
Center. `supervisor_feed.supervisor_payload` is the contract those will
consume; this router exposes only what an operator needs to control and
observe T7 itself.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.deps import (get_db, require_god, require_not_observation,
                      require_tenant_user)
from app.models.ai_operations_models import AIConversationThread
from app.models.models import User
from app.services.ai_operations import (audit, budget, channels, continuity,
                                        contracts, flags, handoff, profiles,
                                        supervisor_feed)
from app.services.ai_operations import constants as C
from app.services.ai_operations import stop as stop_controls
from app.services.lead_scope import active_workspace_org_id

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/ai-operations", tags=["ai-operations"])
god_router = APIRouter(prefix="/god/ai-operations", tags=["god"])


def _org_id(request: Request, user: User, db: Session) -> str:
    """The caller's own organization, or 409.

    THE ONLY PLACE A TENANT IS RESOLVED ON THIS SURFACE. Routes take no
    organization parameter, so there is no argument anywhere here that could
    widen what a caller sees — the same property `tenant_scheduling` relies
    on for the voice bridge.
    """
    org_id = active_workspace_org_id(user, db, request)
    if not org_id:
        raise HTTPException(
            status_code=409,
            detail=("No workspace is selected. Choose a customer workspace "
                    "before opening AI Operations."))
    return org_id


def _thread(db: Session, thread_id: str, org_id: str) -> AIConversationThread:
    """Load one conversation INSIDE this tenant, or 404.

    404 rather than 403 for another tenant's thread, deliberately: telling a
    caller that a conversation exists somewhere else is itself a disclosure.
    """
    row = (db.query(AIConversationThread)
           .filter(AIConversationThread.id == thread_id,
                   AIConversationThread.organization_id == org_id)
           .first())
    if row is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return row


# ═══════════════════════════════════════════════════════════════════════════
# THE CUSTOMER SURFACE — read
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/overview")
def overview(request: Request, db: Session = Depends(get_db),
             user: User = Depends(require_tenant_user)):
    """Headline operational state for this customer's AI workforce."""
    return supervisor_feed.overview(db, _org_id(request, user, db))


@router.get("/threads")
def list_threads(request: Request, group: Optional[str] = Query(None),
                 employee_id: Optional[str] = Query(None),
                 limit: int = Query(100, ge=1, le=500),
                 db: Session = Depends(get_db),
                 user: User = Depends(require_tenant_user)):
    return {"threads": supervisor_feed.active_objectives(
        db, _org_id(request, user, db), employee_id=employee_id, group=group,
        limit=limit)}


@router.get("/threads/{thread_id}")
def thread_detail(thread_id: str, request: Request,
                  db: Session = Depends(get_db),
                  user: User = Depends(require_tenant_user)):
    org_id = _org_id(request, user, db)
    thread = _thread(db, thread_id, org_id)
    return {
        "thread": continuity.summarize(db, thread),
        "history": continuity.history(db, thread, limit=60),
        "communications": supervisor_feed.communications(
            db, org_id, thread_id=thread_id, limit=100),
        "activity": supervisor_feed.activity(db, org_id,
                                             thread_id=thread_id, limit=100),
    }


@router.get("/communications")
def list_communications(request: Request, state: Optional[str] = Query(None),
                        limit: int = Query(100, ge=1, le=500),
                        db: Session = Depends(get_db),
                        user: User = Depends(require_tenant_user)):
    return {"communications": supervisor_feed.communications(
        db, _org_id(request, user, db), state=state, limit=limit)}


@router.get("/scheduled")
def list_scheduled(request: Request, limit: int = Query(100, ge=1, le=500),
                   db: Session = Depends(get_db),
                   user: User = Depends(require_tenant_user)):
    return {"scheduled": supervisor_feed.scheduled(
        db, _org_id(request, user, db), limit=limit)}


@router.get("/handoffs")
def list_handoffs(request: Request, db: Session = Depends(get_db),
                  user: User = Depends(require_tenant_user)):
    return {"handoffs": handoff.open_handoffs(db, _org_id(request, user, db))}


@router.get("/blocked")
def list_blocked(request: Request, limit: int = Query(100, ge=1, le=500),
                 db: Session = Depends(get_db),
                 user: User = Depends(require_tenant_user)):
    return {"blocked": supervisor_feed.blocked_work(
        db, _org_id(request, user, db), limit=limit)}


@router.get("/activity")
def list_activity(request: Request, employee_id: Optional[str] = Query(None),
                  limit: int = Query(100, ge=1, le=500),
                  db: Session = Depends(get_db),
                  user: User = Depends(require_tenant_user)):
    return {"activity": supervisor_feed.activity(
        db, _org_id(request, user, db), employee_id=employee_id, limit=limit)}


@router.get("/employees/{employee_id}/status")
def employee_status(employee_id: str, request: Request,
                    db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_user)):
    return supervisor_feed.employee_status(db, _org_id(request, user, db),
                                           employee_id)


@router.get("/budget")
def budget_report(request: Request, employee_id: Optional[str] = Query(None),
                  db: Session = Depends(get_db),
                  user: User = Depends(require_tenant_user)):
    return budget.report(db, _org_id(request, user, db),
                         employee_id=employee_id)


@router.get("/stop-reasons")
def stop_reasons(user: User = Depends(require_tenant_user)):
    """The vocabulary, so the console's filters are not a second copy."""
    return {"reasons": stop_controls.reasons_catalogue()}


# ═══════════════════════════════════════════════════════════════════════════
# THE CUSTOMER SURFACE — control
# ═══════════════════════════════════════════════════════════════════════════
#
# Every mutation below is guarded by `require_not_observation` as well as by
# the tenant gate: an executive observing a customer read-only must not be
# able to stop that customer's AI workforce.

@router.post("/threads/{thread_id}/takeover")
def takeover(thread_id: str, request: Request,
             payload: dict = Body(default={}),
             db: Session = Depends(get_db),
             user: User = Depends(require_tenant_user),
             _guard: User = Depends(require_not_observation)):
    """A person takes this conversation. The AI stops at its next gate."""
    org_id = _org_id(request, user, db)
    thread = _thread(db, thread_id, org_id)
    row = stop_controls.take_over(
        db, thread, user_id=user.id,
        reason_code=(payload or {}).get("reason_code") or "manual_takeover",
        note=(payload or {}).get("note"))
    db.commit()
    return {"ok": True, "ownership_id": row.id,
            "thread": continuity.summarize(db, thread)}


@router.post("/threads/{thread_id}/release")
def release(thread_id: str, request: Request,
            payload: dict = Body(default={}),
            db: Session = Depends(get_db),
            user: User = Depends(require_tenant_user),
            _guard: User = Depends(require_not_observation)):
    """Hand it back. The AI resumes only if the person says it may."""
    org_id = _org_id(request, user, db)
    thread = _thread(db, thread_id, org_id)
    released = stop_controls.release(
        db, thread, user_id=user.id,
        ai_may_resume=bool((payload or {}).get("ai_may_resume")),
        note=(payload or {}).get("note"))
    db.commit()
    return {"ok": released, "thread": continuity.summarize(db, thread)}


@router.post("/threads/{thread_id}/stop")
def stop_thread(thread_id: str, request: Request,
                payload: dict = Body(default={}),
                db: Session = Depends(get_db),
                user: User = Depends(require_tenant_user),
                _guard: User = Depends(require_not_observation)):
    org_id = _org_id(request, user, db)
    thread = _thread(db, thread_id, org_id)
    reason = (payload or {}).get("reason") or C.STOP_SUPERVISOR
    stopped = stop_controls.stop_thread(db, None, thread, reason=reason,
                                        actor_kind=C.ACTOR_HUMAN,
                                        actor_id=user.id)
    audit.human_action(db, organization_id=org_id, actor_user_id=user.id,
                       action="ai_operations.stop_thread",
                       target_type="ai_conversation_thread",
                       target_id=thread.id, details={"reason": reason})
    db.commit()
    return {"ok": stopped, "thread": continuity.summarize(db, thread)}


@router.post("/employees/{employee_id}/pause-work")
def pause_employee_work(employee_id: str, request: Request,
                        payload: dict = Body(default={}),
                        db: Session = Depends(get_db),
                        user: User = Depends(require_tenant_user),
                        _guard: User = Depends(require_not_observation)):
    """Cancel this employee's pending work in THIS organization.

    Not the employee's own pause switch — that lives on the workforce
    engine's employee row and belongs to its screens. Two owners for one
    flag is how a pause stops meaning anything.
    """
    org_id = _org_id(request, user, db)
    cancelled = stop_controls.pause_employee_work(
        db, organization_id=org_id, employee_id=employee_id, user_id=user.id,
        reason=(payload or {}).get("reason") or "paused by an operator")
    db.commit()
    return {"ok": True, "cancelled_actions": cancelled}


@router.post("/stop-all")
def stop_all(request: Request, payload: dict = Body(default={}),
             db: Session = Depends(get_db),
             user: User = Depends(require_tenant_user),
             _guard: User = Depends(require_not_observation)):
    """The tenant-level stop. Every open conversation, every pending action.

    Available to any user who can reach this surface, on purpose. Stopping is
    not a privileged act: the person who can see the AI working is the person
    who should be able to stop it, and requiring an admin would mean the
    person watching it go wrong has to go and find one.
    """
    org_id = _org_id(request, user, db)
    result = stop_controls.stop_all_for_organization(
        db, organization_id=org_id, user_id=user.id,
        reason=(payload or {}).get("reason") or C.STOP_SUPERVISOR)
    db.commit()
    return {"ok": True, **result}


# ═══════════════════════════════════════════════════════════════════════════
# GOD MODE — platform state, unattributable inbound, and the proofs
# ═══════════════════════════════════════════════════════════════════════════

@god_router.get("/state")
def platform_state(db: Session = Depends(get_db),
                   user: User = Depends(require_god)):
    """Why nothing is sending, in one factual answer."""
    return {
        "dark_launch": flags.state(),
        "providers": channels.describe(db),
        "workforce_contracts": contracts.availability(),
        "operations": list(C.ALL_OPERATIONS),
        "operation_authority": {op: contracts.tool_for(op)
                                for op in C.ALL_OPERATIONS},
    }


@god_router.get("/unrouted")
def unrouted(limit: int = Query(50, ge=1, le=200),
             db: Session = Depends(get_db),
             user: User = Depends(require_god)):
    """Inbound messages that could not be attributed to any tenant.

    God-only because an event with no organization has no tenant to show it
    to, and showing it to a guess would be the disclosure the router refused
    to make in the first place.
    """
    return {"events": supervisor_feed.unrouted_inbound(db, None, limit=limit)}


@god_router.get("/profiles")
def list_profiles(user: User = Depends(require_god)):
    return {"profiles": profiles.catalogue(),
            "note": ("Building a profile creates a clearly-marked synthetic "
                     "organization with invented contacts. Nothing it does "
                     "can reach a real person.")}


@god_router.post("/simulate")
def simulate(payload: dict = Body(default={}),
             db: Session = Depends(get_db),
             user: User = Depends(require_god)):
    """Run a synthetic lifecycle through the real engine.

    REFUSES WITHOUT AN EXPLICIT ACKNOWLEDGEMENT. Creating synthetic
    organizations in a deployment is a deliberate act, not something a
    misdirected click should do — `confirm_synthetic_data: true` is the
    acknowledgement, and the profile builders refuse without it regardless.
    """
    if not (payload or {}).get("confirm_synthetic_data"):
        raise HTTPException(
            status_code=400,
            detail=("This creates a synthetic organization and synthetic "
                    "contacts. Send confirm_synthetic_data: true to "
                    "proceed."))
    from app.services.ai_operations import simulator
    profile_key = (payload or {}).get("profile") or "reactivation"
    scenario = (payload or {}).get("scenario")
    try:
        profile = profiles.build(db, profile_key, allow_synthetic_data=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if profile_key == "reactivation":
        scenarios = ([scenario] if scenario
                     else list(simulator.REACTIVATION_SCENARIOS))
        reports = [simulator.reactivation(db, profile, scenario=s)
                   for s in scenarios]
    else:
        reports = [simulator.full_lifecycle(db, profile)]
    db.commit()
    return {"profile": profile.as_dict(), "reports": reports,
            "dark_launch": flags.state()}


@god_router.post("/evaluate")
def evaluate(payload: dict = Body(default={}),
             db: Session = Depends(get_db),
             user: User = Depends(require_god)):
    """Run the adversarial evaluation harness and return its results."""
    if not (payload or {}).get("confirm_synthetic_data"):
        raise HTTPException(
            status_code=400,
            detail=("The harness builds synthetic organizations. Send "
                    "confirm_synthetic_data: true to proceed."))
    from app.services.ai_operations import evaluation
    result = evaluation.run(db, allow_synthetic_data=True,
                            triggered_by=user.id)
    db.commit()
    return result


@god_router.get("/supervisor/{organization_id}")
def supervisor_payload(organization_id: str, db: Session = Depends(get_db),
                       user: User = Depends(require_god)):
    """The whole operational read for one customer — the contract T9 consumes.

    Takes an organization id because God Mode is the one authority that may
    ask about a customer it is not inside. Every other route on this surface
    answers only for the caller's own tenant.
    """
    return supervisor_feed.supervisor_payload(db, organization_id)
