"""THE PRICING & COMPENSATION CONSOLE — who may configure payroll, and what a
change must never reach.

TWO THINGS THIS FILE DEFENDS ABOVE THE REST

  1. ONLY god_admin. A sales manager runs a team and a super_admin runs a
     platform; neither sets what the company pays people or how far the margin
     may be discounted. Every route returns 403 to both.

  2. CHANGING A PLAN CANNOT CHANGE A PAST PAYOUT. An entry snapshots the rate
     that produced it. Repricing in June must explain January, not rewrite it —
     that is the difference between a compensation system and an accounting
     problem.

The seed is tested for what it does NOT do as much as what it does: it must not
invent a Growth or Professional rate, and it must not overwrite a figure
somebody set by hand.
"""

import itertools
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from app.models.compensation_models import (BASIS_FIXED, BASIS_PCT_COLLECTED,
                                            COMP_EARNED, PAYEE_OVERRIDE,
                                            PAYEE_SELLER,
                                            CompensationEntry,
                                            CompensationPackageCap,
                                            CompensationPlan, CompensationRule)
from app.models.models import AuditLogEntry, Platform, User
from app.models.pricing_policy_models import PricingPolicy
from app.models.sales_models import (ROLE_SALES_MANAGER, ROLE_SALES_REP,
                                     SCOPE_BRAND_SALES_ORG, BrandPackage,
                                     BrandSalesOrg, Membership, Opportunity)
from app.services import compensation as comp
from app.services import evosys_comp_seed as seed
from app.services import pipeline_projection as proj
from app.services import pricing_authority as pa
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


@pytest.fixture()
def world(db_session):
    plat = Platform(name="EvoSys Pro", slug="evo-%d" % next(_SEQ))
    db_session.add(plat); db_session.commit()
    brand = BrandSalesOrg(platform_id=plat.id, name="EvoSys Pro Sales",
                          slug="evo-s-%d" % next(_SEQ))
    db_session.add(brand); db_session.commit()

    def pkg(name, key, price, monthly=None, contract=None, term=None):
        p = BrandPackage(platform_id=plat.id, name=name,
                         key="%s-%d" % (key, next(_SEQ)),
                         price=Decimal(price), setup_fee=Decimal(price),
                         monthly_price=Decimal(monthly) if monthly else None,
                         contract_monthly_price=Decimal(contract) if contract else None,
                         contract_term_months=term, currency="USD", is_active=True)
        db_session.add(p); db_session.commit()
        return p

    starter = pkg("Starter", "starter", "1497.00", "597.00", "500.00", 13)
    growth = pkg("Growth", "growth", "2495.00")
    professional = pkg("Professional", "professional", "4995.00")
    saas = pkg("Multi-Tenant Custom", "multi_tenant", "0.00")

    def user(role, name, reports_to=None):
        u = User(organization_id=None, email="u%d@evosyspro.live" % next(_SEQ),
                 password_hash=hash_password("x"), full_name=name,
                 role="advisor", must_change_password=False)
        db_session.add(u); db_session.commit()
        db_session.add(Membership(user_id=u.id, scope_type=SCOPE_BRAND_SALES_ORG,
                                  scope_id=brand.id, role=role, is_active=True,
                                  reports_to_user_id=reports_to))
        db_session.commit()
        return u

    manager = user(ROLE_SALES_MANAGER, "Manager")
    rep = user(ROLE_SALES_REP, "Rep", reports_to=manager.id)

    god = User(organization_id=None, email="owner%d@evosyspro.live" % next(_SEQ),
               password_hash=hash_password("x"), full_name="Owner",
               role="god_admin", must_change_password=False)
    db_session.add(god); db_session.commit()

    su = User(organization_id=None, email="su%d@evosyspro.live" % next(_SEQ),
              password_hash=hash_password("x"), full_name="Super",
              role="super_admin", must_change_password=False)
    db_session.add(su); db_session.commit()

    return dict(plat=plat, brand=brand, starter=starter, growth=growth,
                professional=professional, saas=saas, manager=manager, rep=rep,
                god=god, super_admin=su)


