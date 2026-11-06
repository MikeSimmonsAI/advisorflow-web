"""VOLT homepage copy correction (2026-11-06 4:17 PM): company-wide 'Why build
your future with VOLT?' on the homepage; Fiber's three reasons only on Fiber.
usage: python3 -I why_flow.py <frontend> <shots_dir>"""
import json, sys
from playwright.sync_api import sync_playwright
FE, SHOTS = sys.argv[1].rstrip("/"), sys.argv[2]
R = []
def check(l, ok, d=""):
    R.append((l, bool(ok))); print(("PASS  " if ok else "FAIL  ") + l + ("" if ok else "  -- %s" % str(d)[:300]), flush=True)
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    for name, args in (("desktop", {"viewport": {"width": 1366, "height": 900}}), ("phone", dict(p.devices["iPhone 13"]))):
        pg = b.new_context(**args).new_page()
        pg.goto(FE + "/volt"); pg.wait_for_selector("[data-why-volt]")
        w = pg.inner_text("[data-why-volt]")
        check(f"{name}: heading", "Why build your future with VOLT?" in w, w[:200])
        check(f"{name}: three company-wide reasons, in order",
              [x.strip() for x in pg.locator("[data-why-volt] h3").all_inner_texts()] == ["Find your opportunity.", "Build toward freedom.", "Grow around driven people."])
        check(f"{name}: reason text", all(x in w for x in ("Explore different projects that fit your ambition and strengths.",
              "Develop experience and confidence that give you more control over your future.",
              "Surround yourself with people who push you to improve and take your next step.")))
        check(f"{name}: values and Ownership meaning", all(x in w.upper() for x in ("VISION", "OWNERSHIP", "LEADERSHIP", "TENACITY"))
              and "Ownership means freedom, independence and control over your future." in w)
        home = pg.inner_text("main") if pg.locator("main").count() else pg.inner_text("body")
        low = home.lower()
        check(f"{name}: Fiber reasons are not on the homepage", not any(x in low for x in ("great markets", "industry-leading", "training and development", "three reasons")))
        check(f"{name}: homepage reasons say nothing about travel or door-to-door", not any(x in w.lower() for x in ("travel", "door")))
        check(f"{name}: Apply Now still in hero" + (" and phone bar" if name == "phone" else " and header"),
              pg.locator("[data-apply=hero]").is_visible() and (pg.locator("[data-apply=bar]").is_visible() if name == "phone" else pg.locator("[data-apply=header]").is_visible()))
        pg.locator("[data-why-volt]").scroll_into_view_if_needed()
        pg.screenshot(path=f"{SHOTS}/{name}-why-volt.png")
        pg.screenshot(path=f"{SHOTS}/{name}-home-full.png", full_page=True)
        pg.goto(FE + "/volt/projects/fiber"); pg.wait_for_selector("[data-opportunity=fiber]")
        ft = pg.inner_text("[data-opportunity=fiber]").upper()
        check(f"{name}: Fiber Explore keeps its three reasons", all(x in ft for x in ("GREAT MARKETS", "INDUSTRY-LEADING PEOPLE AND LEADERSHIP", "TRAINING AND DEVELOPMENT")))
        pg.goto(FE + "/volt/projects/aca"); pg.wait_for_selector("[data-opportunity=aca]")
        at = pg.inner_text("[data-opportunity=aca]").lower()
        check(f"{name}: ACA Explore has no Fiber reasons or travel claims", "great markets" not in at and "door-to-door" not in at and "requires travel" not in at)
        # popup still opens on the homepage
        pg.goto(FE + "/volt"); pg.wait_for_selector("[data-why-volt]")
        pg.wait_for_selector("[data-dialog=talent]", timeout=15000)
        check(f"{name}: 10-second popup still opens", True)
    b.close()
ok = sum(1 for _, x in R if x); print(json.dumps({"passed": ok, "total": len(R)})); sys.exit(0 if ok == len(R) else 1)
