"""
Sales Workspace models — brand sales tenancy, memberships, packages, opportunities.

APPROVED ARCHITECTURE (see claude/SALES_WORKSPACE_ARCHITECTURE.md, Aug 25 2026).

THE RULE THIS FILE EXISTS TO ENFORCE
------------------------------------
    "The EvoSys Pro sales organization is NOT an EvoSys Pro customer tenant.
     Brand sales users sell the product; customer organizations use the product.
     These are separate tenancy and permission domains."   — Mike

        AdvisorFlow (god)
          └── Platform                    EvoSys Pro | BookaBoost | Harmony Hustle
                ├── BrandSalesOrg         SELLS the product  (this file)
                │     └── Opportunity → Won → provisions ↓
                └── Organization          USES the product   (models.py, untouched)

A BrandSalesOrg is deliberately NOT an `organizations` row. Customer tenants must
never inherit the sales workspace, and sales staff must never inherit customer
tenant data. Two domains, two tables.

WHY MEMBERSHIPS AND NOT users.role
----------------------------------
`users.role` (god_admin | super_admin | org_admin | advisor) is load-bearing in
every guard in app/deps.py and every route in App.jsx. It is NOT repurposed here.

This layer is purely ADDITIVE: a user may hold several memberships in different
scopes, so a Sales Manager can also sell personally, and a person can hold a
customer-tenant role elsewhere without conflict. Nothing in the existing app
changes behaviour because of this file.

NOTHING IN THIS MODULE IS BILLING. Sales packages are a separate catalog from the
Stripe plans in billing_router (see BrandPackage docstring).
"""

from sqlalchemy import (
    Column, String, Boolean, DateTime, Date, ForeignKey, Text, Integer,
    Numeric, UniqueConstraint, Index,
)
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.models.models import Base


def gen_uuid():
    return str(uuid.uuid4())


# ── Scope + role vocabularies ────────────────────────────────────────────────
# Kept as plain strings (the codebase convention — see the VARCHAR rule in
# BACKEND_PASSDOWN: never call .value on these) with module constants so typos
# fail review rather than silently creating a role nobody can grant.

SCOPE_PLATFORM        = "platform"
SCOPE_BRAND_SALES_ORG = "brand_sales_org"
SCOPE_CUSTOMER_ORG    = "customer_org"
SCOPE_TYPES = (SCOPE_PLATFORM, SCOPE_BRAND_SALES_ORG, SCOPE_CUSTOMER_ORG)

ROLE_SALES_MANAGER = "sales_manager"
ROLE_SALES_REP     = "sales_rep"
BRAND_SALES_ROLES  = (ROLE_SALES_MANAGER, ROLE_SALES_REP)


class Membership(Base):
    """A contextual role assignment. Additive to users.role, never a replacement.

    One user may hold many rows: EvoSys Pro Sales Manager here, something else
    elsewhere. Resolution order used by the guards:
        god_admin / super_admin / org_admin / advisor  → users.role  (unchanged)
        sales_manager / sales_rep                      → this table
    """
    __tablename__ = "memberships"

    id         = Column(String, primary_key=True, default=gen_uuid)
    user_id    = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    # Polymorphic scope. Deliberately NOT a ForeignKey — scope_id points at
    # different tables depending on scope_type, and a real FK would force one.
    # Integrity is enforced in the service layer, which is the tradeoff for
    # keeping customer orgs and brand sales orgs in genuinely separate domains.
    scope_type = Column(String, nullable=False)   # SCOPE_TYPES
    scope_id   = Column(String, nullable=False)
    role       = Column(String, nullable=False)   # e.g. BRAND_SALES_ROLES

    is_active  = Column(Boolean, default=True, nullable=False)
    granted_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        # A user holds a given role in a given scope at most once.
        UniqueConstraint("user_id", "scope_type", "scope_id", "role",
                         name="uq_membership_user_scope_role"),
        Index("ix_memberships_user", "user_id", "is_active"),
        Index("ix_memberships_scope", "scope_type", "scope_id", "is_active"),
    )


