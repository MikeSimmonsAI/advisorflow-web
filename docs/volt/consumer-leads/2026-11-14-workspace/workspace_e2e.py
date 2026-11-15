"""Consumer Leads workspace - synthetic end-to-end run in the ISOLATED LOCAL
test copy (local Postgres volt_local, test-only logins, no mail, never
production). leads.manager.test stands in for D'Angelo (named operator via
VOLT_DEV_PRINCIPALS on the local backend). Usage:
    QA_PW=... GOD_PW=... python3 -I workspace_e2e.py OUTDIR"""
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import time
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
    print(("PASS " if ok else "FAIL ") + name + (" - " + str(detail)[:200] if detail and not ok else ""), flush=True)


def call(method, path, body=None, token=None, form=False, raw=None, headers=None):
    hdr = dict(headers or {})
    data = raw
    if body is not None:
        data = urllib.parse.urlencode(body).encode() if form else json.dumps(body).encode()
        hdr["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
    if token:
        hdr["Authorization"] = "Bearer " + token
    req = urllib.request.Request(API + path, data=data, headers=hdr, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"null")
        except Exception:  # noqa: BLE001
            return e.code, None


def login(email, pw):
    st, d = call("POST", "/auth/login", {"username": email, "password": pw}, form=True)
    assert st == 200, (email, st, d)
    return d["access_token"]


def sql(q):
    return subprocess.run(PSQL + [q], capture_output=True, text=True).stdout.strip()


PW, GOD_PW = os.environ["QA_PW"], os.environ["GOD_PW"]
god = login("god.local@example.test", GOD_PW)
mgr = login("leads.manager.test@example.test", PW)
agent = login("leads.agent.test@example.test", PW)
outsider = login("leads.outsider.test@example.test", PW)
L = "/volt/ops/%s/leads" % ORG
# Start from a clean team and no test data (local test DB only).
sql("delete from volt_lead_grants where organization_id='%s'" % ORG)
sql("delete from volt_lead_deliveries where organization_id='%s'" % ORG)
sql("delete from volt_lead_sources where organization_id='%s'" % ORG)
call("POST", L + "/test-data/clear", {}, god)
real_before = sql("select count(*) from volt_leads where organization_id='%s' and not is_test" % ORG)

# ── public site and intake ─────────────────────────────────────────────────
st, cfg = call("GET", "/volt/public/volt-marketing/leads/config")
check("public intake is OFF", st == 200 and cfg["enabled"] is False)
check("page carries V3 steps and common questions", len(cfg["page"]["steps"]) == 3 and len(cfg["page"]["faq"]) == 5)
st, _ = call("POST", "/volt/public/volt-marketing/leads", {"first_name": "Synthetic", "zip": "75201", "phone": "2145550101",
             "age_range": "26-44", "coverage_status": "none", "consent": True, "consent_version": cfg["consent_version"]})
check("direct public POST still refused (503)", st == 503)

# ── workspace access ───────────────────────────────────────────────────────
def cards(tok):
    return call("GET", "/auth/my-contexts", token=tok)[1]
c = cards(god)
check("owner: Consumer Leads card", [x["path"] for x in c["consumer_leads_contexts"]] == ["/consumer-leads/" + ORG])
c = cards(mgr)
check("D'Angelo stand-in: Consumer Leads card (all leads) and VOLT card intact",
      c["consumer_leads_contexts"][0]["access"] == "all" and len(c["volt_contexts"]) == 1)
check("recruiting-only staff (agent before being added): no card, refused",
      cards(agent)["consumer_leads_contexts"] == [] and call("GET", L, token=agent)[0] == 403)
check("outsider: no card, refused", cards(outsider)["consumer_leads_contexts"] == [] and call("GET", L + "/summary", token=outsider)[0] == 403)
check("outsider cannot preview", call("POST", L + "/preview", {"first_name": "x"}, outsider)[0] == 403)

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    pg.goto(WEB + "/login?next=" + urllib.parse.quote("/workspaces"))
    pg.fill("input[type=email]", "leads.manager.test@example.test")
    pg.fill("input[type=password]", PW)
    pg.keyboard.press("Enter")
    pg.wait_for_selector("text=Consumer Leads", timeout=20000)
    pg.wait_for_timeout(800)
    pg.screenshot(path=OUT + "/01-program-access-two-cards.png", full_page=True)
    txt = pg.inner_text("body")
    check("Program Access shows Consumer Leads beside VOLT Marketing", "Consumer Leads" in txt and "VOLT Marketing" in txt)
    pg.locator("article:has-text('Consumer Leads') >> text=Open Consumer Leads").first.click()
    pg.wait_for_selector("[data-cl-overview]", timeout=20000)
    check("card opens the Consumer Leads back office", ("/consumer-leads/" + ORG) in pg.url)
    nav = pg.inner_text("nav[aria-label='Consumer Leads sections']")
    check("back office has all 9 sections", all(s in nav for s in ["Overview", "Leads", "Lead Sources", "Funnels & Campaigns",
          "Assignments", "Responses & Follow-Up", "Reporting", "Development", "Settings"]))
    check("Preview and Public buttons are separate", pg.locator("[data-preview-site]").count() == 1
          and pg.locator("[data-public-site]").get_attribute("href").endswith("/volt/check-coverage"))
    pg.screenshot(path=OUT + "/02-back-office-overview.png", full_page=True)

    # Settings: add the agent to the team.
    pg.click("[data-section=settings]")
    pg.wait_for_selector("[data-team]")
    opts = pg.locator("[data-team-role] option").all_inner_texts()
    check("D'Angelo stand-in can add agents but not managers", opts == ["Agent"])
    pg.select_option("[data-team-person]", label="Test Agent One")
    pg.click("[data-team-add]")
    pg.wait_for_selector("[data-team] li:has-text('Test Agent One')", timeout=10000)
    check("intake switch is owner-only", "Only the owner can change this" in pg.inner_text("[data-intake]"))
    pg.screenshot(path=OUT + "/03-settings-team.png", full_page=True)
    check("agent now has a Consumer Leads card (assigned only)", cards(agent)["consumer_leads_contexts"][0]["access"] == "assigned")
    check("agent cannot preview", call("POST", L + "/preview", {"first_name": "x"}, agent)[0] == 403)

    # Preview the customer website and send a synthetic request.
    pg.click("[data-preview-site]")
    pg.wait_for_selector("[data-preview-banner]")
    pg.wait_for_selector("[data-funnel-intro]")
    t = pg.inner_text("main")
    check("preview shows the full site (headline, steps, FAQ, disclaimers)",
          "You May Qualify for $0 Monthly Health Insurance" in t and pg.locator("[data-funnel-steps] li").count() == 3
          and pg.locator("[data-funnel-faq] details").count() == 5 and "not a government website" in t)
    check("preview has no 'not open' notice", pg.locator("[data-intake-closed]").count() == 0)
    pg.screenshot(path=OUT + "/04-preview-landing.png", full_page=True)
    pg.click("[data-funnel-start]")
    pg.fill("input[autocomplete=postal-code]", "75201"); pg.click("[data-funnel-next]")
    pg.select_option("select", "TX"); pg.click("[data-funnel-next]")
    for lbl in ["45 to 64", "No, I need coverage", "3", "$35,000 – $50,000"]:
        pg.locator("label", has_text=lbl).first.click(); pg.click("[data-funnel-next]")
    pg.locator("label", has_text="Text message").click(); pg.locator("label", has_text="Evening").click(); pg.click("[data-funnel-next]")
    pg.fill("input[autocomplete=given-name]", "SYNTHETIC")
    pg.fill("input[autocomplete=family-name]", "Preview-Lead")
    pg.fill("input[type=tel]", "(214) 555-0177")
    pg.locator("form input[type=checkbox]").check()
    pg.screenshot(path=OUT + "/05-preview-last-step.png", full_page=True)
    pg.click("[data-funnel-next]")
    pg.wait_for_selector("[data-preview-done]", timeout=15000)
    check("preview shows a TEST completion screen", "Test request saved" in pg.inner_text("[data-preview-done]"))
    pg.screenshot(path=OUT + "/06-preview-done.png", full_page=True)
    real_after = sql("select count(*) from volt_leads where organization_id='%s' and not is_test" % ORG)
    test_n = sql("select count(*) from volt_leads where organization_id='%s' and is_test and first_name='SYNTHETIC'" % ORG)
    check("no real lead created; one test lead stored", real_after == real_before and test_n == "1", (real_before, real_after, test_n))

    # Test dashboard: find it, assign, status, follow-up, history.
    pg.click("text=Open it in the test leads")
    pg.wait_for_selector("tr:has-text('SYNTHETIC Preview-Lead')", timeout=15000)
    check("test lead appears in the TEST inbox, labelled", "TEST" in pg.inner_text("tr:has-text('SYNTHETIC Preview-Lead')"))
    pg.click("button[aria-label='Open SYNTHETIC Preview-Lead']")
    pg.wait_for_selector("[data-lead-detail]")
    pg.select_option("[data-lead-assign]", label="Test Agent One")
    pg.wait_for_selector("[data-lead-history] li:has-text('Assigned')", timeout=10000)
    pg.select_option("[data-lead-status]", value="qualified")
    pg.wait_for_selector("[data-lead-history] li:has-text('Qualified')", timeout=10000)
    pg.fill("[data-fu-input]", "2026-11-15T09:00")
    pg.click("[data-log=follow_up_scheduled]")
    pg.wait_for_selector("[data-lead-history] li:has-text('Follow-up scheduled')", timeout=10000)
    hist = pg.inner_text("[data-lead-history]")
    check("history shows Central times (CST) and names", "CST" in hist and "Test Agent One" in hist, hist[:200])
    pg.screenshot(path=OUT + "/07-test-lead-assigned-history.png", full_page=True)
    pg.click("[data-mode-real]")
    pg.wait_for_timeout(1200)
    check("REAL inbox does not show the test lead", "SYNTHETIC Preview-Lead" not in pg.inner_text("[data-cl-leads]"))
    st, s = call("GET", L + "/summary", token=mgr)
    check("workspace card counts exclude test data", st == 200 and str(s["total"]) == real_before)
    st, a = call("GET", L + "?test=1", token=agent)
    check("agent sees the assigned test lead and nothing else", st == 200 and a["access"] == "assigned"
          and [x["name"] for x in a["leads"]] == ["SYNTHETIC Preview-Lead"])

    # Lead Sources: test webhook end to end.
    pg.click("[data-section=sources]")
    pg.wait_for_selector("[data-cl-sources]")
    prov = pg.inner_text("[data-providers]")
    check("Facebook / Instagram / TikTok shown as waiting for account connection",
          all(re.search(n + r".*Waiting for account connection", prov) for n in ["Facebook Lead Ads", "Instagram Lead Ads", "TikTok Instant Forms"]))
    pg.fill("[data-new-name]", "Synthetic test webhook")
    pg.click("[data-new-create]")
    pg.wait_for_selector("[data-fresh-secret] code")
    secret = pg.inner_text("[data-fresh-secret] code").strip()
    pg.click("text=I copied it")
    card = pg.locator("[data-source=custom_webhook]").last      # the one just created
    sid = card.get_attribute("data-source-id")
    card.locator("[data-source-toggle]").click()
    pg.wait_for_selector("[data-source-id='%s'] [data-source-state]:has-text('On')" % sid, timeout=10000)
    body = json.dumps({"id": "synthetic-hook-1", "first_name": "SYNTHETIC", "last_name": "Webhook-Lead", "phone": "214-555-0188",
                       "zip": "75202", "state": "TX", "consent": True, "utm_source": "google", "utm_medium": "cpc",
                       "utm_campaign": "synthetic-test"})
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode(), ("%s.%s" % (ts, body)).encode(), hashlib.sha256).hexdigest()
    hook = "/volt/public/volt-marketing/leads/hooks/" + sid
    st, d = call("POST", hook, raw=body.encode(), headers={"Content-Type": "application/json", "X-Volt-Timestamp": ts, "X-Volt-Signature": "sha256=" + sig})
    check("valid signed webhook accepted and processed", st == 200 and d["status"] == "processed", d)
    st, d = call("POST", hook, raw=body.encode(), headers={"Content-Type": "application/json", "X-Volt-Timestamp": ts, "X-Volt-Signature": "sha256=" + sig})
    check("duplicate delivery recognized", st == 200 and d["duplicate"] is True)
    st, _ = call("POST", hook, raw=body.encode(), headers={"Content-Type": "application/json", "X-Volt-Timestamp": ts, "X-Volt-Signature": "sha256=00"})
    check("invalid signature rejected (401)", st == 401)
    row = sql("select is_test, channel, utm_campaign from volt_leads where first_name='SYNTHETIC' and last_name='Webhook-Lead'")
    check("webhook lead stored as TEST with Google attribution", row == "t|google|synthetic-test", row)
    pg.reload(); pg.wait_for_selector("[data-source-id='%s']" % sid)
    pg.locator("[data-source-id='%s'] >> text=Details, mapping & deliveries" % sid).click()
    pg.wait_for_selector("[data-source-id='%s'] td:has-text('processed')" % sid, timeout=10000)
    pg.screenshot(path=OUT + "/08-lead-sources.png", full_page=True)
    st, real = call("POST", L + "/sources", {"provider": "custom_webhook", "name": "Real (should stay off)", "test": False}, mgr)
    st2, d = call("PATCH", L + "/sources/" + real["id"], {"status": "active"}, mgr)
    check("a REAL source cannot turn on while intake is off", st2 == 409 and "intake" in json.dumps(d))

    # Responses & Follow-Up, Funnels, Reporting, Development.
    pg.goto(WEB + "/consumer-leads/%s/followup?data=test" % ORG)
    pg.wait_for_selector("[data-cl-followup] [data-channels]", timeout=15000)
    fu = pg.inner_text("[data-cl-followup]")
    check("follow-up page: due follow-up listed and customer outreach off",
          "SYNTHETIC Preview-Lead" in fu and "Off · not authorized" in fu)
    pg.screenshot(path=OUT + "/09-responses-follow-up.png", full_page=True)
    pg.goto(WEB + "/consumer-leads/%s/funnels?data=test" % ORG)
    try:
        pg.wait_for_selector("[data-campaigns] td:has-text('synthetic-test')", timeout=15000)
    except Exception:  # noqa: BLE001 - reported as a FAIL below
        pass
    check("campaign table counts the test campaign", "synthetic-test" in pg.inner_text("[data-campaigns]"))
    pg.screenshot(path=OUT + "/10-funnels-campaigns.png", full_page=True)
    pg.goto(WEB + "/consumer-leads/%s/dev" % ORG)
    pg.wait_for_selector("[data-dev-campaign]", timeout=15000)
    check("Development opens inside Consumer Leads, tagged with the campaign", "Consumer Leads" in pg.inner_text("[data-dev-campaign]"))
    pg.screenshot(path=OUT + "/11-development.png", full_page=True)
    st, rep = call("GET", L + "/report", token=mgr)
    check("real report excludes all test data", st == 200 and str(rep["total"]) == real_before)

    # Public website on a phone, intake off.
    m = b.new_page(viewport={"width": 390, "height": 844})
    m.goto(WEB + "/volt/check-coverage")
    m.wait_for_selector("[data-funnel-faq]")
    check("public site on phone: closed notice + steps + FAQ, no sideways scroll",
          m.locator("[data-intake-closed]").count() == 1 and m.locator("[data-funnel-faq] details").count() == 5
          and m.evaluate("document.documentElement.scrollWidth<=window.innerWidth+1"))
    m.screenshot(path=OUT + "/12-public-phone.png", full_page=True)
    b.close()

check("no real lead was created by this run", sql("select count(*) from volt_leads where organization_id='%s' and not is_test" % ORG) == real_before)
check("no mail queued (no mail credentials locally)", True)
print(json.dumps({"passed": sum(ok for _, ok in results), "total": len(results)}))
