"""SALES COMPENSATION — projection, earning, and the line between them.

THE LINE THIS FILE DEFENDS

An open opportunity produces NUMBERS. A won-and-collected deal produces ROWS.
Nothing an open pipeline can do creates a payable, and `earn()` refuses without
a collected payment, so "Won" on its own never becomes money.

The second thing it defends is that the projection and the payout are the SAME
computation. A forecast and a payroll run that disagree about one deal, with
nobody able to say which is wrong, is the failure mode a separate projection
formula guarantees.

THE REAL EVOSYS STRUCTURE IS THE WORKED EXAMPLE
    Starter, $1,497 implementation
    rep      $500 fixed
    manager  $100 fixed override
    total payout on this package capped at $800
"""

import itertools
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from app.models.compensation_models import (
    BASIS_FIXED, BASIS_PCT_COLLECTED, BASIS_PCT_MRR, BASIS_PCT_SETUP,
    COMP_EARNED, COMP_PAID, COMP_PAYABLE, DEAL_CUSTOM, DEAL_STANDARD,
    PAYEE_OVERRIDE, PAYEE_SELLER, PROJ_PENDING_APPROVAL, PROJ_PROJECTED,
    PROJ_UNCONFIGURED, CompensationEntry, CompensationPackageCap,
    CompensationPlan, CompensationRule, StageProbability)
from app.models.models import Platform, User
from app.models.sales_models import (APPROVAL_PENDING, ROLE_SALES_MANAGER,
                                     ROLE_SALES_REP, SCOPE_BRAND_SALES_ORG,
                                     BrandPackage, BrandSalesOrg, Membership,
                                     Opportunity, PricingApprovalRequest)
from app.services import compensation as comp
from app.services import pipeline_projection as proj
from app.services.auth_service import hash_password

_SEQ = itertools.count(1)


# ═════════════════════════════════════════════════════════════════════════════
# Fixtures — one brand, one Starter package, a rep reporting to a manager
# ═════════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def platform(db_session):
    p = Platform(name="EvoSys Pro", slug="evo-%d" % next(_SEQ))
    db_session.add(p); db_session.commit()
    return p


@pytest.fixture()
def brand(db_session, platform):
    b = BrandSalesOrg(platform_id=platform.id, name="EvoSys Pro Sales",
                      slug="evo-sales-%d" % next(_SEQ))
    db_session.add(b); db_session.commit()
    return b


@pytest.fixture()
def starter(db_session, platform):
    p = BrandPackage(platform_id=platform.id, name="Starter",
                     key="starter-%d" % next(_SEQ),
                     price=Decimal("1497.00"), setup_fee=Decimal("1497.00"),
                     monthly_price=Decimal("597.00"),
                     contract_monthly_price=Decimal("500.00"),
                     contract_term_months=13, currency="USD")
    db_session.add(p); db_session.commit()
    return p


def _user(db, name="Person"):
    u = User(organization_id=None, email="u%d@evosyspro.live" % next(_SEQ),
             password_hash=hash_password("x"), full_name=name,
             role="advisor", must_change_password=False)
    db.add(u); db.commit()
    return u


@pytest.fixture()
def manager(db_session, brand):
    u = _user(db_session, "Manager")
    db_session.add(Membership(user_id=u.id, scope_type=SCOPE_BRAND_SALES_ORG,
                              scope_id=brand.id, role=ROLE_SALES_MANAGER,
                              is_active=True))
    db_session.commit()
    return u


@pytest.fixture()
def rep(db_session, brand, manager):
    u = _user(db_session, "Rep")
    db_session.add(Membership(user_id=u.id, scope_type=SCOPE_BRAND_SALES_ORG,
                              scope_id=brand.id, role=ROLE_SALES_REP,
                              is_active=True, reports_to_user_id=manager.id))
    db_session.commit()
    return u


