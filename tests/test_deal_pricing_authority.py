"""PRICING FLOORS — how far off catalogue a person may go, per component.

THE ONE THING THIS FILE DEFENDS

A deal that guts the recurring rate and rescues the total by inflating the
one-time fee must still be caught. MRR is what the business is built on; a
healthy TCV sitting on a destroyed MRR is a worse deal wearing a better number.
So setup and monthly are fenced separately and a breach of either is a breach,
whatever the total does.

Second: with NO policy configured, authority must be exactly what it was before
this engine existed — a rep discounts nothing, a manager is unbounded. Installing
a guardrail that silently hands every rep a discount they never had would be a
worse defect than having no guardrail.
"""

import itertools
from decimal import Decimal

import pytest

from app.models.pricing_policy_models import PricingPolicy
from app.models.sales_models import (ROLE_SALES_MANAGER, ROLE_SALES_REP,
                                     SCOPE_BRAND_SALES_ORG, BrandPackage,
                                     BrandSalesOrg, Membership)
from app.models.models import Platform, User
from app.services import pricing_authority as pa
from app.services.auth_service import hash_password

_SEQ = itertools.count(1)


@pytest.fixture()
def platform(db_session):
    p = Platform(name="EvoSys Pro", slug="evosyspro-%d" % next(_SEQ))
    db_session.add(p)
    db_session.commit()
    return p


@pytest.fixture()
def brand(db_session, platform):
    b = BrandSalesOrg(platform_id=platform.id, name="EvoSys Pro Sales",
                      slug="evosys-%d" % next(_SEQ))
    db_session.add(b)
    db_session.commit()
    return b


@pytest.fixture()
def starter(db_session, platform):
    """The real shape of the Starter package: $1,497 one-time, $597/mo
    month-to-month, $500/mo on a 13-month agreement."""
    p = BrandPackage(platform_id=platform.id, name="Starter",
                     key="starter-%d" % next(_SEQ),
                     price=Decimal("1497.00"), setup_fee=Decimal("1497.00"),
                     monthly_price=Decimal("597.00"),
                     contract_monthly_price=Decimal("500.00"),
                     contract_term_months=13, currency="USD")
    db_session.add(p)
    db_session.commit()
    return p


def _member(db, brand, role, email=None):
    u = User(organization_id=None, email=email or ("u%d@evosyspro.live" % next(_SEQ)),
             password_hash=hash_password("x"), full_name="Person",
             role="advisor", must_change_password=False)
    db.add(u)
    db.commit()
    db.add(Membership(user_id=u.id, scope_type=SCOPE_BRAND_SALES_ORG,
                      scope_id=brand.id, role=role, is_active=True))
    db.commit()
    return u


# ═════════════════════════════════════════════════════════════════════════════
# 1. Unconfigured = the behaviour that existed before this engine
# ═════════════════════════════════════════════════════════════════════════════

def test_with_no_policy_a_rep_may_not_discount_at_all(db_session, brand, starter):
    rep = _member(db_session, brand, ROLE_SALES_REP)
    r = pa.evaluate(db_session, rep, starter, brand_sales_org_id=brand.id,
                    billing_option="month_to_month", proposed_monthly=500)
    assert r["outcome"] == pa.NEEDS_APPROVAL
    assert r["ceilings"]["source"] == "fallback"


def test_with_no_policy_a_manager_is_unbounded(db_session, brand, starter):
    mgr = _member(db_session, brand, ROLE_SALES_MANAGER)
    r = pa.evaluate(db_session, mgr, starter, brand_sales_org_id=brand.id,
                    billing_option="month_to_month", proposed_monthly=1)
    assert r["outcome"] == pa.ALLOWED


def test_quoting_catalogue_exactly_is_never_a_breach(db_session, brand, starter):
    rep = _member(db_session, brand, ROLE_SALES_REP)
    r = pa.evaluate(db_session, rep, starter, brand_sales_org_id=brand.id,
                    billing_option="month_to_month",
                    proposed_setup=1497, proposed_monthly=597)
    assert r["outcome"] == pa.ALLOWED


def test_quoting_above_catalogue_is_never_a_breach(db_session, brand, starter):
    """A negative discount is not a discount. Selling ABOVE list must not
    require a manager's permission."""
    rep = _member(db_session, brand, ROLE_SALES_REP)
    r = pa.evaluate(db_session, rep, starter, brand_sales_org_id=brand.id,
                    billing_option="month_to_month", proposed_monthly=700)
    assert r["outcome"] == pa.ALLOWED


# ═════════════════════════════════════════════════════════════════════════════
# 2. A configured policy, and the components fenced apart
# ═════════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def rep_policy(db_session, brand):
    """Reps may give 10% off either component on this brand."""
    p = PricingPolicy(brand_sales_org_id=brand.id, role=ROLE_SALES_REP,
                      max_discount_pct_setup=Decimal("10"),
                      max_discount_pct_monthly=Decimal("10"),
                      is_active=True)
    db_session.add(p)
    db_session.commit()
    return p


