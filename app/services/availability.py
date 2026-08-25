"""
The availability engine.

THE ONE THING THIS MODULE EXISTS TO DO
--------------------------------------
Return the times when EVERY required participant is free — an INTERSECTION.

Grok's `computeSlots` is a UNION:

    const all = q.staff.flatMap((s) => computeStaffSlots(s, q))

which surfaces a slot if ANY rep is free. That answers "book me with whoever is
available", which is a genuinely different product. Blake + Michael + Mike on
one Discovery + Demo needs the opposite operation, so the union is not reused;
`subtractIntervals` — Grok's actually-good primitive — is, ported below as
`subtract_intervals`.

    Blake:    11:00  13:00  14:00  16:00
    Michael:  10:00  11:00  14:00  15:00
    Mike:     11:00  12:00  14:00  16:00
    ------------------------------------
    Returned: 11:00  14:00

TIME MODEL
----------
Everything crossing a function boundary is a NAIVE UTC datetime, matching
`datetime.utcnow()` used throughout the codebase.

Recurring rules are stored as (day_of_week, minutes-from-midnight) in the user's
own timezone and resolved against each concrete date with `zoneinfo`. That is
what makes DST correct: 9:00am local stays 9:00am local through a transition and
only its UTC offset moves. Resolving a working window to UTC once and repeating
it would silently shift everyone's day by an hour twice a year.
"""
from datetime import datetime, timedelta, date, time
from typing import Iterable, List, Optional, Sequence, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:                                    # pragma: no cover
    ZoneInfo = None

from sqlalchemy.orm import Session

from app.models.models import User
from app.models.scheduling_models import (
    AvailabilityProfile, AvailabilityWindow, AvailabilityBlock,
    AppointmentParticipant, SalesAppointment,
    BLOCK_RECURRING, BLOCK_TIME_OFF, DEFAULT_TIMEZONE, BLOCKING_STATUSES,
)

Interval = Tuple[datetime, datetime]

# Slot grid. Openings are offered on clean boundaries rather than at arbitrary
# minutes — "11:07 AM" is a correct answer to the maths and a useless one to a
# salesperson.
DEFAULT_SLOT_STEP_MINUTES = 15


# ── timezone helpers ────────────────────────────────────────────────────────

def _zone(tzname: Optional[str]):
    if ZoneInfo is None:
        return None
    try:
        return ZoneInfo(tzname or DEFAULT_TIMEZONE)
    except Exception:
        # An unknown IANA name must not take the whole scheduler down. Fall back
        # to the team default and keep going.
        try:
            return ZoneInfo(DEFAULT_TIMEZONE)
        except Exception:
            return None


