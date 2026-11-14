"""VOLT Consumer Leads - synthetic end-to-end transaction in an ISOLATED LOCAL
test environment (local Postgres `volt_local`, test-only logins, no mail
credentials, never production). Usage:
    QA_PW=... GOD_PW=... python3 -I synthetic_e2e.py OUTDIR
Every step prints PASS/FAIL; screenshots go to OUTDIR."""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

API, WEB = "http://127.0.0.1:8011", "http://localhost:5173"
ORG = "349d8055-3258-42a8-ab96-911f9ca8c582"
OUT = sys.argv[1]
PSQL = ["psql", "postgresql://postgres@localhost:5433/volt_local?host=/tmp", "-At", "-c"]
CT = ZoneInfo("America/Chicago")
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (" - " + str(detail) if detail else ""), flush=True)


def call(method, path, body=None, token=None, form=False):
    data = None
    hdr = {}
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


def ct(iso):
    d = datetime.fromisoformat(iso)
    d = d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    return d.astimezone(CT).strftime("%b %-d, %Y %-I:%M:%S %p %Z")


PW, GOD_PW = os.environ["QA_PW"], os.environ["GOD_PW"]
god = login("god.local@example.test", GOD_PW)
mgr = login("leads.manager.test@example.test", PW)
agent = login("leads.agent.test@example.test", PW)
agent2 = login("leads.agent2.test@example.test", PW)
outsider = login("leads.outsider.test@example.test", PW)
base = "/volt/ops/%s/leads" % ORG
ids = json.loads(sql("select json_object_agg(email,id) from users where email like 'leads.%test@example.test'"))
sql("delete from volt_lead_events; delete from volt_leads; delete from volt_lead_grants; delete from volt_lead_settings;")

