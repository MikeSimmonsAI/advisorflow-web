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
    Column, Integer, String, Boolean, DateTime, Date, ForeignKey, Text,
    UniqueConstraint, Index, Enum as SAEnum, LargeBinary
)
from sqlalchemy.orm import relationship, declarative_base
from sqlalchemy.sql import func
from datetime import datetime
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
    REPLY_RECEIVED = "reply_received"


# ---------------------------------------------------------------------------
# Platform — the brand layer above Organizations.
# Each spinoff (BookaBoost, EvoSys Pro, Harmony Hustle) is a Platform.
# Organizations belong to a Platform. Super admins are scoped to a Platform.
# AdvisorFlow (god_admin) sits above all Platforms and sees everything.
# ---------------------------------------------------------------------------
class Platform(Base):
    __tablename__ = "platforms"

    id = Column(String, primary_key=True, default=gen_uuid)
    name = Column(String, nullable=False)         # e.g. "BookaBoost"
    slug = Column(String, unique=True, nullable=False)  # bookaboost | evosyspro | harmonyhustle
    domain = Column(String, nullable=True)        # e.g. "app.bookaboost.live"
    support_email = Column(String, nullable=True) # e.g. "support@bookaboost.live"
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())

    organizations = relationship("Organization", back_populates="platform")