@pytest.fixture()
def lone_rep(db_session, brand):
    """A rep who reports to NOBODY. There is no override to pay."""
    u = _user(db_session, "Lone Rep")
    db_session.add(Membership(user_id=u.id, scope_type=SCOPE_BRAND_SALES_ORG,
                              scope_id=brand.id, role=ROLE_SALES_REP,
                              is_active=True))
    db_session.commit()
    return u


def _opp(db, brand, owner, pkg, **kw):
    o = Opportunity(brand_sales_org_id=brand.id, owner_user_id=owner.id,
                    company_name=kw.pop("company_name", "Acme Memorial"),
                    selected_package_id=pkg.id if pkg else None,
                    stage=kw.pop("stage", "closing"),
                    status=kw.pop("status", "open"), **kw)
    db.add(o); db.commit()
    return o


@pytest.fixture()
def evosys_plan(db_session, brand, starter):
    """Mike's stated direct-sales structure, as configuration."""
    plan = CompensationPlan(brand_sales_org_id=brand.id, name="EvoSys Direct",
                            effective_from=date(2026, 1, 1), holdback_days=14,
                            max_override_levels=2, is_active=True)
    db_session.add(plan); db_session.commit()
    db_session.add(CompensationRule(
        plan_id=plan.id, package_id=starter.id, payee_kind=PAYEE_SELLER,
        basis=BASIS_FIXED, amount=Decimal("500.00"), sort_order=1))
    db_session.add(CompensationRule(
        plan_id=plan.id, package_id=starter.id, payee_kind=PAYEE_OVERRIDE,
        override_level=1, basis=BASIS_FIXED, amount=Decimal("100.00"),
        sort_order=2))
    db_session.add(CompensationPackageCap(
        plan_id=plan.id, package_id=starter.id,
        max_total_payout=Decimal("800.00")))
    db_session.commit()
    return plan


# ═════════════════════════════════════════════════════════════════════════════
# 1. The deal economics the engine multiplies — $1,497 + ($500 × 13) = $7,997
# ═════════════════════════════════════════════════════════════════════════════

def test_a_fixed_term_deal_totals_seven_nine_nine_seven(db_session, brand, rep, starter):
    o = _opp(db_session, brand, rep, starter,
             billing_option="term_agreement", contract_term_months=13)
    econ = comp.deal_economics(db_session, o)
    assert econ["implementation_fee"] == Decimal("1497.00")
    assert econ["mrr"] == Decimal("500.00")
    assert econ["term_months"] == 13
    assert econ["quote"]["recurring_contract_value"] == 6500.0
    assert econ["tcv"] == Decimal("7997.0")


def test_a_month_to_month_deal_has_no_contract_total(db_session, brand, rep, starter):
    """$1,497 + $597/mo, no term. There is no TCV to quote and inventing a
    12- or 13-month one would overstate the book."""
    o = _opp(db_session, brand, rep, starter, billing_option="month_to_month")
    econ = comp.deal_economics(db_session, o)
    assert econ["implementation_fee"] == Decimal("1497.00")
    assert econ["mrr"] == Decimal("597.00")
    assert econ["term_months"] is None
    assert econ["quote"]["recurring_contract_value"] is None


def test_a_custom_deal_is_recognised_as_custom(db_session, brand, rep, starter):
    o = _opp(db_session, brand, rep, starter, billing_option="term_agreement",
             custom_unit_price=Decimal("250.00"), custom_min_units=15,
             custom_unit_label="active paying customer", custom_term_months=12)
    econ = comp.deal_economics(db_session, o)
    assert econ["deal_kind"] == DEAL_CUSTOM
    assert econ["mrr"] == Decimal("3750.00")     # 250 x 15
    assert econ["term_months"] == 12


# ═════════════════════════════════════════════════════════════════════════════
# 2. The real EvoSys structure
# ═════════════════════════════════════════════════════════════════════════════

