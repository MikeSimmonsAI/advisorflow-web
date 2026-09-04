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
from typing import List, Optional

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
# Plan catalog — LEGACY. NOT A PRICING AUTHORITY FOR NEW BILLING (P5).
#
# This dictionary predates the deal-driven billing system. It is now used in
# exactly three places and each is deliberate:
#
#   GET /plans          DISPLAY ONLY - the catalogue a self-serve customer may
#                       choose from.
#   GET /subscription   DISPLAY ONLY - `plan_details` enriches a plan KEY for
#                       the UI. Nothing is billed from it.
#   POST /checkout      LEGACY SELF-SERVE BILLING - the only remaining path
#                       that prices from this dict, and only for an
#                       organization that has NO BillingAgreement. See the
#                       guard on that route.
#
# New, deal-driven billing does not read this dict at all. It flows
# Implementation -> BillingAgreement -> app/services/billing_operations.py,
# and the amount is copied from the agreement.
#
# DO NOT price anything new from here, and do not "reconcile" an existing
# customer against it: a customer on a negotiated or legacy rate would be
# repriced to list, which is the one outcome P5 exists to prevent. The
# reconciliation tooling in app/services/billing_migration.py reads it for
# COMPARISON ONLY and says so at every use.
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
    eventually adds tenant data to.

    DISPLAY ONLY. A customer with a negotiated deal is not on any of these
    prices, and what they pay comes from their BillingAgreement."""
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
        # DISPLAY ONLY: the catalogue entry for a plan KEY, for the UI. It is
        # not what this customer is billed - `GET /billing/overview` reports
        # that, from the agreement and the invoice mirror.
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
    """LEGACY SELF-SERVE CHECKOUT. Catalogue-priced, and kept working.

    P5 did not rewrite this route. It added two guards in front of it, because
    the catalogue price is correct ONLY for a customer who has no negotiated
    deal and no subscription yet - and it was previously applied to everybody
    who called.
    """
    if req.plan not in ("starter", "growth", "professional"):
        raise HTTPException(status_code=400, detail="Invalid plan. Choose starter, growth, or professional.")
    if req.interval not in ("month", "year"):
        raise HTTPException(status_code=400, detail="Interval must be 'month' or 'year'.")

    org = scope.organization

    # ── GUARD 1: A NEGOTIATED DEAL IS NOT PRICED FROM THE CATALOGUE ─────────
    # An organization with a live BillingAgreement has an approved amount that
    # somebody negotiated. Letting this route bill them list price would be a
    # silent repricing, and it is the exact failure P5 exists to prevent, so
    # the agreement path is preferred by refusing this one rather than by
    # quietly substituting the agreement's number behind a UI still showing
    # the catalogue price.
    from app.services import billing_agreement as _agreements  # noqa: E402
    live_agreement = _agreements.current_for_organization(db, org.id)
    if live_agreement is not None:
        raise HTTPException(
            status_code=409,
            detail="This organization is billed from an approved agreement, "
                   "not the self-serve catalogue. Use "
                   "POST /billing/agreements/{agreement_id}/subscribe to "
                   "execute it.")

    _stripe_client()

    # ── GUARD 2: NO SECOND SUBSCRIPTION ────────────────────────────────────
    # Previously absent. An organization already carrying a live Stripe
    # subscription that called this route got a SECOND one - two recurring
    # charges for one customer. Stripe is asked rather than trusting the local
    # column, because a cancelled subscription may still be named there and
    # must not block a legitimate new signup.
    existing_sub = getattr(org, "stripe_subscription_id", None)
    if existing_sub:
        try:
            existing = stripe.Subscription.retrieve(existing_sub)
            existing_status = getattr(existing, "status", None)
        except Exception as exc:
            # Cannot prove it is safe, and this route needs Stripe anyway.
            # Refusing costs a signup nothing that would have worked.
            logger.warning("checkout blocked: subscription %s unreadable: %s",
                           existing_sub, exc)
            raise HTTPException(
                status_code=503,
                detail="Existing subscription could not be verified. Please "
                       "try again shortly.")
        if existing_status in ("active", "trialing", "past_due", "unpaid"):
            raise HTTPException(
                status_code=409,
                detail="This organization already has an active subscription. "
                       "Use the billing portal to change plans.")

    plan_info = PLANS[req.plan]

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


# ═════════════════════════════════════════════════════════════════════════════
# P4 — BILLING OPERATIONS
#
# THIN BY DESIGN. Every handler below does three things and nothing else:
# take the BillingScope P3 resolved, hand it to a service, and translate a
# refusal into an HTTP status. There is no Stripe call, no tenant check and no
# money arithmetic in this file - all three live in app/services/.
#
# No handler accepts an organization id. The subject is always the active
# authorized workspace, so a caller who knows another tenant's UUID, invoice id
# or Stripe id has nothing to put it in. Invoice and agreement ids ARE accepted
# and are matched inside an organization-scoped query by the services, which is
# why guessing one returns the same "no such invoice" as inventing one.
#
# READS require billing_view. MUTATIONS require billing_manage.
# ═════════════════════════════════════════════════════════════════════════════

from app.services import billing_operations as ops  # noqa: E402
from app.services.billing_operations import BillingOperationRefused  # noqa: E402
from app.services.stripe_gateway import (LiveModeRefused,  # noqa: E402
                                         StripeOperationFailed,
                                         StripeUnavailable)


def _handle(fn, *args, **kwargs):
    """Run a billing service call and translate its failure modes.

    One translator rather than a try/except in every handler, so the mapping
    from failure to status code is decided once:

        refused         400  the request is not valid for this data
        unavailable     503  Stripe could not be reached - retryable
        live key        503  refused on purpose; this build is sandbox-only
        Stripe refusal  402  Stripe declined the operation itself
    """
    try:
        return fn(*args, **kwargs)
    except BillingOperationRefused as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except LiveModeRefused as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except StripeUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="Billing is temporarily unavailable. %s" % exc)
    except StripeOperationFailed as exc:
        raise HTTPException(status_code=402, detail=str(exc))


# ── Reads ────────────────────────────────────────────────────────────────────

@router.get("/overview")
def billing_overview(scope: BillingScope = Depends(require_billing_view),
                     db: Session = Depends(get_db)):
    """Everything the organization Billing screen needs, in one request.

    Built this way so P6's UI never calls Stripe and never learns a raw Stripe
    object shape.
    """
    return _handle(ops.billing_overview, db, scope)


@router.get("/invoices")
def list_invoices(scope: BillingScope = Depends(require_billing_view),
                  db: Session = Depends(get_db)):
    return {"invoices": _handle(ops.list_invoices, db, scope)}


@router.get("/invoices/{invoice_id}")
def get_invoice(invoice_id: str,
                scope: BillingScope = Depends(require_billing_view),
                db: Session = Depends(get_db)):
    invoice = _handle(ops._invoice_in_scope, db, scope, invoice_id)
    return ops.describe_invoice(invoice)


@router.get("/payments")
def list_payments(scope: BillingScope = Depends(require_billing_view),
                  db: Session = Depends(get_db)):
    return {"payments": _handle(ops.list_payments, db, scope)}


@router.get("/agreement")
def get_agreement(scope: BillingScope = Depends(require_billing_view),
                  db: Session = Depends(get_db)):
    agreement = _handle(ops.current_agreement, db, scope)
    return ops._describe_agreement(agreement)


# ── Mutations ────────────────────────────────────────────────────────────────

class InvoiceLineItemRequest(BaseModel):
    # INTEGER MINOR UNITS, and the type is the guard. A float here is how a
    # rounding error becomes a customer's invoice.
    amount_cents: int
    description: Optional[str] = None
    currency: Optional[str] = None


class CreateInvoiceRequest(BaseModel):
    line_items: List[InvoiceLineItemRequest]
    description: Optional[str] = None
    days_until_due: int = 30
    # OPT-IN DUPLICATE PROTECTION. One id per submission, reused on retry, and
    # the retry returns the first invoice instead of making a second. Omitted,
    # two submissions are two invoices - which is right, because billing the
    # same amount twice is a normal thing to want.
    request_id: Optional[str] = None


@router.post("/invoices")
def create_invoice(req: CreateInvoiceRequest,
                   scope: BillingScope = Depends(require_billing_manage),
                   db: Session = Depends(get_db)):
    """Create a DRAFT invoice. Nothing is charged until it is finalized."""
    items = [{"amount_cents": i.amount_cents,
              "description": i.description,
              "currency": i.currency} for i in req.line_items]
    return _handle(ops.create_draft_invoice, db, scope, items,
                   req.description, req.days_until_due, req.request_id)


@router.post("/invoices/{invoice_id}/finalize")
def finalize_invoice(invoice_id: str,
                     scope: BillingScope = Depends(require_billing_manage),
                     db: Session = Depends(get_db)):
    return _handle(ops.finalize_invoice, db, scope, invoice_id)


@router.post("/invoices/{invoice_id}/send")
def send_invoice(invoice_id: str,
                 scope: BillingScope = Depends(require_billing_manage),
                 db: Session = Depends(get_db)):
    return _handle(ops.send_invoice, db, scope, invoice_id)


@router.post("/invoices/{invoice_id}/void")
def void_invoice(invoice_id: str,
                 scope: BillingScope = Depends(require_billing_manage),
                 db: Session = Depends(get_db)):
    return _handle(ops.void_invoice, db, scope, invoice_id)


@router.post("/agreements/{agreement_id}/subscribe")
def subscribe_agreement(agreement_id: str,
                        scope: BillingScope = Depends(require_billing_manage),
                        db: Session = Depends(get_db)):
    """Execute a BillingAgreement as a Stripe subscription.

    Retry-safe: an agreement that already has one returns it with
    created=false rather than making a second.
    """
    return _handle(ops.create_subscription, db, scope, agreement_id)


class CancelSubscriptionRequest(BaseModel):
    # Defaults to end-of-period: the customer paid for the period they are in.
    at_period_end: bool = True


@router.post("/agreements/{agreement_id}/cancel")
def cancel_agreement_subscription(
        agreement_id: str,
        req: CancelSubscriptionRequest = CancelSubscriptionRequest(),
        scope: BillingScope = Depends(require_billing_manage),
        db: Session = Depends(get_db)):
    return _handle(ops.cancel_subscription, db, scope, agreement_id,
                   req.at_period_end)


# ═════════════════════════════════════════════════════════════════════════════
# P5 — RECONCILIATION. READ ONLY, AND THERE IS NO OTHER MODE.
#
# Neither route below can write. The service they call has no mutation path at
# all - not a disabled one, not one behind a flag - because a reconciliation
# endpoint that can also "fix" things is one somebody eventually calls with the
# wrong parameter against production billing.
#
# The per-organization report is scoped by BillingScope like every other P4
# read, so it can only ever describe the active authorized workspace. The
# platform-wide report requires god_admin AND the platform_billing capability,
# matching /billing/reconcile/{org_id} above.
# ═════════════════════════════════════════════════════════════════════════════

from app.services import billing_migration as migration  # noqa: E402
from app.services.billing_access import \
    resolve_billing_scope as _resolve_scope  # noqa: E402


# ═════════════════════════════════════════════════════════════════════════════
# P6 — CAN THIS PERSON SEE BILLING AT ALL?
#
# The sidebar needs an answer it can render a nav item from, and there was no
# honest source for it. `GET /settings/my-capabilities` looks like the right
# one and is not, for two reasons that both matter:
#
#   1. IT RESOLVES AGAINST `users.organization_id`. That is the legacy tenant
#      column P3 removed from billing precisely because it names the wrong
#      organization for anyone holding more than one membership. A sidebar
#      built on it would offer - or hide - Billing based on a workspace the
#      person is not standing in.
#   2. `capabilities.resolve` refuses a non-admin role BEFORE it reads grants,
#      so a bookkeeper holding `billing_view` resolves to "no". P3 documented
#      that deviation and worked around it; the sidebar cannot.
#
# So this asks the SAME function the billing routes ask, against the ACTIVE
# workspace, and reports rather than refuses - which is what
# `resolve_billing_scope` was built for.
#
# THIS IS NOT ACCESS CONTROL. Hiding a nav item never is: every billing route
# enforces its own dependency, and the security tests call them directly with
# the nav item hidden. It exists so the sidebar stops offering a door that
# opens onto a 403.
# ═════════════════════════════════════════════════════════════════════════════

@router.get("/access")
def billing_access(request: Request,
                   db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    """What this caller may do with billing, in the workspace they are in.

    Answers 200 for everyone, including "nothing" - a person with no billing
    authority is not an error, and a 403 here would leave the sidebar unable
    to tell "denied" from "the request failed".
    """
    scope = _resolve_scope(db, current_user, request)
    return {
        "can_view": scope.can_view,
        "can_manage": scope.can_manage,
        "organization_id": scope.organization_id,
        "organization_name": (scope.organization.name
                              if scope.organization is not None else None),
    }




@router.get("/reconciliation")
def billing_reconciliation(scope: BillingScope = Depends(require_billing_view),
                           db: Session = Depends(get_db)):
    """This organization's billing records, compared. Writes nothing.

    Shows what the catalogue would charge alongside what this customer is
    actually charged, so the gap is visible. The gap is information, not a
    defect to be corrected: a customer on a negotiated rate is meant to differ
    from list.
    """
    return _handle(migration.reconcile_organization, db, scope.organization)


@router.get("/reconciliation/platform", include_in_schema=False)
def platform_billing_reconciliation(
    limit: int = 500,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _cap: User = Depends(require_capability("platform_billing")),
):
    """Every organization, compared. Writes nothing.

    The P5 worklist: which customers are billed with no agreement, where the
    approved deal and Stripe disagree, and which local Stripe ids no longer
    resolve.
    """
    if current_user.role != "god_admin":
        raise HTTPException(status_code=403, detail="God admin only.")
    return _handle(migration.reconcile_all, db, limit)
