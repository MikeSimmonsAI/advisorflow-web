"""INSURANCE TEAM & ACCESS — people, profiles, teams, locations, invitations,
offboarding and history for one agency workspace (#523).

Everything here is called with an `actor` and an `org_id` already resolved
from the active workspace. Authority comes from app/services/agency/access.py:
  * who may act at all: the route guard (module/action) in front of each call;
  * how far: the actor's delegation ceiling (permissions, visibility, locations);
  * on whom: protection - owners/admins, yourself, and anyone holding more than
    you are never changed from here.
Every change is audited (agency_access.*). Invitation tokens are never stored
or logged in the clear.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.agency_access_models import (AgencyAccessProfile, AgencyInvite, AgencyMemberAccess, AgencyTeam,
                                             AgencyTeamMember)
from app.models.agency_models import AgencyAppointment
from app.models.agency_retention_models import (AgencyClientProfile, AgencyRenewal, AgencyRenewalEvent,
                                                AgencyRetentionStaff)
from app.models.location_models import Location, UserLocation
from app.models.models import Organization, User
from app.services.agency import access as AX

INVITE_DAYS = 7
PENDING, ACCEPTED, REVOKED, EXPIRED = "pending", "accepted", "revoked", "expired"
NEW_LOGIN, EXISTING_LOGIN = "new_login", "existing_login"
MEMBER_ROLE = "advisor"           # the workspace role every invited member holds; access comes from the profile
_ELEVATED = ("org_admin", "super_admin", "god_admin", "manager")


class AccessError(Exception):
    def __init__(self, msg: str, status: int = 400, extra: Optional[Dict[str, Any]] = None):
        super().__init__(msg)
        self.status, self.extra = status, extra or {}


def _now() -> datetime:
    return datetime.utcnow()


def _iso(v):
    return v.replace(microsecond=0).isoformat() + "Z" if v else None


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _jl(raw, default):
    try:
        v = json.loads(raw) if raw else default
        return v if v is not None else default
    except (TypeError, ValueError):
        return default


def _audit(db: Session, org_id: str, actor: User, action: str, ttype: str, tid: Optional[str],
           details: Optional[Dict[str, Any]] = None):
    from app.routers.audit_log_router import log_action
    log_action(db, org_id, actor.id, "agency_access." + action, ttype, tid or org_id, details=details or {},
               commit=False)


def _name(u: Optional[User]) -> Optional[str]:
    return (u.full_name or u.email) if u else None


# ── authority ───────────────────────────────────────────────────────────────

def _role(db, u: User, org_id: str) -> Optional[str]:
    return AX.workspace_role(db, u, org_id)


def protection(db: Session, org_id: str, actor: User, target: User) -> Optional[str]:
    """Why `actor` may not change `target`'s access, or None."""
    if target.id == actor.id:
        return "You can't change your own access."
    trole = _role(db, target, org_id)
    if AX.is_owner_role(trole) and AX.member_row(db, org_id, target.id) is None:
        return "Agency owners and admins are managed by the owner, not from Team & Access."
    if getattr(target, "role", None) in ("god_admin", "super_admin"):
        return "Platform accounts aren't managed from an agency."
    c = AX.ceiling(db, actor, org_id)
    if c["owner"]:
        return None
    row = AX.member_row(db, org_id, target.id)
    t_eff = AX.effective(db, target, org_id, row) if row is not None else AX.legacy(trole)
    if AX.exceeds(t_eff["permissions"], c["permissions"]) or \
            AX.SCOPE_RANK.get(t_eff["record_scope"], 0) > AX.SCOPE_RANK.get(c["record_scope"], 0):
        return "%s holds access you don't, so only someone above them can change it." % _name(target)
    return None


def _refuse_if_protected(db, org_id, actor, target, what):
    why = protection(db, org_id, actor, target)
    if why:
        _audit(db, org_id, actor, "escalation_denied", "user", target.id, {"attempted": what, "reason": why})
        db.commit()
        raise AccessError(why, 403)


def _delegate(db, org_id, actor, perms, scope, location_ids=None, what="grant access"):
    try:
        AX.check_delegation(db, actor, org_id, perms, scope, location_ids)
    except PermissionError as e:
        _audit(db, org_id, actor, "escalation_denied", "organization", org_id, {"attempted": what, "reason": str(e)})
        db.commit()
        raise AccessError(str(e), 403)


def in_workspace(db: Session, org_id: str, user_id: str) -> Optional[User]:
    u = db.query(User).filter(User.id == user_id).first()
    if u is None:
        return None
    if u.organization_id == org_id:
        return u
    try:
        from app.services import workspace_access
        if workspace_access.workspace_role(u, db, org_id):
            return u
    except Exception:  # noqa: BLE001
        pass
    return u if AX.member_row(db, org_id, u.id) is not None else None


def _org_locations(db, org_id, include_archived=False):
    q = db.query(Location).filter(Location.organization_id == org_id)
    if not include_archived:
        q = q.filter(Location.is_active.is_(True))
    return q.order_by(Location.is_primary.desc(), Location.slug, Location.name).all()


def _check_locations(db, org_id, ids: Iterable[str]) -> List[str]:
    ids = [i for i in (ids or []) if i]
    if not ids:
        return []
    found = {l.id for l in db.query(Location).filter(Location.organization_id == org_id, Location.id.in_(ids),
                                                      Location.is_active.is_(True)).all()}
    if set(ids) - found:
        raise AccessError("One of those locations isn't in this agency.", 404)
    return list(dict.fromkeys(ids))


def _check_teams(db, org_id, ids: Iterable[str]) -> List[str]:
    ids = [i for i in (ids or []) if i]
    if not ids:
        return []
    found = {t.id for t in db.query(AgencyTeam).filter(AgencyTeam.organization_id == org_id, AgencyTeam.id.in_(ids),
                                                        AgencyTeam.is_archived.is_(False)).all()}
    if set(ids) - found:
        raise AccessError("One of those teams isn't in this agency.", 404)
    return list(dict.fromkeys(ids))


# ── profiles ────────────────────────────────────────────────────────────────

def ensure_starters(db: Session, org_id: str, is_demo: bool = False) -> None:
    have = {p.template_key for p in db.query(AgencyAccessProfile).filter(
        AgencyAccessProfile.organization_id == org_id, AgencyAccessProfile.template_key.isnot(None)).all()}
    added = False
    for st in AX.STARTERS:
        if st["key"] in have:
            continue
        db.add(AgencyAccessProfile(organization_id=org_id, name=st["name"], description=st["description"],
                                   template_key=st["key"], permissions_json=json.dumps(AX.clean(st["permissions"])),
                                   record_scope=st["record_scope"], dashboard=st["dashboard"],
                                   aggregate_only=bool(st.get("aggregate_only")), is_demo=is_demo))
        added = True
    if added:
        db.flush()


def _profile_members(db, org_id, profile_id) -> List[AgencyMemberAccess]:
    return db.query(AgencyMemberAccess).filter(AgencyMemberAccess.organization_id == org_id,
                                               AgencyMemberAccess.profile_id == profile_id,
                                               AgencyMemberAccess.status != "removed").all()


def profile_json(db: Session, p: AgencyAccessProfile) -> Dict[str, Any]:
    members = _profile_members(db, p.organization_id, p.id)
    return {"id": p.id, "name": p.name, "description": p.description, "template_key": p.template_key,
            "permissions": AX.load(p.permissions_json), "record_scope": p.record_scope,
            "scope_label": AX.SCOPE_LABELS.get(p.record_scope), "dashboard": p.dashboard,
            "aggregate_only": bool(p.aggregate_only), "archived": bool(p.is_archived),
            "frozen": bool(p.frozen_from_id), "version": p.version, "members": len(members),
            "updated_at": _iso(p.updated_at)}


def profiles(db: Session, org_id: str, include_archived: bool = False) -> List[Dict[str, Any]]:
    q = db.query(AgencyAccessProfile).filter(AgencyAccessProfile.organization_id == org_id)
    if not include_archived:
        q = q.filter(AgencyAccessProfile.is_archived.is_(False))
    order = {st["key"]: i for i, st in enumerate(AX.STARTERS)}
    rows = sorted(q.all(), key=lambda p: (order.get(p.template_key, 50), p.name.lower()))
    return [profile_json(db, p) for p in rows]