def _h(db, u):
    return {"Authorization": "Bearer " + create_access_token(u, db)}


# ═════════════════════════════════════════════════════════════════════════════
# 1. Only the owner may configure payroll
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("who", ["rep", "manager", "super_admin"])
def test_nobody_below_god_may_read_the_console(client, db_session, world, who):
    r = client.get("/god/pricing/overview", headers=_h(db_session, world[who]))
    assert r.status_code == 403


@pytest.mark.parametrize("who", ["rep", "manager", "super_admin"])
def test_nobody_below_god_may_create_a_pricing_policy(client, db_session, world, who):
    r = client.post("/god/pricing/policies",
                    json={"brand_sales_org_id": world["brand"].id,
                          "role": ROLE_SALES_REP, "max_discount_pct_monthly": 90},
                    headers=_h(db_session, world[who]))
    assert r.status_code == 403
    assert db_session.query(PricingPolicy).count() == 0


@pytest.mark.parametrize("who", ["rep", "manager", "super_admin"])
def test_nobody_below_god_may_create_a_compensation_plan(client, db_session, world, who):
    r = client.post("/god/pricing/plans",
                    json={"brand_sales_org_id": world["brand"].id, "name": "Sneaky",
                          "effective_from": "2026-01-01"},
                    headers=_h(db_session, world[who]))
    assert r.status_code == 403
    assert db_session.query(CompensationPlan).count() == 0


def test_unauthenticated_is_refused(client, world):
    assert client.get("/god/pricing/overview").status_code == 401


def test_the_owner_may_read_the_console(client, db_session, world):
    r = client.get("/god/pricing/overview", headers=_h(db_session, world["god"]))
    assert r.status_code == 200
    body = r.json()
    assert any(b["id"] == world["brand"].id for b in body["brands"])
    assert len(body["packages"]) >= 4


# ═════════════════════════════════════════════════════════════════════════════
# 2. Pricing policies
# ═════════════════════════════════════════════════════════════════════════════

def test_a_policy_can_be_created_and_governs_immediately(client, db_session, world):
    r = client.post("/god/pricing/policies",
                    json={"brand_sales_org_id": world["brand"].id,
                          "role": ROLE_SALES_REP,
                          "max_discount_pct_setup": 5,
                          "max_discount_pct_monthly": 10},
                    headers=_h(db_session, world["god"]))
    assert r.status_code == 200

    caps = pa.ceilings_for(db_session, world["brand"].id, ROLE_SALES_REP)
    assert caps["source"] == "policy"
    assert caps["max_discount_pct_monthly"] == Decimal("10")


def test_a_percentage_outside_zero_to_one_hundred_is_refused(client, db_session, world):
    for bad in (-1, 101):
        r = client.post("/god/pricing/policies",
                        json={"brand_sales_org_id": world["brand"].id,
                              "role": ROLE_SALES_REP,
                              "max_discount_pct_monthly": bad},
                        headers=_h(db_session, world["god"]))
        assert r.status_code == 400


def test_an_unknown_role_is_refused(client, db_session, world):
    r = client.post("/god/pricing/policies",
                    json={"brand_sales_org_id": world["brand"].id,
                          "role": "chief_discount_officer",
                          "max_discount_pct_monthly": 10},
                    headers=_h(db_session, world["god"]))
    assert r.status_code == 400


def test_an_end_date_before_the_start_is_refused(client, db_session, world):
    r = client.post("/god/pricing/policies",
                    json={"brand_sales_org_id": world["brand"].id,
                          "role": ROLE_SALES_REP,
                          "effective_from": "2026-06-01",
                          "effective_to": "2026-01-01"},
                    headers=_h(db_session, world["god"]))
    assert r.status_code == 400


def test_a_policy_for_an_unknown_brand_is_refused(client, db_session, world):
    r = client.post("/god/pricing/policies",
                    json={"brand_sales_org_id": "does-not-exist",
                          "role": ROLE_SALES_REP, "max_discount_pct_monthly": 10},
                    headers=_h(db_session, world["god"]))
    assert r.status_code == 404


