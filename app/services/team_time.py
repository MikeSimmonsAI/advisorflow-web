"""TIMEKEEPING for the EvoSys Pro team app.

THE SERVER'S CLOCK IS THE SOURCE OF TRUTH. Clock In and Clock Out take no time
from the phone; they stamp datetime.utcnow() here. A retried request is safe:
clocking in while already clocked in returns the open shift, clocking out while
already clocked out returns the last shift. Two simultaneous Clock In requests
cannot both open a shift - `open_marker` is UNIQUE, so the second insert fails
in the database and is answered with the shift the first one opened.

DISPLAY TIME ZONE: the person's own booking time zone, else their
organization's, else America/Chicago. Weeks run Monday 00:00 to Monday 00:00
LOCAL time, converted to UTC with zoneinfo, so a week that contains a
daylight-saving change is 167 or 169 real hours long and every shift inside it
is measured in real elapsed time. A shift that crosses midnight or a week
boundary is split at the boundary, never counted twice and never lost.
"""
from __future__ import annotations

from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.models import Organization, User
from app.models.team_app_models import TeamTimeCorrection, TeamTimeEntry, TeamTimeScope

DEFAULT_TZ = "America/Chicago"
MAX_SHIFT = timedelta(hours=24)


def now_utc() -> datetime:
    return datetime.utcnow().replace(microsecond=0)


def tz_name_for(db: Session, user: User) -> str:
    for cand in (getattr(user, "booking_timezone", None),
                 _org_tz(db, getattr(user, "organization_id", None)), DEFAULT_TZ):
        if cand:
            try:
                ZoneInfo(cand)
                return cand
            except (ZoneInfoNotFoundError, ValueError):
                continue
    return DEFAULT_TZ


def _org_tz(db: Session, org_id: Optional[str]) -> Optional[str]:
    if not org_id:
        return None
    org = db.query(Organization).filter(Organization.id == org_id).first()
    return getattr(org, "timezone", None) if org else None


def iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z") if dt else None


def week_bounds(tz_name: str, anchor_local: date) -> Tuple[datetime, datetime, date]:
    """(start_utc, end_utc, monday) of the local Monday-to-Monday week."""
    tz = ZoneInfo(tz_name)
    monday = anchor_local - timedelta(days=anchor_local.weekday())
    return (_local_midnight_utc(monday, tz), _local_midnight_utc(monday + timedelta(days=7), tz), monday)


