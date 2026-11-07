"""VOLT #535 (2026-11-06 evening) - ACA Healthcare Outreach correction.

usage: python3 -I aca_flow.py <frontend> <api> <shots_dir> <staff_email> <staff_pw> <legacy_draft_sql_cmd>
Checks the homepage card, the ACA Explore page, a new ACA application with
no license info, an earlier-form draft (license answers as history), the
review screen, the staff hiring file, and that Fiber is unchanged.
Synthetic people only (@example.test).
"""
import json, os, subprocess, sys, uuid, urllib.request
from playwright.sync_api import sync_playwright

FE, API, SHOTS, STAFF, STAFF_PW = sys.argv[1].rstrip("/"), sys.argv[2].rstrip("/"), sys.argv[3], sys.argv[4], sys.argv[5]
PSQL = sys.argv[6] if len(sys.argv) > 6 else ""
S = "volt-marketing"
R = []
os.makedirs(SHOTS, exist_ok=True)
SALES = ("insurance sales", "sales agent", "close polic", "closing polic", "commission", "licensed agent",
         "licensed enrollment", "obamacare", "free healthcare", "everyone qualifies")


def check(label, ok, detail=""):
    R.append((label, bool(ok)))
    print(("PASS  " if ok else "FAIL  ") + label + ("" if ok else "  -- %s" % str(detail)[:300]), flush=True)


def call(method, path, body=None, tok=None):
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + tok} if tok else {})})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def no_sales(txt):
    low = txt.lower()
    return [w for w in SALES if w in low]


