"""PER-DEVICE AUTHENTICATION SESSIONS.

WHY THIS TABLE EXISTS
---------------------
Until this module, a session WAS a column: `users.session_token` held one UUID,
`auth_service.create_access_token` overwrote it on every login and on every
refresh, and `deps.get_current_user` refused any JWT whose `jti` did not equal
it. One row, one column, one live credential per human being.

That is a perfectly good single-session control and a fatal one for a phone.
The web client refreshes every 30 minutes; each refresh minted a new UUID and
wrote it over the column, so a rep with a browser tab open anywhere was signing
their phone out twice an hour without touching either device. Logging in on the
phone did the same thing to the desktop in the other direction. Two clients
could not both be true at once because there was only one place for the truth
to live.

So the truth moves out of the column and into rows: ONE ROW PER LIVE SESSION,
which in practice means one per device. Everything the column bought is kept:

  * REVOCATION IS STILL REAL. A row carries `revoked_at`; `deps` refuses a token
    whose row is revoked or expired, and it does that BEFORE any legacy
    fallback, so revoking a session cannot be undone by a stale column value.
  * FORCE-LOGOUT STILL WORKS. `revoke_all_for_user` ends every row at once —
    that is what an administrator's force-logout, a password change and an
    account deactivation each call now. Multi-device support was NOT bought by
    weakening the kill switch; it was bought by giving the kill switch more
    than one thing to kill.
  * PER-DEVICE LOGOUT IS NEW AND IS THE POINT. Signing out on the phone ends
    the phone's row and leaves the desktop's alone.

WHAT A ROW IS NOT
-----------------
A row is not authority. It says *this credential is live*; it says nothing
about what the holder may do. Roles, memberships, capabilities and the
executive portfolio are all unchanged and are still consulted on every request.
A session row cannot widen anyone's access, and `device_id` is a convenience
for replacing a device's own previous row — it is caller-supplied, never
trusted, and never consulted in an authorisation decision.

REFRESH ROTATES A ROW, IT DOES NOT CREATE ONE
---------------------------------------------
`/auth/refresh` used to mint a whole new session. Here it rotates the `jti` on
the CALLER'S OWN ROW: the old token dies (its jti no longer exists), the other
devices' rows are untouched, and the table does not grow by one row every 30
minutes forever. An expired or revoked row cannot be rotated — a dead
credential must not be able to breathe new life into itself, which is the one
property that makes revocation worth having.
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Index, String,
                        Text)

from app.models.models import Base


def gen_uuid():
    return str(uuid.uuid4())


# Client families. Recorded for display ("iPhone · signed in 2 days ago") and
# for nothing else — no route branches on this value, and a caller who lies
# about it gains nothing.
CLIENT_WEB = "web"
CLIENT_IOS = "ios"
CLIENT_ANDROID = "android"
CLIENT_UNKNOWN = "unknown"
CLIENT_TYPES = (CLIENT_WEB, CLIENT_IOS, CLIENT_ANDROID, CLIENT_UNKNOWN)

# Why a session ended. Kept because "my phone signed itself out" is a support
# question, and the answer is usually one of these five.
REVOKE_LOGOUT = "logout"                  # the holder signed out on that device
REVOKE_LOGOUT_ALL = "logout_all"          # the holder signed out everywhere
REVOKE_REPLACED = "replaced"              # same device signed in again
REVOKE_PASSWORD_CHANGE = "password_change"
REVOKE_ADMIN = "admin"                    # force-logout / reset / deactivate
REVOKE_REASONS = (REVOKE_LOGOUT, REVOKE_LOGOUT_ALL, REVOKE_REPLACED,
                  REVOKE_PASSWORD_CHANGE, REVOKE_ADMIN)


class UserSession(Base):
    """One live authenticated credential. Usually one per device."""

    __tablename__ = "user_sessions"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)

    # The JWT `jti` this row backs. Unique because two live credentials sharing
    # an identifier would make revocation ambiguous, and an ambiguous
    # revocation is not one.
    jti = Column(String, nullable=False, unique=True, index=True)

    client = Column(String, nullable=False, default=CLIENT_UNKNOWN)

    # A stable per-INSTALL identifier the client generates once and keeps in
    # secure storage. Used only to replace that install's own previous row so a
    # phone that signs in twice leaves one session behind instead of a pile.
    # Never an authorisation input.
    device_id = Column(String, nullable=True, index=True)
    device_name = Column(String, nullable=True)      # "Mike's iPhone"
    app_version = Column(String, nullable=True)

    ip_address = Column(String, nullable=True)
    user_agent = Column(Text, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_seen_at = Column(DateTime, nullable=True)

    # Belt and braces with the JWT's own `exp`. A token whose signature is still
    # valid but whose row has passed this instant is refused, which means an
    # expiry can be shortened server-side without waiting for tokens to age out.
    expires_at = Column(DateTime, nullable=False)

    revoked_at = Column(DateTime, nullable=True)
    revoked_reason = Column(String, nullable=True)

    __table_args__ = (
        Index("ix_user_sessions_user_live", "user_id", "revoked_at"),
    )

    # ── read-only helpers ────────────────────────────────────────────────────
    #
    # `is_live` is deliberately a property on the model rather than a query
    # filter alone: `deps` loads a row by jti and then asks it this question, so
    # the definition of "live" exists once and both the loader and the lister
    # use it.

    @staticmethod
    def _aware(value):
        """SQLite hands back naive datetimes; Postgres may hand back aware ones.

        Comparing the two raises TypeError, and an authorisation check that
        raises is an outage. Everything written here is UTC, so a naive value is
        read as UTC rather than as local time.
        """
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    @property
    def is_expired(self) -> bool:
        exp = self._aware(self.expires_at)
        return exp is not None and exp <= datetime.now(timezone.utc)

    @property
    def is_live(self) -> bool:
        return not self.is_revoked and not self.is_expired

    def to_public_dict(self) -> dict:
        """What the holder is shown about their own sessions.

        No jti. A session list that hands back the credential identifier turns a
        read endpoint into a way to enumerate live tokens.
        """
        return {
            "id": self.id,
            "client": self.client,
            "device_name": self.device_name,
            "app_version": self.app_version,
            "created_at": self.created_at,
            "last_seen_at": self.last_seen_at,
            "expires_at": self.expires_at,
            "revoked_at": self.revoked_at,
            "revoked_reason": self.revoked_reason,
            "is_live": self.is_live,
        }


def default_expiry(hours: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=hours)
