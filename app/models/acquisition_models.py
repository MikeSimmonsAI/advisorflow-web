"""MANAGED CLIENT ACQUISITION (Issue #452) - the only tables it adds.

Canonical entities are REUSED, never copied:
  * a prospect business IS a Lead in the client's organization (contact
    person in first/last name, business facts in Lead.custom_fields);
  * an appointment IS a BookingLink; a user IS a User; conversation is the
    existing Message / EmailMessage / Reply records.

What exists nowhere else is the acquisition JOURNEY (discovered -> ... ->
won / lost with recurring contract value), the EVIDENCE behind each fact,
the client's business profile and pause switch, and the client's feedback.

TENANT RULE: every row carries a NOT NULL organization_id and every query in
app/services/acquisition filters on it first. New tables only - created by
Base.metadata.create_all, no auto_migrate entry needed.
"""
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from app.models.models import Base, gen_uuid


def _org_col(unique: bool = False):
    return Column(String, ForeignKey("organizations.id", ondelete="CASCADE"),
                  nullable=False, index=True, unique=unique)


class AcquisitionProfile(Base):
    """One per client organization: who they serve, where, and when."""
    __tablename__ = "acquisition_profiles"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org_col(unique=True)
    business_name = Column(String, nullable=True)       # NULL = the org's name
    owner_name = Column(String, nullable=True)
    contact_phone = Column(String, nullable=True)
    contact_email = Column(String, nullable=True)
    service_area = Column(Text, nullable=True)          # JSON list of city names
    segments = Column(Text, nullable=True)              # JSON list of segment keys
    services = Column(Text, nullable=True)              # JSON list of free-text services
    min_job_value = Column(Float, nullable=True)        # monthly, USD
    exclusions = Column(Text, nullable=True)            # JSON list of domains/addresses never to contact
    insurance_note = Column(Text, nullable=True)        # only what the client has verified
    certifications_note = Column(Text, nullable=True)
    availability_note = Column(Text, nullable=True)     # e.g. "Tue/Thu mornings"
    appointment_minutes = Column(Integer, nullable=False, default=45)
    notify_user_ids = Column(Text, nullable=True)       # JSON list of user ids, checked on read
    # EMERGENCY PAUSE - starts paused. Every future worker checks this first.
    paused = Column(Boolean, nullable=False, default=True)
    paused_reason = Column(String(300), nullable=True)
    paused_at = Column(DateTime, nullable=True)
    paused_by_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # The 30-day pilot clock starts only when live operations are verified
    # running - never at setup. Nothing in P1 sets it.
    pilot_started_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AcquisitionOpportunity(Base):
    """One prospect business's place in the acquisition journey."""
    __tablename__ = "acquisition_opportunities"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org_col()
    lead_id = Column(String, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False,
                     unique=True, index=True)
    stage = Column(String(30), nullable=False, default="discovered")
    stage_entered_at = Column(DateTime, nullable=True)
    segment = Column(String(40), nullable=True)
    dedupe_key = Column(String(300), nullable=False)
    domain = Column(String, nullable=True)
    source = Column(String(60), nullable=True)          # manual / places / import ...
    source_url = Column(String, nullable=True)
    is_practice = Column(Boolean, nullable=False, default=False)
    score = Column(Integer, nullable=True)
    score_reasons = Column(Text, nullable=True)          # JSON list of strings
    disqualified_reason = Column(String(300), nullable=True)
    booking_link_id = Column(String, ForeignKey("booking_links.id", ondelete="SET NULL"), nullable=True)
    quote_amount = Column(Float, nullable=True)          # one-time or first-month
    monthly_value = Column(Float, nullable=True)         # recurring contract value
    lost_reason = Column(String(300), nullable=True)
    outcome_note = Column(Text, nullable=True)
    interested_at = Column(DateTime, nullable=True)
    booked_at = Column(DateTime, nullable=True)
    held_at = Column(DateTime, nullable=True)
    quoted_at = Column(DateTime, nullable=True)
    closed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("organization_id", "dedupe_key", name="uq_acq_opp_org_dedupe"),
        Index("ix_acq_opp_org_stage", "organization_id", "stage"),
    )


class AcquisitionEvidence(Base):
    """One sourced fact about a prospect - what, from where, how sure."""
    __tablename__ = "acquisition_evidence"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org_col()
    opportunity_id = Column(String, ForeignKey("acquisition_opportunities.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    kind = Column(String(40), nullable=False)
    value = Column(Text, nullable=True)
    source_url = Column(String, nullable=True)
    confidence = Column(Float, nullable=True)            # 0..1
    verified = Column(Boolean, nullable=False, default=False)
    recorded_by_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    recorded_by_job = Column(String(60), nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class AcquisitionEvent(Base):
    """Audit trail of journey moves - who moved what, when, and why."""
    __tablename__ = "acquisition_events"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org_col()
    opportunity_id = Column(String, ForeignKey("acquisition_opportunities.id", ondelete="CASCADE"),
                            nullable=True, index=True)
    kind = Column(String(40), nullable=False)             # stage / pause / resume / setup / feedback
    from_stage = Column(String(30), nullable=True)
    to_stage = Column(String(30), nullable=True)
    actor_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    note = Column(String(500), nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class AcquisitionFeedback(Base):
    """What the client tells us about a prospect or the service."""
    __tablename__ = "acquisition_feedback"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org_col()
    user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    opportunity_id = Column(String, ForeignKey("acquisition_opportunities.id", ondelete="SET NULL"),
                            nullable=True)
    rating = Column(String(20), nullable=True)            # good_fit / poor_fit / general
    body = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class AcquisitionOperator(Base):
    """A login that may run ONE client's acquisition operations without God
    Mode (owner decision 2026-10-31: wholesale@ operates the pilot). Granted
    and revoked only by the platform owner; grants nothing else anywhere."""
    __tablename__ = "acquisition_operators"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org_col()
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    is_active = Column(Boolean, nullable=False, default=True)
    granted_by_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (UniqueConstraint("organization_id", "user_id", name="uq_acq_operator_org_user"),)
