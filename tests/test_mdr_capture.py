# -*- coding: utf-8 -*-
"""Before/after screenshots of the rebuilt My Deal Room screens (evidence only).

Same seed, same sizes, run once on `main` (BEFORE) and once on the PR (AFTER):

    MDR_CAPTURE=/path MDR_LABEL=before|after pytest tests/test_mdr_capture.py

Seed: the EvoSense sandbox review data, the two sale-date cases from
test_mdr_redesign_browser, a populated seller portfolio (imported, three
valued, three packages) and an empty one. Skipped unless MDR_CAPTURE is set.
"""
import os
from pathlib import Path

import pytest

from tests.test_bookaboost_invites import w  # noqa: F401
from tests.test_portfolio_acquisitions import pf  # noqa: F401
from tests.test_portfolio_browser import _ready
from tests.test_wholesale_access_builder import sent, ws  # noqa: F401
from tests.test_wholesale_access_browser import ORIGIN, _launch
from tests.test_wholesale_access_browser_roles import _page

OUT = os.environ.get("MDR_CAPTURE")
LABEL = os.environ.get("MDR_LABEL", "after")
pytestmark = pytest.mark.skipif(not OUT, reason="evidence capture only (set MDR_CAPTURE)")

SCREENS = [
    ("acquisition-command", "/wholesale/evosense"),
    ("discovery-inbox", "/wholesale/evosense/inbox"),
    ("foreclosures", "/wholesale/foreclosures"),
    ("properties", "/wholesale/properties"),
    ("strategies", "/wholesale/evosense/strategies"),
    ("buyer-intelligence", "/wholesale/buyers"),
    ("buy-boxes", "/wholesale/buyers/buy-boxes"),
    ("deal-room", "/wholesale/deal-room"),
    ("investor-outreach", "/wholesale/deal-room/investors"),
    ("dispositions", "/wholesale/dispositions"),
    ("deal-operations", "/wholesale"),
    ("appointments", "/wholesale/appointments"),
    ("contracts-closing", "/wholesale/closing"),
    ("funding", "/wholesale/funding"),
    ("calculator", "/wholesale/calculator"),
    ("team-board", "/wholesale/board"),
    ("canvassing", "/wholesale/canvass"),
    ("callbacks", "/wholesale/callbacks"),
    ("team", "/wholesale/team"),
    ("providers-controls", "/wholesale/evosense/controls"),
    ("exceptions", "/wholesale/exceptions"),
    ("pilot", "/wholesale/pilot"),
    ("settings", "/wholesale/settings"),
]


def test_capture(client, pf):
    from playwright.sync_api import sync_playwright
    from app.services.evosense import sandbox_seed as SS
    from tests.test_mdr_redesign_browser import _sale_case
    db = pf["db"]
    SS.seed_review(db, pf["evo"].id, pf["evo_seat"], replies=True)
    db.commit()
    _sale_case(db, pf["evo"].id, "2124 S Ervay St", "2026-12-01", "2026-11-03")
    _sale_case(db, pf["evo"].id, "6120 Wedgwood Dr", "2026-12-01", "2026-12-08")
    _ready(pf)
    empty = pf["c"].post("/wholesale/portfolios", headers=pf["owner_h"], json={
        "name": "Empty synthetic portfolio", "seller_name": "Example Seller (synthetic)", "is_test": True}).json()["portfolio"]
    screens = SCREENS + [("seller-portfolios-list", "/wholesale/portfolios"),
                         ("seller-portfolio-populated", "/wholesale/portfolios/%s" % pf["p"]["id"]),
                         ("seller-portfolio-empty", "/wholesale/portfolios/%s" % empty["id"])]
    d = Path(OUT) / LABEL
    d.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pwr:
        b = _launch(pwr)
        for tag, (wd, ht) in (("desktop", (1440, 900)), ("phone", (390, 844))):
            pg = _page(b, client, db, pf["evo_seat"], wd, ht, workspace=pf["evo"].id)
            for name, path in screens:
                pg.goto(ORIGIN + path)
                pg.wait_for_load_state("networkidle")
                pg.wait_for_timeout(900)
                pg.screenshot(path=str(d / ("%s-%s.png" % (name, tag))))
            pg.close()
        b.close()
