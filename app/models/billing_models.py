"""
Customer SaaS billing — the brand-scoped catalogue, the brand's billing
policy, and the ledger of everything Stripe has told us.

═══════════════════════════════════════════════════════════════════════════
READ THIS BEFORE TOUCHING ANYTHING IN HERE
═══════════════════════════════════════════════════════════════════════════

THIS FILE IS ABOUT WHAT A CUSTOMER ORGANIZATION PAYS US EVERY MONTH.
It is NOT about what a salesperson sold, and it is NOT about what a
salesperson is paid.

There are two different economic objects in this platform and they both use
the words "Starter", "Growth" and "Professional":

    CUSTOMER SAAS SUBSCRIPTION          SALES / DIRECT-SALE
    (this file)                         (sales_models.BrandPackage)
    Starter    $497 / month             Starter      $1,497 one-time setup
    Growth     $997 / month             Growth       $2,495 one-time setup
    Professional $1,997 / month         Professional $4,995 one-time setup
    charged by Stripe, recurring        quoted by package_pricing, one-time
    drives entitlements                 drives compensation

`sales_models.BrandPackage` states the rule and it is repeated here because
this is the file most likely to get it wrong:

    "These are NOT the same products and must NOT be mapped to each other
    blindly. `billing_plan_key` is the deliberate, later connection point to
    billing. It stays NULL until someone decides what a sold package should
    charge, and no code may infer it."

That decision is still open. Nothing in this file infers it.

The trap is that both catalogues key on the same three strings. A `.get(key)`
against the wrong table returns a plausible, wrong number with no error. Every
lookup here goes through `billing_catalog`, which only ever reads
`brand_billing_plans`.

═══════════════════════════════════════════════════════════════════════════
WHY THESE ARE TABLES AND NOT A PYTHON DICT
═══════════════════════════════════════════════════════════════════════════

They replace a module-global `PLANS` dict in billing_router.py (and a second,
independently-maintained copy of the same prices in the React bundle). A
global dict cannot be brand-scoped, so a second white-label brand would have
had its customers charged EvoSys Pro's prices, offered EvoSys Pro's three tier
names, and sent a receipt reading "BookaBoost Growth".

The sales half of this platform was built multi-brand from day one —
`brand_packages` is unique on (platform_id, key), compensation plans fall back
from brand to platform default. The billing half was not. These tables close
that gap using the same shape, so "build once at the platform level, configure
per brand" is true of billing too.
"""

from datetime import datetime

from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Index, Integer,
                        Numeric, String, Text, UniqueConstraint)
from sqlalchemy.orm import relationship

from app.models.models import Base, gen_uuid


# ──────────────────────────────────────────────────────────────────────────────
# Vocabulary. Plain strings, no PG ENUM — new values must not need a migration.
# ──────────────────────────────────────────────────────────────────────────────

class BillingInterval:
    MONTH = "month"
    YEAR = "year"
    ALL = (MONTH, YEAR)


class BillingCommitment:
    """WHETHER THE CUSTOMER COMMITTED TO A TERM — a second, separate axis.

    Interval is how often the card is charged. Commitment is what the customer
    promised in exchange for the rate. They are independent, and conflating
    them is why this class exists rather than a third "interval".

    A term agreement and a month-to-month deal are BOTH billed monthly. They
    differ only in price: the term rate is lower and is earned by committing to
    `term_months`. So "Starter month-to-month" is not a different interval and
    it is emphatically not a different PRODUCT — the customer bought Starter.
    It is the same tier at its no-commitment price.

    THIS IS WHY THERE ARE NOT SIX PACKAGES. Six tiers would mean six things to
    name, six things to sell, six sets of entitlements to keep in step, and a
    rep choosing between "Starter" and "Starter MTM" in a dropdown. One tier
    with two configured prices keeps the product identity singular and puts the
    commercial choice where it belongs — on the deal.

    The strings match `package_pricing.BILLING_MONTH_TO_MONTH` /
    `BILLING_TERM_AGREEMENT` deliberately. The sales side already had this
    vocabulary; a second set of words for the same two ideas would need a
    translation table that someone eventually gets backwards.
    """
    MONTH_TO_MONTH = "month_to_month"
    TERM = "term_agreement"
    ALL = (MONTH_TO_MONTH, TERM)

    # What a caller that says nothing means. The TERM rate is the default
    # because it is what `monthly_cents` has always held, so every existing
    # caller keeps resolving the price it resolved before this class existed.
    DEFAULT = TERM


