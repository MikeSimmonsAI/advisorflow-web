"""A server error tells the customer a reference that is in the log line."""
import logging

from fastapi.testclient import TestClient


def test_unhandled_error_carries_a_reference_that_is_logged(caplog):
    from app.main import app
    path = "/__boom_for_test"
    if not any(getattr(r, "path", "") == path for r in app.routes):
        @app.get(path)
        def _boom():
            raise RuntimeError("kaboom with internals")
    c = TestClient(app, raise_server_exceptions=False)
    try:
        with caplog.at_level(logging.ERROR):
            r = c.get(path)
    finally:
        # the route must not outlive this test (the GET-route sweep walks every route)
        app.router.routes[:] = [rt for rt in app.router.routes if getattr(rt, "path", "") != path]
    assert r.status_code == 500
    body = r.json()
    ref = body["reference"]
    assert len(ref) == 8 and ref in body["detail"]
    assert "kaboom" not in r.text                      # internals stay in the log
    assert any(ref in rec.getMessage() for rec in caplog.records)
