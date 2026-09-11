"""
Calendar + Sales Workspace scheduling — the behavioural suite.

WHAT THIS FILE DEFENDS, and why each one earns a test rather than a comment:

  ENGINE          participant intersection (not a union), work hours, lunch,
                  PTO, blocked time, buffers, minimum notice, horizon, and
                  EXTERNAL busy — the one the engine reads from a cache that
                  nobody was refreshing.
  OUTCOME         the T9 completion gap: a verdict is written by a human,
                  never inferred from a clock; NULL means "not yet"; the
                  status and the outcome can never contradict each other; a
                  meeting that did not happen stops blocking calendars.
  RECONCILIATION  a provider-side DELETE heals, a provider-side MOVE raises a
                  visible conflict and overwrites nothing, an unreachable
                  provider claims nothing at all.
  CONCURRENCY     two bookings for one slot, and an external event that
                  appears between the search and the save.
  TIMEZONE / DST  spring forward and fall back, resolved per local date.
  PRIVACY         cross-brand isolation, another rep's meeting titles,
                  external event titles that were never stored, and provider
                  tokens that must never appear in a payload.
  MOBILE          the field view's contract, from the same payload.

Provider behaviour is exercised through a FAKE registered in the provider
registry, never over the network. Nothing in this suite may reach Microsoft or
Google — see the registry's `register_provider` seam and the teardown that
clears it.
"""
import itertools
from datetime import datetime, timedelta, date

import pytest

from app.models.models import Platform, User
from app.models.sales_models import (
    BrandSalesOrg, Membership, Opportunity,
    SCOPE_BRAND_SALES_ORG, ROLE_SALES_MANAGER, ROLE_SALES_REP,
    STAGE_DISCOVERY, STAGE_PROPOSAL, STAGE_WON,
)
from app.models.scheduling_models import (
    AvailabilityProfile, AvailabilityWindow, AvailabilityBlock,
    MeetingType, SalesAppointment, AppointmentParticipant,
    APPT_SCHEDULED, APPT_COMPLETED, APPT_CANCELLED, APPT_NO_SHOW,
    BLOCK_RECURRING, BLOCK_TIME_OFF,
    CONF_PENDING, CONF_CONFIRMED,
    OUTCOME_COMPLETED, OUTCOME_NO_SHOW, OUTCOME_CANCELLED, OUTCOME_RESCHEDULED,
    OUTCOME_FOLLOW_UP, OUTCOME_PROPOSAL_NEEDED, OUTCOME_WON, OUTCOME_LOST,
    OUTCOMES_OCCURRED,
)
from app.models.calendar_models import (
    CalendarConnection, ExternalBusyBlock, AppointmentSyncLog,
    PROVIDER_MICROSOFT, PROVIDER_GOOGLE,
    CONFLICT_DELETED, CONFLICT_MOVED, CONFLICT_ORPHANED,
    SYNC_SYNCED,
)
from app.services import availability as av
from app.services import external_busy as extbusy
from app.services import appointment_outcome as apoutcome
from app.services import appointment_reconcile as apreconcile
from app.services import calendar_providers as reg
from app.services.calendar_providers.base import (
    CalendarProvider, SyncResult, BusyInterval, ExternalEventState,
)
from app.services.auth_service import hash_password, create_access_token

_SEQ = itertools.count(1)


# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture()
def platform(db_session):
    p = Platform(name="EvoSys Pro", slug="csx-plat-%d" % next(_SEQ))
    db_session.add(p)
    db_session.commit()
    return p


@pytest.fixture()
def brand(db_session, platform):
    b = BrandSalesOrg(platform_id=platform.id, name="EvoSys Sales",
                      slug="csx-brand-%d" % next(_SEQ),
                      timezone="America/Chicago")
    db_session.add(b)
    db_session.commit()
    return b


@pytest.fixture()
def brand_b(db_session, platform):
    """A SECOND brand. Exists purely so every isolation claim has a real other
    side — a tenancy test against a brand that does not exist proves nothing."""
    b = BrandSalesOrg(platform_id=platform.id, name="Other Sales",
                      slug="csx-brandb-%d" % next(_SEQ),
                      timezone="America/Chicago")
    db_session.add(b)
    db_session.commit()
    return b


def _user(db, brand=None, role=ROLE_SALES_REP, name="Rep", tz=None):
    u = User(organization_id=None,
             email="csx%d@test.live" % next(_SEQ),
             password_hash=hash_password("x"), full_name=name,
             role="advisor", must_change_password=False)
    db.add(u)
    db.commit()
    if brand is not None:
        db.add(Membership(user_id=u.id, scope_type=SCOPE_BRAND_SALES_ORG,
                          scope_id=brand.id, role=role, is_active=True))
        db.commit()
    if tz:
        prof = av.get_or_create_profile(db, u, default_timezone=tz)
        prof.timezone = tz
        db.commit()
    return u


def _h(u, db):
    return {"Authorization": "Bearer " + create_access_token(u, db)}


def _profile(db, user, tz="America/Chicago", start=9 * 60, end=17 * 60,
             lunch=(12 * 60, 13 * 60), notice=0, buf_before=0, buf_after=0,
             days=range(5)):
    """A deliberate, known availability profile.

    Written explicitly rather than relying on `get_or_create_profile`'s
    defaults, because a test whose expectations depend on a default is a test
    that silently changes meaning when the default does.
    """
    prof = av.get_or_create_profile(db, user, default_timezone=tz)
    prof.timezone = tz
    prof.min_notice_minutes = notice
    prof.buffer_before_minutes = buf_before
    prof.buffer_after_minutes = buf_after
    prof.booking_horizon_days = 365
    db.query(AvailabilityWindow).filter(
        AvailabilityWindow.profile_id == prof.id).delete(synchronize_session=False)
    db.query(AvailabilityBlock).filter(
        AvailabilityBlock.profile_id == prof.id).delete(synchronize_session=False)
    for dow in days:
        db.add(AvailabilityWindow(profile_id=prof.id, day_of_week=dow,
                                  start_minute=start, end_minute=end))
        if lunch:
            db.add(AvailabilityBlock(profile_id=prof.id, kind=BLOCK_RECURRING,
                                     label="Lunch", day_of_week=dow,
                                     start_minute=lunch[0], end_minute=lunch[1]))
    db.commit()
    return prof


def _next_weekday(offset_days=7):
    """A Monday comfortably in the future, so `min_notice` and the horizon are
    never the thing a test is accidentally measuring."""
    d = (datetime.utcnow() + timedelta(days=offset_days)).date()
    while d.weekday() != 0:
        d += timedelta(days=1)
    return d


def _appt(db, brand, users, starts_at, duration=30, status=APPT_SCHEDULED,
          opportunity=None, meeting_type=None, title="Test Meeting"):
    a = SalesAppointment(
        brand_sales_org_id=brand.id,
        opportunity_id=(opportunity.id if opportunity else None),
        meeting_type_id=(meeting_type.id if meeting_type else None),
        title=title, starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=duration),
        timezone="America/Chicago", status=status,
        confirmation_status=CONF_PENDING)
    db.add(a)
    db.flush()
    for u in users:
        prof = av.get_or_create_profile(db, u)
        bs, be = av.buffered_window(prof, a.starts_at, a.ends_at)
        db.add(AppointmentParticipant(
            appointment_id=a.id, user_id=u.id, is_required=True,
            busy_start_at=bs, busy_end_at=be,
            is_blocking=(status == APPT_SCHEDULED)))
    db.commit()
    return a


def _connect(db, user, provider=PROVIDER_MICROSOFT):
    """A live, calendar-capable connection.

    `calendar_scope_ok` is set because the registry checks it: a Microsoft
    connection made before calendar permission was requested is live for email
    and useless for calendar, and treating that as connected is how a booking
    reports success while writing to nothing.
    """
    c = CalendarConnection(user_id=user.id, provider=provider,
                           is_connected=True, calendar_scope_ok=True,
                           account_email="cal%d@test.live" % next(_SEQ),
                           connected_at=datetime.utcnow())
    db.add(c)
    # The provider layer reads the token off the USER row, so a connection
    # without one is not actually usable.
    setattr(user, provider + "_oauth_refresh_token_encrypted", "fake-token")
    db.commit()
    return c


