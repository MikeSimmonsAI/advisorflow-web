"""Appointment times in each service company's OWN time zone (#452).

The shared slot engine (calendar_router.compute_day_slots, also used by SCI)
works in America/Chicago wall time, and BookingLink.booked_time is stored in
that same Chicago wall time so every module sees the same conflicts for a
calendar owner. This module leaves both alone and converts at the edges:

* a company in Central keeps the shared engine exactly as before;
* a company in any other IANA zone gets its own 9:00-17:00 local day, checked
  against the same calendar busy times and the same stored bookings;
* every conversion goes through zoneinfo, so DST changes are handled by the
  tz database, never by a fixed hour offset.
"""
from __future__ import annotations

from datetime import date as date_cls, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

ENGINE_TZ = "America/Chicago"
DAY_START, LAST_START = 9, 16          # local: first and last hour-long start
MAX_DAYS_OUT = 14


def valid_tz(name: Optional[str]) -> bool:
    if not name:
        return False
    try:
        ZoneInfo(str(name))
        return True
    except Exception:                                            # noqa: BLE001
        return False


def company_tz(profile) -> str:
    tz = getattr(profile, "timezone", None) if profile is not None else None
    return tz if valid_tz(tz) else ENGINE_TZ


def convert(naive: datetime, from_tz: str, to_tz: str) -> datetime:
    if from_tz == to_tz:
        return naive
    return naive.replace(tzinfo=ZoneInfo(from_tz)).astimezone(ZoneInfo(to_tz)).replace(tzinfo=None)


def to_engine(local: datetime, tz: str) -> datetime:
    """Company wall time -> the Chicago wall time bookings are stored in."""
    return convert(local, tz, ENGINE_TZ)


def from_engine(stored: Optional[datetime], tz: str) -> Optional[datetime]:
    return None if stored is None else convert(stored.replace(tzinfo=None), ENGINE_TZ, tz)


def utc_of(local: datetime, tz: str) -> datetime:
    return local.replace(tzinfo=ZoneInfo(tz)).astimezone(timezone.utc).replace(tzinfo=None)


def label(local: datetime, tz: str) -> str:
    """'Thu Nov 05, 02:00 PM CST' - the abbreviation follows DST."""
    return "%s %s" % (local.strftime("%a %b %d, %I:%M %p"), local.replace(tzinfo=ZoneInfo(tz)).tzname())


def _overlaps(a0, a1, b0, b1) -> bool:
    return a0 < b1 and a1 > b0


def slots_for(db, booker, profile, target: date_cls, *, minutes: int = 60,
              now: Optional[datetime] = None, busy_fn=None) -> Dict[str, Any]:
    """Free hour-long starts on `target` (a COMPANY-local date), as company
    local ISO strings, with the zone name. Fails closed on an unreadable calendar."""
    from app.services.acquisition.calendar_setup import filter_slots
    tz = company_tz(profile)
    day = target.isoformat()
    if tz == ENGINE_TZ and busy_fn is None:
        from app.routers.calendar_router import compute_day_slots
        raw = compute_day_slots(db, booker, target, program_visit=True, minutes=minutes)
        res = {"date": day, "timezone": tz,
               "slots": filter_slots(profile, raw.get("slots", []), now) if profile else raw.get("slots", [])}
        if raw.get("reason"):
            res["reason"] = raw["reason"]
        return res
    now_utc = now or datetime.utcnow()
    today = convert(now_utc, "UTC", tz).date()
    if target < today:
        return {"date": day, "timezone": tz, "slots": [], "reason": "Date is in the past"}
    if target > today + timedelta(days=MAX_DAYS_OUT):
        return {"date": day, "timezone": tz, "slots": [], "reason": "Date is more than 14 days out"}
    if target.weekday() >= 5:
        return {"date": day, "timezone": tz, "slots": [], "reason": "Weekend — no appointments"}
    cands = [datetime(target.year, target.month, target.day, h) for h in range(DAY_START, LAST_START + 1)]
    # Busy times and stored bookings are in Chicago wall time, per Chicago date.
    eng_days = sorted({to_engine(c, tz).date() for c in cands} |
                      {to_engine(c + timedelta(minutes=minutes), tz).date() for c in cands})
    busy: List = []
    if busy_fn is None:
        from app.routers.calendar_router import _booking_window_busy as busy_fn  # noqa: N813
    for d in eng_days:
        iv, err = busy_fn(db, booker, d)
        if err:
            return {"date": day, "timezone": tz, "slots": [], "calendar_error": err,
                    "reason": "Calendar is temporarily unavailable. Please try again shortly."}
        busy.extend(iv or [])
    from app.models.models import BookingLink
    lo = to_engine(cands[0], tz) - timedelta(hours=4)
    hi = to_engine(cands[-1], tz) + timedelta(hours=4)
    for b in (db.query(BookingLink).filter(BookingLink.user_id == booker.id,
                                           BookingLink.status.in_(["booked", "confirmed"]),
                                           BookingLink.booked_time >= lo, BookingLink.booked_time <= hi).all()):
        bt = b.booked_time.replace(tzinfo=None)
        busy.append((bt, bt + timedelta(minutes=int(b.appt_duration or 60))))
    buffer_cut = convert(now_utc, "UTC", tz) + timedelta(hours=2)
    free = []
    for c in cands:
        e0, e1 = to_engine(c, tz), to_engine(c + timedelta(minutes=minutes), tz)
        if c < buffer_cut:
            continue
        if any(_overlaps(e0, e1, b0, b1) for b0, b1 in busy):
            continue
        free.append(c.strftime("%Y-%m-%dT%H:%M:00"))
    return {"date": day, "timezone": tz, "slots": filter_slots(profile, free, now, tz=tz) if profile else free}
