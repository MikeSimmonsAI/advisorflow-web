"""SCI APPOINTMENTS for the appointment setter: real times, real bookings.

Two rules carry everything here:

  * ONLY OFFER WHAT THE CALENDAR RETURNED. Times come from the same engine the
    family's booking page uses (calendar_router.compute_day_slots): office
    hours, the advisor's booked visits, their connected calendar (fails closed
    - an unreadable calendar offers nothing), the same-day buffer, 60-minute
    visits on the hour.
  * NEVER REPORT A BOOKING THE CALENDAR HAS NOT CONFIRMED. `book` re-checks
    the time is still free at the moment of booking, books through the same
    confirmation path the booking page uses (calendar event, family
    confirmation text, advisor notice), then reads the BookingLink back. Only
    a link that is booked at exactly that time is "confirmed".

Location separation is unchanged: the booking is the family's own link, for
the program's booking user, and the confirmation is signed by the family's
own location (survey_identity.location_identity).
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

CENTRAL = "America/Chicago"
PHONE, VISIT = "phone", "visit"
LABELS = {PHONE: "Phone Appointment", VISIT: "Planning Visit"}
# One length everywhere: what the AI says, the times offered, the booking
# record and the calendar event.
MINUTES = {PHONE: 30, VISIT: 60}


def minutes_for(kind) -> int:
    return MINUTES.get(kind or VISIT, MINUTES[VISIT])


def _today_central():
    from datetime import timezone
    from zoneinfo import ZoneInfo
    return datetime.now(timezone.utc).astimezone(ZoneInfo(CENTRAL))


def _staging() -> bool:
    import os
    return (os.environ.get("APP_ENV") or "").lower() == "staging"


def program_advisor(db: Session, prog):
    """Whose calendar a program family books onto: the program's booking user,
    else its primary contact. None = the program takes no bookings."""
    from app.models.models import User
    uid = getattr(prog, "booking_user_id", None) or getattr(prog, "primary_contact_user_id", None)
    return db.query(User).filter(User.id == uid).first() if uid else None


def available_slots(db: Session, prog, *, want: int = 2, days: int = 14,
                    now_central: Optional[datetime] = None, kind: str = VISIT) -> Tuple[List[datetime], Optional[str]]:
    """Up to `want` genuine open times (naive Central), spread over different
    days when possible. ([], reason) when nothing can honestly be offered."""
    from app.routers.calendar_router import compute_day_slots
    advisor = program_advisor(db, prog)
    if advisor is None:
        return [], "no booking calendar is set for the program"
    now_c = now_central or _today_central()
    today = now_c.date()
    out: List[datetime] = []
    seen_days = set()
    spare: List[datetime] = []
    for i in range(days + 1):
        d = today + timedelta(days=i)
        if d.weekday() >= 5:
            continue
        res = compute_day_slots(db, advisor, d, program_visit=True, today=today, minutes=minutes_for(kind))
        if res.get("calendar_error"):
            return [], "the calendar cannot be read right now"
        slots = [datetime.fromisoformat(x) for x in res.get("slots") or []]
        if not slots:
            continue
        # one morning-ish and one afternoon-ish option across two days
        pick = slots[0] if not out or out[-1].hour >= 12 else (
            next((s for s in slots if s.hour >= 13), slots[0]))
        if d not in seen_days:
            out.append(pick)
            seen_days.add(d)
        spare.extend(s for s in slots if s != pick)
        if len(out) >= want:
            break
    for s in spare:
        if len(out) >= want:
            break
        if s not in out:
            out.append(s)
    out.sort()
    return out[:want], (None if out else "no open times in the next two weeks")


def slots_on_day(db: Session, prog, day, want: int = 2, kind: str = VISIT) -> Tuple[List[datetime], Optional[str]]:
    """Open times on one day the family asked for (morning and afternoon if
    both exist). ([], reason) when that day has none."""
    from app.routers.calendar_router import compute_day_slots
    advisor = program_advisor(db, prog)
    if advisor is None or day.weekday() >= 5:
        return [], "no times that day"
    res = compute_day_slots(db, advisor, day, program_visit=True, today=_today_central().date(),
                            minutes=minutes_for(kind))
    if res.get("calendar_error"):
        return [], "the calendar cannot be read right now"
    slots = [datetime.fromisoformat(x) for x in res.get("slots") or []]
    if not slots:
        return [], "no open times that day"
    am = [s for s in slots if s.hour < 12]
    pm = [s for s in slots if s.hour >= 12]
    pick = ([am[0]] if am else []) + ([pm[0]] if pm else [])
    for s in slots:
        if len(pick) >= want:
            break
        if s not in pick:
            pick.append(s)
    return sorted(pick[:want]), None


def describe(slot: datetime) -> str:
    return slot.strftime("%A, %B %-d at %-I:%M %p").replace(":00 ", " ")


def still_free(db: Session, prog, slot: datetime, kind: str = VISIT) -> bool:
    from app.routers.calendar_router import compute_day_slots
    advisor = program_advisor(db, prog)
    if advisor is None:
        return False
    res = compute_day_slots(db, advisor, slot.date(), program_visit=True, today=_today_central().date(),
                            minutes=minutes_for(kind))
    if res.get("calendar_error"):
        return False
    return slot.strftime("%Y-%m-%dT%H:%M:00") in set(res.get("slots") or [])


class _InternalRequest:
    """The booking page's own confirmation call, made in-process."""

    def __init__(self, payload: Dict):
        self._payload = payload

    async def json(self):
        return self._payload