class BrandSalesOrg(Base):
    """The sales team that sells ONE brand. Sits under a Platform, beside — never
    inside — that platform's customer organizations.

    Reusable across brands from day one (decision #1): EvoSys Pro Sales,
    BookaBoost Sales, Harmony Hustle Sales. Nothing here hardcodes a brand.
    """
    __tablename__ = "brand_sales_orgs"

    id          = Column(String, primary_key=True, default=gen_uuid)
    platform_id = Column(String, ForeignKey("platforms.id", ondelete="CASCADE"), nullable=False)
    name        = Column(String, nullable=False)          # "EvoSys Pro Sales"
    slug        = Column(String, nullable=False, unique=True)
    timezone    = Column(String, default="America/Chicago")  # default only; per-user wins
    is_active   = Column(Boolean, default=True, nullable=False)
    created_at  = Column(DateTime, default=datetime.utcnow)
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_brand_sales_orgs_platform", "platform_id", "is_active"),
    )


class BrandPackage(Base):
    """What the sales team SELLS. A separate catalog from the Stripe billing plans.

    Decision #8 — explicit: EvoSys Pro sells Starter $1,497 / Growth $2,495 /
    Professional $4,995 / Multi-Tenant Custom. The Stripe plans in billing_router
    are $497 / $997 / $1,997. These are NOT the same products and must NOT be
    mapped to each other blindly.

    `billing_plan_key` is the deliberate, later connection point to billing. It
    stays NULL until someone decides what a sold package should charge, and no
    code may infer it.
    """
    __tablename__ = "brand_packages"

    id            = Column(String, primary_key=True, default=gen_uuid)
    platform_id   = Column(String, ForeignKey("platforms.id", ondelete="CASCADE"), nullable=False)
    key           = Column(String, nullable=False)   # starter | growth | professional | multi_tenant
    name          = Column(String, nullable=False)
    description   = Column(Text, nullable=True)

    # Numeric, not float — this is money. NULL price = quoted per deal (custom).
    price         = Column(Numeric(12, 2), nullable=True)
    currency      = Column(String, default="USD")
    billing_period = Column(String, default="monthly")   # monthly | annual | one_time | custom
    setup_fee     = Column(Numeric(12, 2), nullable=True)

    is_custom     = Column(Boolean, default=False, nullable=False)  # Multi-Tenant/Custom
    sort_order    = Column(Integer, default=0)
    is_active     = Column(Boolean, default=True, nullable=False)

    # Deliberate, later link to billing. Never inferred (decision #8).
    billing_plan_key = Column(String, nullable=True)

    created_at    = Column(DateTime, default=datetime.utcnow)
    updated_at    = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("platform_id", "key", name="uq_brand_package_platform_key"),
        Index("ix_brand_packages_platform", "platform_id", "is_active"),
    )


# ── Opportunity lifecycle ────────────────────────────────────────────────────
# Decision #6: a NEW table. The existing `leads` table is funeral-shaped
# (tier = pre_need | at_need | imminent) and holds 14,735 rows of unrelated
# customer data. Overloading it would corrupt both domains.

STAGE_PROSPECT   = "prospect"
STAGE_CONTACTED  = "contacted"
STAGE_DISCOVERY  = "discovery"
STAGE_DEMO_BUILD = "demo_build"
STAGE_PROPOSAL   = "demo_proposal"
STAGE_CLOSING    = "closing"
STAGE_WON        = "won"
STAGE_ONBOARDING = "onboarding"
STAGE_LIVE       = "live"
STAGE_LOST       = "lost"

# ORDERED. The pipeline board renders in this order and "days in stage" and
# forward/backward stage moves are computed from the index, so do not reorder
# without checking both. `lost` is deliberately NOT in here: it is an exit, not
# a column position, and lives beside the board.
OPPORTUNITY_STAGES = (
    STAGE_PROSPECT, STAGE_CONTACTED, STAGE_DISCOVERY, STAGE_DEMO_BUILD,
    STAGE_PROPOSAL, STAGE_CLOSING, STAGE_WON, STAGE_ONBOARDING, STAGE_LIVE,
)

