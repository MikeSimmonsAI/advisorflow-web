"""The wholesale arithmetic, tested without a database.

These are the numbers somebody is going to make an offer on, so they are tested
directly rather than through an endpoint. Every function under test is pure, so
the fixtures here are plain objects — which is the point of keeping the math out
of the models in the first place.

THE THEME OF THIS FILE IS WHAT THE MODULE REFUSES TO INVENT. Most of these tests
assert a None, and each of those Nones is a place where a plausible default
would have produced a number a person would have believed.
"""

from types import SimpleNamespace

import pytest

from app.services.wholesale_analysis import (
    arv_from_comps, calculate_offer, comp_statistics, deal_summary, money,
    qualify_seller,
)


def comp(price=None, sqft=None, included=True):
    return SimpleNamespace(sale_price=price, square_feet=sqft, included=included)


def settings(**kw):
    base = dict(investor_percentage=70.0, default_wholesale_fee=10000,
                transaction_cost_percent=3.0, transaction_cost_flat=0,
                min_buyer_margin=None, high_threshold=70, medium_threshold=45,
                review_below_completeness=40)
    base.update(kw)
    return SimpleNamespace(**base)


# ── money() ─────────────────────────────────────────────────────────────────

def test_money_returns_none_for_unparseable_rather_than_zero():
    """A zero here would silently raise every offer this module calculates."""
    assert money(None) is None
    assert money("") is None
    assert money("not a number") is None
    assert money(True) is None
    assert money("$185,000") == 185000
    assert money("185000.50") == money("185000.50")


# ── ARV ─────────────────────────────────────────────────────────────────────

def test_arv_from_no_comps_is_none_not_a_guess():
    result = arv_from_comps([])
    assert result["arv"] is None
    assert result["method"] is None
    assert "No comps are included" in result["warnings"][0]


def test_arv_ignores_excluded_comps():
    comps = [comp(200000, 1000), comp(900000, 1000, included=False)]
    result = arv_from_comps(comps, subject_sqft=1000)
    assert result["arv"] == 200000
    assert result["comp_count"] == 1


def test_arv_uses_median_price_per_sqft_when_sizes_are_known():
    # $/sqft: 200, 180, 220 -> median 200 -> 200 x 1500 = 300,000
    comps = [comp(200000, 1000), comp(180000, 1000), comp(220000, 1000)]
    result = arv_from_comps(comps, subject_sqft=1500)
    assert result["arv"] == 300000
    assert result["method"] == "comps:median_psf"


def test_arv_falls_back_to_median_price_and_says_so():
    comps = [comp(200000), comp(240000), comp(260000)]
    result = arv_from_comps(comps, subject_sqft=1500)
    assert result["arv"] == 240000
    assert result["method"] == "comps:median_price"
    assert any("Fell back" in w for w in result["warnings"])


def test_arv_warns_when_the_evidence_is_thin():
    result = arv_from_comps([comp(200000, 1000)], subject_sqft=1000)
    assert result["arv"] == 200000
    assert any("weak evidence" in w for w in result["warnings"])


def test_arv_with_no_priced_comps_is_none():
    result = arv_from_comps([comp(None, 1200), comp(0, 1100)], subject_sqft=1200)
    assert result["arv"] is None


# ── The offer formula ───────────────────────────────────────────────────────

def test_mao_shows_its_working():
    # 300,000 x 70% = 210,000 - 40,000 repairs - 9,000 costs - 10,000 fee
    result = calculate_offer(300000, 40000, 70, 10000, 3.0, 0)
    assert result["mao"] == 151000
    labels = [s["label"] for s in result["steps"]]
    assert labels[0] == "ARV"
    assert labels[-1] == "= maximum allowable offer"


def test_investor_percentage_is_an_input_not_a_constant():
    """The 70% rule is this organization's setting, not a platform truth."""
    at_70 = calculate_offer(300000, 0, 70, 0, 0, 0)["mao"]
    at_65 = calculate_offer(300000, 0, 65, 0, 0, 0)["mao"]
    at_80 = calculate_offer(300000, 0, 80, 0, 0, 0)["mao"]
    assert at_70 == 210000
    assert at_65 == 195000
    assert at_80 == 240000


def test_no_arv_means_no_offer_and_an_explanation():
    result = calculate_offer(None, 40000, 70, 10000)
    assert result["mao"] is None
    assert result["blocked"] == "no_arv"
    assert "Nothing is assumed" in result["warnings"][0]


def test_no_investor_percentage_means_no_offer():
    result = calculate_offer(300000, 0, None, 10000)
    assert result["mao"] is None
    assert result["blocked"] == "no_investor_percentage"


def test_missing_repairs_are_treated_as_zero_and_warned_about_loudly():
    result = calculate_offer(300000, None, 70, 10000, 0, 0)
    assert result["mao"] == 200000
    assert any("as if repairs were zero" in w for w in result["warnings"])


