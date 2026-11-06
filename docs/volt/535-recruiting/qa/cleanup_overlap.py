"""Local PostgreSQL check of the cleanup lock: the 2026-11-05 production case."""
import json, subprocess, sys, threading, time, urllib.request, uuid, zlib
API = "http://127.0.0.1:8011"; S = "volt-marketing"
def call(m, p, b=None, t=None, form=False):
    data = None; h = {}
    if form: data = b.encode(); h["Content-Type"] = "application/x-www-form-urlencoded"
    elif b is not None: data = json.dumps(b).encode(); h["Content-Type"] = "application/json"
    if t: h["Authorization"] = "Bearer " + t
    r = urllib.request.Request(API + p, data=data, method=m, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=120) as x: return x.status, json.loads(x.read() or b"null")
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read() or b"null")
c, b = call("POST", "/auth/login", "username=god.local%40example.test&password=LocalPass%212026", form=True)
god = b["access_token"]
org = [v for v in call("GET", "/auth/my-contexts", t=god)[1]["volt_contexts"]][0]["organization_id"]
# start from zero, then seed a known set
print("pre-clean", call("POST", "/volt/ops/%s/test-records/purge" % org, t=god))
for i in range(3):
    c, r = call("POST", "/volt/public/%s/register" % S, {"first_name": "Lock", "last_name": "Test%d" % i,
        "email": "lock-%d-%s@example.test" % (i, uuid.uuid4().hex[:5]), "phone": "5550100%03d" % i,
        "date_of_birth": "1990-01-01", "password": "Volt-Test-2026!", "project_key": "aca"})
    assert c == 200, r
key = zlib.crc32(("volt-test-cleanup:" + org).encode()) - (1 << 31)
# 1. A first cleanup is still running (it holds the tenant lock in an open transaction).
holder = subprocess.Popen(["psql", "-h", "/tmp", "-p", "5433", "-U", "postgres", "volt_local", "-c",
                           "BEGIN; SELECT pg_advisory_xact_lock(%d); SELECT pg_sleep(6); COMMIT;" % key],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1.5)
t0 = time.time(); c, r = call("POST", "/volt/ops/%s/test-records/purge" % org, t=god)
print("second request while first runs:", c, r, "in %.2fs" % (time.time() - t0))
assert c == 409 and r["detail"]["code"] == "cleanup_running"
holder.wait()
# 2. Once the first is done, a run reports exactly what it removed.
c, r = call("POST", "/volt/ops/%s/test-records/purge" % org, t=god); print("next run:", c, r)
assert c == 200 and r["reps"] == 3 and r["applications"] == 3 and r["logins"] == 3
# 3. Repeat: zeros.
c, r = call("POST", "/volt/ops/%s/test-records/purge" % org, t=god); print("repeat:", c, r)
assert c == 200 and all(v == 0 for v in r.values())
# 4. Two clicks fired at once on fresh data: exactly one does the work, the other is refused or reports zeros.
for i in range(2):
    call("POST", "/volt/public/%s/register" % S, {"first_name": "Dbl", "last_name": "Click%d" % i,
        "email": "dbl-%d-%s@example.test" % (i, uuid.uuid4().hex[:5]), "phone": "5550101%03d" % i,
        "date_of_birth": "1990-01-01", "password": "Volt-Test-2026!", "project_key": "fiber"})
out = []
ts = [threading.Thread(target=lambda: out.append(call("POST", "/volt/ops/%s/test-records/purge" % org, t=god))) for _ in range(2)]
[t.start() for t in ts]; [t.join() for t in ts]
print("simultaneous:", out)
done = [r for c, r in out if c == 200 and r["reps"]]
assert len(done) == 1 and done[0]["reps"] == 2
assert all((c == 409) or (c == 200 and (r["reps"] == 2 or r["reps"] == 0)) for c, r in out)
print("PASS")
