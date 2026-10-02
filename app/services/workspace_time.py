"""THE WORKSPACE'S CALENDAR - one resolver for "today", "this month" and "due today".

THE RULE
--------
Timestamps are stored as naive UTC. CALENDAR questions - what day is it, is
this task due today, is this enrollment in this month, how many days until a
contract ends - are answered in the WORKSPACE's timezone. UTC's calendar is
wrong for every workspace in the Americas from 7pm (Central, CDT) or 6pm (CST)
until midnight, every single evening, and on the last evening of every month
it is a different month.

WHICH TIMEZONE
--------------
    1. Organization.timezone, when set to a valid IANA name;
    2. otherwise the advisors' booking_timezone, when they agree on one;
    3. otherwise scheduling_models.DEFAULT_TIMEZONE.

Steps 2 and 3 are exactly what activity_reporting resolved before this module
existed, so a workspace that has not chosen a timezone sees no change.

DST
---
Day and month boundaries are computed by converting LOCAL midnight to UTC with
zoneinfo for that specific date - never by adding a fixed offset - so the day
DST starts is 23 hours long and the day it ends is 25, and both are counted
correctly.

Date-only business fields (a contract end date, a move date) stay dates: they
are compared with `local_today()`, never converted through a timezone.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Optional, Tuple

try:
    from zoneinfo import ZoneInfo, available_timezones
except ImportError:                                              # pragma: no cover
    ZoneInfo = None
    available_timezones = lambda: set()                         # noqa: E731

from sqlalchemy.orm import Session

UTC = "UTC"


def default_timezone() -> str:
    from app.models.scheduling_models import DEFAULT_TIMEZONE
    return DEFAULT_TIMEZONE


def is_valid_timezone(name: Optional[str]) -> bool:
    """A real IANA zone. Abbreviations ("CST", "EST") are refused: they are not
    zones, they are one half of a zone's year."""
    if not name or not isinstance(name, str) or "/" not in name and name != "UTC":
        return False
    try:
        ZoneInfo(name)
        return True
    except Exception:                                            # noqa: BLE001
        return False


def workspace_timezone(db: Session, organization_id: Optional[str]) -> str:
    if not organization_id:
        return default_timezone()
    from app.models.models import Organization, User
    return resolve(db, organization_id)[0]


def resolve(db: Session, organization_id: Optional[str]) -> Tuple[str, str]:
    """(timezone, source) - source is one of: workspace, primary_location,
    advisors, platform_default. Shown in settings so an admin can see WHY."""
    if not organization_id:
        return default_timezone(), "platform_default"
    from app.models.models import Organization, User
    tz = db.query(Organization.timezone).filter(Organization.id == organization_id).scalar()
    if is_valid_timezone(tz):
        return tz, "workspace"
    try:
        from app.models.location_models import Location
        loc_tz = (db.query(Location.timezone)
                  .filter(Location.organization_id == organization_id, Location.is_primary.is_(True))
                  .scalar())
        if is_valid_timezone(loc_tz):
            return loc_tz, "primary_location"
    except Exception:                                            # noqa: BLE001
        pass
    zones = [z for (z,) in db.query(User.booking_timezone)
             .filter(User.organization_id == organization_id, User.booking_timezone.isnot(None))
             .distinct().all() if is_valid_timezone(z)]
    if len(zones) == 1:
        return zones[0], "advisors"
    return default_timezone(), "platform_default"


def _zone(tzname: str):
    try:
        return ZoneInfo(tzname)
    except Exception:                                            # noqa: BLE001
        return ZoneInfo(UTC)


def to_local(dt_utc: datetime, tzname: str) -> datetime:
    """Naive UTC -> aware local."""
    return dt_utc.replace(tzinfo=ZoneInfo(UTC)).astimezone(_zone(tzname))


def local_midnight_utc(day: date, tzname: str) -> datetime:
    """Naive UTC instant of local 00:00 on `day` (DST-correct)."""
    return datetime.combine(day, time.min, tzinfo=_zone(tzname)).astimezone(ZoneInfo(UTC)).replace(tzinfo=None)


def local_date(now: Optional[datetime], tzname: str) -> date:
    return to_local(now or datetime.utcnow(), tzname).date()


