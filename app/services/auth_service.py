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


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain_password: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain_password.encode(), hashed.encode())


def create_access_token(user: User, db: Session) -> str:
    """
    Generate a new JWT for the user.  A fresh session_token UUID is written
    to the DB and embedded as the `jti` claim, which single-session
    enforcement in get_current_user uses to reject tokens from previous logins.
    """
    jti = str(uuid.uuid4())
    user.session_token = jti
    db.add(user)
    db.commit()
    payload = {
        "sub": user.id,
        "org_id": user.organization_id,
        "role": user.role,
        "jti": jti,
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRY_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise ValueError("Token has expired")
    except jwt.InvalidTokenError:
        raise ValueError("Invalid token")


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    user = db.query(User).filter(User.email == email, User.is_active == True).first()
    if not user or not verify_password(password, user.password_hash):
        return None
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    return user
