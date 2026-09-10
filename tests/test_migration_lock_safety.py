"""The startup migration must never be able to take the platform down.

═══════════════════════════════════════════════════════════════════════════
THE OUTAGE THESE TESTS EXIST TO PREVENT
═══════════════════════════════════════════════════════════════════════════
A container was replaced mid-request and left a Postgres session `idle in
transaction` on a SELECT against `organizations`, holding ACCESS SHARE with
no client left alive to end it. The next boot asked for ACCESS EXCLUSIVE on
the same table to run

    ALTER TABLE organizations ADD COLUMN IF NOT EXISTS stripe_customer_id

and queued behind it — with no `lock_timeout`, forever. Once a DDL request is
queued, every ordinary SELECT queues behind THAT, so the table froze. Four
consecutive boots deadlocked, including a rollback to the previous
known-good commit, and the API was down for over an hour.

Two properties make that impossible now, and both are asserted here:

  BOUNDED — every column-add is capped by attempts x lock_timeout, so the
  migration step cannot add unbounded time to boot no matter how contended
  the database is.

  NON-FATAL — a column that cannot be added is logged and skipped, and the
  remaining columns still get their chance. The service comes up.

The third property, the one that stops the orphan existing at all, is
`idle_in_transaction_session_timeout` on the engine — asserted at the bottom.
"""

import time
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from app import auto_migrate
from app.auto_migrate import COLUMNS_TO_ADD, run_auto_migrations


def _sqlite(tmp_path, name="lock.db"):
    return create_engine("sqlite:///%s" % (tmp_path / name))


class TestALockedTableCannotHangTheBoot:
    def test_a_column_that_never_gets_its_lock_does_not_raise(self, tmp_path):
        """The whole outage in one assertion: startup completes anyway.

        `run_auto_migrations` is what `main.on_startup` calls before uvicorn
        binds a port. If it raises or blocks, the service never comes up — so
        a permanently-unavailable lock must end in a log line, not an
        exception and not a wait.
        """
        engine = _sqlite(tmp_path)
        with engine.connect() as conn:
            conn.execute(text("CREATE TABLE users (id TEXT PRIMARY KEY)"))
            conn.commit()

        original = auto_migrate.text

        def _every_alter_is_locked(sql, *a, **k):
            if isinstance(sql, str) and sql.lstrip().upper().startswith("ALTER TABLE"):
                raise OperationalError(sql, {}, Exception(
                    "canceling statement due to lock timeout"))
            return original(sql, *a, **k)

        started = time.monotonic()
        with patch.object(auto_migrate, "text",
                          side_effect=_every_alter_is_locked), \
             patch.object(auto_migrate, "_COLUMN_ADD_RETRY_SECONDS", 0):
            run_auto_migrations(engine)   # must return, must not raise
        elapsed = time.monotonic() - started

        # Bounded in practice, not just in principle. With the retry pause
        # patched to zero the only cost left is the attempts themselves.
        assert elapsed < 60, (
            "a fully contended database must not turn startup into a wait; "
            "took %.1fs" % elapsed)

    def test_an_error_that_is_not_contention_is_not_retried(self, tmp_path):
        """The bug the first version of this change shipped with.

        Retrying every failure meant that on a database where most tables do
        not exist yet, several hundred permanent errors each slept between
        attempts — turning a fast no-op into minutes of dead boot time. That
        is the same outage from the other side, so it gets its own test.
        """
        engine = _sqlite(tmp_path)
        with engine.connect() as conn:
            conn.execute(text("CREATE TABLE users (id TEXT PRIMARY KEY)"))
            conn.commit()

        target = next(c for c in COLUMNS_TO_ADD if c[0] == "users")
        original = auto_migrate.text
        attempts = {"n": 0}

        def _permanent_failure(sql, *a, **k):
            if isinstance(sql, str) and ("ADD COLUMN %s" % target[1]) in sql:
                attempts["n"] += 1
                raise OperationalError(sql, {}, Exception("no such table: users"))
            return original(sql, *a, **k)

        with patch.object(auto_migrate, "text", side_effect=_permanent_failure):
            run_auto_migrations(engine)

        assert attempts["n"] == 1, (
            "a permanent error is an answer, not a delay — it must be taken "
            "on the first attempt")

    def test_contention_is_told_apart_from_a_definite_answer(self):
        """The predicate itself, since everything above depends on it."""
        class _Orig:
            def __init__(self, code):
                self.pgcode = code

        lock_timeout = OperationalError("x", {}, _Orig("55P03"))
        deadlock = OperationalError("x", {}, _Orig("40P01"))
        no_table = OperationalError("x", {}, _Orig("42P01"))

        assert auto_migrate._is_lock_contention(lock_timeout)
        assert auto_migrate._is_lock_contention(deadlock)
        assert not auto_migrate._is_lock_contention(no_table), \
            "an undefined table will never become defined by waiting"

        # SQLite and other drivers carry no SQLSTATE; fall back to the text.
        assert auto_migrate._is_lock_contention(
            OperationalError("x", {}, Exception("canceling statement due to "
                                                "lock timeout")))
        assert not auto_migrate._is_lock_contention(
            OperationalError("x", {}, Exception("syntax error at or near")))

    def test_the_work_is_bounded_by_attempts_not_by_the_database(self):
        """No unbounded wait exists anywhere in the column-add path.

        The defect was not "a lock was held" — locks are held all the time.
        It was that the wait had no ceiling. These two constants ARE the
        ceiling, so they are asserted directly: a future edit that removes
        the bound has to delete a test that says why it is there.
        """
        assert auto_migrate._COLUMN_ADD_ATTEMPTS >= 1
        assert auto_migrate._COLUMN_ADD_ATTEMPTS <= 5, \
            "more attempts is more boot time; the point is a small ceiling"
        assert auto_migrate._COLUMN_ADD_RETRY_SECONDS <= 2

    def test_a_slow_column_does_not_block_the_columns_after_it(self, tmp_path):
        """Per-statement commits, proven by outcome rather than by reading.

        The original code ran every ALTER in ONE transaction, so a lock taken
        for column 1 was still held while column 40 waited. One contended
        table therefore froze every table in the list. Here the first column
        fails and the rest must still land.
        """
        engine = _sqlite(tmp_path)
        with engine.connect() as conn:
            conn.execute(text("CREATE TABLE users (id TEXT PRIMARY KEY)"))
            conn.commit()

        target = [c for c in COLUMNS_TO_ADD if c[0] == "users"]
        assert len(target) > 2, "fixture assumes several user columns"
        first_col = target[0][1]
        later_cols = [c[1] for c in target[1:]]

        original = auto_migrate.text
        state = {"n": 0}

        def _fail_only_the_first(sql, *a, **k):
            if isinstance(sql, str) and ("ADD COLUMN %s" % first_col) in sql:
                state["n"] += 1
                raise OperationalError(sql, {}, Exception("lock timeout"))
            return original(sql, *a, **k)

        with patch.object(auto_migrate, "text", side_effect=_fail_only_the_first), \
             patch.object(auto_migrate, "_COLUMN_ADD_RETRY_SECONDS", 0):
            run_auto_migrations(engine)

        with engine.connect() as conn:
            cols = {r[1] for r in conn.execute(
                text("PRAGMA table_info(users)")).fetchall()}

        assert first_col not in cols, "fixture did not actually block it"
        assert state["n"] == auto_migrate._COLUMN_ADD_ATTEMPTS, \
            "the blocked column should be retried, and only a bounded number of times"
        for c in later_cols:
            assert c in cols, (
                "%s was lost because an earlier column failed — that is the "
                "one-big-transaction defect" % c)


