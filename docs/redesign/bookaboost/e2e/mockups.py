"""BookaBoost Program Access - screenshots of the REAL built UI + checks.

LOCAL ONLY. Needs run_local.py (backend on 127.0.0.1:8010, synthetic data),
the branch's frontend built with VITE_API_BASE_URL=http://127.0.0.1:8010 and
served on localhost:5173, and (for the EvoSys before/after) origin/main's
frontend built the same way on localhost:5174. Every request to any other
host is aborted.

Usage: python -I mockups.py OUT_DIR
Writes PNGs to OUT_DIR and prints PASS/FAIL lines.
"""
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
APP = "http://localhost:5173"
BEFORE = "http://localhost:5174"
PW = "LocalTest!1"
results = []
blocked = set()
errors = []
FOREIGN = re.compile(r"EvoSys|EvoSense", re.I)


def ok(name, cond, extra=""):
    results.append(("PASS" if cond else "FAIL") + "  " + name + (" - " + str(extra) if extra else ""))


def guard(page, tag):
    page.set_default_timeout(90000)   # a busy machine is slow, not broken
    def route(r):
        host = r.request.url.split("/")[2].split(":")[0] if r.request.url.startswith("http") else ""
        # the app's own font files (already loaded by frontend/index.html) are
        # allowed so the screenshots show the real type; nothing else leaves.
        if not r.request.url.startswith("http") or host in ("127.0.0.1", "localhost", "fonts.googleapis.com", "fonts.gstatic.com"):
            return r.continue_()
        blocked.add(host)
        return r.abort()
    page.route("**/*", route)
    page.on("pageerror", lambda e: errors.append(tag + " PAGEERROR " + str(e)))
    page.on("console", lambda m: errors.append(tag + " " + m.text)
            if m.type == "error" and not re.search(r"Failed to load resource|net::ERR|40[134]", m.text) else None)


def overflow(page):
    return page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")


def visible_text(page):
    return page.evaluate("document.body.innerText")


def login(page, base, email):
    page.goto(base + "/login", wait_until="networkidle")
    page.evaluate("localStorage.clear(); sessionStorage.clear()")
    page.goto(base + "/login", wait_until="networkidle")
    page.fill("input[type=email]", email)
    page.fill("input[type=password]", PW)
    page.keyboard.press("Enter")
    page.wait_for_timeout(2500)
    page.wait_for_load_state("networkidle")


