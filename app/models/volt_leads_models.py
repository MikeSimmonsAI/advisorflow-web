"""VOLT consumer leads (DEV-4) - the tables for the consumer eligibility funnel
and the agent dashboard live here, separate from the recruiting tables in
volt_models.py.

Registered in app/models/registry.py, so `python -m app.migrate` (pre-deploy)
and app start-up create any NEW table defined here with create_all. Rules for
this file (enforced by review and by the relay's file scope):
  - new tables only, every table name starting with `volt_lead`;
  - new columns on existing tables are added through app/auto_migrate.py;
  - no Alembic revisions (production does not run Alembic; see app/migrate.py).
Empty until DEV-4 adds the models.
"""
from app.models.models import Base  # noqa: F401  (models defined here subclass Base)

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint

from app.models.models import gen_uuid


class VoltLeadSettings(Base):
    """Per-tenant switches. Intake is OFF until the owner records the review."""
    __tablename__ = "volt_lead_settings"

    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True)
    intake_enabled = Column(Boolean, nullable=False, default=False)
    # Free-text reviewer note (privacy copy / eligibility claims / consent / routing).
    review_note = Column(Text, nullable=True)
    reviewed_by = Column(String, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class VoltLead(Base):
    __tablename__ = "volt_leads"
    __table_args__ = (
        UniqueConstraint("organization_id", "dedup_key", name="uq_volt_lead_dedup"),
        Index("ix_volt_leads_org_assigned", "organization_id", "assigned_user_id"),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    dedup_key = Column(String, nullable=False)           # sha256 of org + normalized phone/email
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=True)
    phone = Column(String, nullable=True)                # digits only
    email = Column(String, nullable=True)
    zip_code = Column(String, nullable=False)
    age_range = Column(String, nullable=True)
    coverage_status = Column(String, nullable=True)
    household_size = Column(Integer, nullable=True)
    help_wanted = Column(Boolean, nullable=False, default=False)
    source = Column(String, nullable=False, default="direct")
    campaign = Column(String, nullable=True)
    consent_version = Column(String, nullable=False)
    consent_text_hash = Column(String, nullable=False)
    consent_at = Column(DateTime, nullable=False)
    status = Column(String, nullable=False, default="new")
    assigned_user_id = Column(String, nullable=True)
    submissions = Column(Integer, nullable=False, default=1)
    # 2026-11-14 (completion): directive fields. Added to the existing table by
    # app/auto_migrate.py (additive, nullable).
    state = Column(String(2), nullable=True)
    income_range = Column(String, nullable=True)
    contact_preference = Column(String, nullable=True)   # call | text | email
    best_time = Column(String, nullable=True)            # morning | afternoon | evening | any
    channel = Column(String, nullable=True)              # meta | google | organic | direct | referral | other
    utm_source = Column(String, nullable=True)
    utm_medium = Column(String, nullable=True)
    utm_campaign = Column(String, nullable=True)
    utm_content = Column(String, nullable=True)
    utm_term = Column(String, nullable=True)
    referrer_host = Column(String, nullable=True)
    landing_path = Column(String, nullable=True)
    appointment_at = Column(DateTime, nullable=True)     # UTC
    enrolled_at = Column(DateTime, nullable=True)        # UTC
    last_contact_at = Column(DateTime, nullable=True)    # UTC
    # Test partition: preview submissions and test sources write is_test=True
    # rows. Every dashboard query filters on it, so test rows never appear in
    # the real inbox, reports or the workspace card, and real rows never in test.
    is_test = Column(Boolean, nullable=False, default=False)
    next_follow_up_at = Column(DateTime, nullable=True)  # UTC
    source_id = Column(String, nullable=True)            # volt_lead_sources.id for external sources
    # Compliance-review flags, e.g. "intake_before_review" (none today).
    flags = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class VoltLeadEvent(Base):
    """Lead history: created, repeat submission, assigned, status, note.
    Never holds contact details."""
    __tablename__ = "volt_lead_events"

    id = Column(String, primary_key=True, default=gen_uuid)
    lead_id = Column(String, ForeignKey("volt_leads.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_id = Column(String, nullable=False, index=True)
    actor_user_id = Column(String, nullable=True)
    action = Column(String, nullable=False)
    old_value = Column(String, nullable=True)
    new_value = Column(String, nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class VoltLeadGrant(Base):
    """Explicit owner grant: this person may see and manage ALL of the tenant's
    consumer leads. Without a grant, a person sees only leads assigned to them."""
    __tablename__ = "volt_lead_grants"

    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(String, primary_key=True)
    granted_by = Column(String, nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    role = Column(String, nullable=False, default="manager")   # manager: every lead | agent: assigned leads only


class VoltLeadSource(Base):
    """Where leads come from besides the website: a signed webhook, a Google Ads
    lead form, a CSV import, or a provider that still needs its account
    connected (Meta, TikTok). Secrets are stored encrypted (crm_secrets)."""
    __tablename__ = "volt_lead_sources"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    provider = Column(String, nullable=False)
    name = Column(String, nullable=False)
    campaign = Column(String, nullable=True)
    is_test = Column(Boolean, nullable=False, default=False)
    status = Column(String, nullable=False, default="paused")   # paused | active | pending_connection
    secret_enc = Column(Text, nullable=True)
    field_map = Column(Text, nullable=True)                      # JSON {target_field: source_key}
    received_count = Column(Integer, nullable=False, default=0)
    failed_count = Column(Integer, nullable=False, default=0)
    duplicate_count = Column(Integer, nullable=False, default=0)
    rejected_count = Column(Integer, nullable=False, default=0)
    last_received_at = Column(DateTime, nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class VoltLeadDelivery(Base):
    """One inbound submission from a source. Idempotent per (source, external
    id). The raw payload is kept only until it is processed (or while it waits
    for a retry), then cleared."""
    __tablename__ = "volt_lead_deliveries"
    __table_args__ = (UniqueConstraint("source_id", "external_id", name="uq_volt_lead_delivery"),)

    id = Column(String, primary_key=True, default=gen_uuid)
    source_id = Column(String, ForeignKey("volt_lead_sources.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_id = Column(String, nullable=False, index=True)
    external_id = Column(String, nullable=False)
    status = Column(String, nullable=False, default="received")  # received | processed | failed
    lead_id = Column(String, nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    payload = Column(Text, nullable=True)
    next_retry_at = Column(DateTime, nullable=True)
    received_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    processed_at = Column(DateTime, nullable=True)
