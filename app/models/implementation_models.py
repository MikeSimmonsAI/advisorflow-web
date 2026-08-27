"""What happens after a deal is Won: provisioning, implementation, launch.

THIS IS THE THIRD TREE, AND IT IS THE ONLY ONE THAT TOUCHES BOTH OTHERS.

    BrandSalesOrg -> Opportunity -> [Won]
                                     |
                                     v
                              Implementation  <-- this file
                                     |
                                     v
                         Organization -> Users -> Leads

An `Implementation` is the single record that spans the crossing. It holds the
source opportunity on one side and the provisioned customer organization on the
other, and it is what makes "which Won deals are not yet live?" a query instead
of a spreadsheet.

WHY A NEW MODEL RATHER THAN EXTENDING SOMETHING
-----------------------------------------------
Nothing to extend. A survey of every model in `app/models/` found one string
constant (`STAGE_ONBOARDING`), one free-text `Proposal.implementation_plan`, and
two comments. There is no onboarding table, no milestone table, no kickoff or
handoff record, and `onboarding_router.py` is public self-serve signup that has
nothing to do with customer implementation despite the name.

WHAT THIS MODEL DELIBERATELY DOES NOT DO
----------------------------------------
* It does not make Won mean provisioned. An Opportunity reaching Won creates
  nothing here; a human has to ask for it.
* It does not give the customer organization a pointer back to the sale. The
  crossing is `Opportunity.customer_organization_id` in one direction only,
  which was the architecture's single designated bridge long before this file
  existed and stays that way.
* It does not charge anybody. `billing_*` below records the INTENT agreed in
  the sale so a human can act on it. Nothing here talks to Stripe.
* It does not copy the sale into the tenant. A sales contact does not become a
  Lead; a salesperson does not become a customer user.
"""

from sqlalchemy import (
    Column, String, Boolean, DateTime, Integer, Text, Numeric, ForeignKey,
    UniqueConstraint, Index,
)
from datetime import datetime
import uuid

from app.models.models import Base


def gen_uuid():
    return str(uuid.uuid4())


# ── implementation lifecycle ────────────────────────────────────────────────
#
# Plain strings, matching the convention every other status column in this
# codebase uses (Opportunity.stage, Proposal.sales_status, BookingLink.status).
# A Postgres enum would need a migration to add a value, and this list will
# grow.

IMPL_NOT_STARTED = "not_started"
IMPL_KICKOFF_SCHEDULED = "kickoff_scheduled"
IMPL_CONFIGURATION = "configuration"
IMPL_DATA_MIGRATION = "data_migration"
IMPL_INTEGRATIONS = "integrations"
IMPL_TESTING = "testing"
IMPL_TRAINING = "training"
IMPL_READY_FOR_LAUNCH = "ready_for_launch"
IMPL_LIVE = "live"
IMPL_BLOCKED = "blocked"

IMPLEMENTATION_STATUSES = (
    IMPL_NOT_STARTED, IMPL_KICKOFF_SCHEDULED, IMPL_CONFIGURATION,
    IMPL_DATA_MIGRATION, IMPL_INTEGRATIONS, IMPL_TESTING, IMPL_TRAINING,
    IMPL_READY_FOR_LAUNCH, IMPL_LIVE, IMPL_BLOCKED,
)

IMPLEMENTATION_STATUS_LABELS = {
    IMPL_NOT_STARTED: "Not started",
    IMPL_KICKOFF_SCHEDULED: "Kickoff scheduled",
    IMPL_CONFIGURATION: "Configuration",
    IMPL_DATA_MIGRATION: "Data migration",
    IMPL_INTEGRATIONS: "Integrations",
    IMPL_TESTING: "Testing",
    IMPL_TRAINING: "Training",
    IMPL_READY_FOR_LAUNCH: "Ready for launch",
    IMPL_LIVE: "Live",
    IMPL_BLOCKED: "Blocked",
}

# Statuses that mean work is still outstanding. `blocked` counts as open —
# a blocked implementation is not finished, it is stuck, and the difference
# belongs in the alert not in the "is it done" test.
IMPLEMENTATION_OPEN_STATUSES = tuple(
    s for s in IMPLEMENTATION_STATUSES if s != IMPL_LIVE
)


