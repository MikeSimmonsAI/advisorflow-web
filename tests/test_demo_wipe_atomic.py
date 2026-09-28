"""DELETE /admin/demo/wipe/{org_id} is all-or-nothing, on SQLite AND Postgres.

What was wrong: the CadenceState delete sat inside `try/except: pass` in the
middle of one transaction. On Postgres a failed statement aborts the whole
transaction, so "swallow and continue" really meant "every later statement
fails with InFailedSqlTransaction" - the caller saw an error about the Lead
delete, not the real cause. On SQLite the same code silently carried on. And
nothing rolled back explicitly on failure.

What these tests hold:
  * a successful wipe deletes only demo rows (and their children) and writes
    exactly one demo.wipe audit entry;
  * a REAL database error mid-wipe (a trigger that aborts DELETE FROM leads,
    after outcomes/replies/messages were already deleted in the same request)
    returns 500 saying nothing was deleted, and every earlier delete is rolled
    back - no partial state, no audit entry;
  * a failure in the final step (the audit write) also rolls everything back;
  * the one genuinely optional step (cadence_states) runs in a SAVEPOINT: if it
    fails the wipe still completes correctly - on Postgres this is exactly the
    case the old try/except turned into InFailedSqlTransaction;
  * scope: a super_admin of another brand gets 404 and nothing is deleted.

Every test runs on SQLite (in-memory, like the rest of the suite) and, when
the local Postgres is reachable, on Postgres too - each Postgres test gets its
own throwaway database. Set DEMO_WIPE_PG_URL to point elsewhere, or to "" to
skip the Postgres variants.
"""
import itertools
import json
import os
import uuid

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

import app.models.registry  # noqa: F401
from app.models.models import (AuditLogEntry, Base, CadenceState, Lead, LeadOutcome,
                               LeadStatus, LeadTier, Message, MessageTrack, Organization,
                               Platform, Reply, User)
from app.services.auth_service import create_access_token, hash_password
from app.services.data_cleanup import DEMO_PREFIX, SAMPLE_TAG

_SEQ = itertools.count(1)

_DEFAULT_PG = "postgresql+psycopg2://postgres@/postgres?host=/var/tmp/pgaf&port=55432"
PG_ADMIN_URL = os.environ.get("DEMO_WIPE_PG_URL", _DEFAULT_PG)


def _pg_available():
    if not PG_ADMIN_URL:
        return False
    try:
        eng = create_engine(PG_ADMIN_URL, connect_args={"connect_timeout": 2})
        with eng.connect() as c:
            c.execute(text("select 1"))
        eng.dispose()
        return True
    except Exception:
        return False


_PG_OK = _pg_available()

BACKENDS = [
    "sqlite",
    pytest.param("postgres", marks=pytest.mark.skipif(
        not _PG_OK, reason="local Postgres not reachable (DEMO_WIPE_PG_URL)")),
]


@pytest.fixture(params=BACKENDS)
def backend(request):
    return request.param


@pytest.fixture()
def wdb(backend, db_session):
    """(session, engine, backend) on the chosen backend."""
    if backend == "sqlite":
        yield db_session, db_session.get_bind(), backend
        return
    name = "wipe_%s" % uuid.uuid4().hex[:12]
    admin = create_engine(PG_ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text('CREATE DATABASE "%s"' % name))
    url = admin.url.set(database=name)
    eng = create_engine(url)
    Base.metadata.create_all(bind=eng)
    session = sessionmaker(autocommit=False, autoflush=False, bind=eng)()
    try:
        yield session, eng, backend
    finally:
        session.close()
        eng.dispose()
        with admin.connect() as c:
            c.execute(text('DROP DATABASE IF EXISTS "%s" WITH (FORCE)' % name))
        admin.dispose()


@pytest.fixture()
def wclient(wdb):
    from fastapi.testclient import TestClient
    from app.deps import get_db
    from app.main import app
    session = wdb[0]

    def _override():
        yield session

    app.dependency_overrides[get_db] = _override
    # raise_server_exceptions=False: a 500 must come back as a response, which
    # is what a real caller sees.
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


# ── builders ───────────────────────────────────────────────────────────────

def _platform(db, name):
    p = Platform(name=name, slug="brand-%s" % uuid.uuid4().hex[:8], short_name=name[:2],
                 tagline="t", support_email="support@%s.test" % uuid.uuid4().hex[:6])
    db.add(p)
    db.commit()
    return p


def _org(db, name, platform):
    o = Organization(name=name, slug="o-%s" % uuid.uuid4().hex[:8], plan="standard",
                     industry="energy", is_active=True, platform_id=platform.id)
    db.add(o)
    db.commit()
    return o


def _user(db, role, org, platform=None, label="u", user_id=None):
    u = User(organization_id=org.id, platform_id=(platform.id if platform else None),
             email="%s-%d-%s@test.local" % (label, next(_SEQ), uuid.uuid4().hex[:6]),
             password_hash=hash_password("TestPass123!"), full_name=label.title(),
             role=role, is_active=True, must_change_password=False)
    if user_id:
        u.id = user_id
    db.add(u)
    db.commit()
    return u


