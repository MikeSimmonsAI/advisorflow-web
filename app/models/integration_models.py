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

# Two kinds, because there are two tenancy trees and a credential must belong to
# exactly one of them. `retell` reaches BrandSalesOrg sales scheduling;
# `retell_tenant` reaches a customer Organization's advisors. A key of one kind
# is refused by every route of the other — not filtered, refused — so a funeral
# home's voice agent has no path at all into brand-sales scheduling, and vice
# versa. See `scope_kind` below and `require_retell` / `require_retell_tenant`.
INTEGRATION_RETELL = "retell"
INTEGRATION_RETELL_TENANT = "retell_tenant"
INTEGRATION_KINDS = (INTEGRATION_RETELL, INTEGRATION_RETELL_TENANT)

SCOPE_BRAND = "brand"
SCOPE_TENANT = "tenant"

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

    # EXACTLY ONE of these two is set, and which one is fixed at issue time.
    # Both are nullable at the database level because neither is universal; the
    # invariant is enforced in `scope_kind`, which raises rather than guessing,
    # and at the boundary by the kind-specific dependency. A row with both set
    # or neither set is a bug that fails closed instead of resolving somewhere
    # unexpected.
    brand_sales_org_id = Column(String, ForeignKey("brand_sales_orgs.id", ondelete="CASCADE"),
                                nullable=True, index=True)
    # The customer tenant, for `retell_tenant` keys. This is a funeral home.
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"),
                             nullable=True, index=True)
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

    def scope_kind(self) -> str:
        """SCOPE_BRAND or SCOPE_TENANT. Raises on an ambiguous row.

        There is no default and no preference order here on purpose. A key whose
        scope cannot be determined must not resolve to whichever tree happens to
        be checked first — that is precisely the mistake this whole two-column
        arrangement exists to make impossible.
        """
        has_brand = bool(self.brand_sales_org_id)
        has_tenant = bool(self.organization_id)
        if has_brand and has_tenant:
            raise ValueError(
                "Credential %s is scoped to both a brand and a tenant." % self.key_prefix)
        if has_brand:
            return SCOPE_BRAND
        if has_tenant:
            return SCOPE_TENANT
        raise ValueError("Credential %s is scoped to nothing." % self.key_prefix)

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
    # Whichever tree this request ran in. Exactly one is ever populated, which
    # makes "did this key ever touch the other tree?" a query rather than an
    # argument.
    brand_sales_org_id = Column(String, nullable=True, index=True)
    organization_id    = Column(String, nullable=True, index=True)
    advisor_user_id    = Column(String, nullable=True)

    external_ref   = Column(String, nullable=True)
    # The record created, in whichever tree. A brand booking is a
    # SalesAppointment; a tenant booking is a BookingLink. Separate columns
    # because they are separate tables and an id that could mean either is an id
    # nobody can safely follow.
    appointment_id   = Column(String, nullable=True)
    booking_link_id  = Column(String, nullable=True)
    lead_id          = Column(String, nullable=True)

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
