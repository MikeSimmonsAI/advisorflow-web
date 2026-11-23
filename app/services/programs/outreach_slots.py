"""THE TEXT SLOT SCHEDULE (Issue #507, owner directive Nov 15 2026).

For a program whose booking policy carries an "outreach" block (Restland only):

  * NEW-FAMILY SLOTS: 8:30 AM Central, then every 8 minutes, the last one
    before the 6:00 PM close (8:30, 8:38, ... 5:58 PM). Each holds one new
    family's first text.
  * FOLLOW-UP SLOTS: 2 minutes after each new-family slot (8:32, 8:40, ...),
    while that is still before the close. Each holds one due follow-up text.

Rules:
  * One text per slot (OutreachTextSlot has a unique (org, slot_at)).
  * A follow-up becomes due on its schedule day (days 3, 6, 10, 14 at the time
    of day its first text went) and takes the first FREE follow-up slot at or
    after that moment - never earlier. Extra due follow-ups queue for the next
    free follow-up slots; they never use a new-family slot, so new families are
    never delayed by them, and they still go when no new family is queued.
  * A slot that was missed (the server was down, a pass came late) is never
    caught up in a burst: anything more than GRACE late is moved to the next
    free follow-up slot from now.
  * Retries, quiet-hour holds and spacing holds all end up here too: whatever
    time they choose, the text goes only in a free follow-up slot at or after it.

Nothing here sends. cadence_service asks gate() before every Restland text and
sends only when it returns None.
"""
import json
import logging
from datetime import datetime, time, timedelta
from typing import Dict, List, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

TZ = "America/Chicago"
GRACE = timedelta(minutes=3)
SEARCH_DAYS = 120
DEFAULTS = {"open": "08:30", "close": "18:00", "new_every_minutes": 8, "followup_offset_minutes": 2,
            "followup_fill": False, "closed_days": ""}
# closed_days (owner, Nov 22 - "I want everything to stop on Sundays, only on
# Sundays"): ISO weekdays (1 = Monday ... 7 = Sunday) on which this program
# sends NOTHING automatic - no new-family texts, no follow-up texts, no
# emails, no AI answers, no daily batch, no survey. The day simply has no
# slots: whatever falls due waits for the next open day, in order. Replies
# still come in and alert staff, bookings still work, STOP is still honoured.
# followup_fill (owner, Nov 21 - "full blast, no slow drip"): follow-up texts
# may use EVERY minute between the new-family slots (not just one slot per
# gap), so follow-ups keep up when new families go every few minutes. Still
# one text per minute in total, never two at once.


def _zone():
    from zoneinfo import ZoneInfo
    return ZoneInfo(TZ)


def _t(s: str) -> time:
    h, m = (s or "00:00").split(":")
    return time(int(h), int(m))


def config_from_policy(raw) -> Optional[Dict]:
    """The "outreach" block of a booking policy, or None (schedule not in use)."""
    try:
        pol = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except ValueError:
        return None
    o = (pol or {}).get("outreach")
    if not isinstance(o, dict) or not o.get("slots"):
        return None
    out = dict(DEFAULTS)
    out.update({k: o[k] for k in DEFAULTS if k in o})
    out["new_every_minutes"] = max(1, int(out["new_every_minutes"]))
    out["followup_offset_minutes"] = max(1, min(out["new_every_minutes"] - 1, int(out["followup_offset_minutes"])))
    out["followup_fill"] = bool(out.get("followup_fill"))
    out["closed_days"] = "".join(sorted({c for c in str(out.get("closed_days") or "") if c in "1234567"}))
    return out


DAY_NAMES = {"1": "Monday", "2": "Tuesday", "3": "Wednesday", "4": "Thursday", "5": "Friday",
             "6": "Saturday", "7": "Sunday"}


def closed_on(day, cfg: Optional[Dict]) -> bool:
    """Is this local date one of the program's closed days (or a no-contact
    day for the whole system, e.g. Thanksgiving)?"""
    from app.services import no_contact_days as NCD
    if NCD.quiet_on(day):
        return True
    return bool(cfg) and str(day.isoweekday()) in (cfg.get("closed_days") or "")


def closed_now(now_utc: Optional[datetime], cfg: Optional[Dict]) -> bool:
    """Is it (Central) a closed day for this program right now?"""
    from app.services import no_contact_days as NCD
    if NCD.quiet_now(now_utc):
        return True
    if not cfg or not cfg.get("closed_days"):
        return False
    return closed_on(_local(now_utc or datetime.utcnow()).date(), cfg)


