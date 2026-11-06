"""VOLT #535 (2026-11-06 afternoon) - Fiber-only application flow and the
"Why Sell Fiber With VOLT?" page.

usage: python3 -I fiber_flow.py <frontend> <api> <shots_dir> [owner_email owner_pw]
Synthetic people only (@example.test). Prints PASS/FAIL and a JSON summary.
"""
import csv, io, json, os, sys, urllib.request, uuid
from playwright.sync_api import sync_playwright

FE, API, SHOTS = sys.argv[1].rstrip("/"), sys.argv[2].rstrip("/"), sys.argv[3]
OWNER = sys.argv[4:6] if len(sys.argv) > 5 else None
S = "volt-marketing"
R = []
os.makedirs(SHOTS, exist_ok=True)
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
UNCONFIRMED = ("per hour", "per week", "commission", "$", "housing provided", "paid travel", "reimburs", "W-2", "1099",
               "employee of t-mobile", "hired by t-mobile", "guaranteed", "start date:")


def check(label, ok, detail=""):
    R.append((label, bool(ok)))
    print(("PASS  " if ok else "FAIL  ") + label + ("" if ok else "  -- %s" % str(detail)[:300]), flush=True)


def call(m, p, body=None, tok=None):
    data, h = None, {}
    if body is not None:
        data = json.dumps(body).encode(); h["Content-Type"] = "application/json"
    if tok:
        h["Authorization"] = "Bearer " + tok
    req = urllib.request.Request(API + p, data=data, method=m, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            raw = r.read()
            try:
                return r.status, json.loads(raw or b"null")
            except ValueError:
                return r.status, raw.decode()
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw or b"null")
        except ValueError:
            return e.code, raw.decode()


tag = uuid.uuid4().hex[:6]
c, d = call("POST", "/volt/public/%s/register" % S, {
    "first_name": "Dangelo", "last_name": "Simmons", "email": "dangelo.fiber-%s@example.test" % tag,
    "phone": "(555) 010-4243", "date_of_birth": "1988-05-17", "password": "Volt-Fixture-2026!", "project_key": "aca"})
assert c == 200, d
DANG = d["rep"]
SELLER, ROOKIE = "seller-%s@example.test" % tag, "rookie-%s@example.test" % tag
PW = "Volt-Applicant-2026!"
out = {"fixture": DANG}


def radio(pg, q, text):
    pg.locator("[data-q=%s] label" % q, has_text=text).first.click()


