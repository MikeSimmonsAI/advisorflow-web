# -*- coding: utf-8 -*-
"""My Deal Room workspace redesign - in a real browser, on the real build.

Regression checks (run whenever frontend/dist and Chromium are present):
  * the My Deal Room rail has the five new groups, Acquisition Command pinned,
    and every rail destination opens a working screen (no page error)
  * BookaBoost's Wholesale rail is untouched (Design A groups)
  * group landing pages list only what the rail shows; restricted roles do
    not see admin tools on the rail or on a landing page
  * the work queue: views, filter, selection and position live in the URL, so
    closing the panel or coming Back returns to the same place
  * phone: compact list, details as a full-width view, no horizontal scroll

Evidence capture: set MDR_SHOTS=/path to also save desktop / mobile
screenshots of every destination (docs/wholesale/my-deal-room-redesign/).
"""
import os
from pathlib import Path

import pytest

from tests.test_bookaboost_invites import w  # noqa: F401
from tests.test_wholesale_access_builder import sent, ws  # noqa: F401
from tests.test_wholesale_access_browser import ORIGIN, _launch
from tests.test_wholesale_access_browser_roles import _page, _person

OUT = os.environ.get("MDR_SHOTS")

MDR_GROUPS = ["acquire", "buyers", "closing", "team", "settings"]
EXPECTED = {
    "acquire": ["/wholesale/evosense/inbox", "/wholesale/foreclosures", "/wholesale/portfolios", "/wholesale/properties",
                "/wholesale/evosense/strategies", "/wholesale/evosense/inbox?ptype=multifamily"],
    "buyers": ["/wholesale/buyers", "/wholesale/buyers/buy-boxes", "/wholesale/deal-room", "/wholesale/deal-room/investors",
               "/wholesale/dispositions", "/wholesale"],
    "closing": ["/wholesale/appointments", "/wholesale/closing", "/wholesale/funding", "/wholesale/calculator"],
    "team": ["/wholesale/board", "/wholesale/canvass", "/wholesale/callbacks", "/wholesale/team"],
    "settings": ["/wholesale/evosense/controls", "/wholesale/exceptions", "/wholesale/pilot", "/wholesale/settings"],
}

_RAIL_JS = """() => {
  const out = {pinned: null, groups: {}};
  const p = document.querySelector('.mdrnav .wsnav-pinned [data-nav-route]');
  out.pinned = p ? p.getAttribute('data-nav-route') : null;
  document.querySelectorAll('.wsnav [data-nav-group]').forEach(g => {
    out.groups[g.getAttribute('data-nav-group')] = [...g.querySelectorAll('[data-nav-route]')].map(a => a.getAttribute('data-nav-route'));
  });
  return out;
}"""


def _shot(page, name, full=False):
    if not OUT:
        return
    d = Path(OUT)
    d.mkdir(parents=True, exist_ok=True)
    page.wait_for_timeout(900)
    page.screenshot(path=str(d / ("%s.png" % name)), full_page=full)


def _settle(page):
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(500)


def _open_all_groups(page):
    for btn in page.query_selector_all(".mdrnav .nav-section-toggle[aria-expanded='false']"):
        btn.click()
    page.wait_for_timeout(150)


def _no_page_error(page):
    txt = page.inner_text("main")
    assert "Something went wrong" not in txt and "This page failed" not in txt, txt[:300]


@pytest.fixture()
def mdr(client, ws):
    from app.services.evosense import sandbox_seed as SS
    db = ws["db"]
    SS.seed_review(db, ws["evo"].id, ws["evo_seat"], replies=True)
    db.commit()
    return ws


