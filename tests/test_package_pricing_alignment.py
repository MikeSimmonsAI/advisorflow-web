"""Approved EvoSys Pro standard packages, end to end (2026-11-15).

Basic $1,600 + $700/mo, Growth $3,200 + $1,300/mo, Professional $5,900 +
$2,400/mo. Flat setup pay 500/200/300, 1000/300/600, 2000/400/900. Month 2+
residual 20/10/15. Package add-on one-time setup stays 15/10/15.

Everything here uses synthetic records and a fake Stripe client.
"""
import itertools
import json
import os
import re
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.models.billing_models import BrandBillingPlan, BillingPayment
from app.models.compensation_models import CompensationEntry
from app.models.implementation_models import Implementation
from app.models.models import Organization, Platform
from app.models.sales_models import (ROLE_SALES_MANAGER, ROLE_SALES_REP, BrandPackage,
                                     BrandSalesOrg, Opportunity)
from app.models.sales_quote_models import SalesQuoteDraft
from app.models.sales_team_models import SalesCollectionSplit
from app.services import capabilities as caps
from app.services import deal_billing
from app.services import deal_builder_commission as dbc
from app.services import evosys_package_catalog as cat
from app.services import billing_catalog
from tests.test_sales_earned_commission import _member, _user

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SEQ = itertools.count(1)
APPROVED = {"basic": (1600, 700, (500, 200, 300)), "growth": (3200, 1300, (1000, 300, 600)),
            "professional": (5900, 2400, (2000, 400, 900))}
KEY = {"basic": "starter", "growth": "growth", "professional": "professional"}


# ── one source ───────────────────────────────────────────────────────────────

def test_standard_table_is_the_approved_blueprint():
    got = {p["plan"]: (int(p["setup"]), int(p["monthly"]),
                       tuple(int(p["payout"][r]) for r in dbc.ROLES)) for p in dbc.STANDARD_PACKAGES}
    assert got == APPROVED
    assert {p["plan"]: p["catalog_key"] for p in dbc.STANDARD_PACKAGES} == KEY
    assert dbc.PACKAGE_PCT == {"rep": 20, "asm": 10, "sevp": 15}
    assert dbc.PACKAGE_ADDON_SETUP_PCT == {"rep": 15, "asm": 10, "sevp": 15}


def test_deal_builder_page_carries_the_same_prices():
    core = open(os.path.join(ROOT, "frontend/public/team-prices/deal-core.js"), encoding="utf-8").read()
    for plan, (setup, month, _pay) in APPROVED.items():
        name = dbc.PLANS[plan]["name"]
        assert re.search(r"\{name:'%s',setup:%d,month:%d," % (name, setup, month), core), name


def test_billing_seed_cannot_put_old_prices_back():
    from app.services import evosys_billing_seed as seed
    plans = {p["key"]: p for p in seed.EVOSYS_PLANS}
    for plan, (_s, month, _p) in APPROVED.items():
        p = plans[KEY[plan]]
        assert p["monthly_cents"] == p["month_to_month_cents"] == month * 100
    assert plans["starter"]["name"] == "Basic"


@pytest.mark.parametrize("plan", list(APPROVED))
def test_full_collection_payouts(plan):
    setup, month, pay = APPROVED[plan]
    v = dbc.view(dbc.split({"plan": plan, "basis": "quote", "monthly": month}), "full")
    by = {l["role"]: l for l in v["layers"]}
    assert [by[r]["one_time"] for r in dbc.ROLES] == [float(x) for x in pay]
    assert [by[r]["monthly"] for r in dbc.ROLES] == [round(month * x, 2) for x in (0.20, 0.10, 0.15)]
    assert v["evosys"]["monthly"] == round(month * 0.55, 2)


def test_addon_one_time_setup_is_not_moved_to_20():
    r = dbc.seller_commission({"plan": "basic", "addon_setup": 200, "monthly": 700, "basis": "quote"})
    assert r["one_time"]["package"] == 500.0
    assert r["one_time"]["addon"] == 30.0          # 15% of the $200 add-on setup
    assert r["recurring"]["monthly"] == 140.0       # 20% of $700


# ── a synthetic EvoSys Pro Sales world ───────────────────────────────────────