def test_starter_pays_the_rep_five_hundred(db_session, brand, rep, starter, evosys_plan):
    o = _opp(db_session, brand, rep, starter, billing_option="term_agreement",
             contract_term_months=13)
    r = comp.compute(db_session, o)
    seller = [p for p in r["payouts"] if p["payee_kind"] == PAYEE_SELLER]
    assert len(seller) == 1
    assert seller[0]["payee_user_id"] == rep.id
    assert seller[0]["amount"] == Decimal("500.00")


def test_the_manager_override_is_one_hundred(db_session, brand, rep, manager,
                                             starter, evosys_plan):
    o = _opp(db_session, brand, rep, starter)
    r = comp.compute(db_session, o)
    over = [p for p in r["payouts"] if p["payee_kind"] == PAYEE_OVERRIDE]
    assert len(over) == 1
    assert over[0]["payee_user_id"] == manager.id
    assert over[0]["override_level"] == 1
    assert over[0]["amount"] == Decimal("100.00")


def test_the_total_is_six_hundred_and_inside_the_cap(db_session, brand, rep,
                                                     starter, evosys_plan):
    o = _opp(db_session, brand, rep, starter)
    r = comp.compute(db_session, o)
    assert r["total"] == Decimal("600.00")
    assert r["capped"] is False
    assert r["cap_amount"] == Decimal("800.00")


def test_no_phantom_override_when_nobody_is_above_the_rep(
        db_session, brand, lone_rep, starter, evosys_plan):
    """A level with nobody in it must pay nobody. A phantom override is expense
    the business does not owe, and it would still show up in projected payroll."""
    o = _opp(db_session, brand, lone_rep, starter)
    r = comp.compute(db_session, o)
    assert [p["payee_kind"] for p in r["payouts"]] == [PAYEE_SELLER]
    assert r["override_total"] == Decimal("0")
    assert r["total"] == Decimal("500.00")


def test_the_package_cap_binds_across_everyone(db_session, brand, rep, manager,
                                               starter, evosys_plan):
    """Raise the rep to $900 with a $100 override: $1,000 asked, $800 allowed.
    The cap is a property of the DEAL, not of one cheque."""
    rule = (db_session.query(CompensationRule)
            .filter(CompensationRule.plan_id == evosys_plan.id,
                    CompensationRule.payee_kind == PAYEE_SELLER).one())
    rule.amount = Decimal("900.00")
    db_session.commit()

    o = _opp(db_session, brand, rep, starter)
    r = comp.compute(db_session, o)
    assert r["capped"] is True
    assert r["total"] == Decimal("800.00")
    # Reduced proportionally, so no single payee absorbs the whole shortfall and
    # the result does not depend on the order the rules happen to be in.
    assert sum(p["amount"] for p in r["payouts"]) == Decimal("800.00")
    assert all(p["capped_from_amount"] > p["amount"] for p in r["payouts"])


def test_an_override_level_nobody_occupies_is_skipped(db_session, brand, rep,
                                                      starter, evosys_plan):
    """A level-2 rule with no second-line manager pays nothing, and does not
    fall back to paying the level-1 manager twice."""
    db_session.add(CompensationRule(
        plan_id=evosys_plan.id, package_id=starter.id, payee_kind=PAYEE_OVERRIDE,
        override_level=2, basis=BASIS_FIXED, amount=Decimal("50.00"),
        sort_order=3))
    db_session.commit()
    o = _opp(db_session, brand, rep, starter)
    r = comp.compute(db_session, o)
    levels = sorted(p["override_level"] for p in r["payouts"]
                    if p["payee_kind"] == PAYEE_OVERRIDE)
    assert levels == [1]


