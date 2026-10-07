"""Dependency-free tests: AI Workforce run-evidence contract.

Run: python3 -m unittest tests.test_ai_workforce_run_evidence

Evidence level: BEHAVIOURAL for normalisation, redaction, shaping, lineage and
current/history selection (the module's pure half is imported directly;
sqlalchemy is not installed here). SOURCE-STATIC for org scoping in the query
functions/router and for UI integration. NOT proven: real DB queries, HTTP
tenant isolation, browser rendering. Pending pytest: see
handoff note in SESSION_LOG_T7 / final relay report.
"""
import importlib.util
import re
import os
import unittest
from datetime import datetime, timedelta

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def _path(*p):
    return os.path.join(ROOT, *p)


def _read(*p):
    with open(_path(*p), encoding="utf-8") as f:
        return f.read()


_spec = importlib.util.spec_from_file_location(
    "run_evidence", _path("app", "services", "workforce", "run_evidence.py"))
RE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RE)

NOW = datetime(2026, 10, 7, 16, 0, 0)


class Run:
    def __init__(self, id, status, started_min_ago=1, ended=False, **kw):
        self.id = id
        self.employee_id = kw.get("employee_id", "emp1")
        self.work_item_id = kw.get("work_item_id")
        self.status = status
        self.mode = kw.get("mode", "live")
        self.trigger = kw.get("trigger", "scheduled")
        self.started_at = NOW - timedelta(minutes=started_min_ago)
        self.ended_at = (self.started_at + timedelta(seconds=30)) if ended else None
        self.summary = kw.get("summary")
        self.abort_reason = kw.get("abort_reason")
        self.error = kw.get("error")
        self.iterations = 1
        self.tool_calls = 2


class Normalisation(unittest.TestCase):
    def test_known_states(self):
        for raw, want in [("running", "running"), ("Completed", "completed"),
                          ("failed", "failed"), ("canceled", "cancelled"),
                          ("hung", "stale"), ("blocked", "blocked"),
                          ("skipped", "skipped"), ("queued", "queued"),
                          ("paused", "paused"), ("accepted", "accepted")]:
            self.assertEqual(RE.normalize_state(raw), want)

    def test_missing_or_unrecognised_is_unknown(self):
        for raw in [None, "", "  ", "working", "switched_on", "active", 5]:
            self.assertEqual(RE.normalize_state(raw), "unknown")

    def test_terminal_defined_once(self):
        self.assertEqual(RE.TERMINAL_STATES,
                         {"completed", "failed", "cancelled", "skipped"})
        self.assertFalse(RE.TERMINAL_STATES & RE.ACTIVE_STATES)
        self.assertNotIn("unknown", RE.ACTIVE_STATES)


class Shape(unittest.TestCase):
    def test_absent_facts_are_none_not_invented(self):
        s = RE.shape_run(Run("r1", "running"), now=NOW)
        self.assertIsNone(s["task_label"])
        self.assertIsNone(s["checkpoint_summary"])
        self.assertIsNone(s["ended_at"])
        self.assertNotIn("progress_percent", s)
        self.assertNotIn("eta", s)
        self.assertEqual(s["evidence"]["source"], "ai_employee_runs")
        self.assertTrue(s["started_at"].endswith("Z"))

    def test_running_not_stale_inside_threshold(self):
        s = RE.shape_run(Run("r1", "running", started_min_ago=5), now=NOW)
        self.assertFalse(s["stale"])

    def test_running_without_end_past_threshold_is_stale(self):
        s = RE.shape_run(Run("r1", "running", started_min_ago=20), now=NOW)
        self.assertTrue(s["stale"])
        self.assertEqual(s["stale_after_seconds"], 900)

    def test_terminal_is_never_stale_or_superseded(self):
        s = RE.shape_run(Run("r1", "completed", started_min_ago=999, ended=True),
                         now=NOW, superseded_by="r2")
        self.assertFalse(s["stale"])
        self.assertIsNone(s["superseded_by"])
        self.assertTrue(s["terminal"])

    def test_unknown_source_state_preserved_and_not_active(self):
        s = RE.shape_run(Run("r1", "mystery"), now=NOW)
        self.assertEqual(s["state"], "unknown")
        self.assertEqual(s["source_state"], "mystery")

    def test_simulation_flag(self):
        self.assertTrue(RE.shape_run(Run("r", "running", mode="simulation"),
                                     now=NOW)["simulated"])


class Redaction(unittest.TestCase):
    def test_secrets_emails_phones_removed(self):
        txt = ("sent via sk_live_ABCDEFGH12345 to jane@example.com "
               "call +1 (555) 123-4567 password=hunter2 "
               "Authorization: Bearer abcdefghijklmnop")
        out = RE.redact(txt)
        for leak in ["sk_live", "jane@example.com", "555", "hunter2",
                     "abcdefghijklmnop"]:
            self.assertNotIn(leak, out)

    def test_shape_redacts_summary_error_and_label(self):
        r = Run("r", "failed", ended=True, summary="mail bob@x.io ok",
                error="token=abc123secret failed",
                abort_reason="api_key=zzzzzz")
        s = RE.shape_run(r, job_label="job password=pw1", now=NOW)
        blob = repr(s)
        for leak in ["bob@x.io", "abc123secret", "zzzzzz", "pw1"]:
            self.assertNotIn(leak, blob)

    def test_bounded_length(self):
        self.assertLessEqual(len(RE.redact("a" * 5000)), RE.SUMMARY_MAX)
        self.assertIsNone(RE.redact("   "))
        self.assertIsNone(RE.redact(None))

    def test_no_objective_or_raw_payload_exposed(self):
        s = RE.shape_run(Run("r", "running"), now=NOW)
        for k in ["objective", "prompt", "model_name", "provider", "error_raw"]:
            self.assertNotIn(k, s)