# 0. Public intake is OFF by default.
st, cfg = call("GET", "/volt/public/volt-marketing/leads/config")
check("public intake OFF by default (config)", st == 200 and cfg["enabled"] is False)
st, d = call("POST", "/volt/public/volt-marketing/leads", {"first_name": "X"})
check("submission refused while intake is OFF", st == 503, st)

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 390, "height": 844})
    pg.goto(WEB + "/volt/check-coverage")
    pg.wait_for_selector("text=This form is not open yet", timeout=15000)
    pg.screenshot(path=OUT + "/01-funnel-intake-off-phone.png", full_page=True)
    check("consumer page shows 'not open yet' while OFF", True)

    # 1. Owner turns intake ON in this TEST database only, with a recorded review note.
    st, _ = call("PUT", base + "/settings", {"intake_enabled": True}, god)
    check("turning intake on WITHOUT a review note is refused", st == 422, st)
    st, s = call("PUT", base + "/settings", {"intake_enabled": True, "reviewed": True,
                                            "review_note": "LOCAL SYNTHETIC TEST ONLY - not a compliance approval"}, god)
    check("owner turns intake on in the isolated test DB", st == 200 and s["intake_enabled"], st)
    st, _ = call("PUT", base + "/settings", {"intake_enabled": False}, mgr)
    check("campaign manager cannot change intake (owner only)", st == 403, st)
    st, g = call("POST", base + "/grants", {"user_id": ids["leads.manager.test@example.test"]}, god)
    check("owner grants the campaign manager full lead access", st == 200, st)

    # 2. Consumer completes the form (synthetic person, reserved 555-01xx number).
    pg.goto(WEB + "/volt/check-coverage?utm_source=facebook&utm_medium=paid_social&utm_campaign=synthetic-e2e-test")
    pg.wait_for_selector("[data-funnel-intro]", timeout=15000)
    intro = pg.inner_text("[data-funnel-intro]")
    check("consumer page leads with the $0 message and the required disclaimers",
          "You May Qualify for $0 Monthly Health Insurance." in intro and "not guaranteed" in intro
          and "not a government website" in intro and "HealthCare.gov" in intro and "does not mean all care is free" in intro)
    pg.screenshot(path=OUT + "/02-funnel-intro-phone.png", full_page=True)
    pg.click("[data-funnel-start]")
    pg.wait_for_selector("text=Question 1 of 8", timeout=15000)
    pg.click("button[type=submit]")
    check("form validation: empty ZIP is rejected in the page", pg.locator("#lead-err").inner_text().startswith("Please enter"))
    pg.fill("input[autocomplete=postal-code]", "75201"); pg.click("button[type=submit]")
    pg.select_option("select", "TX"); pg.click("button[type=submit]")
    pg.check("input[name=age_range] >> nth=3")
    check("65+ answer shows the Medicare note", pg.is_visible("[data-medicare-note]"))
    pg.screenshot(path=OUT + "/03-funnel-medicare-note-phone.png", full_page=True)
    pg.check("input[name=age_range] >> nth=2"); pg.click("button[type=submit]")
    pg.check("input[name=coverage_status] >> nth=0"); pg.click("button[type=submit]")
    pg.check("input[name=household_size] >> nth=1"); pg.click("button[type=submit]")
    pg.check("input[name=income_range] >> nth=2"); pg.click("button[type=submit]")
    pg.check("input[name=contact_preference] >> nth=0"); pg.check("input[name=best_time] >> nth=1")
    pg.click("button[type=submit]")
    pg.wait_for_selector("text=Question 8 of 8")
    pg.fill("input[autocomplete=given-name]", "SYNTHETIC")
    pg.fill("input[autocomplete=family-name]", "Test-Lead")
    pg.fill("input[type=tel]", "(214) 555-0143")
    pg.fill("input[type=email]", "synthetic.lead@example.test")
    pg.click("button[type=submit]")
    check("form validation: consent is required", "agree to be contacted" in pg.locator("#lead-err").inner_text())
    pg.check("input[type=checkbox]")
    pg.screenshot(path=OUT + "/04-funnel-contact-phone.png", full_page=True)
    pg.click("button[type=submit]")
    pg.wait_for_selector("text=Thank you.", timeout=15000)
    pg.screenshot(path=OUT + "/05-funnel-submitted-phone.png", full_page=True)
    check("consumer submission accepted by the API (thank-you shown)", True)

    # 3. Persistence + consent evidence + attribution, straight from the database.
    row = json.loads(sql("select row_to_json(l) from (select id, first_name, last_name, phone, email, zip_code, state, age_range,"
                         " coverage_status, household_size, income_range, contact_preference, best_time, source, channel, campaign,"
                         " utm_medium, consent_version, consent_text_hash, consent_at,"
                         " status, submissions, created_at, organization_id from volt_leads) l") or "null")
    check("lead persisted in volt_leads", row and row["first_name"] == "SYNTHETIC" and row["phone"] == "2145550143", row and row["id"])
    check("campaign isolation: lead stored under the VOLT tenant", row["organization_id"] == ORG)
    check("consent evidence stored (version, text hash, time)", row["consent_version"] == cfg["consent_version"]
          and len(row["consent_text_hash"]) == 64 and row["consent_at"])
    check("attribution stored (channel meta, utm medium + campaign)", row["channel"] == "meta" and row["utm_medium"] == "paid_social"
          and row["campaign"] == "synthetic-e2e-test")
    check("new intake fields stored (state, income, contact preference, best time)",
          (row["state"], row["income_range"], row["contact_preference"], row["best_time"]) == ("TX", "35k_50k", "call", "afternoon"), row)
    lead_id = row["id"]

    # 4. Duplicate + validation + no information leak.
    st, d = call("POST", "/volt/public/volt-marketing/leads", {"first_name": "SYNTHETIC", "zip": "75201", "phone": "2145550143",
                 "age_range": "45-64", "coverage_status": "none", "consent": True, "consent_version": cfg["consent_version"]})
    check("repeat submission gets the same answer (no 'already on file' leak)", st == 200 and "received" in d["message"])
    check("duplicate merged, not a second lead", sql("select count(*)||':'||max(submissions) from volt_leads") == "1:2")
    st, d = call("POST", "/volt/public/volt-marketing/leads", {"first_name": "SYNTHETIC", "zip": "7520", "phone": "2145550143",
                 "age_range": "45-64", "coverage_status": "none", "consent": True, "consent_version": cfg["consent_version"]})
    check("server-side validation: bad ZIP rejected", st == 422 and d["detail"]["field"] == "zip")
    st, d = call("POST", "/volt/public/volt-marketing/leads", {"first_name": "SYNTHETIC", "zip": "75201", "phone": "2145550143",
                 "age_range": "45-64", "coverage_status": "none", "consent": True, "consent_version": "old"})
    check("server-side validation: stale consent version rejected", st == 422 and d["detail"]["field"] == "consent_version")

    # 5. Dashboard (campaign manager) shows the lead.
    dash = b.new_page(viewport={"width": 1440, "height": 900})
    dash.goto(WEB + "/login?next=" + urllib.parse.quote("/volt-ops/%s/leads" % ORG))
    dash.fill("input[type=email]", "leads.manager.test@example.test")
    dash.fill("input[type=password]", PW)
    dash.keyboard.press("Enter")
    dash.wait_for_selector("table[data-table=leads] >> text=SYNTHETIC Test-Lead", timeout=20000)
    dash.screenshot(path=OUT + "/05-dashboard-lead-appears.png", full_page=True)
    check("lead appears on the internal dashboard (campaign manager)", True)

    # 6. Assign to a test agent, change status, add a note - in the UI.
    dash.click("button[aria-label='Open SYNTHETIC Test-Lead']")
    dash.wait_for_selector("[data-lead-detail]")
    dash.select_option("[data-lead-assign]", label="Test Agent One")
    dash.wait_for_selector("[data-lead-history] li:has-text('Assigned')", timeout=10000)
    dash.click("[data-log=call_attempted]")
    dash.wait_for_selector("[data-lead-history] li:has-text('Call attempted')", timeout=10000)
    dash.click("[data-log=contact_completed]")
    dash.wait_for_selector("[data-lead-history] li:has-text('Contact completed')", timeout=10000)
    dash.select_option("[data-lead-status]", value="qualified")
    dash.wait_for_selector("[data-lead-history] li:has-text('Qualified')", timeout=10000)
    dash.fill("[data-appt-input]", "2026-11-20T14:30")
    dash.click("[data-log=appointment_set]")
    dash.wait_for_selector("[data-lead-history] li:has-text('Appointment set')", timeout=10000)
    dash.fill("[data-lead-note]", "Synthetic call attempt - test only")
    dash.click("aside >> text=Save note")
    dash.wait_for_selector("[data-lead-history] li:has-text('Synthetic call attempt')", timeout=10000)
    dash.screenshot(path=OUT + "/06-dashboard-assigned-status-history.png", full_page=True)
    hist_text = dash.inner_text("[data-lead-history]")
    check("dashboard history shows Central times with CST/CDT and names", ("CST" in hist_text or "CDT" in hist_text)
          and "Test Agent One" in hist_text and "Test Campaign Manager" in hist_text, hist_text[:200])
    ov = b.new_page(viewport={"width": 1440, "height": 900})
    ov.add_init_script("localStorage.setItem('af_token', %s)" % json.dumps(mgr))
    dash.goto(WEB + "/volt-ops/%s" % ORG)
    dash.wait_for_selector("[data-campaign-card=consumer-leads] [data-card-kpi=total] b", timeout=20000)
    card = dash.inner_text("[data-campaign-card=consumer-leads]")
    check("campaign card shows real counts and actions", "Consumer Leads" in card and "Health Insurance Lead Acquisition" in card
          and "Open Dashboard" in card and "View Customer Website" in card, card[:200])
    dash.screenshot(path=OUT + "/07-campaign-card.png", full_page=False)
    b.close()

