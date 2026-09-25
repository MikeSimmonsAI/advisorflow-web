"""THE RACE. $1.00/day, six workers each about to spend $0.20 at the same moment.

A read-then-write budget check lets two workers both see $0.80 spent and both
buy. The atomic conditional UPDATE in `budget.reserve` must let exactly five
through, and the ledger must say exactly $1.00.

This needs real concurrency against a real file database — the in-memory
StaticPool fixture serializes everything through one connection and would
prove nothing — so it builds its own engine.
"""
import os
import threading
import time

import pytest
from sqlalchemy.exc import OperationalError

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.models.models import Base, Organization
import app.models.registry  # noqa: F401  (every table)
from app.models.evosense_models import EvoSenseBudgetCounter, EvoSenseCostEntry, EvoSenseStrategy
from app.services.evosense import budget as B
from app.services.evosense import common as C


def _backends():
    yield "sqlite"
    if os.environ.get("EVOSENSE_TEST_PG_URL"):
        yield "postgres"


def _engine(backend, tmp_path):
    if backend == "postgres":
        from sqlalchemy import text
        url = os.environ["EVOSENSE_TEST_PG_URL"]
        eng = create_engine(url, pool_size=10)
        with eng.begin() as c:
            c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
        return eng
    eng = create_engine("sqlite:///%s" % (tmp_path / "race.db"),
                        connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(eng, "connect")
    def _wal(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA journal_mode=WAL")
    return eng


@pytest.mark.parametrize("backend", list(_backends()))
def test_six_concurrent_twenty_cent_lookups_on_a_one_dollar_budget(backend, tmp_path):
    """Six real threads, six sessions, released together by a barrier.

    PostgreSQL runs when EVOSENSE_TEST_PG_URL is set (READ COMMITTED - the
    isolation level where a read-then-write check really does double-spend).
    SQLite always runs; SQLite refuses a second concurrent writer with
    "database is locked", so a worker that hits that retries its whole
    transaction, exactly as the hunt job would. Either way: five succeed."""
    engine = _engine(backend, tmp_path)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    s = Session()
    org = Organization(name="Race Org", slug="race-org", plan="standard", industry="real_estate")
    s.add(org)
    s.flush()
    strat = EvoSenseStrategy(organization_id=org.id, name="Race (TEST)", status="active",
                             daily_budget_cents=100, is_test=True)
    s.add(strat)
    C.controls(s, org.id)
    s.commit()
    org_id, strat_id = org.id, strat.id
    s.close()

    barrier = threading.Barrier(6)
    results = []
    lock = threading.Lock()

    def worker(i):
        barrier.wait()
        for attempt in range(200):
            db = Session()
            try:
                st = db.query(EvoSenseStrategy).get(strat_id)
                ok, why, entry = B.reserve(db, org_id, st, 20, provider_key="sandbox_skiptrace",
                                           connector_kind="sandbox", capability="CONTACT_ENRICHMENT",
                                           operation="race", is_test=True)
                if ok:
                    B.charge(db, entry)
                db.commit()
                with lock:
                    results.append((ok, why))
                return
            except OperationalError as exc:
                db.rollback()
                if "locked" not in str(exc):
                    with lock:
                        results.append(("error", repr(exc)))
                    return
                time.sleep(0.01)
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                with lock:
                    results.append(("error", repr(exc)))
                return
            finally:
                db.close()
        with lock:
            results.append(("error", "gave up"))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not [r for r in results if r[0] == "error"], results
    assert len(results) == 6
    assert sum(1 for ok, _ in results if ok is True) == 5
    assert [why for ok, why in results if ok is False] == [B.DAILY_REACHED]

    db = Session()
    ledger = db.query(EvoSenseCostEntry).filter(EvoSenseCostEntry.organization_id == org_id).all()
    assert sum(e.total_cents for e in ledger if e.status == "charged") == 100
    assert len(ledger) == 5
    counter = (db.query(EvoSenseBudgetCounter)
               .filter(EvoSenseBudgetCounter.organization_id == org_id,
                       EvoSenseBudgetCounter.scope == "strategy:%s" % strat_id,
                       EvoSenseBudgetCounter.period == C.day_key()).one())
    assert counter.spent_cents == 100
    db.close()
    engine.dispose()


def test_a_stale_read_cannot_spend_money_that_is_gone(tmp_path):
    """Deterministic interleaving: two workers both READ $0.20 left; the first
    spends it; the second's reservation must be refused even though its own
    read said there was room. That is the read-then-write bug, pinned."""
    engine = _engine("sqlite", tmp_path)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    s = Session()
    org = Organization(name="Stale Org", slug="stale-org", plan="standard", industry="real_estate")
    s.add(org)
    s.flush()
    strat = EvoSenseStrategy(organization_id=org.id, name="S (TEST)", status="active",
                             daily_budget_cents=20, is_test=True)
    s.add(strat)
    C.controls(s, org.id)
    s.commit()
    a, b = Session(), Session()
    sa, sb = a.query(EvoSenseStrategy).get(strat.id), b.query(EvoSenseStrategy).get(strat.id)
    assert B.remaining(a, org.id, sa) == 20 and B.remaining(b, org.id, sb) == 20
    a.commit()
    b.commit()
    kw = dict(provider_key="p", connector_kind="sandbox", capability="CONTACT_ENRICHMENT", operation="t")
    assert B.reserve(a, org.id, sa, 20, **kw)[0] is True
    a.commit()
    ok, why, _ = B.reserve(b, org.id, sb, 20, **kw)
    assert ok is False and why == B.DAILY_REACHED
    b.rollback()
    a.close()
    b.close()
    engine.dispose()


def test_refund_returns_the_money_to_the_same_day(db_session, sample_org):
    strat = EvoSenseStrategy(organization_id=sample_org.id, name="R (TEST)", status="active",
                             daily_budget_cents=40, is_test=True)
    db_session.add(strat)
    db_session.flush()
    ok, _, e1 = B.reserve(db_session, sample_org.id, strat, 20, provider_key="p", connector_kind="sandbox",
                          capability="CONTACT_ENRICHMENT", operation="t")
    ok2, _, e2 = B.reserve(db_session, sample_org.id, strat, 20, provider_key="p", connector_kind="sandbox",
                           capability="CONTACT_ENRICHMENT", operation="t")
    assert ok and ok2
    assert B.reserve(db_session, sample_org.id, strat, 20, provider_key="p", connector_kind="sandbox",
                     capability="CONTACT_ENRICHMENT", operation="t")[0] is False
    B.refund(db_session, e2, strat)
    assert B.remaining(db_session, sample_org.id, strat) == 20
    assert B.reserve(db_session, sample_org.id, strat, 20, provider_key="p", connector_kind="sandbox",
                     capability="CONTACT_ENRICHMENT", operation="t")[0] is True