def local_to_utc(day: date, minute_of_day: int, tzname: str) -> datetime:
    """A local wall-clock time on a given local date -> naive UTC.

    `minute_of_day` may exceed 1440 for a window that runs past midnight; the
    overflow rolls into the next day before the conversion, so a 22:00-02:00
    shift resolves correctly rather than throwing.

    DST: on a spring-forward date the 2:00-3:00 wall clock does not exist. fold=0
    resolves it forward, which keeps a working window continuous instead of
    raising and blanking somebody's whole day.
    """
    extra_days, minute = divmod(int(minute_of_day), 1440)
    d = day + timedelta(days=extra_days)
    naive_local = datetime.combine(d, time(hour=minute // 60, minute=minute % 60))
    tz = _zone(tzname)
    if tz is None:                                     # pragma: no cover
        return naive_local
    aware_local = naive_local.replace(tzinfo=tz)
    return aware_local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def utc_to_local(dt_utc: datetime, tzname: str) -> datetime:
    """Naive UTC -> naive local wall clock, for display and for deciding which
    local day an instant belongs to."""
    tz = _zone(tzname)
    if tz is None or dt_utc is None:                   # pragma: no cover
        return dt_utc
    return (dt_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)).replace(tzinfo=None)


def local_dates_covering(start_utc: datetime, end_utc: datetime, tzname: str) -> List[date]:
    """Every local calendar date the UTC range touches, with a day of padding on
    each side so a window whose local day straddles the UTC boundary is not
    dropped."""
    first = utc_to_local(start_utc, tzname).date() - timedelta(days=1)
    last = utc_to_local(end_utc, tzname).date() + timedelta(days=1)
    out, d = [], first
    while d <= last:
        out.append(d)
        d += timedelta(days=1)
    return out


# ── interval algebra ────────────────────────────────────────────────────────

def normalize(intervals: Iterable[Interval]) -> List[Interval]:
    """Sort, drop empties, merge overlapping and touching ranges.

    Every operation below assumes normalized input, so anything that builds
    intervals hands them through here first.
    """
    cleaned = sorted([(s, e) for s, e in intervals if s and e and s < e])
    if not cleaned:
        return []
    out = [cleaned[0]]
    for s, e in cleaned[1:]:
        ls, le = out[-1]
        if s <= le:
            out[-1] = (ls, max(le, e))
        else:
            out.append((s, e))
    return out


def subtract_intervals(base: Iterable[Interval], blocks: Iterable[Interval]) -> List[Interval]:
    """base minus blocks. Ported from Grok's `subtractIntervals`, which is the
    one primitive there worth keeping.

    Every rule in this engine — lunch, time off, an existing meeting, minimum
    notice — is ultimately expressed as a subtraction from the working day.
    """
    result = normalize(base)
    for bs, be in normalize(blocks):
        nxt: List[Interval] = []
        for s, e in result:
            if be <= s or bs >= e:
                nxt.append((s, e))          # no overlap
                continue
            if bs > s:
                nxt.append((s, bs))         # keep the head
            if be < e:
                nxt.append((be, e))         # keep the tail
        result = nxt
    return normalize(result)


def intersect_intervals(a: Iterable[Interval], b: Iterable[Interval]) -> List[Interval]:
    """The times present in BOTH. This is the operation Grok never had."""
    aa, bb = normalize(a), normalize(b)
    out: List[Interval] = []
    i = j = 0
    while i < len(aa) and j < len(bb):
        s = max(aa[i][0], bb[j][0])
        e = min(aa[i][1], bb[j][1])
        if s < e:
            out.append((s, e))
        # Advance whichever ends first; the other may still overlap what's next.
        if aa[i][1] < bb[j][1]:
            i += 1
        else:
            j += 1
    return out


def intersect_all(sets: Sequence[Iterable[Interval]]) -> List[Interval]:
    """Fold intersect across N people.

    An EMPTY sequence returns [] rather than "always free". A meeting with no
    resolved required participants is a bug upstream, and answering it with
    unlimited availability would hide that bug behind a plausible screen.
    """
    if not sets:
        return []
    acc = normalize(list(sets[0]))
    for nxt in sets[1:]:
        acc = intersect_intervals(acc, nxt)
        if not acc:
            return []
    return acc


# ── profiles ────────────────────────────────────────────────────────────────

def get_or_create_profile(db: Session, user: User,
                          default_timezone: str = DEFAULT_TIMEZONE) -> AvailabilityProfile:
    """A person with no profile yet gets the standard weekday shape.

    Deliberately NOT "available 24/7": an unconfigured person appearing free at
    3am would produce openings nobody can attend, and the salesperson would stop
    trusting the finder after the first one.
    """
    prof = (db.query(AvailabilityProfile)
            .filter(AvailabilityProfile.user_id == user.id).first())
    if prof:
        return prof

    prof = AvailabilityProfile(user_id=user.id, timezone=default_timezone or DEFAULT_TIMEZONE)
    db.add(prof)
    db.flush()
    # Mon-Fri, 9:00-17:00 local, with a 12:00-13:00 lunch.
    for dow in range(5):
        db.add(AvailabilityWindow(profile_id=prof.id, day_of_week=dow,
                                  start_minute=9 * 60, end_minute=17 * 60))
        db.add(AvailabilityBlock(profile_id=prof.id, kind=BLOCK_RECURRING, label="Lunch",
                                 day_of_week=dow, start_minute=12 * 60, end_minute=13 * 60))
    db.flush()
    return prof


# ── per-user free time ──────────────────────────────────────────────────────

def busy_intervals_for_user(db: Session, user_id: str,
                            start_utc: datetime, end_utc: datetime,
                            exclude_appointment_id: Optional[str] = None) -> List[Interval]:
    """Existing commitments, already buffer-expanded.

    Reads `busy_start_at`/`busy_end_at` on the participant row rather than the
    appointment's own times, because those columns are the appointment window
    already padded by THIS person's buffers, frozen at booking. That is also
    exactly what the database exclusion constraint guards.
    """
    q = (db.query(AppointmentParticipant)
         .filter(AppointmentParticipant.user_id == user_id,
                 AppointmentParticipant.is_blocking.is_(True),
                 AppointmentParticipant.busy_start_at < end_utc,
                 AppointmentParticipant.busy_end_at > start_utc))
    if exclude_appointment_id:
        # Rescheduling: a meeting must not be treated as blocking itself.
        q = q.filter(AppointmentParticipant.appointment_id != exclude_appointment_id)
    return normalize([(p.busy_start_at, p.busy_end_at) for p in q.all()])


def free_intervals_for_user(db: Session, user: User,
                            start_utc: datetime, end_utc: datetime,
                            now_utc: Optional[datetime] = None,
                            duration_minutes: int = 0,
                            exclude_appointment_id: Optional[str] = None,
                            ignore_notice: bool = False) -> List[Interval]:
    """When this one person is genuinely free, in naive UTC.

    Order matters: build the working day, then subtract everything that removes
    time from it, then clip to the bookable horizon. Each step is a subtraction
    so no rule can be accidentally skipped by an early return.
    """
    now_utc = now_utc or datetime.utcnow()
    prof = get_or_create_profile(db, user)
    if not prof.accepts_bookings:
        return []

    tz = prof.timezone or DEFAULT_TIMEZONE
    days = local_dates_covering(start_utc, end_utc, tz)

    # 1. Working hours, expanded per local date so DST resolves per day.
    windows = db.query(AvailabilityWindow).filter(
        AvailabilityWindow.profile_id == prof.id).all()
    by_dow = {}
    for w in windows:
        by_dow.setdefault(w.day_of_week, []).append(w)

    working: List[Interval] = []
    for d in days:
        for w in by_dow.get(d.weekday(), []):
            working.append((local_to_utc(d, w.start_minute, tz),
                            local_to_utc(d, w.end_minute, tz)))
    working = normalize(working)
    if not working:
        return []

    # 2. Recurring blocks (lunch), expanded the same way.
    blocks = db.query(AvailabilityBlock).filter(
        AvailabilityBlock.profile_id == prof.id).all()
    cut: List[Interval] = []
    rec_by_dow = {}
    for b in blocks:
        if b.kind == BLOCK_RECURRING and b.day_of_week is not None:
            rec_by_dow.setdefault(b.day_of_week, []).append(b)
    for d in days:
        for b in rec_by_dow.get(d.weekday(), []):
            cut.append((local_to_utc(d, b.start_minute, tz),
                        local_to_utc(d, b.end_minute, tz)))

    # 3. Time off — already absolute instants.
    for b in blocks:
        if b.kind == BLOCK_TIME_OFF and b.starts_at and b.ends_at:
            cut.append((b.starts_at, b.ends_at))

    # 4. Existing meetings, buffer-expanded.
    cut.extend(busy_intervals_for_user(db, user.id, start_utc, end_utc,
                                       exclude_appointment_id))

    free = subtract_intervals(working, cut)

    # 5. Clip to the requested range and to the bookable horizon.
    earliest = start_utc
    if not ignore_notice:
        earliest = max(earliest, now_utc + timedelta(minutes=prof.min_notice_minutes or 0))
    latest = min(end_utc, now_utc + timedelta(days=prof.booking_horizon_days or 3650))
    free = intersect_intervals(free, [(earliest, latest)] if earliest < latest else [])

    # 6. A gap that cannot hold the meeting is not an opening.
    if duration_minutes:
        need = timedelta(minutes=duration_minutes)
        free = [(s, e) for s, e in free if (e - s) >= need]
    return free


# ── the intersection ────────────────────────────────────────────────────────

def _snap_up(dt: datetime, step_minutes: int) -> datetime:
    """Round forward to the next clean grid boundary."""
    dt = dt.replace(second=0, microsecond=0)
    rem = dt.minute % step_minutes
    if rem:
        dt += timedelta(minutes=step_minutes - rem)
    return dt


def slots_from_intervals(intervals: Sequence[Interval], duration_minutes: int,
                         step_minutes: int = DEFAULT_SLOT_STEP_MINUTES,
                         limit: int = 200) -> List[Interval]:
    """Cut continuous free ranges into bookable start times on the grid."""
    need = timedelta(minutes=duration_minutes)
    out: List[Interval] = []
    for s, e in intervals:
        cur = _snap_up(s, step_minutes)
        while cur + need <= e:
            out.append((cur, cur + need))
            if len(out) >= limit:
                return out
            cur += timedelta(minutes=step_minutes)
    return out


def find_shared_slots(db: Session,
                      required_users: Sequence[User],
                      optional_users: Sequence[User],
                      start_utc: datetime,
                      end_utc: datetime,
                      duration_minutes: int,
                      now_utc: Optional[datetime] = None,
                      step_minutes: int = DEFAULT_SLOT_STEP_MINUTES,
                      exclude_appointment_id: Optional[str] = None,
                      limit: int = 200) -> dict:
    """The headline operation.

    Returns openings where every REQUIRED participant is free, each annotated
    with which OPTIONAL participants happen to be free too — an optional person
    must never remove a slot, only decorate it.

    `blockers` explains an empty result. "No times available" with no reason is
    the single most infuriating thing a scheduler can say, so when the answer is
    empty the caller can tell the salesperson which person had no free time in
    the window.
    """
    now_utc = now_utc or datetime.utcnow()
    if not required_users:
        return {"slots": [], "required": [], "optional": [],
                "blockers": ["No required participants could be resolved."]}

    per_user = {}
    for u in list(required_users) + list(optional_users):
        if u.id in per_user:
            continue
        per_user[u.id] = free_intervals_for_user(
            db, u, start_utc, end_utc, now_utc=now_utc,
            duration_minutes=duration_minutes,
            exclude_appointment_id=exclude_appointment_id)

    blockers = [u.full_name or u.email for u in required_users if not per_user.get(u.id)]

    shared = intersect_all([per_user[u.id] for u in required_users])
    raw = slots_from_intervals(shared, duration_minutes, step_minutes, limit)

    slots = []
    for s, e in raw:
        also_free = [u.id for u in optional_users
                     if any(fs <= s and e <= fe for fs, fe in per_user.get(u.id, []))]
        slots.append({
            "starts_at": s, "ends_at": e,
            "optional_available_user_ids": also_free,
            "optional_available_count": len(also_free),
        })

    if not slots and not blockers:
        blockers = ["Every required participant has free time in this window, "
                    "but never at the same moment."]

    return {
        "slots": slots,
        "required": [u.id for u in required_users],
        "optional": [u.id for u in optional_users],
        "blockers": blockers,
        "free_by_user": per_user,
    }


# ── conflict detection ──────────────────────────────────────────────────────

def buffered_window(prof: AvailabilityProfile,
                    starts_at: datetime, ends_at: datetime) -> Interval:
    """The range this person actually occupies for a meeting at this time."""
    return (starts_at - timedelta(minutes=prof.buffer_before_minutes or 0),
            ends_at + timedelta(minutes=prof.buffer_after_minutes or 0))


def find_conflicts(db: Session, user_ids: Sequence[str],
                   starts_at: datetime, ends_at: datetime,
                   exclude_appointment_id: Optional[str] = None) -> List[dict]:
    """Who is already booked across this window.

    Called INSIDE the booking transaction. It catches every ordinary conflict
    and is the only protection on SQLite; on Postgres the exclusion constraint
    added in auto_migrate.py settles the genuine concurrent race that this check
    cannot see.

    Buffers are applied per participant, so the person whose padding creates the
    clash is the person named in the error.
    """
    conflicts = []
    for uid in user_ids:
        prof = (db.query(AvailabilityProfile)
                .filter(AvailabilityProfile.user_id == uid).first())
        if prof:
            bs, be = buffered_window(prof, starts_at, ends_at)
        else:
            bs, be = starts_at, ends_at

        q = (db.query(AppointmentParticipant, SalesAppointment)
             .join(SalesAppointment,
                   SalesAppointment.id == AppointmentParticipant.appointment_id)
             .filter(AppointmentParticipant.user_id == uid,
                     AppointmentParticipant.is_blocking.is_(True),
                     AppointmentParticipant.busy_start_at < be,
                     AppointmentParticipant.busy_end_at > bs))
        if exclude_appointment_id:
            q = q.filter(AppointmentParticipant.appointment_id != exclude_appointment_id)

        for part, appt in q.all():
            user = db.query(User).filter(User.id == uid).first()
            conflicts.append({
                "user_id": uid,
                "user_name": user.full_name if user else uid,
                "appointment_id": appt.id,
                "title": appt.title,
                "starts_at": appt.starts_at,
                "ends_at": appt.ends_at,
            })
    return conflicts
