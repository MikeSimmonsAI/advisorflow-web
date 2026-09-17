"""THE REPORTING CHAIN IS THE ONLY SOURCE OF LEADERSHIP. Ten cases prove it.

WHY THIS FILE IS LONG AND SPECIFIC.

Before this, "who are the managers for this meeting" was answered by
`meeting_roles.resolve_slot("sales_manager")`, which returns every user holding
a sales-manager membership anywhere in the brand. That answer is defensible for
an internal booking - a human reads the candidate list and chooses. It is
indefensible for a public inbound booking, where nobody reads anything: the
platform computes availability against whoever it resolved and offers the
result to a stranger.

The failure is silent and it scales the wrong way. A rep whose manager is two
states away gets their public calendar intersected with people who have never
met them; the times on offer just get worse; and every new manager hired
anywhere in the brand makes it worse again.

So the test that matters most in this file is case 6: a rep in one reporting
line must not see anybody from another line, no matter how many managers exist
elsewhere in the same brand. The other nine exist because a resolver that is
right about the happy path and wrong about a deactivated manager or a
hand-edited loop is a resolver that fails in production and not in CI.

THE NAMES. Blake/Michael/Mike and the Kentucky line come from the requirement
and appear ONLY as test fixtures. Nothing in app/ knows them - that is asserted
directly in test_no_person_or_place_is_named_in_the_resolver at the end.
"""

import itertools
from datetime import datetime

import pytest

from app.models.models import Platform, User
from app.models.sales_models import (
    BrandSalesOrg, Membership,
    SCOPE_BRAND_SALES_ORG, ROLE_SALES_MANAGER, ROLE_SALES_REP,
)
from app.services import leadership_chain as lc
from app.services.auth_service import hash_password

_SEQ = itertools.count(1)


# ── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture()
def platform(db_session):
    p = Platform(name="Test Platform", slug="chain-plat-%d" % next(_SEQ))
    db_session.add(p)
    db_session.commit()
    return p


@pytest.fixture()
def brand(db_session, platform):
    b = BrandSalesOrg(platform_id=platform.id, name="Brand Sales",
                      slug="chain-brand-%d" % next(_SEQ))
    db_session.add(b)
    db_session.commit()
    return b


@pytest.fixture()
def other_brand(db_session, platform):
    b = BrandSalesOrg(platform_id=platform.id, name="Other Brand Sales",
                      slug="chain-other-%d" % next(_SEQ))
    db_session.add(b)
    db_session.commit()
    return b


