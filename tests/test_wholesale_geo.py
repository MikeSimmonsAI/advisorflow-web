"""Geography normalization — and the line it refuses to cross.

Two halves, and the second matters more than the first:

    what it SHOULD match   spelling, punctuation, case, ZIP+4, "County",
                           "Texas"/"TX", "Ft."/"Fort"
    what it MUST NOT       the city Dallas and the county Dallas are not
                           asserted to be the same place, and two same-named
                           places in different states are not the same place

A normalizer that is too eager is worse than one that is too strict: a buyer
who receives a deal outside their box stops opening the emails.
"""

from types import SimpleNamespace

from app.services.wholesale_geo import (
    geography_verdict, match_place, normalize_place, normalize_state,
    normalize_zip, resolve_containment,
)


def prop(**kw):
    base = dict(city="Dallas", county="Dallas", state="TX", zip_code="75201",
                market="DFW")
    base.update(kw)
    return SimpleNamespace(**base)


# ── States ──────────────────────────────────────────────────────────────────

def test_state_names_and_codes_are_the_same_state():
    assert normalize_state("Texas") == "TX"
    assert normalize_state("texas") == "TX"
    assert normalize_state("  TX  ") == "TX"
    assert normalize_state("tx") == "TX"
    assert normalize_state("New Mexico") == "NM"


def test_an_unrecognised_state_is_none_not_passed_through():
    """Otherwise a typo matches another typo, which is worse than no match."""
    assert normalize_state("Republic of Texas") is None
    assert normalize_state("ZZ") is None
    assert normalize_state("") is None
    assert normalize_state(None) is None


# ── ZIPs ────────────────────────────────────────────────────────────────────

def test_zip_plus_four_is_the_same_postal_area():
    assert normalize_zip("76107-1234") == "76107"
    assert normalize_zip("761071234") == "76107"
    assert normalize_zip("76107") == "76107"
    assert normalize_zip(" 76107 ") == "76107"


def test_something_that_is_not_a_zip_is_none():
    assert normalize_zip("ABCDE") is None
    assert normalize_zip("1234") is None
    assert normalize_zip(None) is None


# ── Place names ─────────────────────────────────────────────────────────────

def test_county_suffixes_are_stripped():
    assert normalize_place("Dallas County") == ("dallas", None)
    assert normalize_place("Dallas Co.") == ("dallas", None)
    assert normalize_place("Orleans Parish") == ("orleans", None)
    assert normalize_place("Juneau Borough") == ("juneau", None)


def test_a_trailing_state_is_split_off_however_it_was_typed():
    assert normalize_place("Dallas, TX") == ("dallas", "TX")
    assert normalize_place("Dallas TX") == ("dallas", "TX")
    assert normalize_place("Dallas County, Texas") == ("dallas", "TX")
    assert normalize_place("  FORT WORTH ,  texas ") == ("fort worth", "TX")


def test_common_abbreviations_resolve():
    assert normalize_place("Ft. Worth") == ("fort worth", None)
    assert normalize_place("Ft Worth, TX") == ("fort worth", "TX")
    assert normalize_place("St. Louis") == ("saint louis", None)


def test_a_place_that_is_only_a_state_name_stays_a_place():
    """Washington the city must not evaporate into a state and an empty name."""
    assert normalize_place("Washington") == ("washington", None)


# ── Matching, with reasons ──────────────────────────────────────────────────

def test_dallas_county_in_the_buy_box_matches_a_county_column_of_dallas():
    """The Phase 1 defect this whole module exists to fix."""
    result = match_place("counties", ["Dallas County, TX"], "Dallas", "TX")
    assert result["verdict"] == "match"
    assert "Dallas" in result["detail"]
    assert "Dallas County, TX" in result["detail"]


def test_zip_plus_four_matches_and_the_reason_says_so():
    result = match_place("zips", ["76107"], "76107-1234")
    assert result["verdict"] == "match"
    assert "76107" in result["detail"]


def test_the_same_city_name_in_a_different_state_is_not_a_match():
    """Dallas, GA is a real place and is not Dallas, TX."""
    result = match_place("cities", ["Dallas, TX"], "Dallas", "GA")
    assert result["verdict"] == "outside"
    assert "different state" in result["detail"]