def closed_for_org(db: Optional[Session], org_id: Optional[str], now_utc: Optional[datetime] = None) -> bool:
    """True when this workspace's program is closed today. False on any error
    or for a workspace without a text schedule (SCI and the rest)."""
    if not org_id:
        return False
    from app.services import no_contact_days as NCD
    if NCD.quiet_now(now_utc):
        return True                                      # no-contact day: every workspace
    try:
        if db is None:
            from app.deps import SessionLocal
            s = SessionLocal()
            try:
                return closed_now(now_utc, config_for_org(s, org_id))
            finally:
                s.close()
        return closed_now(now_utc, config_for_org(db, org_id))
    except Exception:                                    # noqa: BLE001 - never blocks on a lookup error
        log.exception("closed-day lookup failed for %s", org_id)
        return False


def days_label(cfg: Optional[Dict]) -> str:
    """"every day" or "every day except Sunday" (for screens and summaries)."""
    closed = [DAY_NAMES[c] for c in (cfg or {}).get("closed_days") or ""]
    return "every day except %s" % " and ".join(closed) if closed else "every day"


_cache: Dict[str, tuple] = {}
CACHE_TTL = timedelta(seconds=60)


def config_for_org(db: Session, org_id: Optional[str]) -> Optional[Dict]:
    if not org_id:
        return None
    hit = _cache.get(org_id)
    if hit is not None and datetime.utcnow() - hit[0] < CACHE_TTL:
        return hit[1]
    cfg = None
    try:
        from app.models.program_models import OutreachProgram
        prog = db.query(OutreachProgram).filter(OutreachProgram.organization_id == org_id).first()
        cfg = config_from_policy(getattr(prog, "booking_policy_json", None)) if prog is not None else None
    except Exception:                                    # noqa: BLE001 - no schedule on a lookup error
        db.rollback()
        cfg = None
    _cache[org_id] = (datetime.utcnow(), cfg)
    return cfg


def owns_window(org_id: Optional[str], db: Optional[Session] = None) -> bool:
    """A program with its own text schedule sets its own hours (owner, Nov 16:
    Restland 8:30 AM - 7:00 PM Central every day). Those hours replace the
    8:30 AM - 7:45 PM Eastern clock for its families; the family's own 8 AM -
    9 PM local clock still applies (contact_hours). Read with its own short
    session when the cache is cold; False on any error (the Eastern clock
    then stays in force)."""
    if not org_id:
        return False
    hit = _cache.get(org_id)
    if hit is not None and datetime.utcnow() - hit[0] < CACHE_TTL:
        return hit[1] is not None
    if db is not None:
        return config_for_org(db, org_id) is not None
    try:
        from app.deps import SessionLocal
        s = SessionLocal()
        try:
            return config_for_org(s, org_id) is not None
        finally:
            s.close()
    except Exception:                                    # noqa: BLE001
        log.exception("own-window lookup failed for %s", org_id)
        return False


def clear_cache() -> None:
    _cache.clear()


# ── the grid ─────────────────────────────────────────────────────────────────

def _utc(local_dt: datetime) -> datetime:
    from datetime import timezone
    return local_dt.astimezone(timezone.utc).replace(tzinfo=None)


def _local(utc_naive: datetime) -> datetime:
    from datetime import timezone
    return utc_naive.replace(tzinfo=timezone.utc).astimezone(_zone())


def day_slots(day, cfg: Dict, kind: str) -> List[datetime]:
    """Naive-UTC slot times of one kind on one local date (DST-safe). A
    closed day (closed_days) has none."""
    if closed_on(day, cfg):
        return []
    open_t, close_t = _t(cfg["open"]), _t(cfg["close"])
    if kind == "followup" and cfg.get("followup_fill"):
        return _fill_slots(day, cfg)
    step = timedelta(minutes=cfg["new_every_minutes"])
    off = timedelta(minutes=cfg["followup_offset_minutes"] if kind == "followup" else 0)
    z = _zone()
    at = datetime.combine(day, open_t).replace(tzinfo=z) + off
    close = datetime.combine(day, close_t).replace(tzinfo=z)
    out = []
    while at < close:
        out.append(_utc(at))
        at = at + step
    return out


def _fill_slots(day, cfg: Dict) -> List[datetime]:
    """Every minute of the window that is not a new-family slot."""
    z = _zone()
    every = max(1, int(cfg["new_every_minutes"]))
    at = datetime.combine(day, _t(cfg["open"])).replace(tzinfo=z)
    close = datetime.combine(day, _t(cfg["close"])).replace(tzinfo=z)
    out, i = [], 0
    while at < close:
        if i % every:
            out.append(_utc(at))
        at, i = at + timedelta(minutes=1), i + 1
    return out


