"""WHAT T2 ALREADY HOLDS FOR THIS CUSTOMER. READ ONLY.

T2 is authoritative commerce and stays that way. A hybrid arrangement — a
catalogue subscription plus a share — keeps its subscription side exactly where
it already lives: `organizations.stripe_subscription_id`,
`organizations.billing_plan_key`, `catalog_purchases`, `billing_invoices`,
`billing_payments`. None of it is copied into a commercial agreement, because
two copies of a price is how a customer ends up being told two different
numbers.

So this module reads and reports. It has no write path, deliberately, and the
one thing it returns is a picture an internal screen can show next to the
custom terms: here is the subscription side, there is the share side, and the
agreement points at the first rather than restating it.

WHY A HYBRID IS NOT FORCED THROUGH STRIPE
-----------------------------------------
Because most of one is not collectable there. A share settled quarterly against
a partner's own collections has no Stripe object to be, and inventing one — a
$0 subscription, a metered price with no meter — produces invoices nobody
should receive and webhooks nobody should act on.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.models import Organization


def _purchases(db: Session, organization_id: str) -> List[Dict[str, Any]]:
    from app.models.purchase_models import CatalogPurchase

    rows = (db.query(CatalogPurchase)
            .filter(CatalogPurchase.organization_id == organization_id)
            .order_by(CatalogPurchase.created_at.desc())
            .limit(100)
            .all())
    return [{
        "id": r.id,
        "item_key": r.item_key,
        "item_name": r.item_name,
        "kind": r.kind,
        "status": r.status,
        "amount_cents": r.amount_cents,
        "currency": r.currency,
        "quantity": r.quantity,
        "billing_interval": r.billing_interval,
        "paid_at": r.paid_at,
    } for r in rows]


def state(db: Session, organization_id: Optional[str]) -> Dict[str, Any]:
    """The T2 commercial position, as T2 holds it.

    `None` where T2 holds nothing. A customer with no subscription is reported
    as having none rather than as having one worth zero — the same distinction
    the rest of this package keeps.
    """
    if not organization_id:
        return {"known": False, "reason": "This agreement is not attached to a "
                                          "customer yet."}

    org = (db.query(Organization)
           .filter(Organization.id == organization_id).first())
    if org is None:
        return {"known": False, "reason": "Customer not found."}

    purchases = _purchases(db, organization_id)
    return {
        "known": True,
        "organization_id": org.id,
        "organization_name": org.name,
        "plan": org.plan,
        "billing_plan_key": getattr(org, "billing_plan_key", None),
        "billing_status": getattr(org, "billing_status", None),
        "billing_current_period_end": getattr(org, "billing_current_period_end", None),
        "has_stripe_customer": bool(getattr(org, "stripe_customer_id", None)),
        "has_stripe_subscription": bool(getattr(org, "stripe_subscription_id", None)),
        "purchases": purchases,
        "purchase_count": len(purchases),
        "note": "T2 remains authoritative for everything listed here. A custom "
                "commercial agreement references it and never restates it.",
    }


def has_subscription_side(db: Session, organization_id: Optional[str]) -> bool:
    s = state(db, organization_id)
    if not s.get("known"):
        return False
    return bool(s.get("has_stripe_subscription") or s.get("billing_plan_key"))
