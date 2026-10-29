"""Buyer matching respects property categories. Synthetic objects only."""
import json
from types import SimpleNamespace as NS

from app.services import wholesale_matching as M


def box(**kw):
    base = dict(is_active=True, markets=None, states=None, counties=None, cities=None, zips=None,
                property_types=None, strategies=None, min_price=None, max_price=None, min_beds=None, max_beds=None,
                min_baths=None, min_sqft=None, max_sqft=None, min_year_built=None, max_year_built=None,
                rehab_tolerance=None, min_spread=None, min_acres=None, max_acres=None, min_units=None, max_units=None,
                min_building_sqft=None, max_building_sqft=None, min_cap_rate=None)
    base.update(kw)
    for k in ("property_types", "counties", "cities", "states"):
        if isinstance(base[k], list):
            base[k] = json.dumps(base[k])
    return NS(**base)


def prop(**kw):
    base = dict(property_type=None, property_category=None, property_subtype=None, acreage=None, unit_count=None,
                building_sqft=None, county="Johnson", city="Cleburne", state="TX", zip_code="76031",
                bedrooms=None, bathrooms=None, square_feet=None, year_built=None, market=None)
    base.update(kw)
    return NS(**base)


DEAL = NS(buyer_price=None, contract_price=None, proposed_offer=None, arv=None, repair_estimate=None)


def test_land_investor_5_to_50_acres_in_johnson():
    b = box(property_types=["land"], counties=["Johnson"], min_acres=5, max_acres=50)
    ok = M.score_buy_box(DEAL, prop(property_category="land", acreage=20), b)
    small = M.score_buy_box(DEAL, prop(property_category="land", acreage=2), b)
    assert not ok["disqualified"] and ok["score"] > small["score"]
    assert any(f["dimension"] == "acres" and f["matched"] for f in ok["factors"])


def test_an_unrelated_category_is_never_matched():
    b = box(property_types=["land"])
    r = M.score_buy_box(DEAL, prop(property_category="multifamily", unit_count=40), b)
    assert r["disqualified"] and "does not buy this kind" in r["disqualified_reason"]


def test_industrial_by_category_or_subtype_and_multifamily_by_units():
    assert not M.score_buy_box(DEAL, prop(property_category="industrial", property_subtype="warehouse",
                                          county="Dallas", city="Dallas"),
                               box(property_types=["warehouse"]))["disqualified"]
    mf = box(property_types=["multifamily"], min_units=10, max_units=100)
    r = M.score_buy_box(DEAL, prop(property_category="multifamily", unit_count=24), mf)
    assert any(f["dimension"] == "units" and f["matched"] for f in r["factors"])


def test_old_house_types_still_match_and_unknown_is_missing_not_a_miss_or_disqualification():
    """buyer_match/v2 (owner rule 2026-10-27): an unknown property fact is neither
    a match nor a miss - it leaves the score and is listed as missing information."""
    assert not M.score_buy_box(DEAL, prop(property_type="single_family"), box(property_types=["single_family"]))["disqualified"]
    assert not M.score_buy_box(DEAL, prop(property_type="single_family"), box(property_types=["residential"]))["disqualified"]
    r = M.score_buy_box(DEAL, prop(), box(property_types=["agricultural"]))
    assert not r["disqualified"] and any(f["dimension"] == "property_type" and f["matched"] is None
                                         and f.get("unknown") for f in r["factors"])
    assert "The property has no type on file." in r["missing"] and r["version"] == "buyer_match/v2"
