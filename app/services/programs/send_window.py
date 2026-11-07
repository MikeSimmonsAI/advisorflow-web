"""SCI OUTREACH WINDOW (owner, Nov 7 2026): seven days a week, 9:00 AM - 6:00
PM Central.

One place for the days and hours that SCI program OUTREACH may go out:
  * new journey starts (journey.release_slots),
  * the daily batch (daily_batch - its own start time sits inside this),
  * every scheduled text touch (cadence_service, for program workspaces),
  * every scheduled email touch (email_touches.in_sending_hours).

It NARROWS, it never widens: the family's own clock (contact_hours: 8:30 AM -
7:45 PM Eastern and 8 AM - 9 PM local) still has to permit the send too, and
STOP/DNC, suppression, provider holds and every other check are untouched.
Replies to a family's own message (the AI and people answering) follow the
family's clock, not this window - they are answers, not outreach.

Seven days means Saturday and Sunday are ordinary sending days. Nothing is
compressed: each touch keeps its own interval; a touch that falls outside the
window waits for the next opening (the same day at 9 AM, or the next day).
"""
from datetime import datetime, time, timedelta
from typing import Optional

TZ = "America/Chicago"
OPEN = time(9, 0)                 # first send of a day, Central
CLOSE = time(18, 0)               # nothing at or after this, Central
DAYS = "1234567"                  # ISO weekdays: 1 = Monday ... 7 = Sunday
DAY_NAMES = {"1": "Mon", "2": "Tue", "3": "Wed", "4": "Thu", "5": "Fri", "6": "Sat", "7": "Sun"}


def _zone():
    from zoneinfo import ZoneInfo
    return ZoneInfo(TZ)


def _local(now_utc: Optional[datetime] = None) -> datetime:
    from datetime import timezone
    n = now_utc or datetime.utcnow()
    if n.tzinfo is None:
        n = n.replace(tzinfo=timezone.utc)
    return n.astimezone(_zone())


def permits(now_utc: Optional[datetime] = None, days: str = DAYS) -> bool:
    """Is this moment inside the SCI outreach window?"""
    lt = _local(now_utc)
    return str(lt.isoweekday()) in days and OPEN <= lt.time().replace(tzinfo=None) < CLOSE


def next_open(now_utc: Optional[datetime] = None, days: str = DAYS) -> datetime:
    """Naive UTC: now if inside the window, else the next opening (9 AM
    Central on the next allowed day). Daylight-saving safe: the opening is
    built on the local calendar, then converted."""
    from datetime import timezone
    n = now_utc or datetime.utcnow()
    if permits(n, days):
        return n.replace(tzinfo=None) if n.tzinfo else n
    lt = _local(n)
    d = lt.date()
    if str(lt.isoweekday()) in days and lt.time().replace(tzinfo=None) < OPEN:
        pass
    else:
        d = d + timedelta(days=1)
    for _ in range(8):
        if str(d.isoweekday()) in days:
            at = datetime.combine(d, OPEN).replace(tzinfo=_zone())
            return at.astimezone(timezone.utc).replace(tzinfo=None)
        d += timedelta(days=1)
    return (n + timedelta(days=1)).replace(tzinfo=None)


STAGGER_MINUTES = 45


def stagger_minutes(key: Optional[str]) -> int:
    """A stable 0..44-minute offset for one record, so work that waited for
    the opening is spread out instead of leaving in one burst at 9:00."""
    import zlib
    return zlib.crc32((key or "").encode("utf-8")) % STAGGER_MINUTES


def describe(days: str = DAYS) -> dict:
    names = [DAY_NAMES[c] for c in "1234567" if c in days]
    label = "Every day" if len(names) == 7 else ", ".join(names)
    return {"days": days, "days_label": label, "open": OPEN.strftime("%H:%M"), "close": CLOSE.strftime("%H:%M"),
            "timezone": TZ, "label": "%s, 9:00 AM - 6:00 PM Central" % label}


def applies_to_org(db, organization_id: Optional[str], cache: Optional[dict] = None) -> bool:
    """Program (SCI) workspaces only - other workspaces keep their own rules."""
    if not organization_id:
        return False
    if cache is not None and organization_id in cache:
        return cache[organization_id]
    try:
        from app.services.programs import identity
        ok = identity.program_for_org(db, organization_id) is not None
    except Exception:                                    # noqa: BLE001 - never blocks on a lookup error
        ok = False
    if cache is not None:
        cache[organization_id] = ok
    return ok
