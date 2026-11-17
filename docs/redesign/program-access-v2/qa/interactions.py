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
        check("stat tiles show 16 / 10 / 3", stats == ["16", "10", "3"], str(stats))
        check("platform health from /health/ready", "All systems normal" in page.locator(".pa2-stats").inner_text())
        check("God Mode link for the owner", page.locator('a.pa2-nav-item[href="/god"]').count() == 1)

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
        page.get_by_role("button", name="All Workspaces", exact=True).click()

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

        # salesperson: no owner-only items, pinned featured
        ctx = b.new_context(viewport={"width": 1536, "height": 1000})
        page = ctx.new_page()
        shots.run(page, "salesperson", {})
        nav = page.locator(".pa2-nav").inner_text()
        check("salesperson: no God Mode / People & Access", "God Mode" not in nav and "People" not in nav, nav)
        check("salesperson: no Executive Deal Builder", "Executive Deal Builder" not in page.content())
        check("salesperson: pinned Deal Builder is featured",
              page.locator("#pa2-feature-title").inner_text() == "Salesperson Deal Builder")
        check("salesperson: own name shown", "Welcome back, Jordan" in page.locator("h1").inner_text())
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
