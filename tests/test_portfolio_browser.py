# -*- coding: utf-8 -*-
"""Portfolio Acquisitions in a real browser (the built frontend in Chromium,
API calls served by the in-process app). Synthetic sandbox data only.

Checks the rail entry (My Deal Room yes, BookaBoost no), the five views, the
evaluation drawer, the package builder and comparison, and that phone and
tablet widths do not scroll sideways. Screenshots go to PF_SHOTS when set."""
import os
from pathlib import Path

import pytest

from tests.test_bookaboost_invites import w  # noqa: F401
from tests.test_portfolio_acquisitions import _evaluate, _fix_and_commit, _pkg, _stage, pf  # noqa: F401
from tests.test_wholesale_access_builder import sent, ws  # noqa: F401
from tests.test_wholesale_access_browser import ORIGIN, _fits, _launch
from tests.test_wholesale_access_browser_roles import _page

OUT = os.environ.get("PF_SHOTS")


def _shot(page, name, full=False):
    page.wait_for_timeout(700)
    if OUT:
        Path(OUT).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(OUT) / ("%s.png" % name)), full_page=full)


def _ready(pf):
    """Imported list, three properties valued, two packages."""
    _fix_and_commit(pf, _stage(pf))
    rows = {r["source_row"]: r for r in pf["c"].get("/wholesale/portfolios/%s" % pf["p"]["id"],
                                                    headers=pf["owner_h"]).json()["rows"]}
    for n, (a, rep, t) in {1: (160000, 25000, 80000), 2: (210000, 30000, 110000), 3: (40000, 0, 18000)}.items():
        assert _evaluate(pf, rows[n]["item_id"], arv=a, repairs=rep, target_offer=t, note="synthetic comps").status_code == 200
    _pkg(pf, "Three valued", item_ids=[rows[n]["item_id"] for n in (1, 2, 3)])
    _pkg(pf, "Three at 10% off", item_ids=[rows[n]["item_id"] for n in (1, 2, 3)], discount_mode="percent",
         discount_value=10)
    _pkg(pf, "Entire portfolio", all=True)
    return rows