# ---------------------------------------------------------------------------
# PlatformEvent — AdvisorFlow's event bus table.
# Every spinoff emits standardized events here. AdvisorFlow reads them to
# power its Command Center intelligence layer (revenue, leads, AI ops, health).
# Platforms never query each other — they only write their own events.
# AdvisorFlow reads all of them. This is the isolation + visibility model.
# ---------------------------------------------------------------------------
class PlatformEvent(Base):
    __tablename__ = "platform_events"

    id = Column(String, primary_key=True, default=gen_uuid)
    event_type = Column(String, nullable=False)   # e.g. "lead.created", "appointment.booked"
    platform = Column(String, nullable=False)      # bookaboost | evosyspro | harmonyhustle
    org_id = Column(String, ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    data = Column(Text, nullable=True)            # JSON payload (see app/events/schema.py)
    occurred_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        # AdvisorFlow: all events for a platform in time order
        Index("ix_platform_events_platform_time", "platform", "occurred_at"),
        # AdvisorFlow: all events for a specific org
        Index("ix_platform_events_org_time", "org_id", "occurred_at"),
        # AdvisorFlow: filter by event type across all platforms
        Index("ix_platform_events_type_time", "event_type", "occurred_at"),
    )


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

    # Platform this org belongs to (BookaBoost, EvoSys Pro, etc.)
    # Nullable for backward compat — existing orgs get assigned on migration.
    platform_id = Column(String, ForeignKey("platforms.id", ondelete="SET NULL"), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())

    # White label / branding
    brand_name = Column(String, nullable=True)            # overrides "BookaBoost" in UI
    brand_logo_url = Column(String, nullable=True)        # URL to org logo
    brand_color_primary = Column(String, nullable=True)   # hex e.g. "#2fb6ff"
    brand_color_accent = Column(String, nullable=True)    # hex e.g. "#1ef0a8"
    favicon_url = Column(String, nullable=True)           # URL to org favicon
    tagline = Column(String, nullable=True)               # short tagline shown in UI
    support_email = Column(String, nullable=True)         # support contact shown in app
    email_sender_name = Column(String, nullable=True)     # "From" name on outbound emails
    industry = Column(String, default="funeral")          # funeral, roofing, insurance, etc.

    # Org contact details — shown on public booking pages instead of hardcoded values
    org_address = Column(String, nullable=True)
    org_phone = Column(String, nullable=True)

    # Social media links — shown in post-appointment survey
    facebook_url = Column(String, nullable=True)
    google_review_url = Column(String, nullable=True)
    instagram_url = Column(String, nullable=True)
    linkedin_url = Column(String, nullable=True)

    # Industry-agnostic tier config — JSON array of tier definitions
    # e.g. [{"value": "pre_need", "label": "Pre-Need", "color": "blue"}, ...]
    tier_config = Column(Text, nullable=True)

    # Per-org appointment type options — JSON array of strings shown in the
    # "Appt type" dropdown on the lead detail page. When null the frontend
    # falls back to the built-in default list so existing orgs are unaffected.
    appointment_types = Column(Text, nullable=True)

    # Per-org feature flags (super admin only). JSON array of feature keys.
    # null = all features enabled (backward-compatible default).
    # [] = no optional features. ["campaigns", "reports", ...] = explicit allow-list.
    enabled_features = Column(Text, nullable=True)

    # What this org calls their non-admin users (e.g. "Agent", "Rep", "Advisor", "FSA").
    # Null = use industry default. Overrides the hardcoded "Advisor" label throughout the UI.
    member_label = Column(String(100), nullable=True)   # singular e.g. "Agent"
    members_label = Column(String(100), nullable=True)  # plural   e.g. "Agents"

    # Custom CRM pipeline stages — JSON array of {key, label, color}
    # Null = use industry default stages from INDUSTRY_STAGES map in crm_native_router.
    crm_stages = Column(Text, nullable=True)

    # Custom field schema for CRM contacts — JSON array of {key, label, type, options?}
    # type: "text" | "number" | "dropdown" | "date"
    crm_custom_fields = Column(Text, nullable=True)

    # Org-level email sender — overrides the global RESEND_API_KEY / EMAIL_FROM_ADDRESS
    # env vars so each brand sends from its own domain (e.g. support@bookaboost.live
    # for BookaBoost, support@evosyspro.live for EvoSys Pro).
    # resend_api_key is stored plaintext (it's an outbound API key, not a user secret).
    from_email = Column(String, nullable=True)       # e.g. "support@bookaboost.live"
    resend_api_key = Column(String, nullable=True)   # org-specific Resend API key

    # Org-level shared Twilio credentials — used as fallback when an advisor
    # has no personal Twilio number configured.  Supports both toll-free and
    # 10DLC numbers; twilio_number_type distinguishes them for reporting.
    # All advisors in the org send FROM this shared number when no personal
    # number is set, with their name in the message body.
    org_twilio_account_sid        = Column(String, nullable=True)
    org_twilio_auth_token_encrypted = Column(String, nullable=True)  # encrypted at rest
    org_twilio_phone_number       = Column(String, nullable=True)   # e.g. "+18449172171"
    org_twilio_caller_id_name     = Column(String, nullable=True)   # e.g. "EvoSys Pro"
    # "toll_free" | "10dlc" | "short_code" — informational, used in dashboards
    org_twilio_number_type        = Column(String, nullable=True, default="toll_free")

    # Stripe billing — populated by billing_router.py on checkout/webhook
    stripe_customer_id      = Column(String, nullable=True)
    stripe_subscription_id  = Column(String, nullable=True)
    stripe_plan_interval    = Column(String, nullable=True)  # 'month' | 'year'
    billing_status          = Column(String, nullable=True)  # 'active' | 'past_due' | 'canceled' | 'trialing'

    platform = relationship("Platform", back_populates="organizations")
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
    # Roles (ascending privilege):
    #   advisor      — sees only their own leads
    #   org_admin    — sees all leads/users in their org
    #   super_admin  — sees all orgs in their platform (scoped by platform_id)
    #   god_admin    — sees everything across all platforms (AdvisorFlow owner)
    role = Column(String, default="advisor")

    # For super_admin: which platform they administer. Null = not yet assigned.
    # god_admin ignores this — they bypass all scoping.
    platform_id = Column(String, ForeignKey("platforms.id", ondelete="SET NULL"), nullable=True)
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

    # Personal booking page — advisor's shareable booking URL (Calendly, Google, or BookaBoost)
    booking_page_url = Column(String, nullable=True)

    # Social media links — shown in post-appointment survey and outreach
    facebook_url = Column(String, nullable=True)
    google_review_url = Column(String, nullable=True)
    instagram_url = Column(String, nullable=True)
    linkedin_url = Column(String, nullable=True)

    # Notification preferences
    notification_email = Column(String, nullable=True)  # where HOT reply alerts go
    notify_on_hot_reply = Column(Boolean, default=True)

    # Auto-send phase — controls inbound-reply AI auto-draft behavior
    # "off"       (default) — feature disabled, normal inbox only
    # "candidate" — eligible inbound replies go to /auto-send review queue
    # "auto"      — eligible simple replies sent immediately, no human review
    auto_send_phase = Column(String, default="off")

    # Profile photo — stored as base64 data URL (e.g. "data:image/jpeg;base64,...")
    # so it's self-contained with no external storage dependency.
    profile_photo_url = Column(Text, nullable=True)

    # Personal contact info (NOT Twilio) — used for directory/profile display
    phone = Column(String, nullable=True)       # e.g. "+14155551234"
    job_title = Column(String, nullable=True)   # e.g. "Senior Advisor"

    # Booking / scheduling settings — advisor configures their own calendar availability
    appt_duration_minutes = Column(Integer, default=30, nullable=True)          # length of each appointment slot
    buffer_minutes = Column(Integer, default=0, nullable=True)                  # gap between back-to-back bookings
    max_bookings_per_day = Column(Integer, default=8, nullable=True)            # cap on same-day bookings
    available_start_time = Column(String, default="09:00", nullable=True)       # daily open time, HH:MM 24h
    available_end_time = Column(String, default="17:00", nullable=True)         # daily close time, HH:MM 24h
    available_days = Column(String, default="0,1,2,3,4", nullable=True)         # comma-sep weekday indices 0=Mon
    booking_timezone = Column(String, default="America/Chicago", nullable=True) # IANA tz for the advisor
    booking_confirmation_message = Column(Text, nullable=True)                  # custom message shown after booking

    # Brute-force / credential-stuffing protection.
    # Incremented on each failed password attempt; reset to 0 on success.
    # When failed_login_attempts reaches LOGIN_LOCKOUT_THRESHOLD (10),
    # lockout_until is set to now + 15 minutes and login is rejected until
    # the timestamp passes. is_active is deliberately NOT modified so an
    # attacker cannot permanently lock out an account with a handful of guesses.
    failed_login_attempts = Column(Integer, default=0, nullable=False)
    lockout_until = Column(DateTime, nullable=True)

    # Single-session enforcement.
    # On every login a new UUID is generated and stored here AND embedded as
    # the JWT's `jti` claim.  get_current_user rejects any token whose jti
    # doesn't match this column, so logging in elsewhere invalidates all
    # previous sessions.  Deactivating or force-logging-out a user clears
    # this value, which makes every outstanding token immediately invalid.
    session_token = Column(String, nullable=True)

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

    # Physical address — collected at import or via lead edit
    street_address = Column(String, nullable=True)
    city = Column(String, nullable=True)
    state = Column(String, nullable=True)
    zip_code = Column(String, nullable=True)

    # Relationship context — the PRIMARY AI guardrail for every message this lead receives.
    # "cold_lead"          — never heard of us, no prior relationship
    # "warm_lead"          — showed interest, responded before, or is a referral
    # "previous_prospect"  — was in the pipeline before but didn't close
    # "existing_customer"  — currently active customer (upsell / cross-sell context)
    # "past_customer"      — was a customer, relationship lapsed
    # "re_engagement"      — was contacted before (by us), went cold, trying again
    relationship_type = Column(String, default="cold_lead", nullable=True)

    # Extra import columns — any CSV columns not in the standard field map are
    # stored here as a JSON object so AI and UI can use them without schema changes.
    custom_fields = Column(Text, nullable=True)

    # Import batch metadata
    import_list_name = Column(String, nullable=True)  # user-supplied label, e.g. "2024 Purchased List"
    imported_by_name = Column(String, nullable=True)  # full name of the user who ran the import
    source_category = Column(String, nullable=True)   # purchased, organic, referral, database, etc.

    # Manual flag — set by any advisor when auto-detection misses a bad contact
    # Values: null (clean), "bad_email" (hide from email/campaign but allow SMS),
    #         "remove_all" (hide from all outreach lists everywhere)
    manual_flag = Column(String, nullable=True)
    manual_flag_reason = Column(String, nullable=True)  # optional note from the advisor

    # Post-appointment case management — "open" until the appointment outcome is resolved
    # Values: open, pending_outcome, sold, lost, follow_up, closed
    case_status = Column(String, default="open", nullable=True)

    # Denormalized timestamp of most recent outbound message (SMS or email).
    # Updated by sms_service.send_sms() and email_router send endpoints.
    # Used for "sent today" badge on Leads list.
    last_messaged_at = Column(DateTime, nullable=True)

    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    organization = relationship("Organization", back_populates="leads")
    assigned_to = relationship("User", back_populates="leads_owned")
    messages = relationship("Message", back_populates="lead", passive_deletes=True)
    replies = relationship("Reply", back_populates="lead", passive_deletes=True)
    cadence_state = relationship("CadenceState", back_populates="lead", uselist=False, passive_deletes=True)
    email_messages = relationship("EmailMessage", back_populates="lead", passive_deletes=True)
    outcomes = relationship("LeadOutcome", back_populates="lead", passive_deletes=True)

    __table_args__ = (
        Index("ix_leads_org_phone", "organization_id", "phone"),
        Index("ix_leads_org_status", "organization_id", "status"),
        # Advisor's own lead view — most common non-admin query
        Index("ix_leads_org_advisor", "organization_id", "assigned_to_id"),
        # Import batch management (list + delete by source_file)
        Index("ix_leads_org_source_file", "organization_id", "source_file"),
        # Cadence job queries for active / due leads
        Index("ix_leads_org_channel_status", "organization_id", "contact_channel", "status"),
    )