def test_a_second_upline_level_is_paid_when_it_exists(db_session, brand, rep,
                                                      manager, starter, evosys_plan):
    director = _user(db_session, "Director")
    db_session.add(Membership(user_id=director.id, scope_type=SCOPE_BRAND_SALES_ORG,
                              scope_id=brand.id, role=ROLE_SALES_MANAGER,
                              is_active=True))
    mgr_m = (db_session.query(Membership)
             .filter(Membership.user_id == manager.id).one())
    mgr_m.reports_to_user_id = director.id
    db_session.add(CompensationRule(
        plan_id=evosys_plan.id, package_id=starter.id, payee_kind=PAYEE_OVERRIDE,
        override_level=2, basis=BASIS_FIXED, amount=Decimal("50.00"), sort_order=3))
    db_session.commit()

    o = _opp(db_session, brand, rep, starter)
    r = comp.compute(db_session, o)
    by_level = {p["override_level"]: p for p in r["payouts"]
                if p["payee_kind"] == PAYEE_OVERRIDE}
    assert by_level[2]["payee_user_id"] == director.id
    assert r["total"] == Decimal("650.00")


# ═════════════════════════════════════════════════════════════════════════════
# 3. The multi-tenant SaaS structure — 10% of collected payments
# ═════════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def saas_plan(db_session, brand):
    plan = CompensationPlan(brand_sales_org_id=brand.id, name="Multi-Tenant SaaS",
                            effective_from=date(2026, 1, 1), is_active=True)
    db_session.add(plan); db_session.commit()
    db_session.add(CompensationRule(
        plan_id=plan.id, payee_kind=PAYEE_SELLER, basis=BASIS_PCT_COLLECTED,
        percent=Decimal("10.000"), sort_order=1))
    db_session.commit()
    return plan


def test_a_collected_percentage_declines_to_guess_while_projecting(
        db_session, brand, rep, starter, saas_plan):
    """Nothing has been collected on an open deal. Reporting 10% of an imagined
    payment would be a forecast of money nobody has sent."""
    o = _opp(db_session, brand, rep, starter)
    r = comp.compute(db_session, o)
    assert r["payouts"] == []


def test_a_collected_percentage_pays_ten_percent_of_real_money(
        db_session, brand, rep, starter, saas_plan):
    o = _opp(db_session, brand, rep, starter)
    r = comp.compute(db_session, o, collected_amount=Decimal("1497.00"))
    assert r["total"] == Decimal("149.70")


def test_percent_of_mrr_pays_across_the_committed_term(db_session, brand, rep,
                                                       starter):
    plan = CompensationPlan(brand_sales_org_id=brand.id, name="MRR plan",
                            effective_from=date(2026, 1, 1), is_active=True)
    db_session.add(plan); db_session.commit()
    db_session.add(CompensationRule(plan_id=plan.id, payee_kind=PAYEE_SELLER,
                                    basis=BASIS_PCT_MRR, percent=Decimal("10"),
                                    sort_order=1))
    db_session.commit()
    o = _opp(db_session, brand, rep, starter, billing_option="term_agreement",
             contract_term_months=13)
    r = comp.compute(db_session, o)
    # 10% of $500 across 13 months.
    assert r["total"] == Decimal("650.00")


def test_percent_of_mrr_on_month_to_month_pays_one_month_not_a_pretend_year(
        db_session, brand, rep, starter):
    """A month-to-month deal has no committed term. Paying on twelve months
    would invent a contract length - the same refusal package_pricing makes."""
    plan = CompensationPlan(brand_sales_org_id=brand.id, name="MRR plan",
                            effective_from=date(2026, 1, 1), is_active=True)
    db_session.add(plan); db_session.commit()
    db_session.add(CompensationRule(plan_id=plan.id, payee_kind=PAYEE_SELLER,
                                    basis=BASIS_PCT_MRR, percent=Decimal("10"),
                                    sort_order=1))
    db_session.commit()
    o = _opp(db_session, brand, rep, starter, billing_option="month_to_month")
    r = comp.compute(db_session, o)
    assert r["total"] == Decimal("59.70")     # 10% of one month of $597


# ═════════════════════════════════════════════════════════════════════════════
# 4. Unconfigured, and custom deals awaiting approval
# ═════════════════════════════════════════════════════════════════════════════

