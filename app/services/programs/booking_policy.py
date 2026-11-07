"""A PROGRAM'S OWN APPOINTMENT RULES (Issue #507, Restland).

Most programs use the platform's usual program visit: weekdays, 60 minutes,
on the hour (calendar_router.compute_day_slots). A program can carry its own
rules in OutreachProgram.booking_policy_json. When it does, EVERY path that
offers or books a time for that program goes through this module:

  * the family's booking page (/calendar/booking/{token}, /calendar/slots,
    /calendar/booking-confirmed),
  * staff "pick a time" and the appointment setter (programs/appointments),
  * reassigning a booked appointment to another team member.

so a time the page shows, a time the AI offers and a time a person books are
the same time, and every one is re-checked at the moment of booking.

The rules (Restland, owner decision Nov 7 2026):

    days        "0123456"   every day (0 = Monday ... 6 = Sunday)
    starts      10:00, 11:30, 13:00, 14:30, 16:00   - the ONLY start times
    duration    45 minutes
    gap_after   45 minutes after every appointment (travel / notes)
    timezone    America/Chicago - local wall time, so daylight saving moves
                nothing: 10:00 is 10:00 in CST and in CDT

16:00 is the last START, not a closing time: it ends at 16:45.

AN APPOINTMENT HOLDS [start, start + duration + gap). Two appointments may not
overlap including their gaps, whatever kind they are (office or home), so one
person is never double-booked and always has the gap after a visit. A booking
made under older rules holds its own recorded length (60 when unknown) plus
this gap. The connected calendar is read for the same held span and FAILS
CLOSED: unreadable means no times.

Office or home: both use the same times and the same calendar. A home visit
needs the family's full address (validated here, never guessed). There is no
mileage or service-area rule - the owner handles travel and staffing.
"""
import json
import logging
import re
from datetime import date as date_cls, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

TZ = "America/Chicago"
HORIZON_DAYS = 14              # same as every program booking page
SAME_DAY_NOTICE = timedelta(hours=2)
LEGACY_MINUTES = 60            # a booking with no recorded length

OFFICE, HOME = "office", "home"

RESTLAND_ORG_ID = "01fed629-9a95-412b-895a-c8b09f37a98c"
RESTLAND_ADDRESS = "13005 Greenville Ave, Dallas, TX 75243"
RESTLAND_POLICY = {
    "version": 1,
    "days": "0123456",
    "starts": ["10:00", "11:30", "13:00", "14:30", "16:00"],
    "duration": 45,
    "gap_after": 45,
    "timezone": TZ,
    "label": "Family File Review",
    "host_name": "Mike Simmons",
    "host_title": "Family Service Advisor",
    "meeting_options": [
        {"key": OFFICE, "label": "At Restland", "appt_label": "Family File Review at Restland",
         "note": RESTLAND_ADDRESS, "address": RESTLAND_ADDRESS},
        {"key": HOME, "label": "At your home", "appt_label": "Family File Review at your home",
         "note": "Mike comes to you, if that's more convenient", "needs_address": True},
    ],
}


# ── reading the rules ───────────────────────────────────────────────────────

def parse(raw) -> Optional[Dict]:
    """The policy dict, or None for anything incomplete (fail to the usual rules)."""
    if not raw:
        return None
    try:
        p = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (ValueError, TypeError):
        log.warning("booking policy: unreadable JSON - the usual rules apply")
        return None
    try:
        starts = [_hhmm(s) for s in p.get("starts") or []]
        dur, gap = int(p.get("duration") or 0), int(p.get("gap_after") or 0)
    except (TypeError, ValueError):
        return None
    if not starts or not (5 <= dur <= 240) or not (0 <= gap <= 240):
        return None
    days = "".join(ch for ch in str(p.get("days") or "") if ch in "0123456")
    if not days:
        return None
    return dict(p, starts=sorted(set(starts)), duration=dur, gap_after=gap, days=days)


def _hhmm(s) -> Tuple[int, int]:
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", str(s))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ValueError("bad time %r" % s)
    return int(m.group(1)), int(m.group(2))


def for_org(db: Session, organization_id: Optional[str]) -> Optional[Dict]:
    if not organization_id:
        return None
    from app.models.program_models import OutreachProgram
    prog = (db.query(OutreachProgram)
            .filter(OutreachProgram.organization_id == organization_id).first())
    return parse(getattr(prog, "booking_policy_json", None)) if prog else None


def for_program(prog) -> Optional[Dict]:
    return parse(getattr(prog, "booking_policy_json", None)) if prog is not None else None


def for_advisor(db: Session, advisor) -> Optional[Dict]:
    """The rules for a calendar owner: their organization's program rules."""
    return for_org(db, getattr(advisor, "organization_id", None))


def option(policy: Dict, key: Optional[str]) -> Optional[Dict]:
    return next((o for o in policy.get("meeting_options") or [] if o.get("key") == key), None)


