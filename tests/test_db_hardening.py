"""Database/deploy hardening: the outage class, closed off and pinned.

═══════════════════════════════════════════════════════════════════════════
THE FAILURE BEING PREVENTED
═══════════════════════════════════════════════════════════════════════════
A Postgres session sat `idle in transaction` holding a lock on
`organizations`. Startup-time `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`
queued behind it with no lock timeout, inside one transaction covering all
369 columns. Four consecutive boots deadlocked — including a rollback to the
previous known-good commit — and the backend never bound a port.

The properties that make that impossible now, each asserted below:

  BOUNDED       a contended lock costs seconds, never the boot
  ISOLATED      one blocked statement does not hold the others' locks
  HONEST        core schema missing = refuse to start, loudly
  SURVIVABLE    non-core schema missing = start, and say so
  VISIBLE       the signals are readable before they become an outage
  GATED         migrations run before the app, and can fail the deploy
  NON-DESTRUCTIVE  nothing here ever terminates a database session
"""

import os
import sqlite3
import time
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from app import auto_migrate
from app.auto_migrate import (CORE_TABLES, LAST_RUN, RequiredSchemaMissing,
                              COLUMNS_TO_ADD, run_auto_migrations)
from app.services import db_health


def _sqlite(tmp_path, name="hard.db"):
    return create_engine("sqlite:///%s" % (tmp_path / name))


def _seed_users(engine):
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE users (id TEXT PRIMARY KEY)"))
        conn.commit()


class TestAHeldLockCannotStopTheBoot:
    """A REAL conflicting lock, not a mocked one."""

    def test_a_genuinely_held_write_lock_does_not_hang_startup(self, tmp_path):
        """SQLite's own locking, held by a second connection, for real.

        This is as close as a test suite gets to the production condition
        without a Postgres server: another connection holds a write lock, and
        the migration has to cope. The assertion that matters is not which
        columns landed — it is that `run_auto_migrations` RETURNED, quickly,
        instead of waiting on a lock that a dead client would never release.
        """
        db_file = str(tmp_path / "locked.db")
        # timeout=0 is SQLite's equivalent of the Postgres `lock_timeout` the
        # production path sets: give up rather than wait. Without it SQLite
        # applies its own five-second busy wait PER STATEMENT, which across
        # the full column list is exactly the unbounded-wait shape this whole
        # change exists to remove — the first run of this test hung on it.
        engine = create_engine("sqlite:///%s" % db_file,
                               connect_args={"timeout": 0})
        with engine.connect() as conn:
            conn.execute(text("CREATE TABLE users (id TEXT PRIMARY KEY)"))
            conn.commit()

        subset = [c for c in COLUMNS_TO_ADD if c[0] == "users"][:5]

        # A second, independent connection takes and HOLDS a write lock.
        holder = sqlite3.connect(db_file, timeout=0, isolation_level=None)
        holder.execute("BEGIN IMMEDIATE")
        holder.execute("CREATE TABLE _lock_probe (x INTEGER)")
        try:
            started = time.monotonic()
            with patch.object(auto_migrate, "COLUMNS_TO_ADD", subset), \
                 patch.object(auto_migrate, "_COLUMN_ADD_RETRY_SECONDS", 0):
                try:
                    run_auto_migrations(engine)
                except RequiredSchemaMissing:
                    # A LEGITIMATE OUTCOME, and the point of the test is not
                    # which one happens. `users` is a core table, so a lock
                    # that genuinely prevents its columns landing SHOULD stop
                    # the app — quickly and by raising. What must never
                    # happen is neither: sitting on the lock.
                    pass
            elapsed = time.monotonic() - started
        finally:
            holder.rollback()
            holder.close()

        assert elapsed < 30, (
            "a held lock must resolve in seconds either way — applied, "
            "skipped, or refused — never waited on; took %.1fs" % elapsed)

    def test_sqlite_lock_contention_is_recognised_as_contention(self):
        """"database is locked" is the same condition under another name.

        Without this the SQLite path would classify a genuine, transient lock
        as a permanent answer and never retry it.
        """
        assert auto_migrate._is_lock_contention(
            OperationalError("x", {}, Exception("database is locked")))
        assert auto_migrate._is_lock_contention(
            OperationalError("x", {}, Exception("database table is locked")))


