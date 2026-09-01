"""GATE 34 - THE BROWSER CAN ACTUALLY SEND WHAT THE CLIENT SENDS.

The workspace work added X-Workspace-Id to every request the frontend makes and
did not add it to CORSMiddleware's allow_headers. Starlette answers a preflight
naming an unlisted header with 400 "Disallowed CORS headers", so the browser
refused to send the request at all:

    Response to preflight request doesn't pass access control check:
    It does not have HTTP ok status.
    net::ERR_FAILED

The server never saw those requests, never refused them, and logged nothing.
The page saw every call reject, and a guard written as `.catch(() => denied)`
told a correctly authorized advisor he had no access to his own workspace.

This gate is the one that would have caught it on the day it was introduced,
and it is written to catch the NEXT one the same way:

 1. IT READS THE HEADERS OUT OF THE FRONTEND CLIENT. Every custom header
    frontend/src/api/client.js attaches must appear in allow_headers. That is
    the assertion the incident actually needed - not a hardcoded list here,
    which would have to be updated by the same person who forgot the last one.

 2. IT SENDS REAL PREFLIGHTS. Every configured production origin x the real
    header set x the routes that matter, asserting 200 and the echoed origin.

 3. AUTH MUST NOT EAT THE PREFLIGHT. An OPTIONS carries no credentials by
    design; a preflight that needs a token can never succeed.

 4. IT IS STILL A BOUNDARY. An unlisted origin is refused, and no wildcard.
"""
import io
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.environ.setdefault("DATABASE_URL", "sqlite:///./_gate34.db")

FAIL, PASSED = [], []


def check(label, ok, detail=""):
    print("  %s %s%s" % ("ok   " if ok else "FAIL ", label,
                         ("\n          -> " + str(detail)[:220]) if detail else ""))
    (PASSED if ok else FAIL).append(label)


def frontend_headers():
    """Every custom header the API client attaches, read from its source."""
    path = os.path.join(REPO, "frontend", "src", "api", "client.js")
    src = io.open(path, encoding="utf-8", errors="replace").read()
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"^\s*//.*$", "", src, flags=re.M)
    found = set()
    for m in re.finditer(r"headers\[\s*['\"]([A-Za-z0-9\-]+)['\"]\s*\]\s*=", src):
        found.add(m.group(1))
    for m in re.finditer(r"['\"]([Xx]-[A-Za-z0-9\-]+)['\"]\s*:", src):
        found.add(m.group(1))
    return sorted(found)


PRODUCTION_ORIGINS = [
    "https://app.evosyspro.live",
    "https://app.bookaboost.live",
]

PREFLIGHTED_ROUTES = [
    ("/auth/workspace/01fed629-9a95-412b-895a-c8b09f37a98c", "GET"),
    ("/auth/my-contexts", "GET"),
    ("/leads/", "GET"),
    ("/leads/status-funnel", "GET"),
    ("/sms/replies", "GET"),
    ("/settings/profile", "GET"),
]


def main():
    print("=" * 78)
    print("GATE 34 - CORS PREFLIGHT: THE BROWSER CAN SEND WHAT THE CLIENT SENDS")
    print("=" * 78)

    from fastapi.testclient import TestClient
    from app.main import app, ALLOWED_ORIGINS, BROWSER_HEADERS

    client = TestClient(app)
    allowed_lower = set(h.lower() for h in BROWSER_HEADERS)

    # ── 1. the frontend's own headers ───────────────────────────────────────
    print("\n--- every header the API client attaches is allowed ---")
    fe = frontend_headers()
    check("the client's headers were found in source", bool(fe), ", ".join(fe))
    for h in fe:
        check("client sends %-18s -> allowed" % h, h.lower() in allowed_lower,
              "" if h.lower() in allowed_lower
              else "MISSING from CORSMiddleware allow_headers - "
                   "every browser request carrying it will be blocked before it is sent")
    check("the workspace header specifically is allowed",
          "x-workspace-id" in allowed_lower,
          "this is the exact header whose absence produced the incident")

    # The server must read the header under the same name the browser may send.
    from app.services import workspace_access
    check("the header the server READS is the header CORS allows",
          workspace_access.WORKSPACE_HEADER.lower() in allowed_lower,
          workspace_access.WORKSPACE_HEADER)

    # ── 2. real preflights ──────────────────────────────────────────────────
    print("\n--- real OPTIONS preflights, production origins, real headers ---")
    request_headers = ",".join(sorted(h.lower() for h in fe)) or "authorization"
    for origin in PRODUCTION_ORIGINS:
        for path, method in PREFLIGHTED_ROUTES:
            r = client.options(path, headers={
                "Origin": origin,
                "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": request_headers,
            })
            ok = (r.status_code == 200 and
                  r.headers.get("access-control-allow-origin") == origin)
            check("OPTIONS %-52s %s" % (path, origin.split("//")[1]), ok,
                  "" if ok else "%s %s / allow-origin=%s" % (
                      r.status_code, r.text[:60],
                      r.headers.get("access-control-allow-origin")))

    # ── 3. the preflight must not need a token ──────────────────────────────
    print("\n--- authentication must not intercept the preflight ---")
    r = client.options(
        "/auth/workspace/01fed629-9a95-412b-895a-c8b09f37a98c",
        headers={"Origin": PRODUCTION_ORIGINS[0],
                 "Access-Control-Request-Method": "GET",
                 "Access-Control-Request-Headers": request_headers})
    check("an unauthenticated preflight succeeds", r.status_code == 200,
          "%s %s - a preflight carries no credentials by design, so a "
          "preflight that needs a token can never succeed" % (r.status_code, r.text[:60]))
    check("...and it is not answered with a redirect",
          r.status_code not in (301, 302, 307, 308), r.status_code)
    check("...and credentials are allowed on the real request",
          r.headers.get("access-control-allow-credentials") == "true")

    # ── 4. still a boundary ─────────────────────────────────────────────────
    print("\n--- and it is still a boundary ---")
    check("no wildcard origin is configured", "*" not in ALLOWED_ORIGINS)
    for bad in ("https://evil.example.com", "http://app.evosyspro.live",
                "https://app.evosyspro.live.evil.com"):
        r = client.options(
            "/leads/", headers={"Origin": bad,
                                "Access-Control-Request-Method": "GET",
                                "Access-Control-Request-Headers": request_headers})
        allow = r.headers.get("access-control-allow-origin")
        check("origin refused: %s" % bad, allow != bad, allow or "no allow-origin")

    check("an unlisted HEADER is still refused",
          client.options("/leads/", headers={
              "Origin": PRODUCTION_ORIGINS[0],
              "Access-Control-Request-Method": "GET",
              "Access-Control-Request-Headers": "x-not-a-real-header",
          }).status_code != 200,
          "the mechanism that caused the incident is intact - it was the LIST "
          "that was wrong, not the enforcement")

    print("\n--- both production brand origins are configured ---")
    for origin in PRODUCTION_ORIGINS:
        check("configured: %s" % origin, origin in ALLOWED_ORIGINS)


def finish():
    print("\n" + "=" * 78)
    print("checks passed: %d" % len(PASSED))
    if FAIL:
        print("\nFAILURES (%d):" % len(FAIL))
        for f in FAIL:
            print("  - %s" % f)
        print("\nPREFLIGHT BROKE")
    else:
        print("\nTHE PREFLIGHT SUCCEEDS FOR EVERY HEADER THE CLIENT SENDS")
    print("=" * 78)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
    finish()
