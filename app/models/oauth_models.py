"""OAUTH AUTHORIZATION TRANSACTIONS.

WHY THIS TABLE EXISTS
---------------------
The Google and Microsoft connect flows used to put the advisor's user_id into
the OAuth `state` parameter and read it back out in the callback:

    state = advisor_user_id                     # /calendar/connect
    state = "setup:%s" % advisor_user_id        # /setup/google-connect
    ...
    real_user_id = state[6:] if state.startswith("setup:") else state
    handle_oauth_callback(db, advisor_user_id=real_user_id, ...)

`state` is a value the BROWSER carries. Nothing signed it, nothing expired it,
and nothing recorded that it had been used. So anyone who could complete a
legitimate provider authorization could name whose account the resulting
refresh token was stored on: run the real consent screen with your own Google
account, hand the callback somebody else's user_id, and that person's stored
integration credentials are replaced with yours. Their calendar writes and
their outbound mail then run through an attacker-controlled grant. The
"setup:" prefix made it worse rather than better — it was a second, equally
unauthenticated way to say the same thing.

WHAT REPLACES IT
----------------
A row, created at INITIATION, while the caller's identity is still known from
their session (or from a verified setup token). The row — not the browser —
carries the identity, and the callback's only job is to resolve the row.

Four properties, each of which closes one of the reported attacks:

  * TAMPER-RESISTANT. The `state` handed to the provider is
    "<transaction id>.<secret>". Only the SHA-256 of the secret is stored, so
    a stolen database row cannot be turned back into a usable state, and a
    modified state fails the digest comparison rather than selecting a
    different user. Changing the id alone is useless without the matching
    secret.
  * EXPIRING. `expires_at` is short (minutes). A consent screen left open
    overnight is not a credential.
  * SINGLE-USE. `consumed_at` is set by a conditional UPDATE, so exactly one
    callback can ever consume a given transaction. A replayed callback URL —
    the same code, the same state, sent twice — is refused the second time.
  * BOUND. `provider`, `flow`, `user_id` and `organization_id` are all recorded
    at initiation and re-checked at consumption. A Google transaction cannot be
    presented to the Microsoft callback, and a user whose tenancy moved between
    initiation and callback is refused rather than silently re-homed.

WHAT A ROW IS NOT
-----------------
A row is not authority to sign in. It authorises exactly one thing: attaching
one provider grant to the one user named on it. It is never consulted by
`deps.get_current_user` and cannot be exchanged for a session.
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, String

from app.models.models import Base


def gen_uuid():
    return str(uuid.uuid4())


# Providers. A transaction is issued for exactly one of these and may only be
# consumed by that provider's callback.
PROVIDER_GOOGLE = "google"
PROVIDER_MICROSOFT = "microsoft"
PROVIDERS = (PROVIDER_GOOGLE, PROVIDER_MICROSOFT)

# Flows. This is the value that used to be the "setup:" prefix on the state
# string. It decides which frontend page the callback redirects back to, and
# nothing else — it is READ from the transaction, never from the browser.
FLOW_SETTINGS = "settings"      # a signed-in advisor connecting from /settings
FLOW_SETUP = "setup"            # an advisor following an admin's setup link
FLOWS = (FLOW_SETTINGS, FLOW_SETUP)

# How long a started authorization may sit unfinished. Long enough for a real
# person to read a consent screen, pick an account and approve; short enough
# that an abandoned tab is not a standing authorization. Google's own consent
# session is comparable.
TRANSACTION_TTL_MINUTES = 15


class OAuthAuthorizationTransaction(Base):
    """One started provider authorization, and whose it is."""

    __tablename__ = "oauth_auth_transactions"

    id = Column(String, primary_key=True, default=gen_uuid)

    # SHA-256 hex of the secret half of the state string. The secret itself is
    # never stored: this column proves a presented state matches without being
    # able to produce one.
    secret_hash = Column(String(64), nullable=False)

    provider = Column(String(32), nullable=False)
    flow = Column(String(32), nullable=False, default=FLOW_SETTINGS)

    # WHOSE GRANT THIS WILL BECOME. The callback reads the user from here and
    # from nowhere else.
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)

    # The tenant the subject belonged to at initiation. Re-checked at
    # consumption so a transaction cannot survive the subject being moved
    # between organizations mid-flow.
    organization_id = Column(String, nullable=True)

    # Who STARTED it, which is not always the subject: an org admin generates a
    # setup link for an advisor. Recorded for audit; never used to widen scope.
    initiated_by_user_id = Column(String, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime, nullable=False)
    consumed_at = Column(DateTime, nullable=True)

    # Audit breadcrumbs. Never consulted in a decision.
    created_ip = Column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_oauth_txn_expires", "expires_at"),
        Index("ix_oauth_txn_consumed", "consumed_at"),
    )

    # ── read-only helpers ────────────────────────────────────────────────────

    @staticmethod
    def _aware(value):
        """SQLite hands back naive datetimes; Postgres may hand back aware ones.

        Comparing the two raises TypeError, and an authorisation check that
        raises is an outage. Everything written here is UTC, so a naive value is
        read as UTC rather than as local time. Same helper, same reason, as
        `UserSession._aware` in session_models.py.
        """
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    @property
    def is_consumed(self) -> bool:
        return self.consumed_at is not None

    @property
    def is_expired(self) -> bool:
        exp = self._aware(self.expires_at)
        return exp is not None and exp <= datetime.now(timezone.utc)

    @property
    def is_usable(self) -> bool:
        return not self.is_consumed and not self.is_expired

    @staticmethod
    def default_expiry() -> datetime:
        return datetime.now(timezone.utc) + timedelta(
            minutes=TRANSACTION_TTL_MINUTES)
