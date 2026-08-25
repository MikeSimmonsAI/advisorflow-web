"""
External calendar BUSY ingestion.

TWO HALVES, KEPT APART ON PURPOSE
---------------------------------
  · refresh_external_busy() talks to a provider and writes the cache. It can be
    slow and it can fail.
  · external_busy_intervals() only reads the cache. It cannot be slow and it
    cannot fail.

The availability engine calls ONLY the second one. That is the whole design:
`free_intervals_for_user` stays a pure function of the database, so a shared
availability search across four people is four indexed queries rather than four
round-trips to Microsoft, and a Graph outage slows nothing down.

There is no second availability algorithm here. External busy time is turned
into ordinary (start, end) intervals and handed to the same subtraction the
engine already applies to meetings, lunch and time off. Adding a provider adds
rows to a table; it does not add a code path that decides who is free.

FAIL-OPEN, VISIBLY
------------------
If a refresh fails, the cache keeps whatever it last knew and the search still
returns slots. Blocking the whole scheduler because Microsoft returned a 500
would be worse. The failure is recorded on the CalendarConnection and surfaced
in the UI, so "we could not read your calendar" is stated rather than silently
converted into "you are free".
"""
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.calendar_models import (
    CalendarConnection, ExternalBusyBlock,
    PROVIDER_MICROSOFT, PROVIDER_GOOGLE,
)

log = logging.getLogger(__name__)

# How long a cached window is trusted before a search refreshes it. Short
# enough that a meeting somebody accepted this morning is honoured; long
# enough that paging through a week of availability is not a vendor DDoS.
CACHE_TTL_MINUTES = 10


# ── the read half: pure, fast, cannot fail ──────────────────────────────────

def external_busy_intervals(db: Session, user_id: str,
                            start_utc: datetime,
                            end_utc: datetime) -> List[Tuple[datetime, datetime]]:
    """Cached external busy intervals overlapping the window.

    Called from inside the availability engine, so it does exactly one indexed
    query and never touches the network. An empty result is indistinguishable
    from "no external calendar", and that is correct: in both cases we know of
    no external commitment and the engine should subtract nothing.
    """
    try:
        rows = (db.query(ExternalBusyBlock)
                .filter(ExternalBusyBlock.user_id == user_id,
                        ExternalBusyBlock.starts_at < end_utc,
                        ExternalBusyBlock.ends_at > start_utc)
                .all())
    except Exception:
        # A broken cache read must not break a booking. Degrade to "we know of
        # no external commitments", which is the pre-Checkpoint-3 behaviour.
        log.exception("external busy cache read failed for user %s", user_id)
        return []
    return [(r.starts_at, r.ends_at) for r in rows if r.ends_at > r.starts_at]


def cache_is_fresh(db: Session, user_id: str, provider: str,
                   start_utc: datetime, end_utc: datetime,
                   now_utc: Optional[datetime] = None,
                   ttl_minutes: int = CACHE_TTL_MINUTES) -> bool:
    """True when the cache already covers this window recently enough.

    Coverage is read from the CONNECTION's recorded window, never inferred from
    which rows exist. A calendar with no meetings this week produces zero rows,
    and judging freshness by row timestamps would re-fetch that empty week on
    every single search — punishing exactly the people with the most free time.
    """
    now_utc = now_utc or datetime.utcnow()
    cutoff = now_utc - timedelta(minutes=ttl_minutes)
    conn = (db.query(CalendarConnection)
            .filter(CalendarConnection.user_id == user_id,
                    CalendarConnection.provider == provider)
            .first())
    if conn is None:
        return False
    if not conn.busy_fetched_at or conn.busy_fetched_at < cutoff:
        return False
    if not conn.busy_window_start or not conn.busy_window_end:
        return False
    return conn.busy_window_start <= start_utc and conn.busy_window_end >= end_utc


# ── the write half: talks to a provider, may be slow, may fail ──────────────

def _record_failure(db: Session, conn: Optional[CalendarConnection], result,
                    now_utc: datetime) -> None:
    if conn is None:
        return
    conn.last_attempt_at = now_utc
    conn.last_error = (result.error_message or result.error_code or "")[:1000]
    conn.last_error_at = now_utc
    conn.failure_count = (conn.failure_count or 0) + 1
    if result.needs_reauth:
        # The grant is dead. Saying so is what lets the UI ask for a reconnect
        # instead of showing a generic failure the user cannot act on.
        conn.calendar_scope_ok = False
        if result.error_code == "reauth":
            conn.is_connected = False


