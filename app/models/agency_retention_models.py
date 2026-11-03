"""INSURANCE RETENTION WORKSPACE — renewals, reviews and service for EXISTING clients.

Built for the Vincent Greene Agency proof of concept (Issue #499) but nothing
here is specific to one agency: every value that differs between agencies is a
row in `agency_retention_settings`, never a constant.

REUSED, NOT COPIED
  * a household / client IS a Lead (leads.id) in the agency workspace;
  * a policy IS an AgencyPolicy (agency_policies.id);
  * a location IS a Location, staff membership of a location IS a UserLocation;
  * a review IS an AgencyAppointment (type="review") - read by nothing that sends.

NEW TABLES ONLY. No column is added to an existing table, so auto_migrate has
nothing to ALTER - create_all builds these whole.

THREE STATUSES THAT ARE NEVER MIXED
  * policy status      -> agency_policies.status (in_force | lapsed | pending ...)
  * renewal workflow   -> agency_renewals.workflow_status
  * appointment        -> agency_appointments.status
`agency_renewals.outcome` (confirmed | lost) is written ONLY by an explicit,
authorized outcome or a verified import - never by a message, a reply or a
booked review.

TENANT RULE: NOT NULL organization_id on every row; every query filters on it
first. Every row carries is_demo so demo and live analytics never mix.
"""
from sqlalchemy import (Boolean, Column, Date, DateTime, ForeignKey, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.sql import func

from app.models.models import Base, gen_uuid


def _org():
    return Column(String, ForeignKey("organizations.id", ondelete="CASCADE"),
                  nullable=False, index=True)


class AgencyRetentionSettings(Base):
    """One per agency workspace. Its presence (enabled=True) turns the retention
    workspace on; Max Life and every other agency workspace are unaffected."""
    __tablename__ = "agency_retention_settings"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"),
                             nullable=False, unique=True)
    enabled = Column(Boolean, nullable=False, default=True)
    display_name = Column(String, nullable=True)        # configurable POC display name
    tagline = Column(String, nullable=True)
    timezone = Column(String, nullable=True)            # agency default; a location may override
    default_offsets = Column(Text, nullable=True)       # JSON list of days before renewal
    send_window_start = Column(String, nullable=False, default="08:30")   # local HH:MM
    send_window_end = Column(String, nullable=False, default="19:45")
    shared_queue = Column(Boolean, nullable=False, default=True)  # agents may claim unassigned work at their locations
    routing_mode = Column(String, nullable=False, default="least_loaded")  # least_loaded | round_robin
    review_minutes = Column(Integer, nullable=False, default=30)
    data_mode = Column(String, nullable=False, default="demo")   # demo | csv_import | integration
    reference = Column(Text, nullable=True)             # JSON: verified public facts + their source
    is_demo = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AgencyRetentionStaff(Base):
    """Routing facts for a staff member. Only stored / verified values."""
    __tablename__ = "agency_retention_staff"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org()
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String, nullable=True)               # producer | service | retention | manager | owner
    languages = Column(Text, nullable=True)             # JSON list e.g. ["en","es"]
    products = Column(Text, nullable=True)              # JSON list of products they may service
    review_capacity = Column(Integer, nullable=True)    # reviews per 14 days; None = not configured
    available = Column(Boolean, nullable=False, default=True)
    is_demo = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, server_default=func.now())
    __table_args__ = (UniqueConstraint("organization_id", "user_id", name="uq_retention_staff_user"),)


class AgencyClientProfile(Base):
    __tablename__ = "agency_client_profiles"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org()
    lead_id = Column(String, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False,
                     unique=True, index=True)
    household_name = Column(String, nullable=True)
    location_id = Column(String, ForeignKey("locations.id", ondelete="SET NULL"), nullable=True, index=True)
    servicing_user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    preferred_language = Column(String, nullable=True)  # en | es
    preferred_channel = Column(String, nullable=True)   # sms | email | phone
    contact_ok = Column(Boolean, nullable=False, default=True)   # False after an opt-out
    source = Column(String, nullable=False, default="demo")      # demo | csv_import | integration
    source_ref = Column(String, nullable=True)
    source_updated_at = Column(DateTime, nullable=True)
    is_demo = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, server_default=func.now())


