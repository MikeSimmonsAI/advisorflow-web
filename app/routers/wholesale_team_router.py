"""/wholesale/team - the Wholesale team: roles and who reports to whom.

    GET    /wholesale/team                     the team, the roles, people not on it yet
    GET    /wholesale/team/me                  my own team role (any team member)
    POST   /wholesale/team/members             {user_id, role, reports_to_user_id?, confirm_lock?}
    DELETE /wholesale/team/members/{user_id}   take off the team (login and work are kept)

Admins of the acting workspace only (org_admin, or super/god), except /me.
New logins are made with the existing POST /admin/users (one-time setup link,
the plan's seat limit applies), then put on the team here.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, require_not_observation, require_tenant_user
from app.models.models import User
from app.services import product_access as PA
from app.services import wholesale_service as svc
from app.services import wholesale_team as T
from app.services.entitlements import require_feature

router = APIRouter(prefix="/wholesale/team", tags=["wholesale-team"],
                   dependencies=[Depends(require_feature("wholesale_real_estate"))])


def _admin(db: Session, user: User) -> None:
    if (getattr(user, "role", None) or "").lower() in ("super_admin", "god_admin"):
        return
    from app.services.lead_scope import effective_role
    if (effective_role(user, db) or "").lower() in ("org_admin", "super_admin"):
        return
    raise HTTPException(403, "Only an admin can change the Wholesale team.")


class MemberIn(BaseModel):
    user_id: str
    role: str
    reports_to_user_id: Optional[str] = None
    confirm_lock: bool = False


@router.get("/me")
def my_role(db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    role = T.role_of(db, user)
    return {"role": role, "role_label": T.ROLE_LABELS.get(role) if role else None,
            "wholesale_only": T.is_wholesale_only(db, user), "field": T.is_field(db, user)}


class RulesIn(BaseModel):
    offer_limit_pct: int


@router.get("/rules")
def get_rules(db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    _admin(db, user)
    from app.services import wholesale_field_offers as O
    out = O.rules_json(O.rules(db, svc.write_org_id(db, user)))
    db.commit()
    return out


@router.put("/rules", dependencies=[Depends(require_not_observation)])
def put_rules(body: RulesIn, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    _admin(db, user)
    from app.services import wholesale_field_offers as O
    return O.set_rules(db, svc.write_org_id(db, user), user, body.offer_limit_pct)


@router.get("")
def get_team(db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    _admin(db, user)
    return T.team(db, svc.write_org_id(db, user))


@router.post("/members", dependencies=[Depends(require_not_observation)])
def put_member(body: MemberIn, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    _admin(db, user)
    if not PA.lock_on(db) and not body.confirm_lock:
        raise HTTPException(409, "Adding the first team member turns on named logins: from then on only the "
                                 "people on this team can open Wholesale. Confirm to continue.")
    try:
        return T.add_or_update(db, svc.write_org_id(db, user), user, body.user_id, body.role,
                               body.reports_to_user_id)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/members/{user_id}", dependencies=[Depends(require_not_observation)])
def delete_member(user_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    _admin(db, user)
    try:
        return T.remove(db, svc.write_org_id(db, user), user, user_id)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
