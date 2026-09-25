"""Phase 7.3 real-browser visual acceptance walk (Chromium, the real local stack).

    python p73look.py <screenshot dir> [path to advisorflow.db]

Logs in with the canonical review account through the real login form and
walks every Wholesale / EvoSense screen, all nine deal-workspace tabs, the
Add Property / Import list / Add Buyer / Import buyers drawers, the Seller
Portal and the Investor Deal Room at 1550 / 1280 / 820 / 390. For each it
checks: horizontal overflow, page errors, touch targets under 44px at
tablet/phone widths, required text, and that no dark (pre-7.3) canvas is left
anywhere on the page. Writes one full-page PNG per screen per width.
"""
import json, pathlib, sqlite3, sys
from playwright.sync_api import sync_playwright

WEB = "http://localhost:5173"
OUT = pathlib.Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
ALL = OUT / "all-widths"; ALL.mkdir(exist_ok=True)
DB = sys.argv[2] if len(sys.argv) > 2 else r"C:\Dev\advisorflow-web\advisorflow.db"
ORG = "acc04387-77b2-4e93-9835-5a559ebd87f2"

db = sqlite3.connect(DB)
FLAG = db.execute("select id from evosense_properties where organization_id=? and street_address='1418 Cedar Springs Rd'", (ORG,)).fetchone()[0]
links = dict(db.execute("select audience, token from wholesale_share_links where organization_id=? and revoked_at is null order by created_at", (ORG,)).fetchall())
DEAL = db.execute("select d.id from wholesale_deals d join wholesale_properties p on p.id=d.property_id where d.organization_id=? and p.street_address='6613 Lovett Ave'", (ORG,)).fetchone()[0]
STRAT = db.execute("select id from evosense_strategies where organization_id=? and status='active' order by created_at limit 1", (ORG,)).fetchone()[0]

# (name, path, must-contain, action) — action: None | ('tab', label) | ('click', text)
TABS = ["Overview", "Seller & conversation", "Analysis & comps", "Offer & approvals", "Contracts & documents",
        "Buyer matching", "Assignment & closing", "Sharing", "Audit history"]
PAGES = [
    ("01-acquisition-command", "/wholesale/evosense", ["EvoSense Acquisition Command", "Top Opportunities", "1418 Cedar Springs Rd", "System status"], None),
    ("03-discovery-inbox", "/wholesale/evosense/inbox", ["Discovery Inbox", "All Properties", "1418 Cedar Springs Rd", "Budget Blocked"], None),
    ("04-property-intelligence", "/wholesale/evosense/property/" + FLAG, ["1418 Cedar Springs Rd", "Opportunity score", "Seller conversation", "Key numbers"], None),
    ("05-strategies", "/wholesale/evosense/strategies", ["EvoSense Strategies", "All Strategies", "DFW Distressed SFR", "Open strategy"], None),
    ("06-strategy-builder", "/wholesale/evosense/strategies/new", ["Tell EvoSense what to hunt", "Your strategy", "How often should it hunt"], None),
    ("06b-strategy-edit", "/wholesale/evosense/strategies/" + STRAT, ["Strategy builder", "Your strategy"], None),
    ("07-providers-controls", "/wholesale/evosense/controls", ["Providers & Controls", "Service Providers", "Data capabilities", "Sandbox"], None),
    ("07b-controls-compliance", "/wholesale/evosense/controls", ["EvoSense controls", "Compliance guarantees"], ("click", "Controls & Compliance")),
    ("07c-usage-costs", "/wholesale/evosense/controls", ["Budget", "Spent this month"], ("click", "Usage & Costs")),
    ("08-deal-operations", "/wholesale", ["Deal Operations", "Expected pipeline value", "Fees collected", "6613 Lovett Ave"], None),
    ("08b-contracts-closing", "/wholesale/closing", ["Contracts & Closing", "4119 Bonnie View Rd"], None),
    ("08c-dispositions", "/wholesale/dispositions", ["Dispositions", "Matched buyers"], None),
    ("09-properties", "/wholesale/properties", ["Properties", "+ Add property", "Import list", "2847 Kilburn Ave"], None),
    ("09b-add-property", "/wholesale/properties?add=1", ["Add a property"], None),
    ("09c-import-list", "/wholesale/properties", ["Import a list"], ("click", "Import list")),
    ("10-cash-buyers", "/wholesale/buyers", ["Cash Buyers", "Trinity Cash Homes", "Verified cash"], None),
    ("10b-add-buyer", "/wholesale/buyers", ["Add"], ("click", "+ Add buyer")),
    ("10c-import-buyers", "/wholesale/buyers", ["Import"], ("click", "Import buyers")),
    ("11-wholesale-settings", "/wholesale/settings", ["Wholesale Settings", "Deal rules", "Seller assistant"], None),
    ("12-seller-portal", "/my-property/" + links.get("seller", "missing"), ["6613 Lovett Ave", "Seller Portal"], None),
    ("13-investor-deal-room", "/investor/" + links.get("buyer", "missing"), ["6613 Lovett Ave", "Make an offer", "Investor Deal Room"], None),
] + [("08d-deal-tab-%d-%s" % (i + 1, t.split(" ")[0].lower()), "/wholesale/deals/" + DEAL, ["6613 Lovett Ave", "Key numbers"], ("tab", t))
     for i, t in enumerate(TABS)]