def _user(db, name, active=True):
    u = User(organization_id=None, email="chain%d@test.live" % next(_SEQ),
             password_hash=hash_password("x"), full_name=name,
             role="advisor", is_active=active, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _seat(db, user, brand, role=ROLE_SALES_REP, reports_to=None, active=True):
    m = Membership(user_id=user.id, scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id=brand.id, role=role, is_active=active,
                   reports_to_user_id=(reports_to.id if reports_to else None))
    db.add(m)
    db.commit()
    return m


@pytest.fixture()
def line(db_session, brand):
    """rep -> manager -> senior, the three-level line every case below uses."""
    senior = _user(db_session, "Senior Leader")
    manager = _user(db_session, "Direct Manager")
    rep = _user(db_session, "Rep")
    _seat(db_session, senior, brand, ROLE_SALES_MANAGER)
    _seat(db_session, manager, brand, ROLE_SALES_MANAGER, reports_to=senior)
    _seat(db_session, rep, brand, ROLE_SALES_REP, reports_to=manager)
    return {"rep": rep, "manager": manager, "senior": senior}


# ═══════════════════════════════════════════════════════════════════════════
# The chain itself
# ═══════════════════════════════════════════════════════════════════════════

def test_the_chain_is_the_reporting_line_nearest_first(db_session, brand, line):
    c = lc.resolve(db_session, line["rep"].id, brand.id, depth=2)
    assert c.ok
    assert c.owner.id == line["rep"].id
    assert c.leader_ids == [line["manager"].id, line["senior"].id], (
        "the direct manager must come first - when a policy books fewer leaders "
        "than are free, it books the nearest, and that ordering is the reason")


def test_depth_bounds_the_walk(db_session, brand, line):
    c = lc.resolve(db_session, line["rep"].id, brand.id, depth=1)
    assert c.leader_ids == [line["manager"].id]


def test_depth_zero_is_the_owner_alone_and_is_not_an_error(db_session, brand, line):
    """A meeting type with no leadership policy resolves depth 0. That is a
    valid configuration, not a broken chain."""
    c = lc.resolve(db_session, line["rep"].id, brand.id, depth=0)
    assert c.ok
    assert c.leaders == []


def test_depth_beyond_the_top_stops_at_the_top(db_session, brand, line):
    c = lc.resolve(db_session, line["rep"].id, brand.id, depth=9)
    assert c.leader_ids == [line["manager"].id, line["senior"].id]


# ═══════════════════════════════════════════════════════════════════════════
# CASE 6 (required) — the whole reason this module exists
# ═══════════════════════════════════════════════════════════════════════════

def test_case6_another_reporting_line_in_the_same_brand_is_never_considered(
        db_session, brand, line):
    """A second, unrelated line in the SAME brand must be invisible.

    This is the requirement stated directly: a rep in one region's line must
    never start searching another region's managers simply because those people
    hold a sales_manager role somewhere in the brand. Under the old
    brand-wide resolution every one of these four would have been a candidate.
    """
    regional_director = _user(db_session, "Regional Director")
    regional_manager = _user(db_session, "Regional Manager")
    other_rep = _user(db_session, "Other Rep")
    _seat(db_session, regional_director, brand, ROLE_SALES_MANAGER)
    _seat(db_session, regional_manager, brand, ROLE_SALES_MANAGER,
          reports_to=regional_director)
    _seat(db_session, other_rep, brand, ROLE_SALES_REP, reports_to=regional_manager)

    # Two more unrelated managers with no reports at all, to make the point that
    # holding the role is not what qualifies somebody.
    for n in ("Unrelated Manager A", "Unrelated Manager B"):
        _seat(db_session, _user(db_session, n), brand, ROLE_SALES_MANAGER)

    theirs = lc.resolve(db_session, other_rep.id, brand.id, depth=2)
    assert theirs.leader_ids == [regional_manager.id, regional_director.id]

    ours = lc.resolve(db_session, line["rep"].id, brand.id, depth=2)
    assert ours.leader_ids == [line["manager"].id, line["senior"].id]

    assert set(ours.leader_ids).isdisjoint(set(theirs.leader_ids)), (
        "two separate reporting lines in one brand produced overlapping "
        "leadership - the resolver is falling back to a brand-wide pool")


# ═══════════════════════════════════════════════════════════════════════════
# CASE 7 — an inactive link
# ═══════════════════════════════════════════════════════════════════════════

def test_case7_a_deactivated_direct_manager_is_skipped_and_recorded(
        db_session, brand, line):
    """The named manager is gone. The next VALID leader in the same line is
    used - and nobody unrelated is substituted."""
    m = (db_session.query(Membership)
         .filter(Membership.user_id == line["manager"].id,
                 Membership.scope_id == brand.id).first())
    m.is_active = False
    db_session.commit()

    # Somebody who would have been picked up by a brand-wide fallback.
    _seat(db_session, _user(db_session, "Unrelated Manager"), brand, ROLE_SALES_MANAGER)

    c = lc.resolve(db_session, line["rep"].id, brand.id, depth=2)
    assert c.leader_ids == [line["senior"].id], (
        "the walk must continue up the SAME line past a dead seat, not sideways")
    assert any(b["user_id"] == line["manager"].id and b["reason"] == "no_active_membership"
               for b in c.broken), "the dead link must be recorded, not silently dropped"


def test_a_deactivated_user_account_is_skipped_too(db_session, brand, line):
    """Seat active, person deactivated. Two different facts, both disqualifying."""
    line["manager"].is_active = False
    db_session.commit()
    c = lc.resolve(db_session, line["rep"].id, brand.id, depth=2)
    assert c.leader_ids == [line["senior"].id]
    assert any(b["reason"] == "inactive_user" for b in c.broken)


def test_a_chain_whose_only_leader_is_gone_reports_broken_not_empty(
        db_session, brand):
    """`broken_link` and `no_leadership_configured` need different fixes.

    One means somebody was deactivated without their reports being moved; the
    other means nobody was ever named. A single "no leadership" message would
    send an operator looking in the wrong place.
    """
    manager = _user(db_session, "Departed Manager")
    rep = _user(db_session, "Rep")
    _seat(db_session, manager, brand, ROLE_SALES_MANAGER, active=False)
    _seat(db_session, rep, brand, ROLE_SALES_REP, reports_to=manager)

    c = lc.resolve(db_session, rep.id, brand.id, depth=2)
    assert c.status == lc.CHAIN_BROKEN_LINK
    assert c.leaders == []
    assert "reassigned" in c.message


# ═══════════════════════════════════════════════════════════════════════════
# CASE 8 — cycles
# ═══════════════════════════════════════════════════════════════════════════

def test_case8_a_two_person_loop_terminates(db_session, brand):
    """A reports to B, B reports to A. Hand-edited charts do this."""
    a = _user(db_session, "A")
    b = _user(db_session, "B")
    _seat(db_session, a, brand, ROLE_SALES_MANAGER)
    _seat(db_session, b, brand, ROLE_SALES_MANAGER, reports_to=a)
    ma = (db_session.query(Membership)
          .filter(Membership.user_id == a.id, Membership.scope_id == brand.id).first())
    ma.reports_to_user_id = b.id
    db_session.commit()

    c = lc.resolve(db_session, a.id, brand.id, depth=10)
    # Terminates, and never returns the same person twice.
    assert len(c.leader_ids) == len(set(c.leader_ids))
    assert a.id not in c.leader_ids, "the walk returned the person it started from"
    assert len(c.leader_ids) <= 1


def test_self_reference_is_refused_explicitly(db_session, brand):
    """reports_to pointing at yourself is a cycle of length one."""
    u = _user(db_session, "Self Manager")
    m = _seat(db_session, u, brand, ROLE_SALES_MANAGER)
    m.reports_to_user_id = u.id
    db_session.commit()

    c = lc.resolve(db_session, u.id, brand.id, depth=3)
    assert c.status == lc.CHAIN_CYCLE
    assert c.leaders == []
    assert any(b["reason"] == "reports_to_self" for b in c.broken)


def test_a_long_loop_cannot_run_away(db_session, brand):
    """Three-person ring, asked for a depth far beyond its length."""
    a, b, c_ = (_user(db_session, "R1"), _user(db_session, "R2"), _user(db_session, "R3"))
    ma = _seat(db_session, a, brand, ROLE_SALES_MANAGER)
    mb = _seat(db_session, b, brand, ROLE_SALES_MANAGER, reports_to=a)
    mc = _seat(db_session, c_, brand, ROLE_SALES_MANAGER, reports_to=b)
    ma.reports_to_user_id = c_.id
    db_session.commit()

    chain = lc.resolve(db_session, a.id, brand.id, depth=500)
    assert len(chain.leader_ids) <= 2
    assert len(chain.leader_ids) == len(set(chain.leader_ids))


# ═══════════════════════════════════════════════════════════════════════════
# CASE 9 — cross-brand corruption
# ═══════════════════════════════════════════════════════════════════════════

def test_case9_a_manager_from_another_brand_is_not_leadership_here(
        db_session, brand, other_brand):
    """The chart names somebody real who manages elsewhere.

    `assert_manager_ok` refuses to STORE this, but a direct database edit, a
    restore, or a brand being split can produce it. It must not resolve.
    """
    foreign_manager = _user(db_session, "Foreign Manager")
    _seat(db_session, foreign_manager, other_brand, ROLE_SALES_MANAGER)

    rep = _user(db_session, "Rep")
    _seat(db_session, rep, brand, ROLE_SALES_REP, reports_to=foreign_manager)

    c = lc.resolve(db_session, rep.id, brand.id, depth=2)
    assert c.leaders == []
    assert c.status == lc.CHAIN_BROKEN_LINK
    assert any(b["user_id"] == foreign_manager.id for b in c.broken)


def test_the_walk_never_crosses_into_another_brands_chart(
        db_session, brand, other_brand):
    """The same human holds seats in BOTH brands, under different managers.

    This is the case that a user-keyed org chart would get wrong. Resolving in
    one brand must follow that brand's line only.
    """
    person = _user(db_session, "Dual Seat")
    mgr_here = _user(db_session, "Manager Here")
    mgr_there = _user(db_session, "Manager There")
    _seat(db_session, mgr_here, brand, ROLE_SALES_MANAGER)
    _seat(db_session, mgr_there, other_brand, ROLE_SALES_MANAGER)
    _seat(db_session, person, brand, ROLE_SALES_REP, reports_to=mgr_here)
    _seat(db_session, person, other_brand, ROLE_SALES_REP, reports_to=mgr_there)

    assert lc.resolve(db_session, person.id, brand.id, 2).leader_ids == [mgr_here.id]
    assert lc.resolve(db_session, person.id, other_brand.id, 2).leader_ids == [mgr_there.id]


# ═══════════════════════════════════════════════════════════════════════════
# CASE 10 — no chain at all
# ═══════════════════════════════════════════════════════════════════════════

def test_case10_a_rep_with_no_manager_reports_a_setup_status(db_session, brand):
    rep = _user(db_session, "Unmanaged Rep")
    _seat(db_session, rep, brand, ROLE_SALES_REP)
    c = lc.resolve(db_session, rep.id, brand.id, depth=2)
    assert c.status == lc.CHAIN_NO_LEADERSHIP
    assert c.leaders == []
    assert "no reporting manager configured" in c.message
    assert not c.satisfies(1)
    assert c.satisfies(0), "a policy requiring zero leaders is still satisfiable"


def test_someone_with_no_seat_in_this_brand_is_not_an_owner_here(
        db_session, brand, other_brand):
    stranger = _user(db_session, "Stranger")
    _seat(db_session, stranger, other_brand, ROLE_SALES_REP)
    c = lc.resolve(db_session, stranger.id, brand.id, depth=2)
    assert c.status == lc.CHAIN_OWNER_NOT_A_MEMBER
    assert c.owner is None


def test_an_inactive_seat_cannot_own_a_booking(db_session, brand, line):
    m = (db_session.query(Membership)
         .filter(Membership.user_id == line["rep"].id,
                 Membership.scope_id == brand.id).first())
    m.is_active = False
    db_session.commit()
    c = lc.resolve(db_session, line["rep"].id, brand.id, depth=2)
    assert c.status == lc.CHAIN_OWNER_NOT_A_MEMBER


def test_a_platform_executive_grant_is_not_a_sales_seat(db_session, brand, platform):
    """Memberships share one table. A brand_executive grant is platform-scoped
    and is not somebody who sells or attends sales calls."""
    from app.models.sales_models import ROLE_BRAND_EXECUTIVE, SCOPE_PLATFORM
    exec_user = _user(db_session, "Brand Executive")
    db_session.add(Membership(user_id=exec_user.id, scope_type=SCOPE_PLATFORM,
                              scope_id=platform.id, role=ROLE_BRAND_EXECUTIVE,
                              is_active=True))
    db_session.commit()
    assert lc.active_membership(db_session, exec_user.id, brand.id) is None
    assert lc.resolve(db_session, exec_user.id, brand.id, 2).status == lc.CHAIN_OWNER_NOT_A_MEMBER


# ═══════════════════════════════════════════════════════════════════════════
# Shape and privacy
# ═══════════════════════════════════════════════════════════════════════════

def test_summary_is_for_internal_screens_and_says_so(db_session, brand, line):
    out = lc.chain_summary(db_session, line["rep"].id, brand.id, 2)
    assert out["ok"] is True
    assert [l["level"] for l in out["leaders"]] == [1, 2]
    assert out["leaders"][0]["full_name"] == "Direct Manager"
    assert "INTERNAL ONLY" in lc.chain_summary.__doc__, (
        "the docstring is the only thing stopping this being returned from a "
        "public endpoint - a visitor reconstructing an org chart from a booking "
        "page is the leak this module exists to prevent")


def test_the_walk_agrees_with_compensations_on_a_healthy_chart(db_session, brand, line):
    """The two must not drift about WHO IS ABOVE WHOM.

    leadership_chain walks the column itself rather than calling
    compensation.upline(), because the two need different behaviour at a
    deactivated seat - compensation must not pay a departed manager's override,
    a meeting must still find a live attendee in the same line. That divergence
    is deliberate and documented.

    What is NOT allowed is the two disagreeing about the chart itself. If a rep's
    commission override goes to one manager while their meetings go to another,
    somebody will eventually be paid for a deal they were never in the room for.
    So on a healthy chart - every seat active, which is the normal case - the two
    must produce the same people in the same order.
    """
    from app.services.compensation import upline
    theirs = upline(db_session, line["rep"].id, brand.id, 2)
    ours = lc.resolve(db_session, line["rep"].id, brand.id, depth=2).leader_ids
    assert ours == theirs, (
        "leadership_chain and compensation.upline disagree about the reporting "
        "line on a fully active chart - they may only differ at a dead seat")


def test_the_divergence_at_a_dead_seat_is_the_documented_one(db_session, brand, line):
    """Compensation stops; a meeting walks through. Asserted both ways."""
    from app.services.compensation import upline
    m = (db_session.query(Membership)
         .filter(Membership.user_id == line["manager"].id,
                 Membership.scope_id == brand.id).first())
    m.is_active = False
    db_session.commit()

    # Compensation stops at the dead seat: it will not pay past a departed
    # manager.
    assert upline(db_session, line["rep"].id, brand.id, 2) == [line["manager"].id]
    # A meeting continues up the SAME line and finds somebody who can attend.
    assert lc.resolve(db_session, line["rep"].id, brand.id, 2).leader_ids == [
        line["senior"].id]


def test_is_manager_seat_reads_the_role_not_the_chart(db_session, brand, line):
    assert lc.is_manager_seat(db_session, line["manager"].id, brand.id) is True
    assert lc.is_manager_seat(db_session, line["rep"].id, brand.id) is False


def test_no_person_or_place_is_named_in_the_resolver():
    """The requirement, asserted rather than promised.

    Names appear in this test file and in a docstring example. They must not
    appear as behaviour anywhere in app/ - a special case for one brand's people
    is exactly what makes the capability un-reusable by the next brand.
    """
    import pathlib
    import re
    root = pathlib.Path(lc.__file__).resolve().parents[1]
    forbidden = ("Blake", "Michael", "Mike", "EvoSys", "BookaBoost",
                 "Kentucky", "Texas")
    targets = ["services/leadership_chain.py", "services/leadership_quorum.py",
               "services/sales_booking_codes.py"]
    for rel in targets:
        text = (root / rel).read_text(encoding="utf-8")
        # Strip the module docstring, which is allowed to use an example.
        body = text.split('"""', 2)[-1]
        for name in forbidden:
            assert not re.search(r"\b%s\b" % name, body), (
                "%s names %r outside its docstring" % (rel, name))