st, det = call("GET", base + "/" + lead_id, token=mgr)
acts = [h["action"] + ("=" + h["new"] if h["new"] else "") for h in det["history"]]
check("assignment recorded (by name)", det["assigned_to"] == "Test Agent One" and "assigned=Test Agent One" in acts, acts)
check("status history kept (New -> Contacted -> Qualified -> Appointment Set)", det["status"] == "appointment_set"
      and "status=Contacted" in acts and "status=Qualified" in acts and "status=Appointment Set" in acts, acts)
check("call attempt, contact, appointment recorded", "call_attempted" in acts and "contact_completed" in acts
      and any(a.startswith("appointment_set") for a in acts))
check("appointment stored in UTC (2:30 PM CST = 20:30 UTC)", det["appointment_at"].startswith("2026-11-20T20:30:00"), det["appointment_at"])
check("note recorded in history", "note" in acts)
ev = sql("select string_agg(action||' '||to_char(created_at,'YYYY-MM-DD\"T\"HH24:MI:SS.MS'), ' | ' order by created_at) from volt_lead_events")
print("EVENTS (UTC):", ev)
for h in det["history"]:
    print("  history: %-18s %-40s UTC %s = %s" % (h["action"], (h["new"] or h["note"] or "")[:40], h["at"], ct(h["at"])))
