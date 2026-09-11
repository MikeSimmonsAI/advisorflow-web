"""MATERIALISATION AND FRESHNESS - fast answers that admit how old they are.

TWO REQUIREMENTS THAT PULL AGAINST EACH OTHER. Section 23 says a manager's
page must not run dozens of expensive queries. Section 13 says a manager must
never be shown a stale number as though it were live. A cache satisfies the
first and breaks the second unless the cache carries its own age - so every
stored payload here carries two timestamps and the reader is told which state
it is in.

THREE STATES, NOT A BOOLEAN:

    LIVE     computed within the last two minutes.
    RECENT   older than that, and NOTHING HAS HAPPENED SINCE. The newest
             authoritative event this scope can see is older than the
             computation, so the payload is old and still correct.
    STALE    older than fifteen minutes, or the last pass failed.

The middle state is the one a boolean cannot express, and it is the common
case: most workspaces are quiet most of the time, and marking their dashboards
"out of date" every two minutes would train people to ignore the marker.

THE CACHE KEY IS THE SCOPE. `Scope.cache_key` returns the whole tuple -
scope_type, scope_id, view, window - and there is no way to construct half of
one. Section 17 lists cached metrics among the things that leak tenants, and a
cache keyed on the view alone is exactly that leak: two customers asking for
`command_center` would share a row.

A FAILED COMPUTATION KEEPS THE LAST GOOD PAYLOAD and records the error. The
alternative - storing the failure as an empty payload - renders as a workspace
with nothing happening, which is the silent-failure shape section 24 is about.
"""

import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Optional

from sqlalchemy.orm import Session

from app.models.workforce_intelligence_models import AIIntelligenceReadModel
from app.services.workforce_intelligence import collect
from app.services.workforce_intelligence import constants as C
from app.services.workforce_intelligence.scope import Scope

_log = logging.getLogger(__name__)


def _row(db: Session, scope: Scope, view_key: str, window_key: str):
    scope_type, scope_id, view, window = scope.cache_key(view_key, window_key)
    return (db.query(AIIntelligenceReadModel)
            .filter(AIIntelligenceReadModel.scope_type == scope_type,
                    AIIntelligenceReadModel.scope_id == scope_id,
                    AIIntelligenceReadModel.view_key == view,
                    AIIntelligenceReadModel.window_key == window)
            .first())


def freshness(row: Optional[AIIntelligenceReadModel],
              watermark: Optional[datetime], *,
              now: Optional[datetime] = None) -> Dict[str, Any]:
    """How old this answer is, and whether that matters."""
    now = now or datetime.utcnow()
    if row is None or not row.payload:
        return {
            "state": C.FRESH_ABSENT,
            "label": C.FRESHNESS_LABELS[C.FRESH_ABSENT],
            "computed_at": None, "age_seconds": None,
            "source_watermark": (watermark.isoformat() if watermark else None),
            "statement": ("This has not been computed yet. What is shown is "
                          "missing, not empty."),
        }
    age = max(0, int((now - row.computed_at).total_seconds()))
    if row.last_error and row.last_error_at and (
            row.computed_at is None or row.last_error_at > row.computed_at):
        state = C.FRESH_STALE
    elif age <= C.FRESH_SECONDS:
        state = C.FRESH_LIVE
    elif watermark is not None and row.computed_at is not None \
            and watermark <= row.computed_at:
        # NOTHING HAS HAPPENED SINCE THIS WAS COMPUTED, so it is old and
        # correct. This is the branch that stops a quiet workspace being
        # permanently labelled out of date.
        state = C.FRESH_RECENT
    elif age >= C.STALE_SECONDS:
        state = C.FRESH_STALE
    else:
        state = C.FRESH_RECENT
    return {
        "state": state,
        "label": C.FRESHNESS_LABELS[state],
        "computed_at": row.computed_at.isoformat() if row.computed_at else None,
        "age_seconds": age,
        "compute_ms": row.compute_ms,
        "source_watermark": (row.source_watermark.isoformat()
                             if row.source_watermark else None),
        "newest_event": watermark.isoformat() if watermark else None,
        "window": row.window_key,
        "last_error": row.last_error,
        "statement": _statement(state, age, row),
    }


