"""WHO MAY USE THE TEAM APP'S CHAT AND TIME - decided on the server, every call.

Nothing here reads an email address. Authority comes from three things that
already exist in the platform:

  1. users.role        god_admin anywhere, super_admin of this platform
                       -> "platform": every team-app right.
  2. memberships       an ACTIVE sales_rep / sales_manager row on one of this
                       platform's brand sales teams -> Chat and Time for
                       themselves.
  3. user_capability_grants, scope (platform, <platform id>)
                       team_chat, team_time            use Chat / Time
                       team_chat_manage                room membership only
                       team_announce                   post announcements
                       team_time_manage                Team Time, scoped by
                                                       team_time_scopes rows

Program Access (workspaces) is a separate answer - /auth/my-contexts - and is
neither read nor widened here. Chat or Time can be granted to someone with no
workspace at all, and a workspace grants neither.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.models import Platform, User
from app.models.sales_models import (BRAND_SALES_ROLES, ROLE_BRAND_EXECUTIVE,
                                     SCOPE_BRAND_SALES_ORG, SCOPE_PLATFORM,
                                     BrandSalesOrg, Membership)
from app.models.models import UserCapabilityGrant

TEAM_KEYS = ("team_chat", "team_time", "team_chat_manage", "team_announce",
             "team_time_manage")
PLATFORM_ROLES = ("god_admin", "super_admin")


@dataclass
class TeamAccess:
    user_id: str
    platform_id: Optional[str]
    platform_name: Optional[str] = None
    is_platform: bool = False
    sales_member: bool = False
    grants: List[str] = field(default_factory=list)

    @property
    def chat(self) -> bool:
        return bool(self.platform_id) and (
            self.is_platform or self.sales_member
            or bool({"team_chat", "team_chat_manage", "team_announce"} & set(self.grants)))

    @property
    def time(self) -> bool:
        return bool(self.platform_id) and (
            self.is_platform or self.sales_member or "team_time" in self.grants)

    @property
    def chat_manage(self) -> bool:
        return bool(self.platform_id) and (self.is_platform or "team_chat_manage" in self.grants)

    @property
    def announce(self) -> bool:
        return bool(self.platform_id) and (self.is_platform or "team_announce" in self.grants)

    @property
    def time_manage(self) -> bool:
        return bool(self.platform_id) and (self.is_platform or "team_time_manage" in self.grants)

    def as_dict(self) -> Dict:
        return {
            "platform_id": self.platform_id,
            "platform_name": self.platform_name,
            "is_platform": self.is_platform,
            "chat": self.chat, "time": self.time,
            "chat_manage": self.chat_manage, "announce": self.announce,
            "time_manage": self.time_manage,
            "manage_access": self.is_platform,
        }


def _brand_org_ids(db: Session, platform_id: str) -> List[str]:
    return [r.id for r in db.query(BrandSalesOrg.id)
            .filter(BrandSalesOrg.platform_id == platform_id).all()]


def resolve_platform_id(db: Session, user: User) -> Optional[str]:
    """Which company's team this login belongs to.

    The user's own platform first; then the platform of a brand sales team they
    sell for; then a platform an executive assignment or a team grant names.
    The platform owner (no platform of their own) uses the first platform that
    has a sales team - there is one company today, and this is written down
    rather than assumed."""
    if getattr(user, "platform_id", None):
        return user.platform_id
    m = (db.query(BrandSalesOrg.platform_id)
         .join(Membership, Membership.scope_id == BrandSalesOrg.id)
         .filter(Membership.user_id == user.id,
                 Membership.scope_type == SCOPE_BRAND_SALES_ORG,
                 Membership.is_active.is_(True))
         .first())
    if m:
        return m[0]
    e = (db.query(Membership.scope_id)
         .filter(Membership.user_id == user.id, Membership.scope_type == SCOPE_PLATFORM,
                 Membership.role == ROLE_BRAND_EXECUTIVE, Membership.is_active.is_(True))
         .first())
    if e:
        return e[0]
    g = (db.query(UserCapabilityGrant.scope_id)
         .filter(UserCapabilityGrant.user_id == user.id,
                 UserCapabilityGrant.scope_type == SCOPE_PLATFORM,
                 UserCapabilityGrant.capability.in_(TEAM_KEYS),
                 UserCapabilityGrant.is_active.is_(True))
         .first())
    if g:
        return g[0]
    if getattr(user, "role", None) == "god_admin":
        row = (db.query(BrandSalesOrg.platform_id).order_by(BrandSalesOrg.created_at).first())
        if row:
            return row[0]
        p = db.query(Platform.id).order_by(Platform.name).first()
        return p[0] if p else None
    return None


def team_grants(db: Session, user_id: str, platform_id: str) -> List[str]:
    rows = (db.query(UserCapabilityGrant.capability)
            .filter(UserCapabilityGrant.user_id == user_id,
                    UserCapabilityGrant.scope_type == SCOPE_PLATFORM,
                    UserCapabilityGrant.scope_id == platform_id,
                    UserCapabilityGrant.capability.in_(TEAM_KEYS),
                    UserCapabilityGrant.is_active.is_(True))
            .all())
    return sorted({r[0] for r in rows})


def is_platform_user(user: User, platform_id: Optional[str]) -> bool:
    role = getattr(user, "role", None)
    if role == "god_admin":
        return True
    if role == "super_admin":
        return not user.platform_id or user.platform_id == platform_id
    return False


def sales_member_ids(db: Session, platform_id: str) -> Set[str]:
    orgs = _brand_org_ids(db, platform_id)
    if not orgs:
        return set()
    rows = (db.query(Membership.user_id)
            .filter(Membership.scope_type == SCOPE_BRAND_SALES_ORG,
                    Membership.scope_id.in_(orgs),
                    Membership.role.in_(BRAND_SALES_ROLES),
                    Membership.is_active.is_(True))
            .all())
    return {r[0] for r in rows}


def access_for(db: Session, user: User) -> TeamAccess:
    if not user or not getattr(user, "is_active", True):
        return TeamAccess(user_id=getattr(user, "id", None), platform_id=None)
    pid = resolve_platform_id(db, user)
    a = TeamAccess(user_id=user.id, platform_id=pid)
    if not pid:
        return a
    p = db.query(Platform).filter(Platform.id == pid).first()
    a.platform_name = p.name if p else None
    a.is_platform = is_platform_user(user, pid)
    a.sales_member = user.id in sales_member_ids(db, pid)
    a.grants = team_grants(db, user.id, pid)
    return a


def require(a: TeamAccess, right: str) -> None:
    if not getattr(a, right):
        raise HTTPException(status_code=403, detail={
            "chat": "Chat is not turned on for your account.",
            "time": "Time is not turned on for your account.",
            "chat_manage": "Managing chat rooms is not part of your access.",
            "announce": "Posting Company Announcements is not part of your access.",
            "time_manage": "Team Time is not part of your access.",
            "is_platform": "Only the platform can change team access.",
        }.get(right, "Not available for your account."))


# ── Who is on the team ───────────────────────────────────────────────────────

def _platform_admins(db: Session, platform_id: str) -> Set[str]:
    rows = (db.query(User).filter(User.is_active.is_(True),
                                  User.role.in_(PLATFORM_ROLES)).all())
    return {u.id for u in rows if is_platform_user(u, platform_id)}


def _grant_holders(db: Session, platform_id: str, keys) -> Set[str]:
    rows = (db.query(UserCapabilityGrant.user_id)
            .filter(UserCapabilityGrant.scope_type == SCOPE_PLATFORM,
                    UserCapabilityGrant.scope_id == platform_id,
                    UserCapabilityGrant.capability.in_(tuple(keys)),
                    UserCapabilityGrant.is_active.is_(True)).all())
    return {r[0] for r in rows}


def _active(db: Session, ids: Set[str]) -> List[User]:
    if not ids:
        return []
    return (db.query(User).filter(User.id.in_(list(ids)), User.is_active.is_(True))
            .order_by(User.full_name).all())


def chat_people(db: Session, platform_id: str) -> List[User]:
    ids = (_platform_admins(db, platform_id) | sales_member_ids(db, platform_id)
           | _grant_holders(db, platform_id, ("team_chat", "team_chat_manage", "team_announce")))
    return _active(db, ids)


def time_people(db: Session, platform_id: str) -> List[User]:
    ids = (_platform_admins(db, platform_id) | sales_member_ids(db, platform_id)
           | _grant_holders(db, platform_id, ("team_time",)))
    return _active(db, ids)


def access_candidates(db: Session, platform_id: str) -> List[User]:
    """Everyone the platform may grant team access to from the Account screen:
    the platform's own users, its sales teams and executives, and anyone who
    already holds a team grant."""
    ids = {r[0] for r in db.query(User.id).filter(User.platform_id == platform_id).all()}
    ids |= sales_member_ids(db, platform_id)
    ids |= {r[0] for r in db.query(Membership.user_id).filter(
        Membership.scope_type == SCOPE_PLATFORM, Membership.scope_id == platform_id,
        Membership.is_active.is_(True)).all()}
    ids |= _grant_holders(db, platform_id, TEAM_KEYS)
    ids |= _platform_admins(db, platform_id)
    return _active(db, ids)
