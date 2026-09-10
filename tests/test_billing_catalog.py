"""THE CATALOGUE IS THE SERVER'S ONLY SOURCE OF PRICE — AND AN UNSET POLICY
IS NOT A DEFAULT.

WHAT THIS FILE DEFENDS

  1. A PLAN BELONGS TO A BRAND. A key that exists for Brand B must not
     resolve, price, or even be discoverable for a Brand A organization.
  2. THE AMOUNT COMES FROM THE ROW. Monthly and annual prices are read from
     `brand_billing_plans`; a plan with no price for the interval asked for is
     refused rather than quoted at something plausible.
  3. UP AND DOWN ARE DECIDED ONCE. `classify_change` is the only place the
     direction of a plan move is worked out, interval tie-break included.
  4. AN UNSET POLICY MEANS DO NOTHING AND SAYS SO. Every accessor in
     `billing_policy` reports `configured: False` for a decision nobody has
     made, and nothing in the stack turns that into a number.
  5. THE SEED IS IDEMPOTENT AND NEVER TOUCHES THE STRIPE MAPPING.
"""

import itertools
import json

import pytest

from app.models.billing_models import (BillingInterval, BrandBillingConfig,
                                       BrandBillingPlan, ChangeTiming,
                                       ProrationBehavior)
from app.models.models import Organization, Platform
from app.services import (billing_catalog, billing_policy, entitlements,
                          evosys_billing_seed)

_SEQ = itertools.count(1)


def _platform(db, label="Alpha"):
    p = Platform(name=label, slug="%s-%d" % (label.lower(), next(_SEQ)))
    db.add(p)
    db.commit()
    return p


def _plan(db, platform, key, *, monthly=49700, annual=None, name=None,
          purchasable=True, active=True, price_id_monthly=None,
          price_id_annual=None, sort_order=10):
    plan = BrandBillingPlan(
        platform_id=platform.id, key=key, name=name or key.title(),
        monthly_cents=monthly, annual_cents=annual, currency="usd",
        is_purchasable=purchasable, is_active=active, sort_order=sort_order,
        stripe_price_id_monthly=price_id_monthly,
        stripe_price_id_annual=price_id_annual)
    db.add(plan)
    db.commit()
    return plan


def _org(db, platform, **kw):
    n = next(_SEQ)
    org = Organization(name="Customer %d" % n, slug="customer-%d" % n,
                       platform_id=platform.id, plan="trial", is_active=True,
                       **kw)
    db.add(org)
    db.commit()
    return org


# ═══════════════════════════════════════════════════════════════════════════
# 2. BRAND ISOLATION — one brand's tier is not another brand's tier
# ═══════════════════════════════════════════════════════════════════════════

def test_a_plan_key_belonging_to_another_brand_does_not_resolve(db_session):
    """Brand B sells "growth". A Brand A organization must not be able to buy
    it, price it, or learn that it exists."""
    brand_a = _platform(db_session, "Alpha")
    brand_b = _platform(db_session, "Beta")
    _plan(db_session, brand_a, "starter", monthly=49700)
    b_growth = _plan(db_session, brand_b, "growth", monthly=99700)

    org_a = _org(db_session, brand_a)
    platform_id = billing_catalog.platform_id_for_org(db_session, org_a)
    assert platform_id == brand_a.id

    assert billing_catalog.resolve_plan(db_session, platform_id, "growth") is None
    with pytest.raises(billing_catalog.PlanNotAvailable):
        billing_catalog.require_purchasable(db_session, platform_id, "growth",
                                            BillingInterval.MONTH)

    # And it is not even listed: the catalogue a Brand A customer sees is
    # Brand A's, so another brand's tier names are not discoverable.
    keys = {p.key for p in billing_catalog.plans_for(db_session, platform_id)}
    assert keys == {"starter"}
    assert b_growth.platform_id == brand_b.id


