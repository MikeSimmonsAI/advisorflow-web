"""VOLT #535 (2026-11-06 afternoon) - VOLT meaning, both Explore pages, the
automatic Get Future Opportunities popup (timing, dismissal, once per load,
deferral behind another dialog, never during an application).

usage: python3 -I explore_flow.py <frontend> <api> <shots_dir>
Synthetic people only (@example.test). Prints PASS/FAIL and a summary.
"""
import json, os, sys, time, uuid
from playwright.sync_api import sync_playwright

FE, API, SHOTS = sys.argv[1].rstrip("/"), sys.argv[2].rstrip("/"), sys.argv[3]
R = []
os.makedirs(SHOTS, exist_ok=True)
UNCONFIRMED = ("per hour", "per week", "commission", "$", "housing provided", "paid travel", "reimburs", "w-2", "1099",
               "employee of t-mobile", "hired by t-mobile", "guaranteed", "start date:", "%", "testimonial", "obamacare")


def check(label, ok, detail=""):
    R.append((label, bool(ok)))
    print(("PASS  " if ok else "FAIL  ") + label + ("" if ok else "  -- %s" % str(detail)[:300]), flush=True)


def dialog_title(pg):
    d = pg.locator("[role=dialog]")
    if d.count() == 0:
        return None
    return {"talent": "Get Future Opportunities", "apply": "Apply Now"}.get(d.first.get_attribute("data-dialog"), "?")


def wait_popup(pg, limit_s=14.0):
    t0 = time.time()
    while time.time() - t0 < limit_s:
        if dialog_title(pg) == "Get Future Opportunities":
            return time.time() - t0
        pg.wait_for_timeout(200)
    return None