class LineageAndSelection(unittest.TestCase):
    def _shape(self, runs):
        sh = [RE.shape_run(r, now=NOW) for r in runs]
        RE.apply_lineage(sh)
        return sh

    def test_later_run_supersedes_earlier_active_on_same_item(self):
        a = Run("old", "running", started_min_ago=10, work_item_id="w1")
        b = Run("new", "running", started_min_ago=2, work_item_id="w1")
        sh = {s["run_id"]: s for s in self._shape([a, b])}
        self.assertEqual(sh["old"]["superseded_by"], "new")
        self.assertTrue(sh["old"]["stale"])
        self.assertIsNone(sh["new"]["superseded_by"])

    def test_different_item_or_employee_does_not_supersede(self):
        a = Run("a", "running", started_min_ago=10, work_item_id="w1")
        b = Run("b", "running", started_min_ago=2, work_item_id="w2")
        c = Run("c", "running", started_min_ago=1, work_item_id="w1",
                employee_id="emp2")
        sh = {s["run_id"]: s for s in self._shape([a, b, c])}
        self.assertIsNone(sh["a"]["superseded_by"])

    def test_current_is_newest_trusted_active(self):
        runs = [Run("done", "completed", 1, ended=True),
                Run("a", "running", 5), Run("b", "running", 3)]
        out = RE.split_current_history(self._shape(runs))
        self.assertEqual(out["current"]["run_id"], "b")
        ids = [r["run_id"] for r in out["history"]]
        self.assertIn("a", ids)
        self.assertIn("done", ids)
        self.assertNotIn("b", ids)

    def test_terminal_stale_unknown_never_current(self):
        runs = [Run("c", "completed", 1, ended=True),
                Run("s", "running", 30), Run("u", "weird", 2)]
        out = RE.split_current_history(self._shape(runs))
        self.assertIsNone(out["current"])
        self.assertEqual(len(out["history"]), 3)

    def test_empty(self):
        out = RE.split_current_history([])
        self.assertEqual(out, {"current": None, "history": []})


class ScopeEnforcementSource(unittest.TestCase):
    """SOURCE-STATIC: every query filters by organization_id in SQL."""
    SRC = _read("app", "services", "workforce", "run_evidence.py")
    ROUTER = _read("app", "routers", "workforce_router.py")

    def test_every_model_query_filters_org(self):
        import re
        for m in re.finditer(r"db\.query\(", self.SRC):
            window = self.SRC[m.start():m.start() + 500]
            self.assertIn("organization_id ==", window)

    def test_no_org_id_parameter_on_routes(self):
        import re
        for name in ["get_runs", "get_employee_runs", "get_run"]:
            sig = re.search(r"def %s\((.*?)\)\s*->" % name, self.ROUTER, re.S)
            self.assertIsNotNone(sig, name)
            self.assertNotIn("organization_id", sig.group(1))
            self.assertIn("require_tenant_user", sig.group(1))

    def test_missing_and_cross_org_run_same_404(self):
        self.assertIn("if run is None", self.ROUTER)
        self.assertIn("No such run.", self.ROUTER)

    def test_limit_clamped(self):
        self.assertEqual(RE.clamp_limit(10 ** 9), RE.LIST_LIMIT_MAX)
        self.assertEqual(RE.clamp_limit("x"), RE.LIST_LIMIT_DEFAULT)
        self.assertEqual(RE.clamp_limit(0), 1)


class UiIntegrationSource(unittest.TestCase):
    """SOURCE-STATIC: the pages mount the component; component uses helpers."""
    COMP = _read("frontend", "src", "components", "WorkforceRunEvidence.jsx")

    def test_pages_mount_component(self):
        emp = _read("frontend", "src", "pages", "AIWorkforceEmployee.jsx")
        top = _read("frontend", "src", "pages", "AIWorkforce.jsx")
        self.assertIn("<WorkforceRunEvidence employeeId={employeeId}", emp)
        self.assertIn("<WorkforceRunEvidence", top)

    def test_component_uses_truth_helpers(self):
        for h in ["selectActiveRun", "lifecycleLabel", "workerDisplayState",
                  "evidenceLabel", "redact", "evidencedPercent", "applyRefresh",
                  "createSequencer"]:
            self.assertIn(h, self.COMP)

    def test_stale_scope_and_retry_guards(self):
        self.assertIn("scope.current !== startedFor", self.COMP)
        self.assertIn("Retry", self.COMP)
        self.assertIn('role="alert"', self.COMP)
        self.assertIn("may be out of date", self.COMP)

    def test_percent_only_from_explicit_field(self):
        self.assertIn("evidencedPercent(run.progress_percent)", self.COMP)

    def test_fields_conditional(self):
        for f in ["run.task_label", "run.started_at", "run.blocked_reason",
                  "run.checkpoint_summary"]:
            self.assertRegex(self.COMP, r"\{%s\s*\?" % re.escape(f))

    def test_run_record_evidence_label_defined(self):
        self.assertIn("run_record: 'Persisted run record'",
                      _read("frontend", "src", "utils", "workforceTruth.js"))

    def test_no_in_repo_consumer_restores_working_count(self):
        for p in [("app", "services", "workforce_intelligence", "command.py")]:
            self.assertIn('"working": None', _read(*p))


if __name__ == "__main__":
    unittest.main()
