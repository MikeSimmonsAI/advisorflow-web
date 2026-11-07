"""VOLT - People & Access (Issue #535, 2026-11-06).

ONE ROW PER LOGIN PER VOLT TENANT (volt_access). No row means active.

WHAT EACH ACTION DOES
---------------------
    suspend   VOLT refuses this login (sign-in and every VOLT request).
              Sessions created up to now are never accepted by VOLT again.
              An applicant-only login (role volt_rep, no other workspace) also
              has its platform sessions revoked outright.
    restore   VOLT accepts NEW sign-ins again. `sessions_before` is kept, so
              no old session comes back. A removed staff membership is put
              back with exactly the role the removal recorded. Nothing else
              (the user row, other workspaces) is touched.
    remove    As suspend, and the VOLT staff membership (only the VOLT one) is
              deactivated. Other workspaces and the platform login are kept.
    archive   Hides an applicant from the default staff queues. NOT an access
              change: sign-in is unaffected.

WHO MAY DO IT (enforced here, on the server)
--------------------------------------------
    owner (god_admin)            everyone in the tenant except another owner
    VOLT org admin / super_admin applicants and ordinary staff; never an owner,
                                 never another org admin, never themselves
    anyone else                  nobody (managers and viewers may look)
The last active VOLT org admin cannot be suspended or removed, and nobody can
change their own access.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.models import User
from app.models.sales_models import Membership
from app.models.volt_models import VoltAccess, VoltApplication, VoltAuditEvent, VoltRep
from app.services.volt import common as C

SCOPE = "customer_org"
ADMIN_ROLES = ("org_admin", "super_admin")
ROLE_LABELS = {"org_admin": "Org admin", "super_admin": "Org admin", "manager": "Recruiting staff",
               "viewer": "View only", "spectator": "View only"}
ACTIONS = ("suspend", "restore", "remove")
STATE_LABELS = {"active": "Active", "suspended": "Suspended", "removed": "Removed"}


def _naive(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def row_for(db: Session, org_id: str, user_id: str) -> Optional[VoltAccess]:
    return (db.query(VoltAccess).filter(VoltAccess.organization_id == org_id,
                                        VoltAccess.user_id == user_id).first())


def state_of(db: Session, org_id: str, user_id: str) -> str:
    r = row_for(db, org_id, user_id)
    return r.state if r is not None else "active"


def blocked(db: Session, org_id: str, user: User) -> bool:
    if user is None or getattr(user, "role", None) == "god_admin":
        return False
    return state_of(db, org_id, user.id) != "active"


def enforce(db: Session, user: User, org_id: str, auth_session=None) -> None:
    """Every VOLT request (applicant and staff) passes through here.

    Refuses a suspended or removed login, and a session VOLT has revoked.
    The owner is never refused: owner oversight is protected."""
    if user is None or getattr(user, "role", None) == "god_admin":
        return
    r = row_for(db, org_id, user.id)
    if r is None:
        return
    if r.state != "active":
        raise HTTPException(status_code=403, detail={
            "message": "Your VOLT Marketing access is not active. Contact support@voltmarketing.live.",
            "code": "volt_access_" + r.state})
    if r.sessions_before is not None:
        started = _naive(getattr(auth_session, "created_at", None))
        # No session row to compare is refused too (fail closed).
        if started is None or started <= r.sessions_before:
            raise HTTPException(status_code=401, detail={
                "message": "Please sign in again.", "code": "volt_session_revoked"})


# ── who is who ───────────────────────────────────────────────────────────────

def _membership(db: Session, org_id: str, user_id: str) -> Optional[Membership]:
    return (db.query(Membership).filter(Membership.user_id == user_id, Membership.scope_type == SCOPE,
                                        Membership.scope_id == org_id)
            .order_by(Membership.created_at.asc()).first())


def staff_role(db: Session, org_id: str, user_id: str) -> Optional[str]:
    """The VOLT staff role of this login: the active membership's role, or for
    a removed login the role the removal recorded."""
    m = _membership(db, org_id, user_id)
    if m is not None and m.is_active:
        return m.role
    r = row_for(db, org_id, user_id)
    return r.removed_role if r is not None else None


def actor_level(db: Session, actor: User, org_id: str) -> Optional[str]:
    """owner | admin | None (may look, may not change)."""
    if getattr(actor, "role", None) == "god_admin":
        return "owner"
    if blocked(db, org_id, actor):
        return None
    m = _membership(db, org_id, actor.id)
    if m is not None and m.is_active and m.role in ADMIN_ROLES:
        return "admin"
    return None


def _active_admins(db: Session, org_id: str) -> List[str]:
    rows = (db.query(Membership, User).join(User, User.id == Membership.user_id)
            .filter(Membership.scope_type == SCOPE, Membership.scope_id == org_id, Membership.is_active.is_(True),
                    Membership.role.in_(ADMIN_ROLES), User.is_active.is_(True), User.role != "god_admin").all())
    return [u.id for _m, u in rows if state_of(db, org_id, u.id) == "active"]


def _refuse(status: int, msg: str, code: str):
    raise HTTPException(status_code=status, detail={"message": msg, "code": code})


def check_allowed(db: Session, org_id: str, actor: User, target: User, action: str) -> None:
    level = actor_level(db, actor, org_id)
    if level is None:
        _refuse(403, "Only the owner or a VOLT org admin can change access.", "not_allowed")
    if target.role == "god_admin":
        _refuse(403, "Owner oversight is protected. The owner's access can't be changed here.", "owner_protected")
    if target.id == actor.id:
        _refuse(409, "You can't change your own access.", "self_lockout")
    role = staff_role(db, org_id, target.id)
    if role in ADMIN_ROLES and level != "owner":
        _refuse(403, "Only the owner can change another org admin's access.", "admin_needs_owner")
    if action in ("suspend", "remove") and role in ADMIN_ROLES and state_of(db, org_id, target.id) == "active":
        others = [u for u in _active_admins(db, org_id) if u != target.id]
        if not others:
            _refuse(409, "This is the last active VOLT org admin. Add another org admin first.", "last_admin")


def _volt_only(db: Session, user: User) -> bool:
    """An applicant login with no other workspace: safe to revoke its platform sessions."""
    if user.role != "volt_rep":
        return False
    other = (db.query(Membership).filter(Membership.user_id == user.id, Membership.is_active.is_(True)).count())
    return other == 0


def change(db: Session, org_id: str, actor: User, target: User, action: str, reason: Optional[str]) -> Dict[str, Any]:
    action = (action or "").strip().lower()
    if action not in ACTIONS:
        _refuse(422, "Choose suspend, restore or remove.", "bad_action")
    check_allowed(db, org_id, actor, target, action)
    reason = (reason or "").strip()[:1000] or None
    r = row_for(db, org_id, target.id)
    old = r.state if r is not None else "active"
    if r is None:
        r = VoltAccess(organization_id=org_id, user_id=target.id, state="active")
        db.add(r)
    now = C.now()
    if action == "restore":
        if old == "active":
            return describe(db, org_id, target)
        if old == "removed" and r.removed_role:
            m = _membership(db, org_id, target.id)
            if m is not None and not m.is_active:
                m.is_active = True
                m.role = r.removed_role
            elif m is None:
                db.add(Membership(user_id=target.id, scope_type=SCOPE, scope_id=org_id, role=r.removed_role,
                                  is_active=True, granted_by=actor.id))
        r.state = "active"
        r.removed_role = None
    else:
        new_state = "suspended" if action == "suspend" else "removed"
        if old == new_state:
            return describe(db, org_id, target)
        r.state = new_state
        # Written the SAME way user_sessions.created_at is (an aware UTC
        # instant through the same column type), so the two compare in the
        # same frame whatever the database session's time zone is.
        r.sessions_before = datetime.now(timezone.utc)
        if action == "remove":
            m = _membership(db, org_id, target.id)
            if m is not None and m.is_active:
                r.removed_role = m.role
                m.is_active = False
        if _volt_only(db, target):
            from app.services import session_service
            session_service.revoke_all_for_user(db, target.id, commit=False)
            db.query(User).filter(User.id == target.id).update({"session_token": None}, synchronize_session=False)
    r.reason, r.changed_by, r.changed_at = reason, actor.id, now
    db.flush()
    C.audit(db, org_id, actor, "access", target.id, "access_" + action, field="volt_access", old=old, new=r.state,
            reason=reason)
    return describe(db, org_id, target)


def set_archived(db: Session, org_id: str, actor: User, rep: VoltRep, archived: bool, reason: Optional[str]) -> VoltRep:
    if actor_level(db, actor, org_id) is None:
        _refuse(403, "Only the owner or a VOLT org admin can archive applicants.", "not_allowed")
    if bool(rep.archived_at) == bool(archived):
        return rep
    rep.archived_at = C.now() if archived else None
    rep.archived_by = actor.id if archived else None
    C.audit(db, org_id, actor, "access", rep.user_id, "archived" if archived else "unarchived",
            field="archive", old=None, new=C.rep_code(rep.serial), reason=(reason or "").strip()[:1000] or None)
    return rep


# ── listing ──────────────────────────────────────────────────────────────────

def describe(db: Session, org_id: str, user: User, rep: Optional[VoltRep] = None,
             apps: Optional[int] = None) -> Dict[str, Any]:
    r = row_for(db, org_id, user.id)
    role = staff_role(db, org_id, user.id)
    if rep is None:
        rep = db.query(VoltRep).filter(VoltRep.organization_id == org_id, VoltRep.user_id == user.id).first()
    roles = []
    if role:
        roles.append(ROLE_LABELS.get(role, "Recruiting staff"))
    if rep is not None:
        roles.append("Applicant")
    if apps is None and rep is not None:
        apps = db.query(VoltApplication).filter(VoltApplication.rep_serial == rep.serial).count()
    name = C.display_name(rep) if rep is not None else (user.full_name or user.email)
    state = r.state if r is not None else "active"
    return {"user_id": user.id, "name": name, "email": user.email, "roles": roles,
            "staff_role": role, "is_admin": role in ADMIN_ROLES, "applicant": rep is not None,
            "rep_id": rep.serial if rep is not None else None, "is_test": bool(rep.is_test) if rep is not None
            else C.is_test_email(user.email),
            "applications": apps or 0, "archived": bool(rep.archived_at) if rep is not None else False,
            "archived_at": C.iso(rep.archived_at) if rep is not None else None,
            "access": state, "access_label": STATE_LABELS.get(state, state),
            "reason": r.reason if r is not None else None, "changed_at": C.iso(r.changed_at) if r is not None else None,
            "platform_active": bool(user.is_active)}


def people(db: Session, org_id: str, *, q: str = "", role: str = "", access: str = "",
           include_archived: bool = False, include_test: bool = False) -> Dict[str, Any]:
    users: Dict[str, User] = {}
    reps: Dict[str, VoltRep] = {}
    for rep, u in (db.query(VoltRep, User).join(User, User.id == VoltRep.user_id)
                   .filter(VoltRep.organization_id == org_id).all()):
        users[u.id], reps[u.id] = u, rep
    for _m, u in (db.query(Membership, User).join(User, User.id == Membership.user_id)
                  .filter(Membership.scope_type == SCOPE, Membership.scope_id == org_id).all()):
        users[u.id] = u
    for _a, u in (db.query(VoltAccess, User).join(User, User.id == VoltAccess.user_id)
                  .filter(VoltAccess.organization_id == org_id).all()):
        users[u.id] = u
    counts = dict(db.query(VoltApplication.rep_serial, func.count(VoltApplication.id))
                  .filter(VoltApplication.organization_id == org_id).group_by(VoltApplication.rep_serial).all())
    rows = []
    for uid, u in users.items():
        if u.role == "god_admin":
            continue                                      # owner oversight: not managed here
        rep = reps.get(uid)
        d = describe(db, org_id, u, rep, counts.get(rep.serial, 0) if rep is not None else 0)
        if not d["roles"]:
            continue                                      # an old membership in another role family
        rows.append(d)
    totals = {"active": sum(1 for d in rows if d["access"] == "active" and (include_test or not d["is_test"])),
              "suspended": sum(1 for d in rows if d["access"] == "suspended" and (include_test or not d["is_test"])),
              "removed": sum(1 for d in rows if d["access"] == "removed" and (include_test or not d["is_test"])),
              "archived": sum(1 for d in rows if d["archived"] and (include_test or not d["is_test"]))}
    ql = (q or "").strip().lower()
    out = []
    for d in rows:
        if not include_test and d["is_test"]:
            continue
        if d["archived"] and not include_archived:
            continue
        if ql and ql not in (d["name"] + " " + (d["email"] or "")).lower():
            continue
        if role == "applicant" and not d["applicant"]:
            continue
        if role == "staff" and not d["staff_role"]:
            continue
        if access and d["access"] != access:
            continue
        out.append(d)
    out.sort(key=lambda d: (0 if d["staff_role"] else 1, d["name"].lower()))
    return {"people": out, "totals": totals, "count": len(out)}


def history(db: Session, org_id: str, user_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    qy = db.query(VoltAuditEvent).filter(VoltAuditEvent.organization_id == org_id,
                                         VoltAuditEvent.entity_type == "access")
    if user_id:
        qy = qy.filter(VoltAuditEvent.entity_id == user_id)
    rows = qy.order_by(VoltAuditEvent.at.desc()).limit(max(1, min(limit, 200))).all()
    ids = {r.actor_user_id for r in rows if r.actor_user_id} | {r.entity_id for r in rows}
    names = {u.id: (u.full_name or u.email) for u in db.query(User).filter(User.id.in_(ids)).all()} if ids else {}
    return [{"at": C.iso(r.at), "action": r.action, "actor": names.get(r.actor_user_id, "System"),
             "target": names.get(r.entity_id, r.entity_id), "target_user_id": r.entity_id,
             "scope": "VOLT Marketing", "old": r.old_value, "new": r.new_value, "reason": r.reason} for r in rows]


# ── seed: authorized VOLT org admins named by the deployment ────────────────

def ensure_admins(db: Session, org_id: str, emails: List[str]) -> List[str]:
    """Grant VOLT org-admin membership to EXISTING logins named in the
    deployment's VOLT_ORG_ADMIN_EMAILS. Never creates an account, never touches
    another workspace, and never re-grants a login whose VOLT access was
    deliberately suspended or removed. Idempotent."""
    granted = []
    for raw in emails:
        email = (raw or "").strip().lower()
        if not email:
            continue
        u = db.query(User).filter(func.lower(User.email) == email).first()
        if u is None or not u.is_active or u.role in ("god_admin", "volt_rep"):
            continue
        if row_for(db, org_id, u.id) is not None:
            continue                                     # an explicit VOLT decision stands
        m = _membership(db, org_id, u.id)
        if m is not None and m.is_active:
            continue
        if m is not None:
            m.is_active, m.role = True, "org_admin"
        else:
            db.add(Membership(user_id=u.id, scope_type=SCOPE, scope_id=org_id, role="org_admin", is_active=True))
        db.flush()
        C.audit(db, org_id, None, "access", u.id, "access_granted", field="volt_role", new="org_admin",
                reason="Authorized VOLT org admin (deployment setting)")
        granted.append(u.id)
    return granted