def _statement(state: str, age: int, row) -> str:
    if state == C.FRESH_LIVE:
        return "Computed moments ago."
    if state == C.FRESH_RECENT:
        return ("Computed %d minutes ago. Nothing has happened in this "
                "workspace since, so these numbers are still correct."
                % (age // 60))
    if state == C.FRESH_STALE and row.last_error:
        return ("Last computed %d minutes ago and the most recent attempt "
                "failed. These numbers may be out of date."
                % (age // 60))
    return ("Computed %d minutes ago and not refreshed since. Newer activity "
            "may not be reflected here." % (age // 60))


def read(db: Session, scope: Scope, view_key: str, *,
         window_key: str = C.DEFAULT_WINDOW,
         now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """The stored payload for one view, with its freshness attached."""
    now = now or datetime.utcnow()
    row = _row(db, scope, view_key, window_key)
    if row is None or not row.payload:
        return None
    try:
        payload = json.loads(row.payload)
    except (ValueError, TypeError):
        _log.warning("t9 readmodel: unreadable payload for %s/%s %s",
                     scope.scope_type, scope.scope_id, view_key)
        return None
    mark = collect.watermark(db, scope)
    return {"data": payload,
            "freshness": freshness(row, mark, now=now),
            "generation": row.generation}


def materialise(db: Session, scope: Scope, view_key: str,
                build: Callable[[], Dict[str, Any]], *,
                window_key: str = C.DEFAULT_WINDOW,
                now: Optional[datetime] = None) -> Dict[str, Any]:
    """Compute a view and store it, keeping the last good copy on failure."""
    now = now or datetime.utcnow()
    row = _row(db, scope, view_key, window_key)
    if row is None:
        scope_type, scope_id, view, window = scope.cache_key(view_key,
                                                             window_key)
        row = AIIntelligenceReadModel(
            scope_type=scope_type, scope_id=scope_id, view_key=view,
            window_key=window, computed_at=now, generation=0)
        db.add(row)

    mark = collect.watermark(db, scope)
    started = datetime.utcnow()
    try:
        payload = build()
    except Exception as exc:                                 # noqa: BLE001
        _log.exception("t9 readmodel: %s failed for %s/%s", view_key,
                       scope.scope_type, scope.scope_id)
        row.last_error = str(exc)[:255]
        row.last_error_at = datetime.utcnow()
        db.flush()
        # THE OLD PAYLOAD SURVIVES. A screen then shows old-but-true with a
        # stale marker instead of an empty dashboard that reads as calm.
        return {"data": _safe_load(row.payload),
                "freshness": freshness(row, mark, now=now),
                "error": row.last_error,
                "generation": row.generation}

    encoded = json.dumps(payload, default=str)
    row.payload = encoded
    row.payload_digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    row.computed_at = now
    row.compute_ms = int((datetime.utcnow() - started).total_seconds() * 1000)
    row.source_watermark = mark
    row.generation = int(row.generation or 0) + 1
    row.row_count = _count_rows(payload)
    row.last_error = None
    row.last_error_at = None
    db.flush()
    return {"data": payload, "freshness": freshness(row, mark, now=now),
            "generation": row.generation}


def _safe_load(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _count_rows(payload: Any) -> int:
    """A rough size, for the diagnostics screen. Never used in a decision."""
    if isinstance(payload, dict):
        for key in ("items", "cards", "findings", "contradictions",
                    "employees"):
            value = payload.get(key)
            if isinstance(value, (list, dict)):
                return len(value)
    if isinstance(payload, list):
        return len(payload)
    return 0


def get_or_build(db: Session, scope: Scope, view_key: str,
                 build: Callable[[], Dict[str, Any]], *,
                 window_key: str = C.DEFAULT_WINDOW,
                 max_age_seconds: int = C.FRESH_SECONDS,
                 force: bool = False,
                 now: Optional[datetime] = None) -> Dict[str, Any]:
    """Serve the stored payload if it is current enough; otherwise recompute.

    THE DEFAULT IS TO RECOMPUTE RATHER THAN TO SERVE SOMETHING OLD, because
    the cost this cache exists to avoid is a page issuing dozens of queries,
    not a page being computed at all. Two minutes is long enough that a
    manager clicking between tabs does not recompute the same numbers four
    times, and short enough that nobody is looking at yesterday.
    """
    now = now or datetime.utcnow()
    if not force:
        row = _row(db, scope, view_key, window_key)
        if row is not None and row.payload and row.computed_at:
            age = (now - row.computed_at).total_seconds()
            if age <= max_age_seconds:
                cached = read(db, scope, view_key, window_key=window_key,
                              now=now)
                if cached is not None:
                    cached["served_from_cache"] = True
                    return cached
    built = materialise(db, scope, view_key, build, window_key=window_key,
                        now=now)
    built["served_from_cache"] = False
    return built


def invalidate(db: Session, scope: Scope, view_key: Optional[str] = None,
               *, window_key: Optional[str] = None) -> int:
    """Drop stored payloads for this scope. Never for any other.

    The scope columns are part of every filter here for the same reason they
    are part of the key: an invalidation that reached past its tenant would be
    a cross-tenant write, which is a worse version of a cross-tenant read.
    """
    q = db.query(AIIntelligenceReadModel).filter(
        AIIntelligenceReadModel.scope_type == scope.scope_type,
        AIIntelligenceReadModel.scope_id == (scope.scope_id or ""))
    if view_key:
        q = q.filter(AIIntelligenceReadModel.view_key == view_key)
    if window_key:
        q = q.filter(AIIntelligenceReadModel.window_key == window_key)
    count = 0
    for row in q.all():
        row.payload = None
        row.payload_digest = None
        row.computed_at = datetime.utcnow() - timedelta(
            seconds=C.STALE_SECONDS + 1)
        count += 1
    db.flush()
    return count