def test_the_same_key_in_two_brands_prices_from_the_callers_own_brand(db_session):
    """Both brands sell "growth" and they are different products at different
    prices. `.get(key)` against the wrong table is the whole failure mode."""
    brand_a = _platform(db_session, "Alpha")
    brand_b = _platform(db_session, "Beta")
    _plan(db_session, brand_a, "growth", monthly=99700)
    _plan(db_session, brand_b, "growth", monthly=250000)

    a_plan = billing_catalog.require_purchasable(
        db_session, brand_a.id, "growth", BillingInterval.MONTH)
    b_plan = billing_catalog.require_purchasable(
        db_session, brand_b.id, "growth", BillingInterval.MONTH)
    assert billing_catalog.price_cents_for(a_plan, "month") == 99700
    assert billing_catalog.price_cents_for(b_plan, "month") == 250000


# ═══════════════════════════════════════════════════════════════════════════
# 4. AN INVALID PACKAGE KEY IS REFUSED — never resolved to something plausible
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("key", ["", None, "platinum", "STARTER", "starter ",
                                 "../starter", "professional-2"])
def test_an_unknown_package_key_is_rejected(db_session, key):
    brand = _platform(db_session)
    _plan(db_session, brand, "starter", monthly=49700)
    with pytest.raises(billing_catalog.PlanNotAvailable):
        billing_catalog.require_purchasable(db_session, brand.id, key,
                                            BillingInterval.MONTH)


def test_a_quoted_tier_is_listed_but_not_purchasable(db_session):
    """Enterprise is advertised and never self-serve, so the guard refuses it
    with a reason rather than tripping over a NULL price."""
    brand = _platform(db_session)
    _plan(db_session, brand, "enterprise", monthly=None, purchasable=False)
    assert billing_catalog.resolve_plan(db_session, brand.id, "enterprise")
    with pytest.raises(billing_catalog.PlanNotAvailable):
        billing_catalog.require_purchasable(db_session, brand.id, "enterprise",
                                            BillingInterval.MONTH)


def test_an_inactive_plan_cannot_be_bought(db_session):
    brand = _platform(db_session)
    _plan(db_session, brand, "legacy", monthly=29700, active=False)
    with pytest.raises(billing_catalog.PlanNotAvailable):
        billing_catalog.require_purchasable(db_session, brand.id, "legacy",
                                            BillingInterval.MONTH)


@pytest.mark.parametrize("interval", ["weekly", "day", "", "MONTH", None])
def test_an_unknown_interval_is_rejected(db_session, interval):
    brand = _platform(db_session)
    _plan(db_session, brand, "starter", monthly=49700, annual=596400)
    with pytest.raises(billing_catalog.PlanNotAvailable):
        billing_catalog.require_purchasable(db_session, brand.id, "starter",
                                            interval)


def test_an_organization_with_no_brand_has_no_catalogue(db_session):
    """A NULL platform_id is a real state and the answer is refusal, not the
    first brand in the table."""
    orphan = Organization(name="Orphan", slug="orphan-%d" % next(_SEQ),
                          platform_id=None, plan="trial", is_active=True)
    db_session.add(orphan)
    db_session.commit()
    assert billing_catalog.platform_id_for_org(db_session, orphan) is None
    assert billing_catalog.resolve_plan(db_session, None, "starter") is None
    with pytest.raises(billing_catalog.PlanNotAvailable):
        billing_catalog.require_purchasable(db_session, None, "starter",
                                            BillingInterval.MONTH)


# ═══════════════════════════════════════════════════════════════════════════
# 5-6. PRICE RESOLUTION — monthly, and annual, from the row and nowhere else
# ═══════════════════════════════════════════════════════════════════════════

def test_monthly_price_resolution_reads_the_catalogue_row(db_session):
    brand = _platform(db_session)
    plan = _plan(db_session, brand, "growth", monthly=99700)
    resolved = billing_catalog.require_purchasable(
        db_session, brand.id, "growth", BillingInterval.MONTH)
    assert resolved.id == plan.id
    assert billing_catalog.price_cents_for(resolved, BillingInterval.MONTH) == 99700
    # Cents, integer, never a float dollar amount.
    assert isinstance(resolved.monthly_cents, int)


