"""BILLING AGREEMENT — the executable commercial relationship.

WHAT THIS IS FOR

`Implementation` already records what was SOLD: billing_option,
contract_term_months, implementation_fee, recurring_amount, currency,
billing_start_date, trial dates, external_billing_ref. It is copied from the
Opportunity at provisioning and is explicitly never recomputed when catalogue
pricing later moves. That makes it the deal's INTENT and its historical record,
and this module does not duplicate that job.

What was missing is the thing billing actually RUNS on. An Implementation says
"we agreed $499/month on a 13-month term". It does not say which Stripe
subscription is charging it, whether it is still the live arrangement, what
replaced it when the customer upgraded, or what the terms were on the invoice
issued nine months ago. BillingAgreement is that record.

    Implementation    what was agreed at provisioning. One per deal.
    BillingAgreement  what is being charged, now. Many per organization over
                      time - renewal, upgrade, replacement, re-signature.

MONEY IS INTEGER MINOR UNITS HERE, AS IT IS EVERYWHERE ELSE IN BILLING.

Implementation and Opportunity hold Numeric(12,2) because that is the sales
vocabulary. Every amount crossing into billing goes through
app/services/money.py exactly once, at conversion time, and is stored in cents
from then on. Two representations of the same money in one system is how
rounding disagreements get shipped, so the boundary is deliberately a single
named function rather than arithmetic scattered across call sites.

HISTORY IS NEVER REWRITTEN

A new agreement never edits an old one. Superseding is a link
(`supersedes_id` / `superseded_by_id`) plus a status change, and the terms on
the superseded row stay exactly as they were. Invoices already issued against
it must keep making sense, which they cannot do if the row they point at has
been edited to say something else.

The commercial snapshot columns exist for the same reason and are written ONCE,
at creation: the legal entity's name, the brand's name, the package's name and
the organization's name as they were when this agreement was made. Ids answer
"which row"; names answer "what did this say at the time", and a rename makes
those two different questions.

THIS MODULE DOES NOT PRICE ANYTHING

It copies approved amounts. `app/services/package_pricing.py` is the pricing
authority and the legacy `PLANS` dictionary is not; neither is consulted here.
An agreement is built from what the Implementation already approved, and if
that is absent the agreement is not invented - see
app/services/billing_agreement.py.
"""

from datetime import datetime

from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Index, Integer,
                        String, Text, UniqueConstraint)

from app.models.models import Base, gen_uuid

# ── Lifecycle ───────────────────────────────────────────────────────────────
#
# DRAFT is a real state, not a placeholder: an agreement is created from the
# provisioning record before anybody has decided to start charging, and the gap
# between "recorded" and "billing" is where a mistake is cheap to fix.
AGREEMENT_DRAFT = "draft"
AGREEMENT_ACTIVE = "active"
AGREEMENT_PAST_DUE = "past_due"
AGREEMENT_SUPERSEDED = "superseded"
AGREEMENT_CANCELLED = "cancelled"
AGREEMENT_ENDED = "ended"
AGREEMENT_STATUSES = (AGREEMENT_DRAFT, AGREEMENT_ACTIVE, AGREEMENT_PAST_DUE,
                      AGREEMENT_SUPERSEDED, AGREEMENT_CANCELLED,
                      AGREEMENT_ENDED)

# The states in which an agreement is the one currently governing billing.
# PAST_DUE is deliberately included: a customer whose card failed is still on
# this agreement, and treating them as having none is how a failed payment
# turns into a silently unbilled account.
AGREEMENT_LIVE_STATUSES = (AGREEMENT_ACTIVE, AGREEMENT_PAST_DUE)

# Billing cadence. Stripe's own words, for the same reason billing_models.py
# uses Stripe's invoice and payment vocabularies verbatim.
INTERVAL_MONTH = "month"
INTERVAL_YEAR = "year"
INTERVAL_ONE_TIME = "one_time"
BILLING_INTERVALS = (INTERVAL_MONTH, INTERVAL_YEAR, INTERVAL_ONE_TIME)

# Where this agreement came from. `migration` marks rows created by the P5
# reconciliation from what a customer is ALREADY paying, which must never be
# mistaken for a freshly approved deal.
SOURCE_IMPLEMENTATION = "implementation"
SOURCE_MANUAL = "manual"
SOURCE_MIGRATION = "migration"
AGREEMENT_SOURCES = (SOURCE_IMPLEMENTATION, SOURCE_MANUAL, SOURCE_MIGRATION)


