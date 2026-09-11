"""STRIPE PRODUCTS AND PRICES FOR THE BRAND CATALOGUE.

The same shape as `stripe_provisioning` does for tiers, and deliberately so:
reuse before create, idempotent on repeat, TEST mode only, preview before
apply. Sharing that module's `_client` and refusal rules means there is one
answer to "may this service create Stripe objects", not two.

WHAT IS DIFFERENT FROM A TIER, and why it needs its own module:

  A ONE-TIME ITEM HAS NO `recurring` BLOCK AT ALL. That single difference is
  the whole reason a one-time service stays a one-time service. A Price
  created with a recurring interval turns a $750 migration into $750 every
  month, and the customer would be right to call that theft rather than a bug.
  It is asserted here rather than assumed.

  A QUOTED ITEM IS SKIPPED, not failed. Its amount lives on the deal, so there
  is no figure to mint a Price from. Skipping is the correct outcome and is
  reported as such — treating it as an error would train operators to ignore
  the report.

  AN ITEM PRICED AT NOTHING IS SKIPPED TOO. A fixed item with no amount is
  unconfigured, not free.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.models import Platform
from app.models.catalog_models import (BrandCatalogItem, CatalogItemKind,
                                       CatalogPricingMode)
from app.services import brand_catalog
from app.services.stripe_provisioning import ProvisioningRefused, _client

log = logging.getLogger(__name__)


def _item_metadata(item: BrandCatalogItem) -> Dict[str, str]:
    """How an object is recognised on a later run. The lookup key, in Stripe.

    `catalog_key` rather than `plan_key` on purpose: a catalogue item and a
    tier must never match each other's search, or provisioning one could reuse
    the other's Product and two different things would share a name on the
    customer's invoice.
    """
    return {"platform_id": item.platform_id,
            "catalog_key": item.key,
            "catalog_kind": item.kind,
            "managed_by": "advisorflow"}


def _price_matches(price: Any, item: BrandCatalogItem) -> bool:
    """Is this Price the one the catalogue describes?

    THE RECURRING SHAPE IS PART OF THE MATCH. A one-time item must match only
    a Price with NO recurring block, and a recurring item only one whose
    interval agrees. Without that check a Price left over from an earlier
    configuration could be reused after the item's kind changed, and the
    customer would be billed on the old shape.
    """
    try:
        if not price.get("active"):
            return False
        if int(price.get("unit_amount") or -1) != int(item.amount_cents or -1):
            return False
        if (str(price.get("currency") or "").lower()
                != str(item.currency or "usd").lower()):
            return False

        rec = price.get("recurring") or None
        if item.kind == CatalogItemKind.ONE_TIME:
            return rec is None
        if rec is None:
            return False
        if (rec.get("interval") or "") != (item.billing_interval or ""):
            return False
        if int(rec.get("interval_count") or 1) != 1:
            return False
    except Exception:                                    # pragma: no cover
        return False
    return True


def _ensure_product(stripe, item: BrandCatalogItem, platform: Platform,
                    dry_run: bool) -> Dict[str, Any]:
    """The brand's Product for this item — reused if it exists, else created."""
    if item.stripe_product_id:
        try:
            prod = stripe.Product.retrieve(item.stripe_product_id)
            if prod and not prod.get("deleted"):
                return {"id": prod["id"], "action": "reused_mapped"}
        except Exception as exc:
            log.warning("catalog provisioning: mapped product %s unreadable: %s",
                        item.stripe_product_id, exc)

    try:
        found = stripe.Product.search(
            query="metadata['platform_id']:'%s' AND metadata['catalog_key']:'%s'"
                  % (item.platform_id, item.key),
            limit=1)
        data = (found or {}).get("data") or []
        if data:
            return {"id": data[0]["id"], "action": "reused_found"}
    except Exception as exc:
        # Search is not on every API version. Falling through to create is
        # safe because the mapping column is written immediately afterwards.
        log.info("catalog provisioning: product search unavailable (%s)", exc)

    if dry_run:
        return {"id": None, "action": "would_create"}

    prod = stripe.Product.create(
        name="%s %s" % (platform.name, item.name),
        # The customer reads this on their invoice, so it is the CUSTOMER
        # description that goes to Stripe — never the internal one, which is
        # written for the team and may say what the work really costs us.
        description=item.customer_description or None,
        metadata=_item_metadata(item),
    )
    return {"id": prod["id"], "action": "created"}


