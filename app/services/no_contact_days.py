"""NO-CONTACT DAYS - whole days on which the system sends NOTHING to anyone
outside the team: no texts, no emails, no AI replies, no reminders, no
follow-ups, no campaign or sales outreach (owner, Nov 23 4:29 PM: "Thanksgiving
... Everything is off ... Sending no communication, no email, no nothing").

A date is a Central calendar day. Built in: Thanksgiving 2026 (Thu Nov 26).
More can be added without a deploy in NO_CONTACT_DATES (comma-separated
YYYY-MM-DD). Every sender asks `quiet_now()` / `quiet_on(day)`:
  * the outreach programs (Restland, SCI): outreach_slots.closed_for_org /
    closed_on - the same switch as Restland's Sundays, so texts, emails, AI
    replies, follow-ups, the daily batch and surveys all hold;
  * contact_hours.check - every cadence touch and anything else that checks
    the contact window;
  * appointment reminders and confirmations (appointment_notifications);
  * EvoSys sales outreach and its booking reminders;
  * the general AI conversation pass.
Alerts to the owners themselves are not customer contact and still go out.
A person sending by hand from the app is a person's choice and is not held.
"""
import os
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Set

BUILT_IN = {"2026-11-26"}          # Thanksgiving 2026 (owner, Nov 23)
TZ = "America/Chicago"


def dates() -> Set[str]:
    extra = {d.strip() for d in (os.getenv("NO_CONTACT_DATES") or "").split(",") if d.strip()}
    return BUILT_IN | extra


def quiet_on(day) -> bool:
    """Is this Central calendar date a no-contact day?"""
    if isinstance(day, datetime):
        day = day.date()
    return isinstance(day, date) and day.isoformat() in dates()


def local_day(now_utc: Optional[datetime] = None) -> date:
    from zoneinfo import ZoneInfo
    now_utc = now_utc or datetime.utcnow()
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    return now_utc.astimezone(ZoneInfo(TZ)).date()


def quiet_now(now_utc: Optional[datetime] = None) -> bool:
    """Is it a no-contact day right now (Central)?"""
    return quiet_on(local_day(now_utc))


def holding_reminders(now_utc: Optional[datetime] = None) -> bool:
    """Appointment messages stay held through the no-contact day AND the next
    morning until 8:30 AM Central - so nothing held goes out at midnight."""
    from zoneinfo import ZoneInfo
    now_utc = now_utc or datetime.utcnow()
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    local = now_utc.astimezone(ZoneInfo(TZ))
    if quiet_on(local.date()):
        return True
    return quiet_on(local.date() - timedelta(days=1)) and (local.hour, local.minute) < (8, 30)
