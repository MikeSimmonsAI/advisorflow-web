"""
Auth Service
Simple JWT-based authentication. No external dependency on BuddyBoss/
WordPress login - this is self-contained so the web app works
independently of Mike's GoDaddy/WordPress site.

Roles:
  - advisor: standard user, sees only their own leads + org-wide dedup checks
  - org_admin: sees all advisors within their organization (Mike's "master view")
  - super_admin: Mike's top-level account, sees across all organizations
    (Restland today, North Star Memorial Group + others later)
"""

import os
import uuid
import bcrypt
import jwt
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from app.models.models import User

_jwt_secret = os.environ.get("JWT_SECRET")
if not _jwt_secret:
    raise RuntimeError(
        "JWT_SECRET environment variable is not set. "
        "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
    )
if len(_jwt_secret) < 32:
    raise RuntimeError(
        "JWT_SECRET must be at least 32 characters long for security. "
        "Generate a strong secret with: python -c \"import secrets; print(secrets.token_hex(32))\""
    )
JWT_SECRET = _jwt_secret
JWT_ALGORITHM = "HS256"
TOKEN_EXPIRY_HOURS = 24  # 24-hour lifetime; frontend refreshes every 30 min while active

# ── A TOKEN MUST SAY WHAT IT IS FOR ─────────────────────────────────────────
#
# THE DEFECT THIS CLOSES. This service is not the only thing in the app that
# signs an HS256 JWT. `app/routers/setup_router.py` mints an integration setup
# token so an advisor can connect Google or Microsoft from an emailed link
# without logging in, and it used to fall back to JWT_SECRET when SECRET_KEY
# was unset — which is how production ran. Two different credentials, one
# signing key, and nothing on either side that said which was which.
#
# `decode_access_token` verified the signature and returned the claims. The
# setup token carries `sub`, so `deps.get_current_user` loaded that user and
# served the request. It carries no `jti`, so the session block was skipped
# entirely and the token kept working after every session had been revoked. A
# 48-hour link meant for "connect your calendar" was a 48-hour bearer
# credential for the whole API.
#
# TWO INDEPENDENT REFUSALS, because either alone is one mistake away from
# reopening it:
#
#   1. PURPOSE. Every access token now carries purpose="access", and decoding
#      refuses anything else — including a token with no purpose claim at all,
#      which is what every non-access token this codebase has ever minted looks
#      like. Fail closed, not fail open-on-absence.
#   2. KEY SEPARATION. setup_router now derives its own signing key (see
#      `setup_router.SETUP_SIGNING_KEY`), so a setup token no longer verifies
#      here at all.
#
# WHAT THIS COSTS. Access tokens minted before this deploy have no `purpose`
# and are refused, so everyone signs in again once. That is the correct price:
# the alternative is accepting purpose-less tokens for a grace period, which is
# precisely the hole, kept open on a timer.
ACCESS_TOKEN_PURPOSE = "access"


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain_password: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain_password.encode(), hashed.encode())


def create_access_token(user: User, db: Session, *, request=None,
                        session=None) -> str:
    """
    Generate a new JWT for the user.

    WHAT CHANGED WHEN MOBILE ARRIVED, AND WHY THE SIGNATURE DID NOT.
    ----------------------------------------------------------------
    This used to write one UUID into `users.session_token` and embed it as the
    JWT's `jti`. One column, one live credential: signing in on a phone signed
    the desktop out, and the web client's 30-minute refresh signed the phone out
    twice an hour. Two devices could not both be true because there was only one
    place for the truth to live.

    A session is now a ROW (`app/models/session_models.py`), so a person can
    hold several at once and each can be ended on its own. `session_service`
    owns that table; this function just asks it for one and signs the jti it
    gets back.

    `session_token` IS STILL WRITTEN, and deliberately. It is no longer the
    control — `deps.get_current_user` trusts the row and only falls back to the
    column when no row exists for the presented jti, which is exactly the case
    for a token minted before this table shipped. Keeping it in step means those
    tokens keep working through the deploy and still die the moment anything
    clears the column.

    The positional signature is unchanged because ~40 call sites, most of them
    tests, pass `(user, db)` and none of them should have to care. `request`
    labels the row with the device; `session` reuses one the caller already has
    (refresh does, having rotated it in place).
    """
    from app.services import session_service

    if session is None:
        session = session_service.start_session(
            db, user, **session_service.describe_client(request))
    jti = session.jti

    # A TARGETED UPDATE, NOT db.add(user).
    #
    # `get_current_user` calls db.expunge(user) for a god_admin and then MUTATES
    # the detached copy — nulling organization_id for neutral God Mode, or
    # setting an X-Org-Override. Re-attaching that instance with db.add() and
    # committing would flush those request-scoped mutations into the users
    # table, which is the one thing the expunge exists to prevent. Updating the
    # row by primary key writes the one column that is actually meant to change
    # and leaves the detached instance detached.
    db.query(User).filter(User.id == user.id).update(
        {"session_token": jti}, synchronize_session=False)
    db.commit()
    try:
        user.session_token = jti      # keep the in-memory copy honest
    except Exception:                 # pragma: no cover - defensive
        pass

    payload = {
        "sub": user.id,
        "org_id": user.organization_id,
        "role": user.role,
        "jti": jti,
        # See ACCESS_TOKEN_PURPOSE above. This claim is what makes this token
        # distinguishable from every other HS256 JWT the platform mints.
        "purpose": ACCESS_TOKEN_PURPOSE,
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRY_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Decode a token that is being presented AS AN ACCESS TOKEN.

    A valid signature is not an answer to "may this authenticate a user". The
    purpose check below is, and it is deliberately positive: the claim must be
    present and must equal ACCESS_TOKEN_PURPOSE. A missing claim is refused
    rather than waved through, because every token this codebase signs for some
    OTHER job has no purpose claim of its own to fail on.

    Every refusal says "Invalid token" and nothing more. Which claim was wrong
    is not information a caller presenting the wrong credential has earned.
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise ValueError("Token has expired")
    except jwt.InvalidTokenError:
        raise ValueError("Invalid token")

    if payload.get("purpose") != ACCESS_TOKEN_PURPOSE:
        raise ValueError("Invalid token")
    return payload


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    user = db.query(User).filter(User.email == email, User.is_active == True).first()
    if not user or not verify_password(password, user.password_hash):
        return None
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    return user