with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    # ── 1. Opportunity page, desktop ────────────────────────────────────────
    dk = b.new_context(viewport={"width": 1366, "height": 900})
    pg = dk.new_page()
    pg.goto(FE + "/volt"); pg.wait_for_selector("[data-explore=fiber]")
    check("Fiber card links Explore", "Explore" in pg.inner_text("[data-explore=fiber]"))
    check("ACA card links to its Explore page", "Explore" in pg.inner_text("[data-explore=aca]"))
    pg.locator("[data-explore=fiber]").click(); pg.wait_for_selector("[data-opportunity=fiber]")
    txt = pg.inner_text("main")
    check("demo headline", "Great markets." in txt and "Serious training." in txt)
    check("three reasons", pg.locator("[data-pillar]").count() == 3 and all(x in txt.upper() for x in ("REASON 1 · GREAT MARKETS", "REASON 2 · INDUSTRY-LEADING PEOPLE AND LEADERSHIP", "REASON 3 · TRAINING AND DEVELOPMENT")))
    check("travel is stated plainly", "This opportunity requires travel." in txt and "Travel. Prepare. Knock. Improve." in txt)
    low = txt.lower()
    check("no unconfirmed job terms on the page", not [w for w in UNCONFIRMED if w.lower() in low], [w for w in UNCONFIRMED if w.lower() in low])
    check("Apply Now prominent in the page hero and at the end", pg.locator("[data-opportunity=fiber] [data-apply=project]").count() == 1 and pg.locator("[data-opportunity=fiber] [data-apply=close]").count() == 1)
    check("sticky header Apply Now on the page", pg.locator("[data-apply=header]").is_visible())
    pg.screenshot(path=SHOTS + "/01-desktop-fiber-opportunity.png", full_page=True)
    pg.locator("[data-apply=header]").click(); pg.wait_for_url("**/volt/apply/fiber")
    check("header Apply Now on the page opens the Fiber application directly", pg.url.endswith("/volt/apply/fiber"))

    # ── 2. Phone: page + bottom bar ─────────────────────────────────────────
    ph = b.new_context(**p.devices["iPhone 13"])
    m = ph.new_page()
    m.goto(FE + "/volt/projects/fiber?ref=%s" % DANG["rep_code"]); m.wait_for_selector("[data-opportunity=fiber]")
    check("phone: bottom Apply Now bar on the page", m.locator("[data-apply=bar]").is_visible())
    m.screenshot(path=SHOTS + "/02-mobile-fiber-opportunity-top.png")
    m.screenshot(path=SHOTS + "/03-mobile-fiber-opportunity-full.png", full_page=True)

    # ── 3. Experienced seller, invite referral, every Fiber step ────────────
    m.goto(FE + "/volt?ref=%s" % DANG["rep_code"]); m.wait_for_selector("[data-apply=bar]")
    m.locator("[data-apply=bar]").click(); m.wait_for_selector("[role=dialog]")
    m.locator("[data-pick=fiber]").click(); m.wait_for_url("**/volt/apply/fiber")
    m.wait_for_selector("text=Did they refer you?")
    m.locator("text=Yes, Dangelo Simmons referred me").click()
    for k, v in (("first_name", "Riley"), ("last_name", "Seller"), ("email", SELLER), ("phone", "(555) 010-5151"),
                 ("date_of_birth", "1994-06-02"), ("password", PW)):
        m.fill("#v-" + k, v)
    m.locator("button[type=submit]").click(); m.wait_for_url("**/volt/me/applications/*")
    app1 = m.url.rsplit("/", 1)[1]
    m.wait_for_selector("[data-step=travel]")
    steps = [x.strip() for x in m.locator(".v-steplink").all_inner_texts()]
    check("Fiber steps (demo): Travel, Sales, Mindset, Review", steps == ["1. Travel", "2. Sales", "3. Mindset", "4. Review"], steps)
    check("Travel step heading from the demo", "Where are you starting from?" in m.inner_text("[data-step=travel]"))
    check("no availability, start date, regions or work history on Fiber",
          m.locator("[data-availability]").count() == 0 and m.locator("#v-start").count() == 0 and m.locator("#v-regions").count() == 0
          and "Full-time" not in m.content() and "Weekends" not in m.content())
    check("referral shown on the first step", "Dangelo Simmons" in m.inner_text("[data-referral]"))
    m.fill("#v-city", "Dallas"); m.fill("#v-state", "TX"); m.fill("#v-zip", "75201")
    m.fill("[data-q=earliest_departure]", "2026-12-01")
    radio(m, "time_away", "1–3 months"); radio(m, "drivers_license", "Yes"); radio(m, "transportation", "It depends")
    m.fill("[data-q=transportation_notes]", "Shared car for now."); radio(m, "smartphone", "Yes")
    m.fill("[data-q=travel_logistics]", "Need two days' notice for my dog sitter.")
    m.wait_for_timeout(1300)
    m.screenshot(path=SHOTS + "/05-mobile-fiber-step1-travel.png", full_page=True)
    m.locator("[data-next]").click(); m.wait_for_selector("[data-step=experience]")
    check("follow-ups hidden until the experience answer", m.locator("[data-q=products_sold]").count() == 0 and m.locator("[data-q=beginner_experience]").count() == 0)
    radio(m, "d2d_experience", "Yes, door-to-door")
    m.fill("[data-q=products_sold]", "Fiber internet and home security"); radio(m, "d2d_duration", "1–2 years")
    m.fill("[data-q=recent_company]", "Example Door Co"); m.fill("[data-q=results]", "38 sales in 6 weeks")
    m.wait_for_timeout(1300)
    check("resume is on the Sales step", m.locator("[data-step=experience] [data-doc=resume]").count() == 1)
    m.screenshot(path=SHOTS + "/06-mobile-fiber-step2-sales.png", full_page=True)
    m.locator("[data-next]").click(); m.wait_for_selector("[data-step=coachability]")
    m.fill("[data-q=motivation]", "I like a goal I can measure every day.")
    m.fill("[data-q=persistence]", "My first week I closed nothing; I kept my count and closed 9 the next week.")
    m.fill("[data-q=coaching_response]", "I write it down and try it on the next door.")
    radio(m, "homeowner_comfort", "Very comfortable")
    check("Mindset step has the travel-role commitment, unchecked", "traveling door-to-door sales role" in m.inner_text("[data-q=commitment]") and not m.locator("[data-q=commitment] input").is_checked())
    m.locator("[data-q=commitment] input").check()
    m.wait_for_timeout(1300)
    m.screenshot(path=SHOTS + "/07-mobile-fiber-step3-mindset.png", full_page=True)
    # Sign out mid-application; resume on another phone.
    m.goto(FE + "/volt/me"); m.locator("header >> text=Sign out").click(); m.wait_for_url("**/volt")
    m2 = b.new_context(**p.devices["Pixel 7"]).new_page()
    m2.goto(FE + "/volt/sign-in"); m2.fill("#v-si-email", SELLER); m2.fill("#v-si-pass", PW)
    m2.locator("button[type=submit]").click(); m2.wait_for_url("**/volt/me")
    m2.locator("[data-application] a", has_text="Resume").first.click(); m2.wait_for_selector("[data-step]")
    m2.locator(".v-steplink", has_text="Travel").click(); m2.wait_for_selector("[data-step=travel]")
    kept = [m2.input_value("[data-q=earliest_departure]"), m2.locator("[data-q=time_away] input[value='1_3_months']").is_checked(),
            m2.input_value("[data-q=transportation_notes]")]
    m2.locator(".v-steplink", has_text="Sales").click(); m2.wait_for_selector("[data-step=experience]")
    kept += [m2.input_value("[data-q=products_sold]"), m2.input_value("[data-q=results]")]
    m2.locator(".v-steplink", has_text="Mindset").click(); m2.wait_for_selector("[data-step=coachability]")
    kept += [m2.input_value("[data-q=coaching_response]"), m2.locator("[data-q=commitment] input").is_checked()]
    check("after sign-out/sign-in every Fiber answer persists",
          kept == ["2026-12-01", True, "Shared car for now.", "Fiber internet and home security", "38 sales in 6 weeks",
                   "I write it down and try it on the next door.", True], kept)
    m2.locator(".v-steplink", has_text="Sales").click(); m2.wait_for_selector("[data-step=experience]")
    m2.set_input_files("[data-doc=resume] input[type=file]", files=[{"name": "seller-resume.pdf", "mimeType": "application/pdf", "buffer": PDF}])
    m2.wait_for_selector("[data-doc=resume] >> text=Under review", timeout=15000)
    check("job answers survive the resume upload", m2.input_value("[data-q=products_sold]") == "Fiber internet and home security")
    m2.locator(".v-steplink", has_text="Review").click(); m2.wait_for_selector("[data-step=review]")
    rv = m2.inner_text("[data-step=review]")
    check("review shows the Fiber file by step", all(x in rv for x in ("Dallas, TX, 75201", "Travel", "1–3 months", "Sales",
                                                                       "Fiber internet and home security", "Mindset", "seller-resume.pdf", "Dangelo Simmons", "Yes, I understand")), rv[:500])
    check("review has no availability section", "Availability" not in rv and "Earliest start" not in rv)
    m2.screenshot(path=SHOTS + "/08-mobile-fiber-review.png", full_page=True)
    m2.locator("[data-submit]").click(); m2.wait_for_selector(".v-step.now >> text=Submitted", timeout=15000)
    check("experienced Fiber application with resume submitted", True)

    # ── 4. Beginner, no resume, desktop ─────────────────────────────────────
    n = b.new_context(viewport={"width": 1280, "height": 860}).new_page()
    n.goto(FE + "/volt/apply/fiber"); n.wait_for_selector("#v-first_name")
    for k, v in (("first_name", "Casey"), ("last_name", "Rookie"), ("email", ROOKIE), ("phone", "5550107171"),
                 ("date_of_birth", "2004-08-15"), ("password", PW)):
        n.fill("#v-" + k, v)
    n.locator("button[type=submit]").click(); n.wait_for_url("**/volt/me/applications/*")
    n.wait_for_selector("[data-step=travel]")
    n.fill("#v-city", "Tulsa"); n.fill("#v-state", "OK"); n.fill("#v-zip", "74103")
    n.locator(".v-steplink", has_text="Review").click(); n.wait_for_selector("[data-step=review]")
    miss = n.inner_text("[data-missing]")
    check("draft lists the Fiber items by step", "Travel:" in miss and "Sales:" in miss and "Mindset:" in miss
          and "availability" not in miss.lower(), miss[:400])
    n.locator("[data-missing] li", has_text="Travel:").first.locator("button").click(); n.wait_for_selector("[data-step=travel]")
    check("Fix link opens the Travel step", True)
    n.fill("[data-q=earliest_departure]", "2027-01-05"); radio(n, "time_away", "Not sure yet"); radio(n, "drivers_license", "Getting")
    radio(n, "transportation", "No"); radio(n, "smartphone", "Smartphone, but limited")
    n.locator("[data-next]").click(); n.wait_for_selector("[data-step=experience]")
    radio(n, "d2d_experience", "No, I'm new")
    check("beginner question appears; seller follow-ups do not", n.locator("[data-q=beginner_experience]").count() == 1 and n.locator("[data-q=products_sold]").count() == 0)
    n.fill("[data-q=beginner_experience]", "Captain of my soccer team; two years at a busy restaurant counter.")
    n.screenshot(path=SHOTS + "/09-desktop-fiber-beginner-experience.png", full_page=True)
    n.locator("[data-next]").click(); n.wait_for_selector("[data-step=coachability]")
    n.fill("[data-q=motivation]", "I want to learn to sell."); n.fill("[data-q=persistence]", "Finished a season after losing 8 straight.")
    n.fill("[data-q=coaching_response]", "Ask questions, then practice."); radio(n, "homeowner_comfort", "Still learning")
    n.locator("[data-q=commitment] input").check()
    n.locator("[data-next]").click(); n.wait_for_selector("[data-step=review]")
    rv = n.inner_text("[data-step=review]")
    check("beginner review: no resume, beginner answer shown", "Not provided" in rv and "soccer team" in rv)
    n.locator("[data-submit]").click(); n.wait_for_selector(".v-step.now >> text=Submitted", timeout=15000)
    check("beginner Fiber application without a resume submitted", True)

    # ── 5. ACA flow unchanged ───────────────────────────────────────────────
    a = b.new_context(viewport={"width": 1280, "height": 860}).new_page()
    a.goto(FE + "/volt/apply/aca"); a.wait_for_selector("#v-first_name")
    for k, v in (("first_name", "Avery"), ("last_name", "Aca"), ("email", "aca-%s@example.test" % tag), ("phone", "5550107272"),
                 ("date_of_birth", "1990-01-15"), ("password", PW)):
        a.fill("#v-" + k, v)
    a.locator("button[type=submit]").click(); a.wait_for_url("**/volt/me/applications/*"); a.wait_for_selector("[data-step=about]")
    steps = [x.strip() for x in a.locator(".v-steplink").all_inner_texts()]
    check("ACA keeps its 4 steps with availability", steps == ["1. About you", "2. Experience", "3. Project questions", "4. Review & submit"]
          and a.locator("[data-availability]").count() == 1, steps)

    # ── 6. Staff ────────────────────────────────────────────────────────────
    if OWNER:
        s = b.new_context(viewport={"width": 1440, "height": 900}, accept_downloads=True).new_page()
        s.goto(FE + "/login"); s.fill("input[type=email]", OWNER[0]); s.fill("input[type=password]", OWNER[1])
        s.locator("button[type=submit]").first.click(); s.wait_for_timeout(3500)
        stok = s.evaluate("localStorage.getItem('af_token')")
        org = call("GET", "/auth/my-contexts", tok=stok)[1]["volt_contexts"][0]["organization_id"]
        s.goto(FE + "/volt-ops/%s/review?include_test=1&travel=long" % org); s.wait_for_selector("[data-table=queue]"); s.wait_for_timeout(1500)
        q = s.inner_text("[data-table=queue]")
        check("travel filter (1 month or more) finds the seller only", "Riley Seller" in q and "Casey Rookie" not in q)
        check("queue shows time away and experience", "Away: 1–3 months · Door-to-door" in q, q[:300])
        s.goto(FE + "/volt-ops/%s/review?include_test=1&experience=none" % org); s.wait_for_timeout(1500)
        q = s.inner_text("[data-table=queue]")
        check("experience filter (new) finds the beginner", "Casey Rookie" in q and "Riley Seller" not in q)
        s.goto(FE + "/volt-ops/%s/review?include_test=1&experience=d2d" % org); s.wait_for_timeout(1500)
        q = s.inner_text("[data-table=queue]")
        check("experience filter (door-to-door) finds the seller", "Riley Seller" in q and "Casey Rookie" not in q)
        s.locator("[data-table=queue] tr", has_text="Riley Seller").first.locator("text=Open").click()
        s.wait_for_selector("[data-hiring]")
        hf = s.inner_text(".v-drawer")
        check("staff file: Fiber answers by step, resume, referral",
              all(x in hf for x in ("Travel", "1–3 months", "Need two days' notice", "Sales",
                                    "Fiber internet and home security", "38 sales in 6 weeks", "Mindset",
                                    "I write it down", "seller-resume.pdf", "Dangelo Simmons")), hf[:600])
        check("staff file: no empty availability or work-history rows for Fiber", "Availability" not in hf and "Work history" not in hf)
        s.screenshot(path=SHOTS + "/10-desktop-staff-fiber-file.png")
        s.locator(".v-drawer >> text=Close").first.click()
        s.goto(FE + "/volt-ops/%s/review?include_test=1" % org); s.wait_for_selector("[data-table=queue]"); s.wait_for_timeout(1200)
        with s.expect_download() as dl:
            s.locator("text=Export CSV").click()
        body = open(dl.value.path(), encoding="utf-8").read()
        rows = list(csv.reader(io.StringIO(body)))
        hdr = rows[0]
        seller = [r for r in rows if "Riley Seller" in r]
        check("CSV: new Fiber columns with labels; no DOB, email or document links",
              all(h in hdr for h in ("Earliest departure", "Time away", "Driver's license", "Smartphone and data", "Door-to-door experience"))
              and seller and "1–3 months" in seller[0] and "1994-06-02" not in body and "@example.test" not in body and "/documents/" not in body)
        s.screenshot(path=SHOTS + "/11-desktop-staff-queue.png")
        out["org"] = org
    b.close()

bad = [l for l, ok in R if not ok]
print(json.dumps({"passed": len(R) - len(bad), "failed": bad, "emails": [SELLER, ROOKIE], **out}))
