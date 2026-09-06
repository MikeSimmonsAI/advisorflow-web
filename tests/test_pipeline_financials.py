"""WHAT THE PIPELINE IS WORTH, AND WHY IT IS NOT ONE NUMBER.

THE DEFECT THIS FILE PINS DOWN

Production reported $75,800 of implementation value and $0 of recurring value
across the open pipeline. The arithmetic was right; the reading was wrong.

`quote()` correctly refuses to give a month-to-month deal a contract total —
there is no agreed number of months to multiply — and the rollup had nowhere
else to put that deal's monthly rate. Month-to-month is also the DEFAULT
billing option, because `normalize_option` fails closed to it rather than
putting a customer under a term nobody signed. Between those two correct
decisions, the recurring half of most of the pipeline was invisible: a deal at
$597/month contributed its setup fee and nothing else, and the headline
"pipeline value" was in practice the sum of the setup fees.

Two smaller holes sat beside it. An opportunity with no package quoted nothing
at all, so older deals carrying only `deal_value` contributed zero to every
figure while still being counted in the deal count. And a sent proposal — the
document the customer is actually holding — was never consulted.

WHAT IS ASSERTED HERE
  - a fixed-term deal contributes setup AND recurring contract value,
  - $1,497 + $500 x 13 = $7,997, the worked example,
  - a month-to-month deal contributes an MRR and NO invented total,
  - a legacy deal resolves safely and is FLAGGED, never counted as $0 recurring,
  - a sent proposal's snapshot governs the deal it was sent for,
  - the totals reconcile exactly to the per-deal rows,
  - tenancy holds,
  - and a legacy `deal_value` never reaches payroll as an implementation fee.
"""

import itertools
from datetime import date, datetime
from decimal import Decimal

import pytest

from app.models.compensation_models import (BASIS_FIXED, BASIS_PCT_SETUP,
                                            PAYEE_SELLER, CompensationPlan,
                                            CompensationRule)
from app.models.models import Platform, Proposal, User
from app.models.sales_models import (ROLE_SALES_REP, SCOPE_BRAND_SALES_ORG,
                                     BrandPackage, BrandSalesOrg, Membership,
                                     Opportunity)
from app.services import compensation as comp
from app.services import deal_pricing as dp
from app.services import pipeline_projection as proj
from app.services.auth_service import hash_password

_SEQ = itertools.count(1)


# ═════════════════════════════════════════════════════════════════════════════
# Fixtures
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
def other_brand(db_session, platform):
    b = BrandSalesOrg(platform_id=platform.id, name="Other Sales",
                      slug="other-sales-%d" % next(_SEQ))
    db_session.add(b); db_session.commit()
    return b


@pytest.fixture()
def starter(db_session, platform):
    """$1,497 setup, $597/mo regular, $500/mo on a 13-month agreement."""
    p = BrandPackage(platform_id=platform.id, name="Starter",
                     key="starter-%d" % next(_SEQ),
                     price=Decimal("1497.00"), setup_fee=Decimal("1497.00"),
                     monthly_price=Decimal("597.00"),
                     contract_monthly_price=Decimal("500.00"),
                     contract_term_months=13, currency="USD")
    db_session.add(p); db_session.commit()
    return p


@pytest.fixture()
def onetime_pkg(db_session, platform):
    """A package with NO recurring rate. One-time by decision, not by omission."""
    p = BrandPackage(platform_id=platform.id, name="Build Only",
                     key="build-%d" % next(_SEQ),
                     price=Decimal("2495.00"), setup_fee=Decimal("2495.00"),
                     currency="USD")
    db_session.add(p); db_session.commit()
    return p


def _user(db, name="Person"):
    u = User(organization_id=None, email="u%d@evosyspro.live" % next(_SEQ),
             password_hash=hash_password("x"), full_name=name,
             role="advisor", must_change_password=False)
    db.add(u); db.commit()
    return u


@pytest.fixture()
def rep(db_session, brand):
    u = _user(db_session, "Rep")
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


