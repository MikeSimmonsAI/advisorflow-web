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


# ── a single call longer than the lease, restart recovery, progress ────────

def test_a_call_longer_than_the_lease_keeps_it_renewed_in_the_background(monkeypatch, db_session, hunted):
    """A county scan can outlast LOCK_FOR. The keeper renews on its own
    schedule DURING the call, so no second worker can claim the strategy."""
    import time
    db, s = db_session, hunted["strategy"]
    token = SCH.claim(db, s)
    beats = []
    keeper = SCH.LeaseKeeper(db, s, token, every=0.02)
    monkeypatch.setattr(keeper, "renew_once", lambda: beats.append(1) or True)
    keeper.start()
    time.sleep(0.3)                                       # "the long county call"
    keeper.stop()
    assert len(beats) >= 5 and keeper.ok()
    SCH.release(db, s, token)


def test_one_renewal_pushes_the_lease_out_and_blocks_a_second_claim(db_session, hunted):
    db, s = db_session, hunted["strategy"]
    token = SCH.claim(db, s)
    sched = SCH.schedule_for(db, s)
    sched.locked_until = C.now() + timedelta(seconds=5)   # about to run out mid-call
    db.commit()
    keeper = SCH.LeaseKeeper(db, s, token)
    assert keeper.renew_once() is True
    db.expire_all()
    assert SCH.schedule_for(db, s).locked_until > C.now() + timedelta(minutes=15)
    assert SCH.claim(db, s) is None
    SCH.release(db, s, token)


def test_the_keeper_notices_a_lost_lease(db_session, hunted):
    db, s = db_session, hunted["strategy"]
    token = SCH.claim(db, s)
    keeper = SCH.LeaseKeeper(db, s, "someone-else")
    assert keeper.renew_once() is False and keeper.ok() is False
    SCH.release(db, s, token)


def test_a_hunt_cut_off_by_a_restart_is_closed_out_by_the_next_one(db_session, hunted):
    from app.models.evosense_models import EvoSenseRun
    from app.services.evosense import hunt as HU
    db, s = db_session, hunted["strategy"]
    dead = EvoSenseRun(organization_id=s.organization_id, strategy_id=s.id, trigger="manual",
                       status="running", counts=C.jdump({"progress": {"sources_done": ["collin_cad"]}}))
    db.add(dead)
    db.commit()
    run = HU.run_strategy(db, s.organization_id, s)
    db.refresh(dead)
    assert dead.status == "failed" and dead.finished_at is not None
    assert "Interrupted" in dead.error and "collin_cad" in dead.error
    assert run.status in ("succeeded", "partial") and run.id != dead.id


def test_progress_is_saved_as_each_source_finishes(db_session, hunted):
    from app.services.evosense import hunt as HU
    db, s = db_session, hunted["strategy"]
    run = HU.run_strategy(db, s.organization_id, s)
    prog = (C.jload(run.counts, {}) or {}).get("progress") or {}
    assert prog.get("stage") == "lookups" and prog.get("sources_done")