ALL_STAGES = OPPORTUNITY_STAGES + (STAGE_LOST,)

# Human labels live with the vocabulary so the API and the UI cannot drift.
STAGE_LABELS = {
    STAGE_PROSPECT:   "Prospect",
    STAGE_CONTACTED:  "Contacted / Qualified",
    STAGE_DISCOVERY:  "Discovery",
    STAGE_DEMO_BUILD: "Demo Build",
    STAGE_PROPOSAL:   "Demo / Proposal",
    STAGE_CLOSING:    "Closing",
    STAGE_WON:        "Won",
    STAGE_ONBOARDING: "Onboarding",
    STAGE_LIVE:       "Live / Completed",
    STAGE_LOST:       "Lost",
}

# Demo build lifecycle (Opportunity.demo_status).
DEMO_NOT_REQUESTED = "not_requested"
DEMO_REQUESTED     = "requested"
DEMO_IN_PROGRESS   = "in_progress"
DEMO_READY         = "ready"
DEMO_DELIVERED     = "delivered"
DEMO_STATUSES = (DEMO_NOT_REQUESTED, DEMO_REQUESTED, DEMO_IN_PROGRESS,
                 DEMO_READY, DEMO_DELIVERED)


class Opportunity(Base):
    """One continuous commercial record: prospect → discovery → demo → won →
    onboarding → live. The salesperson never re-creates the record to move stage.

    Tenancy: owned by a BrandSalesOrg (the sellers), and once Won it points at
    the Organization it produced (the customer). Those are the two separate
    domains meeting at exactly one nullable column.
    """
    __tablename__ = "opportunities"

    id                  = Column(String, primary_key=True, default=gen_uuid)
    brand_sales_org_id  = Column(String, ForeignKey("brand_sales_orgs.id", ondelete="CASCADE"),
                                 nullable=False)
    owner_user_id       = Column(String, ForeignKey("users.id"), nullable=True)

    # Prospect identity
    company_name = Column(String, nullable=False)
    contact_name = Column(String, nullable=True)
    phone        = Column(String, nullable=True)
    email        = Column(String, nullable=True)
    website      = Column(String, nullable=True)
    industry     = Column(String, nullable=True)
    # Decision #13 — captured, never assumed. Grok hardcoded America/Chicago and
    # that was a real defect; do not repeat it.
    timezone     = Column(String, nullable=True)

    stage        = Column(String, default=STAGE_PROSPECT, nullable=False)
    status       = Column(String, default="open", nullable=False)  # open | won | lost
    source       = Column(String, nullable=True)

    # Packages / value — decision #9. deal_value derives from the package unless
    # explicitly overridden, and the override is recorded and audited rather than
    # silently replacing the derived number.
    package_interest_id = Column(String, ForeignKey("brand_packages.id"), nullable=True)
    selected_package_id = Column(String, ForeignKey("brand_packages.id"), nullable=True)
    deal_value          = Column(Numeric(12, 2), nullable=True)
    deal_value_override = Column(Boolean, default=False, nullable=False)
    deal_value_override_by     = Column(String, ForeignKey("users.id"), nullable=True)
    deal_value_override_at     = Column(DateTime, nullable=True)
    deal_value_override_reason = Column(Text, nullable=True)

    # Next action drives the "My Next Actions" list from lifecycle state and due
    # dates (spec) — not arbitrary reminders.
    next_action        = Column(String, nullable=True)
    next_action_due_at = Column(DateTime, nullable=True)

    # Lifecycle stamps
    contacted_at            = Column(DateTime, nullable=True)
    discovery_completed_at  = Column(DateTime, nullable=True)
    # ── Demo build ──────────────────────────────────────────────────────────
    # The salesperson must be able to see whether their demo is being built
    # WITHOUT calling Mike to ask. That is the whole reason these live on the
    # opportunity rather than in someone's head.
    demo_status             = Column(String, nullable=True)   # DEMO_STATUSES
    demo_owner_user_id      = Column(String, ForeignKey("users.id"), nullable=True)
    demo_requested_at       = Column(DateTime, nullable=True)
    demo_due_at             = Column(DateTime, nullable=True)
    demo_ready_at           = Column(DateTime, nullable=True)
    demo_requirements       = Column(Text, nullable=True)
    demo_url                = Column(String, nullable=True)
    demo_notes              = Column(Text, nullable=True)     # internal, not customer-facing

    proposal_status         = Column(String, nullable=True)
    proposal_sent_at        = Column(DateTime, nullable=True)
    won_at                  = Column(DateTime, nullable=True)
    lost_at                 = Column(DateTime, nullable=True)
    loss_reason             = Column(Text, nullable=True)
    stage_changed_at        = Column(DateTime, default=datetime.utcnow)  # powers "days in stage"

    # Decision #7 — the permanent link between the deal and the customer it
    # created. NULL until Won provisions an organization. This is the single
    # point where the selling domain touches the using domain.
    customer_organization_id = Column(String, ForeignKey("organizations.id"), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_opportunities_org_stage", "brand_sales_org_id", "stage"),
        Index("ix_opportunities_owner", "owner_user_id", "status"),
        Index("ix_opportunities_customer_org", "customer_organization_id"),
        Index("ix_opportunities_due", "next_action_due_at"),
    )