def _local_midnight_utc(d: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(d, dtime(0, 0), tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)


def overlap_seconds(entry: TeamTimeEntry, start: datetime, end: datetime, now: datetime) -> int:
    a = max(entry.clock_in_at, start)
    b = min(entry.clock_out_at or now, end)
    return max(0, int((b - a).total_seconds()))


def serialize(entry: TeamTimeEntry, now: datetime, user: Optional[User] = None,
              corrections: int = 0) -> Dict:
    end = entry.clock_out_at or now
    out = {
        "id": entry.id,
        "user_id": entry.user_id,
        "clock_in_at": iso(entry.clock_in_at),
        "clock_out_at": iso(entry.clock_out_at),
        "open": entry.clock_out_at is None,
        "seconds": max(0, int((end - entry.clock_in_at).total_seconds())),
        "corrections": corrections,
    }
    if user is not None:
        out["user_name"] = user.full_name
    return out


def open_entry(db: Session, user_id: str) -> Optional[TeamTimeEntry]:
    return db.query(TeamTimeEntry).filter(TeamTimeEntry.open_marker == user_id).first()


def status(db: Session, user: User) -> Dict:
    now = now_utc()
    e = open_entry(db, user.id)
    return {"server_now": iso(now), "time_zone": tz_name_for(db, user),
            "clocked_in": e is not None, "entry": serialize(e, now) if e else None}


def clock_in(db: Session, user: User, platform_id: str) -> Dict:
    existing = open_entry(db, user.id)
    if existing:
        return {"already": True, **status(db, user)}
    db.add(TeamTimeEntry(platform_id=platform_id, user_id=user.id,
                         clock_in_at=now_utc(), open_marker=user.id))
    try:
        db.commit()
    except IntegrityError:
        # Another request opened the shift first. Its shift is the answer.
        db.rollback()
        return {"already": True, **status(db, user)}
    return {"already": False, **status(db, user)}


def clock_out(db: Session, user: User) -> Dict:
    now = now_utc()
    n = (db.query(TeamTimeEntry)
         .filter(TeamTimeEntry.open_marker == user.id, TeamTimeEntry.clock_out_at.is_(None))
         .update({TeamTimeEntry.clock_out_at: now, TeamTimeEntry.open_marker: None,
                  TeamTimeEntry.updated_at: now}, synchronize_session=False))
    db.commit()
    last = (db.query(TeamTimeEntry).filter(TeamTimeEntry.user_id == user.id)
            .order_by(TeamTimeEntry.clock_in_at.desc()).first())
    return {"already": n == 0, **status(db, user),
            "last_entry": serialize(last, now) if last else None}


def _entries_in(db: Session, user_ids: List[str], start: datetime, end: datetime) -> List[TeamTimeEntry]:
    if not user_ids:
        return []
    return (db.query(TeamTimeEntry)
            .filter(TeamTimeEntry.user_id.in_(user_ids),
                    TeamTimeEntry.clock_in_at < end,
                    (TeamTimeEntry.clock_out_at.is_(None)) | (TeamTimeEntry.clock_out_at > start))
            .order_by(TeamTimeEntry.clock_in_at.desc()).all())


def _correction_counts(db: Session, ids: List[str]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    if ids:
        for (eid,) in db.query(TeamTimeCorrection.entry_id).filter(
                TeamTimeCorrection.entry_id.in_(ids)).all():
            out[eid] = out.get(eid, 0) + 1
    return out


def parse_local_date(s: Optional[str], tz_name: str) -> date:
    if s:
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            raise HTTPException(status_code=400, detail="Use a date like 2026-11-16.")
    return datetime.now(ZoneInfo(tz_name)).date()


def my_week(db: Session, user: User, week_of: Optional[str]) -> Dict:
    tz = tz_name_for(db, user)
    start, end, monday = week_bounds(tz, parse_local_date(week_of, tz))
    now = now_utc()
    rows = _entries_in(db, [user.id], start, end)
    counts = _correction_counts(db, [r.id for r in rows])
    days = []
    zi = ZoneInfo(tz)
    for i in range(7):
        d = monday + timedelta(days=i)
        ds, de = _local_midnight_utc(d, zi), _local_midnight_utc(d + timedelta(days=1), zi)
        days.append({"date": d.isoformat(),
                     "seconds": sum(overlap_seconds(r, ds, de, now) for r in rows)})
    return {"time_zone": tz, "week_start": monday.isoformat(),
            "week_start_utc": iso(start), "week_end_utc": iso(end), "server_now": iso(now),
            "total_seconds": sum(overlap_seconds(r, start, end, now) for r in rows),
            "days": days,
            "entries": [serialize(r, now, corrections=counts.get(r.id, 0)) for r in rows]}


# ── Team Time ────────────────────────────────────────────────────────────────

def managed_user_ids(db: Session, manager: User, access, all_people: List[User]) -> List[str]:
    """Platform: every person with Time. A delegated manager: ONLY the people
    assigned to them in team_time_scopes. Nobody else, ever."""
    if access.is_platform:
        return [u.id for u in all_people]
    rows = (db.query(TeamTimeScope.member_user_id)
            .filter(TeamTimeScope.platform_id == access.platform_id,
                    TeamTimeScope.manager_user_id == manager.id).all())
    return [r[0] for r in rows]


def team_range(db: Session, manager: User, user_ids: List[str], date_from: Optional[str],
               date_to: Optional[str], only_user: Optional[str]) -> Dict:
    tz = tz_name_for(db, manager)
    zi = ZoneInfo(tz)
    d_from = parse_local_date(date_from, tz)
    d_to = parse_local_date(date_to, tz) if date_to else d_from + timedelta(days=6)
    if d_to < d_from:
        raise HTTPException(status_code=400, detail="The end date is before the start date.")
    if (d_to - d_from).days > 62:
        raise HTTPException(status_code=400, detail="Choose a range of 62 days or less.")
    start = _local_midnight_utc(d_from, zi)
    end = _local_midnight_utc(d_to + timedelta(days=1), zi)
    ids = list(user_ids)
    if only_user:
        if only_user not in ids:
            raise HTTPException(status_code=403, detail="That person is not on your team.")
        ids = [only_user]
    now = now_utc()
    rows = _entries_in(db, ids, start, end)
    users = {u.id: u for u in db.query(User).filter(User.id.in_(ids)).all()} if ids else {}
    counts = _correction_counts(db, [r.id for r in rows])
    people = []
    for uid in ids:
        u = users.get(uid)
        if not u:
            continue
        mine = [r for r in rows if r.user_id == uid]
        people.append({"user_id": uid, "name": u.full_name,
                       "clocked_in": any(r.clock_out_at is None for r in mine),
                       "total_seconds": sum(overlap_seconds(r, start, end, now) for r in mine)})
    people.sort(key=lambda p: p["name"].lower())
    return {"time_zone": tz, "date_from": d_from.isoformat(), "date_to": d_to.isoformat(),
            "server_now": iso(now), "people": people,
            "entries": [serialize(r, now, users.get(r.user_id), counts.get(r.id, 0)) for r in rows]}


def parse_input_time(value: Optional[str], tz_name: str) -> Optional[datetime]:
    """'2026-11-16T08:30' (local wall time in the display zone) or an ISO time
    with an offset/Z. Returns naive UTC. A wall time that does not exist (the
    spring-forward hour) is refused rather than guessed."""
    if value in (None, ""):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail="Use a time like 2026-11-16T08:30.")
    if dt.tzinfo is None:
        zi = ZoneInfo(tz_name)
        local = dt.replace(tzinfo=zi)
        back = local.astimezone(timezone.utc).astimezone(zi)
        if back.replace(tzinfo=None) != dt:
            raise HTTPException(status_code=400,
                                detail="That time does not exist on that day (clocks moved forward).")
        dt = local
    return dt.astimezone(timezone.utc).replace(tzinfo=None, microsecond=0)


def correct(db: Session, actor: User, entry: TeamTimeEntry, clock_in_at: Optional[str],
            clock_out_at: Optional[str], reason: str) -> TeamTimeCorrection:
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise HTTPException(status_code=400, detail="A reason is required for every correction.")
    tz = tz_name_for(db, actor)
    new_in = parse_input_time(clock_in_at, tz) or entry.clock_in_at
    new_out = parse_input_time(clock_out_at, tz) if clock_out_at else entry.clock_out_at
    now = now_utc()
    if new_out is not None and new_out <= new_in:
        raise HTTPException(status_code=400, detail="Clock Out must be after Clock In.")
    if new_in > now or (new_out and new_out > now):
        raise HTTPException(status_code=400, detail="A correction cannot be in the future.")
    if new_out is not None and new_out - new_in > MAX_SHIFT:
        raise HTTPException(status_code=400, detail="A single shift cannot be longer than 24 hours.")
    clash = (db.query(TeamTimeEntry)
             .filter(TeamTimeEntry.user_id == entry.user_id, TeamTimeEntry.id != entry.id,
                     TeamTimeEntry.clock_in_at < (new_out or now),
                     (TeamTimeEntry.clock_out_at.is_(None)) | (TeamTimeEntry.clock_out_at > new_in))
             .first())
    if clash:
        raise HTTPException(status_code=409, detail="That would overlap another shift for this person.")
    c = TeamTimeCorrection(entry_id=entry.id, changed_by=actor.id, reason=reason[:1000],
                           before_clock_in_at=entry.clock_in_at, before_clock_out_at=entry.clock_out_at,
                           after_clock_in_at=new_in, after_clock_out_at=new_out)
    entry.clock_in_at = new_in
    if new_out is not None and entry.clock_out_at is None:
        entry.open_marker = None
    entry.clock_out_at = new_out
    entry.updated_at = now
    db.add(c)
    try:
        from app.routers.audit_log_router import log_action
        log_action(db, None, actor.id, action="team_time.corrected", target_type="team_time_entry",
                   target_id=entry.id,
                   before={"clock_in_at": iso(c.before_clock_in_at), "clock_out_at": iso(c.before_clock_out_at)},
                   after={"clock_in_at": iso(new_in), "clock_out_at": iso(new_out)},
                   note=reason[:500], commit=False)
    except Exception:  # pragma: no cover - the correction table is the record
        pass
    db.commit()
    return c


def history(db: Session, entry_id: str) -> List[Dict]:
    rows = (db.query(TeamTimeCorrection).filter(TeamTimeCorrection.entry_id == entry_id)
            .order_by(TeamTimeCorrection.created_at.desc()).all())
    names = {u.id: u.full_name for u in db.query(User).filter(
        User.id.in_([r.changed_by for r in rows])).all()} if rows else {}
    return [{"id": r.id, "changed_by": r.changed_by, "changed_by_name": names.get(r.changed_by),
             "reason": r.reason, "changed_at": iso(r.created_at),
             "before": {"clock_in_at": iso(r.before_clock_in_at), "clock_out_at": iso(r.before_clock_out_at)},
             "after": {"clock_in_at": iso(r.after_clock_in_at), "clock_out_at": iso(r.after_clock_out_at)}}
            for r in rows]
