"""
The decided EvoSys Pro billing configuration, applied from God Mode.

NO SEED SCRIPT. NO RENDER SHELL. NO DIRECT DATABASE INSERT. NO DEVELOPER.
A God admin previews this and applies it from a screen, the same way
`evosys_comp_seed` is applied through POST /god/pricing/seed/evosys. Anything
that requires someone to SSH into a box to configure a product is not a
product feature.

WHAT IS SEEDED, AND WHAT IS DELIBERATELY NOT

  SEEDED    the three purchasable SaaS tiers and their monthly prices
  SEEDED    the Enterprise tier, listed and quoted, NOT self-serve
  SEEDED    upgrade timing/proration and downgrade timing/proration

  NOT SEEDED  failed-payment consequence      POLICY REQUIRED
  NOT SEEDED  subscription cancellation timing POLICY REQUIRED
  NOT SEEDED  trial length                     POLICY REQUIRED
  NOT SEEDED  refund / chargeback clawback     POLICY REQUIRED
  NOT SEEDED  Stripe product and price ids     created in Stripe, then recorded

The four POLICY REQUIRED rows are left NULL on purpose. An unset policy means
the engine does nothing, which is exactly today's behaviour; writing a
plausible default here would turn a decision nobody made into money customers
feel. See billing_policy.py.

ANNUAL PRICING IS DELIBERATELY ABSENT
The previous code billed `monthly x 11` on a twelve-month interval while the
UI described the same discount two other ways - "Month 13 free" in one place
and `price x 11/12` in another. Three descriptions of one discount is not a
discount anybody can reconcile, so no annual price is seeded. When the annual
offer is decided it goes in `annual_cents` as a real number, once.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.models.billing_models import (BrandBillingConfig, BrandBillingPlan,
                                       ChangeTiming, ProrationBehavior)

log = logging.getLogger(__name__)


# ── The decided customer SaaS subscription prices ─────────────────────────────
#
# THESE ARE THE MONTHLY SUBSCRIPTION PRICES, NOT THE DIRECT-SALE SETUP FEES.
# The sales catalogue for the same three names is $1,497 / $2,495 / $4,995 as
# ONE-TIME implementation fees and lives in `brand_packages`. Two different
# economic objects; same three words. Nothing here reads or writes that table.
EVOSYS_PLANS = [
    {
        "key": "starter",
        "name": "Starter",
        "sort_order": 10,
        "monthly_cents": 49700,          # $497 / month
        "max_leads": 2500,
        "max_users": 2,
        "is_purchasable": True,
        "features": [
            "AI email cadence (8 emails / 14 days)",
            "Up to 2 users",
            "Up to 2,500 leads",
        ],
    },
    {
        "key": "growth",
        "name": "Growth",
        "sort_order": 20,
        "monthly_cents": 99700,          # $997 / month
        "max_leads": 5000,
        "max_users": 3,
        "is_purchasable": True,
        "features": [
            "AI email + SMS 1,000/mo",
            "AI voice 300 min/mo",
            "Up to 3 users",
            "Up to 5,000 leads",
        ],
    },
    {
        "key": "professional",
        "name": "Professional",
        "sort_order": 30,
        "monthly_cents": 199700,         # $1,997 / month
        "max_leads": 7500,
        "max_users": 5,
        "is_purchasable": True,
        "features": [
            "AI email + SMS 3,000/mo",
            "AI voice 750 min/mo",
            "Up to 5 users / 3 locations",
            "Priority support + 24-month price lock",
        ],
    },
    {
        "key": "enterprise",
        "name": "Enterprise",
        "sort_order": 40,
        "monthly_cents": None,           # quoted, never self-serve
        "max_leads": None,               # NULL = unlimited
        "max_users": None,
        # is_purchasable False is what makes the checkout guard refuse this
        # tier with a clear reason instead of tripping over a NULL price.
        "is_purchasable": False,
        "features": [
            "Everything in Professional",
            "Unlimited leads, users, and locations",
            "White-label available",
        ],
    },
]


# ── The decided plan-change policy ────────────────────────────────────────────
#
# Upgrades take effect IMMEDIATELY, WITH PRORATION - the customer asked for
# more, gets it now, and is charged the difference for the remainder of the
# period.
#
# Downgrades take effect AT THE END OF THE CURRENT PAID PERIOD, with NO credit
# and NO refund - the customer keeps the tier they already bought until it runs
# out, and the lower price starts at the next renewal.
#
# Stored as brand configuration rather than written into the billing engine, so
# a second brand can decide differently without a code change.
EVOSYS_UPGRADE_TIMING = ChangeTiming.IMMEDIATE
EVOSYS_UPGRADE_PRORATION = ProrationBehavior.CREATE_PRORATIONS
EVOSYS_DOWNGRADE_TIMING = ChangeTiming.PERIOD_END
EVOSYS_DOWNGRADE_PRORATION = ProrationBehavior.NONE


def seed(db: Session, platform_id: str, *, apply: bool = False) -> dict:
    """Preview (default) or apply the decided EvoSys billing configuration.

    IDEMPOTENT AND NON-DESTRUCTIVE. An existing plan is UPDATED in the fields
    this seed owns and left alone everywhere else - in particular
    `stripe_product_id` and the two `stripe_price_id_*` columns are NEVER
    written here. Those are created in Stripe and recorded afterwards, and a
    re-run of this seed must not blank the mapping and start minting anonymous
    prices again.

    A POLICY REQUIRED field that is already set is likewise never overwritten:
    if somebody has decided a cancellation timing, re-seeding does not
    un-decide it.
    """
    actions = []

    for spec in EVOSYS_PLANS:
        existing = (db.query(BrandBillingPlan)
                    .filter(BrandBillingPlan.platform_id == platform_id,
                            BrandBillingPlan.key == spec["key"])
                    .first())
        payload = {
            "name": spec["name"],
            "sort_order": spec["sort_order"],
            "monthly_cents": spec["monthly_cents"],
            "max_leads": spec["max_leads"],
            "max_users": spec["max_users"],
            "is_purchasable": spec["is_purchasable"],
            "features_json": json.dumps(spec["features"]),
        }

        if existing is None:
            actions.append({"object": "plan", "key": spec["key"],
                            "action": "create", "detail": payload})
            if apply:
                db.add(BrandBillingPlan(platform_id=platform_id,
                                        key=spec["key"], is_active=True,
                                        currency="usd", **payload))
        else:
            changed = {k: v for k, v in payload.items()
                       if getattr(existing, k, None) != v}
            if changed:
                actions.append({"object": "plan", "key": spec["key"],
                                "action": "update", "detail": changed})
                if apply:
                    for k, v in changed.items():
                        setattr(existing, k, v)
            else:
                actions.append({"object": "plan", "key": spec["key"],
                                "action": "unchanged"})

    # ── Policy ────────────────────────────────────────────────────────────
    cfg = (db.query(BrandBillingConfig)
           .filter(BrandBillingConfig.platform_id == platform_id).first())

    decided = {
        "upgrade_timing": EVOSYS_UPGRADE_TIMING,
        "upgrade_proration": EVOSYS_UPGRADE_PRORATION,
        "downgrade_timing": EVOSYS_DOWNGRADE_TIMING,
        "downgrade_proration": EVOSYS_DOWNGRADE_PRORATION,
    }

    if cfg is None:
        actions.append({"object": "config", "action": "create",
                        "detail": decided})
        if apply:
            db.add(BrandBillingConfig(platform_id=platform_id, **decided))
    else:
        changed = {k: v for k, v in decided.items()
                   if getattr(cfg, k, None) != v}
        if changed:
            actions.append({"object": "config", "action": "update",
                            "detail": changed})
            if apply:
                for k, v in changed.items():
                    setattr(cfg, k, v)
        else:
            actions.append({"object": "config", "action": "unchanged"})

    if apply:
        db.commit()

    return {
        "applied": bool(apply),
        "platform_id": platform_id,
        "actions": actions,
        "not_seeded": {
            "past_due_grace_days": "POLICY REQUIRED",
            "suspend_on_past_due": "POLICY REQUIRED",
            "cancel_timing": "POLICY REQUIRED",
            "trial_days": "POLICY REQUIRED",
            "clawback_policy": "POLICY REQUIRED",
            "annual_cents": "Not decided - the previous annual discount was "
                            "described three different ways and none of them "
                            "agreed. Set a real number once it is decided.",
            "stripe_product_id / stripe_price_id_*":
                "Created in Stripe, then recorded here. This seed never writes "
                "them, so re-running it cannot blank the mapping.",
        },
        "explanation": (
            "Seeds the customer SaaS subscription catalogue and the decided "
            "plan-change policy for this brand. It does NOT touch "
            "brand_packages, which is the separate sales catalogue that "
            "compensation is calculated from, and it does not set any policy "
            "that has not been decided."
        ),
    }