# ---------------------------------------------------------------------------
# Message - outbound SMS log
# ---------------------------------------------------------------------------
class Message(Base):
    __tablename__ = "messages"

    id = Column(String, primary_key=True, default=gen_uuid)
    lead_id = Column(String, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
    sender_id = Column(String, ForeignKey("users.id"), nullable=False)

    body = Column(Text, nullable=False)
    twilio_sid = Column(String, nullable=True)  # Twilio's message SID for tracking
    twilio_status = Column(String, nullable=True)  # queued, sent, delivered, failed
    booking_link_id = Column(String, ForeignKey("booking_links.id", ondelete="SET NULL"), nullable=True)

    # Twilio delivery receipt — updated by the /sms/status-callback webhook
    # Values: pending (default), sent, delivered, failed, undelivered
    delivery_status = Column(String, default="pending", nullable=True)
    delivery_status_at = Column(DateTime, nullable=True)  # when Twilio last updated this

    sent_at = Column(DateTime, server_default=func.now())

    lead = relationship("Lead", back_populates="messages")
    sender = relationship("User", back_populates="messages_sent")


# ---------------------------------------------------------------------------
# Reply - inbound SMS log
# ---------------------------------------------------------------------------
class Reply(Base):
    __tablename__ = "replies"

    id = Column(String, primary_key=True, default=gen_uuid)
    lead_id = Column(String, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)

    body = Column(Text, nullable=False)
    source = Column(String, default="sms")  # sms | email
    twilio_sid = Column(String, nullable=True)
    is_hot = Column(Boolean, default=False)
    hot_reason = Column(String, nullable=True)
    classification = Column(SAEnum(ReplyClassification), nullable=True, default=ReplyClassification.NEUTRAL)
    classification_confidence = Column(String, nullable=True)
    classification_reasoning = Column(Text, nullable=True)

    received_at = Column(DateTime, server_default=func.now())
    reviewed_at = Column(DateTime, nullable=True)

    lead = relationship("Lead", back_populates="replies")


# ---------------------------------------------------------------------------
# BookingLink - stateless token booking system
# (mirrors the existing advisorflow-booking.vercel.app backend)
# ---------------------------------------------------------------------------
class BookingLink(Base):
    __tablename__ = "booking_links"

    id = Column(String, primary_key=True, default=gen_uuid)
    token = Column(String, unique=True, nullable=False, default=gen_uuid)
    lead_id = Column(String, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)

    status = Column(String, default="pending")  # pending, booked, confirmed, expired, cancelled
    booked_time = Column(DateTime, nullable=True)
    calendar_event_id = Column(String, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    expires_at = Column(DateTime, nullable=True)
    review_request_sent_at = Column(DateTime, nullable=True)  # set by review_request_cron

    # Appointment reminder tracking — set by appointment_reminder_cron.py
    confirmation_sent   = Column(Boolean, default=False)  # immediate lead confirmation on booking
    reminder_24hr_sent  = Column(Boolean, default=False)  # 24-hour reminder to lead
    reminder_1hr_sent   = Column(Boolean, default=False)  # 1-hour reminder to lead


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
    lead_id = Column(String, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
    recorded_by_id = Column(String, ForeignKey("users.id"), nullable=False)
    booking_link_id = Column(String, ForeignKey("booking_links.id", ondelete="SET NULL"), nullable=True)  # which appointment this outcome is from, if any

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

    lead = relationship("Lead", back_populates="outcomes")
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
    ADVISOR_FLAGGED = "advisor_flagged"


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
    lead_id = Column(String, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, unique=True)

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
    lead_id = Column(String, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
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

    id = Column(String, primary_key=True, default=gen_uuid)
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

    id = Column(String, primary_key=True, default=gen_uuid)
    template_id = Column(String, ForeignKey("cadence_templates.id"), nullable=False)
    touch_number = Column(Integer, nullable=False)  # 1-based
    day_offset = Column(Integer, nullable=False)    # days after cadence start
    send_hour = Column(Integer, default=10)         # 0-23 hour in advisor's timezone
    channel = Column(String, default="sms")         # sms | email | both
    message_template = Column(String, nullable=True)  # optional pre-filled message
    subject_template = Column(String, nullable=True)  # for email touches
    is_active = Column(Boolean, default=True)

    template = relationship("CadenceTemplate", back_populates="touches")


# ── Pipeline Conversations ─────────────────────────────────────────────────────
# Tracks the full AI conversation pipeline for each lead.
# Stage progression: outreach_sent → replied → ai_responding → booking_sent
#                  → booked → confirmed → kept → sale
# Replaces the old auto-send queue with a proper pipeline model.

class PipelineConversation(Base):
    __tablename__ = "pipeline_conversations"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    lead_id = Column(String, ForeignKey("leads.id"), nullable=False)
    advisor_id = Column(String, ForeignKey("users.id"), nullable=False)

    # Current stage in the pipeline
    stage = Column(String, default="outreach_sent")
    # outreach_sent | replied | ai_responding | booking_sent |
    # booked | confirmed | kept | sale | stopped | dnc

    # Context set when pipeline was launched
    lead_type = Column(String, nullable=True)    # file_check, code_lead, new_inquiry, etc.
    channel = Column(String, default="sms")      # sms | email | both
    tone = Column(String, default="warm")        # cold | warm | hot | urgent
    ai_direction = Column(String, nullable=True) # custom instruction for AI

    # AI auto-conversation state
    auto_respond = Column(Boolean, default=True)       # AI responds automatically
    confidence_threshold = Column(Integer, default=85) # below this % → flag for review
    response_delay_seconds = Column(Integer, default=180)  # 2-5 min random delay

    # Flagged for human review
    flagged = Column(Boolean, default=False)
    flag_reason = Column(String, nullable=True)
    flagged_reply_body = Column(Text, nullable=True)
    flagged_suggested_response = Column(Text, nullable=True)
    flagged_at = Column(DateTime, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)

    # Cadence schedule — Day 1(x2), 2, 4, 6, 8, 10, 12, 14
    touch_number = Column(Integer, default=0)          # which touch we're on (0=not started)
    next_send_at = Column(DateTime, nullable=True)     # when to send next touch
    paused = Column(Boolean, default=False)            # advisor paused AI
    paused_reason = Column(String, nullable=True)      # why paused
    started_at = Column(DateTime, nullable=True)       # when conversation started
    completed_at = Column(DateTime, nullable=True)     # when sequence finished

    # Engagement tracking
    messages_sent = Column(Integer, default=0)
    replies_received = Column(Integer, default=0)
    ai_responses_sent = Column(Integer, default=0)
    ai_responses_flagged = Column(Integer, default=0)
    last_outbound_at = Column(DateTime, nullable=True)
    last_inbound_at = Column(DateTime, nullable=True)

    # Outcome tracking
    booking_link_sent_at = Column(DateTime, nullable=True)
    booked_at = Column(DateTime, nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    appointment_kept_at = Column(DateTime, nullable=True)
    sale_recorded_at = Column(DateTime, nullable=True)

    # Notifications sent
    booking_notification_sent = Column(Boolean, default=False)
    confirmation_notification_sent = Column(Boolean, default=False)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ── Advisor Availability Blocks ───────────────────────────────────────────────
# Stores date/time blocks when an advisor is unavailable for bookings.
# Checked by /calendar/available-slots before returning slots to booking app.

class BlockType(str, enum.Enum):
    DATE_RANGE = "date_range"   # vacation / full days off
    SLOT       = "slot"         # specific day+time blocked
    RECURRING  = "recurring"    # e.g. every Friday after 3pm


class AdvisorAvailabilityBlock(Base):
    __tablename__ = "advisor_availability_blocks"

    id              = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    advisor_id      = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)
    block_type      = Column(SAEnum(BlockType), nullable=False)

    # Date range block (vacation)
    start_date      = Column(Date, nullable=True)
    end_date        = Column(Date, nullable=True)

    # Specific slot block
    block_date      = Column(Date, nullable=True)
    block_time      = Column(String, nullable=True)   # "09:00", "09:30", etc.

    # Recurring block
    recur_day_of_week = Column(Integer, nullable=True)    # 0=Mon … 6=Sun
    recur_after_time  = Column(String, nullable=True)     # block slots >= this time
    recur_before_time = Column(String, nullable=True)     # block slots <= this time

    reason          = Column(String, nullable=True)
    cancel_existing = Column(Boolean, default=False)

    created_at      = Column(DateTime, default=datetime.utcnow)
    created_by_id   = Column(String, ForeignKey("users.id"), nullable=True)


# ── Voice Calls ───────────────────────────────────────────────────────────────
# Records every AI voice call — outbound, voicemail, recording, transcript.

class VoiceCall(Base):
    __tablename__ = "voice_calls"

    id              = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    lead_id         = Column(String, ForeignKey("leads.id"), nullable=False, index=True)
    advisor_id      = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)

    # Call details
    call_sid        = Column(String, nullable=True)          # Twilio call SID
    to_phone        = Column(String, nullable=False)
    from_phone      = Column(String, nullable=True)
    call_number     = Column(Integer, default=1)             # 1, 2, or 3 (max 3 attempts)
    status          = Column(String, default="initiating")   # initiating | ringing | in_progress | completed | failed
    twilio_status   = Column(String, nullable=True)          # Twilio's raw status

    # Outcome
    outcome         = Column(String, nullable=True)          # booked | no_answer | not_interested | completed | escalated | failed | booking_requested
    escalation_reason = Column(String, nullable=True)

    # Recording
    recording_url   = Column(String, nullable=True)
    recording_sid   = Column(String, nullable=True)
    duration_seconds = Column(Integer, nullable=True)

    # Transcript
    transcript      = Column(Text, nullable=True)

    # Booking
    booking_url_sent = Column(Boolean, default=False)

    # Voicemail
    voicemail_left  = Column(Boolean, default=False)
    voicemail_transcript = Column(Text, nullable=True)

    # Error
    error_message   = Column(String, nullable=True)

    # Timing
    started_at      = Column(DateTime, nullable=True)
    ended_at        = Column(DateTime, nullable=True)
    created_at      = Column(DateTime, default=datetime.utcnow)


# ── Voice Call Campaign ───────────────────────────────────────────────────────
# Bulk outbound call campaigns — fire AI calls to multiple leads at once.

class VoiceCallCampaign(Base):
    __tablename__ = "voice_call_campaigns"

    id              = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)
    advisor_id      = Column(String, ForeignKey("users.id"), nullable=False, index=True)

    # Campaign details
    name            = Column(String, nullable=False)
    description     = Column(String, nullable=True)
    status          = Column(String, default="pending")  # pending | running | paused | completed | cancelled

    # Lead targeting
    lead_ids        = Column(Text, nullable=True)        # JSON array of lead IDs
    tier_filter     = Column(String, nullable=True)      # e.g. "pre_need,at_need"
    total_leads     = Column(Integer, default=0)

    # Progress
    calls_initiated = Column(Integer, default=0)
    calls_completed = Column(Integer, default=0)
    calls_answered  = Column(Integer, default=0)
    calls_voicemail = Column(Integer, default=0)
    calls_failed    = Column(Integer, default=0)
    bookings_detected = Column(Integer, default=0)

    # Config
    concurrent_calls = Column(Integer, default=5)        # max simultaneous calls
    call_window_start = Column(String, default="09:00")  # CST
    call_window_end   = Column(String, default="17:00")  # CST

    # Scheduling
    scheduled_at    = Column(DateTime, nullable=True)    # null = run immediately
    started_at      = Column(DateTime, nullable=True)
    completed_at    = Column(DateTime, nullable=True)
    created_at      = Column(DateTime, default=datetime.utcnow)


# ── BookingFollowup — tracks post-appointment thank you + survey sends ────────
# One row per booking followup attempt. Prevents duplicate sends on every
# cron tick after the appointment time passes.

class BookingFollowup(Base):
    __tablename__ = "booking_followups"

    id              = Column(String, primary_key=True, default=gen_uuid)
    booking_link_id = Column(String, ForeignKey("booking_links.id", ondelete="CASCADE"), nullable=False)
    lead_id         = Column(String, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True)
    advisor_id      = Column(String, ForeignKey("users.id"), nullable=False)

    # Which channel was used
    channel         = Column(String, default="sms")   # sms | email
    sent_at         = Column(DateTime, default=datetime.utcnow)

    # Survey token so we can look up the booking from the survey link
    survey_token    = Column(String, unique=True, default=gen_uuid)

    # Status
    thank_you_sent  = Column(Boolean, default=False)
    survey_link_sent = Column(Boolean, default=False)
    error           = Column(String, nullable=True)

    created_at      = Column(DateTime, default=datetime.utcnow)


# ── SurveyResponse — lead's answers to the post-appointment survey ────────────

class SurveyResponse(Base):
    __tablename__ = "survey_responses"

    id              = Column(String, primary_key=True, default=gen_uuid)
    booking_followup_id = Column(String, ForeignKey("booking_followups.id", ondelete="CASCADE"), nullable=False)
    lead_id         = Column(String, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True)
    advisor_id      = Column(String, ForeignKey("users.id"), nullable=False)

    # Core satisfaction rating (1-5 stars)
    rating          = Column(Integer, nullable=True)
    # Open text feedback
    feedback        = Column(Text, nullable=True)
    # Lead's social handles (optional, soft ask)
    facebook_handle = Column(String, nullable=True)
    instagram_handle = Column(String, nullable=True)

    submitted_at    = Column(DateTime, default=datetime.utcnow)
    created_at      = Column(DateTime, default=datetime.utcnow)


# ── TierDefinition — per-org tier/track configuration ─────────────────────────
# Replaces the old hardcoded LeadTier + MessageTrack Python enums with
# database-driven rows so each org can define its own tier names, track
# keys, and AI tone context without requiring a code change.
#
# create_all() creates this table fresh on any environment that doesn't
# have it yet; existing orgs get their defaults seeded via /tier-definitions/seed-defaults.

class TierDefinition(Base):
    __tablename__ = "tier_definitions"

    id                   = Column(String, primary_key=True, default=gen_uuid)
    organization_id      = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)

    # Lowercase key used in code / DB matching (e.g. "pre_need", "at_need")
    tier_key             = Column(String, nullable=False)
    # Human-readable label shown in the UI (e.g. "Pre-Need", "At-Need")
    tier_label           = Column(String, nullable=False)

    # Which cadence track drives messaging for this tier
    track_key            = Column(String, nullable=False)   # e.g. "pre_need_lock_price"
    track_label          = Column(String, nullable=False)   # e.g. "Pre-Need Lock Price"

    # Optional prompt hint injected into AI message generation for this tier
    ai_tone_context      = Column(Text, nullable=True)

    # Whether advisors can manually assign this tier to a lead in the UI
    is_manual_selectable = Column(Boolean, default=True)

    # Soft-delete: inactive tiers are hidden from pickers but preserved historically
    is_active            = Column(Boolean, default=True)

    # Display order in the UI — lower numbers appear first
    sort_order           = Column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint("organization_id", "tier_key", name="uq_org_tier_key"),
    )


