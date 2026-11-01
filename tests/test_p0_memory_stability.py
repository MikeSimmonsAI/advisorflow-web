"""P0 2026-10-29: backend memory exhaustion and background-job stability.

Covers the repair in PR "P0: Fix AdvisorFlow Backend Memory Exhaustion and Job
Stability" (issue #424 context):

  * the Wholesale sweep's owner-review-flag rule gives the SAME results as the
    code it replaced, while loading only flagged owners and only the property
    columns it reads, per tenant;
  * the EvoSense property-classification backfill gives the same results in
    bounded chunks;
  * loop-job rows left 'running' by a killed process are closed, and nothing
    else is touched;
  * heavy background passes take turns and always release their turn;
  * the memory-hygiene helper never raises;
  * a lease that cannot be taken skips the pass (fail closed).
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.models.evosense_models import EvoSenseOwner, EvoSenseOwnership, EvoSenseProperty
from app.models.models import Organization
from app.models.wholesale_models import WholesaleWorkException


# ── helpers ────────────────────────────────────────────────────────────────

def _org(db, slug):
    o = Organization(name=slug, slug=slug)
    db.add(o)
    db.commit()
    return o


def _seed_owner_flags(db, org, *, n_owners=60, prefix="x"):
    """Owners with every review_flags shape seen in production ("[]" for most),
    each linked to one or two properties of mixed status / score / test flag."""
    shapes = ['["ESTATE"]', "[]", None, "null", "", '[""]', '["ET_AL", "LIFE_ESTATE"]', "not json"]
    for i in range(n_owners):
        raw = shapes[i % len(shapes)]
        o = EvoSenseOwner(id="%s-o%d" % (prefix, i), organization_id=org.id, owner_type="person",
                          display_name="Owner %s %d" % (prefix, i), resolution="resolved",
                          name_truncated=False, review_flags=raw, is_test=False)
        db.add(o)
        for k in range(1 + i % 2):
            pid = "%s-p%d-%d" % (prefix, i, k)
            db.add(EvoSenseProperty(
                id=pid, organization_id=org.id, street_address="%d %s Flag St" % (i, prefix),
                status="needs_you" if (i + k) % 3 == 0 else "low_opportunity",
                identity_status="resolved", opportunity_score=(i * 13 + k * 7) % 100,
                signal_count=1, has_conflicts=False, is_test=(i % 5 == 4),
                opportunity_detail="x" * 2000))
            db.add(EvoSenseOwnership(id="%s-l%d-%d" % (prefix, i, k), organization_id=org.id,
                                     property_id=pid, owner_id=o.id, source="county",
                                     is_current=(k == 0 or i % 4 != 0), in_conflict=False))
    db.commit()


def _reference_owner_flag_rule(db, org_id, want, fire):
    """The rule exactly as it was before the P0 change (copied), as an oracle."""
    from app.services.evosense import readiness as RD
    flagged = (db.query(EvoSenseOwner).filter(EvoSenseOwner.organization_id == org_id,
                                              EvoSenseOwner.review_flags.isnot(None)).all())
    for o in flagged:
        try:
            flags = [f for f in json.loads(o.review_flags or "[]") if f]
        except (TypeError, ValueError):
            flags = []
        if not flags:
            continue
        for link in db.query(EvoSenseOwnership).filter(EvoSenseOwnership.organization_id == org_id,
                                                        EvoSenseOwnership.owner_id == o.id,
                                                        EvoSenseOwnership.is_current.is_(True)).all():
            ep = db.query(EvoSenseProperty).filter(EvoSenseProperty.id == link.property_id,
                                                   EvoSenseProperty.organization_id == org_id).first()
            if ep is None or not want(ep.is_test):
                continue
            if not RD.worth_working(db, ep):
                continue
            fire("title_ownership_anomaly", subject_type="evosense_property",
                 subject_id=ep.id, title="Check title / ownership: %s" % (ep.street_address or ""),
                 detail="Owner of record %s is flagged: %s." % (o.display_name or "(unnamed)",
                                                                ", ".join(sorted(flags))),
                 priority=45, source="sweep:owner_review_flags", is_test=bool(ep.is_test))


def _collect(rule, db, org_id, include_test, cap=None):
    """Run a rule with a recording `fire`, the same cap semantics as sweep()."""
    made, fired = {}, []

    def want(is_test):
        return include_test or not is_test

    def fire(kind, **kw):
        if cap is not None and made.get(kind, 0) >= cap:
            made["capped:" + kind] = 1
            return
        made[kind] = made.get(kind, 0) + 1
        fired.append((kw["subject_id"], kw["title"], kw["detail"], kw["is_test"], kw["priority"]))

    if rule is _reference_owner_flag_rule:
        rule(db, org_id, want, fire)
    else:
        rule(db, org_id, want, fire, made, cap)
    return made, fired


# ── owner review flags: same results, bounded memory ────────────────────────

@pytest.mark.parametrize("include_test", [False, True])
def test_owner_flag_rule_matches_the_previous_implementation(db_session, include_test):
    from app.services.wholesale_exceptions import _owner_flag_rule
    org = _org(db_session, "flags-a")
    _seed_owner_flags(db_session, org)
    old_made, old_fired = _collect(_reference_owner_flag_rule, db_session, org.id, include_test)
    new_made, new_fired = _collect(_owner_flag_rule, db_session, org.id, include_test)
    assert old_fired, "the fixture must exercise the rule"
    assert new_made == old_made
    assert sorted(new_fired) == sorted(old_fired)


def test_owner_flag_rule_small_chunks_still_match(db_session, monkeypatch):
    from app.services import wholesale_exceptions as WX
    monkeypatch.setattr(WX, "OWNER_FLAG_CHUNK", 3)
    org = _org(db_session, "flags-chunks")
    _seed_owner_flags(db_session, org, n_owners=41)
    old_made, old_fired = _collect(_reference_owner_flag_rule, db_session, org.id, False)
    new_made, new_fired = _collect(WX._owner_flag_rule, db_session, org.id, False)
    assert new_made == old_made and sorted(new_fired) == sorted(old_fired)


def test_owner_flag_rule_stops_scanning_once_capped_with_the_same_report(db_session):
    from app.services.wholesale_exceptions import _owner_flag_rule
    org = _org(db_session, "flags-cap")
    _seed_owner_flags(db_session, org)
    old_made, old_fired = _collect(_reference_owner_flag_rule, db_session, org.id, False, cap=3)
    new_made, new_fired = _collect(_owner_flag_rule, db_session, org.id, False, cap=3)
    assert old_made == new_made == {"title_ownership_anomaly": 3, "capped:title_ownership_anomaly": 1}
    assert len(new_fired) == 3


def test_owner_flag_rule_never_crosses_tenants(db_session):
    from app.services.wholesale_exceptions import _owner_flag_rule
    a, b = _org(db_session, "flags-ten-a"), _org(db_session, "flags-ten-b")
    _seed_owner_flags(db_session, a, prefix="a")
    _seed_owner_flags(db_session, b, prefix="b")
    # a cross-tenant link that must never surface: org B's ownership row
    # pointing at org A's property
    db_session.add(EvoSenseOwnership(id="cross", organization_id=b.id, property_id="a-p0-0",
                                     owner_id="b-o0", source="county", is_current=True,
                                     in_conflict=False))
    db_session.commit()
    _, fired_b = _collect(_owner_flag_rule, db_session, b.id, True)
    assert fired_b and all(sid.startswith("b-") for sid, *_ in fired_b)
    _, fired_a = _collect(_owner_flag_rule, db_session, a.id, True)
    assert fired_a and all(sid.startswith("a-") for sid, *_ in fired_a)


def test_owner_flag_rule_holds_no_owner_objects_and_releases_its_properties(db_session):
    from app.services.wholesale_exceptions import _owner_flag_rule
    org = _org(db_session, "flags-mem")
    _seed_owner_flags(db_session, org)
    org_id = org.id
    db_session.expunge_all()
    _collect(_owner_flag_rule, db_session, org_id, False)
    held = list(db_session.identity_map.values())
    assert not any(isinstance(o, EvoSenseOwner) for o in held)
    assert not any(isinstance(o, EvoSenseProperty) for o in held)


def test_full_sweep_repeated_cycles_are_idempotent(db_session):
    """Repeated automatic passes: the first raises, the rest raise nothing new,
    and every open item is unique per subject."""
    from app.services import wholesale_exceptions as WX
    org = _org(db_session, "flags-cycles")
    _seed_owner_flags(db_session, org)
    first = WX.sweep(db_session, org.id, None, include_test=False, cap_per_kind=None)
    db_session.commit()
    assert first.get("title_ownership_anomaly", 0) > 0
    for _ in range(5):
        again = WX.sweep(db_session, org.id, None, include_test=False, cap_per_kind=None)
        db_session.commit()
        assert again.get("title_ownership_anomaly", 0) == 0
    rows = db_session.query(WholesaleWorkException).filter(
        WholesaleWorkException.organization_id == org.id,
        WholesaleWorkException.kind == "title_ownership_anomaly").all()
    assert len(rows) == len({r.subject_id for r in rows}) == first["title_ownership_anomaly"]


# ── EvoSense classification backfill: same results in chunks ───────────────

def test_property_class_backfill_chunked_matches_and_counts(db_session, monkeypatch):
    from app.services.evosense import property_class as PCL
    a, b = _org(db_session, "pcl-a"), _org(db_session, "pcl-b")
    for org, pre in ((a, "pa"), (b, "pb")):
        for i in range(23):
            db_session.add(EvoSenseProperty(id="%s%d" % (pre, i), organization_id=org.id,
                                            street_address="%d Class St" % i, status="new",
                                            identity_status="resolved", signal_count=0,
                                            has_conflicts=False, is_test=False,
                                            property_type=("SFR" if i % 2 else "COMMERCIAL")))
    db_session.commit()
    monkeypatch.setattr(PCL, "BACKFILL_CHUNK", 4)
    assert PCL.backfill(db_session, a.id, limit=10) == 10
    db_session.commit()
    assert db_session.query(EvoSenseProperty).filter(
        EvoSenseProperty.organization_id == a.id, EvoSenseProperty.property_category.isnot(None)).count() == 10
    assert db_session.query(EvoSenseProperty).filter(
        EvoSenseProperty.organization_id == b.id, EvoSenseProperty.property_category.isnot(None)).count() == 0
    assert PCL.backfill(db_session) == 36          # the remaining 13 + 23, across both tenants
    db_session.commit()
    assert db_session.query(EvoSenseProperty).filter(EvoSenseProperty.property_category.is_(None)).count() == 0
    # and every column equals what the unchunked classifier writes for the row
    for p in db_session.query(EvoSenseProperty).all():
        expect = PCL.classify(PCL._evidence_for(db_session, p.organization_id, [p.id]).get(p.id, []),
                              legacy_type=p.property_type, person=None)
        assert PCL.apply(p, expect) is False, p.id   # nothing would change: already identical
    db_session.rollback()


# ── abandoned job-run rows ─────────────────────────────────────────────────

def test_close_abandoned_runs_closes_only_dead_loop_rows(db_session):
    from app.models.job_models import JobRun
    from app.services.job_run_service import ABANDONED_SUMMARY, close_abandoned_runs
    now = datetime.now(timezone.utc)
    rows = {
        "dead_hunt": JobRun(job_name="evosense_hunt_loop", started_at=now - timedelta(hours=5), status="running"),
        "dead_sweep": JobRun(job_name="wholesale_exception_sweep_loop", started_at=now - timedelta(hours=4),
                             status="running"),
        "live_hunt": JobRun(job_name="evosense_hunt_loop", started_at=now - timedelta(minutes=50),
                            status="running"),
        "old_cron": JobRun(job_name="cadence_cron", started_at=now - timedelta(hours=9), status="running"),
        "done": JobRun(job_name="evosense_hunt_loop", started_at=now - timedelta(hours=6), status="success",
                       finished_at=now - timedelta(hours=6), duration_ms=1000),
    }
    for r in rows.values():
        db_session.add(r)
    db_session.commit()
    ids = {k: r.id for k, r in rows.items()}
    factory = lambda: db_session.__class__(bind=db_session.get_bind())          # noqa: E731
    assert close_abandoned_runs(factory, now=now) == 2
    db_session.expire_all()
    get = lambda k: db_session.query(JobRun).filter(JobRun.id == ids[k]).one()  # noqa: E731
    for k in ("dead_hunt", "dead_sweep"):
        assert get(k).status == "error" and get(k).finished_at is not None
        assert get(k).error_summary == ABANDONED_SUMMARY
    assert get("live_hunt").status == "running"          # could still be running elsewhere
    assert get("old_cron").status == "running"           # a cron is another process
    assert get("done").status == "success"
    assert close_abandoned_runs(factory, now=now) == 0   # idempotent


def test_close_abandoned_runs_never_raises():
    from app.services.job_run_service import close_abandoned_runs

    def broken():
        raise RuntimeError("database down")
    assert close_abandoned_runs(broken) == 0


# ── heavy passes take turns ─────────────────────────────────────────────────

def test_heavy_passes_never_overlap_and_always_release():
    import app.main as M

    async def scenario():
        M._heavy_pass_lock = None
        active, peak, order = [0], [0], []

        async def heavy(name, fail=False):
            assert await M._heavy_turn(name)
            try:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
                order.append(name)
                await asyncio.sleep(0.02)
                if fail:
                    raise RuntimeError("pass failed")
            finally:
                active[0] -= 1
                await M._release_heavy_turn(name)

        results = await asyncio.gather(heavy("evosense_hunt_loop"),
                                       heavy("wholesale_exception_sweep_loop", fail=True),
                                       heavy("support_intelligence_loop"), return_exceptions=True)
        assert peak[0] == 1
        assert len(order) == 3
        assert isinstance(results[1], RuntimeError)
        assert not M._get_heavy_pass_lock().locked()       # a failing pass still hands the turn on

    asyncio.run(scenario())


def test_a_heavy_pass_that_cannot_get_its_turn_is_skipped(monkeypatch):
    import app.main as M
    monkeypatch.setattr(M, "HEAVY_PASS_WAIT_SECONDS", 0.05)

    async def scenario():
        M._heavy_pass_lock = None
        assert await M._heavy_turn("evosense_hunt_loop")
        assert await M._heavy_turn("wholesale_exception_sweep_loop") is False
        await M._release_heavy_turn("evosense_hunt_loop")
        assert await M._heavy_turn("wholesale_exception_sweep_loop") is True
        await M._release_heavy_turn("wholesale_exception_sweep_loop")

    asyncio.run(scenario())


def test_pass_claimed_fails_closed(monkeypatch):
    import app.main as M
    from app.services import job_run_service as J

    def boom(*a, **k):
        raise RuntimeError("no lease")
    monkeypatch.setattr(J, "claim_pass", boom)
    assert asyncio.run(M._pass_claimed("evosense_hunt_loop", 900)) is False


# ── memory hygiene ──────────────────────────────────────────────────────────

def test_memory_hygiene_never_raises_and_can_be_disabled(monkeypatch):
    from app.services import memory_hygiene as MH
    out = MH.after_heavy_pass("test")
    assert set(out) == {"rss_before_mb", "rss_after_mb", "trimmed"}
    monkeypatch.setenv("MEMORY_HYGIENE_DISABLED", "1")
    assert MH.after_heavy_pass("test") == {"rss_before_mb": None, "rss_after_mb": None, "trimmed": False}
    monkeypatch.delenv("MEMORY_HYGIENE_DISABLED")
    monkeypatch.setattr(MH.gc, "collect", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert MH.after_heavy_pass("test")["trimmed"] is False
