"""THE ONE PLACE THAT DECIDES WHETHER A CATALOGUE ITEM MAY BE SOLD.

Every purchase path — customer self-serve, seller-assisted, God — asks the
same questions here rather than each forming its own opinion. The plan
catalogue learned this the expensive way: when "is this buyable" lived in the
caller, one caller eventually got it wrong and a customer bought something
they should not have been offered.

THE RULES, and why each one refuses rather than resolves:

  NOT PRICED IS NOT SELLABLE. A fixed-price item with no amount is refused,
  never treated as free. Zero is a real price and it is worse than an error.

  A QUOTED ITEM CARRIES NO CATALOGUE PRICE. Its amount belongs to the deal and
  arrives through the pricing-authority machinery. Self-serve cannot sell one
  at all: there is nothing for a customer to click.

  THE KIND DECIDES THE SHAPE. A recurring item must have an interval; a
  one-time item must not. This is checked rather than assumed, because an
  interval quietly appearing on a one-time service is how a single charge
  becomes a subscription — the exact defect this platform has already fixed
  once, in the other direction.

  AVAILABILITY IS PER AUDIENCE. `self_service` and `seller_assisted` are asked
  separately. An item a seller may scope on a call is not automatically an
  item to put behind a button.

  INACTIVE IS INVISIBLE, not merely unbuyable. An inactive item is absent from
  every listing, so nobody is offered something that will then be refused.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.billing_models import BillingInterval
from app.models.catalog_models import (BrandCatalogItem, CatalogItemKind,
                                       CatalogPricingMode)


class ItemNotAvailable(RuntimeError):
    """This item cannot be sold to this audience, and the message says why.

    Carries a sentence written for the person who has to fix it, because the
    usual cause is configuration rather than a bad request.
    """


# ═══════════════════════════════════════════════════════════════════════════
# VALIDATION — what a well-formed item looks like
# ═══════════════════════════════════════════════════════════════════════════

def validate(kind: Optional[str], pricing_mode: Optional[str],
             amount_cents: Optional[int],
             billing_interval: Optional[str]) -> List[str]:
    """Every reason this combination could not be a coherent catalogue item.

    Returns a list rather than raising on the first, so somebody configuring an
    item is told everything wrong with it at once instead of discovering the
    problems one save at a time.
    """
    problems: List[str] = []

    if kind not in CatalogItemKind.ALL:
        problems.append("Kind must be one of: %s."
                        % ", ".join(CatalogItemKind.ALL))
    if pricing_mode not in CatalogPricingMode.ALL:
        problems.append("Pricing mode must be one of: %s."
                        % ", ".join(CatalogPricingMode.ALL))

    if pricing_mode == CatalogPricingMode.QUOTED and amount_cents is not None:
        problems.append(
            "A quoted item must not carry a catalogue price. Its amount comes "
            "from the deal, and a figure stored here is a figure that gets "
            "quoted.")

    if amount_cents is not None and amount_cents < 0:
        problems.append("A price cannot be negative.")

    if kind == CatalogItemKind.RECURRING_ADDON:
        if billing_interval not in BillingInterval.ALL:
            problems.append(
                "A recurring add-on needs a billing interval (%s)."
                % ", ".join(BillingInterval.ALL))
    elif kind == CatalogItemKind.ONE_TIME:
        if billing_interval:
            problems.append(
                "A one-time item must not have a billing interval. An "
                "interval is what turns a single charge into a subscription.")

    return problems


def is_sellable(item: BrandCatalogItem) -> bool:
    """Whether this item could be sold to ANYBODY, ignoring audience.

    A quoted item is sellable without a catalogue amount — that is the point of
    it. A fixed item without one is not.
    """
    if not item.is_active:
        return False
    if validate(item.kind, item.pricing_mode, item.amount_cents,
                item.billing_interval):
        return False
    if item.pricing_mode == CatalogPricingMode.FIXED:
        return item.amount_cents is not None
    return True


# ═══════════════════════════════════════════════════════════════════════════
# THE GATES — one per audience, both refusing by default
# ═══════════════════════════════════════════════════════════════════════════

def _explain_unsellable(item: BrandCatalogItem) -> str:
    """Why this item cannot be sold, in words somebody can act on."""
    if not item.is_active:
        return "%r is not active." % item.key
    problems = validate(item.kind, item.pricing_mode, item.amount_cents,
                        item.billing_interval)
    if problems:
        return "%r is misconfigured: %s" % (item.key, " ".join(problems))
    if (item.pricing_mode == CatalogPricingMode.FIXED
            and item.amount_cents is None):
        return ("%r has no price configured, so it cannot be sold. A price "
                "nobody entered is not free." % item.key)
    return "%r cannot be sold." % item.key


def require_purchasable(db: Session, platform_id: Optional[str],
                        key: Optional[str]) -> BrandCatalogItem:
    """THE SELF-SERVE GATE. A customer may buy this, or this raises.

    Scoped to the caller's own brand, so one brand's customer cannot buy — or
    discover — another brand's catalogue, which is the same defence
    `billing_catalog.require_purchasable` provides for tiers.
    """
    item = resolve(db, platform_id, key)
    if item is None:
        raise ItemNotAvailable("No item %r is available." % (key,))
    if not is_sellable(item):
        raise ItemNotAvailable(_explain_unsellable(item))
    if item.pricing_mode == CatalogPricingMode.QUOTED:
        raise ItemNotAvailable(
            "%r is quoted rather than priced, so it cannot be bought "
            "directly. Your account manager can price it for you." % item.key)
    if not item.self_service:
        raise ItemNotAvailable(
            "%r is not available for self-service purchase." % item.key)
    return item


def require_seller_sellable(db: Session, platform_id: Optional[str],
                            key: Optional[str]) -> BrandCatalogItem:
    """THE SELLER GATE. Wider than self-serve, and still a gate.

    A seller MAY sell a quoted item — pricing it is what the pricing-authority
    machinery is for — but may not sell an inactive one, a misconfigured one,
    or one this brand has not enabled for seller-assisted sale.
    """
    item = resolve(db, platform_id, key)
    if item is None:
        raise ItemNotAvailable("No item %r is available." % (key,))
    if not is_sellable(item):
        raise ItemNotAvailable(_explain_unsellable(item))
    if not item.seller_assisted:
        raise ItemNotAvailable(
            "%r is not enabled for seller-assisted sale." % item.key)
    return item


# ═══════════════════════════════════════════════════════════════════════════
# READING
# ═══════════════════════════════════════════════════════════════════════════

def resolve(db: Session, platform_id: Optional[str],
            key: Optional[str]) -> Optional[BrandCatalogItem]:
    """One item, BY BRAND AND KEY. None when this brand has no such item.

    The brand scope is not an optimisation. A key belonging to another brand
    resolves to nothing here, so it cannot be bought and its existence is not
    revealed by the difference between "no such item" and "not yours".
    """
    if not platform_id or not key:
        return None
    return (db.query(BrandCatalogItem)
            .filter(BrandCatalogItem.platform_id == platform_id,
                    BrandCatalogItem.key == key)
            .first())


def items_for(db: Session, platform_id: Optional[str],
              kind: Optional[str] = None,
              active_only: bool = True) -> List[BrandCatalogItem]:
    """This brand's catalogue, in display order."""
    if not platform_id:
        return []
    q = db.query(BrandCatalogItem).filter(
        BrandCatalogItem.platform_id == platform_id)
    if kind:
        q = q.filter(BrandCatalogItem.kind == kind)
    if active_only:
        q = q.filter(BrandCatalogItem.is_active.is_(True))
    return q.order_by(BrandCatalogItem.sort_order.asc(),
                      BrandCatalogItem.name.asc()).all()


