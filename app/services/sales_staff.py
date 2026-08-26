"""Adding and managing the people who SELL a brand.

THE GAP THIS CLOSES. Until now there was no supported way to add a brand-sales
user. `POST /god/users` creates a users row but no membership; nothing anywhere
created a `SCOPE_BRAND_SALES_ORG` membership. Every real one in production was
written by `seed_evosyspro_sales.py`. That is why two people existed, were
correct, and still could not log in - they were seeded, not created through any
path a human could take twice.

ONE HUMAN, ONE IDENTITY, MANY MEMBERSHIPS. The identity is looked up by
NORMALISED email before anything is created. If the person already exists -
because they are a customer advisor, or sell another brand - that row is reused
and the new membership is added beside their existing ones. Nothing existing is
touched. A second users row for the same human is the failure mode this service
exists to prevent, and `find_identity` is the only way in.

organization_id STAYS NULL for a brand-sales-only user. It is a positive
assertion that they sell the product rather than use a tenant of it, and this
service never sets it - not to a default, not to "the first organization", not
at all. A person who ALREADY has an organization_id (a customer advisor being
given a sales seat) keeps it: their tenancy is a separate, legitimate fact and
removing it would revoke access they were not asked about.

NO PASSWORD IS EVER CREATED HERE. A new identity gets a secret that is generated,
hashed and discarded inside one function, and `must_change_password = True`. The
person is reached through the existing one-time link in `staff_activation`. There
is no code path in this module that can return a password, because none of them
ever holds one.

ROLES. `BRAND_SALES_ROLES` is exactly ("sales_manager", "sales_rep"). There is no
Product Specialist role in the vocabulary and this module will not invent one -
`assert_role` refuses anything else rather than writing a string the guards do
not understand, which would produce a membership that grants nothing and reads
like a bug in the workspace.
"""
from __future__ import annotations

import re
import secrets
from datetime import datetime
from typing import Optional, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.models import User
from app.models.sales_models import (
    Membership, BrandSalesOrg, SCOPE_BRAND_SALES_ORG,
    BRAND_SALES_ROLES, ROLE_SALES_MANAGER, ROLE_SALES_REP,
)
from app.services.auth_service import hash_password
from app.routers.audit_log_router import log_action

# A deliberately loose shape check. The authority on whether an address works is
# whether mail reaches it; refusing anything with an @ and a dot would reject
# real addresses and teach nobody anything.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(raw: str) -> str:
    """Lowercased and trimmed. This is the ONLY form used to look up or store an
    identity, so `Blake@X.com` and `blake@x.com` can never become two people."""
    return (raw or "").strip().lower()


def assert_email(raw: str) -> str:
    email = normalize_email(raw)
    if not _EMAIL.match(email):
        raise HTTPException(status_code=400,
                            detail="That does not look like an email address.")
    return email


def assert_role(role: str) -> str:
    if role not in BRAND_SALES_ROLES:
        raise HTTPException(
            status_code=400,
            detail="Role must be one of: %s. There is no other brand-sales role "
                   "in the system." % ", ".join(BRAND_SALES_ROLES))
    return role


def _unknowable_password() -> str:
    """Generated, hashed by the caller, and discarded in the same breath.

    The account must have SOME hash so that no code path treats it as
    password-less, and nobody - including whoever runs this - may ever know what
    it is. Same pattern as `customer_activation`.
    """
    return secrets.token_urlsafe(48)


# ── identity ────────────────────────────────────────────────────────────────

def find_identity(db: Session, email: str) -> Optional[User]:
    """The canonical users row for this address, or None. Normalised lookup."""
    return db.query(User).filter(User.email == normalize_email(email)).first()


def identity_summary(db: Session, user: User) -> dict:
    """What the operator is shown BEFORE they commit to anything.

    Deliberately includes every existing membership, including ones in other
    brands and customer scopes. Adding a seat to somebody who already sells for
    a different brand is a real decision, and the person making it should see
    that fact rather than discover it afterwards.
    """
    rows = (db.query(Membership)
              .filter(Membership.user_id == user.id)
              .order_by(Membership.created_at.asc()).all())
    out = []
    for m in rows:
        name = None
        if m.scope_type == SCOPE_BRAND_SALES_ORG:
            b = db.query(BrandSalesOrg).filter(BrandSalesOrg.id == m.scope_id).first()
            name = b.name if b else None
        out.append({"id": m.id, "scope_type": m.scope_type, "scope_id": m.scope_id,
                    "scope_name": name, "role": m.role, "is_active": bool(m.is_active)})
    return {
        "exists": True,
        "user_id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "is_active": bool(user.is_active),
        "organization_id": user.organization_id,
        "has_password": bool(user.password_hash),
        "has_signed_in": user.last_login_at is not None,
        "must_change_password": bool(user.must_change_password),
        "memberships": out,
    }


