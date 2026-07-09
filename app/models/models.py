"""
AdvisorFlow Web - Database Models
SQLAlchemy models for multi-tenant SMS lead outreach platform.

Architecture notes:
- Organization = a company/cemetery group (e.g. "Restland", later "North Star Memorial Group")
- User = an individual advisor account, belongs to one Organization
- Lead = a contact record, scoped to an Organization (NOT globally shared across orgs)
- ContactRegistry = the org-wide dedup table. Phone+LastName is the dedup key.
  When ANY advisor in the org uploads a lead that matches an existing ContactRegistry
  entry, it is flagged as duplicate and skipped/merged rather than re-imported,
  and assigned to whichever advisor already owns it (or left with original owner).
- Message = outbound SMS log (tied to Twilio)
- Reply = inbound SMS log, linked back to the Lead it came from
- BookingLink = stateless token booking system (matches the existing Vercel booking backend)
"""

from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, ForeignKey, Text,
    UniqueConstraint, Index, Enum as SAEnum, Numeric
)
from sqlalchemy.orm import relationship, declarative_base
from sqlalchemy.sql import func
import enum
import uuid

Base = declarative_base()


def gen_uuid():
    return str(uuid.uuid4())


class LeadTier(str, enum.Enum):
    PRE_NEED = "pre_need"
    AT_NEED = "at_need"
    IMMINENT = "imminent"
    CONTRACT_SOLD = "contract_sold"
    EMAIL_ONLY = "email_only"
    ADDR_ONLY = "addr_only"
    PARTIAL = "partial"
    NEW_INQUIRY = "new_inquiry"  # brand-new web/cold lead, no prior relationship with Restland


class ReplyClassification(str, enum.Enum):
    """
    Richer reply categorization than the old binary is_hot flag - matches
    the desktop app's Interested/Callback/DNC/Neutral reply tagging,
    which the web app never had. Populated by
    reply_classification_service.classify_reply().

    NOT_INTERESTED, WRONG_NUMBER, and QUESTION were added per Mike's
    explicit request for a fuller reclassification set - the original
    four (interested/callback/dnc/neutral) didn't distinguish "actively
    doesn't want this" from a wrong-number bounce or an open question
    that doesn't fit hot/cold/dnc.
    """
    INTERESTED = "interested"  # shown to advisors as "Hot Lead" - drives is_hot=True
    CALLBACK = "callback"
    DNC = "dnc"
    NEUTRAL = "neutral"
    NOT_INTERESTED = "not_interested"
    WRONG_NUMBER = "wrong_number"
    QUESTION = "question"


class EngagementTemperature(str, enum.Enum):
    """
    Hot/warm/cold engagement classification - separate from LeadTier
    (which describes the lead's source/type like Pre-Need vs Contract
    Sold). This was a real gap flagged from the desktop app's
    Re-Engagement screen, which filters leads by HOT/WARM/COLD tabs -
    an axis the web version never had. Driven by reply recency and
    sentiment, not by lead source.
    """
    HOT = "hot"        # replied with interest, or imminent/urgent tier
    WARM = "warm"       # active in cadence, no reply yet but recently touched
    COLD = "cold"       # no engagement in a long stretch, or low-priority track
    UNKNOWN = "unknown"  # not yet classified (e.g. brand new import)


class LeadStatus(str, enum.Enum):
    NEW = "new"
    QUEUED = "queued"
    SENT = "sent"
    REPLIED = "replied"
    HOT = "hot"
    BOOKED = "booked"
    DNC = "dnc"
    DEAD = "dead"
    NEEDS_TIER_REVIEW = "needs_tier_review"


class MessageTrack(str, enum.Enum):
    PRE_NEED_LOCK_PRICE = "pre_need_lock_price"
    AT_NEED_SUPPORT = "at_need_support"
    IMMINENT_SUPPORT = "imminent_support"
    UPSELL_EXISTING_CUSTOMER = "upsell_existing"
    EMAIL_ONLY_NURTURE = "email_only_nurture"
    NEEDS_REVIEW = "needs_review"
    NEW_INQUIRY_INTRO = "new_inquiry_intro"  # cold web/lead-gen lead, no prior file-review relationship


