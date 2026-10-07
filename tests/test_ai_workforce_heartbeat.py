"""Dependency-free tests: AI Workforce persisted heartbeat / checkpoint truth.

Run: python3 -m unittest tests.test_ai_workforce_heartbeat

Evidence level: BEHAVIOURAL for liveness (run_evidence imported directly) and
for the runtime writers (_start_run/_heartbeat/_finish_run are extracted from
runtime.py with ast and executed against stubs; sqlalchemy is not installed
here). SOURCE-STATIC for call-site wiring, migration and UI. NOT proven: real
DB writes/commits, HTTP tenant isolation, browser rendering.
PENDING pytest (needs sqlalchemy/fastapi): runtime.execute against SQLite
asserting last_heartbeat_at advances per turn and survives a commit; GET
/workforce/runs as org A cannot see org B heartbeat rows.
"""
import ast
import importlib.util
import os
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

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


def run(status="running", started_s=600, hb_s=None, lease=None, ended=False,
        stage=None, **kw):
    return SimpleNamespace(
        id="r1", employee_id="e1", work_item_id=None, status=status,
        mode="live", trigger="scheduled", summary=None, abort_reason=None,
        error=None, iterations=1, tool_calls=0,
        started_at=NOW - timedelta(seconds=started_s),
        ended_at=(NOW - timedelta(seconds=5)) if ended else None,
        last_heartbeat_at=(NOW - timedelta(seconds=hb_s)) if hb_s is not None else None,
        lease_seconds=lease, current_stage=stage, **kw)


class Liveness(unittest.TestCase):
    def test_fresh_heartbeat_prevents_hung_even_if_started_long_ago(self):
        s = RE.shape_run(run(started_s=7200, hb_s=10, lease=120), now=NOW)
        self.assertFalse(s["stale"])
        self.assertEqual(s["liveness_source"], "heartbeat")
        self.assertIsNone(s["stale_reason"])

    def test_lease_crossing_marks_stale_with_reason(self):
        ok = RE.shape_run(run(hb_s=120, lease=120), now=NOW)
        self.assertFalse(ok["stale"])          # exactly at the lease is not past it
        bad = RE.shape_run(run(hb_s=121, lease=120), now=NOW)
        self.assertTrue(bad["stale"])
        self.assertEqual(bad["stale_after_seconds"], 120)
        self.assertIn("No heartbeat", bad["stale_reason"])
        self.assertEqual(bad["liveness_source"], "heartbeat")

    def test_default_lease_when_none_recorded(self):
        s = RE.shape_run(run(hb_s=500, lease=None), now=NOW)
        self.assertTrue(s["stale"])
        self.assertEqual(s["stale_after_seconds"], RE.DEFAULT_LEASE_SECONDS)
        self.assertIsNone(s["lease_seconds"])   # default is not presented as recorded
        self.assertIn("default", s["stale_reason"])

    def test_legacy_fallback_is_labelled(self):
        young = RE.shape_run(run(started_s=600), now=NOW)
        self.assertFalse(young["stale"])
        old = RE.shape_run(run(started_s=16 * 60), now=NOW)
        self.assertTrue(old["stale"])
        self.assertEqual(old["liveness_source"], "started_at_legacy")
        self.assertIn("legacy", old["stale_reason"])
        self.assertIsNone(old["last_heartbeat_at"])

    def test_terminal_never_stale(self):
        for st in ("completed", "failed"):
            s = RE.shape_run(run(status=st, ended=True, started_s=99999,
                                 hb_s=99999, lease=1), now=NOW)
            self.assertFalse(s["stale"])
            self.assertIsNone(s["liveness_source"])
        # an ended_at on a row still marked running is not treated as live either
        s = RE.shape_run(run(ended=True, started_s=99999), now=NOW)
        self.assertFalse(s["stale"])

    def test_no_start_no_heartbeat_stays_unknown(self):
        r = run()
        r.started_at = None
        s = RE.shape_run(r, now=NOW)
        self.assertFalse(s["stale"])
        self.assertIsNone(s["liveness_source"])

    def test_no_invented_progress_or_eta(self):
        s = RE.shape_run(run(hb_s=5, lease=120, stage="planning turn 2"), now=NOW)
        for k in ("progress_percent", "eta", "eta_seconds"):
            self.assertNotIn(k, s)
        self.assertEqual(s["stage"], "planning turn 2")
        self.assertEqual(s["lease_seconds"], 120)

    def test_updated_at_prefers_heartbeat(self):
        s = RE.shape_run(run(hb_s=5), now=NOW)
        self.assertEqual(s["updated_at"], RE._iso(NOW - timedelta(seconds=5)))

    def test_stage_redacted_on_read(self):
        s = RE.shape_run(run(hb_s=1, stage="token=abcd1234efgh5678"), now=NOW)
        self.assertNotIn("abcd1234", s["stage"])

    def test_stale_run_not_current(self):
        lst = [RE.shape_run(run(hb_s=500, lease=120), now=NOW)]
        self.assertIsNone(RE.split_current_history(lst)["current"])
        lst = [RE.shape_run(run(hb_s=5, lease=120), now=NOW)]
        self.assertIsNotNone(RE.split_current_history(lst)["current"])


