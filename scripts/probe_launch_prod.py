"""Live production probe for the Launch Engine — unauthenticated.

WHAT THIS CAN AND CANNOT PROVE.

It proves the routes are mounted and GUARDED: a 401/403 on every customer and
staff route means the endpoint exists and refuses an anonymous caller. That is
the single most important property of this feature — an onboarding surface that
answered without a session would be a cross-tenant data leak — and it is
exactly what an unauthenticated probe is good at.

It cannot prove a customer can save an answer. That needs a signed-in session.
Reporting a 401 as "verified working" is how a punch list gets signed off while
still broken, so the two are labelled separately below.
"""

import json
import sys
import urllib.error
import urllib.request

API = "https://advisorflow-backend.onrender.com"

# Every route the feature adds. `expect_guarded` means anonymous access MUST be
# refused; a 200 here would be a defect, not a pass.
ROUTES = [
    ("GET", "/launch/config"),
    ("GET", "/launch/me"),
    ("GET", "/launch/me/steps/company"),
    ("PUT", "/launch/me/steps/company"),
    ("POST", "/launch/me/submit"),
    ("GET", "/launch/me/files"),
    ("POST", "/launch/me/files"),
    ("GET", "/god/launch"),
    ("GET", "/god/launch/any-org-id"),
    ("POST", "/god/launch/any-org-id/review"),
]

GUARDED = {401, 403}


def probe(method, path):
    req = urllib.request.Request(f"{API}{path}", method=method)
    req.add_header("Accept", "application/json")
    if method in ("PUT", "POST"):
        req.data = b"{}"
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return r.status, r.read(600)
    except urllib.error.HTTPError as e:
        return e.code, e.read(600)
    except Exception as e:  # noqa: BLE001
        return 0, str(e).encode()


def main() -> int:
    print("=== anonymous access must be REFUSED on every route ===")
    bad = 0
    for method, path in ROUTES:
        status, body = probe(method, path)
        ok = status in GUARDED
        if not ok:
            bad += 1
        snippet = body[:90].decode("utf-8", "replace").replace("\n", " ")
        print(f"{'OK  ' if ok else 'FAIL'} {method:5s} {path:38s} {status}  {snippet}")

    print()
    if bad:
        print(f"{bad} route(s) did NOT refuse an anonymous caller — this is a defect.")
        return 1
    print("All routes exist and refuse anonymous callers.")
    print()
    print("NOT PROVEN HERE (needs a signed-in session): save, resume, upload,")
    print("submit, staff read. A 401 proves the door is locked, not that the")
    print("room behind it is furnished.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