def public_out(item: BrandCatalogItem) -> Dict[str, Any]:
    """What a CUSTOMER may see. No Stripe ids, no internal note.

    The internal description is withheld because it is written for the team —
    delivery caveats, who to route the work to, what it really costs us. The
    Stripe ids are withheld for the reason every customer-facing payload
    withholds them: an id on a screen is an id in a request.
    """
    return {
        "key": item.key,
        "name": item.name,
        "description": item.customer_description,
        "kind": item.kind,
        "kind_label": CatalogItemKind.LABELS.get(item.kind, item.kind),
        "pricing_mode": item.pricing_mode,
        "amount_cents": item.amount_cents,
        "currency": item.currency or "usd",
        "billing_interval": item.billing_interval,
        "category": item.category,
        "sort_order": item.sort_order,
        # Stated so the screen renders "Contact us" rather than a blank price.
        "is_quoted": item.pricing_mode == CatalogPricingMode.QUOTED,
    }


def admin_out(item: BrandCatalogItem) -> Dict[str, Any]:
    """Everything an operator configuring the catalogue needs, plus a
    straight answer about whether this row can currently be sold.

    `blockers` is the point of this shape. An item that looks configured but
    is not sellable is the failure an operator cannot see from the columns
    alone, so it is computed and named rather than left to be inferred.
    """
    problems = validate(item.kind, item.pricing_mode, item.amount_cents,
                        item.billing_interval)
    if (not problems and item.pricing_mode == CatalogPricingMode.FIXED
            and item.amount_cents is None):
        # The SAME sentence the purchase gates give, deliberately. An operator
        # reading why a row is blocked and a customer-facing refusal that
        # describes the same condition differently is how two people end up
        # believing they are looking at two problems.
        problems = ["No price configured, so this cannot be sold. A price "
                    "nobody entered is not free."]

    return {
        "id": item.id,
        "platform_id": item.platform_id,
        "key": item.key,
        "name": item.name,
        "customer_description": item.customer_description,
        "internal_description": item.internal_description,
        "kind": item.kind,
        "kind_label": CatalogItemKind.LABELS.get(item.kind, item.kind),
        "pricing_mode": item.pricing_mode,
        "pricing_mode_label": CatalogPricingMode.LABELS.get(
            item.pricing_mode, item.pricing_mode),
        "amount_cents": item.amount_cents,
        "currency": item.currency or "usd",
        "billing_interval": item.billing_interval,
        "stripe_product_id": item.stripe_product_id,
        "stripe_price_id": item.stripe_price_id,
        # A fixed-price item needs a Price to be charged cleanly; a quoted one
        # is priced per deal and has nothing to map in advance.
        "stripe_mapped": bool(item.stripe_price_id),
        "needs_stripe_mapping": bool(
            item.pricing_mode == CatalogPricingMode.FIXED
            and item.amount_cents is not None
            and not item.stripe_price_id),
        "self_service": bool(item.self_service),
        "seller_assisted": bool(item.seller_assisted),
        "is_active": bool(item.is_active),
        "entitlement_key": item.entitlement_key,
        "entitlement_value": item.entitlement_value,
        "category": item.category,
        "sort_order": item.sort_order,
        "sellable": is_sellable(item),
        "blockers": problems,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }
