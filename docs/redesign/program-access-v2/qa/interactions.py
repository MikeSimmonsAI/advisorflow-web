"""Click through Program Access V2 in a real browser (test data, fake API).

Same setup as shots.py. Prints one PASS/FAIL line per check and exits 1 on any
failure.
"""
import json
import sys

from playwright.sync_api import sync_playwright

sys.path.insert(0, __import__("os").path.dirname(__file__))
import shots  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (" - " + detail if detail and not ok else ""), flush=True)


def main():
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome",
                              args=["--no-sandbox", "--disable-gpu", "--no-first-run", "--disable-sync",
                                    "--disable-background-networking"])
        ctx = b.new_context(viewport={"width": 1536, "height": 1000})
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        log = shots.run(page, "owner", {})

        cards = page.locator(".pa2-grid [data-program-key]")
        check("16 workspace cards render", cards.count() == 16, str(cards.count()))
        check("3 prospect previews render", page.locator(".pa2-pv").count() == 3)
        check("featured banner shows the first card (SCI)", page.locator("#pa2-feature-title").inner_text() == "SCI")
        stats = page.locator(".pa2-stat-value").all_inner_texts()
        check("stat tiles show 16 / 10 / 3 / Live", stats == ["16", "10", "3", "Live"], str(stats))
        check("platform health from /health/ready", "Platform Health" in page.locator(".pa2-stats").inner_text())
        # desktop cards are SHORT and WIDE (mockup first row: ~333 x 140)
        box = page.locator(".pa2-grid .pa2-card").first.bounding_box()
        ratio = box["width"] / box["height"]
        check("desktop cards are short and wide (2.2-2.6 : 1)", 2.2 <= ratio <= 2.6, "%.0fx%.0f ratio %.2f" % (box["width"], box["height"], ratio))
        cols = len({round(b["x"]) for b in page.locator(".pa2-grid .pa2-card").evaluate_all("els => els.map(e => e.getBoundingClientRect().toJSON())")})
        check("four columns at desktop width", cols == 4, str(cols))
        hs = page.locator(".pa2-grid .pa2-card").evaluate_all("els => els.map(e => Math.round(e.getBoundingClientRect().height))")
        check("first row tall (~120-145), later rows compact (90-112)",
              all(120 <= h <= 145 for h in hs[:4]) and all(90 <= h <= 112 for h in hs[4:]), str(hs))
        clipped = page.locator(".pa2-grid .pa2-card").evaluate_all("""els => els.filter(c => {
            const n = c.querySelector('.pa2-card-titles').getBoundingClientRect();
            const f = c.querySelector('.pa2-card-foot').getBoundingClientRect();
            const name = c.querySelector('.pa2-card-name');
            return n.bottom > f.top + 1 || name.scrollHeight > name.clientHeight + 1 }).map(c => c.dataset.programKey)""")
        check("no card text overlaps its buttons or is cut off", not clipped, str(clipped))
        check("pinned/featured first: first card is the first in server order (SCI)",
              page.locator(".pa2-grid .pa2-card-name").first.inner_text() == "SCI")
        check("God Mode link for the owner", page.locator('a.pa2-nav-item[href="/god"]').count() == 1)
        nav = [" ".join(t.split()) for t in page.locator(".pa2-nav-item").all_inner_texts()]
        check("owner sidebar says where each link goes",
              nav == ["Program Access", "Demos 7", "Sales — My Day", "Notifications 2", "People & Access", "Training",
                      "God Mode", "Change Password"], str(nav))

        # search
        page.fill(".pa2-search input", "deal builder")
        names = page.locator(".pa2-grid .pa2-card-name").all_inner_texts()
        check("search filters cards", sorted(names) == ["Executive Deal Builder", "Salesperson Deal Builder"], str(names))
        check("featured banner hides while searching", page.locator(".pa2-feature").count() == 0)
        page.fill(".pa2-search input", "zzzz")
        check("search with no match shows a message", "No workspace you can open" in page.locator(".pa2-section").inner_text())
        page.fill(".pa2-search input", "")

        # filters (and filters + search together)
        page.get_by_role("button", name="Operations", exact=True).click()
        names = sorted(page.locator(".pa2-grid .pa2-card-name").all_inner_texts())
        check("Operations filter", names == ["Acquisition Control Center", "Consumer Leads", "VOLT Marketing"], str(names))
        page.fill(".pa2-search input", "volt")
        names = sorted(page.locator(".pa2-grid .pa2-card-name").all_inner_texts())
        check("filter + search combine", names == ["Consumer Leads", "VOLT Marketing"], str(names))
        page.fill(".pa2-search input", "")
        page.get_by_role("button", name="Administration", exact=True).click()
        check("Administration filter", page.locator(".pa2-grid .pa2-card-name").all_inner_texts() == ["Back Office"])
        page.get_by_role("button", name="All Workspaces (16)", exact=True).click()

        # list / grid toggle (remembered)
        page.get_by_role("button", name="List view").click()
        check("list view", page.locator(".pa2-card--list").count() == 16)
        check("list view remembered", page.evaluate("localStorage.getItem('ev.programAccess.view')") == "list")
        page.get_by_role("button", name="Grid view").click()

        # pin -> saved on the server -> becomes featured and moves first
        page.get_by_role("button", name="Pin Commercial Cleaning Blueprint").click()
        page.wait_for_timeout(300)
        puts = [l for l in log if l == ("PUT", "/launcher/pins")]
        check("pin saved through /launcher/pins", len(puts) == 1)
        check("pinned card becomes featured",
              page.locator("#pa2-feature-title").inner_text() == "Commercial Cleaning Blueprint")
        check("pinned card listed first", page.locator(".pa2-grid .pa2-card-name").first.inner_text() == "Commercial Cleaning Blueprint")
        page.get_by_role("button", name="Unpin Commercial Cleaning Blueprint").click()
        page.wait_for_timeout(300)
        check("unpin restores SCI as featured", page.locator("#pa2-feature-title").inner_text() == "SCI")
        check("Manage Access (owner) goes to access management",
              page.locator(".pa2-feature a.pa2-btn--ghost").get_attribute("href") == "/god/access")

        # notifications
        page.get_by_role("button", name="Notifications: 2 need attention").click()
        check("notifications list opens", page.locator(".pa2-bell-pop li").count() == 2)
        page.keyboard.press("Escape")
        check("Escape closes notifications", page.locator(".pa2-bell-pop").count() == 0)

        # previews are real links
        hrefs = page.locator(".pa2-pv a").evaluate_all("els => els.map(e => [e.getAttribute('href'), e.target])")
        check("preview buttons are real new-tab links", all(h and t == "_blank" for h, t in hrefs), str(hrefs))

        # deal builder is a plain link with the Back target
        db = page.get_by_role("link", name="Open Deal Builder: Executive Deal Builder")
        check("Executive Deal Builder link", db.get_attribute("href") == "/team-prices/executive-deal-builder?from=/workspaces")

        # keyboard: Tab reaches card actions with a visible focus ring
        page.locator(".pa2-chip").first.focus()
        for _ in range(4):
            page.keyboard.press("Tab")
        focused = page.evaluate("document.activeElement && document.activeElement.className")
        check("keyboard reaches workspace actions", "pa2" in (focused or ""), focused)

        # opening a workspace goes through openProgramItem -> /workspace/<id>
        page.get_by_role("button", name="Open Workspace: Atlantis Light & Power").click()
        page.wait_for_timeout(500)
        check("Open Workspace navigates to the workspace", "/workspace/org-atl" in page.url, page.url)
        check("no page errors", not errors, "; ".join(errors))
        ctx.close()

        # demos page: sidebar item, view switch, filters, dead links, back
        ctx = b.new_context(viewport={"width": 1536, "height": 1000})
        page = ctx.new_page()
        shots.run(page, "owner", {})
        page.get_by_role("link", name="Demos 7").click()
        page.wait_for_selector(".pa2-demos")
        check("Demos opens from the sidebar (url ?view=demos)", "view=demos" in page.url, page.url)
        check("Demos lists all 7 demos incl. lost deals", page.locator(".pa2-demo").count() == 7)
        names = page.locator(".pa2-demo-name").all_inner_texts()
        check("lost demos are back (Countryside, VP Wraps, Building Equity, Commercial Cleaning)",
              all(n in names for n in ["Countryside Land Partners", "VP Wraps Vehicle Protection",
                                       "Building Equity Investments LLC", "Commercial Cleaning Blueprint"]), str(names))
        check("workspace cards and banner are not shown on Demos", page.locator(".pa2-grid .pa2-card").count() == 0
              and page.locator(".pa2-feature").count() == 0)
        page.get_by_role("button", name="Lost (4)").click()
        check("Lost filter", page.locator(".pa2-demo").count() == 4)
        page.get_by_role("button", name="Link expired (1)").click()
        dead = page.locator(".pa2-demo").first
        check("expired demo has no Open link", dead.locator("a").count() == 0 and "no longer works" in dead.inner_text())
        page.get_by_role("button", name="All (7)").click()
        hrefs = page.locator(".pa2-demo a").evaluate_all("els => els.map(e => [e.getAttribute('href'), e.target])")
        check("live demos open in a new tab", len(hrefs) == 6 and all(h and t == "_blank" for h, t in hrefs), str(hrefs))
        page.fill(".pa2-search input", "wraps")
        check("search works on Demos", page.locator(".pa2-demo-name").all_inner_texts() == ["VP Wraps Vehicle Protection"])
        page.fill(".pa2-search input", "")
        page.go_back()
        page.wait_for_timeout(300)
        check("browser Back returns to the workspaces", page.locator(".pa2-grid .pa2-card").count() == 16)
        page.get_by_role("link", name="Demos 7").click()
        page.get_by_role("button", name="← Program Access").click()
        page.wait_for_timeout(300)
        check("Program Access button returns to the workspaces", page.locator(".pa2-grid .pa2-card").count() == 16 and "view=" not in page.url)
        ctx.close()

        # salesperson: no owner-only items, pinned featured
        ctx = b.new_context(viewport={"width": 1536, "height": 1000})
        page = ctx.new_page()
        shots.run(page, "salesperson", {})
        nav = page.locator(".pa2-nav").inner_text()
        check("salesperson: no God Mode / People & Access", "God Mode" not in nav and "People" not in nav, nav)
        check("salesperson: no Demos item (server lists none)", "Demos" not in nav, nav)
        check("salesperson: no My Workspaces item", "My Workspaces" not in nav, nav)
        check("salesperson: no Executive Deal Builder", "Executive Deal Builder" not in page.content())
        check("salesperson: pinned Deal Builder is featured",
              page.locator("#pa2-feature-title").inner_text() == "Salesperson Deal Builder")
        check("salesperson: own name shown", "Welcome back, Jordan" in page.locator("h1").inner_text())
        check("salesperson: no Manage Access", page.locator(".pa2-feature a.pa2-btn--ghost").count() == 0)
        check("salesperson: no My Day (no sales Back Office)", "My Day" not in nav)
        ctx.close()

        # operator: health endpoint down -> no health tile
        ctx = b.new_context(viewport={"width": 1536, "height": 1000})
        page = ctx.new_page()
        shots.run(page, "operator", {"health": None})
        check("health tile hidden when /health/ready fails", "Platform Health" not in page.locator("main").inner_text())
        ctx.close()

        # phone: drawer navigation
        ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
        page = ctx.new_page()
        shots.run(page, "owner", {})
        overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        check("phone: no horizontal overflow", overflow <= 0, str(overflow))
        cols = len({round(b["x"]) for b in page.locator(".pa2-grid .pa2-card").evaluate_all("els => els.map(e => e.getBoundingClientRect().toJSON())")})
        check("phone: cards stack in one column", cols == 1, str(cols))
        top = page.locator(".pa2-grid .pa2-card").first.bounding_box()["y"]
        check("phone: first workspace card starts on the first screen", top < 600, str(round(top)))
        labels = page.locator(".pa2-stat").evaluate_all("els => els.filter(e => { const l = e.querySelector('.pa2-stat-label'); return l.scrollWidth > l.clientWidth + 1 }).length")
        check("phone: stat labels not clipped", labels == 0, str(labels))
        small = page.locator(".pa2-btn, .pa2-chip, .pa2-menu, .pa2-bell-btn").evaluate_all("els => els.filter(e => { const r = e.getBoundingClientRect(); return r.width && r.height < 30 }).length")
        check("phone: touch targets at least 30px tall", small == 0, str(small))
        page.get_by_role("button", name="Open menu").click()
        page.wait_for_timeout(400)
        box = page.locator(".pa2-side").bounding_box()
        check("phone: menu drawer opens", box and box["x"] >= -1, str(box))
        page.get_by_role("button", name="Close menu").click()
        page.wait_for_timeout(400)
        box = page.locator(".pa2-side").bounding_box()
        check("phone: menu drawer closes", box and box["x"] < -100, str(box))
        ctx.close()
        b.close()
    print(f"{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
