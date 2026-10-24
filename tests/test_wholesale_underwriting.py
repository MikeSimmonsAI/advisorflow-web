"""Deal Intelligence Engine: formulas checked by hand, and the Analyze-deal
path through the API on a TEST deal (no outside calls)."""
import io

import pytest

from app.models.wholesale_models import WholesaleComp, WholesaleDeal
from app.services import wholesale_underwriting as UW

ES = "/wholesale/evosense"


def ok(r, what=""):
    assert r.status_code in (200, 201), "%s: %s %s" % (what, r.status_code, r.text[:400])
    return r.json()


# ── formulas (hand-checked) ─────────────────────────────────────────────────

def test_flip_math_by_hand():
    a = UW.assumptions({})
    r = UW.flip_scenario(200000, 30000, 100000, a)
    # closing 2% of 100k = 2,000; holding 5 mo: tax 200k*2.2%/12*5 = 1,833.33 + ins 750 + util 1,000
    # selling 8% of 200k = 16,000; all cash
    assert r["buy_closing"] == 2000 and r["holding"] == pytest.approx(3583.33, abs=0.01)
    assert r["selling"] == 16000 and r["financing"] == 0
    assert r["net_profit"] == pytest.approx(48416.67, abs=0.01)
    assert r["cash_invested"] == pytest.approx(135583.33, abs=0.01)
    assert r["roi_pct"] == pytest.approx(35.71, abs=0.01)


def test_flip_with_a_loan_counts_interest_and_points_and_less_cash():
    a = UW.assumptions({"flip_loan_pct": 80})
    r = UW.flip_scenario(200000, 30000, 100000, a)
    loan = 0.8 * 130000                                   # 104,000
    assert r["financing"] == pytest.approx(loan * 0.12 / 12 * 5 + loan * 0.02, abs=0.01)
    assert r["cash_invested"] == pytest.approx(135583.33 + r["financing"] - loan, abs=0.01)


def test_rental_math_by_hand():
    a = UW.assumptions({"rent_monthly": 1800})
    r = UW.rental_scenario(200000, 30000, 100000, a)
    # NOI = 21,600 - vacancy 1,728 - 23% of rent 4,968 - tax 4,400 - insurance 1,800 = 8,704
    assert r["noi_annual"] == 8704
    assert r["payment_monthly"] == pytest.approx(524.41, abs=0.01)       # 75k at 7.5% for 30 years
    assert r["cash_flow_annual"] == pytest.approx(8704 - 524.41 * 12, abs=0.2)
    assert r["cash_invested"] == 57000                                   # 25k down + 30k repairs + 2k closing
    assert r["cap_rate_pct"] == pytest.approx(6.7, abs=0.01)


def test_rental_never_assumes_rent():
    r = UW.rental_scenario(200000, 30000, 100000, UW.assumptions({}))
    assert r["available"] is False and "rent" in r["why"]


def test_offer_range_rounds_down_to_500():
    from decimal import Decimal
    r = UW.wholesale_scenario(200000, 30000, Decimal("94321"), 10000, UW.assumptions({}))
    assert r["offer_high"] == 94000 and r["offer_low"] == 84500       # 94,321 x 0.9 = 84,888.9 -> 84,500
    assert r["buyer_price_at_mao"] == 104000


def test_bad_assumptions_are_refused_with_a_sentence():
    with pytest.raises(ValueError):
        UW.assumptions({"flip_selling_pct": 150})
    with pytest.raises(ValueError):
        UW.assumptions({"rent_monthly": -5})


def test_itemized_repairs():
    est = UW.itemized([{"category": "Roof", "item": "Roof replacement", "quantity": 20, "unit": "square",
                        "unit_cost": 450},
                       {"category": "Kitchen", "item": "Cabinets", "quantity": 1, "unit_cost": 6000}],
                      contingency_pct=10, quality="standard")
    assert est["low"] == 15000 and est["base"] == 16500 and est["high"] == 18000
    assert est["by_category"] == {"Roof": 9000.0, "Kitchen": 6000.0}
    with pytest.raises(ValueError):
        UW.itemized([{"item": "Paint", "quantity": 0, "unit_cost": 10}])
    with pytest.raises(ValueError):
        UW.itemized([])


# ── through the API, one TEST deal ──────────────────────────────────────────

def _deal(client, H, street="300 Underwrite Ln"):
    pid = ok(client.post(ES + "/properties", headers=H, json={
        "street_address": street, "city": "Dallas", "state": "TX", "zip_code": "75216", "county": "Dallas",
        "property_type": "single_family", "square_feet": 1400, "is_test": True, "signals": ["VACANT"]}))["property_id"]
    ok(client.post(ES + "/properties/%s/owner" % pid, headers=H, json={"name": "Underwrite Test Owner"}))
    return pid, ok(client.post(ES + "/properties/%s/promote" % pid, headers=H, json={}))["deal_id"]


CSV = ("address,sale_price,sale_date,sqft,beds,baths,distance_miles,mls_number\n"
       "310 Underwrite Ln,205000,2026-07-01,1380,3,2,0.2,MLS-1001\n"
       "322 Underwrite Ln,212000,2026-08-15,1420,3,2,0.3,MLS-1002\n"
       "400 Other St,209000,2026-06-20,1395,3,2,0.5,MLS-1003\n"
       "500 No Source St,199000,2026-06-20,1395,3,2,0.5,\n")


