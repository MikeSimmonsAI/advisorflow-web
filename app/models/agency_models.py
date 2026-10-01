"""INSURANCE AGENCY VERTICAL (Max Life Command) — domain tables that did not exist.

Canonical entities are REUSED, not copied:
  * a prospect IS a Lead (leads.id) in an insurance-agency workspace;
    agency_prospect_profiles only holds the STATED family/need facts a Lead
    has no columns for;
  * an agent IS a User (users.id); agency_agent_profiles holds the
    distribution facts (jurisdictions, specializations, availability, cap);
  * conversation is Message / Reply - the copilot only ADDS simulated events
    (agency_copilot_events), it never writes a provider-backed message.

Appointments get their own table on purpose: booking_links drive real
reminder/confirmation crons (reminder_24hr_sent, confirmation_sent), so a demo
appointment written there could trigger a real send. agency_appointments is
read by nothing that sends.

TENANT RULE: every row carries a NOT NULL organization_id and every query in
app/services/agency filters on it first. Every row carries is_demo.
JSON-ish columns are Text holding JSON (portable across SQLite and Postgres).
"""
from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.sql import func

from app.models.models import Base, gen_uuid


def _org_col():
    return Column(String, ForeignKey("organizations.id", ondelete="CASCADE"),
                  nullable=False, index=True)


