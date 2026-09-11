"""
The decided EvoSys Pro billing configuration, applied from God Mode.

NO SEED SCRIPT. NO RENDER SHELL. NO DIRECT DATABASE INSERT. NO DEVELOPER.
A God admin previews this and applies it from a screen, the same way
`evosys_comp_seed` is applied through POST /god/pricing/seed/evosys. Anything
that requires someone to SSH into a box to configure a product is not a
product feature.

WHAT IS SEEDED, AND WHAT IS DELIBERATELY NOT

  SEEDED    the three purchasable SaaS tiers and their monthly prices
  SEEDED    their settled capacity: users, active leads, emails/mo, SMS/mo
  SEEDED    the Custom tier, listed and quoted, NOT self-serve
  SEEDED    upgrade timing/proration and downgrade timing/proration

  NOT SEEDED  term_months                      NOT DECIDED (see below)
  NOT SEEDED  voice / AI usage allowances      not a plan dimension

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
# The sales catalogue for the same three names holds the ONE-TIME
# implementation fees ($1,500 / $2,500 / $5,000 as approved) and lives in
# `brand_packages`. Two different economic objects; same three words. Nothing
# here reads or writes that table.
#
# APPROVED PRICING, SEPTEMBER 2026. The recurring figures below replace the
# earlier $497 / $997 / $1,997. They are stated here — brand configuration a
# God admin previews and applies — and nowhere in the billing engine, which
# reads them back out of `brand_billing_plans` like any other brand's.
#
#                          setup      TERM /mo    MONTH-TO-MONTH /mo
#   Starter                $1,500       $500            $597
#   Growth                 $2,500     $1,000          $1,297
#   Professional           $5,000     $2,000          $2,597
#   Custom            approved amounts only, never a default — see
#                     deal_billing.custom_recurring_authority()
#
# TWO PRICES PER TIER, NOT SIX TIERS. `monthly_cents` is the committed rate
# earned by a term agreement; `month_to_month_cents` is the same tier's price
# for a customer who committed to nothing. The customer buys Starter either
# way — the commitment is a property of the deal, not a different product.
#
# ════════════════════════════════════════════════════════════════════════════
# THE FEATURES LISTS ARE EMPTY, AND THAT IS THE FIX (2026-09-11)
# ════════════════════════════════════════════════════════════════════════════
#
# They used to carry the whole customer-facing plan card as prose:
#
#     Starter       "Up to 2 users"                  WRONG — settled at 1
#     Growth        "AI email + SMS 1,000/mo"        WRONG — settled at 1,500
#     Growth        "AI voice 300 min/mo"            GONE  — AI Voice is now a
#                                                            separate add-on
#     Professional  "AI voice 750 min/mo"            GONE  — likewise
#     Professional  "Up to 5 users / 3 locations"    capacity, not prose
#     Professional  "Priority support + 24-month
#                    price lock"                     WRONG — nobody configured
#                                                            a 24-month term,
#                                                            and support is the
#                                                            support product's
#                                                            own entitlement
#     (max_leads)   Professional 7,500               WRONG — settled at 10,000
#
# Every one of those was a sentence a person typed, rendered to a paying
# customer as though it were configuration, and correctable only by a deploy.
# Capacity now lives in COLUMNS (`max_users`, `max_leads`,
# `email_monthly_allowance`, `sms_monthly_allowance`, `max_locations`) and
# support lives in `support_entitlement_configs`, both of which God Mode
# edits and the customer's card reads. So the features list is emptied rather
# than rewritten: a second place to state capacity is how the first one got
# out of date.
#
# WHAT IS DELIBERATELY NOT SEEDED HERE:
#   term_months          Nobody has decided the committed term's length. NULL
#                        makes the label read "Term agreement", which is true.
#                        Writing 24 to match the old string would be inventing
#                        the very commitment that was wrong.
#   voice allowances     AI Voice is a catalogue add-on. It has no plan column
#                        on purpose.
#   AI usage limits      Not settled. Not guessed.
EVOSYS_PLANS = [
    {
        "key": "starter",
        "name": "Starter",
        "sort_order": 10,
        "monthly_cents": 50000,          # $500 / month, committed
        "month_to_month_cents": 59700,   # $597 / month, no commitment
        "max_leads": 2500,
        "max_users": 1,
        "max_locations": None,
        "email_monthly_allowance": 2500,
        "sms_monthly_allowance": 500,
        "is_purchasable": True,
        "features": [],
    },
    {
        "key": "growth",
        "name": "Growth",
        "sort_order": 20,
        "monthly_cents": 100000,         # $1,000 / month, committed
        "month_to_month_cents": 129700,  # $1,297 / month, no commitment
        "max_leads": 5000,
        "max_users": 3,
        "max_locations": None,
        "email_monthly_allowance": 5000,
        "sms_monthly_allowance": 1500,
        "is_purchasable": True,
        "features": [],
    },
    {
        "key": "professional",
        "name": "Professional",
        "sort_order": 30,
        "monthly_cents": 200000,         # $2,000 / month, committed
        "month_to_month_cents": 259700,  # $2,597 / month, no commitment
        "max_leads": 10000,
        "max_users": 5,
        "max_locations": None,
        "email_monthly_allowance": 10000,
        "sms_monthly_allowance": 3000,
        "is_purchasable": True,
        "features": [],
    },
    {
        # THE KEY STAYS `enterprise`; THE NAME BECOMES `Custom`.
        #
        # The settled tier list names this one Custom, and "Enterprise" on a
        # customer card is a product name nobody sells any more. The KEY is
        # not renamed: it is written into `organizations.billing_plan_key`,
        # into Stripe subscription metadata and into every historical
        # entitlement snapshot, and renaming it would orphan all three to
        # correct a display string. `deal_billing`'s custom-pricing path is
        # the mechanism behind this tier and is untouched.
        "key": "enterprise",
        "name": "Custom",
        "sort_order": 40,
        "monthly_cents": None,           # quoted, never self-serve
        "month_to_month_cents": None,    # likewise — a quote, not a rate card
        # NULL everywhere: a Custom deal's ceilings are recorded on its own
        # CustomerEntitlementSnapshot when the deal is written, which is where
        # `plan_limits` reads them from. A number here would be a rate card
        # for a tier that does not have one.
        "max_leads": None,
        "max_users": None,
        "max_locations": None,
        "email_monthly_allowance": None,
        "sms_monthly_allowance": None,
        # is_purchasable False is what makes the checkout guard refuse this
        # tier with a clear reason instead of tripping over a NULL price.
        "is_purchasable": False,
        "features": [],
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
            "month_to_month_cents": spec["month_to_month_cents"],
            "max_leads": spec["max_leads"],
            "max_users": spec["max_users"],
            # The capacity that used to be prose. Included in the diff the God
            # preview shows, so applying this seed is a change an operator can
            # read line by line before it reaches a customer's plan card.
            "max_locations": spec["max_locations"],
            "email_monthly_allowance": spec["email_monthly_allowance"],
            "sms_monthly_allowance": spec["sms_monthly_allowance"],
            "is_purchasable": spec["is_purchasable"],
            # An EMPTY list, written deliberately: this clears the stale
            # marketing sentences off every existing row rather than leaving
            # them beside the columns that now say the same thing correctly.
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
            "term_months": "NOT DECIDED. The plan cards said '24-month price "
                           "lock' because that string was typed into a "
                           "features list, not because a term was configured. "
                           "Left NULL, so the customer-facing label reads "
                           "'Term agreement' until somebody decides.",
            "voice / AI usage allowances":
                "Deliberately absent. AI Voice is a separate catalogue "
                "add-on, Lead Scraper likewise, and no AI usage limit has "
                "been settled. There is no plan column for any of them, so "
                "none can be advertised by accident.",
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