class AgencyRenewal(Base):
    """One renewal term of one policy - the unit of retention work."""
    __tablename__ = "agency_renewals"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org()
    lead_id = Column(String, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True)
    policy_id = Column(String, ForeignKey("agency_policies.id", ondelete="CASCADE"), nullable=False, index=True)
    location_id = Column(String, ForeignKey("locations.id", ondelete="SET NULL"), nullable=True, index=True)
    product = Column(String, nullable=True)
    renewal_date = Column(Date, nullable=False, index=True)
    workflow_status = Column(String, nullable=False, default="not_started", index=True)
    # not_started | in_outreach | needs_attention | review_booked | reviewed | closed
    attention_reason = Column(String, nullable=True)    # stored reason code (see services)
    attention_detail = Column(Text, nullable=True)
    outcome = Column(String, nullable=True, index=True)  # confirmed | lost   (explicit only)
    outcome_at = Column(DateTime, nullable=True)
    outcome_by = Column(String, nullable=True)
    outcome_source = Column(String, nullable=True)       # staff | csv_import | integration
    outcome_note = Column(Text, nullable=True)
    assigned_user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    assigned_at = Column(DateTime, nullable=True)
    assignment_reason = Column(String, nullable=True)
    version = Column(Integer, nullable=False, default=1)  # optimistic lock for claim / reassign
    next_action = Column(String, nullable=True)
    next_action_at = Column(DateTime, nullable=True)
    review_appointment_id = Column(String, nullable=True)
    last_activity_at = Column(DateTime, nullable=True)
    source = Column(String, nullable=False, default="demo")
    source_updated_at = Column(DateTime, nullable=True)
    is_demo = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, server_default=func.now())
    __table_args__ = (UniqueConstraint("policy_id", "renewal_date", name="uq_renewal_policy_term"),)


class AgencyRenewalWorkflow(Base):
    __tablename__ = "agency_renewal_workflows"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org()
    name = Column(String, nullable=False)
    kind = Column(String, nullable=False, default="renewal_outreach")  # renewal_outreach | annual_review | appointment_reminder
    offsets = Column(Text, nullable=False)               # JSON list of days before renewal, descending
    channel = Column(String, nullable=False, default="sms")
    template = Column(Text, nullable=True)
    template_es = Column(Text, nullable=True)
    window_days = Column(Integer, nullable=False, default=60)
    products = Column(Text, nullable=True)               # JSON list; None = all
    location_ids = Column(Text, nullable=True)           # JSON list; None = all permitted
    state = Column(String, nullable=False, default="draft")   # draft | running | paused
    delivery_mode = Column(String, nullable=False, default="simulated")  # simulated | live
    last_run_at = Column(DateTime, nullable=True)
    created_by = Column(String, nullable=True)
    is_demo = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AgencyRenewalEnrollment(Base):
    __tablename__ = "agency_renewal_enrollments"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org()
    workflow_id = Column(String, ForeignKey("agency_renewal_workflows.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    renewal_id = Column(String, ForeignKey("agency_renewals.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    state = Column(String, nullable=False, default="active")
    # active | paused | needs_human | completed | stopped
    steps_done = Column(Text, nullable=True)             # JSON list of offsets already processed
    next_offset = Column(Integer, nullable=True)
    next_run_at = Column(DateTime, nullable=True, index=True)
    paused_reason = Column(String, nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    is_demo = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, server_default=func.now())
    __table_args__ = (UniqueConstraint("workflow_id", "renewal_id", name="uq_enrollment_workflow_renewal"),)


class AgencyRenewalEvent(Base):
    """Per-record history: outreach, replies, routing, bookings, outcomes.
    delivery_status is the honest state of an outbound message:
    simulated (never handed to a provider) | queued | delivered | failed."""
    __tablename__ = "agency_renewal_events"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org()
    renewal_id = Column(String, ForeignKey("agency_renewals.id", ondelete="CASCADE"), nullable=True, index=True)
    lead_id = Column(String, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True)
    enrollment_id = Column(String, nullable=True, index=True)
    kind = Column(String, nullable=False, index=True)
    direction = Column(String, nullable=True)            # out | in | internal
    channel = Column(String, nullable=True)
    body = Column(Text, nullable=True)
    delivery_status = Column(String, nullable=True)
    detail = Column(Text, nullable=True)                 # JSON: classification, from/to, reason...
    idempotency_key = Column(String, nullable=True, unique=True)
    handled = Column(Boolean, nullable=False, default=False)   # inbound reply resolved by a person
    actor_user_id = Column(String, nullable=True)
    is_demo = Column(Boolean, nullable=False, default=False)
    at = Column(DateTime, nullable=False, index=True)
