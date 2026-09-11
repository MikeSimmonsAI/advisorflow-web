"""GOD MODE OWNS THE COMMERCIAL CATALOGUE.

Everything a brand sells that is not the base subscription is configured here
and nowhere else. The seller surfaces and the customer surfaces CONSUME this
catalogue according to their own authority; neither of them creates items,
prices them, or decides who may buy them.

That separation is the point. A seller who could invent a product would be
inventing a price, and a customer screen that could enable an item would be
deciding what the company sells.

NO PRICES ARE SEEDED HERE. An item is created unpriced, inactive to every
audience, and refused by every purchase path until somebody configures it
deliberately. A price that appears without a person entering it is a price
nobody agreed to.

Extends the existing God Billing console rather than standing up a second
control plane — same `require_god`, same audit helper, same preview-then-apply
shape for anything that reaches Stripe.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, require_god
from app.models.models import Platform, User
from app.models.catalog_models import (BrandCatalogItem, CatalogItemKind,
                                       CatalogPricingMode)
from app.services import brand_catalog
from app.routers.audit_log_router import log_action

log = logging.getLogger(__name__)
router = APIRouter(prefix="/god/catalog", tags=["god-catalog"])

AUDIT_TARGET = "brand_catalog_item"


def _audit(db: Session, user: User, action: str, target_id: Optional[str],
           before: Optional[dict] = None, after: Optional[dict] = None) -> None:
    """Catalogue edits are commercial decisions, so they leave a trail.

    Never load-bearing: an audit failure must not stop a configuration change
    that has already been made, or the operator retries and makes it twice.
    """
    try:
        log_action(db, None, getattr(user, "id", None), action=action,
                   target_type=AUDIT_TARGET, target_id=target_id,
                   details={"before": before, "after": after})
    except Exception:                                    # pragma: no cover
        log.exception("god_catalog: audit write failed for %s", action)


def _require_platform(db: Session, platform_id: str) -> Platform:
    platform = db.query(Platform).filter(Platform.id == platform_id).first()
    if platform is None:
        raise HTTPException(status_code=404, detail="No such brand.")
    return platform


class CatalogItemIn(BaseModel):
    """A new item. Deliberately minimal to CREATE, fully editable after.

    Only the identity and the shape are required. Price, availability and
    Stripe mapping are separate deliberate steps, so the reflex act of adding
    something to the catalogue cannot also put it on sale.
    """
    key: str
    name: str
    kind: str
    pricing_mode: str = CatalogPricingMode.FIXED
    customer_description: Optional[str] = None
    internal_description: Optional[str] = None
    amount_cents: Optional[int] = None
    currency: str = "usd"
    billing_interval: Optional[str] = None
    entitlement_key: Optional[str] = None
    entitlement_value: Optional[int] = None
    category: Optional[str] = None
    sort_order: int = 0


class CatalogItemPatch(BaseModel):
    """Every field is optional; only what is sent is changed.

    `self_service` and `seller_assisted` are here rather than on create for the
    reason above — enabling an item is its own decision, made once somebody has
    looked at the price.
    """
    name: Optional[str] = None
    customer_description: Optional[str] = None
    internal_description: Optional[str] = None
    kind: Optional[str] = None
    pricing_mode: Optional[str] = None
    amount_cents: Optional[int] = None
    currency: Optional[str] = None
    billing_interval: Optional[str] = None
    stripe_product_id: Optional[str] = None
    stripe_price_id: Optional[str] = None
    self_service: Optional[bool] = None
    seller_assisted: Optional[bool] = None
    is_active: Optional[bool] = None
    entitlement_key: Optional[str] = None
    entitlement_value: Optional[int] = None
    category: Optional[str] = None
    sort_order: Optional[int] = None


@router.get("/brands/{platform_id}/items")
def list_items(platform_id: str,
               kind: Optional[str] = Query(None),
               include_inactive: bool = Query(True),
               db: Session = Depends(get_db),
               user: User = Depends(require_god)):
    """This brand's whole catalogue, with a straight answer per row about
    whether it can currently be sold.

    INACTIVE ROWS ARE INCLUDED BY DEFAULT, unlike every customer-facing
    listing. This is the configuration screen: the item somebody disabled and
    forgot is exactly what an operator came here to find, and hiding it would
    make the catalogue look complete when it is not.
    """
    _require_platform(db, platform_id)
    items = brand_catalog.items_for(db, platform_id, kind=kind,
                                    active_only=not include_inactive)
    out = [brand_catalog.admin_out(i) for i in items]

    return {
        "platform_id": platform_id,
        "items": out,
        "counts": {
            "total": len(out),
            "sellable": sum(1 for i in out if i["sellable"]),
            "self_service": sum(1 for i in out if i["self_service"]
                                and i["sellable"]),
            "seller_assisted": sum(1 for i in out if i["seller_assisted"]
                                   and i["sellable"]),
            "needs_stripe_mapping": sum(1 for i in out
                                        if i["needs_stripe_mapping"]),
            "blocked": sum(1 for i in out if i["blockers"]),
        },
        "kinds": [{"key": k, "label": CatalogItemKind.LABELS[k]}
                  for k in CatalogItemKind.ALL],
        "pricing_modes": [{"key": m, "label": CatalogPricingMode.LABELS[m]}
                          for m in CatalogPricingMode.ALL],
        "explanation": (
            "Nothing here is sellable until it is priced and enabled for an "
            "audience. `blockers` says what is stopping a row that looks "
            "configured but is not."
        ),
    }


@router.post("/brands/{platform_id}/items")
def create_item(platform_id: str, body: CatalogItemIn,
                db: Session = Depends(get_db),
                user: User = Depends(require_god)):
    """Add an item to this brand's catalogue.

    CREATED OFF. `self_service` and `seller_assisted` are not settable here
    and default to false, so a newly added item is visible to this screen and
    to nobody else. Enabling it is a second, deliberate call made once
    somebody has looked at the price.
    """
    _require_platform(db, platform_id)

    problems = brand_catalog.validate(body.kind, body.pricing_mode,
                                      body.amount_cents, body.billing_interval)
    if problems:
        # 422 rather than 400: the request was understood and is internally
        # inconsistent. The message lists EVERY problem, so the operator is
        # not told about them one save at a time.
        raise HTTPException(status_code=422, detail=" ".join(problems))

    existing = brand_catalog.resolve(db, platform_id, body.key)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail="This brand already has an item with the key %r. Keys are "
                   "how every other surface refers to an item, so they cannot "
                   "be reused." % body.key)

    item = BrandCatalogItem(
        platform_id=platform_id,
        key=body.key, name=body.name,
        customer_description=body.customer_description,
        internal_description=body.internal_description,
        kind=body.kind, pricing_mode=body.pricing_mode,
        amount_cents=body.amount_cents, currency=body.currency or "usd",
        billing_interval=body.billing_interval,
        entitlement_key=body.entitlement_key,
        entitlement_value=body.entitlement_value,
        category=body.category, sort_order=body.sort_order,
        # Off. See the docstring.
        self_service=False, seller_assisted=False, is_active=True,
    )
    db.add(item)
    db.commit()
    db.refresh(item)

    _audit(db, user, "catalog.item_created", item.id,
           after={"key": item.key, "kind": item.kind,
                  "pricing_mode": item.pricing_mode,
                  "amount_cents": item.amount_cents})
    db.commit()
    return brand_catalog.admin_out(item)


@router.patch("/brands/{platform_id}/items/{item_id}")
def update_item(platform_id: str, item_id: str, body: CatalogItemPatch,
                db: Session = Depends(get_db),
                user: User = Depends(require_god)):
    """Change one item. Only the fields sent are touched.

    THE WHOLE ROW IS RE-VALIDATED, not just the fields that moved. Changing a
    recurring add-on to one-time while leaving its interval behind would
    produce a one-time item that bills every month, and validating only the
    edited field is exactly how that gets through.
    """
    _require_platform(db, platform_id)
    item = (db.query(BrandCatalogItem)
            .filter(BrandCatalogItem.id == item_id,
                    BrandCatalogItem.platform_id == platform_id)
            .first())
    if item is None:
        raise HTTPException(status_code=404, detail="No such catalogue item.")

    data = body.model_dump(exclude_unset=True)
    before = brand_catalog.admin_out(item)

    merged_kind = data.get("kind", item.kind)
    merged_mode = data.get("pricing_mode", item.pricing_mode)
    merged_amount = data.get("amount_cents", item.amount_cents)
    merged_interval = data.get("billing_interval", item.billing_interval)

    problems = brand_catalog.validate(merged_kind, merged_mode, merged_amount,
                                      merged_interval)
    if problems:
        raise HTTPException(status_code=422, detail=" ".join(problems))

    for field, value in data.items():
        setattr(item, field, value)
    db.commit()
    db.refresh(item)

    after = brand_catalog.admin_out(item)
    # Only what MOVED, so the trail reads as a change rather than a snapshot.
    changed = {k: {"from": before.get(k), "to": after.get(k)}
               for k in data if before.get(k) != after.get(k)}
    _audit(db, user, "catalog.item_updated", item.id,
           before={k: v["from"] for k, v in changed.items()},
           after={k: v["to"] for k, v in changed.items()})
    db.commit()
    return after


class ProvisionIn(BaseModel):
    # Preview by default, like the brand plan provisioning it sits beside.
    apply: bool = False


@router.post("/brands/{platform_id}/stripe/provision")
def provision_catalog(platform_id: str, body: ProvisionIn = ProvisionIn(),
                      db: Session = Depends(get_db),
                      user: User = Depends(require_god)):
    """Create or reuse this brand's catalogue Products and Prices in Stripe.

    Idempotent, TEST-mode only, and safe to run repeatedly — the same rules
    the brand plan provisioning follows, through the same `_client` refusal so
    there is one answer to "may this service create Stripe objects".

    Quoted and unpriced items are SKIPPED with a reason rather than failing the
    run: a quoted item has no catalogue amount by design, and an unpriced one
    is simply not configured yet.
    """
    from app.services import catalog_provisioning, stripe_provisioning

    platform = _require_platform(db, platform_id)

    try:
        if not body.apply:
            return catalog_provisioning.provision_brand_catalog(
                db, platform, dry_run=True)

        # The preview runs first and is what the audit records, so the row
        # says what was INTENDED in the same transaction that performs it.
        planned = catalog_provisioning.provision_brand_catalog(
            db, platform, dry_run=True)
        result = catalog_provisioning.provision_brand_catalog(
            db, platform, dry_run=False)
    except stripe_provisioning.ProvisioningRefused as exc:
        # 409: understood and refused on a precondition the caller can fix —
        # a live key, or no key at all.
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception:                                    # pragma: no cover
        log.exception("catalog provisioning failed for %s", platform_id)
        raise HTTPException(
            status_code=502,
            detail="Stripe refused the provisioning request. Nothing was "
                   "written. See the service log for the processor's reply.")

    _audit(db, user, "catalog.stripe_provisioned", platform_id,
           before={"planned": planned.get("summary")},
           # PUBLIC object ids only. prod_… and price_… appear on the
           # customer's own receipt; no key or secret reaches this row.
           after={"summary": result.get("summary"),
                  "mapped": {r["key"]: (r.get("price") or {}).get("id")
                             for r in result.get("items", [])
                             if not r.get("skipped")}})
    db.commit()
    return result
