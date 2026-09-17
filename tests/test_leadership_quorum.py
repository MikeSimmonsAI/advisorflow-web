"""THE FIVE QUORUM CASES, plus what the quorum must never do.

THE RULE BEING TESTED.

    bookable  =  the owner is free
                 AND at least `leadership_minimum` leaders from THAT owner's
                 reporting chain are free

and nothing else. Specifically NOT "the owner and every named manager", which
is what `find_shared_slots` would compute and which loses a slot whenever any
one of three people is busy. On real calendars that is most of the week, and
the prospect - who only ever needed one decision-maker in the room - is shown
an empty page.

Cases 1-5 are the required matrix, stated in terms of who is free:

    1. owner free, direct free, senior busy   → bookable, owner + direct
    2. owner free, direct busy,  senior free  → bookable, owner + senior
    3. owner free, both free                  → bookable, all three when the
                                                policy includes extras
    4. owner free, no leader free             → NOT offered
    5. owner busy, both leaders free          → NOT offered

Case 4 is the one worth staring at. The tempting behaviour when a rep's own
chain has nobody free is to widen the search - and a scheduler that does that
will happily put a prospect in front of a manager who has never met the rep,
does not know the deal, and was not told why the meeting appeared. There is no
such fallback, and `test_case4_never_reaches_outside_the_chain` proves it by
leaving an entire second reporting line wide open and asserting nothing is
offered.

BUSY IS EXPRESSED AS A REAL CALENDAR FACT. Every "busy" below is an actual
blocking SalesAppointment or a real time-off block, not a stubbed free/busy
list - so these tests exercise the same availability engine the product uses,
and they would fail if the quorum module started computing availability of its
own.
"""

import itertools
from datetime import datetime, timedelta

import pytest

from app.models.models import Platform, User
from app.models.sales_models import (
    BrandSalesOrg, Membership,
    SCOPE_BRAND_SALES_ORG, ROLE_SALES_MANAGER, ROLE_SALES_REP,
)
from app.models.scheduling_models import (
    AvailabilityWindow, AvailabilityBlock, SalesAppointment,
    AppointmentParticipant, MeetingType,
    APPT_SCHEDULED, APPT_CANCELLED, CONF_PENDING, BLOCK_RECURRING, BLOCK_TIME_OFF,
    LEADERSHIP_REPORTING_CHAIN,
)
from app.services import availability as av
from app.services import leadership_chain as lc
from app.services import leadership_quorum as lq
from app.services.auth_service import hash_password

_SEQ = itertools.count(1)
TZ = "America/Chicago"


# ── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture()
def platform(db_session):
    p = Platform(name="Test Platform", slug="quorum-plat-%d" % next(_SEQ))
    db_session.add(p)
    db_session.commit()
    return p


@pytest.fixture()
def brand(db_session, platform):
    b = BrandSalesOrg(platform_id=platform.id, name="Brand Sales",
                      slug="quorum-brand-%d" % next(_SEQ), timezone=TZ)
    db_session.add(b)
    db_session.commit()
    return b


