"""
Calendar + Sales Workspace scheduling — the adversarial pass.

Everything here is an attempt to BREAK the thing rather than to confirm it
works. The behavioural suite is next door in test_calendar_scheduling.py; this
file is the list from the brief that a happy-path suite would never reach:

  · a suspended and a removed user, still holding meetings
  · a replayed prospect confirmation (the double webhook)
  · a revoked and an expired confirmation token
  · rescheduling a cancelled meeting, and cancelling twice
  · recording an outcome twice, and after a cancellation
  · an all-day external event
  · a provider that disagrees with us about the timezone
  · a rep reaching a manager-only route by guessing a URL
  · an appointment id from another brand in every path that takes one
  · realistic scale

A test that passes here is worth more than one that passes next door, because
the interesting failures in a scheduling system are all at the edges.
"""
import itertools
import time
from datetime import datetime, timedelta, date

import pytest

from app.models.models import Platform, User
from app.models.sales_models import (
    BrandSalesOrg, Membership, Opportunity,
    SCOPE_BRAND_SALES_ORG, ROLE_SALES_MANAGER, ROLE_SALES_REP,
)
from app.models.scheduling_models import (
    AvailabilityProfile, AvailabilityWindow, AvailabilityBlock,
    SalesAppointment, AppointmentParticipant,
    APPT_SCHEDULED, APPT_CANCELLED, APPT_COMPLETED, APPT_NO_SHOW,
    BLOCK_RECURRING,
    CONF_PENDING, CONF_CONFIRMED, CONF_DECLINED,
    OUTCOME_COMPLETED, OUTCOME_NO_SHOW, OUTCOME_CANCELLED, OUTCOME_FOLLOW_UP,
)
from app.models.calendar_models import (
    AppointmentConfirmationToken, CalendarConnection, ExternalBusyBlock,
    PROVIDER_MICROSOFT,
)
from app.services import availability as av
from app.services import appointment_outcome as apoutcome
from app.services.auth_service import hash_password, create_access_token

_SEQ = itertools.count(1)


# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture()
def brand(db_session):
    p = Platform(name="EvoSys Pro", slug="atk-plat-%d" % next(_SEQ))
    db_session.add(p)
    db_session.commit()
    b = BrandSalesOrg(platform_id=p.id, name="EvoSys Sales",
                      slug="atk-brand-%d" % next(_SEQ),
                      timezone="America/Chicago")
    db_session.add(b)
    db_session.commit()
    return b


@pytest.fixture()
def brand_b(db_session, brand):
    b = BrandSalesOrg(platform_id=brand.platform_id, name="Other Sales",
                      slug="atk-brandb-%d" % next(_SEQ),
                      timezone="America/Chicago")
    db_session.add(b)
    db_session.commit()
    return b


def _user(db, brand=None, role=ROLE_SALES_REP, name="Rep"):
    u = User(organization_id=None, email="atk%d@test.live" % next(_SEQ),
             password_hash=hash_password("x"), full_name=name,
             role="advisor", must_change_password=False)
    db.add(u)
    db.commit()
    if brand is not None:
        db.add(Membership(user_id=u.id, scope_type=SCOPE_BRAND_SALES_ORG,
                          scope_id=brand.id, role=role, is_active=True))
        db.commit()
    return u


def _h(u, db):
    return {"Authorization": "Bearer " + create_access_token(u, db)}


def _profile(db, user, notice=0, days=range(7)):
    prof = av.get_or_create_profile(db, user, default_timezone="America/Chicago")
    prof.timezone = "America/Chicago"
    prof.min_notice_minutes = notice
    prof.booking_horizon_days = 365
    db.query(AvailabilityWindow).filter(
        AvailabilityWindow.profile_id == prof.id).delete(synchronize_session=False)
    db.query(AvailabilityBlock).filter(
        AvailabilityBlock.profile_id == prof.id).delete(synchronize_session=False)
    for dow in days:
        db.add(AvailabilityWindow(profile_id=prof.id, day_of_week=dow,
                                  start_minute=0, end_minute=24 * 60 - 1))
    db.commit()
    return prof