# ---------------------------------------------------------------------------
# RevokedToken - JWT deny-list for token revocation (logout / compromise).
# When a token is revoked (logout, forced sign-out of a fired advisor, etc.)
# its jti (JWT ID) is written here. decode_access_token checks this table
# and rejects any token whose jti appears in the list.
# Rows whose expires_at is in the past can be pruned safely — an already-
# expired token is harmless whether its jti is here or not.
# ---------------------------------------------------------------------------
class RevokedToken(Base):
    __tablename__ = "revoked_tokens"

    jti = Column(String, primary_key=True)          # UUID from the token's jti claim
    expires_at = Column(DateTime, nullable=False)   # natural expiry of the original token
    revoked_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_revoked_tokens_expires_at", "expires_at"),
    )


# ── Client Proposal Portal ─────────────────────────────────────────────────────
#
# Proposals are rich, multi-block sales documents created by advisors/admins
# and shared with prospects/clients via a secure magic-link portal.
# Clients never touch the internal app — they land on /portal/view/:token
# and see a completely different, premium full-screen experience.
#
# Architecture:
#   Proposal        — the document (metadata, status, branding overrides)
#   ProposalBlock   — ordered content blocks (text / image / pdf / video / divider)
#   ProposalToken   — one-time or limited magic-link access tokens per recipient
#   ProposalView    — analytics: every open/scroll/download event
#
# White-label note: branding_override JSON is stored now but UI controls are
# not exposed yet. Future: per-proposal logo, accent color, company name override.