class TestTheOrphanedTransactionCannotSurvive:
    """The root cause, closed at the source rather than at the symptom.

    Everything above keeps a boot alive while a lock is held. This is what
    stops the lock being held in the first place: an abandoned transaction is
    terminated by Postgres itself instead of outliving the container that
    opened it.
    """

    def test_postgres_sessions_carry_an_idle_in_transaction_timeout(self):
        from app.deps import build_pool_kwargs

        opts = build_pool_kwargs(
            "postgresql://u:p@h:5432/d")["connect_args"]["options"]

        assert "idle_in_transaction_session_timeout" in opts, (
            "without this, a session left idle in a transaction by a dying "
            "container holds its locks forever — which is exactly what took "
            "the platform down")

        # Parsed rather than string-matched, so the assertion is about the
        # VALUE being sane and not about how the flag happens to be spelled.
        ms = int(opts.split("idle_in_transaction_session_timeout=")[1].split()[0])
        assert ms > 0, "0 disables the timeout entirely"
        assert 60_000 <= ms <= 600_000, (
            "under a minute risks killing a request waiting on a slow external "
            "call inside a transaction; over ten minutes stops being a "
            "meaningful bound")

    def test_sqlite_is_left_alone(self):
        """The option is Postgres-only; sending it to SQLite breaks connect."""
        from app.deps import build_pool_kwargs

        args = build_pool_kwargs("sqlite:///./x.db")["connect_args"]
        assert "options" not in args
        assert args == {"check_same_thread": False}

    def test_the_settings_are_read_through_a_pure_function(self):
        """Why this is a function at all, pinned so it stays one.

        The first version of these tests asserted the same thing by reloading
        `app.deps` with a Postgres URL. That rebinds `engine` and
        `SessionLocal` while every module that already imported them keeps the
        old objects — which detached a large part of the suite from its
        database and produced dozens of unrelated failures. Configuration that
        needs asserting should be reachable without reloading the module that
        owns the connection.
        """
        import app.deps as deps

        assert callable(deps.build_pool_kwargs)
        assert deps._pool_kwargs == deps.build_pool_kwargs(deps.DATABASE_URL)
