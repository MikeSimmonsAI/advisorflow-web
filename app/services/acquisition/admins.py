"""EVOSYS ACQUISITION ADMINISTRATION - a program grant, not God (#452, 2026-11-03).

Owner's architecture: God Mode is the global control plane over every platform.
EvoSys Pro is an operating platform beneath it, with its own program
administrators. Running the Acquisition Control Center - the master prospect
pool, research, distribution, service-company onboarding, worker access,
reporting - is EvoSys program administration. It must not require God.

A grant is an existing Membership row (no new tables, no new auth):
    scope_type="product", scope_id="acquisition", role="program_admin"
granted and revoked only by God (global delegation), audited, revocable.

What it opens: the Acquisition master screens and every client's operations.
What it never opens: God Mode, other platforms (BookaBoost, ...), other
programs, or the power to make another Acquisition admin.

Bootstrap: ACQUISITION_ADMIN_EMAILS (comma separated) names logins the owner
asked for. On startup each named, active, non-God login without a grant gets
one (idempotent; never revokes; skips unknown emails). The env var is the
owner's written instruction; the row records who/why.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.models.models import User
from app.models.sales_models import Membership, SCOPE_PRODUCT

log = logging.getLogger(__name__)

PRODUCT = "acquisition"
ROLE = "program_admin"
INTERNAL_ROLES = ("god_admin",)
ENV = "ACQUISITION_ADMIN_EMAILS"


def _rows(db: Session, active_only: bool = True):
    q = db.query(Membership).filter(Membership.scope_type == SCOPE_PRODUCT, Membership.scope_id == PRODUCT,
                                    Membership.role == ROLE)
    return q.filter(Membership.is_active.is_(True)) if active_only else q


def is_admin_grant(db: Session, user: Optional[User]) -> bool:
    if user is None or not getattr(user, "id", None):
        return False
    return _rows(db).filter(Membership.user_id == user.id).first() is not None


def is_master(db: Session, user: Optional[User]) -> bool:
    """May run the Acquisition master workspace: the global owner, or an
    explicitly granted EvoSys Acquisition admin."""
    if user is None:
        return False
    return getattr(user, "role", None) == "god_admin" or is_admin_grant(db, user)


def require_master(db: Session, user: User) -> User:
    if not is_master(db, user):
        raise HTTPException(403, "This needs EvoSys Acquisition administrator access.")
    return user


def access_label(db: Session, user: User) -> str:
    if getattr(user, "role", None) == "god_admin":
        return "owner"
    if is_admin_grant(db, user):
        return "admin"
    return "operator"


def admins(db: Session) -> List[Dict[str, Any]]:
    rows = (db.query(Membership, User).join(User, User.id == Membership.user_id)
            .filter(Membership.scope_type == SCOPE_PRODUCT, Membership.scope_id == PRODUCT,
                    Membership.role == ROLE, Membership.is_active.is_(True)).all())
    return [{"user_id": u.id, "email": u.email, "name": u.full_name or u.email,
             "granted_at": m.created_at.isoformat() if m.created_at else None, "granted_by": m.granted_by}
            for m, u in rows]


def grant(db: Session, email: str, granted_by: Optional[str]) -> Dict[str, Any]:
    """`granted_by` is a users.id (foreign key) or None for the owner's written
    instruction (ACQUISITION_ADMIN_EMAILS) - the audit log records the source."""
    e = (email or "").strip().lower()
    u = db.query(User).filter(User.email.ilike(e)).first() if e else None
    if u is None:
        raise HTTPException(404, "No login with that email")
    if not u.is_active:
        raise HTTPException(409, "That login is not active")
    if u.role in INTERNAL_ROLES:
        return {"email": u.email, "action": "unchanged", "note": "The owner already has full access"}
    row = _rows(db, active_only=False).filter(Membership.user_id == u.id).first()
    if row is None:
        db.add(Membership(user_id=u.id, scope_type=SCOPE_PRODUCT, scope_id=PRODUCT, role=ROLE, is_active=True,
                          granted_by=granted_by, created_at=datetime.utcnow()))
        action = "granted"
    elif not row.is_active:
        row.is_active, row.granted_by = True, granted_by
        action = "reactivated"
    else:
        action = "unchanged"
    db.commit()
    return {"email": u.email, "user_id": u.id, "action": action}


def revoke(db: Session, user_id: str) -> Dict[str, Any]:
    row = _rows(db).filter(Membership.user_id == user_id).first()
    if row is None:
        raise HTTPException(404, "Not an Acquisition admin")
    row.is_active = False
    db.commit()
    return {"user_id": user_id, "action": "revoked"}


def bootstrap(db: Session) -> List[Dict[str, Any]]:
    """Grant the logins the owner named in ACQUISITION_ADMIN_EMAILS. Adds only."""
    out = []
    for e in [x.strip().lower() for x in (os.environ.get(ENV) or "").split(",") if x.strip()]:
        try:
            u = db.query(User).filter(User.email.ilike(e)).first()
            if u is None or not u.is_active or u.role in INTERNAL_ROLES:
                continue
            if _rows(db).filter(Membership.user_id == u.id).first() is not None:
                continue
            r = grant(db, e, granted_by=None)
            log.warning("AUDIT: acquisition program_admin %s for %s (from %s)", r["action"], e, ENV)
            try:
                from app.routers.audit_log_router import log_action
                log_action(db, None, u.id, "acquisition_admin_granted", "user", u.id,
                           details={"source": ENV, "action": r["action"]})
            except Exception:  # noqa: BLE001 - the grant stands; the log line above is the record
                pass
            out.append(r)
        except Exception:  # noqa: BLE001 - startup must never fail on this
            db.rollback()
            log.exception("acquisition admin bootstrap failed for %s", e)
    return out


def _dep():
    from app.deps import get_current_user, get_db

    def require_acq_master(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> User:
        """FastAPI dependency: God or an EvoSys Acquisition admin."""
        return require_master(db, user)
    return require_acq_master


require_acq_master = _dep()
