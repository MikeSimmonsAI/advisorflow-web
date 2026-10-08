"""One authoritative read decision for the shared sales calendar.

Appointments, opportunity tasks (next action) and activities (opportunity
timeline) are normalised into ONE event contract here. This module has no
database, HTTP or provider dependency: the router loads rows and this decides
what the viewer may see, how each row is described and in what order.

Rules, all enforced here so web and mobile cannot disagree:

* A seller sees only events on opportunities they own or appointments they are
  a participant of. A manager sees the whole authorised brand. Rows from any
  other brand are dropped even if the caller's query let them through.
* A missing fact is None and is named in `unavailable`; nothing is invented.
* Original UTC instants and the source timezone are preserved. The local date
  is derived in the event's own timezone.
* Ordering is total: (starts_at, type rank, id), rows without a time last.
* Overlapping sources never produce two events for one fact.
* Nothing here sends, invites, reminds or contacts anyone. It is read-only.
"""
from datetime import date, datetime, time, timedelta, timezone as _tz
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

try:  # pragma: no cover - zoneinfo ships with the runtime
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

EVENT_CAP = 500
MAX_RANGE_DAYS = 62

TYPE_APPOINTMENT = "appointment"
TYPE_TASK = "task"
TYPE_ACTIVITY = "activity"
EVENT_TYPES = (TYPE_APPOINTMENT, TYPE_TASK, TYPE_ACTIVITY)
_TYPE_RANK = {TYPE_APPOINTMENT: 0, TYPE_TASK: 1, TYPE_ACTIVITY: 2}

# bucket: the one distinction the UI renders.
BUCKET_SCHEDULED = "scheduled"      # a booked appointment, still live
BUCKET_TASK = "task"                # internal to-do with a due time
BUCKET_COMPLETED = "completed"      # happened (completed / no-show / activity)
BUCKET_CANCELLED = "cancelled"
BUCKET_UNSCHEDULED = "unscheduled"  # work with no date at all
BUCKETS = (BUCKET_SCHEDULED, BUCKET_TASK, BUCKET_COMPLETED,
           BUCKET_CANCELLED, BUCKET_UNSCHEDULED)

# Timeline rows that merely record a change to an appointment which is already
# on the calendar in its own right.
APPOINTMENT_MIRROR_ACTIVITY = ("appointment_booked", "appointment_cancelled",
                               "appointment_rescheduled", "confirmation")

DEFAULT_TZ = "America/Chicago"


# ── time helpers ────────────────────────────────────────────────────────────

def _zone(name: Optional[str]):
    if ZoneInfo is None:
        return None
    for candidate in (name, DEFAULT_TZ):
        try:
            return ZoneInfo(candidate)
        except Exception:
            continue
    return None


def _utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Stored instants are naive UTC. Attach UTC; convert aware values."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_tz.utc)
    return dt.astimezone(_tz.utc)


def _iso_utc(dt: Optional[datetime]) -> Optional[str]:
    u = _utc(dt)
    return u.strftime("%Y-%m-%dT%H:%M:%SZ") if u else None


def _local(dt: Optional[datetime], tzname: Optional[str]) -> Optional[datetime]:
    u = _utc(dt)
    if u is None:
        return None
    z = _zone(tzname)
    return u.astimezone(z) if z else u


def range_bounds(date_from: date, date_to: date, tzname: Optional[str]):
    """[date_from 00:00, date_to+1 00:00) in `tzname`, as naive UTC datetimes.

    A DST day is 23 or 25 hours long, so the end is the next local midnight,
    not start + n*24h."""
    z = _zone(tzname)

    def at(d: date) -> datetime:
        naive = datetime.combine(d, time(0, 0))
        if z is None:
            return naive
        return naive.replace(tzinfo=z).astimezone(_tz.utc).replace(tzinfo=None)

    return at(date_from), at(date_to + timedelta(days=1))


# ── normalisation ───────────────────────────────────────────────────────────