def test_a_future_policy_does_not_govern_yet(client, db_session, world):
    """Scheduling next quarter's ceiling must not remove this quarter's."""
    client.post("/god/pricing/policies",
                json={"brand_sales_org_id": world["brand"].id,
                      "role": ROLE_SALES_REP, "max_discount_pct_monthly": 10},
                headers=_h(db_session, world["god"]))
    client.post("/god/pricing/policies",
                json={"brand_sales_org_id": world["brand"].id,
                      "role": ROLE_SALES_REP, "max_discount_pct_monthly": 40,
                      "effective_from": str(date.today() + timedelta(days=30))},
                headers=_h(db_session, world["god"]))
    caps = pa.ceilings_for(db_session, world["brand"].id, ROLE_SALES_REP)
    assert caps["max_discount_pct_monthly"] == Decimal("10")


def test_an_expired_policy_stops_governing(client, db_session, world):
    client.post("/god/pricing/policies",
                json={"brand_sales_org_id": world["brand"].id,
                      "role": ROLE_SALES_REP, "max_discount_pct_monthly": 40,
                      "effective_to": str(date.today() - timedelta(days=1))},
                headers=_h(db_session, world["god"]))
    caps = pa.ceilings_for(db_session, world["brand"].id, ROLE_SALES_REP)
    # Falls back to "a rep discounts nothing", not to the expired 40%.
    assert caps["source"] == "fallback"


def test_deactivating_a_policy_returns_authority_to_the_fallback(
        client, db_session, world):
    r = client.post("/god/pricing/policies",
                    json={"brand_sales_org_id": world["brand"].id,
                          "role": ROLE_SALES_REP, "max_discount_pct_monthly": 25},
                    headers=_h(db_session, world["god"]))
    pid = r.json()["id"]
    client.patch("/god/pricing/policies/%s" % pid, json={"is_active": False},
                 headers=_h(db_session, world["god"]))
    assert pa.ceilings_for(db_session, world["brand"].id,
                           ROLE_SALES_REP)["source"] == "fallback"


def test_a_hard_floor_refuses_instead_of_routing(client, db_session, world):
    """False is a hard stop, not permission. There is deliberately no setting
    that lets a rep past the floor unrecorded."""
    client.post("/god/pricing/policies",
                json={"brand_sales_org_id": world["brand"].id,
                      "role": ROLE_SALES_REP, "max_discount_pct_monthly": 10,
                      "below_floor_requires_approval": False},
                headers=_h(db_session, world["god"]))
    verdict = pa.evaluate(db_session, world["rep"], world["starter"],
                          brand_sales_org_id=world["brand"].id,
                          billing_option="month_to_month", proposed_monthly=100)
    assert verdict["outcome"] == pa.REFUSED


def test_one_brands_policy_still_cannot_govern_another(client, db_session, world):
    other_plat = Platform(name="Other", slug="oth-%d" % next(_SEQ))
    db_session.add(other_plat); db_session.commit()
    other = BrandSalesOrg(platform_id=other_plat.id, name="Other Sales",
                          slug="oth-s-%d" % next(_SEQ))
    db_session.add(other); db_session.commit()
    client.post("/god/pricing/policies",
                json={"brand_sales_org_id": other.id, "role": ROLE_SALES_REP,
                      "max_discount_pct_monthly": 90},
                headers=_h(db_session, world["god"]))
    assert pa.ceilings_for(db_session, world["brand"].id,
                           ROLE_SALES_REP)["source"] == "fallback"


# ═════════════════════════════════════════════════════════════════════════════
# 3. Compensation plans and rules
# ═════════════════════════════════════════════════════════════════════════════

def test_a_plan_needs_a_start_date(client, db_session, world):
    """Without one it cannot be superseded later without rewriting history."""
    r = client.post("/god/pricing/plans",
                    json={"brand_sales_org_id": world["brand"].id, "name": "P"},
                    headers=_h(db_session, world["god"]))
    assert r.status_code == 400


