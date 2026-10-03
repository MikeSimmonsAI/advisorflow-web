"""BOOKED TIMES ON THE WIRE.

`BookingLink.booked_time` is stored as NAIVE LOCAL WALL TIME in the advisor's
booking zone (see app/services/tenant_scheduling.py: the Vercel booking flow,
the calendar confirm route and the Retell flow all write it that way, and the
reminder texts format it as-is).

Every other naive datetime in this API is UTC, and the JSON layer marks naive
values as UTC ("...Z"). So a 3:00 PM Central appointment went to the browser
as "15:00Z" and was shown as 10:00 AM in Dallas - on Availability, the Pipeline
appointments tab, the phone app and lead detail.

This module is the one crossing for READERS that send a booked time to a
browser or compare it with "now":

    zone_for(advisor)          the advisor's booking zone (same rule as
                               tenant_scheduling.Settings)
    zones(db, user_ids)        {user_id: zone} in one query
    to_wire(dt, zone)          "2026-10-03T15:00:00-05:00" - an explicit offset,
                               so every browser shows the wall time booked
    to_utc(dt, zone)           naive UTC, for comparing with utcnow()
    WINDOW                     how far a local wall time can sit from UTC; rows
                               inside now +/- WINDOW must be classified one by
                               one, rows outside it are past/upcoming for sure

Storage is NOT changed: writers and the reminder texts keep the local
convention.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, Optional

from sqlalchemy.orm import Session

DEFAULT_ZONE = "America/Chicago"   # tenant_scheduling.DEFAULT_TIMEZONE
# UTC offsets run from -12:00 to +14:00; a margin either side.
WINDOW = timedelta(hours=15)


def _valid(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(name)
        return name
    except Exception:
        return None


def zone_for(advisor) -> str:
    raw = (getattr(advisor, "booking_timezone", None) or "").strip()
    return _valid(raw) or DEFAULT_ZONE


def zones(db: Session, user_ids: Iterable[Optional[str]]) -> Dict[str, str]:
    from app.models.models import User
    ids = {u for u in user_ids if u}
    out: Dict[str, str] = {}
    if ids:
        for uid, tz in db.query(User.id, User.booking_timezone).filter(User.id.in_(ids)).all():
            out[uid] = _valid((tz or "").strip()) or DEFAULT_ZONE
    return out


def zone_of(zmap: Dict[str, str], user_id: Optional[str]) -> str:
    return zmap.get(user_id or "", DEFAULT_ZONE)


def to_wire(dt: Optional[datetime], zone: str) -> Optional[str]:
    if dt is None:
        return None
    from zoneinfo import ZoneInfo
    if dt.tzinfo is not None:
        return dt.isoformat()
    return dt.replace(tzinfo=ZoneInfo(_valid(zone) or DEFAULT_ZONE)).isoformat()


def to_utc(dt: datetime, zone: str) -> datetime:
    from zoneinfo import ZoneInfo
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(_valid(zone) or DEFAULT_ZONE))
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def is_upcoming(dt: Optional[datetime], zone: str, now_utc: Optional[datetime] = None) -> bool:
    if dt is None:
        return False
    return to_utc(dt, zone) >= (now_utc or datetime.utcnow())


def zone_of_user(db: Session, user_id: Optional[str]) -> str:
    """One advisor's zone, memoised on the session for the request."""
    if not user_id:
        return DEFAULT_ZONE
    memo = db.info.setdefault("_booking_zones", {})
    if user_id not in memo:
        memo.update(zones(db, [user_id]))
        memo.setdefault(user_id, DEFAULT_ZONE)
    return memo[user_id]


def wire(db: Session, booking) -> Optional[str]:
    """A BookingLink's booked_time for the browser, with its advisor's offset."""
    bt = getattr(booking, "booked_time", None)
    if bt is None:
        return None
    return to_wire(bt, zone_of_user(db, getattr(booking, "user_id", None)))


def wire_obj(booking) -> Optional[str]:
    """wire() for code that holds the row but not the session."""
    from sqlalchemy.orm import object_session
    db = object_session(booking)
    if db is None:
        bt = getattr(booking, "booked_time", None)
        return to_wire(bt, DEFAULT_ZONE) if bt is not None else None
    return wire(db, booking)
