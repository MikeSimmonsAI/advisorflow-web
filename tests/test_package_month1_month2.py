"""Basic / Growth / Professional: Month 1 is the flat one-time fee and flat
payout; Month 2 onward is the monthly fee with residuals locked at
Salesperson 20% / Area Sales Manager 10% / Sr EVP 15% (owner instruction,
2026-11-15).

Covers the rules, the earned records (with a full rep -> ASM -> Sr EVP team),
the Month-2 start on the monthly payment link, and Exhibit A.
"""
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.services import deal_builder_commission as dbc
from app.services import deal_billing

# Configured flat payouts and the Deal Builder's configured monthly prices.
PACKAGES = {
    "basic": {"setup": 1600, "month": 700, "pay": (500, 200, 300)},
    "growth": {"setup": 3200, "month": 1300, "pay": (1000, 300, 600)},
    "professional": {"setup": 5900, "month": 2400, "pay": (2000, 400, 900)},
}
RESIDUAL = (Decimal("20"), Decimal("10"), Decimal("15"))


def test_rules_lock_the_residual_at_20_10_15():
    assert dbc.PACKAGE_PCT == {"rep": Decimal("20"), "asm": Decimal("10"), "sevp": Decimal("15")}
    assert dbc.RULES["package_pct"] == [20.0, 10.0, 15.0]
    assert dbc.RULES_VERSION == "2026-11-15"


@pytest.mark.parametrize("plan", list(PACKAGES))
def test_month1_is_the_flat_payout_only(plan):
    p = PACKAGES[plan]
    # Month 1: the customer pays exactly the one-time fee; nothing monthly.
    v = dbc.view(dbc.split({"plan": plan, "basis": "paid", "monthly": p["month"],
                            "collected_setup": p["setup"], "collected_monthly": 0}), "full")
    by = {l["role"]: l for l in v["layers"]}
    assert tuple(by[r]["one_time"] for r in dbc.ROLES) == tuple(float(x) for x in p["pay"])
    assert all(by[r]["monthly"] == 0.0 for r in dbc.ROLES)
    # The flat payout is never a percentage of the one-time fee.
    assert by["rep"]["one_time"] != round(p["setup"] * 0.20, 2)


@pytest.mark.parametrize("plan", list(PACKAGES))
def test_month2_is_the_monthly_fee_at_20_10_15(plan):
    p = PACKAGES[plan]
    v = dbc.view(dbc.split({"plan": plan, "basis": "paid", "monthly": p["month"],
                            "collected_setup": 0, "collected_monthly": p["month"]}), "full")
    by = {l["role"]: l for l in v["layers"]}
    want = [float(Decimal(p["month"]) * pct / 100) for pct in RESIDUAL]
    assert [by[r]["monthly"] for r in dbc.ROLES] == want
    assert all(by[r]["one_time"] == 0.0 for r in dbc.ROLES)
    assert v["evosys"]["monthly"] == float(Decimal(p["month"]) * Decimal("0.55"))


def test_basic_rep_one_time_is_500_not_320():
    r = dbc.seller_commission({"plan": "basic", "basis": "quote", "monthly": 700})
    assert r["one_time"]["package"] == 500.0
    assert r["recurring"]["monthly"] == 140.0
    assert "Month 2" in r["recurring"]["rule"]


@pytest.mark.parametrize("seat,role", [("rep", "rep"), ("asm", "asm"), ("sevp", "sevp")])
def test_rep_view_and_exhibit_show_only_that_seat(seat, role):
    from app.services import sales_contractor as sc
    html = sc.exhibit_html(seat, "A-test")
    pct = {"rep": "20%", "asm": "10%", "sevp": "15%"}[role]
    assert "Month 1" in html and "Month 2" in html
    assert "<b>%s</b> of each monthly fee collected" % pct in html
    # Each package row carries this seat's flat amount and no other seat's.
    names = {"basic": "Basic", "growth": "Growth", "professional": "Professional"}
    for plan, p in PACKAGES.items():
        row_start = "%s (one-time fee ${:,}): ".format(p["setup"]) % names[plan]
        row = html[html.index(row_start):].split("</li>")[0]
        mine = p["pay"][dbc.ROLES.index(role)]
        assert "<b>${:,}</b> flat".format(mine) in row
        for other in dbc.ROLES:
            theirs = p["pay"][dbc.ROLES.index(other)]
            if other != role and theirs != mine:
                assert "${:,}</b>".format(theirs) not in row
    v = dbc.view(dbc.split({"plan": "basic", "basis": "quote", "monthly": 700}), "rep")
    assert [l["role"] for l in v["layers"]] == ["rep"]
    assert "evosys" not in v and "unclaimed" not in v


