"""VOLT #535 (2026-11-06) - Apply Now, Talent Pool and the step-by-step hiring file.

usage: python3 -I hiring_flow.py <frontend> <api> <shots_dir> [owner_email owner_pw]
Synthetic people only (@example.test). Prints PASS/FAIL and a JSON summary.
"""
import csv, io, json, os, sys, time, urllib.request, uuid
from playwright.sync_api import sync_playwright

FE, API, SHOTS = sys.argv[1].rstrip("/"), sys.argv[2].rstrip("/"), sys.argv[3]
OWNER = sys.argv[4:6] if len(sys.argv) > 5 else None
S = "volt-marketing"
R = []
os.makedirs(SHOTS, exist_ok=True)
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def check(label, ok, detail=""):
    R.append((label, bool(ok)))
    print(("PASS  " if ok else "FAIL  ") + label + ("" if ok else "  -- %s" % str(detail)[:300]), flush=True)


def call(m, p, body=None, tok=None, form=None):
    data, h = None, {}
    if form is not None:
        data = form.encode(); h["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
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
    "first_name": "Dangelo", "last_name": "Simmons", "email": "dangelo.fixture-%s@example.test" % tag,
    "phone": "(555) 010-4242", "date_of_birth": "1988-05-17", "password": "Volt-Fixture-2026!", "project_key": "aca"})
assert c == 200, d
DANG = d["rep"]
EMAIL1, EMAIL2, POOL = "work.history-%s@example.test" % tag, "first.job-%s@example.test" % tag, "pool-%s@example.test" % tag
PW = "Volt-Applicant-2026!"
out = {"fixture": DANG}


def tok_of(page):
    return page.evaluate("localStorage.getItem('af_volt_token')")