def test_annual_price_resolution_reads_the_annual_column(db_session):
    """The EvoSys seed deliberately sets NO annual price, so this constructs a
    brand that has decided one. $9,970 a year against $997 a month."""
    brand = _platform(db_session)
    plan = _plan(db_session, brand, "growth", monthly=99700, annual=997000)
    resolved = billing_catalog.require_purchasable(
        db_session, brand.id, "growth", BillingInterval.YEAR)
    assert resolved.id == plan.id
    assert billing_catalog.price_cents_for(resolved, BillingInterval.YEAR) == 997000
    assert billing_catalog.price_cents_for(resolved, BillingInterval.MONTH) == 99700


def test_a_plan_with_no_annual_price_refuses_an_annual_purchase(db_session):
    """No fallback to monthly x 11, monthly x 12, or anything else. The
    previous code had three disagreeing descriptions of one annual discount."""
    brand = _platform(db_session)
    _plan(db_session, brand, "starter", monthly=49700, annual=None)
    assert billing_catalog.require_purchasable(
        db_session, brand.id, "starter", BillingInterval.MONTH)
    with pytest.raises(billing_catalog.PlanNotAvailable):
        billing_catalog.require_purchasable(db_session, brand.id, "starter",
                                            BillingInterval.YEAR)


def test_a_stripe_price_id_satisfies_the_interval_without_a_cents_column(db_session):
    """A brand whose Stripe Products exist is purchasable on that mapping."""
    brand = _platform(db_session)
    _plan(db_session, brand, "growth", monthly=99700, annual=None,
          price_id_annual="price_annual_fake")
    plan = billing_catalog.require_purchasable(db_session, brand.id, "growth",
                                               BillingInterval.YEAR)
    assert billing_catalog.stripe_price_id_for(plan, BillingInterval.YEAR) == \
        "price_annual_fake"


def test_malformed_features_never_break_a_price(db_session):
    brand = _platform(db_session)
    plan = _plan(db_session, brand, "starter", monthly=49700)
    plan.features_json = "{not json"
    db_session.commit()
    assert billing_catalog.features_for(plan) == []
    assert billing_catalog.price_cents_for(plan, BillingInterval.MONTH) == 49700


# ═══════════════════════════════════════════════════════════════════════════
# classify_change — up, down, sideways, and the interval tie-break
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def tiers(db_session):
    brand = _platform(db_session, "Tiered")
    return {
        "brand": brand,
        "starter": _plan(db_session, brand, "starter", monthly=49700,
                         annual=596400, sort_order=10),
        "growth": _plan(db_session, brand, "growth", monthly=99700,
                        annual=1196400, sort_order=20),
        "quoted": _plan(db_session, brand, "enterprise", monthly=None,
                        purchasable=False, sort_order=40),
    }


def test_a_more_expensive_tier_is_an_upgrade(db_session, tiers):
    assert billing_catalog.classify_change(
        db_session, tiers["starter"], "month", tiers["growth"], "month") \
        == billing_catalog.UPGRADE


def test_a_cheaper_tier_is_a_downgrade(db_session, tiers):
    assert billing_catalog.classify_change(
        db_session, tiers["growth"], "month", tiers["starter"], "month") \
        == billing_catalog.DOWNGRADE


def test_the_same_plan_and_interval_is_lateral(db_session, tiers):
    assert billing_catalog.classify_change(
        db_session, tiers["starter"], "month", tiers["starter"], "month") \
        == billing_catalog.LATERAL


def test_annual_is_compared_at_its_monthly_equivalent(db_session, tiers):
    """$5,964 a year is not a twelve-fold upgrade on $497 a month; it is the
    same tier. Dividing by twelve is what stops a tie-break being skipped."""
    assert billing_catalog._monthly_equivalent_cents(tiers["starter"], "year") \
        == 49700
    # Starter annual -> Growth monthly is still an upgrade on tier price.
    assert billing_catalog.classify_change(
        db_session, tiers["starter"], "year", tiers["growth"], "month") \
        == billing_catalog.UPGRADE


