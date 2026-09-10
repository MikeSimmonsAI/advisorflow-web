"""WHO MAY PRESENT, WHICH BRAND, AND WHERE THEY ARE UP TO.

THE ENTITLEMENT

    demo_suite   over a PLATFORM   →  may present that brand
    demo_admin   over a PLATFORM   →  may rebuild that brand's environment

Both are ordinary rows in `user_capability_grants`, resolved by
`capabilities.has_platform_capability`. There is no second authority engine
here and no boolean on `users`: the capability registry is the one place a
permission is named, and the (scope_type, scope_id) pair is the one way a
permission is scoped.

WHAT DEMO ACCESS IS NOT

It is not God access. It is not customer access. It is not access to another
brand's demonstration. A person holding `demo_suite` over EvoSys Pro can open
the EvoSys Pro demonstration environment and do nothing else that they could
not do a minute before it was granted — no customer's leads, no platform
administration, no membership they did not already hold.

That separation is the whole reason the entitlement exists. If presenting the
product required a customer workspace membership, then every salesperson
capable of running a demo would also be inside a real tenant, and the fastest
way to give somebody demo access would be to give them access to a customer.

THE PRESENTER'S PLACE IS PER PERSON

Two reps demonstrating the same brand on the same afternoon share the seeded
world and each keep their own step counter. Resetting a SESSION puts one
presenter back at step one; rebuilding the WORLD is `demo_admin` and affects
everybody standing in it. Conflating those two would mean one rep's reset
wiped another rep's live demonstration.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.demo_suite_models import (SESSION_COMPLETE, SESSION_READY,
                                          SESSION_RUNNING, DemoEnvironment,
                                          DemoSession)
from app.models.models import Organization, Platform, User
from app.models.sales_models import BrandSalesOrg
from app.services import capabilities, demo_content
from app.services import demo_environment as denv

log = logging.getLogger(__name__)

CAP_PRESENT = "demo_suite"
CAP_ADMIN = "demo_admin"


def is_god(user: Optional[User]) -> bool:
    return getattr(user, "role", None) == "god_admin"


# ─────────────────────────────────────────────────────────────────────────────
# ENTITLEMENT
# ─────────────────────────────────────────────────────────────────────────────

def presentable_platforms(db: Session, user: User) -> List[Platform]:
    """Every brand this person may present.

    god_admin gets every platform, because the owner precedes grants — the
    same answer `has_platform_capability` gives, expressed here as a list
    rather than as a wildcard hidden inside a per-platform lookup.
    """
    if is_god(user):
        return db.query(Platform).order_by(Platform.name).all()
    ids = capabilities.platforms_with_capability(db, user, CAP_PRESENT)
    if not ids:
        return []
    return (db.query(Platform).filter(Platform.id.in_(ids))
            .order_by(Platform.name).all())


def may_present(db: Session, user: User, platform_id: str) -> bool:
    return capabilities.has_platform_capability(db, user, platform_id,
                                                CAP_PRESENT)


def may_admin(db: Session, user: User, platform_id: str) -> bool:
    """Rebuild authority. `demo_admin` OR god — never `demo_suite` alone.

    A presenter who could rebuild the world could destroy a colleague's demo
    mid-meeting by clicking the wrong button, so the two are separate
    capabilities rather than one with a confirmation dialog.
    """
    return capabilities.has_platform_capability(db, user, platform_id,
                                                CAP_ADMIN)


def entitlement_summary(db: Session, user: User) -> Dict[str, Any]:
    """What THIS caller may do, said in words. The frontend asks this instead
    of deciding from a role, which is how a sidebar comes to disagree with the
    server."""
    plats = presentable_platforms(db, user)
    return {
        "is_god": is_god(user),
        "may_present_any": bool(plats),
        "brands": [{
            "platform_id": p.id, "platform_name": p.name,
            "platform_slug": p.slug,
            "may_present": True,
            "may_admin": may_admin(db, user, p.id),
        } for p in plats],
    }


def assert_may_present(db: Session, user: User, platform_id: str) -> Platform:
    """403 for a brand this person may not present, 404 for one that is not
    there. Both are refusals; they are different refusals and saying so is
    correct — a brand's existence is not a secret, its demonstration
    environment is not sensitive, and pretending otherwise would leave a
    presenter unable to tell "I need access" from "wrong link"."""
    plat = db.query(Platform).filter(Platform.id == platform_id).first()
    if plat is None:
        raise HTTPException(status_code=404, detail="No such brand.")
    if not may_present(db, user, platform_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have Demo Suite access for %s. The platform "
                   "owner grants it from God Mode → Manage Access."
                   % plat.name)
    return plat


def assert_may_admin(db: Session, user: User, platform_id: str) -> Platform:
    plat = db.query(Platform).filter(Platform.id == platform_id).first()
    if plat is None:
        raise HTTPException(status_code=404, detail="No such brand.")
    if not may_admin(db, user, platform_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Rebuilding a demonstration environment requires demo "
                   "environment administration for %s. Presenting it does not."
                   % plat.name)
    return plat


def presenting_context(db: Session, user: User, platform_id: str
                       ) -> Tuple[Platform, DemoEnvironment, Organization,
                                  BrandSalesOrg]:
    """Everything a demo request needs, with every gate passed in order.

        entitlement  → the person may present this brand
        environment  → the brand has one, and it is built
        tenants      → both resolve, and both are proved to be demo tenants

    Called by every Demo Suite route. One place to be wrong, one place to test.
    """
    plat = assert_may_present(db, user, platform_id)
    env = denv.get_environment(db, platform_id)
    env, org, brand = denv.require_ready(db, env)
    return plat, env, org, brand


# ─────────────────────────────────────────────────────────────────────────────
# THE PRESENTER'S SESSION
# ─────────────────────────────────────────────────────────────────────────────

def _completed_list(session: DemoSession) -> List[str]:
    raw = session.completed_steps or ""
    return [s for s in raw.split("\n") if s]


def _set_completed(session: DemoSession, keys: List[str]) -> None:
    # Ordered, de-duplicated, preserving first completion order.
    seen, out = set(), []
    for k in keys:
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    session.completed_steps = "\n".join(out)


def get_session(db: Session, env: DemoEnvironment, user: User,
                scenario_key: str, create: bool = True
                ) -> Optional[DemoSession]:
    scenario = demo_content.scenario_or_404(scenario_key)
    row = (db.query(DemoSession)
           .filter(DemoSession.environment_id == env.id,
                   DemoSession.user_id == user.id,
                   DemoSession.scenario_key == scenario_key)
           .first())
    if row is None and create:
        row = DemoSession(
            environment_id=env.id, user_id=user.id,
            scenario_key=scenario_key, status=SESSION_READY,
            current_step=0, total_steps=len(scenario["steps"]))
        db.add(row)
        db.commit()
        db.refresh(row)
    if row is not None and row.total_steps != len(scenario["steps"]):
        # The scenario gained or lost a step since this session started. Trust
        # the code, not the row: a stale counter would make the progress bar
        # lie for the rest of the presentation.
        row.total_steps = len(scenario["steps"])
        db.commit()
    return row


def session_payload(db: Session, env: DemoEnvironment, user: User,
                    scenario_key: str) -> Dict[str, Any]:
    """The scenario, the presenter's place in it, and the coach's content.

    THE WHOLE SCENARIO IS RETURNED, not just the current step. A presenter
    being asked a question three steps ahead needs to be able to look, and a
    coach that can only see the current step forces them back to a printed
    run sheet — which is the thing this replaces.
    """
    scenario = demo_content.scenario_or_404(scenario_key)
    session = get_session(db, env, user, scenario_key)
    done = set(_completed_list(session))
    steps = []
    for i, st in enumerate(scenario["steps"]):
        item = dict(st)
        item["index"] = i
        item["done"] = st["key"] in done
        item["current"] = (i == session.current_step)
        steps.append(item)
    return {
        "scenario": {
            "key": scenario["key"], "name": scenario["name"],
            "audience": scenario["audience"], "minutes": scenario["minutes"],
            "summary": scenario["summary"], "opening": scenario["opening"],
            "closing": scenario["closing"],
        },
        "steps": steps,
        "session": {
            "status": session.status,
            "current_step": session.current_step,
            "total_steps": session.total_steps,
            "completed": sorted(done),
            "started_at": (session.started_at.isoformat()
                           if session.started_at else None),
            "completed_at": (session.completed_at.isoformat()
                             if session.completed_at else None),
        },
    }


def mark_step(db: Session, env: DemoEnvironment, user: User,
              scenario_key: str, step_key: str) -> Dict[str, Any]:
    """Record that a step has been performed, and move the pointer on.

    IDEMPOTENT AND ORDER-TOLERANT. A presenter may legitimately re-run a step
    a prospect asked to see again, or skip one that came up in conversation
    already. Neither should corrupt the counter, so completion is a SET and
    the pointer is derived from it rather than incremented blindly.
    """
    scenario = demo_content.scenario_or_404(scenario_key)
    step = demo_content.find_step(scenario, step_key)
    if step is None:
        raise HTTPException(status_code=404,
                            detail="No such step in this scenario.")
    session = get_session(db, env, user, scenario_key)
    done = _completed_list(session)
    done.append(step_key)
    _set_completed(session, done)

    keys = [s["key"] for s in scenario["steps"]]
    done_set = set(_completed_list(session))
    # The pointer lands on the first step NOT yet done, which is where a
    # presenter picking the demo back up actually wants to be.
    nxt = next((i for i, k in enumerate(keys) if k not in done_set), len(keys))
    session.current_step = min(nxt, len(keys) - 1) if keys else 0

    now = datetime.utcnow()
    if session.started_at is None:
        session.started_at = now
    session.last_advanced_at = now
    if done_set >= set(keys):
        session.status = SESSION_COMPLETE
        session.completed_at = now
    else:
        session.status = SESSION_RUNNING
        session.completed_at = None
    db.commit()
    return session_payload(db, env, user, scenario_key)


def reset_session(db: Session, env: DemoEnvironment, user: User,
                  scenario_key: str) -> Dict[str, Any]:
    """Put THIS presenter back at step one. Touches nobody else's demo.

    Deliberately does NOT re-seed the world: a presenter who wants a clean
    story usually wants to run the same scenario again, and the records they
    moved are the evidence that it worked. Rebuilding the world is a separate,
    `demo_admin` operation with a separate button and a different consequence.
    """
    session = get_session(db, env, user, scenario_key)
    session.completed_steps = None
    session.current_step = 0
    session.status = SESSION_READY
    session.started_at = None
    session.completed_at = None
    session.last_advanced_at = datetime.utcnow()
    db.commit()
    denv.record_event(db, env, user, "reset_session",
                      scenario_key=scenario_key,
                      detail="Presenter session reset to step one.",
                      commit=True)
    return session_payload(db, env, user, scenario_key)


def readiness(db: Session, env: DemoEnvironment, user: User) -> Dict[str, Any]:
    """How far this presenter has got across every scenario.

    Feeds both the Demo Suite's own picker and the Demo Presenter training
    path, which is the point: training that says "practise this" and a demo
    that knows whether you did are the same fact, and storing it twice would
    let them disagree.
    """
    rows = {r.scenario_key: r for r in
            db.query(DemoSession)
            .filter(DemoSession.environment_id == env.id,
                    DemoSession.user_id == user.id).all()}
    out = []
    for entry in demo_content.catalogue():
        row = rows.get(entry["key"])
        out.append({
            **entry,
            "status": row.status if row else SESSION_READY,
            "completed_steps": (len(_completed_list(row)) if row else 0),
            "practised": bool(row and row.status == SESSION_COMPLETE),
        })
    return {"scenarios": out,
            "practised_count": sum(1 for s in out if s["practised"]),
            "total": len(out)}
