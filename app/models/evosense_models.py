"""EVOSENSE ACQUISITION ENGINE — the tables that exist BEFORE a deal does.

    strategy  ->  hunt  ->  source observations  ->  canonical property
              ->  signals  ->  Property Opportunity
              ->  owner / person / contact point  ->  Contact Confidence
              ->  enrichment decision + cost ledger (atomic budget)
              ->  outreach eligibility  ->  engagement (existing cadence engine)
              ->  seller facts + outcome  ->  Seller Intent
              ->  NEEDS YOU  ->  promotion into the EXISTING Wholesale deal

READ THE ARCHITECTURE RECONCILIATION (handoff/WHOLESALE_PHASE7_EVOSENSE_REPORT.md
§0) BEFORE ADDING A TABLE HERE. The short version:

  * A discovered property is NOT a WholesaleProperty. Promotion creates one.
  * A known owner is NOT a Lead. A Lead is created only when EvoSense works the
    owner, so DNC / suppression / cadence / plan capacity apply unchanged.
  * EvoSense persons and contact points are property-owner RELATIONSHIP objects,
    not a second contact database. `converged_contact_ref` is reserved for the
    Universal Intake `org_contacts.id` they will map to (unused in Phase 7).

TENANT RULE: every table carries `organization_id NOT NULL`, every index leads
with it, and every service query filters on it. Two organizations may both
discover 1418 Cedar Springs Rd; they share nothing about it.

SANDBOX RULE: `is_test` on a property travels to everything derived from it,
exactly like `WholesaleProperty.is_test` and `Lead.is_test`.
"""
import threading
from datetime import datetime, timedelta

from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Index, Integer,
                        Numeric, String, Text, UniqueConstraint)

from app.models.models import Base, gen_uuid


_last_stamp = [None]
_stamp_lock = threading.Lock()


def _now():
    """UTC now, strictly increasing within this process.

    "Latest decision / engagement / message" is read by timestamp. Windows'
    clock can hand two rows written in the same millisecond the SAME value,
    which made "latest" a coin toss there (found by running the suite on
    Windows). Nudging a tie forward by a microsecond keeps insertion order."""
    with _stamp_lock:
        t = datetime.utcnow()
        if _last_stamp[0] is not None and t <= _last_stamp[0]:
            t = _last_stamp[0] + timedelta(microseconds=1)
        _last_stamp[0] = t
        return t


# ─────────────────────────────────────────────────────────────────────────────
# Strategy and organization controls
# ─────────────────────────────────────────────────────────────────────────────

class EvoSenseStrategy(Base):
    """WHAT EvoSense hunts for. Never deleted: archived, so attribution holds."""

    __tablename__ = "evosense_strategies"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String, nullable=False, default="draft")   # draft|active|paused|archived
    version = Column(Integer, nullable=False, default=1)        # bumped on every edit
    cloned_from_id = Column(String, nullable=True)

    # Geography — JSON arrays of strings. Empty means "no restriction".
    markets = Column(Text, nullable=True)
    states = Column(Text, nullable=True)
    counties = Column(Text, nullable=True)
    cities = Column(Text, nullable=True)
    zips = Column(Text, nullable=True)

    property_types = Column(Text, nullable=True)                # ["single_family"]
    min_value = Column(Integer, nullable=True)                  # whole dollars
    max_value = Column(Integer, nullable=True)
    min_equity_pct = Column(Integer, nullable=True)             # 35 = 35%
    min_ownership_years = Column(Integer, nullable=True)
    occupancy_preferences = Column(Text, nullable=True)         # ["vacant","non_owner_occupied"]
    owner_geography = Column(String, nullable=True)             # any|absentee|out_of_state

    required_signals = Column(Text, nullable=True)              # JSON
    preferred_signals = Column(Text, nullable=True)             # JSON
    excluded_signals = Column(Text, nullable=True)              # JSON

    min_opportunity_score = Column(Integer, nullable=False, default=60)
    min_contact_confidence = Column(Integer, nullable=False, default=60)
    handoff_intent_threshold = Column(Integer, nullable=False, default=70)
    target_fee = Column(Integer, nullable=True)                 # whole dollars

    daily_budget_cents = Column(Integer, nullable=False, default=0)
    monthly_budget_cents = Column(Integer, nullable=True)
    max_cost_per_property_cents = Column(Integer, nullable=True)
    approval_over_cents = Column(Integer, nullable=True)        # lookup needs a person above this

    provider_preferences = Column(Text, nullable=True)          # JSON {capability: [provider_key]}
    outreach_policy = Column(Text, nullable=True)               # JSON, see strategy.DEFAULT_OUTREACH
    nurture_policy = Column(Text, nullable=True)                # JSON, see strategy.DEFAULT_NURTURE
    # PILOT / CONTROLLED mode: hard record cap, hard spend cap, no outreach,
    # paid data off unless explicitly allowed, never scheduled automatically.
    pilot_mode = Column(Boolean, nullable=False, default=False)
    pilot_max_properties = Column(Integer, nullable=True)
    pilot_max_spend_cents = Column(Integer, nullable=True)
    pilot_allow_paid = Column(Boolean, nullable=False, default=False)

    is_test = Column(Boolean, nullable=False, default=False)
    created_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    activated_at = Column(DateTime, nullable=True)
    archived_at = Column(DateTime, nullable=True)
    last_hunt_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    __table_args__ = (
        Index("ix_es_strategy_org_status", "organization_id", "status"),
    )