def test_a_rep_may_discount_within_the_ceiling(db_session, brand, starter, rep_policy):
    rep = _member(db_session, brand, ROLE_SALES_REP)
    # 597 -> 550 is 7.9% off, inside 10%.
    r = pa.evaluate(db_session, rep, starter, brand_sales_org_id=brand.id,
                    billing_option="month_to_month", proposed_monthly=550)
    assert r["outcome"] == pa.ALLOWED
    assert r["ceilings"]["source"] == "policy"


def test_a_rep_past_the_ceiling_is_routed_not_refused(db_session, brand, starter, rep_policy):
    """NEEDS_APPROVAL, never REFUSED. A negotiated price is a question for a
    manager, not a thing the product throws away."""
    rep = _member(db_session, brand, ROLE_SALES_REP)
    r = pa.evaluate(db_session, rep, starter, brand_sales_org_id=brand.id,
                    billing_option="month_to_month", proposed_monthly=400)
    assert r["outcome"] == pa.NEEDS_APPROVAL
    assert r["outcome"] != pa.REFUSED
    assert "monthly" in r["summary"]


def test_a_gutted_monthly_is_caught_even_when_the_total_looks_fine(
        db_session, brand, starter, rep_policy):
    """THE TRADE THIS ENGINE EXISTS TO CATCH.

    Monthly cut to $300 (49.7% off) while the setup fee is raised to $3,000.
    Any total-only check passes this happily. The recurring rate is the
    business, so it breaches on its own terms.
    """
    rep = _member(db_session, brand, ROLE_SALES_REP)
    r = pa.evaluate(db_session, rep, starter, brand_sales_org_id=brand.id,
                    billing_option="month_to_month",
                    proposed_setup=3000, proposed_monthly=300)
    assert r["outcome"] == pa.NEEDS_APPROVAL
    breached = {b["component"] for b in r["breaches"]}
    assert pa.COMPONENT_MONTHLY in breached
    # And the inflated setup fee is NOT reported as a breach - it is not a
    # discount at all.
    assert pa.COMPONENT_SETUP not in breached


def test_setup_and_monthly_breach_independently(db_session, brand, starter, rep_policy):
    rep = _member(db_session, brand, ROLE_SALES_REP)
    r = pa.evaluate(db_session, rep, starter, brand_sales_org_id=brand.id,
                    billing_option="month_to_month",
                    proposed_setup=1000, proposed_monthly=597)
    breached = {b["component"] for b in r["breaches"]}
    assert breached == {pa.COMPONENT_SETUP}


def test_a_term_deal_is_measured_against_the_contracted_rate(
        db_session, brand, starter, rep_policy):
    """$500 IS the 13-month rate. Measuring it against the $597 month-to-month
    rate would report the catalogue's own agreement discount as a breach on
    every single term deal ever sold."""
    rep = _member(db_session, brand, ROLE_SALES_REP)
    r = pa.evaluate(db_session, rep, starter, brand_sales_org_id=brand.id,
                    billing_option="term_agreement", proposed_monthly=500)
    assert r["outcome"] == pa.ALLOWED


def test_an_untouched_component_is_not_judged(db_session, brand, starter, rep_policy):
    """Editing only the monthly rate must not re-examine a setup fee that was
    already inside its ceiling."""
    rep = _member(db_session, brand, ROLE_SALES_REP)
    r = pa.evaluate(db_session, rep, starter, brand_sales_org_id=brand.id,
                    billing_option="month_to_month", proposed_monthly=560)
    assert [c["component"] for c in r["components"]] == [pa.COMPONENT_MONTHLY]


def test_a_short_term_is_a_discount_wearing_a_commitment(db_session, brand, starter):
    """The agreement rate is EARNED by the length. Selling the rate with less of
    the length is the same giveaway by another route."""
    db_session.add(PricingPolicy(brand_sales_org_id=brand.id, role=ROLE_SALES_REP,
                                 min_term_months=12, is_active=True))
    db_session.commit()
    rep = _member(db_session, brand, ROLE_SALES_REP)
    r = pa.evaluate(db_session, rep, starter, brand_sales_org_id=brand.id,
                    billing_option="term_agreement", proposed_term_months=3)
    assert r["outcome"] == pa.NEEDS_APPROVAL
    assert any(b["component"] == pa.COMPONENT_TERM for b in r["breaches"])


# ═════════════════════════════════════════════════════════════════════════════
# 3. Resolution order and scope isolation
# ═════════════════════════════════════════════════════════════════════════════

