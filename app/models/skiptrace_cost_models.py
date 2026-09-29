"""Skip-trace economics: the provider/product price catalogue and the
per-workspace cost ESTIMATES a paid run must be confirmed against.

Stream XE (express overhaul 2026-09-28). NEW TABLES ONLY - nothing here alters
an existing table, so `Base.metadata.create_all` creates them and no
COLUMNS_TO_ADD entry is needed. Registry line (coordinator):

    import app.models.skiptrace_cost_models  # noqa: F401  (imported for side effects)

skiptrace_provider_products  PLATFORM-WIDE price catalogue (no tenant data).
    Seeded from the code-level catalogue in app/services/skiptrace_costing.py.
    Every price carries its source URL + the date it was read, or says
    "unverified - owner to confirm". Money is Numeric (decimal-safe cents):
    Tracerfy's normal trace is 2 cents, a Derrick-style benchmark is 1 cent,
    and volume tiers go below a cent - integers would round them away.
    `configured` is NOT a column: it is a read-only presence check of the
    provider's credential env var(s), computed at read time, never printed.

skiptrace_cost_estimates  PER-WORKSPACE (organization_id NOT NULL). One row
    per "what would this batch cost" question. A paid run may only be queued
    against a CONFIRMED, unexpired, unconsumed estimate of the same org,
    provider/product and record count - the explicit human confirmation that
    carries the estimate id. Consumed once, so one confirmation can never
    authorize two paid runs (duplicate-billing guard).
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, Numeric, String, Text

from app.models.models import Base, gen_uuid

ESTIMATE_STATUSES = ("estimated", "confirmed", "consumed", "expired")
PRICE_STATUSES = ("vendor_published", "third_party_reported", "unverified")
MINIMUM_KINDS = ("none", "subscription", "spend_floor")


class SkipTraceProviderProduct(Base):
    """One priced product of one skip-trace provider (platform-wide)."""

    __tablename__ = "skiptrace_provider_products"

    id = Column(String, primary_key=True, default=gen_uuid)
    key = Column(String, nullable=False, unique=True)            # "tracerfy:normal_batch"
    provider = Column(String, nullable=False)                    # "tracerfy"
    product = Column(String, nullable=False)                     # "normal_batch"
    label = Column(String, nullable=False)
    endpoint_label = Column(String, nullable=True)               # human description of the endpoint
    # Money, decimal-safe, in CENTS. NULL = not known (never guessed).
    cost_per_hit_cents = Column(Numeric(12, 4), nullable=True)
    cost_per_request_cents = Column(Numeric(12, 4), nullable=True)
    misses_charged = Column(Boolean, nullable=True)              # NULL = unknown
    monthly_minimum_cents = Column(Numeric(14, 4), nullable=True)
    minimum_kind = Column(String, nullable=False, default="none")  # none | subscription | spend_floor
    included_records = Column(Integer, nullable=True)            # records a subscription includes
    minimum_purchase_cents = Column(Numeric(14, 4), nullable=True)  # one-time credit purchase floor
    expected_hit_rate = Column(Numeric(5, 4), nullable=True)     # only a vendor/measured figure; NULL otherwise
    batch_supported = Column(Boolean, nullable=True)
    webhook_supported = Column(Boolean, nullable=True)
    api_available = Column(Boolean, nullable=True)
    credential_env = Column(String, nullable=True)               # env var NAME(S), comma-separated; never a value
    price_status = Column(String, nullable=False, default="unverified")
    source_url = Column(String, nullable=True)
    verified_at = Column(String, nullable=True)                  # ISO date the price was read; NULL = unverified
    notes = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)    # eligible for recommendation
    is_current = Column(Boolean, nullable=False, default=False)  # the product the integration uses today
    catalogue_version = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_st_products_provider", "provider"),
    )


class SkipTraceCostEstimate(Base):
    """What a proposed skip-trace batch would cost - shown BEFORE any run."""

    __tablename__ = "skiptrace_cost_estimates"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    product_key = Column(String, nullable=False)
    provider = Column(String, nullable=False)
    property_kind = Column(String, nullable=True)                # wholesale | evosense | NULL (count only)
    property_ids = Column(Text, nullable=True)                   # JSON list of BILLABLE ids (after dedupe)
    records = Column(Integer, nullable=False, default=0)         # submitted
    already_traced = Column(Integer, nullable=False, default=0)  # excluded by dedupe
    not_found = Column(Integer, nullable=False, default=0)       # ids not in this org (ignored)
    billable_requests = Column(Integer, nullable=False, default=0)
    expected_hit_rate = Column(Numeric(5, 4), nullable=True)
    expected_hits = Column(Numeric(14, 4), nullable=True)
    estimated_total_cents = Column(Numeric(14, 4), nullable=True)
    maximum_total_cents = Column(Numeric(14, 4), nullable=True)
    monthly_total_cents = Column(Numeric(14, 4), nullable=True)
    assumptions = Column(Text, nullable=True)                    # JSON list of strings
    status = Column(String, nullable=False, default="estimated")
    created_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    confirmed_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    consumed_at = Column(DateTime, nullable=True)
    consumed_by = Column(String, nullable=True)                  # which trigger path consumed it
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_st_estimates_org_created", "organization_id", "created_at"),
    )
