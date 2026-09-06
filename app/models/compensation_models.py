"""WHAT A DEAL PAYS THE PEOPLE WHO SOLD IT.

FOUR STATES, AND CONFLATING ANY TWO OF THEM IS THE FAILURE THIS FILE PREVENTS:

  PROJECTED   an OPEN opportunity, showing what it would pay if it closed on
              today's terms. NEVER STORED IN THIS FILE. It is computed on
              demand from the live deal, because the moment a projection is
              persisted it starts looking like a liability, and a pipeline
              report becomes a payroll report by accident.
  EARNED      the deal is Won AND the first required customer payment has
              actually been collected. Only here does a row appear.
  PAYABLE     earned, and the holdback has elapsed. Funds have had time to
              clear.
  PAID        money left the business, and it is recorded.

WON IS NOT EARNED. Somebody changing a stage to Won does not create money;
collected funds do. `CompensationEntry` cannot be created without a collection
reference for exactly that reason.

RATES ARE DATA, AND HISTORY IS FROZEN
-------------------------------------
A plan is versioned by effective date, and an entry snapshots the numbers that
produced it — rate, basis, and the plan it came from. Repricing a plan next
quarter must not silently rewrite what somebody was paid last quarter, which is
what happens when a payout is stored as a foreign key to a mutable rate.

NOTHING HERE INVENTS A RATE. Every amount and percentage is nullable and
unconfigured by default. An unconfigured package pays nothing and SAYS it is
unconfigured, rather than defaulting to a number nobody agreed to.
"""

from datetime import datetime

from sqlalchemy import (Boolean, Column, Date, DateTime, ForeignKey, Index,
                        Integer, Numeric, String, Text, UniqueConstraint)

from app.models.models import Base, gen_uuid

# ── How an amount is computed ────────────────────────────────────────────────
# Deliberately a vocabulary rather than one percentage field: the direct package
# structure pays a FIXED dollar amount, the multi-tenant SaaS structure pays a
# PERCENTAGE of collected payments, and a plan has to express both without the
# engine knowing which brand it is serving.
BASIS_FIXED            = "fixed"              # flat dollars per closed deal
BASIS_PCT_SETUP        = "pct_implementation"  # % of the one-time fee
BASIS_PCT_MRR          = "pct_mrr"             # % of the monthly rate
BASIS_PCT_TCV          = "pct_tcv"             # % of total contract value
BASIS_PCT_COLLECTED    = "pct_collected"       # % of each eligible payment
BASES = (BASIS_FIXED, BASIS_PCT_SETUP, BASIS_PCT_MRR, BASIS_PCT_TCV,
         BASIS_PCT_COLLECTED)

BASIS_LABELS = {
    BASIS_FIXED:         "Fixed amount",
    BASIS_PCT_SETUP:     "% of implementation fee",
    BASIS_PCT_MRR:       "% of monthly recurring",
    BASIS_PCT_TCV:       "% of total contract value",
    BASIS_PCT_COLLECTED: "% of collected payments",
}

# ── Who is being paid ────────────────────────────────────────────────────────
PAYEE_SELLER   = "seller"    # the opportunity owner
PAYEE_OVERRIDE = "override"  # somebody above them in the org chart
PAYEE_KINDS = (PAYEE_SELLER, PAYEE_OVERRIDE)

# ── Which payments a rule is eligible against ────────────────────────────────
ELIGIBLE_INITIAL   = "initial"    # the first required payment only
ELIGIBLE_RECURRING = "recurring"  # each recurring payment
ELIGIBLE_BOTH      = "both"
ELIGIBILITIES = (ELIGIBLE_INITIAL, ELIGIBLE_RECURRING, ELIGIBLE_BOTH)

# ── Which kind of deal a rule applies to ─────────────────────────────────────
DEAL_STANDARD = "standard"
DEAL_CUSTOM   = "custom"
DEAL_KINDS = (DEAL_STANDARD, DEAL_CUSTOM)

# ── Entry states ─────────────────────────────────────────────────────────────
# PROJECTED is deliberately absent: it is not a row. See the module docstring.
COMP_EARNED  = "earned"
COMP_PAYABLE = "payable"
COMP_PAID    = "paid"
COMP_VOID    = "void"      # the deal unwound before payment
COMP_STATES  = (COMP_EARNED, COMP_PAYABLE, COMP_PAID, COMP_VOID)

COMP_STATE_LABELS = {
    COMP_EARNED:  "Earned",
    COMP_PAYABLE: "Payable",
    COMP_PAID:    "Paid",
    COMP_VOID:    "Void",
}

