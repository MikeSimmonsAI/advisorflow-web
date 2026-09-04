"""BILLING MIRROR — invoices, payments, and the webhook event ledger.

WHAT THIS IS FOR

Before this module the platform had a Stripe subscription integration and no
record of an invoice anywhere. `invoice.payment_failed` was not handled, so an
organization whose card declined stayed `billing_status = 'active'` in the
database until somebody opened the Stripe dashboard. Past-due was invisible.

These tables are a MIRROR. Stripe is authoritative for whether money moved;
nothing here decides that, and nothing here may contradict it. Every row is
written from a Stripe object, keyed by that object's Stripe id, and re-writing
the same object twice must produce the same row.

WHAT THIS IS NOT

Not a pricing engine. `app/services/package_pricing.py` is the pricing
authority and this module does not duplicate, re-derive or second-guess it.

Not the agreement layer. `BillingAgreement` arrives in P2 and is the executable
commercial agreement. `Implementation` already holds the deal's billing INTENT
(billing_option, contract_term_months, implementation_fee, recurring_amount,
currency, billing_start_date, trial dates, external_billing_ref) and keeps that
job unchanged - see implementation_models.py. Nothing here duplicates those
columns. `Invoice.billing_agreement_id` is present and nullable so P2 can link
without a migration; it carries no ForeignKey yet because the table it would
point at does not exist.

MONEY IS INTEGER MINOR UNITS. Conversion happens only in app/services/money.py.

THE LEGAL ENTITY IS NOT THE BRAND. EVO INTEGRATED SOLUTIONS LLC sells;
EvoSys Pro is what the customer recognises. An invoice records BOTH, as
snapshots, because a legal name or a brand name can change and an invoice that
has already been paid must keep saying who issued it. P1 introduces
`merchant_entities` and fills `merchant_entity_id`; until then the legal name
is captured from Stripe's own `account_name` on the invoice, which is a fact
Stripe reports rather than a constant hardcoded here.
"""

from datetime import datetime

from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Index, Integer,
                        String, Text, UniqueConstraint)

from app.models.models import Base, gen_uuid

# ── Stripe's own vocabularies, used verbatim ────────────────────────────────
#
# Deliberately not translated into a parallel set of local names. A second
# vocabulary is a second thing to keep in sync, and the first time it drifts a
# screen tells somebody an invoice is settled when Stripe says it is not.

INVOICE_DRAFT = "draft"
INVOICE_OPEN = "open"
INVOICE_PAID = "paid"
INVOICE_UNCOLLECTIBLE = "uncollectible"
INVOICE_VOID = "void"
INVOICE_STATUSES = (INVOICE_DRAFT, INVOICE_OPEN, INVOICE_PAID,
                    INVOICE_UNCOLLECTIBLE, INVOICE_VOID)

PAYMENT_SUCCEEDED = "succeeded"
PAYMENT_FAILED = "failed"
PAYMENT_PENDING = "pending"
PAYMENT_REFUNDED = "refunded"
PAYMENT_PARTIALLY_REFUNDED = "partially_refunded"
PAYMENT_STATUSES = (PAYMENT_SUCCEEDED, PAYMENT_FAILED, PAYMENT_PENDING,
                    PAYMENT_REFUNDED, PAYMENT_PARTIALLY_REFUNDED)

# What kind of charge this invoice represents. The implementation fee is a
# ONE-TIME charge that sits alongside the recurring rate and is never folded
# into it - package_pricing.py is explicit that they are different money.
INVOICE_KIND_SUBSCRIPTION = "subscription"
INVOICE_KIND_IMPLEMENTATION = "implementation"
INVOICE_KIND_ONE_TIME = "one_time"
INVOICE_KIND_MANUAL = "manual"

# Webhook ledger states.
EVENT_RECEIVED = "received"
EVENT_PROCESSED = "processed"
EVENT_IGNORED = "ignored"
EVENT_FAILED = "failed"


