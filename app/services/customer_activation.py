"""The customer's first administrator, without a password ever existing in clear.

THE PROBLEM THIS REPLACES
-------------------------
Four endpoints in this codebase create a user by generating a plaintext password
and returning it in the HTTP response body. Nothing emails it. It is handed to
whoever called the API, to relay by whatever means they have - which in practice
means a chat message, a ticket, or a note. A password that travels that way
outlives its purpose, and Checkpoint 6 forbids it outright.

WHAT HAPPENS INSTEAD
--------------------
The customer admin is created with a random password nobody ever sees: it is
generated, hashed, and discarded inside one function. The account cannot be
logged into with it, because it is not knowable. What the operator gets is an
activation token, shown exactly once, which the customer exchanges for a
password they choose themselves.

Only a SHA-256 hash and a short non-secret prefix are stored, the same
discipline as the Retell integration keys. If the operator loses the link, the
answer is to issue a new one - which revokes the old - not to recover it.

NOTHING HERE SENDS EMAIL. Checkpoint 6 §9 forbids automatically emailing
credentials without operator awareness, and Mike's standing rule forbids
automatic delivery of temporary credentials at this stage. The operator gets the
link and decides how it travels.

TENANCY IS EXPLICIT, ALWAYS
---------------------------
An existing user is added to a customer organisation only when an operator names
that user and that organisation. Nothing here matches on an email address, and
nothing here infers membership from a lead, an opportunity, or a sales contact
who happens to share a domain with the customer.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.models import Organization, User
from app.models.implementation_models import (
    Implementation, CustomerActivation,
    INVITE_PENDING, INVITE_ACCEPTED, INVITE_REVOKED, INVITE_EXPIRED,
)
from app.services.auth_service import hash_password
from app.routers.audit_log_router import log_action

TOKEN_PREFIX_LEN = 12
DEFAULT_TTL_HOURS = 72

# Roles a customer's own administrator may hold. Deliberately excludes every
# control-plane role: provisioning must not be able to mint a god.
CUSTOMER_ADMIN_ROLES = ("org_admin", "advisor", "viewer")


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _mint() -> Tuple[str, str, str]:
    """(full token, prefix, hash). The full token is returned to exactly one
    caller and is never stored, logged or audited."""
    raw = "act_" + secrets.token_urlsafe(32)
    return raw, raw[:TOKEN_PREFIX_LEN], _hash(raw)


def _unknowable_password() -> str:
    """A password generated and thrown away inside one expression.

    The account must have a password hash because the column is NOT NULL and
    every login path expects one. It must not be a password anybody can use, so
    it is never returned by this module and never leaves this function.
    """
    return secrets.token_urlsafe(48)


# ── creating / inviting the customer admin ──────────────────────────────────

def create_customer_admin(
    db: Session,
    org: Organization,
    actor: User,
    *,
    full_name: str,
    email: str,
    role: str = "org_admin",
    implementation: Optional[Implementation] = None,
    ttl_hours: int = DEFAULT_TTL_HOURS,
) -> Tuple[User, CustomerActivation, str]:
    """Create a brand-new identity inside this customer organisation.

    Returns `(user, activation, raw_token)`. The raw token is the only copy that
    will ever exist; the caller shows it once and does not persist it.
    """
    email = (email or "").strip().lower()
    full_name = (full_name or "").strip()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email address is required.")
    if not full_name:
        raise HTTPException(status_code=400, detail="A name is required.")
    if role not in CUSTOMER_ADMIN_ROLES:
        raise HTTPException(status_code=400,
                            detail="Role must be one of: %s." % ", ".join(CUSTOMER_ADMIN_ROLES))

    if db.query(User).filter(User.email == email).first() is not None:
        # Not "we'll add them instead". Adding an existing identity to a tenant
        # is a different, deliberate act with its own function, because doing it
        # by accident is how one person's account ends up inside a customer they
        # do not work for.
        raise HTTPException(status_code=409,
                            detail="A user with that email already exists. "
                                   "Use 'add existing user' if this is genuinely the same person.")

    user = User(
        organization_id=org.id,
        email=email,
        full_name=full_name,
        role=role,
        platform_id=org.platform_id,
        password_hash=hash_password(_unknowable_password()),
        must_change_password=False,   # they will SET one, not change a known one
        is_active=True,
    )
    db.add(user)
    db.flush()

    activation, raw = _issue(db, user, org, actor, implementation, ttl_hours)

    log_action(
        db, org.id, actor.id,
        action="customer_admin_created",
        target_type="user", target_id=user.id,
        platform_id=org.platform_id,
        brand_sales_org_id=(implementation.brand_sales_org_id if implementation else None),
        after={"email": user.email, "full_name": user.full_name, "role": user.role,
               "organization_id": org.id},
        details={"activation_prefix": activation.token_prefix,
                 "expires_at": activation.expires_at.isoformat()},
        note="Created with an unknowable password; activation link issued.",
        commit=False,
    )
    db.commit()
    db.refresh(user)
    db.refresh(activation)
    return user, activation, raw


def add_existing_user(
    db: Session,
    org: Organization,
    actor: User,
    *,
    user_id: str,
    role: Optional[str] = None,
) -> User:
    """Give an existing identity access to this customer organisation.

    Requires the operator to name the user by id. Matching on email is exactly
    the inference Checkpoint 6 §9 forbids, and it is the mechanism by which a
    salesperson with a customer's domain in their address ends up inside the
    tenant.

    NOTE ON THE ARCHITECTURE'S LIMIT, STATED PLAINLY: customer tenancy in this
    codebase is a single column, `users.organization_id`. There is no customer
    membership table - `Membership` with `SCOPE_CUSTOMER_ORG` exists but grants
    nothing, as the Checkpoint 6 survey recorded. So a user belongs to exactly
    ONE customer organisation at a time, and moving them means moving them.
    This function therefore refuses to move a user who is already inside a
    different tenant, rather than silently transferring them out of it. Making
    customer membership genuinely additive is a schema change, not something to
    fake here.
    """
    u = db.query(User).filter(User.id == user_id).first()
    if u is None:
        raise HTTPException(status_code=404, detail="User not found.")
    if u.organization_id and u.organization_id != org.id:
        raise HTTPException(status_code=409,
                            detail="That user already belongs to another customer organisation. "
                                   "Customer tenancy is a single organisation per identity in this "
                                   "architecture; move them deliberately or create a separate identity.")
    if role is not None and role not in CUSTOMER_ADMIN_ROLES:
        raise HTTPException(status_code=400,
                            detail="Role must be one of: %s." % ", ".join(CUSTOMER_ADMIN_ROLES))

    before = {"organization_id": u.organization_id, "role": u.role, "platform_id": u.platform_id}
    u.organization_id = org.id
    if role:
        u.role = role
    if not u.platform_id:
        u.platform_id = org.platform_id

    log_action(
        db, org.id, actor.id,
        action="customer_user_added",
        target_type="user", target_id=u.id,
        platform_id=org.platform_id,
        before=before,
        after={"organization_id": u.organization_id, "role": u.role},
        note="Existing identity added to customer organisation by explicit id.",
        commit=False,
    )
    db.commit()
    db.refresh(u)
    return u


def resend(db: Session, activation: CustomerActivation, actor: User,
           ttl_hours: int = DEFAULT_TTL_HOURS) -> Tuple[CustomerActivation, str]:
    """Revoke the outstanding link and issue a fresh one.

    Extending the existing token would mean a link that leaked stays valid for
    longer, which is the opposite of what somebody clicking 'resend' after a
    mistake wants.
    """
    if activation.status == INVITE_ACCEPTED:
        raise HTTPException(status_code=409, detail="That invitation has already been accepted.")
    org = db.query(Organization).filter(Organization.id == activation.organization_id).first()
    user = db.query(User).filter(User.id == activation.user_id).first()
    if org is None or user is None:
        raise HTTPException(status_code=404, detail="Invitation target no longer exists.")

    now = datetime.utcnow()
    activation.status = INVITE_REVOKED
    activation.revoked_at = now

    impl = (db.query(Implementation)
              .filter(Implementation.id == activation.implementation_id).first()
            if activation.implementation_id else None)
    fresh, raw = _issue(db, user, org, actor, impl, ttl_hours,
                        send_count=(activation.send_count or 1) + 1)

    log_action(
        db, org.id, actor.id,
        action="customer_admin_invited",
        target_type="customer_activation", target_id=fresh.id,
        platform_id=org.platform_id,
        before={"activation_prefix": activation.token_prefix, "status": INVITE_REVOKED},
        after={"activation_prefix": fresh.token_prefix,
               "expires_at": fresh.expires_at.isoformat(),
               "send_count": fresh.send_count},
        details={"user_email": user.email},
        note="Resend: previous link revoked, new link issued.",
        commit=False,
    )
    db.commit()
    db.refresh(fresh)
    return fresh, raw


def revoke(db: Session, activation: CustomerActivation, actor: User) -> CustomerActivation:
    if activation.status == INVITE_ACCEPTED:
        raise HTTPException(status_code=409, detail="That invitation has already been accepted.")
    activation.status = INVITE_REVOKED
    activation.revoked_at = datetime.utcnow()
    log_action(
        db, activation.organization_id, actor.id,
        action="customer_admin_invite_revoked",
        target_type="customer_activation", target_id=activation.id,
        after={"status": INVITE_REVOKED},
        commit=False,
    )
    db.commit()
    db.refresh(activation)
    return activation


# ── redeeming ───────────────────────────────────────────────────────────────

def resolve(db: Session, raw_token: Optional[str]) -> CustomerActivation:
    """Find a usable activation by its token. Fails closed and fails identically.

    Every rejection returns the same 400, so a token cannot be probed for
    'exists but expired' versus 'never existed'.
    """
    bad = HTTPException(status_code=400, detail="This activation link is invalid or has expired.")
    if not raw_token or len(raw_token) <= TOKEN_PREFIX_LEN:
        raise bad
    row = (db.query(CustomerActivation)
             .filter(CustomerActivation.token_prefix == raw_token[:TOKEN_PREFIX_LEN])
             .first())
    if row is None:
        raise bad
    if not secrets.compare_digest(row.token_hash, _hash(raw_token)):
        raise bad
    now = datetime.utcnow()
    if row.status == INVITE_PENDING and row.expires_at and row.expires_at <= now:
        row.status = INVITE_EXPIRED
        db.commit()
        raise bad
    if not row.is_usable(now):
        raise bad
    return row


def accept(db: Session, raw_token: str, new_password: str) -> User:
    """Exchange the token for a password the customer chose. One-shot."""
    if not new_password or len(new_password) < 10:
        raise HTTPException(status_code=400,
                            detail="Choose a password of at least 10 characters.")
    row = resolve(db, raw_token)
    user = db.query(User).filter(User.id == row.user_id).first()
    if user is None or not user.is_active:
        raise HTTPException(status_code=400, detail="This activation link is invalid or has expired.")

    now = datetime.utcnow()
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    user.failed_login_attempts = 0
    user.lockout_until = None
    row.status = INVITE_ACCEPTED
    row.accepted_at = now

    log_action(
        db, row.organization_id, user.id,
        action="customer_admin_activated",
        target_type="user", target_id=user.id,
        after={"activation_prefix": row.token_prefix, "status": INVITE_ACCEPTED},
        note="Customer set their own password via activation link.",
        commit=False,
    )
    db.commit()
    db.refresh(user)
    return user


# ── queries ─────────────────────────────────────────────────────────────────

def latest_for_user(db: Session, user_id: str) -> Optional[CustomerActivation]:
    return (db.query(CustomerActivation)
              .filter(CustomerActivation.user_id == user_id)
              .order_by(CustomerActivation.created_at.desc())
              .first())


def invite_state(db: Session, org_id: str) -> Dict[str, Any]:
    """Whether this customer has anybody who can log in yet.

    Drives the 'Customer Admin Not Invited' queue in §37, and it is a real
    query rather than a flag somebody has to remember to set.
    """
    users = (db.query(User)
               .filter(User.organization_id == org_id, User.is_active.is_(True))
               .all())
    admins = [u for u in users if u.role in CUSTOMER_ADMIN_ROLES]
    pending, accepted = 0, 0
    for u in admins:
        a = latest_for_user(db, u.id)
        if a is None:
            continue
        if a.status == INVITE_ACCEPTED:
            accepted += 1
        elif a.is_usable():
            pending += 1
    return {
        "user_count": len(users),
        "admin_count": len(admins),
        "invites_pending": pending,
        "invites_accepted": accepted,
        "has_admin": bool(admins),
        "needs_invite": not admins,
    }


def invite_state_bulk(db: Session, org_ids) -> Dict[str, Dict[str, Any]]:
    """`invite_state` for many organisations at once, in two queries.

    The single-org version costs one users query plus one activation lookup per
    admin. The won-queue called it once per implementation, which is where the
    ~100 repeated `customer_activations` selects on God Sales Operations came
    from. Same definitions, same numbers - counted for the whole set at once.

    An org with no users is absent from both queries and comes back as the same
    all-zero / needs_invite shape the single-org version returns.
    """
    ids = sorted({str(o) for o in org_ids if o})
    out = {i: {"user_count": 0, "admin_count": 0, "invites_pending": 0,
               "invites_accepted": 0, "has_admin": False, "needs_invite": True}
           for i in ids}
    if not ids:
        return out

    users = (db.query(User)
               .filter(User.organization_id.in_(ids), User.is_active.is_(True))
               .all())
    admins = [u for u in users if u.role in CUSTOMER_ADMIN_ROLES]
    for u in users:
        out[str(u.organization_id)]["user_count"] += 1
    for u in admins:
        out[str(u.organization_id)]["admin_count"] += 1

    admin_ids = sorted({u.id for u in admins})
    latest = {}
    if admin_ids:
        # Ordered oldest-first so the last write per user wins, which is the row
        # `latest_for_user` would have returned with its DESC + first().
        for a in (db.query(CustomerActivation)
                    .filter(CustomerActivation.user_id.in_(admin_ids))
                    .order_by(CustomerActivation.created_at.asc()).all()):
            latest[a.user_id] = a

    for u in admins:
        a = latest.get(u.id)
        if a is None:
            continue
        bucket = out[str(u.organization_id)]
        if a.status == INVITE_ACCEPTED:
            bucket["invites_accepted"] += 1
        elif a.is_usable():
            bucket["invites_pending"] += 1

    for i in ids:
        out[i]["has_admin"] = bool(out[i]["admin_count"])
        out[i]["needs_invite"] = not out[i]["admin_count"]
    return out


def activation_url(base_url: Optional[str], raw_token: str) -> str:
    """The link the operator sends. Built from a caller-supplied base so nothing
    here hardcodes a brand's domain - Checkpoint 6 §42."""
    base = (base_url or "").rstrip("/")
    return "%s/activate?token=%s" % (base, raw_token) if base else "/activate?token=%s" % raw_token


# ── internal ────────────────────────────────────────────────────────────────

def _issue(db: Session, user: User, org: Organization, actor: User,
           implementation: Optional[Implementation], ttl_hours: int,
           send_count: int = 1) -> Tuple[CustomerActivation, str]:
    raw, prefix, digest = _mint()
    now = datetime.utcnow()
    row = CustomerActivation(
        user_id=user.id,
        organization_id=org.id,
        implementation_id=implementation.id if implementation is not None else None,
        token_prefix=prefix,
        token_hash=digest,
        status=INVITE_PENDING,
        expires_at=now + timedelta(hours=max(1, int(ttl_hours))),
        send_count=send_count,
        last_sent_at=now,
        created_at=now,
        created_by=actor.id,
    )
    db.add(row)
    db.flush()
    return row, raw
