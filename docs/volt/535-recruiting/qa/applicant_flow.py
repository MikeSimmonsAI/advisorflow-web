"""VOLT #535 - applicant flow through the real screens (phone viewport).

usage: python3 -I applicant_flow.py <frontend_base> <api_base> <shots_dir>
Synthetic people only (@example.test). Prints PASS/FAIL lines and a JSON summary.
"""
import json, sys, time, urllib.request, uuid
from playwright.sync_api import sync_playwright, expect

FE, API, SHOTS = sys.argv[1].rstrip("/"), sys.argv[2].rstrip("/"), sys.argv[3]
SLUG = "volt-marketing"
RESULTS = []


def check(label, ok, detail=""):
    RESULTS.append((label, bool(ok)))
    print(("PASS  " if ok else "FAIL  ") + label + ("" if ok else "  -- %s" % detail), flush=True)


def call(m, p, body=None, tok=None):
    req = urllib.request.Request(API + p, data=json.dumps(body).encode() if body is not None else None, method=m)
    req.add_header("Content-Type", "application/json")
    if tok:
        req.add_header("Authorization", "Bearer " + tok)
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


tag = uuid.uuid4().hex[:6]
# The acceptance-test fixture recruiter (synthetic).
code, d = call("POST", "/volt/public/%s/register" % SLUG, {
    "first_name": "Dangelo", "last_name": "Simmons", "email": "dangelo.fixture-%s@example.test" % tag,
    "phone": "(555) 010-4242", "date_of_birth": "1988-05-17", "password": "Volt-Fixture-2026!", "project_key": "aca"})
assert code == 200, d
DANGELO = d["rep"]
print("fixture recruiter", DANGELO, flush=True)