class MessageDirection(str, enum.Enum):
    OUTBOUND = "outbound"
    INBOUND = "inbound"


class CadenceStatus(str, enum.Enum):
    """State of a lead's position in the 9-touch re-engagement cadence."""
    ACTIVE = "active"          # still progressing through touches
    PAUSED = "paused"          # manually paused by advisor
    COMPLETED = "completed"    # finished all 9 touches with no resolution
    STOPPED_REPLIED = "stopped_replied"   # exited cadence because lead replied
    STOPPED_BOOKED = "stopped_booked"     # exited cadence because lead booked
    STOPPED_DNC = "stopped_dnc"           # exited cadence due to STOP/compliance


class NotificationType(str, enum.Enum):
    HOT_REPLY = "hot_reply"
    BOOKING_CONFIRMED = "booking_confirmed"
    CADENCE_COMPLETED = "cadence_completed"


# ---------------------------------------------------------------------------
# Organization - top-level tenant. Restland today, North Star Memorial Group
# (and other cemeteries/funeral homes) later. Each org has its own isolated
# lead pool and dedup registry.
# ---------------------------------------------------------------------------
class Organization(Base):
    __tablename__ = "organizations"

    id = Column(String, primary_key=True, default=gen_uuid)
    name = Column(String, nullable=False)  # e.g. "Restland Cemetery & Funeral Home"
    slug = Column(String, unique=True, nullable=False)  # e.g. "restland"
    plan = Column(String, default="trial")  # trial, standard ($299/mo), enterprise
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())

    # White label / branding
    brand_name = Column(String, nullable=True)        # overrides "BookaBoost" in UI
    brand_logo_url = Column(String, nullable=True)    # URL to org logo
    brand_color_primary = Column(String, nullable=True)   # hex e.g. "#2fb6ff"
    brand_color_accent = Column(String, nullable=True)    # hex e.g. "#1ef0a8"
    industry = Column(String, default="funeral")          # funeral, roofing, insurance, etc.

    # Industry-agnostic tier config — JSON array of tier definitions
    # e.g. [{"value": "pre_need", "label": "Pre-Need", "color": "blue"}, ...]
    tier_config = Column(Text, nullable=True)

    users = relationship("User", back_populates="organization")
    leads = relationship("Lead", back_populates="organization")
    contact_registry_entries = relationship("ContactRegistry", back_populates="organization")


# ---------------------------------------------------------------------------
# User - an individual advisor. Each user has their own Twilio config so
# Mike isn't paying for anyone else's SMS usage.
# ---------------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    email = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    must_change_password = Column(Boolean, default=True)
    full_name = Column(String, nullable=False)
    role = Column(String, default="advisor")  # advisor, org_admin, super_admin (Mike)
    is_active = Column(Boolean, default=True)

    # Twilio config - each advisor brings their own account/number
    twilio_account_sid = Column(String, nullable=True)
    twilio_auth_token_encrypted = Column(String, nullable=True)  # encrypted at rest
    twilio_phone_number = Column(String, nullable=True)
    twilio_caller_id_name = Column(String, nullable=True)  # e.g. "Restland Cemetery"

    # Google Calendar OAuth - per-advisor, so bookings land on the right calendar
    google_oauth_refresh_token_encrypted = Column(String, nullable=True)
    google_calendar_id = Column(String, nullable=True)  # usually "primary" or a specific calendar ID
    google_calendar_connected = Column(Boolean, default=False)

    # Microsoft 365 OAuth - EMAIL ONLY, deliberately separate from Google
    # Calendar above. Per Mike's explicit instruction: the calendar stays
    # Google, but real outgoing email should send AS the advisor's real
    # Restland Outlook/Microsoft 365 address, not a generic SendGrid
    # sender. Both connections coexist independently per advisor - one
    # isn't a replacement for the other.
    microsoft_oauth_refresh_token_encrypted = Column(String, nullable=True)
    microsoft_email_address = Column(String, nullable=True)  # the real Outlook address mail gets sent FROM
    microsoft_365_connected = Column(Boolean, default=False)

    # Notification preferences
    notification_email = Column(String, nullable=True)  # where HOT reply alerts go
    notify_on_hot_reply = Column(Boolean, default=True)

    created_at = Column(DateTime, server_default=func.now())
    last_login_at = Column(DateTime, nullable=True)

    organization = relationship("Organization", back_populates="users")
    leads_owned = relationship("Lead", back_populates="assigned_to")
    messages_sent = relationship("Message", back_populates="sender")