def _ensure_price(stripe, item: BrandCatalogItem, product_id: Optional[str],
                  dry_run: bool) -> Dict[str, Any]:
    """The Price for this item. Reused wherever possible, never reshaped.

    A Price's amount and recurrence are immutable in Stripe, so a changed
    catalogue amount means a NEW Price rather than an edit. The old one is
    left alone: archiving a Price a live subscription still points at would
    break that customer's next renewal.
    """
    if item.stripe_price_id:
        try:
            price = stripe.Price.retrieve(item.stripe_price_id)
            if _price_matches(price, item):
                return {"id": price["id"], "action": "reused_mapped"}
            # Mapped but no longer describing the catalogue. Say so loudly:
            # silently minting a second Price is how a brand ends up with six
            # Prices for one product and no idea which is charged.
            log.info("catalog provisioning: mapped price %s no longer matches "
                     "item %s; a new Price is needed",
                     item.stripe_price_id, item.key)
        except Exception as exc:
            log.warning("catalog provisioning: mapped price %s unreadable: %s",
                        item.stripe_price_id, exc)

    if product_id:
        try:
            for price in (stripe.Price.list(product=product_id, active=True,
                                            limit=100) or {}).get("data") or []:
                if _price_matches(price, item):
                    return {"id": price["id"], "action": "reused_found"}
        except Exception as exc:
            log.info("catalog provisioning: price list unavailable (%s)", exc)

    if dry_run or not product_id:
        return {"id": None, "action": "would_create"}

    payload: Dict[str, Any] = {
        "product": product_id,
        "unit_amount": int(item.amount_cents),
        "currency": (item.currency or "usd").lower(),
        "metadata": _item_metadata(item),
    }
    # ══════════════════════════════════════════════════════════════════════
    # THE ONE LINE THAT KEEPS A ONE-TIME CHARGE ONE-TIME.
    # ══════════════════════════════════════════════════════════════════════
    # `recurring` is added ONLY for a recurring add-on. A one-time Price with
    # an interval is a $750 migration billed every month forever.
    if item.kind == CatalogItemKind.RECURRING_ADDON:
        payload["recurring"] = {"interval": item.billing_interval,
                                "interval_count": 1}

    price = stripe.Price.create(**payload)
    return {"id": price["id"], "action": "created"}


def provision_brand_catalog(db: Session, platform: Platform, *,
                            dry_run: bool = False) -> Dict[str, Any]:
    """Create or reuse this brand's catalogue Products and Prices.

    Idempotent: a second call creates nothing. Reuse is attempted from the
    mapping column first, then from the Product's own active Prices, before
    anything is created — so this is safe to run after a database restore, and
    safe when somebody has already made the Price by hand.

    Items that cannot yield a Price are SKIPPED WITH A REASON rather than
    failing the run. A quoted item has no catalogue amount by design, and an
    unpriced item is unconfigured; neither is an error, and treating them as
    one would teach operators to ignore this report.
    """
    stripe = _client()

    results: List[Dict[str, Any]] = []
    created_products = created_prices = reused = skipped = 0

    for item in brand_catalog.items_for(db, platform.id, active_only=False):
        row: Dict[str, Any] = {"key": item.key, "name": item.name,
                               "kind": item.kind}

        if item.pricing_mode == CatalogPricingMode.QUOTED:
            row.update(skipped=True,
                       reason="Quoted items are priced on the deal, so there "
                              "is no catalogue amount to create a Price from.")
            skipped += 1
            results.append(row)
            continue

        if item.amount_cents is None:
            row.update(skipped=True,
                       reason="No price configured. An unpriced item is "
                              "unconfigured, not free.")
            skipped += 1
            results.append(row)
            continue

        problems = brand_catalog.validate(item.kind, item.pricing_mode,
                                          item.amount_cents,
                                          item.billing_interval)
        if problems:
            row.update(skipped=True, reason=" ".join(problems))
            skipped += 1
            results.append(row)
            continue

        product = _ensure_product(stripe, item, platform, dry_run)
        price = _ensure_price(stripe, item, product["id"], dry_run)

        if not dry_run:
            if product["id"]:
                item.stripe_product_id = product["id"]
            if price["id"]:
                item.stripe_price_id = price["id"]

        created_products += 1 if product["action"] == "created" else 0
        created_prices += 1 if price["action"] == "created" else 0
        reused += 1 if price["action"].startswith("reused") else 0

        row.update(skipped=False,
                   product={"id": product["id"], "action": product["action"]},
                   price={"id": price["id"], "action": price["action"]},
                   amount_cents=item.amount_cents,
                   billing_interval=item.billing_interval)
        results.append(row)

    if not dry_run:
        db.commit()

    return {
        "platform_id": platform.id,
        "dry_run": bool(dry_run),
        "items": results,
        "summary": {
            "considered": len(results),
            "products_created": created_products,
            "prices_created": created_prices,
            "reused": reused,
            "skipped": skipped,
        },
        "explanation": (
            "Creates only what is missing. An already-mapped Price is reused, "
            "never duplicated. Quoted and unpriced items are skipped with a "
            "reason - neither is an error."
        ),
    }
