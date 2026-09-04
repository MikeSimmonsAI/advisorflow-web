"""MERCHANT ENTITY — who is legally issuing the charge.

WHAT THIS IS FOR

P0 mirrors invoices and payments but has no idea who sold anything. It fills
`Invoice.merchant_legal_name` from Stripe's own `account_name`, which the P0
docstring is explicit about being a stopgap: it is a fact Stripe reports about
an account, not this application's statement about its own business. Rename the
Stripe account and every future invoice silently changes who issued it.

This module is that statement. `MerchantEntity` is the LEGAL entity behind the
Stripe merchant relationship - today EVO INTEGRATED SOLUTIONS LLC - and it is
the application's business identity. Stripe remains authoritative for whether
money moved; it is not authoritative for who we are.

THREE DIFFERENT THINGS, DELIBERATELY NOT ONE MODEL

    MerchantEntity   the legal seller. Signs, invoices, banks, is audited.
                     EVO INTEGRATED SOLUTIONS LLC.
    Platform         the BRAND - the customer-facing product identity, with
                     its own domain, logo, colours and support address.
                     EvoSys Pro. Already exists in app/models/models.py and is
                     NOT duplicated here.
    Organization     the SaaS customer being billed. Already exists, already
                     carries stripe_customer_id / stripe_subscription_id /
                     billing_status, and those are NOT duplicated here either.

One legal entity can stand behind several brands - EvoSys Pro, BookaBoost and
Harmony & Hustle are three platforms and one LLC today - which is exactly why
collapsing brand into entity would be wrong. The link therefore hangs off
Platform (`platforms.merchant_entity_id`), not off Organization: an
organization's issuer is derived from the brand it belongs to, so an
organization moving between brands cannot end up with a stale issuer.

NO SECRETS LIVE HERE

Only non-secret Stripe IDENTIFIERS and configuration STATUS. The account id
(`acct_...`) is a public identifier that appears in webhook payloads and in the
dashboard URL. API keys and webhook signing secrets stay in the environment,
where P0 already reads them from. tests/test_merchant_entity.py asserts this
structurally rather than trusting the convention to hold.

No Stripe Connect. A single first-party merchant account per entity; the
account id is recorded so a second entity can be added later without a
migration, not because platform accounts are coming.
"""

from datetime import datetime

from sqlalchemy import (Boolean, Column, DateTime, Index, String, Text,
                        UniqueConstraint)

from app.models.models import Base, gen_uuid

# ── Entity types. Deliberately small and descriptive; this is a label for a
#    human reading an invoice, not a tax engine input. ────────────────────────
ENTITY_LLC = "llc"
ENTITY_CORPORATION = "corporation"
ENTITY_SOLE_PROPRIETOR = "sole_proprietor"
ENTITY_PARTNERSHIP = "partnership"
ENTITY_TYPES = (ENTITY_LLC, ENTITY_CORPORATION, ENTITY_SOLE_PROPRIETOR,
                ENTITY_PARTNERSHIP)

# ── Stripe configuration status.
#
# What this records is whether the APPLICATION has been told about the merchant
# account, not whether Stripe considers it in good standing. Those are separate
# facts and conflating them would let a screen claim an account is healthy on
# the strength of a row somebody typed. VERIFIED means a read-only Stripe call
# confirmed the account id resolves - which P1 does NOT make; it is here so a
# later phase has somewhere honest to put the answer.
STRIPE_UNCONFIGURED = "unconfigured"
STRIPE_CONFIGURED = "configured"
STRIPE_VERIFIED = "verified"
STRIPE_DISABLED = "disabled"
STRIPE_CONFIG_STATUSES = (STRIPE_UNCONFIGURED, STRIPE_CONFIGURED,
                          STRIPE_VERIFIED, STRIPE_DISABLED)

# The legal entity and brand in operation today. Named constants rather than
# rows created by import: seeding is an explicit call (see
# app/services/merchant_entity.py), so importing this module never writes.
EVO_LEGAL_NAME = "EVO INTEGRATED SOLUTIONS LLC"
EVO_SLUG = "evo-integrated-solutions"
EVOSYS_PRO_PLATFORM_SLUG = "evosyspro"