class SubscriptionStatus:
    """Mirrors Stripe's own subscription status vocabulary.

    Deliberately Stripe's words, not ours. This value is copied from a webhook
    payload; inventing a parallel vocabulary would mean a translation layer
    that has to be kept correct forever, and the first missed status would
    silently become "unknown" on a screen someone bills from.
    """
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    UNPAID = "unpaid"
    INCOMPLETE = "incomplete"
    INCOMPLETE_EXPIRED = "incomplete_expired"
    PAUSED = "paused"

    # The states in which an organization holds a subscription it should not be
    # allowed to duplicate. Used by the change-plan guard: a second checkout
    # while one of these is live is what produced double-billing.
    OCCUPIED = (TRIALING, ACTIVE, PAST_DUE, UNPAID, INCOMPLETE, PAUSED)


class ChangeTiming:
    IMMEDIATE = "immediate"
    PERIOD_END = "period_end"
    ALL = (IMMEDIATE, PERIOD_END)


class ProrationBehavior:
    """Stripe's own proration_behavior values, passed straight through."""
    CREATE_PRORATIONS = "create_prorations"
    NONE = "none"
    ALWAYS_INVOICE = "always_invoice"
    ALL = (CREATE_PRORATIONS, NONE, ALWAYS_INVOICE)


class BillingEventOutcome:
    PROCESSED = "processed"
    DUPLICATE = "duplicate"
    IGNORED = "ignored"
    FAILED = "failed"


# ──────────────────────────────────────────────────────────────────────────────
# BrandBillingPlan — the customer SaaS catalogue, per brand
# ──────────────────────────────────────────────────────────────────────────────

