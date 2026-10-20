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
from sqlalchemy import Column, DateTime, Float, ForeignKey, Index, Integer, String, Text
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