# ---------------------------------------------------------------------------
# ContactRegistry - the org-wide dedup ledger.
# Dedup key = normalized_phone + normalized_last_name.
# This is intentionally lightweight: we are NOT hosting full lead data here,
# just enough to detect "has anyone already contacted this person."
# ---------------------------------------------------------------------------
class ContactRegistry(Base):
    __tablename__ = "contact_registry"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    normalized_phone = Column(String, nullable=False)  # E.164, digits only
    normalized_last_name = Column(String, nullable=False)  # lowercased, stripped
    first_seen_lead_id = Column(String, ForeignKey("leads.id"), nullable=True)
    owning_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    organization = relationship("Organization", back_populates="contact_registry_entries")

    __table_args__ = (
        UniqueConstraint("organization_id", "normalized_phone", "normalized_last_name",
                          name="uq_contact_dedup_key"),
        Index("ix_contact_registry_lookup", "organization_id", "normalized_phone", "normalized_last_name"),
    )


# ---------------------------------------------------------------------------
# Lead - an individual contact/prospect record, scoped to one org and
# assigned to one advisor (the advisor who imported it / owns the relationship).
# ---------------------------------------------------------------------------
class Lead(Base):
    __tablename__ = "leads"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    assigned_to_id = Column(String, ForeignKey("users.id"), nullable=True)

    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    phone = Column(String, nullable=True)  # E.164 normalized
    phone_raw = Column(String, nullable=True)  # original as imported
    email = Column(String, nullable=True)

    tier = Column(String, nullable=True)  # pre_need, at_need, imminent, contract_sold, email_only, etc
    engagement_temperature = Column(SAEnum(EngagementTemperature), default=EngagementTemperature.UNKNOWN)
    message_track = Column(String, nullable=True)  # which offer/template track applies
    contact_channel = Column(String, default="sms")  # "sms" or "email_only" - drives queue routing
    status = Column(String, default="new")  # new, sent, replied, hot, booked, dnc, etc
    source_year = Column(Integer, nullable=True)  # e.g. 2012, 2013 (which cohort batch)
    source_file = Column(String, nullable=True)  # original upload filename for traceability

    # CRM history carried over from import - feeds the AI lead-quality analysis
    # Mike requested (last action taken + last contact date + original status
    # reason) so the AI can judge what kind of lead this really is, not just
    # rely on the Lead Type field alone.
    last_action_raw = Column(String, nullable=True)  # e.g. "Called: LM/No Answer"
    last_contact_date = Column(DateTime, nullable=True)
    status_reason_raw = Column(String, nullable=True)  # e.g. "Contract Sold", "Attempting Contact"
    ai_lead_quality_note = Column(Text, nullable=True)  # populated by AI analysis pass, Phase 2

    is_duplicate = Column(Boolean, default=False)  # true if matched existing registry entry
    duplicate_of_lead_id = Column(String, ForeignKey("leads.id"), nullable=True)

    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    organization = relationship("Organization", back_populates="leads")
    assigned_to = relationship("User", back_populates="leads_owned")
    messages = relationship("Message", back_populates="lead")
    replies = relationship("Reply", back_populates="lead")
    cadence_state = relationship("CadenceState", back_populates="lead", uselist=False)
    email_messages = relationship("EmailMessage", back_populates="lead")

    __table_args__ = (
        Index("ix_leads_org_phone", "organization_id", "phone"),
        Index("ix_leads_org_status", "organization_id", "status"),
    )


# ---------------------------------------------------------------------------
# Message - outbound SMS log
# ---------------------------------------------------------------------------
class Message(Base):
    __tablename__ = "messages"

    id = Column(String, primary_key=True, default=gen_uuid)
    lead_id = Column(String, ForeignKey("leads.id"), nullable=False)
    sender_id = Column(String, ForeignKey("users.id"), nullable=False)

    body = Column(Text, nullable=False)
    twilio_sid = Column(String, nullable=True)  # Twilio's message SID for tracking
    twilio_status = Column(String, nullable=True)  # queued, sent, delivered, failed
    booking_link_id = Column(String, ForeignKey("booking_links.id"), nullable=True)

    sent_at = Column(DateTime, server_default=func.now())

    lead = relationship("Lead", back_populates="messages")
    sender = relationship("User", back_populates="messages_sent")


