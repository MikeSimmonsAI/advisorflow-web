"""coverage.voltmarketing.live: the customer coverage site's short address."""
from fastapi.testclient import TestClient

from app.main import app


def test_coverage_subdomain_may_submit_the_public_form():
    c = TestClient(app)
    r = c.options("/volt/public/volt-marketing/leads", headers={
        "Origin": "https://coverage.voltmarketing.live",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type"})
    assert r.headers.get("access-control-allow-origin") == "https://coverage.voltmarketing.live"


def test_unknown_lookalike_origin_is_not_allowed():
    c = TestClient(app)
    r = c.options("/volt/public/volt-marketing/leads", headers={
        "Origin": "https://coverage.voltmarketing.live.evil.test",
        "Access-Control-Request-Method": "POST"})
    assert r.headers.get("access-control-allow-origin") != "https://coverage.voltmarketing.live.evil.test"