# States a PROJECTION can report. Not stored — returned by the service so a
# screen can say why a number is not money yet.
PROJ_PROJECTED        = "projected"
PROJ_PENDING_APPROVAL = "pending_approval"
PROJ_UNCONFIGURED     = "unconfigured"
PROJECTION_LABELS = {
    PROJ_PROJECTED:        "Projected",
    PROJ_PENDING_APPROVAL: "Pending approval",
    PROJ_UNCONFIGURED:     "No plan configured",
}

# The EvoSys direct-sales holdback: commissions are paid after funds have had
# two weeks to clear. A DEFAULT, not a law — every plan carries its own.
DEFAULT_HOLDBACK_DAYS = 14


class CompensationPlan(Base):
    """A dated set of rules for one sales organization.

    Versioned by `effective_from` rather than edited in place. An entry records
    which plan paid it, so a plan superseded in March still explains a January
    payout instead of being overwritten by the rate that replaced it.
    """
    __tablename__ = "compensation_plans"

    id = Column(String, primary_key=True, default=gen_uuid)
    # NULL = the platform-wide default plan.
    brand_sales_org_id = Column(String,
                                ForeignKey("brand_sales_orgs.id", ondelete="CASCADE"),
                                nullable=True, index=True)
    name = Column(String, nullable=False)

    effective_from = Column(Date, nullable=False)
    # NULL = still current. A plan is retired by dating it, never deleted:
    # deleting it would orphan the explanation for every payout it produced.
    effective_to   = Column(Date, nullable=True)

    # Days between EARNED and PAYABLE — time for funds to clear.
    holdback_days = Column(Integer, default=DEFAULT_HOLDBACK_DAYS, nullable=False)

    # How many override levels above the seller this plan will ever pay. A
    # ceiling on the walk up the org chart, so a deep reporting line cannot
    # quietly manufacture six layers of expense.
    max_override_levels = Column(Integer, default=1, nullable=False)

    is_active  = Column(Boolean, default=True, nullable=False)
    note       = Column(Text, nullable=True)
    created_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_comp_plans_scope_active", "brand_sales_org_id", "is_active"),
    )


class CompensationRule(Base):
    """One line of a plan: who gets paid, on what, how much.

    A rule with neither `amount` nor `percent` set is UNCONFIGURED and pays
    nothing. That is the deliberate resting state for a package whose rate
    nobody has told this system yet — silence, not a guess.
    """
    __tablename__ = "compensation_rules"

    id      = Column(String, primary_key=True, default=gen_uuid)
    plan_id = Column(String, ForeignKey("compensation_plans.id", ondelete="CASCADE"),
                     nullable=False, index=True)

    # ── What it applies to. NULL means "any". ───────────────────────────────
    package_id = Column(String, ForeignKey("brand_packages.id", ondelete="CASCADE"),
                        nullable=True, index=True)
    deal_kind  = Column(String, nullable=True)   # DEAL_KINDS, or NULL for both

    # ── Who it pays ─────────────────────────────────────────────────────────
    payee_kind     = Column(String, nullable=False, default=PAYEE_SELLER)
    # 1 = the seller's direct manager, 2 = that person's manager, and so on.
    # Only meaningful for PAYEE_OVERRIDE.
    override_level = Column(Integer, nullable=True)

    # ── How much ────────────────────────────────────────────────────────────
    basis   = Column(String, nullable=False)
    amount  = Column(Numeric(12, 2), nullable=True)   # for BASIS_FIXED
    percent = Column(Numeric(6, 3), nullable=True)    # for every pct_ basis

    # For BASIS_PCT_MRR: how many months of the recurring rate this pays on.
    # NULL means the whole term, which for a month-to-month deal is nothing
    # bounded - so a month-to-month deal with a NULL here pays on one month
    # rather than on an imaginary contract length.
    recurring_months = Column(Integer, nullable=True)

    eligible_payment = Column(String, nullable=True, default=ELIGIBLE_INITIAL)

    # Per-payout ceiling. The PLAN-wide, per-package ceiling is separate: see
    # CompensationPackageCap, which is what "total payout must never exceed
    # $800 on the $1,497 package" actually means.
    max_amount = Column(Numeric(12, 2), nullable=True)

    sort_order = Column(Integer, default=0, nullable=False)
    is_active  = Column(Boolean, default=True, nullable=False)
    note       = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_comp_rules_plan_pkg", "plan_id", "package_id", "is_active"),
    )