def _appt(db, brand, users, starts_at=None, duration=30,
          status=APPT_SCHEDULED, prospect_email=None):
    starts_at = starts_at or (datetime.utcnow() + timedelta(days=2))
    a = SalesAppointment(
        brand_sales_org_id=brand.id, title="Attack Meeting",
        starts_at=starts_at, ends_at=starts_at + timedelta(minutes=duration),
        timezone="America/Chicago", status=status,
        confirmation_status=CONF_PENDING,
        prospect_name="Sam", prospect_email=prospect_email)
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


def _token(db, appt, **kw):
    import secrets
    t = AppointmentConfirmationToken(
        appointment_id=appt.id, token=secrets.token_urlsafe(32),
        recipient_email=appt.prospect_email or "sam@example.com",
        recipient_name="Sam", **kw)
    db.add(t)
    db.commit()
    return t


# ═══════════════════════════════════════════════════════════════════════
# SUSPENDED AND REMOVED USERS
# ═══════════════════════════════════════════════════════════════════════

def test_a_suspended_user_leaves_the_roster_and_the_grid(client, db_session, brand):
    """A suspended account must not be offered as bookable.

    Booking a meeting onto somebody who has been locked out produces a meeting
    nobody attends and a calendar event nobody can see.
    """
    mgr = _user(db_session, brand, ROLE_SALES_MANAGER)
    victim = _user(db_session, brand, name="Suspended")
    _profile(db_session, mgr)
    _profile(db_session, victim)

    before = client.get("/sales/availability/team", headers=_h(mgr, db_session))
    assert victim.id in {m["user_id"] for m in before.json()["members"]}

    victim.is_active = False
    db_session.commit()

    after = client.get("/sales/availability/team", headers=_h(mgr, db_session))
    assert victim.id not in {m["user_id"] for m in after.json()["members"]}

    cal = client.get("/sales/calendar/view", headers=_h(mgr, db_session))
    assert victim.id not in {p["user_id"] for p in cal.json()["people"]}

    # And they cannot be booked.
    r = client.post("/sales/appointments", headers=_h(mgr, db_session),
                    json={"starts_at": (datetime.utcnow()
                                        + timedelta(days=2)).isoformat(),
                          "duration_minutes": 30,
                          "required_user_ids": [mgr.id, victim.id]})
    assert r.status_code == 400
    assert "not an active member" in r.json()["detail"]


def test_a_removed_users_existing_meeting_does_not_break_the_calendar(
        client, db_session, brand):
    """A participant whose user row is gone must not take a screen down.

    The appointment is still real and the rest of the room still needs to see
    it, so the missing person is skipped rather than crashed on.
    """
    mgr = _user(db_session, brand, ROLE_SALES_MANAGER)
    leaver = _user(db_session, brand, name="Leaver")
    _profile(db_session, mgr)
    _profile(db_session, leaver)
    a = _appt(db_session, brand, [mgr, leaver])

    # The membership is gone AND the user row with it — the hardest version.
    db_session.query(Membership).filter(
        Membership.user_id == leaver.id).delete(synchronize_session=False)
    db_session.query(User).filter(User.id == leaver.id).delete(
        synchronize_session=False)
    db_session.commit()

    r = client.get("/sales/calendar/view", headers=_h(mgr, db_session))
    assert r.status_code == 200, r.text
    assert a.id in {x["id"] for x in r.json()["appointments"]}

    detail = client.get("/sales/appointments/%s" % a.id, headers=_h(mgr, db_session))
    assert detail.status_code == 200, detail.text

    avail = client.get("/sales/availability/team", headers=_h(mgr, db_session))
    assert avail.status_code == 200, avail.text


# ═══════════════════════════════════════════════════════════════════════
# THE DOUBLE WEBHOOK — a replayed prospect confirmation
# ═══════════════════════════════════════════════════════════════════════

def test_a_replayed_confirmation_does_not_double_apply(client, db_session, brand):
    """A prospect double-clicks, or a scanner replays the POST.

    The second submission must be idempotent: the answer stands, and the FIRST
    redemption time is the one kept — that timestamp is the evidence of when
    they actually agreed, and letting a replay overwrite it would destroy the
    only record that matters in a dispute.
    """
    u = _user(db_session, brand, ROLE_SALES_MANAGER)
    a = _appt(db_session, brand, [u], prospect_email="sam@example.com")
    tok = _token(db_session, a)

    first = client.post("/sales/appointments/confirm/%s/respond" % tok.token,
                        json={"action": "confirm"})
    assert first.status_code == 200, first.text
    db_session.refresh(a)
    db_session.refresh(tok)
    assert a.confirmation_status == CONF_CONFIRMED
    first_redeemed = tok.first_redeemed_at
    assert first_redeemed is not None

    second = client.post("/sales/appointments/confirm/%s/respond" % tok.token,
                         json={"action": "confirm"})
    assert second.status_code == 200, second.text
    db_session.refresh(a)
    db_session.refresh(tok)
    assert a.confirmation_status == CONF_CONFIRMED, "the answer must still stand"
    assert tok.first_redeemed_at == first_redeemed, (
        "a replay must not overwrite when the prospect actually answered")
    assert (tok.use_count or 0) >= 2, "the replay itself should be counted"


