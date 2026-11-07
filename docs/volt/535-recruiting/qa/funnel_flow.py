"""VOLT #535 (2026-11-06 night) - My Application, Zoom booking, People & Access in a browser.

usage: python3 -I funnel_flow.py <frontend> <api> <shots_dir> <staff_email> <staff_pw> <org_id>
Synthetic @example.test people only. Desktop and phone. Prints PASS/FAIL.
"""
import json, os, sys, urllib.error, urllib.parse, urllib.request, uuid
from datetime import datetime, timedelta, timezone
from playwright.sync_api import sync_playwright

FE, API, SHOTS, STAFF, STAFF_PW, ORG = [a.rstrip("/") for a in sys.argv[1:3]] + sys.argv[3:7]
S = "volt-marketing"
R = []
os.makedirs(SHOTS, exist_ok=True)


def check(label, ok, detail=""):
    R.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + label + ("" if ok else "  -- %s" % str(detail)[:300]), flush=True)


def call(method, path, body=None, tok=None, form=None):
    data, headers = None, {}
    if form is not None:
        data = urllib.parse.urlencode(form).encode(); headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode(); headers["Content-Type"] = "application/json"
    if tok:
        headers["Authorization"] = "Bearer " + tok
    try:
        with urllib.request.urlopen(urllib.request.Request(API + path, method=method, data=data, headers=headers), timeout=60) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


staff = call("POST", "/auth/login", form={"username": STAFF, "password": STAFF_PW})[1]["access_token"]
home = call("GET", "/volt/ops/%s" % ORG, tok=staff)[1]
aca = [p for p in home["projects"] if p["key"] == "aca"][0]["id"]
PW = "Volt-Funnel-2026!"


def applicant(first, last):
    email = "funnel.%s.%s@example.test" % (last.lower(), uuid.uuid4().hex[:6])
    st, out = call("POST", "/volt/public/%s/register" % S, {"first_name": first, "last_name": last, "email": email,
                   "phone": "(555) 010-2222", "date_of_birth": "1993-03-03", "password": PW, "project_key": "aca"})
    tok, app = out["access_token"], out["application_id"]
    call("PUT", "/volt/rep/%s/hiring-profile" % S, {"city": "Dallas", "state": "TX", "zip_code": "75201", "earliest_start": "2026-12-01",
         "availability": {"types": ["part_time"], "notes": ""}, "work_history": [], "no_experience": True}, tok=tok)
    call("PUT", "/volt/rep/%s/applications/%s/answers" % (S, app), {"answers": {"outreach_experience": "some",
         "comfortable_explaining": "yes", "languages": "English, Spanish"}}, tok=tok)
    call("POST", "/volt/rep/%s/applications/%s/submit" % (S, app), tok=tok)
    return email, tok, app