def test_a_plan_and_a_rule_can_be_configured_and_are_used(client, db_session, world):
    h = _h(db_session, world["god"])
    plan = client.post("/god/pricing/plans",
                       json={"brand_sales_org_id": world["brand"].id,
                             "name": "Direct", "effective_from": "2026-01-01",
                             "holdback_days": 14},
                       headers=h).json()
    client.post("/god/pricing/plans/%s/rules" % plan["id"],
                json={"package_id": world["starter"].id,
                      "payee_kind": PAYEE_SELLER, "basis": BASIS_FIXED,
                      "amount": 500},
                headers=h)
    opp = Opportunity(brand_sales_org_id=world["brand"].id,
                      owner_user_id=world["rep"].id, company_name="Acme",
                      selected_package_id=world["starter"].id, status="open")
    db_session.add(opp); db_session.commit()
    assert comp.compute(db_session, opp)["seller_total"] == Decimal("500.00")


def test_an_override_rule_without_a_level_is_refused(client, db_session, world):
    h = _h(db_session, world["god"])
    plan = client.post("/god/pricing/plans",
                       json={"brand_sales_org_id": world["brand"].id,
                             "name": "D", "effective_from": "2026-01-01"},
                       headers=h).json()
    r = client.post("/god/pricing/plans/%s/rules" % plan["id"],
                    json={"payee_kind": PAYEE_OVERRIDE, "basis": BASIS_FIXED,
                          "amount": 100},
                    headers=h)
    assert r.status_code == 400


def test_an_unknown_basis_is_refused(client, db_session, world):
    h = _h(db_session, world["god"])
    plan = client.post("/god/pricing/plans",
                       json={"brand_sales_org_id": world["brand"].id,
                             "name": "D", "effective_from": "2026-01-01"},
                       headers=h).json()
    r = client.post("/god/pricing/plans/%s/rules" % plan["id"],
                    json={"payee_kind": PAYEE_SELLER, "basis": "vibes",
                          "amount": 100},
                    headers=h)
    assert r.status_code == 400


def test_a_rule_with_no_amount_reports_unconfigured_not_zero(client, db_session, world):
    """The distinction the owner asked to keep: $0 reads as a decision,
    unconfigured reads as a decision nobody has made."""
    h = _h(db_session, world["god"])
    plan = client.post("/god/pricing/plans",
                       json={"brand_sales_org_id": world["brand"].id,
                             "name": "D", "effective_from": "2026-01-01"},
                       headers=h).json()
    rule = client.post("/god/pricing/plans/%s/rules" % plan["id"],
                       json={"package_id": world["growth"].id,
                             "payee_kind": PAYEE_SELLER, "basis": BASIS_FIXED},
                       headers=h).json()
    assert rule["configured"] is False
    assert rule["amount"] is None


def test_a_rule_is_deactivated_never_deleted(client, db_session, world):
    """A deleted rule cannot explain the payout it produced."""
    h = _h(db_session, world["god"])
    plan = client.post("/god/pricing/plans",
                       json={"brand_sales_org_id": world["brand"].id,
                             "name": "D", "effective_from": "2026-01-01"},
                       headers=h).json()
    rule = client.post("/god/pricing/plans/%s/rules" % plan["id"],
                       json={"payee_kind": PAYEE_SELLER, "basis": BASIS_FIXED,
                             "amount": 500},
                       headers=h).json()
    client.delete("/god/pricing/rules/%s" % rule["id"], headers=h)
    row = db_session.query(CompensationRule).filter(
        CompensationRule.id == rule["id"]).one()
    assert row.is_active is False


def test_a_cap_is_upserted_not_duplicated(client, db_session, world):
    h = _h(db_session, world["god"])
    plan = client.post("/god/pricing/plans",
                       json={"brand_sales_org_id": world["brand"].id,
                             "name": "D", "effective_from": "2026-01-01"},
                       headers=h).json()
    for amount in (800, 750):
        client.post("/god/pricing/plans/%s/caps" % plan["id"],
                    json={"package_id": world["starter"].id,
                          "max_total_payout": amount}, headers=h)
    caps = db_session.query(CompensationPackageCap).filter(
        CompensationPackageCap.plan_id == plan["id"]).all()
    assert len(caps) == 1
    assert caps[0].max_total_payout == Decimal("750.00")


