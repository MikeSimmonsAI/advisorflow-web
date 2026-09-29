"""LEAD INTELLIGENCE — the platform's own prospect pool, scrape jobs and routing rules.

WHAT THESE TABLES ARE
---------------------
    LeadIntelScrapeJob     one search run by a God operator (industry, query,
                           geography, radius, max results, intended destination)
    LeadIntelProspect      one discovered business, after normalize -> dedupe ->
                           suppression -> qualification, waiting for a human to
                           route it
    LeadIntelRoutingRule   an operator-defined SUGGESTION of where a prospect
                           should go. Rules never route anything by themselves.

WHAT THEY ARE NOT
-----------------
They are not tenant data. A prospect in this pool belongs to no organization
until a God operator explicitly routes it, and routing puts it into the
destination organization's Universal Intake as a STAGED import batch for that
organization to review. Nothing here creates a Lead, grants consent, enrolls a
cadence or sends anything.

`destination_org_id` on a job / prospect is an INTENT recorded at search time.
It is never acted on automatically: `routed_org_id` is only ever written by the
explicit POST /god/lead-intelligence/route action.

Master contacts (app/models/master_contact_models.py) are deliberately NOT
routable: they are one customer's people, and copying them into another
customer's book is exactly the cross-tenant leak the master database exists to
prevent.
"""

from sqlalchemy import (Boolean, Column, DateTime, Index, Integer, String, Text,
                        func)

from app.models.models import Base, gen_uuid


class LeadIntelScrapeJob(Base):
    __tablename__ = "lead_intel_scrape_jobs"

    id = Column(String, primary_key=True, default=gen_uuid)
    created_by_id = Column(String, nullable=True)
    provider = Column(String, nullable=False, default="google_places")
    industry = Column(String, nullable=True)
    query = Column(String, nullable=True)
    effective_query = Column(String, nullable=True)
    location = Column(String, nullable=True)
    radius_meters = Column(Integer, nullable=True)
    max_results = Column(Integer, nullable=True)
    # INTENT ONLY. Never routed to automatically.
    destination_org_id = Column(String, nullable=True, index=True)
    status = Column(String, nullable=False, default="searched")   # searched | staged | imported
    result_count = Column(Integer, nullable=False, default=0)
    staged_count = Column(Integer, nullable=False, default=0)
    imported_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class LeadIntelProspect(Base):
    __tablename__ = "lead_intel_prospects"

    id = Column(String, primary_key=True, default=gen_uuid)
    job_id = Column(String, nullable=True, index=True)
    source = Column(String, nullable=False, default="google_places")
    external_id = Column(String, nullable=True, index=True)      # e.g. Google place_id
    industry = Column(String, nullable=True, index=True)

    name = Column(String, nullable=True)
    phone_raw = Column(String, nullable=True)
    phone_e164 = Column(String, nullable=True, index=True)
    email = Column(String, nullable=True)
    website = Column(String, nullable=True)
    address = Column(String, nullable=True)
    city = Column(String, nullable=True, index=True)
    state = Column(String, nullable=True, index=True)
    zip_code = Column(String, nullable=True)
    rating = Column(String, nullable=True)
    reviews_count = Column(Integer, nullable=True)

    # discovered -> invalid | duplicate | suppressed | qualified ; routed is
    # tracked separately (routed_org_id) so the funnel stays countable.
    stage = Column(String, nullable=False, default="discovered", index=True)
    duplicate_of_id = Column(String, nullable=True)
    suppressed_reason = Column(String, nullable=True)

    bucket = Column(String, nullable=True, index=True)     # READY_TO_SEND | REVIEW_REQUIRED | EXCLUDED
    priority = Column(String, nullable=True, index=True)   # HIGH | MEDIUM | LOW (None when excluded)
    score = Column(Integer, nullable=True, index=True)
    best_channel = Column(String, nullable=True)
    decision_json = Column(Text, nullable=True)            # per-channel decisions from qualification.qualify_one

    destination_org_id = Column(String, nullable=True, index=True)   # intent, from the job
    routed_org_id = Column(String, nullable=True, index=True)
    routed_batch_id = Column(String, nullable=True)
    routed_at = Column(DateTime, nullable=True)
    routed_by_id = Column(String, nullable=True)
    routed_rule_id = Column(String, nullable=True)

    created_at = Column(DateTime, server_default=func.now(), index=True)

    __table_args__ = (
        Index("ix_lead_intel_prospect_stage_bucket", "stage", "bucket"),
    )


class LeadIntelRoutingRule(Base):
    __tablename__ = "lead_intel_routing_rules"

    id = Column(String, primary_key=True, default=gen_uuid)
    name = Column(String, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=100)
    match_industry = Column(String, nullable=True)
    match_state = Column(String, nullable=True)
    match_city = Column(String, nullable=True)
    min_score = Column(Integer, nullable=True)
    # Comma-separated buckets the rule applies to. EXCLUDED is never allowed.
    allowed_buckets = Column(String, nullable=False, default="READY_TO_SEND")
    destination_org_id = Column(String, nullable=False, index=True)
    created_by_id = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
