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
    raise_limit_pct: Optional[int] = None
    commission_pct: Optional[float] = None


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
    return O.set_rules(db, svc.write_org_id(db, user), user, body.offer_limit_pct, body.raise_limit_pct,
                       body.commission_pct)


@router.get("")
def get_team(db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    _admin(db, user)
    return T.team(db, svc.write_org_id(db, user))


@router.post("/members", dependencies=[Depends(require_not_observation)])
def put_member(body: MemberIn, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    _admin(db, user)
    org_id = svc.write_org_id(db, user)
    # Only the legacy (EvoSys) seat turns a lock on for everyone; a second
    # tenant's own seats never change who else can open Wholesale (#272).
    if T.seat_key_for_org(db, org_id) == PA.WHOLESALE and not PA.lock_on(db) and not body.confirm_lock:
        raise HTTPException(409, "Adding the first team member turns on named logins: from then on only the "
                                 "people on this team can open Wholesale. Confirm to continue.")
    try:
        return T.add_or_update(db, org_id, user, body.user_id, body.role,
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


# ═══════════════════════ #439 CUSTOM ACCESS (profiles, people, invitations) ═══
#
# Same workspace rule as everything above (svc.write_org_id). Who may use it:
# the workspace's admins (and God/super), or a person whose own custom access
# includes "Team and access administration" - and they can never give more
# than they hold (wholesale_access.check_delegation). The server fence has
# already checked a custom-access login's module/action before any of this runs.
from typing import Any, Dict  # noqa: E402

from fastapi import Body, Request  # noqa: E402

from app.services import wholesale_access as A  # noqa: E402
from app.services import wholesale_team_access as TA  # noqa: E402


def _access_admin(db: Session, user: User, org_id: str, need: str = "view") -> None:
    try:
        _admin(db, user)
        return
    except HTTPException:
        pass
    if T.role_of(db, user, org_id) == T.CUSTOM:
        eff = A.effective(db, user, org_id)
        if A.allows(eff, "team_admin", need):
            return
    raise HTTPException(403, "Only an admin can change the Wholesale team.")


def _ws(db: Session, user: User, need: str = "view") -> str:
    org_id = svc.write_org_id(db, user)
    _access_admin(db, user, org_id, need)
    return org_id


def _base(request: Request, given: Optional[str]) -> str:
    return (given or request.headers.get("origin") or "").rstrip("/")


def _run(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except TA.NeedsConfirmation as exc:
        raise HTTPException(409, {"message": str(exc), "impact": exc.impact, "needs_confirmation": True})
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except TA.AccessError as exc:
        raise HTTPException(400, str(exc))


@router.get("/access/catalog")
def access_catalog(request: Request, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    org_id = _ws(db, user)
    cap, cap_scope = A.ceiling(db, user, org_id)
    return {"modules": A.catalog(request.app.routes),
            "templates": [{"key": k, "name": t["name"], "description": t["description"],
                           "record_scope": t["record_scope"], "permissions": A.clean(t["permissions"], A.catalog(request.app.routes))}
                          for k, t in A.TEMPLATES.items()],
            "scopes": [{"key": s, "label": A.SCOPE_LABELS[s]} for s in A.SCOPES],
            "your_ceiling": {"permissions": cap, "record_scope": cap_scope},
            "never": ["God Mode", "Any other workspace or brand", "Billing and plan",
                      "Platform settings and secrets", "The advisor / CRM side"]}


@router.get("/directory")
def directory(db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    org_id = _ws(db, user)
    base = T.team(db, org_id)
    people = []
    for m in base["members"]:
        u = T.in_workspace(db, org_id, m["user_id"])
        acc = TA.member_access(db, org_id, m["user_id"], user)
        people.append({**m, "access": acc, "protected": acc.get("protected"), "owned_work": TA.owned_work(db, org_id, m["user_id"]),
                       "last_active_at": (u.last_login_at.isoformat() + "Z") if u is not None and u.last_login_at else None})
    suspended = []
    for o in base["not_on_team"]:
        if o.get("was_member"):
            acc = TA.member_access(db, org_id, o["user_id"], user)
            if acc["status"] == "suspended":
                suspended.append({**o, "access": acc, "protected": acc.get("protected")})
    return {"members": people, "suspended": suspended, "not_on_team": [o for o in base["not_on_team"]
                                                                       if o["user_id"] not in {s["user_id"] for s in suspended}],
            "invites": TA.invites(db, org_id), "profiles": TA.profiles(db, org_id),
            "lock_on": base["lock_on"], "legacy_roles": base["roles"]}


@router.get("/access/profiles")
def list_profiles(include_archived: bool = False, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    return {"items": TA.profiles(db, _ws(db, user), include_archived)}


@router.post("/access/profiles", dependencies=[Depends(require_not_observation)])
def create_profile(body: Dict[str, Any] = Body(...), db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    org_id = _ws(db, user, "manage")
    return _run(TA.create_profile, db, org_id, user, name=body.get("name"), template_key=body.get("template_key"),
                permissions=body.get("permissions"), record_scope=body.get("record_scope"),
                description=body.get("description"))


@router.post("/access/profiles/{profile_id}/duplicate", dependencies=[Depends(require_not_observation)])
def duplicate_profile(profile_id: str, body: Dict[str, Any] = Body(default={}), db: Session = Depends(get_db),
                      user: User = Depends(require_tenant_user)):
    return _run(TA.duplicate_profile, db, _ws(db, user, "manage"), user, profile_id, (body or {}).get("name"))


@router.put("/access/profiles/{profile_id}", dependencies=[Depends(require_not_observation)])
def update_profile(profile_id: str, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                   user: User = Depends(require_tenant_user)):
    return _run(TA.update_profile, db, _ws(db, user, "manage"), user, profile_id, name=body.get("name"),
                permissions=body.get("permissions"), record_scope=body.get("record_scope"),
                description=body.get("description"), confirm=bool(body.get("confirm")),
                keep_existing_members=bool(body.get("keep_existing_members")))


@router.post("/access/profiles/{profile_id}/retire", dependencies=[Depends(require_not_observation)])
def retire_profile(profile_id: str, body: Dict[str, Any] = Body(default={}), db: Session = Depends(get_db),
                   user: User = Depends(require_tenant_user)):
    return _run(TA.retire_profile, db, _ws(db, user, "manage"), user, profile_id, (body or {}).get("move_to"))


@router.post("/access/preview")
def preview(body: Dict[str, Any] = Body(...), db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    return _run(TA.preview, db, _ws(db, user), profile_id=body.get("profile_id"), permissions=body.get("permissions"),
                grants=body.get("grants"), denies=body.get("denies"), record_scope=body.get("record_scope"))


@router.get("/members/{user_id}/access")
def get_member_access(user_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    org_id = _ws(db, user)
    out = _run(TA.member_access, db, org_id, user_id, user)
    out["owned_work"] = TA.owned_work(db, org_id, user_id)
    return out


@router.put("/members/{user_id}/access", dependencies=[Depends(require_not_observation)])
def put_member_access(user_id: str, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                      user: User = Depends(require_tenant_user)):
    return _run(TA.set_access, db, _ws(db, user, "manage"), user, user_id, profile_id=body.get("profile_id"),
                grants=body.get("grants"), denies=body.get("denies"), record_scope=body.get("record_scope"),
                reports_to_user_id=body.get("reports_to_user_id"))


@router.post("/members/{user_id}/suspend", dependencies=[Depends(require_not_observation)])
def suspend_member(user_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    return _run(TA.set_status, db, _ws(db, user, "manage"), user, user_id, False)


@router.post("/members/{user_id}/reactivate", dependencies=[Depends(require_not_observation)])
def reactivate_member(user_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    return _run(TA.set_status, db, _ws(db, user, "manage"), user, user_id, True)


@router.post("/members/{user_id}/remove", dependencies=[Depends(require_not_observation)])
def remove_member(user_id: str, body: Dict[str, Any] = Body(default={}), db: Session = Depends(get_db),
                  user: User = Depends(require_tenant_user)):
    return _run(TA.remove, db, _ws(db, user, "manage"), user, user_id, (body or {}).get("reassign_to"))


@router.get("/invites")
def list_invites(db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    return {"items": TA.invites(db, _ws(db, user))}


@router.post("/invites", dependencies=[Depends(require_not_observation)])
def create_invite(request: Request, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                  user: User = Depends(require_tenant_user)):
    org_id = _ws(db, user, "manage")
    return _run(TA.invite, db, org_id, user, email=body.get("email"), full_name=body.get("full_name"),
                profile_id=body.get("profile_id"), grants=body.get("grants"), denies=body.get("denies"),
                record_scope=body.get("record_scope"), reports_to_user_id=body.get("reports_to_user_id"),
                base_url=_base(request, body.get("base_url")), send=bool(body.get("send")))


@router.post("/invites/{invite_id}/resend", dependencies=[Depends(require_not_observation)])
def resend_invite(invite_id: str, request: Request, body: Dict[str, Any] = Body(default={}),
                  db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    return _run(TA.resend, db, _ws(db, user, "manage"), user, invite_id, _base(request, (body or {}).get("base_url")),
                bool((body or {}).get("send")))


@router.post("/invites/{invite_id}/cancel", dependencies=[Depends(require_not_observation)])
def cancel_invite(invite_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    return _run(TA.cancel, db, _ws(db, user, "manage"), user, invite_id)


@router.get("/history")
def access_history(db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    return {"items": TA.history(db, _ws(db, user))}


# The person ACCEPTING has no seat in the workspace yet, so this lives outside
# the /wholesale feature gate. It still needs a signed-in login, and the
# invitation must name exactly that login.
accept_router = APIRouter(prefix="/wholesale-invites", tags=["wholesale-team"])


@accept_router.post("/accept")
def accept_invite(body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                  user: User = Depends(require_tenant_user)):
    try:
        return TA.accept(db, user, (body or {}).get("token"))
    except (TA.AccessError, LookupError) as exc:
        raise HTTPException(400, str(exc))