def test_the_interval_breaks_the_tie_when_the_tier_price_is_equal(db_session, tiers):
    """Same tier, different commitment. Moving to annual commits more money
    sooner and applies immediately; moving to monthly reduces commitment and
    waits for the period already bought to run out."""
    assert billing_catalog.classify_change(
        db_session, tiers["starter"], "month", tiers["starter"], "year") \
        == billing_catalog.UPGRADE
    assert billing_catalog.classify_change(
        db_session, tiers["starter"], "year", tiers["starter"], "month") \
        == billing_catalog.DOWNGRADE


def test_no_current_plan_reads_as_an_upgrade(db_session, tiers):
    assert billing_catalog.classify_change(
        db_session, None, None, tiers["growth"], "month") \
        == billing_catalog.UPGRADE


def test_an_incomparable_price_is_lateral_rather_than_a_guess(db_session, tiers):
    """A quoted tier has no number to compare, so no timing policy is applied
    on the strength of a figure that does not exist."""
    assert billing_catalog.classify_change(
        db_session, tiers["quoted"], "month", tiers["growth"], "month") \
        == billing_catalog.LATERAL
    assert billing_catalog.classify_change(
        db_session, tiers["growth"], "month", tiers["quoted"], "month") \
        == billing_catalog.LATERAL


# ═══════════════════════════════════════════════════════════════════════════
# AN UNSET POLICY IS NOT A DEFAULT — every accessor says "not decided"
# ═══════════════════════════════════════════════════════════════════════════

def test_a_brand_with_no_config_row_has_every_policy_unconfigured(db_session):
    """No config row at all. Nothing may fall back to EvoSys's decisions —
    they are EvoSys's, not a universal truth about how software is billed."""
    brand = _platform(db_session)
    assert billing_policy.config_for(db_session, brand.id) is None

    for direction in (billing_catalog.UPGRADE, billing_catalog.DOWNGRADE):
        behavior = billing_policy.change_behavior(db_session, brand.id, direction)
        assert behavior["configured"] is False
        assert behavior["timing"] is None
        assert behavior["proration"] is None

    past_due = billing_policy.past_due_behavior(db_session, brand.id)
    assert past_due["configured"] is False
    assert past_due["suspends"] is False        # NOT a decision not to suspend
    assert past_due["grace_days"] is None
    assert past_due["reason"] == billing_policy.POLICY_REQUIRED

    cancel = billing_policy.cancel_behavior(db_session, brand.id)
    assert cancel["configured"] is False and cancel["timing"] is None

    assert billing_policy.trial_days(db_session, brand.id) is None

    clawback = billing_policy.clawback_behavior(db_session, brand.id)
    assert clawback["configured"] is False
    assert clawback["adjusts_compensation"] is False


def test_a_config_row_with_null_policies_is_still_undecided(db_session):
    """The row existing is not a decision. Every POLICY REQUIRED column is
    NULL and must report as open, not as a False anybody chose."""
    brand = _platform(db_session)
    db_session.add(BrandBillingConfig(
        platform_id=brand.id,
        upgrade_timing=ChangeTiming.IMMEDIATE,
        upgrade_proration=ProrationBehavior.CREATE_PRORATIONS))
    db_session.commit()

    described = billing_policy.describe(db_session, brand.id)
    assert described["has_config_row"] is True
    assert described["upgrade"]["configured"] is True
    # Half-configured is not configured: downgrade has neither value set.
    assert described["downgrade"]["configured"] is False
    assert set(described["policy_required"]) == {
        "downgrade timing/proration", "failed-payment consequence",
        "subscription cancellation timing", "refund/chargeback clawback"}
    assert described["trial_days"] is None


def test_describe_lists_every_open_question_for_an_unconfigured_brand(db_session):
    brand = _platform(db_session)
    described = billing_policy.describe(db_session, brand.id)
    assert described["has_config_row"] is False
    assert set(described["policy_required"]) == {
        "upgrade timing/proration", "downgrade timing/proration",
        "failed-payment consequence", "subscription cancellation timing",
        "refund/chargeback clawback"}


