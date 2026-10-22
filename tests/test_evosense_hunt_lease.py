"""A hunt lock is a short renewable lease: a dead worker frees its strategy in
minutes (not the old fixed 2 hours), a live hunt keeps it, and a hunt whose
lease was taken over stops instead of double-running."""
from datetime import timedelta

from app.services.evosense import common as C
from app.services.evosense import scheduler as SCH
from tests.test_evosense_autonomy import hunted  # noqa: F401  (fixture)


def test_lease_is_short():
    assert SCH.LOCK_FOR <= timedelta(minutes=30)


def test_dead_worker_lock_expires_and_live_one_renews(db_session, hunted):
    db, s = db_session, hunted["strategy"]
    token = SCH.claim(db, s)
    assert token and SCH.claim(db, s) is None            # held
    sched = SCH.schedule_for(db, s)
    sched.locked_until = C.now() - timedelta(seconds=1)  # worker died; lease ran out
    db.commit()
    token2 = SCH.claim(db, s)
    assert token2
    assert SCH.renew(db, s, token) is False              # the dead holder cannot renew
    assert SCH.renew(db, s, token2) is True
    SCH.release(db, s, token2)


def test_hunt_stops_when_lease_lost(monkeypatch, db_session, hunted):
    from app.services.evosense import hunt as HU
    db, s = db_session, hunted["strategy"]
    monkeypatch.setattr(SCH, "renew", lambda *_a, **_k: False)
    run = HU.run_strategy(db, s.organization_id, s)
    assert run.status == "failed" and "lease" in (run.error or "")
