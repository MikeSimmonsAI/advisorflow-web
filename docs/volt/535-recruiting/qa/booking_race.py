"""VOLT #535 - Zoom booking capacity under real concurrency (PostgreSQL).

usage: python3 -I booking_race.py <api> <staff_email> <staff_pw>
Creates synthetic @example.test applicants, opens their Zoom step, and fires
simultaneous booking requests. Prints PASS/FAIL lines and a JSON summary.
"""
import json, sys, threading, urllib.error, urllib.parse, urllib.request, uuid
from datetime import datetime, timedelta

API, STAFF, PW = sys.argv[1].rstrip("/"), sys.argv[2], sys.argv[3]
S = "volt-marketing"
R = []


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
    req = urllib.request.Request(API + path, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


st, lg = call("POST", "/auth/login", form={"username": STAFF, "password": PW})
staff = lg["access_token"]
org = None
st, ctx = call("GET", "/auth/my-contexts", tok=staff)
for c in (ctx.get("contexts") or ctx.get("program_contexts") or []) if isinstance(ctx, dict) else []:
    if c.get("type") == "volt_ops":
        org = c["organization_id"]
if org is None:
    org = sys.argv[4] if len(sys.argv) > 4 else None
home = call("GET", "/volt/ops/%s" % org, tok=staff)[1]
aca = [p for p in home["projects"] if p["key"] == "aca"][0]["id"]


def applicant(tag):
    st, out = call("POST", "/volt/public/%s/register" % S, {
        "first_name": "Race", "last_name": tag, "email": "race.%s.%s@example.test" % (tag.lower(), uuid.uuid4().hex[:6]),
        "phone": "(555) 010-%04d" % (abs(hash(tag)) % 10000), "date_of_birth": "1991-01-01", "password": "Volt-Race-2026!",
        "project_key": "aca"})
    tok, app = out["access_token"], out["application_id"]
    call("PUT", "/volt/rep/%s/hiring-profile" % S, {"city": "Dallas", "state": "TX", "zip_code": "75201",
         "earliest_start": "2026-12-01", "availability": {"types": ["part_time"], "notes": ""}, "work_history": [],
         "no_experience": True}, tok=tok)
    call("PUT", "/volt/rep/%s/applications/%s/answers" % (S, app), {"answers": {
        "outreach_experience": "no", "comfortable_explaining": "learn", "languages": "English"}}, tok=tok)
    st, sub = call("POST", "/volt/rep/%s/applications/%s/submit" % (S, app), tok=tok)
    if st != 200:
        # required project documents: upload a tiny PDF per requirement
        for m in (sub.get("detail") or {}).get("missing", []):
            pass
    st, sch = call("POST", "/volt/ops/%s/applications/%s/scheduling" % (org, app), {"stage": "interview"}, tok=staff)
    return tok, app, st


def session(cap, days):
    start = (datetime.utcnow() + timedelta(days=days)).strftime("%Y-%m-%dT10:00")
    st, s = call("POST", "/volt/ops/%s/sessions" % org, {"kind": "interview", "project_id": aca, "starts_local": start,
                 "timezone": "America/Chicago", "duration_minutes": 30, "join_url": "https://zoom.us/j/81234567890",
                 "capacity": cap, "booking_open": True, "title": "Race test %d" % cap}, tok=staff)
    assert st == 200, s
    return s["id"]


def burst(pairs, sid):
    results = [None] * len(pairs)
    gate = threading.Barrier(len(pairs))

    def go(i, tok, app):
        gate.wait()
        results[i] = call("POST", "/volt/rep/%s/applications/%s/zoom-booking" % (S, app), {"session_id": sid}, tok=tok)
    ts = [threading.Thread(target=go, args=(i, t, a)) for i, (t, a) in enumerate(pairs)]
    [t.start() for t in ts]; [t.join() for t in ts]
    return results


people = [applicant("P%02d" % i) for i in range(12)]
check("12 synthetic applicants made eligible", all(p[2] == 200 for p in people), [p[2] for p in people])
pairs = [(p[0], p[1]) for p in people]

s1 = session(1, 20)
res = burst(pairs[:8], s1)
ok = [r for r in res if r[0] == 200]
bad = [r for r in res if r[0] == 409 and (r[1].get("detail") or {}).get("code") == "slot_unavailable"]
check("capacity 1: 8 simultaneous requests -> exactly 1 booking", len(ok) == 1 and len(bad) == 7, [r[0] for r in res])

s3 = session(3, 21)
rest = pairs[8:] + [p for p, r in zip(pairs[:8], res) if r[0] != 200]
res3 = burst(rest[:10], s3)
ok3 = [r for r in res3 if r[0] == 200]
check("capacity 3: 10 simultaneous requests -> exactly 3 bookings", len(ok3) == 3, [r[0] for r in res3])

# Same application, 6 parallel duplicate requests -> one booking, no error.
spare = applicant("Dup")
s5 = session(5, 22)
res5 = burst([(spare[0], spare[1])] * 6, s5)
codes = sorted(r[0] for r in res5)
created = sum(1 for r in res5 if r[0] == 200 and r[1].get("created"))
check("duplicate requests from one application -> one booking (all 200, one created)", codes == [200] * 6 and created == 1, [(r[0], r[1].get("created") if r[0] == 200 else r[1]) for r in res5])

sessions = {x["id"]: x for x in call("GET", "/volt/ops/%s/sessions" % org, tok=staff)[1]["items"]}
check("server counts: 1/1, 3/3, 1/5", (sessions[s1]["booked"], sessions[s3]["booked"], sessions[s5]["booked"]) == (1, 3, 1),
      (sessions[s1]["booked"], sessions[s3]["booked"], sessions[s5]["booked"]))
check("full sessions show Full", sessions[s1]["availability"] == "full" and sessions[s3]["availability"] == "full")
print(json.dumps({"passed": sum(R), "failed": len(R) - sum(R), "sessions": [s1, s3, s5]}))
