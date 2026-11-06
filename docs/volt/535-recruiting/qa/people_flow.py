"""VOLT people-focused homepage (approved mockup, Nov 6): images, layout
stability, copy rules and preserved behavior.
usage: python3 -I people_flow.py <frontend> <shots_dir>"""
import json, sys
from playwright.sync_api import sync_playwright

FE, SHOTS = sys.argv[1].rstrip("/"), sys.argv[2]
R = []


def check(l, ok, d=""):
    R.append((l, bool(ok))); print(("PASS  " if ok else "FAIL  ") + l + ("" if ok else "  -- %s" % str(d)[:300]), flush=True)


CLS_JS = """window.__cls = 0; new PerformanceObserver((l) => { for (const e of l.getEntries()) if (!e.hadRecentInput) window.__cls += e.value })
  .observe({ type: 'layout-shift', buffered: true });"""

with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    for name, args in (("desktop", {"viewport": {"width": 1366, "height": 900}}), ("phone", dict(p.devices["iPhone 13"]))):
        ctx = b.new_context(**args)
        ctx.add_init_script(CLS_JS)
        pg = ctx.new_page()
        pg.goto(FE + "/volt?ref=REP-1050"); pg.wait_for_selector("[data-photo=hero]")
        pg.wait_for_timeout(1500)
        imgs = pg.evaluate("""[...document.querySelectorAll('.volt img[data-photo]')].map(i => ({p: i.dataset.photo, alt: i.alt,
            w: i.getAttribute('width'), h: i.getAttribute('height'), lazy: i.loading, srcset: !!i.srcset, src: i.currentSrc || i.src}))""")
        names = [i["p"] for i in imgs]
        check(f"{name}: all seven people images present", names == ["hero", "card-fiber", "card-aca", "reason-1", "reason-2", "reason-3", "banner"], names)
        check(f"{name}: every image has alt text, reserved size and a srcset",
              all(i["alt"].startswith("Illustrative photo:") and i["w"] and i["h"] and i["srcset"] for i in imgs), imgs)
        check(f"{name}: hero eager, everything below the fold lazy",
              imgs[0]["lazy"] == "eager" and all(i["lazy"] == "lazy" for i in imgs[1:]))
        check(f"{name}: images come from the central VOLT image folder", all("/brands/volt/people/" in i["src"] for i in imgs))
        check(f"{name}: no image alt claims VOLT staff", not any("volt" in i["alt"].lower() or "employee" in i["alt"].lower() for i in imgs))
        # scroll through to load everything and measure layout shift
        for y in range(0, 12000, 400):
            pg.evaluate("window.scrollTo(0,%d)" % y); pg.wait_for_timeout(50)
        pg.wait_for_timeout(600)
        loaded = pg.evaluate("[...document.querySelectorAll('.volt img[data-photo]')].every(i => i.complete && i.naturalWidth > 0)")
        check(f"{name}: all images load", loaded)
        cls = pg.evaluate("window.__cls")
        check(f"{name}: layout shift stays low (CLS {cls:.3f} < 0.1)", cls < 0.1, cls)
        check(f"{name}: no sideways scrolling", pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"),
              pg.evaluate("[document.documentElement.scrollWidth, window.innerWidth]"))
        body = pg.inner_text("main")
        check(f"{name}: hero copy", all(x in body for x in ("Good people.", "Big ambition.", "Your next move.",
                                                          "Build your skills. Meet your people. Explore what’s next with VOLT.")))
        check(f"{name}: company-wide reasons in order",
              [x.strip() for x in pg.locator("[data-why-volt] h3").all_inner_texts()] == ["Find your opportunity.", "Build toward freedom.", "Grow around driven people."])
        check(f"{name}: values and Ownership meaning kept", "Ownership means freedom, independence and control over your future." in body
              and all(w in body.upper() for w in ("VISION", "OWNERSHIP", "LEADERSHIP", "TENACITY")))
        check(f"{name}: closing banner text", all(x in body for x in ("Work hard.", "Grow together.", "Enjoy the journey.", "Your next chapter starts here.")))
        low = body.lower()
        check(f"{name}: Fiber reasons not on the homepage", not any(x in low for x in ("great markets", "industry-leading", "training and development")))
        why = pg.inner_text("[data-why-volt]").lower()
        check(f"{name}: general sections say nothing about travel or door-to-door",
              not any(x in why for x in ("travel", "door")) and not any(x in pg.inner_text(".v-phero").lower() for x in ("travel", "door")))
        check(f"{name}: Fiber card keeps its travel line; ACA card does not mention travel",
              "On the road" in pg.inner_text("[data-project=fiber]") and "road" not in pg.inner_text("[data-project=aca]").lower())
        if name == "phone":
            check("phone: bottom Apply Now bar visible, header Apply hidden",
                  pg.locator("[data-apply=bar]").is_visible() and not pg.locator("[data-apply=header]").is_visible())
        else:
            check("desktop: header Opportunities / Why VOLT / Sign In / Apply Now",
                  pg.locator(".v-navlink", has_text="Opportunities").is_visible() and pg.locator(".v-navlink", has_text="Why VOLT").is_visible()
                  and pg.locator("[data-apply=header]").is_visible())
            pg.evaluate("window.scrollTo(0,0)")
            pg.locator(".v-navlink", has_text="Why VOLT").click(); pg.wait_for_timeout(900)
            top = pg.evaluate("document.getElementById('why').getBoundingClientRect().top")
            check("desktop: 'Why VOLT' scrolls to the reasons", -5 < top < 200, top)
        # Apply flows keep the referral
        pg.goto(FE + "/volt?ref=REP-1050"); pg.wait_for_selector("[data-apply-card=fiber]")
        pg.locator("[data-apply-card=fiber]").click(); pg.wait_for_url("**/volt/apply/fiber")
        pg.wait_for_selector("text=Did they refer you?")
        check(f"{name}: card Apply Now goes to the Fiber application with the invite referral", "Dangelo Simmons" in pg.inner_text("main"))
        pg.goto(FE + "/volt"); pg.wait_for_selector("[data-apply=closing]")
        pg.locator("[data-apply=closing]").click(); pg.wait_for_selector("[data-pick]")
        check(f"{name}: closing Apply Now opens the project chooser", pg.locator("[data-pick]").count() == 2)
        pg.keyboard.press("Escape")
        pg.goto(FE + "/volt"); pg.wait_for_selector("[data-explore-all]")
        pg.locator("[data-explore-all]").click(); pg.wait_for_timeout(900)
        top = pg.evaluate("document.getElementById('projects').getBoundingClientRect().top")
        check(f"{name}: hero Explore Opportunities scrolls to the cards", -5 < top < 260, top)
        pg.goto(FE + "/volt/projects/fiber"); pg.wait_for_selector("[data-opportunity=fiber]")
        ft = pg.inner_text("[data-opportunity=fiber]").upper()
        check(f"{name}: Fiber Explore keeps its three reasons",
              all(x in ft for x in ("GREAT MARKETS", "INDUSTRY-LEADING PEOPLE AND LEADERSHIP", "TRAINING AND DEVELOPMENT")))
        ctx.close()
    b.close()
ok = sum(1 for _, x in R if x); print(json.dumps({"passed": ok, "total": len(R)})); sys.exit(0 if ok == len(R) else 1)