# ═════════════════════════════════════════════════════════════════════════════
# 4. HISTORICAL SAFETY — the one that matters most
# ═════════════════════════════════════════════════════════════════════════════

def test_changing_a_rate_does_not_touch_an_existing_payout(client, db_session, world):
    """Repricing in June must EXPLAIN January, not rewrite it."""
    h = _h(db_session, world["god"])
    plan = client.post("/god/pricing/plans",
                       json={"brand_sales_org_id": world["brand"].id,
                             "name": "Direct", "effective_from": "2026-01-01"},
                       headers=h).json()
    rule = client.post("/god/pricing/plans/%s/rules" % plan["id"],
                       json={"package_id": world["starter"].id,
                             "payee_kind": PAYEE_SELLER, "basis": BASIS_FIXED,
                             "amount": 500},
                       headers=h).json()

    opp = Opportunity(brand_sales_org_id=world["brand"].id,
                      owner_user_id=world["rep"].id, company_name="Acme",
                      selected_package_id=world["starter"].id, status="won")
    db_session.add(opp); db_session.commit()
    comp.earn(db_session, opp, collection_reference="pi_1",
              collected_amount=Decimal("1497.00"))
    entry = db_session.query(CompensationEntry).filter(
        CompensationEntry.payee_user_id == world["rep"].id).one()
    assert entry.amount == Decimal("500.00")

    # Halve the rate through the console.
    client.patch("/god/pricing/rules/%s" % rule["id"], json={"amount": 250},
                 headers=h)

    db_session.refresh(entry)
    assert entry.amount == Decimal("500.00")
    assert entry.rate_amount == Decimal("500.00")
    assert entry.state == COMP_EARNED


def test_deactivating_a_plan_does_not_void_an_existing_payout(client, db_session, world):
    h = _h(db_session, world["god"])
    plan = client.post("/god/pricing/plans",
                       json={"brand_sales_org_id": world["brand"].id,
                             "name": "Direct", "effective_from": "2026-01-01"},
                       headers=h).json()
    client.post("/god/pricing/plans/%s/rules" % plan["id"],
                json={"package_id": world["starter"].id,
                      "payee_kind": PAYEE_SELLER, "basis": BASIS_FIXED,
                      "amount": 500},
                headers=h)
    opp = Opportunity(brand_sales_org_id=world["brand"].id,
                      owner_user_id=world["rep"].id, company_name="Acme",
                      selected_package_id=world["starter"].id, status="won")
    db_session.add(opp); db_session.commit()
    comp.earn(db_session, opp, collection_reference="pi_1",
              collected_amount=Decimal("1497.00"))

    client.patch("/god/pricing/plans/%s" % plan["id"], json={"is_active": False},
                 headers=h)
    entry = db_session.query(CompensationEntry).filter(
        CompensationEntry.payee_user_id == world["rep"].id).one()
    assert entry.amount == Decimal("500.00")
    assert entry.state == COMP_EARNED


# ═════════════════════════════════════════════════════════════════════════════
# 5. Audit
# ═════════════════════════════════════════════════════════════════════════════

def test_creating_a_policy_is_audited_with_the_actor(client, db_session, world):
    client.post("/god/pricing/policies",
                json={"brand_sales_org_id": world["brand"].id,
                      "role": ROLE_SALES_REP, "max_discount_pct_monthly": 10},
                headers=_h(db_session, world["god"]))
    row = (db_session.query(AuditLogEntry)
           .filter(AuditLogEntry.action == "pricing_policy.created").one())
    assert row.actor_user_id == world["god"].id
    assert row.target_type == "pricing_policy"


def test_an_update_records_the_old_and_the_new_value(client, db_session, world):
    h = _h(db_session, world["god"])
    pid = client.post("/god/pricing/policies",
                      json={"brand_sales_org_id": world["brand"].id,
                            "role": ROLE_SALES_REP,
                            "max_discount_pct_monthly": 10},
                      headers=h).json()["id"]
    client.patch("/god/pricing/policies/%s" % pid,
                 json={"max_discount_pct_monthly": 25}, headers=h)
    row = (db_session.query(AuditLogEntry)
           .filter(AuditLogEntry.action == "pricing_policy.updated").one())
    assert "10" in (row.before_state or "")
    assert "25" in (row.after_state or "")