def _profile(db, org_id, pid) -> AgencyAccessProfile:
    p = db.query(AgencyAccessProfile).filter(AgencyAccessProfile.id == pid,
                                             AgencyAccessProfile.organization_id == org_id).first()
    if p is None:
        raise AccessError("Profile not found.", 404)
    return p


def _validate_profile_fields(name, record_scope, dashboard):
    if not (name or "").strip():
        raise AccessError("Give the profile a name.")
    if record_scope not in AX.SCOPES:
        raise AccessError("Unknown record visibility.")
    if dashboard not in AX.DASHBOARDS:
        raise AccessError("Unknown dashboard.")


def _name_taken(db, org_id, name, but=None) -> bool:
    q = db.query(AgencyAccessProfile.id).filter(AgencyAccessProfile.organization_id == org_id,
                                                func.lower(AgencyAccessProfile.name) == name.strip().lower(),
                                                AgencyAccessProfile.is_archived.is_(False))
    if but:
        q = q.filter(AgencyAccessProfile.id != but)
    return q.first() is not None


def create_profile(db: Session, org_id: str, actor: User, *, name: str, description: Optional[str] = None,
                   template_key: Optional[str] = None, permissions: Optional[Dict] = None,
                   record_scope: str = AX.ASSIGNED, dashboard: str = "auto", aggregate_only: bool = False):
    _validate_profile_fields(name, record_scope, dashboard)
    if _name_taken(db, org_id, name):
        raise AccessError("A profile called %s already exists." % name.strip())
    perms = AX.clean(permissions or {})
    _delegate(db, org_id, actor, perms, record_scope, what="create profile")
    p = AgencyAccessProfile(organization_id=org_id, name=name.strip()[:80], description=(description or "")[:600] or None,
                            template_key=None if template_key in [s["key"] for s in AX.STARTERS] else template_key,
                            permissions_json=json.dumps(perms), record_scope=record_scope, dashboard=dashboard,
                            aggregate_only=bool(aggregate_only), created_by_id=actor.id, updated_by_id=actor.id)
    db.add(p)
    db.flush()
    _audit(db, org_id, actor, "profile_created", "agency_access_profile", p.id,
           {"name": p.name, "permissions": perms, "record_scope": record_scope})
    db.commit()
    return profile_json(db, p)


def duplicate_profile(db: Session, org_id: str, actor: User, profile_id: str, name: Optional[str] = None):
    src = _profile(db, org_id, profile_id)
    base = (name or "%s (copy)" % src.name).strip()
    n, i = base, 2
    while _name_taken(db, org_id, n):
        n, i = "%s %d" % (base, i), i + 1
    return create_profile(db, org_id, actor, name=n, description=src.description, permissions=AX.load(src.permissions_json),
                          record_scope=src.record_scope, dashboard=src.dashboard, aggregate_only=src.aggregate_only)


def _widens(old_perms, new_perms, old_scope, new_scope, old_agg, new_agg) -> bool:
    return bool(AX.exceeds(new_perms, old_perms)) or \
        AX.SCOPE_RANK.get(new_scope, 0) > AX.SCOPE_RANK.get(old_scope, 0) or (old_agg and not new_agg)


def update_profile(db: Session, org_id: str, actor: User, profile_id: str, *, name: Optional[str] = None,
                   description: Optional[str] = None, permissions: Optional[Dict] = None,
                   record_scope: Optional[str] = None, dashboard: Optional[str] = None,
                   aggregate_only: Optional[bool] = None, apply: Optional[str] = None):
    """`apply` decides what happens to current members when the edit WIDENS access:
    'update' (they get the wider access) or 'freeze' (they stay on a frozen copy
    of today's version). Without a choice a widening edit is refused with the
    list of affected people - access is never broadened silently."""
    p = _profile(db, org_id, profile_id)
    if p.is_archived:
        raise AccessError("That profile is retired.")
    old_perms, old_scope, old_agg = AX.load(p.permissions_json), p.record_scope, bool(p.aggregate_only)
    new_perms = AX.clean(permissions) if permissions is not None else old_perms
    new_scope = record_scope or old_scope
    new_dash = dashboard or p.dashboard
    new_agg = old_agg if aggregate_only is None else bool(aggregate_only)
    _validate_profile_fields(name if name is not None else p.name, new_scope, new_dash)
    if name is not None and _name_taken(db, org_id, name, but=p.id):
        raise AccessError("A profile called %s already exists." % name.strip())
    _delegate(db, org_id, actor, new_perms, new_scope, what="edit profile")
    members = _profile_members(db, org_id, p.id)
    widening = _widens(old_perms, new_perms, old_scope, new_scope, old_agg, new_agg)
    if widening and members and apply not in ("update", "freeze"):
        users = db.query(User).filter(User.id.in_([m.user_id for m in members])).all()
        raise AccessError("This change gives more access to %d %s. Choose whether they get it now or stay on "
                          "today's version." % (len(members), "person" if len(members) == 1 else "people"), 409,
                          {"affected": [{"user_id": u.id, "name": _name(u)} for u in users],
                           "added": AX.exceeds(new_perms, old_perms)})
    frozen_id = None
    if widening and members and apply == "freeze":
        fr = AgencyAccessProfile(organization_id=org_id, name="%s (v%d)" % (p.name, p.version),
                                 description=p.description, permissions_json=json.dumps(old_perms),
                                 record_scope=old_scope, dashboard=p.dashboard, aggregate_only=old_agg,
                                 frozen_from_id=p.id, version=p.version, created_by_id=actor.id, is_demo=p.is_demo)
        db.add(fr)
        db.flush()
        for m in members:
            m.profile_id = fr.id
        frozen_id = fr.id
    before = {"permissions": old_perms, "record_scope": old_scope, "aggregate_only": old_agg}
    if name is not None:
        p.name = name.strip()[:80]
    if description is not None:
        p.description = description[:600] or None
    p.permissions_json, p.record_scope, p.dashboard, p.aggregate_only = json.dumps(new_perms), new_scope, new_dash, new_agg
    p.version, p.updated_by_id = (p.version or 1) + 1, actor.id
    _audit(db, org_id, actor, "profile_updated", "agency_access_profile", p.id,
           {"before": before, "after": {"permissions": new_perms, "record_scope": new_scope,
                                        "aggregate_only": new_agg}, "widened": widening,
            "members": len(members), "applied": apply if widening else None, "frozen_copy": frozen_id})
    db.commit()
    return profile_json(db, p)


def retire_profile(db: Session, org_id: str, actor: User, profile_id: str, move_to: Optional[str] = None):
    p = _profile(db, org_id, profile_id)
    members = _profile_members(db, org_id, p.id)
    if members and not move_to:
        raise AccessError("%d %s still use this profile. Move them to another profile first." %
                          (len(members), "person" if len(members) == 1 else "people"), 409,
                          {"members": len(members)})
    target = None
    if move_to:
        target = _profile(db, org_id, move_to)
        if target.is_archived or target.id == p.id:
            raise AccessError("Pick an active profile to move them to.")
        for m in members:
            u = db.query(User).filter(User.id == m.user_id).first()
            _refuse_if_protected(db, org_id, actor, u, "move to profile")
        _delegate(db, org_id, actor, AX.load(target.permissions_json), target.record_scope, what="retire profile")
        for m in members:
            m.profile_id, m.updated_by_id = target.id, actor.id
    p.is_archived, p.updated_by_id = True, actor.id
    _audit(db, org_id, actor, "profile_retired", "agency_access_profile", p.id,
           {"name": p.name, "moved": len(members), "to": target.name if target else None})
    db.commit()
    return profile_json(db, p)


