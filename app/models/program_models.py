"""LOCATION OUTREACH PROGRAMS - a multi-location customer run through one person.

Built for SCI (Service Corporation International): one company, dozens of
funeral homes and cemeteries, ONE regional human (Kerry Allan) who is the
customer-facing contact for every one of them. The family must experience
their LOCAL home - "Kerry Allan | Eastern Gate Memorial Gardens" - never a
generic corporate sender.

NOTHING HERE IS SCI-SPECIFIC IN CODE. SCI is rows: an OutreachProgram for its
organization, LocationProfiles for its homes, CampaignFamilies, assets. Any
other multi-location customer is configured the same way.

ADDITIVE ONLY. Every table here is new; nothing alters an existing table, so
create_all() is the whole migration and no existing tenant is touched. A
customer with no OutreachProgram row behaves exactly as before.

ORIGINAL SOURCE VALUES ARE NEVER OVERWRITTEN. ProgramSourceRecord keeps the
customer's row exactly as supplied (raw JSON); every derived decision
(location, duplicate link, data-note flag) sits beside it, and verification
results live in ContactVerification - never on top of the original.
"""

from datetime import datetime
import uuid

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Index, Integer, LargeBinary, String,
    Text, UniqueConstraint,
)

from app.models.models import Base


def _uuid() -> str:
    return str(uuid.uuid4())


# ── vocabularies (strings, not DB enums: additive and SQLite-safe) ───────────

RESPONSE_CLASSES = ("hot", "active", "low", "opt_out", "bad_data")
HANDLING_STATES = ("new", "opened", "responded", "active", "closed")
EMAIL_MODES = ("none", "hosted", "attached")
LOCATION_STATUS = ("mapped", "location_review")
LINK_STATUS = ("primary", "linked", "duplicate_review", "unique")


