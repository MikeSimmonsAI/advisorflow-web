"""fiber_intake_router is NOT part of the running application.

Evidence captured here so it cannot drift silently:
  * app.main never includes it - no route under /intake/fiber exists, and
    importing the app does not even import the module;
  * the live fiber surface is /fiber-leads (GET + POST), served by
    app.routers.fiber_leads_router - a different router, and it stays live;
  * no application module imports fiber_intake_router.

If someone mounts fiber_intake_router again, the first test fails and they
must look at why: its new-lead path constructs Lead(service_address=...,
extra_data=...), neither of which is a Lead column, and calls lead_capacity
without importing it.
"""
import os
import re
import sys

APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")


def _routes():
    from app.main import app
    return [(getattr(r, "path", ""), set(getattr(r, "methods", None) or ()),
             getattr(getattr(r, "endpoint", None), "__module__", ""))
            for r in app.routes]


def test_app_starts_and_mounts_no_fiber_intake_route():
    routes = _routes()
    assert routes, "app.main.app has no routes - it did not start"
    assert not [p for p, _, _ in routes if p.startswith("/intake/fiber")]
    assert not [m for _, _, m in routes if m == "app.routers.fiber_intake_router"]


def test_live_fiber_leads_routes_are_served_by_fiber_leads_router():
    live = {(p, frozenset(ms)): mod for p, ms, mod in _routes() if p == "/fiber-leads"}
    methods = set()
    for (p, ms), mod in live.items():
        assert mod == "app.routers.fiber_leads_router", (p, ms, mod)
        methods |= ms
    assert {"GET", "POST"} <= methods


def test_fiber_leads_resolves_over_http(client, auth_headers):
    """The live route is reachable (not 404/405) through the real app."""
    r = client.get("/fiber-leads", headers=auth_headers)
    assert r.status_code not in (404, 405), r.text
    r = client.get("/intake/fiber/anything")
    assert r.status_code == 404


def test_no_application_module_imports_fiber_intake_router():
    pat = re.compile(r"^\s*(from\s+app\.routers(\.fiber_intake_router\s+import|\s+import\s+[^#\n]*\bfiber_intake_router\b)"
                     r"|import\s+app\.routers\.fiber_intake_router)", re.M)
    offenders = []
    for root, _, files in os.walk(APP_DIR):
        for f in files:
            if f.endswith(".py") and f != "fiber_intake_router.py":
                path = os.path.join(root, f)
                if pat.search(open(path, encoding="utf-8").read()):
                    offenders.append(path)
    assert offenders == []


def test_importing_the_app_does_not_load_fiber_intake_router():
    import app.main  # noqa: F401
    # Other tests in the same worker may import it directly (for its own unit
    # test), so only assert when it was not already loaded by a test.
    if "app.routers.fiber_intake_router" in sys.modules:
        mod = sys.modules["app.routers.fiber_intake_router"]
        from app.main import app
        assert all(getattr(getattr(r, "endpoint", None), "__module__", "") != mod.__name__
                   for r in app.routes)