class Proposal(Base):
    __tablename__ = "proposals"

    id              = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)
    created_by_id   = Column(String, ForeignKey("users.id"), nullable=False)

    title           = Column(String, nullable=False)
    # Short subtitle shown on portal cover — e.g. "Prepared for Acme Corp"
    subtitle        = Column(String, nullable=True)
    # Client-facing name shown at top of portal ("Hi, Sarah 👋")
    client_name     = Column(String, nullable=True)
    client_email    = Column(String, nullable=True)
    client_company  = Column(String, nullable=True)

    # draft | published | archived
    status          = Column(String, default="draft", nullable=False)

    # Future white-label: {"logo_url": "...", "accent": "#087cff", "company_name": "Acme"}
    branding_override = Column(Text, nullable=True)  # JSON string

    # Optional expiry for the whole proposal (not just tokens)
    expires_at      = Column(DateTime, nullable=True)

    # Soft-delete
    deleted_at      = Column(DateTime, nullable=True)

    created_at      = Column(DateTime, default=datetime.utcnow)
    updated_at      = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    blocks          = relationship("ProposalBlock", back_populates="proposal",
                                   order_by="ProposalBlock.position",
                                   cascade="all, delete-orphan")
    tokens          = relationship("ProposalToken", back_populates="proposal",
                                   cascade="all, delete-orphan")
    views           = relationship("ProposalView", back_populates="proposal",
                                   cascade="all, delete-orphan")