def test_a_half_set_direction_is_refused_rather_than_half_applied(db_session):
    """A timing with no proration is not a policy. Applying one without the
    other either bills someone early or gives away a tier."""
    brand = _platform(db_session)
    db_session.add(BrandBillingConfig(platform_id=brand.id,
                                      downgrade_timing=ChangeTiming.PERIOD_END))
    db_session.commit()
    behavior = billing_policy.change_behavior(db_session, brand.id,
                                              billing_catalog.DOWNGRADE)
    assert behavior["configured"] is False
    assert behavior["timing"] is None


def test_a_decided_brand_reports_exactly_what_it_decided(db_session):
    brand = _platform(db_session)
    db_session.add(BrandBillingConfig(
        platform_id=brand.id,
        upgrade_timing=ChangeTiming.IMMEDIATE,
        upgrade_proration=ProrationBehavior.CREATE_PRORATIONS,
        downgrade_timing=ChangeTiming.PERIOD_END,
        downgrade_proration=ProrationBehavior.NONE,
        suspend_on_past_due=False, cancel_timing=ChangeTiming.PERIOD_END,
        trial_days=14, clawback_policy="reverse_on_refund"))
    db_session.commit()

    up = billing_policy.change_behavior(db_session, brand.id,
                                        billing_catalog.UPGRADE)
    assert (up["configured"], up["timing"], up["proration"]) == \
        (True, ChangeTiming.IMMEDIATE, ProrationBehavior.CREATE_PRORATIONS)

    # False here IS a decision, and it reads differently from NULL.
    past_due = billing_policy.past_due_behavior(db_session, brand.id)
    assert past_due["configured"] is True and past_due["suspends"] is False

    assert billing_policy.trial_days(db_session, brand.id) == 14
    clawback = billing_policy.clawback_behavior(db_session, brand.id)
    # Recorded is not implemented — a string nobody understands changes nothing.
    assert clawback["configured"] is True
    assert clawback["adjusts_compensation"] is False
    assert clawback["reason"] == "recorded_not_implemented"
    assert billing_policy.describe(db_session, brand.id)["policy_required"] == []


def test_trial_days_never_gives_away_a_month_by_default(db_session):
    brand = _platform(db_session)
    db_session.add(BrandBillingConfig(platform_id=brand.id, trial_days=0))
    db_session.commit()
    assert billing_policy.trial_days(db_session, brand.id) is None


# ═══════════════════════════════════════════════════════════════════════════
# ENTITLEMENTS — nothing auto-suspends while the policy is open
# ═══════════════════════════════════════════════════════════════════════════

def test_a_past_due_org_is_not_suspended_when_no_policy_is_configured(db_session):
    """The wiring exists; nothing is withdrawn from anybody. A default grace
    period would cut off a paying customer over a card that expired on a
    Friday, on a schedule nobody chose."""
    brand = _platform(db_session)
    org = _org(db_session, brand, billing_status="past_due",
               stripe_customer_id="cus_pastdue", stripe_subscription_id="sub_1")
    assert billing_policy.past_due_behavior(db_session, brand.id)["configured"] \
        is False
    assert entitlements.billing_suspension_reason(db_session, org) is None


def test_an_unpaid_org_is_not_suspended_when_no_policy_is_configured(db_session):
    brand = _platform(db_session)
    org = _org(db_session, brand, billing_status="unpaid",
               stripe_customer_id="cus_unpaid")
    assert entitlements.billing_suspension_reason(db_session, org) is None


def test_a_brand_that_decided_not_to_suspend_does_not_suspend(db_session):
    brand = _platform(db_session)
    db_session.add(BrandBillingConfig(platform_id=brand.id,
                                      suspend_on_past_due=False))
    db_session.commit()
    org = _org(db_session, brand, billing_status="past_due",
               stripe_customer_id="cus_decided")
    assert entitlements.billing_suspension_reason(db_session, org) is None


def test_a_cancelled_org_is_never_suspended_by_billing_status(db_session):
    """Cancellation is a lifecycle decision made by a person, never something
    a webhook turns into an access decision."""
    brand = _platform(db_session)
    db_session.add(BrandBillingConfig(platform_id=brand.id,
                                      suspend_on_past_due=True))
    db_session.commit()
    org = _org(db_session, brand, billing_status="canceled",
               stripe_customer_id="cus_gone")
    assert entitlements.billing_suspension_reason(db_session, org) is None