def public_options(policy: Dict) -> List[Dict]:
    """What the family's page shows - never anything internal."""
    out = []
    for o in policy.get("meeting_options") or []:
        out.append({"key": o["key"], "label": o.get("label") or o["key"],
                    "appt_label": o.get("appt_label") or policy.get("label") or "Appointment",
                    "note": o.get("note") or "", "needs_address": bool(o.get("needs_address"))})
    return out


def js_days(policy: Dict) -> List[int]:
    """Bookable weekdays for the browser (JS getDay: 0 = Sunday)."""
    return sorted({(int(d) + 1) % 7 for d in policy["days"]})


# ── addresses ───────────────────────────────────────────────────────────────

_STATE = re.compile(r"\b([A-Z]{2})\b")
_ZIP = re.compile(r"\b(\d{5})(?:-\d{4})?\b")


def clean_address(raw: Optional[str]) -> Optional[str]:
    """A complete US street address, tidied, or None. Complete = a street
    number and name, a city, a two-letter state and a 5-digit ZIP."""
    a = re.sub(r"\s+", " ", (raw or "").replace("\n", ", ")).strip(" ,")
    if len(a) < 12 or len(a) > 300:
        return None
    if not re.match(r"^\d+[A-Za-z]?\s+\S+", a):            # "1234 Elm St"
        return None
    if not _ZIP.search(a) or not _STATE.search(a.upper()):
        return None
    if a.count(",") < 1:                                     # street, city ...
        return None
    return a


def meeting_choice(policy: Dict, key: Optional[str], address: Optional[str]) -> Dict:
    """Validate the family's choice. {"key","appt_label","address"} or ValueError."""
    opts = policy.get("meeting_options") or []
    if not opts:
        return {"key": None, "appt_label": policy.get("label") or "Appointment", "address": None}
    o = option(policy, key) or (opts[0] if key in (None, "") and len(opts) == 1 else None)
    if o is None:
        raise ValueError("Choose where you'd like to meet.")
    if o.get("needs_address"):
        clean = clean_address(address)
        if clean is None:
            raise ValueError("Please enter your full home address: street, city, state and ZIP.")
        return {"key": o["key"], "appt_label": o.get("appt_label") or policy.get("label"), "address": clean}
    return {"key": o["key"], "appt_label": o.get("appt_label") or policy.get("label"),
            "address": o.get("address")}


def place_text(booking, policy: Optional[Dict] = None) -> str:
    """Where the visit happens, for confirmations, reminders and the calendar."""
    addr = (getattr(booking, "visit_address", None) or "").strip()
    if getattr(booking, "meeting_type", None) == HOME:
        return "at your home (%s)" % addr if addr else "at your home"
    if addr:
        return "at Restland, %s" % addr if addr == RESTLAND_ADDRESS else "at %s" % addr
    return ""


# ── the slot engine for a policy ────────────────────────────────────────────

def _today_central() -> datetime:
    from datetime import timezone
    from zoneinfo import ZoneInfo
    return datetime.now(timezone.utc).astimezone(ZoneInfo(TZ)).replace(tzinfo=None)


def _held(start: datetime, minutes: int, gap: int) -> Tuple[datetime, datetime]:
    return start, start + timedelta(minutes=minutes + gap)


def day_slots(db: Session, advisor, target_date: date_cls, policy: Dict, *, today: Optional[date_cls] = None,
              now_local: Optional[datetime] = None, exclude_booking_id: Optional[str] = None,
              busy_reader=None) -> Dict:
    """Open start times on one day for one person, under these rules.
    Same answer shape as calendar_router.compute_day_slots."""
    from app.models.models import BookingLink
    now_local = now_local or _today_central()
    today = today or now_local.date()
    out = {"date": target_date.isoformat(), "advisor_id": advisor.id, "slots": [],
           "duration_minutes": policy["duration"], "gap_minutes": policy["gap_after"]}
    if target_date < today:
        return dict(out, reason="Date is in the past")
    if target_date > today + timedelta(days=HORIZON_DAYS):
        return dict(out, reason="Date is more than %d days out" % HORIZON_DAYS)
    if str(target_date.weekday()) not in policy["days"]:
        return dict(out, reason="No appointments on this day")

    dur, gap = policy["duration"], policy["gap_after"]
    candidates = [datetime(target_date.year, target_date.month, target_date.day, h, m)
                  for h, m in policy["starts"]]

    # Every booked/confirmed appointment of this person that could touch the day.
    lo = datetime(target_date.year, target_date.month, target_date.day) - timedelta(days=1)
    hi = lo + timedelta(days=3)
    q = (db.query(BookingLink)
         .filter(BookingLink.user_id == advisor.id, BookingLink.status.in_(("booked", "confirmed")),
                 BookingLink.booked_time >= lo, BookingLink.booked_time < hi))
    if exclude_booking_id:
        q = q.filter(BookingLink.id != exclude_booking_id)
    held = []
    for b in q.all():
        bt = b.booked_time.replace(tzinfo=None) if getattr(b.booked_time, "tzinfo", None) else b.booked_time
        try:
            mins = int(getattr(b, "appt_duration", None) or LEGACY_MINUTES)
        except (TypeError, ValueError):
            mins = LEGACY_MINUTES
        held.append(_held(bt, mins, gap))

    def clashes(s: datetime) -> bool:
        s0, s1 = _held(s, dur, gap)
        return any(s0 < e and b0 < s1 for b0, e in held)

    free = [s for s in candidates if not clashes(s)]

    # The connected calendar, read once for the day, failing closed.
    if busy_reader is None:
        from app.routers.calendar_router import _booking_window_busy as busy_reader
    busy, err = busy_reader(db, advisor, target_date)
    if err:
        log.warning("booking policy: calendar unreadable for %s (%s)", advisor.id, err)
        return dict(out, reason="Calendar is temporarily unavailable. Please try again shortly.",
                    calendar_error=err)
    if busy:
        free = [s for s in free
                if not any(s < be and bs < _held(s, dur, gap)[1] for bs, be in busy)]

    if target_date == now_local.date():
        cutoff = now_local + SAME_DAY_NOTICE
        free = [s for s in free if s >= cutoff]
    out["slots"] = [s.strftime("%Y-%m-%dT%H:%M:00") for s in free]
    if not free:
        out["reason"] = "There are no remaining times on that day."
    return out


