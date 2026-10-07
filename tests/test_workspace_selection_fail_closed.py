"""Selected-workspace verdict: a held workspace is granted, anything else fails
closed with ONE refusal - no database, no SQLAlchemy, runs under stdlib.

The module is loaded by file path so `app/__init__` (and its heavy imports) is
never executed. Also simulates the scope-before-lookup / cross-org-mutation
contract with an in-memory store so the verdict logic actually runs.
"""
import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load():
    path = os.path.join(ROOT, "app", "services", "workspace_selection.py")
    spec = importlib.util.spec_from_file_location("workspace_selection_pure", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ws = _load()


def test_no_header_is_legacy_behaviour():
    assert ws.selection_verdict(None, ["a"]) == ws.NONE
    assert ws.selection_verdict("", ["a"]) == ws.NONE
    assert ws.selection_verdict("   ", ["a"]) == ws.NONE


def test_held_workspace_is_granted_and_others_denied():
    assert ws.selection_verdict("a", ["a", "b"]) == ws.GRANTED
    assert ws.selection_verdict("c", ["a", "b"]) == ws.DENIED


def test_membership_removal_fails_closed():
    assert ws.selection_verdict("b", ["a", "b"]) == ws.GRANTED
    assert ws.selection_verdict("b", ["a"]) == ws.DENIED       # b removed
    assert ws.selection_verdict("b", []) == ws.DENIED          # zero memberships


def test_legacy_column_still_counts_as_held():
    assert ws.selection_verdict("home", [], legacy_org_id="home") == ws.GRANTED
    assert ws.selection_verdict("other", [], legacy_org_id="home") == ws.DENIED


def test_missing_and_cross_org_ids_are_indistinguishable():
    held = ["a"]
    missing = ws.selection_verdict("does-not-exist", held)
    cross = ws.selection_verdict("tenant-b-real-org", held)
    assert missing == cross == ws.DENIED


# ── scope-before-lookup contract, executed against an in-memory store ────────

class _Store:
    def __init__(self, rows):
        self.rows = rows  # id -> {"org": ..., "v": ...}

    def get_in_scope(self, org, row_id):
        r = self.rows.get(row_id)
        return r if r is not None and r["org"] == org else None

    def update_in_scope(self, org, row_id, value):
        r = self.get_in_scope(org, row_id)
        if r is None:
            return False
        r["v"] = value
        return True


def _respond(store, held, requested, row_id):
    if ws.selection_verdict(requested, held) != ws.GRANTED:
        return 403, ws.DENIED_DETAIL
    r = store.get_in_scope(requested, row_id)
    return (200, r["v"]) if r else (404, "Not found")


def test_cross_tenant_detail_reveals_no_more_than_a_missing_id():
    store = _Store({"x1": {"org": "a", "v": 1}, "y1": {"org": "b", "v": 2}})
    assert _respond(store, ["a"], "a", "x1") == (200, 1)
    assert _respond(store, ["a"], "a", "y1") == _respond(store, ["a"], "a", "nope")
    # a header for the other tenant is refused before any lookup
    assert _respond(store, ["a"], "b", "y1")[0] == 403


def test_mutation_cannot_touch_another_tenant():
    store = _Store({"y1": {"org": "b", "v": 2}})
    assert store.update_in_scope("a", "y1", 99) is False
    assert store.rows["y1"]["v"] == 2


def test_get_current_user_enforces_the_verdict_and_keeps_auth_paths_open():
    with open(os.path.join(ROOT, "app", "deps.py"), encoding="utf-8") as fh:
        body = fh.read()
    assert "selection_verdict(" in body and "_ws.DENIED" in body
    assert 'startswith("/auth/")' in body
