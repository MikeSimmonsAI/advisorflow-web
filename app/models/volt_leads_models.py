"""VOLT consumer leads (DEV-4) - the tables for the consumer eligibility funnel
and the agent dashboard live here, separate from the recruiting tables in
volt_models.py.

Registered in app/models/registry.py, so `python -m app.migrate` (pre-deploy)
and app start-up create any NEW table defined here with create_all. Rules for
this file (enforced by review and by the relay's file scope):
  - new tables only, every table name starting with `volt_lead`;
  - no new columns on existing tables (that needs auto_migrate, outside scope);
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
