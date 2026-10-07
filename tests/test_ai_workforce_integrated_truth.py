"""Dependency-free tests: lifecycle truth wired into real AI Workforce surfaces.

Run: python3 -m unittest tests.test_ai_workforce_integrated_truth

Evidence level: SOURCE-STATIC for the JSX assertions (the files contain the
guards); BEHAVIOURAL for the backend statement functions, which are extracted
from source with `ast` and executed against synthetic deployments (sqlalchemy
is not installed in this environment, so the modules cannot be imported).
Neither proves runtime tenant isolation or browser behaviour.
"""
import ast
import os
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def _read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
        return f.read()


def _extract(path, names, env):
    tree = ast.parse(_read(*path))
    wanted = [n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name in names]
    mod = ast.Module(body=wanted, type_ignores=[])
    ns = dict(env)
    exec(compile(mod, "/".join(path), "exec"), ns)
    return ns


class _C:
    EV_FACT, EV_UNKNOWN = "fact", "unknown"
    SEV_CRITICAL = "critical"
    DEPLOY_LIVE_STATES = frozenset({"controlled", "active"})
    DEPLOY_SUSPENDED = "suspended"


class _D:
    def __init__(self, state):
        self.state = state


EXEC = _extract(("app", "services", "workforce_intelligence", "executive.py"),
                {"_status_statement"}, {"C": _C})
CMD = _extract(("app", "services", "workforce_intelligence", "command.py"),
               {"_workforce_statement"}, {"C": _C})

NO_ATTENTION = {"by_severity": {}}


class BackendNeverClaimsWorking(unittest.TestCase):
    def test_executive_statement_says_switched_on(self):
        live = [_D("active"), _D("controlled")]
        deps = live + [_D("paused")]
        text = EXEC["_status_statement"](deps, live, NO_ATTENTION)
        self.assertIn("switched on", text)
        self.assertNotIn("working", text)

    def test_executive_statement_none_live(self):
        text = EXEC["_status_statement"]([_D("paused")], [], NO_ATTENTION)
        self.assertIn("none of them is switched on", text)
        self.assertNotIn("working", text)

    def test_executive_critical_still_surfaced(self):
        live = [_D("active")]
        text = EXEC["_status_statement"](
            live, live, {"by_severity": {"critical": 2}})
        self.assertIn("switched on", text)
        self.assertIn("2 things cannot wait", text)

    def test_command_statement_explains_permission_vs_work(self):
        live = [_D("active")]
        text = CMD["_workforce_statement"](live, live, [])
        self.assertIn("switched on", text)
        self.assertIn("not evidence", text)
        self.assertNotIn("is working", text)

    def test_command_statement_none_live(self):
        text = CMD["_workforce_statement"]([_D("paused")], [], [])
        self.assertIn("None of them is switched on", text)

    def test_payload_working_is_unknown_not_zero(self):
        src = _read("app", "services", "workforce_intelligence", "command.py")
        self.assertIn('"working": None', src)
        self.assertIn('"switched_on"', src)
        ex = _read("app", "services", "workforce_intelligence", "executive.py")
        self.assertIn("C.EV_UNKNOWN", ex.split('"working": _classed(')[1][:200])


class FrontendIntegration(unittest.TestCase):
    def setUp(self):
        self.pages = {n: _read("frontend", "src", "pages", n + ".jsx")
                      for n in ("AIWorkforceCommand", "AIWorkforceEmployee",
                                "AITeam", "AIWorkforce")}

    def test_every_surface_imports_the_helpers(self):
        for name, src in self.pages.items():
            self.assertIn("utils/workforceTruth", src, name)

    def test_command_working_tile_is_switched_on(self):
        src = self.pages["AIWorkforceCommand"]
        self.assertNotIn('label="Working"', src)
        self.assertIn('label="Switched on"', src)
        self.assertIn("data.workforce?.switched_on", src)

    def test_team_page_never_labels_activation_as_working(self):
        src = self.pages["AITeam"]
        self.assertNotIn("active: 'Working'", src)
        self.assertNotIn("working: 'Working'", src)
        self.assertIn("active: 'Switched on'", src)

    def test_stale_and_latest_wins_on_every_loader(self):
        for name in ("AIWorkforceCommand", "AIWorkforceEmployee", "AITeam"):
            src = self.pages[name]
            self.assertIn("loadSeq.isCurrent(token)", src, name)
            self.assertIn("applyRefresh(lastGood.current, outcome)", src, name)
            self.assertIn("mounted.current", src, name)

    def test_employee_switch_clears_previous_employee_detail(self):
        src = self.pages["AIWorkforceEmployee"]
        self.assertIn("setCard(null); setAttention([]); setQuality(null)", src)
        self.assertIn("scopeRef.current !== employeeId", src)
        self.assertIn("scopeRef.current !== startedFor", src)
        self.assertIn("const key = `${what}:${deploymentId}`", src)

    def test_command_actions_are_locked_per_item_not_globally(self):
        src = self.pages["AIWorkforceCommand"]
        self.assertIn("guard.tryAcquire(key)", src)
        self.assertIn("`act:${item.id}:${what}`", src)
        self.assertIn("`escalate:${item.id}`", src)
        self.assertEqual(src.count("guard.release(key)"), 1)

    def test_hire_is_locked_per_template(self):
        src = self.pages["AITeam"]
        self.assertIn("`hire:${templateKey}`", src)
        self.assertIn("guard.release(key)", src)

    def test_unknown_open_work_is_not_zero(self):
        src = self.pages["AIWorkforceEmployee"]
        self.assertNotIn("open_work_items ?? 0", src)
        self.assertIn("'Unknown'", src)


if __name__ == "__main__":
    unittest.main()