class Implementation(Base):
    """One provisioned customer, from Won to Live.

    UNIQUE ON BOTH SIDES. `opportunity_id` and `organization_id` each carry a
    unique constraint, which is what makes provisioning idempotent at the
    DATABASE rather than only in the service: two concurrent Provision clicks
    cannot produce two customer organizations for one deal, because the second
    insert cannot commit. The service checks first and returns the original;
    the constraint is what makes that check trustworthy under a race.
    """
    __tablename__ = "implementations"

    id = Column(String, primary_key=True, default=gen_uuid)

    # ── the crossing ──
    # Both NOT NULL: an implementation without a source deal is not a handoff,
    # and one without a customer is not an implementation.
    opportunity_id = Column(String, ForeignKey("opportunities.id"),
                            nullable=False, unique=True, index=True)
    organization_id = Column(String, ForeignKey("organizations.id"),
                             nullable=False, unique=True, index=True)
    # Denormalised so a god-level list can filter by brand without joining
    # through the opportunity, and so the row still says which brand sold it if
    # the opportunity is ever archived.
    platform_id = Column(String, ForeignKey("platforms.id"), nullable=True, index=True)
    brand_sales_org_id = Column(String, nullable=True, index=True)

    # What was sold. A reference, not a copy of the price — the proposal is the
    # historical record of what was agreed and must not be restated here where
    # it could drift.
    package_id = Column(String, ForeignKey("brand_packages.id"), nullable=True)
    accepted_proposal_id = Column(String, nullable=True)
    accepted_proposal_version = Column(Integer, nullable=True)

    # Who sold it, kept for the read-only post-Won view. NOT an authority.
    sold_by_user_id = Column(String, ForeignKey("users.id"), nullable=True)

    # ── who is doing the work ──
    # Deliberately NOT defaulted to the salesperson. Selling and implementing
    # are different jobs, and auto-assigning the rep would quietly make the
    # rep accountable for a delivery they are not staffed to do.
    owner_user_id = Column(String, ForeignKey("users.id"), nullable=True, index=True)
    owner_assigned_at = Column(DateTime, nullable=True)
    owner_assigned_by = Column(String, ForeignKey("users.id"), nullable=True)

    status = Column(String, default=IMPL_NOT_STARTED, nullable=False, index=True)
    # Set when status becomes `blocked`, cleared when it leaves. Kept as its own
    # column rather than inferred from status history so "why is this stuck"
    # survives a later status change.
    blocker_note = Column(Text, nullable=True)
    blocked_at = Column(DateTime, nullable=True)

    target_launch_date = Column(DateTime, nullable=True)
    kickoff_at = Column(DateTime, nullable=True)
    ready_for_launch_at = Column(DateTime, nullable=True)
    # Live is an explicit, authorised act — never a side effect of provisioning
    # or of the last milestone ticking over.
    launched_at = Column(DateTime, nullable=True)
    launched_by = Column(String, ForeignKey("users.id"), nullable=True)

    notes = Column(Text, nullable=True)
    last_activity_at = Column(DateTime, nullable=True, index=True)

    # ── billing INTENT, not billing action ──
    #
    # Recorded so a human can act on it deliberately. Nothing in this codebase
    # reads these to charge anybody, and `BrandPackage.billing_plan_key` remains
    # unwired on purpose: the sales packages and the Stripe plans are different
    # products at different prices and must not be mapped to each other blindly.
    # See the docstring on BrandPackage.
    # WHAT THE CUSTOMER ACTUALLY SIGNED. Copied from the opportunity at
    # provisioning and never recomputed: once the deal is closed, the catalogue
    # can be repriced and this must still say what was agreed. Without it, a
    # $500/month customer is indistinguishable from a discounted one and nothing
    # records that thirteen payments are owed.
    billing_option       = Column(String, nullable=True)   # BILLING_OPTIONS
    contract_term_months = Column(Integer, nullable=True)

    billing_status = Column(String, default="not_configured", nullable=True)
    implementation_fee = Column(Numeric(12, 2), nullable=True)
    recurring_amount = Column(Numeric(12, 2), nullable=True)
    currency = Column(String, default="USD", nullable=True)
    billing_start_date = Column(DateTime, nullable=True)
    trial_start = Column(DateTime, nullable=True)
    trial_end = Column(DateTime, nullable=True)
    billing_notes = Column(Text, nullable=True)
    external_billing_ref = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_by = Column(String, ForeignKey("users.id"), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow, nullable=True)

    __table_args__ = (
        Index("ix_impl_status_launch", "status", "target_launch_date"),
        Index("ix_impl_brand_status", "brand_sales_org_id", "status"),
    )

    def is_live(self) -> bool:
        return self.status == IMPL_LIVE and self.launched_at is not None