def test_a_negative_mao_is_returned_not_clamped():
    """'This deal does not work' is a real answer and has to be sayable."""
    result = calculate_offer(100000, 90000, 70, 10000, 0, 0)
    assert result["mao"] < 0
    assert any("does not work" in w for w in result["warnings"])


def test_transaction_costs_combine_percentage_and_flat():
    result = calculate_offer(200000, 0, 100, 0, 2.0, 1500)
    # 200,000 - (4,000 + 1,500)
    assert result["mao"] == 194500


# ── deal_summary ────────────────────────────────────────────────────────────

def test_summary_prefers_the_deal_snapshot_over_current_settings():
    deal = SimpleNamespace(
        arv=300000, arv_source="manual", arv_method="manual",
        repair_estimate=20000, repair_estimate_source="manual", repair_notes=None,
        investor_percentage_used=60, desired_wholesale_fee=15000,
        transaction_costs=None, max_allowable_offer=None, proposed_offer=150000,
        contract_price=None, buyer_price=None, assignment_fee=None,
        wholesale_fee_collected=None)
    out = deal_summary(deal, settings(investor_percentage=70))
    assert out["investor_percentage"] == 60
    assert out["investor_percentage_source"] == "deal"
    # 300,000 x 60% = 180,000 - 20,000 - 9,000 - 15,000
    assert out["max_allowable_offer"] == 136000


def test_summary_computes_spread_and_buyer_margin():
    deal = SimpleNamespace(
        arv=300000, arv_source="estimated", arv_method="comps:median_psf",
        repair_estimate=40000, repair_estimate_source="manual", repair_notes=None,
        investor_percentage_used=70, desired_wholesale_fee=10000,
        transaction_costs=None, max_allowable_offer=None, proposed_offer=150000,
        contract_price=150000, buyer_price=165000, assignment_fee=None,
        wholesale_fee_collected=None)
    out = deal_summary(deal, settings())
    assert out["estimated_spread"] == 15000          # 165,000 - 150,000
    assert out["estimated_buyer_margin"] == 95000    # 300,000 - 165,000 - 40,000


def test_summary_flags_a_thin_buyer_margin_without_blocking_it():
    deal = SimpleNamespace(
        arv=200000, arv_source=None, arv_method=None,
        repair_estimate=50000, repair_estimate_source=None, repair_notes=None,
        investor_percentage_used=70, desired_wholesale_fee=10000,
        transaction_costs=None, max_allowable_offer=None, proposed_offer=None,
        contract_price=120000, buyer_price=140000, assignment_fee=None,
        wholesale_fee_collected=None)
    out = deal_summary(deal, settings(min_buyer_margin=25000))
    assert out["estimated_buyer_margin"] == 10000
    assert any("below this" in w for w in out["warnings"])


# ── Qualification ───────────────────────────────────────────────────────────

def profile(**kw):
    base = dict(considering_selling=None, is_available=None, asking_price=None,
                timeline=None, property_condition=None, occupancy=None,
                motivation=None, decision_makers=None, needs_human=False,
                needs_human_reason=None)
    base.update(kw)
    return SimpleNamespace(**base)


