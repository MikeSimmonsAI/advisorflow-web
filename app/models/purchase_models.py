"""WHAT A CUSTOMER HAS ACTUALLY BOUGHT FROM THE CATALOGUE.

One table for both shapes, because they are the same commercial event seen
from two angles: somebody agreed to pay for a named item at a named price.
What differs is HOW Stripe collects it, and that difference lives in `kind`
and in two nullable Stripe columns rather than in two tables that would have
to be kept in step.

════════════════════════════════════════════════════════════════════════════
EACH OBLIGATION STAYS ITS OWN OBLIGATION
════════════════════════════════════════════════════════════════════════════

The platform already carries this rule for the two it had - a one-time
implementation fee and a recurring subscription are separate objects, separate
links, separate states, and a payment for one never touches the other. Add-ons
and services join that rule rather than bending it:

  A RECURRING ADD-ON becomes an ITEM on the customer's existing Stripe
  subscription. Not a second subscription - that is the double-billing defect
  this codebase has already paid for once. Stripe may invoice the base plan
  and the add-ons together, which is correct Stripe architecture; AdvisorFlow
  still knows them apart, because each row here names exactly one catalogue
  item and one price.

  A ONE-TIME SERVICE is its own `mode="payment"` checkout. It produces no
  subscription and no invoice event, so the checkout completion is the
  authoritative moment - the same reasoning that governs the setup fee.

  REMOVING AN ADD-ON deletes that subscription ITEM. It must never cancel the
  subscription, and the two operations are different Stripe calls rather than
  one call with a flag, so they cannot be confused.

════════════════════════════════════════════════════════════════════════════
THE PRICE IS SNAPSHOTTED, NOT REFERENCED
════════════════════════════════════════════════════════════════════════════

`amount_cents` is copied here at the moment of sale and never re-read from the
catalogue. A brand that raises a price next quarter must not retroactively
change what a customer already agreed to, and a report of what was sold must
not quietly restate itself. The catalogue says what something costs TODAY;
this says what somebody actually bought.
"""
from sqlalchemy import (Column, DateTime, ForeignKey, Integer, String, Text,
                        func)

from app.models.models import Base, gen_uuid


class PurchaseStatus:
    """WHERE THIS PURCHASE IS, and each value means one unambiguous thing."""

    # A checkout exists and nobody has paid. The seller can resend the link;
    # the customer owes nothing yet. NOT a sale.
    PENDING = "pending"

    # A one-time purchase whose money arrived. Terminal and good.
    PAID = "paid"

    # A recurring add-on currently on the subscription, being billed.
    ACTIVE = "active"

    # A recurring add-on that was removed, or a checkout abandoned and
    # withdrawn. Kept rather than deleted: what a customer used to pay for is
    # part of the commercial record.
    CANCELED = "canceled"

    ALL = (PENDING, PAID, ACTIVE, CANCELED)

    # The states in which this purchase is COSTING the customer money, and so
    # the ones that count towards revenue and entitlements.
    LIVE = (PAID, ACTIVE)


class PricingSource:
    """WHERE THE AMOUNT CAME FROM — the audit answer to 'who set this price'."""

    # Straight off the catalogue. Nobody chose it on this deal.
    CATALOGUE = "catalogue"

    # A quoted item priced on the deal by an authorised seller, through the
    # existing pricing-authority machinery. Carries an approval trail.
    QUOTED = "quoted"

    ALL = (CATALOGUE, QUOTED)


class CatalogPurchase(Base):
    """One customer, one catalogue item, one agreed price."""

    __tablename__ = "catalog_purchases"

    id = Column(String, primary_key=True, default=gen_uuid)

    organization_id = Column(String,
                             ForeignKey("organizations.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    # Denormalised from the org so a brand-scoped report does not have to join
    # through a customer that may since have moved brand.
    platform_id = Column(String, ForeignKey("platforms.id", ondelete="SET NULL"),
                         nullable=True, index=True)

    # SET NULL, not CASCADE. Deleting a catalogue item must not erase the
    # record that somebody bought it — the money moved, and the row is how that
    # stays answerable.
    catalog_item_id = Column(String,
                             ForeignKey("brand_catalog_items.id",
                                        ondelete="SET NULL"),
                             nullable=True, index=True)
    # Snapshots, for the same reason: what was sold, under the name it was sold
    # under, whatever the catalogue says later.
    item_key = Column(String, nullable=False)
    item_name = Column(String, nullable=False)
    kind = Column(String, nullable=False)          # CatalogItemKind

    # ── What was agreed ───────────────────────────────────────────────────
    amount_cents = Column(Integer, nullable=False)
    currency = Column(String, default="usd", nullable=False)
    quantity = Column(Integer, default=1, nullable=False)
    billing_interval = Column(String, nullable=True)   # recurring only
    pricing_source = Column(String, nullable=False, default=PricingSource.CATALOGUE)

    status = Column(String, nullable=False, default=PurchaseStatus.PENDING,
                    index=True)

    # ── Stripe ────────────────────────────────────────────────────────────
    # Which one is populated says how this was collected, and they are mutually
    # exclusive by construction: a subscription item is never a checkout and a
    # checkout never becomes a subscription item.
    stripe_subscription_item_id = Column(String, nullable=True, index=True)
    stripe_checkout_session_id = Column(String, nullable=True, index=True)
    stripe_payment_intent_id = Column(String, nullable=True, index=True)
    stripe_price_id = Column(String, nullable=True)

    # THE LINK, PERSISTED. A seller must be able to reopen, copy or resend the
    # payment page without going through browser history — the same reason the
    # deal billing links are stored rather than shown once and lost.
    checkout_url = Column(Text, nullable=True)

    # ── Who ───────────────────────────────────────────────────────────────
    # The customer buying for themselves leaves `sold_by_user_id` NULL, and a
    # seller-assisted sale names the rep. That distinction is what a commission
    # question is answered from, so it is recorded rather than inferred.
    created_by_user_id = Column(String,
                                ForeignKey("users.id", ondelete="SET NULL"),
                                nullable=True)
    sold_by_user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"),
                             nullable=True, index=True)

    note = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now())
    paid_at = Column(DateTime, nullable=True)
    canceled_at = Column(DateTime, nullable=True)

    def __repr__(self) -> str:                      # pragma: no cover
        return "<CatalogPurchase %s %s %s>" % (self.organization_id,
                                               self.item_key, self.status)
