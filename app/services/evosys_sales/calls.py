"""EvoSys Sales 'Calls': Business Growth Calls on the existing scheduler
(owner, 2026-11-20, design pages 11-18).

Everything books through `sales_scheduling_router.create_appointment`, so the
real double-booking checks (our own calendar + the host's connected calendar),
the Zoom room and the calendar invitation all come from the code that already
runs every other booking. This module only adds what EvoSys Sales needs on
top: hosts, a day view, an availability check before booking, "book without
sending", and a text confirmation from the team number.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.models import User
from app.models.sales_models import Membership, Opportunity

log = logging.getLogger(__name__)
TZ = "America/Chicago"
TITLE = "Business Growth Call"


def hosts(db: Session, bso: str) -> List[Dict]:
    """People who can host: active members of the EvoSys Sales team."""
    rows = (db.query(User).join(Membership, Membership.user_id == User.id)
            .filter(Membership.scope_id == bso, Membership.is_active.is_(True)).all())
    seen, out = set(), []
    for u in rows:
        if u.id in seen:
            continue
        seen.add(u.id)
        out.append({"id": u.id, "name": u.full_name or u.email})
    return sorted(out, key=lambda x: x["name"] or "")


def default_host_id(db: Session, bso: str, user: User) -> str:
    """The person whose calendar the booking page books (lead owner), else the
    signed-in person."""
    o = (db.query(Opportunity).filter(Opportunity.brand_sales_org_id == bso, Opportunity.owner_user_id.isnot(None))
         .order_by(Opportunity.created_at.desc()).first())
    return o.owner_user_id if o is not None else user.id


def check(db: Session, host: User, starts_at: datetime, minutes: int,
          exclude_appointment_id: Optional[str] = None) -> Dict:
    """Is the host free then? Reads our calendar AND the host's connected
    calendar (fresh), and says whether it's inside the host's set hours."""
    from app.services import availability as av
    from app.services import external_busy as extbusy
    from app.routers.sales_scheduling_router import _refresh_external
    ends_at = starts_at + timedelta(minutes=minutes)
    out = {"available": True, "conflicts": [], "outside_hours": False, "calendar_readable": True}
    if starts_at <= datetime.utcnow():
        return dict(out, available=False, conflicts=["That time is already in the past."])
    for c in av.find_conflicts(db, [host.id], starts_at, ends_at, exclude_appointment_id=exclude_appointment_id):
        out["conflicts"].append("Already booked: %s" % (c.get("title") or "another call"))
    try:
        vis = _refresh_external(db, [host], starts_at - timedelta(hours=1), ends_at + timedelta(hours=1), force=True)
        out["calendar_readable"] = not (vis or {}).get("unreadable")
        if extbusy.external_conflicts(db, [host.id], starts_at, ends_at):
            out["conflicts"].append("Busy on the connected calendar")
    except Exception:                                                # noqa: BLE001
        out["calendar_readable"] = False
    prof = av.get_or_create_profile(db, host)
    local = av.utc_to_local(starts_at, prof.timezone or TZ)
    m0 = local.hour * 60 + local.minute
    from app.models.scheduling_models import AvailabilityWindow
    wins = db.query(AvailabilityWindow).filter(AvailabilityWindow.profile_id == prof.id,
                                               AvailabilityWindow.day_of_week == local.weekday()).all()
    if wins and not any(w.start_minute <= m0 and m0 + minutes <= w.end_minute for w in wins):
        out["outside_hours"] = True
    out["available"] = not out["conflicts"]
    return out


def day(db: Session, bso: str, host: User, d: date) -> Dict:
    """One day for the host, in Central Time: booked calls, open times (the
    same rules as the booking page) and calendar conflicts."""
    from app.models.scheduling_models import SalesAppointment
    from app.services import availability as av
    from app.services import external_busy as extbusy
    from app.routers.sales_scheduling_router import FindTimeIn, find_team_time
    start, end = av.local_to_utc(d, 0, TZ), av.local_to_utc(d + timedelta(days=1), 0, TZ)
    appts = (db.query(SalesAppointment).filter(SalesAppointment.brand_sales_org_id == bso,
                                               SalesAppointment.status != "cancelled",
                                               SalesAppointment.starts_at >= start, SalesAppointment.starts_at < end)
             .order_by(SalesAppointment.starts_at).all())
    items = [{"kind": "call", "id": a.id, "starts_at": a.starts_at.isoformat() + "Z",
              "ends_at": a.ends_at.isoformat() + "Z", "title": a.prospect_company or a.title,
              "lead_id": a.opportunity_id} for a in appts]
    try:
        r = find_team_time(FindTimeIn(duration_minutes=30, required_user_ids=[host.id], date_from=d, date_to=d,
                                      brand_sales_org_id=bso), user=host, db=db)
        for s in (r or {}).get("slots") or []:
            st = str(s.get("starts_at") or s.get("start") or "")
            if st:
                items.append({"kind": "open", "starts_at": st if st.endswith("Z") or "+" in st else st + "Z"})
    except Exception as e:                                           # noqa: BLE001
        log.info("[evosys calls] no open times for %s: %s", d, getattr(e, "detail", e))
    try:
        for b in extbusy.external_busy_intervals(db, host.id, start, end):
            items.append({"kind": "busy", "starts_at": b[0].isoformat() + "Z", "ends_at": b[1].isoformat() + "Z"})
    except Exception:                                                # noqa: BLE001
        pass
    items.sort(key=lambda x: x["starts_at"])
    return {"date": d.isoformat(), "timezone": TZ, "host": {"id": host.id, "name": host.full_name or host.email},
            "items": items}


def book(db: Session, bso: str, user: User, *, host: User, starts_at: datetime, minutes: int, title: str,
         timezone: str, notes: Optional[str], opp: Optional[Opportunity], guest: Dict, send: bool) -> Dict:
    """Book through the real scheduler; optionally send the invitation (email
    via the calendar invite, text via the team number with the Zoom link)."""
    from app.routers.sales_scheduling_router import BookIn, create_appointment
    from app.models.scheduling_models import SalesAppointment
    body = BookIn(starts_at=starts_at, duration_minutes=minutes, opportunity_id=opp.id if opp else None,
                  brand_sales_org_id=bso, title=title or TITLE, timezone=timezone or TZ,
                  required_user_ids=[host.id], meeting_provider="zoom", notes=notes or None,
                  prospect_name=guest.get("name") or (opp.contact_name if opp else None),
                  prospect_email=guest.get("email") or (opp.email if opp else None),
                  prospect_phone=guest.get("phone") or (opp.phone if opp else None),
                  prospect_timezone=timezone or TZ, send_invite=bool(send))
    made = create_appointment(body, user=user, db=db)
    appt_id = made.get("id") if isinstance(made, dict) else getattr(made, "id", None)
    a = db.get(SalesAppointment, appt_id)
    texted = None
    if send and opp is not None and a is not None:
        from app.services.evosys_sales import booking_page as BP
        BP._after_booking(db, opp, a.starts_at, a.id)       # text from the team number + owner alert + stop follow-up
        texted = True
    return {"id": appt_id, "texted": texted, "invite_emailed": bool(send and a is not None and a.prospect_email)}


def replays(db: Session, appt) -> Dict:
    """Recordings exist only if the provider returns one; we never record."""
    from app.services import appointment_meetings as apmeet
    row = apmeet.get_meeting_row(db, appt.id)
    return {"items": [], "reason": ("Recording is off for Business Growth Calls, so there is no replay."
                                    if row is not None else "No Zoom meeting on this call.")}