def local_today(db: Session, organization_id: Optional[str], now: Optional[datetime] = None) -> date:
    return local_date(now, workspace_timezone(db, organization_id))


def day_bounds(db: Session, organization_id: Optional[str], day: Optional[date] = None,
               now: Optional[datetime] = None, tzname: Optional[str] = None) -> Tuple[datetime, datetime, str]:
    """[start, end) of one local day as naive UTC, plus the zone used."""
    tz = tzname if is_valid_timezone(tzname) else workspace_timezone(db, organization_id)
    d = day or local_date(now, tz)
    return local_midnight_utc(d, tz), local_midnight_utc(d + timedelta(days=1), tz), tz


def month_bounds(db: Session, organization_id: Optional[str], now: Optional[datetime] = None,
                 tzname: Optional[str] = None) -> Tuple[datetime, datetime, str]:
    """[start, end) of the current local month as naive UTC, plus the zone used."""
    tz = tzname if is_valid_timezone(tzname) else workspace_timezone(db, organization_id)
    today = local_date(now, tz)
    first = today.replace(day=1)
    nxt = (first + timedelta(days=32)).replace(day=1)
    return local_midnight_utc(first, tz), local_midnight_utc(nxt, tz), tz


def tz_bounds_for_day(day: date, tzname: str) -> Tuple[datetime, datetime]:
    """For callers that already hold a zone (a brand sales org's own timezone)."""
    return local_midnight_utc(day, tzname), local_midnight_utc(day + timedelta(days=1), tzname)


def month_bounds_tz(now: Optional[datetime], tzname: str) -> Tuple[datetime, datetime]:
    today = local_date(now, tzname)
    first = today.replace(day=1)
    nxt = (first + timedelta(days=32)).replace(day=1)
    return local_midnight_utc(first, tzname), local_midnight_utc(nxt, tzname)


def trend_days(db: Session, organization_id: Optional[str], days: int,
               now: Optional[datetime] = None) -> Tuple[date, datetime, str]:
    """First local day of an N-day trend ending today, its UTC start, and the zone.
    Charts bucket by LOCAL date: an 8pm reply belongs to today, not tomorrow."""
    tz = workspace_timezone(db, organization_id)
    today = local_date(now, tz)
    first = today - timedelta(days=max(1, days) - 1)
    return first, local_midnight_utc(first, tz), tz


def common_timezones():
    """For pickers: the Americas first (where customers are), then the rest."""
    prefer = ["America/New_York", "America/Chicago", "America/Denver", "America/Phoenix",
              "America/Los_Angeles", "America/Anchorage", "Pacific/Honolulu", "America/Puerto_Rico"]
    rest = sorted(z for z in available_timezones() if "/" in z and not z.startswith(("Etc/", "SystemV/"))
                  and z not in prefer)
    return prefer + rest


# ── date-only values stored as timestamps ──────────────────────────────────
#
# Several screens store a DATE ("due Oct 5") in a DateTime column: some as
# midnight UTC (a browser parsing "2026-10-05"), some as noon UTC (the
# "T12:00:00" convention). Read through a timezone, midnight UTC is 7pm the
# PREVIOUS evening in Central - which made a date-only follow-up show as due
# (and then overdue) a day early. A value exactly on one of those two anchors
# is treated as the calendar date it was written as; anything else is a real
# instant and is read in the workspace's timezone.

def is_date_anchor(dt: Optional[datetime]) -> bool:
    return bool(dt) and dt.minute == 0 and dt.second == 0 and dt.microsecond == 0 and dt.hour in (0, 12)


def due_local_date(dt: datetime, tzname: str) -> date:
    return dt.date() if is_date_anchor(dt) else local_date(dt, tzname)


def is_overdue(dt: Optional[datetime], now: datetime, tzname: str) -> bool:
    if not dt:
        return False
    if is_date_anchor(dt):
        return dt.date() < local_date(now, tzname)
    return dt < now


def is_due_by_today(dt: Optional[datetime], now: datetime, tzname: str) -> bool:
    return bool(dt) and due_local_date(dt, tzname) <= local_date(now, tzname)