# ── milestones ──────────────────────────────────────────────────────────────

MILESTONE_PENDING = "pending"
MILESTONE_IN_PROGRESS = "in_progress"
MILESTONE_DONE = "done"
MILESTONE_SKIPPED = "skipped"
MILESTONE_BLOCKED = "blocked"

MILESTONE_STATUSES = (MILESTONE_PENDING, MILESTONE_IN_PROGRESS,
                      MILESTONE_DONE, MILESTONE_SKIPPED, MILESTONE_BLOCKED)

# Statuses that count as settled when computing completion. `skipped` counts:
# a milestone the customer does not need is not outstanding work, and treating
# it as incomplete would make every percentage wrong for every customer who
# does not buy every module.
MILESTONE_SETTLED = (MILESTONE_DONE, MILESTONE_SKIPPED)


class ImplementationMilestone(Base):
    """One step of onboarding for one customer.

    Rows, not columns. A fixed set of boolean columns would mean a migration
    every time a new onboarding step is needed, and would force every customer
    to carry every step whether or not they bought it.
    """
    __tablename__ = "implementation_milestones"

    id = Column(String, primary_key=True, default=gen_uuid)
    implementation_id = Column(String,
                               ForeignKey("implementations.id", ondelete="CASCADE"),
                               nullable=False, index=True)

    # Stable machine key from the template, e.g. "business_profile".
    key = Column(String, nullable=False)
    label = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    position = Column(Integer, default=0, nullable=False)

    # A milestone the customer genuinely cannot launch without. Advisory at
    # launch time, not a hard gate — see `launch()` in the service. Blocking
    # launch on an optional step is how a customer waits a week for a checkbox.
    is_required = Column(Boolean, default=False, nullable=False)

    status = Column(String, default=MILESTONE_PENDING, nullable=False)
    notes = Column(Text, nullable=True)

    completed_at = Column(DateTime, nullable=True)
    completed_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("implementation_id", "key", name="uq_impl_milestone_key"),
        Index("ix_impl_milestone_status", "implementation_id", "status"),
    )


# ── customer activation ─────────────────────────────────────────────────────

INVITE_PENDING = "pending"
INVITE_ACCEPTED = "accepted"
INVITE_REVOKED = "revoked"
INVITE_EXPIRED = "expired"


class CustomerActivation(Base):
    """An invitation for a customer's first administrator to set their password.

    WHY THIS EXISTS INSTEAD OF A TEMPORARY PASSWORD.
    -------------------------------------------------
    Today this codebase creates users by generating a plaintext password and
    returning it in an HTTP response body — in four separate endpoints. Nothing
    emails it; it is simply handed to whoever called the API, to relay by
    whatever means they choose. That is the mechanism a Checkpoint 6 rule
    forbids outright, and rightly: a password that travels through a chat
    window, a ticket or a sticky note is a password that outlives its purpose.

    So provisioning creates the customer admin with a random password that is
    never returned, never logged and never known to anybody, and issues one of
    these instead. The operator sends the link; the customer sets their own
    password; the token is consumed.

    ONLY A HASH IS STORED. The token is shown once, to the operator who created
    it, and exists nowhere afterwards — the same discipline as the integration
    keys in `integration_models.py`.
    """
    __tablename__ = "customer_activations"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    organization_id = Column(String, ForeignKey("organizations.id"),
                             nullable=False, index=True)
    implementation_id = Column(String, ForeignKey("implementations.id"),
                               nullable=True, index=True)

    # Non-secret lookup handle; the secret half is hashed.
    token_prefix = Column(String, nullable=False, unique=True, index=True)
    token_hash = Column(String, nullable=False)

    status = Column(String, default=INVITE_PENDING, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    accepted_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)

    # How many times the operator has re-issued. A resend revokes the previous
    # token and mints a new one rather than extending the old, so a link that
    # leaked cannot be rescued by the person who leaked it.
    send_count = Column(Integer, default=1, nullable=False)
    last_sent_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_by = Column(String, ForeignKey("users.id"), nullable=True)

    def is_usable(self, now=None) -> bool:
        now = now or datetime.utcnow()
        if self.status != INVITE_PENDING:
            return False
        if self.expires_at and self.expires_at <= now:
            return False
        return True