class EvoSenseControl(Base):
    """One row per organization: kill switches and the org-level paid budget.

    Every switch is enforced SERVER-SIDE in the service that would act, not by
    hiding a button. `paused_all` stops everything EvoSense does on its own."""

    __tablename__ = "evosense_controls"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False,
                             unique=True)
    paused_all = Column(Boolean, nullable=False, default=False)
    paused_discovery = Column(Boolean, nullable=False, default=False)
    paused_paid_data = Column(Boolean, nullable=False, default=False)
    paused_sms = Column(Boolean, nullable=False, default=False)
    paused_email = Column(Boolean, nullable=False, default=False)
    paused_voice = Column(Boolean, nullable=False, default=False)
    paused_ai_replies = Column(Boolean, nullable=False, default=False)
    org_daily_budget_cents = Column(Integer, nullable=True)     # NULL = strategy budgets only
    org_monthly_budget_cents = Column(Integer, nullable=True)
    owner_touch_cap_days = Column(Integer, nullable=False, default=7)   # one touch per owner per N days
    score_weights = Column(Text, nullable=True)                 # JSON {signal_type: points}; NULL = catalog
    last_hunt_at = Column(DateTime, nullable=True)
    last_hunt_status = Column(String, nullable=True)
    last_command_view_at = Column(DateTime, nullable=True)
    updated_by_id = Column(String, nullable=True)
    updated_at = Column(DateTime, default=_now, onupdate=_now)