class ProposalBlock(Base):
    """
    A single content block inside a proposal.
    Blocks are ordered by `position` (0-indexed).

    block_type values:
        text        — rich text body (markdown stored, rendered client-side)
        image       — uploaded image; file_url points to stored file
        pdf         — uploaded PDF; file_url points to stored file
        video       — embed URL (YouTube / Vimeo / Loom)
        divider     — visual section break, no content
        cta         — call-to-action button with label + href
        website_url — live iframe embed of an external URL
    """
    __tablename__ = "proposal_blocks"

    id          = Column(String, primary_key=True, default=gen_uuid)
    proposal_id = Column(String, ForeignKey("proposals.id", ondelete="CASCADE"),
                         nullable=False, index=True)

    block_type  = Column(String, nullable=False)  # text|image|pdf|video|divider|cta
    position    = Column(Integer, nullable=False, default=0)

    # Primary content — meaning depends on block_type:
    #   text    → markdown string
    #   image   → caption
    #   pdf     → display title
    #   video   → optional title
    #   cta     → button label
    content     = Column(Text, nullable=True)

    # Secondary content — meaning depends on block_type:
    #   image / pdf → relative upload path or absolute URL
    #   video       → embed URL
    #   cta         → destination href
    file_url    = Column(String, nullable=True)

    # Original filename (for display in download UI)
    file_name   = Column(String, nullable=True)
    # Bytes — used to show file size in download badge
    file_size   = Column(Integer, nullable=True)

    created_at  = Column(DateTime, default=datetime.utcnow)

    proposal    = relationship("Proposal", back_populates="blocks")


