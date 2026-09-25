"""Buyer matching — the score, and the reasons behind it.

The rule this file exists to hold in place: A DIMENSION A BUY BOX DOES NOT
CONSTRAIN IS NOT COUNTED AGAINST THE BUYER. A sparse buy box must not score
badly for being sparse, or the product teaches people to invent constraints to
make a number go up.
"""

import json
from types import SimpleNamespace

from app.services.wholesale_matching import match_deal_to_buyers, score_buy_box


def box(**kw):
    base = dict(id="box1", is_active=True, markets=None, states=None, counties=None,
                cities=None, zips=None, property_types=None, strategies=None,
                min_price=None, max_price=None, min_beds=None, max_beds=None,
                min_baths=None, min_sqft=None, max_sqft=None, min_year_built=None,
                max_year_built=None, rehab_tolerance=None, min_spread=None)
    for key, value in kw.items():
        base[key] = json.dumps(value) if isinstance(value, list) else value
    return SimpleNamespace(**base)


def prop(**kw):
    base = dict(street_address="123 Test St", city="dallas", state="tx",
                county="dallas", market="dfw", zip_code="75201",
                property_type="single_family", bedrooms=3, bathrooms=2,
                square_feet=1500, year_built=1985)
    base.update(kw)
    return SimpleNamespace(**base)


def deal(**kw):
    base = dict(arv=300000, repair_estimate=30000, buyer_price=165000,
                contract_price=150000, proposed_offer=150000)
    base.update(kw)
    return SimpleNamespace(**base)


def buyer(bid="b1", boxes=None, **kw):
    base = dict(id=bid, is_active=True, do_not_contact=False, is_test=False,
                company_name="Buyer %s" % bid, contact_name=None,
                buy_boxes=boxes or [])
    base.update(kw)
    return SimpleNamespace(**base)


def dim(result, name):
    return next(f for f in result["factors"] if f["dimension"] == name)


# ── The silence rule ────────────────────────────────────────────────────────

def test_an_unconstrained_dimension_is_not_counted_either_way():
    result = score_buy_box(deal(), prop(), box(states=["tx"]))
    assert dim(result, "price")["matched"] is None
    assert dim(result, "property_type")["matched"] is None
    # Only geography was constrained, and it matched, so this is a clean 100.
    assert result["score"] == 100


def test_a_sparse_buy_box_is_not_punished_for_being_sparse():
    sparse = score_buy_box(deal(), prop(), box(states=["tx"]))
    detailed = score_buy_box(deal(), prop(),
                             box(states=["tx"], property_types=["single_family"],
                                 min_price=100000, max_price=200000))
    assert sparse["score"] == detailed["score"] == 100


# ── Geography ───────────────────────────────────────────────────────────────

def test_geography_is_answered_on_the_most_specific_field_the_box_names():
    """A buyer who named ZIPs is judged on ZIPs, not on all five fields."""
    result = score_buy_box(deal(), prop(zip_code="75201"),
                           box(zips=["75201"], states=["ok"]))
    assert dim(result, "geography")["matched"] is True
    assert "ZIP" in dim(result, "geography")["detail"]


def test_a_property_outside_the_stated_geography_is_disqualified_with_a_reason():
    result = score_buy_box(deal(), prop(state="ok", county="tulsa", city="tulsa",
                                        market="tulsa", zip_code="74103"),
                           box(states=["tx"]))
    assert result["disqualified"] is True
    assert "outside this buyer's stated geography" in result["disqualified_reason"]


def test_unknown_geography_is_not_treated_as_outside_it():
    """Missing is missing. Disqualifying on an absent county is a wrong answer."""
    result = score_buy_box(deal(), prop(county=None), box(counties=["dallas"]))
    assert result["disqualified"] is False
    assert dim(result, "geography")["matched"] is False


# ── Price ───────────────────────────────────────────────────────────────────

def test_price_outside_the_range_disqualifies():
    result = score_buy_box(deal(buyer_price=400000), prop(),
                           box(min_price=100000, max_price=200000))
    assert result["disqualified"] is True
    assert "price is outside" in result["disqualified_reason"]


def test_price_falls_back_through_buyer_then_contract_then_proposed():
    result = score_buy_box(
        SimpleNamespace(arv=300000, repair_estimate=0, buyer_price=None,
                        contract_price=None, proposed_offer=150000),
        prop(), box(min_price=100000, max_price=200000))
    assert dim(result, "price")["matched"] is True


# ── Rehab tolerance ─────────────────────────────────────────────────────────

def test_rehab_tolerance_is_an_ordering_not_an_equality():
    """A buyer who takes a full gut also takes light work."""
    light_job = deal(arv=300000, repair_estimate=6000)      # 2% -> light
    assert dim(score_buy_box(light_job, prop(), box(rehab_tolerance="full_gut")),
               "rehab")["matched"] is True
    heavy_job = deal(arv=300000, repair_estimate=70000)     # 23% -> heavy
    assert dim(score_buy_box(heavy_job, prop(), box(rehab_tolerance="light")),
               "rehab")["matched"] is False


# ── Spread ──────────────────────────────────────────────────────────────────

def test_minimum_spread_is_measured_against_arv_minus_price_minus_repairs():
    d = deal(arv=300000, repair_estimate=40000, buyer_price=200000)  # spread 60,000
    assert dim(score_buy_box(d, prop(), box(min_spread=50000)), "spread")["matched"] is True
    assert dim(score_buy_box(d, prop(), box(min_spread=80000)), "spread")["matched"] is False


# ── Ranking ─────────────────────────────────────────────────────────────────

def test_every_factor_carries_a_readable_reason():
    result = score_buy_box(deal(), prop(), box(states=["tx"], min_price=1, max_price=2))
    for factor in result["factors"]:
        assert factor["detail"] and isinstance(factor["detail"], str)


def test_a_buyer_with_several_boxes_keeps_their_best():
    rental_dallas = box(cities=["dallas"], rehab_tolerance="light")
    flip_fortworth = box(cities=["fort worth"], rehab_tolerance="full_gut")
    flip_fortworth.id = "box2"
    b = buyer(boxes=[flip_fortworth, rental_dallas])
    results = match_deal_to_buyers(deal(arv=300000, repair_estimate=60000),
                                   prop(city="dallas"), [b])
    assert results[0]["buy_box"].id == "box1"   # the Dallas box, not the Fort Worth one


def test_inactive_and_opted_out_buyers_are_not_scored_at_all():
    results = match_deal_to_buyers(
        deal(), prop(),
        [buyer("b1", [box(states=["tx"])], is_active=False),
         buyer("b2", [box(states=["tx"])], do_not_contact=True),
         buyer("b3", [box(states=["tx"])])])
    assert [r["buyer"].id for r in results] == ["b3"]


def test_a_buyer_with_no_buy_box_is_returned_with_an_explanation():
    """Not silently dropped — 'we have never asked them what they want' is useful."""
    results = match_deal_to_buyers(deal(), prop(), [buyer("b1", [])])
    assert len(results) == 1
    assert results[0]["score"] == 0
    assert "no active buy box" in results[0]["factors"][0]["detail"]


def test_qualified_buyers_rank_above_disqualified_ones():
    results = match_deal_to_buyers(
        deal(), prop(state="tx"),
        [buyer("outside", [box(states=["ok"])]),
         buyer("inside", [box(states=["tx"])])])
    assert results[0]["buyer"].id == "inside"
    assert results[-1]["buyer"].id == "outside"
    assert results[-1]["disqualified"] is True