CHECK = """(w) => {
  const over = document.documentElement.scrollWidth - window.innerWidth;
  const root = document.querySelector('.evo-drawer') || document.querySelector('.evo-app') || document.querySelector('.wr') || document.body;
  const small = w > 1024 ? [] : [...root.querySelectorAll('button, a.evo-btn, select, input:not([type=checkbox]):not([type=radio]):not([type=file])')]
    .filter(e => { const r = e.getBoundingClientRect(); const cs = getComputedStyle(e);
                   return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && r.height < 43.5 })
    .map(e => (e.className || e.tagName) + ':' + Math.round(e.getBoundingClientRect().height)).slice(0, 5);
  // Any large DARK surface left on the page (the rejected 7.2 look), outside the
  // hero banners, whose dark overlay is intentional.
  const lum = (c) => { const m = c.match(/rgba?\\(([\\d.]+),\\s*([\\d.]+),\\s*([\\d.]+)(?:,\\s*([\\d.]+))?/); if (!m) return null;
    if (m[4] !== undefined && +m[4] < 0.5) return null; return (0.2126*m[1] + 0.7152*m[2] + 0.0722*m[3]) / 255 };
  const dark = [...document.querySelectorAll('main *, .evo-drawer *, .wr *, .sidebar, .top-bar')].filter(e => {
    if (e.closest('.evo-herobox, .evo-strat__art, .evo-ring, .evo-btn, .btn, .evo-thumb, .wsx-user__avatar, .user-avatar, .evo-toggle, svg')) return false;
    const r = e.getBoundingClientRect(); if (r.width < 160 || r.height < 60) return false;
    const L = lum(getComputedStyle(e).backgroundColor); return L !== null && L < 0.35 })
    .map(e => e.tagName + '.' + String(e.className).slice(0, 40)).slice(0, 4);
  const text = (document.querySelector('.evo-drawer') ? document.querySelector('.evo-drawer').innerText + '\\n' : '') + document.body.innerText;
  return {overflow: over, small_targets: small, dark_surfaces: dark, text} }"""

report = []
with sync_playwright() as p:
    b = p.chromium.launch()
    for w in (1550, 1280, 820, 390):
        touch = w <= 820
        ctx = b.new_context(viewport={"width": w, "height": 950 if w > 500 else 860}, has_touch=touch, is_mobile=w <= 500)
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
        pg.goto(WEB + "/login", wait_until="networkidle")
        pg.fill("input[type=email]", "evosense.review@example.test")
        pg.fill("input[type=password]", "EvoSense-Review-2026!")
        pg.keyboard.press("Enter")
        pg.wait_for_timeout(3500)
        for name, path, must, action in PAGES:
            pg.goto(WEB + path, wait_until="networkidle")
            pg.wait_for_timeout(1200)
            try:
                if action and action[0] == "tab":
                    pg.locator(".ws-tab", has_text=action[1]).first.click()
                    pg.wait_for_timeout(1000)
                elif action and action[0] == "click":
                    pg.get_by_role("button", name=action[1]).first.click() if pg.get_by_role("button", name=action[1]).count() \
                        else pg.get_by_role("tab", name=action[1]).first.click()
                    pg.wait_for_timeout(900)
            except Exception as e:                               # noqa: BLE001
                errs.append("action %s failed: %s" % (name, str(e)[:120]))
            info = pg.evaluate(CHECK, w)
            text = info.pop("text")
            missing = [m for m in must if m.lower() not in text.lower()]
            leaked = [s for s in ("469-553-7417",) if s in text]
            pg.screenshot(path=str(ALL / f"{name}-{w}.png"), full_page=True)
            if w == 1550:
                pg.screenshot(path=str(OUT / f"{name}-desktop.png"), full_page=True)
            if w == 390 and name in ("01-acquisition-command", "08-deal-operations", "13-investor-deal-room"):
                pg.screenshot(path=str(OUT / f"{name}-mobile.png"), full_page=True)
            report.append({"width": w, "page": name, "missing": missing, "platform_phone_shown": leaked, **info})
        report.append({"width": w, "page_errors": errs})
        ctx.close()
    b.close()

(OUT / "review-report.json").write_text(json.dumps(report, indent=1))
problems = [r for r in report if r.get("overflow", 0) > 0 or r.get("small_targets") or r.get("missing")
            or r.get("page_errors") or r.get("dark_surfaces") or r.get("platform_phone_shown")]
print(json.dumps(problems, indent=1))
print("screens checked:", len([r for r in report if "page" in r]), "| problems:", len(problems))