def _proposal(db, opp, creator, **kw):
    p = Proposal(opportunity_id=opp.id, brand_sales_org_id=opp.brand_sales_org_id,
                 created_by_id=creator.id,
                 title=kw.pop("title", "Proposal"),
                 proposal_number=kw.pop("proposal_number", "EV-%d" % next(_SEQ)),
                 version=kw.pop("version", 1),
                 status="published", **kw)
    db.add(p); db.commit()
    return p


# ═════════════════════════════════════════════════════════════════════════════
# 1 & 2. A fixed-term deal contributes setup AND recurring contract value
# ═════════════════════════════════════════════════════════════════════════════

def test_fixed_term_deal_resolves_setup_mrr_term_rcv_and_tcv(
        db_session, brand, rep, starter):
    """The worked example, end to end: 1497 + (500 x 13) = 7997."""
    o = _opp(db_session, brand, rep, starter,
             billing_option="term_agreement", contract_term_months=13)
    res = dp.resolve(db_session, o)

    assert res["source"] == dp.SOURCE_PACKAGE
    assert res["structure"] == dp.STRUCTURE_TERM
    assert res["implementation_fee"] == Decimal("1497.00")
    assert res["mrr"] == Decimal("500.00")
    assert res["term_months"] == 13
    assert res["recurring_contract_value"] == Decimal("6500.00")
    assert res["total_contract_value"] == Decimal("7997.00")
    assert res["pricing_complete"] is True


def test_fixed_term_deal_contributes_both_components_to_the_pipeline(
        db_session, brand, rep, starter):
    o = _opp(db_session, brand, rep, starter,
             billing_option="term_agreement", contract_term_months=13)
    r = proj.project(db_session, [o], brand_sales_org_id=brand.id)

    assert r["pipeline_implementation"] == 1497.0
    assert r["pipeline_recurring_contract_value"] == 6500.0
    assert r["pipeline_total_fixed_contract_value"] == 7997.0
    # THE DEFECT, NAMED: 1,497 was being reported as the whole of this deal.
    assert r["pipeline_total_fixed_contract_value"] != r["pipeline_implementation"]
    assert r["fixed_term_deal_count"] == 1
    assert r["pricing_incomplete_count"] == 0


# ═════════════════════════════════════════════════════════════════════════════
# 3. Month-to-month: a real MRR, and no invented contract total
# ═════════════════════════════════════════════════════════════════════════════

def test_month_to_month_reports_mrr_and_no_fixed_recurring_value(
        db_session, brand, rep, starter):
    o = _opp(db_session, brand, rep, starter, billing_option="month_to_month")
    res = dp.resolve(db_session, o)

    assert res["structure"] == dp.STRUCTURE_M2M
    assert res["mrr"] == Decimal("597.00")
    assert res["term_months"] is None
    # No commitment exists, so there is no total to state.
    assert res["recurring_contract_value"] is None
    assert res["total_contract_value"] is None
    # But this is NOT incomplete pricing. The terms are known; they are simply
    # open-ended, which is a different thing from unknown.
    assert res["pricing_complete"] is True


def test_month_to_month_mrr_is_reported_per_month_never_multiplied(
        db_session, brand, rep, starter):
    o = _opp(db_session, brand, rep, starter, billing_option="month_to_month")
    r = proj.project(db_session, [o], brand_sales_org_id=brand.id)

    assert r["pipeline_monthly_recurring"] == 597.0
    assert r["month_to_month_deal_count"] == 1
    # No fabricated 12 or 13 months of revenue anywhere in the payload.
    assert r["pipeline_recurring_contract_value"] == 0
    assert r["pipeline_total_fixed_contract_value"] == 1497.0
    for bad in (597 * 12, 597 * 13):
        assert r["pipeline_recurring_contract_value"] != bad
        assert r["pipeline_total_fixed_contract_value"] != 1497 + bad


def test_a_month_to_month_deal_is_no_longer_invisible(
        db_session, brand, rep, starter):
    """THE REGRESSION GUARD for the reported symptom.

    Before this change the whole payload for this deal was $1,497 of
    implementation and $0 of recurring, with the $597/month appearing nowhere.
    """
    o = _opp(db_session, brand, rep, starter, billing_option="month_to_month")
    r = proj.project(db_session, [o], brand_sales_org_id=brand.id,
                     include_deals=True)
    assert r["pipeline_monthly_recurring"] > 0
    assert r["deals"][0]["mrr"] == 597.0
    assert r["deals"][0]["structure"] == dp.STRUCTURE_M2M