def _lead(db, org, owner, **kw):
    lead = Lead(organization_id=org.id, assigned_to_id=owner.id,
                first_name="Pat", last_name="Family",
                phone="1214555%04d" % next(_SEQ),
                email="lead-%s@example.com" % uuid.uuid4().hex[:6],
                tier=LeadTier.PRE_NEED, message_track=MessageTrack.PRE_NEED_LOCK_PRICE,
                status=LeadStatus.NEW, **kw)
    db.add(lead)
    db.commit()
    return lead


def _children(db, lead, sender):
    db.add(Message(lead_id=lead.id, sender_id=sender.id, body="hello"))
    db.add(Reply(lead_id=lead.id, body="hi back"))
    db.add(LeadOutcome(lead_id=lead.id, recorded_by_id=sender.id))
    db.add(CadenceState(lead_id=lead.id))
    db.commit()


def _h(db, user):
    return {"Authorization": "Bearer " + create_access_token(user, db)}


def _counts(db, lead_ids):
    db.expire_all()
    return {
        "leads": db.query(Lead).filter(Lead.id.in_(lead_ids)).count(),
        "messages": db.query(Message).filter(Message.lead_id.in_(lead_ids)).count(),
        "replies": db.query(Reply).filter(Reply.lead_id.in_(lead_ids)).count(),
        "outcomes": db.query(LeadOutcome).filter(LeadOutcome.lead_id.in_(lead_ids)).count(),
        "cadence": db.query(CadenceState).filter(CadenceState.lead_id.in_(lead_ids)).count(),
    }


def _wipe_audits(db):
    db.expire_all()
    return db.query(AuditLogEntry).filter(AuditLogEntry.action == "demo.wipe").all()


@pytest.fixture()
def world(wdb):
    db = wdb[0]
    plat = _platform(db, "Wipe Brand")
    home = _org(db, "Operator Home", plat)
    target = _org(db, "Demo Customer", plat)
    sup = _user(db, "super_admin", home, platform=plat, label="sup")
    real_adv = _user(db, "advisor", target, label="real")
    demo_adv = _user(db, "advisor", target, label="demo", user_id=DEMO_PREFIX + uuid.uuid4().hex)
    demo_leads = [
        _lead(db, target, real_adv, source_file=SAMPLE_TAG),
        _lead(db, target, real_adv, is_test=True),
    ]
    for dl in demo_leads:
        _children(db, dl, real_adv)
    real = _lead(db, target, real_adv)
    _children(db, real, real_adv)
    return {"db": db, "plat": plat, "target": target, "sup": sup, "real_adv": real_adv,
            "demo_adv_id": demo_adv.id, "demo_ids": [l.id for l in demo_leads],
            "real_id": real.id}


FULL = {"leads": 2, "messages": 2, "replies": 2, "outcomes": 2, "cadence": 2}
EMPTY = {"leads": 0, "messages": 0, "replies": 0, "outcomes": 0, "cadence": 0}
REAL_FULL = {"leads": 1, "messages": 1, "replies": 1, "outcomes": 1, "cadence": 1}


def _abort_trigger(db, backend, table):
    """A REAL server-side error on DELETE FROM <table> - not a Python fake."""
    if backend == "sqlite":
        db.execute(text("CREATE TRIGGER wipe_boom BEFORE DELETE ON %s "
                        "BEGIN SELECT RAISE(ABORT, 'induced failure'); END" % table))
    else:
        db.execute(text("CREATE FUNCTION wipe_boom() RETURNS trigger AS $$ BEGIN "
                        "RAISE EXCEPTION 'induced failure'; END $$ LANGUAGE plpgsql"))
        db.execute(text("CREATE TRIGGER wipe_boom BEFORE DELETE ON %s "
                        "FOR EACH ROW EXECUTE FUNCTION wipe_boom()" % table))
    db.commit()


# ── tests ──────────────────────────────────────────────────────────────────

