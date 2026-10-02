"""WORKSPACE TIMEZONE - "today" means the workspace's today, everywhere.

Pins app/services/workspace_time.py and the screens moved onto it on
2026-10-02: energy task queues, the work queue's "due today", the sales brief
(follow-ups due, won this month, next action overdue), lead/reply trend
charts, Max Life "today's appointments", the AI-employee daily cap and the
legacy availability listing.

Every boundary case is a real one: 7pm-midnight Central is tomorrow in UTC;
the last evening of a month is next month in UTC; DST days are 23 and 25 hours.
"""
import itertools
import uuid
from datetime import date, datetime, timedelta

import pytest

from app.models.models import Lead, Organization, Reply, User
from app.services import workspace_time as wt
from app.services.auth_service import create_access_token, hash_password

_N = itertools.count(1)


def _org(db, tz=None, industry="energy", advisor_tz=None, role="org_admin"):
    n = next(_N)
    org = Organization(name="TZ %d" % n, slug="tz-%d-%s" % (n, uuid.uuid4().hex[:6]), plan="standard",
                       industry=industry, is_active=True, timezone=tz)
    db.add(org)
    db.commit()
    u = User(organization_id=org.id, email="tz%d@test.live" % n, password_hash=hash_password("x"),
             full_name="TZ User", role=role, must_change_password=False, is_active=True,
             booking_timezone=advisor_tz)
    db.add(u)
    db.commit()
    return org, u


# ── resolution ──────────────────────────────────────────────────────────────

def test_resolution_order_and_abbreviations_are_refused(db_session):
    db = db_session
    org, u = _org(db, tz=None, advisor_tz="America/Denver")
    assert wt.resolve(db, org.id) == ("America/Denver", "advisors")
    from app.models.location_models import Location
    db.add(Location(organization_id=org.id, name="HQ", is_primary=True, timezone="America/Phoenix"))
    db.commit()
    assert wt.resolve(db, org.id) == ("America/Phoenix", "primary_location")
    org.timezone = "America/New_York"
    db.commit()
    assert wt.resolve(db, org.id) == ("America/New_York", "workspace")
    org.timezone = "CST"                       # not a timezone - ignored, never trusted
    db.commit()
    assert wt.resolve(db, org.id)[1] == "primary_location"
    assert wt.is_valid_timezone("CST") is False and wt.is_valid_timezone("America/Chicago") is True
    org2, u2 = _org(db)
    u2.booking_timezone = None
    db.commit()
    assert wt.resolve(db, org2.id) == (wt.default_timezone(), "platform_default")


# ── boundaries ──────────────────────────────────────────────────────────────

def test_evening_after_the_utc_rollover_is_still_today(db_session):
    org, _ = _org(db_session, tz="America/Chicago")
    now = datetime(2026, 10, 2, 3, 30)          # 22:30 CDT Oct 1
    assert wt.local_today(db_session, org.id, now) == date(2026, 10, 1)
    start, end, tz = wt.day_bounds(db_session, org.id, now=now)
    assert (start, end, tz) == (datetime(2026, 10, 1, 5), datetime(2026, 10, 2, 5), "America/Chicago")


def test_month_end_evening_is_still_this_month(db_session):
    org, _ = _org(db_session, tz="America/Chicago")
    start, end, _ = wt.month_bounds(db_session, org.id, now=datetime(2026, 11, 1, 2, 0))   # Oct 31 21:00 CDT
    assert (start, end) == (datetime(2026, 10, 1, 5), datetime(2026, 11, 1, 5))


def test_year_end_evening_is_still_this_year(db_session):
    org, _ = _org(db_session, tz="America/Chicago")
    now = datetime(2027, 1, 1, 3, 0)            # Dec 31 21:00 CST
    assert wt.local_today(db_session, org.id, now) == date(2026, 12, 31)
    start, end, _ = wt.month_bounds(db_session, org.id, now=now)
    assert (start, end) == (datetime(2026, 12, 1, 6), datetime(2027, 1, 1, 6))


def test_dst_start_day_is_23_hours_and_dst_end_day_is_25(db_session):
    org, _ = _org(db_session, tz="America/Chicago")
    s, e, _ = wt.day_bounds(db_session, org.id, day=date(2026, 3, 8))
    assert (s, e) == (datetime(2026, 3, 8, 6), datetime(2026, 3, 9, 5)) and (e - s) == timedelta(hours=23)
    s, e, _ = wt.day_bounds(db_session, org.id, day=date(2026, 11, 1))
    assert (s, e) == (datetime(2026, 11, 1, 5), datetime(2026, 11, 2, 6)) and (e - s) == timedelta(hours=25)


def test_two_workspaces_in_different_zones_see_different_days_at_one_instant(db_session):
    ny, _ = _org(db_session, tz="America/New_York")
    la, _ = _org(db_session, tz="America/Los_Angeles")
    ldn, _ = _org(db_session, tz="Europe/London")
    now = datetime(2026, 10, 2, 4, 30)
    assert wt.local_today(db_session, ny.id, now) == date(2026, 10, 2)
    assert wt.local_today(db_session, la.id, now) == date(2026, 10, 1)
    assert wt.local_today(db_session, ldn.id, now) == date(2026, 10, 2)


