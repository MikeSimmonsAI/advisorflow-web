"""VOLT #535 - staff + applicant round trip through the real screens.

usage: python3 -I staff_flow.py <fe> <api> <shots> <staff_email> <staff_pw> <applicant_email> <applicant_pw>
                                <other_applicant_email> <other_pw> [<unentitled_email> <unentitled_pw>]
"""
import csv, io, json, sys, time, urllib.request
from playwright.sync_api import sync_playwright

FE, API, SHOTS = sys.argv[1].rstrip("/"), sys.argv[2].rstrip("/"), sys.argv[3]
STAFF, STAFF_PW, APL, APL_PW, OTHER, OTHER_PW = sys.argv[4:10]
UNENT = sys.argv[10:12] if len(sys.argv) > 11 else None
SLUG = "volt-marketing"
RESULTS = []
PDF2 = b"%PDF-1.4\n% replacement v2\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def check(label, ok, detail=""):
    RESULTS.append((label, bool(ok)))
    print(("PASS  " if ok else "FAIL  ") + label + ("" if ok else "  -- %s" % str(detail)[:300]), flush=True)


def call(m, p, body=None, tok=None):
    req = urllib.request.Request(API + p, data=json.dumps(body).encode() if body is not None else None, method=m)
    req.add_header("Content-Type", "application/json")
    if tok:
        req.add_header("Authorization", "Bearer " + tok)
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


def apl_token(email, pw):
    c, b = call("POST", "/volt/public/%s/login" % SLUG, {"email": email, "password": pw})
    assert c == 200, b
    return b["access_token"]


def staff_login(page, email, pw):
    page.goto(FE + "/login")
    page.fill("input[type=email]", email)
    page.fill("input[type=password]", pw)
    page.locator("button[type=submit]").first.click()
    page.wait_for_timeout(2500)


