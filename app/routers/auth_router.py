import time
import threading
from collections import defaultdict
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.deps import get_db, get_current_user
from app.limiter import limiter
from app.services.auth_service import authenticate_user, create_access_token, hash_password, verify_password
from app.models.models import User, Organization, Platform
from app.models.sales_models import Membership, BrandSalesOrg, SCOPE_BRAND_SALES_ORG

import logging

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# ---------------------------------------------------------------------------
# Simple in-memory brute-force throttle for /auth/login
# Tracks failed attempts per (IP, email) key. After MAX_FAILURES failures
# within WINDOW_SECONDS the endpoint returns 429 until the window resets.
# This is per-process (fine for a single Render worker). For multi-process
# deployments swap _login_failures for a Redis-backed counter.
# ---------------------------------------------------------------------------
_MAX_FAILURES = 10        # attempts before lockout
_WINDOW_SECONDS = 900     # 15-minute sliding window
_LOCKOUT_SECONDS = 900    # 15-minute lockout once limit hit
_login_lock = threading.Lock()
_login_failures: dict[str, list[float]] = defaultdict(list)  # key -> [timestamp, ...]


def _login_throttle_check(request: Request, email: str) -> None:
    """Raise 429 if the (IP, email) pair has too many recent failures."""
    ip = request.client.host if request.client else "unknown"
    key = f"{ip}:{email.lower()}"
    now = time.monotonic()
    with _login_lock:
        # Prune timestamps outside the window
        _login_failures[key] = [t for t in _login_failures[key] if now - t < _WINDOW_SECONDS]
        if len(_login_failures[key]) >= _MAX_FAILURES:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many failed login attempts. Please wait 15 minutes before trying again.",
                headers={"Retry-After": str(_LOCKOUT_SECONDS)},
            )


def _login_record_failure(request: Request, email: str) -> None:
    ip = request.client.host if request.client else "unknown"
    key = f"{ip}:{email.lower()}"
    with _login_lock:
        _login_failures[key].append(time.monotonic())


def _login_clear_failures(request: Request, email: str) -> None:
    ip = request.client.host if request.client else "unknown"
    key = f"{ip}:{email.lower()}"
    with _login_lock:
        _login_failures.pop(key, None)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    full_name: str
    # OPTIONAL as of Aug 25 2026. A brand-sales user has no customer tenancy,
    # so this is legitimately null. It was a required `str`, which meant such a
    # user authenticated successfully and then the RESPONSE failed validation,
    # returning 500 on a correct password.
    organization_id: Optional[str] = None
    must_change_password: bool = False


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8)


def _detect_platform_slug(request: Request) -> str | None:
    """
    Detect which platform the login is coming from by reading the Origin
    or Referer header.  Returns a slug like 'bookaboost' or 'evosyspro',
    or None if the request comes from an unrecognised or local origin
    (localhost / 127.0.0.1) — localhost is always allowed through so
    development & testing aren't broken.
    """
    origin = request.headers.get("origin") or request.headers.get("referer") or ""
    origin = origin.lower()

    # Local dev — no platform restriction
    if not origin or "localhost" in origin or "127.0.0.1" in origin:
        return None

    # AdvisorFlow god domain — god_admin only, no platform restriction needed
    if "advisorflow" in origin:
        return "advisorflow"
    if "evosyspro" in origin:
        return "evosyspro"
    if "harmonyhustle" in origin:
        return "harmonyhustle"
    if "bookaboost" in origin:
        return "bookaboost"

    # Unknown origin — treat as BookaBoost (the default / legacy domain)
    return "bookaboost"


def _user_platform_slugs(db: Session, user: User) -> set:
    """Every platform this user is entitled to log in from.

    Two independent sources, because a person can belong to either domain — or,
    like Mike, to both:

      · CUSTOMER TENANCY — their organization's platform. Legacy orgs with no
        platform_id keep defaulting to 'bookaboost', unchanged behaviour.
      · BRAND SALES — the platform behind each active brand_sales_org
        membership. A salesperson HAS no organization, so this is the only
        source they have.

    Returns an empty set for a user with neither. The caller must treat that as
    a refusal: before this existed, a NULL-org user fell through to the
    'bookaboost' legacy default, which both locked EvoSys Pro salespeople out of
    their own domain AND let them in on someone else's.
    """
    slugs = set()

    if user.organization_id:
        org = db.query(Organization).filter(Organization.id == user.organization_id).first()
        if org:
            plat = (db.query(Platform).filter(Platform.id == org.platform_id).first()
                    if org.platform_id else None)
            # Legacy orgs predate platforms and have always been BookaBoost.
            slugs.add(plat.slug if plat else "bookaboost")

    sales_rows = (
        db.query(Platform.slug)
        .join(BrandSalesOrg, BrandSalesOrg.platform_id == Platform.id)
        .join(Membership, Membership.scope_id == BrandSalesOrg.id)
        .filter(Membership.user_id == user.id,
                Membership.scope_type == SCOPE_BRAND_SALES_ORG,
                Membership.is_active.is_(True),
                BrandSalesOrg.is_active.is_(True))
        .all()
    )
    slugs.update(r[0] for r in sales_rows)
    return slugs


