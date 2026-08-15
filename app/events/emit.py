"""
AdvisorFlow Event Emitter
--------------------------
emit() writes a standardized event record to the platform_events table.
It is intentionally non-blocking and fail-safe: if the write fails for
any reason (DB hiccup, table missing, malformed data), it logs the error
and returns None rather than crashing the caller.

The event system is best-effort instrumentation. A failed emit must never
break a lead send, an appointment booking, or any user-facing operation.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session
from sqlalchemy import text

from app.events.schema import EventType

_log = logging.getLogger(__name__)

# Resolve the platform slug from environment (set per Render service).
# PLATFORM_SLUG must be one of: bookaboost | evosyspro | harmonyhustle
# Falls back to "unknown" so missing config never crashes an emit.
_DEFAULT_PLATFORM = os.environ.get("PLATFORM_SLUG", "bookaboost")


def emit(
    db: Session,
    event_type: EventType,
    *,
    org_id: Optional[str] = None,
    platform: Optional[str] = None,
    data: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
) -> Optional[str]:
    """
    Write a platform event to the platform_events table.

    Args:
        db:         Active SQLAlchemy session. The event is committed
                    independently so a caller rollback doesn't lose it.
        event_type: One of the EventType enum values.
        org_id:     The org this event belongs to (nullable for platform-
                    level events like demo.requested).
        platform:   Platform slug override. Defaults to PLATFORM_SLUG env var.
        data:       Arbitrary JSON-serializable payload (see schema.PAYLOAD_SCHEMA).
        user_id:    ID of the user who triggered this event (nullable for AI actions).

    Returns:
        The new event's UUID string, or None on failure.
    """
    import uuid
    event_id = str(uuid.uuid4())
    platform_slug = platform or _DEFAULT_PLATFORM

    try:
        payload_json = json.dumps(data or {}, default=str)
        db.execute(
            text("""
                INSERT INTO platform_events
                    (id, event_type, platform, org_id, user_id, data, occurred_at)
                VALUES
                    (:id, :event_type, :platform, :org_id, :user_id, :data, :occurred_at)
            """),
            {
                "id": event_id,
                "event_type": event_type.value if hasattr(event_type, "value") else str(event_type),
                "platform": platform_slug,
                "org_id": org_id,
                "user_id": user_id,
                "data": payload_json,
                "occurred_at": datetime.now(timezone.utc),
            }
        )
        db.commit()
        return event_id
    except Exception as exc:
        _log.warning(
            "emit(): failed to write event=%s org=%s platform=%s — %s",
            event_type, org_id, platform_slug, exc,
        )
        # Non-fatal: never propagate. The caller's operation continues.
        try:
            db.rollback()
        except Exception:
            pass
        return None
