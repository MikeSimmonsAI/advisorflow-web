"""Houses only + the six ready-made starter searches.

Houses only scores vacant lots / land-only / commercial accounts 0 ("excluded")
from what the record actually says - a MISSING building value is not a vacant
lot. Starters are created as DRAFTS and never change an existing strategy."""
from types import SimpleNamespace

import pytest

from app.services.evosense import scoring as SC
from app.services.evosense import strategy as ST


def ok(r):
    assert r.status_code in (200, 201), "%s %s" % (r.status_code, r.text[:400])
    return r.json()


def prop(**kw):
    base = dict(property_type="single_family", appraisal_improvement_value=None, square_feet=None,
                year_built=None, equity_pct=None, estimated_value=None, estimated_value_source=None,
                appraisal_value=150000, appraisal_year=2026, appraisal_land_value=None,
                appraisal_source="dcad (DCAD 2026 certified appraisal (property-tax value))", appraisal_at=None,
                ownership_years=30, street_address="1 Main St", zip_code="75203", city="Dallas",
                county="Dallas", state="TX")
    base.update(kw)
    return SimpleNamespace(**base)


def strat(**kw):
    s = SimpleNamespace(houses_only=True, states='["TX"]', counties=None, cities=None, zips=None,
                        markets=None, property_types=None, excluded_signals=None, preferred_signals=None,
                        required_signals=None, min_equity_pct=None, min_value=None, max_value=None,
                        owner_geography="any", min_ownership_years=None, id="s1", version=1)
    for k, v in kw.items():
        setattr(s, k, v)
    return s


SIG = [{"signal_type": "ABSENTEE_OWNER", "freshness": "current", "sources": ["dcad"],
        "record_sources": ["dcad"], "observed_at": None}]


def test_land_only_reason_reads_the_record_not_a_guess():
    assert SC.land_only_reason(prop(property_type="land"))
    assert SC.land_only_reason(prop(property_type="commercial"))
    assert SC.land_only_reason(prop(appraisal_improvement_value=0))                 # documented $0
    assert SC.land_only_reason(prop(appraisal_improvement_value=None)) is None      # unknown != vacant
    assert SC.land_only_reason(prop(appraisal_improvement_value=0, square_feet=1200)) is None
    assert SC.land_only_reason(prop(appraisal_improvement_value=85000)) is None


def test_houses_only_strategy_scores_a_lot_zero_but_keeps_it(monkeypatch):
    lot = SC.property_opportunity(prop(appraisal_improvement_value=0), SIG, strat())
    assert lot["value"] == 0 and lot["label"] == "excluded"
    assert "Houses only" in lot["factors"][0]["label"]
    off = SC.property_opportunity(prop(appraisal_improvement_value=0), SIG, strat(houses_only=False))
    assert off["label"] != "excluded"
    house = SC.property_opportunity(prop(appraisal_improvement_value=90000), SIG, strat())
    assert house["label"] != "excluded"


def test_houses_only_is_a_strategy_field():
    clean, problems = ST.validate({"name": "x", "houses_only": "true"})
    assert not problems and clean["houses_only"] is True
    assert ST.BOOL_FIELDS[-1] == "houses_only"


def test_six_starters_with_safe_defaults():
    keys = [s["key"] for s in ST.starters_payload()]
    assert keys == ["behind_on_taxes", "tired_landlord", "problem_property", "long_time_owner",
                    "pre_foreclosure", "probate"]
    for k in keys:
        v = ST.starter_values(k)
        clean, problems = ST.validate(v)
        assert not problems, (k, problems)
        assert v["houses_only"] is True and "land" not in v["property_types"]
        assert "commercial" not in v["property_types"] and v["daily_budget_cents"] == 0
        assert v["outreach_policy"]["auto_outreach"] is False
    assert "paid off" in ST.STARTERS["long_time_owner"]["description"]


def test_starter_creates_a_draft_and_leaves_existing_strategies_alone(client, auth_headers):
    mine = ok(client.post("/wholesale/evosense/strategies", headers=auth_headers,
                          json={"name": "New Check Out", "states": ["TX"],
                                "property_types": ["single_family", "land", "commercial"]}))
    items = ok(client.get("/wholesale/evosense/strategy-starters", headers=auth_headers))["items"]
    assert len(items) == 6
    s = ok(client.post("/wholesale/evosense/strategy-starters", headers=auth_headers, json={"key": "tired_landlord"}))
    assert s["status"] == "draft" and s["houses_only"] is True and s["name"] == "Tired Landlord"
    assert "Houses only" in s["summary"] and "Collin" in s["counties"]
    again = ok(client.post("/wholesale/evosense/strategy-starters", headers=auth_headers, json={"key": "tired_landlord"}))
    assert again["name"] == "Tired Landlord (2)"
    after = ok(client.get("/wholesale/evosense/strategies/%s" % mine["id"], headers=auth_headers))
    assert after["version"] == mine["version"] and after["houses_only"] is False
    assert after["property_types"] == ["single_family", "land", "commercial"]
    assert client.post("/wholesale/evosense/strategy-starters", headers=auth_headers, json={"key": "nope"}).status_code == 404
