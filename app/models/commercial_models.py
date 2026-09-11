"""CUSTOM COMMERCIAL AGREEMENTS — the deals that are not a subscription.

WHY THIS EXISTS
---------------
T2 answers one commercial question completely: what did this customer buy from
the catalogue, and what does Stripe collect for it. That question has a floor
under it — a price, an interval, a checkout — and every customer who fits it is
served by `brand_billing_plans`, `brand_catalog_items` and `catalog_purchases`,
which stay authoritative and are NOT duplicated here.

Some customers do not fit it. A revenue-share customer pays nothing up front,
nothing monthly, and instead splits collections with the brand on terms that
are negotiated per deal. A hybrid customer has a catalogue subscription AND a
share. A quoted customer has a number that exists only in a signed document.
None of those can be expressed as a catalogue item, and forcing them into one
would mean inventing a price — which is the one thing a commerce system must
never do.

So this module models THE ARRANGEMENT rather than THE PURCHASE, and links to
T2 rather than replacing it. A hybrid agreement's fixed side is a
`catalog_purchase`; only the share lives here.

════════════════════════════════════════════════════════════════════════════
UNKNOWN IS A VALUE. ZERO IS A DIFFERENT VALUE.
════════════════════════════════════════════════════════════════════════════

Terms are rows, not columns, for one reason: a NULL column cannot tell
"nobody has decided yet" apart from "the answer is nothing". A revenue-share
deal that genuinely has a $0 monthly base is NOT the same deal as one whose
monthly base has never been discussed, and a settlement engine that treats the
second as the first will pay somebody a number nobody agreed to.

`commercial_terms.state` therefore carries that difference explicitly:

  answered        somebody decided, and `value_json` holds the decision —
                  including a decision of zero.
  required        nobody has decided, and activation cannot proceed until
                  they do.
  unknown         nobody has decided, and it does not block activation.
  not_applicable  somebody decided the question does not apply here.

Nothing in this package ever reads an absent term as zero. Settlement refuses
instead. See `app/services/commercial/settlement.py`.

════════════════════════════════════════════════════════════════════════════
NO CUSTOMER IS NAMED IN THIS FILE
════════════════════════════════════════════════════════════════════════════

There is a first real arrangement behind this work, and it is named nowhere in
this module, in the services beside it, or in the tests' assertions about the
engine — because a two-party split, an unfinished term sheet and a particular
pair of percentages are DATA. A later deal with three parties, a tiered basis
and a complete term sheet uses the same tables and the same code paths, and a
test in tests/test_commercial_atlantis.py enforces that mechanically by
failing if any customer's name, contact or split reaches this package.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON, Boolean, Column, Date, DateTime, ForeignKey, Index, Integer,
    Numeric, String, Text, UniqueConstraint,
)

from app.models.models import Base, gen_uuid

# ── commercial model types ──────────────────────────────────────────────────
#
# What SHAPE this arrangement is. Not what stage it is at, and not how it is
# collected — those are `status` and the collections source respectively.
TYPE_STANDARD_SUBSCRIPTION = "standard_subscription"
TYPE_CUSTOM_FIXED          = "custom_fixed"
TYPE_REVENUE_SHARE         = "revenue_share"
TYPE_HYBRID                = "hybrid"
TYPE_CUSTOM_QUOTED         = "custom_quoted"

AGREEMENT_TYPES = (
    TYPE_STANDARD_SUBSCRIPTION,
    TYPE_CUSTOM_FIXED,
    TYPE_REVENUE_SHARE,
    TYPE_HYBRID,
    TYPE_CUSTOM_QUOTED,
)

AGREEMENT_TYPE_LABELS = {
    TYPE_STANDARD_SUBSCRIPTION: "Standard subscription",
    TYPE_CUSTOM_FIXED:          "Custom fixed fee",
    TYPE_REVENUE_SHARE:         "Revenue share",
    TYPE_HYBRID:                "Hybrid (fixed + revenue share)",
    TYPE_CUSTOM_QUOTED:         "Custom quoted",
}

# Types whose economics depend on a split of collections. Used to decide which
# questions apply and whether an allocation has to reconcile.
SHARE_BEARING_TYPES = (TYPE_REVENUE_SHARE, TYPE_HYBRID)

# ── lifecycle ───────────────────────────────────────────────────────────────
#
# DRAFT             being written; nothing depends on it yet.
# TERMS_REQUIRED    real, in use, and knowingly incomplete. This is the state
#                   that lets onboarding run while a term sheet is unfinished.
# READY_FOR_APPROVAL every activation-required term is answered.
# APPROVED          an authorised approver signed off on the terms.
# ACTIVE            approved AND in force from the effective date.
# SUSPENDED         in force but halted; settlement does not calculate.
# ENDED             finished. Historical settlements stay readable.
AG_DRAFT              = "draft"
AG_TERMS_REQUIRED     = "terms_required"
AG_READY_FOR_APPROVAL = "ready_for_approval"
AG_APPROVED           = "approved"
AG_ACTIVE             = "active"
AG_SUSPENDED          = "suspended"
AG_ENDED              = "ended"

AGREEMENT_STATUSES = (AG_DRAFT, AG_TERMS_REQUIRED, AG_READY_FOR_APPROVAL,
                      AG_APPROVED, AG_ACTIVE, AG_SUSPENDED, AG_ENDED)

AGREEMENT_STATUS_LABELS = {
    AG_DRAFT:              "Draft",
    AG_TERMS_REQUIRED:     "Terms required",
    AG_READY_FOR_APPROVAL: "Ready for approval",
    AG_APPROVED:           "Approved",
    AG_ACTIVE:             "Active",
    AG_SUSPENDED:          "Suspended",
    AG_ENDED:              "Ended",
}

# Statuses in which the arrangement is a live commercial fact rather than a
# draft. Onboarding may proceed in every one of them.
AGREEMENT_OPEN_STATUSES = (AG_DRAFT, AG_TERMS_REQUIRED, AG_READY_FOR_APPROVAL,
                           AG_APPROVED, AG_ACTIVE, AG_SUSPENDED)

# ── term states ─────────────────────────────────────────────────────────────
TERM_ANSWERED       = "answered"
TERM_REQUIRED       = "required"
TERM_UNKNOWN        = "unknown"
TERM_NOT_APPLICABLE = "not_applicable"

TERM_STATES = (TERM_ANSWERED, TERM_REQUIRED, TERM_UNKNOWN, TERM_NOT_APPLICABLE)

# ── party types ─────────────────────────────────────────────────────────────
PARTY_PLATFORM_BRAND = "platform_brand"   # the brand / its sales org
PARTY_CUSTOMER_ORG   = "customer_org"     # the customer tenant
PARTY_EXTERNAL       = "external"         # a third party named in the deal

PARTY_TYPES = (PARTY_PLATFORM_BRAND, PARTY_CUSTOMER_ORG, PARTY_EXTERNAL)

# ── collections sources ─────────────────────────────────────────────────────
#
# Where an authoritative collection figure came from. A settlement may only be
# calculated from records whose source is one of these AND whose status is
# approved — see settlement.py. "future_integration" is deliberately listed and
# deliberately produces no records: it records an intention, not a feed.
SOURCE_STRIPE              = "stripe"
SOURCE_MANUAL_APPROVED     = "manual_approved_entry"
SOURCE_ACCOUNTING          = "connected_accounting_system"
SOURCE_EXTERNAL_BILLING    = "external_billing_provider"
SOURCE_IMPORT              = "import"
SOURCE_FUTURE_INTEGRATION  = "future_integration"

COLLECTION_SOURCES = (SOURCE_STRIPE, SOURCE_MANUAL_APPROVED, SOURCE_ACCOUNTING,
                      SOURCE_EXTERNAL_BILLING, SOURCE_IMPORT,
                      SOURCE_FUTURE_INTEGRATION)

# Sources that can actually produce a record this platform is willing to settle
# from today. The others are modelled so an agreement can NAME them; they do
# not silently become a feed.
IMPLEMENTED_COLLECTION_SOURCES = (SOURCE_STRIPE, SOURCE_MANUAL_APPROVED)

COLLECTION_DRAFT     = "draft"
COLLECTION_SUBMITTED = "submitted"
COLLECTION_APPROVED  = "approved"
COLLECTION_REJECTED  = "rejected"

COLLECTION_STATUSES = (COLLECTION_DRAFT, COLLECTION_SUBMITTED,
                       COLLECTION_APPROVED, COLLECTION_REJECTED)

# ── attribution ─────────────────────────────────────────────────────────────
ATTR_ALL_ELIGIBLE      = "all_eligible_collections"
ATTR_PLATFORM_ONLY     = "platform_attributed_only"
ATTR_SPECIFIC_SERVICE  = "specific_service"
ATTR_CUSTOM_RULE       = "custom_rule"
ATTR_UNKNOWN_REVIEW    = "unknown_review_required"

ATTRIBUTION_RULES = (ATTR_ALL_ELIGIBLE, ATTR_PLATFORM_ONLY,
                     ATTR_SPECIFIC_SERVICE, ATTR_CUSTOM_RULE)

# A record may carry the review state; an agreement rule may not.
ATTRIBUTION_RECORD_STATES = ATTRIBUTION_RULES + (ATTR_UNKNOWN_REVIEW,)

# ── settlement ──────────────────────────────────────────────────────────────
SETTLE_DRAFT           = "draft"
SETTLE_CALCULATED      = "calculated"
SETTLE_REVIEW_REQUIRED = "review_required"
SETTLE_APPROVED        = "approved"
SETTLE_SETTLED         = "settled"

SETTLEMENT_STATUSES = (SETTLE_DRAFT, SETTLE_CALCULATED, SETTLE_REVIEW_REQUIRED,
                       SETTLE_APPROVED, SETTLE_SETTLED)

# ── onboarding milestone override modes ─────────────────────────────────────
#
# COMPLETED_NORMALLY is here for symmetry and is what the ordinary path already
# means; it is recorded only when somebody explicitly restores a step that had
# been overridden.
MODE_COMPLETED_NORMALLY   = "completed_normally"
MODE_COMPLETED_PREVIOUSLY = "completed_previously"
MODE_NOT_APPLICABLE       = "not_applicable"
MODE_WAIVED               = "waived"

OVERRIDE_MODES = (MODE_COMPLETED_NORMALLY, MODE_COMPLETED_PREVIOUSLY,
                  MODE_NOT_APPLICABLE, MODE_WAIVED)

OVERRIDE_MODE_LABELS = {
    MODE_COMPLETED_NORMALLY:   "Completed",
    MODE_COMPLETED_PREVIOUSLY: "Completed previously",
    MODE_NOT_APPLICABLE:       "Not applicable",
    MODE_WAIVED:               "Waived",
}

# Modes that SATISFY a requirement. Every one of them is a decision somebody
# made and signed their name to — none of them is the software deciding a step
# did not matter.
OVERRIDE_SATISFYING_MODES = (MODE_COMPLETED_NORMALLY, MODE_COMPLETED_PREVIOUSLY,
                             MODE_NOT_APPLICABLE, MODE_WAIVED)

# What an override can be attached to. Kinds map onto rows that already exist
# in the launch engine, plus the two pre-sale facts that live on the
# opportunity and have no onboarding row of their own.
ITEM_MILESTONE   = "milestone"
ITEM_INTEGRATION = "integration"
ITEM_CHECK       = "check"
ITEM_TRAINING    = "training"
ITEM_INTAKE_STEP = "intake_step"
ITEM_DEMO        = "demo"
ITEM_DISCOVERY   = "discovery"

OVERRIDE_ITEM_KINDS = (ITEM_MILESTONE, ITEM_INTEGRATION, ITEM_CHECK,
                       ITEM_TRAINING, ITEM_INTAKE_STEP, ITEM_DEMO,
                       ITEM_DISCOVERY)


# ════════════════════════════════════════════════════════════════════════════
# THE AGREEMENT
# ════════════════════════════════════════════════════════════════════════════

class CommercialAgreement(Base):
    """One commercial arrangement between a brand and a customer.

    Scoped by platform (the white-label brand) and, once the customer tenant
    exists, by organization. Both are indexed and both are filtered on every
    read path — an agreement is the most commercially sensitive row this
    platform holds, and a cross-tenant read of one would disclose another
    company's economics.

    `organization_id` is NULLABLE on purpose. An arrangement can be agreed
    before the tenant is provisioned; the agreement is then bound to the
    opportunity and picks up its organization when provisioning happens.
    """
    __tablename__ = "commercial_agreements"

    id = Column(String, primary_key=True, default=gen_uuid)

    # ── where it lives ──
    platform_id = Column(String, ForeignKey("platforms.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    brand_sales_org_id = Column(String, nullable=True, index=True)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"),
                             nullable=True, index=True)
    opportunity_id = Column(String, nullable=True, index=True)
    implementation_id = Column(String, nullable=True, index=True)

    # ── what it is ──
    agreement_type = Column(String, nullable=False, index=True)   # AGREEMENT_TYPES
    status = Column(String, nullable=False, default=AG_DRAFT, index=True)
    name = Column(String, nullable=True)
    reference = Column(String, nullable=True)
    currency = Column(String, nullable=False, default="usd")

    effective_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True)

    # ── T2 linkage, never T2 duplication ──
    #
    # A hybrid agreement's fixed side IS a catalogue purchase / subscription
    # and stays there. This column records that the link exists so an internal
    # reader can follow it; no price, interval or Stripe id is copied.
    references_t2_subscription = Column(Boolean, nullable=False, default=False)
    t2_note = Column(Text, nullable=True)

    # ── document ──
    #
    # Deliberately a reference, not a vault. Uploaded files live in the launch
    # engine's existing store (`implementation_intake_files`); an externally
    # held document is named here. A document does NOT approve an agreement —
    # see `approved_by_user_id`.
    document_file_id = Column(String, nullable=True)
    document_reference = Column(String, nullable=True)
    document_note = Column(Text, nullable=True)

    # ── approval / lifecycle stamps ──
    approved_by_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    approval_note = Column(Text, nullable=True)

    activated_by_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    activated_at = Column(DateTime, nullable=True)

    suspended_by_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    suspended_at = Column(DateTime, nullable=True)
    suspension_reason = Column(Text, nullable=True)

    ended_by_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    ended_at = Column(DateTime, nullable=True)
    end_reason = Column(Text, nullable=True)

    notes = Column(Text, nullable=True)

    # Optimistic concurrency. Two people editing one term sheet is the normal
    # case here (a seller and a finance approver), and the loser of a race must
    # be told rather than silently overwritten.
    version = Column(Integer, nullable=False, default=1)

    created_by_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_commercial_agreement_org_status", "organization_id", "status"),
        Index("ix_commercial_agreement_platform_status", "platform_id", "status"),
    )


class CommercialParty(Base):
    """A side of the arrangement.

    Two parties is the common case and nothing assumes it. A party is named
    rather than inferred, because "the provider side" and "the customer side"
    are not always the same as "the brand" and "the tenant" — a deal can name a
    third party who is neither, and a party's name in a term sheet is rarely
    the name of an object this platform already holds.
    """
    __tablename__ = "commercial_agreement_parties"

    id = Column(String, primary_key=True, default=gen_uuid)
    agreement_id = Column(String,
                          ForeignKey("commercial_agreements.id", ondelete="CASCADE"),
                          nullable=False, index=True)

    party_key = Column(String, nullable=False)      # stable within one agreement
    display_name = Column(String, nullable=False)
    party_type = Column(String, nullable=False)     # PARTY_TYPES

    organization_id = Column(String, nullable=True)
    brand_sales_org_id = Column(String, nullable=True)
    external_reference = Column(String, nullable=True)

    # Whether this party is on the receiving end of a settlement. Recorded, not
    # acted on: nothing in this phase moves money to anybody.
    is_payee = Column(Boolean, nullable=False, default=True)

    position = Column(Integer, nullable=False, default=0)
    note = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("agreement_id", "party_key",
                         name="uq_commercial_party_key"),
    )


class CommercialAllocation(Base):
    """One party's share of the basis.

    `percent` is NULLABLE, and that is the whole reason this is a table rather
    than two columns on the agreement: a deal can name three parties and know
    only two of the percentages. An allocation with a NULL percent is UNKNOWN
    and blocks activation; it never reads as zero.

    `fixed_amount_cents` exists for a deal whose split is partly a flat figure
    off the top. `tier_json` carries a tiered schedule where one is agreed; it
    is stored and displayed, and the settlement engine refuses to calculate
    from a tier shape it does not implement rather than guessing.
    """
    __tablename__ = "commercial_revenue_shares"

    id = Column(String, primary_key=True, default=gen_uuid)
    agreement_id = Column(String,
                          ForeignKey("commercial_agreements.id", ondelete="CASCADE"),
                          nullable=False, index=True)
    party_id = Column(String,
                      ForeignKey("commercial_agreement_parties.id", ondelete="CASCADE"),
                      nullable=False, index=True)

    percent = Column(Numeric(9, 6), nullable=True)
    fixed_amount_cents = Column(Integer, nullable=True)
    tier_json = Column(JSON, nullable=True)

    position = Column(Integer, nullable=False, default=0)
    note = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("agreement_id", "party_id",
                         name="uq_commercial_allocation_party"),
    )


class CommercialTerm(Base):
    """One answerable fact about the arrangement, and whether anybody answered.

    The value lives in `value_json` as `{"value": ...}` so that a stored value
    of `None`, `0` or `false` is distinguishable from no row at all, and so a
    multi-select answer needs no second column.

    `label` and `definition_version` are SNAPSHOTS of the question definition
    as it stood when this was answered. Definitions are configuration and can
    be edited; a term already answered must keep saying what was actually asked.
    """
    __tablename__ = "commercial_terms"

    id = Column(String, primary_key=True, default=gen_uuid)
    agreement_id = Column(String,
                          ForeignKey("commercial_agreements.id", ondelete="CASCADE"),
                          nullable=False, index=True)

    key = Column(String, nullable=False)
    state = Column(String, nullable=False, default=TERM_UNKNOWN)   # TERM_STATES
    value_json = Column(JSON, nullable=True)

    label = Column(String, nullable=True)
    definition_version = Column(Integer, nullable=True)
    required_for_activation = Column(Boolean, nullable=False, default=False)

    # Where the answer came from: the customer's own onboarding screen, an
    # internal operator, or an import. Kept because "the customer said so" and
    # "we assumed" are different provenances for the same number.
    source = Column(String, nullable=True)

    answered_by_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    answered_at = Column(DateTime, nullable=True)
    note = Column(Text, nullable=True)

    revision = Column(Integer, nullable=False, default=1)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("agreement_id", "key", name="uq_commercial_term_key"),
        Index("ix_commercial_term_state", "agreement_id", "state"),
    )


class CommercialQuestionDefinition(Base):
    """A question the platform knows how to ask about a commercial structure.

    Configuration, not code. A row with `platform_id = NULL` is a platform
    default available to every brand; a row with a platform_id is that brand's
    own question, or its override of a default by the same key.

    This is what keeps the four questions a revenue-share deal needs out of a
    JSX file. A future structure that needs different questions gets rows,
    not a deploy.
    """
    __tablename__ = "commercial_question_definitions"

    id = Column(String, primary_key=True, default=gen_uuid)
    platform_id = Column(String, ForeignKey("platforms.id", ondelete="CASCADE"),
                         nullable=True, index=True)

    key = Column(String, nullable=False, index=True)
    label = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    help_text = Column(Text, nullable=True)

    # select | multiselect | text | textarea | money | percent | integer |
    # boolean | date  — validated in app/services/commercial/questions.py
    kind = Column(String, nullable=False, default="select")

    # Which agreement types this question applies to. NULL/empty = all types.
    applies_to_types = Column(JSON, nullable=True)

    # customer | internal | both — who is asked. A percentage split is not a
    # question to put in front of the customer's own onboarding screen.
    audience = Column(String, nullable=False, default="both")

    required_for_activation = Column(Boolean, nullable=False, default=False)
    allowed_values = Column(JSON, nullable=True)      # [{value,label,description?}]
    validation = Column(JSON, nullable=True)          # {min,max,max_length,...}
    default_value = Column(JSON, nullable=True)       # {"value": ...} or NULL

    display_order = Column(Integer, nullable=False, default=0)
    version = Column(Integer, nullable=False, default=1)
    is_active = Column(Boolean, nullable=False, default=True)

    created_by_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("platform_id", "key", name="uq_commercial_question_key"),
    )


class CommercialCollectionRecord(Base):
    """An authoritative statement of what was collected in a period.

    A settlement may only be calculated from APPROVED records. Nothing in this
    platform manufactures one: a Stripe-sourced record is built from
    `billing_payments` rows that already exist, and a manual record is typed by
    a named person and approved by another.

    `gross_cents` is NOT NULL because a record that cannot state its own figure
    is not a record; an unknown period simply has no row, and settlement
    refuses for want of coverage rather than calculating against zero.
    """
    __tablename__ = "commercial_collection_records"

    id = Column(String, primary_key=True, default=gen_uuid)
    agreement_id = Column(String,
                          ForeignKey("commercial_agreements.id", ondelete="CASCADE"),
                          nullable=False, index=True)
    organization_id = Column(String, nullable=True, index=True)

    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)

    source = Column(String, nullable=False)            # COLLECTION_SOURCES
    source_reference = Column(String, nullable=True)

    currency = Column(String, nullable=False, default="usd")
    gross_cents = Column(Integer, nullable=False)

    # Adjustments are NULLABLE and that is deliberate: "we have not worked out
    # the refunds for this period" is not "there were no refunds".
    adjustments_cents = Column(Integer, nullable=True)
    adjustments_note = Column(Text, nullable=True)

    attribution_state = Column(String, nullable=False, default=ATTR_UNKNOWN_REVIEW)
    attribution_note = Column(Text, nullable=True)

    status = Column(String, nullable=False, default=COLLECTION_DRAFT, index=True)
    submitted_by_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    submitted_at = Column(DateTime, nullable=True)
    approved_by_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    rejected_reason = Column(Text, nullable=True)

    note = Column(Text, nullable=True)
    created_by_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_commercial_collection_period",
              "agreement_id", "period_start", "period_end"),
    )


class CommercialSettlement(Base):
    """A CALCULATION, and never a payment.

    `SETTLE_SETTLED` exists in the vocabulary because a settlement that has
    been paid is a real state this platform will one day need. Nothing in this
    phase writes it: there is no approved payout infrastructure behind it, and
    `app/services/commercial/settlement.py` refuses distribution outright.

    `blocked_reasons_json` is populated on a refusal so the screen can say WHY
    a period will not calculate instead of showing an empty statement.
    """
    __tablename__ = "commercial_settlements"

    id = Column(String, primary_key=True, default=gen_uuid)
    agreement_id = Column(String,
                          ForeignKey("commercial_agreements.id", ondelete="CASCADE"),
                          nullable=False, index=True)
    organization_id = Column(String, nullable=True, index=True)

    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)

    status = Column(String, nullable=False, default=SETTLE_DRAFT, index=True)

    currency = Column(String, nullable=False, default="usd")
    basis_cents = Column(Integer, nullable=True)
    gross_cents = Column(Integer, nullable=True)
    adjustments_cents = Column(Integer, nullable=True)

    calculation_json = Column(JSON, nullable=True)     # per-party detail
    data_source_json = Column(JSON, nullable=True)     # which records were used
    blocked_reasons_json = Column(JSON, nullable=True)

    calculated_by_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    calculated_at = Column(DateTime, nullable=True)
    approved_by_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)

    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_commercial_settlement_period",
              "agreement_id", "period_start", "period_end"),
    )


class OnboardingMilestoneOverride(Base):
    """"This step was satisfied, and here is who said so and why."

    The gap this closes: a customer who was given a demo before the onboarding
    workflow existed cannot tick a box that did not exist at the time, and the
    honest options were previously to lie about a completion date or to make
    them sit through a second demo to satisfy a database.

    Every non-normal completion carries an actor, a timestamp of the DECISION
    (not a fabricated timestamp of the work), a reason, and an audit entry.
    `previously_completed_on` is nullable with `previously_completed_date_known`
    beside it, because "about two weeks ago" is the truth on a lot of these and
    inventing a date to fill a NOT NULL column is exactly what this table is
    here to stop.
    """
    __tablename__ = "onboarding_milestone_overrides"

    id = Column(String, primary_key=True, default=gen_uuid)
    implementation_id = Column(String, nullable=False, index=True)
    organization_id = Column(String, nullable=False, index=True)

    item_kind = Column(String, nullable=False)     # OVERRIDE_ITEM_KINDS
    item_key = Column(String, nullable=False)
    item_label = Column(String, nullable=True)

    mode = Column(String, nullable=False)          # OVERRIDE_MODES
    reason = Column(Text, nullable=False)
    note = Column(Text, nullable=True)

    previously_completed_on = Column(Date, nullable=True)
    previously_completed_date_known = Column(Boolean, nullable=False, default=False)

    decided_by_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    decided_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Superseded rather than deleted: the history of what was waived and by
    # whom is the point of the table.
    is_active = Column(Boolean, nullable=False, default=True)
    superseded_at = Column(DateTime, nullable=True)
    superseded_by_user_id = Column(String, ForeignKey("users.id"), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_onboarding_override_item",
              "implementation_id", "item_kind", "item_key"),
        Index("ix_onboarding_override_active",
              "implementation_id", "is_active"),
    )
