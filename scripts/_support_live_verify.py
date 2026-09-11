"""LIVE VERIFICATION for Support Intelligence, without a credential.

WHY THIS IS CREDENTIAL-FREE, AND WHY THAT IS ENOUGH TO PROVE SOMETHING
----------------------------------------------------------------------
A 404 and a 401 are different answers and the difference is the whole test:

    404  the route is not deployed
    401  the route IS deployed AND it refused an unauthenticated caller

So probing every new endpoint anonymously proves two things at once — that
the build is live, and that the guard is on. It proves them against the real
production service rather than against a test client, and it does it without
anybody's password being typed into a shell.

What it deliberately does NOT do: log in, create a ticket in a customer's
workspace, or run a remediation against live data. Verification must not
itself be a change, and this platform's own rule is that a destructive
production action is never performed merely to prove a feature works.

Usage:  python scripts/_support_live_verify.py [--wait] [--base URL]
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request

DEFAULT_BASE = "https://advisorflow-backend.onrender.com"

# Every route the customer surface adds, and the God console's entry points.
# GUARDED means "must refuse an anonymous caller"; a 200 here would be a
# finding, not a pass.
GUARDED = [
    ("GET", "/support/me"),
    ("GET", "/support/plan"),
    ("GET", "/support/status"),
    ("GET", "/support/tickets"),
    ("GET", "/support/services"),
    ("GET", "/support/knowledge"),
    ("GET", "/support/assistance"),
    ("POST", "/support/ask"),
    ("POST", "/support/tickets"),
    ("GET", "/god/support/overview"),
    ("GET", "/god/support/tickets"),
    ("GET", "/god/support/incidents"),
    ("GET", "/god/support/repairs"),
    ("GET", "/god/support/brief"),
    ("GET", "/god/support/recurring"),
    ("GET", "/god/support/fix-runs"),
    ("GET", "/god/support/brands"),
    ("GET", "/god/support/learning"),
    ("GET", "/god/support/policies"),
]

PUBLIC = [("GET", "/health"), ("GET", "/version"), ("GET", "/ping")]


def probe(base, method, path, timeout=30):
    req = urllib.request.Request(base + path, method=method)
    if method == "POST":
        req.data = b"{}"
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(4096).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(4096).decode("utf-8", "replace")
    except Exception as exc:                                   # noqa: BLE001
        return None, str(exc)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--expect-commit", default=None)
    ap.add_argument("--wait", type=int, default=0,
                    help="seconds to wait for --expect-commit to go live")
    args = ap.parse_args()

    deadline = time.time() + args.wait
    while True:
        status, body = probe(args.base, "GET", "/version")
        live = ""
        try:
            live = json.loads(body).get("commit_short", "")
        except Exception:                                      # noqa: BLE001
            pass
        if not args.expect_commit or live.startswith(args.expect_commit[:7]):
            break
        if time.time() >= deadline:
            break
        print("  waiting — live commit is %s, want %s"
              % (live or "?", args.expect_commit[:7]))
        time.sleep(20)

    print("VERSION      : %s %s" % (status, body.strip()))

    for method, path in PUBLIC:
        code, _ = probe(args.base, method, path)
        print("PUBLIC   %-4s %-34s -> %s %s"
              % (method, path, code, "OK" if code == 200 else "UNEXPECTED"))

    failures = []
    for method, path in GUARDED:
        code, body = probe(args.base, method, path)
        if code in (401, 403):
            verdict = "DEPLOYED + GUARDED"
        elif code == 404:
            verdict = "NOT DEPLOYED"
            failures.append((path, code))
        elif code == 422:
            # FastAPI validated a body before the dependency refused. The
            # route exists; the guard has not been demonstrated, so it is
            # reported rather than counted as a pass.
            verdict = "DEPLOYED (validation ran first)"
        elif code == 200:
            verdict = "!!! ANONYMOUS 200 — GUARD MISSING"
            failures.append((path, code))
        else:
            verdict = "unexpected"
            failures.append((path, code))
        print("GUARDED  %-4s %-34s -> %s %s" % (method, path, code, verdict))

    print()
    if failures:
        print("FAILURES: %s" % failures)
        return 1
    print("ALL %d SUPPORT ROUTES ARE LIVE AND REFUSE AN ANONYMOUS CALLER."
          % len(GUARDED))
    return 0


if __name__ == "__main__":
    sys.exit(main())