# ═════════════════════════════════════════════════════════════════════════════
# 4. Legacy opportunities resolve safely and are FLAGGED, never zeroed
# ═════════════════════════════════════════════════════════════════════════════

def test_a_legacy_package_deal_resolves_from_the_catalogue(
        db_session, brand, rep, starter):
    """No billing option was ever set on this row — the field predates it.

    It must not fail, and it must not be treated as a term agreement. It falls
    closed to month-to-month and reports the catalogue's regular rate.
    """
    o = _opp(db_session, brand, rep, starter, billing_option=None)
    res = dp.resolve(db_session, o)
    assert res["source"] == dp.SOURCE_PACKAGE
    assert res["structure"] == dp.STRUCTURE_M2M
    assert res["implementation_fee"] == Decimal("1497.00")
    assert res["mrr"] == Decimal("597.00")
    assert res["recurring_contract_value"] is None
    assert res["pricing_complete"] is True


def test_a_one_time_package_is_complete_not_incomplete(
        db_session, brand, rep, onetime_pkg):
    """A package with no monthly rate is a DECISION, not a gap."""
    o = _opp(db_session, brand, rep, onetime_pkg)
    res = dp.resolve(db_session, o)
    assert res["structure"] == dp.STRUCTURE_ONE_TIME
    assert res["implementation_fee"] == Decimal("2495.00")
    assert res["mrr"] is None
    assert res["pricing_complete"] is True


def test_an_opportunity_with_only_deal_value_is_flagged_not_zeroed(
        db_session, brand, rep):
    o = _opp(db_session, brand, rep, None, deal_value=Decimal("9000.00"))
    res = dp.resolve(db_session, o)

    assert res["source"] == dp.SOURCE_LEGACY
    assert res["structure"] == dp.STRUCTURE_UNKNOWN
    assert res["legacy_one_time_value"] == Decimal("9000.00")
    assert res["pricing_complete"] is False
    assert res["incomplete_reason"]
    # Its recurring value is UNKNOWN. Reporting 0 would be a claim nobody can
    # support; the field stays None and the deal is counted as incomplete.
    assert res["recurring_contract_value"] is None
    assert res["mrr"] is None


def test_legacy_value_counts_as_one_time_and_raises_the_incomplete_count(
        db_session, brand, rep):
    o = _opp(db_session, brand, rep, None, deal_value=Decimal("9000.00"))
    r = proj.project(db_session, [o], brand_sales_org_id=brand.id,
                     include_deals=True)

    assert r["pipeline_implementation"] == 9000.0
    assert r["pipeline_recurring_contract_value"] == 0
    assert r["pricing_incomplete_count"] == 1
    row = r["deals"][0]
    assert row["pricing_complete"] is False
    assert row["legacy_one_time_value"] == 9000.0
    # It is NOT an implementation fee, and must never be reported as one.
    assert row["implementation_fee"] is None


def test_an_opportunity_with_no_pricing_at_all_contributes_nothing_and_says_so(
        db_session, brand, rep):
    o = _opp(db_session, brand, rep, None)
    r = proj.project(db_session, [o], brand_sales_org_id=brand.id)
    assert r["opportunity_count"] == 1
    assert r["pipeline_implementation"] == 0
    assert r["pricing_incomplete_count"] == 1


# ═════════════════════════════════════════════════════════════════════════════
# 5. A sent proposal is authoritative for the deal it was sent for
# ═════════════════════════════════════════════════════════════════════════════

def test_a_sent_proposal_snapshot_supplies_the_pricing(
        db_session, brand, rep, starter):
    """The customer is holding a 13-month agreement. The opportunity row still
    says month-to-month. The document wins."""
    o = _opp(db_session, brand, rep, starter, billing_option="month_to_month")
    _proposal(db_session, o, rep, package_id=starter.id,
              billing_option="term_agreement", contract_term_months=13,
              sent_at=datetime(2026, 8, 1, 12, 0))

    res = dp.resolve(db_session, o)
    assert res["source"] == dp.SOURCE_PROPOSAL
    assert res["structure"] == dp.STRUCTURE_TERM
    assert res["recurring_contract_value"] == Decimal("6500.00")
    assert res["total_contract_value"] == Decimal("7997.00")
    assert res["proposal_number"]