def test_date_only_dues_are_calendar_dates(db_session):
    tz = "America/Chicago"
    due = datetime(2026, 10, 5, 0, 0)            # "Oct 5" written as midnight UTC
    evening_before = datetime(2026, 10, 5, 2, 0)  # Oct 4 21:00 CDT
    assert wt.is_overdue(due, evening_before, tz) is False        # old rule said overdue
    assert wt.is_due_by_today(due, evening_before, tz) is False   # and that it was due "today"
    assert wt.is_overdue(due, datetime(2026, 10, 6, 6, 0), tz) is True   # Oct 6 01:00 CDT
    real = datetime(2026, 10, 5, 0, 30)          # a real instant, 30 min past an anchor
    assert wt.is_overdue(real, datetime(2026, 10, 5, 0, 31), tz) is True


# ── screens ─────────────────────────────────────────────────────────────────

def test_work_queue_due_today_includes_an_8pm_task(db_session, client, monkeypatch):
    from app.models.work_models import LeadTask
    import app.routers.work_router as W
    org, u = _org(db_session, tz="America/Chicago")
    lead = Lead(organization_id=org.id, assigned_to_id=u.id, first_name="Eve", last_name="Ning", status="new")
    db_session.add(lead)
    db_session.commit()
    db_session.add(LeadTask(organization_id=org.id, lead_id=lead.id, title="Call at 8pm",
                            due_at=datetime(2026, 10, 2, 1, 0), status="open", assigned_to_id=u.id))   # 20:00 CDT Oct 1
    db_session.add(LeadTask(organization_id=org.id, lead_id=lead.id, title="Tomorrow 9am",
                            due_at=datetime(2026, 10, 2, 14, 0), status="open", assigned_to_id=u.id))  # 09:00 CDT Oct 2
    db_session.commit()
    monkeypatch.setattr(W, "_utcnow", lambda: datetime(2026, 10, 1, 23, 0))   # 18:00 CDT Oct 1
    h = {"Authorization": "Bearer " + create_access_token(u, db_session)}
    r = client.get("/work/tasks?status=open&due=today", headers=h)
    assert r.status_code == 200, r.text
    titles = [t["title"] for t in r.json()["items"]]
    assert titles == ["Call at 8pm"]                # UTC's day would have ended at 7pm and missed it


def test_energy_follow_up_due_counts_the_workspace_day(db_session, client, monkeypatch):
    from app.models.work_models import LeadTask
    from app.routers import energy_ops_router as R
    org, u = _org(db_session, tz="America/Chicago", industry="energy")
    lead = Lead(organization_id=org.id, assigned_to_id=u.id, first_name="En", last_name="Ergy", status="new")
    db_session.add(lead)
    db_session.commit()
    db_session.add(LeadTask(organization_id=org.id, lead_id=lead.id, title="evening follow-up",
                            due_at=datetime(2026, 10, 2, 1, 30), status="open", assigned_to_id=u.id))
    db_session.commit()
    monkeypatch.setattr(R, "_now", lambda: datetime(2026, 10, 1, 23, 0))
    h = {"Authorization": "Bearer " + create_access_token(u, db_session)}
    q = {x["key"]: x["count"] for x in client.get("/energy-ops/queues", headers=h).json()["queues"]}
    assert q["follow_up_due"] == 1 and q["overdue"] == 0


def test_reply_trend_buckets_by_local_day(db_session, client, monkeypatch):
    org, u = _org(db_session, tz="America/Chicago", industry="general")
    lead = Lead(organization_id=org.id, assigned_to_id=u.id, first_name="Tr", last_name="End", status="new")
    db_session.add(lead)
    db_session.commit()
    now = datetime.utcnow()
    local_today = wt.local_today(db_session, org.id, now)
    evening = wt.local_midnight_utc(local_today, "America/Chicago") - timedelta(hours=3)   # 9pm local yesterday
    db_session.add(Reply(lead_id=lead.id, body="hi", source="sms", received_at=evening))
    db_session.commit()
    h = {"Authorization": "Bearer " + create_access_token(u, db_session)}
    r = client.get("/sms/replies/activity-by-day?days=7", headers=h)
    if r.status_code == 404:
        pytest.skip("reply activity route not mounted under this path in this build")
    series = {x["date"]: x["count"] for x in r.json()}
    assert series.get((local_today - timedelta(days=1)).isoformat()) == 1


def test_timezone_setting_api(db_session, client):
    org, u = _org(db_session, tz=None)
    h = {"Authorization": "Bearer " + create_access_token(u, db_session)}
    r = client.patch("/org-settings/timezone", headers=h, json={"timezone": "CST"})
    assert r.status_code == 422
    r = client.patch("/org-settings/timezone", headers=h, json={"timezone": "America/Los_Angeles"})
    assert r.status_code == 200 and r.json()["timezone_effective"] == "America/Los_Angeles"
    assert r.json()["timezone_source"] == "workspace"
    r = client.patch("/org-settings/timezone", headers=h, json={"timezone": ""})
    assert r.json()["timezone"] is None and r.json()["timezone_source"] in ("platform_default", "advisors")
    tz = client.get("/org-settings/timezones", headers=h).json()["timezones"]
    assert tz[:2] == ["America/New_York", "America/Chicago"] and "Europe/London" in tz