class TestRequiredSchemaFailsFastAndOptionalDoesNot:
    """The judgement call in item 3, both directions."""

    def _fail_column(self, table, column, message="canceling statement due to lock timeout"):
        original = auto_migrate.text

        def _side_effect(sql, *a, **k):
            if isinstance(sql, str) and ("ADD COLUMN %s" % column) in sql \
                    and ("ALTER TABLE %s " % table) in sql:
                raise OperationalError(sql, {}, Exception(message))
            return original(sql, *a, **k)
        return _side_effect

    def test_a_missing_CORE_column_refuses_to_start(self, tmp_path):
        """Serving a platform that cannot authenticate is worse than not
        starting — and much worse than not saying so."""
        engine = _sqlite(tmp_path, "core.db")
        _seed_users(engine)
        target = next(c for c in COLUMNS_TO_ADD if c[0] == "users")
        assert "users" in CORE_TABLES

        with patch.object(auto_migrate, "text",
                          side_effect=self._fail_column("users", target[1])), \
             patch.object(auto_migrate, "_COLUMN_ADD_RETRY_SECONDS", 0):
            with pytest.raises(RequiredSchemaMissing) as caught:
                run_auto_migrations(engine)

        err = caught.value
        assert err.columns == ["users.%s" % target[1]]
        # The remedy is in the message, because this line is what somebody
        # reads at 2am with the platform down.
        assert "will not start" in str(err)
        assert "idle-in-transaction" in str(err)
        assert LAST_RUN["outcome"] == "failed_required"
        assert "users.%s" % target[1] in LAST_RUN["missing_required"]

    def test_the_failure_is_raised_not_swallowed_by_startup(self):
        """`main.on_startup` must let this one through.

        A caught RequiredSchemaMissing would produce the exact thing the
        hardening exists to remove: a process that is alive, reports healthy,
        and 500s every authenticated request.
        """
        import inspect as _inspect
        import app.main as _main

        src = _inspect.getsource(_main.on_startup)
        head = src.split("2a-pre")[0]
        assert "RequiredSchemaMissing" in head
        assert "raise" in head, (
            "startup must re-raise a required-schema failure so the process "
            "exits instead of serving broken")

    def test_the_core_table_set_is_small_on_purpose(self):
        """A large CORE_TABLES rebuilds the outage from the other side.

        Every table listed here is a table whose contention can stop the
        platform from starting. Three is a judgement; thirty would be a
        regression dressed as caution.
        """
        assert CORE_TABLES == frozenset({"users", "organizations", "platforms"})
        assert len(CORE_TABLES) <= 5

    def test_a_missing_NON_core_column_still_starts(self, tmp_path):
        """A broken billing screen beats a dead platform."""
        engine = _sqlite(tmp_path, "noncore.db")
        _seed_users(engine)
        non_core = next(c for c in COLUMNS_TO_ADD if c[0] not in CORE_TABLES)

        with patch.object(auto_migrate, "text",
                          side_effect=self._fail_column(non_core[0], non_core[1])), \
             patch.object(auto_migrate, "_COLUMN_ADD_RETRY_SECONDS", 0):
            run_auto_migrations(engine)     # must not raise

        assert LAST_RUN["outcome"] in ("ok", "degraded")


