"""BUYING FROM THE CATALOGUE — the one engine behind every purchase path.

The customer buying for themselves and a seller selling on their behalf run
THIS code. Only the gate differs, and the gate is the caller's business. Two
implementations of "add an add-on" would agree right up until the day they did
not, and the day they did not would be a billing day.

════════════════════════════════════════════════════════════════════════════
THE THREE RULES THIS MODULE EXISTS TO KEEP
════════════════════════════════════════════════════════════════════════════

  A RECURRING ADD-ON JOINS THE EXISTING SUBSCRIPTION. It becomes an ITEM on
  it — never a second subscription. This codebase has already paid for that
  mistake once: every plan card was a live checkout, and a customer who
  clicked another tier was billed for both, every month, until somebody
  noticed. An add-on with no subscription to join is REFUSED, not given one.

  A ONE-TIME SERVICE IS A `mode="payment"` CHECKOUT AND NOTHING ELSE. It
  creates no subscription, touches no subscription state, and never marks the
  setup fee paid. It produces no invoice event either, which is why the
  checkout completion is the authoritative moment — the same reasoning the
  setup fee already runs on.

  REMOVING AN ADD-ON REMOVES ONE ITEM. `SubscriptionItem.delete`, never
  `Subscription.delete`. They are different calls precisely so a cancellation
  cannot be mistaken for a removal, and the base subscription survives.

THE AMOUNT IS RESOLVED SERVER-SIDE, ALWAYS. A request names an item key and a
quantity. A quoted item's amount arrives through the pricing-authority path
with an explicit authorisation; nothing else may carry a figure.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.catalog_models import BrandCatalogItem, CatalogItemKind
from app.models.models import Organization, User
from app.models.purchase_models import (CatalogPurchase, PricingSource,
                                        PurchaseStatus)
from app.services import brand_catalog

log = logging.getLogger(__name__)


class PurchaseRefused(RuntimeError):
    """A precondition failed. The message is written for the person who can
    act on it, because the usual cause is configuration or state rather than a
    malformed request."""


def _stripe():
    from app.routers.billing_router import _stripe_client
    import stripe
    _stripe_client()
    return stripe


def _brand_base_url(db: Session, org: Organization) -> str:
    from app.routers.billing_router import _brand_base_url as _b
    return _b(db, org)


# ═══════════════════════════════════════════════════════════════════════════
# READING WHAT A CUSTOMER HOLDS
# ═══════════════════════════════════════════════════════════════════════════

def purchases_for(db: Session, org: Organization,
                  statuses: Optional[tuple] = None) -> List[CatalogPurchase]:
    q = db.query(CatalogPurchase).filter(
        CatalogPurchase.organization_id == org.id)
    if statuses:
        q = q.filter(CatalogPurchase.status.in_(statuses))
    return q.order_by(CatalogPurchase.created_at.desc()).all()


def purchase_out(p: CatalogPurchase) -> Dict[str, Any]:
    """What a purchase looks like to a customer or a seller.

    No Stripe ids. The checkout URL IS included — it is the thing a customer
    has to open to pay, and a link nobody can reach is not a link — but every
    object identifier behind it stays server-side.
    """
    return {
        "id": p.id,
        "item_key": p.item_key,
        "item_name": p.item_name,
        "kind": p.kind,
        "kind_label": CatalogItemKind.LABELS.get(p.kind, p.kind),
        "amount_cents": p.amount_cents,
        "currency": p.currency or "usd",
        "quantity": p.quantity or 1,
        # The whole line, computed once here so two screens cannot disagree
        # about what "3 x $25/mo" comes to.
        "total_cents": (p.amount_cents or 0) * (p.quantity or 1),
        "billing_interval": p.billing_interval,
        "status": p.status,
        "is_recurring": p.kind == CatalogItemKind.RECURRING_ADDON,
        "pricing_source": p.pricing_source,
        "checkout_url": p.checkout_url,
        "note": p.note,
        "created_at": p.created_at,
        "paid_at": p.paid_at,
        "canceled_at": p.canceled_at,
        # Whether this one can still be removed. Stated by the server so the
        # screen does not have to work out which states are removable.
        "removable": (p.kind == CatalogItemKind.RECURRING_ADDON
                      and p.status == PurchaseStatus.ACTIVE),
    }


def entitlement_totals(db: Session, org: Organization) -> Dict[str, int]:
    """What the customer's LIVE purchases add to their limits.

    Only PAID and ACTIVE rows count. A pending checkout is somebody who has not
    paid, and granting capacity on the strength of an unpaid link is how a
    platform gives away the thing it is trying to sell.

    Returns an empty dict when nothing grants anything, which is the common
    case: plenty of real items — training, a migration, priority support —
    change what is DELIVERED without changing what the software permits.
    """
    totals: Dict[str, int] = {}
    rows = (db.query(CatalogPurchase, BrandCatalogItem)
            .join(BrandCatalogItem,
                  BrandCatalogItem.id == CatalogPurchase.catalog_item_id)
            .filter(CatalogPurchase.organization_id == org.id,
                    CatalogPurchase.status.in_(PurchaseStatus.LIVE))
            .all())
    for purchase, item in rows:
        if not item.entitlement_key or item.entitlement_value is None:
            continue
        totals[item.entitlement_key] = (
            totals.get(item.entitlement_key, 0)
            + int(item.entitlement_value) * int(purchase.quantity or 1))
    return totals


# ═══════════════════════════════════════════════════════════════════════════
# PRICING — the server decides, always
# ═══════════════════════════════════════════════════════════════════════════

def resolve_amount(item: BrandCatalogItem,
                   quoted_cents: Optional[int] = None) -> int:
    """What one unit of this item costs on this sale.

    A FIXED item takes the catalogue amount and nothing else — a quoted figure
    against a fixed item is an attempt to reprice it, and is refused rather
    than honoured.

    A QUOTED item has no catalogue amount by design, so the figure must arrive
    from the caller — and only ever from a caller that has already checked the
    seller's pricing authority. This function does not grant that authority; it
    refuses to proceed without a figure, which is what makes the omission
    impossible to miss.
    """
    from app.models.catalog_models import CatalogPricingMode

    if item.pricing_mode == CatalogPricingMode.QUOTED:
        if quoted_cents is None:
            raise PurchaseRefused(
                "%r is a quoted item, so it needs a price for this deal. "
                "Nothing in the catalogue says what it costs." % item.key)
        if quoted_cents < 0:
            raise PurchaseRefused("A price cannot be negative.")
        return int(quoted_cents)

    if quoted_cents is not None and int(quoted_cents) != int(
            item.amount_cents or -1):
        raise PurchaseRefused(
            "%r has a fixed catalogue price and cannot be repriced on a deal. "
            "Ask for it to be configured as a quoted item if it should be "
            "negotiable." % item.key)

    if item.amount_cents is None:
        raise PurchaseRefused(
            "%r has no price configured, so it cannot be sold. A price nobody "
            "entered is not free." % item.key)
    return int(item.amount_cents)


def _price_payload(db: Session, org: Organization, item: BrandCatalogItem,
                   amount_cents: int, recurring: bool) -> Dict[str, Any]:
    """The Stripe price for this line — a mapped Price where one exists.

    The inline fallback mints an ad-hoc price, which is the ONLY way to charge
    a quoted amount (there is no Price object for a figure invented on a deal)
    and a stopgap for a brand whose catalogue has not been provisioned. It is
    never used to charge a DIFFERENT amount than the mapped Price would: a
    fixed item at its catalogue amount takes the mapped id whenever there is
    one.
    """
    from app.models.catalog_models import CatalogPricingMode

    if (item.stripe_price_id
            and item.pricing_mode != CatalogPricingMode.QUOTED
            and int(item.amount_cents or -1) == int(amount_cents)):
        return {"price": item.stripe_price_id}

    from app.routers.billing_router import _brand_display_name
    data: Dict[str, Any] = {
        "currency": (item.currency or "usd").lower(),
        "unit_amount": int(amount_cents),
        "product_data": {"name": "%s %s" % (_brand_display_name(db, org),
                                            item.name)},
    }
    # ONLY for a recurring line. An interval on a one-time service is the
    # single change that turns one charge into a subscription.
    if recurring:
        data["recurring"] = {"interval": item.billing_interval or "month"}
    return {"price_data": data}


# ═══════════════════════════════════════════════════════════════════════════
# BUYING
# ═══════════════════════════════════════════════════════════════════════════

def add_recurring_addon(db: Session, org: Organization,
                        item: BrandCatalogItem, *, quantity: int = 1,
                        amount_cents: Optional[int] = None,
                        actor: Optional[User] = None,
                        sold_by: Optional[User] = None,
                        note: Optional[str] = None) -> CatalogPurchase:
    """Put a recurring add-on onto the customer's EXISTING subscription.

    Refuses when there is no subscription to join. Creating one here would be
    a second subscription for a customer who already has one, which is the
    double-billing defect this platform has already fixed — and giving an
    add-on its own subscription would mean two renewal dates, two invoices and
    two things to cancel.

    Stripe prorates the new item according to its own rules for mid-period
    additions; the customer sees it on their next invoice. No amount is
    charged here and now.
    """
    if quantity < 1:
        raise PurchaseRefused("Quantity must be at least 1.")
    if item.kind != CatalogItemKind.RECURRING_ADDON:
        raise PurchaseRefused("%r is not a recurring add-on." % item.key)

    sub_id = getattr(org, "stripe_subscription_id", None)
    status = (getattr(org, "billing_status", None) or "").lower()
    from app.models.billing_models import SubscriptionStatus
    if not sub_id or status not in SubscriptionStatus.OCCUPIED:
        raise PurchaseRefused(
            "This customer has no active subscription, so there is nothing for "
            "an add-on to attach to. Start the subscription first — an add-on "
            "must never become a second subscription of its own.")

    # ALREADY HELD? Adding it twice would bill twice for the same thing and
    # leave two rows that both claim to be the live one.
    existing = (db.query(CatalogPurchase)
                .filter(CatalogPurchase.organization_id == org.id,
                        CatalogPurchase.item_key == item.key,
                        CatalogPurchase.status == PurchaseStatus.ACTIVE)
                .first())
    if existing is not None:
        raise PurchaseRefused(
            "This customer already has %r. Change its quantity rather than "
            "adding it a second time." % item.key)

    unit = resolve_amount(item, amount_cents)
    stripe = _stripe()
    payload = _price_payload(db, org, item, unit, recurring=True)

    try:
        sub_item = stripe.SubscriptionItem.create(
            subscription=sub_id, quantity=int(quantity),
            metadata={"org_id": org.id, "catalog_key": item.key,
                      "managed_by": "advisorflow"},
            **payload)
    except Exception as exc:
        log.warning("catalog_purchase: could not add %s to %s: %s",
                    item.key, sub_id, exc)
        raise PurchaseRefused(
            "The payment processor rejected the add-on. Nothing was charged "
            "and nothing was changed.")

    purchase = CatalogPurchase(
        organization_id=org.id, platform_id=getattr(org, "platform_id", None),
        catalog_item_id=item.id, item_key=item.key, item_name=item.name,
        kind=item.kind, amount_cents=unit,
        currency=(item.currency or "usd"), quantity=int(quantity),
        billing_interval=item.billing_interval,
        pricing_source=(PricingSource.QUOTED if amount_cents is not None
                        else PricingSource.CATALOGUE),
        # ACTIVE immediately, and correctly: Stripe has accepted the item and
        # it is on the subscription being billed. There is no pending state for
        # something already attached.
        status=PurchaseStatus.ACTIVE,
        stripe_subscription_item_id=sub_item.get("id"),
        stripe_price_id=((sub_item.get("price") or {}).get("id")
                         if isinstance(sub_item.get("price"), dict) else None),
        created_by_user_id=getattr(actor, "id", None),
        sold_by_user_id=getattr(sold_by, "id", None),
        note=note,
    )
    db.add(purchase)
    db.commit()
    db.refresh(purchase)
    return purchase


def remove_recurring_addon(db: Session, org: Organization,
                           purchase: CatalogPurchase) -> CatalogPurchase:
    """Take ONE add-on off the subscription. Never the subscription itself.

    `SubscriptionItem.delete` and `Subscription.delete` are different calls,
    and this module only ever reaches for the first. A customer removing an
    add-on has not asked to stop being a customer.
    """
    if purchase.organization_id != org.id:
        raise PurchaseRefused("That purchase belongs to another customer.")
    if purchase.kind != CatalogItemKind.RECURRING_ADDON:
        raise PurchaseRefused("Only a recurring add-on can be removed.")
    if purchase.status != PurchaseStatus.ACTIVE:
        raise PurchaseRefused("That add-on is not active.")

    stripe = _stripe()
    if purchase.stripe_subscription_item_id:
        try:
            stripe.SubscriptionItem.delete(purchase.stripe_subscription_item_id)
        except Exception as exc:
            # An item Stripe no longer has is already gone. Recording that
            # locally is the correct repair; refusing would leave a row
            # claiming the customer pays for something they do not.
            log.warning("catalog_purchase: could not delete subscription item "
                        "%s (%s) - recording removal locally",
                        purchase.stripe_subscription_item_id, exc)

    purchase.status = PurchaseStatus.CANCELED
    purchase.canceled_at = datetime.utcnow()
    db.commit()
    db.refresh(purchase)
    return purchase


def withdraw_pending(db: Session, org: Organization,
                     purchase: CatalogPurchase) -> CatalogPurchase:
    """Take back an unpaid checkout. Nothing was charged, so nothing is refunded.

    A pending purchase is a link somebody was sent and has not paid. Leaving it
    outstanding is not harmless: the link keeps working, so a service quoted in
    error can still be paid for days later by a customer who never heard it was
    withdrawn.

    THE SESSION IS EXPIRED AT STRIPE, not merely marked cancelled here. A row
    that says "withdrawn" beside a link that still takes money is the worst of
    both records. An expiry Stripe refuses is logged and the local withdrawal
    still stands — a session that cannot be expired is usually one that already
    expired.

    REFUSES A PAID PURCHASE. Money that arrived is a different conversation
    (a refund), with different authority, and quietly flipping a paid row to
    cancelled would hide it.
    """
    if purchase.organization_id != org.id:
        raise PurchaseRefused("That purchase belongs to another customer.")
    if purchase.status != PurchaseStatus.PENDING:
        raise PurchaseRefused(
            "That purchase is %s, so there is no unpaid checkout to withdraw."
            % purchase.status)

    if purchase.stripe_checkout_session_id:
        stripe = _stripe()
        try:
            stripe.checkout.Session.expire(purchase.stripe_checkout_session_id)
        except Exception as exc:
            log.info("catalog_purchase: could not expire session %s (%s) - "
                     "recording the withdrawal locally anyway",
                     purchase.stripe_checkout_session_id, exc)

    purchase.status = PurchaseStatus.CANCELED
    purchase.canceled_at = datetime.utcnow()
    purchase.checkout_url = None          # the link is no longer a link
    db.commit()
    db.refresh(purchase)
    return purchase


def start_one_time_checkout(db: Session, org: Organization,
                            item: BrandCatalogItem, *, quantity: int = 1,
                            amount_cents: Optional[int] = None,
                            actor: Optional[User] = None,
                            sold_by: Optional[User] = None,
                            note: Optional[str] = None) -> CatalogPurchase:
    """A `mode="payment"` checkout for a one-time service. Nothing else.

    NOT A SUBSCRIPTION, and the session carries no `subscription_data` at all
    so it cannot accidentally become one. The purchase is PENDING until the
    webhook says the money arrived: a customer who opened a link has not paid,
    and treating "they finished the form" as "the money arrived" is the exact
    error the setup-fee split exists to prevent.

    The link is PERSISTED. A seller must be able to reopen, copy or resend it
    without going through browser history.
    """
    if quantity < 1:
        raise PurchaseRefused("Quantity must be at least 1.")
    if item.kind != CatalogItemKind.ONE_TIME:
        raise PurchaseRefused("%r is not a one-time item." % item.key)

    unit = resolve_amount(item, amount_cents)
    stripe = _stripe()

    from app.routers.billing_router import _get_or_create_customer
    customer_id = _get_or_create_customer(org, db)
    base_url = _brand_base_url(db, org)

    purchase = CatalogPurchase(
        organization_id=org.id, platform_id=getattr(org, "platform_id", None),
        catalog_item_id=item.id, item_key=item.key, item_name=item.name,
        kind=item.kind, amount_cents=unit,
        currency=(item.currency or "usd"), quantity=int(quantity),
        billing_interval=None,
        pricing_source=(PricingSource.QUOTED if amount_cents is not None
                        else PricingSource.CATALOGUE),
        status=PurchaseStatus.PENDING,
        created_by_user_id=getattr(actor, "id", None),
        sold_by_user_id=getattr(sold_by, "id", None),
        note=note,
    )
    db.add(purchase)
    # Flushed rather than committed so the id exists to put in the session
    # metadata. The webhook matches on that id, so a session created before the
    # row it names would be a payment with nowhere to land.
    db.flush()

    line = _price_payload(db, org, item, unit, recurring=False)
    try:
        session = stripe.checkout.Session.create(
            customer=customer_id,
            mode="payment",
            line_items=[{**line, "quantity": int(quantity)}],
            # `purpose` is what keeps this apart from the SETUP FEE, which is
            # also a payment-mode session. Without it the webhook would have
            # two payment-mode shapes and no way to tell which obligation the
            # money settled.
            metadata={"org_id": org.id,
                      "purpose": "catalog_purchase",
                      "catalog_purchase_id": purchase.id,
                      "catalog_key": item.key},
            success_url="%s/billing?success=1&part=purchase" % base_url,
            cancel_url="%s/billing?canceled=1" % base_url,
        )
    except Exception as exc:
        db.rollback()
        log.warning("catalog_purchase: checkout failed for %s/%s: %s",
                    org.id, item.key, exc)
        raise PurchaseRefused(
            "The payment processor would not start a checkout. Nothing was "
            "charged.")

    purchase.stripe_checkout_session_id = session.get("id")
    purchase.checkout_url = session.get("url")
    db.commit()
    db.refresh(purchase)
    return purchase


def mark_one_time_paid(db: Session, org: Organization, session_obj: dict
                       ) -> Optional[CatalogPurchase]:
    """The webhook's half. Called only for a verified, paid session.

    IDEMPOTENT ON THE PURCHASE ROW ITSELF: a purchase already PAID is returned
    untouched, so a Stripe retry — or somebody resending the event from the
    dashboard — banks nothing twice. `BillingPayment.collection_reference`
    catches the same thing from the money side, under the same unique
    constraint every other payment on this platform uses.

    DOES NOT TOUCH SUBSCRIPTION STATE. A customer who bought a migration has
    bought exactly one thing; inferring a plan from it would put them on a
    subscription nobody sold them.
    """
    meta = session_obj.get("metadata") or {}
    purchase_id = meta.get("catalog_purchase_id")
    if not purchase_id:
        return None

    purchase = (db.query(CatalogPurchase)
                .filter(CatalogPurchase.id == purchase_id,
                        CatalogPurchase.organization_id == org.id)
                .first())
    if purchase is None:
        log.warning("catalog_purchase: session names purchase %s which does "
                    "not belong to org %s - ignoring", purchase_id, org.id)
        return None

    if purchase.status == PurchaseStatus.PAID:
        return purchase

    purchase.status = PurchaseStatus.PAID
    purchase.paid_at = datetime.utcnow()
    purchase.stripe_payment_intent_id = session_obj.get("payment_intent")
    return purchase