def session(cap, days, kind="interview"):
    start = (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%dT10:00")
    st, s = call("POST", "/volt/ops/%s/sessions" % ORG, {"kind": kind, "project_id": aca, "starts_local": start, "timezone": "America/Chicago",
                 "duration_minutes": 30, "join_url": "https://zoom.us/j/81234567891", "capacity": cap, "booking_open": True,
                 "title": "Browser QA %s" % kind}, tok=staff)
    assert st == 200, s
    return s


def login(page, email, pw=PW):
    page.goto(FE + "/volt/sign-in"); page.fill("#v-si-email", email); page.fill("#v-si-pass", pw)
    page.locator("button[type=submit]").click(); page.wait_for_url("**/volt/me**", timeout=20000)


def no_overflow(page):
    return page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")


with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    e1, t1, a1 = applicant("Jordan", "Taylor")
    # A second application (Fiber, draft) to test the switcher.
    fib = call("POST", "/volt/rep/%s/applications" % S, {"project_key": "fiber"}, tok=t1)[1]
    slot1 = session(1, 9)
    slot_group = session(3, 10)

    for name, ctx_args in (("desktop", {"viewport": {"width": 1366, "height": 900}}), ("phone", dict(p.devices["iPhone 13"]))):
        pg = b.new_context(**ctx_args).new_page()
        login(pg, e1)
        pg.goto(FE + "/volt/me?application=" + a1); pg.wait_for_selector("[data-my-application]")
        body = pg.inner_text("main")
        check(f"{name}: My Application, not a rep dashboard", "Your next step starts here" in body and "Rep dashboard" not in body
              and "My referrals" not in body and "leaderboard" not in body.lower() and "Referral" not in body, body[:200])
        check(f"{name}: shows the application reference, not a REP id", pg.inner_text("[data-reference]").startswith("VOLT-") and "REP-" not in body)
        check(f"{name}: waiting Zoom message until staff open the step", pg.locator("[data-zoom=waiting]").count() == 1)
        check(f"{name}: no horizontal overflow", no_overflow(pg))
        pg.screenshot(path=f"{SHOTS}/{name}-01-my-application-waiting.png", full_page=True)
        if name == "desktop":
            # Switch to the Fiber application; status is its own.
            pg.select_option("[data-app-switch]", fib["id"]); pg.wait_for_selector(f"[data-my-application='{fib['id']}']")
            check("switcher: Fiber application shows its own status (draft) and next action",
                  "Continue application" in pg.inner_text("[data-next-action]") and pg.locator("[data-progress=draft]").count() == 1)
            pg.select_option("[data-app-switch]", a1); pg.wait_for_selector(f"[data-my-application='{a1}']")
        pg.context.close()

    # Staff opens the Zoom step in the application file (browser).
    s = b.new_context(viewport={"width": 1440, "height": 900}).new_page()
    s.goto(FE + "/login"); s.fill("input[type=email]", STAFF); s.fill("input[type=password]", STAFF_PW)
    s.locator("button[type=submit]").first.click(); s.wait_for_timeout(2500)
    s.goto(FE + "/volt-ops/%s/review?include_test=1&reached=submitted" % ORG); s.wait_for_selector("[data-table=queue]")
    s.wait_for_timeout(800)
    s.locator("[data-table=queue] tr", has_text="Jordan Taylor").first.locator("text=Open").click()
    s.wait_for_selector("[data-zoom-step]")
    s.select_option("[data-stage-select]", "interview"); s.locator("[data-save-stage]").click()
    s.wait_for_selector("[data-zoom-step] >> text=Can choose a time", timeout=15000)
    check("staff: Zoom step opened from the application file", True)
    s.screenshot(path=f"{SHOTS}/desktop-02-staff-zoom-step.png", full_page=True)
    s.keyboard.press("Escape"); s.wait_for_timeout(300)
    check("staff: Escape closes the application file", s.locator(".v-drawer").count() == 0)

    # Applicant books on a phone: picker closes only after server success.
    ph = b.new_context(**p.devices["iPhone 13"]).new_page()
    login(ph, e1)
    ph.goto(FE + "/volt/me?application=" + a1); ph.wait_for_selector("[data-zoom=choose]")
    check("phone: 'Choose a Zoom date' shown once eligible", "Choose your Zoom date" in ph.inner_text("[data-zoom=choose]"))
    ph.locator("[data-open-picker]").click(); ph.wait_for_selector("[data-dialog=zoom-picker] [data-time]")
    dlg = ph.inner_text("[data-dialog=zoom-picker]")
    check("picker lists date, time, time zone, duration and spots", "America/Chicago" in dlg and "30 minutes" in dlg and "spot" in dlg, dlg[:300])
    check("picker shows no join link", "zoom.us" not in ph.content().split("data-dialog=\"zoom-picker\"")[1] if "data-dialog=\"zoom-picker\"" in ph.content() else True)
    box = ph.locator("[data-dialog=zoom-picker]").bounding_box()
    check("phone: picker fits the viewport", box and box["width"] <= 390 + 1)
    ph.screenshot(path=f"{SHOTS}/phone-03-zoom-picker.png")
    # Stale picker: someone else takes the capacity-1 slot first.
    e2, t2, a2 = applicant("Avery", "Brooks")
    call("POST", "/volt/ops/%s/applications/%s/scheduling" % (ORG, a2), {"stage": "interview"}, tok=staff)
    st, _ = call("POST", "/volt/rep/%s/applications/%s/zoom-booking" % (S, a2), {"session_id": slot1["id"]}, tok=t2)
    check("another applicant took the capacity-1 slot", st == 200)
    ph.locator(f"[data-choose='{slot1['id']}']").click()
    ph.wait_for_selector("[data-zoom-error]", timeout=15000)
    check("failure: picker stays open with a clear message", ph.locator("[data-dialog=zoom-picker]").count() == 1
          and "no longer" in ph.inner_text("[data-zoom-error]").lower() or "filled" in ph.inner_text("[data-zoom-error]").lower(),
          ph.inner_text("[data-zoom-error]"))
    ph.wait_for_timeout(600)
    check("failure: options refreshed (full slot gone)", ph.locator(f"[data-choose='{slot1['id']}']").count() == 0)
    ph.screenshot(path=f"{SHOTS}/phone-04-picker-slot-gone.png")
    ph.locator(f"[data-choose='{slot_group['id']}']").click()
    ph.wait_for_selector("[data-zoom=booked]", timeout=15000)
    check("success: picker closed automatically", ph.locator("[data-dialog=zoom-picker]").count() == 0)
    zb = ph.inner_text("[data-zoom=booked]")
    check("success: 'Zoom scheduled' with the details, application stays active",
          "Zoom scheduled" in zb and "America/Chicago" in zb and "application stays active" in zb, zb[:300])
    check("success: Join button for the booked attendee", ph.locator("[data-join]").count() == 1)
    check("status tag reads Zoom scheduled", "zoom scheduled" in ph.inner_text("[data-status-tag]").lower())
    check("phone: no horizontal overflow after booking", no_overflow(ph))
    ph.screenshot(path=f"{SHOTS}/phone-05-zoom-scheduled.png", full_page=True)
    # Cancel / choose again releases the seat.
    ph.locator("[data-cancel]").click(); ph.locator("[data-cancel-confirm]").click()
    ph.wait_for_selector("[data-zoom=choose]", timeout=15000)
    sess = {x["id"]: x for x in call("GET", "/volt/ops/%s/sessions" % ORG, tok=staff)[1]["items"]}
    check("applicant cancel released the seat", sess[slot_group["id"]]["booked"] == 0)
    ph.locator("[data-open-picker]").click(); ph.wait_for_selector(f"[data-choose='{slot_group['id']}']")
    ph.locator(f"[data-choose='{slot_group['id']}']").click(); ph.wait_for_selector("[data-zoom=booked]", timeout=15000)

    # Desktop booked view.
    dk = b.new_context(viewport={"width": 1366, "height": 900}).new_page()
    login(dk, e1); dk.goto(FE + "/volt/me?application=" + a1); dk.wait_for_selector("[data-zoom=booked]")
    dk.screenshot(path=f"{SHOTS}/desktop-06-my-application-booked.png", full_page=True)

    # Staff: Zoom availability screen.
    s.goto(FE + "/volt-ops/%s/sessions" % ORG); s.wait_for_selector("[data-table=sessions]"); s.wait_for_timeout(500)
    row1 = s.locator(f"[data-session='{slot1['id']}']")
    check("staff: capacity-1 slot shows 1 / 1 and Full", "1 / 1" in row1.inner_text() and "full" in row1.inner_text().lower(), row1.inner_text())
    rowg = s.locator(f"[data-session='{slot_group['id']}']")
    check("staff: group slot shows 1 / 3 and 2 left", "1 / 3" in rowg.inner_text() and "2 left" in rowg.inner_text(), rowg.inner_text())
    s.screenshot(path=f"{SHOTS}/desktop-07-zoom-availability.png", full_page=True)
    s.locator(f"[data-toggle='{slot_group['id']}']").click(); s.wait_for_selector(f"[data-session='{slot_group['id']}'] [data-availability=closed]", timeout=10000)
    check("staff: Close availability keeps the booking", "1 / 3" in s.locator(f"[data-session='{slot_group['id']}']").inner_text())
    s.locator(f"[data-toggle='{slot_group['id']}']").click(); s.wait_for_selector(f"[data-session='{slot_group['id']}'] [data-availability=open]", timeout=10000)
    s.locator("[data-new-session]").click(); s.wait_for_selector("[data-form=session]")
    s.fill("[data-form=session] input[type=url]", "https://zoom.us/s/81234567891?zak=host"); s.fill("[data-form=session] input[type=datetime-local]",
         (datetime.now(timezone.utc) + timedelta(days=15)).strftime("%Y-%m-%dT10:00"))
    s.locator("[data-form=session] button[type=submit]").click(); s.wait_for_timeout(1200)
    check("staff: host/start link refused in the form", "HOST start link" in s.inner_text("[data-form=session]"))
    s.locator("[data-form=session] button", has_text="Cancel").click()

    # People & Access (desktop + phone).
    s.goto(FE + "/volt-ops/%s/people" % ORG); s.wait_for_selector("[data-table=people]")
    s.locator("label", has_text="Include test records").locator("input").check(); s.wait_for_timeout(900)
    s.fill("[data-people-search]", "Brooks"); s.wait_for_timeout(900)
    rows = s.locator("[data-table=people] tbody tr")
    check("people: search finds the applicant", rows.count() >= 1 and "Avery Brooks" in rows.first.inner_text())
    s.screenshot(path=f"{SHOTS}/desktop-08-people.png", full_page=True)
    rows.first.locator("[data-manage]").click(); s.wait_for_selector("[data-person-drawer]")
    s.locator("[data-act=suspend]").click(); s.wait_for_selector("[data-dialog=confirm]")
    cb = s.inner_text("[data-confirm-body]")
    check("confirm shows exact target, scope and consequence", "Avery Brooks" in cb and "VOLT Marketing" in cb and "sessions" in cb, cb)
    s.fill("#v-confirm-reason", "Browser QA suspend")
    s.screenshot(path=f"{SHOTS}/desktop-09-confirm-suspend.png")
    s.locator("[data-confirm-go]").click(); s.wait_for_function("document.querySelector('[data-access-state]') && /suspended/i.test(document.querySelector('[data-access-state]').innerText)", timeout=10000)
    check("drawer shows Suspended", True)
    s.screenshot(path=f"{SHOTS}/desktop-10-drawer-suspended.png", full_page=True)
    st, _ = call("GET", "/volt/rep/%s/me" % S, tok=t2)
    check("suspended applicant's existing session is refused", st in (401, 403), st)
    st, _ = call("POST", "/volt/public/%s/login" % S, {"email": e2, "password": PW})
    check("suspended applicant cannot sign in", st == 403, st)
    s.locator("[data-act=restore]").click(); s.locator("[data-confirm-go]").click()
    s.wait_for_function("/active/i.test(document.querySelector('[data-access-state]').innerText)", timeout=10000)
    st, _ = call("GET", "/volt/rep/%s/me" % S, tok=t2)
    check("restore: old session stays refused", st == 401, st)
    st, li = call("POST", "/volt/public/%s/login" % S, {"email": e2, "password": PW})
    check("restore: a fresh sign-in works", st == 200)
    s.locator("[data-act=archive]").click(); s.locator("[data-confirm-go]").click(); s.wait_for_timeout(1200)
    st, _ = call("GET", "/volt/rep/%s/me" % S, tok=li["access_token"])
    check("archive does not change sign-in", st == 200, st)
    s.locator("[data-act=unarchive]").click(); s.locator("[data-confirm-go]").click(); s.wait_for_timeout(800)
    s.keyboard.press("Escape"); s.wait_for_timeout(300)
    check("Escape closes the access drawer", s.locator("[data-person-drawer]").count() == 0)
    hist = s.inner_text("[data-access-history]")
    check("access history lists the changes with the reason", "suspend" in hist and "Browser QA suspend" in hist, hist[:300])
    pp = b.new_context(**p.devices["iPhone 13"]).new_page()
    pp.goto(FE + "/login"); pp.fill("input[type=email]", STAFF); pp.fill("input[type=password]", STAFF_PW)
    pp.locator("button[type=submit]").first.click(); pp.wait_for_timeout(2500)
    pp.goto(FE + "/volt-ops/%s/people" % ORG); pp.wait_for_selector("[data-table=people]"); pp.wait_for_timeout(800)
    check("phone: People & Access has no horizontal overflow", no_overflow(pp))
    pp.screenshot(path=f"{SHOTS}/phone-11-people.png", full_page=True)
    pp.locator("[data-manage]").first.click(); pp.wait_for_selector("[data-person-drawer]")
    check("phone: drawer fits the viewport", pp.locator("[data-person-drawer]").bounding_box()["width"] <= 391)
    pp.screenshot(path=f"{SHOTS}/phone-12-people-drawer.png")
    pp.goto(FE + "/volt-ops/%s/sessions" % ORG); pp.wait_for_selector("[data-table=sessions]"); pp.wait_for_timeout(500)
    check("phone: Zoom availability has no horizontal overflow", no_overflow(pp))
    pp.screenshot(path=f"{SHOTS}/phone-13-zoom-availability.png", full_page=True)

    # Applicant cannot reach staff screens.
    st, _ = call("GET", "/volt/ops/%s/people" % ORG, tok=t1)
    check("applicant token cannot reach People & Access", st in (401, 403), st)
    # Old referral link: ordinary signup, no picker.
    rp = b.new_context(viewport={"width": 1366, "height": 900}).new_page()
    rp.goto(FE + "/volt?ref=REP-1050&project=aca"); rp.wait_for_url("**/volt/apply/aca", timeout=15000)
    check("old referral link opens ordinary signup with no referral field",
          rp.locator("#v-ref").count() == 0 and "referred" not in rp.inner_text("main").lower())
    rp.screenshot(path=f"{SHOTS}/desktop-14-old-ref-link-signup.png", full_page=True)
    print(json.dumps({"passed": sum(R), "failed": len(R) - sum(R)}))
