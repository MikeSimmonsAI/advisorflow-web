"""
Video meeting provider models — Checkpoint 4.

ZOOM IS PROVIDER #1, NOT THE ARCHITECTURE. Nothing in this file names Zoom in a
column, a constraint or a table name. `provider` is a string from the vocabulary
below, and adding Teams or Google Meet later means adding a value and a provider
class — not a migration and not a second appointment model.

WHY A SEPARATE TABLE rather than more columns on SalesAppointment
------------------------------------------------------------------
`sales_appointments.meeting_url` already exists and is a plain, user-supplied
string — someone can paste any link into it, and Checkpoint 2 let them. That
column stays exactly what it is: whatever a human typed.

A PROVISIONED meeting is a different thing. It has an id in someone else's
system, a host URL that must never reach a prospect, a sync status, and a
failure mode. Mixing "the rep pasted a link" with "we created a Zoom meeting and
own its lifecycle" in one nullable column is how you end up unable to answer
"did we create this, and can we cancel it?"

HOST URLS ARE NOT ATTENDEE URLS
-------------------------------
`start_url` from Zoom embeds a host token that starts the meeting AS the host.
Anyone holding it can impersonate the host. It is stored encrypted, is never
returned by any prospect-facing endpoint, and is never put in an email, a
calendar event body or an .ics file. `join_url` is the only link that travels.
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

PROVIDER_ZOOM = "zoom"
PROVIDER_TEAMS = "teams"          # not implemented — reserved so the vocabulary
PROVIDER_MEET = "google_meet"     # is stable before the code exists
PROVIDER_NONE = "none"            # explicitly no video: phone, in person
MEETING_PROVIDERS = (PROVIDER_ZOOM,)          # what is actually implemented
KNOWN_PROVIDERS = (PROVIDER_ZOOM, PROVIDER_TEAMS, PROVIDER_MEET, PROVIDER_NONE)

PROVIDER_LABELS = {
    PROVIDER_ZOOM: "Zoom",
    PROVIDER_TEAMS: "Microsoft Teams",
    PROVIDER_MEET: "Google Meet",
    PROVIDER_NONE: "No video",
}

# Lifecycle of the PROVIDER's copy of the meeting, not of the appointment.
MEET_PENDING   = "pending"
MEET_CREATED   = "created"
MEET_UPDATED   = "updated"
MEET_CANCELLED = "cancelled"
MEET_FAILED    = "failed"
MEET_NOT_REQUIRED = "not_required"   # the meeting type does not want video
MEET_STATES = (MEET_PENDING, MEET_CREATED, MEET_UPDATED, MEET_CANCELLED,
               MEET_FAILED, MEET_NOT_REQUIRED)

# What a human must act on. NOT_REQUIRED and CANCELLED are resting states.
MEET_NEEDS_ATTENTION = (MEET_FAILED,)

MEET_LABELS = {
    MEET_PENDING:      "Creating…",
    MEET_CREATED:      "Zoom ready",
    MEET_UPDATED:      "Zoom updated",
    MEET_CANCELLED:    "Zoom cancelled",
    MEET_FAILED:       "Zoom failed",
    MEET_NOT_REQUIRED: "No video meeting",
}


class AppointmentMeeting(Base):
    """The provider's copy of one sales appointment's video meeting.

    ONE ROW PER APPOINTMENT (unique constraint). A reschedule UPDATES this row
    and the provider meeting behind it; it never creates a second one. That is
    what keeps the join URL in the prospect's original invitation valid after a
    meeting moves.
    """
    __tablename__ = "appointment_meetings"

    id = Column(String, primary_key=True, default=gen_uuid)
    appointment_id = Column(String, ForeignKey("sales_appointments.id", ondelete="CASCADE"),
                            nullable=False)
    # Denormalised so a manager view can scope by brand without joining through
    # the appointment, and so multi-brand isolation is enforceable on this table.
    brand_sales_org_id = Column(String, ForeignKey("brand_sales_orgs.id", ondelete="CASCADE"),
                                nullable=True)

    provider = Column(String, nullable=False)              # KNOWN_PROVIDERS
    provider_meeting_id = Column(String, nullable=True)    # what update/cancel keys on

    # THE ONLY LINK THAT MAY REACH A PROSPECT.
    join_url = Column(String, nullable=True)
    # Convenience copies from the provider. A passcode is not a secret from the
    # attendee — it is printed in the invitation — so it is not encrypted.
    passcode = Column(String, nullable=True)
    dial_in_info = Column(Text, nullable=True)

    # HOST-ONLY. Encrypted at rest with the same Fernet key as OAuth tokens, and
    # excluded from every serializer. Holding this URL lets someone start the
    # meeting AS the host, so it is treated as a credential, not a link.
    host_url_encrypted = Column(Text, nullable=True)

    status = Column(String, default=MEET_PENDING, nullable=False)   # MEET_STATES
    attempts = Column(Integer, default=0, nullable=False)
    provider_error = Column(Text, nullable=True)      # message only, never a token

    created_at     = Column(DateTime, default=datetime.utcnow)
    updated_at     = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_synced_at = Column(DateTime, nullable=True)  # last SUCCESSFUL provider call
    cancelled_at   = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("appointment_id", name="uq_appointment_meeting_appt"),
        Index("ix_appointment_meeting_status", "status", "created_at"),
        Index("ix_appointment_meeting_brand", "brand_sales_org_id"),
    )


class MeetingProviderConfig(Base):
    """Per-BRAND video provider credentials.

    Why per brand and not one global Zoom account: EvoSys Pro, BookaBoost and
    Harmony Hustle are different businesses to a prospect. A BookaBoost demo
    arriving from an EvoSys Pro Zoom account is a white-label leak.

    Resolution order is config row → environment variables. The env vars are the
    single-brand path that works today with no setup; this table is what makes
    the second brand possible without a code change.

    SECRETS ARE ENCRYPTED with the same Fernet key as OAuth refresh tokens, and
    no endpoint ever returns them — the API returns `has_credentials: true`.
    """
    __tablename__ = "meeting_provider_configs"

    id = Column(String, primary_key=True, default=gen_uuid)
    brand_sales_org_id = Column(String, ForeignKey("brand_sales_orgs.id", ondelete="CASCADE"),
                                nullable=False)
    provider = Column(String, nullable=False)      # KNOWN_PROVIDERS

    # Zoom Server-to-Server OAuth. Chosen over user-level OAuth deliberately:
    # meetings are hosted by the BRAND, not by whichever rep happened to book
    # them, so a rep leaving must not take the meetings with them. There is also
    # no per-user consent screen to complete before the team can sell.
    account_id_encrypted     = Column(Text, nullable=True)
    client_id_encrypted      = Column(Text, nullable=True)
    client_secret_encrypted  = Column(Text, nullable=True)

    # Which Zoom user hosts. "me" resolves to the account owner. A shared sales
    # host is usually better than an individual's mailbox.
    host_identifier = Column(String, nullable=True)

    is_active = Column(Boolean, default=True, nullable=False)
    # Set only by a real API round-trip, never optimistically on save.
    last_verified_at = Column(DateTime, nullable=True)
    last_error       = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("brand_sales_org_id", "provider",
                         name="uq_meeting_config_brand_provider"),
    )


class MeetingIntelligence(Base):
    """Placeholder for post-meeting intelligence. Deliberately EMPTY of logic.

    Checkpoint 4 explicitly does not build the AI meeting assistant. What it
    does build is the ATTACHMENT POINT, so that when transcripts and summaries
    arrive they hang off the existing appointment and opportunity rather than
    justifying a second opportunity model to hold them.

    Nothing writes to this table yet. It exists so the shape is decided now,
    while the surrounding design is fresh, instead of being improvised later
    under delivery pressure.
    """
    __tablename__ = "meeting_intelligence"

    id = Column(String, primary_key=True, default=gen_uuid)
    appointment_id = Column(String, ForeignKey("sales_appointments.id", ondelete="CASCADE"),
                            nullable=False)
    # BOTH links are stored. The appointment is where the conversation happened;
    # the opportunity is what a manager actually reads. Deriving one from the
    # other at query time is a join on every deal-review screen.
    opportunity_id = Column(String, ForeignKey("opportunities.id", ondelete="CASCADE"),
                            nullable=True)

    # References, not payloads. A recording lives with the provider that made it.
    provider = Column(String, nullable=True)
    recording_reference = Column(String, nullable=True)
    transcript_reference = Column(String, nullable=True)
    transcript_text = Column(Text, nullable=True)

    # Derived, and marked as derived. A human must always be able to tell what
    # a model asserted from what a person recorded.
    ai_summary          = Column(Text, nullable=True)
    questions_asked     = Column(Text, nullable=True)
    objections          = Column(Text, nullable=True)
    competitors_mentioned = Column(Text, nullable=True)
    action_items        = Column(Text, nullable=True)
    next_steps          = Column(Text, nullable=True)
    discovery_gaps      = Column(Text, nullable=True)
    risk_signals        = Column(Text, nullable=True)
    generated_by        = Column(String, nullable=True)   # model/version that produced it
    generated_at        = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_meeting_intel_appt", "appointment_id"),
        Index("ix_meeting_intel_opp", "opportunity_id"),
    )
