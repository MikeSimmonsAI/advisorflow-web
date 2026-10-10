"""WHO MAY USE A PLATFORM PRODUCT - named logins, not whole workspaces.

EvoSys Wholesale used to be open to every person in any workspace entitled to
`wholesale_real_estate` (or with no feature list at all). Mike's rule
(2026-10-09): the Wholesaler is for the logins he names, plus God mode, and
nobody else.

A grant is a Membership row: scope_type="product", scope_id="wholesale",
role="member". Revoking deactivates the row; nothing is deleted.

THE LOCK TURNS ON WITH THE FIRST GRANT AND STAYS ON. Before any login was
ever named, the old workspace rule still decides, so deploying this changes
nothing for anyone until a login is named. Revoking every login afterwards
leaves only God mode; it never reopens the product. From the first grant on: god_admin, or an active grant,
or refused - on every Wholesale route (entitlements.require_feature) and in the
navigation (branding `platform.offered.wholesale`).
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.models import User
from app.models.sales_models import Membership, SCOPE_PRODUCT

WHOLESALE = "wholesale"
WHOLESALE_FEATURE = "wholesale_real_estate"
PRODUCTS = (WHOLESALE,)
MEMBER = "member"


def _rows(db: Session, product: str, active_only: bool = True):
    q = db.query(Membership).filter(Membership.scope_type == SCOPE_PRODUCT, Membership.scope_id == product)
    if active_only:
        q = q.filter(Membership.is_active.is_(True))
    return q


def lock_on(db: Session, product: str = WHOLESALE) -> bool:
    """True once any login has EVER been named for the product. Revoking
    everyone leaves the lock on (God mode only); it never reopens the product."""
    return _rows(db, product, active_only=False).first() is not None


def may_use(db: Session, user: Optional[User], product: str = WHOLESALE) -> bool:
    if user is None:
        return False
    if getattr(user, "role", None) == "god_admin":
        return True
    if not lock_on(db, product):
        return True                                    # not configured yet: unchanged behaviour
    return _rows(db, product).filter(Membership.user_id == user.id).first() is not None


def holders(db: Session, product: str = WHOLESALE) -> List[Dict]:
    out = []
    for m in _rows(db, product, active_only=False).order_by(Membership.created_at.asc()).all():
        u = db.query(User).filter(User.id == m.user_id).first()
        out.append({"user_id": m.user_id, "email": getattr(u, "email", None),
                    "name": getattr(u, "full_name", None), "active": bool(m.is_active),
                    "granted_at": m.created_at.isoformat() + "Z" if m.created_at else None})
    return out


def _user_by_email(db: Session, email: str) -> User:
    e = (email or "").strip().lower()
    if not e:
        raise ValueError("an email is required")
    u = db.query(User).filter(User.email.ilike(e)).first()
    if u is None:
        raise LookupError("no login with the email %s" % e)
    return u


def grant(db: Session, email: str, *, granted_by: Optional[str] = None, product: str = WHOLESALE) -> Dict:
    """Name a login for the product. Idempotent; reactivates a revoked grant."""
    if product not in PRODUCTS:
        raise ValueError("unknown product")
    u = _user_by_email(db, email)
    row = _rows(db, product, active_only=False).filter(Membership.user_id == u.id).first()
    if row is None:
        row = Membership(user_id=u.id, scope_type=SCOPE_PRODUCT, scope_id=product, role=MEMBER,
                         is_active=True, granted_by=granted_by, created_at=datetime.utcnow())
        db.add(row)
        action = "granted"
    elif not row.is_active:
        row.is_active, row.granted_by = True, granted_by
        action = "reactivated"
    else:
        action = "unchanged"
    db.commit()
    return {"email": u.email, "action": action, "lock_on": True}


def revoke(db: Session, email: str, *, product: str = WHOLESALE) -> Dict:
    u = _user_by_email(db, email)
    row = _rows(db, product).filter(Membership.user_id == u.id).first()
    if row is None:
        return {"email": u.email, "action": "not a holder", "lock_on": lock_on(db, product)}
    row.is_active = False
    db.commit()
    return {"email": u.email, "action": "revoked", "lock_on": True}