class BrandBillingPlan(Base):
    """One purchasable subscription tier, for one brand.

    THE SERVER'S ONLY SOURCE OF PRICE. The browser identifies a plan by `key`
    and an interval by name; it never sends an amount and never sends a Stripe
    price id. Both are resolved here. That is the whole reason this table
    exists rather than the frontend passing `price` to checkout.

    STRIPE PRICE IDS ARE PREFERRED OVER AD-HOC AMOUNTS. The previous checkout
    built an inline `price_data` block on every call, which created a brand new
    anonymous Price object in Stripe for every single checkout — thousands of
    one-off Prices, no Product, and nothing in the Stripe dashboard that can be
    reported on or reconciled. When `stripe_price_id_monthly` /
    `_annual` are set they are used directly. The cents columns remain as the
    display price and as the fallback for a brand that has not yet had its
    Stripe Products created, so the two can be checked against each other
    rather than one silently drifting from the other.
    """
    __tablename__ = "brand_billing_plans"

    id = Column(String, primary_key=True, default=gen_uuid)

    # WHICH BRAND SELLS THIS. Not nullable: a plan with no brand is a plan that
    # would be offered to every brand's customers, which is exactly the
    # global-dict behaviour this table replaces.
    platform_id = Column(String, ForeignKey("platforms.id", ondelete="CASCADE"),
                         nullable=False, index=True)

    key = Column(String, nullable=False)          # starter | growth | ...
    name = Column(String, nullable=False)         # display name
    description = Column(Text, nullable=True)
    sort_order = Column(Integer, default=0, nullable=False)

    # ── Price ─────────────────────────────────────────────────────────────
    # Cents, integer, never float. Nullable for a "contact us" tier such as
    # Enterprise, which is listed but not self-serve purchasable.
    # `monthly_cents` is the COMMITTED (term-agreement) monthly rate — the
    # lower one, earned by committing to a term. It keeps that name because it
    # is what every existing caller already resolves.
    monthly_cents = Column(Integer, nullable=True)
    annual_cents = Column(Integer, nullable=True)

    # ── The no-commitment monthly rate ────────────────────────────────────
    #
    # THE SAME TIER, PRICED FOR A CUSTOMER WHO PROMISED NOTHING. Higher than
    # `monthly_cents` by design: the term rate is a discount earned by the
    # commitment, so a month-to-month customer pays the standard price.
    #
    # A SEPARATE COLUMN RATHER THAN A SEPARATE PLAN. Making "Starter MTM" its
    # own `brand_billing_plans` row would double the catalogue, split one
    # product's entitlements across two rows that have to be kept in step, and
    # let a customer end up on a "tier" nobody sells. The tier is Starter; this
    # is one of its two prices.
    #
    # NULL is a real state: a brand that only sells term agreements has no
    # month-to-month price, and `deal_billing` then refuses that deal by name
    # rather than falling back to the term rate — which would hand a
    # no-commitment customer the discount.
    month_to_month_cents = Column(Integer, nullable=True)

    currency = Column(String, default="usd", nullable=False)

    # ── Stripe object mapping ─────────────────────────────────────────────
    # Not secrets. These are public object identifiers (prod_..., price_...)
    # and are safe to store and to log. Nothing in this file ever stores an
    # API key or a webhook secret; those stay in the environment.
    stripe_product_id = Column(String, nullable=True)
    # The COMMITTED monthly Price, matching `monthly_cents`.
    stripe_price_id_monthly = Column(String, nullable=True)
    stripe_price_id_annual = Column(String, nullable=True)
    # The no-commitment monthly Price, matching `month_to_month_cents`. A
    # DIFFERENT Stripe Price on the SAME Stripe Product — which is exactly how
    # Stripe models one product sold at two prices, so nothing here invents a
    # structure Stripe does not already have.
    stripe_price_id_month_to_month = Column(String, nullable=True)

    # ── Entitlement ceilings ──────────────────────────────────────────────
    # These were previously returned to the UI and enforced by NOTHING - no
    # query anywhere read max_leads or max_users. They are kept here so the
    # entitlement layer has somewhere real to read them from, and so the gap
    # between "advertised" and "enforced" is visible in one place.
    max_leads = Column(Integer, nullable=True)     # NULL = unlimited
    max_users = Column(Integer, nullable=True)     # NULL = unlimited
    features_json = Column(Text, nullable=True)    # JSON list of display strings

    # Self-serve purchasable. An Enterprise tier is listed and quoted, never
    # checked out, so the checkout guard has something explicit to refuse on
    # rather than tripping over a NULL price.
    is_purchasable = Column(Boolean, default=True, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        # Same shape as BrandPackage's (platform_id, key). One brand cannot
        # have two plans called "growth"; two brands can each have one.
        UniqueConstraint("platform_id", "key", name="uq_brand_billing_plan_key"),
        Index("ix_brand_billing_plans_lookup", "platform_id", "is_active"),
    )


# ──────────────────────────────────────────────────────────────────────────────
# BrandBillingConfig — the brand's billing POLICY
# ──────────────────────────────────────────────────────────────────────────────

