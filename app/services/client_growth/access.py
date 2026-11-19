"""Who may use Client Growth - decided here and nowhere else.

    owner   god_admin (God Mode). Also the only one with God Mode controls:
            access grants and template switches (/god/client-growth/*,
            guarded by the existing require_god).
    staff   the EvoSys Pro sales program owners (existing brand grant
            `sales_program_owner` - platform@evosyspro.live and Michael
            Schlueter today), or anyone God Mode granted the brand-scoped
            `client_growth` capability on EvoSys Pro Sales.
    None    everybody else: sales reps and managers, customer users, insurance
            staff, applicants, prospects.

Nothing in a request can raise this answer. The Program Access card is shown
from the same function the API enforces.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models.models import User
from app.models.sales_models import BrandSalesOrg

CAPABILITY = "client_growth"
PATH = "/client-growth"


def brand(db: Session) -> Optional[BrandSalesOrg]:
    """The EvoSys Pro sales team whose prospects Client Growth works with."""
    from app.services import sales_team
    return sales_team.program_brand(db)


def level(db: Session, user: Optional[User]) -> Optional[str]:
    if user is None or not getattr(user, "is_active", False):
        return None
    if getattr(user, "role", None) == "god_admin":
        return "owner"
    b = brand(db)
    if b is None:
        return None
    from app.services import capabilities as caps
    from app.services import sales_team
    if b.id in caps.brands_with_capability(db, user, sales_team.CAP_OWNER):
        return "staff"
    if b.id in caps.brands_with_capability(db, user, CAPABILITY):
        return "staff"
    return None


class Ctx:
    def __init__(self, user: User, bso: BrandSalesOrg, level: str):
        self.user, self.bso, self.level = user, bso, level

    @property
    def is_owner(self) -> bool:
        return self.level == "owner"


def require(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Ctx:
    lv = level(db, user)
    b = brand(db) if lv else None
    if lv is None or b is None:
        raise HTTPException(403, "Client Growth is for authorized EvoSys Pro platform administrators.")
    return Ctx(user, b, lv)


def contexts_for(db: Session, user: User) -> List[Dict[str, Any]]:
    """The Program Access card - one, only for someone who passes `level`."""
    try:
        lv = level(db, user)
        if not lv:
            return []
        return [{"type": "client_growth", "organization_id": "client-growth", "organization_name": "EvoSys Pro",
                 "path": PATH, "access": lv}]
    except Exception:  # pragma: no cover - never let a hint break login
        return []


def people_with_access(db: Session) -> List[Dict[str, Any]]:
    """Everyone who can open Client Growth right now, and why."""
    from app.models.models import UserCapabilityGrant
    from app.models.sales_models import SCOPE_BRAND_SALES_ORG
    from app.services import sales_team
    out: Dict[str, Dict[str, Any]] = {}
    for u in db.query(User).filter(User.role == "god_admin", User.is_active.is_(True)).all():
        out[u.id] = {"user_id": u.id, "name": u.full_name, "email": u.email, "via": "God Mode",
                     "removable": False}
    b = brand(db)
    if b is not None:
        rows = (db.query(UserCapabilityGrant, User).join(User, User.id == UserCapabilityGrant.user_id)
                .filter(UserCapabilityGrant.scope_type == SCOPE_BRAND_SALES_ORG,
                        UserCapabilityGrant.scope_id == b.id, UserCapabilityGrant.is_active.is_(True),
                        UserCapabilityGrant.capability.in_((sales_team.CAP_OWNER, CAPABILITY)),
                        User.is_active.is_(True)).all())
        for g, u in rows:
            if u.id in out and out[u.id]["via"] == "God Mode":
                continue
            entry = out.setdefault(u.id, {"user_id": u.id, "name": u.full_name, "email": u.email,
                                          "via": None, "removable": False, "grant": False})
            if g.capability == sales_team.CAP_OWNER:
                entry["via"] = "Sales program owner"
            else:
                entry["grant"] = True
                entry["via"] = entry["via"] or "Client Growth access"
                entry["removable"] = True
    return sorted(out.values(), key=lambda r: (r["via"] != "God Mode", (r["name"] or r["email"] or "").lower()))


def set_grant(db: Session, actor: User, target: User, on: bool) -> List[str]:
    """Add or remove ONLY the client_growth brand grant, keeping the person's
    other brand grants exactly as they are. Audited by set_brand_grants."""
    from app.services import capabilities as caps
    b = brand(db)
    if b is None:
        raise HTTPException(409, "The EvoSys Pro sales team was not found.")
    if getattr(target, "role", None) == "god_admin":
        raise HTTPException(400, "God Mode already has Client Growth.")
    current = caps.brand_grants_for(db, target.id, b.id)
    wanted = sorted(set(current) | {CAPABILITY}) if on else [k for k in current if k != CAPABILITY]
    return caps.set_brand_grants(db, target, b.id, b.name, actor, wanted)