class BillingAgreement(Base):
    """One executable billing arrangement between a merchant and a customer."""

    __tablename__ = "billing_agreements"

    id = Column(String, primary_key=True, default=gen_uuid)

    # ── the hierarchy this agreement sits in ───────────────────────────────
    # organization_id carries a real ForeignKey because billing_agreements is
    # a NEW table created by create_all(), which can declare constraints.
    # merchant_entity_id and platform_id follow the convention P0 and P1 set
    # and carry none: they point at rows whose own link columns were added by
    # auto_migrate, which cannot add constraints, and a half-enforced
    # relationship is worse than a consistently unenforced one.
    organization_id = Column(String, ForeignKey("organizations.id"),
                             nullable=False, index=True)
    merchant_entity_id = Column(String, nullable=True, index=True)
    platform_id = Column(String, nullable=True, index=True)

    # ── what it came from ──────────────────────────────────────────────────
    implementation_id = Column(String, nullable=True, index=True)
    opportunity_id = Column(String, nullable=True, index=True)
    package_id = Column(String, nullable=True, index=True)
    source = Column(String, default=SOURCE_IMPLEMENTATION, nullable=False)

    # ── executable terms ───────────────────────────────────────────────────
    status = Column(String, default=AGREEMENT_DRAFT, nullable=False, index=True)
    currency = Column(String, default="USD", nullable=False)

    # Minor units. See the module docstring: one representation, converted once.
    setup_fee_cents = Column(Integer, nullable=True)
    recurring_amount_cents = Column(Integer, nullable=True)
    billing_interval = Column(String, default=INTERVAL_MONTH, nullable=True)

    # billing_option is the sales vocabulary (month_to_month | term_agreement)
    # and is carried across verbatim rather than translated, so the agreement
    # and the deal can be compared without a mapping table in between.
    billing_option = Column(String, nullable=True)
    contract_term_months = Column(Integer, nullable=True)

    # ── quantity-based pricing, when the deal is priced per unit ───────────
    # NULL means a flat agreement. These are the approved custom-pricing
    # fields copied from the Opportunity; nothing here derives them.
    quantity = Column(Integer, nullable=True)
    unit_label = Column(String, nullable=True)          # "active paying customer"
    min_units = Column(Integer, nullable=True)          # contracted minimum
    custom_unit_price_cents = Column(Integer, nullable=True)
    has_custom_pricing = Column(Boolean, default=False, nullable=False)

    # ── dates ──────────────────────────────────────────────────────────────
    billing_start_date = Column(DateTime, nullable=True)
    trial_start = Column(DateTime, nullable=True)
    trial_end = Column(DateTime, nullable=True)
    term_end_date = Column(DateTime, nullable=True)
    next_billing_date = Column(DateTime, nullable=True)

    # ── Stripe: NON-SECRET REFERENCES ONLY ─────────────────────────────────
    # The customer reference is duplicated from Organization deliberately: this
    # is the customer the agreement was executed against, and an organization
    # whose Stripe customer is later replaced must not retroactively change
    # which customer a historical agreement charged.
    stripe_customer_id = Column(String, nullable=True, index=True)
    stripe_subscription_id = Column(String, nullable=True, index=True)
    stripe_price_id = Column(String, nullable=True)

    # ── supersession: history is linked, never edited ──────────────────────
    supersedes_id = Column(String, nullable=True, index=True)
    superseded_by_id = Column(String, nullable=True, index=True)
    supersede_reason = Column(String, nullable=True)   # renewal | upgrade | ...

    # ── immutable commercial snapshot, written once at creation ────────────
    # Ids answer "which row". These answer "what did this say at the time",
    # and a rename makes those different questions.
    merchant_legal_name = Column(String, nullable=True)
    brand_name = Column(String, nullable=True)
    organization_name = Column(String, nullable=True)
    package_name = Column(String, nullable=True)

    notes = Column(Text, nullable=True)

    # ── lifecycle timestamps ───────────────────────────────────────────────
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_by = Column(String, nullable=True)
    activated_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        # ONE AGREEMENT PER IMPLEMENTATION, and this is the idempotency
        # guarantee rather than a tidiness rule. Provisioning is retried - by a
        # user double-clicking, by a job re-running - and without this a retry
        # produces a second agreement for the same deal, which is a second
        # subscription waiting to happen.
        UniqueConstraint("implementation_id",
                         name="uq_billing_agreements_implementation"),
        Index("ix_billing_agreements_org_status", "organization_id", "status"),
        Index("ix_billing_agreements_org_created", "organization_id", "created_at"),
        Index("ix_billing_agreements_subscription", "stripe_subscription_id"),
    )

    def __repr__(self):                                  # pragma: no cover
        return "<BillingAgreement %s org=%s %s>" % (
            self.id, self.organization_id, self.status)

    @property
    def is_live(self) -> bool:
        """Whether this agreement currently governs billing."""
        return self.status in AGREEMENT_LIVE_STATUSES