class BrandBillingConfig(Base):
    """How this brand behaves when money goes wrong, or a plan changes.

    EVERY POLICY FIELD HERE IS NULLABLE AND MEANS "NOT DECIDED YET". That is
    load-bearing, not laziness. Several of these decisions were explicitly
    deferred, and the alternative to a NULL is a hardcoded default that looks
    like a decision nobody made:

      - a `grace_days = 3` nobody chose starts cutting off paying customers
      - a `suspend_on_past_due = True` nobody chose does it faster
      - a `cancel_timing = immediate` nobody chose deletes the rest of a period
        a customer already paid for

    Code that reads these must handle NULL by DOING NOTHING and saying so, not
    by falling back to a guess. `billing_policy.py` is the only module allowed
    to read them and it is written that way.
    """
    __tablename__ = "brand_billing_configs"

    id = Column(String, primary_key=True, default=gen_uuid)
    platform_id = Column(String, ForeignKey("platforms.id", ondelete="CASCADE"),
                         nullable=False, unique=True, index=True)

    # ── Plan change timing and proration ──────────────────────────────────
    #
    # DECIDED for EvoSys Pro, and seeded: an upgrade takes effect immediately
    # WITH proration (the customer gets what they paid more for, now, and is
    # charged the difference); a downgrade takes effect at the end of the
    # period they have already paid for, with NO immediate credit or refund
    # (they keep the tier they bought until it runs out).
    #
    # Stored as configuration rather than written into the engine so a second
    # brand can choose differently without touching core billing code.
    upgrade_timing = Column(String, nullable=True)       # ChangeTiming
    upgrade_proration = Column(String, nullable=True)    # ProrationBehavior
    downgrade_timing = Column(String, nullable=True)
    downgrade_proration = Column(String, nullable=True)

    # ── Failed payment ────────────────────────────────────────────────────
    #
    # POLICY REQUIRED — deliberately UNSET. Today a failed payment changes
    # billing_status and nothing else: no entitlement is withdrawn. Shipping
    # unset preserves exactly that behaviour while making the switch real, so
    # turning it on later is a configuration change and not a code change.
    #
    # past_due_grace_days NULL   = no automatic consequence, ever
    # suspend_on_past_due NULL   = same; entitlements ignore billing status
    past_due_grace_days = Column(Integer, nullable=True)
    suspend_on_past_due = Column(Boolean, nullable=True)

    # ── Cancellation ──────────────────────────────────────────────────────
    #
    # POLICY REQUIRED — deliberately UNSET. Note this is SUBSCRIPTION
    # cancellation (stop charging), which is a different thing from CUSTOMER
    # cancellation in customer_lifecycle.py (offboarding a workspace).
    # CANCELLATION IS NOT DELETION applies to both: neither removes history.
    cancel_timing = Column(String, nullable=True)        # ChangeTiming

    # ── Trial ─────────────────────────────────────────────────────────────
    # NULL = no trial offered. Never guessed - a trial nobody configured that
    # silently gives away a month is a revenue decision made by a default.
    trial_days = Column(Integer, nullable=True)

    # ── Refund / chargeback → compensation clawback ───────────────────────
    #
    # POLICY REQUIRED and NOT IMPLEMENTED. A refund is RECORDED (see
    # BillingPayment.refunded_cents) and never mutates paid compensation
    # history. When a policy exists it will be expressed as append-only
    # adjustment entries referencing the original collection, the Stripe
    # reversal and the original compensation entry - never by editing what was
    # already paid to a person.
    clawback_policy = Column(String, nullable=True)

    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ──────────────────────────────────────────────────────────────────────────────
# BillingEvent — the idempotency ledger
# ──────────────────────────────────────────────────────────────────────────────

class BillingEvent(Base):
    """Every Stripe webhook we have ever accepted, keyed on Stripe's event id.

    THIS IS BOTH THE DEDUP KEY AND THE AUDIT TRAIL, on purpose. The pattern is
    lifted from IntegrationRequestLog, whose docstring puts it best:

        "Keeping both in one table means the answer to 'did this already
        happen?' and the answer to 'what happened?' can never disagree."

    Stripe retries. It retries on timeout, on a non-2xx, and on its own
    schedule for up to three days, and it can deliver the same event twice
    even on success. Without this table, a retried `invoice.paid` re-runs
    every write behind it - including creating a second compensation entry for
    one payment. The unique constraint on stripe_event_id is what makes a
    retry a no-op instead of a duplicate.

    NO SECRET VALUE IS EVER WRITTEN HERE. Not the signing secret, not an API
    key, not a card number, not a PaymentMethod's details. Stripe object ids
    (evt_, in_, pi_, sub_, cus_) are public identifiers and are safe. The
    payload itself is NOT stored: it is large, it changes shape between API
    versions, and it can carry customer PII we have no reason to keep a second
    copy of.
    """
    __tablename__ = "billing_events"

    id = Column(String, primary_key=True, default=gen_uuid)

    # Stripe's own event id (evt_...). THE dedup key.
    stripe_event_id = Column(String, nullable=False, unique=True, index=True)
    event_type = Column(String, nullable=False, index=True)

    # Resolved scope. Both nullable: an event can arrive before we can map it
    # to anything (an unknown customer, a subscription for a deleted org), and
    # recording that we saw it and could not place it is more useful than
    # dropping it.
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="SET NULL"),
                             nullable=True, index=True)
    platform_id = Column(String, ForeignKey("platforms.id", ondelete="SET NULL"),
                         nullable=True, index=True)

    # Public Stripe object ids this event concerned - what makes the ledger
    # answerable ("what happened to invoice in_123?") without keeping payloads.
    stripe_object_id = Column(String, nullable=True, index=True)
    stripe_customer_id = Column(String, nullable=True)
    stripe_subscription_id = Column(String, nullable=True)

    outcome = Column(String, nullable=False, default=BillingEventOutcome.PROCESSED)
    detail = Column(Text, nullable=True)   # human-readable, never a secret

    # Did this event cause compensation to be earned, and for which entries.
    # Answers "why did this person get paid" from the payment side.
    earned_compensation = Column(Boolean, default=False, nullable=False)
    compensation_note = Column(Text, nullable=True)

    received_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    processed_at = Column(DateTime, nullable=True)


