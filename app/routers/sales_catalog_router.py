"""SELLER-ASSISTED CATALOGUE SALES.

A rep selling an add-on or a service to an existing customer, through the SAME
engine the customer's own Billing page uses. `catalog_purchase` does the work;
this module decides only who may ask and, for a quoted item, whether this
person may set the price.

════════════════════════════════════════════════════════════════════════════
WHAT A SELLER MAY AND MAY NOT DO
════════════════════════════════════════════════════════════════════════════

  THEY MAY SELL WHAT GOD ENABLED FOR THEM. `seller_assisted` is a separate
  flag from `self_service` because they are separate decisions: a service a
  rep scopes on a call is not automatically something to put behind a button,
  and a small capacity top-up is exactly that.

  THEY MAY NOT REPRICE A FIXED ITEM. Not by a little, not with a note. The
  catalogue amount is the amount, and `catalog_purchase.resolve_amount`
  refuses a figure that differs from it. An item that should be negotiable is
  configured as QUOTED — a decision that belongs to God Mode, not to the rep
  looking at it.

  THEY MAY PRICE A QUOTED ITEM ONLY WITH THE AUTHORITY TO DO SO. Which is
  where this differs from package pricing, and the difference is worth being
  explicit about rather than papering over:

      A PACKAGE has a catalogue price, so "20% off" is a meaningful, checkable
      statement and `pricing_authority.evaluate` checks it against the brand's
      configured ceilings.

      A QUOTED CATALOGUE ITEM HAS NO CATALOGUE PRICE — that is what quoted
      means. There is no percentage to compute and no floor to breach, so the
      discount-ceiling machinery has nothing to measure. Inventing a reference
      price to measure against would be inventing the commercial policy this
      module is supposed to enforce.

      So the question reduces to the one that IS answerable: may this person
      set a commercial amount at all? That is resolved through
      `pricing_authority.actor_role` — the same role resolution the package
      engine uses, including its treatment of platform roles — and an ordinary
      rep is refused. The refusal names a manager rather than silently
      accepting the figure.

  NOTHING HERE CARRIES A STRIPE PRICE ID OR CHOOSES A STRIPE OBJECT. The
  request names an item and, for a quoted item, an amount. Everything else is
  resolved server-side.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db
from app.models.catalog_models import CatalogItemKind, CatalogPricingMode
from app.models.models import Organization, User
from app.models.purchase_models import CatalogPurchase, PricingSource
from app.services import brand_catalog, catalog_purchase, pricing_authority
from app.services.sales_access import require_sales_member
from app.routers.audit_log_router import log_action

log = logging.getLogger(__name__)
router = APIRouter(prefix="/sales/catalog", tags=["sales-catalog"])


def _brand_sales_org_ids_for(db: Session, org: Organization) -> List[str]:
    """The brand sales orgs that sell for THIS customer's brand."""
    from app.models.sales_models import BrandSalesOrg
    platform_id = getattr(org, "platform_id", None)
    if not platform_id:
        return []
    return [r[0] for r in db.query(BrandSalesOrg.id)
            .filter(BrandSalesOrg.platform_id == platform_id).all()]


def _customer(db: Session, organization_id: str, user: User) -> Organization:
    """The customer, IF this seller sells for their brand.

    Being a sales member somewhere is not permission to sell everywhere. Without
    this check a rep at one brand could price and charge another brand's
    customer by guessing an id — the route dependency only asks whether they
    belong to SOME sales org. `assert_can_view_opportunity` already draws this
    line for the pipeline; money deserves it at least as much.

    A customer outside the seller's brands answers 404 rather than 403, for the
    same reason the pipeline does: a refusal that distinguishes "not yours" from
    "does not exist" turns the route into a directory of other brands' customers.
    """
    org = (db.query(Organization)
           .filter(Organization.id == organization_id).first())
    if org is None:
        raise HTTPException(status_code=404, detail="No such customer.")

    from app.services.sales_access import is_god, sales_org_ids
    if is_god(user):
        return org
    mine = set(sales_org_ids(user, db))
    if not mine.intersection(_brand_sales_org_ids_for(db, org)):
        raise HTTPException(status_code=404, detail="No such customer.")
    return org


