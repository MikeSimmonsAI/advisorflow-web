"""Type-specific details and analysis - synthetic properties only; nothing invented."""
import pytest

from app.models.evosense_models import EvoSenseProperty
from app.services.evosense import type_details as TD


def f(value, source="Entered by x", verified=False):
    return {"value": value, "source": source, "verified": verified}


def test_commercial_uses_the_income_approach_never_arv():
    a = TD.analysis("commercial", {"gross_income": f(240000), "vacancy_pct": f(10), "operating_expenses": f(66000),
                                   "cap_rate": f(8), "building_sqft": f(12000)})
    v = a["values"]
    assert v["Effective gross income"] == 216000 and v["NOI"] == 150000
    assert v["Income value"] == pytest.approx(1875000) and v["Value per building sq ft"] == pytest.approx(156.25)
    assert a["stage"] == "preliminary" and "ARV" not in " ".join(l["label"] for l in a["lines"])
    assert all(l["inputs"] for l in a["lines"])                         # every number shows its inputs


def test_multifamily_noi_and_per_unit_and_supported_only_with_evidence():
    ok = {k: f(v, verified=True) for k, v in {"gross_income": 120000, "vacancy_pct": 5, "operating_expenses": 48000,
                                              "cap_rate": 7.5, "unit_count": 10}.items()}
    a = TD.analysis("multifamily", ok)
    assert a["values"]["NOI"] == pytest.approx(66000) and a["values"]["Value per unit"] == pytest.approx(88000)
    assert a["stage"] == "supported"


def test_missing_inputs_say_so_and_nothing_is_defaulted():
    a = TD.analysis("commercial", {"gross_income": f(100000)})
    assert a["stage"] == "needs_information" and a["lines"] == []
    assert "Vacancy allowance (%)" in a["missing"] and "Annual operating expenses ($)" in a["missing"]
    assert "Cap rate (%)" in a["missing"] or a["values"] == {}


def test_land_price_per_acre_and_costs():
    a = TD.analysis("land", {"acreage": f(12.5), "comp_price_per_acre": f(40000), "development_costs": f(100000),
                             "holding_costs": f(20000)})
    assert a["values"]["Market value from comparables"] == 500000
    assert a["values"]["Exit value after costs"] == 380000


def test_agricultural_market_value_is_kept_apart_from_the_productivity_tax_value():
    a = TD.analysis("agricultural", {"acreage": f(100), "comp_price_per_acre": f(9000)},
                    appraisal={"value": 41000})
    assert a["values"]["Market value from comparables"] == 900000
    assert "NOT market value" in a["tax_value"]["label"] and a["tax_value"]["value"] == 41000
    assert "Rollback-tax exposure ($)" in a["missing"]


def test_residential_points_to_the_existing_deal_analysis_and_unknown_needs_a_type():
    assert TD.analysis("residential", {})["delegate"] == "deal_analysis"
    u = TD.analysis("unknown", {})
    assert u["stage"] == "needs_information" and "Property type" in u["missing"]


def test_spread_only_from_a_contract_price_and_an_investor_price():
    a = TD.analysis("land", {"purchase_price": f(200000)})
    assert "Potential assignment spread" not in a["values"]
    a = TD.analysis("land", {"purchase_price": f(200000), "buyer_price": f(235000), "acquisition_expenses": f(5000)})
    assert a["values"]["Potential assignment spread"] == 30000


def test_entered_facts_are_audited_and_a_list_never_overwrites_a_person(db_session, sample_org, sample_advisor):
    p = EvoSenseProperty(organization_id=sample_org.id, street_address="9 Synth Acreage Rd", city="Cleburne", state="TX",
                         is_test=True, status="new", property_category="land")
    db_session.add(p)
    db_session.flush()
    TD.set_facts(db_session, p, sample_advisor, {"acreage": "12.5"}, {"acreage": "survey 2019"})
    assert TD.import_claims(p, {"acreage": 15, "zoning": "AG"}) == 1        # zoning only
    v = TD.view(db_session, p)
    by = {x["key"]: x for x in v["section"]}
    assert by["acreage"]["value"] == 12.5 and by["acreage"]["verified"] is True
    assert by["zoning"]["source"] == "Imported list (claim)"
    assert by["utilities"]["source"] == "Not on record" and by["utilities"]["value"] is None
    with pytest.raises(ValueError):
        TD.set_facts(db_session, p, sample_advisor, {"cap_rate": "75"})
    with pytest.raises(ValueError):
        TD.set_facts(db_session, p, sample_advisor, {"nonsense": 1})


def test_an_import_brings_type_and_type_facts_as_the_lists_claim(db_session, sample_org):
    from app.services.evosense import hunt as HU
    from app.services.evosense import property_class as PC
    csv_text = ("street_address,city,state,zip_code,property_type,property_subtype,acreage,units,noi,cap_rate,zoning\n"
                "77 Synth Commerce Dr,Dallas,TX,75201,Commercial,Office,1.2,,150000,8,CR\n"
                "78 Synth Ranch Rd,Cleburne,TX,76031,Ranch,,120,,,abc,AG\n")
    out = HU.import_csv(db_session, sample_org.id, csv_text, user=None, filename="synthetic_types.csv")
    assert out["counts"]["created"] == 2
    assert any("cap_rate ignored" in r["reason"] for r in out["rejected"])     # bad value named, row kept
    props = {p.street_address: p for p in db_session.query(EvoSenseProperty).all()}
    office, ranch = props["77 Synth Commerce Dr"], props["78 Synth Ranch Rd"]
    PC.classify_properties(db_session, sample_org.id, [office, ranch])
    assert (office.property_category, office.property_class_confidence) == ("commercial", "list_claim")
    assert ranch.property_category == "agricultural"
    v = TD.view(db_session, office)
    noi = {f["key"]: f for f in v["section"]}["noi"]
    assert noi["value"] == 150000 and noi["source"] == "Imported list (claim)" and noi["verified"] is False
    assert v["analysis"]["values"]["Income value"] == 1875000 and v["analysis"]["stage"] == "preliminary"
    pv = HU.preview_csv(db_session, sample_org.id, csv_text)
    assert pv["property_types"] == {"commercial": 1, "agricultural": 1} and pv["type_columns"]["acreage"] == 2


def test_non_residential_types_carry_a_closing_checklist_and_a_contract_warning(db_session, sample_org):
    p = EvoSenseProperty(organization_id=sample_org.id, street_address="5 Synth Ag Rd", city="Cleburne", state="TX",
                         is_test=True, status="new", property_category="agricultural")
    db_session.add(p)
    db_session.flush()
    v = TD.view(db_session, p)
    assert any("rollback" in x for x in v["closing_checklist"]) and "written for houses" in v["contract_note"]
    p.property_category = "residential"
    assert TD.view(db_session, p)["contract_note"] is None