def _g(obj: Any, name: str, default=None):
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def _event(*, type_: str, source_id: str, title: Optional[str], brand_id: str,
           opp: Any, owner_id: Optional[str], owner_name: Optional[str],
           starts: Optional[datetime], ends: Optional[datetime],
           tzname: Optional[str], tz_source: str, all_day: Optional[bool],
           status: str, bucket: str, source_table: str,
           recorded_at: Optional[datetime], participant_ids: Sequence[str] = (),
           extra_unavailable: Sequence[str] = (), blockers: Sequence[str] = (),
           meta: Optional[dict] = None) -> dict:
    ls, le = _local(starts, tzname), _local(ends, tzname)
    ev = {
        "id": "%s:%s" % (type_, source_id),
        "type": type_,
        "source": source_table,
        "source_id": source_id,
        "title": title or None,
        "brand_sales_org_id": brand_id,
        "owner": ({"user_id": owner_id, "name": owner_name} if owner_id else None),
        "participant_user_ids": sorted(set(participant_ids)),
        "opportunity_id": _g(opp, "id") if opp is not None else None,
        "company": _g(opp, "company_name") if opp is not None else None,
        "starts_at": _iso_utc(starts),
        "ends_at": _iso_utc(ends),
        "timezone": tzname or None,
        "timezone_source": tz_source,
        "starts_at_local": ls.replace(tzinfo=None).isoformat() if ls else None,
        "ends_at_local": le.replace(tzinfo=None).isoformat() if le else None,
        "local_date": ls.date().isoformat() if ls else None,
        "local_end_date": le.date().isoformat() if le else None,
        "all_day": all_day,
        "status": status,
        "bucket": bucket,
        "evidence": {"source_table": source_table, "source_id": source_id,
                     "recorded_at": _iso_utc(recorded_at)},
        "blockers": list(blockers),
    }
    unavailable = list(extra_unavailable)
    for field in ("title", "owner", "starts_at", "ends_at", "timezone",
                  "company", "opportunity_id"):
        if ev[field] is None and field not in unavailable:
            unavailable.append(field)
    ev["unavailable"] = sorted(unavailable)
    ev["_sort_start"] = _utc(starts)
    ev["_sort_end"] = _utc(ends)
    if meta:
        ev.update(meta)
    return ev


def appointment_event(appt: Any, opp: Any = None, owner_name: Optional[str] = None,
                      participant_ids: Sequence[str] = ()) -> dict:
    status = _g(appt, "status") or "scheduled"
    if status == "cancelled":
        bucket = BUCKET_CANCELLED
    elif status in ("completed", "no_show"):
        bucket = BUCKET_COMPLETED
    else:
        bucket = BUCKET_SCHEDULED
    owner_id = _g(opp, "owner_user_id") if opp is not None else None
    return _event(
        type_=TYPE_APPOINTMENT, source_id=_g(appt, "id"), title=_g(appt, "title"),
        brand_id=_g(appt, "brand_sales_org_id"), opp=opp, owner_id=owner_id,
        owner_name=owner_name, starts=_g(appt, "starts_at"), ends=_g(appt, "ends_at"),
        tzname=_g(appt, "timezone"), tz_source="appointment", all_day=False,
        status=status, bucket=bucket, source_table="sales_appointments",
        recorded_at=_g(appt, "created_at"), participant_ids=participant_ids,
        meta={"meeting_type": _g(appt, "meeting_type"),
              "location": _g(appt, "location")})