def lead(**kw):
    base = dict(status="new", manual_flag=None, phone="+12145550000",
                email="a@b.com", allow_sms=None, allow_email=None, allow_voice=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_a_dnc_contact_is_excluded_before_anything_else_is_considered():
    result = qualify_seller(profile(considering_selling=True, timeline="asap"),
                            None, settings(), lead(status="dnc"))
    assert result["band"] == "excluded"
    assert result["score"] == 0
    assert "do-not-contact" in result["reasons"][0]


def test_every_channel_denied_is_excluded():
    result = qualify_seller(profile(considering_selling=True), None, settings(),
                            lead(allow_sms=False, allow_email=False, allow_voice=False))
    assert result["band"] == "excluded"


def test_not_selling_is_excluded_regardless_of_score():
    result = qualify_seller(profile(considering_selling=False, timeline="asap",
                                    property_condition="distressed"),
                            None, settings(), lead())
    assert result["band"] == "excluded"


def test_a_thin_profile_goes_to_review_rather_than_being_banded():
    """A confident band computed from two known facts is a confident wrong answer."""
    result = qualify_seller(profile(considering_selling=True), None,
                            settings(review_below_completeness=40), lead())
    assert result["band"] == "review"
    assert result["completeness"] < 40
    assert any("floor" in r for r in result["reasons"])


def test_a_complete_motivated_seller_bands_high():
    result = qualify_seller(
        profile(considering_selling=True, timeline="asap", asking_price=120000,
                property_condition="poor", occupancy="vacant",
                motivation="inherited, wants it gone",
                decision_makers="just me"),
        None, settings(), lead())
    assert result["completeness"] == 100
    assert result["band"] == "high"
    assert result["score"] >= 70


def test_thresholds_come_from_the_organization():
    p = profile(considering_selling=True, timeline="90_days", asking_price=1,
                property_condition="good", occupancy="tenant", motivation="x",
                decision_makers="y")
    strict = qualify_seller(p, None, settings(high_threshold=95, medium_threshold=90), lead())
    loose = qualify_seller(p, None, settings(high_threshold=20, medium_threshold=10), lead())
    assert strict["band"] == "low"
    assert loose["band"] == "high"


def test_an_asking_price_above_the_offer_ceiling_costs_points():
    deal_cheap = SimpleNamespace(max_allowable_offer=100000)
    common = dict(considering_selling=True, timeline="asap",
                  property_condition="fair", occupancy="vacant",
                  motivation="x", decision_makers="y")
    at_ceiling = qualify_seller(profile(asking_price=95000, **common),
                                deal_cheap, settings(), lead())
    way_over = qualify_seller(profile(asking_price=180000, **common),
                              deal_cheap, settings(), lead())
    assert at_ceiling["score"] > way_over["score"]
    assert any("at or below" in r for r in at_ceiling["reasons"])


def test_needs_human_routes_to_review_whatever_the_score():
    result = qualify_seller(
        profile(considering_selling=True, timeline="asap", asking_price=1,
                property_condition="poor", occupancy="vacant", motivation="x",
                decision_makers="y", needs_human=True,
                needs_human_reason="angry message"),
        None, settings(), lead())
    assert result["band"] == "review"


# ── comp_statistics ──────────────────────────────────────────────

def priced(price, sqft, included=True, cid=None):
    return SimpleNamespace(id=cid, sale_price=price, square_feet=sqft,
                           included=included)


def test_comp_statistics_on_an_empty_set_is_all_none_not_zero():
    """A dash on the screen. Zero would read as 'these houses are worthless'."""
    stats = comp_statistics([])
    assert stats["median_price_per_sqft"] is None
    assert stats["average_price_per_sqft"] is None
    assert stats["median_sale_price"] is None
    assert stats["low_price_per_sqft"] is None
    assert stats["included_count"] == 0


def test_comp_statistics_reports_median_and_average_separately():
    """They are shown side by side precisely so a skewed set is visible."""
    stats = comp_statistics([priced(100000, 1000),    # 100/sqft
                             priced(110000, 1000),    # 110/sqft
                             priced(600000, 1000)])   # 600/sqft, the outlier
    assert stats["median_price_per_sqft"] == 110
    assert stats["average_price_per_sqft"] == 270
    assert stats["low_price_per_sqft"] == 100
    assert stats["high_price_per_sqft"] == 600


def test_comp_statistics_excludes_what_the_user_excluded():
    """Excluding is arithmetic. The row stays; it stops counting."""
    stats = comp_statistics([priced(100000, 1000),
                             priced(600000, 1000, included=False)])
    assert stats["included_count"] == 1
    assert stats["excluded_count"] == 1
    assert stats["median_price_per_sqft"] == 100
    # The excluded row still gets its own $/sqft, because it is still shown.
    assert len(stats["rows"]) == 2


def test_comp_statistics_marks_each_comp_against_the_median():
    stats = comp_statistics([priced(100000, 1000, cid="a"),
                             priced(200000, 1000, cid="b"),
                             priced(300000, 1000, cid="c")])
    by_id = {r["id"]: r for r in stats["rows"]}
    assert by_id["a"]["vs_median_pct"] == -50
    assert by_id["b"]["vs_median_pct"] == 0
    assert by_id["c"]["vs_median_pct"] == 50


def test_comp_statistics_gives_no_per_sqft_when_no_comp_has_a_size():
    stats = comp_statistics([priced(100000, None), priced(120000, None)])
    assert stats["median_price_per_sqft"] is None
    assert stats["median_sale_price"] == 110000
    assert stats["sized_count"] == 0


def test_comp_statistics_ignores_a_zero_or_junk_square_footage():
    """Dividing by a typo is how a comp comes out at $2,000,000 per foot."""
    stats = comp_statistics([priced(100000, 0), priced(100000, "abc"),
                             priced(100000, 1000)])
    assert stats["sized_count"] == 1
    assert stats["median_price_per_sqft"] == 100


def test_comp_statistics_subject_is_expressed_at_its_own_arv():
    stats = comp_statistics([priced(100000, 1000)], subject_sqft=1500,
                            subject_value=180000)
    assert stats["subject_square_feet"] == 1500
    assert stats["subject_price_per_sqft"] == 120


def test_comp_statistics_subject_without_an_arv_has_no_per_sqft():
    """The subject column is never filled from the comps it is compared with."""
    stats = comp_statistics([priced(100000, 1000)], subject_sqft=1500)
    assert stats["subject_square_feet"] == 1500
    assert stats["subject_price_per_sqft"] is None
