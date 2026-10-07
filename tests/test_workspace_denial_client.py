"""DENIED WORKSPACE SWITCH: client recovery proof + direct-header reader inventory.

* Runs tests/frontend/workspaceDenial.test.mjs under plain node (skips if node
  is unavailable): select B -> canonical 403 -> selection/branding/location/
  in-flight cleared -> late B reply dropped -> A never restored ->
  /auth/my-contexts recovers.
* Pins the ONLY places the server reads X-Workspace-Id, so a new direct reader
  (which would bypass the fail-closed verdict) fails this test loudly. Stdlib
  only; the inventory test runs without pytest/SQLAlchemy.
"""
import os
import re
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8-sig") as fh:
        return fh.read()


def test_denied_switch_recovery_executes():
    script = os.path.join(ROOT, "tests", "frontend", "workspaceDenial.test.mjs")
    try:
        proc = subprocess.run(["node", script], capture_output=True, text=True,
                              cwd=ROOT, timeout=120)
    except (FileNotFoundError, OSError):
        pytest.skip("node is not available on this machine")
    assert proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")


def test_client_uses_the_extracted_denial_decision():
    body = _read("frontend", "src", "api", "client.js")
    assert "_applyWorkspaceDenial(res.status, detail" in body
    assert "_isStaleWorkspaceResponse(wsId" in body
    assert "That workspace is not available to this account." not in body


def test_only_two_server_sites_read_the_workspace_header():
    readers = []
    for dirpath, _dirs, files in os.walk(os.path.join(ROOT, "app")):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            for n, line in enumerate(open(path, encoding="utf-8-sig"), 1):
                code = line.split("#", 1)[0]
                if re.search(r"headers(\.get\(|\[)\s*(WORKSPACE_HEADER|[\"']X-Workspace-Id)",
                             code, re.I):
                    readers.append(os.path.relpath(path, ROOT).replace(os.sep, "/"))
    assert sorted(readers) == ["app/deps.py", "app/services/workspace_access.py"], readers


def test_deps_verdict_runs_before_executive_observation_and_exempts_only_auth():
    body = _read("app", "deps.py")
    verdict = body.index("_ws.selection_verdict(")
    assert verdict < body.index('request.headers.get("X-Executive-Observe")')
    assert 'not request.url.path.startswith("/auth/")' in body
    assert 'user.role != "god_admin"' in body[verdict - 600:verdict]
