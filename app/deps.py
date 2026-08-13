"""
Shared FastAPI dependencies: DB session injection and auth guard.
"""

import logging
import os
import uuid
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from app.services.auth_service import decode_access_token, is_token_revoked
from app.models.models import User, Organization

<<<<<<< Updated upstream
_log = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./advisorflow.db")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {})
=======
logger = logging.getLogger("advisorflow.audit")

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL environment variable is not set. "
        "Set it to a valid database connection string before starting the server."
    )

_env = os.environ.get("ENVIRONMENT", os.environ.get("ENV", "development")).lower()
if "sqlite" in DATABASE_URL and _env not in ("development", "dev", "local", "test"):
    raise RuntimeError(
        f"sqlite:// DATABASE_URL is not permitted in environment '{_env}'. "
        "Use a real database URL or set ENVIRONMENT=development for local use."
    )

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
)
>>>>>>> Stashed changes
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

    # Reject tokens that have been explicitly revoked (e.g. via /auth/logout)
    jti = payload.get("jti")
    if jti and is_token_revoked(db, jti):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has been revoked")

    user = db.query(User).filter(User.id == payload["sub"]).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")

<<<<<<< Updated upstream
    # Enforce must_change_password server-side: any endpoint other than
    # /auth/change-password and /auth/login is blocked until the user sets a
    # real password. The frontend shows a modal but we must also block at the
    # API level so a motivated user can't skip it with raw HTTP calls.
    if user.must_change_password:
        path = request.url.path
        if not path.startswith("/auth/"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You must change your password before continuing. Use /auth/change-password."
            )
=======
    if user.must_change_password and request.url.path != '/auth/change-password':
        raise HTTPException(status_code=403, detail='Password change required')
>>>>>>> Stashed changes

    # Super admin context override: allows the platform owner to "enter" any
    # org's data without logging in as that org's user.
    # expunge() detaches the user object from SQLAlchemy's session BEFORE we
    # mutate organization_id, so the change is never tracked as a pending DB
    # write - the real row in the users table stays untouched.
    if user.role == "super_admin":
        org_override = request.headers.get("X-Org-Override")
        if org_override:
            # Validate UUID format before hitting the DB.
            # Some DB engines raise on a malformed UUID type cast even inside a
            # parameterized query; catching it here gives a clean 400 instead.
            try:
                uuid.UUID(org_override)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="X-Org-Override must be a valid UUID"
                )

            target_org = db.query(Organization).filter(Organization.id == org_override).first()

            # Log every attempt — success AND failure — so probing is visible.
            logger.warning(
                "org_override_attempt user_id=%s target_org_id=%s found=%s path=%s",
                user.id,
                org_override,
                target_org is not None,
                request.url.path,
            )

            if target_org:
                _log.info(
                    "AUDIT: super_admin %s (id=%s) activated X-Org-Override -> org=%s from IP=%s",
                    user.email, user.id, org_override,
                    request.client.host if request.client else "unknown",
                )
                db.expunge(user)
                user.organization_id = org_override
            else:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Override org not found"
                )

    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role not in ("org_admin", "super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user
