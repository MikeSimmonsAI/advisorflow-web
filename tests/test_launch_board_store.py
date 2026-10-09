"""Issue #21 regression: durable launch-board store (stdlib only).

Run: python3 -m unittest tests.test_launch_board_store

The store's SQL is portable `:name` SQL, so it runs here unchanged against stdlib
sqlite3 through a small Tx adapter. Not covered (no SQLAlchemy/Postgres here): the
SqlAlchemyTx wrapper itself and the Alembic migration run - those are checked
statically (DDL parity with the model) and remain unexecuted.
"""
import ast
import os
import re
import sqlite3
import tempfile
import unittest
from unittest import mock

from app.services import launch_board as lb
from app.services import launch_board_store as st

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

DDL = """
CREATE TABLE launch_board_projects (id INTEGER PRIMARY KEY, name VARCHAR(200) NOT NULL,
  name_key VARCHAR(200) NOT NULL UNIQUE, lane VARCHAR(16) NOT NULL, priority INTEGER NOT NULL,
  version INTEGER NOT NULL, data TEXT NOT NULL, created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE launch_board_events (project_id INTEGER NOT NULL REFERENCES launch_board_projects(id),
  seq INTEGER NOT NULL, at VARCHAR(40) NOT NULL, actor VARCHAR(200) NOT NULL, action VARCHAR(40) NOT NULL,
  detail TEXT NOT NULL, request_key VARCHAR(120) UNIQUE, PRIMARY KEY (project_id, seq));
"""


class SqliteTx:
    """Test Tx over a file-backed sqlite3 database (separate connection per Tx, like sessions)."""

    def __init__(self, path):
        self.c = sqlite3.connect(path, isolation_level=None, timeout=5)
        self.c.row_factory = sqlite3.Row
        self.began = False   # reads autocommit (no lock held), writes open a transaction: READ COMMITTED like Postgres

    def query(self, sql, params=None):
        return [dict(r) for r in self.c.execute(sql, params or {}).fetchall()]

    def execute(self, sql, params=None):
        if not self.began:
            self.c.execute("BEGIN IMMEDIATE")
            self.began = True
        try:
            return self.c.execute(sql, params or {}).rowcount
        except sqlite3.IntegrityError as e:
            raise st.ConflictError(str(e))

    def commit(self):
        if self.began:
            self.c.execute("COMMIT")

    def rollback(self):
        if self.began:
            self.c.execute("ROLLBACK")
            self.began = False

    def close(self):
        self.c.close()


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        self.path = os.path.join(self.d.name, "db.sqlite")
        c = sqlite3.connect(self.path)
        c.executescript(DDL)
        c.close()
        self.store = st.DbStore(lambda: SqliteTx(self.path))

    def add(self, name="A", key=None):
        return self.store.mutate(lambda b: lb.add_project(b, name, actor="mike")["id"], key)

    def count(self, table):
        c = sqlite3.connect(self.path)
        try:
            return c.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
        finally:
            c.close()


class Persistence(Base):
    def test_round_trip_all_state(self):
        pid, _ = self.add("A")
        self.store.mutate(lambda b: lb.approve(b, pid, "mike"))
        self.store.mutate(lambda b: lb.set_lane(b, pid, "active", "mike"))
        self.store.mutate(lambda b: lb.record_evidence(b, pid, "test", "verified", "run-1", "mike"))
        self.store.mutate(lambda b: lb.complete_task(b, pid, "slice 1", "mike"))
        r = lb.view(st.DbStore(lambda: SqliteTx(self.path)).load())["lanes"]["active"][0]  # fresh store
        self.assertEqual((r["name"], r["approved"], r["tasks_completed"]), ("A", True, 1))
        self.assertEqual(r["evidence"]["test"]["ref"], "run-1")
        self.assertEqual(r["product_state"], "in_progress")
        self.assertEqual(r["version"], 5)
        self.assertEqual([h["action"] for h in r["history"]],
                         ["created", "approved", "lane", "evidence", "task_completed"])

    def test_priority_order_and_queue_stable_after_reload(self):
        ids = [self.add(n)[0] for n in ("A", "B", "C")]
        for i in ids:
            self.store.mutate(lambda b, i=i: lb.approve(b, i, "mike"))
        self.store.mutate(lambda b: lb.set_priority(b, ids[2], 1))
        self.store.mutate(lambda b: lb.set_priority(b, ids[0], 2))
        self.store.mutate(lambda b: lb.set_priority(b, ids[1], 2))   # tie -> id order
        v = lb.view(self.store.load())
        self.assertEqual([q["name"] for q in v["queue"]], ["C", "A", "B"])

    def test_archive_keeps_project_and_history(self):
        pid, _ = self.add("A")
        self.store.mutate(lambda b: lb.set_lane(b, pid, "archived", "mike"))
        v = lb.view(self.store.load())
        self.assertEqual(v["summary"]["archived"], 1)
        self.assertEqual(self.count("launch_board_projects"), 1)
        self.assertEqual(self.count("launch_board_events"), 2)

    def test_history_is_append_only_in_storage(self):
        pid, _ = self.add("A")
        self.store.mutate(lambda b: lb.set_priority(b, pid, 3, "mike"))
        before = self.store.load()["projects"][0]["history"]

        def rewrite(b):
            b["projects"][0]["history"][0]["detail"] = "tampered"
        with self.assertRaises(lb.BoardError):
            self.store.mutate(rewrite)
        def truncate(b):
            b["projects"][0]["history"].pop()
        with self.assertRaises(lb.BoardError):
            self.store.mutate(truncate)
        self.assertEqual(self.store.load()["projects"][0]["history"], before)

    def test_duplicate_name_refused_case_insensitive(self):
        self.add("A")
        with self.assertRaises(lb.BoardError):
            self.add("a")
        self.assertEqual(self.count("launch_board_projects"), 1)


