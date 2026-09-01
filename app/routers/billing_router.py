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


def _require_admin(current_user: User = Depends(get_current_user)):
    if current_user.role not in ("org_admin", "super_admin", "god_admin"):
        raise HTTPException(status_code=403, detail="Billing access requires admin role.")
    return current_user


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
def get_plans(current_user: User = Depends(_require_admin)):
    return {"plans": PLANS}


# ---------------------------------------------------------------------------
# GET /billing/subscription
# ---------------------------------------------------------------------------
@router.get("/subscription")
def get_subscription(
    current_user: User = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

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
    current_user: User = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    if req.plan not in ("starter", "growth", "professional"):
        raise HTTPException(status_code=400, detail="Invalid plan. Choose starter, growth, or professional.")
    if req.interval not in ("month", "year"):
        raise HTTPException(status_code=400, detail="Interval must be 'month' or 'year'.")

    plan_info = PLANS[req.plan]
    _stripe_client()

    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

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
    current_user: User = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    _stripe_client()
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
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

    etype = event["type"]
    data = event["data"]["object"]

    if etype == "checkout.session.completed":
        org_id = data.get("metadata", {}).get("org_id")
        plan = data.get("metadata", {}).get("plan")
        interval = data.get("metadata", {}).get("interval", "month")
        sub_id = data.get("subscription")
        customer_id = data.get("customer")
        if org_id and plan:
            org = db.query(Organization).filter(Organization.id == org_id).first()
            if org:
                org.plan = plan
                org.stripe_subscription_id = sub_id
                org.stripe_customer_id = customer_id
                org.stripe_plan_interval = interval
                org.billing_status = "active"
                db.commit()
                logger.info("Billing activated: org=%s plan=%s", org_id, plan)

    elif etype == "customer.subscription.updated":
        sub_id = data.get("id")
        org = db.query(Organization).filter(Organization.stripe_subscription_id == sub_id).first()
        if org:
            org.billing_status = data.get("status")
            meta_plan = data.get("metadata", {}).get("plan")
            if meta_plan:
                org.plan = meta_plan
            db.commit()

    elif etype == "customer.subscription.deleted":
        sub_id = data.get("id")
        org = db.query(Organization).filter(Organization.stripe_subscription_id == sub_id).first()
        if org:
            org.billing_status = "canceled"
            org.plan = "trial"
            org.stripe_subscription_id = None
            db.commit()
            logger.info("Subscription canceled: org=%s", org.id)

    return {"received": True}


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