class EvoSenseProviderConfig(Base):
    """Per-organization provider configuration and health.

    Credentials are NEVER stored here — adapters read them from the environment
    by name, exactly like `wholesale_enrichment`. Health is written by the
    engine as calls succeed and fail."""

    __tablename__ = "evosense_provider_configs"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    provider_key = Column(String, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    priority = Column(Integer, nullable=False, default=100)     # lower runs first
    cost_overrides = Column(Text, nullable=True)                # JSON {capability: cents}
    health = Column(String, nullable=False, default="connected")
    consecutive_failures = Column(Integer, nullable=False, default=0)
    degraded_until = Column(DateTime, nullable=True)
    rate_limited_until = Column(DateTime, nullable=True)
    last_success_at = Column(DateTime, nullable=True)
    last_failure_at = Column(DateTime, nullable=True)
    last_failure_reason = Column(String, nullable=True)
    calls_total = Column(Integer, nullable=False, default=0)
    successes_total = Column(Integer, nullable=False, default=0)
    last_attempt_at = Column(DateTime, nullable=True)
    last_record_count = Column(Integer, nullable=True)
    last_verified_at = Column(DateTime, nullable=True)           # a probe or a run actually succeeded
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    __table_args__ = (
        UniqueConstraint("organization_id", "provider_key", name="uq_es_provider_org_key"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Canonical property, observations, identity review
# ─────────────────────────────────────────────────────────────────────────────

class EvoSenseProperty(Base):
    """ONE real-world property inside ONE organization.

    Facts here are the CURRENT RESOLVED view; each carries its source and when
    it was observed. The evidence behind them is in `evosense_observations`
    and `evosense_signals`, never overwritten."""

    __tablename__ = "evosense_properties"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)

    # identity
    address_key = Column(String, nullable=True)       # normalized street + unit + zip5
    street_key = Column(String, nullable=True)        # normalized street + city/state (no zip)
    apn_key = Column(String, nullable=True)           # normalized APN + county
    street_address = Column(String, nullable=True)
    unit = Column(String, nullable=True)
    city = Column(String, nullable=True)
    state = Column(String, nullable=True)
    zip_code = Column(String, nullable=True)
    county = Column(String, nullable=True)
    parcel_apn = Column(String, nullable=True)
    latitude = Column(Numeric(10, 6), nullable=True)
    longitude = Column(Numeric(10, 6), nullable=True)

    # physical
    property_type = Column(String, nullable=True)
    bedrooms = Column(Numeric(5, 1), nullable=True)
    bathrooms = Column(Numeric(5, 1), nullable=True)
    square_feet = Column(Integer, nullable=True)
    year_built = Column(Integer, nullable=True)

    # economics (each with provenance)
    estimated_value = Column(Integer, nullable=True)
    estimated_value_source = Column(String, nullable=True)
    estimated_value_at = Column(DateTime, nullable=True)
    mortgage_balance = Column(Integer, nullable=True)
    mortgage_source = Column(String, nullable=True)
    equity_pct = Column(Integer, nullable=True)
    equity_basis = Column(String, nullable=True)      # provider | computed | missing
    last_sale_date = Column(DateTime, nullable=True)
    ownership_years = Column(Integer, nullable=True)
    occupancy = Column(String, nullable=True)         # vacant|owner_occupied|tenant|unknown
    occupancy_source = Column(String, nullable=True)

    # lifecycle
    status = Column(String, nullable=False, default="new")   # see inbox.BUCKETS
    identity_status = Column(String, nullable=False, default="resolved")  # resolved|review
    first_strategy_id = Column(String, ForeignKey("evosense_strategies.id"), nullable=True)
    best_strategy_id = Column(String, nullable=True)
    discovered_at = Column(DateTime, default=_now)
    last_observed_at = Column(DateTime, default=_now)
    last_evaluated_at = Column(DateTime, nullable=True)

    # cached current scores (the history is in evosense_scores)
    opportunity_score = Column(Integer, nullable=True)
    data_confidence = Column(String, nullable=True)   # high|medium|low|insufficient
    contact_confidence = Column(Integer, nullable=True)
    seller_intent = Column(Integer, nullable=True)
    signal_count = Column(Integer, nullable=False, default=0)
    next_action = Column(String, nullable=True)
    next_action_detail = Column(String, nullable=True)
    blocked_reason = Column(String, nullable=True)
    archived_at = Column(DateTime, nullable=True)      # pilot rollback: hidden, never deleted
    archive_reason = Column(String, nullable=True)

    fact_ranks = Column(Text, nullable=True)          # JSON {field: source rank}
    # conflicts the operator should see
    has_conflicts = Column(Boolean, nullable=False, default=False)
    conflicts = Column(Text, nullable=True)           # JSON list

    promoted_property_id = Column(String, nullable=True)   # wholesale_properties.id
    promoted_deal_id = Column(String, nullable=True)       # wholesale_deals.id
    promoted_at = Column(DateTime, nullable=True)

    is_test = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    __table_args__ = (
        Index("ix_es_prop_org_address", "organization_id", "address_key"),
        Index("ix_es_prop_org_street", "organization_id", "street_key"),
        Index("ix_es_prop_org_apn", "organization_id", "apn_key"),
        Index("ix_es_prop_org_status", "organization_id", "status"),
        Index("ix_es_prop_org_score", "organization_id", "opportunity_score"),
    )


class EvoSenseObservation(Base):
    """One thing one source said about one property, at one time. Append-only.

    Idempotency: (organization, provider, source_reference) identifies an
    observation; re-ingesting the same record refreshes `last_seen_at` rather
    than creating a second observation."""

    __tablename__ = "evosense_observations"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    property_id = Column(String, ForeignKey("evosense_properties.id", ondelete="CASCADE"),
                         nullable=True)                  # NULL while in identity review
    strategy_id = Column(String, nullable=True)
    run_id = Column(String, nullable=True)
    provider_key = Column(String, nullable=False)
    connector_kind = Column(String, nullable=False)     # real|sandbox|manual|import
    capability = Column(String, nullable=False)
    source_reference = Column(String, nullable=False)
    observed_at = Column(DateTime, default=_now)
    last_seen_at = Column(DateTime, default=_now)
    payload = Column(Text, nullable=True)                # normalized JSON (never credentials)
    match_type = Column(String, nullable=True)           # exact|probable|ambiguous|new
    match_keys = Column(Text, nullable=True)
    ledger_id = Column(String, nullable=True)
    is_test = Column(Boolean, nullable=False, default=False)
    # Raw evidence, exactly as the source delivered it (never credentials).
    raw_payload = Column(Text, nullable=True)
    content_hash = Column(String, nullable=True)         # sha256 of raw_payload
    adapter_version = Column(String, nullable=True)
    source_updated_at = Column(DateTime, nullable=True)  # the source's own "as of"
    source_url = Column(String, nullable=True)           # canonical public link
    processing_status = Column(String, nullable=True)    # ingested|review|rejected
    processing_error = Column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint("organization_id", "provider_key", "source_reference",
                         name="uq_es_obs_org_provider_ref"),
        Index("ix_es_obs_org_property", "organization_id", "property_id"),
    )


class EvoSenseIdentityReview(Base):
    """An observation whose property identity was ambiguous. Never auto-merged."""

    __tablename__ = "evosense_identity_reviews"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    observation_id = Column(String, ForeignKey("evosense_observations.id", ondelete="CASCADE"),
                            nullable=False)
    candidate_property_ids = Column(Text, nullable=False)   # JSON
    reason = Column(String, nullable=True)
    status = Column(String, nullable=False, default="open")  # open|merged|new|dismissed
    resolved_property_id = Column(String, nullable=True)
    resolved_by_id = Column(String, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_now)

    __table_args__ = (Index("ix_es_idrev_org_status", "organization_id", "status"),)


class EvoSenseSignal(Base):
    """Evidence that a property is interesting. NOT truth because a provider said it."""

    __tablename__ = "evosense_signals"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    property_id = Column(String, ForeignKey("evosense_properties.id", ondelete="CASCADE"),
                         nullable=False)
    signal_type = Column(String, nullable=False)
    observation_id = Column(String, nullable=True)
    source = Column(String, nullable=False)             # provider key or "operator"
    connector_kind = Column(String, nullable=True)
    source_reference = Column(String, nullable=True)
    observed_at = Column(DateTime, default=_now)
    effective_at = Column(DateTime, nullable=True)
    stale_at = Column(DateTime, nullable=True)
    confidence = Column(Integer, nullable=True)         # 0-100
    strength = Column(Integer, nullable=True)           # 0-100
    raw_value = Column(Text, nullable=True)
    normalized_value = Column(Text, nullable=True)
    provenance = Column(Text, nullable=True)            # JSON
    cost_cents = Column(Integer, nullable=False, default=0)
    active = Column(Boolean, nullable=False, default=True)   # False = retracted, kept
    created_by_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=_now)

    __table_args__ = (
        Index("ix_es_sig_org_property", "organization_id", "property_id"),
        Index("ix_es_sig_org_type", "organization_id", "signal_type"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Owner / entity / person / contact graph
# ─────────────────────────────────────────────────────────────────────────────

class EvoSenseOwner(Base):
    """An owner of record — an individual, a couple, an LLC, a trust, an estate.

    `resolution` is honest: an LLC whose people are not legitimately known is
    `unresolved`, and nothing here invents a beneficial owner."""

    __tablename__ = "evosense_owners"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    owner_type = Column(String, nullable=False, default="unknown")
    display_name = Column(String, nullable=True)
    name_key = Column(String, nullable=True)
    mailing_street = Column(String, nullable=True)
    mailing_city = Column(String, nullable=True)
    mailing_state = Column(String, nullable=True)
    mailing_zip = Column(String, nullable=True)
    mailing_key = Column(String, nullable=True)
    resolution = Column(String, nullable=False, default="resolved")  # resolved|unresolved
    last_enriched_at = Column(DateTime, nullable=True)
    last_touch_at = Column(DateTime, nullable=True)      # owner-level frequency cap
    is_test = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    __table_args__ = (
        Index("ix_es_owner_org_name", "organization_id", "name_key"),
        Index("ix_es_owner_org_mailing", "organization_id", "mailing_key"),
    )


class EvoSenseOwnership(Base):
    """Property -> owner, as each source reported it. Conflicts are rows, not overwrites."""

    __tablename__ = "evosense_ownerships"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    property_id = Column(String, ForeignKey("evosense_properties.id", ondelete="CASCADE"),
                         nullable=False)
    owner_id = Column(String, ForeignKey("evosense_owners.id", ondelete="CASCADE"),
                      nullable=False)
    source = Column(String, nullable=False)
    observation_id = Column(String, nullable=True)
    observed_at = Column(DateTime, default=_now)
    is_current = Column(Boolean, nullable=False, default=True)
    in_conflict = Column(Boolean, nullable=False, default=False)
    corrected_by_id = Column(String, nullable=True)       # manual owner correction

    __table_args__ = (
        UniqueConstraint("organization_id", "property_id", "owner_id", "source",
                         name="uq_es_ownership"),
        Index("ix_es_ownership_org_owner", "organization_id", "owner_id"),
    )


class EvoSensePerson(Base):
    """A human connected to an owner: the owner themselves, a co-owner, an heir,
    an executor, a registered agent. Only created when a source says so."""

    __tablename__ = "evosense_persons"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    owner_id = Column(String, ForeignKey("evosense_owners.id", ondelete="CASCADE"),
                      nullable=False)
    full_name = Column(String, nullable=True)
    name_key = Column(String, nullable=True)
    role = Column(String, nullable=False, default="owner")   # owner|co_owner|heir|executor|agent|unknown
    source = Column(String, nullable=False)
    lead_id = Column(String, nullable=True)                  # set when EvoSense works this person
    converged_contact_ref = Column(String, nullable=True)    # future org_contacts.id (§Convergence)
    is_test = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=_now)

    __table_args__ = (Index("ix_es_person_org_owner", "organization_id", "owner_id"),)


class EvoSenseContactPoint(Base):
    """One phone or email for one person, with where it came from and what we know.

    status: active | wrong_party | invalid | suppressed | opted_out
    A wrong-party or suppressed contact point is never deleted — it is the
    record that stops a second strategy from contacting it."""

    __tablename__ = "evosense_contact_points"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    person_id = Column(String, ForeignKey("evosense_persons.id", ondelete="CASCADE"),
                       nullable=False)
    owner_id = Column(String, nullable=False)
    kind = Column(String, nullable=False)                   # phone|email
    value = Column(String, nullable=False)                  # normalized (E.164 / lowercase)
    raw_value = Column(String, nullable=True)
    source = Column(String, nullable=False)
    connector_kind = Column(String, nullable=True)
    provider_confidence = Column(Integer, nullable=True)
    line_type = Column(String, nullable=True)               # mobile|landline|voip|unknown
    validation = Column(String, nullable=True)              # valid|invalid|unverified
    validated_at = Column(DateTime, nullable=True)
    agreeing_sources = Column(Integer, nullable=False, default=1)
    mailing_match = Column(Boolean, nullable=True)      # source's mailing == owner of record's
    status = Column(String, nullable=False, default="active")
    status_reason = Column(String, nullable=True)
    last_response_at = Column(DateTime, nullable=True)
    positive_response = Column(Boolean, nullable=False, default=False)
    converged_contact_ref = Column(String, nullable=True)
    first_seen_at = Column(DateTime, default=_now)
    last_seen_at = Column(DateTime, default=_now)
    is_test = Column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint("organization_id", "person_id", "kind", "value",
                         name="uq_es_contact_point"),
        Index("ix_es_cp_org_value", "organization_id", "kind", "value"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Scores
# ─────────────────────────────────────────────────────────────────────────────

class EvoSenseScore(Base):
    """Every score ever computed. A new scoring version never rewrites history."""

    __tablename__ = "evosense_scores"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    property_id = Column(String, ForeignKey("evosense_properties.id", ondelete="CASCADE"),
                         nullable=False)
    subject_type = Column(String, nullable=False)     # property|contact_point|engagement
    subject_id = Column(String, nullable=False)
    score_type = Column(String, nullable=False)       # property_opportunity|contact_confidence|seller_intent|data_confidence
    version = Column(String, nullable=False)
    value = Column(Integer, nullable=True)            # NULL = insufficient evidence
    label = Column(String, nullable=True)             # band / data confidence label
    inputs = Column(Text, nullable=True)              # JSON
    factors = Column(Text, nullable=True)             # JSON [{points, label, evidence}]
    strategy_id = Column(String, nullable=True)
    is_current = Column(Boolean, nullable=False, default=True)
    calculated_at = Column(DateTime, default=_now)

    __table_args__ = (
        Index("ix_es_score_org_prop_type", "organization_id", "property_id", "score_type"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Money: ledger, atomic budget counters, enrichment decisions
# ─────────────────────────────────────────────────────────────────────────────

class EvoSenseBudgetCounter(Base):
    """Spend per (organization, scope, period). Incremented with a single
    conditional UPDATE, so two workers can never both spend the last dollar."""

    __tablename__ = "evosense_budget_counters"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    scope = Column(String, nullable=False)            # "org" | "strategy:<id>"
    period = Column(String, nullable=False)           # "d:2026-09-24" | "m:2026-09"
    spent_cents = Column(Integer, nullable=False, default=0)
    limit_cents = Column(Integer, nullable=True)

    __table_args__ = (
        UniqueConstraint("organization_id", "scope", "period", name="uq_es_budget_counter"),
    )


class EvoSenseCostEntry(Base):
    """The acquisition cost ledger — one row per paid (or attempted) operation."""

    __tablename__ = "evosense_cost_ledger"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    provider_key = Column(String, nullable=False)
    connector_kind = Column(String, nullable=True)
    capability = Column(String, nullable=False)
    operation = Column(String, nullable=False)
    strategy_id = Column(String, nullable=True)
    property_id = Column(String, nullable=True)
    owner_id = Column(String, nullable=True)
    contact_point_id = Column(String, nullable=True)
    decision_id = Column(String, nullable=True)
    quantity = Column(Integer, nullable=False, default=1)
    unit_cost_cents = Column(Integer, nullable=False, default=0)
    total_cents = Column(Integer, nullable=False, default=0)
    status = Column(String, nullable=False, default="reserved")  # reserved|charged|failed_refunded|failed_charged
    success = Column(Boolean, nullable=True)
    period_day = Column(String, nullable=False)
    period_month = Column(String, nullable=False)
    is_test = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=_now)

    __table_args__ = (
        Index("ix_es_ledger_org_day", "organization_id", "period_day"),
        Index("ix_es_ledger_org_property", "organization_id", "property_id"),
    )


class EvoSenseEnrichmentDecision(Base):
    """Why EvoSense did, or did not, spend money on a property. Every one kept."""

    __tablename__ = "evosense_enrichment_decisions"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    property_id = Column(String, ForeignKey("evosense_properties.id", ondelete="CASCADE"),
                         nullable=False)
    strategy_id = Column(String, nullable=True)
    owner_id = Column(String, nullable=True)
    capability = Column(String, nullable=False, default="CONTACT_ENRICHMENT")
    decision = Column(String, nullable=False)
    reasons = Column(Text, nullable=True)             # JSON list
    provider_key = Column(String, nullable=True)
    estimated_cost_cents = Column(Integer, nullable=True)
    ledger_id = Column(String, nullable=True)
    outcome = Column(String, nullable=True)           # found|no_match|provider_failed|skipped
    decided_by = Column(String, nullable=False, default="engine")   # engine|user
    decided_by_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=_now)

    __table_args__ = (Index("ix_es_decision_org_property", "organization_id", "property_id"),)


# ─────────────────────────────────────────────────────────────────────────────
# Working the owner: engagement, facts, outcomes, handoff, feedback
# ─────────────────────────────────────────────────────────────────────────────

class EvoSenseEngagement(Base):
    """One attempt to work one owner about one property, through one contact point."""

    __tablename__ = "evosense_engagements"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    property_id = Column(String, ForeignKey("evosense_properties.id", ondelete="CASCADE"),
                         nullable=False)
    owner_id = Column(String, nullable=True)
    person_id = Column(String, nullable=True)
    contact_point_id = Column(String, nullable=True)
    lead_id = Column(String, nullable=True)
    strategy_id = Column(String, nullable=True)
    campaign_key = Column(String, nullable=True)
    channel = Column(String, nullable=True)
    status = Column(String, nullable=False, default="pending")
    # pending|blocked|active|responded|nurture|stopped|handed_off|promoted
    blocked_reason = Column(String, nullable=True)
    eligibility = Column(Text, nullable=True)          # JSON of the last check
    touches = Column(Integer, nullable=False, default=0)
    last_touch_at = Column(DateTime, nullable=True)
    delivery_mode = Column(String, nullable=True)       # cadence|sandbox_simulated
    last_outcome = Column(String, nullable=True)
    nurture_until = Column(DateTime, nullable=True)
    nurture_reason = Column(String, nullable=True)
    nurture_resumed_at = Column(DateTime, nullable=True)
    cadence_paused_by_evosense = Column(Boolean, nullable=False, default=False)
    is_test = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    __table_args__ = (
        Index("ix_es_eng_org_property", "organization_id", "property_id"),
        Index("ix_es_eng_org_status", "organization_id", "status"),
    )


class EvoSenseMessage(Base):
    """A message in an EvoSense engagement.

    For a real (non-test) owner the words also live in the platform's own
    `replies` / `messages` tables against the seller Lead — `platform_ref`
    points there. For a sandbox owner no message ever leaves the building, and
    `delivery` says `sandbox_simulated` in plain words."""

    __tablename__ = "evosense_messages"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    engagement_id = Column(String, ForeignKey("evosense_engagements.id", ondelete="CASCADE"),
                           nullable=False)
    property_id = Column(String, nullable=False)
    direction = Column(String, nullable=False)          # outbound|inbound
    channel = Column(String, nullable=False, default="sms")
    body = Column(Text, nullable=False)
    delivery = Column(String, nullable=False)           # cadence_enrolled|sandbox_simulated|received|manual_entry
    platform_ref = Column(String, nullable=True)
    outcome = Column(String, nullable=True)
    reading = Column(Text, nullable=True)                # JSON of the reader's output
    created_at = Column(DateTime, default=_now)

    __table_args__ = (Index("ix_es_msg_org_eng", "organization_id", "engagement_id"),)


class EvoSenseFact(Base):
    """One fact, with where it came from. SELLER STATED is never blended with
    PROVIDER DATA or SYSTEM ESTIMATE."""

    __tablename__ = "evosense_facts"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    property_id = Column(String, ForeignKey("evosense_properties.id", ondelete="CASCADE"),
                         nullable=False)
    engagement_id = Column(String, nullable=True)
    message_id = Column(String, nullable=True)
    fact_type = Column(String, nullable=False)
    value = Column(String, nullable=True)
    truth_state = Column(String, nullable=False)        # seller_stated|provider|estimate|human
    quote = Column(String, nullable=True)
    extracted_by = Column(String, nullable=True)        # rules|ai|operator
    confidence = Column(Integer, nullable=True)
    superseded = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=_now)

    __table_args__ = (Index("ix_es_fact_org_property", "organization_id", "property_id"),)


class EvoSenseHandoff(Base):
    """NEEDS YOU. Automation stops here; a person decides."""

    __tablename__ = "evosense_handoffs"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    property_id = Column(String, ForeignKey("evosense_properties.id", ondelete="CASCADE"),
                         nullable=False)
    engagement_id = Column(String, nullable=True)
    reasons = Column(Text, nullable=False)              # JSON [{code, label}]
    priority = Column(Integer, nullable=False, default=50)
    next_action = Column(String, nullable=True)
    status = Column(String, nullable=False, default="open")   # open|acknowledged|promoted|dismissed
    resolved_by_id = Column(String, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    resolution_note = Column(String, nullable=True)
    is_test = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=_now)

    __table_args__ = (Index("ix_es_handoff_org_status", "organization_id", "status"),)


class EvoSenseFeedback(Base):
    """Operator judgement. Recorded, never auto-trained on."""

    __tablename__ = "evosense_feedback"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    property_id = Column(String, ForeignKey("evosense_properties.id", ondelete="CASCADE"),
                         nullable=False)
    kind = Column(String, nullable=False)
    reason = Column(String, nullable=True)
    user_id = Column(String, nullable=True)
    snapshot = Column(Text, nullable=True)              # scores/signals at the time
    created_at = Column(DateTime, default=_now)


class EvoSenseRun(Base):
    """One hunt. The job's own observable record."""

    __tablename__ = "evosense_runs"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    strategy_id = Column(String, nullable=True)
    trigger = Column(String, nullable=False, default="manual")   # manual|schedule|test
    status = Column(String, nullable=False, default="running")   # running|succeeded|partial|failed|skipped
    counts = Column(Text, nullable=True)
    error = Column(String, nullable=True)
    started_at = Column(DateTime, default=_now)
    finished_at = Column(DateTime, nullable=True)

    __table_args__ = (Index("ix_es_run_org_started", "organization_id", "started_at"),)


class EvoSenseEvent(Base):
    """EvoSense's own audit trail, with a non-human actor, before promotion.
    Same vocabulary as `WholesaleEvent` (actor_type user|system|ai|automation)."""

    __tablename__ = "evosense_events"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    property_id = Column(String, nullable=True)
    strategy_id = Column(String, nullable=True)
    action = Column(String, nullable=False)
    actor_type = Column(String, nullable=False, default="system")
    actor_user_id = Column(String, nullable=True)
    summary = Column(String, nullable=True)
    details = Column(Text, nullable=True)
    is_test = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=_now)

    __table_args__ = (
        Index("ix_es_event_org_created", "organization_id", "created_at"),
        Index("ix_es_event_org_property", "organization_id", "property_id"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Phase 7.1 — autonomous hunting
# ─────────────────────────────────────────────────────────────────────────────

class EvoSenseHuntSchedule(Base):
    """When a strategy hunts on its own, and the lock that stops two hunts of
    the same strategy running at once.

    One row per strategy (created on first need). The platform's background
    loop (`JobName.EVOSENSE_HUNT`, owned by the backend in service_role) reads
    these; the manual "Run hunt" takes the SAME lock through the SAME service.

    cadence   manual  — never automatically
              daily   — every 24 hours from the last run
              interval— every `interval_hours`
    Lock: `locked_until` is claimed with one conditional UPDATE
    (`... WHERE locked_until IS NULL OR locked_until < now`), so two workers
    cannot both win. A crashed hunt's lock expires on its own.
    """

    __tablename__ = "evosense_hunt_schedules"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    strategy_id = Column(String, ForeignKey("evosense_strategies.id"), nullable=False, unique=True)
    cadence = Column(String, nullable=False, default="daily")      # manual|daily|interval
    interval_hours = Column(Integer, nullable=True)
    next_due_at = Column(DateTime, nullable=True)
    last_scheduled_at = Column(DateTime, nullable=True)
    last_run_id = Column(String, nullable=True)
    last_status = Column(String, nullable=True)       # succeeded|partial|failed|skipped_paused|skipped_lock|...
    last_error = Column(String, nullable=True)
    consecutive_failures = Column(Integer, nullable=False, default=0)
    lock_token = Column(String, nullable=True)
    locked_until = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    __table_args__ = (Index("ix_es_sched_org_due", "organization_id", "next_due_at"),)


# ─────────────────────────────────────────────────────────────────────────────
# Platform layer — shared public-source reachability
# ─────────────────────────────────────────────────────────────────────────────

class EvoSenseSourceAccess(Base):
    """PLATFORM-LEVEL access state of one PUBLIC source adapter.

    THE ONE DELIBERATE EXCEPTION TO THE TENANT RULE ABOVE, and why it is safe.
    A public source that refuses the platform's servers (TAD answering 403)
    refuses EVERY tenant: the block is a fact about our egress, not about any
    organization. Recording it once stops twenty tenants from each hammering a
    server that has already said no.

    It holds NO tenant data: no organization id, no property, no record, no
    count or timestamp attributable to a tenant's activity, and the stored
    reason is a generic code + host phrase ("AUTH_FAILED: www.tad.org refused
    the request (403)"). Every tenant sees the same row; nothing in it says who
    tripped it. Tenant enablement, usage, budgets and history stay in
    `evosense_provider_configs` (organization-scoped).

    blocked   true after an authorization refusal (401/403). While blocked the
              adapter is never called automatically - not by hunts, not by
              lookups. Only a PLATFORM admin's explicit Verify (one request)
              may test it again; a success clears the block.
    """

    __tablename__ = "evosense_source_access"

    id = Column(String, primary_key=True, default=gen_uuid)
    provider_key = Column(String, nullable=False, unique=True)
    blocked = Column(Boolean, nullable=False, default=False)
    blocked_code = Column(String, nullable=True)        # AUTH_FAILED
    blocked_reason = Column(String, nullable=True)      # generic, host-level; never tenant data
    blocked_at = Column(DateTime, nullable=True)
    last_probe_at = Column(DateTime, nullable=True)     # platform-admin verify only
    last_probe_ok_at = Column(DateTime, nullable=True)
    cleared_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=_now, onupdate=_now)