def book(db: Session, prog, lead, slot: datetime, kind: str, *, family_text: bool = True) -> Dict:
    """Book `slot` for this family, through the booking page's own path.
    {"ok": True, "status": "confirmed", "booking_id", "booked_time"} only when
    the BookingLink reads back booked at that exact time."""
    import asyncio
    from app.models.models import BookingLink, User
    from app.services.sms_service import get_or_create_booking_link
    advisor = program_advisor(db, prog)
    if advisor is None:
        return {"ok": False, "status": "no_calendar", "reason": "no booking calendar is set"}
    from app.services.sci_staging_demo import is_demo_lead
    from app.services.test_records import is_owner_test_lead
    owner_test = is_owner_test_lead(lead)
    if (getattr(lead, "is_test", False) and not _staging() and not owner_test) or is_demo_lead(lead):
        # A SYNTHETIC contact in production - and a made-up staging demo family
    # anywhere - never writes to a real calendar:
        # the time is re-checked against the live calendar and reported as a
        # simulated booking - never as a confirmed one.
        free = still_free(db, prog, slot, kind)
        return {"ok": free, "status": "simulated" if free else "taken", "simulated": True,
                "booked_time": slot.isoformat(),
                "reason": None if free else "that time is no longer open"}
    # Serialise bookings on this calendar (Postgres row lock; a no-op on SQLite)
    try:
        db.query(User).filter(User.id == advisor.id).with_for_update().first()
    except Exception:                                    # noqa: BLE001
        pass
    # An existing booking for this family is never doubled.
    existing = (db.query(BookingLink)
                .filter(BookingLink.lead_id == lead.id, BookingLink.user_id == advisor.id,
                        BookingLink.status.in_(("booked", "confirmed"))).first())
    if existing is not None and existing.booked_time:
        same = existing.booked_time.replace(tzinfo=None) == slot
        return {"ok": same, "status": "confirmed" if same else "already_booked",
                "booking_id": existing.id, "booked_time": existing.booked_time.isoformat()}
    if not still_free(db, prog, slot, kind):
        return {"ok": False, "status": "taken", "reason": "that time is no longer open"}
    link = get_or_create_booking_link(db, lead, advisor)
    link.booked_time = slot
    link.appt_duration = minutes_for(kind)
    db.flush()
    from app.routers.calendar_router import booking_confirmed_webhook
    payload = {"booking_token": link.token, "slot_display": slot.strftime("%Y-%m-%dT%H:%M:00"),
               "appt_label": ("TEST - owner live test, safe to delete" if owner_test
                              else LABELS.get(kind, LABELS[VISIT])),
               "lead_name": ("%s %s" % (lead.first_name or "", lead.last_name or "")).strip(),
               "lead_phone": lead.phone or "", "duration_minutes": minutes_for(kind),
               # Booked from an email conversation: the confirmation goes back by
               # email to the writer, not as a text to the record's phone.
               "skip_family_text": not family_text}
    try:
        try:
            asyncio.get_running_loop()
            running = True
        except RuntimeError:
            running = False
        if running:                                      # pragma: no cover - called from sync code
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(1) as ex:
                ex.submit(lambda: asyncio.run(booking_confirmed_webhook(_InternalRequest(payload), db))).result()
        else:
            asyncio.run(booking_confirmed_webhook(_InternalRequest(payload), db))
    except Exception as exc:                             # noqa: BLE001
        log.exception("setter booking failed for lead %s", lead.id)
        db.rollback()
        return {"ok": False, "status": "error", "reason": str(exc)[:200]}
    db.refresh(link)
    bt = link.booked_time.replace(tzinfo=None) if link.booked_time else None
    if (link.status or "") in ("booked", "confirmed") and bt == slot:
        return {"ok": True, "status": "confirmed", "booking_id": link.id, "booked_time": bt.isoformat()}
    return {"ok": False, "status": "unconfirmed",
            "reason": "the calendar did not confirm (status %s)" % link.status}


def notify_team_booked(db: Session, prog, lead, slot: datetime, kind: str) -> None:
    """A booking is a family response: the whole team hears about it (plain
    wording carriers deliver; the details are in the email and the app)."""
    from app.models.program_models import ProgramAlert
    from app.services.programs import responses as R
    title = R.alert_title(prog)
    sms = "%s: A family booked an appointment. Please check your SCI Appointments." % title
    who = ("%s %s" % (lead.first_name or "", lead.last_name or "")).strip().title() or "A family"
    email = "%s booked a %s for %s." % (who, LABELS.get(kind, "visit").lower(), describe(slot))
    targets = [("primary", "sms", prog.alert_phone), ("primary", "email", prog.alert_email)]
    for m in R._managers(prog):
        if (m.get("role") or "").lower() == "team":
            if m.get("phone"):
                targets.append(("team", "sms", m["phone"]))
            if m.get("email"):
                targets.append(("team", "email", m["email"]))
    from app.services.sci_staging_demo import is_demo_lead
    test = bool(getattr(lead, "is_test", False)) or is_demo_lead(lead)
    if test:
        targets = []                    # a synthetic contact never pages the team
    for aud, ch, to in targets:
        ok, why = R.staff_alert_gate(to, ch, aud, bool(prog.staff_sms_alerts_enabled))
        rc: Dict = {}
        text = ("TEST - " + sms) if (test and ch == "sms") else (sms if ch == "sms" else
                                                                  ("TEST ONLY - " if test else "") + email)
        if ok:
            ok, why = R._deliver_staff_alert(db, prog, ch, to, text, kind="booked", receipt=rc)
        db.add(R.sms_alert_record(ProgramAlert(organization_id=prog.organization_id, kind="booked",
                                               audience=aud, channel=ch, recipient=to, delivered=bool(ok),
                                               reason=why, message=text[:2000]), rc))


def offered(conv) -> List[datetime]:
    try:
        return [datetime.fromisoformat(x) for x in json.loads(conv.offered_slots or "[]")]
    except (ValueError, TypeError):
        return []