def test_an_org_that_never_subscribed_is_never_suspended(db_session):
    """No stripe customer id means no billing relationship at all — reading
    that as "not paying" locks out the customers being set up."""
    brand = _platform(db_session)
    db_session.add(BrandBillingConfig(platform_id=brand.id,
                                      suspend_on_past_due=True))
    db_session.commit()
    org = _org(db_session, brand, billing_status="past_due",
               stripe_customer_id=None)
    assert entitlements.billing_suspension_reason(db_session, org) is None


# ═══════════════════════════════════════════════════════════════════════════
# THE SEED — idempotent, and it never writes a Stripe mapping
# ═══════════════════════════════════════════════════════════════════════════

def test_the_seed_previews_without_writing_anything(db_session):
    brand = _platform(db_session)
    preview = evosys_billing_seed.seed(db_session, brand.id, apply=False)
    assert preview["applied"] is False
    assert db_session.query(BrandBillingPlan).count() == 0
    assert db_session.query(BrandBillingConfig).count() == 0
    assert {a["action"] for a in preview["actions"]} == {"create"}


def test_the_seed_is_idempotent(db_session):
    """A second apply changes nothing. Re-running a price seed must not
    rewrite what a god_admin has since edited, nor churn updated_at."""
    brand = _platform(db_session)
    first = evosys_billing_seed.seed(db_session, brand.id, apply=True)
    assert first["applied"] is True
    plan_count = db_session.query(BrandBillingPlan).count()
    assert plan_count == len(evosys_billing_seed.EVOSYS_PLANS)

    second = evosys_billing_seed.seed(db_session, brand.id, apply=True)
    assert {a["action"] for a in second["actions"]} == {"unchanged"}
    assert db_session.query(BrandBillingPlan).count() == plan_count
    assert db_session.query(BrandBillingConfig).filter(
        BrandBillingConfig.platform_id == brand.id).count() == 1


def test_the_seed_carries_the_APPROVED_evosys_recurring_prices(db_session):
    """The approved figures, pinned. September 2026: $500 / $1,000 / $2,000 per
    month, replacing $497 / $997 / $1,997.

    Pinned in a test rather than only in a comment because these are the
    numbers that land on a customer's card. A refactor that reintroduces the
    old figures is a silent pricing regression nobody would see until an
    invoice, and this is the only thing in the repo that would notice.
    """
    brand = _platform(db_session)
    evosys_billing_seed.seed(db_session, brand.id, apply=True)

    expected = {"starter": 50000, "growth": 100000, "professional": 200000}
    for key, cents in expected.items():
        plan = billing_catalog.resolve_plan(db_session, brand.id, key)
        assert plan is not None, key
        assert plan.monthly_cents == cents, key
        assert plan.is_purchasable is True, key

    # Enterprise / Custom carries NO price. A default here would be an invented
    # figure on a deal that is priced by negotiation, and `is_purchasable`
    # False is what makes checkout refuse it with a reason instead of tripping
    # over the NULL.
    ent = billing_catalog.resolve_plan(db_session, brand.id, "enterprise")
    assert ent.monthly_cents is None
    assert ent.is_purchasable is False


def test_no_plan_is_purchasable_until_its_stripe_price_is_recorded(db_session):
    """Configured price, no Stripe object — deliberately, until TEST-mode
    Stripe configuration exists. The catalogue states the amount; nothing can
    charge it yet, and `deal_billing` says so by name rather than inventing a
    price on the fly."""
    brand = _platform(db_session)
    evosys_billing_seed.seed(db_session, brand.id, apply=True)
    for key in ("starter", "growth", "professional"):
        plan = billing_catalog.resolve_plan(db_session, brand.id, key)
        assert billing_catalog.price_cents_for(plan, "month") is not None
        assert billing_catalog.stripe_price_id_for(plan, "month") is None


