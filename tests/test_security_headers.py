"""The security headers survived being moved off BaseHTTPMiddleware.

SecurityHeadersMiddleware used to be a `BaseHTTPMiddleware`, which implements
`call_next` by running the rest of the app in a separate anyio task and piping
its ASGI messages through a pair of memory object streams. That is a task, two
streams and a wrapper response allocated on 100% of requests - every API call,
every CORS preflight, every `/ping` - in order to set five constant headers.

On a 512 MB instance that was restarting out of memory, that was worth removing
rather than explaining. It is now plain ASGI, editing the header list on the
`http.response.start` message.

A header change that silently stops being applied is the kind of regression
nobody notices until an audit, so every header is asserted by name, on the
ordinary path, on the error path, and on a preflight.
"""

import pytest

from app.main import SECURITY_HEADERS

EXPECTED = {k.decode(): v.decode() for k, v in SECURITY_HEADERS}


def test_the_header_set_is_what_we_think_it_is():
    """Pins the set itself, so removing one is a deliberate act with a diff."""
    assert EXPECTED == {
        "strict-transport-security": "max-age=31536000; includeSubDomains",
        "x-frame-options": "DENY",
        "x-content-type-options": "nosniff",
        "referrer-policy": "strict-origin-when-cross-origin",
        "permissions-policy": "camera=(), microphone=(), geolocation=(), payment=()",
    }


@pytest.mark.parametrize("header,value", sorted(EXPECTED.items()))
def test_header_is_present_on_a_normal_response(client, header, value):
    r = client.get("/health")
    assert r.headers.get(header) == value


@pytest.mark.parametrize("header", sorted(EXPECTED))
def test_header_is_present_on_a_404(client, header):
    """Not just on routes that exist - a 404 is a response too."""
    r = client.get("/definitely-not-a-route-xyz")
    assert r.status_code == 404
    assert header in r.headers


@pytest.mark.parametrize("header", sorted(EXPECTED))
def test_header_is_present_on_an_unauthenticated_401(client, header):
    r = client.get("/notifications/")
    assert r.status_code in (401, 403)
    assert header in r.headers


def test_server_fingerprint_header_is_stripped(client):
    r = client.get("/health")
    assert "server" not in {k.lower() for k in r.headers}


def test_head_requests_get_the_headers_too(client):
    """The uptime monitor sends HEAD; a bodyless response still carries them."""
    r = client.head("/health")
    assert r.status_code == 200
    for header, value in EXPECTED.items():
        assert r.headers.get(header) == value


def test_cors_preflight_still_answers_normally(client):
    """A preflight must still be answered. It does NOT carry these headers.

    THIS IS PRE-EXISTING AND IT IS NOT THE REWRITE. `add_middleware` prepends,
    so the execution order is CORSMiddleware -> RequestContextMiddleware ->
    SecurityHeadersMiddleware -> app. CORSMiddleware answers a preflight itself
    and returns without calling downstream, so SecurityHeadersMiddleware has
    never seen an OPTIONS request and has never put a header on one. Verified
    against the previous commit before this file was written: the preflight
    response carried exactly the eight CORS/content headers it carries now.

    It is left alone deliberately. The headers a preflight would gain are
    instructions to a browser about rendering a document - framing, MIME
    sniffing, referrer, feature permissions - and a preflight renders nothing;
    the real GET that follows carries all of them. Fixing it would mean moving
    SecurityHeadersMiddleware outside CORSMiddleware, which reorders it against
    RequestContextMiddleware, whose own comment above explains at length why it
    must run first. That is a real risk for no security gain.

    The assertion here is that the preflight keeps WORKING - a broken preflight
    is the failure documented in main.py where every API call rejected in the
    browser and the server never saw a request to log.
    """
    r = client.options(
        "/notifications/",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert "authorization" in r.headers.get("access-control-allow-headers", "").lower()
    assert not any(h in r.headers for h in EXPECTED), (
        "a preflight now carries the security headers - if that was deliberate, "
        "update this test and say why the middleware order changed")


def test_cors_headers_are_not_clobbered(client):
    """The middleware appends; it must not drop what CORSMiddleware set."""
    r = client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert r.headers.get("x-frame-options") == "DENY"


def test_middleware_is_not_a_starlette_base_http_middleware():
    """The regression this file is really about.

    Reverting to BaseHTTPMiddleware would keep every assertion above green
    while putting the per-request task and streams back. Assert the shape.
    """
    from starlette.middleware.base import BaseHTTPMiddleware
    from app.main import SecurityHeadersMiddleware
    assert not issubclass(SecurityHeadersMiddleware, BaseHTTPMiddleware)
    assert hasattr(SecurityHeadersMiddleware, "__call__")
    assert not hasattr(SecurityHeadersMiddleware, "dispatch")