def test_a_revoked_token_is_refused(client, db_session, brand):
    u = _user(db_session, brand, ROLE_SALES_MANAGER)
    a = _appt(db_session, brand, [u], prospect_email="sam@example.com")
    tok = _token(db_session, a, revoked_at=datetime.utcnow())

    r = client.post("/sales/appointments/confirm/%s/respond" % tok.token,
                    json={"action": "confirm"})
    body = r.json()
    assert body.get("ok") is False
    db_session.refresh(a)
    assert a.confirmation_status == CONF_PENDING, (
        "a revoked link must change nothing")


def test_an_expired_token_is_refused(client, db_session, brand):
    u = _user(db_session, brand, ROLE_SALES_MANAGER)
    a = _appt(db_session, brand, [u], prospect_email="sam@example.com")
    tok = _token(db_session, a,
                 expires_at=datetime.utcnow() - timedelta(days=1))

    r = client.post("/sales/appointments/confirm/%s/respond" % tok.token,
                    json={"action": "confirm"})
    assert r.json().get("ok") is False
    db_session.refresh(a)
    assert a.confirmation_status == CONF_PENDING


def test_a_garbage_token_is_refused_without_leaking_whether_it_existed(
        client, db_session, brand):
    r = client.post("/sales/appointments/confirm/%s/respond" % ("x" * 40),
                    json={"action": "confirm"})
    assert r.json().get("ok") is False
    # Nothing that hints at the shape of the id space or the store behind it.
    assert "traceback" not in r.text.lower()
    assert "sql" not in r.text.lower()


def test_the_confirm_get_is_still_side_effect_free(client, db_session, brand):
    """An email scanner fetching the link must not confirm the meeting.

    Worth re-asserting here because the whole point of the attack pass is the
    things that happen without a human involved.
    """
    u = _user(db_session, brand, ROLE_SALES_MANAGER)
    a = _appt(db_session, brand, [u], prospect_email="sam@example.com")
    tok = _token(db_session, a)

    for _ in range(3):
        assert client.get(
            "/sales/appointments/confirm/%s" % tok.token).status_code == 200
    db_session.refresh(a)
    assert a.confirmation_status == CONF_PENDING


# ═══════════════════════════════════════════════════════════════════════
# LIFECYCLE RACES
# ═══════════════════════════════════════════════════════════════════════

def test_a_cancelled_meeting_cannot_be_rescheduled(client, db_session, brand):
    """The reschedule/cancel race, from the losing side.

    Reviving a cancelled meeting by moving it would put a meeting back on
    calendars that were told it was off, and the prospect already has a
    cancellation notice.
    """
    u = _user(db_session, brand, ROLE_SALES_MANAGER)
    _profile(db_session, u)
    a = _appt(db_session, brand, [u])

    assert client.post("/sales/appointments/%s/cancel" % a.id,
                       headers=_h(u, db_session), json={}).status_code == 200

    r = client.post("/sales/appointments/%s/reschedule" % a.id,
                    headers=_h(u, db_session),
                    json={"starts_at": (datetime.utcnow()
                                        + timedelta(days=4)).isoformat()})
    assert r.status_code == 400
    assert "cancelled" in r.json()["detail"].lower()


def test_cancelling_twice_is_idempotent(client, db_session, brand):
    u = _user(db_session, brand, ROLE_SALES_MANAGER)
    _profile(db_session, u)
    a = _appt(db_session, brand, [u])

    first = client.post("/sales/appointments/%s/cancel" % a.id,
                        headers=_h(u, db_session), json={"reason": "one"})
    assert first.status_code == 200
    cancelled_at = first.json()["cancelled_at"]

    second = client.post("/sales/appointments/%s/cancel" % a.id,
                         headers=_h(u, db_session), json={"reason": "two"})
    assert second.status_code == 200
    assert second.json()["cancelled_at"] == cancelled_at, (
        "the second cancellation must not move the time it was cancelled")
    assert second.json()["cancel_reason"] == "one"