def _user(db, name):
    u = User(organization_id=None, email="quorum%d@test.live" % next(_SEQ),
             password_hash=hash_password("x"), full_name=name,
             role="advisor", must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _seat(db, user, brand, role=ROLE_SALES_REP, reports_to=None):
    db.add(Membership(user_id=user.id, scope_type=SCOPE_BRAND_SALES_ORG,
                      scope_id=brand.id, role=role, is_active=True,
                      reports_to_user_id=(reports_to.id if reports_to else None)))
    db.commit()


def _profile(db, user, start=9 * 60, end=17 * 60, tz=TZ, days=range(5)):
    """A known 9-5 weekday profile. Written out rather than relying on defaults,
    because a test whose expectation depends on a default silently changes
    meaning when the default does."""
    prof = av.get_or_create_profile(db, user, default_timezone=tz)
    prof.timezone = tz
    prof.min_notice_minutes = 0
    prof.buffer_before_minutes = 0
    prof.buffer_after_minutes = 0
    prof.booking_horizon_days = 365
    prof.accepts_bookings = True
    db.query(AvailabilityWindow).filter(
        AvailabilityWindow.profile_id == prof.id).delete(synchronize_session=False)
    db.query(AvailabilityBlock).filter(
        AvailabilityBlock.profile_id == prof.id).delete(synchronize_session=False)
    for dow in days:
        db.add(AvailabilityWindow(profile_id=prof.id, day_of_week=dow,
                                  start_minute=start, end_minute=end))
    db.commit()
    return prof


def _monday(offset_days=7):
    d = (datetime.utcnow() + timedelta(days=offset_days)).date()
    while d.weekday() != 0:
        d += timedelta(days=1)
    return d


def _busy_all_day(db, brand, user, day):
    """A real blocking appointment covering the whole working day.

    A genuine SalesAppointment rather than a stub, so the availability engine is
    the thing under test alongside the quorum.
    """
    start = av.local_to_utc(day, 8 * 60, TZ)
    end = av.local_to_utc(day, 18 * 60, TZ)
    a = SalesAppointment(brand_sales_org_id=brand.id, title="Booked solid",
                         starts_at=start, ends_at=end, timezone=TZ,
                         status=APPT_SCHEDULED, confirmation_status=CONF_PENDING)
    db.add(a)
    db.flush()
    db.add(AppointmentParticipant(appointment_id=a.id, user_id=user.id,
                                  is_required=True, busy_start_at=start,
                                  busy_end_at=end, is_blocking=True))
    db.commit()
    return a


@pytest.fixture()
def team(db_session, brand):
    """owner -> direct -> senior, all with identical 9-5 weekday availability."""
    senior = _user(db_session, "Senior Leader")
    direct = _user(db_session, "Direct Manager")
    owner = _user(db_session, "Owner Rep")
    _seat(db_session, senior, brand, ROLE_SALES_MANAGER)
    _seat(db_session, direct, brand, ROLE_SALES_MANAGER, reports_to=senior)
    _seat(db_session, owner, brand, ROLE_SALES_REP, reports_to=direct)
    for u in (senior, direct, owner):
        _profile(db_session, u)
    return {"owner": owner, "direct": direct, "senior": senior}


POLICY = lq.QuorumPolicy(policy=LEADERSHIP_REPORTING_CHAIN, owner_required=True,
                         minimum=1, depth=2, include_additional=True)
POLICY_MIN_ONLY = lq.QuorumPolicy(policy=LEADERSHIP_REPORTING_CHAIN,
                                  owner_required=True, minimum=1, depth=2,
                                  include_additional=False)


def _search(db, brand, team, policy=POLICY, day=None, duration=60):
    day = day or _monday()
    start = av.local_to_utc(day, 0, TZ)
    end = av.local_to_utc(day, 23 * 60 + 59, TZ)
    return lq.resolve_and_find(db, team["owner"].id, brand.id, policy,
                               start, end, duration)


# ═══════════════════════════════════════════════════════════════════════════
# CASES 1-5
# ═══════════════════════════════════════════════════════════════════════════

def test_case1_direct_free_senior_busy_books_owner_and_direct(db_session, brand, team):
    day = _monday()
    _busy_all_day(db_session, brand, team["senior"], day)
    out = _search(db_session, brand, team, day=day)
    assert out["slots"], out.get("reason")
    for slot in out["slots"]:
        assert slot["leader_user_ids"] == [team["direct"].id]
        assert set(slot["participant_user_ids"]) == {team["owner"].id, team["direct"].id}


def test_case2_direct_busy_senior_free_books_owner_and_senior(db_session, brand, team):
    day = _monday()
    _busy_all_day(db_session, brand, team["direct"], day)
    out = _search(db_session, brand, team, day=day)
    assert out["slots"], out.get("reason")
    for slot in out["slots"]:
        assert slot["leader_user_ids"] == [team["senior"].id], (
            "the skip-level leader must be usable when the direct manager is not "
            "- requiring the direct manager specifically is the intersection "
            "behaviour this replaced")


def test_case3_both_free_books_all_three_when_extras_are_included(
        db_session, brand, team):
    out = _search(db_session, brand, team, policy=POLICY)
    assert out["slots"], out.get("reason")
    slot = out["slots"][0]
    assert set(slot["leader_user_ids"]) == {team["direct"].id, team["senior"].id}
    assert len(slot["participant_user_ids"]) == 3


def test_case3b_both_free_books_only_the_nearest_when_extras_are_excluded(
        db_session, brand, team):
    """Same calendars, different policy. The NEAREST leader is chosen - the rep's
    own manager is a more appropriate attendee than somebody two levels up."""
    out = _search(db_session, brand, team, policy=POLICY_MIN_ONLY)
    assert out["slots"]
    for slot in out["slots"]:
        assert slot["leader_user_ids"] == [team["direct"].id]
        assert slot["available_leader_user_ids"] == [team["direct"].id,
                                                     team["senior"].id]


def test_case4_owner_free_no_leader_free_offers_nothing(db_session, brand, team):
    day = _monday()
    _busy_all_day(db_session, brand, team["direct"], day)
    _busy_all_day(db_session, brand, team["senior"], day)
    out = _search(db_session, brand, team, day=day)
    assert out["slots"] == []
    reason = out.get("reason") or ""
    # The reason must name the CHAIN specifically. "No times available" would be
    # true and useless: it sends an operator to look at diaries when what they
    # need to know is which two people to chase.
    assert "chain" in reason.lower()
    assert team["direct"].full_name in reason and team["senior"].full_name in reason


def test_case4_never_reaches_outside_the_chain(db_session, brand, team):
    """THE FALLBACK THAT MUST NOT EXIST.

    An entire second reporting line, fully staffed and completely free, sits in
    the same brand. The owner's own chain is busy. The correct answer is still
    nothing - a prospect must not be put in front of a manager who has never met
    the rep and was not told why the meeting appeared.
    """
    day = _monday()
    _busy_all_day(db_session, brand, team["direct"], day)
    _busy_all_day(db_session, brand, team["senior"], day)

    for n in ("Other Director", "Other Manager", "Spare Manager"):
        other = _user(db_session, n)
        _seat(db_session, other, brand, ROLE_SALES_MANAGER)
        _profile(db_session, other)

    out = _search(db_session, brand, team, day=day)
    assert out["slots"] == [], (
        "slots were offered using leadership from outside the owner's reporting "
        "chain - this is the brand-wide manager pool defect returning")


def test_case5_owner_busy_leaders_free_offers_nothing(db_session, brand, team):
    day = _monday()
    _busy_all_day(db_session, brand, team["owner"], day)
    out = _search(db_session, brand, team, day=day)
    assert out["slots"] == []
    assert team["owner"].full_name in (out.get("reason") or "")


# ═══════════════════════════════════════════════════════════════════════════
# The availability rules are NOT reimplemented here
# ═══════════════════════════════════════════════════════════════════════════

def test_time_off_removes_a_leader(db_session, brand, team):
    """PTO is an AvailabilityBlock, not an appointment. The quorum must respect
    it without knowing what it is - which it does, because it never looks."""
    day = _monday()
    prof = av.get_or_create_profile(db_session, team["direct"])
    db_session.add(AvailabilityBlock(
        profile_id=prof.id, kind=BLOCK_TIME_OFF, label="PTO",
        starts_at=av.local_to_utc(day, 0, TZ),
        ends_at=av.local_to_utc(day, 23 * 60 + 59, TZ)))
    db_session.commit()
    out = _search(db_session, brand, team, day=day)
    assert out["slots"]
    for slot in out["slots"]:
        assert team["direct"].id not in slot["leader_user_ids"]


def test_a_lunch_block_carves_the_middle_out_for_everyone(db_session, brand, team):
    day = _monday()
    for u in team.values():
        prof = av.get_or_create_profile(db_session, u)
        db_session.add(AvailabilityBlock(
            profile_id=prof.id, kind=BLOCK_RECURRING, label="Lunch",
            day_of_week=day.weekday(), start_minute=12 * 60, end_minute=13 * 60))
    db_session.commit()
    out = _search(db_session, brand, team, day=day, duration=60)
    starts = [av.utc_to_local(s["starts_at"], TZ).hour for s in out["slots"]]
    assert 12 not in starts


def test_a_cancelled_meeting_stops_blocking(db_session, brand, team):
    """BLOCKING_STATUSES is the availability engine's rule. Asserting it here
    proves the quorum reads that engine rather than querying appointments."""
    day = _monday()
    appt = _busy_all_day(db_session, brand, team["direct"], day)
    _busy_all_day(db_session, brand, team["senior"], day)
    assert _search(db_session, brand, team, day=day)["slots"] == []

    appt.status = APPT_CANCELLED
    for p in db_session.query(AppointmentParticipant).filter(
            AppointmentParticipant.appointment_id == appt.id).all():
        p.is_blocking = False
    db_session.commit()
    assert _search(db_session, brand, team, day=day)["slots"]


def test_a_slot_must_fit_entirely_inside_one_free_interval(db_session, brand, team):
    """A 60-minute meeting cannot straddle a 30-minute gap in a leader's day."""
    day = _monday()
    mid_start = av.local_to_utc(day, 12 * 60, TZ)
    a = SalesAppointment(brand_sales_org_id=brand.id, title="Midday",
                         starts_at=mid_start,
                         ends_at=mid_start + timedelta(minutes=30),
                         timezone=TZ, status=APPT_SCHEDULED,
                         confirmation_status=CONF_PENDING)
    db_session.add(a)
    db_session.flush()
    for u in (team["direct"], team["senior"]):
        db_session.add(AppointmentParticipant(
            appointment_id=a.id, user_id=u.id, is_required=True,
            busy_start_at=mid_start, busy_end_at=mid_start + timedelta(minutes=30),
            is_blocking=True))
    db_session.commit()

    out = _search(db_session, brand, team, day=day, duration=60)
    for slot in out["slots"]:
        assert not (slot["starts_at"] < mid_start + timedelta(minutes=30)
                    and mid_start < slot["ends_at"]), \
            "a slot overlapping a leader's meeting was offered"


# ═══════════════════════════════════════════════════════════════════════════
# Policy plumbing and backwards compatibility
# ═══════════════════════════════════════════════════════════════════════════

def test_a_meeting_type_with_no_policy_is_inert(db_session, brand):
    """Every existing meeting type in every brand has leadership_policy NULL.

    This is the backwards-compatibility guarantee: reading a legacy row must
    produce a policy that does not engage, so nothing already configured changes
    behaviour when this ships.
    """
    mt = MeetingType(brand_sales_org_id=brand.id, key="legacy", name="Legacy",
                     duration_minutes=30, required_slots="opportunity_owner")
    db_session.add(mt)
    db_session.commit()
    pol = lq.QuorumPolicy.from_meeting_type(mt)
    assert pol.active is False
    assert pol.minimum == 0 and pol.depth == 0


def test_an_inactive_policy_searches_no_leaders_at_all(db_session, brand, team):
    """With no policy the chain is not walked - depth 0 - so a legacy meeting
    type cannot accidentally acquire leadership requirements."""
    pol = lq.QuorumPolicy(policy=None)
    out = _search(db_session, brand, team, policy=pol)
    assert out["slots"]
    for slot in out["slots"]:
        assert slot["leader_user_ids"] == []
        assert slot["participant_user_ids"] == [team["owner"].id]


def test_a_missing_chain_is_a_setup_status_not_an_empty_calendar(db_session, brand):
    """The distinction the whole status vocabulary exists for."""
    rep = _user(db_session, "Unmanaged Rep")
    _seat(db_session, rep, brand, ROLE_SALES_REP)
    _profile(db_session, rep)
    day = _monday()
    out = lq.resolve_and_find(
        db_session, rep.id, brand.id, POLICY,
        av.local_to_utc(day, 0, TZ), av.local_to_utc(day, 23 * 60, TZ), 60)
    assert out["slots"] == []
    assert out["chain_status"] == lc.CHAIN_NO_LEADERSHIP
    assert "no reporting manager configured" in out["reason"]


def test_an_unknown_owner_is_refused_before_any_calendar_is_read(db_session, brand):
    day = _monday()
    out = lq.resolve_and_find(
        db_session, "no-such-user", brand.id, POLICY,
        av.local_to_utc(day, 0, TZ), av.local_to_utc(day, 23 * 60, TZ), 60)
    assert out["slots"] == []
    assert out["chain_status"] == lc.CHAIN_OWNER_NOT_A_MEMBER


def test_minimum_two_requires_two_free_leaders(db_session, brand, team):
    day = _monday()
    pol = lq.QuorumPolicy(policy=LEADERSHIP_REPORTING_CHAIN, minimum=2, depth=2,
                          include_additional=True)
    assert _search(db_session, brand, team, policy=pol, day=day)["slots"]
    _busy_all_day(db_session, brand, team["senior"], day)
    assert _search(db_session, brand, team, policy=pol, day=day)["slots"] == []


def test_minimum_beyond_the_chain_length_is_a_setup_status(db_session, brand, team):
    """Asking for three leaders from a two-leader chain is a configuration
    error, and must not be reported as a busy week."""
    pol = lq.QuorumPolicy(policy=LEADERSHIP_REPORTING_CHAIN, minimum=3, depth=2,
                          include_additional=True)
    out = _search(db_session, brand, team, policy=pol)
    assert out["slots"] == []
    assert out["chain_status"] != lc.CHAIN_OK


# ═══════════════════════════════════════════════════════════════════════════
# The final recheck
# ═══════════════════════════════════════════════════════════════════════════

def test_an_uncapped_search_does_not_stop_after_one_slot(db_session, brand, team):
    """THE REGRESSION. `slots_from_intervals` returns after its first slot when
    given limit=0, because its guard is `len(out) >= limit`. Forwarding the
    caller's 0 through to it meant an uncapped search found exactly one
    candidate - and the booking path calls this uncapped to re-check one
    specific time, so every booking except the earliest offered one was refused
    as unavailable."""
    day = _monday()
    out = lq.quorum_slots(db_session, team["owner"],
                          [team["direct"], team["senior"]], POLICY,
                          av.local_to_utc(day, 0, TZ),
                          av.local_to_utc(day, 23 * 60, TZ), 60, limit=0)
    assert len(out["slots"]) > 1, (
        "an uncapped quorum search returned %d slot(s)" % len(out["slots"]))


def test_slot_is_still_open_returns_who_is_not(db_session, brand, team):
    day = _monday()
    out = _search(db_session, brand, team, day=day)
    slot = out["slots"][0]
    people = [team["owner"], team["direct"]]
    assert lq.slot_is_still_open(db_session, people,
                                 slot["starts_at"], slot["ends_at"]) == []

    # Somebody books over it in the meantime - which is the whole scenario the
    # recheck exists for.
    a = SalesAppointment(brand_sales_org_id=brand.id, title="Snuck in",
                         starts_at=slot["starts_at"], ends_at=slot["ends_at"],
                         timezone=TZ, status=APPT_SCHEDULED,
                         confirmation_status=CONF_PENDING)
    db_session.add(a)
    db_session.flush()
    db_session.add(AppointmentParticipant(
        appointment_id=a.id, user_id=team["direct"].id, is_required=True,
        busy_start_at=slot["starts_at"], busy_end_at=slot["ends_at"],
        is_blocking=True))
    db_session.commit()

    gone = lq.slot_is_still_open(db_session, people,
                                 slot["starts_at"], slot["ends_at"])
    assert [u.id for u in gone] == [team["direct"].id]


def test_the_recheck_respects_minimum_notice_that_has_since_bitten(
        db_session, brand, team):
    """A slot that has drifted inside the notice window since it was offered is
    genuinely no longer bookable, and the recheck must say so."""
    prof = av.get_or_create_profile(db_session, team["owner"])
    prof.min_notice_minutes = 60 * 24 * 30
    db_session.commit()
    day = _monday()
    start = av.local_to_utc(day, 10 * 60, TZ)
    gone = lq.slot_is_still_open(db_session, [team["owner"]], start,
                                 start + timedelta(minutes=60))
    assert [u.id for u in gone] == [team["owner"].id]