# ──────────────────────────────────────────────────────────────────────────────
# BillingInvoice / BillingPayment
# ──────────────────────────────────────────────────────────────────────────────

class BillingInvoice(Base):
    """A Stripe invoice, mirrored locally.

    God Mode's revenue panel currently renders MRR, Collected-30d and Past-Due
    as honest empty placeholders annotated "needs invoices table". This is that
    table. Mirroring rather than calling Stripe on every page load also stops
    the dashboard from being as slow and as fragile as Stripe's API on its
    worst day.

    Amounts are integer cents. Never float - a float dollar amount is a
    rounding error waiting to be someone's payout.
    """
    __tablename__ = "billing_invoices"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    platform_id = Column(String, ForeignKey("platforms.id", ondelete="SET NULL"),
                         nullable=True, index=True)

    stripe_invoice_id = Column(String, nullable=False, unique=True, index=True)
    stripe_subscription_id = Column(String, nullable=True, index=True)
    stripe_customer_id = Column(String, nullable=True)

    status = Column(String, nullable=True)          # Stripe's invoice status
    currency = Column(String, default="usd", nullable=False)
    amount_due_cents = Column(Integer, nullable=True)
    amount_paid_cents = Column(Integer, nullable=True)

    billing_plan_key = Column(String, nullable=True)
    billing_interval = Column(String, nullable=True)

    period_start = Column(DateTime, nullable=True)
    period_end = Column(DateTime, nullable=True)
    paid_at = Column(DateTime, nullable=True)

    # Public hosted URL, safe to store; it is what a customer clicks in the
    # Billing screen to see their own receipt.
    hosted_invoice_url = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BillingPayment(Base):
    """Money that actually moved, and whether it earned commission.

    ONE ROW PER UNDERLYING PAYMENT, NOT ONE PER STRIPE EVENT. Several Stripe
    events describe the same payment - invoice.paid,
    invoice.payment_succeeded, payment_intent.succeeded and charge.succeeded
    can all fire for one card charge. Treating each as its own collection
    would credit one payment two, three or four times, and would pay
    commission on all of them. `collection_reference` is unique, and it is the
    SAME string handed to compensation.earn(), so the local row and the
    compensation entry cannot disagree about how many payments there were.
    """
    __tablename__ = "billing_payments"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    platform_id = Column(String, ForeignKey("platforms.id", ondelete="SET NULL"),
                         nullable=True, index=True)

    # The stable, deduplicated identity of this payment. Also the exact value
    # passed to compensation.earn(collection_reference=...), whose own unique
    # constraint then makes double-earning impossible from the other side too.
    collection_reference = Column(String, nullable=False, unique=True, index=True)

    stripe_invoice_id = Column(String, nullable=True, index=True)
    stripe_payment_intent_id = Column(String, nullable=True, index=True)
    stripe_charge_id = Column(String, nullable=True)

    currency = Column(String, default="usd", nullable=False)
    amount_cents = Column(Integer, nullable=False)

    # First payment on a subscription vs a renewal. Compensation rules can
    # differ between the two (the known SaaS configuration pays on both), so
    # the distinction is recorded rather than re-derived later from dates.
    is_initial = Column(Boolean, default=False, nullable=False)

    # Refunds are RECORDED here and never rewrite compensation history.
    refunded_cents = Column(Integer, default=0, nullable=False)
    refunded_at = Column(DateTime, nullable=True)

    # Compensation outcome for this payment, including the reason when nothing
    # was earned. "No rule was configured for this package" is a real answer
    # and has to be visible - otherwise an unpaid commission looks identical to
    # a bug.
    earned_compensation = Column(Boolean, default=False, nullable=False)
    compensation_skipped_reason = Column(Text, nullable=True)
    opportunity_id = Column(String, nullable=True, index=True)

    collected_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)