def test_analyze_deal_end_to_end(client, auth_headers, db_session):
    H = auth_headers
    _, deal_id = _deal(client, H)
    first = ok(client.get("/wholesale/deals/%s/underwriting" % deal_id, headers=H))
    assert first["arv"]["value"] is None and first["offer"]["mao"] is None
    assert any(m.startswith("ARV") for m in first["missing"])
    assert first["workflow"][0]["key"] == "analyze" and first["workflow"][0]["state"] == "attention"
    assert first["scenarios"]["base"]["flip"]["available"] is False

    # comps from a CSV: the row without a source is refused, never guessed
    up = ok(client.post("/wholesale/deals/%s/comps/import" % deal_id, headers=H,
                        files={"file": ("comps.csv", io.BytesIO(CSV.encode()), "text/csv")}))
    assert up["added"] == 3 and len(up["refused"]) == 1 and "source_reference" in up["refused"][0]["why"]
    again = ok(client.post("/wholesale/deals/%s/comps/import" % deal_id, headers=H,
                           files={"file": ("comps.csv", io.BytesIO(CSV.encode()), "text/csv")}))
    assert again["added"] == 0                                     # the same sales are never added twice
    comps = db_session.query(WholesaleComp).filter(WholesaleComp.deal_id == deal_id).all()
    assert len(comps) == 3 and all(c.verification_state == "manual" and c.source_reference for c in comps)

    # itemized repairs become the deal's MANUAL estimate
    rep = ok(client.post("/wholesale/deals/%s/repairs/itemized" % deal_id, headers=H, json={
        "items": [{"category": "Roof", "item": "Roof", "quantity": 1, "unit_cost": 12000},
                  {"category": "Flooring", "item": "LVP", "quantity": 1200, "unit": "sq ft", "unit_cost": 4}],
        "contingency_pct": 10}))
    assert rep["estimate"]["base"] == pytest.approx(18480)
    deal = db_session.query(WholesaleDeal).filter(WholesaleDeal.id == deal_id).first()
    db_session.refresh(deal)
    assert deal.repair_status == "MANUAL_ESTIMATE" and float(deal.repair_estimate) == pytest.approx(18480)

    run = ok(client.post("/wholesale/deals/%s/underwriting" % deal_id, headers=H,
                         json={"assumptions": {"rent_monthly": 1750}}))
    if run["arv"]["value"] is None:
        # the workspace's comp rules did not accept the manual comps: a person states the ARV
        ok(client.patch("/wholesale/deals/%s/analysis" % deal_id, headers=H, json={"arv": 209000}))
        run = ok(client.post("/wholesale/deals/%s/underwriting" % deal_id, headers=H,
                             json={"assumptions": {"rent_monthly": 1750}}))
    assert run["arv"]["value"] and run["repairs"]["amount"] == pytest.approx(18480)
    db_session.refresh(deal)
    # the MAO is the deal's own calculation, not a second formula
    assert run["offer"]["mao"] == pytest.approx(float(deal.max_allowable_offer))
    assert run["offer"]["range_high"] <= run["offer"]["mao"] and run["offer"]["range_low"] < run["offer"]["range_high"]
    base = run["scenarios"]["base"]
    assert base["flip"]["available"] and base["rental"]["available"] and base["wholesale"]["available"]
    assert run["assumptions"]["rent_monthly"] == 1750
    assert run["history"] and run["history"][0]["mao"] == run["offer"]["mao"]
    # the last run's assumptions are what the screen shows next time
    again = ok(client.get("/wholesale/deals/%s/underwriting" % deal_id, headers=H))
    assert again["assumptions"]["rent_monthly"] == 1750
    steps = {w["key"]: w["state"] for w in again["workflow"]}
    assert steps["analyze"] == "done" and steps["repairs"] == "done"


def test_underwriting_is_tenant_scoped(client, auth_headers, db_session):
    from app.models.models import Organization, User
    from app.services.auth_service import create_access_token, hash_password
    _, deal_id = _deal(client, auth_headers, street="301 Underwrite Ln")
    other = Organization(name="Other UW Co", slug="other-uw", plan="standard", industry="real_estate")
    db_session.add(other)
    db_session.commit()
    u = User(organization_id=other.id, email="uw.other@example.test", full_name="Other", role="org_admin",
             password_hash=hash_password("TestPass123!"), must_change_password=False)
    db_session.add(u)
    db_session.commit()
    h = {"Authorization": "Bearer %s" % create_access_token(u, db_session)}
    assert client.get("/wholesale/deals/%s/underwriting" % deal_id, headers=h).status_code in (403, 404)
    for path, body in (("/wholesale/deals/%s/underwriting" % deal_id, {}),
                       ("/wholesale/deals/%s/repairs/itemized" % deal_id,
                        {"items": [{"item": "Roof", "quantity": 1, "unit_cost": 1}]})):
        assert client.post(path, headers=h, json=body).status_code in (403, 404), path
    r = client.post("/wholesale/deals/%s/comps/import" % deal_id, headers=h,
                    files={"file": ("c.csv", io.BytesIO(CSV.encode()), "text/csv")})
    assert r.status_code in (403, 404)
    assert db_session.query(WholesaleComp).filter(WholesaleComp.deal_id == deal_id).count() == 0
