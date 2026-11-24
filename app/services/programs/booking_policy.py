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

THE CUSTOMER'S TIME COMES FIRST (owner, Nov 24 - Restland): "If a customer
says 10 o'clock, we book 10 o'clock. I don't care if you double book, triple
book." A program with allow_overlap (Restland) never loses a time because
another appointment, its gap or the connected calendar already holds it, and
a time the family or a person ASKS for (any_time) can be any time 7:00 AM -
8:00 PM Central, any day, up to a year out, as long as it is not in the past.
Offers (the booking page, the AI's two suggested times) still use the five
set starts and the two-week window.

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
# Days the owner opened by hand that the rules would otherwise close (a
# Sunday, or past the booking window). Owner, Nov 23: "Sun Dec 13 is
# available". More can be added with OWNER_OPEN_DATES=YYYY-MM-DD,...
OWNER_OPEN_BUILT_IN = {"2026-12-13"}


def owner_open(day: date_cls) -> bool:
    import os
    extra = {x.strip() for x in (os.environ.get("OWNER_OPEN_DATES") or "").split(",") if x.strip()}
    return day.isoformat() in (OWNER_OPEN_BUILT_IN | extra)
SAME_DAY_NOTICE = timedelta(hours=2)
LEGACY_MINUTES = 60            # a booking with no recorded length
# A time ASKED for in a program that books on top of other appointments.
ASKED_FIRST, ASKED_LAST = (7, 0), (20, 0)
ASKED_HORIZON_DAYS = 366

OFFICE, HOME, REMOTE = "office", "home", "remote"
# Owner, Nov 22: a REMOTE call (phone or video) at a real date and time, ONLY
# when the family asks for one as the only way they can meet - never offered
# ("on_request": not on the family's booking page, never suggested by the
# AI). The person assigned sets the call up and sends the details. Same
# times, same calendar, counted as an appointment like the other two.
REMOTE_OPTION = {"key": REMOTE, "label": "By phone or video call",
                 "appt_label": "Family File Review by phone or video call",
                 "note": "Only when the family asked for a call", "on_request": True}

RESTLAND_ORG_ID = "01fed629-9a95-412b-895a-c8b09f37a98c"
RESTLAND_ADDRESS = "13005 Greenville Ave, Dallas, TX 75243"
RESTLAND_POLICY = {
    "version": 1,
    "allow_overlap": True,
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
        REMOTE_OPTION,
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


# Programs whose customers' times are always booked, even on top of another
# appointment (owner, Nov 24). Restland's stored rules may predate the flag.
OVERLAP_ORGS = {RESTLAND_ORG_ID}


def _with_org(policy: Optional[Dict], organization_id: Optional[str]) -> Optional[Dict]:
    if policy is not None and organization_id in OVERLAP_ORGS:
        policy = dict(policy, allow_overlap=True)
    return policy


def allows_overlap(policy: Optional[Dict]) -> bool:
    return bool(policy and policy.get("allow_overlap"))


def for_org(db: Session, organization_id: Optional[str]) -> Optional[Dict]:
    if not organization_id:
        return None
    from app.models.program_models import OutreachProgram
    prog = (db.query(OutreachProgram)
            .filter(OutreachProgram.organization_id == organization_id).first())
    return _with_org(parse(getattr(prog, "booking_policy_json", None)), organization_id) if prog else None


def for_program(prog) -> Optional[Dict]:
    if prog is None:
        return None
    return _with_org(parse(getattr(prog, "booking_policy_json", None)), getattr(prog, "organization_id", None))


def for_advisor(db: Session, advisor) -> Optional[Dict]:
    """The rules for a calendar owner: their organization's program rules."""
    return for_org(db, getattr(advisor, "organization_id", None))


def option(policy: Dict, key: Optional[str]) -> Optional[Dict]:
    return next((o for o in policy.get("meeting_options") or [] if o.get("key") == key), None)


def public_options(policy: Dict, staff: bool = False) -> List[Dict]:
    """What the family's page shows - never anything internal. A choice made
    only on the family's request ("on_request", the remote call) shows to
    staff, never on the family's page."""
    out = []
    for o in policy.get("meeting_options") or []:
        if o.get("on_request") and not staff:
            continue
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
    if getattr(booking, "meeting_type", None) == REMOTE:
        return "by phone or video call (we'll send you the call details)"
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
              busy_reader=None, any_time: int = 0) -> Dict:
    """Open start times on one day for one person, under these rules.
    Same answer shape as calendar_router.compute_day_slots.

    any_time (minutes, owner Nov 23 - "if the customer requested 3 o'clock,
    sure, we can do 3 o'clock"): besides the set start times, every time on
    that step between the first and the last start is a candidate - for a
    time the family or a person ASKED for. Offers still use the set starts.
    The same appointment length, gap, calendar and same-day notice apply."""
    from app.models.models import BookingLink
    now_local = now_local or _today_central()
    today = today or now_local.date()
    out = {"date": target_date.isoformat(), "advisor_id": advisor.id, "slots": [],
           "duration_minutes": policy["duration"], "gap_minutes": policy["gap_after"]}
    if target_date < today:
        return dict(out, reason="Date is in the past")
    overlap = allows_overlap(policy)
    asked = bool(any_time) and overlap      # the customer's (or a person's) own time
    opened = owner_open(target_date) or asked
    horizon = ASKED_HORIZON_DAYS if asked else HORIZON_DAYS
    if target_date > today + timedelta(days=horizon) and not owner_open(target_date):
        return dict(out, reason="Date is more than %d days out" % horizon)
    if str(target_date.weekday()) not in policy["days"] and not opened:
        return dict(out, reason="No appointments on this day")
    from app.services import no_contact_days as _ncd
    if _ncd.quiet_on(target_date) and not asked:
        return dict(out, reason="Closed that day (holiday)")

    dur, gap = policy["duration"], policy["gap_after"]
    candidates = [datetime(target_date.year, target_date.month, target_date.day, h, m)
                  for h, m in policy["starts"]]
    if asked:
        day0 = datetime(target_date.year, target_date.month, target_date.day)
        candidates = sorted(set(candidates) | {day0.replace(hour=ASKED_FIRST[0], minute=ASKED_FIRST[1]),
                                               day0.replace(hour=ASKED_LAST[0], minute=ASKED_LAST[1])})
    if any_time and candidates:
        first, last = min(candidates), max(candidates)
        t, extra = first, set(candidates)
        while t <= last:
            extra.add(t)
            t += timedelta(minutes=int(any_time))
        candidates = sorted(extra)

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

    if overlap:
        # Double and triple booking are fine (owner, Nov 24): no other
        # appointment, gap or calendar entry takes a time away.
        free = list(candidates)
        if target_date == now_local.date():
            cutoff = now_local if asked else now_local + SAME_DAY_NOTICE
            free = [s for s in free if s >= cutoff]
        out["slots"] = [s.strftime("%Y-%m-%dT%H:%M:00") for s in free]
        if not free:
            out["reason"] = "There are no remaining times on that day."
        return out

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
            busy_reader=None, any_time: int = 5) -> bool:
    """Is this exact time free? Any requested time counts (15-minute steps
    between the first and last start), not only the set starts."""
    res = day_slots(db, advisor, slot.date(), policy, exclude_booking_id=exclude_booking_id,
                    busy_reader=busy_reader, any_time=any_time)
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


