"""
Audit Log router and helper.

Task 1: self-contained audit ledger for admin-visible activity.
No other routes are wired to log_action() yet by design; this module only
defines the persistence helper and the read-only admin endpoint.
"""

import json
from datetime import datetime, date
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.deps import get_db, require_admin
from app.models.models import AuditLogEntry, User

router = APIRouter(prefix="/audit-log", tags=["audit-log"])


def _details_to_text(details: Any | None) -> str | None:
    """
    Details is stored as Text so callers can pass either a string or a small
    structured object. Dict/list payloads are JSON-serialized for readability.
    """
    if details is None:
        return None
    if isinstance(details, str):
        return details
    return json.dumps(details, sort_keys=True, default=str)


def log_action(
    db: Session,
    organization_id: str,
    actor_user_id: str,
    action: str,
    target_type: str,
    target_id: str,
    details: Any | None = None,
) -> AuditLogEntry:
    """
    Persist an audit event.

    Keep this helper small and boring on purpose: other routers/services can
    call it after completing sensitive actions like lead reassignment,
    password resets, suppression changes, template edits, imports, etc.
    """
    entry = AuditLogEntry(
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        action=action.strip(),
        target_type=target_type.strip(),
        target_id=target_id,
        details=_details_to_text(details),
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


class AuditLogEntryOut(BaseModel):
    id: str
    organization_id: str
    actor_user_id: str
    actor_name: str | None = None  # resolved separately, see list_audit_log - a raw UUID fragment alone doesn't tell an admin who actually did this
    action: str
    target_type: str
    target_id: str
    details: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AuditLogListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    entries: list[AuditLogEntryOut]


@router.get("/actions")
def list_audit_actions(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Return distinct action names for this org — used to populate the action filter dropdown."""
    from sqlalchemy import text
    rows = db.execute(text("""
        SELECT DISTINCT action FROM audit_log_entries
        WHERE organization_id = :org_id
        ORDER BY action
    """), {"org_id": current_user.organization_id}).mappings().all()
    return {"actions": [r["action"] for r in rows]}


@router.get("", response_model=AuditLogListResponse)
def list_audit_log(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
    action: str | None = Query(default=None, description="Optional exact action filter."),
    actor: str | None = Query(default=None, description="Filter by actor name (partial match)."),
    date_from: Optional[date] = Query(default=None, description="Start date (inclusive), YYYY-MM-DD."),
    date_to: Optional[date] = Query(default=None, description="End date (inclusive), YYYY-MM-DD."),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """
    Admin-only, organization-scoped audit log.

    A caller can only see events for current_user.organization_id. Even if a
    valid target_id from another org is guessed, it does not matter because
    the query is constrained at the organization boundary first.
    """
    query = db.query(AuditLogEntry).filter(AuditLogEntry.organization_id == current_user.organization_id)

    if action:
        query = query.filter(AuditLogEntry.action == action)

    if date_from:
        from datetime import datetime as dt
        query = query.filter(AuditLogEntry.created_at >= dt.combine(date_from, dt.min.time()))

    if date_to:
        from datetime import datetime as dt, timedelta
        query = query.filter(AuditLogEntry.created_at < dt.combine(date_to, dt.min.time()) + timedelta(days=1))

    # Actor name filter: resolve matching user IDs first, then filter by them
    if actor:
        matched_actors = (
            db.query(User.id)
            .filter(
                User.organization_id == current_user.organization_id,
                User.full_name.ilike(f"%{actor}%"),
            )
            .all()
        )
        actor_ids = [row[0] for row in matched_actors]
        if actor_ids:
            query = query.filter(AuditLogEntry.actor_user_id.in_(actor_ids))
        else:
            # No matching users — return empty result set
            query = query.filter(AuditLogEntry.actor_user_id == "__no_match__")

    total = query.count()
    entries = (
        query
        .order_by(AuditLogEntry.created_at.desc(), AuditLogEntry.id.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )

    # Resolve actor names in one batch query rather than one query per
    # entry - a raw actor_user_id alone doesn't tell an admin who actually
    # did something, which defeats much of the point of an audit log.
    actor_ids = {entry.actor_user_id for entry in entries}
    actors_by_id = {}
    if actor_ids:
        actors = db.query(User).filter(User.id.in_(actor_ids)).all()
        actors_by_id = {actor.id: actor.full_name for actor in actors}

    entries_out = [
        AuditLogEntryOut(
            id=entry.id,
            organization_id=entry.organization_id,
            actor_user_id=entry.actor_user_id,
            actor_name=actors_by_id.get(entry.actor_user_id),
            action=entry.action,
            target_type=entry.target_type,
            target_id=entry.target_id,
            details=entry.details,
            created_at=entry.created_at,
        )
        for entry in entries
    ]

    return AuditLogListResponse(total=total, limit=limit, offset=offset, entries=entries_out)