def test_rescheduling_to_the_same_time_is_a_no_op(client, db_session, brand):
    """No provider write, no SEQUENCE bump, no confirmation reset.

    A reschedule that lands on the same time would otherwise re-send every
    invitation and reset the prospect's confirmation for nothing — which looks
    to the prospect like the meeting moved twice.
    """
    u = _user(db_session, brand, ROLE_SALES_MANAGER)
    _profile(db_session, u)
    a = _appt(db_session, brand, [u])
    a.confirmation_status = CONF_CONFIRMED
    db_session.commit()

    r = client.post("/sales/appointments/%s/reschedule" % a.id,
                    headers=_h(u, db_session),
                    json={"starts_at": a.starts_at.isoformat(),
                          "duration_minutes": 30})
    assert r.status_code == 200
    assert r.json()["rescheduled_count"] == 0
    assert r.json()["confirmation_status"] == CONF_CONFIRMED


def test_a_reschedule_resets_the_prospect_confirmation(client, db_session, brand):
    """They agreed to a time that no longer exists.

    Carrying the confirmation forward would show the rep a "confirmed" meeting
    nobody has actually agreed to.
    """
    u = _user(db_session, brand, ROLE_SALES_MANAGER)
    _profile(db_session, u)
    a = _appt(db_session, brand, [u])
    a.confirmation_status = CONF_CONFIRMED
    a.confirmed_at = datetime.utcnow()
    db_session.commit()

    r = client.post("/sales/appointments/%s/reschedule" % a.id,
                    headers=_h(u, db_session),
                    json={"starts_at": (datetime.utcnow()
                                        + timedelta(days=5)).isoformat()})
    assert r.status_code == 200, r.text
    assert r.json()["confirmation_status"] == CONF_PENDING
    assert r.json()["confirmed_at"] is None


def test_recording_an_outcome_twice_replaces_rather_than_accumulates(
        client, db_session, brand):
    """A rep corrects a mis-click. The record must end up in ONE state.

    And the earlier `completed_at` must not survive a correction to a no-show,
    or the meeting is simultaneously completed and not attended.
    """
    u = _user(db_session, brand, ROLE_SALES_MANAGER)
    a = _appt(db_session, brand, [u],
              starts_at=datetime.utcnow() - timedelta(hours=3))

    r1 = client.post("/sales/appointments/%s/outcome" % a.id,
                     headers=_h(u, db_session), json={"outcome": OUTCOME_COMPLETED})
    assert r1.status_code == 200
    assert r1.json()["outcome_state"]["completed_at"]

    r2 = client.post("/sales/appointments/%s/outcome" % a.id,
                     headers=_h(u, db_session), json={"outcome": OUTCOME_NO_SHOW})
    assert r2.status_code == 200, r2.text
    st = r2.json()["outcome_state"]
    assert st["outcome"] == OUTCOME_NO_SHOW
    assert st["occurred"] is False
    assert st["completed_at"] is None, (
        "a correction to no-show must clear the completion stamp")
    assert r2.json()["status"] == APPT_NO_SHOW

    facts = apoutcome.completion_facts(db_session, brand.id)
    assert facts["occurred"] == 0
    assert facts["did_not_occur"] == 1
    assert facts["total"] == 1, "one appointment, one verdict — never two"


def test_an_unknown_outcome_value_is_refused(client, db_session, brand):
    u = _user(db_session, brand, ROLE_SALES_MANAGER)
    a = _appt(db_session, brand, [u],
              starts_at=datetime.utcnow() - timedelta(hours=2))
    r = client.post("/sales/appointments/%s/outcome" % a.id,
                    headers=_h(u, db_session),
                    json={"outcome": "definitely_happened_trust_me"})
    assert r.status_code == 400
    assert "Unknown outcome" in r.json()["detail"]


def test_an_unknown_attendance_value_is_refused(client, db_session, brand):
    u = _user(db_session, brand, ROLE_SALES_MANAGER)
    a = _appt(db_session, brand, [u],
              starts_at=datetime.utcnow() - timedelta(hours=2))
    r = client.post("/sales/appointments/%s/outcome" % a.id,
                    headers=_h(u, db_session),
                    json={"outcome": OUTCOME_COMPLETED,
                          "attendance": {u.id: "probably"}})
    assert r.status_code == 400
    db_session.refresh(a)
    assert a.outcome is None, (
        "a refused outcome must write nothing at all, not the valid half")


