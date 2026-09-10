"""GOD MODE — CUSTOMER SAAS BILLING. The brand catalogue, the brand policy,
and the real money that has moved.

WHY THIS ROUTER EXISTS. `brand_billing_plans` and `brand_billing_configs`
replaced a module-global price dict and a second copy of the same prices in the
React bundle. Tables are only an improvement on a hardcoded dict if somebody
other than a developer can read and change them, and until this router there
was no way to do either without a database client. A price that requires an
engineer is a price that stops being maintained — which is how the two copies
drifted in the first place.

EVERY ROUTE IS god_admin, through the same `require_god` the pricing console
uses. Not super_admin: a subscription price is what every customer of a brand
pays, and the policy fields decide whether a failed payment costs somebody
access. Neither is delegable to a tenant admin.

THIS ROUTER READS AND WRITES CONFIGURATION. IT DOES NOT BILL.
It never calls Stripe, never creates a subscription, never computes a price for
a checkout. `billing_catalog` resolves price, `billing_policy` reads policy, and
`billing_router` is the only thing that talks to Stripe. A second place that
knew how to price a checkout would be a second answer to what a customer owes.

═══════════════════════════════════════════════════════════════════════════
SECRETS
═══════════════════════════════════════════════════════════════════════════
NOTHING HERE READS THE ENVIRONMENT. STRIPE_SECRET_KEY and
STRIPE_WEBHOOK_SECRET are never loaded, never returned, never logged and never
placed in an error message, and this module imports no helper that would.

Stripe OBJECT ids are a different thing and are returned in full: prod_,
price_, sub_, cus_, in_ and evt_ are public identifiers that appear in the
Stripe dashboard and on the customer's own receipt. God Mode showing them is
what makes "which Price is this brand actually selling" answerable. The write
path additionally REFUSES a value that looks like a credential (sk_, rk_,
whsec_) in a Stripe id column, so a mis-paste cannot put a key in a column this
API returns.

═══════════════════════════════════════════════════════════════════════════
AN UNSET POLICY IS NOT A ZERO, AND A MISSING NUMBER IS NOT A ZERO
═══════════════════════════════════════════════════════════════════════════
Two conventions from the modules this one serves are carried through every
response below.

`billing_policy` returns POLICY REQUIRED for a decision nobody has made, and
/brands and /brands/{id} report that list rather than presenting inaction as a
rule — "nothing happens when a payment fails" looks identical to "billing is
broken" unless somebody says which it is.

/revenue returns null with a `no_source` reason for a figure that has no
source, never 0. God Mode's revenue band renders <NoSource/> placeholders today
for exactly this reason; replacing an honest placeholder with a fabricated $0 —
"no payment has ever been recorded" rendered as "we collected nothing" — is
strictly worse than the placeholder it replaced.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.deps import get_db, require_god
from app.models.billing_models import (BillingEvent, BillingInterval,
                                       BillingInvoice, BillingPayment,
                                       BrandBillingConfig, BrandBillingPlan,
                                       ChangeTiming, ProrationBehavior,
                                       SubscriptionStatus)
from app.models.models import Organization, Platform, User
from app.routers.audit_log_router import log_action
from app.services import billing_catalog, billing_policy

log = logging.getLogger(__name__)

router = APIRouter(prefix="/god/billing", tags=["god-billing"])

AUDIT_TARGET_PLAN = "brand_billing_plan"
AUDIT_TARGET_CONFIG = "brand_billing_config"

# The organization row that represents the platform itself rather than a paying
# customer. Excluded from every revenue figure for the same reason
# RevenueMetrics.billingFacts excludes it: counting ourselves as a customer
# makes every ratio on the screen wrong.
PLATFORM_OWN_ORG_ID = "org-god-platform"

# A value in one of the three Stripe ID columns that starts with any of these is
# a credential, not an object id. Refused on write so it can never be stored in
# a column this API returns. The offending value is never echoed back.
CREDENTIAL_PREFIXES = ("sk_", "rk_", "whsec_")

# The policy questions `billing_policy.describe` reports on. Kept in the same
# words describe() uses so "decided" and "policy_required" cannot disagree.
ALL_POLICY_QUESTIONS = (
    "upgrade timing/proration",
    "downgrade timing/proration",
    "failed-payment consequence",
    "subscription cancellation timing",
    "refund/chargeback clawback",
)


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _audit(db: Session, actor: User, action: str, target_type: str,
           target_id: str, before: Optional[dict], after: Optional[dict],
           note: Optional[str] = None, platform_id: Optional[str] = None) -> None:
    """Who changed a price or a policy, from what, to what.

    Same shape as god_pricing_router._audit, including `organization_id` being
    the ACTOR's — NULL for a god_admin. A brand's billing catalogue belongs to a
    brand, not to a customer tenant, and attributing it to one would file the
    change under somebody else's history.
    """
    log_action(db, getattr(actor, "organization_id", None), actor.id,
               action=action, target_type=target_type, target_id=target_id,
               before=before, after=after, note=note,
               platform_id=platform_id, commit=False)


def _fields_set(body: BaseModel) -> Dict[str, Any]:
    """Only what the caller actually sent.

    Load-bearing, not a convenience. An absent field must leave the stored value
    alone; if omission meant NULL then saving a plan's name would blank its
    Stripe price mapping, and setting a trial length would silently un-decide a
    cancellation policy somebody had chosen.
    """
    if hasattr(body, "model_dump"):
        return body.model_dump(exclude_unset=True)
    return body.dict(exclude_unset=True)


def _require_platform(db: Session, platform_id: str) -> Platform:
    p = db.query(Platform).filter(Platform.id == platform_id).first()
    if p is None:
        raise HTTPException(status_code=404, detail="Brand not found.")
    return p


def _reject_credential(value: Optional[str], field: str) -> Optional[str]:
    """A secret key pasted into a Stripe id column is refused here.

    The error names the FIELD and never the value. Echoing back "sk_live_..."
    to say it was rejected would write the key into an HTTP response and,
    through the audit trail, into the database — which is precisely the outcome
    the check exists to prevent.
    """
    if value is None:
        return None
    v = str(value).strip()
    if not v:
        return None
    if v.startswith(CREDENTIAL_PREFIXES):
        raise HTTPException(
            status_code=400,
            detail="%s looks like a Stripe API key or webhook secret, not an "
                   "object id. Secrets belong in the environment and are never "
                   "stored. Expected a public id such as prod_… or price_…."
                   % field)
    return v


def _plan_full(p: BrandBillingPlan) -> Dict[str, Any]:
    """EVERYTHING about a plan, Stripe object ids included.

    Deliberately not `billing_router._plan_public`, which withholds the Stripe
    mapping from customers. This is the screen where somebody answers "is this
    brand actually selling the Price I created in Stripe, or is checkout still
    minting anonymous ones", and that question cannot be answered without the
    ids.
    """
    return {
        "id": p.id,
        "platform_id": p.platform_id,
        "key": p.key,
        "name": p.name,
        "description": p.description,
        "sort_order": p.sort_order,
        # `monthly_cents` is the COMMITTED rate; `month_to_month_cents` is the
        # same tier without a commitment. Both are shown because a screen that
        # showed one would make the other invisible to the person configuring it.
        "monthly_cents": p.monthly_cents,
        "annual_cents": p.annual_cents,
        "month_to_month_cents": p.month_to_month_cents,
        "currency": p.currency,
        "stripe_product_id": p.stripe_product_id,
        "stripe_price_id_monthly": p.stripe_price_id_monthly,
        "stripe_price_id_annual": p.stripe_price_id_annual,
        "stripe_price_id_month_to_month": p.stripe_price_id_month_to_month,
        # Advertised ceilings. NULL is UNLIMITED, not zero — the screen must be
        # able to tell them apart, so neither is coerced.
        "max_leads": p.max_leads,
        "max_users": p.max_users,
        "features": billing_catalog.features_for(p),
        "is_purchasable": bool(p.is_purchasable),
        "is_active": bool(p.is_active),
        # A plan Stripe knows nothing about still checks out — through the
        # inline price_data fallback, which mints an anonymous Price per
        # checkout. Saying so here is how that gets noticed and fixed.
        "stripe_mapped": bool(p.stripe_price_id_monthly or p.stripe_price_id_annual),
        "created_at": p.created_at,
        "updated_at": p.updated_at,
    }


def _config_out(cfg: Optional[BrandBillingConfig]) -> Optional[Dict[str, Any]]:
    """The raw stored policy row. NULL means UNDECIDED and is sent as null.

    `billing_policy.describe` is what says what those NULLs MEAN; this is the
    editable state behind it, so the screen writing the row and the screen
    reading the behaviour are looking at the same values.
    """
    if cfg is None:
        return None
    return {
        "id": cfg.id,
        "platform_id": cfg.platform_id,
        "upgrade_timing": cfg.upgrade_timing,
        "upgrade_proration": cfg.upgrade_proration,
        "downgrade_timing": cfg.downgrade_timing,
        "downgrade_proration": cfg.downgrade_proration,
        "past_due_grace_days": cfg.past_due_grace_days,
        "suspend_on_past_due": cfg.suspend_on_past_due,
        "cancel_timing": cfg.cancel_timing,
        "trial_days": cfg.trial_days,
        "clawback_policy": cfg.clawback_policy,
        "notes": cfg.notes,
        "created_at": cfg.created_at,
        "updated_at": cfg.updated_at,
    }


def _monthly_equivalent_cents(plan: BrandBillingPlan,
                              interval: Optional[str],
                              commitment: Optional[str] = None) -> Optional[int]:
    """One month of this plan at this interval and commitment, or None.

    Annual is divided by twelve, the same rule `billing_catalog.classify_change`
    compares tiers with, so an annual customer contributes one month of revenue
    to MRR rather than twelve.

    THE COMMITMENT IS NOT OPTIONAL DETAIL — it is half the price. Starter is
    $500/mo committed and $597/mo month-to-month, and both are the MONTH
    interval, so passing interval alone made `price_cents_for` fall through to
    its default and report every month-to-month customer at the discounted term
    rate. Found on the first live subscription: Stripe charged $597, MRR said
    $500.

    A NULL commitment still means the term rate, and deliberately so. That is
    the behaviour every caller had before this argument existed, it is what
    `price_cents_for` documents as its default, and the alternative — treating
    unknown as month-to-month — would inflate MRR on old rows instead. An
    UNDERSTATED number that matches the previous behaviour is the safe side of
    an unknown; the fix for the unknown is the webhook now recording it.
    """
    cents = billing_catalog.price_cents_for(
        plan, interval or BillingInterval.MONTH, commitment)
    if cents is None:
        return None
    if (interval or BillingInterval.MONTH) == BillingInterval.YEAR:
        return cents // 12
    return cents


# ═══════════════════════════════════════════════════════════════════════════
# WHICH BRANDS ARE ACTUALLY CONFIGURED
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/brands")
def list_brands(db: Session = Depends(get_db),
                user: User = Depends(require_god)):
    """Every brand, and whether it can bill anybody yet.

    THE UNCONFIGURED LIST IS THE POINT OF THIS SCREEN, the same way the
    unconfigured-package list is the point of the pricing console. A brand with
    no plans offers its customers nothing and its Billing page is empty; a brand
    with no config row has every plan-change policy undecided, so change-plan
    refuses with a 409 the customer cannot act on. Both look like an outage from
    the customer's side and neither is one.
    """
    platforms = db.query(Platform).order_by(Platform.name.asc()).all()

    # Two counts in one pass rather than a query per brand: how many plans, and
    # how many a customer could actually buy today.
    totals: Dict[str, int] = {}
    active: Dict[str, int] = {}
    purchasable: Dict[str, int] = {}
    for pid, n in (db.query(BrandBillingPlan.platform_id,
                            func.count(BrandBillingPlan.id))
                   .group_by(BrandBillingPlan.platform_id).all()):
        totals[pid] = int(n)
    for pid, n in (db.query(BrandBillingPlan.platform_id,
                            func.count(BrandBillingPlan.id))
                   .filter(BrandBillingPlan.is_active.is_(True))
                   .group_by(BrandBillingPlan.platform_id).all()):
        active[pid] = int(n)
    for pid, n in (db.query(BrandBillingPlan.platform_id,
                            func.count(BrandBillingPlan.id))
                   .filter(BrandBillingPlan.is_active.is_(True),
                           BrandBillingPlan.is_purchasable.is_(True))
                   .group_by(BrandBillingPlan.platform_id).all()):
        purchasable[pid] = int(n)

    config_ids = {c.platform_id for c in db.query(BrandBillingConfig).all()}

    brands = []
    for p in platforms:
        policy = billing_policy.describe(db, p.id)
        brands.append({
            "platform_id": p.id,
            "name": p.name,
            "slug": p.slug,
            "is_active": bool(p.is_active),
            "plan_count": totals.get(p.id, 0),
            "active_plan_count": active.get(p.id, 0),
            "purchasable_plan_count": purchasable.get(p.id, 0),
            "has_config_row": p.id in config_ids,
            # Verbatim from billing_policy. Not recomputed here — a second
            # opinion about which policies are open is a second answer.
            "policy_required": policy["policy_required"],
            "trial_days": policy["trial_days"],
            # "Can this brand sell anything at all today."
            "catalogue_configured": purchasable.get(p.id, 0) > 0,
        })

    return {
        "brands": brands,
        "explanation": (
            "A brand with no purchasable plan cannot sell a subscription and "
            "its customers' Billing screen is empty. Anything in "
            "policy_required is UNDECIDED: the billing engine takes no action "
            "on it, and a plan change in that direction is refused rather than "
            "applied on a guessed schedule."
        ),
    }


@router.get("/brands/{platform_id}")
def brand_detail(platform_id: str, db: Session = Depends(get_db),
                 user: User = Depends(require_god)):
    """One brand's whole billing configuration — catalogue and policy.

    Plans come back in FULL, Stripe object ids included. This is God Mode; the
    customer-facing view is `billing_router._plan_public`, which withholds them.
    """
    platform = _require_platform(db, platform_id)
    plans = billing_catalog.plans_for(db, platform_id, include_inactive=True)
    cfg = billing_policy.config_for(db, platform_id)

    return {
        "brand": {"platform_id": platform.id, "name": platform.name,
                  "slug": platform.slug, "is_active": bool(platform.is_active)},
        "plans": [_plan_full(p) for p in plans],
        "config": _config_out(cfg),
        "policy": billing_policy.describe(db, platform_id),
        # The count of customers this catalogue governs. Changing a price here
        # changes what these organizations are quoted next.
        "organization_count": (db.query(Organization)
                               .filter(Organization.platform_id == platform_id,
                                       Organization.id != PLATFORM_OWN_ORG_ID)
                               .count()),
    }


# ═══════════════════════════════════════════════════════════════════════════
# THE CATALOGUE — one plan at a time
# ═══════════════════════════════════════════════════════════════════════════

class PlanIn(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    sort_order: Optional[int] = None
    # Cents, integer, never float — a float dollar amount is a rounding error
    # waiting to be somebody's invoice. NULL is "no price at this interval",
    # which is how a quoted Enterprise tier is expressed, not zero.
    monthly_cents: Optional[int] = None
    annual_cents: Optional[int] = None
    # The same tier's no-commitment monthly rate, and its own Stripe Price.
    # Configuration, not a second product — see BillingCommitment.
    month_to_month_cents: Optional[int] = None
    max_leads: Optional[int] = None
    max_users: Optional[int] = None
    features: Optional[List[str]] = None
    is_purchasable: Optional[bool] = None
    is_active: Optional[bool] = None
    stripe_product_id: Optional[str] = None
    stripe_price_id_monthly: Optional[str] = None
    stripe_price_id_annual: Optional[str] = None
    stripe_price_id_month_to_month: Optional[str] = None


def _validate_cents(value: Optional[int], field: str) -> Optional[int]:
    if value is None:
        return None
    try:
        cents = int(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400,
                            detail="%s must be a whole number of cents." % field)
    if cents < 0:
        raise HTTPException(status_code=400,
                            detail="%s cannot be negative." % field)
    return cents


def _validate_ceiling(value: Optional[int], field: str) -> Optional[int]:
    if value is None:
        return None
    if int(value) < 0:
        raise HTTPException(status_code=400,
                            detail="%s cannot be negative. Leave it empty for "
                                   "unlimited." % field)
    return int(value)


@router.put("/brands/{platform_id}/plans/{key}")
def upsert_plan(platform_id: str, key: str, body: PlanIn,
                db: Session = Depends(get_db),
                user: User = Depends(require_god)):
    """Create or update ONE plan in ONE brand's catalogue.

    UPSERT, keyed on (platform_id, key) — the table's own unique constraint. A
    create-only route would fail the second save with a database error the
    screen could not explain, and a separate update route would mean the screen
    had to know which of the two a given key needed.

    ONLY THE FIELDS SENT ARE WRITTEN. That is what makes it safe to save a name
    change from a screen that does not render the Stripe mapping: an omitted
    `stripe_price_id_monthly` leaves the mapping alone rather than blanking it
    and sending checkout back to minting anonymous Prices.

    THIS PRICE IS WHAT CUSTOMERS ARE CHARGED. There is no second copy in the
    browser and no default anywhere: `billing_catalog` reads this row, and
    checkout sends only a plan key. So the number written here is the number on
    the card statement, which is why the change is audited with its before and
    after.
    """
    _require_platform(db, platform_id)

    plan_key = (key or "").strip().lower()
    if not plan_key:
        raise HTTPException(status_code=400, detail="A plan needs a key.")

    data = _fields_set(body)
    plan = (db.query(BrandBillingPlan)
            .filter(BrandBillingPlan.platform_id == platform_id,
                    BrandBillingPlan.key == plan_key).first())
    creating = plan is None
    before = None if creating else _plan_full(plan)

    if creating:
        name = (data.get("name") or "").strip()
        if not name:
            # No invented display name. "Starter" derived from the key would be
            # this file's guess at a brand's own product naming, and it is what
            # a customer sees on their receipt.
            raise HTTPException(
                status_code=400,
                detail="A new plan needs a name — it is what the customer sees "
                       "on the plan card and on their receipt.")
        plan = BrandBillingPlan(platform_id=platform_id, key=plan_key,
                                name=name, currency="usd")
        db.add(plan)

    if "name" in data:
        name = (data["name"] or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="A plan needs a name.")
        plan.name = name
    if "description" in data:
        plan.description = (data["description"] or None)
    if "sort_order" in data:
        plan.sort_order = int(data["sort_order"] or 0)
    if "monthly_cents" in data:
        plan.monthly_cents = _validate_cents(data["monthly_cents"], "monthly_cents")
    if "annual_cents" in data:
        plan.annual_cents = _validate_cents(data["annual_cents"], "annual_cents")
    if "month_to_month_cents" in data:
        plan.month_to_month_cents = _validate_cents(
            data["month_to_month_cents"], "month_to_month_cents")
    if "max_leads" in data:
        plan.max_leads = _validate_ceiling(data["max_leads"], "max_leads")
    if "max_users" in data:
        plan.max_users = _validate_ceiling(data["max_users"], "max_users")
    if "features" in data:
        features = data["features"]
        if features is None:
            plan.features_json = None
        elif isinstance(features, list):
            plan.features_json = json.dumps([str(f) for f in features])
        else:
            raise HTTPException(status_code=400,
                                detail="features must be a list of strings.")
    if "is_purchasable" in data:
        plan.is_purchasable = bool(data["is_purchasable"])
    if "is_active" in data:
        plan.is_active = bool(data["is_active"])
    for field in ("stripe_product_id", "stripe_price_id_monthly",
                  "stripe_price_id_annual", "stripe_price_id_month_to_month"):
        if field in data:
            setattr(plan, field, _reject_credential(data[field], field))

    db.flush()
    after = _plan_full(plan)
    _audit(db, user,
           "billing_plan.created" if creating else "billing_plan.updated",
           AUDIT_TARGET_PLAN, plan.id, before=before, after=after,
           note="brand=%s plan=%s" % (platform_id, plan_key),
           platform_id=platform_id)
    db.commit()
    return {"created": creating, "plan": after}


# ═══════════════════════════════════════════════════════════════════════════
# THE POLICY — what happens when money goes wrong, or a plan changes
# ═══════════════════════════════════════════════════════════════════════════

class ConfigIn(BaseModel):
    """Every field optional, and OMITTED IS NOT NULL.

    A field the caller did not send is left exactly as it was. A field sent as
    `null` is deliberately un-decided again, and both are audited. That
    distinction is the whole reason this is not a plain replace: the four
    POLICY REQUIRED columns mean "nobody has decided", so a partial save that
    quietly blanked them would un-make a decision by not mentioning it.
    """
    upgrade_timing: Optional[str] = None
    upgrade_proration: Optional[str] = None
    downgrade_timing: Optional[str] = None
    downgrade_proration: Optional[str] = None
    past_due_grace_days: Optional[int] = None
    suspend_on_past_due: Optional[bool] = None
    cancel_timing: Optional[str] = None
    trial_days: Optional[int] = None
    clawback_policy: Optional[str] = None
    notes: Optional[str] = None


def _validate_timing(value: Optional[str], field: str) -> Optional[str]:
    if value in (None, ""):
        return None
    if value not in ChangeTiming.ALL:
        raise HTTPException(status_code=400,
                            detail="%s must be one of: %s"
                                   % (field, ", ".join(ChangeTiming.ALL)))
    return value


def _validate_proration(value: Optional[str], field: str) -> Optional[str]:
    if value in (None, ""):
        return None
    if value not in ProrationBehavior.ALL:
        raise HTTPException(status_code=400,
                            detail="%s must be one of: %s"
                                   % (field, ", ".join(ProrationBehavior.ALL)))
    return value


def _validate_days(value: Optional[int], field: str) -> Optional[int]:
    if value is None:
        return None
    days = int(value)
    if days < 0:
        raise HTTPException(status_code=400,
                            detail="%s cannot be negative. Leave it empty to "
                                   "leave the policy undecided." % field)
    return days


@router.put("/brands/{platform_id}/config")
def set_config(platform_id: str, body: ConfigIn,
               db: Session = Depends(get_db),
               user: User = Depends(require_god)):
    """Decide — or deliberately un-decide — this brand's billing policy.

    ═══════════════════════════════════════════════════════════════════════
    SETTING A POLICY REQUIRED FIELD IS AN EXPLICIT ACT, AND ONLY EVER THAT.
    ═══════════════════════════════════════════════════════════════════════
    `past_due_grace_days`, `suspend_on_past_due`, `cancel_timing`, `trial_days`
    and `clawback_policy` ship UNSET on purpose. Unset means the engine does
    nothing: no customer is suspended, no subscription is cancelled early, no
    trial is given away and no paid compensation is touched. Those are today's
    behaviours, preserved exactly.

    So nothing in this endpoint fills one in. There is no default, no partial
    write, and no inference from a neighbouring field — a value only lands in
    one of those columns because a god_admin named that column in this request.
    Turning on suspension is somebody deciding to cut off paying customers on a
    schedule, and it must not be possible to do it by saving a form that
    happened to include the field.

    The response echoes back which policies are now DECIDED and which remain
    OPEN, straight from `billing_policy.describe`, so the person who just saved
    can see what they did and did not turn on.
    """
    _require_platform(db, platform_id)
    data = _fields_set(body)

    cfg = billing_policy.config_for(db, platform_id)
    creating = cfg is None
    before = _config_out(cfg)
    before_policy = billing_policy.describe(db, platform_id)

    if creating:
        cfg = BrandBillingConfig(platform_id=platform_id)
        db.add(cfg)

    if "upgrade_timing" in data:
        cfg.upgrade_timing = _validate_timing(data["upgrade_timing"], "upgrade_timing")
    if "downgrade_timing" in data:
        cfg.downgrade_timing = _validate_timing(data["downgrade_timing"], "downgrade_timing")
    if "cancel_timing" in data:
        cfg.cancel_timing = _validate_timing(data["cancel_timing"], "cancel_timing")
    if "upgrade_proration" in data:
        cfg.upgrade_proration = _validate_proration(data["upgrade_proration"],
                                                    "upgrade_proration")
    if "downgrade_proration" in data:
        cfg.downgrade_proration = _validate_proration(data["downgrade_proration"],
                                                      "downgrade_proration")
    if "past_due_grace_days" in data:
        cfg.past_due_grace_days = _validate_days(data["past_due_grace_days"],
                                                 "past_due_grace_days")
    if "trial_days" in data:
        cfg.trial_days = _validate_days(data["trial_days"], "trial_days")
    if "suspend_on_past_due" in data:
        v = data["suspend_on_past_due"]
        # None is meaningful and is NOT False. False is "this brand decided not
        # to suspend"; None is "nobody has decided", and billing_policy reports
        # them differently on purpose.
        cfg.suspend_on_past_due = None if v is None else bool(v)
    if "clawback_policy" in data:
        cfg.clawback_policy = (data["clawback_policy"] or None)
    if "notes" in data:
        cfg.notes = (data["notes"] or None)

    db.flush()
    after = _config_out(cfg)
    after_policy = billing_policy.describe(db, platform_id)

    open_before = set(before_policy["policy_required"])
    open_after = set(after_policy["policy_required"])
    newly_decided = sorted(open_before - open_after)
    newly_reopened = sorted(open_after - open_before)

    _audit(db, user,
           "billing_config.created" if creating else "billing_config.updated",
           AUDIT_TARGET_CONFIG, cfg.id, before=before, after=after,
           note="brand=%s decided=%s reopened=%s"
                % (platform_id, newly_decided or "none", newly_reopened or "none"),
           platform_id=platform_id)
    db.commit()

    return {
        "created": creating,
        "config": after,
        "policy": after_policy,
        # What this save actually settled, and what it did not.
        "decided": [q for q in ALL_POLICY_QUESTIONS if q not in open_after],
        "policy_required": after_policy["policy_required"],
        "newly_decided": newly_decided,
        "newly_reopened": newly_reopened,
        "trial": {
            "days": after_policy["trial_days"],
            "offered": after_policy["trial_days"] is not None,
        },
        "explanation": (
            "Anything still listed in policy_required is UNDECIDED and the "
            "billing engine takes no action on it — nothing suspends a "
            "customer, cancels early, or adjusts paid compensation. Only the "
            "fields named in this request were written; every other value was "
            "left exactly as it was."
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════
# SEEDING THE DECIDED EVOSYS BILLING CONFIGURATION
#
# A PREVIEW AND A COMMIT, NOT A SCRIPT — the same shape as
# POST /god/pricing/seed/evosys, for the same reason. Writing prices into a live
# database from a migration or a shell means nobody sees what changed until a
# customer's card is charged the wrong amount. This shows exactly what it will
# write, and writes only when a god_admin says so.
#
# Idempotent, and it never writes a Stripe id or a POLICY REQUIRED field. See
# evosys_billing_seed for what is deliberately left out and why.
# ═══════════════════════════════════════════════════════════════════════════

class SeedIn(BaseModel):
    # Explicit, and false by default. The preview is the safe call; writing what
    # every customer of a brand will be charged should require saying so.
    apply: bool = False


class EntitlementSnapshotIn(BaseModel):
    """The ceilings a Custom customer's agreement actually granted.

    EVERY FIELD OPTIONAL, AND OMITTED IS NOT ZERO. A dimension nobody agreed is
    left unrecorded and reported as NEEDS CONFIGURATION — it does not become
    unlimited and it does not become a standard tier's number.

    To record a genuinely uncapped dimension, name it in `unlimited` rather than
    leaving it blank. "Nobody decided" and "we agreed no cap" are different
    commercial facts and this is the only place they can be told apart.
    """
    max_leads: Optional[int] = None
    max_users: Optional[int] = None
    max_locations: Optional[int] = None
    sms_monthly_allowance: Optional[int] = None
    voice_minutes_monthly_allowance: Optional[int] = None
    email_monthly_allowance: Optional[int] = None
    unlimited: Optional[List[str]] = None
    features: Optional[List[str]] = None
    opportunity_id: Optional[str] = None
    note: Optional[str] = None


@router.get("/customers/{organization_id}/entitlements")
def read_customer_entitlements(organization_id: str,
                               db: Session = Depends(get_db),
                               user: User = Depends(require_god)) -> dict:
    """What this customer is entitled to, and how confidently we know it."""
    from app.services import plan_limits

    org = db.query(Organization).filter(Organization.id == organization_id).first()
    if org is None:
        raise HTTPException(status_code=404, detail="Customer not found.")
    return {
        "organization_id": org.id,
        "organization_name": org.name,
        "billing_plan_key": getattr(org, "billing_plan_key", None),
        "entitlement": plan_limits.entitlement_state(db, org),
        "recordable_dimensions": list(plan_limits.SNAPSHOT_DIMENSIONS),
    }


@router.put("/customers/{organization_id}/entitlements")
def set_customer_entitlements(organization_id: str, body: EntitlementSnapshotIn,
                              db: Session = Depends(get_db),
                              user: User = Depends(require_god)) -> dict:
    """Record what a Custom customer's agreement granted.

    SUPERSEDES, NEVER OVERWRITES. A renegotiation writes a new snapshot and
    marks the previous one superseded, so what the customer was entitled to last
    quarter stays answerable. That is the same reason a proposal is versioned
    rather than edited.

    A SNAPSHOT OF LITERAL NUMBERS, not a pointer at the catalogue — so a brand
    later raising Starter's lead ceiling cannot retroactively change what this
    customer was sold.
    """
    from datetime import datetime as _dt

    from app.models.billing_models import CustomerEntitlementSnapshot
    from app.services import entitlements as _ent
    from app.services import plan_limits

    org = db.query(Organization).filter(Organization.id == organization_id).first()
    if org is None:
        raise HTTPException(status_code=404, detail="Customer not found.")

    data = _fields_set(body)

    unlimited = data.get("unlimited")
    if unlimited is not None:
        bad = [k for k in unlimited if k not in plan_limits.SNAPSHOT_DIMENSIONS]
        if bad:
            raise HTTPException(
                status_code=400,
                detail="Not recordable dimension(s): %s. Valid: %s"
                       % (", ".join(sorted(set(bad))),
                          ", ".join(plan_limits.SNAPSHOT_DIMENSIONS)))

    features = data.get("features")
    if features is not None:
        # Validated against the ONE feature registry, so a typo cannot be
        # recorded as an entitlement that grants nothing and is never noticed.
        features = _ent.normalize_keys(features)

    for field in plan_limits.SNAPSHOT_DIMENSIONS:
        if field in data and data[field] is not None and int(data[field]) < 0:
            raise HTTPException(status_code=400,
                                detail="%s cannot be negative." % field)

    before = plan_limits.entitlement_state(db, org)

    previous = plan_limits.current_snapshot(db, org)
    if previous is not None:
        previous.superseded_at = _dt.utcnow()

    snap = CustomerEntitlementSnapshot(
        organization_id=org.id,
        platform_id=getattr(org, "platform_id", None),
        opportunity_id=data.get("opportunity_id"),
        source="custom_deal",
        features_json=json.dumps(features) if features is not None else None,
        unlimited_json=json.dumps(sorted(set(unlimited))) if unlimited else None,
        note=(data.get("note") or None),
        created_by=user.id,
    )
    for field in plan_limits.SNAPSHOT_DIMENSIONS:
        if field in data:
            setattr(snap, field, data[field])
    db.add(snap)
    db.flush()

    after = plan_limits.entitlement_state(db, org)
    _audit(db, user, "customer.entitlements_recorded", "organization", org.id,
           before=before, after=after,
           note="entitlement snapshot for %s" % org.name,
           platform_id=getattr(org, "platform_id", None))
    db.commit()
    return {"organization_id": org.id, "snapshot_id": snap.id,
            "superseded_snapshot_id": getattr(previous, "id", None),
            "entitlement": after}


@router.post("/brands/{platform_id}/seed")
def seed_brand(platform_id: str, body: SeedIn = SeedIn(),
               db: Session = Depends(get_db),
               user: User = Depends(require_god)):
    """Preview (default) or apply the decided EvoSys billing configuration."""
    from app.services import evosys_billing_seed

    _require_platform(db, platform_id)

    if not body.apply:
        return evosys_billing_seed.seed(db, platform_id, apply=False)

    # The audit row is written BEFORE the apply, from the preview of the very
    # same session, so the seed's own commit carries both. An audit committed
    # separately afterwards can be lost by a failure that has already changed
    # prices — the record of a price change must not be able to go missing while
    # the price change survives.
    planned = evosys_billing_seed.seed(db, platform_id, apply=False)
    _audit(db, user, "billing_catalogue.seeded", AUDIT_TARGET_PLAN,
           platform_id, before=None,
           after={"actions": planned.get("actions"),
                  "not_seeded": sorted(planned.get("not_seeded", {}).keys())},
           note="Decided EvoSys billing catalogue and plan-change policy "
                "seeded for brand %s" % platform_id,
           platform_id=platform_id)
    return evosys_billing_seed.seed(db, platform_id, apply=True)


# ═══════════════════════════════════════════════════════════════════════════
# STRIPE TEST PROVISIONING — the one place this router touches Stripe
# ═══════════════════════════════════════════════════════════════════════════
#
# THE BOUNDARY AT THE TOP OF THIS FILE STILL HOLDS. This router does not BILL:
# it does not price a checkout, create a subscription, or decide what a customer
# owes. What it does here is CREATE THE OBJECTS THE CATALOGUE ALREADY DESCRIBES
# and write their public ids back into the same configuration model every other
# route on this file reads. The amounts come from `BrandBillingPlan` and from
# nowhere else — there is no price in this endpoint, in the service behind it,
# or anywhere on the path.
#
# TEST MODE IS ENFORCED ON THE KEY. `stripe_provisioning._client()` refuses an
# `sk_live_`/`rk_live_` key before making a single call, so this cannot put a
# purchasable Price in front of a real customer even if pointed at live
# credentials by mistake.

class ProvisionIn(BaseModel):
    # Preview by default, for the same reason `seed_brand` previews by default:
    # the destructive-looking operation should require somebody to say so.
    apply: bool = False


@router.post("/brands/{platform_id}/stripe/provision")
def provision_stripe(platform_id: str, body: ProvisionIn = ProvisionIn(),
                     db: Session = Depends(get_db),
                     user: User = Depends(require_god)):
    """Create or reuse this brand's TEST Stripe Products and Prices.

    Idempotent: a second call creates nothing. Reuse is attempted from the
    mapping column first, then from the Product's own active Prices, before
    anything is created — so this is safe to run after a database restore, and
    safe when somebody has already made the Price by hand.
    """
    from app.services import stripe_provisioning

    platform = _require_platform(db, platform_id)

    try:
        if not body.apply:
            return stripe_provisioning.provision_brand(db, platform, dry_run=True)

        # The preview runs first and is what the audit records, so the row says
        # what was INTENDED in the same transaction that performs it — the same
        # ordering `seed_brand` uses, and for the same reason.
        planned = stripe_provisioning.provision_brand(db, platform, dry_run=True)
        result = stripe_provisioning.provision_brand(db, platform, dry_run=False)
    except stripe_provisioning.ProvisioningRefused as exc:
        # 409, not 500: the request was understood and refused on a
        # precondition the caller can fix (wrong key mode, key absent).
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:                                # pragma: no cover
        log.exception("stripe provisioning failed for %s", platform_id)
        # The message is Stripe's and carries no credential — the key is never
        # part of an error body — but it is still not echoed verbatim.
        raise HTTPException(
            status_code=502,
            detail="Stripe refused the provisioning request. Nothing was "
                   "written. See the service log for the processor's reply.")

    _audit(db, user, "billing_catalogue.stripe_provisioned", AUDIT_TARGET_PLAN,
           platform_id, before={"planned": planned.get("summary")},
           after={"summary": result.get("summary"),
                  # PUBLIC object ids only. prod_… and price_… appear on the
                  # customer's own receipt; no key or secret reaches this row.
                  "mapped": {p["plan_key"]: {
                      k: v.get("price_id")
                      for k, v in (p.get("commitments") or {}).items()}
                      for p in result.get("plans", []) if "commitments" in p}},
           note="TEST-mode Stripe Products/Prices provisioned for brand %s"
                % platform_id,
           platform_id=platform_id)
    db.commit()
    return result


# ═══════════════════════════════════════════════════════════════════════════
# REVENUE — real numbers, or an honest null
# ═══════════════════════════════════════════════════════════════════════════

def _no_source(reason: str, **extra) -> Dict[str, Any]:
    """A figure that has no source says so, and does NOT say zero.

    God Mode's revenue band has rendered <NoSource/> placeholders for MRR,
    Collected-30d and Past-Due since there was nothing to read. Now that the
    tables exist, the placeholder is replaced by a real number where one exists
    and by THIS where one does not. A fabricated 0 would be a downgrade on the
    placeholder: "no payment has ever been recorded" and "we collected nothing
    this month" are different facts, and only one of them means something is
    broken.
    """
    out = {"value_cents": None, "no_source": reason}
    out.update(extra)
    return out


@router.get("/revenue")
def revenue(platform_id: Optional[str] = Query(None),
            db: Session = Depends(get_db),
            user: User = Depends(require_god)):
    """MRR, collected in the last 30 days, and the state of every subscription.

    READ FROM THE LOCAL MIRROR, never from Stripe. `billing_invoices` and
    `billing_payments` are written by the webhook; calling Stripe from here
    would make this screen exactly as fast and as available as Stripe's API on
    its worst day, for figures we already hold.

    `platform_id` segments by brand. Without it the figures cover every brand,
    which is the platform owner's own P&L view.
    """
    scope_name = None
    if platform_id:
        scope_name = _require_platform(db, platform_id).name

    now = datetime.utcnow()
    cutoff = now - timedelta(days=30)

    orgs_q = db.query(Organization).filter(Organization.id != PLATFORM_OWN_ORG_ID)
    if platform_id:
        orgs_q = orgs_q.filter(Organization.platform_id == platform_id)
    orgs = orgs_q.all()

    def _status(o) -> str:
        return (getattr(o, "billing_status", None) or "").lower()

    active_orgs = [o for o in orgs if _status(o) == SubscriptionStatus.ACTIVE]
    trialing = [o for o in orgs if _status(o) == SubscriptionStatus.TRIALING]
    past_due = [o for o in orgs if _status(o) == SubscriptionStatus.PAST_DUE]
    with_subscription = [o for o in orgs
                         if getattr(o, "stripe_subscription_id", None)]

    # ── MRR ───────────────────────────────────────────────────────────────
    #
    # Every ACTIVE organization's current plan, priced from ITS OWN BRAND's
    # catalogue and reduced to one month. Trialing organizations are excluded:
    # a trial is not revenue until it converts, and counting it is how a
    # forecast becomes a fiction.
    plan_cache: Dict[Any, Any] = {}
    mrr_cents = 0
    priced = 0
    unpriced: List[Dict[str, Any]] = []
    currencies = set()

    for o in active_orgs:
        key = getattr(o, "billing_plan_key", None) or o.plan
        cache_key = (o.platform_id, key)
        if cache_key not in plan_cache:
            plan_cache[cache_key] = billing_catalog.resolve_plan(
                db, o.platform_id, key)
        plan = plan_cache[cache_key]
        interval = getattr(o, "stripe_plan_interval", None) or BillingInterval.MONTH
        commitment = getattr(o, "billing_commitment", None)
        cents = _monthly_equivalent_cents(plan, interval, commitment) if plan else None
        if cents is None:
            # Named, not silently dropped. An active subscription whose plan key
            # resolves to nothing in its brand's catalogue is a real problem —
            # the customer is being charged something this platform cannot
            # explain — and burying it inside a total is how it stays unnoticed.
            #
            # A plan that exists but has no price AT THIS COMMITMENT lands here
            # too, and that is correct: `price_cents_for` refuses to fall back
            # between commitments, so a month-to-month customer on a plan with
            # no month-to-month price is named rather than quietly counted at
            # the term rate they never agreed to.
            unpriced.append({"organization_id": o.id, "name": o.name,
                             "plan_key": key, "interval": interval,
                             "commitment": commitment,
                             "reason": ("no such plan in this brand's catalogue"
                                        if plan is None else
                                        "plan has no %s price configured at %s"
                                        % (interval, commitment or "the term rate"))})
            continue
        mrr_cents += cents
        priced += 1
        currencies.add((plan.currency or "usd").lower())

    if not with_subscription:
        # Nothing has a subscription at all. That is "no source", not "$0 of
        # revenue" — the two look identical on a tile and mean opposite things.
        mrr = _no_source(
            "No organization in this scope has a subscription recorded, so "
            "there is nothing to price. This is missing data, not zero revenue.",
            active_organizations=0, priced_organizations=0)
    elif active_orgs and priced == 0:
        mrr = _no_source(
            "No active organization's plan resolves to a price in its brand's "
            "catalogue, so MRR cannot be computed.",
            active_organizations=len(active_orgs), priced_organizations=0,
            unpriced_organizations=unpriced)
    else:
        mrr = {
            "value_cents": mrr_cents,
            "no_source": None,
            "currency": (sorted(currencies)[0] if len(currencies) == 1
                         else ("mixed" if currencies else "usd")),
            "active_organizations": len(active_orgs),
            "priced_organizations": priced,
            # Non-empty means the figure is understated by however much these
            # customers actually pay. It is reported next to the number rather
            # than in a log nobody reads.
            "unpriced_organizations": unpriced,
            "basis": ("Sum of each ACTIVE organization's current plan reduced "
                      "to one month; annual prices are divided by twelve. "
                      "Trialing organizations are excluded."),
        }

    # ── Collected in the last 30 days ─────────────────────────────────────
    #
    # Money that actually arrived, MINUS what was refunded from those same
    # payments. Reporting gross collections while a refund sits unmentioned
    # beside them is how a month looks better than the bank does.
    pay_q = db.query(BillingPayment)
    if platform_id:
        pay_q = pay_q.filter(BillingPayment.platform_id == platform_id)
    payments_ever = pay_q.count()

    if payments_ever == 0:
        collected = _no_source(
            "No payment has ever been recorded in this scope. The webhook "
            "writes billing_payments on invoice.paid; until one arrives there "
            "is nothing to sum, which is not the same as having collected "
            "nothing.")
    else:
        window = pay_q.filter(BillingPayment.collected_at >= cutoff)
        gross = window.with_entities(
            func.coalesce(func.sum(BillingPayment.amount_cents), 0)).scalar() or 0
        refunded = window.with_entities(
            func.coalesce(func.sum(BillingPayment.refunded_cents), 0)).scalar() or 0
        collected = {
            "value_cents": int(gross) - int(refunded),
            "no_source": None,
            "gross_cents": int(gross),
            "refunded_cents": int(refunded),
            "payments_counted": window.count(),
            "window_days": 30,
            "since": cutoff,
            "basis": ("Sum of billing_payments.amount_cents collected in the "
                      "last 30 days, less refunded_cents on those same "
                      "payments."),
        }

    inv_q = db.query(BillingInvoice)
    if platform_id:
        inv_q = inv_q.filter(BillingInvoice.platform_id == platform_id)

    return {
        "scope": {"platform_id": platform_id, "brand_name": scope_name,
                  "all_brands": platform_id is None},
        "generated_at": now,
        "mrr": mrr,
        "collected_30d": collected,
        # Counts of organizations by the status Stripe last told us about. These
        # are real rows either way, so a zero here is a zero and not a gap —
        # which is why they are plain integers while the money figures are not.
        "active_count": len(active_orgs),
        "trialing_count": len(trialing),
        "past_due_count": len(past_due),
        "organizations_in_scope": len(orgs),
        "organizations_with_subscription": len(with_subscription),
        "invoices_recorded": inv_q.count(),
        "counts_basis": ("organizations.billing_status, mirrored from Stripe by "
                         "the webhook. The platform's own organization is "
                         "excluded from every figure."),
        "explanation": (
            "Any figure returning null carries a no_source reason and must be "
            "rendered as 'no source', never as zero. Nothing here calls Stripe: "
            "every number is read from the local mirror the webhook maintains."
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════
# THE CUSTOMER ROSTER — one row per paying relationship, operationally usable
# ═══════════════════════════════════════════════════════════════════════════
#
# /revenue answers "how are we doing". This answers "WHICH CUSTOMER NEEDS
# SOMETHING DONE ABOUT IT", which is a different question and the one somebody
# actually opens a billing screen to ask. A total tells you a number is wrong;
# only a roster tells you whose card failed.
#
# Read entirely from the local mirror. Nothing here calls Stripe.

# The operational states a finance user filters on. Each is a QUESTION about
# what needs attention, not a restatement of Stripe's status vocabulary -
# "payment_problem" spans past_due and unpaid because to the person working the
# list they are the same job.
FILTERS = ("all", "active", "trialing", "payment_problem", "pending_downgrade",
           "pending_cancellation", "no_payment_method", "held_capacity",
           "commercial_data_incomplete", "canceled")


def _customer_row(db: Session, o: Organization, plan_cache: dict) -> Dict[str, Any]:
    """Everything a finance operator needs about one customer, in one row."""
    key = getattr(o, "billing_plan_key", None) or o.plan
    cache_key = (o.platform_id, key)
    if cache_key not in plan_cache:
        plan_cache[cache_key] = billing_catalog.resolve_plan(db, o.platform_id, key)
    plan = plan_cache[cache_key]
    interval = getattr(o, "stripe_plan_interval", None) or BillingInterval.MONTH
    commitment = getattr(o, "billing_commitment", None)

    # MRR for THIS customer, priced from THEIR OWN brand's catalogue at THEIR
    # OWN commitment. None - never 0 - when the plan does not resolve, because
    # "we cannot explain what this customer is paying" and "this customer pays
    # nothing" are opposite facts that must not render identically.
    mrr_cents = _monthly_equivalent_cents(plan, interval, commitment) if plan else None

    last_payment = (db.query(BillingPayment)
                    .filter(BillingPayment.organization_id == o.id)
                    .order_by(BillingPayment.collected_at.desc().nullslast())
                    .first())
    invoice_count = (db.query(BillingInvoice)
                     .filter(BillingInvoice.organization_id == o.id).count())

    # Held inbound prospects. A customer sitting on held leads is a customer
    # with real business they cannot work - the most actionable upgrade
    # conversation on this screen.
    from app.services import lead_capacity
    held = lead_capacity.held_count(db, o.id)

    # THE COMMERCIAL CHAIN, where one exists. A customer created outside the
    # pipeline has no opportunity and no rep, and that is stated rather than
    # fabricated - an invented salesperson on a self-serve signup would
    # eventually pay somebody a commission.
    source = {"from_pipeline": False, "opportunity_id": None,
              "sales_rep": None, "note": "Created outside pipeline - "
                                          "commercial data incomplete"}
    try:
        from app.models.sales_models import Opportunity
        opp = (db.query(Opportunity)
               .filter(Opportunity.customer_organization_id == o.id)
               .order_by(Opportunity.created_at.asc())
               .first())
        if opp is not None:
            rep = (db.query(User).filter(User.id == opp.owner_user_id).first()
                   if getattr(opp, "owner_user_id", None) else None)
            source = {
                "from_pipeline": True,
                "opportunity_id": opp.id,
                "sales_rep": (getattr(rep, "full_name", None)
                              or getattr(rep, "email", None)) if rep else None,
                "note": None,
            }
    except Exception:                                    # pragma: no cover
        log.debug("god_billing: could not resolve commercial source for %s", o.id)

    implementation_status = None
    # THE SETUP FEE IS THE OTHER HALF OF THIS CUSTOMER'S MONEY, and until now
    # this roster could not see it at all: every column here describes the
    # subscription. A customer paying $1,000/mo whose $2,500 implementation fee
    # was never collected looked identical to one who paid it — which is the
    # combined-charge blind spot showing up in finance's own list.
    setup_fee = None
    try:
        from app.models.implementation_models import Implementation
        impl = (db.query(Implementation)
                .filter(Implementation.organization_id == o.id)
                .order_by(Implementation.created_at.desc())
                .first())
        implementation_status = getattr(impl, "status", None)
        if impl is not None:
            setup_fee = {
                # not_sent | checkout_pending | paid | failed
                "status": getattr(impl, "setup_payment_status", None) or "not_sent",
                "paid_cents": getattr(impl, "setup_paid_cents", None),
                "paid_at": getattr(impl, "setup_paid_at", None),
                # Present so a finance user can reopen the exact page that was
                # sent rather than asking the rep to generate another one.
                "checkout_url": getattr(impl, "setup_checkout_url", None),
            }
    except Exception:                                    # pragma: no cover
        pass

    status = (getattr(o, "billing_status", None) or "").lower()
    return {
        "organization_id": o.id,
        "name": o.name,
        "platform_id": o.platform_id,
        "plan_key": key,
        "plan_name": getattr(plan, "name", None),
        "billing_status": status or None,
        "interval": interval,
        # What the customer promised, not just how often they are charged.
        # NULL means the subscription predates the column or its Stripe price
        # is not in this brand's catalogue - the screen says "not recorded"
        # rather than picking a rate on the customer's behalf.
        "commitment": commitment,
        "commitment_label": (billing_catalog.commitment_label(commitment)
                             if commitment else None),
        "mrr_cents": mrr_cents,
        "mrr_unavailable_reason": (
            None if mrr_cents is not None else
            ("no such plan in this brand's catalogue" if plan is None
             else "plan has no %s price configured at %s"
                  % (interval, commitment or "the term rate"))),
        "currency": getattr(plan, "currency", None) or "usd",
        "current_period_end": getattr(o, "billing_current_period_end", None),
        "trial_end": getattr(o, "billing_trial_end", None),
        "pending_plan": getattr(o, "billing_pending_plan_key", None),
        "pending_effective_at": getattr(o, "billing_pending_effective_at", None),
        "cancel_at_period_end": bool(getattr(o, "billing_cancel_at_period_end", False)),
        "card_last4": getattr(o, "billing_card_last4", None),
        "card_brand": getattr(o, "billing_card_brand", None),
        "has_subscription": bool(getattr(o, "stripe_subscription_id", None)),
        "last_payment_at": getattr(last_payment, "collected_at", None),
        "last_payment_cents": getattr(last_payment, "amount_cents", None),
        "invoice_count": invoice_count,
        "held_lead_count": held,
        "implementation_status": implementation_status,
        # None means no implementation record exists to carry a setup fee —
        # deliberately not a fabricated "not_sent", which would claim a bill
        # that was never owed.
        "setup_fee": setup_fee,
        "commercial_source": source,
        "customer_since": getattr(o, "created_at", None),
    }


def _matches_filter(row: Dict[str, Any], f: str) -> bool:
    if f in (None, "", "all"):
        return True
    status = row["billing_status"] or ""
    if f == "active":
        return status == SubscriptionStatus.ACTIVE
    if f == "trialing":
        return status == SubscriptionStatus.TRIALING
    if f == "payment_problem":
        # past_due and unpaid are one job to whoever works this list.
        return status in (SubscriptionStatus.PAST_DUE, "unpaid")
    if f == "canceled":
        return status in ("canceled", "incomplete_expired")
    if f == "pending_downgrade":
        return bool(row["pending_plan"])
    if f == "pending_cancellation":
        return bool(row["cancel_at_period_end"])
    if f == "no_payment_method":
        # Only meaningful for somebody who is supposed to be paying.
        return row["has_subscription"] and not row["card_last4"]
    if f == "held_capacity":
        return (row["held_lead_count"] or 0) > 0
    if f == "commercial_data_incomplete":
        return not row["commercial_source"]["from_pipeline"]
    return True


@router.get("/customers")
def billing_customers(platform_id: Optional[str] = Query(None),
                      filter: str = Query("all"),
                      q: Optional[str] = Query(None),
                      limit: int = Query(200, ge=1, le=500),
                      db: Session = Depends(get_db),
                      user: User = Depends(require_god)):
    """One row per customer, with everything needed to act on it.

    `platform_id` segments by brand. BRAND ISOLATION IS THE POINT: without it
    this is the platform owner's own book, and with it a brand's finance user
    sees only their own customers. Brand A's billing must never appear in
    Brand B's list.
    """
    if filter not in FILTERS:
        raise HTTPException(
            status_code=400,
            detail="Unknown filter %r. Expected one of: %s"
                   % (filter, ", ".join(FILTERS)))

    if platform_id:
        _require_platform(db, platform_id)

    orgs_q = db.query(Organization).filter(Organization.id != PLATFORM_OWN_ORG_ID)
    if platform_id:
        orgs_q = orgs_q.filter(Organization.platform_id == platform_id)
    if q:
        orgs_q = orgs_q.filter(Organization.name.ilike("%%%s%%" % q.strip()))

    plan_cache: Dict[Any, Any] = {}
    rows = [_customer_row(db, o, plan_cache)
            for o in orgs_q.order_by(Organization.name.asc()).limit(limit).all()]

    matched = [r for r in rows if _matches_filter(r, filter)]

    # Counts for every filter, computed over the SAME scope, so the tabs show
    # real numbers rather than making somebody click each one to find out.
    counts = {f: sum(1 for r in rows if _matches_filter(r, f)) for f in FILTERS}

    priced = [r["mrr_cents"] for r in matched if r["mrr_cents"] is not None]
    return {
        "customers": matched,
        "counts": counts,
        "total_in_scope": len(rows),
        "shown": len(matched),
        "filter": filter,
        "scope": {"platform_id": platform_id, "all_brands": platform_id is None},
        # The MRR of what is on screen, and how much of it could be priced.
        # Reported together so a total is never read as complete when it is not.
        "visible_mrr_cents": sum(priced) if priced else None,
        "visible_priced": len(priced),
        "visible_unpriced": len(matched) - len(priced),
        "explanation": (
            "Every figure is read from the local mirror the Stripe webhook "
            "maintains; nothing here calls Stripe. A null mrr_cents carries a "
            "reason and means the plan could not be priced - it never means "
            "the customer pays nothing."
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════
# THE EVENT LEDGER — did Stripe tell us, and what did we do about it
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/events")
def billing_events(organization_id: Optional[str] = Query(None),
                   platform_id: Optional[str] = Query(None),
                   limit: int = Query(50, ge=1, le=200),
                   offset: int = Query(0, ge=0),
                   db: Session = Depends(get_db),
                   user: User = Depends(require_god)):
    """Every Stripe webhook we have accepted, newest first.

    THIS IS THE ANSWER TO "DID STRIPE TELL US ABOUT THIS, AND WHAT DID WE DO."
    `billing_events` is both the dedup key and the audit trail on purpose, so
    "did this already happen" and "what happened" cannot disagree. A row with
    outcome `duplicate` is Stripe having retried and this platform correctly
    doing nothing a second time — which is the single most confusing thing to
    look at without this screen, because it looks like a payment that went
    missing.

    `earned_compensation` and `compensation_note` are included because the
    other question asked of this table is "why did somebody get paid for this",
    and "no rule was configured for that package" is a real answer that has to
    be visible — otherwise an unpaid commission looks identical to a bug.

    NO PAYLOAD IS STORED AND NONE IS RETURNED. Stripe object ids (evt_, in_,
    sub_, cus_) are public identifiers; the payload can carry customer PII
    there is no reason to keep a second copy of, and no secret ever reaches
    this table.
    """
    q = db.query(BillingEvent)
    if organization_id:
        q = q.filter(BillingEvent.organization_id == organization_id)
    if platform_id:
        q = q.filter(BillingEvent.platform_id == platform_id)

    total = q.count()
    rows = (q.order_by(BillingEvent.received_at.desc())
            .offset(offset).limit(limit).all())

    return {
        "events": [{
            "id": e.id,
            "stripe_event_id": e.stripe_event_id,
            "event_type": e.event_type,
            "organization_id": e.organization_id,
            "platform_id": e.platform_id,
            "stripe_object_id": e.stripe_object_id,
            "stripe_customer_id": e.stripe_customer_id,
            "stripe_subscription_id": e.stripe_subscription_id,
            "outcome": e.outcome,
            "detail": e.detail,
            "earned_compensation": bool(e.earned_compensation),
            "compensation_note": e.compensation_note,
            "received_at": e.received_at,
            "processed_at": e.processed_at,
        } for e in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": (offset + len(rows)) < total,
        "explanation": (
            "Newest first. outcome 'duplicate' means Stripe redelivered an "
            "event this platform had already handled and nothing ran a second "
            "time — that is the idempotency ledger working, not a lost payment."
        ),
    }