class FakeStripe:
    """Records every call; prices live in memory."""
    def __init__(self, existing):
        self.prices = {p["id"]: dict(p) for p in existing}
        self.created = []
        outer = self

        class _Price:
            @staticmethod
            def retrieve(pid):
                return outer.prices[pid]

            @staticmethod
            def list(product=None, active=True, limit=100):
                return {"data": [p for p in outer.prices.values()
                                 if p["product"] == product and p["active"]]}

            @staticmethod
            def create(**kw):
                pid = "price_new_%d" % (len(outer.created) + 1)
                p = {"id": pid, "active": True, "unit_amount": kw["unit_amount"],
                     "currency": kw["currency"], "product": kw["product"],
                     "recurring": kw["recurring"], "metadata": kw["metadata"], "livemode": False}
                outer.prices[pid] = p
                outer.created.append(p)
                return p
        self.Price = _Price


def _old_price(pid, product, cents, commitment):
    return {"id": pid, "active": True, "unit_amount": cents, "currency": "usd", "product": product,
            "recurring": {"interval": "month", "interval_count": 1},
            "metadata": {"commitment": commitment}, "livemode": False}


@pytest.fixture()
def evo(db_session, monkeypatch):
    db = db_session
    plat = Platform(name="EvoSys Pro", slug="evo-align-%d" % next(_SEQ))
    other = Platform(name="Other Brand", slug="other-align-%d" % next(_SEQ))
    db.add_all([plat, other]); db.commit()
    brand = BrandSalesOrg(platform_id=plat.id, name="EvoSys Pro Sales", slug="evosyspro-sales")
    db.add(brand); db.commit()
    old = {"starter": (1500, 597, 500), "growth": (2500, 1297, 1000), "professional": (5000, 2597, 2000)}
    pkgs, plans, prices = {}, {}, []
    for key, (setup, m2m, term) in old.items():
        pkg = BrandPackage(platform_id=plat.id, key=key, name=key.title(), price=Decimal(setup) - 3,
                           setup_fee=Decimal(setup), monthly_price=Decimal(m2m),
                           contract_monthly_price=Decimal(term), contract_term_months=13,
                           billing_plan_key=key, is_active=True)
        plan = BrandBillingPlan(platform_id=plat.id, key=key, name=key.title(), is_active=True,
                                is_purchasable=True, monthly_cents=term * 100,
                                month_to_month_cents=m2m * 100, stripe_product_id="prod_%s" % key,
                                stripe_price_id_monthly="price_old_term_%s" % key,
                                stripe_price_id_month_to_month="price_old_m2m_%s" % key)
        prices += [_old_price("price_old_term_%s" % key, "prod_%s" % key, term * 100, "term"),
                   _old_price("price_old_m2m_%s" % key, "prod_%s" % key, m2m * 100, "month_to_month")]
        db.add_all([pkg, plan]); pkgs[key] = pkg; plans[key] = plan
    custom = BrandPackage(platform_id=plat.id, key="multi_tenant", name="Multi-Tenant / Custom",
                          is_custom=True, is_active=True)
    other_pkg = BrandPackage(platform_id=other.id, key="growth", name="Growth", setup_fee=Decimal("999"),
                             monthly_price=Decimal("99"), is_active=True)
    db.add_all([custom, other_pkg]); db.commit()

    god = _user(db, "God", role="god_admin")
    owner = _user(db, "Owner")
    caps.set_brand_grants(db, owner, brand.id, brand.name, god, ["sales_program_owner"])
    sevp = _user(db, "Sr EVP"); _member(db, sevp, brand, ROLE_SALES_MANAGER)
    caps.set_brand_grants(db, sevp, brand.id, brand.name, god, ["sales_team_executive"])
    asm = _user(db, "ASM"); _member(db, asm, brand, ROLE_SALES_MANAGER, sevp)
    rep = _user(db, "Rep"); _member(db, rep, brand, ROLE_SALES_REP, asm)
    stripe = FakeStripe(prices)
    return SimpleNamespace(db=db, plat=plat, other=other, brand=brand, pkgs=pkgs, plans=plans,
                           custom=custom, other_pkg=other_pkg, god=god, rep=rep, asm=asm, sevp=sevp,
                           stripe=stripe)