def task_event(opp: Any, org_tz: Optional[str], owner_name: Optional[str] = None) -> dict:
    """An opportunity's next action. No completion fact exists on the source, so
    a task is never reported completed - only open, or closed with its deal."""
    due = _g(opp, "next_action_due_at")
    tzname = _g(opp, "timezone")
    tz_source = "opportunity"
    if not tzname:
        tzname, tz_source = org_tz, "brand_default"
    deal_open = (_g(opp, "status") or "open") == "open"
    local = _local(due, tzname)
    # A due stamp at exactly local midnight is how a date-only due is stored.
    all_day = (local.hour, local.minute, local.second) == (0, 0, 0) if local else None
    if due is None:
        bucket, blockers = BUCKET_UNSCHEDULED, ["No due date on this next action."]
    else:
        bucket, blockers = BUCKET_TASK, []
    if not deal_open:
        bucket = BUCKET_COMPLETED if due is not None else BUCKET_UNSCHEDULED
    return _event(
        type_=TYPE_TASK, source_id=_g(opp, "id"), title=_g(opp, "next_action"),
        brand_id=_g(opp, "brand_sales_org_id"), opp=opp,
        owner_id=_g(opp, "owner_user_id"), owner_name=owner_name,
        starts=due, ends=None, tzname=tzname, tz_source=tz_source, all_day=all_day,
        status="open" if deal_open else "closed_with_deal", bucket=bucket,
        source_table="opportunities", recorded_at=_g(opp, "updated_at"),
        extra_unavailable=("ends_at", "completion"), blockers=blockers)


def activity_event(ev: Any, opp: Any, org_tz: Optional[str],
                   owner_name: Optional[str] = None) -> dict:
    tzname = _g(opp, "timezone")
    tz_source = "opportunity"
    if not tzname:
        tzname, tz_source = org_tz, "brand_default"
    return _event(
        type_=TYPE_ACTIVITY, source_id=_g(ev, "id"), title=_g(ev, "summary"),
        brand_id=_g(opp, "brand_sales_org_id"), opp=opp,
        owner_id=_g(opp, "owner_user_id"), owner_name=owner_name,
        starts=_g(ev, "occurred_at"), ends=None, tzname=tzname, tz_source=tz_source,
        all_day=False, status="recorded", bucket=BUCKET_COMPLETED,
        source_table="opportunity_events", recorded_at=_g(ev, "occurred_at"),
        extra_unavailable=("ends_at",),
        meta={"activity_type": _g(ev, "event_type")})


# ── visibility, de-duplication, ordering ────────────────────────────────────

def visible_to(event: dict, viewer_id: str, brand_id: str, is_manager: bool) -> bool:
    if event["brand_sales_org_id"] != brand_id:
        return False                       # never cross-brand, whoever asks
    if is_manager:
        return True
    owner = event["owner"]["user_id"] if event["owner"] else None
    return owner == viewer_id or viewer_id in event["participant_user_ids"]


def dedupe(events: Iterable[dict]):
    """Returns (kept, suppressed[{id, reason, duplicate_of}])."""
    seen: Dict[str, dict] = {}
    suppressed: List[dict] = []
    for e in events:
        if e["id"] in seen:
            suppressed.append({"id": e["id"], "reason": "same_source_row",
                               "duplicate_of": e["id"]})
            continue
        seen[e["id"]] = e
    appts = [e for e in seen.values() if e["type"] == TYPE_APPOINTMENT
             and e["bucket"] != BUCKET_CANCELLED]
    appt_opps = {e["opportunity_id"] for e in seen.values()
                 if e["type"] == TYPE_APPOINTMENT and e["opportunity_id"]}
    live_start = {(a["opportunity_id"], a["starts_at"]): a["id"]
                  for a in appts if a["opportunity_id"] and a["starts_at"]}
    kept = []
    for e in seen.values():
        if e["type"] == TYPE_TASK and e["starts_at"]:
            dup = live_start.get((e["opportunity_id"], e["starts_at"]))
            if dup:
                suppressed.append({"id": e["id"], "reason": "task_matches_appointment",
                                   "duplicate_of": dup})
                continue
        if (e["type"] == TYPE_ACTIVITY
                and e.get("activity_type") in APPOINTMENT_MIRROR_ACTIVITY
                and e["opportunity_id"] in appt_opps):
            suppressed.append({"id": e["id"], "reason": "activity_mirrors_appointment",
                               "duplicate_of": None})
            continue
        kept.append(e)
    return kept, sorted(suppressed, key=lambda s: (s["id"], s["reason"]))