class CompensationPackageCap(Base):
    """The most this plan will pay out IN TOTAL on one package, across everyone.

    Separate from `CompensationRule.max_amount` because it is a different claim.
    A per-rule cap limits one person's cheque; this limits the whole deal - the
    seller's commission plus every override together. "$500 to the rep, $100 to
    the manager, never more than $800 all in" needs both kinds, and collapsing
    them into one field is how the third override level quietly breaks the cap.
    """
    __tablename__ = "compensation_package_caps"

    id      = Column(String, primary_key=True, default=gen_uuid)
    plan_id = Column(String, ForeignKey("compensation_plans.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    package_id = Column(String, ForeignKey("brand_packages.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    max_total_payout = Column(Numeric(12, 2), nullable=False)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("plan_id", "package_id", name="uq_comp_cap_plan_package"),
    )


class CompensationEntry(Base):
    """MONEY THE BUSINESS ACTUALLY OWES SOMEBODY.

    A row here only ever exists because a deal was Won AND a required payment
    was collected. There is no `projected` state and no nullable collection
    reference, so an open pipeline physically cannot produce one.

    The rate fields are a SNAPSHOT, not a join. `plan_id` records which plan
    decided this, but `basis`, `rate_percent` and `rate_amount` record what it
    actually said at the time - so a later edit to that plan explains history
    rather than rewriting it.
    """
    __tablename__ = "compensation_entries"

    id = Column(String, primary_key=True, default=gen_uuid)

    brand_sales_org_id = Column(String,
                                ForeignKey("brand_sales_orgs.id", ondelete="CASCADE"),
                                nullable=False, index=True)
    opportunity_id = Column(String, ForeignKey("opportunities.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    # Who is paid.
    payee_user_id  = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    payee_kind     = Column(String, nullable=False)
    override_level = Column(Integer, nullable=True)

    # What decided it, and what it said. Both, for the reason in the docstring.
    plan_id      = Column(String, ForeignKey("compensation_plans.id"), nullable=True)
    rule_id      = Column(String, ForeignKey("compensation_rules.id"), nullable=True)
    basis        = Column(String, nullable=False)
    rate_percent = Column(Numeric(6, 3), nullable=True)
    rate_amount  = Column(Numeric(12, 2), nullable=True)

    # The deal economics this was computed against, frozen. Without these a
    # payout cannot be re-checked after the deal is repriced.
    basis_implementation_fee = Column(Numeric(12, 2), nullable=True)
    basis_mrr                = Column(Numeric(12, 2), nullable=True)
    basis_term_months        = Column(Integer, nullable=True)
    basis_tcv                = Column(Numeric(12, 2), nullable=True)
    basis_collected_amount   = Column(Numeric(12, 2), nullable=True)

    amount   = Column(Numeric(12, 2), nullable=False)
    currency = Column(String, default="USD", nullable=False)
    # Set when a package cap reduced this payout, so a short cheque is
    # explainable without re-deriving the whole plan.
    capped_from_amount = Column(Numeric(12, 2), nullable=True)

    state = Column(String, default=COMP_EARNED, nullable=False, index=True)

    # WHY THIS IS EARNED. Not nullable in spirit: the service refuses to create
    # an entry without one. Kept as a free string because the collection may be
    # a Stripe payment, a bank transfer or a recorded cheque.
    collection_reference = Column(String, nullable=False)
    collected_at         = Column(DateTime, nullable=False)
    earned_at            = Column(DateTime, default=datetime.utcnow, nullable=False)
    # earned_at + the plan's holdback. Computed on creation and stored, because
    # a later plan edit must not move a payout date somebody was promised.
    payable_at           = Column(DateTime, nullable=True)
    paid_at              = Column(DateTime, nullable=True)
    payment_reference    = Column(String, nullable=True)

    note       = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        # One payout per person per level per collection. Re-running the earn
        # step on the same collected payment must not pay anybody twice.
        UniqueConstraint("opportunity_id", "payee_user_id", "override_level",
                         "collection_reference", name="uq_comp_entry_once"),
        Index("ix_comp_entries_brand_state", "brand_sales_org_id", "state"),
        Index("ix_comp_entries_payee_state", "payee_user_id", "state"),
    )


class StageProbability(Base):
    """The chance a deal at this stage closes, for weighted pipeline value.

    A table and not a constant because it is a commercial judgement that
    differs by brand and gets tuned from real win rates. UNCONFIGURED BY
    DEFAULT: with no rows, weighted revenue reports "not configured" rather
    than applying invented odds to real money.
    """
    __tablename__ = "stage_probabilities"

    id = Column(String, primary_key=True, default=gen_uuid)
    brand_sales_org_id = Column(String,
                                ForeignKey("brand_sales_orgs.id", ondelete="CASCADE"),
                                nullable=True, index=True)
    stage = Column(String, nullable=False)
    probability_pct = Column(Numeric(5, 2), nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("brand_sales_org_id", "stage", name="uq_stage_prob_scope"),
    )