def _load_writers():
    """Extract the three writer functions from runtime.py and bind stubs."""
    src = _read("app", "services", "workforce", "runtime.py")
    tree = ast.parse(src)
    want = {"_now", "_start_run", "_heartbeat", "_finish_run"}
    body = [n for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name in want]
    consts = [n for n in tree.body if isinstance(n, ast.Assign)
              and any(getattr(t, "id", "") == "STAGE_MAX" for t in n.targets)]
    mod = ast.Module(body=consts + body, type_ignores=[])
    bumps = []
    ns = {
        "datetime": datetime, "Optional": __import__("typing").Optional,
        "AIEmployeeRun": lambda **kw: SimpleNamespace(
            id="run1", ended_at=None, **kw),
        "AIEmployee": object, "AIWorkItem": object, "Session": object,
        "model_router": SimpleNamespace(PLAN="plan"),
        "performance": SimpleNamespace(bump=lambda *a, **k: bumps.append(a)),
        "run_evidence": RE, "_time": __import__("time"),
        "_log": SimpleNamespace(warning=lambda *a, **k: None),
    }
    exec(compile(mod, "runtime_extract", "exec"), ns)
    return ns


class FakeDB:
    def __init__(self, fail=False):
        self.fail, self.flushes, self.nested = fail, 0, 0

    def add(self, o):
        pass

    def flush(self):
        self.flushes += 1
        if self.fail:
            raise RuntimeError("db down")

    def begin_nested(self):
        db = self

        class Ctx:
            def __enter__(s):
                db.nested += 1

            def __exit__(s, et, ev, tb):
                return False        # propagate
        return Ctx()


class WriterPolicy(unittest.TestCase):
    def setUp(self):
        self.ns = _load_writers()
        self.emp = SimpleNamespace(id="e1", organization_id="orgA")

    def start(self, db=None):
        return self.ns["_start_run"](db or FakeDB(), self.emp, None,
                                     objective="x", mode="live",
                                     trigger="manual", lease_seconds=120)

    def test_start_records_initial_heartbeat_stage_lease_and_org(self):
        r = self.start()
        self.assertEqual(r.organization_id, "orgA")
        self.assertIsNotNone(r.last_heartbeat_at)
        self.assertEqual(r.current_stage, "started")
        self.assertEqual(r.lease_seconds, 120)
        self.assertEqual(r.last_heartbeat_at, r.started_at)

    def test_heartbeat_refreshes_and_redacts_and_bounds(self):
        r = self.start()
        before = r.last_heartbeat_at
        r.last_heartbeat_at = before - timedelta(seconds=60)
        db = FakeDB()
        self.assertTrue(self.ns["_heartbeat"](
            db, r, "api_key=sk-abcdefghijk1234 " + "x" * 300))
        self.assertGreater(r.last_heartbeat_at, before - timedelta(seconds=60))
        self.assertNotIn("sk-abcdefghijk", r.current_stage)
        self.assertLessEqual(len(r.current_stage), 80)
        self.assertEqual(db.nested, 1)          # savepoint-scoped

    def test_heartbeat_failure_is_swallowed_and_not_success(self):
        r = self.start()
        db = FakeDB(fail=True)
        self.assertFalse(self.ns["_heartbeat"](db, r, "planning"))
        self.assertEqual(r.status, "running")   # not marked finished/successful
        self.assertIsNone(r.ended_at)

    def test_heartbeat_never_revives_terminal_run(self):
        r = self.start()
        self.ns["_finish_run"](FakeDB(), r, "completed", summary="done")
        hb, stage = r.last_heartbeat_at, r.current_stage
        self.assertFalse(self.ns["_heartbeat"](FakeDB(), r, "planning"))
        self.assertEqual((r.last_heartbeat_at, r.current_stage), (hb, stage))

    def test_finish_records_terminal_state_and_redacts(self):
        r = self.start()
        self.ns["_finish_run"](
            FakeDB(), r, "failed",
            summary="mail bob@example.com or +1 (555) 123-4567",
            error="RuntimeError: password=hunter2hunter2")
        self.assertEqual(r.status, "failed")
        self.assertIsNotNone(r.ended_at)
        self.assertEqual(r.current_stage, "failed")
        self.assertNotIn("bob@example.com", r.summary)
        self.assertNotIn("555", r.summary)
        self.assertNotIn("hunter2", r.error)

    def test_finish_empty_fields_are_null(self):
        r = self.start()
        self.ns["_finish_run"](FakeDB(), r, "completed")
        self.assertIsNone(r.summary)
        self.assertIsNone(r.abort_reason)
        self.assertIsNone(r.error)


