"""Re-pull one organization's billing state from Stripe.

WHY A BILLING SYSTEM NEEDS THIS

Webhooks fail. An endpoint is down during a deploy, a secret is rotated, an
event exhausts its retries, a bug rejects something it should have applied. A
mirror with no reconciliation path has no way back to the truth, and the
divergence is silent - the local row simply keeps saying whatever it last
heard.

So this exists from the first phase rather than being added after the first
incident. It reads from Stripe and writes locally through the SAME upsert
functions the webhook uses, which means reconciliation cannot drift from live
processing: there is one way to turn a Stripe object into a row.

READ-ONLY AGAINST STRIPE. It lists and retrieves. It never creates, modifies,
finalizes, voids or charges anything.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.models.models import Organization
from app.services import stripe_sync

log = logging.getLogger(__name__)

DEFAULT_LIMIT = 100


def reconcile_organization(db: Session, organization_id: str,
                           stripe_module: Any = None,
                           limit: int = DEFAULT_LIMIT) -> Dict[str, Any]:
    """Re-pull invoices and payments for one organization.

    `stripe_module` is injected so tests can supply a fake. Production passes
    nothing and the configured client is used.
    """
    org = (db.query(Organization)
           .filter(Organization.id == organization_id).first())
    if org is None:
        return {"ok": False, "error": "organization not found",
                "organization_id": organization_id}
    if not org.stripe_customer_id:
        return {"ok": True, "organization_id": org.id,
                "note": "no Stripe customer for this organization; nothing to reconcile",
                "invoices": 0, "payments": 0}

    if stripe_module is None:
        import stripe as stripe_module  # noqa: PLC0415  (configured by the router)

    out: Dict[str, Any] = {"ok": True, "organization_id": org.id,
                           "stripe_customer_id": org.stripe_customer_id,
                           "invoices": 0, "payments": 0, "ignored": [],
                           "errors": []}

    try:
        invoices = stripe_module.Invoice.list(
            customer=org.stripe_customer_id, limit=limit)
        for inv in (invoices.get("data") if isinstance(invoices, dict)
                    else getattr(invoices, "data", []) or []):
            row, ignored = stripe_sync.upsert_invoice_from_stripe(db, inv)
            if ignored:
                out["ignored"].append(ignored)
                continue
            stripe_sync.apply_invoice_state_to_organization(db, row)
            out["invoices"] += 1
    except Exception as exc:
        # Reported, never swallowed. A partial reconcile that says it succeeded
        # is worse than one that says which half failed.
        out["ok"] = False
        out["errors"].append("invoices: %s: %s" % (type(exc).__name__, str(exc)[:300]))
        log.exception("billing reconcile: invoice pull failed for org=%s", org.id)

    try:
        intents = stripe_module.PaymentIntent.list(
            customer=org.stripe_customer_id, limit=limit)
        for pi in (intents.get("data") if isinstance(intents, dict)
                   else getattr(intents, "data", []) or []):
            row, ignored = stripe_sync.upsert_payment_from_stripe(db, pi)
            if ignored:
                out["ignored"].append(ignored)
                continue
            out["payments"] += 1
    except Exception as exc:
        out["ok"] = False
        out["errors"].append("payments: %s: %s" % (type(exc).__name__, str(exc)[:300]))
        log.exception("billing reconcile: payment pull failed for org=%s", org.id)

    db.commit()
    out["billing_status"] = org.billing_status
    return out
