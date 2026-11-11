# -*- coding: utf-8 -*-
"""Connect ChatGPT to the VOLT development relay (#571, 2026-11-10): OAuth 2.1
sign-in with PKCE, the MCP server, exact-commit release approval, revocation."""
import base64
import hashlib
import json
import secrets
from urllib.parse import parse_qs, urlparse

import pytest

from app.models.volt_models import VoltDevJob, VoltDevToken
from app.services.volt import devdispatch as D
from tests.test_volt_dev_relay import _GH, _next, _report, dev, runner_token  # noqa: F401
from tests.test_volt_recruiting import H, _user, volt  # noqa: F401

CB = "https://chatgpt.com/connector_platform_oauth_redirect"


class _GH2(_GH):
    def __call__(self, method, path, body=None, timeout=15):
        if path.endswith("/actions/workflows/volt-dev.yml"):
            self.calls.append((method, path, body))
            return 200, {"state": "active"}, None
        if "/actions/workflows/volt-dev.yml/runs" in path:
            self.calls.append((method, path, body))
            return 200, {"workflow_runs": [{"event": "schedule", "status": "completed", "conclusion": "success"}]}, None
        return super().__call__(method, path, body, timeout)


@pytest.fixture()
def gh(dev, monkeypatch):
    fake = _GH2()
    monkeypatch.setattr(D, "_gh", fake)
    monkeypatch.setenv("VOLT_DEV_GITHUB_TOKEN", "ghp_secret_should_never_leak")
    D._CHECK["at"] = None
    return fake


def _pkce():
    v = secrets.token_urlsafe(48)
    ch = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    return v, ch


def _register(c, uris=(CB,), method="none"):
    return c.post("/volt/dev/oauth/register", json={"client_name": "ChatGPT", "redirect_uris": list(uris),
                                                     "token_endpoint_auth_method": method})


def _connect(dev, who, client=None):
    """The whole ChatGPT sign-in, as ChatGPT and the browser would do it."""
    c, db = dev["c"], dev["db"]
    client = client or _register(c).json()
    verifier, challenge = _pkce()
    r = c.get("/volt/dev/oauth/authorize", params={
        "response_type": "code", "client_id": client["client_id"], "redirect_uri": CB, "state": "st-1",
        "code_challenge": challenge, "code_challenge_method": "S256", "scope": "volt.dev"}, follow_redirects=False)
    assert r.status_code == 302, r.text
    loc = r.headers["location"]
    assert loc.startswith("https://voltmarketing.live/volt-dev/connect?request=")
    req = parse_qs(urlparse(loc).query)["request"][0]
    info = c.get("/volt/dev/oauth/request/%s" % req, headers=H(db, who))
    d = c.post("/volt/dev/oauth/request/%s/decision" % req, headers=H(db, who), json={"allow": True})
    return client, verifier, req, info, d


def _code(d):
    q = parse_qs(urlparse(d.json()["redirect"]).query)
    assert q["state"] == ["st-1"]
    return q["code"][0]


def _tokens(dev, who):
    client, verifier, _req, _info, d = _connect(dev, who)
    t = dev["c"].post("/volt/dev/oauth/token", data={"grant_type": "authorization_code", "code": _code(d),
                                                     "redirect_uri": CB, "client_id": client["client_id"],
                                                     "code_verifier": verifier})
    assert t.status_code == 200, t.text
    return client, t.json()


def _rpc(dev, token, method, params=None, mid=1):
    hdr = {"Authorization": "Bearer " + token} if token else {}
    return dev["c"].post("/volt/dev/mcp", headers=hdr, json={"jsonrpc": "2.0", "id": mid, "method": method,
                                                             "params": params or {}})


def _tool(dev, token, name, **args):
    r = _rpc(dev, token, "tools/call", {"name": name, "arguments": args})
    assert r.status_code == 200, r.text
    return r.json()["result"]


def test_discovery_and_unauthenticated_mcp_points_to_sign_in(dev):
    c = dev["c"]
    meta = c.get("/.well-known/oauth-authorization-server").json()
    assert meta["code_challenge_methods_supported"] == ["S256"] and meta["registration_endpoint"].endswith("/volt/dev/oauth/register")
    res = c.get("/.well-known/oauth-protected-resource/volt/dev/mcp").json()
    assert res["resource"].endswith("/volt/dev/mcp") and res["authorization_servers"]
    r = _rpc(dev, None, "initialize", {"protocolVersion": "2025-06-18"})
    assert r.status_code == 401 and "resource_metadata=" in r.headers["www-authenticate"]


def test_registration_only_accepts_chatgpt_callbacks(dev):
    c = dev["c"]
    assert _register(c, uris=("https://evil.example.com/cb",)).status_code == 400
    assert _register(c, uris=("http://chatgpt.com/cb",)).status_code == 400
    ok = _register(c)
    assert ok.status_code == 201 and ok.json()["client_id"].startswith("vdc_") and "client_secret" not in ok.json()
    conf = _register(c, method="client_secret_post").json()
    assert conf["client_secret"].startswith("vds_")