def test_the_audit_feed_is_god_only(client, db_session, world):
    assert client.get("/god/pricing/audit",
                      headers=_h(db_session, world["manager"])).status_code == 403
    assert client.get("/god/pricing/audit",
                      headers=_h(db_session, world["god"])).status_code == 200


# ═════════════════════════════════════════════════════════════════════════════
# 6. The seed — what it writes, and what it refuses to invent
# ═════════════════════════════════════════════════════════════════════════════

def test_the_preview_writes_nothing(client, db_session, world):
    r = client.post("/god/pricing/seed/evosys",
                    json={"brand_sales_org_id": world["brand"].id},
                    headers=_h(db_session, world["god"]))
    assert r.status_code == 200
    assert r.json()["applied"] is False
    assert db_session.query(CompensationPlan).count() == 0


def test_the_seed_writes_the_decided_starter_rules(client, db_session, world):
    r = client.post("/god/pricing/seed/evosys",
                    json={"brand_sales_org_id": world["brand"].id,
                          "confirm": True},
                    headers=_h(db_session, world["god"]))
    assert r.status_code == 200 and r.json()["applied"] is True

    opp = Opportunity(brand_sales_org_id=world["brand"].id,
                      owner_user_id=world["rep"].id, company_name="Acme",
                      selected_package_id=world["starter"].id, status="open")
    db_session.add(opp); db_session.commit()
    result = comp.compute(db_session, opp)
    assert result["seller_total"] == Decimal("500.00")
    assert result["override_total"] == Decimal("100.00")
    assert result["total"] == Decimal("600.00")
    assert result["cap_amount"] == Decimal("800.00")


def test_the_seeded_cap_binds_at_eight_hundred(client, db_session, world):
    client.post("/god/pricing/seed/evosys",
                json={"brand_sales_org_id": world["brand"].id, "confirm": True},
                headers=_h(db_session, world["god"]))
    rule = (db_session.query(CompensationRule)
            .filter(CompensationRule.payee_kind == PAYEE_SELLER,
                    CompensationRule.package_id == world["starter"].id).one())
    rule.amount = Decimal("900.00")
    db_session.commit()
    opp = Opportunity(brand_sales_org_id=world["brand"].id,
                      owner_user_id=world["rep"].id, company_name="Acme",
                      selected_package_id=world["starter"].id, status="open")
    db_session.add(opp); db_session.commit()
    result = comp.compute(db_session, opp)
    assert result["capped"] is True
    assert result["total"] == Decimal("800.00")


def test_the_seeded_holdback_is_fourteen_days(client, db_session, world):
    client.post("/god/pricing/seed/evosys",
                json={"brand_sales_org_id": world["brand"].id, "confirm": True},
                headers=_h(db_session, world["god"]))
    collected = datetime(2026, 3, 1, 12, 0)
    opp = Opportunity(brand_sales_org_id=world["brand"].id,
                      owner_user_id=world["rep"].id, company_name="Acme",
                      selected_package_id=world["starter"].id, status="won")
    db_session.add(opp); db_session.commit()
    made = comp.earn(db_session, opp, collection_reference="pi_1",
                     collected_amount=Decimal("1497.00"), collected_at=collected)
    assert made[0].payable_at == collected + timedelta(days=14)


def test_the_seed_does_not_invent_growth_or_professional(client, db_session, world):
    """The distinction the owner explicitly asked to keep."""
    body = client.post("/god/pricing/seed/evosys",
                       json={"brand_sales_org_id": world["brand"].id,
                             "confirm": True},
                       headers=_h(db_session, world["god"])).json()
    unconfigured = {p["name"] for p in body["unconfigured_packages"]}
    assert "Growth" in unconfigured
    assert "Professional" in unconfigured

    for pkg in (world["growth"], world["professional"]):
        assert db_session.query(CompensationRule).filter(
            CompensationRule.package_id == pkg.id).count() == 0
        opp = Opportunity(brand_sales_org_id=world["brand"].id,
                          owner_user_id=world["rep"].id, company_name="X",
                          selected_package_id=pkg.id, status="open")
        db_session.add(opp); db_session.commit()
        assert comp.compute(db_session, opp)["payouts"] == []