# ═══════════════════════════════════════════════════════════════════════
# TIME — all-day events and a provider that disagrees
# ═══════════════════════════════════════════════════════════════════════

def test_an_all_day_external_event_blocks_the_whole_day(db_session, brand):
    """An all-day event is a real commitment.

    Providers return these as a date range with no time, and the cache stores
    the resolved interval. Treating it as a zero-length event would leave the
    whole day bookable while the person is at a conference.
    """
    u = _user(db_session, brand)
    _profile(db_session, u)
    day = (datetime.utcnow() + timedelta(days=7)).date()

    db_session.add(ExternalBusyBlock(
        user_id=u.id, provider=PROVIDER_MICROSOFT,
        starts_at=av.local_to_utc(day, 0, "America/Chicago"),
        ends_at=av.local_to_utc(day + timedelta(days=1), 0, "America/Chicago"),
        is_all_day=True, fetched_at=datetime.utcnow()))
    db_session.commit()

    start = av.local_to_utc(day, 0, "America/Chicago")
    end = av.local_to_utc(day + timedelta(days=1), 0, "America/Chicago")
    free = av.free_intervals_for_user(db_session, u, start, end,
                                      now_utc=start - timedelta(days=1))
    assert free == [], "an all-day event must leave no bookable time that day"


def test_an_appointment_keeps_the_timezone_it_was_agreed_in(client, db_session,
                                                            brand):
    """The provider does not get to decide what the meeting time means.

    `timezone` on the appointment is the wall clock the humans agreed, and it
    is what the invitation and the calendar event are rendered in. A provider
    that reports the event back in UTC has not changed that.
    """
    u = _user(db_session, brand, ROLE_SALES_MANAGER)
    _profile(db_session, u)
    day = (datetime.utcnow() + timedelta(days=3)).date()

    r = client.post("/sales/appointments", headers=_h(u, db_session),
                    json={"starts_at": av.local_to_utc(
                        day, 14 * 60, "America/Chicago").isoformat(),
                        "duration_minutes": 30,
                        "timezone": "America/Chicago",
                        "required_user_ids": [u.id]})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["timezone"] == "America/Chicago"
    # The resolved wall clock is 2pm regardless of where the viewer or the
    # provider sits.
    assert str(body["starts_at_local"])[11:16] == "14:00"


def test_an_unknown_timezone_is_refused_loudly(client, db_session, brand):
    """Silently defaulting somebody's whole calendar to Central is worse than
    a 400."""
    u = _user(db_session, brand, ROLE_SALES_MANAGER)
    r = client.put("/sales/availability/me", headers=_h(u, db_session),
                   json={"timezone": "Mars/Olympus_Mons"})
    assert r.status_code == 400
    assert "Unknown timezone" in r.json()["detail"]


def test_a_backwards_working_window_is_refused(client, db_session, brand):
    u = _user(db_session, brand, ROLE_SALES_MANAGER)
    r = client.put("/sales/availability/me", headers=_h(u, db_session),
                   json={"windows": [{"day_of_week": 0,
                                      "start_minute": 1020,
                                      "end_minute": 540}]})
    assert r.status_code == 400


# ═══════════════════════════════════════════════════════════════════════
# GUESSING URLS
# ═══════════════════════════════════════════════════════════════════════

MANAGER_ONLY_GETS = [
    "/sales/calendar/conflicts",
    "/sales/appointments/completion-facts",
]


@pytest.mark.parametrize("path", MANAGER_ONLY_GETS)
def test_a_rep_guessing_a_manager_url_is_refused(client, db_session, brand, path):
    rep = _user(db_session, brand, ROLE_SALES_REP)
    assert client.get(path, headers=_h(rep, db_session)).status_code == 403


@pytest.mark.parametrize("path", MANAGER_ONLY_GETS + [
    "/sales/calendar/view",
    "/sales/calendar/sync-status",
    "/sales/appointments/pending-outcome",
])
def test_every_new_route_refuses_an_anonymous_caller(client, path):
    assert client.get(path).status_code == 401