# ── the fake provider ───────────────────────────────────────────────────────

class FakeProvider(CalendarProvider):
    """A provider that does exactly what a test tells it to.

    Registered through the registry's own seam, so every call site — the sync
    orchestrator, the reconciler, the availability refresh — picks it up with
    NO `if testing:` branch in production code. Production code that knows it
    is being tested is production code that is not being tested.
    """
    key = PROVIDER_MICROSOFT

    # Class-level so a test can set behaviour before the registry builds one.
    busy = []
    events = {}
    fail_get_event = None      # a SyncResult to return instead of a state
    fail_write = None
    created = []
    updated = []
    cancelled = []
    ready = True
    _seq = itertools.count(1)

    @classmethod
    def reset(cls):
        cls.busy = []
        cls.events = {}
        cls.fail_get_event = None
        cls.fail_write = None
        cls.created = []
        cls.updated = []
        cls.cancelled = []
        cls.ready = True

    def is_ready(self):
        return (True, None) if FakeProvider.ready else (False, "not connected")

    def supports_read_back(self):
        return True

    def create_event(self, payload):
        if FakeProvider.fail_write:
            return FakeProvider.fail_write
        eid = "ev-%d" % next(FakeProvider._seq)
        FakeProvider.created.append((eid, payload))
        FakeProvider.events[eid] = ExternalEventState(
            exists=True, starts_at=payload.starts_at, ends_at=payload.ends_at,
            etag="e1", claimed_appointment_id=payload.advisorflow_appointment_id,
            subject=payload.subject)
        return SyncResult(ok=True, external_event_id=eid)

    def update_event(self, external_event_id, payload):
        if FakeProvider.fail_write:
            return FakeProvider.fail_write
        FakeProvider.updated.append((external_event_id, payload))
        FakeProvider.events[external_event_id] = ExternalEventState(
            exists=True, starts_at=payload.starts_at, ends_at=payload.ends_at,
            etag="e2", claimed_appointment_id=payload.advisorflow_appointment_id,
            subject=payload.subject)
        return SyncResult(ok=True, external_event_id=external_event_id)

    def cancel_event(self, external_event_id, payload=None):
        FakeProvider.cancelled.append(external_event_id)
        FakeProvider.events.pop(external_event_id, None)
        return SyncResult(ok=True, external_event_id=external_event_id)

    def get_busy(self, start_utc, end_utc):
        return [BusyInterval(starts_at=s, ends_at=e)
                for s, e in FakeProvider.busy
                if e > start_utc and s < end_utc], None

    def get_event(self, external_event_id):
        if FakeProvider.fail_get_event is not None:
            return None, FakeProvider.fail_get_event
        st = FakeProvider.events.get(external_event_id)
        if st is None:
            # Gone. A normal answer, NOT an error — that distinction is what
            # lets the reconciler tell "somebody deleted this" from "Microsoft
            # was down", which are opposite situations.
            return ExternalEventState(exists=False), None
        return st, None


@pytest.fixture()
def fake_provider():
    FakeProvider.reset()
    reg.register_provider(PROVIDER_MICROSOFT, lambda u, c, o: FakeProvider(u, c, o))
    yield FakeProvider
    # MUST reset, or the fake leaks into every test that runs afterwards.
    reg.reset_providers()
    FakeProvider.reset()


# ═══════════════════════════════════════════════════════════════════════
# THE ENGINE — intersection, and every rule that removes time
# ═══════════════════════════════════════════════════════════════════════

def test_intersection_is_not_a_union(db_session, brand):
    """THE headline behaviour. Three people, one answer.

    A union ("any one of them is free") is a different product and would offer
    a time two of the three cannot make. This asserts the opposite: a slot
    survives only if EVERYBODY required is free.
    """
    a = _user(db_session, brand, name="Blake")
    b = _user(db_session, brand, name="Michael")
    c = _user(db_session, brand, name="Mike")
    day = _next_weekday()

    # Blake free 9–13 and 14–16; Michael 10–11 and 14–15; Mike 11–12 and 14–16.
    _profile(db_session, a, start=9 * 60, end=16 * 60, lunch=(13 * 60, 14 * 60))
    _profile(db_session, b, start=10 * 60, end=15 * 60, lunch=(11 * 60, 14 * 60))
    _profile(db_session, c, start=11 * 60, end=16 * 60, lunch=(12 * 60, 14 * 60))

    start = av.local_to_utc(day, 0, "America/Chicago")
    end = av.local_to_utc(day + timedelta(days=1), 0, "America/Chicago")
    res = av.find_shared_slots(db_session, [a, b, c], [], start, end, 30)

    assert res["slots"], "there is a genuine overlap; the engine must find it"
    for s in res["slots"]:
        local = av.utc_to_local(s["starts_at"], "America/Chicago")
        mins = local.hour * 60 + local.minute
        # The only window all three share is 14:00–15:00.
        assert 14 * 60 <= mins < 15 * 60, (
            "returned %s, which at least one participant cannot make" % local)


def test_an_optional_participant_never_removes_a_slot(db_session, brand):
    a = _user(db_session, brand, name="Blake")
    opt = _user(db_session, brand, name="Optional")
    day = _next_weekday()
    _profile(db_session, a, start=9 * 60, end=17 * 60, lunch=None)
    # The optional person works a single hour, nowhere near most of the day.
    _profile(db_session, opt, start=16 * 60, end=17 * 60, lunch=None)

    start = av.local_to_utc(day, 0, "America/Chicago")
    end = av.local_to_utc(day + timedelta(days=1), 0, "America/Chicago")
    res = av.find_shared_slots(db_session, [a], [opt], start, end, 30)

    assert len(res["slots"]) > 2, "the optional person must not narrow the answer"
    decorated = [s for s in res["slots"] if s["optional_available_count"]]
    assert decorated, "slots the optional person CAN make must say so"


def test_lunch_pto_and_buffers_each_remove_time(db_session, brand):
    u = _user(db_session, brand, name="Solo")
    day = _next_weekday()
    _profile(db_session, u, start=9 * 60, end=17 * 60, lunch=(12 * 60, 13 * 60),
             buf_before=15, buf_after=15)
    prof = av.get_or_create_profile(db_session, u)

    # PTO for the afternoon.
    db_session.add(AvailabilityBlock(
        profile_id=prof.id, kind=BLOCK_TIME_OFF, label="PTO",
        starts_at=av.local_to_utc(day, 15 * 60, "America/Chicago"),
        ends_at=av.local_to_utc(day, 17 * 60, "America/Chicago")))
    # And a meeting 10:00–10:30, which with buffers occupies 09:45–10:45.
    _appt(db_session, brand, [u],
          av.local_to_utc(day, 10 * 60, "America/Chicago"))
    db_session.commit()

    start = av.local_to_utc(day, 0, "America/Chicago")
    end = av.local_to_utc(day + timedelta(days=1), 0, "America/Chicago")
    free = av.free_intervals_for_user(db_session, u, start, end,
                                      now_utc=start - timedelta(days=1))

    def covered(h, m=0):
        t = av.local_to_utc(day, h * 60 + m, "America/Chicago")
        return any(s <= t < e for s, e in free)

    # The meeting is 10:00–10:30 and the buffers are 15 minutes either side, so
    # the occupied window is 09:45–10:45. 09:30 is genuinely free; 09:50 is not.
    assert covered(9, 30) is True, "09:30 is before the leading buffer starts"
    assert covered(9, 50) is False, "09:50 is inside the meeting's own buffer"
    assert covered(10, 15) is False, "inside the meeting"
    assert covered(10, 40) is False, "inside the trailing buffer"
    assert covered(11, 0) is True, "genuinely free"
    assert covered(12, 30) is False, "lunch"
    assert covered(14, 0) is True, "genuinely free"
    assert covered(15, 30) is False, "PTO"


def test_minimum_notice_removes_the_next_hour(db_session, brand):
    u = _user(db_session, brand)
    # Working every day, so the only thing that can remove the next hour is
    # the notice rule itself.
    _profile(db_session, u, start=0, end=24 * 60 - 1, lunch=None,
             notice=120, days=range(7))
    now = datetime.utcnow()
    free = av.free_intervals_for_user(db_session, u, now, now + timedelta(hours=6),
                                      now_utc=now)
    assert free, "beyond the notice window there should be free time"
    assert min(s for s, _ in free) >= now + timedelta(minutes=119)


