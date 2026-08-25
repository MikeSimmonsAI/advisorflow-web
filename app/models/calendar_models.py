"""
External calendar sync — connections, cached busy, confirmation tokens.

CHECKPOINT 3. Registers on the SAME Base as the other model modules.

THE RULE
--------
`sales_appointments` is the source of truth. Everything here is DOWNSTREAM: a
provider event is a copy that AdvisorFlow pushes out and can rebuild. Nothing in
this module may ever cause an appointment to be lost, rolled back, or hidden
because a provider failed.

WHERE THE TOKENS LIVE — deliberately NOT here
---------------------------------------------
OAuth refresh tokens stay in the columns that already hold them:

    users.microsoft_oauth_refresh_token_encrypted   (+ microsoft_email_address)
    users.google_oauth_refresh_token_encrypted      (+ google_calendar_id)

both written through app/utils/crypto.py (Fernet, ENCRYPTION_KEY). Copying them
into a second table would mean two places to keep in sync, two places to leak
from, and two places to rotate. `CalendarConnection` holds only the STATE that
Checkpoint 3 needs — is it working, when did it last sync, what broke — and
reads the token from the user row when it needs one.

A connection belongs to a USER, not to a customer tenant and not to a brand
sales org. One human, one calendar, however many brands they sell.
"""

from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Text, Integer,
    UniqueConstraint, Index,
)
from datetime import datetime
import uuid

from app.models.models import Base


def gen_uuid():
    return str(uuid.uuid4())


# ── vocabularies ─────────────────────────────────────────────────────────────

PROVIDER_MICROSOFT = "microsoft"
PROVIDER_GOOGLE    = "google"
PROVIDER_ICS       = "ics"           # not a connection — the fallback delivery
PROVIDERS = (PROVIDER_MICROSOFT, PROVIDER_GOOGLE)

PROVIDER_LABELS = {
    PROVIDER_MICROSOFT: "Microsoft 365",
    PROVIDER_GOOGLE:    "Google Calendar",
    PROVIDER_ICS:       "Email invitation",
}

# Per-participant sync state. NOT_CONNECTED is not a failure — it is the normal
# state for someone who has chosen not to connect a calendar, and it routes to
# the .ics fallback rather than to an error.
SYNC_NOT_CONNECTED = "not_connected"
SYNC_PENDING       = "pending"
SYNC_SYNCED        = "synced"
SYNC_FAILED        = "failed"
SYNC_RETRYING      = "retrying"
SYNC_REAUTH        = "reauth_required"   # token rejected: only the user can fix it
SYNC_ICS_SENT      = "ics_sent"
SYNC_STATES = (SYNC_NOT_CONNECTED, SYNC_PENDING, SYNC_SYNCED, SYNC_FAILED,
               SYNC_RETRYING, SYNC_REAUTH, SYNC_ICS_SENT)

# States a human needs to do something about. Drives Manager Attention.
SYNC_NEEDS_ATTENTION = (SYNC_FAILED, SYNC_REAUTH, SYNC_RETRYING)

# What each state is called in the UI. Defined beside the vocabulary so a new
# state cannot be added without someone deciding what a human should read —
# and so no screen invents its own wording for the same condition.
#
# NOT_CONNECTED and ICS_SENT deliberately do NOT say "failed" or "error". They
# are normal outcomes for someone who has not connected a calendar, and dressing
# them up as problems would push people to fix something that is not broken.
SYNC_LABELS = {
    SYNC_NOT_CONNECTED: "Calendar not connected",
    SYNC_PENDING:       "Syncing…",
    SYNC_SYNCED:        "On their calendar",
    SYNC_ICS_SENT:      "Invite sent by email",
    SYNC_RETRYING:      "Retrying",
    SYNC_FAILED:        "Calendar sync failed",
    SYNC_REAUTH:        "Reconnect required",
}


class CalendarConnection(Base):
    """One user's link to one provider. State only — never the token itself.

    `is_connected` is written from the OAuth callback and from a failed refresh;
    it is never inferred optimistically. If token validation has not actually
    succeeded, this says so, because a UI that claims CONNECTED when the token
    is dead is worse than one that says nothing.
    """
    __tablename__ = "calendar_connections"

    id       = Column(String, primary_key=True, default=gen_uuid)
    user_id  = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    provider = Column(String, nullable=False)          # PROVIDERS

    is_connected  = Column(Boolean, default=False, nullable=False)
    account_email = Column(String, nullable=True)      # the mailbox we actually reached
    calendar_id   = Column(String, nullable=True)      # Google: "primary". Graph: default calendar.

    # Whether the granted consent actually covers calendar writes. Microsoft's
    # original consent in this app asked for Mail.Send only, so an old
    # connection can be live for email and useless for calendar. That is a
    # different problem from "not connected" and the UI must be able to say so.
    calendar_scope_ok = Column(Boolean, default=False, nullable=False)

    connected_at    = Column(DateTime, nullable=True)
    disconnected_at = Column(DateTime, nullable=True)
    last_sync_at    = Column(DateTime, nullable=True)   # last SUCCESSFUL operation
    last_attempt_at = Column(DateTime, nullable=True)
    last_error      = Column(Text, nullable=True)       # message only — never a token
    last_error_at   = Column(DateTime, nullable=True)
    failure_count   = Column(Integer, default=0, nullable=False)

    # Which busy window was last successfully read, and when.
    #
    # This lives here rather than being inferred from external_busy_blocks
    # because a calendar with NO meetings this week produces no rows at all.
    # Judging freshness by row timestamps would re-fetch that empty week on
    # every keystroke — the emptier someone's calendar, the harder we would hit
    # the vendor. Recording the window itself makes "we looked and found
    # nothing" a fact we can store.
    busy_window_start = Column(DateTime, nullable=True)
    busy_window_end   = Column(DateTime, nullable=True)
    busy_fetched_at   = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_calendar_conn_user_provider"),
        Index("ix_calendar_conn_user", "user_id", "is_connected"),
    )


