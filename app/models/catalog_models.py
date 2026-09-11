"""THE THINGS A BRAND SELLS THAT ARE NOT THE SUBSCRIPTION ITSELF.

════════════════════════════════════════════════════════════════════════════
WHY THIS IS A SEPARATE TABLE FROM `brand_billing_plans`
════════════════════════════════════════════════════════════════════════════

A BASE PLAN IS WHAT THE CUSTOMER *IS*. Exactly one at a time, it drives
entitlements through `billing_plan_key`, and `resolve_plan` answers "what tier
is this organization on". Every one of those sentences is false for an add-on:
a customer can hold several at once, they sit alongside the plan rather than
replacing it, and a one-time service is not a subscription at all.

Folding them into `brand_billing_plans` would make `resolve_plan` ambiguous —
and the failure mode is not cosmetic. An add-on row reachable by
`require_purchasable` is an add-on a customer can buy AS THEIR BASE PLAN,
which would silently replace the tier they are paying for with "Additional
Users". The tables are separate so that cannot be expressed.

WHAT IS SHARED IS SHARED. Brand scoping, the Stripe product/price mapping
convention, integer cents, and the rule that a customer names an ITEM and the
server resolves the money — all identical, deliberately, so there is one way
to think about commercial configuration rather than two.

════════════════════════════════════════════════════════════════════════════
NOTHING HERE IS SELLABLE UNTIL SOMEBODY SAYS SO
════════════════════════════════════════════════════════════════════════════

Every availability flag defaults to FALSE and every amount defaults to NULL.
A newly created item is visible to God Mode and to nobody else, priced at
nothing, and refused by every purchase path. That is the correct default for
a table whose rows charge people money: the cost of an item nobody enabled is
one configuration step, and the cost of an item enabled by default is a
customer buying something the brand never decided to sell.

THERE ARE NO SEEDED PRICES IN THIS MODULE, and there should never be. The
catalogue is brand configuration. A price that appears without somebody
entering it is a price nobody agreed to.
"""
from sqlalchemy import (Boolean, Column, ForeignKey, Integer, String, Text,
                        UniqueConstraint, func)
from sqlalchemy.types import DateTime

from app.models.models import Base, gen_uuid


class CatalogItemKind:
    """WHAT SHAPE OF OBLIGATION THIS ITEM CREATES.

    Not a display category — the kind decides which purchase path may sell it,
    which Stripe object it becomes, and which of the customer's separate
    obligations it joins. `billing_separation` in the engineering protocol is
    the rule this enum exists to keep: a one-time service must never be able to
    turn into a recurring charge by a configuration edit.
    """

    # Billed every period alongside the base subscription, as an additional
    # item on the SAME Stripe subscription. Additional users, extra capacity,
    # a premium integration.
    RECURRING_ADDON = "recurring_addon"

    # Charged once. Implementation work, a data migration, a training session.
    # NEVER joins the subscription: a one-time payment that started a
    # subscription is the defect this platform has already fixed once.
    ONE_TIME = "one_time"

    ALL = (RECURRING_ADDON, ONE_TIME)

    LABELS = {
        RECURRING_ADDON: "Recurring add-on",
        ONE_TIME: "One-time product or service",
    }


class CatalogPricingMode:
    """WHETHER THE PRICE IS SETTLED OR NEGOTIATED."""

    # The catalogue holds the amount. Nobody may charge a different one
    # without the pricing-authority machinery.
    FIXED = "fixed"

    # The amount belongs to the deal, not the catalogue — bespoke development,
    # a migration whose size is unknown until somebody looks. Carries NO
    # amount here on purpose: a "suggested" price on a quoted item is a price
    # that gets quoted.
    QUOTED = "quoted"

    ALL = (FIXED, QUOTED)

    LABELS = {FIXED: "Fixed price", QUOTED: "Quoted / custom"}


