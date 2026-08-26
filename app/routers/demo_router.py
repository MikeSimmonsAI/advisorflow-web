"""The demo control surface.

TWO INDEPENDENT LOCKS, AND THEY ARE NOT THE SAME LOCK
-----------------------------------------------------
1. ENVIRONMENT. Every route below returns 404 unless `APP_ENV=demo`. Not 403 -
   404. In production these paths do not exist as far as a caller can tell,
   which is the correct answer: a 403 would confirm the demo surface is part of
   this build and invite someone to go looking for a way in.

2. IDENTITY. Inside the demo environment the mutating routes still require a
   god-level user. Demo Mode does not relax RBAC. A demo rep is still a rep,
   a demo advisor still cannot see another tenant, and the reset button is not
   something a prospect can find by clicking around during a presentation.

THE ONE UNAUTHENTICATED ROUTE is `/demo/environment`, which returns only which
environment answered. The frontend needs that before login to decide whether to
paint the banner, and the URL already tells anybody the same thing.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user
from app.limiter import limiter
from app.models.models import User
from app.services import environment as env
from app.services import demo_runner as runner
from app.services import demo_scenarios as registry

log = logging.getLogger(__name__)

router = APIRouter(prefix="/demo", tags=["demo"])

# Reset and seed rebuild the whole demo world. They are cheap, but an operator
# leaning on a button during a presentation should not be able to queue fifty
# rebuilds behind themselves.
WRITE_LIMIT = "20/minute"


def require_demo_env() -> None:
    """404 outside the demo environment. See the module docstring."""
    if not env.is_demo():
        raise HTTPException(status_code=404, detail="Not Found")


# THE ENVIRONMENT CHECK MUST RUN BEFORE AUTHENTICATION, AND THAT TAKES A
# DECORATOR-LEVEL DEPENDENCY.
#
# Calling `require_demo_env()` from inside `require_demo_operator` is too late:
# FastAPI resolves `get_current_user` while solving the signature, so an
# unauthenticated caller in production got `401 Not authenticated` - which
# confirms the route exists and is worth probing. Route `dependencies=[...]`
# are solved BEFORE the endpoint's own parameters, so declaring it there is
# what actually produces a 404. The test suite caught this; the comment that
# used to sit here claimed the behaviour the code did not have.
DEMO_ONLY = [Depends(require_demo_env)]


def require_demo_operator(current_user: User = Depends(get_current_user)) -> User:
    """A god-level user. Pair with DEMO_ONLY on the route, which runs first."""
    require_demo_env()
    role = (getattr(current_user, "role", "") or "").lower()
    if role not in ("god_admin", "super_admin"):
        raise HTTPException(status_code=403,
                            detail="Demo controls require a platform owner.")
    return current_user


class ScenarioIn(BaseModel):
    scenario: str


class AdvanceIn(BaseModel):
    scenario: str
    # Optional guard: the operator names the step they believe is next, and the
    # runner refuses if it is not. Stops a double-tap from silently running the
    # step after the one they meant.
    step: str = None


@router.get("/environment")
def demo_environment():
    """Which environment is answering. Unauthenticated by design.

    Returns the same shape in production, where `demo_mode` is false and
    `banner` is null - so the frontend has one code path rather than a
    404-means-production special case.
    """
    return env.banner_payload()


@router.get("/scenarios", dependencies=DEMO_ONLY)
def list_scenarios(_: None = Depends(require_demo_env)):
    """The catalogue, grouped by domain in the UI. No auth: knowing which
    demo scenarios exist reveals nothing, and the operator needs it on the
    login screen."""
    return {"scenarios": registry.catalogue(),
            "environment": env.banner_payload()}


@router.get("/state", dependencies=DEMO_ONLY)
def demo_state(db: Session = Depends(get_db),
               operator: User = Depends(require_demo_operator)):
    """Everything the control panel renders: every scenario's position, the
    firewall's status, and the recent event log."""
    return runner.overview(db)


@router.post("/seed", dependencies=DEMO_ONLY)
@limiter.limit(WRITE_LIMIT)
def demo_seed(request: Request, body: ScenarioIn,
              db: Session = Depends(get_db),
              operator: User = Depends(require_demo_operator)):
    """Reset everything, then build one scenario's starting world."""
    out = runner.seed_scenario(db, body.scenario, operator=operator)
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail=out.get("error"))
    return out


@router.post("/advance", dependencies=DEMO_ONLY)
@limiter.limit(WRITE_LIMIT)
def demo_advance(request: Request, body: AdvanceIn,
                 db: Session = Depends(get_db),
                 operator: User = Depends(require_demo_operator)):
    """Run the next step of a scenario."""
    out = runner.advance_scenario(db, body.scenario, operator=operator,
                                  step_key=body.step)
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail=out.get("error"))
    return out


@router.post("/reset", dependencies=DEMO_ONLY)
@limiter.limit(WRITE_LIMIT)
def demo_reset(request: Request, db: Session = Depends(get_db),
               operator: User = Depends(require_demo_operator)):
    """Remove every demo-owned record and return every scenario to empty."""
    return runner.reset_all(db, operator=operator)