def test_no_plan_reports_unconfigured_not_zero(db_session, brand, rep, starter):
    """A confident $0 looks like a decision. "No plan configured" is the truth."""
    o = _opp(db_session, brand, rep, starter)
    r = comp.compute(db_session, o)
    assert r["status"] == PROJ_UNCONFIGURED
    assert r["total"] is None


def test_an_unconfigured_package_rate_pays_nothing_rather_than_guessing(
        db_session, brand, rep, platform, evosys_plan):
    """Growth has no rule. It must not inherit Starter's $500."""
    growth = BrandPackage(platform_id=platform.id, name="Growth",
                          key="growth-%d" % next(_SEQ),
                          price=Decimal("2495.00"), currency="USD")
    db_session.add(growth); db_session.commit()
    o = _opp(db_session, brand, rep, growth)
    r = comp.compute(db_session, o)
    assert r["payouts"] == []
    assert r["total"] == Decimal("0")


def test_a_deal_awaiting_pricing_approval_is_marked_pending(
        db_session, brand, rep, starter, evosys_plan):
    o = _opp(db_session, brand, rep, starter)
    db_session.add(PricingApprovalRequest(
        brand_sales_org_id=brand.id, opportunity_id=o.id, requested_by=rep.id,
        reason="Negotiated below floor", status=APPROVAL_PENDING,
        request_kind="custom_deal"))
    db_session.commit()
    r = comp.compute(db_session, o)
    assert r["status"] == PROJ_PENDING_APPROVAL


# ═════════════════════════════════════════════════════════════════════════════
# 5. EARNED — Won is not enough; collected funds are
# ═════════════════════════════════════════════════════════════════════════════

def test_an_open_deal_cannot_be_earned(db_session, brand, rep, starter, evosys_plan):
    o = _opp(db_session, brand, rep, starter, status="open")
    with pytest.raises(comp.NotEarnable):
        comp.earn(db_session, o, collection_reference="pi_1",
                  collected_amount=Decimal("1497"))


def test_won_alone_does_not_pay_anybody(db_session, brand, rep, starter, evosys_plan):
    """THE RULE: we pay on collected funds. A stage change is not money."""
    o = _opp(db_session, brand, rep, starter, status="won")
    with pytest.raises(comp.NotEarnable):
        comp.earn(db_session, o, collection_reference="", collected_amount=Decimal("1497"))
    assert db_session.query(CompensationEntry).count() == 0


def test_a_zero_collection_earns_nothing(db_session, brand, rep, starter, evosys_plan):
    o = _opp(db_session, brand, rep, starter, status="won")
    with pytest.raises(comp.NotEarnable):
        comp.earn(db_session, o, collection_reference="pi_1",
                  collected_amount=Decimal("0"))


def test_won_plus_collected_funds_creates_entries(db_session, brand, rep, manager,
                                                  starter, evosys_plan):
    o = _opp(db_session, brand, rep, starter, status="won")
    made = comp.earn(db_session, o, collection_reference="pi_abc",
                     collected_amount=Decimal("1497.00"))
    assert len(made) == 2
    assert {e.state for e in made} == {COMP_EARNED}
    assert {e.payee_user_id for e in made} == {rep.id, manager.id}


def test_earning_twice_on_the_same_payment_does_not_pay_twice(
        db_session, brand, rep, starter, evosys_plan):
    o = _opp(db_session, brand, rep, starter, status="won")
    comp.earn(db_session, o, collection_reference="pi_abc",
              collected_amount=Decimal("1497.00"))
    comp.earn(db_session, o, collection_reference="pi_abc",
              collected_amount=Decimal("1497.00"))
    assert db_session.query(CompensationEntry).count() == 2


