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


WALK = os.environ.get("MDR_WALKTHROUGH")


def _ctx(b, client, db, user, wd, ht, out, workspace):
    """A recording browser context with the same sign-in and routing as _page."""
    import json
    from app.services.auth_service import create_access_token
    from tests.test_wholesale_access_browser_roles import _router
    token = create_access_token(user, db)
    me = json.dumps({"id": user.id, "full_name": user.full_name, "email": user.email, "role": user.role,
                     "organization_id": user.organization_id, "must_change_password": False})
    ctx = b.new_context(viewport={"width": wd, "height": ht}, record_video_dir=out, record_video_size={"width": wd, "height": ht})
    ctx.route("**/*", _router(client, token, workspace))
    ctx.add_init_script("localStorage.setItem('af_token', %s); localStorage.setItem('af_user', %s); localStorage.setItem('af_workspace_id', %s);"
                        % (json.dumps(token), json.dumps(me), json.dumps(workspace)))
    return ctx


@pytest.mark.skipif(not WALK, reason="walkthrough recording only (set MDR_WALKTHROUGH)")
def test_walkthrough(client, pf):
    """Two short screen recordings: the queue flow and the portfolio flow."""
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
    out = Path(WALK)
    out.mkdir(parents=True, exist_ok=True)
    pause = lambda pg, ms=1400: pg.wait_for_timeout(ms)  # noqa: E731
    with sync_playwright() as pwr:
        b = _launch(pwr)
        # 1. queue
        ctx = _ctx(b, client, db, pf["evo_seat"], 1440, 900, str(out / "queue"), pf["evo"].id)
        pg = ctx.new_page()
        pg.goto(ORIGIN + "/wholesale/evosense")
        pg.wait_for_load_state("networkidle"); pause(pg, 2200)
        pg.click(".wq-tab:has-text('Upcoming Auctions')"); pg.wait_for_load_state("networkidle"); pause(pg)
        pg.click(".wq-row:has-text('2124 S Ervay St')"); pause(pg, 2600)
        pg.click(".wq-row:has-text('6120 Wedgwood Dr')"); pause(pg, 2600)
        pg.click(".wq-tab:has-text('All')"); pg.wait_for_load_state("networkidle"); pause(pg)
        pg.fill("#wq-q", "fort worth"); pg.press("#wq-q", "Enter"); pg.wait_for_load_state("networkidle"); pause(pg)
        pg.locator(".wq-row").nth(1).click(); pause(pg)
        pg.click(".wq-detail a:has-text('Open full property')"); pg.wait_for_load_state("networkidle"); pause(pg)
        pg.go_back(); pg.wait_for_load_state("networkidle"); pause(pg, 2000)
        ctx.close()
        # 2. portfolio
        ctx = _ctx(b, client, db, pf["evo_seat"], 1440, 900, str(out / "portfolio"), pf["evo"].id)
        pg = ctx.new_page()
        pg.on("dialog", lambda d: d.accept("Two valued properties"))
        pg.goto(ORIGIN + "/wholesale/portfolios/%s" % empty["id"]); pg.wait_for_load_state("networkidle"); pause(pg, 2200)
        pg.goto(ORIGIN + "/wholesale/portfolios/%s" % pf["p"]["id"]); pg.wait_for_load_state("networkidle"); pause(pg, 2000)
        pg.locator(".pf-table tbody tr").first.click(); pg.wait_for_selector(".pf-review .pf-eval"); pause(pg, 2600)
        pg.click(".pf-review .evo-x"); pause(pg)
        pg.locator(".pf-table tbody input[type=checkbox]").nth(0).check(); pause(pg, 600)
        pg.locator(".pf-table tbody input[type=checkbox]").nth(1).check(); pause(pg)
        pg.click(".pf-selbar-sticky button:has-text('Build offer package')")
        pg.wait_for_selector(".pf-compare"); pause(pg, 2600)
        ctx.close()
        b.close()
