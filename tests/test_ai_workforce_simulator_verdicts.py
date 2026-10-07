"""Dependency-free guards on the simulator's verdict logic.

The scenarios themselves need the database. What can be proven without it is
that no "nothing happened" verdict can pass on empty evidence, so this reads
simulator.py as source (no SQLAlchemy import) and exercises the verdict
helpers with fakes.
"""
import ast
import pathlib
import types

SRC = pathlib.Path(__file__).resolve().parents[1] / \
    "app/services/workforce/simulator.py"
TREE = ast.parse(SRC.read_text())
FUNCS = {n.name: n for n in TREE.body if isinstance(n, ast.FunctionDef)}


class _C:
    ASSIGNED = "assigned"
    DENY_UNKNOWN_TOOL = "unknown_tool"


def _load(*names):
    """Compile only the named top-level helpers into a stub namespace."""
    mod = ast.Module(body=[FUNCS[n] for n in names], type_ignores=[])
    ns = {"C": _C, "Dict": dict, "Lead": object}
    exec(compile(mod, str(SRC), "exec"), ns)
    return types.SimpleNamespace(**ns)


class _Q:
    def __init__(self, n):
        self.n = n

    def filter(self, *a):
        return self

    def count(self):
        return self.n


class _FakeWorld:
    """Just enough World for the helpers: configurable evidence."""

    def __init__(self, *, sent=0, decisions=0, ledger=0, state="assigned"):
        self._sent, self._ledger, self._state = sent, ledger, state
        self.org = types.SimpleNamespace(id=1)
        self.db = types.SimpleNamespace(query=lambda *a: _Q(decisions))

    def executions(self, *, tool_key=None):
        rows = [types.SimpleNamespace(decision="allowed")
                for _ in range(self._sent)]
        rows += [types.SimpleNamespace(decision="denied")
                 for _ in range(self._ledger)]
        return rows

    def state_of(self, lead):
        return self._state


def _models_stub(monkeypatch=None):
    import sys
    pkg = types.ModuleType("app.models.workforce_models")
    pkg.AIEligibilityResult = types.SimpleNamespace(
        organization_id=types.SimpleNamespace(__eq__=lambda s, o: True))
    for name in ("app", "app.models"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["app.models.workforce_models"] = pkg


def _helpers():
    _models_stub()
    return _load("_result", "_engine_acted", "_no_send_result")


def test_no_send_with_no_evidence_does_not_pass():
    h = _helpers()
    out = h._no_send_result(_FakeWorld(), object(), "t", "no SMS sent")
    assert out["passed"] is False
    assert "never acted" in out["actual"]


def test_no_send_passes_only_with_engine_evidence():
    h = _helpers()
    for kw in ({"decisions": 1}, {"ledger": 1}, {"state": "do_not_contact"}):
        out = h._no_send_result(_FakeWorld(**kw), object(), "t", "no SMS sent")
        assert out["passed"] is True, kw


def test_a_send_always_fails_even_with_evidence():
    h = _helpers()
    out = h._no_send_result(_FakeWorld(sent=1, decisions=1), object(), "t",
                            "no SMS sent")
    assert out["passed"] is False


def test_every_scenario_sets_an_explicit_verdict_path():
    """Every scenario returns through _result or a no-send helper, and every
    explicit `passed=False` guard names why nothing was checked."""
    scenarios = [n for k, n in FUNCS.items() if k.startswith("s_")]
    assert len(scenarios) > 80
    for fn in scenarios:
        calls = [c.func.id for c in ast.walk(fn)
                 if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)]
        assert "_result" in calls or "_no_send_result" in calls, fn.name


def test_no_scenario_reduces_to_a_bare_nothing_check():
    """The old shape `... if not sent else ...` with no evidence check must
    not come back: any scenario inspecting `sent` goes through the helper."""
    for name, fn in FUNCS.items():
        if not name.startswith("s_"):
            continue
        names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
        if "sent" in names and name not in (
                "s_eligibility_allows_clean_record",
                "s_eligibility_blocks_dnc",
                "s_eligibility_review_does_not_send",
                "s_injection_cannot_reach_other_tenant",
                "s_shadow_records_and_does_not_send",
                "s_identical_send_is_suppressed"):
            raise AssertionError("%s bypasses _no_send_result" % name)


def test_calendar_refusal_scenarios_run_under_simulated_adapters():
    """Without them gate 8 refuses the booking before the calendar rule is
    consulted, and 'refused' proves nothing."""
    for name in ("s_cannot_book_unoffered_time", "s_cannot_book_in_the_past"):
        src = ast.get_source_segment(SRC.read_text(), FUNCS[name])
        assert "use_simulated_adapters" in src, name
        assert "_GATE_CODES" in src, name


def test_gate_codes_cover_activation_stage():
    src = SRC.read_text()
    assert "C.DENY_ACTIVATION_STAGE" in src.split("_GATE_CODES = ")[1][:600]