def test_principal_signs_in_and_chatgpt_gets_a_revocable_token(dev, gh):
    c, db = dev["c"], dev["db"]
    client, verifier, req, info, d = _connect(dev, dev["collab"])
    assert info.json()["authorized"] is True and info.json()["email"] == dev["collab"].email
    code = _code(d)
    # Wrong verifier: refused. Right one: tokens. Same code again: refused, and what it made is revoked.
    bad = c.post("/volt/dev/oauth/token", data={"grant_type": "authorization_code", "code": code, "client_id": client["client_id"],
                                                "redirect_uri": CB, "code_verifier": "x" * 50})
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_grant"
    t = c.post("/volt/dev/oauth/token", data={"grant_type": "authorization_code", "code": code, "client_id": client["client_id"],
                                              "redirect_uri": CB, "code_verifier": verifier})
    assert t.status_code == 200 and t.headers["cache-control"] == "no-store"
    tok = t.json()
    assert tok["access_token"].startswith("vdo_") and tok["refresh_token"].startswith("vdr_") and tok["expires_in"] == 3600
    who = _tool(dev, tok["access_token"], "relay_status")
    assert who["structuredContent"]["email"] == dev["collab"].email and who["isError"] is False
    assert who["structuredContent"]["github"]["ok"] is True
    replay = c.post("/volt/dev/oauth/token", data={"grant_type": "authorization_code", "code": code, "client_id": client["client_id"],
                                                   "redirect_uri": CB, "code_verifier": verifier})
    assert replay.status_code == 400
    assert _rpc(dev, tok["access_token"], "tools/list").status_code == 401        # replay killed it
    # Stored as hashes only.
    dump = json.dumps([(x.token_hash, x.refresh_hash) for x in db.query(VoltDevToken).all()])
    assert tok["access_token"] not in dump and tok["refresh_token"] not in dump
    # The request page can be answered once.
    assert c.post("/volt/dev/oauth/request/%s/decision" % req, headers=H(db, dev["collab"]), json={"allow": True}).status_code == 410