# ── earned records: Month 1 then Month 2 for each package, with a full team ──

from tests.test_sales_earned_commission import team  # noqa: F401  (fixture)
from tests.test_deal_billing import world  # noqa: F401  (fixture)


@pytest.mark.parametrize("plan_key", ["starter", "growth", "professional"])
def test_earned_month1_flat_then_month2_residual(team, plan_key):
    """A synthetic deal per package with a full rep -> ASM -> Sr EVP team:
    Month 1 collects exactly the one-time fee, Month 2 one monthly fee."""
    import tests.test_sales_earned_commission as base
    plan = {"starter": "basic"}.get(plan_key, plan_key)
    p = PACKAGES[plan]
    team.econ.update(key=plan_key, fee=Decimal(p["setup"]), mrr=Decimal(p["month"]))
    opp = base._deal(team)
    month1 = base._by_role(base._pay(team, opp, "m1", str(p["setup"])))
    assert month1 == {r: float(x) for r, x in zip(dbc.ROLES, p["pay"])}
    month2 = base._by_role(base._pay(team, opp, "m2", str(p["month"]),
                                     when=datetime(2027, 1, 1, 12)))
    assert month2 == {r: float((Decimal(p["month"]) * RESIDUAL[i] / 100).quantize(Decimal("0.01")))
                      for i, r in enumerate(dbc.ROLES)}
    entries = base.comp.CompensationEntry if hasattr(base.comp, "CompensationEntry") else None
    from app.models.compensation_models import CompensationEntry
    rows = team.db.query(CompensationEntry).filter_by(opportunity_id=opp.id).all()
    m2 = [e for e in rows if e.collection_reference == "m2"]
    assert sorted(float(e.rate_percent) for e in m2) == [10.0, 15.0, 20.0]
    m1 = [e for e in rows if e.collection_reference == "m1"]
    assert all(e.rate_percent is None for e in m1)          # flat, no percentage


# ── the monthly payment link starts billing in Month 2 ───────────────────────

def test_add_one_month_handles_month_ends():
    assert deal_billing.add_one_month(datetime(2026, 1, 31)) == datetime(2026, 2, 28)
    assert deal_billing.add_one_month(datetime(2026, 12, 15, 9)) == datetime(2027, 1, 15, 9)


def test_month2_start_only_for_program_package_deals(db_session):
    from types import SimpleNamespace
    now = datetime(2026, 11, 15, 22, 0)
    opp = SimpleNamespace(id="x", brand_sales_org_id="brand")
    with patch("app.services.sales_program_comp.applies", return_value=True), \
         patch.object(deal_billing, "implementation_for",
                      return_value=SimpleNamespace(setup_paid_at=datetime(2026, 11, 10, 12))):
        start = deal_billing.monthly_billing_start(db_session, opp, now=now)
        assert start == datetime(2026, 12, 10, 12)       # one month after the first payment
        assert deal_billing.monthly_billing_start(
            db_session, opp, pricing_mode=deal_billing.PRICING_CUSTOM, now=now) is None
    with patch("app.services.sales_program_comp.applies", return_value=True), \
         patch.object(deal_billing, "implementation_for",
                      return_value=SimpleNamespace(setup_paid_at=None)):
        start = deal_billing.monthly_billing_start(db_session, opp, now=now)
        # Not paid yet: one month from now, kept just inside Stripe's window.
        assert now + timedelta(days=27) < start < deal_billing.add_one_month(now)
    with patch("app.services.sales_program_comp.applies", return_value=True), \
         patch.object(deal_billing, "implementation_for",
                      return_value=SimpleNamespace(setup_paid_at=datetime(2026, 8, 1))):
        # Paid long ago: Month 2 already began, so billing starts as soon as Stripe allows.
        assert deal_billing.monthly_billing_start(db_session, opp, now=now) == now + timedelta(days=2)
    with patch("app.services.sales_program_comp.applies", return_value=False):
        assert deal_billing.monthly_billing_start(db_session, opp, now=now) is None