def _may_price_quoted(db: Session, user: User, org: Organization) -> Dict[str, Any]:
    """May THIS person put a commercial amount on a quoted item?

    Answered through the same role resolution the package pricing engine uses,
    so a platform owner, a sales manager and a rep are treated consistently
    across both surfaces rather than by two rules that drift apart.
    """
    brand_sales_org_id = None
    try:
        from app.models.sales_models import BrandSalesOrg
        row = (db.query(BrandSalesOrg)
               .filter(BrandSalesOrg.platform_id == getattr(org, "platform_id", None))
               .first())
        brand_sales_org_id = getattr(row, "id", None)
    except Exception:                                    # pragma: no cover
        brand_sales_org_id = None

    from app.models.sales_models import ROLE_SALES_MANAGER

    role = pricing_authority.actor_role(user, db, brand_sales_org_id)
    elevated = role == ROLE_SALES_MANAGER
    return {
        "role": role,
        "may_price": elevated,
        "reason": None if elevated else (
            "Setting the amount on a quoted item needs a sales manager. A "
            "quoted item has no catalogue price, so there is no discount "
            "ceiling to measure this against — the authority question is "
            "whether you may set a commercial figure at all."),
    }


@router.get("/customers/{organization_id}")
def customer_catalog(organization_id: str,
                     user: User = Depends(require_sales_member),
                     db: Session = Depends(get_db)):
    """What this rep can sell this customer, and what they already hold.

    The offer list is SELLER-ASSISTED items, which is a different set from the
    customer's own screen. `may_price_quoted` is answered here so the UI can
    disable a control rather than let a rep fill in a figure and then be told
    no — the server refuses either way, but being told first is not the same
    experience as being told after.
    """
    org = _customer(db, organization_id, user)
    from app.services import billing_catalog
    platform_id = billing_catalog.platform_id_for_org(db, org)

    held = catalog_purchase.purchases_for(db, org)
    from app.models.purchase_models import PurchaseStatus
    held_keys = {p.item_key for p in held
                 if p.status == PurchaseStatus.ACTIVE}

    offers: List[Dict[str, Any]] = []
    for item in brand_catalog.items_for(db, platform_id):
        if not item.seller_assisted or not brand_catalog.is_sellable(item):
            continue
        out = brand_catalog.public_out(item)
        out["already_held"] = item.key in held_keys
        offers.append(out)

    authority = _may_price_quoted(db, user, org)
    from app.models.billing_models import SubscriptionStatus

    return {
        "organization_id": org.id,
        "customer_name": org.name,
        "offers": offers,
        "held": [catalog_purchase.purchase_out(p) for p in held],
        "authority": authority,
        # An add-on has nothing to attach to without a subscription. Said here
        # so a rep is not left guessing why the control is refused.
        "can_sell_addons": bool(
            getattr(org, "stripe_subscription_id", None)
            and (getattr(org, "billing_status", None) or "").lower()
            in SubscriptionStatus.OCCUPIED),
    }


class SellRequest(BaseModel):
    item: str
    quantity: int = 1
    # ONLY meaningful for a QUOTED item, and refused on a fixed one. Named
    # `quoted_amount_cents` rather than `amount` so a request carrying it
    # against a fixed item reads as what it is — an attempt to reprice
    # something that is not negotiable.
    quoted_amount_cents: Optional[int] = None
    note: Optional[str] = None