def is_open(db: Session, advisor, slot: datetime, policy: Dict, *, exclude_booking_id: Optional[str] = None,
            busy_reader=None) -> bool:
    res = day_slots(db, advisor, slot.date(), policy, exclude_booking_id=exclude_booking_id,
                    busy_reader=busy_reader)
    return not res.get("calendar_error") and slot.strftime("%Y-%m-%dT%H:%M:00") in set(res.get("slots") or [])


# ── reassignment (staff only) ───────────────────────────────────────────────

def reassign(db: Session, prog, booking, new_user, actor, *, busy_reader=None) -> Dict:
    """Move a booked appointment to another team member of the SAME program.
    Keeps the time, place, family and history; refuses when the new person is
    not free (their bookings + gap, their calendar). Audited. The family is
    not messaged - who goes is an internal decision."""
    from app.models.models import Lead, User
    from app.routers.audit_log_router import log_action
    lead = db.query(Lead).filter(Lead.id == booking.lead_id).first()
    if lead is None or lead.organization_id != prog.organization_id:
        raise PermissionError("not this program's appointment")
    if (booking.status or "") not in ("booked", "confirmed") or booking.booked_time is None:
        raise ValueError("Only a booked appointment can be reassigned.")
    if new_user.id == booking.user_id:
        return {"ok": True, "unchanged": True, "user_id": new_user.id}
    policy = for_program(prog)
    slot = booking.booked_time.replace(tzinfo=None) if getattr(booking.booked_time, "tzinfo", None) \
        else booking.booked_time
    # Serialise on the new person's calendar (Postgres row lock; no-op on SQLite).
    try:
        db.query(User).filter(User.id == new_user.id).with_for_update().first()
    except Exception:                                    # noqa: BLE001
        pass
    if policy is not None:
        if not is_open(db, new_user, slot, dict(policy, days="0123456"), exclude_booking_id=booking.id,
                       busy_reader=busy_reader) and not _open_off_grid(db, new_user, booking, policy, busy_reader):
            raise ValueError("%s isn't free at that time." % (new_user.full_name or "That person"))
    before = {"user_id": booking.user_id}
    booking.user_id = new_user.id
    log_action(db, prog.organization_id, getattr(actor, "id", None), action="program.appointment_reassign",
               target_type="booking_link", target_id=booking.id, before=before,
               after={"user_id": new_user.id, "meeting_type": getattr(booking, "meeting_type", None)},
               commit=False)
    db.commit()
    return {"ok": True, "user_id": new_user.id, "booking_id": booking.id}


def _open_off_grid(db, user, booking, policy, busy_reader) -> bool:
    """A booking made before these rules may sit off the five start times; it
    can still move if the new person has the whole held span free."""
    from app.models.models import BookingLink
    slot = booking.booked_time.replace(tzinfo=None) if getattr(booking.booked_time, "tzinfo", None) \
        else booking.booked_time
    if slot.strftime("%H:%M") in {"%02d:%02d" % hm for hm in policy["starts"]}:
        return False                                  # on the grid: day_slots already answered
    mins = int(getattr(booking, "appt_duration", None) or LEGACY_MINUTES)
    s0, s1 = _held(slot, mins, policy["gap_after"])
    for b in (db.query(BookingLink).filter(BookingLink.user_id == user.id, BookingLink.id != booking.id,
                                           BookingLink.status.in_(("booked", "confirmed")),
                                           BookingLink.booked_time >= s0 - timedelta(days=1),
                                           BookingLink.booked_time < s1 + timedelta(days=1)).all()):
        b0, b1 = _held(b.booked_time.replace(tzinfo=None), int(b.appt_duration or LEGACY_MINUTES),
                       policy["gap_after"])
        if s0 < b1 and b0 < s1:
            return False
    if busy_reader is None:
        from app.routers.calendar_router import _booking_window_busy as busy_reader
    busy, err = busy_reader(db, user, slot.date())
    if err:
        return False
    return not any(s0 < be and bs < s1 for bs, be in busy or [])