class BrandCatalogItem(Base):
    """One sellable thing, owned by one brand.

    THE CUSTOMER NAMES AN ITEM. THE SERVER RESOLVES THE MONEY. Same rule as
    the plan catalogue, and for the same reason: an amount that travels in a
    request is an amount somebody can edit.
    """

    __tablename__ = "brand_catalog_items"
    __table_args__ = (
        # A key is how every other surface refers to this item, so it has to
        # be stable and unambiguous WITHIN a brand. Across brands the same key
        # is fine and expected — two brands both selling "extra_users" are
        # selling two different things at two different prices.
        UniqueConstraint("platform_id", "key", name="uq_catalog_brand_key"),
    )

    id = Column(String, primary_key=True, default=gen_uuid)

    # WHICH BRAND SELLS THIS. Not nullable, for the reason the plan table
    # gives: an item with no brand is an item offered to every brand's
    # customers.
    platform_id = Column(String, ForeignKey("platforms.id", ondelete="CASCADE"),
                         nullable=False, index=True)

    key = Column(String, nullable=False)          # extra_users | data_migration
    name = Column(String, nullable=False)         # what the customer reads

    # WHAT THE CUSTOMER IS TOLD, and what the team needs to know. Separate
    # because they are different audiences with different needs: the customer
    # description appears on an invoice and a purchase confirmation; the
    # internal note is for whoever has to deliver or support the thing.
    customer_description = Column(Text, nullable=True)
    internal_description = Column(Text, nullable=True)

    kind = Column(String, nullable=False)          # CatalogItemKind
    pricing_mode = Column(String, nullable=False,
                          default=CatalogPricingMode.FIXED)

    # ── Price ─────────────────────────────────────────────────────────────
    #
    # Integer cents, never float. NULL means NOT PRICED, which is a real and
    # common state: a newly created item, or a quoted item whose amount lives
    # on the deal. Not priced is not sellable — every purchase path refuses it
    # rather than inventing a figure or falling back to zero. Zero would be
    # worse than refusing: it is a real price, and it is free.
    amount_cents = Column(Integer, nullable=True)
    currency = Column(String, default="usd", nullable=False)

    # RECURRING ITEMS ONLY. NULL on a one-time item, and that is enforced
    # rather than assumed — an interval on a one-time product is the first
    # step towards it quietly becoming a subscription.
    billing_interval = Column(String, nullable=True)   # BillingInterval

    # ── Stripe object mapping ─────────────────────────────────────────────
    # Public identifiers (prod_…, price_…), not secrets — the same values that
    # appear on the customer's own receipt. Never shown in a customer-facing
    # payload all the same: an id on a screen is an id in a request.
    #
    # NULL means "not provisioned yet". Provisioning creates what is missing
    # and reuses what exists, exactly as the brand plan provisioning does.
    stripe_product_id = Column(String, nullable=True)
    stripe_price_id = Column(String, nullable=True)

    # ── Who may sell it ───────────────────────────────────────────────────
    #
    # BOTH DEFAULT TO FALSE. An item exists in the catalogue before anybody
    # decides who may buy it, and the safe answer in between is nobody. These
    # are separate flags because they are separate decisions: a professional
    # service a seller scopes on a call is not a thing to put behind a
    # self-serve button, and a small capacity top-up is exactly that.
    self_service = Column(Boolean, default=False, nullable=False)
    seller_assisted = Column(Boolean, default=False, nullable=False)

    is_active = Column(Boolean, default=True, nullable=False)

    # ── What buying it actually DOES ──────────────────────────────────────
    #
    # Optional, and honest about being optional. Plenty of real items grant no
    # entitlement at all — a training session, a migration, priority support
    # answered by people rather than by a flag. NULL says "this is sold and
    # delivered, it does not change what the software permits", which is a
    # better record than inventing a key that nothing reads.
    #
    # `entitlement_key` names a limit or feature; `entitlement_value` is how
    # much of it one unit grants. The consuming side is deliberately not built
    # here: wiring an entitlement that nothing enforces would be a claim this
    # module cannot keep.
    entitlement_key = Column(String, nullable=True)
    entitlement_value = Column(Integer, nullable=True)

    # ── How it is presented ───────────────────────────────────────────────
    category = Column(String, nullable=True)       # "Capacity", "Services"
    sort_order = Column(Integer, default=0, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now())

    def __repr__(self) -> str:                      # pragma: no cover
        return "<BrandCatalogItem %s/%s %s>" % (self.platform_id, self.key,
                                                self.kind)