def create_identity(db: Session, email: str, full_name: str, actor: User) -> User:
    """One new users row, with NO tenancy and no knowable password.

    Raises rather than returning an existing row: the caller must have already
    looked, because "create" silently meaning "reuse" is how duplicates get made
    somewhere else instead.
    """
    email = assert_email(email)
    full_name = (full_name or "").strip()
    if len(full_name) < 2:
        raise HTTPException(status_code=400, detail="A full name is required.")
    if find_identity(db, email) is not None:
        raise HTTPException(status_code=409,
                            detail="A user with that email already exists. Reuse "
                                   "that identity rather than creating a second one.")

    user = User(
        email=email,
        full_name=full_name,
        # NOT a default, NOT "the first organization". A brand-sales user belongs
        # to no customer tenant, and that is the whole point.
        organization_id=None,
        password_hash=hash_password(_unknowable_password()),
        must_change_password=True,
        # `users.role` stays the baseline tenant role; brand-sales capability
        # comes from the membership. Writing "sales_rep" here would put a string
        # the tenant guards do not know into the column they read.
        role="advisor",
        is_active=True,
    )
    db.add(user)
    db.flush()

    log_action(
        db, None, actor.id,
        action="sales_user_created",
        target_type="user", target_id=user.id,
        after={"email": user.email, "full_name": user.full_name,
               "organization_id": None, "users_role": user.role},
        details={"created_by": actor.email},
        note="Brand-sales identity created with organization_id NULL and an "
             "unknowable password. Access is granted by membership and unlocked "
             "by a one-time link.",
        commit=False,
    )
    return user


# ── membership ──────────────────────────────────────────────────────────────

def get_membership(db: Session, user_id: str, bso_id: str) -> Optional[Membership]:
    """This person's brand-sales membership in this brand, active or not.

    Returns the ACTIVE one if there is one, otherwise the most recent inactive
    row, so reactivating somebody reuses their history instead of stacking a
    second row beside it.
    """
    rows = (db.query(Membership)
              .filter(Membership.user_id == user_id,
                      Membership.scope_type == SCOPE_BRAND_SALES_ORG,
                      Membership.scope_id == bso_id,
                      Membership.role.in_(BRAND_SALES_ROLES))
              .order_by(Membership.created_at.desc()).all())
    for m in rows:
        if m.is_active:
            return m
    return rows[0] if rows else None


def assert_manager_ok(db: Session, bso_id: str,
                      manager_user_id: Optional[str]) -> Optional[str]:
    """A reporting manager must actually manage THIS brand.

    Naming somebody from another brand, or a rep, would put a line on the org
    chart that means nothing. Refused rather than stored.
    """
    if not manager_user_id:
        return None
    m = (db.query(Membership)
           .filter(Membership.user_id == manager_user_id,
                   Membership.scope_type == SCOPE_BRAND_SALES_ORG,
                   Membership.scope_id == bso_id,
                   Membership.role == ROLE_SALES_MANAGER,
                   Membership.is_active.is_(True))
           .first())
    if m is None:
        raise HTTPException(
            status_code=400,
            detail="That person is not an active sales manager in this brand, so "
                   "they cannot be named as the reporting manager.")
    return manager_user_id


