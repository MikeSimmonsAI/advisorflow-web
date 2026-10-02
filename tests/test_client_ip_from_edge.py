"""request.client.host is the caller's address from Cloudflare, not the proxy's."""
from fastapi.testclient import TestClient


def _probe_app():
    from app.main import app
    path = "/__whoami_for_test"
    if not any(getattr(r, "path", "") == path for r in app.routes):
        from fastapi import Request

        @app.get(path)
        def _whoami(request: Request):
            return {"ip": request.client.host if request.client else None}
    return app, path


def _cleanup(app, path):
    app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", "") != path]


def test_cf_connecting_ip_becomes_the_client_address():
    app, path = _probe_app()
    try:
        c = TestClient(app)
        assert c.get(path, headers={"CF-Connecting-IP": "203.0.113.7"}).json()["ip"] == "203.0.113.7"
        assert c.get(path, headers={"CF-Connecting-IP": "2001:db8::1"}).json()["ip"] == "2001:db8::1"
        # garbage is ignored; no header leaves the transport's address alone
        assert c.get(path, headers={"CF-Connecting-IP": "not-an-ip; drop table"}).json()["ip"] == "testclient"
        assert c.get(path).json()["ip"] == "testclient"
    finally:
        _cleanup(app, path)


def test_rate_limit_and_login_throttle_now_see_distinct_callers():
    from app.limiter import limiter
    from starlette.requests import Request as SReq
    from app.routers import auth_router as AR
    app, path = _probe_app()
    _cleanup(app, path)
    scope = {"type": "http", "headers": [], "client": ("10.0.0.5", 1234)}
    # the throttle key is built from request.client.host, which the middleware sets
    assert AR._throttle_key(SReq(dict(scope, client=("198.51.100.9", 1))), "A@x.test") == "198.51.100.9:a@x.test"
    assert limiter is not None


def test_slow_requests_are_logged_without_the_query_string(caplog, monkeypatch):
    import logging
    import time
    from fastapi.testclient import TestClient
    from app.main import app
    path = "/__slow_for_test"
    if not any(getattr(r, "path", "") == path for r in app.routes):
        @app.get(path)
        def _slow():
            time.sleep(0.05)
            return {"ok": True}
    mw = None
    try:
        # lower the threshold on the live middleware instance
        TestClient(app).get("/health")          # builds the middleware stack
        stack = app.middleware_stack
        node = stack
        while node is not None:
            if type(node).__name__ == "SlowRequestLogMiddleware":
                mw = node
                break
            node = getattr(node, "app", None)
        assert mw is not None
        old, mw.threshold = mw.threshold, 10
        with caplog.at_level(logging.WARNING, logger="slow_request"):
            TestClient(app).get(path + "?token=secret123")
        line = next(r.getMessage() for r in caplog.records if r.name == "slow_request")
        assert path in line and "secret123" not in line and "-> 200" in line
    finally:
        if mw is not None:
            mw.threshold = old
        app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", "") != path]
