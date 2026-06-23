"""
Compliance Center router

ORIGIN NOTE: the core logic here (phone normalization, idempotent
duplicate handling, the permanent-DNC-updates-matching-lead behavior,
org-isolation pattern) was drafted by ChatGPT in a separate task, then
reviewed and corrected here before merging. The original draft assumed
a different codebase shape than this one actually has:
  - imported from a nonexistent app.db module (real: app.deps)
  - assumed Integer primary keys/foreign keys (real: String/UUID
    everywhere - Organization.id, Lead.id, User.id all use gen_uuid)
  - assumed models live in separate per-model files (real: one
    app/models/models.py)
  - the frontend used raw fetch() with no auth header at all, which
    would have failed against this app's real JWT-based auth
The business logic itself held up well and is preserved; only the
structural/integration assumptions needed fixing.
"""

import re
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.deps import get_db, require_admin
from app.models.models import User, Lead, SuppressionEntry, SuppressionSource
from app.routers.audit_log_router import log_action

router = APIRouter(prefix="/compliance", tags=["compliance"])


def normalize_phone(phone: str) -> str:
    """
    REAL BUG FIXED HERE: this used to produce a +1XXXXXXXXXX format,
    which never matched the actual format every imported Lead.phone
    value uses (digits-only, e.g. "12145550101", produced by
    dedup_service.normalize_phone). That mismatch meant the Compliance
    Center's "Add Permanent DNC" action could create a suppression
    entry but silently fail to ever flip the matching real Lead's
    status to DNC, since the SQL equality check never matched. Now
    delegates to the same shared normalization function the rest of
    the app already uses, so suppression entries and real lead phone
    numbers are always in the same format.
    """
    from app.services.dedup_service import normalize_phone as shared_normalize_phone
    normalized = shared_normalize_phone(phone)
    if len(normalized) != 11 or not normalized.startswith("1"):
        raise HTTPException(status_code=422, detail="Phone must be a valid 10-digit US number.")
    return normalized


class SuppressionCreate(BaseModel):
    phone: str = Field(..., min_length=7, max_length=32)
    reason: str = Field(..., min_length=1, max_length=500)
    source: SuppressionSource = SuppressionSource.MANUAL


class PermanentDNCCreate(BaseModel):
    phone: str = Field(..., min_length=7, max_length=32)
    reason: str = Field(default="Permanent DNC", max_length=500)


class SuppressionOut(BaseModel):
    id: str
    phone: str
    reason: str
    source: SuppressionSource
    added_at: datetime

    class Config:
        from_attributes = True


class SuppressionStats(BaseModel):
    total: int
    manual: int
    reply_stop: int


class SuppressionListResponse(BaseModel):
    stats: SuppressionStats
    entries: list[SuppressionOut]


def _find_existing_entry(db: Session, organization_id: str, normalized_phone: str) -> SuppressionEntry | None:
    return (
        db.query(SuppressionEntry)
        .filter(SuppressionEntry.organization_id == organization_id, SuppressionEntry.phone == normalized_phone)
        .first()
    )


def _build_stats(db: Session, organization_id: str) -> SuppressionStats:
    entries = db.query(SuppressionEntry).filter(SuppressionEntry.organization_id == organization_id).all()
    manual = sum(1 for e in entries if e.source == SuppressionSource.MANUAL)
    reply_stop = sum(1 for e in entries if e.source == SuppressionSource.REPLY_STOP)
    return SuppressionStats(total=manual + reply_stop, manual=manual, reply_stop=reply_stop)


@router.get("/suppression-list", response_model=SuppressionListResponse)
def list_suppression_entries(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    entries = (
        db.query(SuppressionEntry)
        .filter(SuppressionEntry.organization_id == current_user.organization_id)
        .order_by(SuppressionEntry.added_at.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    return SuppressionListResponse(stats=_build_stats(db, current_user.organization_id), entries=entries)


@router.post("/suppression-list", response_model=SuppressionOut, status_code=status.HTTP_201_CREATED)
def add_suppression_entry(
    payload: SuppressionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    normalized_phone = normalize_phone(payload.phone)

    existing = _find_existing_entry(db, current_user.organization_id, normalized_phone)
    if existing:
        # Idempotent: adding the same number twice returns the existing
        # record rather than creating a duplicate row (the org+phone
        # unique constraint would reject a true duplicate insert anyway -
        # this check makes that case a clean 201 instead of a 500).
        return existing

    entry = SuppressionEntry(
        organization_id=current_user.organization_id,
        phone=normalized_phone,
        reason=payload.reason.strip(),
        source=payload.source,
    )
    db.add(entry)
    db.commit()

    log_action(
        db, current_user.organization_id, current_user.id,
        action="compliance.suppress", target_type="suppression_entry", target_id=entry.id,
        details={"phone": normalized_phone, "reason": entry.reason, "source": entry.source.value if hasattr(entry.source, "value") else entry.source},
    )

    return entry


@router.post("/permanent-dnc", response_model=SuppressionOut, status_code=status.HTTP_201_CREATED)
def add_permanent_dnc(
    payload: PermanentDNCCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """
    Adds a number to the suppression list AND, if a matching Lead exists
    in the same organization, sets that Lead's status to DNC - the
    manual "Add Permanent DNC" action from the Compliance Center.
    """
    normalized_phone = normalize_phone(payload.phone)

    entry = _find_existing_entry(db, current_user.organization_id, normalized_phone)
    if not entry:
        entry = SuppressionEntry(
            organization_id=current_user.organization_id,
            phone=normalized_phone,
            reason=(payload.reason or "Permanent DNC").strip(),
            source=SuppressionSource.MANUAL,
        )
        db.add(entry)

    lead = (
        db.query(Lead)
        .filter(Lead.organization_id == current_user.organization_id, Lead.phone == normalized_phone)
        .first()
    )
    if lead:
        lead.status = "dnc"

    db.commit()

    log_action(
        db, current_user.organization_id, current_user.id,
        action="compliance.permanent_dnc", target_type="suppression_entry", target_id=entry.id,
        details={
            "phone": normalized_phone,
            "reason": entry.reason,
            "matched_lead_id": lead.id if lead else None,
        },
    )

    return entry


@router.delete("/suppression-list/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_suppression_entry(
    entry_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    entry = (
        db.query(SuppressionEntry)
        .filter(SuppressionEntry.id == entry_id, SuppressionEntry.organization_id == current_user.organization_id)
        .first()
    )
    if not entry:
        raise HTTPException(status_code=404, detail="Suppression entry not found.")

    # Capture details before delete - the row (and entry.id) won't exist
    # to reference after db.delete(). This is the highest-stakes compliance
    # action in this router: removing a number from suppression means it
    # becomes contactable again, so this absolutely needs a paper trail.
    deleted_phone = entry.phone
    deleted_reason = entry.reason
    deleted_id = entry.id

    db.delete(entry)
    db.commit()

    log_action(
        db, current_user.organization_id, current_user.id,
        action="compliance.unsuppress", target_type="suppression_entry", target_id=deleted_id,
        details={"phone": deleted_phone, "original_reason": deleted_reason},
    )

    return None