# ──────────────────────────────────────────────────────────────────────────────
# CustomerEntitlementSnapshot — what a NON-CATALOGUE customer actually bought
# ──────────────────────────────────────────────────────────────────────────────

class CustomerEntitlementSnapshot(Base):
    """The agreed limits for a customer who sits on no catalogue tier.

    ═══════════════════════════════════════════════════════════════════════
    THE HOLE THIS FILLS, AND THE TWO WRONG WAYS TO FILL IT
    ═══════════════════════════════════════════════════════════════════════

    A Custom deal is billed at its own negotiated rate against an inline Stripe
    price, so the customer's organization ends with NO `billing_plan_key` — by
    design, because naming a tier would bill them against a plan they are not
    paying for. But `plan_limits.effective_plan()` reads exactly that key, so a
    Custom customer resolves to no plan, and no plan means no ceiling.

    The two obvious fixes are both wrong:

      ASSIGN THE NEAREST STANDARD TIER. Caps a customer at limits they never
      agreed to, using a tier chosen by whoever wrote the mapping. A $3,750/mo
      customer held to Starter's two users is a support ticket that reads like
      a platform fault.

      LEAVE THEM UNLIMITED. Silently grants every ceiling in the product to the
      one category of customer whose terms were negotiated individually, which
      is precisely where "unlimited" is least likely to be what was sold.

    So the limits are neither guessed nor ignored: they are RECORDED, from the
    agreement, by a person, and read from here.

    ═══════════════════════════════════════════════════════════════════════
    A SNAPSHOT, NOT A REFERENCE
    ═══════════════════════════════════════════════════════════════════════

    Every number here is a literal copy taken when the agreement was accepted.
    Nothing points at `brand_billing_plans`, so a later catalogue edit — a brand
    raising Starter's lead ceiling, say — cannot retroactively change what an
    existing customer was sold. Same reason a proposal snapshots its pricing
    rather than re-deriving it.

    ═══════════════════════════════════════════════════════════════════════
    NULL MEANS UNSET, AND UNSET IS NOT UNLIMITED
    ═══════════════════════════════════════════════════════════════════════

    A NULL ceiling here does NOT mean "no limit". It means nobody has recorded
    one, which is a different thing and is reported as such:
    `plan_limits.entitlement_state()` returns those dimensions in
    `unset_dimensions` so a screen can say NEEDS CONFIGURATION rather than
    implying an allowance that was never agreed.

    To record a genuinely uncapped dimension — which some Custom deals really do
    buy — name it in `unlimited_json`. That is a decision somebody made and can
    be audited, rather than an absence being read as one.
    """
    __tablename__ = "customer_entitlement_snapshots"

    id = Column(String, primary_key=True, default=gen_uuid)

    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    platform_id = Column(String, nullable=True, index=True)

    # PROVENANCE. Which deal this came from, so the agreed ceilings can always
    # be traced back to the paperwork that agreed them.
    opportunity_id = Column(String, nullable=True, index=True)
    source = Column(String, nullable=False, default="custom_deal")

    # ── The agreed ceilings. NULL = not recorded, NOT unlimited. ──────────
    max_leads = Column(Integer, nullable=True)
    max_users = Column(Integer, nullable=True)
    max_locations = Column(Integer, nullable=True)
    sms_monthly_allowance = Column(Integer, nullable=True)
    voice_minutes_monthly_allowance = Column(Integer, nullable=True)
    email_monthly_allowance = Column(Integer, nullable=True)

    # The agreed feature allow-list, as entitlements.FEATURES keys. NULL means
    # not recorded here, and the organization's own `enabled_features` governs.
    features_json = Column(Text, nullable=True)

    # Dimensions deliberately agreed as uncapped, named one by one. A JSON list
    # of the ceiling field names above.
    unlimited_json = Column(Text, nullable=True)

    note = Column(Text, nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # History is kept. A renegotiation supersedes rather than overwrites, so
    # what a customer was entitled to last quarter stays answerable.
    superseded_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_customer_entitlement_current", "organization_id", "superseded_at"),
    )