class ProposalToken(Base):
    """
    Magic-link access token.  Each sent invite creates one row.
    Resolving the token starts a 48-hour portal session (stored client-side).
    Admins can revoke a token (redeemed_at = sentinel) or let it expire.
    """
    __tablename__ = "proposal_tokens"

    id              = Column(String, primary_key=True, default=gen_uuid)
    proposal_id     = Column(String, ForeignKey("proposals.id", ondelete="CASCADE"),
                             nullable=False, index=True)

    # The opaque token string in the magic link URL
    token           = Column(String, unique=True, nullable=False, default=gen_uuid)

    # Who this link was sent to (display only — no auth account required)
    recipient_email = Column(String, nullable=True)
    recipient_name  = Column(String, nullable=True)

    # Null = never expires (admin access), otherwise 48h from creation
    expires_at      = Column(DateTime, nullable=True)

    # Set on first redemption — subsequent hits still allowed until expiry
    first_redeemed_at = Column(DateTime, nullable=True)

    # Revocation: set to a past datetime to hard-block the token
    revoked_at      = Column(DateTime, nullable=True)

    # Content protection: disables right-click, drag, download buttons,
    # text selection, and keyboard save/print shortcuts in the portal
    protect_content = Column(Boolean, default=False, nullable=False)

    created_at      = Column(DateTime, default=datetime.utcnow)

    proposal        = relationship("Proposal", back_populates="tokens")
    views           = relationship("ProposalView", back_populates="token",
                                   cascade="all, delete-orphan")