class StripeWebhookEvent(Base):
    """THE IDEMPOTENCY LEDGER. The most important table in this module.

    Stripe retries, and will deliver the same event more than once - that is
    documented behaviour, not an edge case. Without a uniqueness claim taken
    BEFORE any financial state is touched, a redelivered `charge.refunded`
    refunds twice in our books while Stripe refunded once.

    The claim is the UNIQUE constraint on `stripe_event_id`. The handler
    inserts here first; an IntegrityError means "already seen" and the request
    returns 200 having changed nothing.

    `payload_json` is kept so a failed event can be replayed and so an
    incident can be reconstructed. It is Stripe's own payload: it contains no
    card number and no CVC, because Stripe never sends those.
    """
    __tablename__ = "stripe_webhook_events"

    id = Column(String, primary_key=True, default=gen_uuid)

    # The uniqueness claim. NOT the primary key: a natural key as PK makes
    # every future foreign key carry Stripe's id, and this table is internal.
    stripe_event_id = Column(String, nullable=False, unique=True, index=True)

    event_type = Column(String, nullable=False, index=True)
    api_version = Column(String, nullable=True)

    # Which Stripe account delivered it. NULL today (single account); P1 fills
    # it. Present now so the ledger does not need rewriting when a second
    # merchant entity arrives with its own account and its own webhook secret.
    stripe_account_id = Column(String, nullable=True, index=True)
    merchant_entity_id = Column(String, nullable=True, index=True)

    # STRIPE'S OWN TIMESTAMP, not ours. Stripe does not guarantee delivery
    # order - `invoice.paid` can arrive before `invoice.finalized` - so
    # ordering decisions must use the time the event was CREATED, never the
    # time it happened to reach us.
    event_created_at = Column(DateTime, nullable=True, index=True)

    payload_json = Column(Text, nullable=True)

    processing_status = Column(String, default=EVENT_RECEIVED, nullable=False,
                               index=True)
    # Why an event was deliberately not applied. An ignored event is a fact to
    # record, not an error to hide: "no organization for this Stripe customer"
    # is the difference between a bug and a webhook for somebody else's data.
    ignored_reason = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)
    attempts = Column(Integer, default=0, nullable=False)

    received_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    processed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_stripe_events_status_received", "processing_status", "received_at"),
        Index("ix_stripe_events_type_created", "event_type", "event_created_at"),
    )


class Invoice(Base):
    """One Stripe invoice, mirrored locally.

    Every amount is integer minor units. Every status is Stripe's own. The
    snapshot columns at the bottom exist because an invoice is a historical
    financial record: re-rendering last year's invoice from today's mutable
    organization, brand and entity rows would show a document that was never
    issued.
    """
    __tablename__ = "invoices"

    id = Column(String, primary_key=True, default=gen_uuid)

    organization_id = Column(String, ForeignKey("organizations.id"),
                             nullable=False, index=True)
    platform_id = Column(String, nullable=True, index=True)
    # No ForeignKey: merchant_entities arrives in P1. Nullable so P0 can mirror
    # invoices today and P1 can fill them in without a schema change.
    merchant_entity_id = Column(String, nullable=True, index=True)
    # Same: billing_agreements arrives in P2.
    billing_agreement_id = Column(String, nullable=True, index=True)

    stripe_invoice_id = Column(String, nullable=False, unique=True, index=True)
    stripe_customer_id = Column(String, nullable=True, index=True)
    stripe_subscription_id = Column(String, nullable=True, index=True)

    # Stripe's human-facing invoice number. This is what a customer quotes back
    # to you on the phone, so it is stored rather than derived.
    number = Column(String, nullable=True, index=True)

    kind = Column(String, default=INVOICE_KIND_SUBSCRIPTION, nullable=True)
    status = Column(String, default=INVOICE_DRAFT, nullable=False, index=True)
    collection_method = Column(String, nullable=True)
    currency = Column(String, default="USD", nullable=False)

    subtotal_cents = Column(Integer, nullable=True)
    tax_cents = Column(Integer, nullable=True)
    discount_cents = Column(Integer, nullable=True)
    total_cents = Column(Integer, nullable=True)
    amount_paid_cents = Column(Integer, nullable=True)
    amount_due_cents = Column(Integer, nullable=True)
    amount_refunded_cents = Column(Integer, default=0, nullable=True)

    hosted_invoice_url = Column(String, nullable=True)
    invoice_pdf = Column(String, nullable=True)

    description = Column(Text, nullable=True)
    internal_note = Column(Text, nullable=True)

    period_start = Column(DateTime, nullable=True)
    period_end = Column(DateTime, nullable=True)
    due_date = Column(DateTime, nullable=True)

    finalized_at = Column(DateTime, nullable=True)
    sent_at = Column(DateTime, nullable=True)
    paid_at = Column(DateTime, nullable=True)
    voided_at = Column(DateTime, nullable=True)
    marked_uncollectible_at = Column(DateTime, nullable=True)

    # WHY A PAYMENT FAILED, in Stripe's own words. Without these, "past due" is
    # a status with no explanation and the first question anybody asks about it
    # has to be answered by opening Stripe.
    attempt_count = Column(Integer, default=0, nullable=True)
    next_payment_attempt = Column(DateTime, nullable=True)
    last_payment_error_code = Column(String, nullable=True)
    last_payment_error_message = Column(Text, nullable=True)

    # ── historical snapshots ───────────────────────────────────────────────
    # The legal seller and the brand are DIFFERENT THINGS and both are
    # recorded. EVO INTEGRATED SOLUTIONS LLC issues; EvoSys Pro is what the
    # customer bought. Captured at mirror time from Stripe's account_name and
    # the organization's platform, so an invoice keeps saying what was true
    # when it was issued.
    merchant_legal_name = Column(String, nullable=True)
    brand_name = Column(String, nullable=True)
    bill_to_org_name = Column(String, nullable=True)
    bill_to_email = Column(String, nullable=True)

    # The last Stripe event applied to this row, and when Stripe created it.
    # This is the ordering guard: an event older than this one is stale and
    # must not overwrite newer state, however late it arrives.
    last_event_id = Column(String, nullable=True)
    last_event_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_invoices_org_status", "organization_id", "status"),
        Index("ix_invoices_org_created", "organization_id", "created_at"),
        Index("ix_invoices_status_next_attempt", "status", "next_payment_attempt"),
    )


