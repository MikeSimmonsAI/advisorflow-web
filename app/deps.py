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
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {})
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

    # Super admin context override: allows the platform owner to "enter" any
    # org's data without logging in as that org's user.
    # expunge() detaches the user object from SQLAlchemy's session BEFORE we
    # mutate organization_id, so the change is never tracked as a pending DB
    # write - the real row in the users table stays untouched.
    if user.role == "super_admin":
        org_override = request.headers.get("X-Org-Override")
        if org_override:
            target_org = db.query(Organization).filter(Organization.id == org_override).first()
            if target_org:
                _log.info(
                    "AUDIT: super_admin %s (id=%s) activated X-Org-Override -> org=%s from IP=%s",
                    user.email, user.id, org_override,
                    request.client.host if request.client else "unknown",
                )
                db.expunge(user)
                user.organization_id = org_override

    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role not in ("org_admin", "super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user