def slots_from(earliest: datetime, cfg: Dict, kind: str, days: int = SEARCH_DAYS):
    """Every slot of `kind` at or after `earliest` (naive UTC), in order."""
    d = _local(earliest).date()
    for i in range(days):
        for s in day_slots(d + timedelta(days=i), cfg, kind):
            if s >= earliest:
                yield s


def permits(now_utc: datetime, cfg: Dict) -> bool:
    if closed_now(now_utc, cfg):
        return False
    lt = _local(now_utc).time().replace(tzinfo=None)
    return _t(cfg["open"]) <= lt < _t(cfg["close"])


def next_open(now_utc: datetime, cfg: Dict) -> datetime:
    """Naive UTC: now when inside the window, else the next opening."""
    if permits(now_utc, cfg):
        return now_utc
    return next(slots_from(now_utc, cfg, "new"))


def new_family_slots(first: datetime, n: int, cfg: Dict, taken=None) -> List[datetime]:
    """n FREE new-family slot times from `first` on (journey starts); slots
    already reserved (`taken`) are skipped, so a second start continues after
    the families already scheduled."""
    taken = taken or set()
    out = []
    it = slots_from(first, cfg, "new")
    while len(out) < n:
        s = next(it)
        if s not in taken:
            out.append(s)
    return out


def taken_slots(db: Session, org_id: str, since: datetime) -> set:
    S = _model()
    return {t for (t,) in db.query(S.slot_at).filter(S.organization_id == org_id, S.slot_at >= since).all()}


# ── reservations ────────────────────────────────────────────────────────────

def _model():
    from app.models.reset_models import OutreachTextSlot
    return OutreachTextSlot


def reservation(db: Session, state_id: str, touch: int):
    S = _model()
    return (db.query(S).filter(S.cadence_state_id == state_id, S.touch_number == touch,
                               S.status == "reserved").order_by(S.slot_at.desc()).first())


def release(db: Session, state_id: str, touch: Optional[int] = None) -> int:
    """Free a family's reserved slot(s) - the slot row is removed so another
    text may take it."""
    S = _model()
    q = db.query(S).filter(S.cadence_state_id == state_id, S.status == "reserved")
    if touch is not None:
        q = q.filter(S.touch_number == touch)
    n = q.delete(synchronize_session=False)
    return n


def reserve(db: Session, org_id: str, state, touch: int, at: datetime, kind: str) -> bool:
    """Take this exact slot for this family's touch. False if it is taken."""
    S = _model()
    sp = db.begin_nested()
    try:
        db.add(S(organization_id=org_id, slot_at=at, kind=kind, cadence_state_id=state.id,
                 lead_id=getattr(state, "lead_id", None), touch_number=touch))
        db.flush()
        sp.commit()
        return True
    except IntegrityError:
        sp.rollback()
        return False


def place_followup(db: Session, org_id: str, state, touch: int, earliest: datetime,
                   cfg: Optional[Dict] = None) -> Optional[datetime]:
    """Give this touch the first free follow-up slot at or after `earliest`;
    set the family's next due time to it. Returns the slot (or None)."""
    cfg = cfg or config_for_org(db, org_id)
    if cfg is None:
        return None
    release(db, state.id, touch)
    S = _model()
    taken = set()
    horizon = earliest + timedelta(days=SEARCH_DAYS)
    for (t,) in db.query(S.slot_at).filter(S.organization_id == org_id, S.slot_at >= earliest,
                                           S.slot_at < horizon).all():
        taken.add(t)
    for s in slots_from(earliest, cfg, "followup"):
        if s in taken:
            continue
        if reserve(db, org_id, state, touch, s, "followup"):
            state.next_touch_due_at = s
            return s
        taken.add(s)
    return None


FIRST_LATE_WINDOW = timedelta(minutes=15)


