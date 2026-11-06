import json, sys, uuid, urllib.request
from playwright.sync_api import sync_playwright
FE, API = "http://localhost:5173", "http://127.0.0.1:8011"
R = []
def check(l, ok, d=""): R.append(ok); print(("PASS  " if ok else "FAIL  ") + l + ("" if ok else "  -- " + str(d)))
def reg(i):
    body = {"first_name": "Title", "last_name": "Tester%d" % i, "email": "title-%d-%s@example.test" % (i, uuid.uuid4().hex[:5]),
            "phone": "5550102%03d" % i, "date_of_birth": "1990-01-01", "password": "Volt-Test-2026!", "project_key": "aca"}
    r = urllib.request.Request(API + "/volt/public/volt-marketing/register", data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r).read()), body
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    # Applicant screens: direct navigation + refresh
    out, body = reg(0)
    m = b.new_context(**p.devices["iPhone 13"]).new_page()
    for path in ("/volt", "/volt/sign-in", "/volt/apply/fiber"):
        m.goto(FE + path); m.wait_for_timeout(2500)
        check("title on %s" % path, m.title() == "VOLT Marketing", m.title())
    m.goto(FE + "/volt/sign-in"); m.fill("#v-si-email", body["email"]); m.fill("#v-si-pass", body["password"]); m.locator("button[type=submit]").click()
    m.wait_for_url("**/volt/me"); m.wait_for_timeout(2000)
    check("title on /volt/me", m.title() == "VOLT Marketing", m.title())
    m.reload(); m.wait_for_timeout(2500); check("title after refresh /volt/me", m.title() == "VOLT Marketing", m.title())
    m.goto(FE + "/volt/me/applications/" + out["application_id"]); m.wait_for_timeout(2500)
    check("title on application page", m.title() == "VOLT Marketing", m.title())
    # Staff: owner moves between programs
    s = b.new_context(viewport={"width": 1440, "height": 900}).new_page()
    s.goto(FE + "/login"); s.fill("input[type=email]", "god.local@example.test"); s.fill("input[type=password]", "LocalPass!2026")
    s.locator("button[type=submit]").first.click(); s.wait_for_timeout(3500)
    s.goto(FE + "/workspaces"); s.wait_for_selector("article:has-text('VOLT Marketing')"); s.wait_for_timeout(1500)
    before = s.title()
    check("Program Access is not titled VOLT", before != "VOLT Marketing", before)
    s.locator("article", has_text="VOLT Marketing").first.locator("button, a").first.click()
    s.wait_for_url("**/volt-ops/**"); s.wait_for_timeout(2500)
    check("title in VOLT Operations after card click", s.title() == "VOLT Marketing", s.title())
    org = s.url.split("/volt-ops/")[1].split("/")[0]
    s.reload(); s.wait_for_timeout(3000); check("title after refresh in operations", s.title() == "VOLT Marketing", s.title())
    s.locator("text=Program Access").first.click(); s.wait_for_url("**/workspaces"); s.wait_for_timeout(2500)
    check("leaving VOLT gives the title back", s.title() != "VOLT Marketing", s.title())
    # Purge UX
    reg(1); reg(2)
    s.goto(FE + "/volt-ops/%s/reps" % org); s.wait_for_selector("[data-action=purge-tests]"); s.wait_for_timeout(1000)
    check("title on reps page (direct)", s.title() == "VOLT Marketing", s.title())
    btn = s.locator("[data-action=purge-tests]")
    btn.click()
    s.wait_for_timeout(80)
    busy = btn.is_disabled() and "Removing" in btn.inner_text()
    btn.click(force=True)  # a repeat click while running does nothing
    check("button shows working state and is disabled while running", busy)
    s.wait_for_selector("[data-purge-result]", timeout=60000)
    msg = s.locator("[data-purge-result]").inner_text()
    check("result shows the completed run's counts", "Removed 3 test reps" in msg and "3 applications" in msg, msg)
    s.wait_for_timeout(800)
    check("list refreshed: no test rows remain", s.locator("table >> text=Title Tester").count() == 0)
    check("button back to normal", btn.is_enabled() and btn.inner_text() == "Remove test records")
    s.screenshot(path="/home/claude/volt-qa/purge-result.png")
    btn.click(); s.wait_for_timeout(2500)
    msg2 = s.locator("[data-purge-result]").inner_text()
    check("repeat shows zero counts", msg2.startswith("Removed 0 test reps, 0 applications, 0 documents, 0 sessions and 0 applicant logins"), msg2)
    b.close()
print("RESULT", sum(R), "/", len(R))
