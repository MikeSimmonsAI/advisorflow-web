"""GOD MODE → MANAGE ACCESS. One person, every context, corrected in place.

    GET  /god/access/directory              names for every picker
    GET  /god/access/users/{id}             the whole access footprint
    POST /god/access/users/{id}/preview     what a change plan would do
    POST /god/access/users/{id}/apply       do it, as one transaction, audited
    GET  /god/access/users/{id}/audit       what has been done to this person

WHY THESE ARE NOT IN `god_router.py`

That file is 2,500 lines of platform diagnostics, voice configuration, revenue
history and Zoom probes, and it contains no membership write at all. Adding
the platform's identity-provisioning surface to the bottom of it would bury
the most consequential routes on the control plane in the least related
company. A distinct prefix also means the whole surface can be reasoned about,
and tested, as one thing.

EVERY ROUTE IS `require_god`, AND THAT IS THE WHOLE AUTHORITY MODEL

There is no delegated variant of this surface, no "managers may manage their
own team" flag, and no capability that unlocks part of it. Root provisioning is
a God function. When delegation is wanted later, it will need a scoped model of
its own — bolting a flag onto these routes is how a second authority layer gets
built by accident, which is exactly what this project was told not to do.
"""
# NO `from __future__ import annotations` IN A ROUTER MODULE.
#
# It turns every annotation into a string, and FastAPI then resolves a request
# body's forward reference against the ENDPOINT FUNCTION'S `__globals__`. On a
# route wrapped by `@limiter.limit` those globals belong to slowapi, not to this
# module, so `PlanIn` is undefined and the app refuses to import. Every other
# router here omits it for the same reason.
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, require_god
from app.limiter import limiter
from app.models.models import AuditLogEntry, User
from app.services import access_management as am

log = logging.getLogger(__name__)

router = APIRouter(prefix="/god/access", tags=["God Mode — Manage Access"])

WRITE_LIMIT = "30/minute"


class PlanIn(BaseModel):
    operations: List[Dict[str, Any]]


def _target(db: Session, user_id: str) -> User:
    """The person being administered.

    `load_user_in_scope` is not used here even though it exists, because the
    route is already god-only and that helper's whole job is narrowing a
    NON-god actor's reach. For a god actor it returns any target unchanged, so
    calling it would add a layer that answers the same thing and reads as
    though a lesser role could get here.
    """
    row = db.query(User).filter(User.id == user_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="User not found")
    return row


@router.get("/directory")
def directory(db: Session = Depends(get_db),
              _god: User = Depends(require_god)):
    """Every brand, sales organization, workspace, role and template — NAMED.

    The whole reason this endpoint exists is that an operator deciding where to
    put somebody is choosing a place, and a place has a name. Nothing on the
    Manage Access screen should ever require reading a UUID.
    """
    return am.directory(db)


@router.get("/users/{user_id}")
def user_footprint(user_id: str, db: Session = Depends(get_db),
                   _god: User = Depends(require_god)):
    """Everything this person holds, everywhere, in sentences.

    Grouped by what the access MEANS — brand contexts, back office, customer
    workspaces, executive portfolio, capabilities, demo entitlement, training —
    rather than by which table it happens to live in.
    """
    return am.footprint(db, _target(db, user_id))


@router.post("/users/{user_id}/preview")
def preview_changes(user_id: str, body: PlanIn,
                    db: Session = Depends(get_db),
                    _god: User = Depends(require_god)):
    """What WOULD change — adding, removing, changing, and what is preserved.

    A read. It performs no write of any kind, which is what lets the operator
    ask the question as many times as they like before answering it.
    """
    return am.preview(db, _target(db, user_id), body.operations)


@router.post("/users/{user_id}/apply")
@limiter.limit(WRITE_LIMIT)
def apply_changes(request: Request, user_id: str, body: PlanIn,
                  db: Session = Depends(get_db),
                  god: User = Depends(require_god)):
    """Execute the plan as ONE transaction, and write ONE audit entry.

    Additions happen before removals and are verified in between, so a
    correction can never strand somebody with neither the access they had nor
    the access they were being given.
    """
    return am.apply(db, _target(db, user_id), body.operations, god)


@router.get("/users/{user_id}/audit")
def user_audit(user_id: str, limit: int = Query(50, ge=1, le=200),
               db: Session = Depends(get_db),
               _god: User = Depends(require_god)):
    """What has been done to this person's access, and by whom.

    Reads the platform's own `audit_log_entries` rather than a private log:
    an access change is a control-plane action and belongs in the same trail as
    every other one. No secrets are ever written there — the before/after
    digest is contexts and names, never tokens, hashes or credentials.
    """
    rows = (db.query(AuditLogEntry)
            .filter(AuditLogEntry.target_type == "user",
                    AuditLogEntry.target_id == user_id)
            .order_by(AuditLogEntry.created_at.desc())
            .limit(limit).all())
    actors = {u.id: u for u in db.query(User).filter(
        User.id.in_([r.actor_user_id for r in rows if r.actor_user_id])).all()} \
        if rows else {}
    out = []
    for r in rows:
        actor = actors.get(r.actor_user_id)
        out.append({
            "id": r.id,
            "action": r.action,
            "at": r.created_at.isoformat() if getattr(r, "created_at", None)
            else None,
            "actor": actor.full_name if actor else None,
            "actor_email": actor.email if actor else None,
            "note": getattr(r, "note", None),
            "before": getattr(r, "before_state", None),
            "after": getattr(r, "after_state", None),
        })
    return {"entries": out, "total": len(out)}
