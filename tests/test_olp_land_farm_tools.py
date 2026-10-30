"""OLP Land Partners / Farm & Ranch tools on the existing property-details record.
Synthetic data only. No schema change: everything lives in type_facts."""
import json
import uuid

import pytest

from app.models.models import Organization
from app.models.evosense_models import EvoSenseProperty
from app.services.evosense import type_details as TD


def f(value, verified=False):
    return {"value": value, "source": "Entered by x", "verified": verified}


class U:
    email = "tester@example.com"
    id = "u-1"


def org(db):
    o = Organization(name="Land Co", slug="lf-" + uuid.uuid4().hex[:10], plan="standard", industry="real_estate",
                     is_active=True, enabled_features=json.dumps(["wholesale_real_estate"]))
    db.add(o); db.flush()
    return o


def prop(db, o, category="land"):
    p = EvoSenseProperty(organization_id=o.id, street_address="County Road 9", state="MO", county="Christian",
                         property_category=category, property_class_source="county_record")
    db.add(p); db.flush()
    return p


def test_checklist_status_accepts_only_ok_missing_problem(db_session):
    p = prop(db_session, org(db_session))
    TD.set_facts(db_session, p, U(), {"dd_access": "OK", "legal_access": "40 ft recorded easement"},
                 {"legal_access": "Easement vol 12 p 34"})
    mine = TD.facts(p)
    assert mine["dd_access"]["value"] == "ok" and mine["dd_access"]["by"] == "tester@example.com"
    assert mine["legal_access"]["evidence"] == "Easement vol 12 p 34"
    for bad in ("maybe", "yes", "done"):
        with pytest.raises(ValueError):
            TD.set_facts(db_session, p, U(), {"dd_access": bad})
    TD.set_facts(db_session, p, U(), {"dd_access": None})          # cleared by the person
    assert "dd_access" not in TD.facts(p)


def test_exit_plan_is_one_of_the_fixed_plans(db_session):
    p = prop(db_session, org(db_session))
    TD.set_facts(db_session, p, U(), {"exit_plan": "seller_finance"})
    assert TD.facts(p)["exit_plan"]["value"] == "seller_finance"
    with pytest.raises(ValueError):
        TD.set_facts(db_session, p, U(), {"exit_plan": "rent it out to aliens"})


def test_view_returns_both_checklists_and_the_tool_values(db_session):
    p = prop(db_session, org(db_session), "agricultural")
    TD.set_facts(db_session, p, U(), {"fc_water": "problem", "water_rights": "No recorded rights", "pasture_acres": 80})
    v = TD.view(db_session, p)
    t = v["tools"]
    assert [r["item"] for r in t["land_checklist"]][:3] == ["Road access", "Zoning", "Power and water"]
    assert len(t["farm_checklist"]) == 8 and t["statuses"] == ["ok", "missing", "problem"]
    assert t["values"]["fc_water"]["value"] == "problem"
    assert t["values"]["pasture_acres"]["value"] == 80
    assert t["values"]["dd_title"]["value"] is None and t["values"]["dd_title"]["source"] == "Not on record"
    assert {e["key"] for e in t["exit_plans"]} == set(TD.EXIT_PLANS)
    # the property page's own section is unchanged: statuses never clutter it
    assert not any(fl["key"].startswith(("dd_", "fc_")) for fl in v["section"])


def test_farm_income_from_entered_acres_rents_and_costs():
    a = TD.analysis("agricultural", {
        "acreage": f(200), "comp_price_per_acre": f(4500),
        "cropland_acres": f(120), "cropland_rent_per_acre": f(150),
        "pasture_acres": f(70), "pasture_rent_per_acre": f(30),
        "hunting_lease_income": f(6000),
        "property_taxes": f(1800), "insurance_costs": f(900), "upkeep_costs": f(2400),
        "cap_rate": f(3), "purchase_price": f(850000)})
    v = a["values"]
    assert v["Farm income per year"] == 120 * 150 + 70 * 30 + 6000            # 26,100
    assert v["Net farm income per year"] == 26100 - 5100                       # 21,000
    assert v["Income value"] == pytest.approx(700000)
    assert v["Return on purchase price (%)"] == pytest.approx(2.47)
    assert v["Market value from comparables"] == 900000                        # market stays separate


def test_no_farm_income_line_without_its_inputs():
    a = TD.analysis("agricultural", {"acreage": f(50), "cropland_acres": f(50)})   # acres but no rent
    assert "Farm income per year" not in a["values"]
    a = TD.analysis("land", {"pasture_acres": f(10), "pasture_rent_per_acre": f(20)})  # land: farm income not used
    assert "Farm income per year" not in a["values"]


def test_seller_financing_terms_are_kept_with_limits(db_session):
    p = prop(db_session, org(db_session))
    TD.set_facts(db_session, p, U(), {"sf_down_payment": "4,500", "sf_rate": "9", "sf_term_years": 5})
    v = TD.view(db_session, p)["tools"]["values"]
    assert (v["sf_down_payment"]["value"], v["sf_rate"]["value"], v["sf_term_years"]["value"]) == (4500, 9, 5)
    with pytest.raises(ValueError):
        TD.set_facts(db_session, p, U(), {"sf_rate": 95})