def test_a_missing_property_field_is_unknown_not_outside():
    result = match_place("counties", ["Dallas"], None, "TX")
    assert result["verdict"] == "unknown"
    assert "none on file" in result["detail"]


def test_an_unconstrained_field_is_neither():
    result = match_place("cities", [], "Dallas", "TX")
    assert result["verdict"] == "unconstrained"


def test_every_verdict_carries_a_sentence_a_person_can_read():
    for field, criteria, value in (("zips", ["75201"], "75201"),
                                   ("cities", ["Austin"], "Dallas"),
                                   ("counties", [], "Dallas"),
                                   ("states", ["TX"], "OK")):
        result = match_place(field, criteria, value, "TX")
        assert result["detail"] and result["detail"][0].isupper()
        assert result["detail"].endswith(".")


# ── The refusal that keeps this honest ──────────────────────────────────────

def test_the_module_does_not_claim_a_city_is_in_a_county():
    """No dataset backs that claim, so the code does not make it.

    `resolve_containment` is the documented seam for a real geographic dataset
    and returns None until one exists. A lookup table typed from memory would be
    wrong somewhere and wrong invisibly.
    """
    assert resolve_containment("fort worth", "tarrant", "TX") is None
    # And the consequence: a county criterion is NOT satisfied by a city column.
    result = match_place("counties", ["Tarrant"], None, "TX")
    assert result["verdict"] == "unknown"


def test_a_city_criterion_is_never_answered_by_the_county_column():
    verdict = geography_verdict({"cities": ["Dallas"]},
                                prop(city=None, county="Dallas"))
    assert verdict["verdict"] == "unknown"


# ── The whole-box verdict ───────────────────────────────────────────────────

def test_a_match_on_any_constrained_field_is_a_match():
    """Phase 1 answered only on the most specific field and discarded the rest.

    A buy box listing ZIPs AND a state means "these ZIPs, and Texas generally".
    A property elsewhere in Texas satisfies it.
    """
    verdict = geography_verdict(
        {"zips": ["76107", "76109"], "states": ["TX"]},
        prop(zip_code="75201", state="TX"))
    assert verdict["verdict"] == "match"
    assert verdict["matched"] == "states"


def test_the_most_specific_match_is_the_one_reported():
    verdict = geography_verdict({"zips": ["75201"], "states": ["TX"]}, prop())
    assert verdict["matched"] == "zips"
    assert "75201" in verdict["detail"]


def test_outside_requires_every_constrained_field_to_disagree():
    verdict = geography_verdict({"states": ["OK"], "cities": ["Tulsa"]},
                                prop(state="TX", city="Dallas"))
    assert verdict["verdict"] == "outside"


def test_one_unknown_field_makes_the_answer_unknown_not_outside():
    """A blank county column must never exclude a buyer from the list."""
    verdict = geography_verdict({"states": ["OK"], "counties": ["Tulsa"]},
                                prop(state="TX", county=None))
    assert verdict["verdict"] == "unknown"


def test_no_geography_at_all_is_unconstrained():
    verdict = geography_verdict({}, prop())
    assert verdict["verdict"] == "unconstrained"


# ── Through the matching engine ─────────────────────────────────────────────

def test_the_matcher_uses_the_normalizer_end_to_end():
    import json

    from app.services.wholesale_matching import score_buy_box

    box = SimpleNamespace(
        id="b", is_active=True,
        counties=json.dumps(["Dallas County, Texas"]), zips=None, cities=None,
        markets=None, states=None, property_types=None, strategies=None,
        min_price=None, max_price=None, min_beds=None, max_beds=None,
        min_baths=None, min_sqft=None, max_sqft=None, min_year_built=None,
        max_year_built=None, rehab_tolerance=None, min_spread=None)
    deal = SimpleNamespace(arv=300000, repair_estimate=20000, buyer_price=150000,
                           contract_price=None, proposed_offer=None)

    result = score_buy_box(deal, prop(county="Dallas"), box)
    geo = next(f for f in result["factors"] if f["dimension"] == "geography")
    assert geo["matched"] is True
    assert result["score"] == 100
    assert result["disqualified"] is False
