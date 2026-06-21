"""
Tests for the CORS configuration in app/main.py.

REAL BUG THIS GUARDS AGAINST: allow_origins=["*"] combined with
allow_credentials=True is invalid per the CORS spec - browsers silently
reject this combination, which blocked every authenticated request from
the live frontend (confirmed via the browser console showing CORS
errors on every single page). The previous config had this exact bug
for a long stretch before it was noticed, since it's invisible to
TestClient-based tests (which bypass real browser CORS enforcement
entirely) - this test exists specifically to catch this class of bug
at the configuration level instead of relying on a live browser to
notice it.
"""

from app.main import app, ALLOWED_ORIGINS


def test_allowed_origins_does_not_use_wildcard():
    """
    The literal bug: "*" combined with allow_credentials=True is
    rejected by every real browser. This must never be a wildcard again.
    """
    assert "*" not in ALLOWED_ORIGINS


def test_allowed_origins_includes_real_production_frontend():
    assert "https://advisorflow-frontend.onrender.com" in ALLOWED_ORIGINS


def test_cors_middleware_has_credentials_enabled_with_explicit_origins():
    """
    Confirms the actual middleware instance registered on the app has
    allow_credentials=True paired with a real origin list, not a
    wildcard - re-checking the live app object, not just the constant,
    in case something else ever overrides the middleware setup.
    """
    cors_middleware = None
    for middleware in app.user_middleware:
        if middleware.cls.__name__ == "CORSMiddleware":
            cors_middleware = middleware
            break

    assert cors_middleware is not None, "CORSMiddleware is not registered on the app"
    kwargs = cors_middleware.kwargs
    assert kwargs.get("allow_credentials") is True
    assert kwargs.get("allow_origins") != ["*"]
    assert "https://advisorflow-frontend.onrender.com" in kwargs.get("allow_origins", [])