@router.post("/login", response_model=TokenResponse)
def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    # Rate-limit check BEFORE hitting the DB so we don't waste queries on locked-out attackers
    _login_throttle_check(request, form_data.username)

    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        _login_record_failure(request, form_data.username)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")

    # Successful login — clear failure counter
    _login_clear_failures(request, form_data.username)

    # --------------------------------------------------------------------------
    # Platform isolation: god_admin can log in from anywhere.
    # For everyone else, verify the login domain matches the user's platform.
    # This stops a BookaBoost advisor from authenticating on app.evosyspro.live.
    # --------------------------------------------------------------------------
    if user.role != "god_admin":
        request_platform = _detect_platform_slug(request)
        # advisorflow domain is god-only — non-god users blocked
        if request_platform == "advisorflow":
            raise HTTPException(status_code=401, detail="Incorrect email or password")
        if request_platform is not None:
            # Which platforms is this person entitled to? Customer tenancy AND
            # brand-sales membership both count; a salesperson has only the
            # latter. An empty set means neither — refuse rather than fall back
            # to a default, which is how a NULL-org user used to land on the
            # wrong brand's domain.
            allowed_slugs = _user_platform_slugs(db, user)

            if request_platform not in allowed_slugs:
                # Return the same error as a bad password — don't leak that the
                # account exists on a different platform.
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Incorrect email or password",
                )

    # SELF-HEALING BACKFILL, for this one person, at the one moment it matters.
    #
    # Customer tenancy used to be the single column `users.organization_id`.
    # Workspace entry now reads customer_org Membership rows instead, and
    # startup materialises the column into those rows for everybody. A user
    # created BETWEEN restarts would otherwise sign in to an empty switcher and
    # no workspace - so the same idempotent migration runs for them here.
    # It writes only what their own column already says: nobody gains access to
    # anything they could not reach before it ran.
    try:
        from app.services import workspace_access
        workspace_access.backfill_from_legacy_column(db, user=user)
    except Exception:
        # A migration must never be the reason a valid password is refused.
        _log.warning("workspace backfill at login failed for user=%s", user.id,
                     exc_info=True)

    token = create_access_token(user, db)
    return TokenResponse(
        access_token=token,
        role=user.role,
        full_name=user.full_name,
        organization_id=user.organization_id,
        must_change_password=user.must_change_password,
    )


@router.get("/my-contexts")
def my_contexts(request: Request,
                db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    """EVERY CONTEXT THIS CALLER MAY ENTER — built by the server.

    The browser renders navigation from this and invents nothing. It does not
    read `users.organization_id`, it does not read localStorage, and it does not
    derive a workspace from a role label. A context absent from this response
    does not exist as far as the UI is concerned.

    And the response is not the control. Every route behind every entry here
    re-checks membership on its own, so hiding a button and refusing a request
    are two independent answers to the same question - which is the only
    arrangement where typing the URL fails too.
    """
    from app.services import workspace_access
    return workspace_access.authorized_contexts(db, current_user)


@router.get("/workspace/{organization_id}")
def enter_workspace(organization_id: str, request: Request,
                    db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)):
    """MAY I ENTER THIS WORKSPACE — the check the URL bar has to pass too.

    The switcher calls this before it navigates, but that is not why it exists.
    It exists so that typing /workspace/some-other-org gets the same answer as
    clicking a button that was never rendered: `assert_workspace_membership`
    raises 403 unless an ACTIVE customer_org membership backs the id.

    Returns the workspace's own identity plus the caller's role IN IT - which is
    not `users.role` and is never derived from it. D'Angelo is a sales_manager
    on the platform and whatever his membership says inside We Epic Game.
    """
    from app.services import workspace_access
    m = workspace_access.assert_workspace_membership(
        db, current_user, organization_id, request)
    org = db.query(Organization).filter(Organization.id == organization_id).first()
    if org is None:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    return {
        "organization_id": org.id,
        "organization_name": org.name,
        "organization_slug": org.slug,
        "workspace_role": m.role,
        "has_back_office": workspace_access.has_back_office(current_user, db),
    }