with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    for name, ctx_args in (("desktop", {"viewport": {"width": 1366, "height": 900}}), ("phone", dict(p.devices["iPhone 13"]))):
        pg = b.new_context(**ctx_args).new_page()
        pg.goto(FE + "/volt"); pg.wait_for_selector("[data-explore=aca]")
        card = pg.locator("[data-explore=aca]").locator("xpath=ancestor::*[contains(@class,'v-pcard') or self::article][1]")
        ctext = card.inner_text() if card.count() else pg.inner_text("body")
        home = pg.inner_text("body")
        check(f"{name}: homepage card is ACA Healthcare Outreach", "ACA Healthcare Outreach" in home)
        check(f"{name}: homepage card says no license required", "No license required" in ctext or "no license required" in ctext.lower(), ctext[:200])
        check(f"{name}: homepage has no insurance-sales wording", not no_sales(home), no_sales(home))
        check(f"{name}: Fiber card still travel door-to-door sales", "T-Mobile D2D Fiber Sales" in home)
        pg.locator("[data-explore=aca]").scroll_into_view_if_needed()
        pg.screenshot(path=f"{SHOTS}/{name}-01-home-aca-card.png", full_page=(name == "phone"))
        pg.locator("[data-explore=aca]").click(); pg.wait_for_selector("[data-opportunity=aca]")
        ex = pg.inner_text("[data-opportunity=aca]")
        check(f"{name}: Explore title ACA Healthcare Outreach", "ACA Healthcare Outreach" in ex)
        check(f"{name}: Explore has the approved description",
              "Help connect eligible people with ACA health coverage and guide them toward enrollment assistance." in ex)
        check(f"{name}: Explore has the $0-premium line with 'where available'", "$0-premium plans where available" in ex)
        check(f"{name}: Explore says eligibility, availability and costs vary", "Eligibility, plan availability and costs vary" in ex)
        check(f"{name}: Explore says no insurance license required", "No insurance license required" in ex)
        check(f"{name}: Explore has no sales/licensed-adviser wording", not no_sales(ex), no_sales(ex))
        pg.screenshot(path=f"{SHOTS}/{name}-02-aca-explore.png", full_page=True)
        pg.goto(FE + "/volt/opportunities/fiber") if False else None
    # Fiber explore unchanged (wording that defines it)
    fp = b.new_context(viewport={"width": 1366, "height": 900}).new_page()
    fp.goto(FE + "/volt"); fp.wait_for_selector("[data-explore=fiber]"); fp.locator("[data-explore=fiber]").click()
    fp.wait_for_selector("[data-opportunity=fiber]")
    ft = fp.inner_text("[data-opportunity=fiber]")
    check("Fiber Explore still door-to-door sales and travel", "door" in ft.lower() and "travel" in ft.lower() and "sales" in ft.lower(), ft[:200])
    fp.screenshot(path=f"{SHOTS}/desktop-03-fiber-explore-unchanged.png")

    # ── New ACA application, no license info ──
    email = "aca.outreach.%s@example.test" % uuid.uuid4().hex[:6]
    PW = "Volt-Outreach-2026!"
    n = b.new_context(**p.devices["iPhone 13"]).new_page()
    n.goto(FE + "/volt/apply/aca"); n.wait_for_selector("#v-first_name")
    for k, v in (("first_name", "Jordan"), ("last_name", "Outreach"), ("email", email), ("phone", "5550108181"),
                 ("date_of_birth", "1995-03-02"), ("password", PW)):
        n.fill("#v-" + k, v)
    n.locator("button[type=submit]").click(); n.wait_for_url("**/volt/me/applications/*")
    app_id = n.url.rsplit("/", 1)[1]
    n.wait_for_selector("[data-step=about]")
    n.fill("#v-city", "Dallas"); n.fill("#v-state", "TX"); n.fill("#v-zip", "75201"); n.fill("#v-start", "2026-12-01")
    n.locator("label.v-check", has_text="Part-time").locator("input").check()
    n.locator("[data-next]").click(); n.wait_for_selector("[data-step=experience]")
    n.locator("[data-no-experience]").check()
    n.locator("[data-next]").click(); n.wait_for_selector("[data-step=project]")
    proj = n.inner_text("[data-step=project]")
    check("application asks the outreach experience question",
          "Do you have experience with community outreach, customer service or helping people access resources?" in proj)
    check("application asks 'Tell us about that experience' as optional", "Tell us about that experience" in proj and "Optional" in proj)
    check("application asks comfortable explaining next steps", "Are you comfortable speaking with people and explaining next steps?" in proj)
    check("application asks languages", "What languages do you speak?" in proj)
    check("application has no license questions", "licens" not in proj.lower(), proj[:300])
    n.screenshot(path=f"{SHOTS}/phone-04-aca-questions.png", full_page=True)
    n.locator("[data-next]").click(); n.wait_for_selector("[data-step=review]")
    miss = n.inner_text("[data-missing]") if n.locator("[data-missing]").count() else ""
    check("review lists the unanswered outreach questions, submit disabled",
          "What languages do you speak?" in miss and "community outreach" in miss and n.locator("[data-submit]").is_disabled(), miss)
    check("missing list has no license or insurance item", "licens" not in miss.lower() and "insurance" not in miss.lower())
    n.screenshot(path=f"{SHOTS}/phone-04b-aca-missing.png", full_page=True)
    n.locator("[data-missing] li", has_text="languages").locator("button").click(); n.wait_for_selector("[data-step=project]")
    n.locator("[data-q=outreach_experience] label", has_text="Not yet").click()
    n.locator("[data-q=comfortable_explaining] label", has_text="I'd like to learn").click()
    n.fill("#v-q-languages", "English, Spanish")
    n.locator("[data-next]").click(); n.wait_for_selector("[data-step=review]")
    rv = n.inner_text("[data-step=review]")
    check("review shows the outreach answers", "Not yet" in rv and "English, Spanish" in rv, rv[:400])
    check("review shows no license item", "licens" not in rv.lower())
    n.screenshot(path=f"{SHOTS}/phone-05-aca-review.png", full_page=True)
    n.locator("[data-submit]").click(); n.wait_for_selector(".v-step.now >> text=Submitted", timeout=15000)
    check("ACA application submitted with no licensing info", True)

    # ── Earlier-form draft: license answers kept as history ──
    email2 = "aca.legacy.%s@example.test" % uuid.uuid4().hex[:6]
    st, out = call("POST", "/volt/public/%s/register" % S, {"first_name": "Lee", "last_name": "Legacy", "email": email2,
                   "phone": "(555) 010-9191", "date_of_birth": "1990-01-01", "password": PW, "project_key": "aca"})
    legacy_id = out.get("application_id")
    if PSQL and legacy_id:
        sql = ("UPDATE volt_applications SET answers='%s' WHERE id='%s'" %
               (json.dumps({"relevant_experience": "Front desk", "license_status": "licensed", "licensed_states": "TX",
                            "license_number": "TEST-123"}), legacy_id))
        subprocess.run(PSQL.split() + ["-c", sql], check=True, capture_output=True)
    lt = out["access_token"]
    a = call("GET", "/volt/rep/%s/applications/%s" % (S, legacy_id), tok=lt)[1]
    check("earlier draft: applicant does not see license answers", not {"license_status", "licensed_states", "license_number"} & set(a["answers"]), a["answers"])
    check("earlier draft: missing list has no license item", not any("licens" in m.lower() for m in a.get("missing", [])), a.get("missing"))
    check("earlier draft: previous experience answer kept", a["answers"].get("relevant_experience") == "Front desk")
    d = b.new_context(**p.devices["iPhone 13"]).new_page()
    d.goto(FE + "/login"); d.fill("input[type=email]", email2); d.fill("input[type=password]", PW)
    d.locator("button[type=submit]").click(); d.wait_for_timeout(2500)
    d.goto(FE + "/volt/me/applications/" + legacy_id); d.wait_for_timeout(2500)
    body = d.inner_text("body")
    check("earlier draft page opens with no license wording", "licens" not in body.lower(), body[:300])
    d.screenshot(path=f"{SHOTS}/phone-06-earlier-draft.png", full_page=True)

    # ── Staff hiring file ──
    import urllib.parse
    try:
        with urllib.request.urlopen(urllib.request.Request(API + "/auth/login", method="POST",
                data=urllib.parse.urlencode({"username": STAFF, "password": STAFF_PW}).encode(),
                headers={"Content-Type": "application/x-www-form-urlencoded"})) as r:
            lg = json.loads(r.read())
    except urllib.error.HTTPError as e:
        lg = json.loads(e.read() or b"null")
    stok = (lg or {}).get("access_token")
    org = os.environ.get("VOLT_ORG")
    if stok and org:
        f = call("GET", "/volt/ops/%s/applications/%s" % (org, app_id), tok=stok)[1]
        ans = f["application"]["answers"]
        check("staff file has the new outreach answers", ans.get("outreach_experience") == "no" and ans.get("languages") == "English, Spanish", ans)
        lf = call("GET", "/volt/ops/%s/applications/%s" % (org, legacy_id), tok=stok)[1]
        legacy_q = [q for q in lf["application"]["questions"] if q.get("staff_only")]
        check("staff file keeps earlier license answers as history", lf["application"]["answers"].get("license_number") == "TEST-123"
              and all("no longer asked" in q["label"] for q in legacy_q) and len(legacy_q) == 3)
        s = b.new_context(viewport={"width": 1440, "height": 900}).new_page()
        s.goto(FE + "/login"); s.fill("input[type=email]", STAFF); s.fill("input[type=password]", STAFF_PW)
        s.locator("button[type=submit]").click(); s.wait_for_timeout(3000)
        s.goto(FE + "/volt-ops/%s" % org); s.wait_for_selector("[data-kpi=awaiting_review]", timeout=30000)
        s.locator("label", has_text="Include test records").locator("input").check(); s.wait_for_timeout(800)
        s.locator("[data-kpi=awaiting_review]").click(); s.wait_for_selector("[data-table=queue]"); s.wait_for_timeout(800)
        row = s.locator("[data-table=queue] tr", has_text="Jordan Outreach").first
        check("staff queue shows outreach/languages, not a license", row.count() >= 1 and "licens" not in row.inner_text().lower()
              and ("New to outreach" in row.inner_text() or "English" in row.inner_text()), row.inner_text() if row.count() else "")
        s.screenshot(path=f"{SHOTS}/desktop-07-staff-queue.png", full_page=True)
        row.locator("text=Open").click(); s.wait_for_selector(".v-drawer >> text=Recruiter attribution")
        dtxt = s.locator(".v-drawer").inner_text()
        check("staff file shows the outreach questions and answers", "What languages do you speak?" in dtxt and "English, Spanish" in dtxt, dtxt[:300])
        check("staff file for a new application shows no license history", "licens" not in dtxt.lower())
        s.screenshot(path=f"{SHOTS}/desktop-08-staff-file.png", full_page=True)
    else:
        check("staff login", False, lg)

    print(json.dumps({"passed": sum(ok for _, ok in R), "failed": sum(not ok for _, ok in R),
                      "new_app": app_id, "legacy_app": legacy_id}))