# ---------------------------------------------------------------------------
# Reply - inbound SMS log
# ---------------------------------------------------------------------------
class Reply(Base):
    __tablename__ = "replies"

    id = Column(String, primary_key=True, default=gen_uuid)
    lead_id = Column(String, ForeignKey("leads.id"), nullable=False)

    body = Column(Text, nullable=False)
    twilio_sid = Column(String, nullable=True)
    is_hot = Column(Boolean, default=False)  # flagged by keyword/sentiment detection
    hot_reason = Column(String, nullable=True)  # e.g. "interested keyword: 'yes'"
    classification = Column(SAEnum(ReplyClassification), nullable=True, default=ReplyClassification.NEUTRAL)
    classification_confidence = Column(String, nullable=True)  # "high" | "medium" | "low"
    classification_reasoning = Column(Text, nullable=True)

    received_at = Column(DateTime, server_default=func.now())
    reviewed_at = Column(DateTime, nullable=True)  # when advisor marked as seen

    lead = relationship("Lead", back_populates="replies")


# ---------------------------------------------------------------------------
# BookingLink - stateless token booking system
# (mirrors the existing advisorflow-booking.vercel.app backend)
# ---------------------------------------------------------------------------
class BookingLink(Base):
    __tablename__ = "booking_links"

    id = Column(String, primary_key=True, default=gen_uuid)
    token = Column(String, unique=True, nullable=False, default=gen_uuid)
    lead_id = Column(String, ForeignKey("leads.id"), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)

    status = Column(String, default="pending")  # pending, booked, expired, cancelled
    booked_time = Column(DateTime, nullable=True)
    calendar_event_id = Column(String, nullable=True)  # Google Calendar event ID once synced

    created_at = Column(DateTime, server_default=func.now())
    expires_at = Column(DateTime, nullable=True)


# ---------------------------------------------------------------------------
# LeadOutcome - the "what does this family actually have/not have" tracker
# Mike specifically asked for, recorded after a completed file
# review/appointment. Real business value: knowing a family has no
# marker means the NEXT follow-up message can specifically reference
# markers instead of being generic - this data feeds directly back into
# future message drafting and into the sales-outcome analytics
# (engagement rate -> booking rate -> show rate -> close rate, broken
# down by what was actually sold).
#
# One row per appointment/visit, not one row per lead - a lead may have
# multiple appointments over time (e.g. a follow-up visit after buying
# a plot, to later discuss a marker), and each visit's outcome should be
# preserved as its own historical record rather than overwritten.
# ---------------------------------------------------------------------------
class LeadOutcome(Base):
    __tablename__ = "lead_outcomes"

    id = Column(String, primary_key=True, default=gen_uuid)
    lead_id = Column(String, ForeignKey("leads.id"), nullable=False)
    recorded_by_id = Column(String, ForeignKey("users.id"), nullable=False)
    booking_link_id = Column(String, ForeignKey("booking_links.id"), nullable=True)  # which appointment this outcome is from, if any

    appointment_date = Column(DateTime, nullable=True)

    # The actual checklist Mike described: what does this family have,
    # what don't they have. Each is nullable=True (not just boolean
    # default False) so "unknown/not asked" is distinguishable from
    # "confirmed they don't have one" - a real distinction Mike needs,
    # since "we never asked" shouldn't be treated the same as "we
    # confirmed they have none."
    has_funeral_arrangement = Column(Boolean, nullable=True)
    has_cemetery_property = Column(Boolean, nullable=True)
    has_marker = Column(Boolean, nullable=True)
    has_memorial = Column(Boolean, nullable=True)
    has_open_closed_status = Column(String, nullable=True)  # "open", "closed", or None if not applicable/unknown

    # Sales outcome - did this specific appointment result in a sale,
    # and what was sold. Feeds the Master Control Board revenue
    # reporting (step 6 of the build plan).
    resulted_in_sale = Column(Boolean, default=False)
    sale_items = Column(Text, nullable=True)  # free-text or comma-separated list of what was sold this visit
    sale_amount = Column(String, nullable=True)  # stored as string deliberately - this is a sales note field for the advisor, not a billing/accounting ledger; real currency math belongs in Restland's actual accounting system, not here

    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    lead = relationship("Lead", backref="outcomes")
    recorded_by = relationship("User")