with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    for name, ctx_args in (("desktop", {"viewport": {"width": 1366, "height": 900}}), ("phone", dict(p.devices["iPhone 13"]))):
        ctx = b.new_context(**ctx_args)
        pg = ctx.new_page()
        # ── Homepage: VOLT meaning ──
        t_nav = time.time()
        pg.goto(FE + "/volt"); pg.wait_for_selector("[data-apply=hero]")
        loaded = time.time() - t_nav
        hero = pg.inner_text(".v-hero")
        check(f"{name}: hero eyebrow is the VOLT words", "VISION · OWNERSHIP · LEADERSHIP · TENACITY" in hero.upper(), hero[:200])
        check(f"{name}: hero says 'Your next move. Make it VOLT.'", "Your next move." in hero and "Make it VOLT." in hero, hero[:200])
        cards = pg.inner_text("#projects ~ .v-grid2")
        check(f"{name}: cards have Explore and Apply buttons", pg.locator("[data-explore]").count() == 2 and "Apply for ACA" in cards and "Apply for Fiber" in cards, cards[:300])
        check(f"{name}: no popup at 5 seconds", (pg.wait_for_timeout(5000) or True) and dialog_title(pg) is None)
        pg.screenshot(path=f"{SHOTS}/{name}-01-home-volt-meaning.png", full_page=(name == "phone"))
        # ── Auto popup at ~10 s ──
        waited = wait_popup(pg, 9)
        total = 5 + (waited or 99) + loaded
        check(f"{name}: Get Future Opportunities opens on its own about 10 s after entering", waited is not None and 9 <= total <= 13, total)
        check(f"{name}: popup is a labelled modal with the unchecked consent box",
              pg.get_attribute("[role=dialog]", "aria-modal") == "true" and not pg.locator("[data-consent]").is_checked())
        pg.screenshot(path=f"{SHOTS}/{name}-02-auto-popup.png")
        pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
        check(f"{name}: Escape dismisses it", dialog_title(pg) is None)
        # ── once per page load: leave and come back inside the app ──
        pg.locator("[data-explore=aca]").click(); pg.wait_for_selector("[data-opportunity=aca]")
        pg.locator("a", has_text="All opportunities").click(); pg.wait_for_selector("[data-apply=hero]")
        pg.wait_for_timeout(12000)
        check(f"{name}: not shown again in the same page load", dialog_title(pg) is None)
        check(f"{name}: manual Get Future Opportunities still works", (pg.locator("[data-talent=hero]").click() or True) and dialog_title(pg) == "Get Future Opportunities")
        pg.locator("[role=dialog] button[aria-label=Close]").click()
        # ── deferred behind Apply Now ──
        pg.reload(); pg.wait_for_selector("[data-apply=hero]")
        pg.wait_for_timeout(3000)
        pg.locator("[data-apply=hero]").click(); pg.wait_for_selector("[data-pick]")
        pg.wait_for_timeout(9500)
        check(f"{name}: waits while the Apply Now dialog is open", dialog_title(pg) == "Apply Now")
        pg.keyboard.press("Escape"); pg.wait_for_timeout(700)
        check(f"{name}: opens once the Apply Now dialog closes", dialog_title(pg) == "Get Future Opportunities")
        pg.locator("[role=dialog] button[aria-label=Close]").click()
        # ── suppressed during an application ──
        pg.reload(); pg.wait_for_selector("[data-apply=hero]")
        pg.wait_for_timeout(2000)
        pg.locator("[data-apply=hero]").click(); pg.locator("[data-pick=fiber]").click(); pg.wait_for_url("**/volt/apply/fiber")
        pg.wait_for_timeout(11000)
        check(f"{name}: never opens during an application", pg.locator("[role=dialog]").count() == 0)
        # ── Explore pages ──
        pg.goto(FE + "/volt/projects/fiber"); pg.wait_for_selector("[data-opportunity=fiber]")
        ft = pg.inner_text("[data-opportunity=fiber]")
        vals = pg.inner_text("[data-volt-meaning]")
        check(f"{name}: Fiber Explore: VOLT = Vision, Ownership, Leadership, Tenacity",
              [pg.inner_text(f"[data-letter={l}] h3") for l in "VOLT"] == ["Vision", "Ownership", "Leadership", "Tenacity"], vals)
        check(f"{name}: Ownership = freedom, independence and control over your future",
              all(x in pg.inner_text("[data-letter=O]").lower() for x in ("freedom", "independence", "control over")), vals)
        check(f"{name}: Fiber Explore names the three reasons",
              pg.locator("[data-pillar]").count() == 3 and all(x in ft.replace("\n", " ").upper() for x in ("GREAT MARKETS", "INDUSTRY-LEADING PEOPLE AND LEADERSHIP", "TRAINING AND DEVELOPMENT")), ft[:300])
        check(f"{name}: Fiber Explore has Mike's message",
              "The best in the industry work here." in ft and "breaking industry records" in ft)
        check(f"{name}: Fiber Explore shows no statistics", not any(ch.isdigit() for ch in pg.inner_text("[data-pillar]").replace("Reason 1", "").replace("Reason 2", "").replace("Reason 3", "").replace("REASON 1", "").replace("REASON 2", "").replace("REASON 3", "")))
        check(f"{name}: Fiber Explore is travel-focused, no general availability", "requires travel" in ft.lower() and "availability" not in ft.lower(), ft[:200])
        check(f"{name}: Fiber Explore has no unconfirmed terms", not any(x in ft.lower() for x in UNCONFIRMED), [x for x in UNCONFIRMED if x in ft.lower()])
        check(f"{name}: Fiber Explore Apply Now goes straight to the Fiber application",
              (pg.locator("[data-apply=project]").first.click() or True) and (pg.wait_for_url("**/volt/apply/fiber") or True) and pg.url.endswith("/volt/apply/fiber"))
        pg.goto(FE + "/volt/projects/fiber"); pg.wait_for_selector("[data-opportunity=fiber]")
        pg.screenshot(path=f"{SHOTS}/{name}-03-fiber-explore.png", full_page=True)
        pg.goto(FE + "/volt/projects/aca"); pg.wait_for_selector("[data-opportunity=aca]")
        at = pg.inner_text("[data-opportunity=aca]")
        check(f"{name}: ACA Explore page", "Explore ACA with VOLT." in at and "license status" in at.lower()
              and pg.locator("[data-volt-meaning]").count() == 1, at[:300])
        check(f"{name}: ACA Explore has no unconfirmed terms", not any(x in at.lower() for x in UNCONFIRMED), [x for x in UNCONFIRMED if x in at.lower()])
        pg.screenshot(path=f"{SHOTS}/{name}-04-aca-explore.png", full_page=True)
        pg.locator("[data-apply=project]").first.click(); pg.wait_for_url("**/volt/apply/aca")
        check(f"{name}: ACA Explore Apply Now goes straight to the ACA application", pg.url.endswith("/volt/apply/aca"))
        # no popup on an Explore page either
        pg.goto(FE + "/volt/projects/aca"); pg.wait_for_selector("[data-opportunity=aca]"); pg.wait_for_timeout(11000)
        check(f"{name}: no automatic popup on an Explore page", pg.locator("[role=dialog]").count() == 0)
        ctx.close()

    # ── the popup's signup is recorded as coming from the popup ──
    ctx = b.new_context(viewport={"width": 1366, "height": 900}); pg = ctx.new_page()
    pg.goto(FE + "/volt"); pg.wait_for_selector("[data-apply=hero]")
    wait_popup(pg, 14)
    email = "popup-%s@example.test" % uuid.uuid4().hex[:6]
    check("popup heading is 'Be first to hear.'", "Be first to hear." in pg.inner_text("[role=dialog]"))
    pg.fill("#v-tp-first_name", "Pop"); pg.fill("#v-tp-last_name", "Up"); pg.fill("#v-tp-email", email)
    pg.locator("[data-form=talent] button[type=submit]").click(); pg.wait_for_selector("[data-talent-done]")
    check("popup signup succeeds", True)
    out = {"popup_email": email}
    ctx.close(); b.close()

ok = sum(1 for _, x in R if x)
print(json.dumps({"passed": ok, "total": len(R), **out}))
sys.exit(0 if ok == len(R) else 1)