with sync_playwright() as p:
    browser = p.chromium.launch()

    # ── A. BookaBoost Program Access ─────────────────────────────────────
    ctx = browser.new_context(viewport={"width": 1600, "height": 1000})
    page = ctx.new_page()
    guard(page, "bb")
    login(page, APP, "bb-admin@example.com")
    ok("A1 BookaBoost admin lands on Program Access", page.url.endswith("/workspaces"), page.url)
    page.wait_for_selector(".bbpa-card")
    page.wait_for_timeout(800)
    ok("A2 BookaBoost browser title", page.title() == "BookaBoost · Program Access", page.title())
    txt = visible_text(page)
    ok("A3 'Choose your business' welcome", "Choose your business." in txt)
    ok("A4 no EvoSys/EvoSense text on BookaBoost Program Access", not FOREIGN.search(txt), FOREIGN.findall(txt))
    names = page.eval_on_selector_all("[aria-label='Your businesses'] .bbpa-title", "e => e.map(x => x.textContent)")
    ok("A5 operational cards are exactly Wholesale + Back Office", sorted(names) == ["Back Office", "Wholesale"], names)
    pv = page.eval_on_selector_all("[aria-label='Authorized previews'] .bbpa-title", "e => e.map(x => x.textContent)")
    ok("A6 Countryside shows under Authorized preview", pv == ["Countryside Land Partners"], pv)
    href = page.get_attribute("[aria-label='Authorized previews'] a.bbpa-card", "href") or ""
    ok("A7 preview card opens a signed-in path, not a demo link", href.startswith("/previews/") and "/demo/" not in href, href)
    html = page.content()
    ok("A8 no public demo address anywhere in the page", "/demo/" not in html)
    ok("A9 logo is the real mark file", page.locator(".bbpa-logo img").get_attribute("src") == "/brands/bookaboost-mark.svg")
    for w, h, tag in ((1600, 1000, "desktop-1600"), (820, 1180, "tablet-820"), (390, 844, "mobile-390")):
        page.set_viewport_size({"width": w, "height": h})
        page.wait_for_timeout(500)
        ok("A10 no horizontal overflow at %s" % tag, overflow(page) <= 0, overflow(page))
        page.screenshot(path=str(OUT / ("A-program-access-%s.png" % tag)), full_page=True)
    page.set_viewport_size({"width": 1600, "height": 1000})
    page.wait_for_timeout(300)
    # keyboard focus: Tab until a card has focus, and its outline is drawn
    focused = None
    for _ in range(12):
        page.keyboard.press("Tab")
        focused = page.evaluate("(() => { const a = document.activeElement; return a && a.classList.contains('bbpa-card') ? [a.getAttribute('aria-label'), getComputedStyle(a).outlineStyle, getComputedStyle(a).outlineColor] : null })()")
        if focused:
            break
    ok("A11 keyboard reaches a card and shows a gold focus ring", bool(focused) and focused[1] == "solid", focused)
    page.screenshot(path=str(OUT / "A-program-access-keyboard-focus.png"))
    # D. the Countryside tile as it appears
    page.locator(".bbpa-previews").screenshot(path=str(OUT / "D-countryside-authorized-preview-tile.png"))

    # open the preview (same tab, signed in, sandboxed frame)
    page.click("[aria-label='Authorized previews'] a.bbpa-card")
    page.wait_for_timeout(2000)
    ok("D1 preview opens inside the app at /previews/…", "/previews/" in page.url, page.url)
    frame = page.locator("iframe")
    ok("D2 preview is in the sandboxed frame (allow-scripts only)", frame.get_attribute("sandbox") == "allow-scripts")
    inner = page.frame_locator("iframe").locator("h1").inner_text()
    ok("D3 the stored demo shows unchanged (its own title)", inner == "Countryside Land Partners", inner)
    page.screenshot(path=str(OUT / "D-countryside-preview-open.png"))
    page.click("text=← Program Access")
    page.wait_for_timeout(1500)

    # B. Back Office (empty state)
    page.click("[aria-label='Your businesses'] button.bbpa-card[data-program='back_office']")
    page.wait_for_timeout(2500)
    page.wait_for_load_state("networkidle")
    ok("B1 Back Office opens /sales", "/sales" in page.url, page.url)
    btxt = visible_text(page)
    ok("B2 Back Office names BookaBoost", "BookaBoost" in btxt)
    ok("B3 Back Office shows the BookaBoost mark", page.locator("img.sw-brandmark--img").count() == 1)
    ok("B4 no EvoSys/EvoSense text on the Back Office landing", not FOREIGN.search(btxt), FOREIGN.findall(btxt))
    ok("B5 no horizontal overflow (Back Office)", overflow(page) <= 0, overflow(page))
    page.screenshot(path=str(OUT / "B-back-office-bookaboost-empty-desktop-1600.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(500)
    page.screenshot(path=str(OUT / "B-back-office-bookaboost-empty-mobile-390.png"), full_page=True)
    page.set_viewport_size({"width": 1600, "height": 1000})

    # C. Wholesale landing
    page.goto(APP + "/workspaces", wait_until="networkidle")
    page.wait_for_selector(".bbpa-card")
    page.click("[aria-label='Your businesses'] button.bbpa-card[data-program='wholesale']")
    page.wait_for_timeout(4000)
    page.wait_for_load_state("networkidle")
    ws_url = page.url
    ok("C1 Wholesale opens a /wholesale screen", "/wholesale" in ws_url, ws_url)
    wtxt = visible_text(page)
    nav = page.eval_on_selector_all("nav a, aside a", "e => e.map(x => x.textContent.trim()).filter(Boolean)")
    ok("C2 Wholesale shows the BookaBoost logo", page.locator("img[src='/brands/bookaboost-logo.svg']").count() >= 1)
    ok("C3 Wholesale navigation present", len(nav) >= 8, len(nav))
    leaks = sorted(set(FOREIGN.findall(wtxt)))
    ok("C4 no EvoSys/EvoSense text visible on the BookaBoost Wholesale landing", not leaks, leaks)
    ok("C5 no horizontal overflow (Wholesale)", overflow(page) <= 0, overflow(page))
    page.screenshot(path=str(OUT / "C-wholesale-bookaboost-desktop-1600.png"), full_page=False)
    (OUT / "C-wholesale-nav.txt").write_text("\n".join(nav))
    ws_id = page.evaluate("localStorage.getItem('af_workspace_id')")
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(2500)
    ok("C6 refresh keeps the workspace and the screen",
       page.url == ws_url and page.evaluate("localStorage.getItem('af_workspace_id')") == ws_id, page.url)

    # sign out from Program Access
    page.goto(APP + "/workspaces", wait_until="networkidle")
    page.wait_for_selector(".bbpa-signout")
    page.click(".bbpa-signout")
    page.wait_for_timeout(2000)
    ok("A12 sign out returns to /login and clears the session",
       "/login" in page.url and page.evaluate("localStorage.getItem('af_token')") is None, page.url)
    ctx.close()

    # ── States: empty (real), loading and error (on a BookaBoost address) ──
    c = browser.new_context(viewport={"width": 1600, "height": 1000})
    pg = c.new_page()
    guard(pg, "bb-empty")
    login(pg, APP, "bb-empty@example.com")
    pg.goto(APP + "/workspaces", wait_until="networkidle")
    pg.wait_for_timeout(1200)
    ok("S1 empty state in the BookaBoost look", pg.locator(".bbpa .bbpa-message").count() == 1
       and "No businesses are assigned" in visible_text(pg))
    pg.screenshot(path=str(OUT / "S-program-access-empty-1600.png"))
    c.close()
    # A BookaBoost address, mapped to this machine; /auth/my-contexts is held
    # (loading) and then answered with a 503 (error).
    b2 = p.chromium.launch(args=["--host-resolver-rules=MAP app.bookaboost.test 127.0.0.1"])
    c = b2.new_context(viewport={"width": 1600, "height": 1000})
    pg = c.new_page()
    pg.route("**/*", lambda r: r.continue_() if any(h in r.request.url for h in ("app.bookaboost.test", "127.0.0.1", "localhost", "fonts.g")) else r.abort())
    bb_app = APP.replace("localhost", "app.bookaboost.test")
    pg.goto(bb_app + "/login", wait_until="domcontentloaded")
    pg.evaluate("""localStorage.setItem('af_token', 'local-placeholder');
                   localStorage.setItem('af_user', JSON.stringify({full_name: 'Jordan Example', role: 'org_admin'}))""")
    hold = []
    pg.route("**/auth/my-contexts*", lambda r: hold.append(r))
    pg.goto(bb_app + "/workspaces", wait_until="domcontentloaded")
    pg.wait_for_timeout(1500)
    ok("S2 loading state in the BookaBoost look", pg.locator(".bbpa .bbpa-skeleton").count() == 2)
    pg.screenshot(path=str(OUT / "S-program-access-loading-1600.png"))
    pg.unroute("**/auth/my-contexts*")
    for r in hold:
        r.abort()
    pg.route("**/auth/my-contexts*", lambda r: r.fulfill(status=503, content_type="application/json",
                                                          body='{"detail":"unavailable (local test)"}'))
    pg.goto(bb_app + "/workspaces", wait_until="domcontentloaded")
    try:
        pg.wait_for_selector(".bbpa [role=alert]", timeout=30000)
    except Exception:
        pass
    pg.wait_for_timeout(2000)   # let the app's start-up splash finish fading
    ok("S3 error state in the BookaBoost look, with Try again",
       pg.locator(".bbpa [role=alert]").count() == 1 and pg.locator(".bbpa-retry").count() == 1)
    ok("S4 BookaBoost title while loading/error", pg.title() == "BookaBoost · Program Access", pg.title())
    pg.screenshot(path=str(OUT / "S-program-access-error-1600.png"))
    b2.close()

    # ── EvoSys Program Access: before (origin/main) vs after (this branch) ──
    shots = {}
    for tag, base in (("before", BEFORE), ("after", APP)):
        c = browser.new_context(viewport={"width": 1440, "height": 900})
        pg = c.new_page()
        guard(pg, "evo-" + tag)
        login(pg, base, "ops@example.com")
        pg.wait_for_selector(".pa-portal")
        pg.wait_for_timeout(1200)
        path = OUT / ("E-evosys-program-access-%s-1440.png" % tag)
        pg.screenshot(path=str(path), full_page=True)
        shots[tag] = path
        ok("E1 EvoSys launcher (%s) has no BookaBoost look" % tag, pg.locator(".bbpa").count() == 0)
        if tag == "after":
            # the same Wholesale navigation as the BookaBoost Wholesale above
            pg.evaluate("""(() => { const li = [...document.querySelectorAll('[role=listitem]')].find(x => x.querySelector('.ev-wcard-name')?.textContent.trim() === 'Wholesale'); li.querySelector('button.ev-wcard-open').click() })()""")
            pg.wait_for_timeout(4000)
            pg.wait_for_load_state("networkidle")
            evo_nav = pg.eval_on_selector_all("nav a, aside a", "e => e.map(x => x.textContent.trim()).filter(Boolean)")
            ok("C7 BookaBoost Wholesale has exactly the EvoSys Wholesale navigation", evo_nav == nav,
               sorted(set(evo_nav) ^ set(nav)))
            pg.screenshot(path=str(OUT / "E-evosys-wholesale-after-1600.png"))
        c.close()
    from PIL import Image, ImageChops
    a, b = Image.open(shots["before"]).convert("RGB"), Image.open(shots["after"]).convert("RGB")
    same = a.size == b.size and ImageChops.difference(a, b).getbbox() is None
    ok("E2 EvoSys Program Access is pixel-identical before/after", same, (a.size, b.size))
    browser.close()

ok("Z1 no console errors", not errors, errors[:6])
print("\n".join(results))
print("blocked hosts:", sorted(blocked))
sys.exit(0 if all(r.startswith("PASS") for r in results) else 1)
