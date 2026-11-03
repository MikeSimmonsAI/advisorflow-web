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
    # The client's postal address, printed in every outreach email (required
    # by law in commercial email). Outreach to real prospects refuses without it.
    mailing_address = Column(Text, nullable=True)
    # The client's user whose connected calendar real walkthroughs are booked on.
    booking_user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # Owner-chosen calendar that PRACTICE walkthroughs are booked on (testing only).
    test_booking_user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # Onboarding (#452 stage 2). Lists are JSON text.
    website = Column(String, nullable=True)
    service_zips = Column(Text, nullable=True)            # JSON list of 5-digit ZIPs
    service_radius_miles = Column(Integer, nullable=True)
    service_states = Column(Text, nullable=True)          # JSON list of 2-letter states (supported territory only)
    timezone = Column(String, nullable=True)              # IANA, e.g. America/Chicago
    recurring_pref = Column(String(20), nullable=True)    # recurring | one_time | both
    capacity_note = Column(Text, nullable=True)
    appointment_attendee = Column(String, nullable=True)  # who meets prospects and closes
    business_description = Column(Text, nullable=True)
    approved_offer = Column(Text, nullable=True)          # what outreach may offer, in the client's words
    qualification_questions = Column(Text, nullable=True) # JSON list of strings
    # free_pilot (Divine Shine) | growth_pilot_250 - a business decision, set by the owner only.
    offer_type = Column(String(30), nullable=True)
    offer_terms = Column(Text, nullable=True)            # what was agreed, in words; no billing is created
    # The EvoSys Sales opportunity this client was converted from (rapid onboarding).
    sales_opportunity_id = Column(String, nullable=True, index=True)
    appointment_minutes = Column(Integer, nullable=False, default=45)
    # #452 Phase 3: where and how EvoSys books this client's appointments.
    min_notice_hours = Column(Integer, nullable=True)       # earliest bookable time from now
    buffer_minutes = Column(Integer, nullable=True)         # recorded for the operator; slot engine owns overlap
    booking_windows = Column(Text, nullable=True)           # JSON {"mon": [["09:00","17:00"]], ...}
    meeting_location = Column(Text, nullable=True)          # default: at the prospect's property
    booking_contact = Column(String, nullable=True)         # who at the client attends / is told
    reschedule_rules = Column(Text, nullable=True)
    confirmation_pref = Column(String(40), nullable=True)   # calendar_only / calendar_and_email
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
    # Supported territory (geo.py): eligible | review | excluded, with the reason.
    geo_status = Column(String(12), nullable=True)
    geo_note = Column(String(200), nullable=True)
    score = Column(Integer, nullable=True)
    score_reasons = Column(Text, nullable=True)          # JSON list of strings
    disqualified_reason = Column(String(300), nullable=True)
    booking_link_id = Column(String, ForeignKey("booking_links.id", ondelete="SET NULL"), nullable=True)
    quote_amount = Column(Float, nullable=True)          # one-time or first-month
    monthly_value = Column(Float, nullable=True)         # recurring contract value
    lost_reason = Column(String(300), nullable=True)
    outcome_note = Column(Text, nullable=True)
    # A won contract as the CLIENT reports it (#452 Phase 4). Reported is not
    # confirmed: EvoSys confirms separately (won_confirmed_at) before it counts
    # as confirmed value. Nothing here is collected revenue.
    contract_type = Column(String(40), nullable=True)      # recurring / one_time / trial
    service_frequency = Column(String(40), nullable=True)  # e.g. 3x weekly
    contract_value = Column(Float, nullable=True)          # total contract value, as reported
    contract_start = Column(DateTime, nullable=True)
    won_confirmed_at = Column(DateTime, nullable=True)
    won_confirmed_by_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
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


class AcquisitionFollowUp(Base):
    """A cleaning client asks EvoSys to follow up (#452 Phase 4). It becomes a
    task in the Acquisition Control Center - it never triggers an email, text
    or call by itself."""
    __tablename__ = "acquisition_followups"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org_col()
    opportunity_id = Column(String, ForeignKey("acquisition_opportunities.id", ondelete="SET NULL"),
                            nullable=True, index=True)
    reason = Column(String(40), nullable=False)
    note = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="open")      # open / in_progress / done / closed
    requested_by_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    assigned_to_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    resolution_note = Column(Text, nullable=True)
    resolved_by_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


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
    # #452 Phase 5: what this worker may do for THIS client (JSON list of
    # permission keys). NULL = a grant made before permissions existed: full
    # operator access, exactly as before.
    permissions = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (UniqueConstraint("organization_id", "user_id", name="uq_acq_operator_org_user"),)


class AcquisitionDiscoveryRun(Base):
    """One metered discovery batch (P2). Every provider request is counted
    here, so spend is never a guess. Planned-only runs record no requests."""
    __tablename__ = "acquisition_discovery_runs"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org_col()
    requested_by_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    provider = Column(String(40), nullable=False, default="google_places")
    segment = Column(String(40), nullable=False)
    city = Column(String(80), nullable=False)
    query = Column(String(300), nullable=False)
    max_results = Column(Integer, nullable=False)
    is_practice = Column(Boolean, nullable=False, default=True)
    status = Column(String(20), nullable=False, default="running")   # running / done / failed
    search_requests = Column(Integer, nullable=False, default=0)
    detail_requests = Column(Integer, nullable=False, default=0)
    cost_usd = Column(Float, nullable=True)            # from configured unit prices, never invented
    found = Column(Integer, nullable=False, default=0)
    added = Column(Integer, nullable=False, default=0)
    duplicates = Column(Integer, nullable=False, default=0)
    excluded = Column(Integer, nullable=False, default=0)
    junk = Column(Integer, nullable=False, default=0)
    error = Column(String(500), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    finished_at = Column(DateTime, nullable=True)
