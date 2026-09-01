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

    print("--- auth is enforced (401/403 expected, NOT 404) ---")
    # A 404 here would mean the router is not mounted - the exact bug we just fixed.

    # THIS ASSERTION MOVED, AND IT USED TO REQUIRE THE BUG.
    #
    # It lived under "public endpoints" and demanded that GET /billing/plans
    # return 200 with NO CREDENTIALS. That endpoint serves the platform's whole
    # price list - per-plan monthly rates, onboarding fees, lead and user
    # ceilings - so the test was pinning an unauthenticated pricing exposure in
    # place. It was marked "(public)" for a plan-picker UI that does not exist:
    # nothing in frontend/src calls it, so nothing was relying on the openness.
    #
    # It now asserts the OPPOSITE, and the pairing below makes this strictly
    # stronger rather than merely different: refusing anonymously is checked,
    # AND the body is checked to be free of pricing, AND the neighbouring
    # billing routes still behave. A guard that refused everything would pass
    # the first check and fail nothing else here - probe_delegation.py CASE 10
    # holds the other half, proving an org_admin who may change the plan can
    # still read it.
    r = c.get("/billing/plans")
    check("GET  /billing/plans (no auth)", r.status_code, (401, 403))
    check("       ...and no pricing in the body",
          "monthly_usd" not in r.text and "onboarding_usd" not in r.text, True)

    check("POST /scraper/search  (no auth)", c.post("/scraper/search", json={"query": "funeral homes"}).status_code, (401, 403))
    check("POST /scraper/import  (no auth)", c.post("/scraper/import", json={"leads": []}).status_code, (401, 403))
    check("GET  /billing/subscription (no auth)", c.get("/billing/subscription").status_code, (401, 403))
    check("GET  /billing/all (no auth)", c.get("/billing/all").status_code, (401, 403))

print()
if failures:
    sys.exit("SMOKE REQUESTS FAILED: " + ", ".join(failures))
print("ALL REQUEST SMOKE CHECKS PASSED")
