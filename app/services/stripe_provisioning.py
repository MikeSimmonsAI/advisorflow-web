"""
CREATING THE STRIPE OBJECTS A BRAND'S CATALOGUE ALREADY DESCRIBES.

WHAT THIS SOLVES. `BrandBillingPlan` holds the amounts — `monthly_cents` for a
committed term, `month_to_month_cents` for the same tier with no commitment —
and three columns for the Stripe ids those amounts should map to. Nothing in
the platform could create the Stripe objects, so every brand's mapping columns
stayed NULL and every checkout fell back to `price_data`, minting an anonymous
one-off Price per checkout: thousands of orphan Prices, no Product, and nothing
in the Stripe dashboard that can be reconciled against the catalogue.

The alternative was a person creating six Prices by hand per brand and pasting
six ids into a form, which is a configuration step that has to be repeated
correctly for every white-label brand forever.

═══════════════════════════════════════════════════════════════════════════
THE CATALOGUE IS THE INPUT. THIS MODULE INVENTS NO PRICE.
═══════════════════════════════════════════════════════════════════════════
Every amount comes from the plan row. There is no dict of tiers in this file,
no $500, no $597 — a figure that appeared here would be a second opinion about
what a brand charges, and the one nobody edits is the one that goes stale.
A plan with no configured cents for a commitment is SKIPPED and reported as
skipped; it is never created at zero.

═══════════════════════════════════════════════════════════════════════════
TEST MODE ONLY. THIS REFUSES A LIVE KEY.
═══════════════════════════════════════════════════════════════════════════
The guard is on the KEY, not on a flag a caller passes. `sk_live_`/`rk_live_`
is refused outright before a single Stripe call is made, so this endpoint
cannot create live-mode objects even if somebody points it at a live key by
mistake. Creating a Product in live mode is not destructive on its own, but it
puts a purchasable Price in front of real customers, and that is not a thing to
do by accident from an internal tool.

═══════════════════════════════════════════════════════════════════════════
IDEMPOTENT, AND NOT BY LUCK
═══════════════════════════════════════════════════════════════════════════
Running this twice must not produce two Prices for one plan. Three layers, in
order, before anything is created:

  1. THE MAPPING COLUMN. If the plan already names a price id, that price is
     retrieved and checked against the catalogue (amount, currency, interval,
     active). Matching means reuse and no write.
  2. THE PRODUCT'S OWN PRICES. Stripe is asked for the active Prices on the
     brand's Product and one matching the catalogue is adopted. This is what
     makes the operation safe after a database restore, or when somebody
     created the Price by hand first.
  3. ONLY THEN is a Price created.

Lookup is by METADATA (`platform_id`, `plan_key`, `commitment`), never by a
hard-coded id, which is what keeps it working for a brand nobody has set up
yet.

A PRICE IS NEVER EDITED. Stripe Prices are immutable by design — an amount
change means a NEW Price, and the old one keeps billing existing subscribers
until they are migrated. When the catalogue amount no longer matches the mapped
Price this module reports `amount_changed` and creates the new Price, and the
old one is left alone rather than deactivated: switching existing subscribers
is a billing decision with customer consequences, not a provisioning step.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.billing_models import BrandBillingPlan
from app.models.models import Platform

log = logging.getLogger(__name__)

# The two commitments a tier is sold on. Each names the column holding its
# amount and the column holding its Stripe Price id, so adding a third
# commitment later is a row here rather than a new branch in the loop.
COMMITMENTS = (
    {
        "key": "term",
        "label": "Committed term",
        "cents_attr": "monthly_cents",
        "price_attr": "stripe_price_id_monthly",
    },
    {
        "key": "month_to_month",
        "label": "Month-to-month",
        "cents_attr": "month_to_month_cents",
        "price_attr": "stripe_price_id_month_to_month",
    },
)

LIVE_PREFIXES = ("sk_live_", "rk_live_")
TEST_PREFIXES = ("sk_test_", "rk_test_")


class ProvisioningRefused(Exception):
    """A precondition failed. The message is safe to show; it names no secret."""


def _client():
    """The Stripe SDK, with the environment's key, in TEST mode or not at all.

    Deliberately not `billing_router._stripe_client`: that one raises an
    HTTPException (a routing concern in a service module) and, more
    importantly, does not care whether the key is live. Here that is the whole
    point.
    """
    key = (os.environ.get("STRIPE_SECRET_KEY") or "").strip()
    if not key:
        raise ProvisioningRefused(
            "STRIPE_SECRET_KEY is not set on this service, so no Stripe object "
            "can be created. Set it in the environment; it is never stored in "
            "the database.")
    if key.startswith(LIVE_PREFIXES):
        raise ProvisioningRefused(
            "This endpoint provisions TEST-mode objects only and the configured "
            "Stripe key is a LIVE key. Creating a purchasable Price in live "
            "mode puts it in front of real customers. Refusing.")
    if not key.startswith(TEST_PREFIXES):
        raise ProvisioningRefused(
            "The configured Stripe key is not recognisable as a test key "
            "(expected sk_test_… or rk_test_…). Refusing rather than guessing "
            "which mode it would act in.")
    import stripe                       # imported here so a missing SDK is a
    stripe.api_key = key                # provisioning error, not an import-time
    return stripe                       # failure for the whole app.


def _plan_metadata(plan: BrandBillingPlan, commitment: Optional[str] = None
                   ) -> Dict[str, str]:
    """How an object is recognised on a later run. The lookup key, in Stripe."""
    md = {"platform_id": plan.platform_id, "plan_key": plan.key,
          "managed_by": "advisorflow"}
    if commitment:
        md["commitment"] = commitment
    return md


def _price_matches(price: Any, cents: int, currency: str) -> bool:
    """Is this Price the one the catalogue describes?

    Every dimension that makes a Price different is checked. `recurring.interval`
    is checked because a yearly Price at the same amount is a different product
    to sell, and `active` because reusing an archived Price would produce a
    checkout that fails at the till.
    """
    try:
        if not price.get("active"):
            return False
        if int(price.get("unit_amount") or -1) != int(cents):
            return False
        if str(price.get("currency") or "").lower() != str(currency or "usd").lower():
            return False
        rec = price.get("recurring") or {}
        if (rec.get("interval") or "") != "month":
            return False
        if int(rec.get("interval_count") or 1) != 1:
            return False
    except Exception:                                    # pragma: no cover
        return False
    return True


def _ensure_product(stripe, plan: BrandBillingPlan, platform: Platform,
                    dry_run: bool) -> Dict[str, Any]:
    """The brand's Product for this tier — reused if it exists, else created.

    A Product per (brand, tier), not per commitment. Term and month-to-month are
    two Prices for the SAME thing, and modelling them as two Products would put
    "Starter" in the Stripe dashboard twice and make revenue reporting per tier
    a manual join.
    """
    # 1. Already mapped.
    if plan.stripe_product_id:
        try:
            prod = stripe.Product.retrieve(plan.stripe_product_id)
            if prod and not prod.get("deleted"):
                return {"id": prod["id"], "action": "reused_mapped"}
        except Exception as exc:
            log.warning("provisioning: mapped product %s unreadable: %s",
                        plan.stripe_product_id, exc)

    # 2. Already exists in Stripe under this brand and tier.
    try:
        found = stripe.Product.search(
            query="metadata['platform_id']:'%s' AND metadata['plan_key']:'%s'"
                  % (plan.platform_id, plan.key),
            limit=1)
        data = (found or {}).get("data") or []
        if data:
            return {"id": data[0]["id"], "action": "reused_found"}
    except Exception as exc:
        # Search is not available on every API version. Falling through to
        # create is safe: the mapping column is written immediately after, so a
        # duplicate can only happen when BOTH the column is empty AND search is
        # unavailable, and the next run reuses whatever this one mapped.
        log.info("provisioning: product search unavailable (%s)", exc)

    if dry_run:
        return {"id": None, "action": "would_create"}

    prod = stripe.Product.create(
        name="%s %s" % (platform.name, plan.name),
        metadata=_plan_metadata(plan),
    )
    return {"id": prod["id"], "action": "created"}


def _ensure_price(stripe, plan: BrandBillingPlan, product_id: Optional[str],
                  commitment: Dict[str, str], cents: int, dry_run: bool
                  ) -> Dict[str, Any]:
    """The Price for one commitment on one tier. Reused wherever possible."""
    currency = (plan.currency or "usd").lower()
    mapped = getattr(plan, commitment["price_attr"], None)

    # 1. Already mapped, and still correct.
    if mapped:
        try:
            price = stripe.Price.retrieve(mapped)
            if _price_matches(price, cents, currency):
                return {"id": price["id"], "action": "reused_mapped"}
            # Mapped but WRONG. Stripe Prices are immutable, so the catalogue
            # has moved since this was created. A new Price is made; the old one
            # keeps billing whoever is already on it until somebody migrates
            # them deliberately.
            log.info("provisioning: %s/%s mapped price %s no longer matches the "
                     "catalogue", plan.key, commitment["key"], mapped)
            outcome = "amount_changed"
        except Exception as exc:
            log.warning("provisioning: mapped price %s unreadable: %s", mapped, exc)
            outcome = "remapped"
    else:
        outcome = "created"

    # 2. Already exists on the product.
    if product_id:
        try:
            existing = stripe.Price.list(product=product_id, active=True, limit=100)
            for price in (existing or {}).get("data") or []:
                if not _price_matches(price, cents, currency):
                    continue
                md = price.get("metadata") or {}
                # A Price with no commitment metadata predates this module.
                # Adopting it on an exact amount match is correct and is what
                # makes a hand-created Price usable.
                if md.get("commitment") in (None, "", commitment["key"]):
                    return {"id": price["id"], "action": "reused_found"}
        except Exception as exc:
            log.info("provisioning: price list unavailable for %s (%s)",
                     product_id, exc)

    if dry_run or not product_id:
        return {"id": None, "action": "would_create"}

    # 3. Create.
    price = stripe.Price.create(
        product=product_id,
        currency=currency,
        unit_amount=int(cents),
        recurring={"interval": "month", "interval_count": 1},
        nickname="%s — %s" % (plan.name, commitment["label"]),
        metadata=_plan_metadata(plan, commitment["key"]),
    )
    return {"id": price["id"], "action": outcome if outcome != "remapped" else "created"}


def provision_brand(db: Session, platform: Platform, *, dry_run: bool = False
                    ) -> Dict[str, Any]:
    """Create or reuse the TEST Stripe objects for every purchasable plan.

    Returns a per-plan, per-commitment report. NOTHING is committed here — the
    caller owns the transaction, so the audit entries and the id writes land
    together or not at all.
    """
    stripe = _client()

    plans = (db.query(BrandBillingPlan)
             .filter(BrandBillingPlan.platform_id == platform.id,
                     BrandBillingPlan.is_active == True)     # noqa: E712
             .order_by(BrandBillingPlan.sort_order.asc())
             .all())

    report: List[Dict[str, Any]] = []
    created = reused = skipped = 0

    for plan in plans:
        entry: Dict[str, Any] = {"plan_key": plan.key, "name": plan.name,
                                 "commitments": {}}

        # A tier that is listed but not self-serve purchasable — Enterprise —
        # has no Price to create. Reported, not silently absent, so the count
        # on the screen adds up to the number of plans.
        if not plan.is_purchasable:
            entry["skipped"] = "not purchasable — quoted, never checked out"
            report.append(entry)
            skipped += 1
            continue

        has_any = any(getattr(plan, c["cents_attr"], None) for c in COMMITMENTS)
        if not has_any:
            entry["skipped"] = "no amount configured for either commitment"
            report.append(entry)
            skipped += 1
            continue

        prod = _ensure_product(stripe, plan, platform, dry_run)
        entry["product"] = prod
        if prod["id"] and not dry_run:
            plan.stripe_product_id = prod["id"]

        for commitment in COMMITMENTS:
            cents = getattr(plan, commitment["cents_attr"], None)
            if not cents:
                # NOT an error and NOT zero. A brand may sell a tier on one
                # commitment only, and inventing the other would create a
                # purchasable price nobody agreed.
                entry["commitments"][commitment["key"]] = {
                    "status": "skipped",
                    "reason": "no %s amount configured" % commitment["label"].lower(),
                }
                continue

            result = _ensure_price(stripe, plan, prod["id"], commitment,
                                   int(cents), dry_run)
            entry["commitments"][commitment["key"]] = {
                "status": result["action"],
                "cents": int(cents),
                "price_id": result["id"],
            }
            if result["id"] and not dry_run:
                setattr(plan, commitment["price_attr"], result["id"])
            if result["action"].startswith("reused"):
                reused += 1
            elif result["id"] or dry_run:
                created += 1

        report.append(entry)

    return {
        "platform_id": platform.id,
        "brand": platform.name,
        "mode": "test",
        "dry_run": bool(dry_run),
        "plans": report,
        "summary": {"prices_created": created, "prices_reused": reused,
                    "plans_skipped": skipped},
    }