def refresh_external_busy(db: Session, user, start_utc: datetime, end_utc: datetime,
                          org=None, now_utc: Optional[datetime] = None,
                          ttl_minutes: int = CACHE_TTL_MINUTES,
                          force: bool = False) -> dict:
    """Re-read one user's external calendar into the cache.

    Returns a small report — never raises, never rolls back the caller's work.
    The caller commits; this function only stages changes, so a refresh can run
    inside a larger transaction without owning it.
    """
    now_utc = now_utc or datetime.utcnow()
    from app.services import calendar_providers as reg

    key = reg.resolve_provider_key(db, user)
    if not reg.is_external_calendar(key):
        # No calendar to read. NOT an error — this is the normal state for
        # someone on the .ics fallback, and reporting it as a failure would
        # light up an alert for a user who has done nothing wrong.
        return {"provider": key, "refreshed": False, "reason": "no_external_calendar",
                "count": 0, "error": None}

    if not force and cache_is_fresh(db, user.id, key, start_utc, end_utc,
                                    now_utc, ttl_minutes):
        return {"provider": key, "refreshed": False, "reason": "cache_fresh",
                "count": 0, "error": None}

    conn = (db.query(CalendarConnection)
            .filter(CalendarConnection.user_id == user.id,
                    CalendarConnection.provider == key)
            .first())

    provider = reg.get_provider(db, user, org=org, prefer=key)
    intervals, err = provider.get_busy(start_utc, end_utc)

    if err is not None and not err.ok:
        _record_failure(db, conn, err, now_utc)
        # The PREVIOUS cache is deliberately left in place. Stale busy time is
        # closer to the truth than no busy time, and wiping it on a transient
        # 500 would offer colleagues slots the person is actually in a meeting
        # for. Fail open on availability, never on accuracy we already had.
        return {"provider": key, "refreshed": False, "reason": "provider_error",
                "count": 0, "error": err.error_code,
                "needs_reauth": err.needs_reauth}

    # Replace this provider's rows for this user wholesale. Diffing against
    # provider event ids sounds tidier but leaves orphans behind whenever an
    # event is deleted upstream, and an orphaned busy block is invisible time
    # nobody can book over and nobody can explain.
    try:
        (db.query(ExternalBusyBlock)
         .filter(ExternalBusyBlock.user_id == user.id,
                 ExternalBusyBlock.provider == key)
         .delete(synchronize_session=False))

        kept = 0
        for iv in intervals:
            if iv.ends_at <= iv.starts_at:
                continue
            db.add(ExternalBusyBlock(
                user_id=user.id, provider=key,
                starts_at=iv.starts_at, ends_at=iv.ends_at,
                provider_event_id=iv.provider_event_id,
                is_all_day=bool(iv.is_all_day),
                is_private=True,          # we were shown a subject; we did not keep it
                fetched_at=now_utc,
                window_start=start_utc, window_end=end_utc,
            ))
            kept += 1

        if conn is not None:
            conn.last_attempt_at = now_utc
            conn.last_sync_at = now_utc
            conn.busy_window_start = start_utc
            conn.busy_window_end = end_utc
            conn.busy_fetched_at = now_utc
            conn.last_error = None
            conn.last_error_at = None
            conn.failure_count = 0
            conn.calendar_scope_ok = True   # a successful read proves the grant
        db.flush()
    except Exception as e:
        log.exception("failed to write external busy cache for user %s", user.id)
        return {"provider": key, "refreshed": False, "reason": "cache_write_failed",
                "count": 0, "error": str(e)[:200]}

    return {"provider": key, "refreshed": True, "reason": None,
            "count": kept, "error": None}


def refresh_many(db: Session, users, start_utc: datetime, end_utc: datetime,
                 org=None, now_utc: Optional[datetime] = None,
                 ttl_minutes: int = CACHE_TTL_MINUTES) -> dict:
    """Refresh a whole participant set before a shared-availability search.

    One user's dead connection must not stop the others being refreshed, so
    each is independent and the report is per user. The search then runs
    against the cache regardless of what happened here.
    """
    report = {}
    for u in users:
        if u is None:
            continue
        try:
            report[u.id] = refresh_external_busy(db, u, start_utc, end_utc,
                                                 org=org, now_utc=now_utc,
                                                 ttl_minutes=ttl_minutes)
        except Exception as e:
            log.exception("external busy refresh blew up for user %s", u.id)
            report[u.id] = {"provider": None, "refreshed": False,
                            "reason": "exception", "count": 0, "error": str(e)[:200]}
    return report