class DiscoveryRecord(Base):
    """Structured discovery answers. One per opportunity.

    Separate table rather than 10 more columns on Opportunity: discovery is
    filled once, read rarely, and the question set will change per brand.
    """
    __tablename__ = "discovery_records"

    id             = Column(String, primary_key=True, default=gen_uuid)
    opportunity_id = Column(String, ForeignKey("opportunities.id", ondelete="CASCADE"),
                            nullable=False, unique=True)

    business_description  = Column(Text, nullable=True)
    business_goals        = Column(Text, nullable=True)
    current_process       = Column(Text, nullable=True)
    current_tools         = Column(Text, nullable=True)
    bottlenecks           = Column(Text, nullable=True)
    lead_sources          = Column(Text, nullable=True)
    team_size             = Column(String, nullable=True)
    appointment_process   = Column(Text, nullable=True)
    follow_up_process     = Column(Text, nullable=True)
    required_integrations = Column(Text, nullable=True)
    automation_opportunities = Column(Text, nullable=True)
    desired_outcome       = Column(Text, nullable=True)
    # What the demo builder actually needs. Kept here rather than only on the
    # Opportunity because it is captured DURING discovery, by the person in the
    # room, and copied forward when the demo is requested.
    demo_requirements     = Column(Text, nullable=True)
    opportunity_notes     = Column(Text, nullable=True)

    # The ordered field list the discovery form renders from. Adding a question
    # means adding a column above and a line here — never a migration to a
    # generic key/value bag, which would make discovery unqueryable.
    FIELDS = (
        ("business_description",     "Business description"),
        ("business_goals",           "Goals"),
        ("current_process",          "Current process"),
        ("current_tools",            "Current systems / tools"),
        ("bottlenecks",              "Bottlenecks / challenges"),
        ("lead_sources",             "Lead sources"),
        ("team_size",                "Team size"),
        ("appointment_process",      "Appointment process"),
        ("follow_up_process",        "Communication / follow-up process"),
        ("required_integrations",    "Required integrations"),
        ("automation_opportunities", "Automation opportunities"),
        ("desired_outcome",          "Desired outcome"),
        ("demo_requirements",        "Demo requirements"),
        ("opportunity_notes",        "Additional notes"),
    )

    completed_at   = Column(DateTime, nullable=True)
    completed_by   = Column(String, ForeignKey("users.id"), nullable=True)
    created_at     = Column(DateTime, default=datetime.utcnow)
    updated_at     = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class OpportunityEvent(Base):
    """Append-only activity timeline. Every material lifecycle event lands here so
    the prospect record shows one chronological story rather than scattered stamps.

    Never updated or deleted — corrections are new rows.
    """
    __tablename__ = "opportunity_events"

    id             = Column(String, primary_key=True, default=gen_uuid)
    opportunity_id = Column(String, ForeignKey("opportunities.id", ondelete="CASCADE"),
                            nullable=False)
    event_type     = Column(String, nullable=False)   # created | stage_changed | appointment_booked …
    summary        = Column(String, nullable=False)
    detail         = Column(Text, nullable=True)
    actor_user_id  = Column(String, ForeignKey("users.id"), nullable=True)
    occurred_at    = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_opportunity_events_opp_time", "opportunity_id", "occurred_at"),
    )


