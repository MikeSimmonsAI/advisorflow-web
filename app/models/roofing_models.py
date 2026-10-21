"""EVOSYS PRO ROOFING PROGRAM - the only tables the roofing vertical adds.

Canonical entities are REUSED, never copied:
  * a homeowner IS a Lead (leads.id). Roofing facts the Lead has no column for
    (roof issue, repair vs replacement, inspection interest, urgency) live in
    Lead.custom_fields under the keys in app/services/roofing/fields.py, which
    is also what lets the universal importer map them and the qualification
    engine write rules on them (`custom_fields.<key>`);
  * a salesperson IS a User; an inspection IS a BookingLink;
  * conversation is Message / EmailMessage / Reply, read through the existing
    /communications endpoints.

What does NOT exist anywhere else is the roofing JOURNEY - Imported ->
Qualified -> Contacted -> Engaged -> Inspection Offered -> Inspection Booked ->
Inspected -> Estimate Sent -> Won / Lost - so it gets its own row per lead
rather than borrowing Lead.tier, which drives message tracks and tone and
would change what the cadence says if it were repurposed as a sales stage.

TENANT RULE: every row carries a NOT NULL organization_id and every query in
app/services/roofing filters on it first. New tables only - created by
Base.metadata.create_all, so no auto_migrate entry is needed.
"""
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.sql import func

from app.models.models import Base, gen_uuid


def _org_col(unique: bool = False):
    return Column(String, ForeignKey("organizations.id", ondelete="CASCADE"),
                  nullable=False, index=True, unique=unique)


class RoofingProfile(Base):
    """One per roofing company (organization): its business configuration."""
    __tablename__ = "roofing_profiles"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org_col(unique=True)
    company_name = Column(String, nullable=True)        # NULL = the org's brand name
    logo_url = Column(String, nullable=True)
    color_primary = Column(String, nullable=True)       # hex, e.g. #0b1f3a
    color_accent = Column(String, nullable=True)        # hex, e.g. #f97316
    service_areas = Column(Text, nullable=True)         # JSON list of strings
    inspection_types = Column(Text, nullable=True)      # JSON list of strings
    inspection_duration_minutes = Column(Integer, nullable=False, default=60)
    default_salesperson_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"),
                                    nullable=True)
    # Who gets the single "inspection booked" alert besides the owner of the
    # lead. JSON list of user ids, every one checked against the org on read.
    notify_user_ids = Column(Text, nullable=True)
    business_phone = Column(String, nullable=True)
    business_email = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class RoofingOpportunity(Base):
    """One homeowner's place in the roofing journey."""
    __tablename__ = "roofing_opportunities"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org_col()
    lead_id = Column(String, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False,
                     unique=True, index=True)
    stage = Column(String(30), nullable=False, default="imported")
    stage_entered_at = Column(DateTime, nullable=True)
    # NO owner column: the owner of a homeowner IS Lead.assigned_to_id, the
    # field lead scope already uses to give a salesperson only their own book.
    # A second owner field here would be two answers to one question.
    booking_link_id = Column(String, ForeignKey("booking_links.id", ondelete="SET NULL"),
                             nullable=True)
    inspection_type = Column(String, nullable=True)
    estimate_amount = Column(Float, nullable=True)
    won_amount = Column(Float, nullable=True)
    lost_reason = Column(String(300), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (Index("ix_roofing_opp_org_stage", "organization_id", "stage"),)


class RoofingStageEvent(Base):
    """Every stage change, who made it and why - the funnel's history."""
    __tablename__ = "roofing_stage_events"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org_col()
    opportunity_id = Column(String, ForeignKey("roofing_opportunities.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    lead_id = Column(String, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False,
                     index=True)
    from_stage = Column(String(30), nullable=True)
    to_stage = Column(String(30), nullable=False)
    actor_user_id = Column(String, nullable=True)       # NULL = derived from activity
    reason = Column(String(300), nullable=True)
    at = Column(DateTime, nullable=False, server_default=func.now())



class RoofingInspection(Base):
    """ONE documented field inspection: the findings a salesperson records at
    the property, the photos, the recommendation and what happened next.

    A booked appointment (BookingLink) says WHEN; this row says WHAT WAS FOUND.
    A homeowner may have several (a re-inspection after a storm), each tied to
    its own appointment. status: in_progress (started / saved as a draft) ->
    completed (a final report, which is the only thing that moves the journey
    to Inspected from here). Free-form lists are JSON text.
    """
    __tablename__ = "roofing_inspections"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org_col()
    lead_id = Column(String, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True)
    opportunity_id = Column(String, ForeignKey("roofing_opportunities.id", ondelete="SET NULL"), nullable=True)
    booking_link_id = Column(String, ForeignKey("booking_links.id", ondelete="SET NULL"), nullable=True, index=True)
    inspector_user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    scheduled_at = Column(DateTime, nullable=True)          # the appointment's local wall-clock time
    inspection_type = Column(String, nullable=True)
    status = Column(String(20), nullable=False, default="in_progress")
    report_number = Column(String(20), nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    completed_by_id = Column(String, nullable=True)
    roof_type = Column(String(40), nullable=True)
    roof_age_years = Column(Integer, nullable=True)
    stories = Column(Integer, nullable=True)
    roof_condition = Column(String(20), nullable=True)
    damage = Column(Text, nullable=True)                    # JSON list of checklist keys
    severity = Column(String(20), nullable=True)
    affected_areas = Column(Text, nullable=True)            # JSON list
    notes = Column(Text, nullable=True)
    recommendation = Column(String(20), nullable=True)
    recommendation_notes = Column(Text, nullable=True)
    insurance_claim = Column(String(20), nullable=True)
    next_action = Column(String(40), nullable=True)
    follow_up_at = Column(DateTime, nullable=True)
    handoff = Column(Text, nullable=True)                   # JSON: last sales hand-off
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (Index("ix_rf_insp_org_status", "organization_id", "status"),)


class RoofingInspectionPhoto(Base):
    """A photo on an inspection. The bytes live wherever wholesale_files keeps
    files (the database unless an object store is configured); the storage key
    never leaves the server - every photo is served through an authenticated,
    organization-checked route."""
    __tablename__ = "roofing_inspection_photos"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org_col()
    inspection_id = Column(String, ForeignKey("roofing_inspections.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    lead_id = Column(String, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True)
    label = Column(String(30), nullable=True)
    caption = Column(String(300), nullable=True)
    is_sample = Column(Boolean, nullable=False, default=False)
    storage_backend = Column(String, nullable=False)
    storage_key = Column(String, nullable=False)
    content_type = Column(String, nullable=False)
    byte_size = Column(Integer, nullable=False, default=0)
    checksum = Column(String, nullable=True)
    original_filename = Column(String, nullable=True)
    position = Column(Integer, nullable=False, default=0)
    created_by_id = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