def _apply(evo):
    r = cat.run(evo.db, apply=True, stripe_client=evo.stripe)
    evo.db.commit()
    return r


def test_preview_writes_nothing(evo):
    r = cat.run(evo.db, apply=False, stripe_client=evo.stripe)
    evo.db.rollback()
    assert not r["applied"] and not evo.stripe.created
    assert float(evo.pkgs["starter"].setup_fee) == 1500
    row = next(p for p in r["packages"] if p["catalog_key"] == "starter")
    assert row["package_changes"]["setup_fee"] == {"from": 1500.0, "to": 1600.0}
    assert row["package_changes"]["contract_monthly_price"] == {"from": 500.0, "to": None}
    assert row["stripe_price"]["action"] == "would_create"
    assert r["verification"]["all_match"] is False


def test_apply_aligns_catalogue_and_stripe_and_is_idempotent(evo):
    r = _apply(evo)
    assert r["applied"] and not r["blockers"]
    assert len(evo.stripe.created) == 3
    for plan, (setup, month, _pay) in APPROVED.items():
        key = KEY[plan]
        pkg, bp = evo.pkgs[key], evo.plans[key]
        evo.db.refresh(pkg); evo.db.refresh(bp)
        assert float(pkg.setup_fee) == setup and float(pkg.monthly_price) == month
        assert pkg.contract_monthly_price is None and pkg.contract_term_months is None
        assert bp.monthly_cents == bp.month_to_month_cents == month * 100
        new = evo.stripe.prices[bp.stripe_price_id_monthly]
        assert new["unit_amount"] == month * 100 and bp.stripe_price_id_month_to_month == new["id"]
        assert new["metadata"]["commitment"] == "standard"
        # The old prices stay recognised for subscriptions already on them.
        legacy = billing_catalog.legacy_price_ids(bp)
        assert set(legacy) == {"price_old_term_%s" % key, "price_old_m2m_%s" % key}
        for old in legacy:
            assert billing_catalog.resolve_plan_by_price_id(evo.db, evo.plat.id, old).key == key
            assert billing_catalog.interval_for_price_id(bp, old) == "month"
    assert evo.pkgs["starter"].name == "Basic" and evo.plans["starter"].name == "Basic"
    assert r["verification"]["all_match"] is True and r["verification"]["stripe_checked"]
    # Untouched: Custom SaaS and every other brand.
    evo.db.refresh(evo.custom); evo.db.refresh(evo.other_pkg)
    assert evo.custom.setup_fee is None and float(evo.other_pkg.setup_fee) == 999
    # Second apply: nothing to change, nothing created.
    r2 = _apply(evo)
    assert len(evo.stripe.created) == 3
    assert all(not p["package_changes"] and not p["plan_changes"] for p in r2["packages"])
    assert all(p["stripe_price"]["action"] == "reused_mapped" for p in r2["packages"])


