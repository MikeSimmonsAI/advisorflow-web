"""Dependency-free tests: run telemetry runs in its OWN transaction.

Run: python3 -m unittest tests.test_ai_workforce_run_telemetry

Evidence level: BEHAVIOURAL against an in-memory session stub that models
committed vs pending state per session (sqlalchemy is not installed here).
NOT proven: real SQLite/Postgres concurrent-session visibility, lock waits,
the after_rollback listener against a real Session, HTTP, browser.
PENDING pytest (needs sqlalchemy): two SessionLocal sessions on a SQLite file
DB: caller flushes work-item changes uncommitted; a telemetry heartbeat commit
is visible to a third session while the caller's changes are not; caller
rollback leaves the heartbeat row but no work-item change; Session
`after_rollback` flips completed -> failed.
"""
import importlib.util
import os
import sys
import types
import unittest
from datetime import datetime, timedelta

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def _load(name, *p):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, *p))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


RE = _load("run_evidence", "app", "services", "workforce", "run_evidence.py")
# resolve `from app.services.workforce import run_evidence` without the
# package __init__ (which needs sqlalchemy)
for _n in ("app", "app.services", "app.services.workforce"):
    sys.modules.setdefault(_n, types.ModuleType(_n))
sys.modules["app.services.workforce"].run_evidence = RE
RT = _load("run_telemetry_under_test", "app", "services", "workforce",
           "run_telemetry.py")


class Col:
    def __init__(self, name):
        self.name = name

    def __eq__(self, v):
        return lambda r: r.get(self.name) == v

    def is_(self, v):
        return lambda r: r.get(self.name) is v

    __hash__ = object.__hash__


class Model:
    _n = 0
    id = Col("id")
    organization_id = Col("organization_id")
    status = Col("status")
    ended_at = Col("ended_at")

    def __init__(self, **kw):
        Model._n += 1
        self.row = dict(kw, rid="run%d" % Model._n, ended_at=None)
        self.row["id"] = self.row["rid"]
        self.rid = self.row["rid"]
        self.__dict__["id"] = self.rid        # instance id shadows the Col


class Store:
    def __init__(self):
        self.committed = {}
        self.sessions = []
        self.fail_update = False
        self.fail_insert = False


class Query:
    def __init__(self, s):
        self.s, self.preds = s, []

    def filter(self, *p):
        self.preds += p
        return self

    def update(self, values, synchronize_session=False):
        if self.s.store.fail_update:
            raise RuntimeError("lock timeout")
        n = 0
        for r in self.s.store.committed.values():   # COMMITTED state only
            if all(p(r) for p in self.preds):
                self.s.pending.append((r, values))
                n += 1
        return n


class Session:
    def __init__(self, store):
        self.store = store
        self.pending, self.added = [], []
        self.commits = self.rollbacks = 0
        self.closed = False
        store.sessions.append(self)

    def add(self, m):
        self.added.append(m)

    def flush(self):
        if self.store.fail_insert:
            raise RuntimeError("fk violation")

    def query(self, _m):
        return Query(self)

    def commit(self):
        for m in self.added:
            self.store.committed[m.rid] = m.row
        for r, v in self.pending:
            r.update(v)
        self.commits += 1
        self.pending, self.added = [], []

    def rollback(self):
        self.rollbacks += 1
        self.pending, self.added = [], []

    def close(self):
        self.closed = True


