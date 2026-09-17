"""/health answers the probes that are actually being sent to it.

Production log, every few minutes, for as long as the logs go back:

    "HEAD /health HTTP/1.1" 405 Method Not Allowed
    "GET  /health HTTP/1.1" 200 OK

An external uptime monitor probes this endpoint from several regions on a
rotation - source IPs in UptimeRobot's 216.144.248.0/24 alongside Hetzner and
AWS ranges - and the regions are split between GET and HEAD. So roughly half
of every check was failing against a service that was entirely healthy.

RFC 9110 is not ambiguous: HEAD is identical to GET except that the server
must not send a body. A resource that answers GET and refuses HEAD is
answering incorrectly, and FastAPI does not add HEAD to a GET route the way
plain Starlette does - it has to be said.
"""

import re
from pathlib import Path

RENDER_YAML = Path(__file__).resolve().parents[1] / "render.yaml"


def test_get_health_is_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_head_health_is_ok_not_405(client):
    """The regression. This was 405 in production."""
    r = client.head("/health")
    assert r.status_code == 200, (
        "HEAD /health returned %d - the uptime monitor sees this as an "
        "outage" % r.status_code)


def test_head_sends_no_body(client):
    """HEAD is GET without the body. Sending one would be a different bug."""
    assert client.head("/health").content == b""


def test_head_and_get_agree_on_status(client):
    assert client.head("/health").status_code == client.get("/health").status_code


def test_the_payload_shape_is_unchanged(client):
    """Anything already consuming /health keeps working - the fix was the
    method list, not the response."""
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["phase"] == "1"
    assert "build" in body


def test_an_unsupported_method_is_still_refused(client):
    """Answering HEAD must not have turned this into an any-method endpoint."""
    for call in (client.post, client.put, client.delete):
        assert call("/health").status_code in (405, 404)


def test_health_needs_no_authentication(client):
    """A probe cannot log in. It was already open; this pins it."""
    assert client.get("/health", headers={}).status_code == 200


def test_render_points_its_health_check_at_it():
    """Without healthCheckPath, Render only checks that the port is open -
    which a process that booted and then lost its database answers just as
    cheerfully as a working one."""
    text = RENDER_YAML.read_text(encoding="utf-8")
    block = text[text.index("name: advisorflow-backend"):]
    block = block[:block.index("  - type:")] if "  - type:" in block else block
    assert re.search(r"^\s*healthCheckPath:\s*/health\s*$", block, re.M), \
        "advisorflow-backend has no healthCheckPath"


def test_the_route_declares_both_methods():
    """Read off the app itself, so a refactor that drops HEAD fails here."""
    import app.main as main
    methods = set()
    for route in main.app.routes:
        if getattr(route, "path", None) == "/health":
            methods |= set(getattr(route, "methods", set()) or set())
    assert {"GET", "HEAD"} <= methods, methods