def test_the_seed_writes_the_multi_tenant_ten_percent(client, db_session, world):
    client.post("/god/pricing/seed/evosys",
                json={"brand_sales_org_id": world["brand"].id, "confirm": True},
                headers=_h(db_session, world["god"]))
    rules = (db_session.query(CompensationRule)
             .filter(CompensationRule.package_id == world["saas"].id).all())
    assert len(rules) == 2
    assert all(r.basis == BASIS_PCT_COLLECTED for r in rules)
    assert all(r.percent == Decimal("10.000") for r in rules)


def test_the_seed_is_idempotent(client, db_session, world):
    h = _h(db_session, world["god"])
    for _ in range(2):
        client.post("/god/pricing/seed/evosys",
                    json={"brand_sales_org_id": world["brand"].id,
                          "confirm": True}, headers=h)
    assert db_session.query(CompensationRule).filter(
        CompensationRule.package_id == world["starter"].id).count() == 2
    assert db_session.query(CompensationPackageCap).count() == 1


def test_re_seeding_never_overwrites_a_hand_set_figure(client, db_session, world):
    """A seed that replaced an existing figure would silently revert a
    deliberate change made on the console."""
    h = _h(db_session, world["god"])
    client.post("/god/pricing/seed/evosys",
                json={"brand_sales_org_id": world["brand"].id, "confirm": True},
                headers=h)
    rule = (db_session.query(CompensationRule)
            .filter(CompensationRule.payee_kind == PAYEE_SELLER,
                    CompensationRule.package_id == world["starter"].id).one())
    client.patch("/god/pricing/rules/%s" % rule.id, json={"amount": 425},
                 headers=h)
    client.post("/god/pricing/seed/evosys",
                json={"brand_sales_org_id": world["brand"].id, "confirm": True},
                headers=h)
    db_session.refresh(rule)
    assert rule.amount == Decimal("425.00")


def test_the_seed_is_god_only(client, db_session, world):
    r = client.post("/god/pricing/seed/evosys",
                    json={"brand_sales_org_id": world["brand"].id, "confirm": True},
                    headers=_h(db_session, world["manager"]))
    assert r.status_code == 403
    assert db_session.query(CompensationPlan).count() == 0


# ═════════════════════════════════════════════════════════════════════════════
# 7. The projection reads the configured plan
# ═════════════════════════════════════════════════════════════════════════════

def test_the_projection_is_empty_before_configuration(client, db_session, world):
    opp = Opportunity(brand_sales_org_id=world["brand"].id,
                      owner_user_id=world["rep"].id, company_name="Acme",
                      selected_package_id=world["starter"].id,
                      billing_option="term_agreement", contract_term_months=13,
                      status="open")
    db_session.add(opp); db_session.commit()
    r = proj.project(db_session, [opp], brand_sales_org_id=world["brand"].id)
    assert r["compensation_plan_configured"] is False


def test_the_projection_reflects_the_seeded_plan(client, db_session, world):
    client.post("/god/pricing/seed/evosys",
                json={"brand_sales_org_id": world["brand"].id, "confirm": True},
                headers=_h(db_session, world["god"]))
    opp = Opportunity(brand_sales_org_id=world["brand"].id,
                      owner_user_id=world["rep"].id, company_name="Acme",
                      selected_package_id=world["starter"].id,
                      billing_option="term_agreement", contract_term_months=13,
                      status="open")
    db_session.add(opp); db_session.commit()
    r = proj.project(db_session, [opp], brand_sales_org_id=world["brand"].id)
    assert r["compensation_plan_configured"] is True
    assert r["pipeline_value"] == 7997.0
    assert r["projected_direct_commissions"] == 500.0
    assert r["projected_manager_overrides"] == 100.0
    assert r["projected_total_compensation"] == 600.0
    assert r["projected_revenue_after_compensation"] == 7397.0
    # Still unavailable — no probabilities were invented by the seed.
    assert r["weighted_available"] is False