@router.post("/customers/{organization_id}/sell")
def sell_to_customer(organization_id: str, body: SellRequest,
                     user: User = Depends(require_sales_member),
                     db: Session = Depends(get_db)):
    """Sell one catalogue item to one customer.

    Runs `catalog_purchase` — the same engine behind the customer's own
    purchase button — so a seller cannot produce an outcome the customer could
    not have produced themselves, and a recurring add-on cannot become a
    second subscription by coming in through a different door.

    The rep is recorded as `sold_by`, which is how a commission question is
    answered later without inferring it from timestamps.
    """
    org = _customer(db, organization_id, user)
    from app.services import billing_catalog
    platform_id = billing_catalog.platform_id_for_org(db, org)

    try:
        item = brand_catalog.require_seller_sellable(db, platform_id, body.item)
    except brand_catalog.ItemNotAvailable as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # ── The pricing gate ──────────────────────────────────────────────────
    quoted = body.quoted_amount_cents
    if item.pricing_mode == CatalogPricingMode.QUOTED:
        if quoted is None:
            raise HTTPException(
                status_code=400,
                detail="%r is a quoted item and needs an amount for this "
                       "deal. Nothing in the catalogue says what it costs."
                       % item.key)
        authority = _may_price_quoted(db, user, org)
        if not authority["may_price"]:
            # 403, not 400: the request is well-formed and this person may not
            # make it. Saying so plainly is better than a vague refusal that
            # leaves a rep editing the number.
            raise HTTPException(status_code=403, detail=authority["reason"])
    elif quoted is not None:
        raise HTTPException(
            status_code=400,
            detail="%r has a fixed catalogue price and cannot be repriced on "
                   "a deal. If it should be negotiable it needs to be "
                   "configured as a quoted item." % item.key)

    try:
        if item.kind == CatalogItemKind.RECURRING_ADDON:
            purchase = catalog_purchase.add_recurring_addon(
                db, org, item, quantity=body.quantity, amount_cents=quoted,
                actor=user, sold_by=user, note=body.note)
        else:
            purchase = catalog_purchase.start_one_time_checkout(
                db, org, item, quantity=body.quantity, amount_cents=quoted,
                actor=user, sold_by=user, note=body.note)
    except catalog_purchase.PurchaseRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    try:
        log_action(db, org.id, user.id, action="sales.catalog_sold",
                   target_type="catalog_purchase", target_id=purchase.id,
                   details={"item": item.key, "kind": item.kind,
                            "quantity": body.quantity,
                            "amount_cents": purchase.amount_cents,
                            "pricing_source": purchase.pricing_source,
                            "status": purchase.status})
    except Exception:                                    # pragma: no cover
        log.exception("sales_catalog: audit write failed")
    db.commit()
    return catalog_purchase.purchase_out(purchase)


@router.post("/purchases/{purchase_id}/resend")
def resend_checkout(purchase_id: str,
                    user: User = Depends(require_sales_member),
                    db: Session = Depends(get_db)):
    """Hand back the payment link for a pending purchase.

    NOT a new checkout. Creating a second session for the same purchase would
    give the customer two live links for one obligation, and paying both is a
    real thing customers do. The stored link is returned, which is why it is
    stored — a seller must never have to go through browser history to find
    what they sent.
    """
    purchase = (db.query(CatalogPurchase)
                .filter(CatalogPurchase.id == purchase_id).first())
    if purchase is None:
        raise HTTPException(status_code=404, detail="No such purchase.")

    # SCOPED, because a payment link is customer data. `require_sales_member`
    # only establishes that this person sells for SOMEBODY; without this a rep
    # at one brand could walk purchase ids and collect another brand's live
    # payment links. Resolving through `_customer` reuses the one brand-scope
    # rule rather than writing a second one that can drift from it.
    _customer(db, purchase.organization_id, user)

    from app.models.purchase_models import PurchaseStatus
    if purchase.status != PurchaseStatus.PENDING:
        raise HTTPException(
            status_code=409,
            detail="That purchase is %s, so there is nothing to pay."
                   % purchase.status)
    if not purchase.checkout_url:
        raise HTTPException(
            status_code=409,
            detail="That purchase has no payment link — it was not collected "
                   "through a checkout.")

    return {"checkout_url": purchase.checkout_url,
            "item_name": purchase.item_name,
            "amount_cents": purchase.amount_cents,
            "quantity": purchase.quantity,
            "status": purchase.status}