check("every history event has a timestamp", all(h["at"] for h in det["history"]))
check("consent and creation timestamps present", bool(det["consent_at"] and det["created_at"]))
print("  consent_at UTC %s = %s; created_at %s" % (det["consent_at"], ct(det["consent_at"]), ct(det["created_at"])))

# 7. Access control.
st, mine = call("GET", base, token=agent)
check("assigned agent sees only their lead", st == 200 and mine["access"] == "assigned" and [l["id"] for l in mine["leads"]] == [lead_id])
st, _ = call("GET", base + "/" + lead_id, token=agent2)
check("another agent cannot open the lead (404)", st == 404, st)
st, l2 = call("GET", base, token=agent2)
check("another agent's list is empty", st == 200 and l2["leads"] == [])
st, _ = call("POST", base + "/" + lead_id + "/assign", {"user_id": ids["leads.agent2.test@example.test"]}, agent)
check("an agent without a grant cannot reassign (403)", st == 403, st)
st, _ = call("GET", base + "/" + lead_id, token=outsider)
check("another business's admin is refused (403)", st == 403, st)
st, _ = call("GET", base)
check("signed-out request refused (401)", st == 401, st)

# 8. Analytics.
st, rep = call("GET", base + "/report", token=mgr)
meta = next((c for c in rep["by_channel"] if c["channel"] == "meta"), {})
check("campaign analytics reflect the lead (channel, appointment, agent)", st == 200 and rep["total"] == 1
      and meta.get("leads") == 1 and meta.get("appointments") == 1 and rep["agents"][0]["assigned"] == 1, rep)
st, card = call("GET", base + "/summary", token=mgr)
check("campaign card API: counts only, no lead details", st == 200 and card["total"] == 1 and card["appointments"] == 1
      and not any(k in card for k in ("phone", "email", "leads")))

# 9. Rate limit (10 per minute per address).
codes = [call("POST", "/volt/public/volt-marketing/leads", {"first_name": "SYNTHETIC"})[0] for _ in range(12)]
check("rate limit engages on rapid submissions (429)", 429 in codes, codes)

# 10. Turn intake back OFF; confirm the public form is closed again.
st, s = call("PUT", base + "/settings", {"intake_enabled": False}, god)
st2, cfg2 = call("GET", "/volt/public/volt-marketing/leads/config")
check("intake turned back OFF after the test", st == 200 and not s["intake_enabled"] and cfg2["enabled"] is False)
audit = sql("select count(*) from volt_audit_events where entity_type in ('lead_settings','lead_grant')")
check("intake changes and grants are in the VOLT audit log", int(audit or 0) >= 3, audit)

ok = all(r[1] for r in results)
print("\nRESULT: %d/%d passed" % (sum(r[1] for r in results), len(results)))
json.dump({"ok": ok, "checks": results, "lead_id": lead_id, "history": det["history"]}, open(OUT + "/result.json", "w"), indent=1)
sys.exit(0 if ok else 1)