def test_an_empty_required_set_returns_nothing_not_everything(db_session, brand):
    """A meeting with no resolved participants is an upstream bug.

    Answering it with unlimited availability would hide that bug behind a
    plausible-looking screen, which is the worst possible failure for a
    scheduler: confidently wrong.
    """
    now = datetime.utcnow()
    res = av.find_shared_slots(db_session, [], [], now, now + timedelta(days=1), 30)
    assert res["slots"] == []
    assert res["blockers"]


def test_a_cancelled_meeting_stops_blocking(db_session, brand):
    u = _user(db_session, brand)
    day = _next_weekday()
    _profile(db_session, u, lunch=None)
    a = _appt(db_session, brand, [u],
              av.local_to_utc(day, 10 * 60, "America/Chicago"))

    start = av.local_to_utc(day, 0, "America/Chicago")
    end = av.local_to_utc(day + timedelta(days=1), 0, "America/Chicago")
    t = av.local_to_utc(day, 10 * 60 + 15, "America/Chicago")

    free = av.free_intervals_for_user(db_session, u, start, end,
                                      now_utc=start - timedelta(days=1))
    assert not any(s <= t < e for s, e in free), "a live meeting blocks"

    db_session.query(AppointmentParticipant).filter(
        AppointmentParticipant.appointment_id == a.id).update(
        {"is_blocking": False}, synchronize_session=False)
    a.status = APPT_CANCELLED
    db_session.commit()

    free = av.free_intervals_for_user(db_session, u, start, end,
                                      now_utc=start - timedelta(days=1))
    assert any(s <= t < e for s, e in free), (
        "a cancelled meeting that keeps blocking fills a rep's week with ghosts")


# ═══════════════════════════════════════════════════════════════════════
# EXTERNAL BUSY — the cache nobody was refreshing
# ═══════════════════════════════════════════════════════════════════════

def test_external_busy_removes_time_from_the_engine(db_session, brand):
    u = _user(db_session, brand)
    day = _next_weekday()
    _profile(db_session, u, lunch=None)
    db_session.add(ExternalBusyBlock(
        user_id=u.id, provider=PROVIDER_MICROSOFT,
        starts_at=av.local_to_utc(day, 10 * 60, "America/Chicago"),
        ends_at=av.local_to_utc(day, 11 * 60, "America/Chicago"),
        fetched_at=datetime.utcnow()))
    db_session.commit()

    start = av.local_to_utc(day, 0, "America/Chicago")
    end = av.local_to_utc(day + timedelta(days=1), 0, "America/Chicago")
    free = av.free_intervals_for_user(db_session, u, start, end,
                                      now_utc=start - timedelta(days=1))
    t = av.local_to_utc(day, 10 * 60 + 30, "America/Chicago")
    assert not any(s <= t < e for s, e in free), (
        "an outside meeting must remove availability, or the whole external "
        "integration is decorative")


def test_find_team_time_refreshes_the_external_cache(client, db_session, brand,
                                                     fake_provider):
    """THE REGRESSION THIS WHOLE PIECE OF WORK STARTED FROM.

    `refresh_many` existed and was never called from the sales surfaces, so the
    finder answered from whatever the cache happened to hold — which for most
    users was nothing. A rep was told somebody was free during a meeting that
    had been on their Outlook calendar for a week.

    This asserts the endpoint actually reads the provider: the fake reports a
    busy hour that is in NO database table when the request starts, and the
    returned slots must respect it.
    """
    mgr = _user(db_session, brand, ROLE_SALES_MANAGER, name="Manager")
    _profile(db_session, mgr, lunch=None)
    _connect(db_session, mgr)

    day = _next_weekday()
    FakeProvider.busy = [(
        av.local_to_utc(day, 10 * 60, "America/Chicago"),
        av.local_to_utc(day, 11 * 60, "America/Chicago"),
    )]
    assert db_session.query(ExternalBusyBlock).count() == 0

    r = client.post("/sales/availability/find", headers=_h(mgr, db_session),
                    json={"required_user_ids": [mgr.id],
                          "duration_minutes": 30,
                          "date_from": day.isoformat(),
                          "date_to": day.isoformat()})
    assert r.status_code == 200, r.text
    body = r.json()

    # It went and looked.
    assert db_session.query(ExternalBusyBlock).count() >= 1, (
        "the finder did not refresh the external cache")
    # And the answer respects what it found.
    for s in body["slots"]:
        local = s["starts_at_local"]
        hhmm = str(local)[11:16]
        assert not ("10:00" <= hhmm < "11:00"), (
            "offered %s, which is inside an external meeting" % hhmm)
    assert body["external_visibility"]["external_checked"] == 1


def test_availability_says_when_a_calendar_could_not_be_read(client, db_session,
                                                             brand, fake_provider):
    """"Free" and "we could not check" must never look the same.

    A provider failure degrades the answer honestly instead of silently
    converting "we do not know" into "they are available".
    """
    mgr = _user(db_session, brand, ROLE_SALES_MANAGER)
    _profile(db_session, mgr, lunch=None)
    _connect(db_session, mgr)
    FakeProvider.ready = False           # the grant is dead

    r = client.get("/sales/availability/team", headers=_h(mgr, db_session))
    assert r.status_code == 200, r.text
    body = r.json()
    me = [m for m in body["members"] if m["user_id"] == mgr.id][0]
    assert me["external"]["external_checked"] is False
    assert me["external"]["message"], "an unverified column must say why"
    assert body["external_visibility"]["complete"] is False


def test_a_dead_grant_is_never_reported_as_an_empty_calendar(db_session, brand,
                                                             fake_provider):
    """THE SILENT BUG. Three correct behaviours that together told a lie.

    `resolve_provider_key` says "microsoft" whenever a live connection row
    exists. `get_provider` may FALL BACK to the .ics provider when Microsoft
    reports itself unready — a revoked consent, a dead token. And
    `IcsEmailProvider.get_busy` correctly returns ([], None), meaning "there is
    no external calendar to read", which is true for somebody who never
    connected one.

    Composed, they turned a dead Microsoft grant into a SUCCESSFUL read of an
    empty Outlook calendar: the cached busy blocks were deleted, the connection
    was stamped healthy so the ten-minute freshness check suppressed the next
    refresh, and the grid reported the person as verified-free. Somebody would
    have been booked over a meeting nobody could see.

    So: the fallback must be detected, the cache must survive, and the report
    must say the calendar could not be read.
    """
    u = _user(db_session, brand)
    conn = _connect(db_session, u)
    day = _next_weekday()
    db_session.add(ExternalBusyBlock(
        user_id=u.id, provider=PROVIDER_MICROSOFT,
        starts_at=av.local_to_utc(day, 10 * 60, "America/Chicago"),
        ends_at=av.local_to_utc(day, 11 * 60, "America/Chicago"),
        fetched_at=datetime.utcnow() - timedelta(hours=2)))
    db_session.commit()

    FakeProvider.ready = False           # the grant is dead

    report = extbusy.refresh_external_busy(
        db_session, u,
        av.local_to_utc(day, 0, "America/Chicago"),
        av.local_to_utc(day + timedelta(days=1), 0, "America/Chicago"),
        force=True)
    db_session.commit()

    assert report["refreshed"] is False
    assert report["reason"] == "provider_unavailable", (
        "falling back to .ics must not be reported as a successful read")
    assert report["needs_reauth"] is True

    # The cache survived.
    assert db_session.query(ExternalBusyBlock).filter(
        ExternalBusyBlock.user_id == u.id).count() == 1

    # The connection is NOT stamped healthy, so freshness cannot suppress the
    # next attempt and the UI can ask for a reconnect.
    db_session.refresh(conn)
    assert conn.calendar_scope_ok is False
    assert conn.last_error
    assert (conn.failure_count or 0) >= 1
    assert extbusy.cache_is_fresh(
        db_session, u.id, PROVIDER_MICROSOFT,
        av.local_to_utc(day, 0, "America/Chicago"),
        av.local_to_utc(day + timedelta(days=1), 0, "America/Chicago")) is False

    # And the grid says so rather than claiming a check it did not make.
    vis = extbusy.visibility_from_report({u.id: report})
    assert vis[u.id]["external_checked"] is False
    assert vis[u.id]["state"] == "reauth_required"
    assert vis[u.id]["message"]


