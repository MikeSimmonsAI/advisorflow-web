"""Dependency-free ordering and paging rules for the lead conversation feeds.

Shared by /leads/{id}/history (communication_history.fetch) and
/leads/{id}/timeline so the cursor rule is stated once and provable without a
database.

THE RULE. The cursor is a timestamp and the next page asks for `ts < cursor`.
That is only exact if no timestamp is ever split across a page boundary: a row
that shares the last row's timestamp but did not fit is neither on this page
nor `< cursor` on the next, so it was silently lost (a bulk send stamped in the
same second is exactly that shape). So:

  * `fetch_with_ties` completes the tie group at each source's cut, and
  * `paginate` never ends a page inside a tie group (the page may exceed
    `limit` by the size of the final group, never fall short of it).
"""

from datetime import datetime
from typing import Any, Callable, Dict, List, Optional


def _tie_key(e: Dict[str, Any]):
    return (str(e.get("channel")), str(e.get("kind") or e.get("type")), str(e.get("id")))


def sort_newest_first(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Newest first; equal timestamps ordered by channel/kind/id so the order
    is identical on every request; undated events last. Two stable passes."""
    base = sorted(events, key=_tie_key)
    dated = sorted((e for e in base if e.get("timestamp") is not None),
                   key=lambda e: e["timestamp"], reverse=True)
    undated = [e for e in base if e.get("timestamp") is None]
    return dated + undated


def event_uid(e: Dict[str, Any]) -> str:
    """Identity that cannot collide across tables: ids are per-table, so kind
    and channel are part of it."""
    return "%s|%s|%s" % (e.get("kind") or e.get("type"), e.get("channel"), e.get("id"))


def paginate(events: List[Dict[str, Any]], limit: int) -> Dict[str, Any]:
    """The newest `limit` events, extended to finish the last tie group."""
    ordered = sort_newest_first(events)
    if len(ordered) <= limit:
        return {"events": ordered, "has_more": False, "next_before": None}
    cut = limit
    boundary = ordered[cut - 1]["timestamp"]
    if boundary is not None:
        while cut < len(ordered) and ordered[cut]["timestamp"] == boundary:
            cut += 1
    page = ordered[:cut]
    next_before = page[-1]["timestamp"]
    # Only undated rows left: nothing a timestamp cursor can address.
    has_more = len(ordered) > cut and next_before is not None
    return {"events": page, "has_more": has_more,
            "next_before": next_before if has_more else None}


def fetch_with_ties(query, column, per_source: int,
                    before: Optional[datetime] = None,
                    value_of: Optional[Callable[[Any], Any]] = None) -> list:
    """`per_source` newest rows older than `before`, plus every further row
    sharing the oldest fetched timestamp (so a LIMIT never splits a tie).
    `query` is SQLAlchemy-style; `value_of(row)` reads the paged value."""
    q = query.filter(column < before) if before else query
    rows = q.order_by(column.desc()).limit(per_source).all()
    if len(rows) < per_source or value_of is None:
        return rows
    last = value_of(rows[-1])
    if last is None:
        return rows
    have = {getattr(r, "id", id(r)) for r in rows}
    extra = [r for r in query.filter(column == last).all()
             if getattr(r, "id", id(r)) not in have]
    return rows + extra
