import time
import threading
from collections import defaultdict
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.deps import get_db, get_current_user
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

    token = create_access_token(user)
    return TokenResponse(
        access_token=token,
        role=user.role,
        full_name=user.full_name,
        organization_id=user.organization_id,
        must_change_password=user.must_change_password,
    )


@router.post("/change-password")
def change_password(
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
    if not verify_password(req.current_password, current_user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")
    if len(req.new_password) < 8:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="New password must be at least 8 characters")

    current_user.password_hash = hash_password(req.new_password)
    current_user.must_change_password = False
    db.commit()
    return {"success": True}