class TestNothingHereEverKillsASession:
    """Item 12, asserted against the source rather than trusted.

    Automated recovery that terminates database sessions is a worse outage
    than the one it responds to: it fires on a threshold, at 3am, against
    whatever happened to be slow. Clearing a stuck transaction stays a
    deliberate human act on evidence — so the code that WOULD do it must not
    exist, and that is checkable.
    """

    DESTRUCTIVE = ("pg_terminate_backend", "pg_cancel_backend",
                   "drop table", "drop database", "truncate")

    def _executable_source(self, module):
        """The module's CODE AND SQL, with comments and docstrings removed.

        Scanning raw source does not work: both files say in prose that they
        will never terminate a session, and a naive substring search fails on
        the sentence promising the thing it is checking for. Comments are
        stripped, and so are standalone docstring expressions — but ordinary
        string literals are KEPT, because that is where SQL lives and SQL is
        exactly what this test is looking for.
        """
        import ast
        import inspect as _inspect
        import io
        import tokenize

        raw = _inspect.getsource(module)
        no_comments = tokenize.untokenize(
            tok for tok in tokenize.generate_tokens(io.StringIO(raw).readline)
            if tok.type != tokenize.COMMENT)

        tree = ast.parse(no_comments)
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                doc = ast.get_docstring(node, clean=False)
                if doc:
                    docstrings.add(doc)

        out = no_comments
        for doc in docstrings:
            out = out.replace(doc, "")
        return out.lower()

    def test_the_health_service_cannot_terminate_anything(self):
        src = self._executable_source(db_health)
        for token in self.DESTRUCTIVE:
            assert token.lower() not in src, (
                "%s appears in db_health's executable code — recovery must "
                "stay deliberate and human, never automatic" % token)

    def test_the_migration_step_cannot_terminate_anything(self):
        import app.migrate as _migrate
        src = self._executable_source(_migrate)
        for token in self.DESTRUCTIVE:
            assert token.lower() not in src, (
                "%s appears in the migration step's executable code" % token)

    def test_the_scan_would_actually_catch_it(self):
        """A guard nobody has seen fail is a guard nobody should trust."""
        import types
        module = types.ModuleType("fake")
        module.__file__ = "fake.py"
        src = (
            'def recover(conn):\n'
            '    """This docstring mentions nothing."""\n'
            '    conn.execute("SELECT pg_terminate_backend(pid)")\n'
        )
        import inspect as _inspect
        with patch.object(_inspect, "getsource", return_value=src):
            found = self._executable_source(module)
        assert "pg_terminate_backend" in found


class TestTheHealthReportIsUsableAndSafe:
    def test_it_never_returns_query_text(self, tmp_path):
        """Query text can carry customer data, and in the wrong query a
        credential. Contention is reported BY TABLE, which is what makes it
        actionable without leaking anything."""
        engine = _sqlite(tmp_path, "health.db")
        report = db_health.inspect(engine)
        blob = repr(report).lower()
        assert "select " not in blob
        assert "password" not in blob
        assert "query" not in report
        assert report["reachable"] is True

    def test_sqlite_says_not_applicable_rather_than_all_clear(self, tmp_path):
        """A health check that reports green because it cannot see is worse
        than one that says it cannot see."""
        engine = _sqlite(tmp_path, "health2.db")
        report = db_health.inspect(engine)
        assert report["backend"] == "sqlite"
        assert report["unavailable_reason"]
        assert "PostgreSQL-only" in report["unavailable_reason"]

    def test_an_unreachable_database_blocks_and_says_so(self):
        bad = create_engine("postgresql://nobody:nobody@127.0.0.1:1/none",
                            connect_args={"connect_timeout": 1})
        report = db_health.preflight(bad)
        assert report["reachable"] is False
        assert report["safe_to_migrate"] is False
        codes = {b["code"] for b in report["blockers"]}
        assert "database_unreachable" in codes
        # Actionable, not just red.
        assert all(b.get("action") for b in report["blockers"])

    def test_every_blocker_carries_an_action(self):
        """A preflight that says "unhealthy" and stops there gets overridden
        by whoever is trying to ship."""
        report = {
            "reachable": True,
            "thresholds": {"idle_in_transaction_seconds": 120,
                           "lock_wait_seconds": 30},
            "idle_in_transaction": {"over_threshold": 2, "oldest_seconds": 4140,
                                    "tables": []},
            "lock_waits": {"over_threshold": 4, "longest_seconds": 2867,
                           "tables": [{"table": "organizations", "waiters": 4,
                                       "longest_seconds": 2867}]},
            "connections": {"used": 95, "limit": 100, "ratio": 0.95},
        }
        blockers = db_health._blockers(report)
        codes = {b["code"] for b in blockers}
        assert codes == {"idle_in_transaction_sessions", "long_lock_waits",
                         "connection_pressure"}
        for b in blockers:
            assert b["message"] and b["action"]
        # The table that was actually stuck is named, because "a lock is held"
        # is not something anybody can act on.
        assert "organizations" in next(
            b["message"] for b in blockers if b["code"] == "long_lock_waits")


