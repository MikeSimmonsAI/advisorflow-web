"""THE WHOLESALE TEAM - who is on it, what each person may do, who they report to.

Built on the existing named-login grant (product_access): a grant is a
Membership row (scope_type="product", scope_id="wholesale"). Its `role`
column used to always be "member"; it now says WHAT KIND of team member the
person is, and `reports_to_user_id` (already on Membership) is the team tree:

    Owner (Mike)  ->  partner / managers  ->  team leads  ->  field specialists, canvassers

    member            Full access. Exactly what every grant meant before this file.
    manager           Wholesale only. Runs a team.
    team_lead         Wholesale only. Runs a small crew.
    field_specialist  Wholesale only, field kit (appointments, walkthroughs, offers).
    canvasser         Wholesale only, door-knocking kit.

Every grant that exists today is "member", so shipping this changes nobody.

`reports_to_user_id` is an org-chart fact used for "my team" views. It never
grants access by itself.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.models import User
from app.models.sales_models import Membership, SCOPE_PRODUCT
from app.services import product_access as PA

MEMBER = PA.MEMBER
MANAGER = "manager"
TEAM_LEAD = "team_lead"
FIELD_SPECIALIST = "field_specialist"
CANVASSER = "canvasser"

ROLES = (MEMBER, MANAGER, TEAM_LEAD, FIELD_SPECIALIST, CANVASSER)
ROLE_LABELS = {
    MEMBER: "Full access",
    MANAGER: "Manager",
    TEAM_LEAD: "Team lead",
    FIELD_SPECIALIST: "Field specialist",
    CANVASSER: "Canvasser",
}
ROLE_HELP = {
    MEMBER: "Everything they can see today, including the advisor side.",
    MANAGER: "Wholesale side only. Runs a team and sees their people's work.",
    TEAM_LEAD: "Wholesale side only. Runs a small crew.",
    FIELD_SPECIALIST: "Wholesale side only. Appointments, walkthroughs and offers in the field.",
    CANVASSER: "Wholesale side only. Knocks doors and passes hot leads up.",
}
# Logins that never see the advisor / CRM side of the app.
WHOLESALE_ONLY = (MANAGER, TEAM_LEAD, FIELD_SPECIALIST, CANVASSER)
# Logins that get the locked-down field kit.
FIELD_ROLES = (FIELD_SPECIALIST, CANVASSER)


def _row(db: Session, user_id: str, active_only: bool = True) -> Optional[Membership]:
    q = db.query(Membership).filter(Membership.scope_type == SCOPE_PRODUCT,
                                    Membership.scope_id == PA.WHOLESALE,
                                    Membership.user_id == user_id)
    if active_only:
        q = q.filter(Membership.is_active.is_(True))
    return q.first()


def role_of(db: Session, user: Optional[User]) -> Optional[str]:
    """The person's team role, or None when they hold no active grant."""
    if user is None or not getattr(user, "id", None):
        return None
    row = _row(db, user.id)
    if row is None:
        return None
    return row.role if row.role in ROLES else MEMBER


def is_wholesale_only(db: Session, user: Optional[User]) -> bool:
    if getattr(user, "role", None) in ("god_admin", "super_admin"):
        return False
    return role_of(db, user) in WHOLESALE_ONLY


def is_field(db: Session, user: Optional[User]) -> bool:
    if getattr(user, "role", None) in ("god_admin", "super_admin"):
        return False
    return role_of(db, user) in FIELD_ROLES


# ── the team page ───────────────────────────────────────────────────────────

def _person(u: User) -> Dict:
    return {"user_id": u.id, "email": u.email, "name": getattr(u, "full_name", None) or u.email,
            "account_role": getattr(u, "role", None), "is_active": bool(getattr(u, "is_active", True))}