@router.post("/verify", response_model=TokenResponse)
def verify(request: Request, form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """Alias for /auth/login — keeps older frontend builds working."""
    return login(request, form_data, db)


@router.post("/refresh")
def refresh_token(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Issue a fresh 2-hour JWT for the currently authenticated user.
    Called silently by the frontend every 30 minutes while the app is open.
    Generates a new session_token UUID, invalidating any other active sessions.
    Returns 401 if the token has expired or the session was invalidated.
    """
    token = create_access_token(current_user, db)
    return {"access_token": token, "token_type": "bearer"}


@router.post("/logout")
def logout(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Invalidate the current session immediately by clearing session_token.
    Any outstanding JWT for this user becomes worthless.
    Re-fetches the real user row in case current_user was detached from the
    DB session by the god_admin X-Org-Override logic in get_current_user.
    """
    real_user = db.query(User).filter(User.id == current_user.id).first()
    if real_user:
        real_user.session_token = None
        db.commit()
    return {"success": True}


@router.post("/change-password")
@limiter.limit("10/hour")
def change_password(
    request: Request,
    req: ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Lets an advisor change their own password - covers the gap flagged
    in the frontend README: advisors were stuck with the temp password
    from app/seed.py with no way to change it themselves. Requires the
    current password to confirm identity, even though the JWT already
    authenticates them, since changing a password is a sensitive action
    worth a second check.
    """
    # Re-fetch the real user row from the DB. current_user may be detached from
    # the SQLAlchemy session when the caller is a god_admin or super_admin with an
    # active X-Org-Override header — get_current_user() calls db.expunge(user) in
    # that case, so any writes to current_user are silently dropped on commit.
    real_user = db.query(User).filter(User.id == current_user.id).first()
    if not real_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if not verify_password(req.current_password, real_user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")
    if len(req.new_password) < 8:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="New password must be at least 8 characters")

    real_user.password_hash = hash_password(req.new_password)
    real_user.must_change_password = False
    db.commit()
    return {"success": True}


# ══ customer activation (Checkpoint 6 §9 / §10) ═════════════════════════════
#
# The customer's first administrator sets their OWN password here, using a
# one-time link an operator sent them. No temporary password exists at any point
# - the account was created with a random secret that was hashed and discarded
# inside one function and is knowable to nobody, including this codebase.
#
# Both routes are public by necessity: the person using them has no account they
# can log into yet. They are therefore rate limited, and every rejection returns
# the SAME message, so a token cannot be probed for "expired" versus "never
# existed" and an email address cannot be confirmed by trying links.

class ActivationAcceptRequest(BaseModel):
    token: str
    new_password: str


# -- brand-sales / staff access activation -----------------------------------
#
# Separate from the customer activation routes because it reads a separate
# table. `customer_activations.organization_id` is NOT NULL, which is right for
# a tenant invitation and structurally wrong for a brand-sales user whose
# `organization_id` is NULL on purpose. See app/models/staff_models.py.
#
# The two token families are distinguishable by their leading characters -
# `act_` for a customer, `stf_` for staff - so the front end routes on the
# token itself rather than guessing or trying both.

class StaffActivationAcceptRequest(BaseModel):
    token: str
    new_password: str


@router.get("/staff-activation")
@limiter.limit("20/hour")
def staff_activation_preview(request: Request, token: str,
                             db: Session = Depends(get_db)):
    """Confirm a sales access link is live and say who it is for.

    Returns the person's own name, their own email and the brand - all of which
    they already know. No user id, no role, no membership internals.
    """
    from app.services import staff_activation as _staff
    return _staff.preview(db, token)


@router.post("/staff-activation/accept")
@limiter.limit("10/hour")
def staff_activation_accept(request: Request, req: StaffActivationAcceptRequest,
                            db: Session = Depends(get_db)):
    """Exchange the one-time token for a password the person chose.

    Deliberately returns no session token: they sign in through the normal front
    door afterwards, so every login keeps one code path with one set of lockout,
    single-session and audit behaviour.
    """
    from app.services import staff_activation as _staff
    user = _staff.accept(db, req.token, req.new_password)
    return {"ok": True, "email": user.email,
            "message": "Password set. You can now sign in."}


@router.get("/activation")
@limiter.limit("20/hour")
def activation_preview(request: Request, token: str, db: Session = Depends(get_db)):
    """Confirm a link is live and say who it is for, before they type a password.

    Returns the invited person's own name, their own email and their own
    organisation's name - all three of which they already know. It returns no
    user id, no organisation id, no role and nothing about the sale.
    """
    from app.services import customer_activation as _act
    from app.models.models import Organization as _Org

    row = _act.resolve(db, token)
    user = db.query(User).filter(User.id == row.user_id).first()
    org = db.query(_Org).filter(_Org.id == row.organization_id).first()
    if user is None or org is None or not user.is_active:
        raise HTTPException(status_code=400,
                            detail="This activation link is invalid or has expired.")
    return {"full_name": user.full_name, "email": user.email,
            "organization_name": org.name, "expires_at": row.expires_at}


@router.post("/activation/accept")
@limiter.limit("10/hour")
def activation_accept(request: Request, req: ActivationAcceptRequest,
                      db: Session = Depends(get_db)):
    """Exchange the one-time token for a password the customer chose.

    Deliberately does NOT return a session token. The customer logs in through
    the normal front door afterwards, which keeps every login through one code
    path with one set of lockout, single-session and audit behaviour.
    """
    from app.services import customer_activation as _act
    user = _act.accept(db, req.token, req.new_password)
    return {"ok": True, "email": user.email,
            "message": "Password set. You can now sign in."}
