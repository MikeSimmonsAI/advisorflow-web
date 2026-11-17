"""Leads outside the four Texas metros are grouped by state; Dallas wording is DFW."""
from app.services.evosys_sales import grouping as G


def m(city, state=None):
    return G.match_market(None, city, state, city or "")[0]


def test_dallas_wording_is_dfw():
    assert m("Dallas Tx & Surrounding") == "dfw"
    assert m("North Texas") == "dfw"
    assert m("Denton & Surrounding Dfw") == "dfw"


def test_outside_metros_grouped_by_state():
    assert m("Jacksonville Fl & Surrounding") == "st_fl"
    assert m("Utah: Mill Creek, Sandy, Draper") == "st_ut"
    assert m("Memphis Tn Metro") == "st_tn"
    assert m("Philadelphia, Pa (Printed)") == "st_pa"
    assert m("Dover, Randolph, Rockaway", "NJ") == "st_nj"
    assert m("Lubbock", "TX") == "st_tx"


def test_unclear_stays_unclear():
    assert m("Wisconsin, Indiana & Illinois") is None
    assert m("Houston and Dallas") is None
    assert m("Bay Area") is None
    assert m(None) is None


def test_metros_win_over_state():
    assert m("Houston, Katy, Sugar Land", "TX") == "houston"
    assert m("Burleson", "TX") == "dfw"


def test_state_markets_listed_with_labels():
    ms = G.markets(None)
    keys = [x["key"] for x in ms]
    assert keys[:4] == ["dfw", "houston", "san_antonio", "austin"]
    assert G.label_of(ms, "st_fl") == "Florida"
    assert G.label_of(ms, "st_tx") == "Texas - other areas"
    assert len(keys) == len(set(keys))