def test_the_seed_never_writes_a_stripe_price_id(db_session):
    """Stripe ids are created in Stripe and recorded afterwards. A re-run that
    blanked the mapping would send checkout back to minting anonymous Prices
    on every call."""
    brand = _platform(db_session)
    evosys_billing_seed.seed(db_session, brand.id, apply=True)

    starter = billing_catalog.resolve_plan(db_session, brand.id, "starter")
    assert starter.stripe_product_id is None
    assert starter.stripe_price_id_monthly is None
    assert starter.stripe_price_id_annual is None

    starter.stripe_product_id = "prod_fake"
    starter.stripe_price_id_monthly = "price_fake_monthly"
    db_session.commit()

    again = evosys_billing_seed.seed(db_session, brand.id, apply=True)
    db_session.refresh(starter)
    assert starter.stripe_product_id == "prod_fake"
    assert starter.stripe_price_id_monthly == "price_fake_monthly"
    for action in again["actions"]:
        assert "stripe_price_id_monthly" not in json.dumps(action.get("detail") or {})


def test_the_seed_leaves_every_open_policy_open(db_session):
    """Four POLICY REQUIRED columns ship NULL on purpose. A plausible default
    here would turn a decision nobody made into money customers feel."""
    brand = _platform(db_session)
    evosys_billing_seed.seed(db_session, brand.id, apply=True)
    cfg = billing_policy.config_for(db_session, brand.id)
    assert cfg.past_due_grace_days is None
    assert cfg.suspend_on_past_due is None
    assert cfg.cancel_timing is None
    assert cfg.trial_days is None
    assert cfg.clawback_policy is None
    # The decided pair IS seeded.
    assert cfg.upgrade_timing == ChangeTiming.IMMEDIATE
    assert cfg.upgrade_proration == ProrationBehavior.CREATE_PRORATIONS
    assert cfg.downgrade_timing == ChangeTiming.PERIOD_END
    assert cfg.downgrade_proration == ProrationBehavior.NONE


def test_a_decided_policy_is_not_un_decided_by_re_seeding(db_session):
    brand = _platform(db_session)
    evosys_billing_seed.seed(db_session, brand.id, apply=True)
    cfg = billing_policy.config_for(db_session, brand.id)
    cfg.cancel_timing = ChangeTiming.PERIOD_END
    cfg.trial_days = 7
    db_session.commit()

    evosys_billing_seed.seed(db_session, brand.id, apply=True)
    db_session.refresh(cfg)
    assert cfg.cancel_timing == ChangeTiming.PERIOD_END
    assert cfg.trial_days == 7


def test_the_seed_sets_no_annual_price(db_session):
    """The previous annual discount was described three different ways and
    none of them agreed, so no annual number is invented here."""
    brand = _platform(db_session)
    evosys_billing_seed.seed(db_session, brand.id, apply=True)
    for key in ("starter", "growth", "professional", "enterprise"):
        plan = billing_catalog.resolve_plan(db_session, brand.id, key)
        assert plan.annual_cents is None, key
        with pytest.raises(billing_catalog.PlanNotAvailable):
            billing_catalog.require_purchasable(db_session, brand.id, key,
                                                BillingInterval.YEAR)


def test_the_seeded_prices_are_the_saas_prices_not_the_setup_fees(db_session):
    """$500 / $1,000 / $2,000 a MONTH. `brand_packages` sells the same three
    names as $1,500 / $2,500 / $5,000 ONE-TIME, and nothing maps them.

    The protection is unchanged by the September 2026 reprice: these two
    catalogues use the same three words for two different economic objects, and
    a setup fee that found its way into `monthly_cents` would bill a customer
    their implementation fee every month.
    """
    brand = _platform(db_session)
    evosys_billing_seed.seed(db_session, brand.id, apply=True)
    prices = {p.key: p.monthly_cents
              for p in billing_catalog.plans_for(db_session, brand.id)}
    assert prices == {"starter": 50000, "growth": 100000,
                      "professional": 200000, "enterprise": None}
    # And none of them is a setup fee.
    assert set(prices.values()).isdisjoint({150000, 250000, 500000})
