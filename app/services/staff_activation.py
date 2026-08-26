"""Secure access activation for brand-sales and other control-plane identities.

THE PROBLEM THIS SOLVES.
------------------------
Two real people had correct production identities and correct, active EvoSys Pro
sales memberships, and still could not get in: their rows carried a password
hash from a one-off seed script whose plaintext was printed once to a terminal
months ago. There was no supported way to give them access. The only reset in
the codebase, `POST /admin/users/{id}/reset-password`, returns a plaintext
password in its response body - the exact mechanism that is forbidden.

So: a one-time link, a cryptographically random token, only a hash stored, an
expiry, single use, and the person chooses their own password.

WHAT ACTIVATION TOUCHES, EXHAUSTIVELY.
--------------------------------------
    users.password_hash
    users.must_change_password       -> False
    users.failed_login_attempts      -> 0
    users.lockout_until              -> None
    staff_activations.status         -> accepted

That is the complete list. It does not touch `users.id`, it does not touch
`users.organization_id`, it does not touch `users.role`, and it does not read or
write `memberships` at all. A person's sales role survives activation because
activation has no code path that could change it - which is a stronger statement
than "we were careful", and `smoke_staff_activation.py` asserts it row by row.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.models import User
from app.models.sales_models import (
    Membership, BrandSalesOrg, SCOPE_BRAND_SALES_ORG, BRAND_SALES_ROLES,
)
from app.models.staff_models import (
    StaffActivation, PURPOSES, PURPOSE_SETUP,
    STAFF_INVITE_PENDING, STAFF_INVITE_ACCEPTED, STAFF_INVITE_REVOKED,
    STAFF_INVITE_EXPIRED,
)
from app.services.auth_service import hash_password
from app.services.sales_access import is_god, is_sales_manager
from app.routers.audit_log_router import log_action

TOKEN_PREFIX_LEN = 12
DEFAULT_TTL_HOURS = 72
MIN_PASSWORD_LEN = 10

# Distinct from the customer `act_` prefix so a link is self-describing: the
# front end routes on it instead of guessing which table a token belongs to.
TOKEN_LEAD = "stf_"

GENERIC_REJECTION = "This setup link is invalid or has expired."


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _mint() -> Tuple[str, str, str]:
    """(full token, prefix, hash). The full token is returned to exactly one
    caller and is never stored, logged or audited."""
    raw = TOKEN_LEAD + secrets.token_urlsafe(32)
    return raw, raw[:TOKEN_PREFIX_LEN], _hash(raw)


# ── authority ───────────────────────────────────────────────────────────────

def can_manage_sales_access(actor: User, brand_sales_org_id: Optional[str],
                            db: Session) -> bool:
    """God, or a sales MANAGER of that same brand.

    A rep cannot issue access links, however senior - handing somebody a login
    is a control-plane act, not a selling act. A manager of another brand cannot
    either, which is what stops a BookaBoost manager minting an EvoSys Pro
    login.
    """
    if is_god(actor):
        return True
    if not brand_sales_org_id:
        return False
    return is_sales_manager(actor, db, brand_sales_org_id)


def assert_can_manage_sales_access(actor: User, brand_sales_org_id: Optional[str],
                                   db: Session) -> None:
    if not can_manage_sales_access(actor, brand_sales_org_id, db):
        raise HTTPException(
            status_code=403,
            detail="Issuing a sales access link requires god authority, or "
                   "sales-manager authority for that brand.")


def brand_ids_for(db: Session, user: User) -> list:
    """The brand sales orgs this user actually belongs to, from `memberships`."""
    rows = (db.query(Membership)
              .filter(Membership.user_id == user.id,
                      Membership.scope_type == SCOPE_BRAND_SALES_ORG,
                      Membership.role.in_(BRAND_SALES_ROLES))
              .all())
    return [m.scope_id for m in rows]


# ── issuing ─────────────────────────────────────────────────────────────────

def issue(db: Session, user: User, actor: User, *,
          brand_sales_org_id: Optional[str] = None,
          purpose: str = PURPOSE_SETUP,
          ttl_hours: int = DEFAULT_TTL_HOURS) -> Tuple[StaffActivation, str]:
    """Mint a one-time link. Returns `(row, raw_token)`.

    Any outstanding pending link for this person is REVOKED first. Issuing a
    second live link would mean an old one that leaked stays usable, which is
    the opposite of what somebody clicking "reset access" wants.
    """
    if purpose not in PURPOSES:
        raise HTTPException(status_code=400,
                            detail="purpose must be one of: %s" % ", ".join(PURPOSES))
    if not user.is_active:
        raise HTTPException(status_code=409,
                            detail="That user is deactivated. Reactivate them before "
                                   "issuing an access link.")

    now = datetime.utcnow()
    superseded = 0
    for old in (db.query(StaffActivation)
                  .filter(StaffActivation.user_id == user.id,
                          StaffActivation.status == STAFF_INVITE_PENDING).all()):
        old.status = STAFF_INVITE_REVOKED
        old.revoked_at = now
        superseded += 1

    prior = (db.query(StaffActivation)
               .filter(StaffActivation.user_id == user.id).count())

    raw, prefix, digest = _mint()
    row = StaffActivation(
        user_id=user.id,
        brand_sales_org_id=brand_sales_org_id,
        purpose=purpose,
        token_prefix=prefix,
        token_hash=digest,
        status=STAFF_INVITE_PENDING,
        expires_at=now + timedelta(hours=max(1, min(int(ttl_hours), 720))),
        send_count=prior + 1,
        last_sent_at=now,
        created_at=now,
        created_by=actor.id,
    )
    db.add(row)
    db.flush()

    # organization_id is NOT passed: a control-plane action belongs to no
    # tenant, which is exactly why AuditLogEntry.organization_id was relaxed to
    # nullable in Checkpoint 6.
    log_action(
        db, None, actor.id,
        action="sales_access_link_issued",
        target_type="user", target_id=user.id,
        brand_sales_org_id=brand_sales_org_id,
        after={"activation_id": row.id,
               "token_prefix": row.token_prefix,
               "purpose": purpose,
               "expires_at": row.expires_at.isoformat(),
               "send_count": row.send_count},
        details={"user_email": user.email,
                 "superseded_pending_links": superseded},
        note="One-time link generated. The token itself is not recorded.",
        commit=False,
    )
    db.commit()
    db.refresh(row)
    return row, raw


def revoke(db: Session, row: StaffActivation, actor: User) -> StaffActivation:
    if row.status == STAFF_INVITE_ACCEPTED:
        raise HTTPException(status_code=409,
                            detail="That link has already been used.")
    row.status = STAFF_INVITE_REVOKED
    row.revoked_at = datetime.utcnow()
    log_action(db, None, actor.id,
               action="sales_access_link_revoked",
               target_type="user", target_id=row.user_id,
               brand_sales_org_id=row.brand_sales_org_id,
               after={"activation_id": row.id, "status": STAFF_INVITE_REVOKED},
               commit=False)
    db.commit()
    db.refresh(row)
    return row


# ── redeeming ───────────────────────────────────────────────────────────────

def resolve(db: Session, raw_token: Optional[str]) -> StaffActivation:
    """Find a usable link. Fails closed, and fails IDENTICALLY.

    Every rejection returns the same message and the same status, so a token
    cannot be probed for "expired" versus "revoked" versus "never existed".
    """
    bad = HTTPException(status_code=400, detail=GENERIC_REJECTION)
    if not raw_token or len(raw_token) <= TOKEN_PREFIX_LEN:
        raise bad
    row = (db.query(StaffActivation)
             .filter(StaffActivation.token_prefix == raw_token[:TOKEN_PREFIX_LEN])
             .first())
    if row is None:
        raise bad
    # Constant-time: the prefix already narrowed it to one row, so this is about
    # not leaking the hash through timing rather than about lookup speed.
    if not secrets.compare_digest(row.token_hash, _hash(raw_token)):
        raise bad
    now = datetime.utcnow()
    if row.status == STAFF_INVITE_PENDING and row.expires_at and row.expires_at <= now:
        row.status = STAFF_INVITE_EXPIRED
        db.commit()
        raise bad
    if not row.is_usable(now):
        raise bad
    return row


def preview(db: Session, raw_token: str) -> dict:
    """Who the link is for, before they type a password.

    Returns the person's own name, their own email and the brand - all three of
    which they already know. No user id, no membership id, no role internals.
    """
    row = resolve(db, raw_token)
    user = db.query(User).filter(User.id == row.user_id).first()
    if user is None or not user.is_active:
        raise HTTPException(status_code=400, detail=GENERIC_REJECTION)
    brand = None
    if row.brand_sales_org_id:
        b = (db.query(BrandSalesOrg)
               .filter(BrandSalesOrg.id == row.brand_sales_org_id).first())
        brand = b.name if b else None
    return {"full_name": user.full_name, "email": user.email,
            "workspace": brand, "purpose": row.purpose,
            "expires_at": row.expires_at}


def accept(db: Session, raw_token: str, new_password: str) -> User:
    """Exchange the token for a password the person chose. One-shot.

    See the module docstring for the exhaustive list of what this writes. It
    does not touch organization_id, role, or memberships.
    """
    if not new_password or len(new_password) < MIN_PASSWORD_LEN:
        raise HTTPException(
            status_code=400,
            detail="Choose a password of at least %d characters." % MIN_PASSWORD_LEN)

    row = resolve(db, raw_token)
    user = db.query(User).filter(User.id == row.user_id).first()
    if user is None or not user.is_active:
        raise HTTPException(status_code=400, detail=GENERIC_REJECTION)

    now = datetime.utcnow()
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    user.failed_login_attempts = 0
    user.lockout_until = None

    row.status = STAFF_INVITE_ACCEPTED
    row.accepted_at = now

    log_action(
        db, None, user.id,
        action="sales_access_activated",
        target_type="user", target_id=user.id,
        brand_sales_org_id=row.brand_sales_org_id,
        after={"activation_id": row.id,
               "token_prefix": row.token_prefix,
               "purpose": row.purpose,
               "status": STAFF_INVITE_ACCEPTED},
        note="User set their own password via a one-time link. Membership, role "
             "and organization_id were not modified.",
        commit=False,
    )
    db.commit()
    db.refresh(user)
    return user


# ── queries ─────────────────────────────────────────────────────────────────

def latest_for_user(db: Session, user_id: str) -> Optional[StaffActivation]:
    return (db.query(StaffActivation)
              .filter(StaffActivation.user_id == user_id)
              .order_by(StaffActivation.created_at.desc())
              .first())


def activation_url(base_url: Optional[str], raw_token: str) -> str:
    """The link an operator sends. Built from a caller-supplied base so nothing
    here hardcodes a brand's domain."""
    base = (base_url or "").rstrip("/")
    return "%s/activate?token=%s" % (base, raw_token) if base \
        else "/activate?token=%s" % raw_token


def access_state(db: Session, user: User) -> dict:
    """How this person's login actually stands. Used by the Sales Team panel.

    `has_signed_in` is the honest signal. A password hash existing means
    nothing on its own - both people this was built for had one and still could
    not get in.
    """
    row = latest_for_user(db, user.id)
    return {
        "has_password": bool(user.password_hash),
        "must_change_password": bool(user.must_change_password),
        "has_signed_in": user.last_login_at is not None,
        "last_login_at": user.last_login_at,
        "locked_out": bool(user.lockout_until and user.lockout_until > datetime.utcnow()),
        "link": ({"id": row.id, "status": row.status, "purpose": row.purpose,
                  "expires_at": row.expires_at, "accepted_at": row.accepted_at,
                  "send_count": row.send_count, "is_usable": row.is_usable()}
                 if row else None),
    }
