"""Deploy identity — which commit is actually running.

═══════════════════════════════════════════════════════════════════════════
WHY THIS ENDPOINT EXISTS
═══════════════════════════════════════════════════════════════════════════

"Is my change live?" had no answer. A push, a green build and a healthy
service prove that SOMETHING is deployed; they do not prove WHICH commit, and
a change that adds no new route is invisible from outside. Verification
degenerated into waiting a plausible number of minutes and assuming - which is
how a stale build gets signed off as verified.

═══════════════════════════════════════════════════════════════════════════
WHAT THIS FILE DEFENDS
═══════════════════════════════════════════════════════════════════════════

  1. THE COMMIT IS READ FROM THE ENVIRONMENT, NEVER HARD-CODED. A literal
     hash in source would agree with itself, tell nobody anything, and go
     stale exactly when it mattered.
  2. MISSING METADATA REPORTS "unknown" AND STILL ANSWERS. Local development
     has no RENDER_GIT_COMMIT and must still boot.
  3. NOTHING SENSITIVE LEAKS. The endpoint is unauthenticated, so it may only
     ever say things that are safe for anyone to read. No tokens, no
     credentials, no repository URL, no unrelated environment variables.
  4. /health KEEPS ITS EXISTING SHAPE, so nothing consuming it breaks.
"""

import pytest


RENDER_VARS = ("RENDER_GIT_COMMIT", "RENDER_GIT_BRANCH", "RENDER_SERVICE_ID",
               "GIT_COMMIT", "SOURCE_VERSION")


@pytest.fixture(autouse=True)
def clean_build_env(monkeypatch):
    """Start every test from no deployment metadata at all."""
    for var in RENDER_VARS:
        monkeypatch.delenv(var, raising=False)


def test_version_reports_the_commit_from_the_environment(client, monkeypatch):
    monkeypatch.setenv("RENDER_GIT_COMMIT", "6cacd51bb90c060e4d2543dd99bb6c6fd9109404")
    monkeypatch.setenv("RENDER_GIT_BRANCH", "main")
    monkeypatch.setenv("RENDER_SERVICE_ID", "srv-abc123")

    body = client.get("/version").json()
    assert body["commit"] == "6cacd51bb90c060e4d2543dd99bb6c6fd9109404"
    assert body["commit_short"] == "6cacd51"
    assert body["branch"] == "main"
    assert body["environment"] == "production"


def test_the_commit_is_not_hard_coded_anywhere(client, monkeypatch):
    """Change the environment, the answer must change with it."""
    monkeypatch.setenv("RENDER_GIT_COMMIT", "aaaaaaaaaaaa")
    first = client.get("/version").json()["commit"]
    monkeypatch.setenv("RENDER_GIT_COMMIT", "bbbbbbbbbbbb")
    second = client.get("/version").json()["commit"]

    assert first == "aaaaaaaaaaaa"
    assert second == "bbbbbbbbbbbb", (
        "the commit did not change with the environment - it is hard-coded, "
        "which makes it a number that agrees with itself and verifies nothing")


def test_missing_metadata_reports_unknown_rather_than_failing(client):
    body = client.get("/version").json()
    assert body["commit"] == "unknown"
    assert body["commit_short"] == "unknown"
    assert body["branch"] == "unknown"
    assert body["environment"] == "development"


def test_a_generic_paas_commit_variable_is_honoured(client, monkeypatch):
    monkeypatch.setenv("SOURCE_VERSION", "cccccccccccc")
    assert client.get("/version").json()["commit"] == "cccccccccccc"


def test_render_git_commit_wins_over_the_fallbacks(client, monkeypatch):
    monkeypatch.setenv("RENDER_GIT_COMMIT", "render1234567")
    monkeypatch.setenv("GIT_COMMIT", "generic123456")
    monkeypatch.setenv("SOURCE_VERSION", "source123456")
    assert client.get("/version").json()["commit"] == "render1234567"


def test_health_keeps_its_existing_shape(client):
    """Adding `build` must not break anything already reading /health."""
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["phase"] == "1"
    assert "build" in body


def test_health_and_version_agree(client, monkeypatch):
    monkeypatch.setenv("RENDER_GIT_COMMIT", "dddddddddddd")
    assert client.get("/health").json()["build"] == client.get("/version").json()


def test_no_secret_ever_appears_in_the_response(client, monkeypatch):
    """The endpoint is unauthenticated. It may only say public things.

    Real secrets are set here and the response is searched for them, rather
    than the field list being eyeballed - a future field that happened to read
    an environment variable would be caught by this and not by inspection.
    """
    monkeypatch.setenv("RENDER_GIT_COMMIT", "eeeeeeeeeeee")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_TOTALLY_FAKE_SENTINEL")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_TOTALLY_FAKE_SENTINEL")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "TWILIO_FAKE_SENTINEL")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-OPENAI_FAKE_SENTINEL")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:PGPASS_SENTINEL@h/db")
    monkeypatch.setenv("JWT_SECRET_KEY", "JWT_FAKE_SENTINEL")

    for path in ("/version", "/health"):
        raw = client.get(path).text
        for sentinel in ("TOTALLY_FAKE_SENTINEL", "TWILIO_FAKE_SENTINEL",
                         "OPENAI_FAKE_SENTINEL", "PGPASS_SENTINEL",
                         "JWT_FAKE_SENTINEL", "postgresql://"):
            assert sentinel not in raw, (
                "%s leaked %r. This endpoint is unauthenticated."
                % (path, sentinel))


def test_the_response_carries_only_the_four_public_build_fields(client):
    body = client.get("/version").json()
    assert set(body) == {"commit", "commit_short", "branch", "environment"}, (
        "an unexpected field appeared on the public build endpoint: %s"
        % sorted(set(body) - {"commit", "commit_short", "branch", "environment"}))


def test_neither_endpoint_requires_authentication(client):
    """Deploy verification must work before anyone can log in."""
    assert client.get("/version").status_code == 200
    assert client.get("/health").status_code == 200