class TestTheDeployIsGatedOnTheMigration:
    """MIGRATION STEP → MIGRATION SUCCESS → APP START."""

    def test_a_blocked_preflight_fails_the_deploy_and_changes_nothing(self):
        import app.migrate as _migrate

        blocked = {
            "reachable": True, "safe_to_migrate": False,
            "idle_in_transaction": {"over_threshold": 1, "oldest_seconds": 4140},
            "lock_waits": {"over_threshold": 0, "longest_seconds": None},
            "connections": {"used": 12, "limit": 100},
            "blockers": [{"code": "idle_in_transaction_sessions",
                          "message": "one stuck session",
                          "action": "end it deliberately"}],
        }
        with patch.object(_migrate, "__name__", _migrate.__name__), \
             patch("app.services.db_health.preflight", return_value=blocked), \
             patch("app.auto_migrate.run_auto_migrations") as ran:
            code = _migrate.main([])

        assert code == _migrate.EXIT_PREFLIGHT_BLOCKED
        assert code != 0, "a blocked preflight must fail the deploy step"
        assert ran.call_count == 0, (
            "nothing may be migrated once the preflight has refused")

    def test_missing_required_schema_fails_the_deploy(self):
        import app.migrate as _migrate

        ok = {"reachable": True, "safe_to_migrate": True, "blockers": [],
              "idle_in_transaction": {"over_threshold": 0, "oldest_seconds": None},
              "lock_waits": {"over_threshold": 0, "longest_seconds": None},
              "connections": {"used": 1, "limit": 100}}
        with patch("app.services.db_health.preflight", return_value=ok), \
             patch("app.models.models.Base.metadata.create_all"), \
             patch("app.auto_migrate.run_auto_migrations",
                   side_effect=RequiredSchemaMissing(["users.session_token"])):
            code = _migrate.main([])

        assert code == _migrate.EXIT_REQUIRED_SCHEMA_MISSING
        assert code != 0

    def test_a_clean_run_exits_zero_so_the_app_may_start(self):
        import app.migrate as _migrate

        ok = {"reachable": True, "safe_to_migrate": True, "blockers": [],
              "idle_in_transaction": {"over_threshold": 0, "oldest_seconds": None},
              "lock_waits": {"over_threshold": 0, "longest_seconds": None},
              "connections": {"used": 1, "limit": 100}}
        with patch("app.services.db_health.preflight", return_value=ok), \
             patch("app.models.models.Base.metadata.create_all"), \
             patch("app.auto_migrate.run_auto_migrations"):
            code = _migrate.main([])
        assert code == _migrate.EXIT_OK

    def test_render_runs_the_migration_before_the_app(self):
        """The ordering is configuration, so it is asserted as configuration.

        Without `preDeployCommand` the gate does not exist no matter how good
        `app/migrate.py` is, and the failure mode is silent.
        """
        import pathlib
        import re
        raw = pathlib.Path("render.yaml").read_text(encoding="utf-8")
        # Strip comments so the prose describing the gate cannot satisfy the
        # assertion that the gate is configured.
        config = "\n".join(l for l in raw.splitlines()
                           if not l.strip().startswith("#"))
        assert "preDeployCommand" in config
        assert "python -m app.migrate" in config

        backend = config.split("name: advisorflow-backend", 1)[1]
        backend = backend.split("- type:", 1)[0]
        assert "preDeployCommand" in backend, \
            "the gate belongs to the service that owns migrations"

    def test_only_one_service_may_change_the_schema(self):
        """advisorflow-voice runs the same ASGI app, so it ran the same DDL.

        Two processes issuing ALTER TABLE against the same tables on separate
        deploy schedules is how two boots end up queued on one lock.
        """
        import pathlib
        raw = pathlib.Path("render.yaml").read_text(encoding="utf-8")
        config = "\n".join(l for l in raw.splitlines()
                           if not l.strip().startswith("#"))
        voice = config.split("name: advisorflow-voice", 1)[1]
        assert "SKIP_STARTUP_MIGRATIONS" in voice


