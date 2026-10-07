"""NORMAL-ACCOUNT WORKSPACE LANDING AND SWITCHING.

Runs tests/frontend/workspaceLanding.test.mjs under plain node (no bundler, no
DOM): zero / one / many workspaces, unknown-is-not-zero, switch targets from the
server list only, customer manager is never a platform operator, and stale
responses after a workspace switch are dropped.

The wiring the node test cannot see is asserted below as source facts.
"""
import os
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FE = os.path.join(ROOT, "frontend", "src")


def _read(*parts):
    with open(os.path.join(FE, *parts), encoding="utf-8-sig") as fh:
        return fh.read()


def test_landing_and_switch_decisions_execute():
    script = os.path.join(ROOT, "tests", "frontend", "workspaceLanding.test.mjs")
    try:
        proc = subprocess.run(["node", script], capture_output=True, text=True,
                              cwd=ROOT, timeout=120)
    except (FileNotFoundError, OSError):
        pytest.skip("node is not available on this machine")
    assert proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")


def test_branding_read_drops_a_response_for_a_workspace_no_longer_selected():
    body = _read("api", "client.js")
    assert "isStaleForWorkspace" in body and "superseded()" in body


def test_switching_workspace_drops_the_previous_workspaces_cache():
    body = _read("api", "client.js")
    start = body.index("export function switchWorkspace")
    fn = body[start:start + 600]
    for needle in ("clearBranding()", "clearWorkspaceLocation()",
                   "resetInFlightGets()", "setWorkspaceContext("):
        assert needle in fn, needle + " missing from switchWorkspace"


def test_entry_points_use_switchWorkspace_not_a_bare_id_write():
    for rel in (("App.jsx",), ("components", "ContextSwitcher.jsx")):
        body = _read(*rel)
        assert "switchWorkspace(" in body, rel
