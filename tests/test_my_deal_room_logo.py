# -*- coding: utf-8 -*-
"""My Deal Room logo: the owner-supplied asset in the existing slots only.

Guards the four things the logo installation promised:
  1. The tracked assets are the owner's file (the mark is a crop of it, not new art).
  2. The EvoSys Wholesale rail shows it in the SAME 180x72 slot; BookaBoost's
     rail, EvoSys Pro's own logo and every non-Wholesale favicon are untouched.
  3. The tab icon becomes the My Deal Room mark on My Deal Room screens and
     goes back to the EvoSys Pro icon on the way out.
  4. The investor portal's 30x30 brand mark shows it for My Deal Room only.
"""
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from tests.test_bookaboost_invites import w  # noqa: F401
from tests.test_deal_room import room  # noqa: F401
from tests.test_wholesale_access_builder import sent, ws  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
BRANDS = ROOT / "frontend" / "public" / "brands"
SRC = ROOT / "frontend" / "src"
LOGO = "/brands/my-deal-room-logo.svg"
MARK = "/brands/my-deal-room-mark.svg"
NS = "{http://www.w3.org/2000/svg}"


# -- 1. assets ---------------------------------------------------------------

def test_logo_asset_is_the_owner_supplied_svg():
    root = ET.parse(BRANDS / "my-deal-room-logo.svg").getroot()
    assert root.get("viewBox") == "0 0 1200 330"
    words = [t.text for t in root.iter(NS + "text")]
    assert words == ["My", "Deal", "Room", "REAL ESTATE DEAL PLATFORM"]
    assert "<script" not in (BRANDS / "my-deal-room-logo.svg").read_text().lower()


def test_mark_is_a_crop_of_the_logo_not_new_artwork():
    logo = ET.parse(BRANDS / "my-deal-room-logo.svg").getroot()
    mark = ET.parse(BRANDS / "my-deal-room-mark.svg").getroot()
    assert mark.get("viewBox") == "30 24 280 280"
    assert not list(mark.iter(NS + "text"))
    def tile(r):
        g = r.find(NS + "g")
        g.tail = None
        return ET.tostring(g)
    assert tile(mark) == tile(logo), "the house tile must be byte-identical to the owner's file"


# -- 2/3. config boundaries --------------------------------------------------

def _brand_block(theme_src, key):
    m = re.search(r"\[THEMES\.%s\]: \{(.*?)\n  \}," % key, theme_src, re.S)
    assert m, key
    return m.group(1)


def test_only_evosys_wholesale_points_at_the_logo():
    t = (SRC / "theme.js").read_text(encoding="utf-8-sig")
    evo = _brand_block(t, "EVOSYSPRO")
    assert "wholesaleLogoUrl: '%s'" % LOGO in evo
    assert "wholesaleFaviconUrl: '%s'" % MARK in evo
    assert "logoUrl: 'data:image/svg+xml;base64," in evo, "EvoSys Pro's own logo must stay"
    bab = _brand_block(t, "BOOKABOOST")
    assert "my-deal-room" not in bab and "wholesaleFaviconUrl" not in bab
    assert "'/brands/bookaboost-logo.svg'" in bab
    # applyTheme's per-brand favicons are not edited.
    assert t.count("my-deal-room") == 2


# -- browser: the real rendered screens ---------------------------------------

def _rail(pg):
    return pg.evaluate("""() => {
      const i = document.querySelector('.sidebar img');
      if (!i) return null;
      const r = i.getBoundingClientRect();
      const l = document.querySelector("link[rel~='icon']");
      return {src: i.getAttribute('src'), w: r.width, h: r.height, loaded: i.complete && i.naturalWidth > 0,
              icon: l && l.getAttribute('href'), title: document.title};
    }""")