def test_successful_wipe_deletes_demo_rows_and_audits(wclient, world):
    db = world["db"]
    r = wclient.delete("/admin/demo/wipe/%s" % world["target"].id, headers=_h(db, world["sup"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["leads_deleted"] == 2
    assert body["demo_advisors_deleted"] == 1
    assert set(body) == {"success", "org", "leads_deleted", "demo_advisors_deleted", "note"}

    assert _counts(db, world["demo_ids"]) == EMPTY
    assert _counts(db, [world["real_id"]]) == REAL_FULL
    assert db.get(User, world["demo_adv_id"]) is None
    assert db.get(User, world["real_adv"].id) is not None
    (e,) = _wipe_audits(db)
    assert e.organization_id == world["target"].id
    assert json.loads(e.details) == {"leads_deleted": 2, "demo_advisors_deleted": 1}


def test_db_error_mid_wipe_rolls_back_everything(wclient, world, wdb):
    """Outcomes, replies and messages are deleted first in the same request;
    the Lead delete then hits a real DB error. None of it may persist."""
    db, _, backend = wdb
    _abort_trigger(db, backend, "leads")
    r = wclient.delete("/admin/demo/wipe/%s" % world["target"].id, headers=_h(db, world["sup"]))
    assert r.status_code == 500, r.text
    assert "nothing was deleted" in r.json()["detail"]

    assert _counts(db, world["demo_ids"]) == FULL
    assert _counts(db, [world["real_id"]]) == REAL_FULL
    assert db.get(User, world["demo_adv_id"]) is not None
    assert _wipe_audits(db) == []


def test_failure_in_audit_write_rolls_back_deletes(wclient, world, monkeypatch):
    """The audit entry is the last step; if it fails the deletes go too."""
    db = world["db"]
    import app.routers.admin_router as ar

    def _boom(*a, **k):
        raise RuntimeError("induced audit failure")

    monkeypatch.setattr(ar, "log_action", _boom)
    r = wclient.delete("/admin/demo/wipe/%s" % world["target"].id, headers=_h(db, world["sup"]))
    assert r.status_code == 500, r.text
    assert "nothing was deleted" in r.json()["detail"]
    assert _counts(db, world["demo_ids"]) == FULL
    assert db.get(User, world["demo_adv_id"]) is not None
    assert _wipe_audits(db) == []


def test_optional_cadence_step_failure_does_not_poison_transaction(wclient, world, wdb):
    """cadence_states delete fails with a real DB error. It runs in a SAVEPOINT,
    so the wipe still completes - on Postgres the old try/except turned this into
    InFailedSqlTransaction on the next statement."""
    db, _, backend = wdb
    _abort_trigger(db, backend, "cadence_states")
    r = wclient.delete("/admin/demo/wipe/%s" % world["target"].id, headers=_h(db, world["sup"]))
    if backend == "postgres":
        # The trigger is FOR EACH ROW, and ON DELETE CASCADE from leads fires it
        # too, so on Postgres the Lead delete itself fails; the requirement is
        # then that it fails SAFELY. The savepoint behaviour is proven directly
        # in test_postgres_savepoint_isolates_optional_failure below.
        assert r.status_code == 500, r.text
        assert _counts(db, world["demo_ids"]) == FULL
        assert _wipe_audits(db) == []
    else:
        assert r.status_code == 200, r.text
        assert r.json()["leads_deleted"] == 2
        assert len(_wipe_audits(db)) == 1
        assert _counts(db, world["demo_ids"])["leads"] == 0


def test_missing_cadence_table_wipe_still_succeeds(wclient, world, wdb):
    """The optional step's real-world failure: an older deploy with no
    cadence_states table. The wipe must complete and audit."""
    db, _, backend = wdb
    db.execute(text("DROP TABLE cadence_states" + (" CASCADE" if backend == "postgres" else "")))
    db.commit()
    r = wclient.delete("/admin/demo/wipe/%s" % world["target"].id, headers=_h(db, world["sup"]))
    assert r.status_code == 200, r.text
    assert r.json()["leads_deleted"] == 2
    db.expire_all()
    assert db.query(Lead).filter(Lead.id.in_(world["demo_ids"])).count() == 0
    assert db.get(Lead, world["real_id"]) is not None
    assert len(_wipe_audits(db)) == 1


def test_other_brand_super_admin_gets_404_and_nothing_deleted(wclient, world):
    db = world["db"]
    other_plat = _platform(db, "Other Brand")
    other_home = _org(db, "Other Home", other_plat)
    foreign = _user(db, "super_admin", other_home, platform=other_plat, label="foreign")
    r = wclient.delete("/admin/demo/wipe/%s" % world["target"].id, headers=_h(db, foreign))
    assert r.status_code == 404, r.text
    assert _counts(db, world["demo_ids"]) == FULL
    assert db.get(User, world["demo_adv_id"]) is not None
    assert _wipe_audits(db) == []


@pytest.mark.skipif(not _PG_OK, reason="local Postgres not reachable")
def test_postgres_savepoint_isolates_optional_failure(world, wdb, backend):
    """Mechanism proof on Postgres: a swallowed error without a savepoint
    poisons the transaction (the old code); inside begin_nested() it does not
    (the new code)."""
    if backend != "postgres":
        pytest.skip("postgres-only mechanism proof")
    from sqlalchemy.exc import InternalError, ProgrammingError
    db = wdb[0]

    # Old pattern: swallow, then the next statement fails.
    try:
        db.execute(text("DELETE FROM no_such_table"))
    except ProgrammingError:
        pass
    with pytest.raises(InternalError, match="InFailedSqlTransaction|current transaction is aborted"):
        db.execute(text("SELECT 1"))
    db.rollback()

    # New pattern: the savepoint absorbs it, the transaction stays usable.
    db.execute(text("DELETE FROM replies WHERE lead_id = :i"), {"i": world["demo_ids"][0]})
    try:
        with db.begin_nested():
            db.execute(text("DELETE FROM no_such_table"))
    except ProgrammingError:
        pass
    assert db.execute(text("SELECT 1")).scalar() == 1
    db.rollback()
