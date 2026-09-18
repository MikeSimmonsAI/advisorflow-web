"""System backfills must not poison each other's "human edited" guard.

THE PRODUCTION FAILURE THIS FILE EXISTS FOR.

`ensure_meeting_types` runs more than one backfill over the same meeting-type
rows. The requires_video one runs first and writes to exactly the rows the
quorum one cares about. The guard was `updated_at == created_at`, read as
"nobody has touched this row" - so the first backfill's own write made every
row look hand-edited to the second, which then skipped it permanently.

EvoSys Pro lived in that state: all seven meeting types correctly seeded,
discovery_demo stuck at public_bookable=false with a NULL leadership policy, and
every visitor to the public site answered "Online booking is not available right
now". Re-running the backfill could never fix it.

Nothing here books, emails or contacts anyone.
"""
import itertools
from datetime import datetime, timedelta

from app.models.models import Platform, User
from app.models.sales_models import BrandSalesOrg
from app.models.scheduling_models import MeetingType, LEADERSHIP_REPORTING_CHAIN
from app.services.auth_service import create_access_token, hash_password
from app.services.meeting_roles import ensure_meeting_types, _edited_by_a_person

_SEQ = itertools.count(1)


def _brand(db, seed=True):
    n = next(_SEQ)
    p = Platform(name="P%d" % n, slug="plat-guard-%d" % n)
    db.add(p); db.commit()
    b = BrandSalesOrg(platform_id=p.id, name="B%d" % n, slug="brand-guard-%d" % n)
    db.add(b); db.commit()
    if seed:
        ensure_meeting_types(db, b.id); db.commit()
    return b


def _mt(db, bso, key="discovery_demo"):
    return (db.query(MeetingType)
              .filter(MeetingType.brand_sales_org_id == bso.id,
                      MeetingType.key == key).first())


def _god(db):
    n = next(_SEQ)
    u = User(organization_id=None, email="guard%d@example.invalid" % n,
             password_hash=hash_password("x"), full_name="Synthetic Operator %d" % n,
             role="god_admin", must_change_password=False)
    db.add(u); db.commit()
    return u


def _h(db, u):
    return {"Authorization": "Bearer " + create_access_token(u, db)}


# ── the invariant that stops the poisoning ──────────────────────────────────

def test_a_system_write_never_looks_like_a_persons_edit(db_session):
    """The whole defect in one assertion.

    After the system writes its defaults, no row may read as human-edited - or
    the next backfill to come along skips it forever.
    """
    bso = _brand(db_session)
    rows = db_session.query(MeetingType).filter(
        MeetingType.brand_sales_org_id == bso.id).all()
    assert rows
    for r in rows:
        assert _edited_by_a_person(r) is False, "%s reads as hand-edited" % r.key


def test_the_stamp_is_set_and_matches_updated_at(db_session):
    bso = _brand(db_session, seed=False)
    ensure_meeting_types(db_session, bso.id)
    db_session.commit()
    mt = _mt(db_session, bso)
    mt.requires_video = False
    mt.leadership_policy = None
    mt.system_defaults_at = None
    mt.updated_at = datetime.utcnow() + timedelta(seconds=1)
    db_session.commit()

    ensure_meeting_types(db_session, bso.id)
    db_session.commit()
    db_session.refresh(mt)
    assert mt.system_defaults_at is not None
    assert mt.updated_at <= mt.system_defaults_at
    assert _edited_by_a_person(mt) is False


# ── the production failure, reproduced ──────────────────────────────────────

def test_a_legacy_row_the_old_guard_skipped_forever_is_now_healed(db_session):
    """EvoSys Pro's exact production shape.

    created_at != updated_at because an earlier backfill wrote to it; no policy;
    not publicly bookable; never stamped. The old guard read that as a human
    edit and skipped it on every call for good.
    """
    bso = _brand(db_session)
    mt = _mt(db_session, bso)
    mt.public_bookable = False
    mt.leadership_policy = None
    mt.leadership_minimum = 0
    mt.leadership_depth = 0
    mt.system_defaults_at = None
    mt.created_at = datetime.utcnow() - timedelta(days=30)
    mt.updated_at = datetime.utcnow()          # the earlier backfill's write
    db_session.commit()

    ensure_meeting_types(db_session, bso.id)
    db_session.commit()
    db_session.refresh(mt)

    assert mt.public_bookable is True
    assert mt.leadership_policy == LEADERSHIP_REPORTING_CHAIN
    assert mt.leadership_minimum == 1
    assert mt.leadership_depth == 2


