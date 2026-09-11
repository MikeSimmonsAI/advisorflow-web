"""THE DEMO SUITE'S HTTP SURFACE — two routers, two audiences, one engine.

    /demo-suite/*        the PRESENTER. Gated by the `demo_suite` capability
                         over the brand being presented. Never by a role.

    /god/demo-suite/*    the OWNER. Building and rebuilding brands'
                         demonstration environments, and reading the demo's
                         own event trail.

WHY THIS IS NOT `/demo/*`

`/demo/*` already exists and belongs to the APP_ENV=demo deployment: every one
of its routes 404s outside that environment, deliberately, so that production
cannot be probed for a demo surface. Mounting the Demo Suite there would have
meant either breaking that guarantee or making the Suite unreachable in the
only environment it is for. Different prefix, different thing.

EVERY ROUTE RESOLVES ENTITLEMENT FIRST, THEN THE ENVIRONMENT, THEN THE TENANTS

`demo_access.presenting_context` is the single place those three gates run, in
that order, and every presenter route below calls it. Hiding a button is not
access control — the Lead Scraper taught this codebase that once already.
"""
# NO `from __future__ import annotations` IN A ROUTER MODULE. It turns every
# annotation into a string, and FastAPI then resolves a request body's forward
# reference against the ENDPOINT FUNCTION'S `__globals__` — which, on a route
# wrapped by `@limiter.limit`, belong to slowapi rather than to this module. The
# app then refuses to import with "name 'ActionIn' is not defined". Every other
# router here omits it for the same reason.
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db, require_god
from app.limiter import limiter
from app.models.demo_suite_models import DemoActionEvent
from app.models.models import Platform, User
from app.services import demo_access, demo_actions, demo_content
from app.services import demo_environment as denv
from app.services import demo_world

log = logging.getLogger(__name__)

router = APIRouter(prefix="/demo-suite", tags=["demo-suite"])
god_router = APIRouter(prefix="/god/demo-suite", tags=["demo-suite (owner)"])

# A presenter leaning on a button during a meeting must not be able to queue
# fifty rebuilds behind themselves. Generous enough that nobody presenting
# normally will ever see it.
ACTION_LIMIT = "60/minute"
BUILD_LIMIT = "10/minute"


class ActionIn(BaseModel):
    action: str
    params: Optional[Dict[str, Any]] = None
    scenario: Optional[str] = None
    step: Optional[str] = None


class StepIn(BaseModel):
    step: str