class SourceWiring(unittest.TestCase):
    rt = _read("app", "services", "workforce", "runtime.py")

    def test_heartbeat_boundaries_in_execute(self):
        for needle in ('_heartbeat(db, run, "eligibility checked")',
                       'planning turn', 'running tool', 'lease_seconds=max_seconds'):
            self.assertIn(needle, self.rt)

    def test_runtime_does_not_commit_or_retry(self):
        self.assertNotIn(".commit(", self.rt)

    def test_heartbeat_swallows_errors_only_inside_helper(self):
        fn = self.rt[self.rt.index("def _heartbeat"):self.rt.index("def _finish_run")]
        self.assertIn("except Exception", fn)
        self.assertIn("begin_nested", fn)

    def test_every_run_constructor_still_valid(self):
        # new columns are nullable: other writers (simulator) need no change
        sim = _read("app", "services", "workforce", "simulator.py")
        self.assertIn("AIEmployeeRun(organization_id=", sim)
        model = _read("app", "models", "workforce_models.py")
        for col in ("last_heartbeat_at", "current_stage", "lease_seconds"):
            line = [l for l in model.splitlines() if l.strip().startswith(col + " =")]
            self.assertTrue(line and "nullable=True" in line[0], col)
        for bad in ("progress_percent", "eta_seconds"):
            self.assertNotIn(bad, model[model.index("class AIEmployeeRun"):
                                        model.index("class AIToolExecution")])

    def test_org_scoping_preserved(self):
        ev = _read("app", "services", "workforce", "run_evidence.py")
        self.assertGreaterEqual(ev.count("organization_id == organization_id"), 3)


class Migration(unittest.TestCase):
    mig = _read("alembic", "versions", "4e6a8c0b2d1f_add_ai_employee_run_liveness.py")

    def test_additive_nullable_reversible(self):
        self.assertIn('down_revision = "1b3d5f7a9c0e"', self.mig)
        self.assertIn("nullable=True", self.mig)
        self.assertIn("def downgrade", self.mig)
        self.assertIn("op.drop_column", self.mig)
        for bad in ("server_default", "op.execute", "UPDATE", "DROP TABLE",
                    "alter_column"):
            self.assertNotIn(bad, self.mig)

    def test_single_alembic_head(self):
        import glob
        import re
        revs, downs = set(), set()
        for f in glob.glob(_path("alembic", "versions", "*.py")):
            with open(f, encoding="utf-8") as fh:
                t = fh.read()
            revs.add(re.search(r"^revision[^=]*=\s*['\"](\w+)", t, re.M).group(1))
            m = re.search(r"^down_revision[^=]*=\s*['\"](\w+)", t, re.M)
            if m:
                downs.add(m.group(1))
        self.assertEqual(revs - downs, {"4e6a8c0b2d1f"})

    def test_auto_migrate_lists_columns(self):
        am = _read("app", "auto_migrate.py")
        for c, t in (("last_heartbeat_at", "TIMESTAMP"),
                     ("current_stage", "VARCHAR"), ("lease_seconds", "INTEGER")):
            self.assertIn('("ai_employee_runs", "%s", "%s")' % (c, t), am)


class UiIntegration(unittest.TestCase):
    ui = _read("frontend", "src", "components", "WorkforceRunEvidence.jsx")

    def test_polling_and_manual_check(self):
        self.assertIn("setInterval", self.ui)
        self.assertIn("clearInterval", self.ui)
        self.assertIn("POLL_INTERVAL_MS", self.ui)
        self.assertIn("document.hidden", self.ui)
        self.assertIn("Check status now", self.ui)
        self.assertIn("gate.tryStart()", self.ui)

    def test_shows_liveness_evidence_and_staleness(self):
        for needle in ("last_heartbeat_at", "run.stage", "lease_seconds",
                       "stale_reason", "started_at_legacy", "not confirmed live"):
            self.assertIn(needle, self.ui)

    def test_no_percent_or_eta_invented(self):
        self.assertNotIn("eta_seconds", self.ui)
        self.assertIn("evidencedPercent(run.progress_percent)", self.ui)


if __name__ == "__main__":
    unittest.main()
