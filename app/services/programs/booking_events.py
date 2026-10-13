"""BOOKING PAGE ACTIVITY - who opened their booking link, which days they
looked at, and who booked, with the moment each happened.

Recorded so a workspace can see WHEN families engage (morning vs afternoon,
which weekday) and how many open versus book. Recording never blocks the page:
any failure is swallowed and the family's request carries on."""
from collections import Counter
from datetime import datetime, timedelta
from typing import Dict, Optional

from sqlalchemy.orm import Session

OPENED, DAY_VIEWED, BOOKED = "opened", "day_viewed", "booked"


def record(db: Session, booking, event: str, detail: Optional[str] = None) -> None:
    try:
        from app.models.models import Lead
        from app.models.program_models import BookingPageEvent
        lead = db.query(Lead).filter(Lead.id == booking.lead_id).first() if booking is not None else None
        db.add(BookingPageEvent(organization_id=getattr(lead, "organization_id", None),
                                booking_link_id=getattr(booking, "id", None),
                                lead_id=getattr(booking, "lead_id", None),
                                event=event, detail=(detail or None) and str(detail)[:120]))
        db.commit()
    except Exception:                                    # noqa: BLE001
        try:
            db.rollback()
        except Exception:                                # noqa: BLE001
            pass


def add_pending(db: Session, booking, lead, event: str, detail: Optional[str] = None) -> None:
    """Same as record, but only adds the row - the caller's commit saves it."""
    try:
        from app.models.program_models import BookingPageEvent
        db.add(BookingPageEvent(organization_id=getattr(lead, "organization_id", None),
                                booking_link_id=getattr(booking, "id", None),
                                lead_id=getattr(booking, "lead_id", None),
                                event=event, detail=(detail or None) and str(detail)[:120]))
    except Exception:                                    # noqa: BLE001
        pass


def summary(db: Session, org_id: str, days: int = 30, tz: str = "America/Chicago") -> Dict:
    """Totals, by hour of day and by weekday (in the workspace's time), and the
    most recent activity with names."""
    from zoneinfo import ZoneInfo
    from app.models.models import Lead
    from app.models.program_models import BookingPageEvent
    since = datetime.utcnow() - timedelta(days=days)
    rows = (db.query(BookingPageEvent)
            .filter(BookingPageEvent.organization_id == org_id, BookingPageEvent.created_at >= since)
            .order_by(BookingPageEvent.created_at.desc()).all())
    zone = ZoneInfo(tz)
    utc = ZoneInfo("UTC")

    def local(dt):
        return dt.replace(tzinfo=utc).astimezone(zone)

    people = {e: set() for e in (OPENED, DAY_VIEWED, BOOKED)}
    by_hour = {e: Counter() for e in (OPENED, BOOKED)}
    by_day = {e: Counter() for e in (OPENED, BOOKED)}
    for r in rows:
        if r.event in people and r.lead_id:
            people[r.event].add(r.lead_id)
        if r.event in by_hour:
            lt = local(r.created_at)
            by_hour[r.event][lt.hour] += 1
            by_day[r.event][lt.strftime("%a")] += 1
    names = {}
    ids = list({r.lead_id for r in rows[:40] if r.lead_id})
    if ids:
        for l in db.query(Lead).filter(Lead.id.in_(ids)).all():
            names[l.id] = " ".join(x for x in (l.first_name, l.last_name) if x) or "Family"
    opened = len(people[OPENED])
    booked = len(people[BOOKED])
    week = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return {
        "days": days, "timezone": tz,
        "opened_people": opened, "looked_at_times_people": len(people[DAY_VIEWED]),
        "booked_people": booked, "open_events": sum(1 for r in rows if r.event == OPENED),
        "booking_rate": round(100.0 * booked / opened, 1) if opened else None,
        "by_hour": [{"hour": h, "opened": by_hour[OPENED][h], "booked": by_hour[BOOKED][h]} for h in range(6, 23)],
        "by_weekday": [{"day": d, "opened": by_day[OPENED][d], "booked": by_day[BOOKED][d]} for d in week],
        "recent": [{"name": names.get(r.lead_id, "Family"), "lead_id": r.lead_id, "event": r.event,
                    "detail": r.detail, "at": r.created_at.strftime("%Y-%m-%dT%H:%M:%SZ")} for r in rows[:40]],
    }
