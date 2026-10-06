"""Lock the SCI physical-campus mapping so edits cannot split co-located
campuses or merge separate addresses. Pure data checks; no DB, no contacts."""
import csv
import os

from app.services.programs import campuses

_ALIASES = os.path.join(os.path.dirname(campuses._CSV), "sci_location_aliases.csv")

# campus -> exactly these entities (the 9 shared campuses)
SHARED = {
    "alabama-heritage-montgomery": {"Alabama Heritage Cemetery", "Alabama Heritage Funeral Home"},
    "bayview-pensacola": {"Bayview Fisher-Pou Chapel", "Bayview Memorial Park"},
    "centuries-memorial-shreveport": {"Centuries Memorial Funeral Home", "Centuries Memorial Park"},
    "eastern-gate-memorial-pensacola": {"Eastern Gate Memorial Funeral Home", "Eastern Gate Memorial Gardens"},
    "elmwood-birmingham": {"Elmwood Cemetery & Mausoleum", "Johns-Ridout's Mortuary-Elmwood Chapel"},
    "greenwood-montgomery": {"Greenwood Serenity Memorial Gardens", "White Chapel-Greenwood Funeral Home"},
    "pine-crest-mobile": {"Pine Crest Cemetery", "Pine Crest Funeral Home"},
    "southern-heritage-pelham": {"Southern Heritage Cemetery", "Southern Heritage Funeral Home"},
    "sunset-brown-service-northport": {"Sunset Brown-Service Funeral Home", "Sunset Brown-Service Memorial Park"},
}
UNVERIFIED = {"Oaklawn Central Care Center", "Pine Crest Cemetery West"}


def _by_campus():
    out = {}
    for r in campuses.load_grouping():
        out.setdefault(r["Campus"], []).append(r)
    return out


def test_locked_counts():
    rows = campuses.load_grouping()
    by = _by_campus()
    assert len(rows) == 39 and len({r["Location"] for r in rows}) == 39
    assert len(by) == 30                       # = standard local numbers eventually required
    assert sum(len(v) == 2 for v in by.values()) == 9
    assert sum(len(v) == 1 for v in by.values()) == 21


def test_shared_campuses_are_exactly_the_colocated_pairs():
    shared = {k: {r["Location"] for r in v} for k, v in _by_campus().items() if len(v) > 1}
    assert shared == SHARED


def test_separate_addresses_are_not_merged():
    by = _by_campus()
    loc = {r["Location"]: r["Campus"] for r in campuses.load_grouping()}
    # Parkhill (4161 Macon Rd) and Striffler-Hamby (4071 Macon Rd) are different addresses
    assert loc["Parkhill Cemetery"] != loc["Striffler-Hamby Mortuary"]
    # unverified entities are never silently folded into a verified campus
    for name in UNVERIFIED:
        assert len(by[loc[name]]) == 1
    assert loc["Oaklawn Central Care Center"] != loc["Oak Lawn Funeral Home"]
    assert loc["Pine Crest Cemetery West"] != loc["Pine Crest Cemetery"]


def test_unverified_flag_is_exactly_the_two_addressless_entities():
    rows = campuses.load_grouping()
    assert {r["Location"] for r in rows if r["Address Status"] == "unverified"} == UNVERIFIED
    for r in rows:
        if r["Location"] in UNVERIFIED:
            assert not (r["City"] or r["State"] or r["Area Code"])   # no invented address
        else:
            assert r["City"] and r["State"] and r["Area Code"]


def test_each_campus_is_one_place():
    for k, v in _by_campus().items():
        assert len({(r["City"], r["State"], r["Area Code"]) for r in v}) == 1, k


def test_every_location_has_its_own_alias():
    locs = {r["Location"] for r in campuses.load_grouping()}
    with open(_ALIASES, encoding="utf-8") as f:
        al = list(csv.DictReader(f))
    assert len(al) == 39 and {a["Location"] for a in al} == locs
    assert len({a["Alias"] for a in al}) == 39      # identity stays per entity
