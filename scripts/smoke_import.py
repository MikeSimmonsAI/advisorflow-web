# scripts/smoke_import.py
# Pre-commit smoke test: import the FastAPI app with throwaway local env vars
# and assert the security-sensitive routes are gone. Never touches production.
import os, sys

# FORCE sqlite - never inherit a production DATABASE_URL from the shell.
os.environ["DATABASE_URL"] = "sqlite:///./advisorflow.db"
os.environ.setdefault("JWT_SECRET", "local" + "0" * 60)
os.environ.setdefault("SECRET_KEY", "local" + "0" * 60)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app, ALLOWED_ORIGINS

paths = sorted({getattr(r, "path", "") for r in app.routes})
print("IMPORT OK - %d routes registered" % len(paths))

bad = [p for p in paths if "bootstrap" in p]
print("bootstrap routes: %s" % (bad or "NONE"))
assert not bad, "FAIL: bootstrap endpoint still registered"

for p in ("/auth/login", "/auth/verify", "/auth/refresh", "/ping", "/health"):
    print("  %-16s %s" % (p, "OK" if p in paths else "MISSING"))

for o in ("https://www.evosyspro.live", "https://www.bookaboost.live",
          "https://app.evosyspro.live", "https://app.bookaboost.live"):
    assert o in ALLOWED_ORIGINS, "FAIL: %s not in ALLOWED_ORIGINS" % o
print("CORS origins: OK (%d total)" % len(ALLOWED_ORIGINS))

import inspect
import app.main as m
src = inspect.getsource(m)
assert "GodMode2024!" not in src, "FAIL: hardcoded god password still present"
print("hardcoded god password: GONE")
print("\nALL SMOKE CHECKS PASSED")

# --- Router mount checks (added Aug 25) ------------------------------------
# billing_router / lead_scraper_router were written but never mounted, so every
# /billing/* and /scraper/* call 404'd in production. Guard against a regression.
print("\n--- mounted router checks ---")
for p in ("/billing/plans", "/billing/subscription", "/billing/checkout",
          "/billing/portal", "/billing/webhook", "/billing/all",
          "/scraper/search", "/scraper/validate", "/scraper/exists", "/scraper/import"):
    assert p in paths, "FAIL: %s is not mounted" % p
    print("  %-24s MOUNTED" % p)

# The scraper is god_admin only. That must be enforced server-side, not just by
# the requireGodAdmin prop in React.
import app.routers.lead_scraper_router as scr
scr_src = inspect.getsource(scr)
assert "Depends(get_current_user)" not in scr_src, \
    "FAIL: a /scraper endpoint still uses bare authentication"
assert scr_src.count("Depends(require_god)") >= 4, \
    "FAIL: expected god guard on all 4 scraper endpoints"
print("  scraper god guard        %d endpoints" % scr_src.count("Depends(require_god)"))

# Scraped leads must never silently land in the god platform org.
assert "_resolve_target_org" in scr_src, "FAIL: org targeting helper missing"
assert scr_src.count("_resolve_target_org(req.target_org_id or req.organization_id") == 2, \
    "FAIL: expected /exists and /import to both resolve the target org"
print("  scraper org targeting    explicit on /exists and /import")

# The UI sends target_org_id; org ids are strings, so parseInt must not be used.
import io as _io
_jsx = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "frontend", "src", "pages", "LeadScraper.jsx")
with _io.open(_jsx, "r", encoding="utf-8") as _f:
    _j = _f.read()
assert "parseInt(targetOrgId" not in _j, "FAIL: LeadScraper.jsx still parseInts a string org id"
assert "target_org_id: String(targetOrgId)" in _j, "FAIL: LeadScraper.jsx not sending string org id"
print("  scraper UI org id        sent as string (no parseInt)")

# Social webhook columns must be on the ORM model, not just in auto_migrate.
from app.models.models import Organization
for col in ("social_webhook_token", "meta_page_access_token",
            "meta_webhook_verify_token", "meta_app_secret", "tiktok_webhook_secret"):
    assert hasattr(Organization, col), "FAIL: Organization.%s missing from ORM" % col
print("  social webhook columns   declared on Organization")

print("\nALL MOUNT + GUARD CHECKS PASSED")