def preview(db: Session, org_id: str, *, profile_id: Optional[str] = None, grants=None, denies=None,
            record_scope: Optional[str] = None, location_ids=None) -> Dict[str, Any]:
    """What a configuration would actually allow - for the builder and drawer."""
    base, scope, agg, dash = {}, AX.ASSIGNED, False, "auto"
    if profile_id:
        p = _profile(db, org_id, profile_id)
        base, scope, agg, dash = AX.load(p.permissions_json), p.record_scope, bool(p.aggregate_only), p.dashboard
    perms = AX.combine(base, AX.clean(grants or {}), AX.clean(denies or {}, add_view=False))
    scope = record_scope if record_scope in AX.SCOPES else scope
    eff = {"permissions": perms, "record_scope": scope, "dashboard": dash, "aggregate_only": agg}
    lines = []
    for mod in AX.MODULES:
        acts = perms.get(mod, [])
        lines.append({"module": mod, "label": AX.MODULE_LABELS[mod], "allowed": bool(acts),
                      "actions": [AX.ACTION_LABELS[(mod, a)] for a in acts]})
    locs = []
    if location_ids:
        locs = [l.name for l in db.query(Location).filter(Location.organization_id == org_id,
                                                           Location.id.in_(list(location_ids))).all()]
    return {"permissions": perms, "record_scope": scope, "scope_label": AX.SCOPE_LABELS[scope],
            "lines": lines, "dashboard": AX.resolve_dashboard(eff), "aggregate_only": agg, "locations": locs,
            "sensitive": [AX.ACTION_LABELS[(m, a)] + " (" + AX.MODULE_LABELS[m] + ")"
                          for m, acts in perms.items() for a in acts if (m, a) in AX.SENSITIVE]}


# ── people ──────────────────────────────────────────────────────────────────

def _people_ids(db: Session, org_id: str) -> List[str]:
    ids = {u[0] for u in db.query(User.id).filter(User.organization_id == org_id).all()}
    try:
        from app.models.sales_models import Membership
        ids |= {m[0] for m in db.query(Membership.user_id).filter(Membership.scope_type == "customer_org",
                                                                  Membership.scope_id == org_id).all()}
    except Exception:  # noqa: BLE001
        pass
    ids |= {m[0] for m in db.query(AgencyMemberAccess.user_id).filter(AgencyMemberAccess.organization_id == org_id).all()}
    return list(ids)


def _invite_status(db, inv: AgencyInvite) -> str:
    if inv.status == PENDING and inv.expires_at and inv.expires_at < _now():
        return EXPIRED
    return inv.status


def person_json(db: Session, org_id: str, actor: User, u: User, *, row=None, locs=None, teams=None,
                profiles_by_id=None, staff=None, workload=None, invite=None) -> Dict[str, Any]:
    role = _role(db, u, org_id)
    row = row if row is not None else AX.member_row(db, org_id, u.id)
    if row is not None:
        eff = AX.effective(db, u, org_id, row)
        profile_name = (eff["profile"] or {}).get("name")
        membership = row.status
    else:
        eff = AX.legacy(role)
        profile_name = "Agency owner" if AX.is_owner_role(role) else (
            "Location manager (legacy)" if role == "manager" else "Agent (legacy)")
        membership = "active" if role else "removed"
    inv_state = _invite_status(db, invite) if invite is not None else None
    sample = bool(row.is_sample) if row is not None else bool(staff and staff.is_demo)
    prot = protection(db, org_id, actor, u)
    return {"user_id": u.id, "name": _name(u), "email": u.email, "role": role, "profile": profile_name,
            "profile_id": row.profile_id if row is not None else None, "configured": row is not None,
            "is_owner": AX.is_owner_role(role) and row is None,
            "record_scope": eff["record_scope"], "scope_label": AX.SCOPE_LABELS.get(eff["record_scope"]),
            "location_ids": locs if locs is not None else AX.user_location_ids(db, org_id, u.id),
            "team_ids": teams if teams is not None else AX.user_team_ids(db, org_id, u.id),
            "account": "enabled" if u.is_active else "disabled", "membership": membership,
            "invitation": inv_state, "invite_id": invite.id if invite is not None else None,
            "available": staff.available if staff is not None else True,
            "workload": (workload or {}).get(u.id, 0),
            "last_sign_in": _iso(getattr(u, "last_login_at", None)),
            "sample": sample, "protected": prot, "can_manage": prot is None and not sample,
            "reports_to_user_id": row.reports_to_user_id if row is not None else None}


def people(db: Session, org_id: str, actor: User, visible_ids: Optional[set] = None) -> List[Dict[str, Any]]:
    ids = _people_ids(db, org_id)
    if visible_ids is not None:
        ids = [i for i in ids if i in visible_ids]
    users = db.query(User).filter(User.id.in_(ids or ["-"]), User.role != "god_admin").all()
    rows = {r.user_id: r for r in db.query(AgencyMemberAccess).filter(AgencyMemberAccess.organization_id == org_id).all()}
    staff = {s.user_id: s for s in db.query(AgencyRetentionStaff).filter(AgencyRetentionStaff.organization_id == org_id).all()}
    loc_rows = db.query(UserLocation.user_id, UserLocation.location_id).filter(UserLocation.organization_id == org_id).all()
    locs: Dict[str, List[str]] = {}
    for uid, lid in loc_rows:
        locs.setdefault(uid, []).append(lid)
    tm = db.query(AgencyTeamMember.user_id, AgencyTeamMember.team_id).join(
        AgencyTeam, AgencyTeam.id == AgencyTeamMember.team_id).filter(
        AgencyTeamMember.organization_id == org_id, AgencyTeam.is_archived.is_(False)).all()
    teams: Dict[str, List[str]] = {}
    for uid, tid in tm:
        teams.setdefault(uid, []).append(tid)
    workload = dict(db.query(AgencyRenewal.assigned_user_id, func.count(AgencyRenewal.id)).filter(
        AgencyRenewal.organization_id == org_id, AgencyRenewal.outcome.is_(None)).group_by(
        AgencyRenewal.assigned_user_id).all())
    invites = {}
    for inv in db.query(AgencyInvite).filter(AgencyInvite.organization_id == org_id).order_by(AgencyInvite.created_at).all():
        if inv.user_id:
            invites[inv.user_id] = inv
    out = []
    for u in users:
        out.append(person_json(db, org_id, actor, u, row=rows.get(u.id), locs=locs.get(u.id, []),
                               teams=teams.get(u.id, []), staff=staff.get(u.id), workload=workload,
                               invite=invites.get(u.id)))
    rank = lambda p: (not p["is_owner"], p["membership"] != "active", p["sample"], (p["name"] or "").lower())  # noqa: E731
    return sorted(out, key=rank)


def member_detail(db: Session, org_id: str, actor: User, user_id: str) -> Dict[str, Any]:
    u = in_workspace(db, org_id, user_id)
    if u is None:
        raise AccessError("Person not found.", 404)
    row = AX.member_row(db, org_id, u.id)
    inv = (db.query(AgencyInvite).filter(AgencyInvite.organization_id == org_id, AgencyInvite.user_id == u.id)
           .order_by(AgencyInvite.created_at.desc()).first())
    staff = db.query(AgencyRetentionStaff).filter(AgencyRetentionStaff.organization_id == org_id,
                                                  AgencyRetentionStaff.user_id == u.id).first()
    p = person_json(db, org_id, actor, u, row=row, staff=staff, invite=inv)
    eff = AX.effective(db, u, org_id, row) if row is not None else AX.legacy(p["role"])
    p["grants"] = AX.load(row.grants_json) if row else {}
    p["denies"] = AX.load(row.denies_json) if row else {}
    p["record_scope_override"] = row.record_scope if row else None
    p["effective"] = preview_eff(eff)
    p["owned_work"] = owned_work(db, org_id, u.id)
    p["history"] = history(db, org_id, user_id=u.id, limit=40)
    return p


def preview_eff(eff: Dict[str, Any]) -> Dict[str, Any]:
    perms = eff.get("permissions") or {}
    return {"lines": [{"module": m, "label": AX.MODULE_LABELS[m], "allowed": bool(perms.get(m)),
                       "actions": [AX.ACTION_LABELS[(m, a)] for a in perms.get(m, [])]} for m in AX.MODULES],
            "record_scope": eff.get("record_scope"), "scope_label": AX.SCOPE_LABELS.get(eff.get("record_scope")),
            "dashboard": AX.resolve_dashboard(eff), "aggregate_only": bool(eff.get("aggregate_only"))}