# ─────────────────────────────────────────────────────────────────────────────
# PRESENTER
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/me")
def my_demo_access(db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    """Which brands this caller may present, and which they may rebuild.

    The frontend asks this instead of deciding from a role — which is how a
    sidebar comes to disagree with the server.
    """
    summary = demo_access.entitlement_summary(db, user)
    # Say whether each brand actually HAS an environment, so the Suite can
    # explain an empty state instead of looking broken.
    for brand in summary["brands"]:
        env = denv.get_environment(db, brand["platform_id"])
        brand["environment_ready"] = bool(env is not None and env.is_ready())
        brand["environment_status"] = env.status if env else "empty"
    return summary


@router.get("/help")
def help_index(_: User = Depends(get_current_user)):
    """Every contextual help topic. Not gated by brand — this is product
    explanation, not demonstration data, and a salesperson reading it before
    they have been granted access is a salesperson preparing."""
    return {"topics": demo_content.all_help()}


@router.get("/help/{key}")
def help_one(key: str, _: User = Depends(get_current_user)):
    topic = demo_content.help_topic(key)
    if topic is None:
        raise HTTPException(status_code=404, detail="No such help topic.")
    return topic


@router.get("/{platform_id}/context")
def presentation_context(platform_id: str,
                         opportunity: Optional[str] = Query(None),
                         db: Session = Depends(get_db),
                         user: User = Depends(get_current_user)):
    """Who am I presenting to, and where does EXIT DEMO take me?

    ===================================================================
    THERE IS NO RETURN URL PARAMETER, ON PURPOSE
    ===================================================================

    The obvious way to build "come back where you started" is a `?return=`
    query string. That is an open redirect with a helpful name: whatever the
    link contains is where the person lands, and a link is the easiest thing
    in the world to send somebody.

    So the browser sends a KIND and an ID, never a destination, and this
    endpoint composes the destination itself from a fixed set of in-app paths.
    There is no input that reaches `return_href`.

    The opportunity id IS accepted — and is authorised here before a single
    fact about it is returned. `_load` applies the same record-level rule the
    rest of the sales workspace applies, so another rep's deal or another
    brand's deal resolves to nothing. A caller who supplies one they may not
    see does not get an error that confirms it exists; they get the library,
    which is where somebody with no valid origin belongs anyway.

    A deal on a DIFFERENT brand from the one being presented is also refused:
    presenting EvoSys Pro's environment while the header names a BookaBoost
    prospect would be a cross-brand claim on screen, and the screen is the
    thing a customer is looking at.
    """
    demo_access.assert_may_present(db, user, platform_id)

    fallback = {
        "opportunity_id": None,
        "company_name": None,
        "contact_name": None,
        "owner_name": None,
        "return_href": "/demo-suite",
        "return_label": "Demo Suite",
        "origin": "library",
    }
    if not opportunity:
        return fallback

    # Imported here rather than at module scope: the sales router imports the
    # demo services for its own nav flag, and a module-level import both ways
    # is a cycle.
    from app.models.sales_models import BrandSalesOrg, Opportunity
    from app.services.sales_access import assert_can_view_opportunity

    opp = (db.query(Opportunity)
           .filter(Opportunity.id == opportunity).first())
    if opp is None:
        return fallback
    try:
        assert_can_view_opportunity(user, opp, db)
    except Exception:
        # Not theirs to see. Fall back silently — a refusal here would tell a
        # caller that the id they guessed is real.
        return fallback

    bso = (db.query(BrandSalesOrg)
           .filter(BrandSalesOrg.id == opp.brand_sales_org_id).first())
    if bso is None or bso.platform_id != platform_id:
        return fallback

    owner = None
    if opp.owner_user_id:
        u = db.query(User).filter(User.id == opp.owner_user_id).first()
        owner = u.full_name if u else None

    return {
        "opportunity_id": opp.id,
        "company_name": opp.company_name,
        "contact_name": opp.contact_name,
        "owner_name": owner,
        # Composed here, from an id this request has already authorised.
        "return_href": "/sales/opportunities/%s" % opp.id,
        "return_label": opp.company_name or "the opportunity",
        "origin": "opportunity",
    }


@router.get("/{platform_id}/world")
def world(platform_id: str, lead: Optional[str] = Query(None),
          db: Session = Depends(get_db),
          user: User = Depends(get_current_user)):
    """Every panel of the demonstration world, in one round trip."""
    plat, env, org, brand = demo_access.presenting_context(db, user, platform_id)
    return {
        "brand": {"platform_id": plat.id, "name": plat.name, "slug": plat.slug},
        "environment": {
            "id": env.id, "status": env.status,
            "seeded_at": env.seeded_at.isoformat() if env.seeded_at else None,
            "workspace": org.name, "sales_org": brand.name,
            "stale": bool(env.seed_version != denv.SEED_VERSION),
        },
        "may_admin": demo_access.may_admin(db, user, platform_id),
        "panels": demo_world.snapshot(db, env, org, brand, lead_key=lead),
        "panel_labels": demo_content.PANELS,
    }


@router.get("/{platform_id}/scenarios")
def scenarios(platform_id: str, db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    """The catalogue, with this presenter's progress against each one."""
    plat, env, org, brand = demo_access.presenting_context(db, user, platform_id)
    return demo_access.readiness(db, env, user)


@router.get("/{platform_id}/scenarios/{scenario_key}")
def scenario(platform_id: str, scenario_key: str,
             db: Session = Depends(get_db),
             user: User = Depends(get_current_user)):
    """One scenario in full, plus where this presenter is in it.

    THE WHOLE SCENARIO, not just the current step: a presenter asked a question
    three steps ahead needs to be able to look without losing their place.
    """
    plat, env, org, brand = demo_access.presenting_context(db, user, platform_id)
    return demo_access.session_payload(db, env, user, scenario_key)


@router.post("/{platform_id}/scenarios/{scenario_key}/step")
def mark_step(platform_id: str, scenario_key: str, body: StepIn,
              db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    """Record that a step has been performed and move the pointer on."""
    plat, env, org, brand = demo_access.presenting_context(db, user, platform_id)
    return demo_access.mark_step(db, env, user, scenario_key, body.step)


@router.post("/{platform_id}/scenarios/{scenario_key}/reset")
def reset_scenario(platform_id: str, scenario_key: str,
                   db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    """Put THIS presenter back at step one. Touches nobody else's demo."""
    plat, env, org, brand = demo_access.presenting_context(db, user, platform_id)
    return demo_access.reset_session(db, env, user, scenario_key)


@router.post("/{platform_id}/action")
@limiter.limit(ACTION_LIMIT)
def run_action(request: Request, platform_id: str, body: ActionIn,
               db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    """Perform one demonstration action against the demo tenant.

    The result carries `narration` — the sentence the presenter can say about
    what just happened. That is not decoration: the whole point of this surface
    is that somebody who is not the platform owner can explain what they just
    did.
    """
    plat, env, org, brand = demo_access.presenting_context(db, user, platform_id)
    out = demo_actions.run(db, env, org, brand, user, body.action,
                           params=body.params, scenario_key=body.scenario,
                           step_key=body.step)
    # Advancing the scenario is a CONSEQUENCE of doing the thing, not a
    # separate click. A presenter who ran the step has run the step.
    if body.scenario and body.step:
        try:
            out["session"] = demo_access.mark_step(db, env, user, body.scenario,
                                                   body.step)["session"]
        except HTTPException:
            # An action run outside a scenario, or with a step key that is not
            # in it, is legitimate — the presenter went off-script. It must not
            # fail the action they just performed.
            out["session"] = None
    out["panels"] = demo_world.snapshot(
        db, env, org, brand, lead_key=(body.params or {}).get("target"))
    return out


@router.post("/{platform_id}/rebuild")
@limiter.limit(BUILD_LIMIT)
def rebuild(request: Request, platform_id: str, db: Session = Depends(get_db),
            user: User = Depends(get_current_user)):
    """Re-seed the SHARED world for this brand. `demo_admin`, never `demo_suite`.

    Separate from resetting a session on purpose: this affects everybody
    presenting this brand, and a presenter mid-meeting must not be able to have
    it done to them by a colleague who meant to reset their own progress.
    """
    demo_access.assert_may_admin(db, user, platform_id)
    out = denv.build(db, platform_id, actor=user)
    denv.record_event(db, denv.get_environment(db, platform_id), user,
                      "rebuild_environment",
                      detail="Demonstration environment rebuilt.", commit=True)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# OWNER
# ─────────────────────────────────────────────────────────────────────────────

@god_router.get("/environments")
def environments(db: Session = Depends(get_db),
                 _god: User = Depends(require_god)):
    """Every brand and the state of its demonstration environment."""
    plats = db.query(Platform).order_by(Platform.name).all()
    return {"environments": [denv.overview(db, p) for p in plats],
            "seed_version": denv.SEED_VERSION,
            "scenarios": demo_content.catalogue()}


@god_router.post("/environments/{platform_id}/build")
@limiter.limit(BUILD_LIMIT)
def build_environment(request: Request, platform_id: str,
                      db: Session = Depends(get_db),
                      god: User = Depends(require_god)):
    """Create or rebuild one brand's demonstration world."""
    return denv.build(db, platform_id, actor=god)


@god_router.post("/environments/{platform_id}/reset")
@limiter.limit(BUILD_LIMIT)
def reset_environment(request: Request, platform_id: str,
                      db: Session = Depends(get_db),
                      god: User = Depends(require_god)):
    """Empty a brand's demonstration world without re-seeding it.

    Distinct from build, which resets and then seeds. This is for taking a
    demonstration environment out of service — the tenants survive, so the
    environment can be rebuilt later with the same ids.
    """
    env = denv.get_environment(db, platform_id)
    if env is None:
        raise HTTPException(status_code=404,
                            detail="This brand has no demonstration "
                                   "environment.")
    return denv.reset(db, env, actor=god)


@god_router.get("/events")
def events(platform_id: Optional[str] = Query(None),
           limit: int = Query(100, ge=1, le=500),
           db: Session = Depends(get_db),
           _god: User = Depends(require_god)):
    """The demo's own trail: who presented what, when, and what was simulated.

    Deliberately NOT in `audit_log_entries`. A demonstration is not a
    control-plane action, and mixing them would mean every genuine audit query
    had to learn to filter demo noise out of itself.
    """
    q = db.query(DemoActionEvent)
    if platform_id:
        q = q.filter(DemoActionEvent.platform_id == platform_id)
    rows = q.order_by(DemoActionEvent.occurred_at.desc()).limit(limit).all()
    return {"events": [{
        "id": r.id, "at": r.occurred_at.isoformat() if r.occurred_at else None,
        "who": r.user_email, "action": r.action,
        "scenario": r.scenario_key, "step": r.step_key,
        "target_type": r.target_type, "target_id": r.target_id,
        "simulated_provider": r.simulated_provider,
        "success": bool(r.success), "detail": r.detail,
    } for r in rows], "total": len(rows)}