def test_an_entry_freezes_the_rate_that_produced_it(db_session, brand, rep,
                                                    starter, evosys_plan):
    """Repricing a plan next quarter must EXPLAIN last quarter's payout, not
    rewrite it."""
    o = _opp(db_session, brand, rep, starter, status="won")
    comp.earn(db_session, o, collection_reference="pi_abc",
              collected_amount=Decimal("1497.00"))
    entry = (db_session.query(CompensationEntry)
             .filter(CompensationEntry.payee_user_id == rep.id).one())
    assert entry.amount == Decimal("500.00")
    assert entry.rate_amount == Decimal("500.00")

    rule = (db_session.query(CompensationRule)
            .filter(CompensationRule.payee_kind == PAYEE_SELLER).one())
    rule.amount = Decimal("50.00")
    db_session.commit()
    db_session.refresh(entry)
    assert entry.amount == Decimal("500.00")
    assert entry.rate_amount == Decimal("500.00")


# ═════════════════════════════════════════════════════════════════════════════
# 6. EARNED is not PAYABLE — the 14-day holdback
# ═════════════════════════════════════════════════════════════════════════════

def test_payable_is_two_weeks_after_collection(db_session, brand, rep, starter,
                                               evosys_plan):
    collected = datetime(2026, 3, 1, 12, 0)
    o = _opp(db_session, brand, rep, starter, status="won")
    made = comp.earn(db_session, o, collection_reference="pi_abc",
                     collected_amount=Decimal("1497.00"), collected_at=collected)
    assert made[0].payable_at == collected + timedelta(days=14)


def test_an_entry_inside_the_holdback_stays_earned(db_session, brand, rep,
                                                   starter, evosys_plan):
    o = _opp(db_session, brand, rep, starter, status="won")
    comp.earn(db_session, o, collection_reference="pi_abc",
              collected_amount=Decimal("1497.00"), collected_at=datetime.utcnow())
    moved = comp.promote_due_to_payable(db_session, now=datetime.utcnow())
    assert moved == 0
    assert {e.state for e in db_session.query(CompensationEntry).all()} == {COMP_EARNED}


def test_an_entry_past_the_holdback_becomes_payable(db_session, brand, rep,
                                                    starter, evosys_plan):
    o = _opp(db_session, brand, rep, starter, status="won")
    comp.earn(db_session, o, collection_reference="pi_abc",
              collected_amount=Decimal("1497.00"),
              collected_at=datetime.utcnow() - timedelta(days=20))
    moved = comp.promote_due_to_payable(db_session, now=datetime.utcnow())
    assert moved == 2
    assert {e.state for e in db_session.query(CompensationEntry).all()} == {COMP_PAYABLE}


def test_an_earned_entry_cannot_skip_the_holdback_and_be_paid(
        db_session, brand, rep, starter, evosys_plan):
    o = _opp(db_session, brand, rep, starter, status="won")
    made = comp.earn(db_session, o, collection_reference="pi_abc",
                     collected_amount=Decimal("1497.00"))
    with pytest.raises(comp.NotEarnable):
        comp.mark_paid(db_session, made[0], payment_reference="chk_1")


def test_a_payable_entry_can_be_paid(db_session, brand, rep, starter, evosys_plan):
    o = _opp(db_session, brand, rep, starter, status="won")
    comp.earn(db_session, o, collection_reference="pi_abc",
              collected_amount=Decimal("1497.00"),
              collected_at=datetime.utcnow() - timedelta(days=20))
    comp.promote_due_to_payable(db_session)
    entry = db_session.query(CompensationEntry).first()
    comp.mark_paid(db_session, entry, payment_reference="chk_991")
    assert entry.state == COMP_PAID
    assert entry.payment_reference == "chk_991"


# ═════════════════════════════════════════════════════════════════════════════
# 7. The pipeline projection
# ═════════════════════════════════════════════════════════════════════════════

def test_the_projection_writes_nothing(db_session, brand, rep, starter, evosys_plan):
    o = _opp(db_session, brand, rep, starter, billing_option="term_agreement",
             contract_term_months=13)
    proj.project(db_session, [o], brand_sales_org_id=brand.id)
    assert db_session.query(CompensationEntry).count() == 0


