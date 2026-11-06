"""Who works in a gold & jewelry workspace - read from the platform's memberships.

The team IS the set of active customer_org Memberships on the organization
(plus legacy users whose home organization it is). Roles are the platform's
own workspace roles, presented in the store's words:

    org_admin / super_admin -> Owner / Admin
    manager                 -> Manager
    advisor / viewer        -> Staff
    spectator               -> View only

Nobody is created here and no role is granted here: inviting and changing
staff goes through the platform's existing workspace access tools.
"""
from typing import Dict, List, Set

from sqlalchemy.orm import Session

from app.models.models import User
from app.models.sales_models import Membership

ROLE_LABEL = {"org_admin": "Owner / Admin", "super_admin": "Owner / Admin", "manager": "Manager",
              "advisor": "Staff", "viewer": "Staff", "spectator": "View only"}


def members(db: Session, org_id: str) -> List[Dict]:
    out: Dict[str, Dict] = {}
    rows = (db.query(Membership, User).join(User, User.id == Membership.user_id)
            .filter(Membership.scope_type == "customer_org", Membership.scope_id == org_id,
                    Membership.is_active.is_(True), User.is_active.is_(True)).all())
    for m, u in rows:
        if u.role == "god_admin":
            continue
        out[u.id] = {"id": u.id, "name": u.full_name or u.email, "email": u.email, "role": m.role,
                     "role_label": ROLE_LABEL.get(m.role, "Staff")}
    for u in (db.query(User).filter(User.organization_id == org_id, User.is_active.is_(True)).all()):
        if u.role == "god_admin" or u.id in out:
            continue
        out[u.id] = {"id": u.id, "name": u.full_name or u.email, "email": u.email, "role": u.role,
                     "role_label": ROLE_LABEL.get(u.role, "Staff")}
    return sorted(out.values(), key=lambda x: (x["name"] or "").lower())


def member_ids(db: Session, org_id: str) -> Set[str]:
    return {m["id"] for m in members(db, org_id)}