def test_a_provider_outage_does_not_wipe_the_existing_cache(db_session, brand,
                                                            fake_provider):
    """Stale busy time is closer to the truth than none.

    Wiping the cache on a transient 500 would offer colleagues slots the person
    is actually in a meeting for — failing open on ACCURACY, which is the one
    direction this system must never fail.
    """
    u = _user(db_session, brand)
    _connect(db_session, u)
    day = _next_weekday()
    db_session.add(ExternalBusyBlock(
        user_id=u.id, provider=PROVIDER_MICROSOFT,
        starts_at=av.local_to_utc(day, 10 * 60, "America/Chicago"),
        ends_at=av.local_to_utc(day, 11 * 60, "America/Chicago"),
        fetched_at=datetime.utcnow() - timedelta(hours=2)))
    db_session.commit()

    class Broken(FakeProvider):
        def get_busy(self, s, e):
            return [], SyncResult.failure("http_500", "boom")

    reg.register_provider(PROVIDER_MICROSOFT, lambda us, c, o: Broken(us, c, o))
    report = extbusy.refresh_external_busy(
        db_session, u, av.local_to_utc(day, 0, "America/Chicago"),
        av.local_to_utc(day + timedelta(days=1), 0, "America/Chicago"),
        force=True)
    db_session.commit()

    assert report["refreshed"] is False
    assert report["reason"] == "provider_error"
    assert db_session.query(ExternalBusyBlock).filter(
        ExternalBusyBlock.user_id == u.id).count() == 1, (
        "the previous cache must survive a provider error")


# ═══════════════════════════════════════════════════════════════════════
# CONCURRENCY — the slot that was true thirty seconds ago
# ═══════════════════════════════════════════════════════════════════════

def test_two_bookings_for_one_slot_and_one_loses(client, db_session, brand):
    mgr = _user(db_session, brand, ROLE_SALES_MANAGER)
    _profile(db_session, mgr, lunch=None, notice=0)
    day = _next_weekday()
    when = (av.local_to_utc(day, 10 * 60, "America/Chicago")).isoformat()

    first = client.post("/sales/appointments", headers=_h(mgr, db_session),
                        json={"starts_at": when, "duration_minutes": 30,
                              "required_user_ids": [mgr.id]})
    assert first.status_code == 201, first.text

    second = client.post("/sales/appointments", headers=_h(mgr, db_session),
                         json={"starts_at": when, "duration_minutes": 30,
                               "required_user_ids": [mgr.id]})
    assert second.status_code == 409, (
        "the second booking of the same slot must be refused, not silently "
        "double-booked")
    assert "another opening" in second.json()["detail"].lower() \
        or "already booked" in second.json()["detail"].lower()


def test_an_external_event_appearing_mid_booking_is_caught(client, db_session,
                                                           brand, fake_provider):
    """"Do not trust a slot just because it was displayed 30 seconds ago."

    The internal conflict check cannot see a meeting that lives only in
    Outlook. This proves the FINAL revalidation does: the fake starts clean, so
    the slot is genuinely offered, and the conflicting event appears before the
    save.
    """
    mgr = _user(db_session, brand, ROLE_SALES_MANAGER)
    _profile(db_session, mgr, lunch=None, notice=0)
    _connect(db_session, mgr)
    day = _next_weekday()
    starts = av.local_to_utc(day, 10 * 60, "America/Chicago")

    # Clean at search time.
    FakeProvider.busy = []
    r = client.post("/sales/availability/find", headers=_h(mgr, db_session),
                    json={"required_user_ids": [mgr.id], "duration_minutes": 30,
                          "date_from": day.isoformat(), "date_to": day.isoformat()})
    assert r.status_code == 200
    assert any(str(s["starts_at_local"])[11:16] == "10:00" for s in r.json()["slots"])

    # Somebody accepts an invitation in the meantime.
    FakeProvider.busy = [(starts, starts + timedelta(minutes=30))]

    booked = client.post("/sales/appointments", headers=_h(mgr, db_session),
                         json={"starts_at": starts.isoformat(),
                               "duration_minutes": 30,
                               "required_user_ids": [mgr.id]})
    assert booked.status_code == 409, (
        "an external event that appeared after the search must block the save")
    assert "connected calendar" in booked.json()["detail"]
    assert db_session.query(SalesAppointment).count() == 0, (
        "nothing may be written when the final check refuses")


def test_an_unreadable_calendar_does_not_veto_a_booking(client, db_session,
                                                        brand, fake_provider):
    """A Microsoft outage must not stop the sales team booking meetings.

    The revalidation fails OPEN on unreadable, closed on a known conflict.
    Refusing on unreadable would hand a vendor a veto over the team's day.
    """
    mgr = _user(db_session, brand, ROLE_SALES_MANAGER)
    _profile(db_session, mgr, lunch=None, notice=0)
    _connect(db_session, mgr)
    day = _next_weekday()

    class Broken(FakeProvider):
        def get_busy(self, s, e):
            return [], SyncResult.failure("http_500", "boom")

    reg.register_provider(PROVIDER_MICROSOFT, lambda u, c, o: Broken(u, c, o))

    r = client.post("/sales/appointments", headers=_h(mgr, db_session),
                    json={"starts_at": av.local_to_utc(
                        day, 10 * 60, "America/Chicago").isoformat(),
                        "duration_minutes": 30,
                        "required_user_ids": [mgr.id]})
    assert r.status_code == 201, r.text


# ═══════════════════════════════════════════════════════════════════════
# TIMEZONE AND DST
# ═══════════════════════════════════════════════════════════════════════

def test_nine_am_stays_nine_am_across_spring_forward(db_session, brand):
    """The whole reason recurring rules are stored as local minutes.

    US DST began 8 March 2026. A working window resolved to UTC once and
    repeated would shift everyone's day by an hour; resolved per local date, it
    does not.
    """
    u = _user(db_session, brand)
    _profile(db_session, u, tz="America/Chicago", start=9 * 60, end=17 * 60,
             lunch=None, days=range(7))

    before = date(2026, 3, 6)     # CST, UTC-6
    after = date(2026, 3, 10)     # CDT, UTC-5

    s_before = av.local_to_utc(before, 9 * 60, "America/Chicago")
    s_after = av.local_to_utc(after, 9 * 60, "America/Chicago")
    assert s_before.hour == 15, "9am CST is 15:00 UTC"
    assert s_after.hour == 14, "9am CDT is 14:00 UTC"

    for d in (before, after):
        start = av.local_to_utc(d, 0, "America/Chicago")
        end = av.local_to_utc(d + timedelta(days=1), 0, "America/Chicago")
        free = av.free_intervals_for_user(db_session, u, start, end,
                                          now_utc=start - timedelta(days=30))
        assert free, "the working day must exist on both sides of the transition"
        first_local = av.utc_to_local(min(s for s, _ in free), "America/Chicago")
        assert first_local.hour == 9, (
            "%s: the day started at %s local, not 9am" % (d, first_local))


def test_the_working_day_survives_fall_back(db_session, brand):
    """1 November 2026: the 1am hour happens twice.

    A naive implementation either duplicates an hour of availability or drops
    the day. Neither is acceptable, so this asserts the day is a single,
    sensible window.
    """
    u = _user(db_session, brand)
    _profile(db_session, u, tz="America/Chicago", start=0, end=23 * 60 + 59,
             lunch=None, days=range(7))
    d = date(2026, 11, 1)
    start = av.local_to_utc(d, 0, "America/Chicago")
    end = av.local_to_utc(d + timedelta(days=1), 0, "America/Chicago")
    free = av.free_intervals_for_user(db_session, u, start, end,
                                      now_utc=start - timedelta(days=30))
    assert free
    total = sum((e - s).total_seconds() for s, e in free) / 3600.0
    # 25 hours of wall clock that day; the window is 23h59m of it.
    assert 23.0 <= total <= 25.5, "fall-back produced %.2f hours" % total


