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
from app.models.billing_models import (BillingInterval, BillingInvoice,
                                       BillingPayment, ChangeTiming,
                                       ProrationBehavior, SubscriptionStatus)
from app.services.capabilities import require_capability
from app.services import (billing_catalog, billing_policy, billing_schedule,
                          billing_webhook)
from app.routers.audit_log_router import log_action

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/billing", tags=["billing"])


def _brand_base_url(db: Session, org: Organization) -> str:
    """Return the customer to THEIR brand's domain, never a platform hostname."""
    from app.services.public_identity import public_base_url as _public_base
    return (_public_base(db, org.id)
            or os.environ.get("APP_BASE_URL", "").strip()
            or "https://advisorflow-frontend.onrender.com")


def _audit(db: Session, org: Organization, user: User, action: str, details: dict) -> None:
    """Money-adjacent state changes go in the audit log, not just a log line.

    Billing was the least-audited control-plane surface in this codebase: plan
    activation, interval change and cancellation were `logger.info` only, while
    a full audited log_action engine was already in use by the neighbouring God
    billing route. A log line rotates away; "who moved this customer to Growth,
    and when" is a question somebody asks months later.

    NEVER carries a secret. Plan keys, intervals and public Stripe object ids
    only - no API key, no webhook secret, no card detail.
    """
    try:
        log_action(db, org.id, getattr(user, "id", None), action=action,
                   target_type="organization", target_id=org.id, details=details)
    except Exception:
        # An audit failure must not take a payment path down with it.
        logger.exception("billing: audit write failed for %s", action)


def _price_payload_for(plan, interval: str, db: Session, org: Organization) -> dict:
    """The Stripe price for this plan, PREFERRING A REAL PRICE OBJECT.

    When the brand's catalogue carries a Stripe price id, that id is used. The
    previous implementation built an inline `price_data` block on every call,
    which created a brand-new anonymous Price in Stripe for every checkout -
    thousands of one-off Prices, no Product, and nothing in the dashboard that
    could be reported on or reconciled.

    The inline fallback remains for a brand whose Stripe Products have not been
    created yet, so a new brand is not blocked on that setup step. It is a
    fallback, not the design.
    """
    price_id = billing_catalog.stripe_price_id_for(plan, interval)
    if price_id:
        return {"price": price_id}

    cents = billing_catalog.price_cents_for(plan, interval)
    if cents is None:
        raise HTTPException(
            status_code=400,
            detail="Plan %r has no %s price configured." % (plan.key, interval))

    # The product NAME comes from the brand, not from a hardcoded string. It is
    # what appears on the customer's card statement and receipt, and it was
    # previously hardcoded to "BookaBoost {plan}" - so an EvoSys Pro customer's
    # invoice read BookaBoost. That is the same defect already fixed twice
    # elsewhere (support email, redirect host); this is the third instance.
    brand_name = _brand_display_name(db, org)
    return {
        "price_data": {
            "currency": plan.currency or "usd",
            "unit_amount": cents,
            "recurring": {"interval": interval},
            "product_data": {"name": "%s %s" % (brand_name, plan.name)},
        }
    }


def _line_item_for(plan, interval: str, db: Session, org: Organization) -> dict:
    payload = _price_payload_for(plan, interval, db, org)
    return {**payload, "quantity": 1}


def _brand_display_name(db: Session, org: Organization) -> str:
    """The brand's own name for receipts. Falls back to the org's platform row."""
    try:
        from app.models.models import Platform
        pid = getattr(org, "platform_id", None)
        if pid:
            platform = db.query(Platform).filter(Platform.id == pid).first()
            if platform is not None:
                for attr in ("display_name", "name", "brand_name"):
                    value = getattr(platform, attr, None)
                    if value:
                        return str(value)
    except Exception:
        logger.exception("billing: could not resolve brand display name")
    return "Subscription"

