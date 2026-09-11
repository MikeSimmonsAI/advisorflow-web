"""THE REQUEST TIMEOUT MUST NOT RIDE THE POOLED CONNECTION OUT OF THE REQUEST.

THE CONFIRMED DEFECT THESE GUARD. `app/deps.get_db` applied the per-request
ceiling with

    db.execute(text("SET statement_timeout = 30000"))

`SET` without `LOCAL` is a SESSION setting on the PostgreSQL backend, and a
SQLAlchemy pool hands that same backend connection to whoever borrows it next.
`pool_reset_on_return` issues a ROLLBACK, which does not undo a plain `SET`.
Reproduced on real PostgreSQL: after a request COMMITTED, the reused pooled
connection still carried the 30-second ceiling, and a background query that
borrowed it was cancelled by a timeout belonging to an HTTP request that had
already finished. The rollback path did not reproduce it — the leak was on the
SUCCESSFUL path, which is why it hid.

The fix is `SET LOCAL`, re-applied by an `after_begin` listener so it survives
a mid-request commit, and reverted by PostgreSQL itself at COMMIT or ROLLBACK.

TWO LAYERS OF TEST, ON PURPOSE.

  * The first group runs everywhere, including the SQLite suite, and asserts
    the WIRING: that the ceiling is applied per transaction rather than once
    per session, and that background sessions get no listener at all. A
    refactor that reintroduces a plain `SET` fails these without needing a
    database that can observe it.
  * The second group needs real PostgreSQL, because the leak is a PostgreSQL
    behaviour and nothing else can prove it. It skips without
    TEST_POSTGRES_URL rather than passing vacuously, and it includes a control
    that asserts the OLD mechanism still leaks — if that control ever stops
    failing to clean up, the rest of this file has stopped measuring anything.

RUNNING THE POSTGRES GROUP:

    set TEST_POSTGRES_URL=postgresql+psycopg2://user:pw@host:5432/dbname
    python -m pytest tests/test_request_statement_timeout.py
"""

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.deps import build_pool_kwargs, install_request_statement_timeout

PG_URL = os.environ.get("TEST_POSTGRES_URL")
requires_pg = pytest.mark.skipif(
    not PG_URL,
    reason="TEST_POSTGRES_URL is not set; the pooled-connection leak is a "
           "PostgreSQL behaviour and cannot be observed on SQLite.")

# Deliberately far below anything a real query needs, so "the ceiling is in
# force" is demonstrated by a cancellation rather than inferred from a setting.
REQUEST_CEILING_MS = 250
# Comfortably above the ceiling. A statement that survives this proves the
# ceiling is NOT in force.
SLEEP_SECONDS = 2.0


# ══ WIRING — runs on every backend ══════════════════════════════════════════

def test_get_db_applies_the_ceiling_per_transaction_not_once(monkeypatch):
    """`SET LOCAL` dies with its transaction, so applying it once would leave
    everything after the first `db.commit()` of a request with no ceiling at
    all. The listener is what makes the ceiling cover the whole request."""
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    applied = []

    class _FakeConn:
        def exec_driver_sql(self, sql):
            applied.append(sql)

    engine = create_engine("sqlite:///:memory:")
    session = sessionmaker(bind=engine)()
    install_request_statement_timeout(session, REQUEST_CEILING_MS)

    # Fire the event the way SQLAlchemy does, twice, as a request that commits
    # in the middle would.
    event.contains(session, "after_begin", None)  # attribute existence check
    session.dispatch.after_begin(session, None, _FakeConn())
    session.dispatch.after_begin(session, None, _FakeConn())
    session.close()

    assert len(applied) == 2, (
        "the ceiling was not re-applied on the second transaction")
    for sql in applied:
        assert sql.upper().startswith("SET LOCAL "), (
            "a plain SET is a SESSION setting and leaks to the next borrower: %r"
            % sql)
        assert str(REQUEST_CEILING_MS) in sql


def test_a_background_session_gets_no_listener():
    """`SessionLocal()` on its own — the shape every background job uses — must
    not acquire the request ceiling. This is the distinction the fix preserves:
    the ceiling belongs to `get_db`, not to the engine."""
    from app.deps import SessionLocal

    applied = []

    class _FakeConn:
        def exec_driver_sql(self, sql):
            applied.append(sql)

    session = SessionLocal()
    try:
        session.dispatch.after_begin(session, None, _FakeConn())
    finally:
        session.close()

    assert applied == [], (
        "a background session inherited a request-scoped statement_timeout")


def test_the_ceiling_is_configurable_and_an_integer():
    """The value is inlined into SQL, so it must be an int by construction —
    this is what makes the inlining safe."""
    from app import deps
    assert isinstance(deps.REQUEST_STATEMENT_TIMEOUT_MS, int)


# ══ POSTGRESQL — the only place the leak is observable ══════════════════════