class Atomicity(Base):
    def test_refusal_rolls_back_every_change(self):
        pid, _ = self.add("A")

        def bad(b):
            lb.set_priority(b, pid, 9)
            lb.set_lane(b, pid, "active")   # refused: unapproved
        with self.assertRaises(lb.BoardError):
            self.store.mutate(bad)
        p = self.store.load()["projects"][0]
        self.assertEqual((p["priority"], p["lane"], p["version"], len(p["history"])), (1, "backlog", 1, 1))

    def test_failure_mid_write_rolls_back(self):
        pid, _ = self.add("A")
        orig = SqliteTx.execute
        calls = []

        def boom(self_, sql, params=None):
            if sql.startswith("INSERT INTO launch_board_events"):
                calls.append(1)
                raise sqlite3.OperationalError("disk I/O error")
            return orig(self_, sql, params)
        with mock.patch.object(SqliteTx, "execute", boom):
            with self.assertRaises(st.StorageError):
                self.store.mutate(lambda b: lb.set_priority(b, pid, 4, "mike"))
        self.assertTrue(calls)
        p = self.store.load()["projects"][0]
        self.assertEqual((p["priority"], p["version"], len(p["history"])), (1, 1, 1))


class Concurrency(Base):
    def test_stale_writer_cannot_overwrite_newer_state(self):
        pid, _ = self.add("A")

        def slow(b):
            # another writer commits between this writer's load and its write
            self.store.mutate(lambda b2: lb.set_priority(b2, pid, 7, "other"))
            lb.set_priority(b, pid, 3, "me")
        with self.assertRaises(st.StaleError):
            self.store.mutate(slow)
        p = self.store.load()["projects"][0]
        self.assertEqual((p["priority"], p["version"]), (7, 2))   # the newer write survived

    def test_expected_version_mismatch_refused(self):
        pid, _ = self.add("A")
        self.store.mutate(lambda b: lb.set_priority(b, pid, 2))   # version 2
        with self.assertRaises(st.StaleError):
            self.store.mutate(lambda b: lb.set_priority(b, pid, 5), expected={pid: 1})
        self.assertEqual(self.store.load()["projects"][0]["priority"], 2)
        self.store.mutate(lambda b: lb.set_priority(b, pid, 5), expected={pid: 2})
        self.assertEqual(self.store.load()["projects"][0]["priority"], 5)

    def test_racing_history_append_collides_on_primary_key(self):
        pid, _ = self.add("A")
        c = sqlite3.connect(self.path)
        c.execute("INSERT INTO launch_board_events VALUES (?,?,?,?,?,?,NULL)", (pid, 2, "t", "x", "a", "d"))
        c.commit()
        c.close()
        # the store sees seq 2 as free? it loads it, so history length is 2 and the next append is seq 3
        self.store.mutate(lambda b: lb.set_priority(b, pid, 2, "mike"))
        self.assertEqual(self.count("launch_board_events"), 3)
        with self.assertRaises(sqlite3.IntegrityError):
            c = sqlite3.connect(self.path)
            try:
                c.execute("INSERT INTO launch_board_events VALUES (?,?,?,?,?,?,NULL)", (pid, 3, "t", "x", "a", "d"))
            finally:
                c.close()


class Idempotency(Base):
    def test_replayed_add_is_a_noop(self):
        pid, replayed = self.add("A", "req-1")
        pid2, replayed2 = self.add("A", "req-1")
        self.assertEqual((replayed, replayed2, pid2), (False, True, pid))
        self.assertEqual(self.count("launch_board_projects"), 1)
        self.assertEqual(self.count("launch_board_events"), 1)

    def test_replayed_action_applies_once(self):
        pid, _ = self.add("A")
        for _ in range(3):
            self.store.mutate(lambda b: lb.complete_task(b, pid, "x", "mike"), "req-2")
        self.assertEqual(self.store.load()["projects"][0]["tasks_completed"], 1)

    def test_refused_request_key_is_not_consumed(self):
        pid, _ = self.add("A")
        with self.assertRaises(lb.BoardError):
            self.store.mutate(lambda b: lb.set_lane(b, pid, "active"), "req-3")
        self.store.mutate(lambda b: lb.approve(b, pid, "mike"), "req-3")
        self.assertTrue(self.store.load()["projects"][0]["approved"])