def test_a_role_specific_policy_beats_a_brand_wide_one(db_session, brand, starter):
    db_session.add(PricingPolicy(brand_sales_org_id=brand.id, role=None,
                                 max_discount_pct_monthly=Decimal("50"),
                                 is_active=True))
    db_session.add(PricingPolicy(brand_sales_org_id=brand.id, role=ROLE_SALES_REP,
                                 max_discount_pct_monthly=Decimal("5"),
                                 is_active=True))
    db_session.commit()
    rep = _member(db_session, brand, ROLE_SALES_REP)
    r = pa.evaluate(db_session, rep, starter, brand_sales_org_id=brand.id,
                    billing_option="month_to_month", proposed_monthly=500)
    # 16% off. Inside the brand-wide 50%, past the rep-specific 5%.
    assert r["ceilings"]["max_discount_pct_monthly"] == 5.0
    assert r["outcome"] == pa.NEEDS_APPROVAL


def test_a_brand_policy_beats_the_platform_default(db_session, brand, starter):
    db_session.add(PricingPolicy(brand_sales_org_id=None, role=ROLE_SALES_REP,
                                 max_discount_pct_monthly=Decimal("2"),
                                 is_active=True))
    db_session.add(PricingPolicy(brand_sales_org_id=brand.id, role=ROLE_SALES_REP,
                                 max_discount_pct_monthly=Decimal("25"),
                                 is_active=True))
    db_session.commit()
    rep = _member(db_session, brand, ROLE_SALES_REP)
    r = pa.evaluate(db_session, rep, starter, brand_sales_org_id=brand.id,
                    billing_option="month_to_month", proposed_monthly=500)
    assert r["ceilings"]["max_discount_pct_monthly"] == 25.0
    assert r["outcome"] == pa.ALLOWED


def test_one_brands_policy_does_not_govern_another(db_session, brand, starter):
    """Never leak a commercial policy across brands. A generous ceiling set for
    one white-label company must not widen anybody else's authority."""
    other_platform = Platform(name="Other", slug="other-plt-%d" % next(_SEQ))
    db_session.add(other_platform)
    db_session.commit()
    other = BrandSalesOrg(platform_id=other_platform.id, name="Other Brand",
                          slug="other-%d" % next(_SEQ))
    db_session.add(other)
    db_session.commit()
    db_session.add(PricingPolicy(brand_sales_org_id=other.id, role=ROLE_SALES_REP,
                                 max_discount_pct_monthly=Decimal("90"),
                                 is_active=True))
    db_session.commit()
    rep = _member(db_session, brand, ROLE_SALES_REP)
    r = pa.evaluate(db_session, rep, starter, brand_sales_org_id=brand.id,
                    billing_option="month_to_month", proposed_monthly=100)
    # Falls back to rep-discounts-nothing, NOT the other brand's 90%.
    assert r["ceilings"]["source"] == "fallback"
    assert r["outcome"] == pa.NEEDS_APPROVAL


def test_an_inactive_policy_is_ignored(db_session, brand, starter):
    db_session.add(PricingPolicy(brand_sales_org_id=brand.id, role=ROLE_SALES_REP,
                                 max_discount_pct_monthly=Decimal("90"),
                                 is_active=False))
    db_session.commit()
    rep = _member(db_session, brand, ROLE_SALES_REP)
    r = pa.evaluate(db_session, rep, starter, brand_sales_org_id=brand.id,
                    billing_option="month_to_month", proposed_monthly=100)
    assert r["ceilings"]["source"] == "fallback"


def test_a_package_with_no_catalogue_rate_has_no_discount_to_measure(
        db_session, brand, rep_policy):
    """An uncatalogued package has no reference price. Reporting 0% would claim
    a comparison that was never possible."""
    custom_pkg = BrandPackage(platform_id=brand.platform_id, name="Custom",
                              key="custom-%d" % next(_SEQ),
                              is_custom=True, currency="USD")
    db_session.add(custom_pkg)
    db_session.commit()
    rep = _member(db_session, brand, ROLE_SALES_REP)
    r = pa.evaluate(db_session, rep, custom_pkg, brand_sales_org_id=brand.id,
                    billing_option="month_to_month", proposed_monthly=250)
    assert r["outcome"] == pa.ALLOWED
    assert r["components"][0]["discount_pct"] is None


def test_a_god_admin_prices_as_a_manager(db_session, brand, starter, rep_policy):
    """Platform authority is broader than a sales manager everywhere else in
    this codebase; a policy row must not fence the owner out of their own
    pricing."""
    god = User(organization_id=None, email="owner%d@evosyspro.live" % next(_SEQ),
               password_hash=hash_password("x"), full_name="Owner",
               role="god_admin", must_change_password=False)
    db_session.add(god)
    db_session.commit()
    r = pa.evaluate(db_session, god, starter, brand_sales_org_id=brand.id,
                    billing_option="month_to_month", proposed_monthly=1)
    assert r["role"] == ROLE_SALES_MANAGER
    assert r["outcome"] == pa.ALLOWED