def test_subscription_link_for_program_package_charges_nothing_in_month1(client, db_session, world):
    import tests.test_deal_billing as tdb
    world["pkg"].billing_plan_key = "growth"
    world["org"].stripe_customer_id = "cus_test123"
    world["impl"].setup_paid_at = datetime.utcnow() - timedelta(days=3)
    world["impl"].setup_payment_status = "paid"
    # The link must match the saved quote (quote-to-checkout check).
    from app.models.sales_quote_models import SalesQuoteDraft
    db_session.add(SalesQuoteDraft(opportunity_id=world["opp"].id, revision=1, plan_key="growth",
                                   setup_total=Decimal("2500"), monthly_total=Decimal("500"),
                                   addon_setup=Decimal("0"), data_json="{}",
                                   rules_version=dbc.RULES_VERSION))
    db_session.commit()
    with patch("app.services.sales_program_comp.applies", return_value=True), \
         patch("stripe.checkout.Session.create", return_value=tdb._Sess()) as create, \
         patch("app.routers.billing_router._stripe_client"), \
         patch("app.routers.billing_router._get_or_create_customer", return_value="cus_test123"), \
         patch("app.routers.billing_router._brand_base_url", return_value="https://brand.test"):
        r = client.post("/sales/opportunities/%s/billing/checkout?part=subscription" % world["opp"].id,
                        headers=tdb._h(db_session, world["god"]))
    assert r.status_code == 200, r.text
    kw = create.call_args.kwargs
    sd = kw["subscription_data"]
    assert sd["proration_behavior"] == "none"
    want = deal_billing.add_one_month(world["impl"].setup_paid_at)
    import calendar
    assert sd["billing_cycle_anchor"] == calendar.timegm(want.utctimetuple())
    assert r.json()["monthly_billing_starts"] == want.date().isoformat()
    # No one-time fee rides along on the monthly link.
    assert len(kw["line_items"]) == 1 and "price" in kw["line_items"][0]


def test_subscription_link_for_other_brands_is_unchanged(client, db_session, world):
    import tests.test_deal_billing as tdb
    world["pkg"].billing_plan_key = "growth"
    world["org"].stripe_customer_id = "cus_test123"
    db_session.commit()
    with patch("stripe.checkout.Session.create", return_value=tdb._Sess()) as create, \
         patch("app.routers.billing_router._stripe_client"), \
         patch("app.routers.billing_router._get_or_create_customer", return_value="cus_test123"), \
         patch("app.routers.billing_router._brand_base_url", return_value="https://brand.test"):
        r = client.post("/sales/opportunities/%s/billing/checkout?part=subscription" % world["opp"].id,
                        headers=tdb._h(db_session, world["god"]))
    assert r.status_code == 200, r.text
    sd = create.call_args.kwargs["subscription_data"]
    assert "billing_cycle_anchor" not in sd and "proration_behavior" not in sd
    assert r.json()["monthly_billing_starts"] is None


def test_customer_copy_states_month1_and_month2():
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    core = open(os.path.join(root, "frontend/public/team-prices/deal-core.js"), encoding="utf-8").read()
    assert "Payment schedule:</b> Month 1" in core and "Month 2 onward" in core
    for f in ("frontend/public/team-prices/sales-builder-frame.html",
              "app/private_pages/deal_builder_executive.html"):
        s = open(os.path.join(root, f), encoding="utf-8").read()
        assert "billing begins afterward" not in s, f
        assert "${billingStart(p)} and is not included in the first payment" in s, f
        assert "STARTS MONTH 2" in s, f
    exe = open(os.path.join(root, "app/private_pages/deal_builder_executive.html"), encoding="utf-8").read()
    assert "packages 15% / 10% / 15%" not in exe