def test_an_unsent_draft_proposal_does_not_govern_the_forecast(
        db_session, brand, rep, starter):
    """A draft is a document nobody has agreed to. If it counted, a rep could
    move the company's forecast by opening an editor."""
    o = _opp(db_session, brand, rep, starter, billing_option="month_to_month")
    _proposal(db_session, o, rep, package_id=starter.id,
              billing_option="term_agreement", contract_term_months=13)

    res = dp.resolve(db_session, o)
    assert res["source"] == dp.SOURCE_PACKAGE
    assert res["structure"] == dp.STRUCTURE_M2M


def test_a_superseded_proposal_does_not_govern_the_forecast(
        db_session, brand, rep, starter):
    o = _opp(db_session, brand, rep, starter, billing_option="month_to_month")
    _proposal(db_session, o, rep, package_id=starter.id,
              billing_option="term_agreement", contract_term_months=13,
              sent_at=datetime(2026, 8, 1, 12, 0),
              superseded_at=datetime(2026, 8, 9, 12, 0))
    res = dp.resolve(db_session, o)
    assert res["source"] == dp.SOURCE_PACKAGE


def test_a_price_withholding_proposal_does_not_govern_the_forecast(
        db_session, brand, rep, starter):
    """A proposal that deliberately quotes no price cannot be the source of a
    price."""
    o = _opp(db_session, brand, rep, starter, billing_option="term_agreement",
             contract_term_months=13)
    _proposal(db_session, o, rep, package_id=starter.id,
              withhold_pricing=True, sent_at=datetime(2026, 8, 1, 12, 0))
    res = dp.resolve(db_session, o)
    assert res["source"] == dp.SOURCE_PACKAGE
    assert res["recurring_contract_value"] == Decimal("6500.00")


def test_the_newest_sent_version_wins(db_session, brand, rep, starter):
    o = _opp(db_session, brand, rep, starter, billing_option="month_to_month")
    _proposal(db_session, o, rep, version=1, package_id=starter.id,
              billing_option="month_to_month",
              sent_at=datetime(2026, 8, 1, 12, 0))
    _proposal(db_session, o, rep, version=2, package_id=starter.id,
              billing_option="term_agreement", contract_term_months=13,
              sent_at=datetime(2026, 8, 5, 12, 0))
    res = dp.resolve(db_session, o)
    assert res["structure"] == dp.STRUCTURE_TERM
    assert res["recurring_contract_value"] == Decimal("6500.00")


def test_resolving_a_deal_does_not_modify_its_proposal(
        db_session, brand, rep, starter):
    """READ ONLY. The forecast must never rewrite a document a customer has."""
    o = _opp(db_session, brand, rep, starter, billing_option="month_to_month")
    p = _proposal(db_session, o, rep, package_id=starter.id,
                  billing_option="term_agreement", contract_term_months=13,
                  base_amount=Decimal("1497.00"),
                  final_amount=Decimal("1497.00"),
                  sent_at=datetime(2026, 8, 1, 12, 0))
    before = (p.billing_option, p.contract_term_months, p.base_amount,
              p.final_amount, p.version, p.sent_at)

    proj.project(db_session, [o], brand_sales_org_id=brand.id,
                 include_deals=True)

    db_session.refresh(p)
    assert (p.billing_option, p.contract_term_months, p.base_amount,
            p.final_amount, p.version, p.sent_at) == before


# ═════════════════════════════════════════════════════════════════════════════
# 8. Custom deals flow through
# ═════════════════════════════════════════════════════════════════════════════

