"""Running the Wholesale team's access: profiles, each person's access,
invitations, suspension, removal and the audit trail (#439).

Everything here acts on ONE workspace - the one the request is in - and
re-uses what already exists:
  * the seat         app/services/product_access.py (+ wholesale_team.py)
  * new logins       the same rules as POST /admin/users: the plan's seat limit,
                     an unknowable password, a one-time setup link
                     (app/services/staff_activation.py)
  * other workspaces app/services/workspace_access.grant_workspace_membership
  * sending email    the brand's own sending identity (launch_invitation) - and
                     only when the owner presses Send
  * the audit trail  audit_log_entries, actions "wholesale_access.*"

Hard limits, all checked on the server:
  * nobody gives anyone more than they hold (wholesale_access.check_delegation);
  * an admin, super admin, God or platform login can never be given a custom
    profile or be invited into a workspace by this screen;
  * a person's own access can't be changed by themselves.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.models import Organization, User
from app.models.sales_models import Membership, SCOPE_PRODUCT
from app.models.wholesale_access_models import WholesaleAccessProfile, WholesaleMemberAccess, WholesaleTeamInvite
from app.services import product_access as PA
from app.services import wholesale_access as A
from app.services import wholesale_team as T

INVITE_DAYS = 7
PENDING, ACCEPTED, CANCELLED = "pending", "accepted", "cancelled"
NEW_LOGIN, EXISTING_LOGIN = "new_login", "existing_login"
_ELEVATED = ("org_admin", "super_admin", "god_admin")


class AccessError(ValueError):
    """A refusal to show the person, in plain words."""


class NeedsConfirmation(Exception):
    def __init__(self, message: str, impact: Dict[str, Any]):
        super().__init__(message)
        self.impact = impact


def _audit(db: Session, org_id: str, actor: User, action: str, target_type: str, target_id: str,
           before: Any = None, after: Any = None, note: Optional[str] = None) -> None:
    from app.routers.audit_log_router import log_action
    log_action(db, org_id, actor.id, "wholesale_access." + action, target_type, target_id,
               before=before, after=after, note=note, commit=False)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _scope(db: Session, org_id: str) -> str:
    return T.seat_key_for_org(db, org_id)


def _catalog() -> List[Dict[str, Any]]:
    from app.main import app
    return A.catalog(app.routes)


def _clean(perms, add_view: bool = True) -> Dict[str, List[str]]:
    return A.clean(perms, _catalog(), add_view=add_view)


def _guard_delegation(db, org_id, actor, perms, scope_key):
    try:
        A.check_delegation(db, actor, org_id, perms, scope_key)
    except PermissionError as exc:
        _audit(db, org_id, actor, "escalation_denied", "user", actor.id, after={"asked": perms, "scope": scope_key},
               note=str(exc))
        db.commit()
        raise AccessError(str(exc))


# ── profiles ────────────────────────────────────────────────────────────────
def profiles(db: Session, org_id: str, include_archived: bool = False) -> List[Dict[str, Any]]:
    q = db.query(WholesaleAccessProfile).filter(WholesaleAccessProfile.seat_scope == _scope(db, org_id),
                                                WholesaleAccessProfile.organization_id == org_id)
    if not include_archived:
        q = q.filter(WholesaleAccessProfile.is_archived.is_(False))
    return [A.profile_json(db, p) for p in q.order_by(WholesaleAccessProfile.name.asc()).all()]


def _profile(db: Session, org_id: str, profile_id: Optional[str]) -> Optional[WholesaleAccessProfile]:
    if not profile_id:
        return None
    p = db.query(WholesaleAccessProfile).filter(WholesaleAccessProfile.id == profile_id,
                                                WholesaleAccessProfile.seat_scope == _scope(db, org_id),
                                                WholesaleAccessProfile.organization_id == org_id).first()
    if p is None:
        raise LookupError("That access profile isn't in this workspace.")
    return p


def create_profile(db: Session, org_id: str, actor: User, *, name: str, template_key: Optional[str] = None,
                   permissions: Optional[Dict] = None, record_scope: Optional[str] = None,
                   description: Optional[str] = None) -> Dict[str, Any]:
    name = (name or "").strip()[:80]
    if not name:
        raise AccessError("Give the profile a name.")
    tpl = A.TEMPLATES.get(template_key or "") if template_key else None
    if template_key and tpl is None:
        raise AccessError("Unknown starter template.")
    perms = _clean(permissions if permissions is not None else (tpl or {}).get("permissions", {}))
    rs = record_scope or (tpl or {}).get("record_scope") or A.ASSIGNED
    if rs not in A.SCOPES:
        raise AccessError("Unknown record visibility.")
    _guard_delegation(db, org_id, actor, perms, rs)
    if _name_taken(db, org_id, name):
        raise AccessError("A profile called %r already exists." % name)
    p = WholesaleAccessProfile(organization_id=org_id, seat_scope=_scope(db, org_id), name=name,
                               description=(description if description is not None else (tpl or {}).get("description")),
                               template_key=template_key, permissions_json=json.dumps(perms), record_scope=rs,
                               created_by_id=actor.id, updated_by_id=actor.id)
    db.add(p)
    db.flush()
    _audit(db, org_id, actor, "profile_created", "access_profile", p.id, after={"name": name, "permissions": perms, "record_scope": rs})
    db.commit()
    return A.profile_json(db, p)


def _name_taken(db, org_id, name, but: Optional[str] = None) -> bool:
    q = db.query(WholesaleAccessProfile.id).filter(WholesaleAccessProfile.seat_scope == _scope(db, org_id),
                                                   WholesaleAccessProfile.is_archived.is_(False),
                                                   func.lower(WholesaleAccessProfile.name) == name.lower())
    if but:
        q = q.filter(WholesaleAccessProfile.id != but)
    return q.first() is not None


def duplicate_profile(db: Session, org_id: str, actor: User, profile_id: str, name: Optional[str] = None) -> Dict[str, Any]:
    src = _profile(db, org_id, profile_id)
    base = (name or "%s (copy)" % src.name).strip()[:80]
    n, candidate = 2, base
    while _name_taken(db, org_id, candidate):
        candidate, n = "%s %d" % (base, n), n + 1
    return create_profile(db, org_id, actor, name=candidate, permissions=json.loads(src.permissions_json or "{}"),
                          record_scope=src.record_scope, description=src.description)


def _members_of(db, org_id, profile_id) -> List[WholesaleMemberAccess]:
    return (db.query(WholesaleMemberAccess)
            .filter(WholesaleMemberAccess.seat_scope == _scope(db, org_id),
                    WholesaleMemberAccess.profile_id == profile_id).all())


def update_profile(db: Session, org_id: str, actor: User, profile_id: str, *, name: Optional[str] = None,
                   permissions: Optional[Dict] = None, record_scope: Optional[str] = None,
                   description: Optional[str] = None, confirm: bool = False,
                   keep_existing_members: bool = False) -> Dict[str, Any]:
    """Change a shared profile. If the change GIVES MORE to people already on
    it, nothing happens until the owner confirms - or chooses to leave those
    people exactly where they are (they move to a frozen copy)."""
    p = _profile(db, org_id, profile_id)
    if p.is_archived:
        raise AccessError("This profile is retired. Duplicate it to use it again.")
    old_perms = json.loads(p.permissions_json or "{}")
    new_perms = _clean(permissions) if permissions is not None else old_perms
    rs = record_scope or p.record_scope
    if rs not in A.SCOPES:
        raise AccessError("Unknown record visibility.")
    _guard_delegation(db, org_id, actor, new_perms, rs)
    if name is not None:
        name = name.strip()[:80]
        if not name:
            raise AccessError("Give the profile a name.")
        if _name_taken(db, org_id, name, but=p.id):
            raise AccessError("A profile called %r already exists." % name)
    members = _members_of(db, org_id, p.id)
    added = A.subset(new_perms, old_perms)
    wider = A._SCOPE_RANK[rs] > A._SCOPE_RANK.get(p.record_scope, 0)
    removed = A.subset(old_perms, new_perms)
    impact = {"members": len(members), "adds": added, "removes": removed,
              "wider_records": wider, "record_scope": rs}
    if members and (added or wider) and not (confirm or keep_existing_members):
        raise NeedsConfirmation(
            "This gives %d %s more access: %s%s. Confirm, or keep them on their current access."
            % (len(members), "person" if len(members) == 1 else "people", ", ".join(added[:5]) or "wider record visibility",
               "..." if len(added) > 5 else ""), impact)
    before = A.profile_json(db, p)
    if members and keep_existing_members and (added or wider or removed):
        frozen = WholesaleAccessProfile(organization_id=org_id, seat_scope=p.seat_scope,
                                        name=_free_name(db, org_id, "%s (before %s)" % (p.name, datetime.utcnow().strftime("%b %d"))),
                                        description=p.description, template_key=p.template_key,
                                        permissions_json=p.permissions_json, record_scope=p.record_scope,
                                        created_by_id=actor.id, updated_by_id=actor.id)
        db.add(frozen)
        db.flush()
        for m in members:
            m.profile_id = frozen.id
        impact["kept_on"] = frozen.name
    p.permissions_json = json.dumps(new_perms)
    p.record_scope = rs
    if name is not None:
        p.name = name
    if description is not None:
        p.description = description
    p.version = (p.version or 1) + 1
    p.updated_by_id = actor.id
    _audit(db, org_id, actor, "profile_updated", "access_profile", p.id, before=before,
           after={"permissions": new_perms, "record_scope": rs, "impact": impact})
    db.commit()
    return dict(A.profile_json(db, p), impact=impact)


def _free_name(db, org_id, base):
    n, candidate = 2, base[:80]
    while _name_taken(db, org_id, candidate):
        candidate, n = "%s %d" % (base[:76], n), n + 1
    return candidate


def retire_profile(db: Session, org_id: str, actor: User, profile_id: str, move_to: Optional[str] = None) -> Dict[str, Any]:
    p = _profile(db, org_id, profile_id)
    members = _members_of(db, org_id, p.id)
    pending = (db.query(WholesaleTeamInvite).filter(WholesaleTeamInvite.profile_id == p.id,
                                                    WholesaleTeamInvite.status == PENDING).count())
    if (members or pending) and not move_to:
        raise AccessError("%d %s and %d pending invitation(s) use this profile. Pick a profile to move them to first."
                          % (len(members), "person" if len(members) == 1 else "people", pending))
    target = _profile(db, org_id, move_to) if move_to else None
    if target is not None:
        if target.id == p.id or target.is_archived:
            raise AccessError("Pick a different, active profile.")
        for m in members:
            m.profile_id = target.id
        for inv in db.query(WholesaleTeamInvite).filter(WholesaleTeamInvite.profile_id == p.id,
                                                        WholesaleTeamInvite.status == PENDING).all():
            inv.profile_id = target.id
    p.is_archived = True
    p.updated_by_id = actor.id
    _audit(db, org_id, actor, "profile_retired", "access_profile", p.id,
           after={"moved_to": target.name if target else None, "members": len(members)})
    db.commit()
    return A.profile_json(db, p)


# ── one person's access ─────────────────────────────────────────────────────
def preview(db: Session, org_id: str, *, profile_id: Optional[str] = None, permissions: Optional[Dict] = None,
            grants: Optional[Dict] = None, denies: Optional[Dict] = None,
            record_scope: Optional[str] = None) -> Dict[str, Any]:
    prof = _profile(db, org_id, profile_id) if profile_id else None
    base = json.loads(prof.permissions_json or "{}") if prof else _clean(permissions or {})
    perms = A.combine(base, _clean(grants or {}), _clean(denies or {}, add_view=False))
    rs = record_scope or (prof.record_scope if prof else A.ASSIGNED)
    cat = _catalog()
    lines = []
    for c in cat:
        acts = perms.get(c["key"], [])
        lines.append({"module": c["key"], "label": c["label"], "group": c["group"], "actions": acts,
                      "summary": (", ".join(A.ACTION_LABELS[a] for a in acts) if acts else "No access"),
                      "record_scoped": c["record_scoped"]})
    return {"permissions": perms, "record_scope": rs, "record_scope_label": A.SCOPE_LABELS.get(rs),
            "modules": lines,
            "never": ["God Mode", "Any other workspace or brand", "Billing and plan", "Platform settings and secrets",
                      "The advisor / CRM side"]}


def _member_user(db, org_id, user_id) -> User:
    u = T.in_workspace(db, org_id, user_id)
    if u is None:
        raise LookupError("No such person in this workspace.")
    return u


def set_access(db: Session, org_id: str, actor: User, user_id: str, *, profile_id: Optional[str],
               grants: Optional[Dict] = None, denies: Optional[Dict] = None, record_scope: Optional[str] = None,
               reports_to_user_id: Optional[str] = None) -> Dict[str, Any]:
    """Put a person on custom access (or change it). Their seat becomes a
    Wholesale-only, default-deny login with exactly this access."""
    u = _member_user(db, org_id, user_id)
    if u.id == actor.id:
        raise AccessError("You can't change your own access.")
    if getattr(u, "role", None) not in ("advisor", "viewer"):
        raise AccessError("Admin logins already have full access to their workspace and can't be given a custom profile. "
                          "Use a separate non-admin login.")
    prof = _profile(db, org_id, profile_id) if profile_id else None
    if prof is not None and prof.is_archived:
        raise AccessError("That profile is retired.")
    g, d = _clean(grants or {}), _clean(denies or {}, add_view=False)
    rs = record_scope or None
    if rs and rs not in A.SCOPES:
        raise AccessError("Unknown record visibility.")
    eff_perms = A.combine(json.loads(prof.permissions_json or "{}") if prof else {}, g, d)
    eff_scope = rs or (prof.record_scope if prof else A.ASSIGNED)
    _guard_delegation(db, org_id, actor, eff_perms, eff_scope)
    scope = _scope(db, org_id)
    before = _access_json(db, org_id, u)
    seat = _seat(db, scope, u.id)
    if seat is None or not seat.is_active or seat.role != T.CUSTOM or reports_to_user_id is not None:
        T.add_or_update(db, org_id, actor, u.id, T.CUSTOM,
                        reports_to_user_id if reports_to_user_id is not None else (seat.reports_to_user_id if seat else None))
    row = A.access_row(db, u.id, scope)
    if row is None:
        row = WholesaleMemberAccess(organization_id=org_id, seat_scope=scope, user_id=u.id)
        db.add(row)
    row.profile_id = prof.id if prof else None
    row.grants_json, row.denies_json, row.record_scope = json.dumps(g), json.dumps(d), rs
    row.updated_by_id = actor.id
    db.flush()
    after = _access_json(db, org_id, u)
    _audit(db, org_id, actor, "member_access_set", "user", u.id, before=before, after=after)
    db.commit()
    return after


def _seat(db, scope, user_id) -> Optional[Membership]:
    return (db.query(Membership).filter(Membership.scope_type == SCOPE_PRODUCT, Membership.scope_id == scope,
                                        Membership.user_id == user_id).first())


def _access_json(db: Session, org_id: str, u: User) -> Dict[str, Any]:
    scope = _scope(db, org_id)
    seat = _seat(db, scope, u.id)
    row = A.access_row(db, u.id, scope)
    eff = A.effective(db, u, org_id) if seat is not None and seat.is_active else {"mode": "none", "permissions": {}, "record_scope": None, "profile": None}
    return {"user_id": u.id, "name": u.full_name or u.email, "email": u.email,
            "status": ("active" if seat is not None and seat.is_active else "suspended" if seat is not None else "not_on_team"),
            "legacy_role": seat.role if seat is not None and seat.role != T.CUSTOM else None,
            "mode": eff["mode"], "profile": eff.get("profile"),
            "grants": json.loads(row.grants_json or "{}") if row else {},
            "denies": json.loads(row.denies_json or "{}") if row else {},
            "record_scope": eff.get("record_scope"), "record_scope_override": row.record_scope if row else None,
            "permissions": eff.get("permissions") if eff["mode"] == "custom" else None,
            "reports_to_user_id": seat.reports_to_user_id if seat is not None else None}


def member_access(db: Session, org_id: str, user_id: str) -> Dict[str, Any]:
    return _access_json(db, org_id, _member_user(db, org_id, user_id))


def set_status(db: Session, org_id: str, actor: User, user_id: str, active: bool) -> Dict[str, Any]:
    """Suspend (keep everything, stop access now) or reactivate."""
    u = _member_user(db, org_id, user_id)
    if u.id == actor.id:
        raise AccessError("You can't suspend yourself.")
    seat = _seat(db, _scope(db, org_id), u.id)
    if seat is None:
        raise AccessError("They aren't on the Wholesale team.")
    seat.is_active = bool(active)
    _audit(db, org_id, actor, "member_reactivated" if active else "member_suspended", "user", u.id)
    db.commit()
    return _access_json(db, org_id, u)


def _assignable_models():
    from app.models.wholesale_models import WholesaleDeal, WholesaleProperty, WholesaleWorkException
    from app.models.wholesale_ops_models import WholesaleCanvassRoute, WholesaleFieldAppointment, WholesaleSellerCallback
    return (("deals", WholesaleDeal), ("properties", WholesaleProperty), ("exceptions", WholesaleWorkException),
            ("appointments", WholesaleFieldAppointment), ("canvass_routes", WholesaleCanvassRoute),
            ("callbacks", WholesaleSellerCallback))


def owned_work(db: Session, org_id: str, user_id: str) -> Dict[str, int]:
    return {k: db.query(M.id).filter(M.organization_id == org_id, M.assigned_to_id == user_id).count()
            for k, M in _assignable_models()}


def remove(db: Session, org_id: str, actor: User, user_id: str, reassign_to: Optional[str] = None) -> Dict[str, Any]:
    """Take someone off the team. Their login stays (it may belong to other
    workspaces); their work here moves to `reassign_to` when given."""
    u = _member_user(db, org_id, user_id)
    if u.id == actor.id:
        raise AccessError("You can't remove yourself.")
    work = owned_work(db, org_id, u.id)
    moved = {}
    if reassign_to:
        to = T.in_workspace(db, org_id, reassign_to)
        if to is None or to.id == u.id or not PA.may_use_in(db, to, db.query(Organization).get(org_id), PA.WHOLESALE):
            raise AccessError("Pick someone on this workspace's team to take over their work.")
        for key, M in _assignable_models():
            moved[key] = (db.query(M).filter(M.organization_id == org_id, M.assigned_to_id == u.id)
                          .update({M.assigned_to_id: to.id}, synchronize_session=False))
    T.remove(db, org_id, actor, u.id)
    scope = _scope(db, org_id)
    row = A.access_row(db, u.id, scope)
    if row is not None:
        db.delete(row)
    _audit(db, org_id, actor, "member_removed", "user", u.id,
           after={"owned_work": work, "moved_to": reassign_to, "moved": moved})
    db.commit()
    return {"removed": u.id, "owned_work": work, "moved": moved}


# ── invitations ─────────────────────────────────────────────────────────────
def _invite_json(db: Session, inv: WholesaleTeamInvite) -> Dict[str, Any]:
    status = inv.status
    if status == PENDING and inv.kind == NEW_LOGIN and inv.user_id:
        u = db.query(User).filter(User.id == inv.user_id).first()
        if u is not None and u.last_login_at is not None:
            status = ACCEPTED
    expired = status == PENDING and inv.expires_at is not None and inv.expires_at < datetime.utcnow()
    prof = db.query(WholesaleAccessProfile).filter(WholesaleAccessProfile.id == inv.profile_id).first() if inv.profile_id else None
    return {"id": inv.id, "email": inv.email, "full_name": inv.full_name, "kind": inv.kind,
            "status": "expired" if expired else status, "profile": {"id": prof.id, "name": prof.name} if prof else None,
            "record_scope": inv.record_scope or (prof.record_scope if prof else None),
            "expires_at": inv.expires_at.isoformat() + "Z" if inv.expires_at else None,
            "send_count": inv.send_count or 0,
            "last_sent_at": inv.last_sent_at.isoformat() + "Z" if inv.last_sent_at else None,
            "created_at": inv.created_at.isoformat() + "Z" if inv.created_at else None}


def invites(db: Session, org_id: str) -> List[Dict[str, Any]]:
    rows = (db.query(WholesaleTeamInvite).filter(WholesaleTeamInvite.seat_scope == _scope(db, org_id),
                                                 WholesaleTeamInvite.organization_id == org_id)
            .order_by(WholesaleTeamInvite.created_at.desc()).limit(200).all())
    return [_invite_json(db, r) for r in rows]


def _load_invite(db, org_id, invite_id) -> WholesaleTeamInvite:
    inv = (db.query(WholesaleTeamInvite).filter(WholesaleTeamInvite.id == invite_id,
                                                WholesaleTeamInvite.seat_scope == _scope(db, org_id),
                                                WholesaleTeamInvite.organization_id == org_id).first())
    if inv is None:
        raise LookupError("No such invitation in this workspace.")
    return inv


def _link(base_url: Optional[str], path: str) -> str:
    base = (base_url or "").rstrip("/")
    return "%s%s" % (base, path)


def invite(db: Session, org_id: str, actor: User, *, email: str, full_name: Optional[str] = None,
           profile_id: Optional[str] = None, grants: Optional[Dict] = None, denies: Optional[Dict] = None,
           record_scope: Optional[str] = None, reports_to_user_id: Optional[str] = None,
           base_url: Optional[str] = None, send: bool = False) -> Dict[str, Any]:
    email = (email or "").strip().lower()
    if "@" not in email or len(email) > 254:
        raise AccessError("Enter a valid email address.")
    prof = _profile(db, org_id, profile_id) if profile_id else None
    if prof is None and not grants:
        raise AccessError("Choose an access profile (or individual permissions).")
    if prof is not None and prof.is_archived:
        raise AccessError("That profile is retired.")
    g, d = _clean(grants or {}), _clean(denies or {}, add_view=False)
    rs = record_scope or None
    if rs and rs not in A.SCOPES:
        raise AccessError("Unknown record visibility.")
    _guard_delegation(db, org_id, actor, A.combine(json.loads(prof.permissions_json or "{}") if prof else {}, g, d),
                      rs or (prof.record_scope if prof else A.ASSIGNED))
    scope = _scope(db, org_id)
    if reports_to_user_id:
        boss = T.in_workspace(db, org_id, reports_to_user_id)
        if boss is None or _seat(db, scope, boss.id) is None:
            raise AccessError("The manager you picked is not on the Wholesale team.")
    dup = (db.query(WholesaleTeamInvite).filter(WholesaleTeamInvite.seat_scope == scope,
                                                func.lower(WholesaleTeamInvite.email) == email,
                                                WholesaleTeamInvite.status == PENDING).first())
    if dup is not None and not (dup.expires_at and dup.expires_at < datetime.utcnow()):
        raise AccessError("%s already has a pending invitation. Resend or cancel it instead." % email)
    existing = db.query(User).filter(func.lower(User.email) == email).first()
    org = db.query(Organization).filter(Organization.id == org_id).first()
    inv = WholesaleTeamInvite(organization_id=org_id, seat_scope=scope, email=email,
                              full_name=(full_name or "").strip()[:120] or None, profile_id=prof.id if prof else None,
                              grants_json=json.dumps(g), denies_json=json.dumps(d), record_scope=rs,
                              reports_to_user_id=reports_to_user_id, invited_by_id=actor.id,
                              expires_at=datetime.utcnow() + timedelta(days=INVITE_DAYS), status=PENDING)
    link = None
    if existing is not None:
        if getattr(existing, "role", None) in _ELEVATED or getattr(existing, "organization_id", None) is None:
            raise AccessError("That email belongs to an admin or platform login, which can't be invited with a custom "
                              "profile. Use a separate non-admin login for this person.")
        seat = _seat(db, scope, existing.id)
        if seat is not None and seat.is_active:
            raise AccessError("%s is already on this team. Edit their access instead." % email)
        if not existing.is_active:
            raise AccessError("That login is deactivated.")
        inv.kind, inv.user_id = EXISTING_LOGIN, existing.id
        raw = "wsi_" + secrets.token_urlsafe(32)
        inv.token_hash = _hash(raw)
        link = _link(base_url, "/wholesale-invite?token=%s" % raw)
    else:
        # A NEW LOGIN: the same rules as POST /admin/users, in this workspace.
        from app.services import plan_limits
        from app.services import staff_activation as SA
        from app.services.auth_service import hash_password
        plan_limits.require_capacity(db, org, plan_limits.LIMIT_USERS, adding=1)
        u = User(organization_id=org_id, email=email, full_name=inv.full_name or email.split("@")[0],
                 role="advisor", password_hash=hash_password(secrets.token_urlsafe(48)), must_change_password=True)
        db.add(u)
        db.flush()
        inv.kind, inv.user_id = NEW_LOGIN, u.id
        db.add(inv)
        db.flush()
        T.add_or_update(db, org_id, actor, u.id, T.CUSTOM, reports_to_user_id)
        db.add(WholesaleMemberAccess(organization_id=org_id, seat_scope=scope, user_id=u.id,
                                     profile_id=inv.profile_id, grants_json=inv.grants_json,
                                     denies_json=inv.denies_json, record_scope=rs, updated_by_id=actor.id))
        _row, raw = SA.issue(db, u, actor, purpose=SA.PURPOSE_SETUP)
        link = SA.activation_url(base_url, raw)
    db.add(inv)
    db.flush()
    _audit(db, org_id, actor, "invited", "team_invite", inv.id,
           after={"email": email, "kind": inv.kind, "profile": prof.name if prof else None, "record_scope": rs})
    out = {"invite": _invite_json(db, inv), "link": link, "delivery": None}
    if send:
        out["delivery"] = _send(db, org, inv, link, actor)
    db.commit()
    out["invite"] = _invite_json(db, inv)
    return out


def _send(db: Session, org: Organization, inv: WholesaleTeamInvite, link: str, actor: User) -> Dict[str, Any]:
    """Email the link from the brand's own sending identity. Called only when
    the owner presses Send."""
    from app.services import launch_invitation as LI
    from app.services.email_service import send_email_via_provider
    from app.services import email_identity
    brand = LI._brand(db, org)
    identity = LI._sending_identity(db, org, brand)
    name = brand.get("name") or "your"
    if not getattr(identity, "from_email", None):
        return {"state": "failed", "error": "No verified sending address is set for %s." % name}
    body = ("<p>Hi%s,</p><p>You've been invited to the %s Wholesale workspace for %s.</p>"
            "<p><a href=\"%s\">%s</a></p><p>This link expires in %d days.</p>"
            % ((" " + inv.full_name) if inv.full_name else "", name, org.name, link,
               "Set your password and sign in" if inv.kind == NEW_LOGIN else "Sign in and accept", INVITE_DAYS))
    try:
        res = send_email_via_provider(inv.email, "You're invited to %s Wholesale" % name, body, org=identity,
                                      message_type="onboarding_invitation", sensitivity=email_identity.SENSITIVE,
                                      template_id="wholesale.team_invitation")
    except Exception as exc:  # noqa: BLE001
        return {"state": "failed", "error": str(exc)[:200]}
    if res.get("success"):
        inv.last_sent_at, inv.send_count = datetime.utcnow(), (inv.send_count or 0) + 1
        _audit(db, org.id, actor, "invite_sent", "team_invite", inv.id, after={"to": inv.email})
        return {"state": "sent"}
    return {"state": "failed", "error": (res.get("error") or "The mail provider refused it.")[:200]}


def resend(db: Session, org_id: str, actor: User, invite_id: str, base_url: Optional[str] = None,
           send: bool = False) -> Dict[str, Any]:
    """A fresh link (the old one stops working) and a fresh 7 days."""
    inv = _load_invite(db, org_id, invite_id)
    if inv.status != PENDING:
        raise AccessError("Only a pending invitation can be resent.")
    inv.expires_at = datetime.utcnow() + timedelta(days=INVITE_DAYS)
    if inv.kind == EXISTING_LOGIN:
        raw = "wsi_" + secrets.token_urlsafe(32)
        inv.token_hash = _hash(raw)
        link = _link(base_url, "/wholesale-invite?token=%s" % raw)
    else:
        from app.services import staff_activation as SA
        u = db.query(User).filter(User.id == inv.user_id).first()
        if u is None or u.last_login_at is not None:
            raise AccessError("They have already signed in.")
        _row, raw = SA.issue(db, u, actor, purpose=SA.PURPOSE_SETUP)
        link = SA.activation_url(base_url, raw)
    _audit(db, org_id, actor, "invite_resent", "team_invite", inv.id)
    org = db.query(Organization).filter(Organization.id == org_id).first()
    delivery = _send(db, org, inv, link, actor) if send else None
    db.commit()
    return {"invite": _invite_json(db, inv), "link": link, "delivery": delivery}


def cancel(db: Session, org_id: str, actor: User, invite_id: str) -> Dict[str, Any]:
    inv = _load_invite(db, org_id, invite_id)
    if _invite_json(db, inv)["status"] not in (PENDING, "expired"):
        raise AccessError("Only a pending invitation can be cancelled. Remove the person from the team instead.")
    inv.status, inv.cancelled_at, inv.token_hash = CANCELLED, datetime.utcnow(), None
    if inv.kind == NEW_LOGIN and inv.user_id:
        from app.models.staff_models import StaffActivation
        for row in db.query(StaffActivation).filter(StaffActivation.user_id == inv.user_id,
                                                    StaffActivation.status == "pending").all():
            row.status, row.revoked_at = "revoked", datetime.utcnow()
        scope = _scope(db, org_id)
        seat = _seat(db, scope, inv.user_id)
        if seat is not None:
            seat.is_active = False
        row = A.access_row(db, inv.user_id, scope)
        if row is not None:
            db.delete(row)
        u = db.query(User).filter(User.id == inv.user_id).first()
        if u is not None and u.last_login_at is None:
            u.is_active = False                    # a login nobody ever used, made only for this invitation
    _audit(db, org_id, actor, "invite_cancelled", "team_invite", inv.id, after={"email": inv.email})
    db.commit()
    return _invite_json(db, inv)


def accept(db: Session, user: User, raw_token: str) -> Dict[str, Any]:
    """An existing login accepts an invitation after signing in normally."""
    if not raw_token:
        raise AccessError("This invitation link is incomplete.")
    inv = db.query(WholesaleTeamInvite).filter(WholesaleTeamInvite.token_hash == _hash(raw_token)).first()
    if inv is None or inv.kind != EXISTING_LOGIN:
        raise AccessError("This invitation link isn't valid. Ask for a new one.")
    if inv.status == ACCEPTED and inv.user_id == user.id:
        return {"organization_id": inv.organization_id, "already": True}
    if inv.status != PENDING:
        raise AccessError("This invitation was cancelled or already used.")
    if inv.expires_at and inv.expires_at < datetime.utcnow():
        raise AccessError("This invitation has expired. Ask the workspace owner to resend it.")
    if (user.email or "").lower() != inv.email.lower() or user.id != inv.user_id:
        raise AccessError("This invitation is for a different login. Sign in as %s to accept it." % inv.email)
    if getattr(user, "role", None) not in ("advisor", "viewer"):
        raise AccessError("Admin logins can't accept a custom-access invitation.")
    org_id = inv.organization_id
    if user.organization_id != org_id:
        from app.services.workspace_access import grant_workspace_membership
        grant_workspace_membership(db, user.id, org_id, role="advisor", granted_by=inv.invited_by_id, commit=False)
    # Accepted FIRST: an EvoSys workspace counts an outside login as one of its
    # people only through an accepted invitation (wholesale_team._people_q).
    inv.status, inv.accepted_at, inv.token_hash = ACCEPTED, datetime.utcnow(), None
    db.flush()
    inviter = db.query(User).filter(User.id == inv.invited_by_id).first() or user
    T.add_or_update(db, org_id, inviter, user.id, T.CUSTOM, inv.reports_to_user_id)
    row = A.access_row(db, user.id, inv.seat_scope)
    if row is None:
        row = WholesaleMemberAccess(organization_id=org_id, seat_scope=inv.seat_scope, user_id=user.id)
        db.add(row)
    row.profile_id, row.grants_json, row.denies_json, row.record_scope = (inv.profile_id, inv.grants_json,
                                                                         inv.denies_json, inv.record_scope)
    row.updated_by_id = inv.invited_by_id
    _audit(db, org_id, user, "invite_accepted", "team_invite", inv.id, after={"user_id": user.id})
    db.commit()
    return {"organization_id": org_id, "already": False}


# ── history ─────────────────────────────────────────────────────────────────
def history(db: Session, org_id: str, limit: int = 200) -> List[Dict[str, Any]]:
    from app.models.models import AuditLogEntry
    rows = (db.query(AuditLogEntry).filter(AuditLogEntry.organization_id == org_id,
                                           AuditLogEntry.action.like("wholesale_access.%"))
            .order_by(AuditLogEntry.created_at.desc()).limit(limit).all())
    names = {u.id: (u.full_name or u.email) for u in db.query(User).filter(
        User.id.in_({r.actor_user_id for r in rows} | {r.target_id for r in rows if r.target_type == "user"})).all()}
    labels = {"profile_created": "Created a profile", "profile_updated": "Changed a profile",
              "profile_retired": "Retired a profile", "member_access_set": "Changed someone's access",
              "member_suspended": "Suspended someone", "member_reactivated": "Reactivated someone",
              "member_removed": "Removed someone from the team", "invited": "Sent an invitation",
              "invite_sent": "Emailed an invitation", "invite_resent": "Resent an invitation",
              "invite_cancelled": "Cancelled an invitation", "invite_accepted": "Accepted an invitation",
              "escalation_denied": "Tried to give more access than they hold (refused)"}
    out = []
    for r in rows:
        key = r.action.split(".", 1)[1]
        out.append({"id": r.id, "action": key, "label": labels.get(key, key), "actor": names.get(r.actor_user_id),
                    "target_type": r.target_type,
                    "target": names.get(r.target_id) if r.target_type == "user" else r.target_id,
                    "note": getattr(r, "note", None),
                    "at": r.created_at.isoformat() + "Z" if r.created_at else None})
    return out