def order_key(e: dict):
    s = e["_sort_start"]
    return (s is None, s or datetime.min.replace(tzinfo=_tz.utc),
            _TYPE_RANK[e["type"]], e["id"])


def _strip(e: dict) -> dict:
    return {k: v for k, v in e.items() if not k.startswith("_")}


def apply_filters(events: List[dict], owner_ids: Optional[Set[str]] = None,
                  types: Optional[Set[str]] = None,
                  buckets: Optional[Set[str]] = None) -> List[dict]:
    out = events
    if owner_ids:
        out = [e for e in out if (e["owner"] and e["owner"]["user_id"] in owner_ids)
               or owner_ids & set(e["participant_user_ids"])]
    if types:
        out = [e for e in out if e["type"] in types]
    if buckets:
        out = [e for e in out if e["bucket"] in buckets]
    return out


def in_range(e: dict, start_utc: datetime, end_utc: datetime) -> bool:
    """Timed events overlap [start, end); a point event sits inside it; an
    undated event belongs to no day."""
    s = e["_sort_start"]
    if s is None:
        return False
    s = s.replace(tzinfo=None)
    en = e.get("_sort_end")
    en = en.replace(tzinfo=None) if en else s
    return s < end_utc and (en > start_utc or s >= start_utc)


def decide(*, viewer_id: str, brand_id: str, is_manager: bool,
           candidates: Sequence[dict], source_status: Dict[str, dict],
           start_utc: datetime, end_utc: datetime,
           owner_ids: Optional[Set[str]] = None, types: Optional[Set[str]] = None,
           buckets: Optional[Set[str]] = None, cap: int = EVENT_CAP) -> dict:
    """The single read decision. `candidates` are already-normalised events."""
    authorised = [e for e in candidates
                  if visible_to(e, viewer_id, brand_id, is_manager)]
    kept, suppressed = dedupe(authorised)

    dated = [e for e in kept if e["_sort_start"] is not None
             and in_range(e, start_utc, end_utc)]
    undated = [e for e in kept if e["_sort_start"] is None]
    pool = dated + undated

    # Facets come from the authorised pool BEFORE the user's filters, so picking
    # a filter never removes the options needed to un-pick it.
    owners: Dict[str, str] = {}
    for e in pool:
        if e["owner"]:
            owners[e["owner"]["user_id"]] = e["owner"]["name"] or e["owner"]["user_id"]
    facets = {
        "owners": [{"user_id": k, "name": v}
                   for k, v in sorted(owners.items(), key=lambda kv: (kv[1] or "", kv[0]))],
        "types": [t for t in EVENT_TYPES if any(e["type"] == t for e in pool)],
        "buckets": [b for b in BUCKETS if any(e["bucket"] == b for e in pool)],
        "counts_by_bucket": {b: sum(1 for e in pool if e["bucket"] == b)
                             for b in BUCKETS},
    }

    shown = sorted(apply_filters(pool, owner_ids, types, buckets), key=order_key)
    truncated = len(shown) > cap
    shown = shown[:cap]

    unavailable_sources = sorted(k for k, v in source_status.items()
                                 if v.get("status") == "unavailable")
    return {
        "events": [_strip(e) for e in shown if e["_sort_start"] is not None],
        "unscheduled": [_strip(e) for e in shown if e["_sort_start"] is None],
        "total": len(shown),
        "truncated": truncated, "limit": cap,
        "facets": facets,
        "suppressed_duplicates": suppressed,
        "sources": source_status,
        # True when any source could not be read: the list is NOT the whole truth.
        "partial": bool(unavailable_sources) or any(
            v.get("status") == "truncated" for v in source_status.values()),
        "unavailable_sources": unavailable_sources,
        "read_only": True,
    }


def iso_utc_z(dt: Optional[datetime]) -> Optional[str]:
    return _iso_utc(dt)