def test_participants_in_different_zones_intersect_on_real_time(db_session, brand):
    """Two people, two timezones, one correct answer.

    Chicago 9–17 and Los Angeles 9–17 overlap only 11:00–17:00 Chicago. A
    system that compared local clock numbers would claim the whole day.
    """
    chi = _user(db_session, brand, name="Chicago")
    la = _user(db_session, brand, name="LA")
    _profile(db_session, chi, tz="America/Chicago", lunch=None)
    _profile(db_session, la, tz="America/Los_Angeles", lunch=None)
    day = _next_weekday()

    start = av.local_to_utc(day, 0, "America/Chicago") - timedelta(hours=12)
    end = av.local_to_utc(day + timedelta(days=1), 0, "America/Chicago") + timedelta(hours=12)
    res = av.find_shared_slots(db_session, [chi, la], [], start, end, 60)
    assert res["slots"]
    for s in res["slots"]:
        c = av.utc_to_local(s["starts_at"], "America/Chicago")
        if c.date() != day:
            continue
        assert 11 <= c.hour < 17, (
            "offered %s Chicago, which is outside one of the two working days" % c)


# ═══════════════════════════════════════════════════════════════════════
# OUTCOME — the T9 completion gap
# ═══════════════════════════════════════════════════════════════════════

def test_a_past_meeting_with_no_outcome_is_unrecorded_not_completed(db_session, brand):
    """NULL means "nobody has said yet" and must never be read as either side.

    Inferring completion from a clock is the exact failure T9 reported, so the
    facts endpoint reports `unrecorded` as its own number and refuses to
    compute a rate over it.
    """
    u = _user(db_session, brand)
    _appt(db_session, brand, [u], datetime.utcnow() - timedelta(hours=3))

    facts = apoutcome.completion_facts(db_session, brand.id)
    assert facts["unrecorded"] == 1
    assert facts["occurred"] == 0
    assert facts["did_not_occur"] == 0
    assert facts["completion_rate"] is None, (
        "a rate over zero recorded verdicts is a claim, not a measurement")
    assert facts["inferred_from_clock"] is False


