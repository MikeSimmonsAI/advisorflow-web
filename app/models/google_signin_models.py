"""GOOGLE SIGN-IN (issue #430) - three small tables, none of them a password.

GoogleSigninTransaction  one started "Continue with Google" round trip. Created
                         at /auth/google/start; spent exactly once at the
                         callback. Carries the OIDC nonce and PKCE verifier and
                         a digest of a cookie set on the starting browser, so a
                         callback that arrives in a different browser (login
                         CSRF) is refused.
UserGoogleIdentity       the explicit link between one EvoSys user and one
                         Google account (`sub`). Created ONLY by a signed-in
                         user from their own settings - never by matching an
                         email address. Sign-in with Google finds the user here
                         and nowhere else.
GoogleLoginTicket        a 2-minute, single-use handle the callback gives the
                         browser (in the URL fragment) so the app can collect
                         its session token with a POST. The session token never
                         travels in a URL.

Secrets are stored as SHA-256 digests; the browser holds the only copy.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, String

from app.models.models import Base


def gen_uuid():
    return str(uuid.uuid4())


def _utcnow():
    return datetime.now(timezone.utc)


PURPOSE_LOGIN = "login"
PURPOSE_LINK = "link"
PURPOSE_LINK_INTENT = "link_intent"   # a signed-in user asked to connect; not yet sent to Google


class GoogleSigninTransaction(Base):
    __tablename__ = "google_signin_transactions"

    id = Column(String, primary_key=True, default=gen_uuid)
    secret_hash = Column(String(64), nullable=False)        # state = "<id>.<secret>"
    browser_hash = Column(String(64), nullable=True)        # digest of the starting browser's cookie
    purpose = Column(String(16), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=True)   # link flows only
    nonce = Column(String(128), nullable=True)
    code_verifier = Column(String(128), nullable=True)
    return_origin = Column(String(255), nullable=False)
    next_path = Column(String(512), nullable=True)
    created_ip = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    expires_at = Column(DateTime, nullable=False)
    consumed_at = Column(DateTime, nullable=True)

    __table_args__ = (Index("ix_gsi_txn_expires", "expires_at"),)

    @staticmethod
    def default_expiry(minutes: int = 10):
        return _utcnow() + timedelta(minutes=minutes)


class UserGoogleIdentity(Base):
    __tablename__ = "user_google_identities"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, unique=True, index=True)
    google_sub = Column(String(255), nullable=False, unique=True, index=True)
    email_at_link = Column(String(320), nullable=True)      # what Google said when linked (display only)
    linked_at = Column(DateTime, default=_utcnow)
    last_login_at = Column(DateTime, nullable=True)


class GoogleLoginTicket(Base):
    __tablename__ = "google_login_tickets"

    id = Column(String, primary_key=True, default=gen_uuid)
    secret_hash = Column(String(64), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    return_origin = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=_utcnow)
    expires_at = Column(DateTime, nullable=False)
    consumed_at = Column(DateTime, nullable=True)
