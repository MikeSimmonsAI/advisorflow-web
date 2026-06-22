"""
Audit Log router and helper.

Task 1: self-contained audit ledger for admin-visible activity.
No other routes are wired to log_action() yet by design; this module only
defines the persistence helper and the read-only admin endpoint.
"""

import json
from datetime import datetime
from typing import Any

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


@router.get("", response_model=AuditLogListResponse)
def list_audit_log(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
    action: str | None = Query(default=None, description="Optional exact action filter."),
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

    total = query.count()
    entries = (
        query
        .order_by(AuditLogEntry.created_at.desc(), AuditLogEntry.id.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )

    return AuditLogListResponse(total=total, limit=limit, offset=offset, entries=entries)