def test_refresh_rotates_in_place_and_disconnect_ends_it(dev, gh):
    c, db = dev["c"], dev["db"]
    client, tok = _tokens(dev, dev["collab"])
    r = c.post("/volt/dev/oauth/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                              "client_id": client["client_id"]})
    new = r.json()
    assert r.status_code == 200 and new["access_token"] != tok["access_token"]
    assert _rpc(dev, tok["access_token"], "tools/list").status_code == 401          # old access token dead
    assert c.post("/volt/dev/oauth/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                                 "client_id": client["client_id"]}).status_code == 400
    assert _rpc(dev, new["access_token"], "tools/list").status_code == 200
    rows = c.get("/volt/dev/%s/tokens" % dev["org"].id, headers=H(db, dev["collab"])).json()["items"]
    assert len(rows) == 1 and rows[0]["kind"] == "oauth"                           # one connection, one row
    c.delete("/volt/dev/%s/tokens/%s" % (dev["org"].id, rows[0]["id"]), headers=H(db, dev["collab"]))
    assert _rpc(dev, new["access_token"], "tools/list").status_code == 401
    assert c.post("/volt/dev/oauth/token", data={"grant_type": "refresh_token", "refresh_token": new["refresh_token"],
                                                 "client_id": client["client_id"]}).status_code == 400


def test_non_principal_cannot_connect_and_lost_authority_kills_the_token(dev, gh):
    c, db = dev["c"], dev["db"]
    other = _user(db, "advisor", dev["org"], "org_admin")          # VOLT admin, but not a named principal
    _client, _v, req, info, d = _connect(dev, other)
    assert info.json()["authorized"] is False and d.status_code == 403
    _client, tok = _tokens(dev, dev["collab"])
    from app.services.volt import access
    access.change(db, dev["org"].id, dev["god"], dev["collab"], "suspend", "test")
    db.commit()
    r = _rpc(dev, tok["access_token"], "tools/list")
    assert r.status_code == 403


def test_expired_access_token_is_refused(dev, gh):
    from datetime import timedelta
    from app.services.volt import common as C
    _client, tok = _tokens(dev, dev["collab"])
    row = dev["db"].query(VoltDevToken).filter(VoltDevToken.kind == "oauth").one()
    row.expires_at = C.now() - timedelta(seconds=1)
    dev["db"].commit()
    assert _rpc(dev, tok["access_token"], "tools/list").status_code == 401


def test_mcp_submit_dispatches_once_and_review_and_exact_commit_release(dev, gh):
    c, db = dev["c"], dev["db"]
    _client, tok = _tokens(dev, dev["collab"])
    a = tok["access_token"]
    init = _rpc(dev, a, "initialize", {"protocolVersion": "2025-06-18"}).json()["result"]
    assert init["protocolVersion"] == "2025-06-18" and "approve_release" in init["instructions"]
    names = {t["name"]: t for t in _rpc(dev, a, "tools/list").json()["result"]["tools"]}
    assert {"submit_change_request", "get_change_request", "get_change_review", "retry_change_request",
            "approve_release"} <= set(names)
    assert names["approve_release"]["annotations"]["destructiveHint"] is True
    assert dev["c"].post("/volt/dev/mcp", headers={"Authorization": "Bearer " + a},
                         json={"jsonrpc": "2.0", "method": "notifications/initialized"}).status_code == 202
    s1 = _tool(dev, a, "submit_change_request", title="Harmless check", request="Add one harmless line to docs/volt.")
    s2 = _tool(dev, a, "submit_change_request", title="Harmless check", request="Add one harmless line to docs/volt.")
    j = s1["structuredContent"]
    assert j["ref"] == "DEV-1" and j["created"] is True and s2["structuredContent"]["created"] is False
    assert j["via"] == "chatgpt" and j["requested_by"] == "Dee Collaborator"
    assert j["dispatch"]["state"] == "dispatched"
    assert len([x for x in gh.calls if x[1].endswith("/dispatches")]) == 1
    assert "ghp_secret_should_never_leak" not in json.dumps(s1)
    # Runner writes it; tests pass.
    job = db.query(VoltDevJob).one()
    _next(dev)
    _report(dev, job.id, status="ready_to_test", changed_files=["docs/volt/x.md"], tests_passed=True,
            pr_number=9, head_sha="abcdef1234567890" + "0" * 24, pr_url="https://github.com/x/y/pull/9")
    st = _tool(dev, a, "get_change_request", ref="DEV-1")["structuredContent"]
    assert st["status"] == "ready_to_test" and "abcdef123456" in st["next"]
    # Approval: needs confirmation and the exact tested commit.
    nc = _tool(dev, a, "approve_release", ref="DEV-1", tested_commit="abcdef1", confirm_production=False)
    assert nc["isError"] is True
    wrong = _tool(dev, a, "approve_release", ref="DEV-1", tested_commit="1234567", confirm_production=True)
    assert wrong["isError"] is True and wrong["structuredContent"]["code"] == "commit_mismatch"
    ok = _tool(dev, a, "approve_release", ref="DEV-1", tested_commit="abcdef1", confirm_production=True)
    assert ok["isError"] is False and ok["structuredContent"]["status"] == "merge_requested"
    assert db.query(VoltDevJob).one().merge_approved_sha.startswith("abcdef1")
    again = _tool(dev, a, "approve_release", ref="DEV-1", tested_commit="abcdef1", confirm_production=True)
    assert again["isError"] is False
    merges = [x for x in gh.calls if x[1].endswith("/dispatches") and "merge" in x[2]["inputs"]["reason"]]
    assert len(merges) == 1


def test_review_returns_the_diff_at_the_tested_commit(dev, gh, monkeypatch):
    _client, tok = _tokens(dev, dev["collab"])
    a = tok["access_token"]
    _tool(dev, a, "submit_change_request", title="Harmless", request="Add one harmless line to docs/volt.")
    job = dev["db"].query(VoltDevJob).one()
    _next(dev)
    _report(dev, job.id, status="ready_to_test", changed_files=["docs/volt/x.md"], tests_passed=True,
            pr_number=9, head_sha="c" * 40)
    real = gh.__call__

    def fake(method, path, body=None, timeout=15):
        if path.endswith("/pulls/9/files?per_page=100"):
            return 200, [{"filename": "docs/volt/x.md", "status": "added", "additions": 1, "deletions": 0,
                          "patch": "@@ -0,0 +1 @@\n+hello"}], None
        return real(method, path, body, timeout)
    monkeypatch.setattr(D, "_gh", fake)
    rv = _tool(dev, a, "get_change_review", ref="DEV-1")["structuredContent"]
    assert rv["review"]["available"] is True and rv["review"]["tested_commit"] == "c" * 40
    assert rv["review"]["files"][0]["diff"].endswith("+hello")


def test_openapi_action_approval_requires_commit_and_accepts_signin_tokens(dev, gh):
    c, db = dev["c"], dev["db"]
    _client, tok = _tokens(dev, dev["staff"])
    hdr = {"Authorization": "Bearer " + tok["access_token"]}
    assert c.post("/volt/dev/api/jobs", headers=hdr, json={"title": "Via GPT", "request": "A harmless VOLT docs line."}).status_code == 200
    job = db.query(VoltDevJob).one()
    _next(dev)
    _report(dev, job.id, status="ready_to_test", changed_files=["docs/volt/x.md"], tests_passed=True, pr_number=5, head_sha="d" * 40)
    assert c.post("/volt/dev/api/jobs/DEV-1/approve-merge", headers=hdr).status_code == 422
    assert c.post("/volt/dev/api/jobs/DEV-1/approve-merge", headers=hdr,
                  json={"tested_commit": "e" * 7, "confirm_production": True}).status_code == 409
    ok = c.post("/volt/dev/api/jobs/DEV-1/approve-merge", headers=hdr, json={"tested_commit": "d" * 12, "confirm_production": True})
    assert ok.status_code == 200 and ok.json()["status"] == "merge_requested"
    # The console still approves with one click (it shows the commit itself).
    schema = c.get("/volt/dev/api/openapi.json").json()
    assert schema["components"]["securitySchemes"]["volt"]["type"] == "oauth2"
    assert c.get("/volt/dev/api/status", headers=hdr).json()["github"]["ok"] is True