with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    # ── 1. Apply Now: desktop ───────────────────────────────────────────────
    dk = b.new_context(viewport={"width": 1366, "height": 860})
    pg = dk.new_page()
    pg.goto(FE + "/volt"); pg.wait_for_selector("[data-apply=hero]")
    txt = pg.inner_text("main")
    check("hero has a red Apply Now", pg.locator("[data-apply=hero]").is_visible())
    check("new names shown, no 'Obamacare'", "ACA Healthcare Outreach" in txt and "T-Mobile D2D Fiber Sales" in txt and "Obamacare" not in pg.content())
    check("homepage copy updated", "traveling door-to-door fiber sales" in txt and "fiber internet outreach" not in txt.lower())
    check("Midterms still shown as closed", "Midterms Flyer Distribution" in txt and "recruiting closed" in txt.lower())
    check("Sign In visible", pg.locator("header >> text=Sign In").is_visible())
    pg.screenshot(path=SHOTS + "/01-desktop-home.png")
    pg.mouse.wheel(0, 1400); pg.wait_for_timeout(400)
    check("desktop: sticky header Apply Now visible after scrolling", pg.locator("[data-apply=header]").is_visible()
          and pg.locator("[data-apply=header]").bounding_box()["y"] < 80)
    check("desktop: no bottom bar", not pg.locator("[data-apply=bar]").is_visible())
    pg.screenshot(path=SHOTS + "/02-desktop-scrolled-sticky-apply.png")
    pg.locator("[data-apply=header]").click()
    dlg = pg.locator("[role=dialog]")
    dlg.wait_for()
    check("dialog is labelled and modal", dlg.get_attribute("aria-modal") == "true" and dlg.get_attribute("aria-labelledby") == "v-apply-title")
    picks = dlg.locator("[data-pick]")
    check("dialog shows exactly the two open projects", picks.count() == 2 and [picks.nth(i).get_attribute("data-pick") for i in range(2)] == ["aca", "fiber"])
    check("focus starts inside the dialog", pg.evaluate("document.activeElement.getAttribute('data-pick')") == "aca")
    for _ in range(4):
        pg.keyboard.press("Tab")
    check("Tab stays inside the dialog", pg.evaluate("!!document.activeElement.closest('[role=dialog]')"))
    pg.keyboard.press("Escape"); pg.wait_for_timeout(200)
    check("Escape closes it and focus returns to Apply Now", dlg.count() == 0 and pg.evaluate("document.activeElement.getAttribute('data-apply')") == "header")
    pg.locator("[data-apply=hero]").click(); dlg.wait_for()
    pg.mouse.click(10, 300); pg.wait_for_timeout(200)
    check("clicking outside dismisses it", pg.locator("[role=dialog]").count() == 0)
    pg.locator("[data-apply=hero]").click(); pg.wait_for_selector("[role=dialog]")
    pg.screenshot(path=SHOTS + "/03-desktop-apply-dialog.png")
    pg.keyboard.press("Enter")                      # keyboard selection of the focused project
    pg.wait_for_url("**/volt/apply/aca")
    check("keyboard selection opens that project's application", pg.url.endswith("/volt/apply/aca"))
    check("no Apply bar or header Apply inside the application", pg.locator("[data-apply]").count() == 0)
    pg.goto(FE + "/volt/projects/fiber"); pg.wait_for_selector("[data-apply=project]")
    pg.locator("[data-apply=header]").click(); pg.wait_for_url("**/volt/apply/fiber")
    check("on a project page Apply Now opens that application directly", pg.url.endswith("/volt/apply/fiber"))

    # ── 2. Phone: bottom bar, safe area, dialog; invite attribution through dialog ──
    ph = b.new_context(**p.devices["iPhone 13"])
    m = ph.new_page()
    m.goto(FE + "/volt?ref=%s" % DANG["rep_code"]); m.wait_for_selector("[data-apply=bar]")
    check("phone: bottom Apply Now visible", m.locator("[data-apply=bar]").is_visible())
    check("phone: header Apply hidden (bar instead)", not m.locator("[data-apply=header]").is_visible())
    pad = m.evaluate("getComputedStyle(document.querySelector('.volt')).paddingBottom")
    check("phone: page padded for the bar", int(float(pad.replace('px', ''))) >= 70, pad)
    m.evaluate("window.scrollTo(0, document.body.scrollHeight)"); m.wait_for_timeout(300)
    bar_top = m.locator(".v-applybar").bounding_box()["y"]
    last = m.locator("footer").bounding_box()
    check("phone: bar does not cover the footer when scrolled to the end", last["y"] + last["height"] <= bar_top + 1, (last, bar_top))
    m.screenshot(path=SHOTS + "/04-mobile-bottom-apply-bar.png")
    m.locator("[data-apply=bar]").click(); m.wait_for_selector("[role=dialog]")
    m.screenshot(path=SHOTS + "/05-mobile-apply-dialog.png")
    m.locator("[data-pick=fiber]").click(); m.wait_for_url("**/volt/apply/fiber")
    m.wait_for_selector("text=Did they refer you?")
    check("invite attribution carried through Apply Now → dialog → application", DANG["rep_code"] in m.content())
    m.locator("text=Yes, Dangelo Simmons referred me").click()
    for k, v in (("first_name", "Jordan"), ("last_name", "Rivera"), ("email", EMAIL1), ("phone", "(555) 010-3131"),
                 ("date_of_birth", "1995-03-09"), ("password", PW)):
        m.fill("#v-" + k, v)
    m.locator("button[type=submit]").click(); m.wait_for_url("**/volt/me/applications/*")
    app1 = m.url.rsplit("/", 1)[1]
    check("no floating Apply action inside the application", m.locator("[data-apply]").count() == 0)
    # Step 1
    m.wait_for_selector("[data-step=about]")
    m.fill("#v-city", "Dallas"); m.fill("#v-state", "TX"); m.fill("#v-zip", "75201"); m.fill("#v-start", "2026-12-01")
    m.fill("#v-regions", "North Texas, Oklahoma")
    m.locator("label.v-check", has_text="Full-time").locator("input").check()
    m.locator("label.v-check", has_text="Weekends").locator("input").check()
    m.fill("#v-avnotes", "Not Sundays")
    m.screenshot(path=SHOTS + "/06-mobile-step1-about.png", full_page=True)
    m.locator("[data-next]").click(); m.wait_for_selector("[data-step=experience]")
    # Step 2: two jobs + resume
    m.locator("text=+ Add a job").click()
    m.fill("#v-emp-0", "Example Telecom"); m.fill("#v-title-0", "Field sales rep"); m.fill("#v-rs-0", "2023-02")
    m.locator("[data-role='0'] label.v-check input").check(); m.fill("#v-sum-0", "Top 10% of the region.")
    m.locator("text=+ Add a job").click()
    m.fill("#v-emp-1", "Example Retail"); m.fill("#v-title-1", "Cashier"); m.fill("#v-rs-1", "2021-06"); m.fill("#v-re-1", "2023-01")
    m.fill("#v-relexp", "Two summers of door-to-door.")
    m.set_input_files("[data-doc=resume] input[type=file]", files=[{"name": "resume.pdf", "mimeType": "application/pdf", "buffer": PDF}])
    m.wait_for_selector("[data-doc=resume] >> text=Under review", timeout=15000)
    m.fill("#v-extra", "Bilingual English/Spanish.")
    m.wait_for_timeout(1300)
    m.screenshot(path=SHOTS + "/07-mobile-step2-experience.png", full_page=True)
    m.locator("[data-next]").click(); m.wait_for_selector("[data-step=project]")
    m.locator("[data-q=willing_travel] label", has_text="Yes").click()
    m.locator("[data-q=comfortable_d2d] label", has_text="Would like details").click()
    m.locator("[data-q=transportation] label", has_text="It depends").click()
    m.fill("[data-q=transportation_notes]", "Shared car on weekdays.")
    m.fill("[data-q=travel_regions]", "Anywhere in the South")
    m.wait_for_timeout(1300)
    m.screenshot(path=SHOTS + "/08-mobile-step3-project.png", full_page=True)
    # Sign out mid-application and resume on another device.
    m.goto(FE + "/volt/me"); m.locator("header >> text=Sign out").click(); m.wait_for_url("**/volt")
    ph2 = b.new_context(**p.devices["Pixel 7"]); m2 = ph2.new_page()
    m2.goto(FE + "/volt/sign-in"); m2.fill("#v-si-email", EMAIL1); m2.fill("#v-si-pass", PW)
    m2.locator("button[type=submit]").click(); m2.wait_for_url("**/volt/me")
    m2.locator("[data-application] a", has_text="Resume").first.click(); m2.wait_for_selector("[data-step]")
    m2.locator(".v-steplink", has_text="About you").click(); m2.wait_for_selector("[data-step=about]")
    keep = [m2.input_value("#v-city"), m2.input_value("#v-state"), m2.input_value("#v-zip"), m2.input_value("#v-start"), m2.input_value("#v-avnotes")]
    av = m2.locator("label.v-check", has_text="Full-time").locator("input").is_checked() and m2.locator("label.v-check", has_text="Weekends").locator("input").is_checked()
    m2.locator(".v-steplink", has_text="Experience").click(); m2.wait_for_selector("[data-step=experience]")
    jobs = [m2.input_value("#v-emp-0"), m2.input_value("#v-rs-0"), m2.input_value("#v-emp-1"), m2.input_value("#v-re-1")]
    cur = m2.locator("[data-role='0'] label.v-check input").is_checked()
    res_ok = "resume.pdf" in m2.inner_text("[data-doc=resume]")
    m2.locator(".v-steplink", has_text="Project questions").click(); m2.wait_for_selector("[data-step=project]")
    radios = [m2.locator("[data-q=willing_travel] input[value=yes]").is_checked(), m2.locator("[data-q=transportation] input[value=explain]").is_checked()]
    notes = m2.input_value("[data-q=transportation_notes]")
    check("after sign-out/sign-in every field persists",
          keep == ["Dallas", "TX", "75201", "2026-12-01", "Not Sundays"] and av and jobs == ["Example Telecom", "2023-02", "Example Retail", "2023-01"]
          and cur and res_ok and all(radios) and notes == "Shared car on weekdays.", (keep, av, jobs, cur, res_ok, radios, notes))
    m2.locator("[data-next]").click(); m2.wait_for_selector("[data-step=review]")
    rv = m2.inner_text("[data-step=review]")
    check("review shows the whole file", all(x in rv for x in ("Dallas, TX, 75201", "Field sales rep", "Present", "resume.pdf", "Would like details", "Dangelo Simmons")), rv[:400])
    m2.screenshot(path=SHOTS + "/09-mobile-step4-review.png", full_page=True)
    m2.locator("[data-submit]").click(); m2.wait_for_selector(".v-step.now >> text=Submitted", timeout=15000)
    check("application with work history and resume submitted", True)

    # ── 3. No experience, no resume, search referral with edit invalidation ──
    ph3 = b.new_context(**p.devices["iPhone 13"]); n = ph3.new_page()
    n.goto(FE + "/volt/apply/aca"); n.wait_for_selector("#v-first_name")
    for k, v in (("first_name", "Casey"), ("last_name", "Newhire"), ("email", EMAIL2), ("phone", "5550107070"),
                 ("date_of_birth", "2004-08-15"), ("password", PW)):
        n.fill("#v-" + k, v)
    n.fill("#v-ref", "Dangelo"); n.wait_for_selector(".v-match")
    n.locator(".v-match", has_text=DANG["rep_code"]).click()
    n.locator("#v-ref").press("End"); n.locator("#v-ref").type("x"); n.wait_for_timeout(300)
    check("editing the name clears the selection", n.locator(".v-selected").count() == 0)
    n.locator("button[type=submit]").click(); n.wait_for_timeout(300)
    check("typed text without a selection is blocked", "Select a matching recruiter" in n.content() and "/volt/apply/aca" in n.url)
    n.fill("#v-ref", "Dangelo"); n.wait_for_selector(".v-match"); n.locator(".v-match", has_text=DANG["rep_code"]).click()
    n.locator("button[type=submit]").click(); n.wait_for_url("**/volt/me/applications/*")
    app2 = n.url.rsplit("/", 1)[1]
    n.wait_for_selector("[data-step=about]")
    n.fill("#v-city", "Tulsa"); n.fill("#v-state", "OK"); n.fill("#v-zip", "74103"); n.fill("#v-start", "2026-11-20")
    n.locator("label.v-check", has_text="Part-time").locator("input").check()
    n.locator("[data-next]").click(); n.wait_for_selector("[data-step=experience]")
    n.locator("[data-no-experience]").check()
    n.locator("[data-next]").click(); n.wait_for_selector("[data-step=project]")
    n.locator("[data-q=outreach_experience] label", has_text="Not yet").click()
    n.locator("[data-q=comfortable_explaining] label", has_text="I'd like to learn").click()
    n.fill("#v-q-languages", "English")
    n.locator("[data-next]").click(); n.wait_for_selector("[data-step=review]")
    check("review: no experience and resume not provided", "No previous work experience" in n.inner_text("[data-step=review]") and "Not provided" in n.inner_text("[data-step=review]"))
    n.locator("[data-submit]").click(); n.wait_for_selector(".v-step.now >> text=Submitted", timeout=15000)
    check("no-experience application without a resume submitted", True)
    t2 = tok_of(n)
    me2 = call("GET", "/volt/rep/%s/me" % S, tok=t2)[1]
    check("search-selected referral saved as the recruiter's id", me2["applications"][0]["referrer"]["rep_id"] == DANG["rep_id"])

    # ── 4. Talent Pool from the homepage, then a repeat ─────────────────────
    t = b.new_context(**p.devices["iPhone 13"]).new_page()
    t.goto(FE + "/volt?ref=%s" % DANG["rep_code"]); t.wait_for_selector("[data-talent=hero]")
    t.locator("[data-talent=hero]").click(); t.wait_for_selector("[data-form=talent]")
    check("talent popup: consent box unchecked by default", not t.locator("[data-consent]").is_checked())
    check("talent popup says it saves interest only", "saves your interest only" in t.inner_text("[role=dialog]"))
    t.fill("#v-tp-first_name", "Pat"); t.fill("#v-tp-last_name", "Pool"); t.fill("#v-tp-email", POOL)
    t.locator("[data-form=talent] label.v-check", has_text="T-Mobile").locator("input").check()
    t.locator("text=Yes, Dangelo Simmons referred me").click()
    t.locator("[data-consent]").check()
    t.screenshot(path=SHOTS + "/10-mobile-talent-popup.png", full_page=True)
    t.locator("[data-form=talent] button[type=submit]").click(); t.wait_for_selector("[data-talent-done]")
    check("talent signup confirmation with a path to opportunities", t.locator("text=See current opportunities").is_visible())
    t.screenshot(path=SHOTS + "/11-mobile-talent-done.png")
    t.locator("text=See current opportunities").click(); t.wait_for_timeout(500)
    check("success path returns to the opportunities", "/volt" in t.url and t.locator("[role=dialog]").count() == 0)
    t.locator("[data-talent=hero]").click(); t.wait_for_selector("[data-form=talent]")
    t.fill("#v-tp-first_name", "Pat"); t.fill("#v-tp-last_name", "Pool"); t.fill("#v-tp-email", POOL.upper())
    t.locator("[data-form=talent] label.v-check", has_text="ACA").locator("input").check()
    t.locator("[data-form=talent] button[type=submit]").click(); t.wait_for_selector("[data-talent-done]")
    t.keyboard.press("Escape")
    check("talent repeat accepted (same confirmation)", True)

    # ── 5. Staff ────────────────────────────────────────────────────────────
    if OWNER:
        s = b.new_context(viewport={"width": 1440, "height": 900}, accept_downloads=True).new_page()
        s.goto(FE + "/login"); s.fill("input[type=email]", OWNER[0]); s.fill("input[type=password]", OWNER[1])
        s.locator("button[type=submit]").first.click(); s.wait_for_timeout(3500)
        stok = s.evaluate("localStorage.getItem('af_token')")
        org = [v for v in call("GET", "/auth/my-contexts", tok=stok)[1]["volt_contexts"]][0]["organization_id"]
        base = "/volt/ops/%s" % org
        pool = call("GET", base + "/talent-pool?include_test=1&q=" + POOL, tok=stok)[1]
        check("one deduplicated Talent Pool record", pool["count"] == 1, pool["count"])
        row = pool["items"][0]
        check("latest email choice kept (second signup left it unchecked → No)", row["notify_opt_in"] is False and row["submit_count"] == 2)
        check("interests merged and invite referral kept", row["interests"] == ["fiber", "aca"] and row["referrer"]["rep_id"] == DANG["rep_id"], row)
        apps = call("GET", base + "/applications?include_test=1", tok=stok)[1]
        check("Talent Pool signup created no application", not any(POOL.split("@")[0] in json.dumps(x) for x in apps["items"]))
        s.goto(FE + "/volt-ops/%s/review?include_test=1&travel=yes" % org); s.wait_for_selector("[data-table=queue]"); s.wait_for_timeout(1200)
        rows_txt = s.inner_text("[data-table=queue]")
        check("travel filter finds the Fiber applicant", "Jordan Rivera" in rows_txt and "Casey Newhire" not in rows_txt)
        s.goto(FE + "/volt-ops/%s/review?include_test=1&experience=none" % org); s.wait_for_timeout(1500)
        check("experience filter finds the first-time applicant", "Casey Newhire" in s.inner_text("[data-table=queue]") and "Jordan Rivera" not in s.inner_text("[data-table=queue]"))
        s.goto(FE + "/volt-ops/%s/review?include_test=1&location=dallas" % org); s.wait_for_timeout(1500)
        check("location filter", "Jordan Rivera" in s.inner_text("[data-table=queue]") and "Casey Newhire" not in s.inner_text("[data-table=queue]"))
        s.locator("[data-table=queue] tr", has_text="Jordan Rivera").filter(has_text="T-Mobile").first.locator("text=Open").click()
        s.wait_for_selector("[data-hiring]")
        hf = s.inner_text(".v-drawer")
        check("staff file shows location, availability, work history, answers, resume",
              all(x in hf for x in ("Dallas, TX, 75201", "Full-time, Weekends", "Field sales rep", "Example Retail",
                                    "Would like details", "Shared car on weekdays.", "resume.pdf", "Dangelo Simmons")), hf[:500])
        s.screenshot(path=SHOTS + "/12-desktop-staff-hiring-file.png")
        s.locator(".v-drawer >> text=Close").click()
        with s.expect_download() as dl:
            s.locator("text=Export CSV").click()
        body = open(dl.value.path(), encoding="utf-8").read()
        rows = list(csv.reader(io.StringIO(body)))
        check("CSV has the new hiring columns, no DOB / email / document links",
              "Willing to travel" in rows[0] and "Dallas" in body and "1995-03-09" not in body and "@example.test" not in body and "/documents/" not in body)
        s.goto(FE + "/volt-ops/%s/talent" % org); s.wait_for_selector("[data-table=talent]")
        s.locator("label", has_text="Include test records").locator("input").check(); s.wait_for_timeout(1200)
        check("Talent Pool tab lists the signup", "Pat Pool" in s.inner_text("[data-table=talent]"))
        s.screenshot(path=SHOTS + "/13-desktop-talent-pool.png")
        out["org"] = org
    b.close()

bad = [l for l, ok in R if not ok]
print(json.dumps({"passed": len(R) - len(bad), "failed": bad, "emails": [EMAIL1, EMAIL2, POOL], **out}))