class ExternalBusyBlock(Base):
    """Cached busy periods read from a connected provider calendar.

    WHY A CACHE AND NOT A LIVE CALL: the shared-availability finder evaluates
    every required participant across a date range, and a live free/busy request
    per user per search would put Microsoft and Google in the hot path of every
    keystroke-speed interaction. The finder refreshes this table once per user
    per search window (with a short TTL) and the availability engine then reads
    it like any other blocking interval.

    Only the INTERVAL is stored. Subject, attendees, location and body are
    deliberately not persisted: a colleague needs to know you are busy, not what
    you are doing. `is_private` records that we were told a title but chose not
    to keep it.
    """
    __tablename__ = "external_busy_blocks"

    id       = Column(String, primary_key=True, default=gen_uuid)
    user_id  = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    provider = Column(String, nullable=False)

    starts_at = Column(DateTime, nullable=False)   # naive UTC
    ends_at   = Column(DateTime, nullable=False)

    # So a refresh can replace exactly what it re-read, and so an event that
    # AdvisorFlow itself created can be recognised and not double-counted.
    provider_event_id = Column(String, nullable=True)
    is_all_day        = Column(Boolean, default=False, nullable=False)
    is_private        = Column(Boolean, default=True, nullable=False)

    fetched_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    # The window this row was fetched as part of, so a stale-cache check knows
    # what was actually covered rather than guessing from row timestamps.
    window_start = Column(DateTime, nullable=True)
    window_end   = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_ext_busy_user_time", "user_id", "starts_at", "ends_at"),
        Index("ix_ext_busy_fetch", "user_id", "provider", "fetched_at"),
    )


class AppointmentConfirmationToken(Base):
    """The prospect-facing confirm / decline link.

    Modelled on ProposalToken, which is the established pattern in this codebase
    for an unauthenticated public link: opaque value, optional expiry, explicit
    revocation, first-redemption recorded. A prospect must never need an account
    to confirm a meeting.

    The token is generated with `secrets.token_urlsafe`, not uuid4 — this one
    guards a state change on a real appointment, so it wants CSPRNG entropy
    rather than a UUID's structured, partly-predictable bytes.
    """
    __tablename__ = "appointment_confirmation_tokens"

    id = Column(String, primary_key=True, default=gen_uuid)
    appointment_id = Column(String, ForeignKey("sales_appointments.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    token = Column(String, unique=True, nullable=False, index=True)

    recipient_email = Column(String, nullable=True)
    recipient_name  = Column(String, nullable=True)

    expires_at        = Column(DateTime, nullable=True)   # NULL = no expiry
    first_redeemed_at = Column(DateTime, nullable=True)
    last_used_at      = Column(DateTime, nullable=True)
    revoked_at        = Column(DateTime, nullable=True)
    use_count         = Column(Integer, default=0, nullable=False)

    # What the prospect chose, kept beside the appointment's own confirmation
    # fields so a later dispute can be answered from the token's own record.
    responded_action = Column(String, nullable=True)      # confirm | decline
    responded_at     = Column(DateTime, nullable=True)
    responded_ip     = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)


class AppointmentSyncLog(Base):
    """Append-only record of every attempt to push an appointment outward.

    Exists because a sync failure is invisible by nature: the meeting still
    looks fine in AdvisorFlow, and the salesperson only finds out when someone
    does not show up. This is what Manager Attention and the retry action read.

    Never stores a token, an access code, or a full provider response — only
    provider, action, outcome and a truncated error message.
    """
    __tablename__ = "appointment_sync_logs"

    id = Column(String, primary_key=True, default=gen_uuid)
    appointment_id = Column(String, ForeignKey("sales_appointments.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    user_id  = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    provider = Column(String, nullable=False)

    action  = Column(String, nullable=False)    # create | update | cancel | ics | invite
    status  = Column(String, nullable=False)    # SYNC_STATES
    ok      = Column(Boolean, default=False, nullable=False)
    external_event_id = Column(String, nullable=True)
    error_code    = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)
    attempt       = Column(Integer, default=1, nullable=False)

    occurred_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_sync_log_appt_time", "appointment_id", "occurred_at"),
        Index("ix_sync_log_status", "status", "occurred_at"),
    )
