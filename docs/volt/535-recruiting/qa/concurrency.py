import json, sys, threading, urllib.request, uuid
B = sys.argv[1]; S = "volt-marketing"
def call(m, p, body=None, tok=None):
    req = urllib.request.Request(B + p, data=json.dumps(body).encode() if body is not None else None, method=m)
    req.add_header("Content-Type", "application/json")
    if tok: req.add_header("Authorization", "Bearer " + tok)
    try:
        with urllib.request.urlopen(req, timeout=60) as r: return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read() or b"null")
def person(email):
    return {"first_name": "Concurrent", "last_name": "Tester", "email": email, "phone": "(555) 010-7777",
            "date_of_birth": "1991-07-04", "password": "Volt-Test-2026!", "project_key": "aca"}
# 1. Ten simultaneous registrations with ONE email -> exactly one account and serial.
email = "race-%s@example.test" % uuid.uuid4().hex[:6]
out = []
ts = [threading.Thread(target=lambda: out.append(call("POST", "/volt/public/%s/register" % S, person(email)))) for _ in range(10)]
[t.start() for t in ts]; [t.join() for t in ts]
codes = sorted(c for c, _ in out)
print("same-email registrations:", codes)
ok = [b for c, b in out if c == 200]
assert len(ok) == 1, "exactly one registration must succeed"
tok = ok[0]["access_token"]
# 2. Ten simultaneous applications to Fiber from two "tabs" -> exactly one application.
res = []
ts = [threading.Thread(target=lambda: res.append(call("POST", "/volt/rep/%s/applications" % S, {"project_key": "fiber"}, tok))) for _ in range(10)]
[t.start() for t in ts]; [t.join() for t in ts]
ids = {b["id"] for c, b in res if c == 200}
print("parallel fiber applications:", sorted(c for c, _ in res), "distinct ids:", len(ids))
assert len(ids) == 1
# 3. Twenty simultaneous fresh registrations -> twenty distinct serials.
out = []
ts = [threading.Thread(target=lambda i=i: out.append(call("POST", "/volt/public/%s/register" % S, person("serial-%d-%s@example.test" % (i, uuid.uuid4().hex[:5]))))) for i in range(20)]
[t.start() for t in ts]; [t.join() for t in ts]
serials = [b["rep"]["rep_id"] for c, b in out if c == 200]
print("parallel registrations:", len(serials), "distinct serials:", len(set(serials)))
assert len(serials) == 20 and len(set(serials)) == 20
me = call("GET", "/volt/rep/%s/me" % S, tok=tok)[1]
print("one person, applications:", [a["project"]["key"] for a in me["applications"]], "rep", me["profile"]["rep_code"])
print("PASS")