# ---------------------------------------------------------------------------
# SuppressionEntry - the Compliance Center's permanent do-not-contact list,
# separate from (but feeding into) Lead.status == DNC. A number can be
# suppressed here even before any matching Lead exists, and the
# suppression check at send-time should consult this table directly,
# not just rely on individual Lead.status flags getting set correctly.
#
# NOTE ON ORIGIN: the core logic here (phone normalization to +1XXXXXXXXXX,
# source tracking, unique-per-org constraint) was drafted by ChatGPT in a
# separate compliance-center build task, then reviewed and corrected here
# before merging - the original draft used Integer primary keys/foreign
# keys, which do not match this codebase's String/UUID convention used
# everywhere else (Organization.id, Lead.id, User.id are all
# String/gen_uuid). Ported the logic, fixed the ID types.
# ---------------------------------------------------------------------------
class SuppressionSource(str, enum.Enum):
    MANUAL = "manual"
    REPLY_STOP = "reply_stop"


class SuppressionEntry(Base):
    __tablename__ = "suppression_entries"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    phone = Column(String, nullable=False)  # normalized to +1XXXXXXXXXX
    reason = Column(Text, nullable=False)
    source = Column(SAEnum(SuppressionSource), nullable=False, default=SuppressionSource.MANUAL)
    added_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("organization_id", "phone", name="uq_suppression_org_phone"),
    )




# ---------------------------------------------------------------------------
# AuditLogEntry - immutable admin/security activity ledger.
# Records who did what, to what object, inside which organization.
# This is intentionally generic so routers/services can log sensitive
# actions without creating a new table for every event type.
# ---------------------------------------------------------------------------
class AuditLogEntry(Base):
    __tablename__ = "audit_log_entries"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    actor_user_id = Column(String, ForeignKey("users.id"), nullable=False)

    action = Column(String, nullable=False)  # e.g. "lead_reassigned", "password_reset"
    target_type = Column(String, nullable=False)  # e.g. "lead", "user", "suppression_entry"
    target_id = Column(String, nullable=False)
    details = Column(Text, nullable=True)

    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_audit_log_org_created_at", "organization_id", "created_at"),
        Index("ix_audit_log_org_action", "organization_id", "action"),
    )


# ---------------------------------------------------------------------------
# Campaign - saved admin lead filter plus optional message-track assignment.
# Used by the Campaign Builder to preview and apply cohort-level track/cadence
# changes without adding new Lead fields or rewriting the import pipeline.
# Filter criteria is stored as JSON text for portability with the current
# SQLite test/dev setup and Postgres production target.
# ---------------------------------------------------------------------------
class Campaign(Base):
    __tablename__ = "campaigns"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    name = Column(String, nullable=False)
    created_by_id = Column(String, ForeignKey("users.id"), nullable=False)
    filter_criteria = Column(Text, nullable=False)
    message_track = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_campaigns_org_created_at", "organization_id", "created_at"),
    )

# ---------------------------------------------------------------------------
# CadenceState - tracks a lead's position in the 9-touch re-engagement
# cadence over 60 days. One row per lead (1:1). The scheduler job reads
# this table to decide who's due for their next touch today.
#
# Default cadence schedule (days since cadence start, matching Mike's
# original "9-touch cadence over 60 days" spec): Day 1, 3, 7, 10, 14, 21,
# 30, 45, 60. Stored as a list of day-offsets on the org/track level via
# CADENCE_SCHEDULE_DAYS in the re_engagement_service, not hardcoded per-lead,
# so the schedule itself stays adjustable without a migration.
# ---------------------------------------------------------------------------
class CadenceState(Base):
    __tablename__ = "cadence_states"

    id = Column(String, primary_key=True, default=gen_uuid)
    lead_id = Column(String, ForeignKey("leads.id"), nullable=False, unique=True)

    status = Column(String, default="active")  # active, paused, completed, cancelled
    current_touch_number = Column(Integer, default=0)  # 0 = not yet sent touch 1
    cadence_started_at = Column(DateTime, server_default=func.now())
    next_touch_due_at = Column(DateTime, nullable=True)
    last_touch_sent_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    lead = relationship("Lead", back_populates="cadence_state")

    __table_args__ = (
        Index("ix_cadence_due", "status", "next_touch_due_at"),
    )