def test_a_custom_deal_flows_into_the_projection(db_session, brand, rep, starter):
    """$250 per customer, 15 minimum, 12-month agreement = $3,750/mo, $45,000."""
    o = _opp(db_session, brand, rep, starter, billing_option="term_agreement",
             custom_unit_price=Decimal("250.00"), custom_min_units=15,
             custom_unit_label="active paying customer", custom_term_months=12)
    res = dp.resolve(db_session, o)
    assert res["source"] == dp.SOURCE_CUSTOM
    assert res["is_custom_rate"] is True
    assert res["mrr"] == Decimal("3750.00")
    assert res["term_months"] == 12
    assert res["recurring_contract_value"] == Decimal("45000.00")

    r = proj.project(db_session, [o], brand_sales_org_id=brand.id)
    assert r["pipeline_recurring_contract_value"] == 45000.0
    assert r["pipeline_total_fixed_contract_value"] == 46497.0


# ═════════════════════════════════════════════════════════════════════════════
# 7. The totals reconcile EXACTLY to the per-deal rows
# ═════════════════════════════════════════════════════════════════════════════

def test_totals_reconcile_to_the_individual_deal_breakdown(
        db_session, brand, rep, starter, onetime_pkg):
    """A headline nobody can reconcile to the deals behind it is how $0 of
    recurring revenue went unquestioned for as long as it did."""
    term = _opp(db_session, brand, rep, starter, company_name="Term Co",
                billing_option="term_agreement", contract_term_months=13)
    m2m = _opp(db_session, brand, rep, starter, company_name="M2M Co",
               billing_option="month_to_month")
    once = _opp(db_session, brand, rep, onetime_pkg, company_name="One Time Co")
    legacy = _opp(db_session, brand, rep, None, company_name="Legacy Co",
                  deal_value=Decimal("4000.00"))

    r = proj.project(db_session, [term, m2m, once, legacy],
                     brand_sales_org_id=brand.id, include_deals=True)
    rows = r["deals"]
    assert len(rows) == 4

    assert r["pipeline_implementation"] == sum(d["one_time_value"] or 0 for d in rows)
    assert r["pipeline_recurring_contract_value"] == sum(
        d["recurring_contract_value"] or 0 for d in rows)
    assert r["pipeline_monthly_recurring"] == sum(
        d["mrr"] or 0 for d in rows if d["structure"] == dp.STRUCTURE_M2M)
    assert r["pipeline_total_fixed_contract_value"] == sum(
        d["fixed_contract_value"] or 0 for d in rows)
    assert r["pricing_incomplete_count"] == sum(
        1 for d in rows if not d["pricing_complete"])

    # And the arithmetic in full: 1497 + 1497 + 2495 + 4000 one-time,
    # 6500 recurring, 597/month open-ended.
    assert r["pipeline_implementation"] == 9489.0
    assert r["pipeline_recurring_contract_value"] == 6500.0
    assert r["pipeline_monthly_recurring"] == 597.0
    assert r["pipeline_total_fixed_contract_value"] == 15989.0
    assert r["pricing_incomplete_count"] == 1


def test_every_deal_row_names_the_source_its_figures_came_from(
        db_session, brand, rep, starter):
    o = _opp(db_session, brand, rep, starter, billing_option="term_agreement",
             contract_term_months=13)
    r = proj.project(db_session, [o], brand_sales_org_id=brand.id,
                     include_deals=True)
    row = r["deals"][0]
    assert row["pricing_source"] == dp.SOURCE_PACKAGE
    assert row["pricing_source_label"]
    assert row["package_name"] == "Starter"


# ═════════════════════════════════════════════════════════════════════════════
# 9. Tenancy
# ═════════════════════════════════════════════════════════════════════════════

def test_the_projection_totals_only_the_opportunities_it_was_given(
        db_session, brand, other_brand, rep, starter):
    """Scoping belongs to the caller, and the rollup must not widen it.

    Passing one brand's deals must never pull in another's — the module reads
    only the iterable it is handed, and this asserts that it stays that way.
    """
    mine = _opp(db_session, brand, rep, starter, company_name="Mine",
                billing_option="term_agreement", contract_term_months=13)
    _opp(db_session, other_brand, rep, starter, company_name="Theirs",
         billing_option="term_agreement", contract_term_months=13)

    r = proj.project(db_session, [mine], brand_sales_org_id=brand.id,
                     include_deals=True)
    assert r["opportunity_count"] == 1
    assert [d["company_name"] for d in r["deals"]] == ["Mine"]
    assert r["pipeline_total_fixed_contract_value"] == 7997.0