def test_rail_destinations_and_landing_pages(client, mdr):
    from playwright.sync_api import sync_playwright
    db, owner = mdr["db"], mdr["evo_seat"]
    with sync_playwright() as pwr:
        b = _launch(pwr)
        page = _page(b, client, db, owner, 1440, 900)
        page.goto(ORIGIN + "/wholesale/evosense")
        _settle(page)
        assert page.query_selector(".layout--mdr"), "My Deal Room shell"
        assert page.query_selector(".mdr-brand__mark[src='/brands/my-deal-room-door.svg']")
        assert "My Deal Room" in page.inner_text(".mdr-brand")
        # groups start closed except the one holding the page
        rail = page.evaluate(_RAIL_JS)
        assert rail["pinned"] == "/wholesale/evosense"
        assert list(rail["groups"]) == MDR_GROUPS
        assert all(v == [] for v in rail["groups"].values()), "only the active group is open by default"
        _shot(page, "01-acquisition-command-desktop")
        _open_all_groups(page)
        rail = page.evaluate(_RAIL_JS)
        assert rail["groups"] == EXPECTED, rail["groups"]
        # full labels fit (no clipped rail entry)
        clipped = page.evaluate("""() => [...document.querySelectorAll('.mdrnav .nav-item')]
            .filter(a => a.scrollWidth > a.clientWidth + 1).map(a => a.textContent)""")
        assert clipped == [], clipped
        _shot(page, "02-rail-all-groups-open")
        # every destination opens a working screen, and the active one is highlighted
        for routes in EXPECTED.values():
            for r in routes:
                page.goto(ORIGIN + r)
                _settle(page)
                _no_page_error(page)
                on = page.query_selector(".mdrnav .nav-item--active")
                assert on and on.get_attribute("data-nav-route") == r, r
                _shot(page, "d-" + r.strip("/").replace("/", "-").replace("?", "-").replace("=", "-"))
        for g in MDR_GROUPS:
            page.goto(ORIGIN + "/wholesale/hub/" + g)
            _settle(page)
            tiles = [a.get_attribute("data-hub-route") for a in page.query_selector_all("[data-hub-route]")]
            assert tiles == EXPECTED[g], (g, tiles)
            _shot(page, "hub-" + g)
        page.goto(ORIGIN + "/wholesale/evosense/activity")
        _settle(page)
        _no_page_error(page)
        assert "System status" in page.inner_text("main")
        _shot(page, "03-activity-system-health")
        # account menu: identity, sign out, website
        page.goto(ORIGIN + "/wholesale/evosense")
        _settle(page)
        page.click(".mdr-acct__btn")
        menu = page.inner_text(".mdr-acct .mdr-pop")
        assert "Sign out" in menu and owner.full_name in menu
        _shot(page, "04-account-menu")
        page.keyboard.press("Escape")
        page.keyboard.press("g")
        page.fill(".mdr-jump .mdr-pop__search", "buy box")
        page.keyboard.press("Enter")
        _settle(page)
        assert page.url.endswith("/wholesale/buyers/buy-boxes")
        b.close()


def test_bookaboost_keeps_its_rail(client, mdr):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pwr:
        b = _launch(pwr)
        page = _page(b, client, mdr["db"], mdr["bab_owner"], 1440, 900)
        page.goto(ORIGIN + "/wholesale")
        _settle(page)
        assert not page.query_selector(".layout--mdr") and not page.query_selector(".mdrnav")
        groups = page.evaluate("() => [...document.querySelectorAll('.wsnav [data-nav-group]')].map(g => g.getAttribute('data-nav-group'))")
        assert "acquire" not in groups and "settings" not in groups
        _shot(page, "90-bookaboost-unchanged")
        b.close()


def test_restricted_role_sees_only_its_tools(client, mdr):
    from playwright.sync_api import sync_playwright
    user = _person(client, mdr, "va.mdr@example.com", "research_assistant")
    with sync_playwright() as pwr:
        b = _launch(pwr)
        page = _page(b, client, mdr["db"], user, 1440, 900, workspace=mdr["evo"].id)
        page.goto(ORIGIN + "/wholesale/properties")
        _settle(page)
        _open_all_groups(page)
        routes = [r for rs in page.evaluate(_RAIL_JS)["groups"].values() for r in rs]
        for admin in ("/wholesale/settings", "/wholesale/team", "/wholesale/pilot", "/wholesale/evosense/controls"):
            assert admin not in routes, admin
        page.goto(ORIGIN + "/wholesale/hub/settings")
        _settle(page)
        tiles = [a.get_attribute("data-hub-route") for a in page.query_selector_all("[data-hub-route]")]
        assert "/wholesale/settings" not in tiles
        _shot(page, "91-restricted-role-rail")
        b.close()