def place_first_late(db: Session, org_id: str, state, now: datetime,
                     cfg: Optional[Dict] = None) -> Optional[datetime]:
    """A first text whose own slot was missed takes the next OPEN minute of
    either kind - still one text per minute, never a burst. If every minute in
    the next FIRST_LATE_WINDOW is held, it takes the earliest one held by a
    follow-up (text 2 or later) and that follow-up moves to the next free
    follow-up slot. Another family's first text is never moved. With nothing
    to take, it falls back to the follow-up queue as before."""
    cfg = cfg or config_for_org(db, org_id)
    if cfg is None:
        return None
    release(db, state.id, 1)
    S = _model()
    horizon = now + FIRST_LATE_WINDOW
    taken = {t for (t,) in db.query(S.slot_at).filter(S.organization_id == org_id, S.slot_at >= now,
                                                         S.slot_at < horizon).all()}
    news = set()
    for s in slots_from(now, cfg, "new"):
        if s >= horizon:
            break
        news.add(s)
    fols = []
    for s in slots_from(now, cfg, "followup"):
        if s >= horizon:
            break
        fols.append(s)
    for s in sorted(news | set(fols)):
        if s in taken:
            continue
        if reserve(db, org_id, state, 1, s, "new" if s in news else "followup"):
            state.next_touch_due_at = s
            return s
    # Every near minute is held: take the earliest one a follow-up holds.
    from app.models.models import CadenceState
    rows = (db.query(S).filter(S.organization_id == org_id, S.status == "reserved", S.kind == "followup",
                               S.touch_number > 1, S.slot_at >= now, S.slot_at < horizon,
                               S.cadence_state_id != state.id)
            .order_by(S.slot_at).all())
    for row in rows:
        other = db.query(CadenceState).filter(CadenceState.id == row.cadence_state_id).first()
        at, other_touch = row.slot_at, row.touch_number
        db.delete(row)
        db.flush()
        won = reserve(db, org_id, state, 1, at, "followup")
        if other is not None:
            # The follow-up always gets a slot back, whether or not we won this one.
            place_followup(db, org_id, other, other_touch, at + timedelta(minutes=1), cfg)
        if won:
            state.next_touch_due_at = at
            log.info("first text for state %s took follow-up slot %s; follow-up re-queued", state.id, at)
            return at
    return place_followup(db, org_id, state, 1, now, cfg)


def assign_new(db: Session, org_id: str, state, at: datetime) -> bool:
    """A new family's first text in its new-family slot (journey start)."""
    release(db, state.id, 1)
    if reserve(db, org_id, state, 1, at, "new"):
        state.next_touch_due_at = at
        return True
    return False


def gate(db: Session, state, lead, touch: int, now: datetime) -> Optional[str]:
    """None = this text may go now, in its slot. Otherwise the text has been
    (re)queued and the reason says until when. Commits nothing itself."""
    org_id = getattr(lead, "organization_id", None)
    cfg = config_for_org(db, org_id)
    if cfg is None:
        return None
    r = reservation(db, state.id, touch)
    due = state.next_touch_due_at or now
    if r is not None and r.slot_at == due:
        if now < r.slot_at:
            return "waiting for its slot at %s" % _local(r.slot_at).strftime("%-I:%M %p %b %-d")
        if now - r.slot_at <= GRACE:
            return None
        # Missed (late pass / downtime): never caught up in a burst.
        if touch == 1:
            # A new family's FIRST text goes in the next open minute, ahead of
            # the follow-up line (owner, Nov 23: Charles Dooley's 9:03 first
            # text was missed by one skipped minute and pushed to 11:05).
            s = place_first_late(db, org_id, state, now, cfg)
        else:
            s = place_followup(db, org_id, state, touch, now, cfg)
        return "slot %s missed - moved to %s" % (_local(r.slot_at).strftime("%-I:%M %p"),
                                                _local(s).strftime("%-I:%M %p %b %-d") if s else "-")
    s = place_followup(db, org_id, state, touch, max(due, now), cfg)
    return "queued for the follow-up slot at %s" % (_local(s).strftime("%-I:%M %p %b %-d") if s else "-")


def mark_used(db: Session, state_id: str, touch: int) -> None:
    S = _model()
    db.query(S).filter(S.cadence_state_id == state_id, S.touch_number == touch,
                       S.status == "reserved").update({"status": "used"}, synchronize_session=False)


def queue_view(db: Session, org_id: str, now: Optional[datetime] = None, limit: int = 40) -> Dict:
    """Read-back: the next reserved slots (times only, no family details)."""
    now = now or datetime.utcnow()
    S = _model()
    rows = (db.query(S).filter(S.organization_id == org_id, S.status == "reserved", S.slot_at >= now - GRACE)
            .order_by(S.slot_at).limit(limit).all())
    counts = {}
    for k in ("new", "followup"):
        counts[k] = db.query(S).filter(S.organization_id == org_id, S.status == "reserved",
                                       S.kind == k).count()
    return {"reserved": counts, "next": [{"at": _local(r.slot_at).strftime("%a %b %-d %-I:%M %p"),
                                          "kind": r.kind, "touch": r.touch_number} for r in rows]}
