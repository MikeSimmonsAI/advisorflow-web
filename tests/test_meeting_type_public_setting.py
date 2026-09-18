"""Opening a meeting type to the website is a decision, not an inference.

`ensure_meeting_types` back-fills the quorum and `public_bookable` onto brands
seeded before the feature, guarded on the row never having been edited
(updated_at == created_at). That guard cannot tell a human edit from an EARLIER
BACKFILL's own write, and the requires_video backfill in the same function
writes to these very rows first. A brand that picked that up on an earlier
deploy has rows that look hand-edited forever, so the quorum backfill skips them
for good and the brand stays unbookable from its own website with nothing on any
screen explaining why.

test_the_control_reaches_a_row_the_backfill_cannot is that situation, reproduced
and then fixed through the control.

Nothing here books, emails or contacts anyone.
"""
import itertools
from datetime import datetime, timedelta

from app.models.models import Platform, User
from app.models.sales_models import (
    BrandSalesOrg, Membership, SCOPE_BRAND_SALES_ORG, ROLE_SALES_REP,
)
from app.models.scheduling_models import MeetingType, LEADERSHIP_REPORTING_CHAIN
from app.services.auth_service import create_access_token, hash_password
from app.services.meeting_roles import ensure_meeting_types

_SEQ = itertools.count(1)
URL = "/god/ops/brands/%s/meeting-types/%s/public"


def _user(db, role="advisor"):
    n = next(_SEQ)
    u = User(organization_id=None, email="mt%d@example.invalid" % n,
             password_hash=hash_password("x"),
             full_name="Synthetic Person %d" % n,
             role=role, must_change_password=False)
    db.add(u); db.commit()
    return u


def _h(db, u):
    return {"Authorization": "Bearer " + create_access_token(u, db)}


def _brand(db, seed=True):
    n = next(_SEQ)
    p = Platform(name="Platform %d" % n, slug="plat-mt-%d" % n)
    db.add(p); db.commit()
    b = BrandSalesOrg(platform_id=p.id, name="Brand %d" % n,
                      slug="brand-mt-%d" % n)
    db.add(b); db.commit()
    if seed:
        ensure_meeting_types(db, b.id)
        db.commit()
    return b


def _mt(db, bso, key):
    return (db.query(MeetingType)
              .filter(MeetingType.brand_sales_org_id == bso.id,
                      MeetingType.key == key).first())


# ── the control ─────────────────────────────────────────────────────────────

def test_enabling_sets_the_flag_and_completes_the_quorum(client, db_session):
    god = _user(db_session, role="god_admin")
    bso = _brand(db_session)
    mt = _mt(db_session, bso, "discovery_demo")
    mt.public_bookable = False
    mt.leadership_policy = None
    db_session.commit()

    r = client.patch(URL % (bso.id, "discovery_demo"),
                     json={"public_bookable": True}, headers=_h(db_session, god))
    assert r.status_code == 200, r.text

    db_session.refresh(mt)
    assert mt.public_bookable is True
    assert mt.leadership_policy == LEADERSHIP_REPORTING_CHAIN
    assert mt.leadership_minimum == 1
    assert mt.leadership_depth == 2
    assert mt.owner_required is True


def test_a_brands_own_policy_is_never_overwritten(client, db_session):
    god = _user(db_session, role="god_admin")
    bso = _brand(db_session)
    mt = _mt(db_session, bso, "discovery_demo")
    mt.public_bookable = False
    mt.leadership_policy = LEADERSHIP_REPORTING_CHAIN
    mt.leadership_minimum = 2
    mt.leadership_depth = 3
    db_session.commit()

    client.patch(URL % (bso.id, "discovery_demo"),
                 json={"public_bookable": True}, headers=_h(db_session, god))
    db_session.refresh(mt)
    assert mt.leadership_minimum == 2
    assert mt.leadership_depth == 3


