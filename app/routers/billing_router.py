"""
Stripe billing endpoints for BookaBoost / AdvisorFlow.

Required env vars (set in Render):
    STRIPE_SECRET_KEY        — sk_live_... or sk_test_...
    STRIPE_WEBHOOK_SECRET    — whsec_... (Stripe Dashboard → Webhooks)
    APP_BASE_URL             — https://advisorflow-frontend.onrender.com

Endpoints:
    GET  /billing/plans       — plan info for plan-picker UI (no auth)
    GET  /billing/subscription — current plan/status for org
    POST /billing/checkout    — create Stripe Checkout session
    POST /billing/portal      — create Stripe Billing Portal session
    POST /billing/webhook     — Stripe webhook (raw body, no auth)
    GET  /billing/all         — god admin: all orgs billing overview
"""

import os
import logging
from typing import Optional

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user
from app.models.models import User, Organization
from app.services.capabilities import require_capability

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/billing", tags=["billing"])

# ---------------------------------------------------------------------------
# Plan catalog
# ---------------------------------------------------------------------------
PLANS = {
    "starter": {
        "name": "Starter",
        "monthly_usd": 497,
        "monthly_cents": 49700,
        "onboarding_usd": 1500,
        "max_leads": 2500,
        "max_users": 2,
        "features": ["AI email cadence (8 emails / 14 days)", "Up to 2 users", "Up to 2,500 leads"],
    },
    "growth": {
        "name": "Growth",
        "monthly_usd": 997,
        "monthly_cents": 99700,
        "onboarding_usd": 2500,
        "max_leads": 5000,
        "max_users": 3,
        "features": ["AI email + SMS 1,000/mo", "AI voice 300 min/mo", "Up to 3 users", "Up to 5,000 leads"],
    },
    "professional": {
        "name": "Professional",
        "monthly_usd": 1997,
        "monthly_cents": 199700,
        "onboarding_usd": 5000,
        "max_leads": 7500,
        "max_users": 5,
        "features": ["AI email + SMS 3,000/mo", "AI voice 750 min/mo", "Up to 5 users / 3 locations", "Priority support + 24-month price lock"],
    },
    "enterprise": {
        "name": "Enterprise",
        "monthly_usd": None,
        "monthly_cents": None,
        "onboarding_usd": None,
        "max_leads": None,
        "max_users": None,
        "features": ["Everything in Professional", "Unlimited leads, users, and locations", "White-label available"],
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _stripe_client():
    key = os.environ.get("STRIPE_SECRET_KEY")
    if not key:
        raise HTTPException(status_code=503, detail="Billing not configured. Contact support.")
    stripe.api_key = key
    return stripe


# ── THE LEGACY GUARD IS GONE. See app/services/billing_access.py ──────────
#
# `_require_admin` checked users.role and every route then read
# `current_user.organization_id` to decide WHOSE billing it was acting on.
# That column is the tenant a person was historically attached to, not the
# workspace they are standing in, and for anyone holding more than one
# membership those are different organizations. Billing was using the wrong
# one, so a dual-workspace user's billing page showed - and could change -
# whichever organization their column happened to name.
#
# require_billing_view / require_billing_manage resolve the ACTIVE
# authorized workspace and hand the route a BillingScope carrying the
# organization already resolved, so no endpoint re-derives it and none of
# them can disagree.
from app.services.billing_access import (BillingScope,  # noqa: E402
                                         require_billing_manage,
                                         require_billing_view)


def _get_or_create_customer(org: Organization, db: Session) -> str:
    _stripe_client()
    if org.stripe_customer_id:
        return org.stripe_customer_id
    customer = stripe.Customer.create(
        name=org.name,
        metadata={"org_id": org.id, "org_slug": org.slug},
    )
    org.stripe_customer_id = customer.id
    db.commit()
    return customer.id


# ---------------------------------------------------------------------------
# GET /billing/plans
#
# WAS PUBLIC - no auth at all - and served the platform's whole price list,
# per-plan lead and user ceilings included, to anyone who asked. It was marked
# "(public)" for a plan-picker UI that does not exist: nothing in frontend/src
# calls this endpoint. So there is no deliberate public-product requirement to
# weigh against closing it, which is the only thing that would have justified
# leaving it open.
#
# `_require_admin` rather than merely authenticated, matching /subscription,
# /checkout and /portal below: the plan catalogue is for the person who can
# actually change the plan. If a public pricing page is wanted later it should
# serve a marketing catalogue written for that purpose, not the live billing
# configuration.
# ---------------------------------------------------------------------------
@router.get("/plans")
def get_plans(scope: BillingScope = Depends(require_billing_view)):
    """The catalogue a customer may choose from.

    Carries no tenant data, but it stays behind billing_view: it is part of
    the billing surface, and a route readable by anyone is a route somebody
    eventually adds tenant data to."""
    return {"plans": PLANS}


# ---------------------------------------------------------------------------
# GET /billing/subscription
# ---------------------------------------------------------------------------
@router.get("/subscription")
def get_subscription(
    scope: BillingScope = Depends(require_billing_view),
    db: Session = Depends(get_db),
):
    # The organization comes from the resolved scope - never from a column
    # on the caller, never from anything the caller supplied.
    org = scope.organization

    result = {
        "plan": org.plan or "trial",
        "billing_status": getattr(org, "billing_status", None) or "trialing",
        "stripe_customer_id": getattr(org, "stripe_customer_id", None),
        "stripe_subscription_id": getattr(org, "stripe_subscription_id", None),
        "stripe_plan_interval": getattr(org, "stripe_plan_interval", None) or "month",
        "plan_details": PLANS.get(org.plan or "trial"),
        "current_period_end": None,
        "cancel_at_period_end": False,
    }

    sub_id = getattr(org, "stripe_subscription_id", None)
    if sub_id:
        try:
            _stripe_client()
            sub = stripe.Subscription.retrieve(sub_id)
            result["current_period_end"] = sub.current_period_end
            result["cancel_at_period_end"] = sub.cancel_at_period_end
        except Exception as e:
            logger.warning("Could not fetch Stripe sub %s: %s", sub_id, e)

    return result


# ---------------------------------------------------------------------------
# POST /billing/checkout
# ---------------------------------------------------------------------------
class CheckoutRequest(BaseModel):
    plan: str
    interval: str = "month"  # month | year


@router.post("/checkout")
def create_checkout(
    req: CheckoutRequest,
    scope: BillingScope = Depends(require_billing_manage),
    db: Session = Depends(get_db),
):
    if req.plan not in ("starter", "growth", "professional"):
        raise HTTPException(status_code=400, detail="Invalid plan. Choose starter, growth, or professional.")
    if req.interval not in ("month", "year"):
        raise HTTPException(status_code=400, detail="Interval must be 'month' or 'year'.")

    plan_info = PLANS[req.plan]
    _stripe_client()

    org = scope.organization

    customer_id = _get_or_create_customer(org, db)
    # The customer paying is a funeral home on a white-label brand. Bouncing
    # them to an AdvisorFlow Render hostname after checkout tells them who
    # their software really belongs to. Their own brand's domain first.
    from app.services.public_identity import public_base_url as _public_base
    base_url = (_public_base(db, org.id)
                or os.environ.get("APP_BASE_URL", "").strip()
                or "https://advisorflow-frontend.onrender.com")

    # Annual: 11 months billed (month 13 free = ~8% discount)
    monthly_cents = plan_info["monthly_cents"]
    unit_amount = (monthly_cents * 11) if req.interval == "year" else monthly_cents

    session = stripe.checkout.Session.create(
        customer=customer_id,
        mode="subscription",
        line_items=[{
            "price_data": {
                "currency": "usd",
                "unit_amount": unit_amount,
                "recurring": {"interval": req.interval},
                "product_data": {
                    "name": f"BookaBoost {plan_info['name']}",
                    "description": " · ".join(plan_info["features"][:2]),
                },
            },
            "quantity": 1,
        }],
        metadata={"org_id": org.id, "plan": req.plan, "interval": req.interval},
        subscription_data={"metadata": {"org_id": org.id, "plan": req.plan}},
        success_url=f"{base_url}/billing?success=1",
        cancel_url=f"{base_url}/billing?canceled=1",
    )
    return {"checkout_url": session.url}


# ---------------------------------------------------------------------------
# POST /billing/portal
# ---------------------------------------------------------------------------
@router.post("/portal")
def create_portal(
    scope: BillingScope = Depends(require_billing_manage),
    db: Session = Depends(get_db),
):
    _stripe_client()
    org = scope.organization
    if not getattr(org, "stripe_customer_id", None):
        raise HTTPException(status_code=400, detail="No billing account. Please select a plan first.")

    # Same reasoning as the checkout session above: return the customer to
    # their own brand's domain, not to an AdvisorFlow deployment hostname.
    from app.services.public_identity import public_base_url as _public_base
    base_url = (_public_base(db, org.id)
                or os.environ.get("APP_BASE_URL", "").strip()
                or "https://advisorflow-frontend.onrender.com")
    session = stripe.billing_portal.Session.create(
        customer=org.stripe_customer_id,
        return_url=f"{base_url}/billing",
    )
    return {"portal_url": session.url}


# ---------------------------------------------------------------------------
# POST /billing/webhook  (no auth — validated by Stripe signature)
# ---------------------------------------------------------------------------
@router.post("/webhook", include_in_schema=False)
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not webhook_secret:
        raise HTTPException(status_code=503, detail="Webhook not configured")

    body = await request.body()
    sig = request.headers.get("stripe-signature", "")
    _stripe_client()

    try:
        event = stripe.Webhook.construct_event(body, sig, webhook_secret)
    except stripe.error.SignatureVerificationError as e:
        logger.warning("Stripe signature failed: %s", e)
        raise HTTPException(status_code=400, detail="Invalid signature")
    except Exception as e:
        logger.error("Stripe webhook error: %s", e)
        raise HTTPException(status_code=400, detail="Webhook error")

    # ── DISPATCH ────────────────────────────────────────────────────────────
    #
    # The three inline handlers that used to live here (checkout.session.
    # completed, customer.subscription.updated, customer.subscription.deleted)
    # moved to app/services/billing_webhooks.py WITH THEIR BEHAVIOUR UNCHANGED,
    # and the invoice, payment and refund events that were never handled at all
    # were added beside them.
    #
    # What is new is not the handling, it is the guarantees around it:
    #
    #   * the event id is claimed in `stripe_webhook_events` BEFORE any
    #     financial state is touched, so a redelivery - which Stripe documents
    #     and does - cannot apply the same transition twice;
    #   * an event older than the last one applied to a row is skipped, because
    #     Stripe does not guarantee delivery order;
    #   * a transient failure raises, so this endpoint answers 500 and Stripe
    #     retries. The previous version returned {"received": True} whatever
    #     happened, which meant a failed `invoice.payment_failed` was lost and
    #     the customer stayed 'active' with a declined card.
    from app.services import billing_webhooks

    try:
        result, was_duplicate = billing_webhooks.handle(db, event)
    except Exception:
        # Deliberately a 500. Stripe will retry, and the event is recorded as
        # failed in the ledger rather than silently dropped.
        logger.exception("Stripe webhook processing failed: %s", event.get("id"))
        raise HTTPException(status_code=500, detail="Webhook processing failed")

    return {"received": True, "duplicate": was_duplicate,
            "result": {k: v for k, v in result.items() if k != "payload"}}


# ---------------------------------------------------------------------------
# GET /billing/all  (god admin — all orgs billing overview)
# ---------------------------------------------------------------------------
@router.get("/all")
def get_all_billing(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _cap: User = Depends(require_capability("platform_billing")),
):
    """Master billing across every customer. God only, and permanently so.

    The inline role check below was already correct and is kept - two
    independent refusals for the platform's whole revenue picture is the right
    number. What `require_capability` adds is that "master billing is God-only"
    now lives in the capability registry as `delegable=False` rather than as a
    role literal in one function. `set_self_management` refuses to delegate it
    with a 400, so no route and no God screen can hand it to a customer, however
    it is used.
    """
    if current_user.role != "god_admin":
        raise HTTPException(status_code=403, detail="God admin only.")
    orgs = db.query(Organization).order_by(Organization.name).all()
    return {
        "orgs": [
            {
                "id": o.id,
                "name": o.name,
                "slug": o.slug,
                "plan": o.plan or "trial",
                "billing_status": getattr(o, "billing_status", None) or "trialing",
                "stripe_customer_id": getattr(o, "stripe_customer_id", None),
                "stripe_subscription_id": getattr(o, "stripe_subscription_id", None),
                "stripe_plan_interval": getattr(o, "stripe_plan_interval", None),
                "is_active": o.is_active,
            }
            for o in orgs
        ]
    }



# ---------------------------------------------------------------------------
# POST /billing/reconcile/{org_id}   (god admin — re-pull state from Stripe)
#
# TENANT AUTHORITY NOTE. This route deliberately uses the SAME guard as
# /billing/all above - god_admin plus the non-delegable `platform_billing`
# capability - and does NOT read current_user.organization_id. The org it acts
# on is an explicit path parameter checked against the database, not inferred
# from the caller's legacy column.
#
# FIXED IN P3: the `_require_admin` helper described below is gone and the
# from current_user.organization_id, which is the legacy authority path the
# platform moved away from. That defect is REAL and is documented in
# claude/BILLING_PHASE0_ARCHITECTURE.md §8; fixing it means re-gating the
# customer-facing routes through lead_scope and adding billing capabilities,
# which is Phase 3. P0 does not expand its use: nothing added here calls it.
# ---------------------------------------------------------------------------
@router.post("/reconcile/{org_id}", include_in_schema=False)
def reconcile_billing(
    org_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _cap: User = Depends(require_capability("platform_billing")),
):
    """Re-pull invoices and payments for one organization from Stripe.

    Webhooks fail; without a reconciliation path a mirror diverges silently.
    Read-only against Stripe - it lists and retrieves, and creates nothing
    there.
    """
    if current_user.role != "god_admin":
        raise HTTPException(status_code=403, detail="God admin only.")

    org = db.query(Organization).filter(Organization.id == org_id).first()
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    _stripe_client()
    from app.services.billing_reconcile import reconcile_organization
    return reconcile_organization(db, org_id)