class AgencyProspectProfile(Base):
    __tablename__ = "agency_prospect_profiles"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org_col()
    lead_id = Column(String, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False,
                     unique=True, index=True)
    household = Column(Text, nullable=True)            # JSON object, stated only
    preferred_contact = Column(String, nullable=True)  # phone | sms | email | video | in_person
    financial_goals = Column(Text, nullable=True)      # JSON list of stated goals
    stated_concerns = Column(Text, nullable=True)      # JSON list
    need_categories = Column(Text, nullable=True)      # JSON list e.g. ["family_protection"]
    retirement_interest = Column(Boolean, nullable=True)
    business_owner_interest = Column(Boolean, nullable=True)
    living_benefits_interest = Column(Boolean, nullable=True)
    intent_level = Column(String, nullable=True, index=True)  # high|medium|low (stated/recorded)
    page = Column(String, nullable=True)               # provenance: intake page
    utm = Column(Text, nullable=True)                  # provenance: JSON
    automation_paused = Column(Boolean, default=False, nullable=False)
    human_takeover = Column(Boolean, default=False, nullable=False)
    takeover_at = Column(DateTime, nullable=True)
    takeover_by = Column(String, nullable=True)
    is_demo = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AgencyAgentProfile(Base):
    __tablename__ = "agency_agent_profiles"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org_col()
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    jurisdictions = Column(Text, nullable=True)     # JSON list of state codes
    specializations = Column(Text, nullable=True)   # JSON list of need categories
    available = Column(Boolean, default=True, nullable=False)
    active = Column(Boolean, default=True, nullable=False)
    max_active = Column(Integer, nullable=True)     # None -> org config max_active_per_agent
    # Only set from a real measurement / import. Never estimated.
    avg_response_minutes = Column(Float, nullable=True)
    is_demo = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class AgencyDistributionConfig(Base):
    __tablename__ = "agency_distribution_configs"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"),
                             nullable=False, unique=True)
    acceptance_timeout_minutes = Column(Integer, nullable=False, default=30)
    escalation_user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    max_active_per_agent = Column(Integer, nullable=False, default=25)
    factors_enabled = Column(Text, nullable=True)      # JSON list
    stalled_days = Column(Integer, nullable=False, default=7)
    response_target_minutes = Column(Integer, nullable=False, default=60)
    review_window_days = Column(Integer, nullable=False, default=30)
    workload_alert_pct = Column(Integer, nullable=False, default=90)
    recruit_stages = Column(Text, nullable=True)       # JSON list (configurable)
    is_demo = Column(Boolean, default=False, nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AgencyAssignment(Base):
    __tablename__ = "agency_assignments"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org_col()
    lead_id = Column(String, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True)
    agent_user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    state = Column(String, nullable=False, default="offered", index=True)
    # offered | accepted | declined | timed_out | escalated
    offered_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    responded_at = Column(DateTime, nullable=True)
    decline_reason = Column(Text, nullable=True)
    note = Column(Text, nullable=True)
    offered_by = Column(String, nullable=True)        # user id or "system"
    attempt = Column(Integer, nullable=False, default=1)
    reasons = Column(Text, nullable=True)             # JSON list (why this agent)
    history = Column(Text, nullable=True)             # JSON list of {at, event, by, note}
    is_demo = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class AgencyAppointment(Base):
    __tablename__ = "agency_appointments"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org_col()
    lead_id = Column(String, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True)
    agent_user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    type = Column(String, nullable=True)       # discovery | review | application | follow_up
    medium = Column(String, nullable=True)     # phone | video | in_person
    starts_at = Column(DateTime, nullable=False, index=True)
    status = Column(String, nullable=False, default="pending")
    notes = Column(Text, nullable=True)
    is_demo = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class AgencyApplication(Base):
    __tablename__ = "agency_applications"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org_col()
    lead_id = Column(String, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True)
    agent_user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    owner_user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    carrier = Column(String, nullable=True)            # as entered; no carrier integration
    product_category = Column(String, nullable=True)
    status = Column(String, nullable=False, default="draft", index=True)
    status_changed_at = Column(DateTime, nullable=True)
    submitted_at = Column(DateTime, nullable=True)
    notes = Column(Text, nullable=True)
    policy_id = Column(String, nullable=True)
    is_demo = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class AgencyApplicationEvent(Base):
    __tablename__ = "agency_application_events"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org_col()
    application_id = Column(String, ForeignKey("agency_applications.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    from_status = Column(String, nullable=True)
    to_status = Column(String, nullable=False)
    at = Column(DateTime, nullable=False)
    by_user_id = Column(String, nullable=True)
    note = Column(Text, nullable=True)


class AgencyApplicationRequirement(Base):
    __tablename__ = "agency_application_requirements"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org_col()
    application_id = Column(String, ForeignKey("agency_applications.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    label = Column(String, nullable=False)
    status = Column(String, nullable=False, default="open")   # open | received | waived
    due = Column(Date, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class AgencyApplicationDocument(Base):
    """Document METADATA only. No file bytes, no carrier upload."""
    __tablename__ = "agency_application_documents"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org_col()
    application_id = Column(String, ForeignKey("agency_applications.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    name = Column(String, nullable=False)
    kind = Column(String, nullable=True)
    added_at = Column(DateTime, nullable=False)
    added_by = Column(String, nullable=True)


class AgencyPolicy(Base):
    __tablename__ = "agency_policies"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org_col()
    lead_id = Column(String, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True)
    application_id = Column(String, nullable=True)
    agent_user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    policy_number = Column(String, nullable=True)
    carrier = Column(String, nullable=True)
    product_category = Column(String, nullable=True)
    status = Column(String, nullable=False, default="pending")   # in_force | lapsed | pending
    effective_date = Column(Date, nullable=True)
    annual_review_date = Column(Date, nullable=True)
    is_demo = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class AgencyTask(Base):
    """Follow-up / service / review / beneficiary-review tasks for agency records."""
    __tablename__ = "agency_tasks"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org_col()
    kind = Column(String, nullable=False, default="follow_up")
    title = Column(String, nullable=False)
    status = Column(String, nullable=False, default="open")   # open | done
    due_at = Column(DateTime, nullable=True)
    lead_id = Column(String, ForeignKey("leads.id", ondelete="CASCADE"), nullable=True, index=True)
    application_id = Column(String, nullable=True, index=True)
    policy_id = Column(String, nullable=True, index=True)
    recruit_id = Column(String, nullable=True, index=True)
    assigned_user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_by = Column(String, nullable=True)
    is_demo = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class AgencyRecruit(Base):
    __tablename__ = "agency_recruits"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org_col()
    name = Column(String, nullable=False)
    email = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    jurisdiction = Column(String, nullable=True)
    stage = Column(String, nullable=False, default="lead", index=True)
    stage_changed_at = Column(DateTime, nullable=True)
    recruiter_user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    exam_status = Column(String, nullable=True)        # as ENTERED; no government integration
    training_progress_pct = Column(Integer, nullable=True)  # as entered
    notes = Column(Text, nullable=True)
    is_demo = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class AgencyRecruitStageEvent(Base):
    __tablename__ = "agency_recruit_stage_events"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org_col()
    recruit_id = Column(String, ForeignKey("agency_recruits.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    from_stage = Column(String, nullable=True)
    to_stage = Column(String, nullable=False)
    at = Column(DateTime, nullable=False)
    by_user_id = Column(String, nullable=True)


class AgencyRecruitMilestone(Base):
    __tablename__ = "agency_recruit_milestones"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org_col()
    recruit_id = Column(String, ForeignKey("agency_recruits.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    label = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending")  # pending | in_progress | done
    due = Column(Date, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class AgencyCopilotEvent(Base):
    """SIMULATED copilot actions. `simulated` is always True for sends: nothing
    in this table was ever handed to Twilio / Resend / Graph."""
    __tablename__ = "agency_copilot_events"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org_col()
    lead_id = Column(String, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = Column(String, nullable=False)    # simulated_send | automation_paused | automation_resumed | takeover
    body = Column(Text, nullable=True)
    simulated = Column(Boolean, default=True, nullable=False)
    by_user_id = Column(String, nullable=True)
    is_demo = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, nullable=False)