def test_closing_it_again_leaves_the_policy_alone(client, db_session):
    god = _user(db_session, role="god_admin")
    bso = _brand(db_session)
    client.patch(URL % (bso.id, "discovery_demo"),
                 json={"public_bookable": True}, headers=_h(db_session, god))
    r = client.patch(URL % (bso.id, "discovery_demo"),
                     json={"public_bookable": False}, headers=_h(db_session, god))
    assert r.status_code == 200
    mt = _mt(db_session, bso, "discovery_demo")
    db_session.refresh(mt)
    assert mt.public_bookable is False
    assert mt.leadership_policy == LEADERSHIP_REPORTING_CHAIN


def test_an_internal_type_cannot_be_opened_to_the_website(client, db_session):
    god = _user(db_session, role="god_admin")
    bso = _brand(db_session)
    r = client.patch(URL % (bso.id, "internal"),
                     json={"public_bookable": True}, headers=_h(db_session, god))
    assert r.status_code == 400
    assert "internal meeting type" in r.json()["detail"]
    mt = _mt(db_session, bso, "internal")
    assert bool(mt.public_bookable) is False


def test_unknown_brand_and_unknown_key_are_404(client, db_session):
    god = _user(db_session, role="god_admin")
    bso = _brand(db_session)
    assert client.patch(URL % ("nope", "discovery_demo"),
                        json={"public_bookable": True},
                        headers=_h(db_session, god)).status_code == 404
    assert client.patch(URL % (bso.id, "no_such_type"),
                        json={"public_bookable": True},
                        headers=_h(db_session, god)).status_code == 404


def test_only_god_may_open_it(client, db_session):
    bso = _brand(db_session)
    rep = _user(db_session)
    db_session.add(Membership(user_id=rep.id, scope_type=SCOPE_BRAND_SALES_ORG,
                              scope_id=bso.id, role=ROLE_SALES_REP,
                              is_active=True))
    db_session.commit()
    r = client.patch(URL % (bso.id, "discovery_demo"),
                     json={"public_bookable": True}, headers=_h(db_session, rep))
    assert r.status_code == 403


def test_brand_detail_shows_which_types_the_website_may_offer(client, db_session):
    god = _user(db_session, role="god_admin")
    bso = _brand(db_session)
    d = client.get("/god/ops/brands/%s" % bso.id,
                   headers=_h(db_session, god)).json()
    rows = d["configuration"]["meeting_types"]
    assert rows, "a seeded brand should list its meeting types"
    by_key = {r["key"]: r for r in rows}
    assert "discovery_demo" in by_key
    for r in rows:
        assert set(["key", "public_bookable", "leadership_policy",
                    "leadership_minimum", "leadership_depth"]).issubset(r)


# ── the defect this control exists for ──────────────────────────────────────

def test_the_control_reaches_a_row_the_backfill_will_not_touch(client, db_session):
    """A row the brand has made its own.

    The backfill deliberately keeps out of a renamed or re-timed meeting type -
    it cannot prove nobody chose those values on purpose. That protection used
    to mean such a row could never be opened to the website at all. The control
    is how a person says yes anyway, and the public resolver then finds it.

    (The other half of this - an UNTOUCHED legacy row that the old guard skipped
    forever - is now healed by the backfill itself, and is covered in
    tests/test_meeting_type_backfill_guard.py.)
    """
    from app.services import public_booking as pb

    god = _user(db_session, role="god_admin")
    bso = _brand(db_session)
    mt = _mt(db_session, bso, "discovery_demo")

    # The brand made this type its own: their name, their duration.
    mt.public_bookable = False
    mt.leadership_policy = None
    mt.name = "Our Own Discovery + Demo"
    mt.duration_minutes = 45
    db_session.commit()

    # The backfill keeps out, correctly.
    ensure_meeting_types(db_session, bso.id)
    db_session.commit()
    db_session.refresh(mt)
    assert mt.public_bookable is False, "backfill overwrote a customised row"
    assert pb.resolve_meeting_type(db_session, bso) is None

    # The control can.
    r = client.patch(URL % (bso.id, "discovery_demo"),
                     json={"public_bookable": True}, headers=_h(db_session, god))
    assert r.status_code == 200
    db_session.refresh(mt)
    assert mt.public_bookable is True
    assert mt.leadership_policy == LEADERSHIP_REPORTING_CHAIN

    resolved = pb.resolve_meeting_type(db_session, bso)
    assert resolved is not None and resolved.key == "discovery_demo"