class OutreachProgram(Base):
    """One per organization that runs location-aware outreach."""
    __tablename__ = "outreach_programs"

    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, unique=True)
    name = Column(String, nullable=False)
    hero_title = Column(String, nullable=True)
    hero_subtitle = Column(String, nullable=True)
    # The human every family hears from, whichever home they belong to.
    primary_contact_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    primary_contact_name = Column(String, nullable=True)
    primary_contact_title = Column(String, nullable=True)
    # Where alerts go. BLANK UNTIL SUPPLIED - never guessed, never hard-coded.
    alert_email = Column(String, nullable=True)
    alert_phone = Column(String, nullable=True)
    # JSON list of {"name", "email", "phone", "role"} - management escalation.
    management_recipients = Column(Text, nullable=True)
    hot_sla_minutes = Column(Integer, nullable=False, default=15)
    # Customers reply by text or email; nothing asks them to call.
    customer_channels = Column(Text, nullable=True)          # JSON ["sms","email"]
    reply_instructions_sms = Column(Text, nullable=True)
    reply_instructions_email = Column(Text, nullable=True)
    # A send with no resolved location is refused and parked in Location Review.
    require_location_to_send = Column(Boolean, nullable=False, default=True)
    # Staff SMS alerts are a real send; off until someone turns them on.
    staff_sms_alerts_enabled = Column(Boolean, nullable=False, default=False)
    logo_asset_id = Column(String, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class LocationProfile(Base):
    """What a family sees of their home. One per Location (plus the review bucket)."""
    __tablename__ = "location_profiles"
    __table_args__ = (
        UniqueConstraint("organization_id", "location_id", name="uq_location_profile_location"),
        Index("ix_location_profiles_org", "organization_id"),
    )

    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    location_id = Column(String, ForeignKey("locations.id"), nullable=False)
    official_name = Column(String, nullable=False)
    # Every spelling the customer's source data used for this home (JSON list).
    source_names = Column(Text, nullable=True)
    is_review_bucket = Column(Boolean, nullable=False, default=False)
    website = Column(String, nullable=True)
    facility_phone = Column(String, nullable=True)
    manager_name = Column(String, nullable=True)
    advisor_names = Column(Text, nullable=True)              # JSON list
    email_display_name = Column(String, nullable=True)       # override; default "<contact> | <home>"
    sms_identity_name = Column(String, nullable=True)        # override for the SMS sign-off
    appointment_link = Column(String, nullable=True)
    logo_asset_id = Column(String, nullable=True)
    hero_asset_id = Column(String, nullable=True)
    brand_settings = Column(Text, nullable=True)             # JSON
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ProgramSourceRecord(Base):
    """One customer source row, preserved exactly, with the decisions made about it.

    The customer's Lead ID is unique per organization: 551 rows in, 551 rows
    here, whatever the duplicate analysis decides. Nothing is ever deleted.
    """
    __tablename__ = "program_source_records"
    __table_args__ = (
        UniqueConstraint("organization_id", "source_lead_id", name="uq_program_source_lead"),
        Index("ix_program_source_org_location", "organization_id", "location_id"),
        Index("ix_program_source_master", "organization_id", "contact_master_key"),
    )

    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    source_lead_id = Column(String, nullable=False)
    import_run_id = Column(String, nullable=True)
    row_number = Column(Integer, nullable=True)
    raw_json = Column(Text, nullable=False)                  # the row exactly as supplied
    # Convenience copies of ORIGINAL values (read-only by convention).
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    email = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    source_status = Column(String, nullable=True)
    source_campaign = Column(String, nullable=True)
    source_channel = Column(String, nullable=True)
    source_location_name = Column(String, nullable=True)
    source_owner = Column(String, nullable=True)
    source_manager = Column(String, nullable=True)
    # Decisions.
    location_id = Column(String, ForeignKey("locations.id"), nullable=True)
    location_status = Column(String, nullable=False, default="location_review")
    campaign_family = Column(String, nullable=True)
    contact_master_key = Column(String, nullable=True)
    link_status = Column(String, nullable=False, default="unique")
    link_reason = Column(String, nullable=True)
    duplicate_review_reason = Column(Text, nullable=True)
    data_note_flags = Column(Text, nullable=True)            # JSON list
    needs_data_review = Column(Boolean, nullable=False, default=False)
    # A PERSON'S DECISIONS SURVIVE A RE-IMPORT: a location assigned by hand, and
    # a review a person cleared, are not undone by staging the file again.
    location_assigned_manually = Column(Boolean, nullable=False, default=False)
    data_review_cleared_at = Column(DateTime, nullable=True)
    duplicate_review_cleared_at = Column(DateTime, nullable=True)
    # Set only when the staged row is later committed to a live contact/lead.
    org_contact_id = Column(String, nullable=True)
    lead_id = Column(String, ForeignKey("leads.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ProgramImportRun(Base):
    __tablename__ = "program_import_runs"

    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    filename = Column(String, nullable=True)
    sha256 = Column(String, nullable=True)
    dry_run = Column(Boolean, nullable=False, default=True)
    summary_json = Column(Text, nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ProgramResponse(Base):
    """A meaningful inbound reply and how fast a person handled it."""
    __tablename__ = "program_responses"
    __table_args__ = (
        Index("ix_program_responses_org_state", "organization_id", "handling_status"),
        Index("ix_program_responses_lead", "lead_id"),
    )

    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    lead_id = Column(String, ForeignKey("leads.id"), nullable=False)
    reply_id = Column(String, nullable=True)
    channel = Column(String, nullable=False)                 # sms | email
    location_id = Column(String, nullable=True)
    campaign_family = Column(String, nullable=True)
    source_lead_id = Column(String, nullable=True)
    response_class = Column(String, nullable=False)
    body_excerpt = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    recommended_action = Column(Text, nullable=True)
    received_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    sla_due_at = Column(DateTime, nullable=True)
    opened_at = Column(DateTime, nullable=True)
    opened_by = Column(String, nullable=True)
    responded_at = Column(DateTime, nullable=True)
    active_at = Column(DateTime, nullable=True)
    closed_at = Column(DateTime, nullable=True)
    handling_status = Column(String, nullable=False, default="new")
    cadence_paused = Column(Boolean, nullable=False, default=False)
    sla_alert_count = Column(Integer, nullable=False, default=0)
    last_sla_alert_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ProgramAlert(Base):
    """Every alert the program decided to raise, and whether it was delivered.

    Staff SMS is recorded with delivered=False and a reason when it is not
    switched on or has no recipient - the decision is auditable even when no
    message leaves the building.
    """
    __tablename__ = "program_alerts"
    __table_args__ = (Index("ix_program_alerts_response", "response_id"),)

    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    response_id = Column(String, nullable=True)
    kind = Column(String, nullable=False)                    # hot | active | low | sla_breach
    audience = Column(String, nullable=False)                # primary | management
    channel = Column(String, nullable=False)                 # in_app | sms | email
    recipient = Column(String, nullable=True)
    delivered = Column(Boolean, nullable=False, default=False)
    reason = Column(String, nullable=True)
    message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class CampaignFamily(Base):
    """One campaign definition rendered per location - never copied per location."""
    __tablename__ = "program_campaign_families"
    __table_args__ = (UniqueConstraint("organization_id", "key", name="uq_program_family_key"),)

    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    key = Column(String, nullable=False)
    name = Column(String, nullable=False)
    source_campaign_patterns = Column(Text, nullable=True)   # JSON list of substrings
    asset_category = Column(String, nullable=True)
    language = Column(String, nullable=False, default="en")
    first_touch_email_mode = Column(String, nullable=False, default="hosted")
    followup_email_mode = Column(String, nullable=False, default="attached")
    sms_template = Column(Text, nullable=True)
    email_subject_template = Column(Text, nullable=True)
    email_body_template = Column(Text, nullable=True)
    cadence_template_id = Column(String, nullable=True)
    # Inactive until a person switches it on: no enrollment happens by default.
    is_active = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ProgramAsset(Base):
    """Logos, facility images and approved flyers. Versioned; never edited in place."""
    __tablename__ = "program_assets"
    __table_args__ = (
        Index("ix_program_assets_org_cat", "organization_id", "category"),
        UniqueConstraint("public_token", name="uq_program_asset_token"),
    )

    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    location_id = Column(String, nullable=True)              # NULL = every location
    kind = Column(String, nullable=False)                    # logo | facility_image | flyer
    category = Column(String, nullable=True)                 # flyer category
    campaign_family = Column(String, nullable=True)
    title = Column(String, nullable=False)
    version = Column(Integer, nullable=False, default=1)
    is_active = Column(Boolean, nullable=False, default=False)
    filename = Column(String, nullable=True)
    content_type = Column(String, nullable=False)
    size_bytes = Column(Integer, nullable=False, default=0)
    sha256 = Column(String, nullable=True)
    data = Column(LargeBinary, nullable=False)
    public_token = Column(String, nullable=False)
    dynamic_fields = Column(Text, nullable=True)             # JSON list the creative uses
    uploaded_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ContactVerification(Base):
    """Verification / enrichment results, kept BESIDE the customer's originals."""
    __tablename__ = "contact_verifications"
    __table_args__ = (Index("ix_contact_verifications_source", "organization_id", "source_record_id"),)

    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    source_record_id = Column(String, ForeignKey("program_source_records.id"), nullable=False)
    verified_phone = Column(String, nullable=True)
    phone_line_type = Column(String, nullable=True)          # mobile | landline | voip | unknown
    phone_ownership_confidence = Column(Integer, nullable=True)
    alternate_phone = Column(String, nullable=True)
    verified_email = Column(String, nullable=True)
    email_confidence = Column(Integer, nullable=True)
    verified_address = Column(Text, nullable=True)
    current_address = Column(Text, nullable=True)
    identity_confidence = Column(Integer, nullable=True)
    provider = Column(String, nullable=True)
    verified_at = Column(DateTime, nullable=True)
    # Verification is evidence about a contact, never permission to contact them.
    outreach_eligible = Column(Boolean, nullable=False, default=False)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ProgramEmailTouch(Base):
    """One automated campaign email to one contact (touch 1 = first, 2 = follow-up).

    The unique key IS the idempotency guard: a touch is claimed by inserting
    its row before the provider is called, so two runner instances (or a
    retried pass) can never email the same family the same touch twice.
    Every outcome is kept - sent, failed, blocked (with the refusal) - so
    "why didn't this family get the guide?" always has an answer.
    """
    __tablename__ = "program_email_touches"
    __table_args__ = (
        UniqueConstraint("lead_id", "touch_number", name="uq_program_email_touch"),
        Index("ix_program_email_touches_org", "organization_id", "status"),
    )

    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    lead_id = Column(String, ForeignKey("leads.id"), nullable=False)
    source_record_id = Column(String, nullable=True)
    campaign_family = Column(String, nullable=True)
    location_id = Column(String, nullable=True)
    touch_number = Column(Integer, nullable=False)
    email_mode = Column(String, nullable=True)               # none | hosted | attached
    flyer_asset_id = Column(String, nullable=True)
    status = Column(String, nullable=False, default="claimed")  # claimed | sent | failed | blocked
    reason = Column(Text, nullable=True)
    email_message_id = Column(String, nullable=True)
    attempted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