with sync_playwright() as p:
    browser = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    desk = browser.new_context(viewport={"width": 1440, "height": 900}, accept_downloads=True)
    s = desk.new_page()
    import os
    OWNER = os.environ.get("VOLT_OWNER_EMAIL"), os.environ.get("VOLT_OWNER_PW")
    if OWNER[0]:
        o = browser.new_context(viewport={"width": 1440, "height": 900}).new_page()
        staff_login(o, OWNER[0], OWNER[1])
        o.goto(FE + "/workspaces")
        o.wait_for_selector("article:has-text('VOLT Marketing')", timeout=30000)
        o.screenshot(path=SHOTS + "/10-desktop-program-access.png", full_page=True)
        check("owner Program Access shows the VOLT Marketing card", True)
        o.locator("article", has_text="VOLT Marketing").first.locator("button, a").first.click()
        o.wait_for_url("**/volt-ops/**", timeout=20000)
        o.wait_for_selector("[data-kpi=starts]")
        check("clicking the Program Access card opens VOLT Operations (owner)", "/volt-ops/" in o.url and "Owner" in o.content(), o.url)
    staff_login(s, STAFF, STAFF_PW)
    s.wait_for_url("**/volt-ops/**", timeout=30000)
    check("recruiting staff land in VOLT Operations after sign-in", "/volt-ops/" in s.url, s.url)
    s.goto(FE + "/workspaces")
    s.wait_for_url("**/volt-ops/**", timeout=30000)
    check("staff whose only program is VOLT: Program Access opens VOLT Operations", True)
    org_id = s.url.split("/volt-ops/")[1].split("/")[0].split("?")[0]
    s.wait_for_selector("[data-kpi=starts]")
    s.locator("label", has_text="Include test records").locator("input").check()
    s.wait_for_timeout(800)
    s.screenshot(path=SHOTS + "/11-desktop-ops-overview.png", full_page=True)
    # Review queue -> applicant's ACA file.
    s.locator("[data-kpi=awaiting_review]").click()
    s.wait_for_selector("[data-table=queue]")
    s.wait_for_timeout(800)
    row = s.locator("[data-table=queue] tr", has_text="Jordan Rivera").filter(has_text="ACA")
    check("submitted ACA application appears in Awaiting review", row.count() == 1)
    s.screenshot(path=SHOTS + "/12-desktop-review-queue.png", full_page=True)
    row.locator("text=Open").click()
    s.wait_for_selector(".v-drawer >> text=Recruiter attribution")
    drawer = s.locator(".v-drawer")
    txt = drawer.inner_text()
    check("file shows profile, answers, documents, attribution and history",
          all(x in txt for x in ("DOB 1995-03-09", "two summers", "experience-summary.pdf", "Original recruiter:", "Dangelo Simmons", "Review history")), txt[:400])
    with s.expect_popup() as pop:
        drawer.locator("text=Open file").first.click()
    check("authorized reviewer opens the stored file", pop.value is not None)
    s.screenshot(path=SHOTS + "/13-desktop-application-file.png", full_page=True)
    # Request replacement + return with reason.
    drawer.locator("input[placeholder^='Reason if requesting']").fill("Please upload the signed version.")
    drawer.locator("text=Request replacement").click()
    s.wait_for_selector(".v-drawer >> text=Replacement requested: Please upload the signed version.")
    drawer.locator("input[placeholder='Exactly what to change']").fill("Upload your signed experience summary.")
    drawer.locator("button", has_text="Return for update").click()
    s.wait_for_selector(".v-drawer [data-stage=needs_update]")
    check("staff requested replacement and returned with a specific reason", True)
    drawer.locator("text=Close").click()

    # Applicant sees the request, replaces, resubmits (phone).
    mob = browser.new_context(**p.devices["iPhone 13"])
    a = mob.new_page()
    a.goto(FE + "/volt/sign-in")
    a.fill("#v-si-email", APL)
    a.fill("#v-si-pass", APL_PW)
    a.locator("button[type=submit]").click()
    a.wait_for_url("**/volt/me")
    a.wait_for_selector("text=Upload your signed experience summary.")
    check("applicant dashboard shows Needs update with the reviewer's reason", "Needs update" in a.content())
    check("document request shows Replace with reviewer reason", "Replace: Please upload the signed version." in a.content())
    a.screenshot(path=SHOTS + "/14-mobile-needs-update.png", full_page=True)
    a.locator("[data-application]", has_text="ACA").locator("a", has_text="Update →").click()
    a.wait_for_selector("[data-doc=experience_summary]")
    a.set_input_files("[data-doc=experience_summary] input[type=file]",
                      files=[{"name": "experience-summary-signed.pdf", "mimeType": "application/pdf", "buffer": PDF2}])
    a.wait_for_selector("text=version 2", timeout=15000)
    a.locator("text=Resubmit application").click()
    a.wait_for_selector(".v-step.now >> text=Submitted", timeout=10000)
    check("applicant replaced the document (v2) and resubmitted", True)

    # Staff accepts v2, approves, marks Started.
    s.goto(FE + "/volt-ops/%s/review?include_test=1&status=submitted&reached=submitted" % org_id)
    s.wait_for_selector("[data-table=queue]")
    s.wait_for_timeout(600)
    s.locator("[data-table=queue] tr", has_text="Jordan Rivera").filter(has_text="ACA").locator("text=Open").click()
    s.wait_for_selector(".v-drawer [data-doc-version='2']")
    check("reviewer sees both versions", s.locator(".v-drawer [data-doc-version]").count() == 2)
    s.locator(".v-drawer [data-doc-version='2'] >> text=Accept").click()
    s.wait_for_selector(".v-drawer [data-doc-version='2'] >> text=accepted")
    s.locator(".v-drawer button", has_text="Approve").click()
    s.wait_for_selector(".v-drawer [data-stage=approved]")
    s.locator(".v-drawer button", has_text="Mark Started").click()
    s.wait_for_selector(".v-drawer [data-stage=started]")
    check("accepted v2, approved and marked Started", True)
    check("no further stage action after Started (cannot double-start from the screen)",
          s.locator(".v-drawer button", has_text="Mark Started").count() == 0)
    s.screenshot(path=SHOTS + "/15-desktop-started.png", full_page=True)
    s.locator(".v-drawer >> text=Close").click()

    # Leaderboard / report / CSV reconcile for ACA.
    s.goto(FE + "/volt-ops/%s" % org_id)
    s.wait_for_selector("[data-kpi=starts]")
    s.locator("label", has_text="Include test records").locator("input").check()
    aca_id = [o for o in s.locator("select").first.locator("option").all() if "ACA" in o.inner_text()][0].get_attribute("value")
    s.locator("select").first.select_option(aca_id)
    s.wait_for_timeout(1200)
    starts = int(s.locator("[data-kpi=starts] b").inner_text())
    lb = s.locator("[data-table=leaderboard] tr", has_text="Dangelo Simmons")
    lb_starts = int(lb.locator("td").nth(5).inner_text()) if lb.count() else -1
    tok_staff0 = s.evaluate("localStorage.getItem('af_token')")
    srv = call("GET", "/volt/ops/%s?include_test=1&project_id=%s" % (org_id, aca_id), tok=tok_staff0)[1]["overview"]
    shown = {k: int(s.locator("[data-kpi=%s] b" % k).inner_text()) for k in ("unique_people", "applications", "awaiting_review", "submitted", "approvals", "starts")}
    check("every KPI on screen matches the server for the ACA filter", all(shown[k] == srv[k] for k in shown), (shown, srv))
    check("leaderboard panel is the ACA (project) view", "Ranked by distinct direct recruits" in s.content())
    check("ACA starts KPI counts the started recruit", starts >= 1, starts)
    check("Dangelo's ACA leaderboard row shows 1 start", lb_starts == 1, lb_starts)
    s.screenshot(path=SHOTS + "/16-desktop-leaderboard.png", full_page=True)
    s.locator("[data-kpi=starts]").click()
    s.wait_for_selector("[data-count]")
    s.wait_for_timeout(800)
    listed = int(s.locator("[data-count]").get_attribute("data-count"))
    with s.expect_download() as dl:
        s.locator("text=Export CSV").click()
    path = dl.value.path()
    rows = list(csv.reader(io.StringIO(open(path, encoding="utf-8").read())))
    check("filtered list count == Starts KPI", listed == starts, (listed, starts))
    check("CSV rows == filtered list", len(rows) - 1 == listed, (len(rows) - 1, listed))
    body = open(path, encoding="utf-8").read()
    check("CSV has stable rep/project refs and recruiter attribution, no DOB/email/doc links",
          "REP-" in body and "aca" in body and "Dangelo Simmons" in body and "1995-03-09" not in body
          and "@example.test" not in body and "/documents/" not in body)
    # Repeat Started through the API: no double count.
    tok_staff = s.evaluate("localStorage.getItem('af_token')")
    q = call("GET", "/volt/ops/%s/applications?include_test=1&project_id=%s&reached=started" % (org_id, aca_id), tok=tok_staff)[1]
    app_id = [r for r in q["items"] if r["applicant"] == "Jordan Rivera"][0]["application_id"]
    call("POST", "/volt/ops/%s/applications/%s/status" % (org_id, app_id), {"status": "started"}, tok_staff)
    again = call("GET", "/volt/ops/%s?include_test=1&project_id=%s" % (org_id, aca_id), tok=tok_staff)[1]
    check("repeating Started does not double-count", again["overview"]["starts"] == starts)

    # Zoom session.
    s.goto(FE + "/volt-ops/%s/sessions" % org_id)
    s.wait_for_selector("text=+ New session")
    s.locator("text=+ New session").click()
    f = s.locator("[data-form=session]")
    f.locator("select").first.select_option("orientation")
    f.locator("input[type=datetime-local]").fill("2026-12-01T18:30")
    f.locator("input[type=url]").fill("https://us02web.zoom.us/s/12345678901?zak=HOSTKEY")
    f.locator("textarea").fill("Join 5 minutes early. Camera on.")
    f.locator("input[aria-label='Find applicant']").fill("Jordan")
    s.wait_for_timeout(700)
    f.locator(".v-match", has_text="Jordan Rivera").first.click()
    f.locator("text=Schedule session").click()
    s.wait_for_selector("text=HOST start link")
    check("host start link is refused", True)
    f.locator("input[type=url]").fill("https://us02web.zoom.us/j/12345678901?pwd=TestOnly")
    f.locator("text=Schedule session").click()
    s.wait_for_selector("[data-session]")
    s.screenshot(path=SHOTS + "/17-desktop-zoom-sessions.png", full_page=True)
    check("staff scheduled an orientation with a participant link", "12345678901" in s.content())
    # Assigned applicant sees it; another applicant does not.
    a.goto(FE + "/volt/me")
    a.wait_for_selector("text=Join Zoom")
    a.screenshot(path=SHOTS + "/18-mobile-upcoming-session.png", full_page=True)
    check("assigned applicant sees local time and Join Zoom", "Orientation" in a.content() and a.locator("text=Join Zoom").count() == 1)
    other = call("GET", "/volt/rep/%s/me" % SLUG, tok=apl_token(OTHER, OTHER_PW))[1]
    check("unassigned applicant receives no session and no link", other.get("sessions") == [])
    o_tok = apl_token(OTHER, OTHER_PW)
    sid = s.locator("[data-session]").first.get_attribute("data-session")
    check("applicant cannot read staff sessions API", call("GET", "/volt/ops/%s/sessions" % org_id, tok=o_tok)[0] == 403)
    # Reschedule, attendance, cancel.
    s.locator("[data-session] >> text=Edit / reschedule").first.click()
    s.locator("[data-form=session] input[type=datetime-local]").fill("2026-12-02T10:00")
    s.locator("[data-form=session] >> text=Save changes").click()
    s.wait_for_timeout(1200)
    me = call("GET", "/volt/rep/%s/me" % SLUG, tok=apl_token(APL, APL_PW))[1]
    check("reschedule reaches the applicant (10:00 CST = 16:00Z)", me["sessions"][0]["starts_at"] == "2026-12-02T16:00:00Z", me["sessions"])
    s.locator("[data-session] select").first.select_option("attended")
    s.wait_for_timeout(800)
    check("attendance recorded", call("GET", "/volt/ops/%s/sessions" % org_id, tok=tok_staff)[1]["items"][0]["attendees"][0]["attendance"] == "attended")
    s.locator("[data-session] >> text=Cancel session").first.click()
    s.wait_for_selector("[data-session] >> text=canceled")
    a.reload()
    a.wait_for_selector("text=Upcoming Sessions")
    a.wait_for_timeout(1000)
    check("canceled session loses its Join action", a.locator("text=Join Zoom").count() == 0 and "Canceled" in a.content())
    a.screenshot(path=SHOTS + "/19-mobile-session-canceled.png", full_page=True)

    # An applicant token never opens operations; an unentitled user is refused.
    ap = mob.new_page()
    ap.goto(FE + "/volt-ops/%s" % org_id)
    ap.wait_for_timeout(2500)
    check("applicant browser cannot open VOLT Operations (sent to staff login)", "/login" in ap.url, ap.url)
    if UNENT:
        u = browser.new_context(viewport={"width": 1280, "height": 860}).new_page()
        staff_login(u, UNENT[0], UNENT[1])
        u.goto(FE + "/workspaces")
        u.wait_for_timeout(3000)
        check("unentitled platform user has no VOLT card", u.locator("text=VOLT Marketing").count() == 0)
        u.goto(FE + "/volt-ops/%s" % org_id)
        u.wait_for_timeout(3000)
        check("unentitled platform user is refused VOLT Operations", "authorized recruiting staff" in u.content())
        u.screenshot(path=SHOTS + "/20-desktop-unentitled-refused.png")
    browser.close()

bad = [l for l, ok in RESULTS if not ok]
print(json.dumps({"passed": len(RESULTS) - len(bad), "failed": bad}))