def test_the_projection_totals_the_deal_and_its_compensation(
        db_session, brand, rep, starter, evosys_plan):
    o = _opp(db_session, brand, rep, starter, billing_option="term_agreement",
             contract_term_months=13)
    r = proj.project(db_session, [o], brand_sales_org_id=brand.id)
    assert r["basis"] == "projected"
    assert r["pipeline_value"] == 7997.0
    assert r["projected_direct_commissions"] == 500.0
    assert r["projected_manager_overrides"] == 100.0
    assert r["projected_total_compensation"] == 600.0
    assert r["projected_revenue_after_compensation"] == 7397.0


def test_a_month_to_month_deal_contributes_only_its_setup_fee(
        db_session, brand, rep, starter, evosys_plan):
    """No committed total means nothing to book beyond the one-time fee."""
    o = _opp(db_session, brand, rep, starter, billing_option="month_to_month")
    r = proj.project(db_session, [o], brand_sales_org_id=brand.id)
    assert r["pipeline_value"] == 1497.0


def test_weighted_revenue_is_unavailable_until_probabilities_are_configured(
        db_session, brand, rep, starter, evosys_plan):
    o = _opp(db_session, brand, rep, starter, billing_option="term_agreement",
             contract_term_months=13)
    r = proj.project(db_session, [o], brand_sales_org_id=brand.id)
    assert r["weighted_available"] is False
    assert r["weighted_pipeline_value"] is None


def test_weighted_revenue_uses_configured_probabilities(db_session, brand, rep,
                                                        starter, evosys_plan):
    db_session.add(StageProbability(brand_sales_org_id=brand.id, stage="closing",
                                    probability_pct=Decimal("50")))
    db_session.commit()
    o = _opp(db_session, brand, rep, starter, stage="closing",
             billing_option="term_agreement", contract_term_months=13)
    r = proj.project(db_session, [o], brand_sales_org_id=brand.id)
    assert r["weighted_pipeline_value"] == 3998.5


def test_a_pending_approval_deal_is_held_out_of_the_headline(
        db_session, brand, rep, starter, evosys_plan):
    """An unapproved deal is not a promise. If it counted, a rep could move the
    company's projected payroll by typing a number."""
    o = _opp(db_session, brand, rep, starter)
    db_session.add(PricingApprovalRequest(
        brand_sales_org_id=brand.id, opportunity_id=o.id, requested_by=rep.id,
        reason="below floor", status=APPROVAL_PENDING, request_kind="custom_deal"))
    db_session.commit()
    r = proj.project(db_session, [o], brand_sales_org_id=brand.id)
    assert r["projected_total_compensation"] == 0.0
    assert r["pending_approval_compensation"] == 600.0
    assert r["pending_approval_deal_count"] == 1


def test_the_projection_says_when_no_plan_is_configured(db_session, brand, rep,
                                                        starter):
    o = _opp(db_session, brand, rep, starter)
    r = proj.project(db_session, [o], brand_sales_org_id=brand.id)
    assert r["compensation_plan_configured"] is False


def test_custom_pricing_flows_straight_into_the_projection(db_session, brand, rep,
                                                           starter, evosys_plan):
    """A custom deal must not need its own commission path — it already feeds
    the quote, so it already feeds compensation."""
    o = _opp(db_session, brand, rep, starter, billing_option="term_agreement",
             custom_unit_price=Decimal("250.00"), custom_min_units=15,
             custom_term_months=12, implementation_fee=Decimal("2000.00"))
    r = proj.project(db_session, [o], brand_sales_org_id=brand.id,
                     include_deals=True)
    # 250 x 15 = 3750/mo x 12 = 45,000 + 2,000 setup
    assert r["pipeline_value"] == 47000.0
    assert r["deals"][0]["deal_kind"] == DEAL_CUSTOM