# ── Pricing approval requests ────────────────────────────────────────────────
# Checkpoint 5. Before this existed, a rep who needed a discount was told
# "ask your manager to apply the adjustment" — an instruction to use Slack.
# The authority itself is unchanged: a manager could always override pricing,
# a rep never could. What was missing was a RECORD OF THE ASKING, which is why
# a manager had nothing to approve and no way to see who was waiting on them.
#
# This table holds the request. It deliberately does NOT hold the outcome of
# applying it: an approved request calls the existing apply_pricing(), so the
# proposal's own price_override_by/_at/_reason columns and the opportunity
# timeline stay the single source of truth for what the price actually is and
# who changed it. Two records of the same fact would eventually disagree.

APPROVAL_PENDING   = "pending"
APPROVAL_APPROVED  = "approved"
APPROVAL_DENIED    = "denied"
APPROVAL_WITHDRAWN = "withdrawn"
# The proposal moved on (was sent, superseded, or the deal closed) before anyone
# decided. Not a decision — the question stopped being answerable.
APPROVAL_STALE     = "stale"

APPROVAL_STATUSES = (APPROVAL_PENDING, APPROVAL_APPROVED, APPROVAL_DENIED,
                     APPROVAL_WITHDRAWN, APPROVAL_STALE)
# Only one of these may exist per proposal at a time.
APPROVAL_OPEN_STATUSES = (APPROVAL_PENDING,)

APPROVAL_LABELS = {
    APPROVAL_PENDING:   "Waiting on you",
    APPROVAL_APPROVED:  "Approved",
    APPROVAL_DENIED:    "Denied",
    APPROVAL_WITHDRAWN: "Withdrawn",
    APPROVAL_STALE:     "No longer applicable",
}


class PricingApprovalRequest(Base):
    """A rep asking a manager to approve a price adjustment they cannot make.

    Scoped by brand_sales_org_id so a manager's queue can be read with one
    indexed query and can never span brands.

    `requested_adjustment` is a signed amount against the package list price,
    matching Proposal.adjustment exactly — a discount is negative. Storing the
    adjustment rather than the final figure means an approval still means what
    the rep asked for if the package price changed underneath it, instead of
    silently approving a different discount than the one requested.
    """
    __tablename__ = "pricing_approval_requests"

    id                 = Column(String, primary_key=True, default=gen_uuid)
    brand_sales_org_id = Column(String, ForeignKey("brand_sales_orgs.id", ondelete="CASCADE"),
                                nullable=False, index=True)
    opportunity_id     = Column(String, ForeignKey("opportunities.id", ondelete="CASCADE"),
                                nullable=False, index=True)
    proposal_id        = Column(String, ForeignKey("proposals.id", ondelete="CASCADE"),
                                nullable=False, index=True)

    requested_by       = Column(String, ForeignKey("users.id"), nullable=False)
    requested_at       = Column(DateTime, default=datetime.utcnow, nullable=False)

    # What the rep is asking for, captured at request time so the queue reads
    # correctly even if the proposal is edited afterwards.
    base_amount          = Column(Numeric(12, 2), nullable=True)
    current_adjustment   = Column(Numeric(12, 2), nullable=True)
    requested_adjustment = Column(Numeric(12, 2), nullable=False)
    currency             = Column(String, default="USD", nullable=True)
    reason               = Column(Text, nullable=False)

    status         = Column(String, default=APPROVAL_PENDING, nullable=False)
    decided_by     = Column(String, ForeignKey("users.id"), nullable=True)
    decided_at     = Column(DateTime, nullable=True)
    decision_note  = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_pricing_approval_brand_status", "brand_sales_org_id", "status"),
        Index("ix_pricing_approval_proposal", "proposal_id", "status"),
    )
