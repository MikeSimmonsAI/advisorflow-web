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
from app.models.models import User, RevokedToken

<<<<<<< Updated upstream
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
=======
JWT_SECRET = os.environ.get("JWT_SECRET")
if not JWT_SECRET:
    raise RuntimeError("JWT_SECRET environment variable is not set. Set it to a random string of at least 32 characters.")
if len(JWT_SECRET) < 32:
    raise RuntimeError(f"JWT_SECRET is too short ({len(JWT_SECRET)} chars). It must be at least 32 characters.")
>>>>>>> Stashed changes
JWT_ALGORITHM = "HS256"
TOKEN_EXPIRY_HOURS = 4  # Short-lived; was 168 h (7 days) — reduced to limit compromise window


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain_password: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain_password.encode(), hashed.encode())


def create_access_token(user: User) -> str:
    expires_at = datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRY_HOURS)
    payload = {
        "sub": user.id,
        "org_id": user.organization_id,
        "role": user.role,
        "exp": expires_at,
        "jti": str(uuid.uuid4()),  # unique token ID — used for revocation
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Decode and validate the JWT. Does NOT check the revocation list here;
    callers that have a DB session should call is_token_revoked() afterwards."""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise ValueError("Token has expired")
    except jwt.InvalidTokenError:
        raise ValueError("Invalid token")


def is_token_revoked(db: Session, jti: str) -> bool:
    """Return True if the given jti has been added to the deny-list."""
    return db.query(RevokedToken).filter(RevokedToken.jti == jti).first() is not None


def revoke_token(db: Session, jti: str, expires_at: datetime) -> None:
    """Add a token's jti to the deny-list so it is rejected on future requests."""
    if not db.query(RevokedToken).filter(RevokedToken.jti == jti).first():
        entry = RevokedToken(jti=jti, expires_at=expires_at)
        db.add(entry)
        db.commit()


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    user = db.query(User).filter(User.email == email, User.is_active == True).first()
    if not user or not verify_password(password, user.password_hash):
        return None
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    return user
