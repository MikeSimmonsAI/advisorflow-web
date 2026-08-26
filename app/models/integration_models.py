"""Machine credentials for trusted external integrations, and their audit trail.

WHY THIS EXISTS. Until now the only inbound credential in this backend was a
per-user login JWT: obtained with an email and password, 24 hours long, and
single-session — issuing a new one invalidates the last, so a service sharing a
human's account would log that human out on every call. There was no way for a
trusted external system to identify itself. The alternative on offer was an
endpoint with no authentication at all, which is not an alternative.

WHAT THIS IS NOT. It is not a user, and it must never become one. A credential
here has no password, cannot log in, holds no role, and cannot reach any route
outside the narrow integration surface it was issued for. It is a scoped key
that says "this system, this brand, these advisors, these two operations".

THE SECRET IS NEVER STORED. Only a SHA-256 hash of the full key is persisted,
alongside a short non-secret prefix used to find the row. The key itself is
printed once, to the operator's terminal, by the issuing script and then exists
nowhere in this system — not in the database, not in a log, not in an email.
SHA-256 rather than bcrypt is deliberate: this is a 32-byte CSPRNG token, not a
human-chosen password, so there is nothing to brute-force offline and a slow KDF
would only tax every request.
"""

from sqlalchemy import (
    Column, String, Boolean, DateTime, Integer, Text, ForeignKey,
    UniqueConstraint, Index,
)
from datetime import datetime
import uuid

from app.models.models import Base


def gen_uuid():
    return str(uuid.uuid4())


# ── vocabulary ──────────────────────────────────────────────────────────────

INTEGRATION_RETELL = "retell"
INTEGRATION_KINDS = (INTEGRATION_RETELL,)

ACTION_PING         = "ping"
ACTION_AVAILABILITY = "availability"
ACTION_BOOK         = "book"
INTEGRATION_ACTIONS = (ACTION_PING, ACTION_AVAILABILITY, ACTION_BOOK)

# The visible, non-secret half of a key. Safe to log, safe to put in a support
# ticket, useless on its own.
KEY_PREFIX_LEN = 12


class IntegrationCredential(Base):
    """One trusted external system, scoped to one brand.

    SCOPE IS THE SECURITY MODEL. `brand_sales_org_id` is not optional and is not
    a filter the caller can widen — it is fixed at issue time. Every request
    made with this key is resolved inside that brand and nowhere else, so a
    compromised key cannot be walked sideways into another brand's calendars.

    `allowed_advisor_ids` narrows it further when a key should only ever reach
    named people. Empty means "any active member of this brand", which is
    already a closed set.
    """
    __tablename__ = "integration_credentials"

    id   = Column(String, primary_key=True, default=gen_uuid)
    # The integration identity that appears in every audit row. A human name,
    # because "who was this?" is the first question anyone asks of a log.
    name = Column(String, nullable=False)
    kind = Column(String, default=INTEGRATION_RETELL, nullable=False)

    # Lookup handle. Non-secret by construction — it is the leading characters
    # of the key and proves nothing on its own.
    key_prefix = Column(String, nullable=False, unique=True, index=True)
    # SHA-256 hex of the FULL key. The key is never stored anywhere.
    key_hash   = Column(String, nullable=False)

    brand_sales_org_id = Column(String, ForeignKey("brand_sales_orgs.id", ondelete="CASCADE"),
                                nullable=False, index=True)
    # Used when the caller names no advisor — the common case for a voice agent
    # that only ever books one person.
    default_advisor_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    # Comma-separated user ids. Empty/NULL = any active member of the brand.
    allowed_advisor_ids = Column(Text, nullable=True)

    # Per-key ceiling, so one integration cannot exhaust the service for another.
    rate_limit_per_minute = Column(Integer, default=60, nullable=False)

    is_active  = Column(Boolean, default=True, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    # Written on every successful authentication so a key nobody uses is visible
    # as such and can be retired.
    last_used_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_by = Column(String, ForeignKey("users.id"), nullable=True)
    note       = Column(Text, nullable=True)

    def advisor_allowlist(self):
        raw = (self.allowed_advisor_ids or "").strip()
        if not raw:
            return []
        return [p.strip() for p in raw.split(",") if p.strip()]

    def is_usable(self, now=None) -> bool:
        """Fail closed: anything other than a live, unrevoked key is a no."""
        if not self.is_active:
            return False
        if self.revoked_at is not None:
            return False
        return True


class IntegrationRequestLog(Base):
    """Append-only record of every request a machine credential made.

    Answers, without opening the application: which integration, what it asked
    for, which advisor it targeted, whether it worked, what was created, when.

    IT IS ALSO THE IDEMPOTENCY LEDGER. `external_ref` is the caller's own id for
    a booking attempt; the unique constraint on (credential_id, external_ref) is
    what makes a retry return the original appointment instead of creating a
    second one. Keeping both in one table means the answer to "did this already
    happen?" and the answer to "what happened?" can never disagree.

    Availability requests carry a NULL `external_ref`, and NULLs do not collide
    in either Postgres or SQLite, so they log freely alongside bookings.

    NO SECRET VALUE IS EVER WRITTEN HERE — only the non-secret key prefix.
    """
    __tablename__ = "integration_request_logs"

    id            = Column(String, primary_key=True, default=gen_uuid)
    credential_id = Column(String, ForeignKey("integration_credentials.id", ondelete="SET NULL"),
                           nullable=True, index=True)
    # Denormalised so the trail survives the credential being deleted.
    integration_name = Column(String, nullable=True)
    key_prefix       = Column(String, nullable=True)

    action = Column(String, nullable=False)          # ping | availability | book
    brand_sales_org_id = Column(String, nullable=True, index=True)
    advisor_user_id    = Column(String, nullable=True)

    external_ref   = Column(String, nullable=True)
    appointment_id = Column(String, nullable=True)

    success     = Column(Boolean, default=False, nullable=False)
    status_code = Column(Integer, nullable=True)
    # Human-readable outcome. Never a token, never a calendar body.
    detail      = Column(Text, nullable=True)

    occurred_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    __table_args__ = (
        UniqueConstraint("credential_id", "external_ref",
                         name="uq_integration_external_ref"),
        Index("ix_integration_log_cred_time", "credential_id", "occurred_at"),
    )
