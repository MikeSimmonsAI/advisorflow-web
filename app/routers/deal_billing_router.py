"""
Bill the deal that was actually sold.

TWO ENDPOINTS, ON THE SALES SURFACE ON PURPOSE. A salesperson closing a deal
should not need God Mode to see whether the money side is ready, or to send the
customer a payment link for the terms they themselves negotiated. Both routes
are guarded by the existing sales authority and a per-record opportunity check;
neither invents a permission.

WHAT THIS DOES NOT DO. It does not decide amounts (deal_pricing does), does not
resolve plans (billing_catalog does), does not record payments (billing_webhook
does), and does not touch compensation (billing_compensation does, off the
webhook). It creates one Stripe Checkout Session from terms that already exist
and were already approved, and it writes nothing to the money tables — because
a payment that has not happened must not leave a trace that looks like one.

THE FRONTEND SUCCESS REDIRECT IS NOT PROOF OF PAYMENT and nothing here treats
it as such. This returns a URL. The webhook, verified against Stripe's
signature, is the only thing in this system that says money arrived.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.deps import get_db
from app.models.models import User
from app.models.sales_models import Opportunity
from app.services import deal_billing
from app.services.sales_access import (
    assert_can_view_opportunity, require_sales_member,
)

log = logging.getLogger("deal_billing_router")

router = APIRouter(prefix="/sales", tags=["Sales — deal billing"])


def _opp_in_scope(db: Session, opportunity_id: str, user: User) -> Opportunity:
    """The opportunity, or 404 — never a 403 that confirms it exists.

    The per-record check is the platform's own `assert_can_view_opportunity`,
    so this route cannot drift from what the rest of the sales surface allows.
    Its refusal is converted to 404: a 403 on a deal you may not see tells you
    the deal is real, which is how one brand's rep enumerates another brand's
    pipeline one id at a time.
    """
    opp = db.query(Opportunity).filter(Opportunity.id == opportunity_id).first()
    if opp is None:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    try:
        assert_can_view_opportunity(user, opp, db)
    except HTTPException:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    except Exception:
        # A scope resolver that errors must NARROW, never widen. Falling open
        # here would turn an internal fault into cross-brand pipeline access.
        log.exception("deal_billing: could not resolve opportunity scope")
        raise HTTPException(status_code=404, detail="Opportunity not found")
    return opp


@router.get("/opportunities/{opportunity_id}/billing")
def deal_billing_readiness(
    opportunity_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_sales_member),
) -> dict:
    """What this deal would charge, and everything standing in the way.

    Safe to call at any stage. Charges nothing, creates nothing, and returns
    named blockers a rep can act on rather than a bare "not ready".
    """
    opp = _opp_in_scope(db, opportunity_id, user)
    return deal_billing.terms_for(db, opp)


@router.post("/opportunities/{opportunity_id}/billing/checkout")
def deal_billing_checkout(
    opportunity_id: str,
    part: str = Query(..., pattern="^(setup|subscription)$",
                      description="Which obligation to bill: setup | subscription"),
    db: Session = Depends(get_db),
    user: User = Depends(require_sales_member),
) -> dict:
    """Create the Stripe Checkout Session for ONE of this deal's obligations.

    ═══════════════════════════════════════════════════════════════════════
    TWO BILLS, NEVER ONE. `part` IS REQUIRED, ON PURPOSE.
    ═══════════════════════════════════════════════════════════════════════
    This route used to combine the implementation fee and the first month into
    a single session — Growth came out as one $3,500 charge — and that is wrong
    as a product rather than merely as presentation:

      * A customer who has paid to be implemented but has not started their
        subscription is a real, common, and currently invisible state. So is
        the reverse.
      * One combined charge cannot be tracked, reconciled, refunded or chased
        separately, and a single "paid" flag cannot answer which half arrived.
      * The setup fee is a one-time payment and the subscription is recurring.
        They are different Stripe object types with different lifecycles.

    So there is no "both" option and no default. A caller that does not say
    which obligation it is billing gets a 422 rather than a guess, because the
    guess is the bug this parameter exists to prevent.

    Refuses unless `terms_for` reports zero blockers, so the amount charged is
    always the approved one. NEITHER call marks anything paid — only a verified
    webhook does that.

    WHAT THIS DOES NOT DECIDE. Whether setup must be paid before the
    subscription may start is a business policy nobody has configured, so the
    two are independent here: either can be created, in either order, and
    neither blocks the other. Inventing an ordering rule would be inventing
    policy.
    """
    opp = _opp_in_scope(db, opportunity_id, user)
    terms = deal_billing.terms_for(db, opp)

    # BLOCKERS ARE SCOPED TO THE OBLIGATION THEY ACTUALLY BLOCK. Refusing a
    # setup-fee page because the customer already has a subscription, or
    # because a Stripe price is missing on the mapped plan, would be the
    # combined-charge coupling reappearing as a refusal: one half's problem
    # stopping the other half's money. `blockers_for_part` decides; anything
    # unscoped still blocks both.
    mine = deal_billing.blockers_for_part(terms["blockers"], part)
    if mine:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "This deal cannot be billed yet.",
                    "part": part,
                    "blockers": mine})
    if not terms["charges"]:
        raise HTTPException(status_code=409,
                            detail={"message": "Nothing to charge.",
                                    "blockers": []})

    setup = next((c for c in terms["charges"] if c["kind"] == "setup_fee"), None)
    sub = next((c for c in terms["charges"] if c["kind"] == "subscription"), None)

    # THE REQUESTED HALF MUST EXIST — CHECKED BEFORE STRIPE IS TOUCHED.
    #
    # Asking to bill a setup fee on a deal that has none is a caller error, not
    # something to silently turn into the other obligation — which is exactly
    # how a subscription gets started by somebody who meant to collect an
    # implementation fee.
    #
    # It sits above the Stripe plumbing for two reasons. A caller error must
    # not depend on whether Stripe happens to be configured — it reported 503
    # "processor unavailable" for a request that was simply wrong. And
    # `_get_or_create_customer` WRITES: it creates a Stripe customer and stamps
    # the id on the organization. Doing that for a request that is about to be
    # refused leaves a real customer record behind for a charge that never
    # existed.
    if part == "setup" and not setup:
        raise HTTPException(
            status_code=409,
            detail={"message": "This deal has no one-time setup fee to bill.",
                    "part": part, "blockers": []})
    if part == "subscription" and not sub:
        raise HTTPException(
            status_code=409,
            detail={"message": "This deal has no recurring subscription to start.",
                    "part": part, "blockers": []})

    from app.models.models import Organization
    org = (db.query(Organization)
           .filter(Organization.id == terms["customer_organization_id"]).first())
    if org is None:
        raise HTTPException(status_code=409, detail="Customer organization not found")

    # Reuse the billing router's own Stripe plumbing rather than a second copy:
    # same client construction, same customer get-or-create, same brand base
    # URL. A private import is the honest cost of not duplicating them.
    from app.routers import billing_router as br

    br._stripe_client()
    import stripe

    customer_id = br._get_or_create_customer(org, db)
    base_url = br._brand_base_url(db, org)

    # Metadata is how the webhook reconnects money to the deal. org_id is what
    # the existing handlers key on; opportunity_id and proposal_id are added so
    # the trail from payment back to what was sold survives without a second
    # lookup that could resolve differently later. `part` is what lets the
    # webhook update ONE state and leave the other alone.
    meta = {"org_id": org.id, "opportunity_id": opp.id,
            "source": "deal_billing", "part": part}
    if terms.get("proposal_id"):
        meta["proposal_id"] = str(terms["proposal_id"])
    # PLAN METADATA BELONGS ONLY TO THE SUBSCRIPTION SESSION. `plan` is what
    # `_handle_checkout_completed` reads to stamp a catalogue tier onto the
    # customer organization — so putting it on a SETUP session would move the
    # customer onto a plan the moment they paid an implementation fee, without
    # a subscription existing at all.
    if sub and part == "subscription":
        # `plan` IS DELIBERATELY ABSENT FOR A CUSTOM DEAL. The webhook reads
        # this key to decide which catalogue tier to stamp on the customer
        # organization; a custom rate belongs to no tier, so naming one here
        # would put the org on a plan it is not paying for. billing_webhook
        # already handles the absence correctly — it logs and leaves
        # `billing_plan_key` alone rather than guessing — which is exactly the
        # behaviour a custom deal needs. `deal_kind` records what it is instead,
        # so the trail is not merely a missing field.
        if sub.get("pricing_mode") == deal_billing.PRICING_CUSTOM:
            meta["deal_kind"] = "custom"
            # What licensed this amount, in the session's own trail: a named
            # manager approval where one exists, otherwise the pricing-authority
            # grant the rate was written under. Never blank — "a custom price
            # appeared" is not an answer anybody can audit.
            if sub.get("authority"):
                meta["pricing_authority"] = str(sub["authority"])
            if sub.get("approval_id"):
                meta["pricing_approval_id"] = str(sub["approval_id"])
        else:
            meta["plan"] = sub["plan"]
        # Recorded for both modes: which rate the customer agreed to is part of
        # what was sold, and the invoice trail should not need the deal to
        # answer it.
        if sub.get("commitment"):
            meta["commitment"] = str(sub["commitment"])

    currency = (getattr(org, "billing_currency", None) or "usd").lower()

    try:
        if part == "subscription":
            if sub.get("pricing_mode") == deal_billing.PRICING_CUSTOM:
                # AN INLINE RECURRING PRICE at the deal's agreed amount.
                # `deal_billing.terms_for` — recomputed above in this same
                # request, never taken from the caller — resolved and vetted
                # that amount, so `sub["cents"]` is the deal's own figure and
                # not anything the caller sent.
                line_item = {
                    "price_data": {
                        "currency": currency,
                        "product_data": {
                            "name": sub.get("label") or "Monthly subscription"},
                        "unit_amount": sub["cents"],
                        # Without this the line is a one-off and the whole
                        # session silently stops being a subscription.
                        "recurring": {"interval": sub.get("interval") or "month"},
                    },
                    "quantity": 1,
                }
                # No `plan` here either: subscription metadata is what the
                # webhook reads on renewals, so naming a tier here would
                # mismap the org on every future invoice, not just the first.
                sub_meta = dict(meta)
            else:
                from app.services import billing_catalog
                platform_id = billing_catalog.platform_id_for_org(db, org)
                plan = billing_catalog.resolve_plan(db, platform_id, sub["plan"])
                # THE SAME COMMITMENT `terms_for` PRICED. Resolving the price
                # without it would charge a month-to-month customer the
                # committed rate — the readiness panel would show $597 and the
                # card would be charged $500.
                price_id = billing_catalog.stripe_price_id_for(
                    plan, "month", sub.get("commitment"))
                line_item = {"price": price_id, "quantity": 1}
                sub_meta = {**meta, "plan": sub["plan"]}
            kwargs = {
                "customer": customer_id,
                "mode": "subscription",
                "line_items": [line_item],
                "metadata": {**meta, "interval": "month"},
                "subscription_data": {"metadata": sub_meta},
                # BRANDED, NEVER A PLATFORM HOSTNAME. `_brand_base_url` resolves
                # the customer's own brand domain first; the environment is only
                # a fallback for an org whose platform row has none.
                "success_url": "%s/billing?success=1&part=subscription" % base_url,
                "cancel_url": "%s/billing?canceled=1&part=subscription" % base_url,
            }
            # NO SETUP FEE ON THIS SESSION. It used to be appended here as a
            # second line item so one payment covered both — Growth billed
            # $3,500 up front. The implementation fee is a separate obligation
            # with its own Stripe object, its own state and its own link; see
            # the `part` docstring above.
            session = stripe.checkout.Session.create(**kwargs)
        else:
            # THE ONE-TIME SETUP PAYMENT. `mode="payment"` — no subscription is
            # created, none is started, and nothing about the customer's plan
            # changes when this is paid.
            #
            # THE PRODUCT NAME IS WHAT THE CUSTOMER READS ON THE CHECKOUT PAGE
            # AND ON THEIR CARD STATEMENT, so it says what it is rather than
            # leaving them to work out why they are being charged.
            session = stripe.checkout.Session.create(
                customer=customer_id,
                mode="payment",
                line_items=[{
                    "price_data": {
                        "currency": currency,
                        "product_data": {
                            "name": "One-time setup & implementation fee",
                            "description":
                                "Covers your onboarding and build. This is a "
                                "single charge and is separate from your "
                                "monthly subscription."},
                        "unit_amount": setup["cents"]},
                    "quantity": 1,
                }],
                metadata=meta,
                # So the one-time payment carries the same trail as a
                # subscription's, which is how the webhook attributes it to the
                # deal without a second lookup.
                payment_intent_data={"metadata": meta},
                success_url="%s/billing?success=1&part=setup" % base_url,
                cancel_url="%s/billing?canceled=1&part=setup" % base_url,
            )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        # Stripe's message can name a price or a customer; it never carries our
        # secret key. Truncated and typed rather than dumped.
        log.exception("deal_billing: checkout session failed")
        raise HTTPException(
            status_code=502,
            detail="The payment processor did not accept this checkout: %s"
                   % str(exc)[:200])

    # ══ THE LINK IS SAVED SO NOBODY HAS TO GO AND FIND IT AGAIN ══
    #
    # A generated payment link that lives only in the HTTP response is a link
    # somebody recovers from browser history, or regenerates, or loses. It is
    # written to the implementation against the obligation it bills, so the
    # Opportunity screen can reopen or resend exactly the one it created.
    #
    # THE STATUS MOVES TO `checkout_pending`, NOT TO PAID. A session that exists
    # is not money that arrived. Only the webhook writes `paid`.
    impl = deal_billing.implementation_for(db, opp)
    if impl is not None:
        now = datetime.utcnow()
        if part == "setup":
            impl.setup_checkout_session_id = session.id
            impl.setup_checkout_url = session.url
            # A setup fee already collected must not be walked backwards by
            # somebody regenerating the link.
            if impl.setup_payment_status != "paid":
                impl.setup_payment_status = "checkout_pending"
        else:
            impl.subscription_checkout_session_id = session.id
            impl.subscription_checkout_url = session.url
            impl.subscription_checkout_at = now
        db.commit()

    try:
        br._audit(db, org, user, "billing.deal_checkout_started",
                  {"opportunity_id": opp.id,
                   "part": part,
                   "stripe_session_id": session.id,
                   "proposal_id": terms.get("proposal_id"),
                   "setup_cents": (setup or {}).get("cents") if part == "setup" else None,
                   "plan": (sub or {}).get("plan") if part == "subscription" else None,
                   # A custom deal has no plan key, so without these the audit
                   # row for the largest sales in the system would be the one
                   # that says the least about what was charged.
                   "pricing_mode": (sub or {}).get("pricing_mode"),
                   "recurring_cents": (sub or {}).get("cents"),
                   "pricing_authority": (sub or {}).get("authority"),
                   "pricing_approval_id": (sub or {}).get("approval_id")})
    except Exception:
        log.exception("deal_billing: audit write failed")

    return {"checkout_url": session.url,
            "part": part,
            "stripe_session_id": session.id,
            # Only the charge this session actually bills, so a caller cannot
            # display "you are collecting $3,500" for a $2,500 setup link.
            "charge": setup if part == "setup" else sub,
            "customer_organization_id": org.id,
            "opportunity_id": opp.id}
