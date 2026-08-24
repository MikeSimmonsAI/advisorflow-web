"""
Shared FastAPI dependencies: DB session injection and auth guard.
"""

import logging
import os
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from app.services.auth_service import decode_access_token
from app.models.models import User, Organization

_log = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./advisorflow.db")

# ── Connection pool hardening ──────────────────────────────────────────────
_is_sqlite = "sqlite" in DATABASE_URL
_pool_kwargs = (
    {"connect_args": {"check_same_thread": False}}
    if _is_sqlite
    else {
        "pool_size": 5,
        "max_overflow": 10,
        "pool_timeout": 30,
        "pool_recycle": 1800,
        "pool_pre_ping": True,
    }
)
engine = create_engine(DATABASE_URL, **_pool_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(request: Request, token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    try:
        payload = decode_access_token(token)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))

    user = db.query(User).filter(User.id == payload["sub"]).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")

    # Single-session enforcement
    token_jti = payload.get("jti")
    if token_jti and user.session_token != token_jti:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired. Please log in again.",
        )

    # Enforce must_change_password server-side
    if user.must_change_password:
        path = request.url.path
        if not path.startswith("/auth/"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You must change your password before continuing. Use /auth/change-password."
            )

    # Cross-org context: god_admin can view any org's data or see ALL orgs combined.
    # expunge() detaches the user from the SQLAlchemy session BEFORE any mutation
    # so changes are never written back to the DB.
    if user.role == "god_admin":
        org_override = request.headers.get("X-Org-Override")
        db.expunge(user)  # always detach first
        if org_override:
            target_org = db.query(Organization).filter(Organization.id == org_override).first()
            if target_org:
                _log.info(
                    "AUDIT: god_admin %s (id=%s) activated X-Org-Override -> org=%s from IP=%s",
                    user.email, user.id, org_override,
                    request.client.host if request.client else "unknown",
                )
                user.organization_id = org_override
        else:
            # No org selected — "All Orgs" god mode.
            # Routers check this flag and skip the org filter, returning data
            # across all organizations. The flag is never persisted.
            user._god_all_orgs = True

    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role not in ("org_admin", "super_admin", "god_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


def require_super_admin(user: User = Depends(get_current_user)) -> User:
    """Platform-operator guard — super_admin and god_admin pass.
    god_admin sits above super_admin and is never blocked by this gate."""
    if user.role not in ("super_admin", "god_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin access required")
    return user


def require_god(user: User = Depends(get_current_user)) -> User:
    """OWNER_CONTROL_PLANE guard — only god_admin accounts pass this."""
    if user.role != "god_admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    return user


def get_platform_org_ids(user: User, db) -> list:
    """Return org IDs scoped to the user's access level:
    - god_admin  → all orgs across all platforms
    - super_admin → all orgs on their platform only (via platform_id)
    - org_admin  → their own org only
    """
    if user.role == "god_admin":
        return [str(row[0]) for row in db.query(Organization.id).all()]
    if user.role == "super_admin" and getattr(user, "platform_id", None):
        return [
            str(row[0])
            for row in db.query(Organization.id)
            .filter(Organization.platform_id == user.platform_id)
            .all()
        ]
    return [str(user.organization_id)]