# ---------------------------------------------------------------------------
# Plan catalog
#
# THE HARDCODED `PLANS` DICT THAT LIVED HERE IS GONE. It is now
# `brand_billing_plans`, scoped per brand, read through billing_catalog.
#
# It had to go rather than be tidied, for three reasons that a dict cannot fix:
#
#   IT COULD NOT BE BRAND-SCOPED. A module global is the same for every brand,
#   so a second white-label brand's customers would have been shown - and
#   charged - EvoSys Pro's prices, offered EvoSys Pro's three tier names, and
#   sent a receipt reading "BookaBoost".
#
#   IT WAS NOT THE ONLY COPY. frontend/src/pages/Billing.jsx carried its own
#   literal array of the same prices and rendered THAT; this endpoint was never
#   called. Two hand-maintained copies of a price list is one copy too many,
#   and they had already drifted on the annual discount.
#
#   IT MADE PRICE A CONSTANT RATHER THAN A DECISION. Changing what a customer
#   pays required a deploy.
#
# The one thing to keep hold of: `brand_packages` is a DIFFERENT catalogue for
# a different purpose (what the sales team sells, and what compensation is
# computed from) and it keys on the same three strings. Nothing here reads it.
# ---------------------------------------------------------------------------


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
def get_plans(current_user: User = Depends(_require_admin),
              db: Session = Depends(get_db)):
    """THIS BRAND'S plans, from the database, scoped to the caller's own brand.

    Previously returned a module-global dict identical for every brand. The
    Billing screen did not even call it - it rendered its own hardcoded copy of
    the same prices, which had already drifted from this one on the annual
    discount. Now there is one source, it is per-brand, and the screen reads it.
    """
    org = db.query(Organization).filter(
        Organization.id == current_user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    platform_id = billing_catalog.platform_id_for_org(db, org)
    if not platform_id:
        return {"plans": [], "configured": False,
                "detail": "This organization is not attached to a brand, so no "
                          "plan catalogue applies."}

    plans = billing_catalog.plans_for(db, platform_id)
    return {
        "plans": [_plan_public(p) for p in plans],
        "configured": bool(plans),
        "current_plan": getattr(org, "billing_plan_key", None) or org.plan,
        "current_interval": getattr(org, "stripe_plan_interval", None) or "month",
    }


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

    platform_id = billing_catalog.platform_id_for_org(db, org)
    current_key = getattr(org, "billing_plan_key", None) or org.plan
    current_plan = billing_catalog.resolve_plan(db, platform_id, current_key)

    result = {
        "plan": current_key or "trial",
        "billing_status": getattr(org, "billing_status", None) or "trialing",
        "stripe_customer_id": getattr(org, "stripe_customer_id", None),
        "stripe_subscription_id": getattr(org, "stripe_subscription_id", None),
        "stripe_plan_interval": getattr(org, "stripe_plan_interval", None) or "month",
        "plan_details": _plan_public(current_plan) if current_plan else None,
        # Read from the LOCAL MIRROR, which the webhook keeps current.
        #
        # This used to be a synchronous, uncached Stripe API call on every load
        # of the Billing screen, which made the page exactly as fast and as
        # available as Stripe's API on its worst day - for two values the
        # webhook already tells us.
        "current_period_end": getattr(org, "billing_current_period_end", None),
        "cancel_at_period_end": bool(getattr(org, "billing_cancel_at_period_end", False)),
        "trial_end": getattr(org, "billing_trial_end", None),
        # A downgrade that has been requested and has not taken effect yet. The
        # customer keeps the tier they paid for until the period ends, so both
        # answers are true at once and the screen shows both rather than
        # pretending the change already happened.
        "pending_plan": getattr(org, "billing_pending_plan_key", None),
        "pending_effective_at": getattr(org, "billing_pending_effective_at", None),
        # WHEN THIS CUSTOMER STARTED. Their own record, not a billing figure -
        # and the one line on a billing screen that is about the relationship
        # rather than the money.
        "customer_since": getattr(org, "created_at", None),
    }

    # WHAT THEY ACTUALLY PAY, RESOLVED SERVER-SIDE.
    #
    # The recurring amount for the plan they are on, at the interval they are
    # billed on. The browser is never told to compute this: an amount derived
    # in a page is an amount a customer can edit, and this screen has already
    # had one hand-maintained price list drift away from the catalogue.
    #
    # None where the plan carries no price for that interval, which the screen
    # must render as "not priced" rather than as free.
    result["recurring_cents"] = (
        billing_catalog.price_cents_for(current_plan, result["stripe_plan_interval"])
        if current_plan else None)
    result["currency"] = getattr(current_plan, "currency", None) or "usd"

    # THE CARD ON FILE, AS A SUMMARY. Brand, last four, expiry - mirrored from
    # what Stripe volunteered on a paid invoice. NULL until an invoice has
    # actually been paid, and never guessed: the screen says "managed in the
    # billing portal" rather than inventing a brand.
    last4 = getattr(org, "billing_card_last4", None)
    result["payment_method"] = {
        "brand": getattr(org, "billing_card_brand", None) if last4 else None,
        "last4": last4,
        "exp_month": getattr(org, "billing_card_exp_month", None) if last4 else None,
        "exp_year": getattr(org, "billing_card_exp_year", None) if last4 else None,
        "on_file": bool(last4),
        # A customer id exists as soon as checkout starts, so it is not proof
        # of a card - but without one there is no Portal to send them to.
        "manageable": bool(getattr(org, "stripe_customer_id", None)),
    }

    # WHAT THE PLAN ACTUALLY ENFORCES, with usage. Shown so a customer can see
    # they are near a ceiling before an action starts failing, and - where a
    # downgrade is scheduled - what they will drop to before it happens.
    from app.services import plan_limits
    result["limits"] = plan_limits.report(db, org)

    # Recent invoices, from the local mirror. The customer can see what they
    # were charged without a round trip, and the hosted receipt URL is Stripe's
    # own public link.
    invoices = (db.query(BillingInvoice)
                .filter(BillingInvoice.organization_id == org.id)
                .order_by(BillingInvoice.period_start.desc().nullslast())
                .limit(12).all())
    result["invoices"] = [{
        "id": i.stripe_invoice_id,
        "status": i.status,
        "amount_paid_cents": i.amount_paid_cents,
        "amount_due_cents": i.amount_due_cents,
        "currency": i.currency,
        "period_start": i.period_start,
        "period_end": i.period_end,
        "paid_at": i.paid_at,
        "hosted_invoice_url": i.hosted_invoice_url,
    } for i in invoices]

    return result


def _plan_public(plan) -> dict:
    """What a customer may see about a plan. No Stripe ids, no internals."""
    return {
        "key": plan.key,
        "name": plan.name,
        "description": plan.description,
        "monthly_cents": plan.monthly_cents,
        "annual_cents": plan.annual_cents,
        "currency": plan.currency,
        "max_leads": plan.max_leads,
        "max_users": plan.max_users,
        "features": billing_catalog.features_for(plan),
        "is_purchasable": bool(plan.is_purchasable),
    }


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
    """Start a NEW subscription. Refuses if one already exists.

    ═══════════════════════════════════════════════════════════════════════
    THIS REFUSAL IS THE FIX FOR A LIVE DOUBLE-BILLING DEFECT.
    ═══════════════════════════════════════════════════════════════════════

    Every plan card on the Billing screen was a live "Select Plan" button, and
    each one called this endpoint, which created a SECOND Stripe subscription
    without cancelling the first. A customer on Starter who clicked Growth was
    billed for both, every month, until somebody noticed.

    A checkout session is for acquiring a subscription. Changing one is a
    different operation with different money consequences, and it now lives at
    POST /billing/change-plan, which modifies the existing subscription in
    place. Splitting them means the duplicate-subscription path is not one
    mis-click away - it does not exist.
    """
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    # ── The price is resolved SERVER-SIDE, from this org's own brand ──────
    #
    # The request names a plan and an interval. It does not carry an amount and
    # it does not carry a Stripe price id, because both are things a customer
    # would enjoy choosing. A plan key belonging to a different brand resolves
    # to nothing here, so one brand's customer cannot buy - or discover -
    # another brand's tier.
    platform_id = billing_catalog.platform_id_for_org(db, org)
    if not platform_id:
        raise HTTPException(
            status_code=409,
            detail="This organization is not attached to a brand, so no plan "
                   "catalogue applies. Contact support.")
    try:
        plan = billing_catalog.require_purchasable(db, platform_id, req.plan, req.interval)
    except billing_catalog.PlanNotAvailable as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # ── Refuse a second primary subscription ─────────────────────────────
    existing_status = (getattr(org, "billing_status", None) or "").lower()
    if getattr(org, "stripe_subscription_id", None) and existing_status in SubscriptionStatus.OCCUPIED:
        raise HTTPException(
            status_code=409,
            detail="This organization already has a subscription. Use "
                   "'change plan' to move between plans - starting a second "
                   "checkout would bill you twice.")

    _stripe_client()
    customer_id = _get_or_create_customer(org, db)

    # The customer paying is a funeral home on a white-label brand. Bouncing
    # them to an AdvisorFlow Render hostname after checkout tells them who
    # their software really belongs to. Their own brand's domain first.
    base_url = _brand_base_url(db, org)

    line_item = _line_item_for(plan, req.interval, db, org)

    trial_days = billing_policy.trial_days(db, platform_id)
    subscription_data = {"metadata": {"org_id": org.id, "plan": plan.key}}
    if trial_days:
        subscription_data["trial_period_days"] = trial_days

    session = stripe.checkout.Session.create(
        customer=customer_id,
        mode="subscription",
        line_items=[line_item],
        metadata={"org_id": org.id, "plan": plan.key, "interval": req.interval},
        subscription_data=subscription_data,
        success_url=f"{base_url}/billing?success=1",
        cancel_url=f"{base_url}/billing?canceled=1",
    )
    _audit(db, org, current_user, "billing.checkout_started",
           {"plan": plan.key, "interval": req.interval})
    return {"checkout_url": session.url}


# ---------------------------------------------------------------------------
# POST /billing/change-plan
# ---------------------------------------------------------------------------
class ChangePlanRequest(BaseModel):
    plan: str
    interval: str = "month"


@router.post("/change-plan")
def change_plan(
    req: ChangePlanRequest,
    current_user: User = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """Move an EXISTING subscription to a different plan, in place.

    Modifies the subscription rather than creating a second one - see the note
    on /checkout for what the previous behaviour cost.

    TIMING AND PRORATION COME FROM BRAND CONFIGURATION, NOT FROM THIS CODE.
    The decided EvoSys policy is: an upgrade applies immediately with
    proration; a downgrade applies at the end of the period already paid for,
    with no credit or refund. Both are stored on the brand's billing config so
    a second brand can choose differently without a code change. If a brand has
    not configured the direction being requested, this endpoint REFUSES rather
    than picking a timing - applying a plan change on a guessed schedule either
    bills someone early or gives away a tier.
    """
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    sub_id = getattr(org, "stripe_subscription_id", None)
    if not sub_id:
        raise HTTPException(
            status_code=409,
            detail="No active subscription to change. Choose a plan to get "
                   "started.")

    platform_id = billing_catalog.platform_id_for_org(db, org)
    try:
        target = billing_catalog.require_purchasable(db, platform_id, req.plan, req.interval)
    except billing_catalog.PlanNotAvailable as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    current = billing_catalog.resolve_plan(
        db, platform_id, getattr(org, "billing_plan_key", None) or org.plan)
    current_interval = getattr(org, "stripe_plan_interval", None)

    direction = billing_catalog.classify_change(
        db, current, current_interval, target, req.interval)

    if direction == billing_catalog.LATERAL:
        raise HTTPException(
            status_code=400,
            detail="That is the plan and billing period you are already on.")

    behavior = billing_policy.change_behavior(db, platform_id, direction)
    if not behavior["configured"]:
        # POLICY REQUIRED. Refusing is the honest answer: the alternative is
        # to invent a timing, and the two options differ by real money.
        raise HTTPException(
            status_code=409,
            detail="No %s policy is configured for this brand, so the change "
                   "cannot be applied. An administrator must set the plan "
                   "change timing and proration behaviour first."
                   % direction)

    _stripe_client()
    try:
        sub = stripe.Subscription.retrieve(sub_id)
        item_id = sub["items"]["data"][0]["id"]
    except Exception as exc:
        logger.warning("change_plan: could not read subscription %s: %s", sub_id, exc)
        raise HTTPException(status_code=502,
                            detail="Could not read the current subscription "
                                   "from the payment processor.")

    # ══════════════════════════════════════════════════════════════════════
    # DEFERRED (downgrade) and IMMEDIATE (upgrade) are genuinely different
    # Stripe operations, not one call with a different flag.
    # ══════════════════════════════════════════════════════════════════════
    if behavior["timing"] == ChangeTiming.PERIOD_END:
        # A SUBSCRIPTION SCHEDULE, not a modify.
        #
        # This previously called Subscription.modify with
        # proration_behavior="none" and billing_cycle_anchor="unchanged",
        # believing that deferred the change. It does not - the item swap takes
        # effect IMMEDIATELY, so the customer lost the tier they had already
        # paid for and received no credit for the difference. Less product,
        # same money. See billing_schedule.py for the full account.
        target_price_id = billing_catalog.stripe_price_id_for(target, req.interval)
        try:
            outcome = billing_schedule.schedule_change_at_period_end(
                sub_id,
                existing_schedule_id=getattr(org, "stripe_schedule_id", None),
                target_price_id=target_price_id,
                target_plan_key=target.key,
                org_id=org.id,
            )
        except billing_schedule.ScheduleError as exc:
            # 409 rather than 502: for the common cause - no Stripe Price
            # mapped for this plan - the processor is not at fault and an
            # administrator can fix it. The message says which.
            raise HTTPException(status_code=409, detail=str(exc))

        # THE PENDING CHANGE IS RECORDED; THE CURRENT PLAN IS NOT TOUCHED.
        # `billing_plan_key` still says what they are entitled to today, which
        # is what entitlements read. Only when Stripe's schedule actually
        # advances does the webhook move it.
        org.stripe_schedule_id = outcome["schedule_id"]
        org.billing_pending_plan_key = target.key
        org.billing_pending_effective_at = (
            outcome["effective_at"] or getattr(org, "billing_current_period_end", None))
        effective_text = "at the end of your current billing period"
        proration_applied = ProrationBehavior.NONE

    else:
        # UPGRADE - immediate, prorated. The customer asked for more and gets
        # it now; Stripe charges the difference for the remainder of the period.
        new_item = _price_payload_for(target, req.interval, db, org)
        try:
            stripe.Subscription.modify(
                sub_id,
                items=[{"id": item_id, **new_item}],
                proration_behavior=behavior["proration"],
                metadata={"org_id": org.id, "plan": target.key},
            )
        except Exception as exc:
            logger.warning("change_plan: modify failed for %s: %s", sub_id, exc)
            raise HTTPException(status_code=502,
                                detail="The payment processor rejected the plan "
                                       "change.")

        # AN UPGRADE SUPERSEDES ANY PENDING DOWNGRADE. Somebody who downgrades
        # on Monday and upgrades on Tuesday must not still drop a tier at month
        # end because a schedule nobody cancelled was still sitting there.
        if getattr(org, "stripe_schedule_id", None):
            billing_schedule.release(org.stripe_schedule_id)
            org.stripe_schedule_id = None
        org.billing_pending_plan_key = None
        org.billing_pending_effective_at = None
        effective_text = "immediately"
        proration_applied = behavior["proration"]

    # THE CURRENT PLAN IS NOT WRITTEN HERE, on purpose. The
    # subscription.updated webhook is the authoritative record of what Stripe
    # actually did, and writing the outcome optimistically from this side is
    # how a UI ends up showing a plan the customer is not on.
    _audit(db, org, current_user, "billing.plan_change_requested", {
        "from": getattr(org, "billing_plan_key", None) or org.plan,
        "to": target.key,
        "interval": req.interval,
        "direction": direction,
        "timing": behavior["timing"],
        "proration": proration_applied,
        "schedule_id": getattr(org, "stripe_schedule_id", None),
    })
    db.commit()

    return {
        "ok": True,
        "direction": direction,
        "timing": behavior["timing"],
        "proration": proration_applied,
        "effective": effective_text,
        "effective_at": getattr(org, "billing_pending_effective_at", None),
        "pending_plan": getattr(org, "billing_pending_plan_key", None),
        # What they keep until then. Stated explicitly so the screen does not
        # have to infer that a downgrade is not yet in force.
        "current_plan": getattr(org, "billing_plan_key", None) or org.plan,
    }


@router.post("/cancel-pending-change")
def cancel_pending_change(
    current_user: User = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """Drop a scheduled downgrade before it lands.

    A customer who scheduled a downgrade and changed their mind should not have
    to upgrade-and-re-downgrade to undo it, and should not have to wait for a
    change they no longer want. Releasing the schedule returns the subscription
    to ordinary billing on the plan they are already on - it charges nothing
    and refunds nothing, because nothing has happened yet.
    """
    org = db.query(Organization).filter(
        Organization.id == current_user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    if not getattr(org, "billing_pending_plan_key", None):
        raise HTTPException(status_code=409,
                            detail="There is no pending plan change to cancel.")

    _stripe_client()
    released = billing_schedule.release(getattr(org, "stripe_schedule_id", None))

    was = org.billing_pending_plan_key
    org.stripe_schedule_id = None
    org.billing_pending_plan_key = None
    org.billing_pending_effective_at = None
    _audit(db, org, current_user, "billing.pending_change_cancelled",
           {"cancelled_pending_plan": was, "released_at_processor": released})
    db.commit()
    return {"ok": True, "cancelled_pending_plan": was,
            "current_plan": getattr(org, "billing_plan_key", None) or org.plan}


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

    # ── Everything past the signature check lives in billing_webhook ──────
    #
    # The handling used to be inline here and had three problems that only a
    # dedicated module fixes properly:
    #
    #   NO IDEMPOTENCY. Stripe retries on timeout, on any non-2xx, and on its
    #   own schedule for up to three days. Every retry re-ran every write.
    #
    #   NO PAYMENT RECORD. invoice.paid was not handled at all, so no invoice
    #   or payment row existed, God Mode's revenue panels had nothing to read,
    #   and no payment could ever reach the compensation engine.
    #
    #   UNVALIDATED PLAN FROM METADATA. `org.plan = metadata["plan"]` with no
    #   membership check against any catalogue, so anyone able to edit a
    #   subscription in the Stripe dashboard could write an arbitrary string
    #   into a field several screens read.
    #
    # A duplicate event returns 200. It has been handled; a non-2xx would have
    # Stripe redeliver it every few hours for three days.
    result = billing_webhook.handle_event(db, event)
    return {"received": True, **result}


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