def team(db: Session, org_id: str) -> Dict:
    """Everyone in this workspace: who is on the Wholesale team (with role and
    manager) and who is not yet. Other organizations' people never appear."""
    people = db.query(User).filter(User.organization_id == org_id).order_by(User.created_at.asc()).all()
    ids = [u.id for u in people]
    rows = {}
    if ids:
        for m in (db.query(Membership)
                  .filter(Membership.scope_type == SCOPE_PRODUCT, Membership.scope_id == PA.WHOLESALE,
                          Membership.user_id.in_(ids)).all()):
            rows[m.user_id] = m
    members, others = [], []
    for u in people:
        m = rows.get(u.id)
        if m is not None and m.is_active:
            role = m.role if m.role in ROLES else MEMBER
            members.append({**_person(u), "role": role, "role_label": ROLE_LABELS[role],
                            "wholesale_only": role in WHOLESALE_ONLY,
                            "reports_to_user_id": m.reports_to_user_id,
                            "added_at": m.created_at.isoformat() + "Z" if m.created_at else None})
        else:
            others.append({**_person(u), "was_member": m is not None})
    return {
        "lock_on": PA.lock_on(db),
        "roles": [{"key": r, "label": ROLE_LABELS[r], "help": ROLE_HELP[r],
                   "wholesale_only": r in WHOLESALE_ONLY} for r in ROLES],
        "members": members,
        "not_on_team": others,
    }


def _check_manager(db: Session, org_id: str, user_id: str, reports_to: Optional[str]) -> Optional[str]:
    """The manager must be an active team member of the same workspace, and the
    tree may not loop back on itself."""
    if not reports_to:
        return None
    if reports_to == user_id:
        raise ValueError("Someone cannot report to themselves.")
    boss = db.query(User).filter(User.id == reports_to, User.organization_id == org_id).first()
    if boss is None or _row(db, boss.id) is None:
        raise ValueError("The manager you picked is not on the Wholesale team.")
    seen, cur = {user_id}, reports_to
    while cur:
        if cur in seen:
            raise ValueError("That would make a loop: they would end up reporting to themselves.")
        seen.add(cur)
        up = _row(db, cur)
        cur = up.reports_to_user_id if up is not None else None
    return reports_to


def _in_org(db: Session, org_id: str, user_id: str) -> User:
    u = db.query(User).filter(User.id == user_id, User.organization_id == org_id).first()
    if u is None:
        raise LookupError("No such person in this workspace.")
    return u


def add_or_update(db: Session, org_id: str, actor: User, user_id: str, role: str,
                  reports_to_user_id: Optional[str] = None) -> Dict:
    """Put someone on the team (or change their role / manager)."""
    if role not in ROLES:
        raise ValueError("Unknown role.")
    u = _in_org(db, org_id, user_id)
    if role in WHOLESALE_ONLY and getattr(u, "role", None) in ("org_admin", "super_admin", "god_admin"):
        raise ValueError("An admin login cannot be made wholesale-only. Use a separate login for field work.")
    if u.id == actor.id and role != MEMBER:
        raise ValueError("You cannot lock your own login down.")
    reports_to = _check_manager(db, org_id, u.id, reports_to_user_id)
    first_ever = not PA.lock_on(db)
    PA.grant(db, u.email, granted_by=actor.id)
    # The person setting up the very first team member keeps their own access:
    # the first grant turns the named-login rule on for everyone.
    if first_ever and getattr(actor, "role", None) != "god_admin" and _row(db, actor.id) is None:
        PA.grant(db, actor.email, granted_by=actor.id)
    row = _row(db, u.id)
    row.role = role
    row.reports_to_user_id = reports_to
    db.commit()
    return team(db, org_id)


def remove(db: Session, org_id: str, actor: User, user_id: str) -> Dict:
    """Take someone off the team. Their login and their work stay; they simply
    can no longer open Wholesale. Anyone who reported to them moves up a level."""
    u = _in_org(db, org_id, user_id)
    if u.id == actor.id:
        raise ValueError("You cannot take yourself off the team.")
    row = _row(db, u.id)
    if row is None:
        return team(db, org_id)
    for child in (db.query(Membership)
                  .filter(Membership.scope_type == SCOPE_PRODUCT, Membership.scope_id == PA.WHOLESALE,
                          Membership.reports_to_user_id == u.id).all()):
        child.reports_to_user_id = row.reports_to_user_id
    row.is_active = False
    db.commit()
    return team(db, org_id)