def test_a_proposal_attached_to_another_deal_is_not_read(
        db_session, brand, rep, starter):
    """`current_proposal` filters on opportunity_id. A sibling deal's document
    must not price this one."""
    a = _opp(db_session, brand, rep, starter, company_name="A",
             billing_option="month_to_month")
    b = _opp(db_session, brand, rep, starter, company_name="B",
             billing_option="month_to_month")
    _proposal(db_session, b, rep, package_id=starter.id,
              billing_option="term_agreement", contract_term_months=13,
              sent_at=datetime(2026, 8, 1, 12, 0))

    assert dp.current_proposal(db_session, a) is None
    assert dp.resolve(db_session, a)["structure"] == dp.STRUCTURE_M2M


# ═════════════════════════════════════════════════════════════════════════════
# 10. Payroll safety — the resolver must not move what anybody earns
# ═════════════════════════════════════════════════════════════════════════════

def test_a_legacy_deal_value_never_becomes_a_commissionable_setup_fee(
        db_session, brand, rep):
    """The one rule that makes the legacy fallback safe.

    `deal_value` is a one-time figure of unknown composition. If it reached
    `implementation_fee`, a percent-of-setup rule would start paying against it
    — a commission derived from a number nobody can explain.
    """
    plan = CompensationPlan(brand_sales_org_id=brand.id, name="Pct Setup",
                            effective_from=date(2026, 1, 1), holdback_days=14,
                            max_override_levels=1, is_active=True)
    db_session.add(plan); db_session.commit()
    db_session.add(CompensationRule(plan_id=plan.id, payee_kind=PAYEE_SELLER,
                                    basis=BASIS_PCT_SETUP,
                                    percent=Decimal("10.000"), sort_order=1))
    db_session.commit()

    o = _opp(db_session, brand, rep, None, deal_value=Decimal("9000.00"))
    econ = comp.deal_economics(db_session, o)
    assert econ["implementation_fee"] is None

    c = comp.compute(db_session, o)
    # Nothing to compute against, so nothing is paid — not $900.
    assert (c["total"] or Decimal("0")) == Decimal("0")


def test_deal_economics_is_unchanged_for_an_ordinary_package_deal(
        db_session, brand, rep, starter):
    """The resolver moved WHICH inputs are chosen, not the arithmetic. A deal
    with a package and no proposal must quote exactly as it always did."""
    o = _opp(db_session, brand, rep, starter,
             billing_option="term_agreement", contract_term_months=13)
    econ = comp.deal_economics(db_session, o)
    assert econ["implementation_fee"] == Decimal("1497.00")
    assert econ["mrr"] == Decimal("500.00")
    assert econ["term_months"] == 13
    assert econ["tcv"] == Decimal("7997.00")
    assert econ["quote"]["recurring_contract_value"] == 6500.0


def test_a_fixed_commission_is_unaffected_by_the_pricing_structure(
        db_session, brand, rep, starter):
    """A $500 fixed rule pays $500 whether the deal is term or month-to-month.
    The projection change must not have moved payroll."""
    plan = CompensationPlan(brand_sales_org_id=brand.id, name="Fixed",
                            effective_from=date(2026, 1, 1), holdback_days=14,
                            max_override_levels=1, is_active=True)
    db_session.add(plan); db_session.commit()
    db_session.add(CompensationRule(plan_id=plan.id, package_id=starter.id,
                                    payee_kind=PAYEE_SELLER, basis=BASIS_FIXED,
                                    amount=Decimal("500.00"), sort_order=1))
    db_session.commit()

    term = _opp(db_session, brand, rep, starter, company_name="Term",
                billing_option="term_agreement", contract_term_months=13)
    m2m = _opp(db_session, brand, rep, starter, company_name="M2M",
               billing_option="month_to_month")
    assert comp.compute(db_session, term)["total"] == Decimal("500.00")
    assert comp.compute(db_session, m2m)["total"] == Decimal("500.00")