def test_rendered_rail_slot_favicon_and_restore(client, ws):
    from playwright.sync_api import sync_playwright
    from tests.test_wholesale_access_browser import ORIGIN, _launch
    from tests.test_wholesale_access_browser_roles import _page
    p, db = ws, ws["db"]
    with sync_playwright() as pwr:
        b = _launch(pwr)
        for width, height in ((1440, 900), (390, 844)):
            pg = _page(b, client, db, p["evo_seat"], width, height)
            pg.goto(ORIGIN + "/wholesale")
            pg.wait_for_load_state("networkidle")
            if width < 800:
                pg.click(".mobile-menu-btn")
            pg.wait_for_timeout(800)
            r = _rail(pg)
            assert r["src"] == LOGO and r["loaded"], r
            assert (r["w"], r["h"]) == (180, 72), "same slot as before: %r" % r
            assert r["icon"] == MARK and r["title"] == "My Deal Room"
            if width > 800:
                # Leave the Wholesale shell inside the same page (a seat is held in
                # Wholesale, so go to the public investor page, which unmounts the
                # shell): the previous tab icon must come back.
                pg.evaluate("() => { history.pushState({}, '', '/investors'); dispatchEvent(new PopStateEvent('popstate')) }")
                pg.wait_for_timeout(1500)
                icon = pg.evaluate("() => [location.pathname, document.title, document.querySelector(\"link[rel~='icon']\").getAttribute('href')]")
                assert icon[0].startswith("/investors") and icon[2] != MARK and icon[2].startswith("data:image/svg+xml"), \
                    "favicon must be restored outside My Deal Room screens: %r" % icon
            pg.close()
        pg = _page(b, client, db, p["bab_owner"], 1440, 900)
        pg.goto(ORIGIN + "/wholesale")
        pg.wait_for_load_state("networkidle")
        pg.wait_for_timeout(800)
        r = _rail(pg)
        assert r and "my-deal-room" not in (r["src"] or "") and "my-deal-room" not in (r["icon"] or ""), r
        pg.close()
        b.close()


def test_investor_brandmark_only_for_my_deal_room(client, room):
    import json
    from playwright.sync_api import sync_playwright
    from tests.test_brand_shots_mdr import _router
    from tests.test_deal_room import _accept, _invite
    from tests.test_wholesale_access_browser import ORIGIN, _launch
    _inv, token = _invite(client, room["A"], "Riley Synth", "riley@practice.invalid")
    itok = _accept(client, token, full_name="Riley Synth", kind="individual")["Authorization"].split(" ", 1)[1]
    js = """() => { const i = document.querySelector('.ir-brand__mark img'); const b = document.querySelector('.ir-brand b');
                    return {img: i && i.getAttribute('src'), loaded: !!(i && i.naturalWidth), w: i && i.getBoundingClientRect().width,
                            name: b && b.textContent} }"""
    with sync_playwright() as pwr:
        b = _launch(pwr)
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        pg.route("**/*", _router(client))
        pg.add_init_script("localStorage.setItem('af_investor_token', %s);" % json.dumps(itok))
        pg.goto(ORIGIN + "/investors")
        pg.wait_for_load_state("networkidle")
        pg.wait_for_timeout(800)
        assert pg.evaluate(js) == {"img": MARK, "loaded": True, "w": 30, "name": "My Deal Room"}
        pg.close()
        for origin, mdr in (("https://evosyspro.live", True), ("https://bookaboost.com", False)):
            pg = b.new_page(viewport={"width": 1440, "height": 900})
            pg.route("**/*", _router(client, origin))
            pg.goto(origin + "/investors")
            pg.wait_for_load_state("networkidle")
            pg.wait_for_timeout(800)
            got = pg.evaluate(js)
            icon = pg.evaluate("() => document.querySelector(\"link[rel~='icon']\").getAttribute('href')")
            if mdr:
                assert got["img"] == MARK and got["loaded"] and got["name"] == "My Deal Room" and icon == MARK, got
            else:
                assert got["img"] is None and got["name"] == "Deal Room" and icon != MARK, got
            pg.close()
        b.close()


@pytest.mark.parametrize("name", ["my-deal-room-logo.svg", "my-deal-room-mark.svg"])
def test_assets_ship_in_the_build(name):
    dist = ROOT / "frontend" / "dist" / "brands" / name
    if not (ROOT / "frontend" / "dist").exists():
        pytest.skip("frontend not built")
    assert dist.read_bytes() == (BRANDS / name).read_bytes()