def test_a_caller_with_no_brand_membership_is_refused(client, db_session):
    """A valid JWT is not a sales membership.

    Somebody with an account but no seat in any brand must get nothing, not an
    empty-but-successful workspace that implies they belong.
    """
    nobody = _user(db_session, None)
    for path in ("/sales/calendar/view", "/sales/calendar/sync-status",
                 "/sales/availability/team",
                 "/sales/appointments/pending-outcome"):
        r = client.get(path, headers=_h(nobody, db_session))
        assert r.status_code == 403, "%s let a non-member through" % path


# Every path that takes an appointment id, with a body that would SUCCEED if
# the caller were entitled to it. A body the schema rejects would give a 422
# before authorization is ever consulted, which proves nothing about the guard
# — so each of these is a genuinely well-formed request from the wrong person.
CROSS_BRAND_PATHS = [
    ("get", "/sales/appointments/%s", None),
    ("get", "/sales/appointments/%s/outcome-options", None),
    ("post", "/sales/appointments/%s/outcome", {"outcome": OUTCOME_CANCELLED}),
    ("post", "/sales/appointments/%s/reconcile", {}),
    ("post", "/sales/appointments/%s/resolve-conflict",
     {"user_id": "whoever", "action": "push_evosys"}),
    ("post", "/sales/appointments/%s/cancel", {"reason": "not mine to cancel"}),
    ("post", "/sales/appointments/%s/reschedule",
     {"starts_at": "2099-01-01T10:00:00"}),
    ("post", "/sales/appointments/%s/resync", {}),
]


@pytest.mark.parametrize("method,template,body", CROSS_BRAND_PATHS)
def test_no_route_accepts_another_brands_appointment_id(
        client, db_session, brand, brand_b, method, template, body):
    """EVERY path that takes an appointment id, not just the read.

    One unguarded write endpoint is enough to cancel another brand's meeting,
    so this is parameterised rather than spot-checked — a new route added
    without `_load_appt` fails here.
    """
    owner = _user(db_session, brand, ROLE_SALES_MANAGER)
    intruder = _user(db_session, brand_b, ROLE_SALES_MANAGER)
    _profile(db_session, owner)
    a = _appt(db_session, brand, [owner])

    path = template % a.id
    fn = getattr(client, method)
    kwargs = {"json": body} if body is not None else {}
    r = fn(path, headers=_h(intruder, db_session), **kwargs)

    # 404 rather than 403: confirming that the id exists is itself a leak.
    assert r.status_code == 404, (
        "%s %s returned %s for another brand's appointment"
        % (method.upper(), template, r.status_code))

    db_session.refresh(a)
    assert a.status == APPT_SCHEDULED, "nothing may have been changed"
    assert a.outcome is None
    assert a.cancelled_at is None
    assert (a.rescheduled_count or 0) == 0


def test_a_nonexistent_appointment_id_is_a_clean_404(client, db_session, brand):
    u = _user(db_session, brand, ROLE_SALES_MANAGER)
    r = client.get("/sales/appointments/not-a-real-id",
                   headers=_h(u, db_session))
    assert r.status_code == 404
    assert "traceback" not in r.text.lower()


def test_resolving_a_conflict_that_does_not_exist_is_refused(client, db_session,
                                                             brand):
    """The endpoint writes to somebody's calendar, so it must refuse to act on
    a participant who is not actually in conflict."""
    mgr = _user(db_session, brand, ROLE_SALES_MANAGER)
    _profile(db_session, mgr)
    a = _appt(db_session, brand, [mgr])

    r = client.post("/sales/appointments/%s/resolve-conflict" % a.id,
                    headers=_h(mgr, db_session),
                    json={"user_id": mgr.id, "action": "push_evosys"})
    assert r.status_code == 400
    assert "not in conflict" in r.json()["detail"]


def test_resolving_a_conflict_for_a_stranger_is_a_404(client, db_session,
                                                      brand, brand_b):
    """A user id from another brand in the body must not resolve to a row."""
    mgr = _user(db_session, brand, ROLE_SALES_MANAGER)
    stranger = _user(db_session, brand_b, ROLE_SALES_REP)
    _profile(db_session, mgr)
    a = _appt(db_session, brand, [mgr])

    r = client.post("/sales/appointments/%s/resolve-conflict" % a.id,
                    headers=_h(mgr, db_session),
                    json={"user_id": stranger.id, "action": "push_evosys"})
    assert r.status_code == 404