# ---------------------------------------------------------------------------
# EmailMessage - outbound email log for email-only leads (no phone number).
# Separate from Message (SMS) since it's a different channel/provider.
# ---------------------------------------------------------------------------
class EmailMessage(Base):
    __tablename__ = "email_messages"

    id = Column(String, primary_key=True, default=gen_uuid)
    lead_id = Column(String, ForeignKey("leads.id"), nullable=False)
    sender_id = Column(String, ForeignKey("users.id"), nullable=False)

    subject = Column(String, nullable=False)
    body_html = Column(Text, nullable=False)
    provider_message_id = Column(String, nullable=True)  # e.g. SendGrid/SES message ID
    status = Column(String, default="queued")  # queued, sent, delivered, bounced, failed

    sent_at = Column(DateTime, server_default=func.now())

    lead = relationship("Lead", back_populates="email_messages")


# ---------------------------------------------------------------------------
# Notification - HOT reply alerts and other advisor-facing notifications.
# Delivered via email today (Phase 2); could add SMS-to-advisor or push
# later without changing this table.
# ---------------------------------------------------------------------------
class Notification(Base):
    __tablename__ = "notifications"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    lead_id = Column(String, ForeignKey("leads.id"), nullable=True)

    type = Column(SAEnum(NotificationType), nullable=False)
    message = Column(Text, nullable=False)
    is_sent = Column(Boolean, default=False)
    sent_at = Column(DateTime, nullable=True)
    is_read = Column(Boolean, default=False)

    created_at = Column(DateTime, server_default=func.now())


# ---------------------------------------------------------------------------
# MessageTemplate - org-customizable copy per message track, for both SMS
# and email channels. Falls back to the hardcoded defaults in
# cadence_service.py / email_service.py when no override exists for a
# given org+track+channel combination, so the system works out of the box
# without anyone touching this table, but Mike (or any org_admin) can
# customize the wording per tier without a code deploy.
# ---------------------------------------------------------------------------
class MessageTemplate(Base):
    __tablename__ = "message_templates"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    message_track = Column(String, nullable=False)
    channel = Column(String, nullable=False)  # "sms" or "email"

    body_template = Column(Text, nullable=False)  # SMS: plain text. Email: HTML body.
    email_subject_template = Column(String, nullable=True)  # only used when channel="email"

    updated_by_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("organization_id", "message_track", "channel", name="uq_template_per_track_channel"),
    )


# ── Cadence Templates ──────────────────────────────────────────────────────────
# Org-level reusable cadence templates. Each template has N touches.
# Each touch defines: day offset, time of day, channel (sms/email/both).

class CadenceTemplate(Base):
    __tablename__ = "cadence_templates"

    id = Column(String, primary_key=True)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    industry = Column(String, default="funeral")
    is_default = Column(Boolean, default=False)
    allow_advisor_override = Column(Boolean, default=False)
    created_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = Column(Boolean, default=True)

    touches = relationship("CadenceTemplateTouch", back_populates="template", order_by="CadenceTemplateTouch.touch_number", cascade="all, delete-orphan")


class CadenceTemplateTouch(Base):
    __tablename__ = "cadence_template_touches"

    id = Column(String, primary_key=True)
    template_id = Column(String, ForeignKey("cadence_templates.id"), nullable=False)
    touch_number = Column(Integer, nullable=False)  # 1-based
    day_offset = Column(Integer, nullable=False)    # days after cadence start
    send_hour = Column(Integer, default=10)         # 0-23 hour in advisor's timezone
    channel = Column(String, default="sms")         # sms | email | both
    message_template = Column(String, nullable=True)  # optional pre-filled message
    subject_template = Column(String, nullable=True)  # for email touches
    is_active = Column(Boolean, default=True)

    template = relationship("CadenceTemplate", back_populates="touches")