email = "mobile.applicant-%s@example.test" % tag
password = "Volt-Applicant-2026!"
summary = {"fixture": DANGELO, "applicant_email": email}

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    ctx = browser.new_context(**p.devices["iPhone 13"])
    page = ctx.new_page()
    page.goto(FE + "/volt")
    page.wait_for_selector("[data-project=aca]")
    page.screenshot(path=SHOTS + "/01-mobile-public-portal.png", full_page=True)
    open_cards = page.locator(".v-card--project")
    check("public portal shows exactly two open projects", open_cards.count() == 2)
    check("ACA open", "ACA (Obamacare) Health Insurance" in open_cards.nth(0).inner_text())
    check("Fiber open", "Fiber Internet Outreach" in open_cards.nth(1).inner_text())
    past = page.locator(".v-past")
    check("Midterms shown as Recruiting closed", "Midterms Flyer Distribution" in past.inner_text() and "recruiting closed" in past.inner_text().lower())
    check("Sign In visible in header", page.locator("header >> text=Sign In").is_visible())
    check("no owner/staff link on public page", page.locator("text=/owner preview|operations/i").count() == 0)
    check("Register & Apply visible", page.locator("text=Register & Apply").first.is_visible())

    page.locator("[data-project=aca] >> text=Register & Apply").click()
    page.wait_for_selector("#v-first_name")
    # Field-level errors first.
    page.locator("button[type=submit]").click()
    page.wait_for_selector(".v-ferr")
    errs = page.locator(".v-ferr").all_inner_texts()
    check("field-level errors shown for empty required fields", len(errs) >= 5, errs)
    page.screenshot(path=SHOTS + "/02-mobile-field-errors.png", full_page=True)
    page.fill("#v-first_name", "Jordan")
    page.fill("#v-last_name", "Rivera")
    page.fill("#v-email", email)
    page.fill("#v-phone", "(555) 010-3131")
    page.fill("#v-date_of_birth", "1995-03-09")
    page.fill("#v-password", password)
    # Type Dangelo but do NOT select: submit must be blocked.
    page.fill("#v-ref", "Dangelo")
    page.wait_for_selector(".v-match")
    match_texts = page.locator(".v-match").all_inner_texts()
    want = "Dangelo Simmons"
    row = [t for t in match_texts if want in t and DANGELO["rep_code"] in t]
    check("typing Dangelo returns 'Dangelo Simmons · %s'" % DANGELO["rep_code"], bool(row), match_texts)
    page.screenshot(path=SHOTS + "/03-mobile-referral-search.png", full_page=True)
    page.locator("button[type=submit]").click()
    page.wait_for_timeout(400)
    check("typed name without selection blocks submit with a prompt",
          "Select a matching recruiter" in page.content() and page.url.endswith("/volt/apply/aca"))
    # Click the matching record.
    page.locator(".v-match", has_text=DANGELO["rep_code"]).click()
    sel = page.locator(".v-selected").inner_text()
    check("selected name shown after click", "Dangelo Simmons" in sel and DANGELO["rep_code"] in sel, sel)
    # Editing after selection clears it.
    page.locator("#v-ref").press("End")
    page.locator("#v-ref").type("x")
    page.wait_for_timeout(300)
    check("editing the search clears the selection", page.locator(".v-selected").count() == 0)
    page.fill("#v-ref", "d’angelo")
    page.wait_for_selector(".v-match")
    check("D’Angelo spelling finds the same record", page.locator(".v-match", has_text=DANGELO["rep_code"]).count() == 1)
    # Keyboard selection.
    page.locator("#v-ref").focus()
    idx = [i for i, t in enumerate(page.locator(".v-match").all_inner_texts()) if DANGELO["rep_code"] in t][0]
    for _ in range(idx + 1):
        page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    check("keyboard selection works", DANGELO["rep_code"] in page.locator(".v-selected").inner_text())
    page.screenshot(path=SHOTS + "/04-mobile-referral-selected.png", full_page=True)
    page.locator("button[type=submit]").click()
    page.wait_for_url("**/volt/me/applications/*")
    app_id = page.url.rsplit("/", 1)[1]
    page.wait_for_selector("#v-answer")
    tok = page.evaluate("localStorage.getItem('af_volt_token')")
    me = call("GET", "/volt/rep/%s/me" % SLUG, tok=tok)[1]
    a = me["applications"][0]
    check("saved project recruiter ID is Dangelo's database ID", a["referrer"]["rep_id"] == DANGELO["rep_id"], a["referrer"])
    check("original recruiter is Dangelo", me["original_recruiter"]["rep_id"] == DANGELO["rep_id"])
    check("one permanent serial issued", me["profile"]["rep_code"].startswith("REP-"))
    check("DOB stored as calendar date", me["profile"]["date_of_birth"] == "1995-03-09", me["profile"]["date_of_birth"])
    summary.update(rep=me["profile"]["rep_code"], aca_application=app_id)
    # Draft save.
    page.fill("#v-answer", "Door-to-door outreach in Dallas for two summers.")
    page.wait_for_selector("text=Draft saved", timeout=10000)
    check("draft saves server-side", True)
    # Private upload.
    pdf = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
    page.set_input_files("[data-doc=experience_summary] input[type=file]",
                         files=[{"name": "experience-summary.pdf", "mimeType": "application/pdf", "buffer": pdf}])
    page.wait_for_selector("[data-doc=experience_summary] >> text=Under review", timeout=15000)
    check("document uploaded and shows Under review", True)
    page.screenshot(path=SHOTS + "/05-mobile-application-draft.png", full_page=True)
    # Sign out, sign back in, resume.
    page.goto(FE + "/volt/me")
    page.locator("header >> text=Sign out").click()
    page.wait_for_url("**/volt")
    page.locator("header >> text=Sign In").click()
    page.fill("#v-si-email", email)
    page.fill("#v-si-pass", password)
    page.locator("button[type=submit]").click()
    page.wait_for_url("**/volt/me")
    page.wait_for_selector("[data-application]")
    dash = page.locator("[data-application]").first.inner_text()
    check("returning applicant lands on dashboard and sees where to resume",
          "Resume" in dash or "Review and submit" in dash, dash)
    page.screenshot(path=SHOTS + "/06-mobile-rep-dashboard.png", full_page=True)
    page.locator("[data-application] >> text=Resume").first.click()
    page.wait_for_selector("#v-answer")
    check("resumed draft answer is intact", "two summers" in page.input_value("#v-answer"))
    page.locator("text=Submit application").click()
    page.wait_for_selector(".v-step.now >> text=Submitted", timeout=10000)
    check("submitted", True)
    # Second project with the same profile.
    page.goto(FE + "/volt/apply/fiber")
    page.wait_for_selector("text=existing profile")
    check("second project uses existing rep ID", me["profile"]["rep_code"] in page.content())
    page.fill("#v-ref", "Dangelo")
    page.wait_for_selector(".v-match")
    page.locator(".v-match", has_text=DANGELO["rep_code"]).click()
    page.locator("button[type=submit]").click()
    page.wait_for_url("**/volt/me/applications/*")
    fiber_id = page.url.rsplit("/", 1)[1]
    me2 = call("GET", "/volt/rep/%s/me" % SLUG, tok=page.evaluate("localStorage.getItem('af_volt_token')"))[1]
    check("two distinct applications, same serial",
          len(me2["applications"]) == 2 and fiber_id != app_id and me2["profile"]["rep_code"] == me["profile"]["rep_code"])
    check("original recruiter unchanged after second application", me2["original_recruiter"]["rep_id"] == DANGELO["rep_id"])
    summary.update(fiber_application=fiber_id)
    # Closed project through the screen.
    page.goto(FE + "/volt/apply/midterms")
    page.wait_for_selector("text=Recruiting closed")
    check("Midterms apply page says Recruiting closed and offers no form", page.locator("#v-ref").count() == 0)
    # Direct signup (blank referral) on a second phone.
    ctx2 = browser.new_context(**p.devices["Pixel 7"])
    p2 = ctx2.new_page()
    p2.goto(FE + "/volt/apply/fiber")
    p2.wait_for_selector("#v-first_name")
    for k, v in (("first_name", "Direct"), ("last_name", "Signup"), ("email", "direct-%s@example.test" % tag),
                 ("phone", "5550109999"), ("date_of_birth", "1990-01-01"), ("password", "Volt-Direct-2026!")):
        p2.fill("#v-" + k, v)
    p2.locator("button[type=submit]").click()
    p2.wait_for_url("**/volt/me/applications/*")
    m3 = call("GET", "/volt/rep/%s/me" % SLUG, tok=p2.evaluate("localStorage.getItem('af_volt_token')"))[1]
    check("blank referral = direct signup", m3["applications"][0]["referrer"] is None and m3["original_recruiter"] is None)
    # Invite link: suggests, requires explicit confirmation.
    ctx3 = browser.new_context(**p.devices["iPhone 13"])
    p3 = ctx3.new_page()
    p3.goto(FE + "/volt?ref=%s&project=fiber" % DANGELO["rep_code"])
    p3.wait_for_url("**/volt/apply/fiber")
    p3.wait_for_selector("text=Did they refer you?")
    check("invite lands on the intended project and shows the matched recruiter",
          DANGELO["rep_code"] in p3.content() and p3.locator(".v-selected").count() == 0)
    p3.screenshot(path=SHOTS + "/07-mobile-invite-confirm.png", full_page=True)
    p3.locator("text=Yes, Dangelo Simmons referred me").click()
    for k, v in (("first_name", "Invited"), ("last_name", "Applicant"), ("email", "invited-%s@example.test" % tag),
                 ("phone", "5550108888"), ("date_of_birth", "1992-12-31"), ("password", "Volt-Invite-2026!")):
        p3.fill("#v-" + k, v)
    p3.locator("button[type=submit]").click()
    p3.wait_for_url("**/volt/me/applications/*")
    m4 = call("GET", "/volt/rep/%s/me" % SLUG, tok=p3.evaluate("localStorage.getItem('af_volt_token')"))[1]
    check("confirmed invite credits Dangelo (source=invite)",
          m4["applications"][0]["referrer"]["rep_id"] == DANGELO["rep_id"] and m4["applications"][0]["referral_source"] == "invite")
    # Invalid invite.
    p3.evaluate("sessionStorage.clear(); localStorage.removeItem('af_volt_token')")
    p3.goto(FE + "/volt?ref=REP-999999&project=aca")
    p3.wait_for_url("**/volt/apply/aca")
    p3.wait_for_selector("text=not valid")
    check("invalid invite is handled and credits nobody", p3.locator(".v-selected").count() == 0)
    summary.update(applicant_token_sample=None, direct_email="direct-%s@example.test" % tag)
    browser.close()

bad = [l for l, ok in RESULTS if not ok]
print(json.dumps({"passed": len(RESULTS) - len(bad), "failed": bad, **summary}))