class FailClosed(unittest.TestCase):
    def test_missing_schema_is_a_precise_storage_error(self):
        with tempfile.TemporaryDirectory() as d:
            store = st.DbStore(lambda: SqliteTx(os.path.join(d, "empty.sqlite")))
            with self.assertRaises(st.StorageError) as cm:
                store.load()
            self.assertIn("tables are missing", str(cm.exception))
            with self.assertRaises(st.StorageError):
                store.mutate(lambda b: lb.add_project(b, "A"))

    def test_unavailable_db_is_storage_error_not_file_fallback(self):
        def down():
            raise ConnectionError("db down")
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "b.json")
            with mock.patch.dict(os.environ, {"LAUNCH_BOARD_PATH": path}):
                os.environ.pop("LAUNCH_BOARD_BACKEND", None)
                with self.assertRaises(st.StorageError):
                    st.DbStore(down).mutate(lambda b: lb.add_project(b, "A"))
                self.assertFalse(os.path.exists(path))

    def test_default_backend_is_db_and_file_is_explicit(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LAUNCH_BOARD_BACKEND", None)
            os.environ["LAUNCH_BOARD_PATH"] = "/x/b.json"
            self.assertIsInstance(st.get_store(), st.DbStore)
            self.assertEqual(st.storage_status(), {"backend": "db", "durable": True, "warning": None})
            os.environ["LAUNCH_BOARD_BACKEND"] = "bogus"
            self.assertIsInstance(st.get_store(), st.DbStore)
            os.environ["LAUNCH_BOARD_BACKEND"] = "file"
            self.assertIsInstance(st.get_store(), st.FileStore)
            s = st.storage_status()
            self.assertFalse(s["durable"])
            self.assertIn("LAUNCH_BOARD_BACKEND", s["warning"])

    def test_file_adapter_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            fs = st.FileStore(os.path.join(d, "b.json"))
            pid, replayed = fs.mutate(lambda b: lb.add_project(b, "A")["id"])
            self.assertEqual((pid, replayed, len(fs.load()["projects"])), (1, False, 1))


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class Wiring(unittest.TestCase):
    def test_router_god_guarded_routes_use_store_and_map_errors(self):
        src = _read("app", "routers", "god_launch_board_router.py")
        self.assertNotIn("lb.mutate", src)
        self.assertNotIn("lb.load", src)
        self.assertIn("store.get_store()", src)
        for code, exc in (("409", "StaleError"), ("503", "StorageError")):
            self.assertRegex(src, r"except store\.%s[^\n]*\n\s+raise HTTPException\(status_code=%s" % (exc, code))
        self.assertIn("require_god", src)

    def test_ui_shows_storage_warning_and_errors(self):
        s = _read("frontend", "src", "pages", "god", "GodLaunchBoard.jsx")
        self.assertIn("board.storage.warning", s)
        self.assertIn("failedBoard(errorText(e))", s)   # 503 detail (e.g. missing tables) reaches the screen

    def test_gitignore_and_model_registered(self):
        self.assertIn("data/launch_board.json", _read(".gitignore").splitlines())
        self.assertIn("launch_board_models", _read("app", "models", "registry.py"))

    def test_schema_is_additive_new_tables_only(self):
        # SCI staging builds its schema with Base.metadata.create_all (app/migrate.py), not
        # Alembic, so the platform-dev revision 5b7d9f1a3c2e (whose parent is not on this
        # line) is deliberately NOT carried. The model must only ADD two new tables and
        # must not reference any existing table other than through its own FK.
        model = _read("app", "models", "launch_board_models.py")
        self.assertEqual(sorted(re.findall(r'__tablename__ = "([a-z_]+)"', model)),
                         ["launch_board_events", "launch_board_projects"])
        self.assertEqual(re.findall(r'ForeignKey\("([a-z_]+)\.', model), ["launch_board_projects"])
        for col in ("name_key", "lane", "priority", "version", "data", "seq", "request_key", "detail", "actor"):
            self.assertIn("%s = Column" % col, model)
        self.assertNotIn("5b7d9f1a3c2e", _all_migrations())

def _all_migrations():
    d = os.path.join(ROOT, "alembic", "versions")
    return "\n".join(_read("alembic", "versions", f) for f in os.listdir(d) if f.endswith(".py"))


if __name__ == "__main__":
    unittest.main()
