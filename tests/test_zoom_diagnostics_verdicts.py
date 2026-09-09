"""Zoom diagnostics must name the RIGHT cause, not the first plausible one.

Live production returned:

    HTTP 400
    error  = invalid_client
    reason = "The app has been disabled by the developer"

and the endpoint answered "client id/secret mismatch — check the pairing".
Zoom uses `invalid_client` for a deactivated app AND for a genuinely wrong
credential pair, putting the real difference only in `reason`. Because the
invalid_client branch was tested first, the endpoint sent the operator to
re-verify a pairing that was never wrong.

That is precisely the failure this endpoint exists to prevent, reproduced
inside the endpoint itself. These tests pin the branch ORDER.
"""

import pytest

from app.models.models import User


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


def _diagnose(monkeypatch, status, payload, env=None):
    """Run the verdict logic against a canned Zoom response."""
    import app.routers.god_router as gr

    defaults = {
        "ZOOM_ACCOUNT_ID": "a" * 22,
        "ZOOM_CLIENT_ID": "b" * 22,
        "ZOOM_CLIENT_SECRET": "c" * 32,
    }
    defaults.update(env or {})
    for key, value in defaults.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("ZOOM_HOST_ID", raising=False)

    import httpx
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(status, payload))

    god = User(id="god", email="g@test.local", role="god_admin", organization_id=None)
    return gr.zoom_diagnostics(_god=god)


class TestVerdictOrdering:
    def test_a_disabled_app_is_not_reported_as_a_credential_mismatch(self, monkeypatch):
        """The exact live production response."""
        out = _diagnose(monkeypatch, 400, {
            "error": "invalid_client",
            "reason": "The app has been disabled by the developer",
        })
        assert out["verdict"] == "app_disabled_in_marketplace"
        assert "activate" in out["explanation"].lower()
        # And it must NOT send anyone to re-check the credentials.
        assert "mismatch" not in out["verdict"]

    def test_a_real_credential_mismatch_still_reports_as_one(self, monkeypatch):
        out = _diagnose(monkeypatch, 400, {
            "error": "invalid_client",
            "reason": "Invalid client_id or client_secret",
        })
        assert out["verdict"] == "client_id_secret_mismatch"

    def test_a_bad_account_id_reports_as_one(self, monkeypatch):
        out = _diagnose(monkeypatch, 400, {
            "error": "invalid_request", "reason": "Invalid account_id",
        })
        assert out["verdict"] == "wrong_account_id"

    def test_wrong_app_type_reports_as_one(self, monkeypatch):
        out = _diagnose(monkeypatch, 400, {
            "error": "unsupported_grant_type", "reason": "",
        })
        assert out["verdict"] == "wrong_app_type"

    def test_missing_env_never_contacts_zoom(self, monkeypatch):
        out = _diagnose(monkeypatch, 400, {}, env={"ZOOM_CLIENT_SECRET": ""})
        assert out["verdict"] == "env_missing"
        assert out["token_request"] is None


class TestNoSecretsLeak:
    def test_no_credential_value_appears_in_the_response(self, monkeypatch):
        marker = "SUPER-SECRET-VALUE-9137"
        out = _diagnose(monkeypatch, 400,
                        {"error": "invalid_client",
                         "reason": "The app has been disabled by the developer"},
                        env={"ZOOM_CLIENT_SECRET": marker})
        assert marker not in repr(out)
        # Presence and shape only — that is what a diagnostic legitimately needs.
        assert out["env"]["ZOOM_CLIENT_SECRET"]["present"] is True
        assert out["env"]["ZOOM_CLIENT_SECRET"]["length"] == len(marker)
        assert "value" not in out["env"]["ZOOM_CLIENT_SECRET"]
