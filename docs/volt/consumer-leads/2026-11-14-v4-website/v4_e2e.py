"""Consumer Leads V4 website - browser checks on the ISOLATED LOCAL copy
(local Postgres volt_local, test-only logins, never production). Usage:
    QA_PW=... GOD_PW=... python3 -I v4_e2e.py OUTDIR"""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

from playwright.sync_api import sync_playwright

API, WEB = "http://127.0.0.1:8011", "http://localhost:5173"
ORG = "349d8055-3258-42a8-ab96-911f9ca8c582"
OUT = sys.argv[1]
PSQL = ["psql", "postgresql://postgres@localhost:5433/volt_local?host=/tmp", "-At", "-c"]
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + (" - " + str(detail)[:240] if detail and not ok else ""), flush=True)


def sql(q):
    return subprocess.run(PSQL + [q], capture_output=True, text=True).stdout.strip()


def call(method, path, body=None, token=None, form=False):
    hdr, data = {}, None
    if body is not None:
        data = urllib.parse.urlencode(body).encode() if form else json.dumps(body).encode()
        hdr["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
    if token:
        hdr["Authorization"] = "Bearer " + token
    try:
        with urllib.request.urlopen(urllib.request.Request(API + path, data=data, headers=hdr, method=method)) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, None


PW, GOD_PW = os.environ["QA_PW"], os.environ["GOD_PW"]
real_before = sql("select count(*) from volt_leads where organization_id='%s' and not is_test" % ORG)
st, mgr_login = call("POST", "/auth/login", {"username": "leads.manager.test@example.test", "password": PW}, form=True)
mgr = mgr_login["access_token"]
call("POST", "/volt/ops/%s/leads/test-data/clear" % ORG, {}, mgr)


def walk(pg, upto=8, age="26 to 44"):
    """Answer questions 1..upto-1 so the page sits on question `upto`."""
    pg.fill("#cp-zip", "75201"); pg.click("[data-funnel-next]")
    if upto == 2: return
    pg.select_option("#cp-state", "TX"); pg.click("[data-funnel-next]")
    if upto == 3: return
    for lbl in [age, "No, I need coverage", "2", "$20,000 – $35,000"]:
        pg.locator("[data-wizard] label.cp-choice", has_text=lbl).first.click(); pg.click("[data-funnel-next]")
    pg.locator("label.cp-choice", has_text="Text message").click()
    pg.locator("label.cp-choice", has_text="Evening").click()
    pg.click("[data-funnel-next]")


with sync_playwright() as p:
    b = p.chromium.launch()
    for name, vp in [("desktop", {"width": 1440, "height": 900}), ("mobile", {"width": 390, "height": 844})]:
        ctx = b.new_context(viewport=vp)
        pg = ctx.new_page()
        errors, posts = [], []
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("request", lambda r: posts.append(r.url) if r.method == "POST" and "/leads" in r.url else None)
        pg.goto(WEB + "/volt/check-coverage?utm_source=facebook&utm_medium=paid_social&utm_campaign=v4-check")
        pg.wait_for_selector("[data-consumer-site] #cp-h1", timeout=20000)
        pg.wait_for_timeout(600)
        body = pg.inner_text("body")
        check(f"{name}: V4 headline", "Health coverage that fits your life and budget." in pg.inner_text("#cp-h1"))
        check(f"{name}: supporting message", "$0 monthly premium" in body and "See what options might be available" in body)
        check(f"{name}: trust points", all(t in pg.inner_text("[data-trust]") for t in
              ["Free to check", "No obligation", "No account required", "Help from licensed agents"]))
        check(f"{name}: six benefits", pg.locator("[data-benefits] li").count() == 6
              and all(t in pg.inner_text("[data-benefits]") for t in ["Potential premium savings", "Doctor visits",
              "Prescription coverage", "Preventive care", "Emergency benefits", "Human guidance"]))
        check(f"{name}: three steps and five questions", pg.locator("[data-funnel-steps] li").count() == 3
              and pg.locator("[data-funnel-faq] details").count() == 5)
        disc = pg.inner_text("[data-funnel-disclaimers]")
        check(f"{name}: disclosures", all(s in disc for s in ["Not everyone qualifies", "not guaranteed",
              "does not mean all care is free", "not a government website"]))
        check(f"{name}: intake-closed notice", pg.locator("[data-intake-closed]").count() == 1)
        check(f"{name}: no recruiting or other brand on the page", not any(w in body for w in
              ["VOLT", "Sign In", "BookaBoost", "STAGING", "Recruit", "EvoSys"]), [w for w in ["VOLT", "Sign In", "BookaBoost", "STAGING", "Recruit", "EvoSys"] if w in body])
        check(f"{name}: no sideways scroll", pg.evaluate("document.documentElement.scrollWidth<=window.innerWidth+1"))
        check(f"{name}: no loading splash left", pg.locator("#bb-splash").count() == 0)
        pg.screenshot(path=f"{OUT}/{name}-1-home.png", full_page=True)
        pg.screenshot(path=f"{OUT}/{name}-1-home-top.png")
        # Questionnaire
        pg.click("[data-funnel-start]")
        pg.wait_for_selector("[data-wizard]")
        check(f"{name}: questions open in a dialog, focus on the question", pg.evaluate("document.activeElement.id") == "cp-q"
              and pg.get_attribute("[data-wizard]", "role") == "dialog")
        pg.click("[data-funnel-next]")
        check(f"{name}: empty ZIP refused", "5-digit ZIP" in pg.inner_text("#cp-err"))
        pg.fill("#cp-zip", "752"); pg.click("[data-funnel-next]")
        check(f"{name}: short ZIP refused", "5-digit ZIP" in pg.inner_text("#cp-err"))
        pg.fill("#cp-zip", "75201"); pg.click("[data-funnel-next]")
        pg.click("[data-funnel-next]")
        check(f"{name}: state required", "choose your state" in pg.inner_text("#cp-err"))
        pg.select_option("#cp-state", "TX"); pg.click("[data-funnel-next]")
        pg.click("[data-funnel-next]")
        check(f"{name}: answer required", "choose an answer" in pg.inner_text("#cp-err"))
        pg.locator("label.cp-choice", has_text="65 or older").click()
        check(f"{name}: Medicare note for 65+", pg.locator("[data-medicare-note]").count() == 1)
        pg.locator("label.cp-choice", has_text="45 to 64").click()
        pg.screenshot(path=f"{OUT}/{name}-2-question.png")
        pg.click("[data-funnel-next]")
        pg.click("[data-back]")
        check(f"{name}: Back works", "question 3 of 8" in pg.inner_text("[data-step]").lower())
        pg.click("[data-funnel-next]")
        seen = []
        for lbl in ["No, I need coverage", "2", "$20,000 – $35,000"]:
            seen.append(pg.inner_text("[data-step]").lower())
            pg.locator("label.cp-choice", has_text=lbl).first.click(); pg.click("[data-funnel-next]")
        seen.append(pg.inner_text("[data-step]").lower())
        pg.click("[data-funnel-next]")
        check(f"{name}: contact method and time required", "how and when" in pg.inner_text("#cp-err"))
        # keyboard: radio group by keyboard
        pg.locator("label.cp-choice", has_text="Phone call").locator("input").focus()
        pg.keyboard.press("ArrowDown")
        kb = pg.evaluate("document.querySelector('input[name=contact_preference]:checked')?.value")
        pg.locator("label.cp-choice", has_text="Evening").click()
        pg.click("[data-funnel-next]")
        seen.append(pg.inner_text("[data-step]").lower())
        check(f"{name}: arrow keys move between answers", kb == "text", kb)
        check(f"{name}: all 8 steps reached in order", seen == ["question %d of 8" % i for i in (4, 5, 6, 7, 8)], seen)
        check(f"{name}: last step inputs and send turned off", pg.locator("#cp-fn").is_disabled()
              and pg.locator("[data-consent]").is_disabled() and pg.locator("[data-funnel-next]").is_disabled()
              and "Requests open soon" in pg.inner_text("[data-funnel-next]"))
        check(f"{name}: consent wording shown", "licensed agent" in pg.inner_text("label.cp-consent"))
        pg.screenshot(path=f"{OUT}/{name}-3-contact-consent-closed.png")
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(150)
        check(f"{name}: Escape closes and returns focus", pg.locator("[data-wizard]").count() == 0
              and pg.evaluate("document.activeElement?.getAttribute('data-funnel-start')") is not None)
        check(f"{name}: nothing was sent, no success message", not posts and "Thank you" not in pg.inner_text("body"))
        check(f"{name}: no browser console errors", not errors, errors[:3])
        ctx.close()

    st, _ = call("POST", "/volt/public/volt-marketing/leads", {"first_name": "Synthetic", "zip": "75201", "phone": "2145550101",
                 "age_range": "26-44", "coverage_status": "none", "consent": True, "consent_version": "2026-11-draft-1"})
    check("direct public POST refused while intake OFF (503)", st == 503)

    # Signed-in preview: synthetic request with attribution, isolated as TEST data.
    ctx = b.new_context(viewport={"width": 1440, "height": 900})
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(WEB + "/login?next=" + urllib.parse.quote("/consumer-leads/%s/preview?utm_source=google&utm_medium=cpc&utm_campaign=v4-preview" % ORG))
    pg.fill("input[type=email]", "leads.manager.test@example.test")
    pg.fill("input[type=password]", PW)
    pg.keyboard.press("Enter")
    pg.wait_for_selector("[data-preview-banner]", timeout=20000)
    pg.wait_for_selector("[data-consumer-site] #cp-h1")
    check("preview: V4 site inside the back office, no closed notice", pg.locator("[data-intake-closed]").count() == 0)
    pg.click("[data-funnel-start]")
    walk(pg, 8, age="45 to 64")
    pg.fill("#cp-fn", "SYNTHETIC"); pg.fill("#cp-ln", "V4-Preview"); pg.fill("#cp-ph", "(214) 555-0166")
    pg.click("[data-funnel-next]")
    check("preview: consent required before sending", "agree to be contacted" in pg.inner_text("#cp-err"))
    pg.locator("[data-consent]").check()
    pg.screenshot(path=f"{OUT}/preview-4-contact-consent.png")
    pg.click("[data-funnel-next]")
    pg.wait_for_selector("[data-preview-done]", timeout=15000)
    pg.screenshot(path=f"{OUT}/preview-5-confirmation.png")
    check("preview: test confirmation shown", "Test request saved" in pg.inner_text("[data-preview-done]"))
    row = sql("select is_test, channel, utm_campaign, state, income_range, contact_preference, best_time from volt_leads "
              "where first_name='SYNTHETIC' and last_name='V4-Preview'")
    check("preview: stored as TEST with every answer and attribution", row == "t|google|v4-preview|TX|20k_35k|text|evening", row)
    check("preview: no real lead created", sql("select count(*) from volt_leads where organization_id='%s' and not is_test" % ORG) == real_before)
    st, rep = call("GET", "/volt/ops/%s/leads/report" % ORG, token=mgr)
    check("real report unchanged by the test lead", st == 200 and str(rep["total"]) == real_before)
    # Back office regression: all nine sections still open.
    pg.goto(WEB + "/consumer-leads/%s" % ORG)
    pg.wait_for_selector("[data-cl-overview]", timeout=15000)
    bad = []
    for s in ["leads", "sources", "funnels", "assignments", "followup", "reporting", "dev", "settings"]:
        pg.click("[data-section=%s]" % s)
        pg.wait_for_timeout(1200)
        if pg.locator(".v-alert--err").count() or len(pg.inner_text("main")) < 200:
            bad.append(s)
    check("back office: all 9 sections still open", not bad, bad)
    check("back office: no page errors", not errors, errors[:3])
    ctx.close()
    b.close()

print(json.dumps({"passed": sum(ok for _, ok in results), "total": len(results)}))
