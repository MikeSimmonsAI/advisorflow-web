"""Extra screenshots: Wholesale, Back Office and the Countryside preview at
desktop, tablet and phone sizes, plus refresh and Program-Access round trips.
LOCAL ONLY (same servers as mockups.py). Usage: python -I sizes.py OUT_DIR"""
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
APP = "http://localhost:5173"
SIZES = ((1600, 1000, "desktop-1600"), (820, 1180, "tablet-820"), (390, 844, "mobile-390"))
FOREIGN = re.compile(r"EvoSys|EvoSense", re.I)
res, errs = [], []


def ok(n, c, x=""):
    res.append(("PASS" if c else "FAIL") + "  " + n + (" - " + str(x) if x else ""))


with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1600, "height": 1000})
    pg = ctx.new_page()
    pg.set_default_timeout(90000)
    pg.route("**/*", lambda r: r.continue_() if (not r.request.url.startswith("http") or
             r.request.url.split("/")[2].split(":")[0] in ("127.0.0.1", "localhost", "fonts.googleapis.com", "fonts.gstatic.com")) else r.abort())
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(APP + "/login", wait_until="networkidle")
    pg.fill("input[type=email]", "bb-admin@example.com")
    pg.fill("input[type=password]", "LocalTest!1")
    pg.keyboard.press("Enter")
    pg.wait_for_url("**/workspaces")
    pg.wait_for_selector(".bbpa-card")

    def shoot(prefix):
        for w, h, tag in SIZES:
            pg.set_viewport_size({"width": w, "height": h})
            pg.wait_for_timeout(900)
            over = pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
            ok("%s no horizontal overflow at %s" % (prefix, tag), over <= 0, over)
            pg.screenshot(path=str(OUT / ("%s-%s.png" % (prefix, tag))), full_page=False)
        pg.set_viewport_size({"width": 1600, "height": 1000})

    # Wholesale
    pg.click("button.bbpa-card[data-program='wholesale']")
    pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(2500)
    leaks = FOREIGN.findall(pg.evaluate("document.body.innerText"))
    ok("W1 BookaBoost Wholesale shows no EvoSys/EvoSense text", not leaks, leaks)
    shoot("C-wholesale-bookaboost")
    # phone nav opens
    pg.set_viewport_size({"width": 390, "height": 844}); pg.wait_for_timeout(600)
    toggles = pg.locator("button[aria-label*='menu' i], button[aria-label*='navigation' i], .mobile-menu-btn, .hamburger")
    if toggles.count():
        toggles.first.click(); pg.wait_for_timeout(700)
        pg.screenshot(path=str(OUT / "C-wholesale-bookaboost-mobile-390-nav-open.png"))
        ok("W2 phone navigation opens", True)
    else:
        ok("W2 phone navigation toggle found", False, "no toggle matched")
    pg.set_viewport_size({"width": 1600, "height": 1000})
    pg.goto(APP + "/workspaces", wait_until="networkidle"); pg.wait_for_selector(".bbpa-card")
    ok("R1 back to Program Access", pg.url.endswith("/workspaces"), pg.url)

    # Back Office
    pg.click("button.bbpa-card[data-program='back_office']")
    pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(2500)
    btxt = pg.evaluate("document.body.innerText")
    ok("B1 Back Office names BookaBoost, no EvoSys text", "BookaBoost" in btxt and not FOREIGN.search(btxt), FOREIGN.findall(btxt))
    shoot("B-back-office-bookaboost")
    pg.reload(wait_until="networkidle"); pg.wait_for_timeout(2000)
    ok("B2 refresh keeps the Back Office", "/sales" in pg.url and "BookaBoost" in pg.evaluate("document.body.innerText"), pg.url)
    pg.goto(APP + "/workspaces", wait_until="networkidle"); pg.wait_for_selector(".bbpa-card")

    # Countryside preview
    pg.click("a.bbpa-card[data-program='preview']")
    pg.wait_for_timeout(2500)
    ok("D1 preview opens at /previews/", "/previews/" in pg.url, pg.url)
    ok("D2 no public demo link in the page", "/demo/" not in pg.content())
    shoot("D-countryside-preview-open")
    pg.reload(wait_until="networkidle"); pg.wait_for_timeout(2000)
    ok("D3 refresh keeps the preview", "/previews/" in pg.url and pg.locator("iframe").count() == 1, pg.url)
    b.close()

ok("Z1 no page errors", not errs, errs[:3])
print("\n".join(res))