class TestRequestsAreBoundedButJobsAreNot:
    """Item 5, and the distinction that keeps it from breaking real work."""

    def test_the_statement_timeout_is_scoped_to_web_requests(self):
        """`get_db` is the FastAPI dependency; jobs build their own sessions.

        A global `statement_timeout` on the engine would be the easy version
        and would eventually kill a legitimate bulk import mid-flight.
        """
        import inspect as _inspect
        import app.deps as deps

        src = _inspect.getsource(deps.get_db)
        assert "statement_timeout" in src

        # And NOT on the engine, where it would reach the cron jobs too.
        assert "statement_timeout" not in repr(
            deps.build_pool_kwargs("postgresql://u:p@h:5432/d"))

    def test_the_jobs_do_not_go_through_get_db(self):
        """The claim above is only true while this stays true."""
        import pathlib
        for name in ("run_cadence_job.py", "run_email_poller.py",
                     "run_ai_conversation_job.py"):
            src = pathlib.Path("app/jobs") / name
            body = src.read_text(encoding="utf-8")
            assert "SessionLocal" in body
            assert "get_db" not in body, (
                "%s would inherit the request statement_timeout" % name)

    def test_the_value_is_sane_for_a_human_waiting_on_a_page(self):
        import app.deps as deps
        assert 5_000 <= deps.REQUEST_STATEMENT_TIMEOUT_MS <= 120_000

    def test_sqlite_is_untouched(self, tmp_path):
        """SQLite has no statement_timeout; sending one breaks every request."""
        import app.deps as deps
        assert deps.build_pool_kwargs("sqlite:///x.db") == {
            "connect_args": {"check_same_thread": False}}


class TestTheOutcomeIsRecorded:
    def test_a_clean_run_records_what_it_did(self, tmp_path):
        engine = _sqlite(tmp_path, "record.db")
        _seed_users(engine)
        run_auto_migrations(engine)

        assert LAST_RUN["outcome"] in ("ok", "degraded")
        assert LAST_RUN["ran_at"], "an unrecorded run cannot be diagnosed later"
        assert LAST_RUN["columns_checked"] == len(COLUMNS_TO_ADD)
        # ELAPSED TIME: "did the migration take four seconds or four minutes"
        # was the single most useful number during the outage, and nothing
        # was recording it.
        assert LAST_RUN["elapsed_seconds"] is not None
        assert LAST_RUN["elapsed_seconds"] >= 0

    def test_running_twice_is_still_idempotent_and_non_destructive(self, tmp_path):
        """Hardening must not have changed what migrations DO."""
        engine = _sqlite(tmp_path, "twice.db")
        _seed_users(engine)
        run_auto_migrations(engine)
        with engine.connect() as conn:
            first = {r[1] for r in conn.execute(
                text("PRAGMA table_info(users)")).fetchall()}

        run_auto_migrations(engine)     # must not raise, must not change
        with engine.connect() as conn:
            second = {r[1] for r in conn.execute(
                text("PRAGMA table_info(users)")).fetchall()}

        assert first == second
        assert "notification_phone" in second

    def test_an_error_summary_never_carries_the_statement(self):
        """SQLAlchemy's str(e) includes the SQL and can include connection
        details. The log line must not."""
        e = OperationalError(
            "ALTER TABLE users ADD COLUMN x",
            {}, Exception("canceling statement due to lock timeout"))
        summary = auto_migrate._safe_error(e)
        assert "lock timeout" in summary
        assert "ALTER TABLE" not in summary
        assert len(summary) <= 200