def test_the_public_resolver_then_finds_it(db_session):
    from app.services import public_booking as pb
    bso = _brand(db_session)
    mt = _mt(db_session, bso)
    mt.public_bookable = False
    mt.leadership_policy = None
    mt.system_defaults_at = None
    mt.created_at = datetime.utcnow() - timedelta(days=30)
    mt.updated_at = datetime.utcnow()
    db_session.commit()
    assert pb.resolve_meeting_type(db_session, bso) is None

    ensure_meeting_types(db_session, bso.id)
    db_session.commit()
    resolved = pb.resolve_meeting_type(db_session, bso)
    assert resolved is not None and resolved.key == "discovery_demo"


def test_video_and_quorum_defaults_both_land_on_one_legacy_row(db_session):
    """Both backfills apply. Neither blocks the other."""
    bso = _brand(db_session)
    mt = _mt(db_session, bso)
    mt.requires_video = False
    mt.public_bookable = False
    mt.leadership_policy = None
    mt.system_defaults_at = None
    mt.created_at = datetime.utcnow() - timedelta(days=30)
    mt.updated_at = datetime.utcnow()
    db_session.commit()

    ensure_meeting_types(db_session, bso.id)
    db_session.commit()
    db_session.refresh(mt)
    assert mt.requires_video is True
    assert mt.public_bookable is True


# ── and a person's decision still wins, forever ─────────────────────────────

def test_a_brand_that_chose_its_own_policy_keeps_it(db_session):
    bso = _brand(db_session)
    mt = _mt(db_session, bso)
    mt.leadership_policy = LEADERSHIP_REPORTING_CHAIN
    mt.leadership_minimum = 3
    mt.leadership_depth = 4
    mt.public_bookable = False
    db_session.commit()

    ensure_meeting_types(db_session, bso.id)
    db_session.commit()
    db_session.refresh(mt)
    assert mt.leadership_minimum == 3
    assert mt.leadership_depth == 4
    assert mt.public_bookable is False


def test_a_human_turning_public_booking_off_is_never_undone(client, db_session):
    """The case that only became possible when the control shipped - and the
    reason the control stamps the row on the way through."""
    god = _god(db_session)
    bso = _brand(db_session)

    # A legacy row: never stamped. A person opens it, then closes it again.
    mt = _mt(db_session, bso)
    mt.system_defaults_at = None
    mt.public_bookable = False
    mt.leadership_policy = None
    db_session.commit()

    url = "/god/ops/brands/%s/meeting-types/discovery_demo/public" % bso.id
    assert client.patch(url, json={"public_bookable": True},
                        headers=_h(db_session, god)).status_code == 200
    assert client.patch(url, json={"public_bookable": False},
                        headers=_h(db_session, god)).status_code == 200

    db_session.expire_all()
    mt = _mt(db_session, bso)
    assert mt.public_bookable is False
    assert _edited_by_a_person(mt) is True, "the control must mark a human decision"

    # Every future backfill must leave that decision alone.
    for _ in range(3):
        ensure_meeting_types(db_session, bso.id)
        db_session.commit()
    db_session.expire_all()
    mt = _mt(db_session, bso)
    assert mt.public_bookable is False, "a person's 'no' was overwritten"


def test_repeated_calls_are_stable(db_session):
    bso = _brand(db_session)
    for _ in range(4):
        ensure_meeting_types(db_session, bso.id)
        db_session.commit()
    rows = db_session.query(MeetingType).filter(
        MeetingType.brand_sales_org_id == bso.id).all()
    assert len(rows) == 7
    for r in rows:
        assert _edited_by_a_person(r) is False