def test_apply_without_a_stripe_key_is_reported_not_guessed(evo, monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    r = cat.run(evo.db, apply=False)
    evo.db.rollback()
    assert any("Stripe" in b for b in r["blockers"])


def test_god_endpoints_preview_and_apply(client, evo):
    from tests.test_deal_billing import _h
    with patch.object(cat, "_stripe", return_value=evo.stripe):
        r = client.get("/god/billing/evosys-standard-packages", headers=_h(evo.db, evo.god))
        assert r.status_code == 200 and r.json()["applied"] is False
        assert not evo.stripe.created
        r = client.post("/god/billing/evosys-standard-packages/apply", headers=_h(evo.db, evo.god))
        assert r.status_code == 200, r.text
        assert r.json()["verification"]["all_match"] is True
        r = client.post("/god/billing/evosys-standard-packages/apply", headers=_h(evo.db, evo.rep))
        assert r.status_code in (401, 403)


def test_god_overview_shows_active_rules_and_marks_old_plans_legacy(client, evo):
    from tests.test_deal_billing import _h
    from app.models.compensation_models import CompensationPlan
    evo.db.add(CompensationPlan(brand_sales_org_id=evo.brand.id, name="EvoSys Direct Sales",
                                effective_from=datetime(2026, 9, 9), is_active=True))
    evo.db.commit()
    d = client.get("/god/pricing/overview", headers=_h(evo.db, evo.god)).json()
    ar = d["active_rules"]
    assert [(p["name"], p["setup"], p["monthly"]) for p in ar["packages"]] == [
        ("Basic", 1600.0, 700.0), ("Growth", 3200.0, 1300.0), ("Professional", 5900.0, 2400.0)]
    assert ar["monthly_residual_pct"] == {"rep": 20.0, "asm": 10.0, "sevp": 15.0}
    plan = next(p for p in d["plans"] if p["name"] == "EvoSys Direct Sales")
    assert plan["legacy_for_program"] is True and "LEGACY" in plan["legacy_note"]


# ── the money path, with a full rep -> ASM -> Sr EVP team ────────────────────

def _deal(evo, plan, *, quote=True, paid_setup_at=None):
    db = evo.db
    key = KEY[plan]
    org = Organization(name="Synthetic %s %d" % (plan, next(_SEQ)), slug="syn-%d" % next(_SEQ),
                       platform_id=evo.plat.id, plan="standard", is_active=True,
                       stripe_customer_id="cus_test")
    db.add(org); db.commit()
    opp = Opportunity(company_name=org.name, brand_sales_org_id=evo.brand.id, stage="won",
                      status="won", selected_package_id=evo.pkgs[key].id,
                      billing_option="month_to_month", owner_user_id=evo.rep.id)
    db.add(opp); db.commit()
    impl = Implementation(organization_id=org.id, opportunity_id=opp.id, platform_id=evo.plat.id,
                          status="not_started")
    if paid_setup_at:
        impl.setup_payment_status = "paid"; impl.setup_paid_at = paid_setup_at
        impl.setup_paid_cents = APPROVED[plan][0] * 100
    db.add(impl); db.commit()
    if quote:
        setup, month, _ = APPROVED[plan]
        db.add(SalesQuoteDraft(opportunity_id=opp.id, revision=1, plan_key=plan,
                               setup_total=Decimal(setup), monthly_total=Decimal(month),
                               addon_setup=Decimal(0), data_json="{}", rules_version=dbc.RULES_VERSION))
        db.commit()
    return SimpleNamespace(org=org, opp=opp, impl=impl)


def _codes(terms, part=None):
    bl = terms["blockers"] if part is None else deal_billing.blockers_for_part(terms["blockers"], part)
    return {b["code"] for b in bl}


@pytest.mark.parametrize("plan", list(APPROVED))
def test_checkout_matches_the_quote_and_month2_starts_from_the_setup_payment(evo, plan):
    _apply(evo)
    setup, month, _ = APPROVED[plan]
    d = _deal(evo, plan)
    t = deal_billing.terms_for(evo.db, d.opp)
    charges = {c["kind"]: c["cents"] for c in t["charges"]}
    assert charges == {"setup_fee": setup * 100, "subscription": month * 100}
    assert t["billable_parts"]["setup"] is True
    # Month 1: the monthly link waits for the actual setup payment.
    assert t["billable_parts"]["subscription"] is False
    assert _codes(t, "subscription") == {deal_billing.B_SETUP_NOT_PAID}
    paid = datetime(2026, 1, 31, 15, 0)
    d.impl.setup_payment_status = "paid"; d.impl.setup_paid_at = paid; evo.db.commit()
    with patch.object(deal_billing, "datetime") as fake_dt:
        fake_dt.utcnow.return_value = datetime(2026, 2, 2, 9, 0)
        start = deal_billing.monthly_billing_start(evo.db, d.opp)
    assert start == datetime(2026, 2, 28, 15, 0)       # month-end: Jan 31 -> Feb 28
    t = deal_billing.terms_for(evo.db, d.opp)
    assert t["billable_parts"]["subscription"] is True


def test_no_saved_quote_blocks_both_links(evo):
    _apply(evo)
    d = _deal(evo, "growth", quote=False)
    t = deal_billing.terms_for(evo.db, d.opp)
    assert deal_billing.B_QUOTE_MISSING in _codes(t, "setup")
    assert not t["billable_parts"]["setup"]


def test_quote_saved_at_old_prices_must_be_revised(evo):
    d = _deal(evo, "basic", quote=False)
    evo.db.add(SalesQuoteDraft(opportunity_id=d.opp.id, revision=3, plan_key="basic",
                               setup_total=Decimal(1500), monthly_total=Decimal(597),
                               addon_setup=Decimal(0), data_json="{}", rules_version="2026-11-13"))
    evo.db.commit()
    _apply(evo)
    t = deal_billing.terms_for(evo.db, d.opp)
    codes = _codes(t)
    assert deal_billing.B_QUOTE_SETUP_MISMATCH in codes and deal_billing.B_QUOTE_MONTHLY_MISMATCH in codes
    b = next(b for b in t["blockers"] if b["code"] == deal_billing.B_QUOTE_SETUP_MISMATCH)
    assert b["quoted_cents"] == 150000 and b["link_cents"] == 160000 and b["revision"] == 3
    assert not t["billable_parts"]["setup"]


def test_quote_with_addons_cannot_be_billed_as_a_bare_package(evo):
    _apply(evo)
    d = _deal(evo, "growth", quote=False)
    evo.db.add(SalesQuoteDraft(opportunity_id=d.opp.id, revision=1, plan_key="growth",
                               setup_total=Decimal(4700), monthly_total=Decimal(1599),
                               addon_setup=Decimal(1500), data_json="{}", rules_version=dbc.RULES_VERSION))
    evo.db.commit()
    t = deal_billing.terms_for(evo.db, d.opp)
    assert deal_billing.B_QUOTE_SETUP_MISMATCH in _codes(t)


def test_old_term_selector_cannot_charge_the_old_term_price(evo):
    _apply(evo)
    d = _deal(evo, "professional")
    d.opp.billing_option = "term_agreement"; d.opp.contract_term_months = 13; evo.db.commit()
    t = deal_billing.terms_for(evo.db, d.opp)
    sub = next(c for c in t["charges"] if c["kind"] == "subscription")
    assert sub["cents"] == 240000


def test_subscription_link_has_no_month1_charge(client, evo):
    from tests.test_deal_billing import _Sess, _h
    _apply(evo)
    paid = datetime.utcnow() - timedelta(days=3)
    d = _deal(evo, "basic", paid_setup_at=paid)
    with patch("stripe.checkout.Session.create", return_value=_Sess()) as create, \
         patch("app.routers.billing_router._stripe_client"), \
         patch("app.routers.billing_router._get_or_create_customer", return_value="cus_test"), \
         patch("app.routers.billing_router._brand_base_url", return_value="https://brand.test"):
        r = client.post("/sales/opportunities/%s/billing/checkout?part=subscription" % d.opp.id,
                        headers=_h(evo.db, evo.god))
        assert r.status_code == 200, r.text
        kw = create.call_args.kwargs
        assert kw["line_items"] == [{"price": evo.plans["starter"].stripe_price_id_monthly, "quantity": 1}]
        assert kw["subscription_data"]["proration_behavior"] == "none"
        import calendar
        assert kw["subscription_data"]["billing_cycle_anchor"] == calendar.timegm(
            deal_billing.add_one_month(paid).utctimetuple())
        # The setup link charges only the setup amount.
        r = client.post("/sales/opportunities/%s/billing/checkout?part=setup" % d.opp.id,
                        headers=_h(evo.db, evo.god))
        assert r.status_code == 200, r.text
        kw = create.call_args.kwargs
        assert kw["mode"] == "payment"
        assert [li["price_data"]["unit_amount"] for li in kw["line_items"]] == [160000]


# ── earned ledger: setup vs recurring ────────────────────────────────────────

def _by_role(rows):
    return {e.payee_kind: float(e.amount) for e in rows}


@pytest.mark.parametrize("plan", list(APPROVED))
def test_setup_payment_books_flat_payout_and_monthly_invoice_books_residual(evo, plan):
    from app.services import billing_webhook
    _apply(evo)
    setup, month, pay = APPROVED[plan]
    d = _deal(evo, plan)
    session = {"id": "cs_test_%d" % next(_SEQ), "payment_status": "paid",
               "payment_intent": "pi_test_%d" % next(_SEQ), "amount_total": setup * 100,
               "currency": "usd", "metadata": {"opportunity_id": d.opp.id, "part": "setup",
                                               "source": "deal_billing", "org_id": d.org.id}}
    billing_webhook._handle_setup_payment(evo.db, d.org, session)
    evo.db.commit()
    rows = evo.db.query(CompensationEntry).filter_by(opportunity_id=d.opp.id).all()
    assert _by_role(rows) == {r: float(x) for r, x in zip(dbc.ROLES, pay)}
    assert all(e.rate_percent is None for e in rows)               # flat, no residual
    split = evo.db.query(SalesCollectionSplit).filter_by(opportunity_id=d.opp.id).one()
    assert float(split.monthly_part) == 0.0
    first_paid_at = d.impl.setup_paid_at
    # A replayed event books nothing twice and does not move the setup date.
    billing_webhook._handle_setup_payment(evo.db, d.org, session)
    evo.db.commit()
    assert evo.db.query(CompensationEntry).filter_by(opportunity_id=d.opp.id).count() == 3
    assert d.impl.setup_paid_at == first_paid_at

    # Month 2: a subscription invoice is residual only.
    from app.services import billing_compensation
    p = BillingPayment(organization_id=d.org.id, platform_id=evo.plat.id,
                       collection_reference="stripe:in_test_%d" % next(_SEQ),
                       stripe_invoice_id="in_test", amount_cents=month * 100, is_initial=False)
    evo.db.add(p); evo.db.flush()
    billing_compensation.earn_for_payment(evo.db, p, commit=True, collection_kind="recurring")
    m2 = evo.db.query(CompensationEntry).filter_by(opportunity_id=d.opp.id,
                                                    collection_reference=p.collection_reference).all()
    assert _by_role(m2) == {"rep": round(month * 0.20, 2), "asm": round(month * 0.10, 2), "sevp": round(month * 0.15, 2)}
    assert all(e.payable_at - e.collected_at == timedelta(days=14) for e in m2)


def test_monthly_invoice_before_setup_never_pays_a_setup_payout(evo):
    from app.services import billing_compensation
    _apply(evo)
    d = _deal(evo, "growth")
    p = BillingPayment(organization_id=d.org.id, platform_id=evo.plat.id,
                       collection_reference="stripe:in_early_%d" % next(_SEQ),
                       amount_cents=130000, is_initial=True)
    evo.db.add(p); evo.db.flush()
    billing_compensation.earn_for_payment(evo.db, p, commit=True, collection_kind="recurring")
    rows = evo.db.query(CompensationEntry).filter_by(opportunity_id=d.opp.id).all()
    assert _by_role(rows) == {"rep": 260.0, "asm": 130.0, "sevp": 195.0}


def test_setup_payment_on_another_brand_still_earns_nothing(db_session):
    from app.services import billing_webhook
    db = db_session
    plat = Platform(name="Other", slug="oth-%d" % next(_SEQ)); db.add(plat); db.commit()
    brand = BrandSalesOrg(platform_id=plat.id, name="Other Sales", slug="oth-sales-%d" % next(_SEQ))
    db.add(brand); db.commit()
    org = Organization(name="Other Co", slug="oc-%d" % next(_SEQ), platform_id=plat.id,
                       plan="standard", is_active=True)
    db.add(org); db.commit()
    opp = Opportunity(company_name="Other Co", brand_sales_org_id=brand.id, stage="won", status="won")
    db.add(opp); db.commit()
    db.add(Implementation(organization_id=org.id, opportunity_id=opp.id, platform_id=plat.id,
                          status="not_started")); db.commit()
    billing_webhook._handle_setup_payment(db, org, {
        "id": "cs_o", "payment_status": "paid", "payment_intent": "pi_o_%d" % next(_SEQ),
        "amount_total": 100000, "currency": "usd", "metadata": {"opportunity_id": opp.id}})
    db.commit()
    p = db.query(BillingPayment).filter_by(organization_id=org.id).one()
    assert p.earned_compensation is False and "not configured" in p.compensation_skipped_reason