def _cycle(db, org_id, user_id, boss_id) -> bool:
    seen, cur = {user_id}, boss_id
    while cur:
        if cur in seen:
            return True
        seen.add(cur)
        r = AX.member_row(db, org_id, cur)
        cur = r.reports_to_user_id if r is not None else None
    return False


def set_access(db: Session, org_id: str, actor: User, user_id: str, *, profile_id: Optional[str] = None,
               grants: Optional[Dict] = None, denies: Optional[Dict] = None, record_scope: Optional[str] = None,
               reports_to_user_id: Optional[str] = None, location_ids: Optional[List[str]] = None,
               team_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    u = in_workspace(db, org_id, user_id)
    if u is None:
        raise AccessError("Person not found.", 404)
    _refuse_if_protected(db, org_id, actor, u, "change access")
    if getattr(u, "role", None) not in ("advisor", "viewer") and u.organization_id == org_id and \
            AX.member_row(db, org_id, u.id) is None and u.role in _ELEVATED:
        raise AccessError("This login has an elevated role. Use a separate member login for custom access.")
    prof = _profile(db, org_id, profile_id) if profile_id else None
    if prof is not None and prof.is_archived:
        raise AccessError("That profile is retired.")
    g, d = AX.clean(grants or {}), AX.clean(denies or {}, add_view=False)
    scope = record_scope if record_scope in AX.SCOPES else None
    eff_perms = AX.combine(AX.load(prof.permissions_json) if prof else {}, g, d)
    eff_scope = scope or (prof.record_scope if prof else AX.ASSIGNED)
    locs = _check_locations(db, org_id, location_ids) if location_ids is not None else None
    _delegate(db, org_id, actor, eff_perms, eff_scope, locs, what="change access")
    if reports_to_user_id:
        if in_workspace(db, org_id, reports_to_user_id) is None:
            raise AccessError("The person they report to isn't in this agency.")
        if _cycle(db, org_id, u.id, reports_to_user_id):
            raise AccessError("That reporting line would loop back to this person.")
    row = AX.member_row(db, org_id, u.id)
    before = None
    if row is None:
        row = AgencyMemberAccess(organization_id=org_id, user_id=u.id)
        db.add(row)
    else:
        before = {"profile_id": row.profile_id, "grants": AX.load(row.grants_json), "denies": AX.load(row.denies_json),
                  "record_scope": row.record_scope}
    row.profile_id, row.grants_json, row.denies_json, row.record_scope = (prof.id if prof else None, json.dumps(g),
                                                                         json.dumps(d), scope)
    row.reports_to_user_id = reports_to_user_id or None
    row.updated_by_id = actor.id
    if locs is not None:
        _set_locations(db, org_id, u.id, locs)
    if team_ids is not None:
        _set_teams(db, org_id, u.id, _check_teams(db, org_id, team_ids))
    db.flush()
    _audit(db, org_id, actor, "access_changed", "user", u.id,
           {"before": before, "after": {"profile": prof.name if prof else None, "grants": g, "denies": d,
                                        "record_scope": scope, "locations": locs, "teams": team_ids}})
    db.commit()
    return member_detail(db, org_id, actor, u.id)


def _set_locations(db, org_id, user_id, loc_ids: List[str]):
    have = {r.location_id: r for r in db.query(UserLocation).filter(UserLocation.organization_id == org_id,
                                                                     UserLocation.user_id == user_id).all()}
    for lid, r in have.items():
        if lid not in loc_ids:
            db.delete(r)
    for i, lid in enumerate(loc_ids):
        if lid not in have:
            db.add(UserLocation(user_id=user_id, location_id=lid, organization_id=org_id, is_primary=(i == 0)))


def _set_teams(db, org_id, user_id, team_ids: List[str]):
    have = {r.team_id: r for r in db.query(AgencyTeamMember).filter(AgencyTeamMember.organization_id == org_id,
                                                                     AgencyTeamMember.user_id == user_id).all()}
    for tid, r in have.items():
        if tid not in team_ids:
            db.delete(r)
    for tid in team_ids:
        if tid not in have:
            db.add(AgencyTeamMember(organization_id=org_id, team_id=tid, user_id=user_id))


def set_locations(db: Session, org_id: str, actor: User, user_id: str, location_ids: List[str]) -> Dict[str, Any]:
    u = in_workspace(db, org_id, user_id)
    if u is None:
        raise AccessError("Person not found.", 404)
    _refuse_if_protected(db, org_id, actor, u, "change locations")
    locs = _check_locations(db, org_id, location_ids)
    c = AX.ceiling(db, actor, org_id)
    if c["location_ids"] is not None:
        current = set(AX.user_location_ids(db, org_id, u.id))
        if (set(locs) ^ current) - set(c["location_ids"]):
            raise AccessError("You can only add or remove locations you work in.", 403)
    _set_locations(db, org_id, u.id, locs)
    _audit(db, org_id, actor, "locations_changed", "user", u.id, {"locations": locs})
    db.commit()
    return member_detail(db, org_id, actor, u.id)


# ── suspend / reactivate / remove, with the work they own ───────────────────

def owned_work(db: Session, org_id: str, user_id: str) -> Dict[str, int]:
    now = _now()
    open_r = db.query(AgencyRenewal).filter(AgencyRenewal.organization_id == org_id,
                                            AgencyRenewal.assigned_user_id == user_id, AgencyRenewal.outcome.is_(None))
    ids = [r.id for r in open_r.all()]
    return {
        "renewals": len(ids),
        "appointments": db.query(AgencyAppointment).filter(
            AgencyAppointment.organization_id == org_id, AgencyAppointment.agent_user_id == user_id,
            AgencyAppointment.status.in_(("booked", "confirmed", "pending")), AgencyAppointment.starts_at >= now).count(),
        "clients": db.query(AgencyClientProfile).filter(AgencyClientProfile.organization_id == org_id,
                                                        AgencyClientProfile.servicing_user_id == user_id).count(),
        "conversations": db.query(AgencyRenewalEvent).filter(
            AgencyRenewalEvent.renewal_id.in_(ids or ["-"]), AgencyRenewalEvent.kind == "reply",
            AgencyRenewalEvent.handled.is_(False)).count(),
    }


def _hand_over(db, org_id, actor, user: User, to: Optional[User], release: bool, why: str) -> Dict[str, int]:
    from app.services.agency import retention as R
    moved = {"renewals": 0, "appointments": 0, "clients": 0}
    now = _now()
    for r in db.query(AgencyRenewal).filter(AgencyRenewal.organization_id == org_id,
                                            AgencyRenewal.assigned_user_id == user.id,
                                            AgencyRenewal.outcome.is_(None)).all():
        r.assigned_user_id = to.id if to else None
        r.assigned_at, r.assignment_reason, r.version = now, why, (r.version or 1) + 1
        R.add_event(db, org_id, r.lead_id, "reassigned", renewal_id=r.id, actor=actor.id, is_demo=r.is_demo,
                    detail={"from": user.id, "from_name": _name(user), "to": to.id if to else None,
                            "to_name": _name(to) if to else "shared queue", "reason": why})
        moved["renewals"] += 1
    for a in db.query(AgencyAppointment).filter(AgencyAppointment.organization_id == org_id,
                                                AgencyAppointment.agent_user_id == user.id,
                                                AgencyAppointment.status.in_(("booked", "confirmed", "pending")),
                                                AgencyAppointment.starts_at >= now).all():
        if to is not None:
            a.agent_user_id = to.id
        else:
            a.status = "needs_reassignment"
        moved["appointments"] += 1
    for c in db.query(AgencyClientProfile).filter(AgencyClientProfile.organization_id == org_id,
                                                  AgencyClientProfile.servicing_user_id == user.id).all():
        c.servicing_user_id = to.id if to else None
        moved["clients"] += 1
    return moved


def change_status(db: Session, org_id: str, actor: User, user_id: str, action: str,
                  reassign_to: Optional[str] = None, release_to_queue: bool = False) -> Dict[str, Any]:
    u = in_workspace(db, org_id, user_id)
    if u is None:
        raise AccessError("Person not found.", 404)
    _refuse_if_protected(db, org_id, actor, u, action)
    row = AX.member_row(db, org_id, u.id)
    if row is None:
        raise AccessError("Give this person an access profile before changing their status.")
    if row.is_sample:
        raise AccessError("Sample staff are part of the demo and can't be suspended or removed.")
    moved = None
    if action in ("suspend", "remove"):
        work = owned_work(db, org_id, u.id)
        has_work = any(work.values())
        to = None
        if reassign_to:
            to = in_workspace(db, org_id, reassign_to)
            r2 = AX.member_row(db, org_id, reassign_to) if to is not None else None
            if to is None or to.id == u.id or (r2 is not None and r2.status != "active") or not to.is_active:
                raise AccessError("Pick an active member of this agency to take over the work.")
        if action == "remove" and has_work and to is None and not release_to_queue:
            raise AccessError("%s still owns work. Reassign it or release it to the shared queue first." % _name(u),
                              409, {"owned_work": work})
        if has_work and (to is not None or release_to_queue):
            moved = _hand_over(db, org_id, actor, u, to, release_to_queue and to is None,
                               "Offboarding: %s %s" % (_name(u), "removed" if action == "remove" else "suspended"))
        row.status = "suspended" if action == "suspend" else "removed"
        if action == "remove":
            try:
                from app.models.sales_models import Membership
                for m in db.query(Membership).filter(Membership.scope_type == "customer_org",
                                                     Membership.scope_id == org_id, Membership.user_id == u.id).all():
                    m.is_active = False
            except Exception:  # noqa: BLE001
                pass
            for inv in db.query(AgencyInvite).filter(AgencyInvite.organization_id == org_id,
                                                     AgencyInvite.user_id == u.id, AgencyInvite.status == PENDING).all():
                inv.status, inv.revoked_at, inv.token_hash = REVOKED, _now(), None
    elif action == "reactivate":
        if row.status == "active":
            raise AccessError("Already active.")
        # Only what was saved - and only if the actor could grant it today.
        eff = AX.effective(db, u, org_id, row)
        if eff["profile"] and eff["profile"]["archived"]:
            raise AccessError("Their saved profile has been retired. Choose a new profile for them first.")
        _delegate(db, org_id, actor, eff["permissions"], eff["record_scope"], eff["location_ids"], what="reactivate")
        row.status = "active"
        if u.organization_id != org_id:
            from app.services.workspace_access import grant_workspace_membership
            grant_workspace_membership(db, u.id, org_id, role=MEMBER_ROLE, granted_by=actor.id, commit=False,
                                       check_capacity=False)
    else:
        raise AccessError("Unknown action.")
    row.status_changed_at, row.updated_by_id = _now(), actor.id
    _audit(db, org_id, actor, {"suspend": "suspended", "remove": "removed", "reactivate": "reactivated"}[action],
           "user", u.id, {"moved": moved, "reassigned_to": reassign_to, "released": bool(release_to_queue)})
    db.commit()
    return member_detail(db, org_id, actor, u.id)


# ── teams ───────────────────────────────────────────────────────────────────

def team_json(db, t: AgencyTeam, names=None) -> Dict[str, Any]:
    members = [r[0] for r in db.query(AgencyTeamMember.user_id).filter(AgencyTeamMember.team_id == t.id).all()]
    names = names or {u.id: _name(u) for u in db.query(User).filter(User.id.in_(members + [t.lead_user_id or "-"])).all()}
    return {"id": t.id, "name": t.name, "description": t.description, "lead_user_id": t.lead_user_id,
            "lead_name": names.get(t.lead_user_id), "location_ids": _jl(t.location_ids_json, []),
            "shared_queue": bool(t.shared_queue), "routing_eligible": bool(t.routing_eligible),
            "archived": bool(t.is_archived), "members": [{"user_id": m, "name": names.get(m)} for m in members]}


def teams(db: Session, org_id: str, include_archived: bool = False) -> List[Dict[str, Any]]:
    q = db.query(AgencyTeam).filter(AgencyTeam.organization_id == org_id)
    if not include_archived:
        q = q.filter(AgencyTeam.is_archived.is_(False))
    return [team_json(db, t) for t in q.order_by(AgencyTeam.name).all()]


def _team(db, org_id, tid) -> AgencyTeam:
    t = db.query(AgencyTeam).filter(AgencyTeam.id == tid, AgencyTeam.organization_id == org_id).first()
    if t is None:
        raise AccessError("Team not found.", 404)
    return t


def _team_fields(db, org_id, actor, t: AgencyTeam, data: Dict[str, Any]):
    if "name" in data:
        if not (data["name"] or "").strip():
            raise AccessError("Give the team a name.")
        t.name = data["name"].strip()[:80]
    if "description" in data:
        t.description = (data["description"] or "")[:400] or None
    if "location_ids" in data:
        locs = _check_locations(db, org_id, data["location_ids"] or [])
        c = AX.ceiling(db, actor, org_id)
        if c["location_ids"] is not None and set(locs) - set(c["location_ids"]):
            raise AccessError("You can only give a team locations you work in.", 403)
        t.location_ids_json = json.dumps(locs)
    if "lead_user_id" in data:
        lead = data["lead_user_id"]
        if lead and in_workspace(db, org_id, lead) is None:
            raise AccessError("The team lead isn't in this agency.")
        t.lead_user_id = lead or None
    for f in ("shared_queue", "routing_eligible"):
        if f in data and data[f] is not None:
            setattr(t, f, bool(data[f]))


def create_team(db: Session, org_id: str, actor: User, data: Dict[str, Any]) -> Dict[str, Any]:
    t = AgencyTeam(organization_id=org_id, name="", created_by_id=actor.id)
    _team_fields(db, org_id, actor, t, {"name": data.get("name"), **data})
    db.add(t)
    db.flush()
    for uid in data.get("member_ids") or []:
        _add_member(db, org_id, actor, t, uid)
    if t.lead_user_id:
        _add_member(db, org_id, actor, t, t.lead_user_id, quiet=True)
    _audit(db, org_id, actor, "team_created", "agency_team", t.id, {"name": t.name})
    db.commit()
    return team_json(db, t)


def update_team(db: Session, org_id: str, actor: User, tid: str, data: Dict[str, Any]) -> Dict[str, Any]:
    t = _team(db, org_id, tid)
    if t.is_archived and not data.get("restore"):
        raise AccessError("That team is archived.")
    before = team_json(db, t)
    _team_fields(db, org_id, actor, t, data)
    if data.get("archive"):
        t.is_archived = True
    if data.get("restore"):
        t.is_archived = False
    for uid in data.get("add_member_ids") or []:
        _add_member(db, org_id, actor, t, uid)
    for uid in data.get("remove_member_ids") or []:
        db.query(AgencyTeamMember).filter(AgencyTeamMember.team_id == t.id, AgencyTeamMember.user_id == uid).delete()
    if t.lead_user_id:
        _add_member(db, org_id, actor, t, t.lead_user_id, quiet=True)
    _audit(db, org_id, actor, "team_archived" if data.get("archive") else "team_updated", "agency_team", t.id,
           {"before": {k: before[k] for k in ("name", "lead_user_id", "location_ids")}, "change": {
               k: v for k, v in data.items() if k not in ("description",)}})
    db.commit()
    return team_json(db, t)


def _add_member(db, org_id, actor, t: AgencyTeam, uid: str, quiet: bool = False):
    u = in_workspace(db, org_id, uid)
    if u is None:
        raise AccessError("That person isn't in this agency.", 404)
    if db.query(AgencyTeamMember.id).filter(AgencyTeamMember.team_id == t.id, AgencyTeamMember.user_id == uid).first():
        return
    db.add(AgencyTeamMember(organization_id=org_id, team_id=t.id, user_id=uid))


# ── locations ───────────────────────────────────────────────────────────────

def locations(db: Session, org_id: str, include_archived: bool = True) -> List[Dict[str, Any]]:
    from app.services.agency.retention import location_verified
    out = []
    for l in _org_locations(db, org_id, include_archived):
        staff = db.query(UserLocation).filter(UserLocation.location_id == l.id).count()
        open_work = db.query(AgencyRenewal).filter(AgencyRenewal.location_id == l.id, AgencyRenewal.outcome.is_(None)).count()
        out.append({"id": l.id, "name": l.name, "city": l.city, "state": l.state, "address": l.address_line1,
                    "phone": l.phone, "timezone": l.timezone, "is_primary": bool(l.is_primary),
                    "archived": not bool(l.is_active), "verified": location_verified(l), "staff": staff,
                    "open_renewals": open_work})
    return out


def _loc(db, org_id, lid) -> Location:
    l = db.query(Location).filter(Location.id == lid, Location.organization_id == org_id).first()
    if l is None:
        raise AccessError("Location not found.", 404)
    return l


def create_location(db: Session, org_id: str, actor: User, data: Dict[str, Any]) -> Dict[str, Any]:
    name = (data.get("name") or "").strip()
    if not name:
        raise AccessError("Give the location a name.")
    if db.query(Location.id).filter(Location.organization_id == org_id, func.lower(Location.name) == name.lower(),
                                    Location.is_active.is_(True)).first():
        raise AccessError("A location called %s already exists." % name)
    import re
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "location"
    base, i = slug, 2
    while db.query(Location.id).filter(Location.organization_id == org_id, Location.slug == slug).first():
        slug, i = "%s-%d" % (base, i), i + 1
    l = Location(organization_id=org_id, name=name[:80], slug=slug, is_primary=False, is_active=True,
                 city=(data.get("city") or "").strip()[:80] or None, state=(data.get("state") or "TX")[:2],
                 address_line1=(data.get("address") or "").strip()[:160] or None,
                 phone=(data.get("phone") or "").strip()[:40] or None, timezone=data.get("timezone") or None,
                 notes="Verified office." if data.get("address") else None, created_by=actor.id)
    db.add(l)
    db.flush()
    _audit(db, org_id, actor, "location_created", "location", l.id, {"name": l.name})
    db.commit()
    return next(x for x in locations(db, org_id) if x["id"] == l.id)


def update_location(db: Session, org_id: str, actor: User, lid: str, data: Dict[str, Any]) -> Dict[str, Any]:
    l = _loc(db, org_id, lid)
    c = AX.ceiling(db, actor, org_id)
    if c["location_ids"] is not None and l.id not in c["location_ids"]:
        raise AccessError("Location not found.", 404)
    before = {"name": l.name, "active": l.is_active}
    if data.get("archive"):
        open_r = db.query(AgencyRenewal).filter(AgencyRenewal.location_id == l.id, AgencyRenewal.outcome.is_(None)).count()
        clients = db.query(AgencyClientProfile).filter(AgencyClientProfile.location_id == l.id).count()
        move_to = data.get("move_work_to")
        if (open_r or clients) and not move_to:
            raise AccessError("%s has %d open renewals and %d clients. Choose a location to move them to before "
                              "archiving." % (l.name, open_r, clients), 409, {"open_renewals": open_r, "clients": clients})
        if move_to:
            dest = _loc(db, org_id, move_to)
            if dest.id == l.id or not dest.is_active:
                raise AccessError("Pick another active location.")
            db.query(AgencyRenewal).filter(AgencyRenewal.location_id == l.id, AgencyRenewal.outcome.is_(None)).update(
                {AgencyRenewal.location_id: dest.id}, synchronize_session=False)
            db.query(AgencyClientProfile).filter(AgencyClientProfile.location_id == l.id).update(
                {AgencyClientProfile.location_id: dest.id}, synchronize_session=False)
        if l.is_primary:
            raise AccessError("Make another location primary before archiving this one.")
        l.is_active = False
    elif data.get("restore"):
        l.is_active = True
    for f, col in (("name", "name"), ("city", "city"), ("phone", "phone"), ("address", "address_line1"),
                   ("timezone", "timezone")):
        if f in data and data[f] is not None:
            setattr(l, col, (data[f] or "").strip()[:160] or None if f != "name" else data[f].strip()[:80])
    if "staff_user_ids" in data and data["staff_user_ids"] is not None:
        want = set(data["staff_user_ids"])
        have = {r.user_id: r for r in db.query(UserLocation).filter(UserLocation.location_id == l.id).all()}
        for uid in want - set(have):
            u = in_workspace(db, org_id, uid)
            if u is None:
                raise AccessError("That person isn't in this agency.", 404)
            db.add(UserLocation(user_id=uid, location_id=l.id, organization_id=org_id))
        for uid in set(have) - want:
            db.delete(have[uid])
    _audit(db, org_id, actor, "location_archived" if data.get("archive") else "location_updated", "location", l.id,
           {"before": before, "change": {k: v for k, v in data.items() if k != "staff_user_ids"},
            "staff": len(data.get("staff_user_ids") or []) if "staff_user_ids" in data else None})
    db.commit()
    return next(x for x in locations(db, org_id) if x["id"] == l.id)


# ── invitations ─────────────────────────────────────────────────────────────

def invite_json(db: Session, inv: AgencyInvite) -> Dict[str, Any]:
    prof = db.query(AgencyAccessProfile).filter(AgencyAccessProfile.id == inv.profile_id).first() if inv.profile_id else None
    by = db.query(User).filter(User.id == inv.invited_by_id).first() if inv.invited_by_id else None
    status = _invite_status(db, inv)
    if inv.kind == NEW_LOGIN and status == PENDING and inv.user_id:
        u = db.query(User).filter(User.id == inv.user_id).first()
        if u is not None and not u.must_change_password:
            status = ACCEPTED
    return {"id": inv.id, "email": inv.email, "name": inv.full_name, "kind": inv.kind, "status": status,
            "profile": prof.name if prof else "Custom", "profile_id": inv.profile_id,
            "record_scope": inv.record_scope, "location_ids": _jl(inv.location_ids_json, []),
            "team_ids": _jl(inv.team_ids_json, []), "invited_by": _name(by), "created_at": _iso(inv.created_at),
            "expires_at": _iso(inv.expires_at), "accepted_at": _iso(inv.accepted_at),
            "last_sent_at": _iso(inv.last_sent_at), "send_count": inv.send_count or 0}


def invites(db: Session, org_id: str) -> List[Dict[str, Any]]:
    return [invite_json(db, i) for i in db.query(AgencyInvite).filter(AgencyInvite.organization_id == org_id)
            .order_by(AgencyInvite.created_at.desc()).all()]


def _load_invite(db, org_id, iid) -> AgencyInvite:
    inv = db.query(AgencyInvite).filter(AgencyInvite.id == iid, AgencyInvite.organization_id == org_id).first()
    if inv is None:
        raise AccessError("Invitation not found.", 404)
    return inv


def _link(base_url: Optional[str], path: str) -> str:
    base = (base_url or "").rstrip("/")
    return base + path if base else path


def invite(db: Session, org_id: str, actor: User, *, email: str, full_name: Optional[str] = None,
           profile_id: Optional[str] = None, grants=None, denies=None, record_scope: Optional[str] = None,
           location_ids=None, team_ids=None, reports_to_user_id: Optional[str] = None,
           base_url: Optional[str] = None, send: bool = False) -> Dict[str, Any]:
    email = (email or "").strip().lower()
    if "@" not in email or len(email) > 254 or " " in email:
        raise AccessError("Enter a valid email address.")
    prof = _profile(db, org_id, profile_id) if profile_id else None
    if prof is None and not grants:
        raise AccessError("Choose an access profile (or individual permissions).")
    if prof is not None and prof.is_archived:
        raise AccessError("That profile is retired.")
    g, d = AX.clean(grants or {}), AX.clean(denies or {}, add_view=False)
    rs = record_scope if record_scope in AX.SCOPES else None
    locs = _check_locations(db, org_id, location_ids or [])
    tms = _check_teams(db, org_id, team_ids or [])
    perms = AX.combine(AX.load(prof.permissions_json) if prof else {}, g, d)
    scope = rs or (prof.record_scope if prof else AX.ASSIGNED)
    _delegate(db, org_id, actor, perms, scope, locs, what="invite")
    if scope != AX.AGENCY and not locs:
        raise AccessError("Choose at least one location for this person.")
    if reports_to_user_id and in_workspace(db, org_id, reports_to_user_id) is None:
        raise AccessError("The person they report to isn't in this agency.")
    dup = db.query(AgencyInvite).filter(AgencyInvite.organization_id == org_id, func.lower(AgencyInvite.email) == email,
                                        AgencyInvite.status == PENDING).first()
    if dup is not None and not (dup.expires_at and dup.expires_at < _now()):
        raise AccessError("%s already has a pending invitation. Resend or revoke it instead." % email, 409)
    existing = db.query(User).filter(func.lower(User.email) == email).first()
    org = db.query(Organization).filter(Organization.id == org_id).first()
    inv = AgencyInvite(organization_id=org_id, email=email, full_name=(full_name or "").strip()[:120] or None,
                       profile_id=prof.id if prof else None, grants_json=json.dumps(g), denies_json=json.dumps(d),
                       record_scope=rs, location_ids_json=json.dumps(locs), team_ids_json=json.dumps(tms),
                       reports_to_user_id=reports_to_user_id or None, invited_by_id=actor.id, status=PENDING,
                       expires_at=_now() + timedelta(days=INVITE_DAYS))
    if existing is not None:
        if getattr(existing, "role", None) in _ELEVATED or existing.organization_id is None:
            raise AccessError("That email belongs to an admin or platform login, which can't be given a member "
                              "profile. Use a separate member login for this person.")
        row = AX.member_row(db, org_id, existing.id)
        if row is not None and row.status == "active":
            raise AccessError("%s is already in this agency. Change their access instead." % email, 409)
        if existing.organization_id == org_id and row is None:
            raise AccessError("%s already works in this agency. Change their access instead." % email, 409)
        if not existing.is_active:
            raise AccessError("That login is disabled.")
        inv.kind, inv.user_id = EXISTING_LOGIN, existing.id
        raw = "agi_" + secrets.token_urlsafe(32)
        inv.token_hash = _hash(raw)
        link = _link(base_url, "/agency-invite?token=%s" % raw)
        db.add(inv)
        db.flush()
    else:
        from app.services import plan_limits
        from app.services import staff_activation as SA
        from app.services.auth_service import hash_password
        from app.services.workspace_access import grant_workspace_membership
        plan_limits.require_capacity(db, org, plan_limits.LIMIT_USERS, adding=1)
        u = User(organization_id=org_id, email=email, full_name=inv.full_name or email.split("@")[0], role=MEMBER_ROLE,
                 password_hash=hash_password(secrets.token_urlsafe(48)), must_change_password=True, is_active=True)
        db.add(u)
        db.flush()
        inv.kind, inv.user_id = NEW_LOGIN, u.id
        db.add(inv)
        db.flush()
        grant_workspace_membership(db, u.id, org_id, role=MEMBER_ROLE, granted_by=actor.id, commit=False,
                                   check_capacity=False)
        _install_access(db, org_id, actor, u, inv)
        _row, raw = SA.issue(db, u, actor, purpose=SA.PURPOSE_SETUP)   # commits; rotates older links
        link = SA.activation_url(base_url, raw)
    _audit(db, org_id, actor, "invited", "agency_invite", inv.id,
           {"email": email, "kind": inv.kind, "profile": prof.name if prof else None, "record_scope": scope,
            "locations": locs})
    out = {"invite": invite_json(db, inv), "link": link, "delivery": None}
    if send:
        out["delivery"] = _send(db, org, inv, link, actor)
    db.commit()
    out["invite"] = invite_json(db, inv)
    return out


def _install_access(db, org_id, actor_or_inviter: User, u: User, inv: AgencyInvite):
    row = AX.member_row(db, org_id, u.id)
    if row is None:
        row = AgencyMemberAccess(organization_id=org_id, user_id=u.id)
        db.add(row)
    row.profile_id, row.grants_json, row.denies_json, row.record_scope = (inv.profile_id, inv.grants_json,
                                                                         inv.denies_json, inv.record_scope)
    row.reports_to_user_id, row.status, row.status_changed_at = inv.reports_to_user_id, "active", _now()
    row.updated_by_id = actor_or_inviter.id
    _set_locations(db, org_id, u.id, _jl(inv.location_ids_json, []))
    _set_teams(db, org_id, u.id, _jl(inv.team_ids_json, []))
    if db.query(AgencyRetentionStaff.id).filter(AgencyRetentionStaff.organization_id == org_id,
                                                AgencyRetentionStaff.user_id == u.id).first() is None:
        db.add(AgencyRetentionStaff(organization_id=org_id, user_id=u.id, title="member", languages='["en"]',
                                    available=True))
    db.flush()


def _send(db: Session, org: Organization, inv: AgencyInvite, link: str, actor: User) -> Dict[str, Any]:
    """Email the link - only when the person pressing Send asked for it."""
    from app.services.email_service import send_email_via_provider
    from app.services import email_identity
    try:
        from app.services.agency.retention import get_settings
        s = get_settings(db, org.id)
        agency = (s.display_name if s and s.display_name else org.name)
    except Exception:  # noqa: BLE001
        agency = org.name
    body = ("<p>Hi%s,</p><p>You've been invited to join <b>%s</b> on EvoSys Pro.</p>"
            "<p><a href=\"%s\">%s</a></p><p>This link works once and expires in %d days. "
            "If you weren't expecting this, you can ignore it.</p>"
            % ((" " + inv.full_name) if inv.full_name else "", agency, link,
               "Set up your login" if inv.kind == NEW_LOGIN else "Sign in and accept", INVITE_DAYS))
    try:
        res = send_email_via_provider(inv.email, "You're invited to %s" % agency, body, org=org,
                                      message_type="onboarding_invitation", sensitivity=email_identity.SENSITIVE,
                                      template_id="agency.team_invitation")
    except Exception as exc:  # noqa: BLE001
        return {"state": "failed", "error": str(exc)[:200]}
    if res.get("success"):
        inv.last_sent_at, inv.send_count = _now(), (inv.send_count or 0) + 1
        _audit(db, org.id, actor, "invite_emailed", "agency_invite", inv.id, {"to": inv.email})
        return {"state": "sent"}
    return {"state": "failed", "error": (res.get("error") or "The mail provider refused it.")[:200]}


def resend(db: Session, org_id: str, actor: User, invite_id: str, base_url: Optional[str] = None,
           send: bool = False) -> Dict[str, Any]:
    """A fresh link (the previous one stops working) and a fresh 7 days."""
    inv = _load_invite(db, org_id, invite_id)
    if invite_json(db, inv)["status"] not in (PENDING, EXPIRED):
        raise AccessError("Only a pending or expired invitation can be resent.")
    prof = _profile(db, org_id, inv.profile_id) if inv.profile_id else None
    if prof is not None and prof.is_archived:
        raise AccessError("That invitation uses a retired profile. Revoke it and invite again.")
    _delegate(db, org_id, actor, AX.combine(AX.load(prof.permissions_json) if prof else {}, AX.load(inv.grants_json),
                                            AX.load(inv.denies_json)),
              inv.record_scope or (prof.record_scope if prof else AX.ASSIGNED), _jl(inv.location_ids_json, []),
              what="resend invitation")
    inv.expires_at, inv.status = _now() + timedelta(days=INVITE_DAYS), PENDING
    if inv.kind == EXISTING_LOGIN:
        raw = "agi_" + secrets.token_urlsafe(32)
        inv.token_hash = _hash(raw)
        link = _link(base_url, "/agency-invite?token=%s" % raw)
    else:
        from app.services import staff_activation as SA
        u = db.query(User).filter(User.id == inv.user_id).first()
        if u is None or not u.must_change_password:
            raise AccessError("They have already set up their login.")
        _row, raw = SA.issue(db, u, actor, purpose=SA.PURPOSE_SETUP)
        link = SA.activation_url(base_url, raw)
    _audit(db, org_id, actor, "invite_resent", "agency_invite", inv.id, {"email": inv.email})
    org = db.query(Organization).filter(Organization.id == org_id).first()
    delivery = _send(db, org, inv, link, actor) if send else None
    db.commit()
    return {"invite": invite_json(db, inv), "link": link, "delivery": delivery}


def revoke(db: Session, org_id: str, actor: User, invite_id: str) -> Dict[str, Any]:
    inv = _load_invite(db, org_id, invite_id)
    if invite_json(db, inv)["status"] not in (PENDING, EXPIRED):
        raise AccessError("Only a pending invitation can be revoked. Remove the person instead.")
    inv.status, inv.revoked_at, inv.token_hash = REVOKED, _now(), None
    if inv.kind == NEW_LOGIN and inv.user_id:
        from app.models.staff_models import StaffActivation
        for r in db.query(StaffActivation).filter(StaffActivation.user_id == inv.user_id,
                                                  StaffActivation.status == "pending").all():
            r.status, r.revoked_at = "revoked", _now()
        row = AX.member_row(db, org_id, inv.user_id)
        if row is not None:
            row.status, row.status_changed_at = "removed", _now()
        u = db.query(User).filter(User.id == inv.user_id).first()
        if u is not None and u.must_change_password and getattr(u, "last_login_at", None) is None:
            u.is_active = False            # a login made only for this invitation and never used
    _audit(db, org_id, actor, "invite_revoked", "agency_invite", inv.id, {"email": inv.email})
    db.commit()
    return invite_json(db, inv)


def invite_preview(db: Session, raw_token: str) -> Dict[str, Any]:
    """What an existing-login invitation is for, without accepting it."""
    inv = db.query(AgencyInvite).filter(AgencyInvite.token_hash == _hash(raw_token or "")).first() if raw_token else None
    if inv is None or inv.kind != EXISTING_LOGIN:
        raise AccessError("This invitation link isn't valid. Ask for a new one.", 404)
    org = db.query(Organization).filter(Organization.id == inv.organization_id).first()
    from app.services.agency.retention import get_settings
    s = get_settings(db, inv.organization_id)
    return {"agency": (s.display_name if s and s.display_name else org.name), "email": inv.email,
            "status": _invite_status(db, inv), "expires_at": _iso(inv.expires_at)}


def accept(db: Session, user: User, raw_token: str) -> Dict[str, Any]:
    """An existing login accepts after signing in normally. Single use, bound to
    the invited login, and rechecked against the inviter's CURRENT authority and
    the profile's CURRENT state."""
    if not raw_token:
        raise AccessError("This invitation link is incomplete.")
    inv = db.query(AgencyInvite).filter(AgencyInvite.token_hash == _hash(raw_token)).first()
    if inv is None or inv.kind != EXISTING_LOGIN:
        raise AccessError("This invitation link isn't valid or was already used. Ask for a new one.")
    if inv.status != PENDING:
        raise AccessError("This invitation was revoked or already used.")
    if inv.expires_at and inv.expires_at < _now():
        raise AccessError("This invitation has expired. Ask the agency to resend it.")
    if user.id != inv.user_id or (user.email or "").lower() != inv.email.lower():
        raise AccessError("This invitation is for a different login. Sign in as %s to accept it." % inv.email, 403)
    if getattr(user, "role", None) not in ("advisor", "viewer"):
        raise AccessError("Admin logins can't accept a member invitation.", 403)
    org_id = inv.organization_id
    inviter = db.query(User).filter(User.id == inv.invited_by_id).first()
    prof = db.query(AgencyAccessProfile).filter(AgencyAccessProfile.id == inv.profile_id).first() if inv.profile_id else None
    if inviter is None or not inviter.is_active or (prof is not None and prof.is_archived):
        raise AccessError("This invitation is no longer valid. Ask the agency to send a new one.")
    perms = AX.combine(AX.load(prof.permissions_json) if prof else {}, AX.load(inv.grants_json), AX.load(inv.denies_json))
    try:
        AX.check_delegation(db, inviter, org_id, perms, inv.record_scope or (prof.record_scope if prof else AX.ASSIGNED),
                            _jl(inv.location_ids_json, []))
    except PermissionError:
        _audit(db, org_id, user, "escalation_denied", "agency_invite", inv.id,
               {"reason": "invitation gives more than its sender now holds"})
        db.commit()
        raise AccessError("This invitation is no longer valid. Ask the agency to send a new one.")
    from app.services.workspace_access import grant_workspace_membership
    grant_workspace_membership(db, user.id, org_id, role=MEMBER_ROLE, granted_by=inv.invited_by_id, commit=False,
                               check_capacity=False)
    inv.status, inv.accepted_at, inv.token_hash = ACCEPTED, _now(), None
    _install_access(db, org_id, inviter, user, inv)
    _audit(db, org_id, user, "invite_accepted", "agency_invite", inv.id, {"user_id": user.id})
    db.commit()
    return {"organization_id": org_id, "path": "/program/insurance"}


def confirm_new_login(db: Session, org_id: str, user: User) -> None:
    """A new login set its own password through the setup link and is now
    signed in. Record acceptance once, and recheck the invitation against the
    inviter's current authority and the profile's current state."""
    inv = (db.query(AgencyInvite).filter(AgencyInvite.organization_id == org_id, AgencyInvite.user_id == user.id,
                                         AgencyInvite.kind == NEW_LOGIN, AgencyInvite.status == PENDING).first())
    if inv is None or user.must_change_password:
        return
    row = AX.member_row(db, org_id, user.id)
    inviter = db.query(User).filter(User.id == inv.invited_by_id).first()
    prof = db.query(AgencyAccessProfile).filter(AgencyAccessProfile.id == inv.profile_id).first() if inv.profile_id else None
    ok = inviter is not None and inviter.is_active and not (prof is not None and prof.is_archived)
    if ok:
        perms = AX.combine(AX.load(prof.permissions_json) if prof else {}, AX.load(inv.grants_json), AX.load(inv.denies_json))
        try:
            AX.check_delegation(db, inviter, org_id, perms, inv.record_scope or (prof.record_scope if prof else AX.ASSIGNED),
                                _jl(inv.location_ids_json, []))
        except PermissionError:
            ok = False
    if not ok:
        if row is not None:
            row.status, row.status_changed_at = "suspended", _now()
        inv.status, inv.revoked_at = REVOKED, _now()
        _audit(db, org_id, user, "escalation_denied", "agency_invite", inv.id,
               {"reason": "invitation no longer valid at first sign-in; access suspended pending review"})
    else:
        inv.status, inv.accepted_at = ACCEPTED, _now()
        _audit(db, org_id, user, "invite_accepted", "agency_invite", inv.id, {"user_id": user.id, "kind": NEW_LOGIN})
    db.commit()


# ── history ─────────────────────────────────────────────────────────────────

_LABELS = {"invited": "Created an invitation", "invite_emailed": "Emailed an invitation",
           "invite_resent": "Resent an invitation", "invite_revoked": "Revoked an invitation",
           "invite_accepted": "Accepted an invitation", "access_changed": "Changed access",
           "locations_changed": "Changed locations", "suspended": "Suspended a member",
           "reactivated": "Reactivated a member", "removed": "Removed a member",
           "profile_created": "Created a profile", "profile_updated": "Edited a profile",
           "profile_retired": "Retired a profile", "team_created": "Created a team", "team_updated": "Updated a team",
           "team_archived": "Archived a team", "location_created": "Created a location",
           "location_updated": "Updated a location", "location_archived": "Archived a location",
           "escalation_denied": "Refused an access change"}


def history(db: Session, org_id: str, user_id: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    from app.models.models import AuditLogEntry
    q = db.query(AuditLogEntry).filter(AuditLogEntry.organization_id == org_id,
                                       AuditLogEntry.action.like("agency_access.%"))
    if user_id:
        q = q.filter((AuditLogEntry.target_id == user_id) | (AuditLogEntry.actor_user_id == user_id))
    rows = q.order_by(AuditLogEntry.created_at.desc()).limit(limit).all()
    names = {u.id: _name(u) for u in db.query(User).filter(User.id.in_({r.actor_user_id for r in rows} |
                                                                         {r.target_id for r in rows} or {"-"})).all()}
    out = []
    for r in rows:
        key = r.action.split(".", 1)[1]
        det = _jl(getattr(r, "details", None), {}) if isinstance(getattr(r, "details", None), str) else (getattr(r, "details", None) or {})
        out.append({"id": r.id, "at": _iso(r.created_at), "action": key, "label": _LABELS.get(key, key),
                    "actor": names.get(r.actor_user_id), "target_type": r.target_type,
                    "target": names.get(r.target_id) or (det.get("email") if isinstance(det, dict) else None) or
                    (det.get("name") if isinstance(det, dict) else None), "details": det})
    return out