class InvoiceLineItem(Base):
    """A line on an invoice.

    Present in P0 even though P0 renders no invoice document, because the
    alternative is re-modelling later: an invoice without its lines can show a
    total and nothing else, and the moment anybody wants to know WHAT a
    customer was charged for, the data was never captured.
    """
    __tablename__ = "invoice_line_items"

    id = Column(String, primary_key=True, default=gen_uuid)
    invoice_id = Column(String, ForeignKey("invoices.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    stripe_line_item_id = Column(String, nullable=True, index=True)

    description = Column(Text, nullable=True)
    quantity = Column(Integer, nullable=True)
    unit_amount_cents = Column(Integer, nullable=True)
    amount_cents = Column(Integer, nullable=True)
    currency = Column(String, default="USD", nullable=True)

    period_start = Column(DateTime, nullable=True)
    period_end = Column(DateTime, nullable=True)
    proration = Column(Boolean, default=False, nullable=True)

    # Where this line came from: mirrored from Stripe, or composed here later.
    source = Column(String, default="stripe", nullable=True)
    sort_order = Column(Integer, default=0, nullable=True)

    __table_args__ = (
        UniqueConstraint("invoice_id", "stripe_line_item_id",
                         name="uq_invoice_line_stripe_id"),
    )


class Payment(Base):
    """Money that actually moved.

    SEPARATE FROM Invoice on purpose. A payment can be partial, can be
    refunded later, can be retried, and can exist with no invoice at all.
    Folding it into the invoice row would mean a refund has nowhere to live
    except by mutating the invoice's paid amount, which destroys the history of
    what was collected and when.
    """
    __tablename__ = "payments"

    id = Column(String, primary_key=True, default=gen_uuid)

    organization_id = Column(String, ForeignKey("organizations.id"),
                             nullable=False, index=True)
    invoice_id = Column(String, ForeignKey("invoices.id", ondelete="SET NULL"),
                        nullable=True, index=True)
    merchant_entity_id = Column(String, nullable=True, index=True)

    stripe_payment_intent_id = Column(String, nullable=True, unique=True, index=True)
    stripe_charge_id = Column(String, nullable=True, index=True)
    stripe_invoice_id = Column(String, nullable=True, index=True)

    amount_cents = Column(Integer, nullable=True)
    currency = Column(String, default="USD", nullable=False)
    status = Column(String, default=PAYMENT_PENDING, nullable=False, index=True)
    refunded_cents = Column(Integer, default=0, nullable=True)

    # DISPLAY ONLY, and all we are ever permitted to hold. No PAN, no CVC, no
    # raw payment method - that is what keeps this application out of PCI scope
    # beyond SAQ-A, and Stripe does not send them in any case.
    payment_method_brand = Column(String, nullable=True)
    payment_method_last4 = Column(String, nullable=True)

    failure_code = Column(String, nullable=True)
    failure_message = Column(Text, nullable=True)

    paid_at = Column(DateTime, nullable=True)
    refunded_at = Column(DateTime, nullable=True)

    last_event_id = Column(String, nullable=True)
    last_event_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_payments_org_status", "organization_id", "status"),
        Index("ix_payments_org_created", "organization_id", "created_at"),
    )