class MerchantEntity(Base):
    """The legal business entity responsible for a Stripe merchant account."""

    __tablename__ = "merchant_entities"

    id = Column(String, primary_key=True, default=gen_uuid)

    # ── legal identity ─────────────────────────────────────────────────────
    # legal_name is the string that goes on an invoice as the issuer. It is
    # UNIQUE because two rows claiming to be the same company is not a state
    # worth being able to represent - it makes "who issued this" ambiguous
    # exactly where it must not be.
    legal_name = Column(String, nullable=False, unique=True)
    # A stable handle for code and seeds to reference, so nothing has to match
    # on the legal name - which can change, and whose change must not break
    # lookups.
    slug = Column(String, nullable=False, unique=True, index=True)
    display_name = Column(String, nullable=True)      # "Evo Integrated Solutions"
    entity_type = Column(String, default=ENTITY_LLC, nullable=True)

    # ── where it is registered, for the invoice footer ─────────────────────
    # Held as plain columns rather than one blob because an invoice template
    # needs the parts. NOT a tax engine and NOT Stripe Tax.
    jurisdiction = Column(String, nullable=True)      # "TX", "DE"
    address_line1 = Column(String, nullable=True)
    address_line2 = Column(String, nullable=True)
    address_city = Column(String, nullable=True)
    address_region = Column(String, nullable=True)
    address_postal_code = Column(String, nullable=True)
    address_country = Column(String, default="US", nullable=True)

    # ── how a customer reaches the seller ──────────────────────────────────
    billing_email = Column(String, nullable=True)
    support_email = Column(String, nullable=True)
    support_phone = Column(String, nullable=True)
    website_url = Column(String, nullable=True)

    # ── Stripe: IDENTIFIERS AND STATUS ONLY ────────────────────────────────
    #
    # acct_... is not a credential. It is in every webhook payload and in the
    # dashboard URL, and recording it is what lets a webhook be attributed to
    # an entity - StripeWebhookEvent.stripe_account_id has been sitting empty
    # since P0 waiting for exactly this.
    #
    # UNIQUE: one Stripe account belongs to one legal entity. Two entities
    # pointing at the same account would make attribution a coin toss.
    stripe_account_id = Column(String, nullable=True, unique=True, index=True)
    # EXTERNAL CONFIRMATION, NEVER THE SOURCE OF TRUTH. Stripe's own name for
    # the account, cached so a mismatch with legal_name is VISIBLE rather than
    # silently authoritative the way it was in P0.
    stripe_account_name_cached = Column(String, nullable=True)
    stripe_account_name_checked_at = Column(DateTime, nullable=True)
    stripe_livemode = Column(Boolean, nullable=True)
    stripe_config_status = Column(String, default=STRIPE_UNCONFIGURED,
                                  nullable=False, index=True)
    stripe_config_note = Column(Text, nullable=True)

    # ── lifecycle ──────────────────────────────────────────────────────────
    is_active = Column(Boolean, default=True, nullable=False)
    # The entity used when nothing more specific is known. Enforced as at most
    # one by app/services/merchant_entity.py rather than by a partial unique
    # index, because a partial index is not portable to the SQLite the tests
    # run on, and a constraint that exists only in production is not one
    # anybody can rely on.
    is_default = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("slug", name="uq_merchant_entities_slug"),
        UniqueConstraint("legal_name", name="uq_merchant_entities_legal_name"),
        Index("ix_merchant_entities_active_default", "is_active", "is_default"),
    )

    def __repr__(self):                                  # pragma: no cover
        return "<MerchantEntity %s %r>" % (self.slug, self.legal_name)


# The invoice snapshot field names P0 already declared on Invoice. Kept next to
# the model so the list cannot drift from what the model can answer. A snapshot
# is taken ONCE, at issue time, and never refreshed: an invoice that has been
# paid must keep saying who issued it even after the company is renamed.
SNAPSHOT_FIELDS = ("merchant_entity_id", "merchant_legal_name",
                   "platform_id", "brand_name")