def test_an_unknown_resolution_action_is_refused(client, db_session, brand):
    mgr = _user(db_session, brand, ROLE_SALES_MANAGER)
    _profile(db_session, mgr)
    a = _appt(db_session, brand, [mgr])
    part = db_session.query(AppointmentParticipant).filter(
        AppointmentParticipant.appointment_id == a.id).first()
    part.sync_conflict = True
    part.sync_conflict_kind = "provider_moved"
    db_session.commit()

    r = client.post("/sales/appointments/%s/resolve-conflict" % a.id,
                    headers=_h(mgr, db_session),
                    json={"user_id": mgr.id, "action": "delete_everything"})
    assert r.status_code == 400


def test_a_bogus_member_filter_returns_nothing_not_everything(client, db_session,
                                                              brand):
    """"Show me these people" with none of them present answers NOTHING.

    Silently widening to the whole team would be a lie about what was filtered,
    and on a screen somebody books from that is the wrong direction to be wrong.
    """
    mgr = _user(db_session, brand, ROLE_SALES_MANAGER)
    _profile(db_session, mgr)
    _appt(db_session, brand, [mgr])

    r = client.get("/sales/calendar/view?member_ids=not-a-user",
                   headers=_h(mgr, db_session))
    assert r.status_code == 200, r.text
    assert r.json()["people"] == []
    assert r.json()["appointments"] == []


# ═══════════════════════════════════════════════════════════════════════
# SCALE
# ═══════════════════════════════════════════════════════════════════════

def test_a_realistic_week_loads_in_one_pass(client, db_session, brand):
    """Eight people, a full week, and a couple of hundred appointments.

    This is a REALISTIC shape for the brand rather than a stress test: the
    thing worth protecting is that the calendar view stays ONE request whose
    cost grows with the window, not one that quietly becomes N+1 per
    appointment as fields get added to the serializer.
    """
    mgr = _user(db_session, brand, ROLE_SALES_MANAGER)
    team = [mgr] + [_user(db_session, brand, name="Rep %d" % i) for i in range(7)]
    for u in team:
        _profile(db_session, u)

    monday = datetime.utcnow() + timedelta(days=3)
    monday = monday.replace(hour=13, minute=0, second=0, microsecond=0)
    made = 0
    # Five weekdays × six slots × the whole team = 240 meetings, which is a
    # heavy but genuinely possible week for eight sellers.
    for d in range(5):
        for slot in range(6):
            for who in team:
                _appt(db_session, brand, [who],
                      starts_at=monday + timedelta(days=d, minutes=slot * 45),
                      duration=30)
                made += 1
    assert made > 200, "the fixture must actually be a realistic week"

    t0 = time.time()
    r = client.get("/sales/calendar/view?date_from=%s&date_to=%s"
                   % ((monday.date()).isoformat(),
                      (monday.date() + timedelta(days=6)).isoformat()),
                   headers=_h(mgr, db_session))
    elapsed = time.time() - t0

    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["appointments"]) >= 200
    assert len(body["people"]) == 8
    # Generous on purpose — this is a regression guard against an accidental
    # per-appointment round trip, not a performance benchmark, and it runs on
    # SQLite in a worker thread.
    assert elapsed < 25, "the calendar view took %.1fs for one week" % elapsed


def test_the_view_refuses_to_be_used_as_an_export(client, db_session, brand):
    """A bounded window and a bounded row count.

    Every day added multiplies the external-busy refresh across the team, so
    the endpoint has a hard ceiling rather than an implicit one.
    """
    mgr = _user(db_session, brand, ROLE_SALES_MANAGER)
    _profile(db_session, mgr)
    r = client.get("/sales/calendar/view?date_from=2026-01-01&date_to=2026-12-31",
                   headers=_h(mgr, db_session))
    assert r.status_code == 400
    assert "at a time" in r.json()["detail"]


def test_find_team_time_refuses_an_unbounded_search(client, db_session, brand):
    mgr = _user(db_session, brand, ROLE_SALES_MANAGER)
    _profile(db_session, mgr)
    r = client.post("/sales/availability/find", headers=_h(mgr, db_session),
                    json={"required_user_ids": [mgr.id], "duration_minutes": 30,
                          "date_from": "2026-01-01", "date_to": "2026-12-31"})
    assert r.status_code == 400
    assert "at a time" in r.json()["detail"]
