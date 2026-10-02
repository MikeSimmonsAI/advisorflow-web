"""TWO INSTANCES, ONE SIDE EFFECT - the database decides, not process memory.

"Two instances" here is literal: two independent SQLAlchemy engines on one
database file, each with its own connection pool, racing from threads. That is
what two Render web instances (or a web instance and a worker) look like to the
database. Anything process-local - a dict, a threading.Lock - would let both
win; these tests fail if a guard ever moves back into memory.
"""
import os
import tempfile
import threading
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import app.models.registry  # noqa: F401
from app.models.models import Base, Lead, Organization, Reply
from app.models.idempotency_models import ActionLease, IdempotencyKey, LoginFailure
from app.services import action_lease, idempotency


@pytest.fixture()
def two_instances():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    url = "sqlite:///" + path

    def make():
        eng = create_engine(url, connect_args={"timeout": 30, "check_same_thread": False})

        @event.listens_for(eng, "connect")
        def _pragma(dbapi, _):
            dbapi.execute("PRAGMA busy_timeout=30000")
            dbapi.isolation_level = None              # let SQLAlchemy emit BEGIN (real savepoints)

        @event.listens_for(eng, "begin")
        def _begin(conn):
            conn.exec_driver_sql("BEGIN IMMEDIATE")
        return eng

    a, b = make(), make()
    Base.metadata.create_all(a)
    yield a, b
    a.dispose()
    b.dispose()
    os.unlink(path)


def _race(engines, fn, rounds=1):
    """Run fn(session) once per engine, simultaneously; return the results."""
    out = []
    lock = threading.Lock()
    barrier = threading.Barrier(len(engines))

    def run(eng):
        s = Session(bind=eng)
        try:
            barrier.wait()
            r = fn(s)
            s.commit()
        except Exception as exc:                       # noqa: BLE001
            s.rollback()
            r = exc
        finally:
            s.close()
        with lock:
            out.append(r)
    ts = [threading.Thread(target=run, args=(e,)) for e in engines]
    [t.start() for t in ts]
    [t.join() for t in ts]
    return out


def test_idempotency_claim_has_exactly_one_winner_across_instances(two_instances):
    for i in range(8):
        res = _race(list(two_instances) * 2, lambda s: idempotency.claim(s, "race.scope", "key-%d" % i))
        assert sorted(res, key=str) == [False, False, False, True], res
    with Session(bind=two_instances[0]) as s:
        assert s.query(IdempotencyKey).filter(IdempotencyKey.scope == "race.scope").count() == 8


def test_action_lease_one_holder_release_and_atomic_takeover_of_a_dead_holder(two_instances):
    a, b = two_instances
    res = _race([a, b, a, b], lambda s: action_lease.acquire(s, "email.manual_send", "lead-1|hello"))
    winners = [r for r in res if isinstance(r, str)]
    assert len(winners) == 1 and res.count(None) == 3
    with Session(bind=a) as s:
        action_lease.release(s, "email.manual_send", "lead-1|hello", winners[0])
    with Session(bind=b) as s:
        tok = action_lease.acquire(s, "email.manual_send", "lead-1|hello")
        assert tok
    # the holder "dies": its lease expires; two instances race to take it over
    later = datetime.utcnow() + timedelta(seconds=500)
    res = _race([a, b], lambda s: action_lease.acquire(s, "email.manual_send", "lead-1|hello", now=later))
    assert len([r for r in res if isinstance(r, str)]) == 1, res


def test_one_stored_reply_per_twilio_message_sid(two_instances):
    a, b = two_instances
    with Session(bind=a) as s:
        org = Organization(name="X", slug="x-sid", plan="standard")
        s.add(org)
        s.commit()
        lead = Lead(organization_id=org.id, first_name="L", status="new")
        s.add(lead)
        s.commit()
        lead_id = lead.id

    def store(s):
        s.add(Reply(lead_id=lead_id, body="YES", source="sms", twilio_sid="SM-RACE-1"))
        s.flush()
        return "stored"
    res = _race([a, b], store)
    assert res.count("stored") == 1 and sum(isinstance(r, IntegrityError) for r in res) == 1, res
    with Session(bind=a) as s:
        assert s.query(Reply).filter(Reply.twilio_sid == "SM-RACE-1").count() == 1
        # replies without a sid (email) are unaffected by the index
        s.add_all([Reply(lead_id=lead_id, body="a", source="email"), Reply(lead_id=lead_id, body="b", source="email")])
        s.commit()


def test_login_failures_are_counted_across_instances(two_instances):
    a, b = two_instances
    from app.routers import auth_router as AR

    class _Req:
        class client:
            host = "203.0.113.9"
    for i in range(AR._MAX_FAILURES):
        with Session(bind=(a if i % 2 else b)) as s:      # failures land on alternating instances
            AR._login_record_failure(_Req, "victim@example.test", s)
    with Session(bind=a) as s:
        with pytest.raises(Exception) as exc:
            AR._login_throttle_check(_Req, "victim@example.test", s)
        assert getattr(exc.value, "status_code", None) == 429
        AR._login_throttle_check(_Req, "someone.else@example.test", s)   # other accounts unaffected


def test_a_claim_rolled_back_with_its_failed_work_allows_a_retry(two_instances):
    a, _ = two_instances
    s = Session(bind=a)
    assert idempotency.claim(s, "retry.scope", "k") is True
    s.rollback()                                  # the protected work failed; nothing happened
    assert idempotency.claim(s, "retry.scope", "k") is True
    s.commit()
    assert idempotency.claim(s, "retry.scope", "k") is False
    s.close()


def test_background_loop_pass_runs_on_exactly_one_instance(two_instances):
    """Every web instance starts the same loops; one pass per interval runs."""
    from app.services.job_run_service import claim_pass
    a, b = two_instances
    fa = lambda: Session(bind=a)                         # noqa: E731
    fb = lambda: Session(bind=b)                         # noqa: E731
    res = []
    lock = threading.Lock()
    barrier = threading.Barrier(4)

    def go(f):
        barrier.wait()
        r = claim_pass("cadence_loop", 3600, f)
        with lock:
            res.append(r)
    ts = [threading.Thread(target=go, args=(f,)) for f in (fa, fb, fa, fb)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(res) == [False, False, False, True], res
    # a different job is independent
    assert claim_pass("review_request_loop", 1800, fb) is True


def test_a_pass_claim_fails_open_when_the_database_is_unreachable():
    from app.services.job_run_service import claim_pass

    def broken():
        raise RuntimeError("database down")
    assert claim_pass("cadence_loop", 3600, broken) is True