def test_portfolio_screens_in_a_real_browser(client, pf):
    from playwright.sync_api import sync_playwright
    _ready(pf)
    pid = pf["p"]["id"]
    with sync_playwright() as pwr:
        b = _launch(pwr)
        pg = _page(b, client, pf["db"], pf["evo_seat"], 1440, 900, workspace=pf["evo"].id)

        # the rail: Acquisition → Portfolio Acquisitions
        pg.goto(ORIGIN + "/wholesale/portfolios")
        pg.wait_for_load_state("networkidle")
        link = pg.locator(".sidebar a[href='/wholesale/portfolios']")
        assert link.count() == 1 and "Seller Portfolios" in link.inner_text()     # My Deal Room redesign label
        assert pg.get_by_role("heading", name="Seller Portfolios").count() >= 1
        assert "Seller Portfolio #001" in pg.inner_text("main")
        _shot(pg, "01-portfolio-list-desktop")

        # B. detail / inventory
        pg.goto(ORIGIN + "/wholesale/portfolios/%s" % pid)
        pg.wait_for_load_state("networkidle")
        body = pg.inner_text("main")
        for text in ("Seller stated", "Our assumptions", "Asking", "Estimated value", "Target offers", "Spread",
                     "Research needed", "partial · 3 of 5", "Import the seller’s list", "Build an offer package"):
            assert text.lower() in body.lower(), text
        assert pg.locator(".pf-table tbody tr").count() == 5
        _shot(pg, "02-portfolio-inventory-desktop", full=True)
        pg.get_by_label("Filter properties").select_option("land")
        assert pg.locator(".pf-table tbody tr").count() == 1
        pg.get_by_label("Filter properties").select_option("")
        pg.get_by_label("Sort properties").select_option("spread")
        assert "Ranked by estimated profit" in pg.inner_text("main")
        first = pg.locator(".pf-table tbody tr").first.inner_text()
        assert "202 Synthetic Elm" in first                      # the biggest spread of the three valued

        # C. evaluation drawer
        pg.locator(".pf-table tbody tr").first.click()
        pg.wait_for_selector(".evo-drawer .pf-eval")
        drawer = pg.inner_text(".evo-drawer")
        for text in ("Seller’s listing", "not verified", "Maximum allowable offer", "Estimated profit",
                     "Change a number", "Internal investment estimate"):
            assert text in drawer, text
        _shot(pg, "03-property-evaluation-desktop")
        pg.keyboard.press("Escape")

        # D. packages + comparison
        pg.get_by_role("tab", name="Packages").click()
        pg.wait_for_selector(".pf-compare")
        comp = pg.inner_text(".pf-compare")
        assert "Three valued" in comp and "Three at 10% off" in comp and "Entire portfolio" in comp
        assert "Est. profit at package offer" in comp
        _shot(pg, "04-package-builder-compare-desktop", full=True)
        # edit one scenario's offer: the other scenario keeps its own
        pg.locator(".pf-pkgtabs button", has_text="Three at 10% off").click()
        box = pg.locator("input.pf-offer").first
        box.fill("70000")
        pg.get_by_role("button", name="Save offers").click()
        pg.wait_for_timeout(900)
        pg.locator(".pf-pkgtabs button", has_text="Three valued").click()
        pg.wait_for_timeout(300)
        assert pg.locator("input.pf-offer").first.input_value() == "80000"

        # E. summary
        pg.get_by_role("tab", name="Summary").click()
        assert "Assumptions in use" in pg.inner_text("main") and "Best potential spread" in pg.inner_text("main")
        _shot(pg, "05-portfolio-summary-desktop", full=True)

        # import preview (review before commit)
        pg.get_by_role("tab", name="Inventory").click()
        pg.get_by_role("button", name="Import seller list").click()
        pg.set_input_files("input[type=file]", files=[{"name": "seller2.csv", "mimeType": "text/csv", "buffer":
                           b"No.,Address,City,Zip,Price,Rent,Type\n9,909 Synthetic Aspen Ct,Testville,75001,77000,900,SFR\n"
                           b"10,101 Synthetic Oak St,Testville,75001,85000,950,SFR\n"}])
        pg.get_by_role("button", name="Preview").click()
        pg.wait_for_selector(".pf-preview")
        prev = pg.inner_text(".pf-preview")
        assert "Possible duplicate" in prev and "OK" in prev
        _shot(pg, "06-import-preview-desktop")
        pg.get_by_role("button", name="Discard").click()
        pg.close()

        # phone and tablet: no sideways scroll; the rail entry is in the mobile menu
        for (wd, ht, tag) in ((390, 844, "mobile"), (820, 1180, "tablet")):
            pg = _page(b, client, pf["db"], pf["evo_seat"], wd, ht, workspace=pf["evo"].id)
            pg.goto(ORIGIN + "/wholesale/portfolios/%s" % pid)
            pg.wait_for_load_state("networkidle")
            pg.wait_for_selector(".pf-table")
            assert _fits(pg), tag
            _shot(pg, "07-portfolio-inventory-%s" % tag, full=True)
            pg.get_by_role("tab", name="Packages").click()
            pg.wait_for_selector(".pf-compare")
            assert _fits(pg), tag + " packages"
            _shot(pg, "08-packages-%s" % tag, full=True)
            if tag == "mobile":
                pg.click(".mobile-menu-btn")
                pg.wait_for_timeout(400)
                assert pg.locator(".sidebar a[href='/wholesale/portfolios']").count() == 1
                _shot(pg, "09-mobile-menu")
            pg.close()

        # BookaBoost: the rail entry is not there
        pg = _page(b, client, pf["db"], pf["bab_owner"], 1440, 900, workspace=pf["bab"].id)
        pg.goto(ORIGIN + "/wholesale")
        pg.wait_for_load_state("networkidle")
        pg.wait_for_timeout(800)
        assert pg.locator(".sidebar a[href='/wholesale/portfolios']").count() == 0
        _shot(pg, "10-bookaboost-rail-unchanged")
        pg.close()
        b.close()
