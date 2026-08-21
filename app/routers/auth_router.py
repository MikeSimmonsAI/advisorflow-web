import time
import threading
from collections import defaultdict
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.deps import get_db, get_current_user
from app.limiter import limiter
from app.services.auth_service import authenticate_user, create_access_token, hash_password, verify_password
from app.models.models import User, Organization, Platform

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
    organization_id: str
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
            # Look up the org's platform
            org = db.query(Organization).filter(Organization.id == user.organization_id).first()
            org_platform = db.query(Platform).filter(Platform.id == org.platform_id).first() if (org and org.platform_id) else None
            org_slug = org_platform.slug if org_platform else "bookaboost"  # legacy orgs default to bookaboost

            if org_slug != request_platform:
                # Return the same error as a bad password — don't leak that the
                # account exists on a different platform.
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Incorrect email or password",
                )

    token = create_access_token(user, db)
    return TokenResponse(
        access_token=token,
        role=user.role,
        full_name=user.full_name,
        organization_id=user.organization_id,
        must_change_password=user.must_change_password,
    )


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