def grant_membership(db: Session, user: User, bso: BrandSalesOrg, role: str,
                     actor: User,
                     reports_to_user_id: Optional[str] = None) -> Tuple[Membership, bool]:
    """Give this person a seat in this brand. Returns `(membership, created)`.

    IDEMPOTENT AND NON-DESTRUCTIVE. If they already hold a seat here it is
    reused - reactivated if it was switched off, and its role updated if asked -
    rather than a second row being written beside it. Memberships in OTHER
    brands and in customer organisations are never read or touched.
    """
    role = assert_role(role)
    reports_to_user_id = assert_manager_ok(db, bso.id, reports_to_user_id)
    if reports_to_user_id == user.id:
        raise HTTPException(status_code=400,
                            detail="Somebody cannot report to themselves.")

    existing = get_membership(db, user.id, bso.id)
    now = datetime.utcnow()

    if existing is not None:
        before = {"role": existing.role, "is_active": bool(existing.is_active),
                  "reports_to_user_id": existing.reports_to_user_id}
        existing.role = role
        existing.is_active = True
        existing.reports_to_user_id = reports_to_user_id
        log_action(
            db, None, actor.id,
            action="sales_membership_granted",
            target_type="user", target_id=user.id,
            brand_sales_org_id=bso.id,
            before=before,
            after={"membership_id": existing.id, "role": role, "is_active": True,
                   "reports_to_user_id": reports_to_user_id},
            details={"user_email": user.email, "brand": bso.name, "reused": True},
            note="Existing seat in this brand reused rather than duplicated.",
            commit=False,
        )
        return existing, False

    m = Membership(
        user_id=user.id,
        scope_type=SCOPE_BRAND_SALES_ORG,
        scope_id=bso.id,
        role=role,
        is_active=True,
        granted_by=actor.id,
        created_at=now,
        reports_to_user_id=reports_to_user_id,
    )
    db.add(m)
    db.flush()
    log_action(
        db, None, actor.id,
        action="sales_membership_granted",
        target_type="user", target_id=user.id,
        brand_sales_org_id=bso.id,
        after={"membership_id": m.id, "role": role, "is_active": True,
               "reports_to_user_id": reports_to_user_id},
        details={"user_email": user.email, "brand": bso.name, "reused": False},
        note="New brand-sales seat. organization_id was not read or written.",
        commit=False,
    )
    return m, True


def change_role(db: Session, m: Membership, new_role: str, actor: User) -> Membership:
    new_role = assert_role(new_role)
    if m.role == new_role:
        return m
    # The table is unique on (user, scope_type, scope_id, role). If a row for the
    # TARGET role already exists this update would violate it, so say what is
    # actually wrong instead of surfacing an integrity error.
    clash = (db.query(Membership)
               .filter(Membership.user_id == m.user_id,
                       Membership.scope_type == m.scope_type,
                       Membership.scope_id == m.scope_id,
                       Membership.role == new_role,
                       Membership.id != m.id)
               .first())
    if clash is not None:
        raise HTTPException(
            status_code=409,
            detail="This person already holds a %s seat in this brand." % new_role)

    before = {"role": m.role}
    m.role = new_role
    # A manager reports to nobody within their own brand. Leaving a stale line
    # pointing at their predecessor would be an org chart nobody edited.
    if new_role == ROLE_SALES_MANAGER:
        m.reports_to_user_id = None
    log_action(
        db, None, actor.id,
        action="sales_membership_role_changed",
        target_type="user", target_id=m.user_id,
        brand_sales_org_id=m.scope_id,
        before=before, after={"role": new_role},
        note="Role changed on the existing seat. No membership was created or "
             "destroyed, and the person's other memberships were not read.",
        commit=False,
    )
    return m


def set_active(db: Session, m: Membership, active: bool, actor: User) -> Membership:
    """Switch a seat on or off.

    DEACTIVATION IS NOT DELETION, on purpose. The row stays, so the opportunities
    they own, the meetings they are on and every audit entry naming them all
    remain exactly as they were. `sales_memberships()` filters on `is_active`, so
    the workspace closes the moment this flips - no session surgery required.
    Their memberships in other brands and in customer organisations are not
    touched, because this function only ever sees one row.
    """
    if bool(m.is_active) == bool(active):
        return m
    before = {"is_active": bool(m.is_active)}
    m.is_active = bool(active)
    log_action(
        db, None, actor.id,
        action=("sales_membership_reactivated" if active
                else "sales_membership_deactivated"),
        target_type="user", target_id=m.user_id,
        brand_sales_org_id=m.scope_id,
        before=before, after={"is_active": bool(active)},
        note=("Seat switched off. History, owned opportunities and audit trail are "
              "unchanged, and no other membership was touched."
              if not active else "Seat switched back on."),
        commit=False,
    )
    return m


def set_reporting_manager(db: Session, m: Membership,
                          manager_user_id: Optional[str], actor: User) -> Membership:
    manager_user_id = assert_manager_ok(db, m.scope_id, manager_user_id)
    if manager_user_id == m.user_id:
        raise HTTPException(status_code=400,
                            detail="Somebody cannot report to themselves.")
    before = {"reports_to_user_id": m.reports_to_user_id}
    m.reports_to_user_id = manager_user_id
    log_action(
        db, None, actor.id,
        action="sales_membership_manager_changed",
        target_type="user", target_id=m.user_id,
        brand_sales_org_id=m.scope_id,
        before=before, after={"reports_to_user_id": manager_user_id},
        note="Reporting line only. This grants and withholds nothing - no guard "
             "reads this column.",
        commit=False,
    )
    return m