def test_recording_completed_makes_the_meeting_authoritatively_occurred(
        client, db_session, brand):
    u = _user(db_session, brand, ROLE_SALES_MANAGER)
    a = _appt(db_session, brand, [u], datetime.utcnow() - timedelta(hours=2))

    r = client.post("/sales/appointments/%s/outcome" % a.id,
                    headers=_h(u, db_session),
                    json={"outcome": OUTCOME_COMPLETED, "notes": "Went well"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["outcome_state"]["occurred"] is True
    assert body["outcome_state"]["completed_at"]
    assert body["status"] == APPT_COMPLETED
    assert body["outcome_state"]["needs_outcome"] is False

    facts = apoutcome.completion_facts(db_session, brand.id)
    assert facts["occurred"] == 1
    assert facts["unrecorded"] == 0
    assert facts["completion_rate"] == 1.0


def test_a_no_show_is_not_occurred_and_releases_the_calendar(client, db_session, brand):
    """A meeting that did not happen must stop blocking time.

    Recording a no-show and leaving the participant rows blocking would keep a
    rep's week full of meetings nobody was in — and `completed_at` must stay
    NULL, or every duration and attendance report counts it.
    """
    u = _user(db_session, brand, ROLE_SALES_MANAGER)
    a = _appt(db_session, brand, [u], datetime.utcnow() - timedelta(hours=2))

    r = client.post("/sales/appointments/%s/outcome" % a.id,
                    headers=_h(u, db_session), json={"outcome": OUTCOME_NO_SHOW})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["outcome_state"]["occurred"] is False
    assert body["outcome_state"]["completed_at"] is None
    assert body["status"] == APPT_NO_SHOW

    parts = db_session.query(AppointmentParticipant).filter(
        AppointmentParticipant.appointment_id == a.id).all()
    assert all(p.is_blocking is False for p in parts), (
        "a meeting nobody attended must not keep blocking calendars")


def test_a_future_meeting_cannot_be_marked_completed(client, db_session, brand):
    """Refused, not merely discouraged.

    A rep marking tomorrow's demo complete is either a mis-click or a habit,
    and either way it re-ruins the dataset this whole feature exists to fix.
    """
    u = _user(db_session, brand, ROLE_SALES_MANAGER)
    a = _appt(db_session, brand, [u], datetime.utcnow() + timedelta(days=2))

    r = client.post("/sales/appointments/%s/outcome" % a.id,
                    headers=_h(u, db_session), json={"outcome": OUTCOME_COMPLETED})
    assert r.status_code == 400
    assert "has not happened yet" in r.json()["detail"]

    # Cancelling a future meeting IS legitimate.
    r2 = client.post("/sales/appointments/%s/outcome" % a.id,
                     headers=_h(u, db_session), json={"outcome": OUTCOME_CANCELLED})
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == APPT_CANCELLED


def test_deal_only_outcomes_are_refused_without_a_deal(client, db_session, brand):
    u = _user(db_session, brand, ROLE_SALES_MANAGER)
    a = _appt(db_session, brand, [u], datetime.utcnow() - timedelta(hours=2))

    opts = client.get("/sales/appointments/%s/outcome-options" % a.id,
                      headers=_h(u, db_session)).json()
    won = [o for o in opts["options"] if o["value"] == OUTCOME_WON][0]
    assert won["available"] is False
    # The reason is RETURNED rather than the option hidden — see the dialog's note.
    assert "not attached to a deal" in won["unavailable_reason"]

    r = client.post("/sales/appointments/%s/outcome" % a.id,
                    headers=_h(u, db_session), json={"outcome": OUTCOME_WON})
    assert r.status_code == 400


def test_the_stage_moves_only_when_explicitly_asked(client, db_session, brand):
    """A stage that moves as a side effect makes every Won in the system suspect."""
    u = _user(db_session, brand, ROLE_SALES_MANAGER)
    opp = Opportunity(brand_sales_org_id=brand.id, owner_user_id=u.id,
                      company_name="Countryside Land Partners",
                      stage=STAGE_DISCOVERY, status="open")
    db_session.add(opp)
    db_session.commit()
    a = _appt(db_session, brand, [u], datetime.utcnow() - timedelta(hours=2),
              opportunity=opp)

    # Not asked for: the outcome lands, the stage does not move.
    r = client.post("/sales/appointments/%s/outcome" % a.id,
                    headers=_h(u, db_session),
                    json={"outcome": OUTCOME_PROPOSAL_NEEDED})
    assert r.status_code == 200, r.text
    db_session.refresh(opp)
    assert opp.stage == STAGE_DISCOVERY
    assert r.json()["outcome_report"]["stage_moved_to"] is None

    # Asked for: it moves, and the deal's next action is set.
    r2 = client.post("/sales/appointments/%s/outcome" % a.id,
                     headers=_h(u, db_session),
                     json={"outcome": OUTCOME_PROPOSAL_NEEDED,
                           "advance_stage": True,
                           "next_action": "Build and send proposal"})
    assert r2.status_code == 200, r2.text
    db_session.refresh(opp)
    assert opp.stage == STAGE_PROPOSAL
    assert opp.next_action == "Build and send proposal"


def test_the_pending_outcome_queue_is_the_visible_gap(client, db_session, brand):
    u = _user(db_session, brand, ROLE_SALES_MANAGER)
    _appt(db_session, brand, [u], datetime.utcnow() - timedelta(hours=3))
    _appt(db_session, brand, [u], datetime.utcnow() + timedelta(days=1))

    r = client.get("/sales/appointments/pending-outcome?scope=team",
                   headers=_h(u, db_session))
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1, (
        "only PAST meetings with no verdict belong in the queue")


def test_attendance_absent_means_unknown_not_everybody_attended(client, db_session,
                                                                brand):
    a_user = _user(db_session, brand, ROLE_SALES_MANAGER)
    b_user = _user(db_session, brand, name="Second")
    a = _appt(db_session, brand, [a_user, b_user],
              datetime.utcnow() - timedelta(hours=2))

    client.post("/sales/appointments/%s/outcome" % a.id,
                headers=_h(a_user, db_session),
                json={"outcome": OUTCOME_COMPLETED})

    parts = db_session.query(AppointmentParticipant).filter(
        AppointmentParticipant.appointment_id == a.id).all()
    assert all(p.attendance_status == "unknown" for p in parts), (
        "the meeting happened; who was in it was not stated and must not be "
        "invented")


def test_a_hostile_participant_id_in_attendance_is_ignored(client, db_session, brand):
    """A body naming somebody else's participant row must not reach it."""
    u = _user(db_session, brand, ROLE_SALES_MANAGER)
    other = _user(db_session, brand, name="Other")
    a = _appt(db_session, brand, [u], datetime.utcnow() - timedelta(hours=2))
    b = _appt(db_session, brand, [other], datetime.utcnow() - timedelta(hours=2))

    r = client.post("/sales/appointments/%s/outcome" % a.id,
                    headers=_h(u, db_session),
                    json={"outcome": OUTCOME_COMPLETED,
                          "attendance": {other.id: "no_show"}})
    assert r.status_code == 200, r.text
    part = db_session.query(AppointmentParticipant).filter(
        AppointmentParticipant.appointment_id == b.id).first()
    assert part.attendance_status == "unknown", (
        "an unknown user id must be ignored, never written through")


# ═══════════════════════════════════════════════════════════════════════
# RECONCILIATION — external edits
# ═══════════════════════════════════════════════════════════════════════

def _synced_appt(db, brand, user, fake, starts_at=None):
    """An appointment that has actually been pushed to the fake provider, so
    `pushed_*` carries the baseline drift detection compares against."""
    from app.services import appointment_sync as apsync
    starts_at = starts_at or (datetime.utcnow() + timedelta(days=3))
    a = _appt(db, brand, [user], starts_at)
    apsync.sync_appointment(db, a, organizer=user)
    db.refresh(a)
    part = db.query(AppointmentParticipant).filter(
        AppointmentParticipant.appointment_id == a.id).first()
    assert part.external_event_id, "the fixture must have actually synced"
    assert part.pushed_starts_at is not None, "the drift baseline must be recorded"
    return a, part


def test_a_provider_deletion_heals_and_is_recorded(db_session, brand, fake_provider):
    """Deterministic: nothing is lost by recreating a deleted copy.

    EvoSys Pro still holds the appointment, and a deleted event carries no
    information an overwrite would destroy.
    """
    u = _user(db_session, brand, ROLE_SALES_MANAGER)
    _profile(db_session, u, lunch=None)
    _connect(db_session, u)
    a, part = _synced_appt(db_session, brand, u, FakeProvider)

    FakeProvider.events.clear()          # somebody deleted it in Outlook
    report = apreconcile.reconcile_appointment(db_session, a, organizer=u)

    assert report["healed"] == 1, report
    assert report["conflicts"] == 0
    db_session.refresh(part)
    assert part.sync_conflict is False
    assert part.external_event_id, "a fresh event must exist after healing"
    assert db_session.query(AppointmentSyncLog).filter(
        AppointmentSyncLog.appointment_id == a.id,
        AppointmentSyncLog.action == "reconcile_heal").count() == 1, (
        "a heal must leave a record; silent self-repair is indistinguishable "
        "from nothing having gone wrong")


def test_a_provider_move_raises_a_conflict_and_overwrites_nothing(
        db_session, brand, fake_provider):
    """NOT deterministic, so NOT healed.

    The likeliest reason somebody moved a meeting in Outlook is that they
    agreed the new time with the prospect. Silently restoring ours would put
    the rep back in a meeting the customer has already left.
    """
    u = _user(db_session, brand, ROLE_SALES_MANAGER)
    _profile(db_session, u, lunch=None)
    _connect(db_session, u)
    a, part = _synced_appt(db_session, brand, u, FakeProvider)

    eid = part.external_event_id
    moved = FakeProvider.events[eid]
    FakeProvider.events[eid] = ExternalEventState(
        exists=True,
        starts_at=moved.starts_at + timedelta(hours=2),
        ends_at=moved.ends_at + timedelta(hours=2),
        etag="e9", claimed_appointment_id=a.id)

    before_updates = len(FakeProvider.updated)
    report = apreconcile.reconcile_appointment(db_session, a, organizer=u)

    assert report["conflicts"] == 1, report
    assert report["healed"] == 0
    assert len(FakeProvider.updated) == before_updates, (
        "a move must not be overwritten — that destroys a real decision")
    db_session.refresh(part)
    assert part.sync_conflict is True
    assert part.sync_conflict_kind == CONFLICT_MOVED
    assert part.conflict_provider_starts_at == moved.starts_at + timedelta(hours=2)
    # BOTH sides are available to the review screen.
    out = apreconcile.conflict_out(a, part, u)
    assert out["evosys"]["pushed_starts_at"]
    assert out["provider_says"]["starts_at"]
    assert any(act["action"] == "reschedule" for act in out["actions"])


def test_an_event_that_belongs_to_another_appointment_is_never_touched(
        db_session, brand, fake_provider):
    """A stored event id is not proof of ownership.

    Acting on an id we cannot prove is a cross-tenant calendar write waiting to
    happen, which is a far worse failure than a stale event.
    """
    u = _user(db_session, brand, ROLE_SALES_MANAGER)
    _profile(db_session, u, lunch=None)
    _connect(db_session, u)
    a, part = _synced_appt(db_session, brand, u, FakeProvider)

    eid = part.external_event_id
    st = FakeProvider.events[eid]
    FakeProvider.events[eid] = ExternalEventState(
        exists=True, starts_at=st.starts_at, ends_at=st.ends_at, etag="e3",
        claimed_appointment_id="somebody-elses-appointment")

    before = len(FakeProvider.updated) + len(FakeProvider.cancelled)
    report = apreconcile.reconcile_appointment(db_session, a, organizer=u)

    assert report["conflicts"] == 1
    db_session.refresh(part)
    assert part.sync_conflict_kind == CONFLICT_ORPHANED
    assert len(FakeProvider.updated) + len(FakeProvider.cancelled) == before, (
        "an unprovable event must never be written to")


def test_an_unreachable_provider_claims_nothing(db_session, brand, fake_provider):
    """An outage is not an edit.

    A reconciler that reported a conflict every time Microsoft had a bad minute
    would train everyone to ignore conflicts, which is worse than having none.
    """
    u = _user(db_session, brand, ROLE_SALES_MANAGER)
    _profile(db_session, u, lunch=None)
    _connect(db_session, u)
    a, part = _synced_appt(db_session, brand, u, FakeProvider)

    FakeProvider.fail_get_event = SyncResult.failure("http_500", "boom")
    report = apreconcile.reconcile_appointment(db_session, a, organizer=u)

    assert report["unknown"] == 1, report
    assert report["conflicts"] == 0
    assert report["healed"] == 0
    db_session.refresh(part)
    assert part.sync_conflict is False


def test_an_unchanged_event_is_silent(db_session, brand, fake_provider):
    """The overwhelmingly common case must be cheap and produce no noise."""
    u = _user(db_session, brand, ROLE_SALES_MANAGER)
    _profile(db_session, u, lunch=None)
    _connect(db_session, u)
    a, part = _synced_appt(db_session, brand, u, FakeProvider)

    report = apreconcile.reconcile_appointment(db_session, a, organizer=u)
    assert report["in_sync"] == 1
    assert report["conflicts"] == 0
    assert report["healed"] == 0


def test_a_reschedule_is_not_mistaken_for_an_external_edit(
        client, db_session, brand, fake_provider):
    """THE REASON DRIFT IS MEASURED AGAINST `pushed_*` AND NOT THE APPOINTMENT.

    Compared against the appointment's live time, our own reschedule looks
    exactly like somebody editing Outlook, and the reconciler would raise a
    conflict against its own work.
    """
    u = _user(db_session, brand, ROLE_SALES_MANAGER)
    _profile(db_session, u, lunch=None, notice=0, start=0, end=24 * 60 - 1,
             days=range(7))
    _connect(db_session, u)
    day = _next_weekday()
    a, part = _synced_appt(db_session, brand, u, FakeProvider,
                           starts_at=av.local_to_utc(day, 10 * 60, "America/Chicago"))

    r = client.post("/sales/appointments/%s/reschedule" % a.id,
                    headers=_h(u, db_session),
                    json={"starts_at": av.local_to_utc(
                        day, 14 * 60, "America/Chicago").isoformat()})
    assert r.status_code == 200, r.text

    db_session.refresh(a)
    report = apreconcile.reconcile_appointment(db_session, a, organizer=u)
    assert report["conflicts"] == 0, (
        "our own reschedule must not read as an outside edit")
    assert report["in_sync"] == 1


def test_resolving_a_conflict_is_manager_only(client, db_session, brand,
                                              fake_provider):
    rep = _user(db_session, brand, ROLE_SALES_REP)
    _profile(db_session, rep, lunch=None)
    _connect(db_session, rep)
    a, part = _synced_appt(db_session, brand, rep, FakeProvider)

    part.sync_conflict = True
    part.sync_conflict_kind = CONFLICT_MOVED
    db_session.commit()

    r = client.post("/sales/appointments/%s/resolve-conflict" % a.id,
                    headers=_h(rep, db_session),
                    json={"user_id": rep.id, "action": "push_evosys"})
    assert r.status_code == 403


def test_unlink_forgets_our_id_without_touching_the_provider(db_session, brand,
                                                             fake_provider):
    """The only safe answer to an orphaned id.

    The event on the other end may belong to somebody else entirely, and the
    one thing we must not do is modify it to tidy up our own bookkeeping.
    """
    mgr = _user(db_session, brand, ROLE_SALES_MANAGER)
    _profile(db_session, mgr, lunch=None)
    _connect(db_session, mgr)
    a, part = _synced_appt(db_session, brand, mgr, FakeProvider)
    part.sync_conflict = True
    part.sync_conflict_kind = CONFLICT_ORPHANED
    db_session.commit()

    before = len(FakeProvider.updated) + len(FakeProvider.cancelled)
    result = apreconcile.resolve_conflict(db_session, a, part, mgr, mgr, "unlink")
    db_session.commit()

    assert result["ok"] is True
    db_session.refresh(part)
    assert part.external_event_id is None
    assert part.sync_conflict is False
    assert len(FakeProvider.updated) + len(FakeProvider.cancelled) == before


# ═══════════════════════════════════════════════════════════════════════
# SYNC — one appointment, provider event ids, no duplicates
# ═══════════════════════════════════════════════════════════════════════

def test_one_appointment_one_provider_event_and_no_duplicates(
        client, db_session, brand, fake_provider):
    """EvoSys Pro owns the appointment; the provider event is a copy.

    Booking, then rescheduling, must UPDATE the same event — not create a
    second one. A reschedule that creates a duplicate leaves the old meeting on
    everyone's calendar, which is how somebody dials into a call that moved.
    """
    mgr = _user(db_session, brand, ROLE_SALES_MANAGER)
    _profile(db_session, mgr, lunch=None, notice=0, start=0, end=24 * 60 - 1,
             days=range(7))
    _connect(db_session, mgr)
    day = _next_weekday()

    r = client.post("/sales/appointments", headers=_h(mgr, db_session),
                    json={"starts_at": av.local_to_utc(
                        day, 10 * 60, "America/Chicago").isoformat(),
                        "duration_minutes": 30,
                        "required_user_ids": [mgr.id]})
    assert r.status_code == 201, r.text
    appt_id = r.json()["id"]
    assert len(FakeProvider.created) == 1
    first_event = FakeProvider.created[0][0]

    # ONE appointment row, whatever the provider did.
    assert db_session.query(SalesAppointment).count() == 1

    r2 = client.post("/sales/appointments/%s/reschedule" % appt_id,
                     headers=_h(mgr, db_session),
                     json={"starts_at": av.local_to_utc(
                         day, 14 * 60, "America/Chicago").isoformat()})
    assert r2.status_code == 200, r2.text

    assert len(FakeProvider.created) == 1, "a reschedule must not create a second event"
    assert FakeProvider.updated, "it must update the existing one"
    assert FakeProvider.updated[-1][0] == first_event
    assert db_session.query(SalesAppointment).count() == 1


def test_cancelling_withdraws_the_same_provider_event(client, db_session, brand,
                                                      fake_provider):
    mgr = _user(db_session, brand, ROLE_SALES_MANAGER)
    _profile(db_session, mgr, lunch=None, notice=0)
    _connect(db_session, mgr)
    day = _next_weekday()

    r = client.post("/sales/appointments", headers=_h(mgr, db_session),
                    json={"starts_at": av.local_to_utc(
                        day, 10 * 60, "America/Chicago").isoformat(),
                        "duration_minutes": 30,
                        "required_user_ids": [mgr.id]})
    appt_id = r.json()["id"]
    eid = FakeProvider.created[0][0]

    r2 = client.post("/sales/appointments/%s/cancel" % appt_id,
                     headers=_h(mgr, db_session), json={"reason": "Prospect moved"})
    assert r2.status_code == 200, r2.text
    assert eid in FakeProvider.cancelled, (
        "a cancellation that does not reach the calendar is the worst of the "
        "three outcomes")


def test_a_disconnected_participant_is_a_success_not_a_failure(
        client, db_session, brand, fake_provider):
    """Someone who never connected a calendar has done nothing wrong.

    The booking is a FULL success and they are not counted as needing
    attention. Dressing that state up as an error pushes people to fix
    something that is not broken.

    The .ics fallback is made deliberately unready here so the participant
    lands on `not_connected` rather than on whatever this environment's email
    sending happens to do. Whether an invitation email leaves the building is a
    different question with its own tests; this one is about the STATE a
    calendar-less participant is reported in.
    """
    mgr = _user(db_session, brand, ROLE_SALES_MANAGER)
    other = _user(db_session, brand, name="No Calendar")
    _profile(db_session, mgr, lunch=None, notice=0)
    _profile(db_session, other, lunch=None, notice=0)
    _connect(db_session, mgr)
    day = _next_weekday()

    class NoIcs(CalendarProvider):
        key = "ics"

        def is_ready(self):
            return False, "No email delivery configured"

    reg.register_provider("ics", lambda u, c, o: NoIcs(u, c, o))

    r = client.post("/sales/appointments", headers=_h(mgr, db_session),
                    json={"starts_at": av.local_to_utc(
                        day, 10 * 60, "America/Chicago").isoformat(),
                        "duration_minutes": 30,
                        "required_user_ids": [mgr.id, other.id]})
    assert r.status_code == 201, r.text
    body = r.json()
    states = {p["user_id"]: p["sync_status"] for p in body["participants"]}
    assert states[mgr.id] == SYNC_SYNCED
    assert states[other.id] == "not_connected"
    # `needs_attention` counts problems a human must fix — not this one.
    assert body["sync_needs_attention"] == 0
    # And the meeting still blocks their time, which is the part that matters.
    parts = db_session.query(AppointmentParticipant).filter(
        AppointmentParticipant.appointment_id == body["id"]).all()
    assert len(parts) == 2
    assert all(p.is_blocking for p in parts)


# ═══════════════════════════════════════════════════════════════════════
# PRIVACY, TENANCY AND PERMISSIONS
# ═══════════════════════════════════════════════════════════════════════

def test_a_rep_from_another_brand_cannot_read_this_calendar(client, db_session,
                                                            brand, brand_b):
    a_user = _user(db_session, brand, ROLE_SALES_MANAGER)
    b_user = _user(db_session, brand_b, ROLE_SALES_MANAGER)
    appt = _appt(db_session, brand, [a_user],
                 datetime.utcnow() + timedelta(days=1))

    r = client.get("/sales/appointments/%s" % appt.id, headers=_h(b_user, db_session))
    # 404, not 403 — confirming the id exists is itself a leak.
    assert r.status_code == 404

    view = client.get("/sales/calendar/view", headers=_h(b_user, db_session))
    assert view.status_code == 200
    assert view.json()["appointments"] == [], "brand B must see none of brand A's"


def test_another_reps_meeting_reads_as_busy_with_no_title(client, db_session, brand):
    """A rep needs to see that a colleague is occupied. They do not get to read
    the colleague's agenda — and the server sends the literal string, so the
    rule cannot be undone in the browser."""
    rep = _user(db_session, brand, ROLE_SALES_REP, name="Viewer")
    other = _user(db_session, brand, ROLE_SALES_REP, name="Colleague")
    _profile(db_session, rep, lunch=None)
    _profile(db_session, other, lunch=None)
    day = _next_weekday()
    _appt(db_session, brand, [other],
          av.local_to_utc(day, 10 * 60, "America/Chicago"),
          title="Discovery · Confidential Corp")

    r = client.get("/sales/availability/team?day=%s" % day.isoformat(),
                   headers=_h(rep, db_session))
    assert r.status_code == 200, r.text
    col = [m for m in r.json()["members"] if m["user_id"] == other.id][0]
    assert col["busy"], "the block must be visible"
    for b in col["busy"]:
        assert b["title"] == "Busy"
        assert b["appointment_id"] is None
        assert b["kind"] == "blocked", (
            "even the meeting's TYPE is more than this viewer is entitled to")
        assert "Confidential" not in str(b)


def test_external_busy_carries_no_title_anywhere(client, db_session, brand):
    """Nothing to leak, because nothing was stored.

    The cache has no subject column at all — this asserts the payload reflects
    that rather than reconstructing something from elsewhere.
    """
    mgr = _user(db_session, brand, ROLE_SALES_MANAGER)
    _profile(db_session, mgr, lunch=None)
    day = _next_weekday()
    db_session.add(ExternalBusyBlock(
        user_id=mgr.id, provider=PROVIDER_MICROSOFT,
        starts_at=av.local_to_utc(day, 10 * 60, "America/Chicago"),
        ends_at=av.local_to_utc(day, 11 * 60, "America/Chicago"),
        is_private=True, fetched_at=datetime.utcnow()))
    db_session.commit()

    r = client.get("/sales/availability/team?day=%s" % day.isoformat(),
                   headers=_h(mgr, db_session))
    col = [m for m in r.json()["members"] if m["user_id"] == mgr.id][0]
    assert col["external_busy"], "the interval must be drawn"
    for x in col["external_busy"]:
        assert x["label"] == "Busy — external calendar"
        assert set(x.keys()) == {"starts_at", "ends_at", "label"}, (
            "the external band must carry the interval and nothing else")


def test_no_payload_ever_contains_a_provider_token(client, db_session, brand,
                                                   fake_provider):
    mgr = _user(db_session, brand, ROLE_SALES_MANAGER)
    _profile(db_session, mgr, lunch=None, notice=0)
    _connect(db_session, mgr)
    day = _next_weekday()
    client.post("/sales/appointments", headers=_h(mgr, db_session),
                json={"starts_at": av.local_to_utc(
                    day, 10 * 60, "America/Chicago").isoformat(),
                    "duration_minutes": 30, "required_user_ids": [mgr.id]})

    for path in ("/sales/calendar/view", "/sales/calendar/sync-status",
                 "/sales/availability/team", "/sales/calendar/conflicts"):
        body = client.get(path, headers=_h(mgr, db_session)).text
        assert "fake-token" not in body, "%s leaked a refresh token" % path
        assert "refresh_token" not in body


def test_a_rep_cannot_book_a_stranger_into_a_meeting(client, db_session, brand,
                                                     brand_b):
    """Without the same-brand check, a rep could name any user id in the body
    and pull a stranger onto their calendar."""
    rep = _user(db_session, brand, ROLE_SALES_REP)
    stranger = _user(db_session, brand_b, ROLE_SALES_REP)
    _profile(db_session, rep, lunch=None, notice=0)
    day = _next_weekday()

    r = client.post("/sales/appointments", headers=_h(rep, db_session),
                    json={"starts_at": av.local_to_utc(
                        day, 10 * 60, "America/Chicago").isoformat(),
                        "duration_minutes": 30,
                        "required_user_ids": [rep.id, stranger.id]})
    assert r.status_code == 400
    assert "not an active member" in r.json()["detail"]


def test_a_removed_member_stops_appearing(client, db_session, brand):
    """Stale membership. A person whose membership is deactivated must drop out
    of the roster and out of the availability grid immediately."""
    mgr = _user(db_session, brand, ROLE_SALES_MANAGER)
    leaver = _user(db_session, brand, name="Leaver")
    r1 = client.get("/sales/calendar/view", headers=_h(mgr, db_session))
    assert leaver.id in {p["user_id"] for p in r1.json()["people"]}

    db_session.query(Membership).filter(
        Membership.user_id == leaver.id).update({"is_active": False},
                                                synchronize_session=False)
    db_session.commit()

    r2 = client.get("/sales/calendar/view", headers=_h(mgr, db_session))
    assert leaver.id not in {p["user_id"] for p in r2.json()["people"]}


def test_completion_facts_never_cross_a_brand(db_session, brand, brand_b):
    a_user = _user(db_session, brand, ROLE_SALES_MANAGER)
    b_user = _user(db_session, brand_b, ROLE_SALES_MANAGER)
    _appt(db_session, brand, [a_user], datetime.utcnow() - timedelta(hours=2))
    _appt(db_session, brand_b, [b_user], datetime.utcnow() - timedelta(hours=2))

    facts = apoutcome.completion_facts(db_session, brand.id)
    assert facts["total"] == 1, "an aggregate that crosses brands is a data leak"


def test_the_conflict_queue_never_crosses_a_brand(db_session, brand, brand_b,
                                                  fake_provider):
    a_user = _user(db_session, brand, ROLE_SALES_MANAGER)
    b_user = _user(db_session, brand_b, ROLE_SALES_MANAGER)
    for u, b in ((a_user, brand), (b_user, brand_b)):
        _profile(db_session, u, lunch=None)
        appt = _appt(db_session, b, [u], datetime.utcnow() + timedelta(days=2))
        part = db_session.query(AppointmentParticipant).filter(
            AppointmentParticipant.appointment_id == appt.id).first()
        part.sync_conflict = True
        part.sync_conflict_kind = CONFLICT_MOVED
        part.sync_conflict_at = datetime.utcnow()
    db_session.commit()

    rows = apreconcile.conflict_rows(db_session, brand.id)
    assert len(rows) == 1
    assert rows[0][0].brand_sales_org_id == brand.id


# ═══════════════════════════════════════════════════════════════════════
# THE MOBILE CONTRACT
# ═══════════════════════════════════════════════════════════════════════

def test_the_field_view_has_everything_it_needs_from_one_payload(
        client, db_session, brand):
    """NO SECOND CALENDAR ENGINE. The phone renders the SAME payload.

    So every field the field view needs — the time, the customer, the
    participants, the confirmation state, the meeting link, the location and
    the phone numbers its quick actions use — has to be present in the ordinary
    calendar response, or somebody will be tempted to add a mobile endpoint.
    """
    mgr = _user(db_session, brand, ROLE_SALES_MANAGER)
    _profile(db_session, mgr, lunch=None, notice=0)
    day = _next_weekday()
    a = _appt(db_session, brand, [mgr],
              av.local_to_utc(day, 10 * 60, "America/Chicago"))
    a.prospect_name = "Samuel St.Germain"
    a.prospect_phone = "+12145550123"
    a.prospect_email = "sam@example.com"
    a.location = "1234 Main St, Dallas TX"
    a.meeting_provider = "in_person"
    db_session.commit()

    r = client.get("/sales/calendar/view?date_from=%s&date_to=%s"
                   % (day.isoformat(), day.isoformat()),
                   headers=_h(mgr, db_session))
    assert r.status_code == 200, r.text
    got = r.json()["appointments"][0]

    for key in ("starts_at_local", "ends_at_local", "duration_minutes",
                "meeting_type", "title", "location", "meeting_provider",
                "confirmation_status", "participants", "video",
                "outcome_state", "opportunity_company"):
        assert key in got, "the field view needs '%s'" % key
    assert got["prospect"]["phone"] == "+12145550123"
    assert got["prospect"]["email"] == "sam@example.com"


def test_the_calendar_view_and_the_availability_grid_stay_two_screens(
        client, db_session, brand):
    """A small guard on a scope decision that was explicit in the brief.

    The two endpoints answer different questions and must not converge: the
    calendar carries booked appointments, the availability grid carries
    per-person free time. If somebody later merges them, this fails.
    """
    mgr = _user(db_session, brand, ROLE_SALES_MANAGER)
    _profile(db_session, mgr, lunch=None)

    cal = client.get("/sales/calendar/view", headers=_h(mgr, db_session)).json()
    avail = client.get("/sales/availability/team", headers=_h(mgr, db_session)).json()

    assert "appointments" in cal and "agenda_today" in cal
    assert "free" in avail["members"][0]
    assert "appointments" not in avail, (
        "the availability grid must not become a second calendar")
    assert "agenda_today" not in avail