# ── two real openings for an outreach message (Issue #507, Nov 15) ──────────

def open_times(db: Session, advisor, policy: Dict, *, count: int = 2, days: int = 7,
               now_local: Optional[datetime] = None, busy_reader=None) -> List[datetime]:
    """The next `count` start times this person really has open (calendar
    read, failing closed), preferring different days. [] when the calendar
    cannot be read or nothing is open in the next `days` days - the message
    then asks what day works instead of naming times."""
    now_local = now_local or _today_central()
    picked: List[datetime] = []
    spare: List[datetime] = []
    for i in range(days + 1):
        d = (now_local + timedelta(days=i)).date()
        res = day_slots(db, advisor, d, policy, now_local=now_local, busy_reader=busy_reader)
        if res.get("calendar_error"):
            return []
        slots = [datetime.strptime(s, "%Y-%m-%dT%H:%M:00") for s in (res.get("slots") or [])]
        if slots:
            picked.append(slots[0])
            spare.extend(slots[1:])
        if len(picked) >= count:
            break
    out = picked[:count]
    for s in spare:
        if len(out) >= count:
            break
        out.append(s)
    return sorted(out)[:count]


def say_time(t: datetime) -> str:
    """"Tuesday, Nov 17 at 10:00 AM"."""
    return "%s at %s" % (t.strftime("%A, %b %-d"), t.strftime("%-I:%M %p"))


def _clock(t: datetime) -> str:
    return t.strftime("%-I:%M %p")


def tomorrow_offer(db: Session, advisor, policy: Dict, channel: str = "sms", *,
                   now_local: Optional[datetime] = None, busy_reader=None) -> str:
    """Owner, Nov 17 (Restland File Review): "What time tomorrow works best
    for you, 10:00 AM or 2:30 PM?" - two times really open TOMORROW (read from
    the calendar at send time, failing closed), spread across the day. A full
    tomorrow names the next day with two openings ("What time Thursday ...").
    Never a time it could not verify."""
    ask = "What day and time work best for you to come in?"
    if advisor is None or not policy:
        return ask
    now_local = now_local or _today_central()
    for i in range(1, 8):
        d = (now_local + timedelta(days=i)).date()
        res = day_slots(db, advisor, d, policy, now_local=now_local, busy_reader=busy_reader)
        if res.get("calendar_error"):
            return ask
        slots = sorted(datetime.strptime(s, "%Y-%m-%dT%H:%M:00") for s in (res.get("slots") or []))
        if len(slots) < 2:
            continue
        first = slots[0]
        later = next((s for s in slots[1:] if s - first >= timedelta(hours=3)), slots[-1])
        day = "tomorrow" if i == 1 else d.strftime("%A")
        return "What time %s works best for you, %s or %s?" % (day, _clock(first), _clock(later))
    return ask


def time_offer(db: Session, advisor, policy: Dict, channel: str = "sms", **kw) -> str:
    """One sentence offering two verified times, or asking for a day."""
    times = open_times(db, advisor, policy, **kw) if advisor is not None and policy else []
    if len(times) >= 2:
        return ("I have %s or %s open at our office. Would either of those work?"
                % (say_time(times[0]), say_time(times[1])))
    return "What day this week works for you to come by?"
