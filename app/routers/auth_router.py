<<<<<<< Updated upstream
import time
import threading
from collections import defaultdict
=======
import logging
from datetime import datetime, timedelta

>>>>>>> Stashed changes
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.deps import get_db, get_current_user, oauth2_scheme
from app.limiter import limiter
from app.services.auth_service import (
    authenticate_user, create_access_token, decode_access_token,
    hash_password, verify_password, revoke_token,
)
from app.models.models import User

logger = logging.getLogger("auth")

router = APIRouter(prefix="/auth", tags=["auth"])

<<<<<<< Updated upstream
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
=======
# After this many consecutive failures the account is locked for LOCKOUT_MINUTES.
LOGIN_LOCKOUT_THRESHOLD = 10
LOCKOUT_MINUTES = 15
>>>>>>> Stashed changes


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


@router.post("/login", response_model=TokenResponse)
<<<<<<< Updated upstream
def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    # Rate-limit check BEFORE hitting the DB so we don't waste queries on locked-out attackers
    _login_throttle_check(request, form_data.username)

    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        _login_record_failure(request, form_data.username)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")

    # Successful login — clear failure counter
    _login_clear_failures(request, form_data.username)

    token = create_access_token(user)
=======
@limiter.limit("10/minute")  # IP-level guard: max 10 attempts per IP per minute
def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    ip = request.client.host if request.client else "unknown"
    username = form_data.username.lower().strip()

    # Look up the user record so we can check/update the lockout state.
    # We always look up by email regardless of whether the password is
    # correct — lockout must be enforced even when the credential is wrong.
    candidate = db.query(User).filter(User.email == username).first()

    # ── Account lockout check ─────────────────────────────────────────────────
    # Use a time-based lockout (lockout_until timestamp) rather than flipping
    # is_active — setting is_active=False lets any attacker permanently
    # disable a known account with just N bad guesses (DoS vector).
    if candidate and candidate.lockout_until and candidate.lockout_until > datetime.utcnow():
        remaining = int((candidate.lockout_until - datetime.utcnow()).total_seconds() / 60) + 1
        logger.warning(
            "Login rejected (account locked): username=%s ip=%s lockout_until=%s",
            username, ip, candidate.lockout_until.isoformat(),
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Account temporarily locked due to too many failed attempts. "
                f"Try again in approximately {remaining} minute(s)."
            ),
        )

    # ── Authenticate ──────────────────────────────────────────────────────────
    authenticated_user = authenticate_user(db, username, form_data.password)

    if not authenticated_user:
        # Log the failure for audit purposes (username, IP, timestamp).
        logger.warning(
            "Failed login attempt: username=%s ip=%s timestamp=%s",
            username, ip, datetime.utcnow().isoformat(),
        )

        # Update failure counter and, if the threshold is reached, set the
        # lockout timestamp. Only meaningful when the account exists — unknown
        # usernames still return the same generic error (no user enumeration).
        if candidate:
            candidate.failed_login_attempts = (candidate.failed_login_attempts or 0) + 1
            if candidate.failed_login_attempts >= LOGIN_LOCKOUT_THRESHOLD:
                candidate.lockout_until = datetime.utcnow() + timedelta(minutes=LOCKOUT_MINUTES)
                logger.warning(
                    "Account locked: username=%s after %d consecutive failures ip=%s lockout_until=%s",
                    username,
                    candidate.failed_login_attempts,
                    ip,
                    candidate.lockout_until.isoformat(),
                )
            db.commit()

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    # ── Successful login — reset failure state ────────────────────────────────
    authenticated_user.failed_login_attempts = 0
    authenticated_user.lockout_until = None
    db.commit()

    token = create_access_token(authenticated_user)
>>>>>>> Stashed changes
    return TokenResponse(
        access_token=token,
        role=authenticated_user.role,
        full_name=authenticated_user.full_name,
        organization_id=authenticated_user.organization_id,
        must_change_password=authenticated_user.must_change_password,
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


@router.post("/logout")
def logout(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Invalidate the caller's current JWT immediately by adding its jti to the
    deny-list. After this call succeeds the token is rejected on every
    subsequent request, even though it has not yet naturally expired.
    """
    try:
        payload = decode_access_token(token)
    except ValueError:
        # Token is already invalid — treat as a successful logout.
        return {"success": True}

    jti = payload.get("jti")
    if jti:
        from datetime import datetime, timezone
        # exp is stored as a Unix timestamp by PyJWT
        expires_at = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
        revoke_token(db, jti, expires_at)

    return {"success": True}