@pytest.fixture()
def pg_engine():
    """ONE connection, no overflow.

    The leak is "the next borrower gets the previous borrower's setting", so
    the test has to guarantee the next borrower gets the SAME backend
    connection. pool_size=1 / max_overflow=0 makes that deterministic instead
    of hopeful.
    """
    kwargs = build_pool_kwargs(PG_URL)
    kwargs.update({"pool_size": 1, "max_overflow": 0})
    engine = create_engine(PG_URL, **kwargs)
    try:
        yield engine
    finally:
        engine.dispose()


def _show_timeout(session):
    return session.execute(text("SHOW statement_timeout")).scalar()


def _sleep(session, seconds=SLEEP_SECONDS):
    session.execute(text("SELECT pg_sleep(:s)"), {"s": seconds})


@requires_pg
def test_pg_the_request_ceiling_applies_where_it_is_intended(pg_engine):
    """A statement inside a request must be cancelled by the request ceiling."""
    from sqlalchemy.exc import DBAPIError

    Factory = sessionmaker(bind=pg_engine)
    db = Factory()
    install_request_statement_timeout(db, REQUEST_CEILING_MS)
    try:
        assert _show_timeout(db) == "250ms"
        with pytest.raises(DBAPIError):
            _sleep(db)
        db.rollback()
    finally:
        db.close()


@requires_pg
def test_pg_a_committed_request_does_not_contaminate_the_next_borrower(pg_engine):
    """THE REPORTED DEFECT, EXACTLY.

    A request applies its ceiling, does work, COMMITS and closes. The next
    borrower of that same pooled connection must see the database's own
    default, not the request's ceiling.
    """
    Factory = sessionmaker(bind=pg_engine)

    request = Factory()
    install_request_statement_timeout(request, REQUEST_CEILING_MS)
    request.execute(text("SELECT 1"))
    request.commit()
    request.close()

    background = Factory()          # no listener: the background-job shape
    try:
        assert _show_timeout(background) == "0", (
            "the request ceiling survived the commit and reached the next "
            "borrower of the pooled connection")
        _sleep(background)          # must NOT be cancelled
        background.commit()
    finally:
        background.close()


@requires_pg
def test_pg_the_rollback_path_is_also_clean(pg_engine):
    """The rollback path did not reproduce the original leak. It must not
    reproduce one now either — the fix has to be safe on both paths, not swap
    which one is broken."""
    Factory = sessionmaker(bind=pg_engine)

    request = Factory()
    install_request_statement_timeout(request, REQUEST_CEILING_MS)
    request.execute(text("SELECT 1"))
    request.rollback()
    request.close()

    background = Factory()
    try:
        assert _show_timeout(background) == "0"
        _sleep(background)
        background.commit()
    finally:
        background.close()


@requires_pg
def test_pg_background_work_keeps_its_intended_timeout_behaviour(pg_engine):
    """A background job sharing the pool with request traffic runs under the
    database's own default. Nothing in `get_db` may narrow it."""
    Factory = sessionmaker(bind=pg_engine)

    for _ in range(3):
        request = Factory()
        install_request_statement_timeout(request, REQUEST_CEILING_MS)
        request.execute(text("SELECT 1"))
        request.commit()
        request.close()

    job = Factory()
    try:
        assert _show_timeout(job) == "0"
        _sleep(job)
        job.commit()
    finally:
        job.close()


@requires_pg
def test_pg_the_ceiling_survives_a_commit_inside_the_same_request(pg_engine):
    """`SET LOCAL` is reverted at COMMIT, so a single application would protect
    only the first transaction of a request. Most write endpoints commit and
    then keep working; those statements must still be covered."""
    from sqlalchemy.exc import DBAPIError

    Factory = sessionmaker(bind=pg_engine)
    db = Factory()
    install_request_statement_timeout(db, REQUEST_CEILING_MS)
    try:
        db.execute(text("SELECT 1"))
        db.commit()                                  # ceiling reverted by PG...
        assert _show_timeout(db) == "250ms"          # ...and re-applied by us
        with pytest.raises(DBAPIError):
            _sleep(db)
        db.rollback()
    finally:
        db.close()


@requires_pg
def test_pg_control_the_old_mechanism_really_did_leak(pg_engine):
    """THE CONTROL.

    Applies the ceiling the way the defective code did — a plain session-level
    `SET` — and asserts that it DOES survive into the next borrower. If this
    ever stops leaking, the assertions above have stopped proving anything and
    somebody should find out why before trusting this file again.

    Cleans up after itself with RESET so it cannot poison the other tests.
    """
    Factory = sessionmaker(bind=pg_engine)

    request = Factory()
    request.execute(text("SET statement_timeout = %d" % REQUEST_CEILING_MS))
    request.execute(text("SELECT 1"))
    request.commit()
    request.close()

    background = Factory()
    try:
        assert _show_timeout(background) == "250ms", (
            "the old mechanism no longer leaks; this control is stale")
    finally:
        background.execute(text("RESET statement_timeout"))
        background.commit()
        background.close()