class Base(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        RT._session_factory = lambda: Session(self.store)
        RT._model = lambda: Model
        self.kw = dict(organization_id="orgA", employee_id="e1",
                       work_item_id="w1", objective="obj", mode="live",
                       trigger="manual", capability="plan", lease_seconds=120)

    def tearDown(self):
        RT._session_factory = None

    def row(self, h):
        return self.store.committed[h.id]


class StartVisibility(Base):
    def test_row_committed_before_execution_in_own_session(self):
        h = RT.start(**self.kw)
        self.assertIn(h.id, self.store.committed)     # visible to others now
        r = self.row(h)
        self.assertEqual((r["organization_id"], r["status"],
                          r["current_stage"], r["lease_seconds"]),
                         ("orgA", "running", "started", 120))
        s = self.store.sessions[0]
        self.assertEqual((s.commits, s.closed), (1, True))

    def test_start_failure_returns_none_and_rolls_back(self):
        self.store.fail_insert = True
        self.assertIsNone(RT.start(**self.kw))
        s = self.store.sessions[0]
        self.assertEqual((s.rollbacks, s.closed), (1, True))
        self.assertEqual(self.store.committed, {})

    def test_handle_is_independent_non_orm(self):
        h = RT.start(**self.kw)
        self.assertTrue(h.independent)
        self.assertEqual((h.tool_calls, h.denied_tool_calls), (0, 0))


class Heartbeat(Base):
    def test_heartbeat_commits_own_transaction_with_counters(self):
        h = RT.start(**self.kw)
        h.tool_calls, h.iterations = 2, 3
        self.assertTrue(RT.heartbeat(h, "planning turn 3"))
        r = self.row(h)
        self.assertEqual((r["tool_calls"], r["iterations"],
                          r["current_stage"]), (2, 3, "planning turn 3"))
        hb = self.store.sessions[-1]
        self.assertEqual((hb.commits, hb.closed), (1, True))
        self.assertIsNot(hb, self.store.sessions[0])

    def test_heartbeat_redacts_and_bounds_stage(self):
        h = RT.start(**self.kw)
        RT.heartbeat(h, "api_key=sk-abcdefghijk1234 " + "x" * 300)
        st = self.row(h)["current_stage"]
        self.assertNotIn("sk-abcdefghijk", st)
        self.assertLessEqual(len(st), 80)

    def test_update_is_org_scoped(self):
        h = RT.start(**self.kw)
        evil = RT.RunHandle(h.id, "orgB", datetime.utcnow())
        self.assertFalse(RT.heartbeat(evil, "hijack"))
        self.assertEqual(self.row(h)["current_stage"], "started")
        self.assertFalse(RT.finalize(evil, "failed"))
        self.assertEqual(self.row(h)["status"], "running")

    def test_timestamp_never_regresses(self):
        h = RT.start(**self.kw)
        future = datetime.utcnow() + timedelta(hours=1)
        h.last_heartbeat_at = future
        RT.heartbeat(h, "x")
        self.assertGreaterEqual(self.row(h)["last_heartbeat_at"], future)

    def test_failure_isolated_not_success_not_retried(self):
        h = RT.start(**self.kw)
        before = len(self.store.sessions)
        self.store.fail_update = True
        self.assertFalse(RT.heartbeat(h, "x"))
        self.assertEqual(len(self.store.sessions), before + 1)  # one try only
        self.assertEqual(self.store.sessions[-1].rollbacks, 1)
        self.assertEqual((h.status, self.row(h)["status"]),
                         ("running", "running"))
        self.assertFalse(RT.finalize(h, "completed"))
        self.assertIsNone(h.ended_at)       # a failed write is not terminal


class Finalize(Base):
    def test_finalize_writes_terminal_once(self):
        h = RT.start(**self.kw)
        self.assertTrue(RT.finalize(h, "completed",
                                    summary="ok bob@example.com",
                                    duration_ms=5))
        r = self.row(h)
        self.assertEqual((r["status"], r["current_stage"]),
                         ("completed", "completed"))
        self.assertIsNotNone(r["ended_at"])
        self.assertNotIn("bob@example.com", r["summary"])
        n = len(self.store.sessions)
        self.assertFalse(RT.finalize(h, "failed", error="late"))
        self.assertEqual(len(self.store.sessions), n)     # no second write
        self.assertEqual(self.row(h)["status"], "completed")

    def test_terminal_not_revived_by_heartbeat(self):
        h = RT.start(**self.kw)
        RT.finalize(h, "failed", error="boom")
        snap = dict(self.row(h))
        self.assertFalse(RT.heartbeat(h, "planning"))
        self.assertEqual(self.row(h), snap)

    def test_db_guard_blocks_stale_handle_overwrite(self):
        h = RT.start(**self.kw)
        stale = RT.RunHandle(h.id, "orgA", h.started_at)
        RT.finalize(h, "completed")
        self.assertFalse(RT.finalize(stale, "failed", error="x"))
        self.assertFalse(RT.heartbeat(stale, "zombie"))
        self.assertEqual(self.row(h)["status"], "completed")

    def test_rollback_correction_only_completed_to_failed(self):
        h = RT.start(**self.kw)
        self.assertFalse(RT.correct_after_rollback(h))     # still running
        RT.finalize(h, "completed")
        self.assertTrue(RT.correct_after_rollback(h))
        self.assertEqual(self.row(h)["status"], "failed")
        self.assertIn("rolled back", self.row(h)["error"])
        h2 = RT.start(**self.kw)
        RT.finalize(h2, "failed", error="real")
        self.assertFalse(RT.correct_after_rollback(h2))
        self.assertEqual(self.row(h2)["error"], "real")


class NoCallerCoupling(Base):
    def test_telemetry_never_commits_a_caller_session(self):
        caller = Session(self.store)
        caller.pending.append(("business", {}))
        h = RT.start(**self.kw)
        RT.heartbeat(h, "x")
        RT.finalize(h, "completed")
        self.assertEqual((caller.commits, caller.rollbacks), (0, 0))
        self.assertEqual(caller.pending, [("business", {})])
        others = [s for s in self.store.sessions if s is not caller]
        self.assertTrue(others and all(s.closed for s in others))


class RuntimeWiring(unittest.TestCase):
    rt = open(os.path.join(ROOT, "app", "services", "workforce",
                           "runtime.py"), encoding="utf-8").read()

    def test_runtime_still_never_commits(self):
        self.assertNotIn(".commit(", self.rt)

    def test_start_prefers_independent_then_falls_back(self):
        fn = self.rt[self.rt.index("def _start_run"):
                     self.rt.index("def _is_independent")]
        self.assertLess(fn.index("run_telemetry.start"), fn.index("db.add(run)"))

    def test_heartbeat_and_finish_route_independent_runs(self):
        for name, nxt in (("def _heartbeat", "def _finish_run"),
                          ("def _finish_run", "def ensure_eligibility")):
            fn = self.rt[self.rt.index(name):self.rt.index(nxt)]
            self.assertIn("_is_independent(run)", fn)
            self.assertIn("run_telemetry.", fn)

    def test_rollback_watch_installed_per_run(self):
        self.assertIn("_watch_rollback(db, run)", self.rt)
        self.assertIn('"after_rollback"', self.rt)

    def test_execute_reports_failed_on_exception(self):
        self.assertIn('_finish_run(db, run, "failed"', self.rt)

    def test_telemetry_module_never_takes_or_commits_caller_session(self):
        src = open(os.path.join(ROOT, "app", "services", "workforce",
                                "run_telemetry.py"), encoding="utf-8").read()
        self.assertNotIn("db.commit", src)
        self.assertNotIn("def start(db", src)


if __name__ == "__main__":
    unittest.main()