class ProposalView(Base):
    """
    One row per portal session. Tracks open, scroll depth, and download.
    duration_seconds is computed when the client sends a 'close' event.
    """
    __tablename__ = "proposal_views"

    id              = Column(String, primary_key=True, default=gen_uuid)
    proposal_id     = Column(String, ForeignKey("proposals.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    token_id        = Column(String, ForeignKey("proposal_tokens.id", ondelete="SET NULL"),
                             nullable=True, index=True)

    opened_at       = Column(DateTime, default=datetime.utcnow)
    closed_at       = Column(DateTime, nullable=True)
    duration_seconds = Column(Integer, nullable=True)

    # 0–100 — highest scroll percentage reached during session
    max_scroll_pct  = Column(Integer, default=0)

    # True once the client hits the download button for any block
    downloaded      = Column(Boolean, default=False)

    # Coarse location context (IP → city only, no street, never stored raw IP)
    viewer_city     = Column(String, nullable=True)

    proposal        = relationship("Proposal", back_populates="views")
    token           = relationship("ProposalToken", back_populates="views")


class ProposalFile(Base):
    """
    Stores uploaded files (images, PDFs) for proposal blocks.
    Files are kept in the database as binary data — fine for small-scale
    agency use (typically <5 MB per file, dozens of files total).
    Served via GET /proposals/files/{file_id} with no auth required so
    client portal links work without a session.
    """
    __tablename__ = "proposal_files"

    id              = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    proposal_id     = Column(String, ForeignKey("proposals.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    filename        = Column(String, nullable=False)
    content_type    = Column(String, nullable=False)   # e.g. image/png, application/pdf
    file_size       = Column(Integer, nullable=False)
    file_data       = Column(LargeBinary, nullable=False)
    created_at      = Column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# CRMContact — Native CRM master record.
# Lives alongside leads but is a richer, long-lived relationship record.
# A contact can optionally link to a Lead (lead_id) but is independent of it.
# Stages are org-specific; defaults are funeral-industry appropriate but can
# be overridden at the org level (future: org_crm_stages JSON column).
# ---------------------------------------------------------------------------
class CRMContact(Base):
    __tablename__ = "crm_contacts"

    id               = Column(String, primary_key=True, default=gen_uuid)
    organization_id  = Column(String, ForeignKey("organizations.id"), nullable=False)

    # Identity
    first_name       = Column(String, nullable=True)
    last_name        = Column(String, nullable=True)
    phone            = Column(String, nullable=True)
    email            = Column(String, nullable=True)

    # Address
    address_street   = Column(String, nullable=True)
    address_city     = Column(String, nullable=True)
    address_state    = Column(String, nullable=True)
    address_zip      = Column(String, nullable=True)

    # CRM pipeline stage
    stage            = Column(String, default="inquiry")
    # Default stages (funeral industry):
    #   inquiry | pre_need | at_need | arrangements | services_complete | aftercare | closed

    # Notes / history
    notes            = Column(Text, nullable=True)

    # Tags — comma-separated or JSON list stored as string for simplicity
    tags             = Column(String, nullable=True)

    # Link back to a lead record (optional)
    lead_id          = Column(String, ForeignKey("leads.id"), nullable=True)

    # Assigned advisor
    assigned_to_id   = Column(String, ForeignKey("users.id"), nullable=True)

    # Timestamps
    created_at       = Column(DateTime, default=datetime.utcnow)
    updated_at       = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_contacted_at = Column(DateTime, nullable=True)

    # Custom field values — JSON object keyed by field key defined in org.crm_custom_fields
    custom_data      = Column(Text, nullable=True)

    # Soft-delete
    is_archived      = Column(Boolean, default=False)

    __table_args__ = (
        Index("ix_crm_contacts_org", "organization_id"),
        Index("ix_crm_contacts_stage", "organization_id", "stage"),
        Index("ix_crm_contacts_lead", "lead_id"),
    )


# ---------------------------------------------------------------------------
# CRMNote — Timeline notes for a CRM contact.
# Stored separately so they accumulate as a feed rather than replacing
# the main notes text field.
# ---------------------------------------------------------------------------
class CRMNote(Base):
    __tablename__ = "crm_notes"

    id          = Column(String, primary_key=True, default=gen_uuid)
    contact_id  = Column(String, ForeignKey("crm_contacts.id", ondelete="CASCADE"), nullable=False)
    author_id   = Column(String, ForeignKey("users.id"), nullable=True)
    content     = Column(Text, nullable=False)
    created_at  = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_crm_notes_contact", "contact_id"),
    )
