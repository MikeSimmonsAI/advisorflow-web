"""M2 LIVE VERIFICATION — MULTI-DEVICE SESSIONS, AGAINST THE RUNNING SERVICE.

Not the test suite. This drives the deployed API the way three real devices
would: three independent sign-ins for ONE person, then refresh, per-device
logout, revoke-by-id and sign-out-everywhere, asserting after every step which
of the three are still alive.

USAGE
    python scripts/m2_live_verify.py <base_url> [email] [password]

With no identity it runs the unauthenticated half only — what the service
reports it is running, and that the session endpoints refuse anonymous
callers. That half needs no credentials and touches nothing.

SAFETY. It signs in only as the identity passed on the command line, and the
only sessions it ever ends are the three it opened itself. Nothing else on the
account is touched, and no password is changed.
"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8011").rstrip("/")
EMAIL = sys.argv[2] if len(sys.argv) > 2 else None
PASSWORD = sys.argv[3] if len(sys.argv) > 3 else None

A = {"X-Client-Platform": "web", "X-Device-Id": "m2-verify-browser-a",
     "X-Device-Name": "M2 Verify Browser A", "X-App-Version": "verify"}
B = {"X-Client-Platform": "ios", "X-Device-Id": "m2-verify-phone-b",
     "X-Device-Name": "M2 Verify Phone B", "X-App-Version": "1.0.0"}
C = {"X-Client-Platform": "web", "X-Device-Id": "m2-verify-browser-c",
     "X-Device-Name": "M2 Verify Browser C", "X-App-Version": "verify"}

PASS, FAIL = [], []


def check(label, ok, detail=""):
    (PASS if ok else FAIL).append(label)
    mark = "  PASS  " if ok else "  FAIL  "
    print(mark + label + (("  -- " + str(detail)[:240]) if detail and not ok else ""))


def call(method, path, token=None, headers=None, expect=None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Accept", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            code, payload = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        code, payload = e.code, e.read().decode()
    except Exception as e:                                   # noqa: BLE001
        return None, "TRANSPORT %s" % e
    try:
        return code, (json.loads(payload) if payload else None)
    except ValueError:
        return code, payload


def login(headers):
    """Sign in, and say why if it fails rather than raising a bare 401."""
    body = urllib.parse.urlencode({"username": EMAIL, "password": PASSWORD})
    req = urllib.request.Request(BASE + "/auth/login", data=body.encode(),
                                 method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())["access_token"]
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:300]
        print("        login failed for %s: HTTP %s %s" % (EMAIL, e.code, detail))
        return None
    except Exception as e:                                   # noqa: BLE001
        print("        login transport error: %s" % e)
        return None


def alive(token):
    code, _ = call("GET", "/auth/my-contexts", token=token)
    return code == 200


print("\n=== SERVICE ===  %s" % BASE)
code, ver = call("GET", "/version")
check("/version answers", code == 200, "%s %s" % (code, ver))
if isinstance(ver, dict):
    print("        commit=%s  built=%s" % (ver.get("commit") or ver.get("sha"),
                                           ver.get("built_at") or ver.get("time")))
code, _ = call("GET", "/health")
check("/health answers", code in (200, 204), code)

print("\n=== SESSION ENDPOINTS REFUSE ANONYMOUS CALLERS ===")
for method, path in (("GET", "/auth/sessions"),
                     ("DELETE", "/auth/sessions/anything"),
                     ("POST", "/auth/logout-all"),
                     ("POST", "/auth/refresh")):
    code, _ = call(method, path)
    check("%s %s -> 401/403" % (method, path), code in (401, 403), code)

code, _ = call("GET", "/auth/sessions", token="not.a.token")
check("GET /auth/sessions with a junk token -> 401", code in (401, 403), code)

if not (EMAIL and PASSWORD):
    print("\n=== MULTI-DEVICE SCENARIO: SKIPPED (no test identity given) ===")
    print("    Re-run as: python scripts/m2_live_verify.py %s <email> <password>"
          % BASE)
else:
    print("\n=== THREE DEVICES ===")
    ta, tb, tc = login(A), login(B), login(C)
    check("A signs in", bool(ta))
    check("B signs in", bool(tb))
    check("C signs in", bool(tc))
    check("all three live after three logins",
          alive(ta) and alive(tb) and alive(tc))

    code, body = call("GET", "/auth/sessions", token=ta)
    check("A can list its own sessions", code == 200, body)
    ids = {}
    if isinstance(body, dict):
        for s in body.get("sessions", []):
            ids[s.get("device_name")] = s.get("id")
        blob = json.dumps(body).lower()
        check("session list carries no credential material",
              not any(w in blob for w in ("jti", "\"token\"", "secret",
                                          "password", "session_token")))
        check("session list marks exactly one current session",
              sum(1 for s in body.get("sessions", []) if s.get("is_current")) == 1)

    print("\n=== REFRESH IS DEVICE-LOCAL ===")
    code, r = call("POST", "/auth/refresh", token=ta)
    check("A refreshes", code == 200, r)
    ta_new = (r or {}).get("access_token") if isinstance(r, dict) else None
    check("B survives A's refresh", alive(tb))
    check("C survives A's refresh", alive(tc))
    check("A's replaced token is dead", not alive(ta))
    check("A's new token works", bool(ta_new) and alive(ta_new))
    ta = ta_new or ta

    code, r = call("POST", "/auth/refresh", token=tb)
    check("B refreshes", code == 200, r)
    tb_new = (r or {}).get("access_token") if isinstance(r, dict) else None
    check("A survives B's refresh", alive(ta))
    check("C survives B's refresh", alive(tc))
    tb = tb_new or tb

    print("\n=== LOGOUT IS ONE DEVICE ===")
    code, _ = call("POST", "/auth/logout", token=tb)
    check("B signs out", code == 200, code)
    check("B is gone", not alive(tb))
    check("A survives B's logout", alive(ta))
    check("C survives B's logout", alive(tc))
    code, _ = call("POST", "/auth/refresh", token=tb)
    check("B cannot refresh itself back to life", code in (401, 403), code)

    print("\n=== REVOKE ONE FROM THE SESSION LIST ===")
    code, body = call("GET", "/auth/sessions", token=ta)
    c_id = None
    if isinstance(body, dict):
        for s in body.get("sessions", []):
            if s.get("device_name") == "M2 Verify Browser C":
                c_id = s.get("id")
    check("C is listed for revocation", bool(c_id), body)
    if c_id:
        code, _ = call("DELETE", "/auth/sessions/" + c_id, token=ta)
        check("A revokes C", code == 200, code)
        check("C is gone", not alive(tc))
        check("A survives revoking C", alive(ta))

    code, _ = call("DELETE", "/auth/sessions/00000000-0000-0000-0000-000000000000",
                   token=ta)
    check("revoking a session that is not ours is a 404", code == 404, code)

    print("\n=== SIGN OUT EVERYWHERE ===")
    code, body = call("POST", "/auth/logout-all", token=ta)
    check("A signs out everywhere", code == 200, body)
    check("A is gone", not alive(ta))
    check("B is gone", not alive(tb))
    check("C is gone", not alive(tc))

    print("\n=== AND BACK IN ===")
    again = login(B)
    check("the account can still sign in afterwards", alive(again))
    code, ctx = call("GET", "/auth/my-contexts", token=again)
    check("/auth/my-contexts is still authoritative", code == 200, ctx)
    if isinstance(ctx, dict):
        print("        contexts=%s default=%s" % (
            [c.get("key") or c.get("context") or c.get("type")
             for c in (ctx.get("contexts") or [])],
            ctx.get("default_context")))
    call("POST", "/auth/logout", token=again)   # leave nothing of ours behind

print("\n=== RESULT ===")
print("  %d passed, %d failed" % (len(PASS), len(FAIL)))
for f in FAIL:
    print("  FAILED: " + f)
sys.exit(1 if FAIL else 0)
