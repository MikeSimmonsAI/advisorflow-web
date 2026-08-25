# scripts/smoke_requests.py
# Real HTTP-level smoke test using Starlette's TestClient - no server, no network,
# no production DB. Proves the newly mounted routes actually respond and that the
# scraper refuses unauthenticated callers.
import os, sys

# FORCE sqlite. Not setdefault - this script starts the app via TestClient,
# which runs the startup hooks, which run auto_migrate. If it inherited a
# production DATABASE_URL from the shell it would execute migrations against
# the live database. Never relax this to setdefault.
os.environ["DATABASE_URL"] = "sqlite:///./advisorflow.db"
os.environ.setdefault("JWT_SECRET", "local" + "0" * 60)
os.environ.setdefault("SECRET_KEY", "local" + "0" * 60)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from app.main import app

failures = []


def check(label, got, expected):
    ok = got in expected if isinstance(expected, (list, tuple, set)) else got == expected
    print("  %-42s -> %-4s %s" % (label, got, "OK" if ok else "FAIL (want %s)" % (expected,)))
    if not ok:
        failures.append(label)


# TestClient runs startup hooks; that is fine against local sqlite.
with TestClient(app) as c:
    print("--- public endpoints ---")
    check("GET  /health", c.get("/health").status_code, 200)

    r = c.get("/billing/plans")
    check("GET  /billing/plans (public)", r.status_code, 200)
    if r.status_code == 200:
        plans = r.json()
        names = list(plans.get("plans", plans).keys()) if isinstance(plans, dict) else "?"
        print("       plans returned: %s" % (names,))

    print("--- auth is enforced (401/403 expected, NOT 404) ---")
    # A 404 here would mean the router is not mounted - the exact bug we just fixed.
    check("POST /scraper/search  (no auth)", c.post("/scraper/search", json={"query": "funeral homes"}).status_code, (401, 403))
    check("POST /scraper/import  (no auth)", c.post("/scraper/import", json={"leads": []}).status_code, (401, 403))
    check("GET  /billing/subscription (no auth)", c.get("/billing/subscription").status_code, (401, 403))
    check("GET  /billing/all (no auth)", c.get("/billing/all").status_code, (401, 403))

print()
if failures:
    sys.exit("SMOKE REQUESTS FAILED: " + ", ".join(failures))
print("ALL REQUEST SMOKE CHECKS PASSED")