def test_queue_views_drawer_and_return_position(client, mdr):
    from playwright.sync_api import sync_playwright
    db, owner = mdr["db"], mdr["evo_seat"]
    with sync_playwright() as pwr:
        b = _launch(pwr)
        page = _page(b, client, db, owner, 1440, 900)
        page.goto(ORIGIN + "/wholesale/evosense?view=all")
        _settle(page)
        rows = page.query_selector_all(".wq-row")
        assert len(rows) >= 3
        # controls, several rows and the selected property's next action in the first viewport
        rows[1].click()
        page.wait_for_selector(".wq-detail .wq-next")
        in_view = page.evaluate("""() => {
          const v = el => { const r = document.querySelector(el).getBoundingClientRect(); return r.top >= 0 && r.top < innerHeight; };
          return {tabs: v('.wq-tabs'), next: v('.wq-detail .wq-next'),
                  rows: [...document.querySelectorAll('.wq-row')].filter(r => r.getBoundingClientRect().bottom < innerHeight).length};
        }""")
        assert in_view["tabs"] and in_view["next"] and in_view["rows"] >= 4, in_view
        assert "sel=" in page.url and "view=all" in page.url
        _shot(page, "05-queue-all-with-detail")
        sel_url = page.url
        page.click(".wq-detail a:has-text('Open full property')")
        _settle(page)
        assert "/wholesale/evosense/property/" in page.url
        page.go_back()
        _settle(page)
        assert page.url == sel_url and page.query_selector(".wq-row.is-sel")
        page.click(".wq-back")
        page.wait_for_timeout(200)
        assert "sel=" not in page.url and "view=all" in page.url
        for v in ("needs_decision", "auctions", "handoffs"):
            page.click(".wq-tab:nth-child(%d)" % (["needs_decision", "auctions", "handoffs", "all"].index(v) + 1))
            _settle(page)
            _no_page_error(page)
            _shot(page, "06-queue-" + v)
        page.fill("#wq-q", "zzzz-nothing")
        page.keyboard.press("Enter")
        _settle(page)
        assert "Nothing matches that filter" in page.inner_text(".wq-rows")
        _shot(page, "07-queue-empty-filter")
        b.close()


def test_phone_compact_list_and_full_width_detail(client, mdr):
    from playwright.sync_api import sync_playwright
    db, owner = mdr["db"], mdr["evo_seat"]
    with sync_playwright() as pwr:
        b = _launch(pwr)
        page = _page(b, client, db, owner, 390, 844)
        page.goto(ORIGIN + "/wholesale/evosense?view=all")
        _settle(page)
        wide = page.evaluate("() => document.documentElement.scrollWidth - innerWidth")
        assert wide <= 1, "no horizontal page scroll (%spx)" % wide
        _shot(page, "m01-queue-phone")
        page.query_selector_all(".wq-row")[0].click()
        page.wait_for_selector(".wq-detail .wq-next")
        w = page.evaluate("() => document.querySelector('.wq-detail').getBoundingClientRect().width")
        assert w >= 389
        _shot(page, "m02-detail-phone")
        page.click(".wq-back")
        page.wait_for_timeout(200)
        page.click(".mobile-menu-btn")
        page.wait_for_timeout(300)
        _shot(page, "m03-nav-phone")
        page.click(".mdrnav [data-nav-group='buyers'] .nav-section-toggle")
        page.click(".mdrnav [data-nav-route='/wholesale/buyers']")
        _settle(page)
        assert page.url.endswith("/wholesale/buyers")
        wide = page.evaluate("() => document.documentElement.scrollWidth - innerWidth")
        _shot(page, "m04-buyers-phone")
        for r in ("/wholesale/portfolios", "/wholesale/foreclosures", "/wholesale/appointments", "/wholesale/hub/closing"):
            page.goto(ORIGIN + r)
            _settle(page)
            _no_page_error(page)
            _shot(page, "m-" + r.strip("/").replace("/", "-"))
        b.close()
