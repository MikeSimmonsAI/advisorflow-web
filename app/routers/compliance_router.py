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

from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.deps import get_db, require_admin, get_current_user
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
    current_user: User = Depends(get_current_user),   # ALL users can view
    limit: int = Query(default=500, ge=1, le=1000),
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
    current_user: User = Depends(get_current_user),   # ALL users can add
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

    leads = (
        db.query(Lead)
        .filter(Lead.organization_id == current_user.organization_id, Lead.phone == normalized_phone)
        .all()
    )
    for lead in leads:
        lead.status = "dnc"

    db.commit()

    log_action(
        db, current_user.organization_id, current_user.id,
        action="compliance.permanent_dnc", target_type="suppression_entry", target_id=entry.id,
        details={
            "phone": normalized_phone,
            "reason": entry.reason,
            "matched_lead_ids": [lead.id for lead in leads],
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


# ── God-admin: master cross-org suppression view ──────────────────────────────

class MasterSuppressionEntry(BaseModel):
    id: str
    organization_id: str
    org_name: str | None = None
    phone: str
    reason: str
    source: SuppressionSource
    added_at: datetime

    class Config:
        from_attributes = True


@router.get("/master-suppression-list")
def master_suppression_list(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    limit: int = Query(default=1000, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
):
    """
    God-admin only: returns suppression entries across ALL organizations.
    Lets the platform operator see who is suppressed where, and (if needed)
    push a phone number to suppression across every org at once.
    """
    if current_user.role not in ("god_admin",):
        raise HTTPException(status_code=403, detail="God admin access required.")

    from sqlalchemy import text
    rows = db.execute(text("""
        SELECT s.id, s.organization_id, o.name as org_name,
               s.phone, s.reason, s.source, s.added_at
        FROM suppression_entries s
        LEFT JOIN organizations o ON o.id = s.organization_id
        ORDER BY s.added_at DESC
        LIMIT :limit OFFSET :offset
    """), {"limit": limit, "offset": offset}).mappings().all()

    total = db.execute(text("SELECT COUNT(*) FROM suppression_entries")).scalar()

    return {
        "total": total,
        "entries": [dict(r) for r in rows],
    }


@router.post("/master-suppress")
def master_suppress_all_orgs(
    payload: PermanentDNCCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    God-admin only: adds a phone number to the suppression list for EVERY
    organization on the platform. Use when someone must never receive any
    message from any client org ever again.
    """
    if current_user.role not in ("god_admin",):
        raise HTTPException(status_code=403, detail="God admin access required.")

    from app.models.models import Organization
    normalized = None
    try:
        normalized = normalize_phone(payload.phone)
    except HTTPException:
        raise

    orgs = db.query(Organization).filter(Organization.is_active == True).all()
    added = []
    for org in orgs:
        existing = _find_existing_entry(db, org.id, normalized)
        if not existing:
            entry = SuppressionEntry(
                organization_id=org.id,
                phone=normalized,
                reason=(payload.reason or "Platform-level suppression").strip(),
                source=SuppressionSource.MANUAL,
            )
            db.add(entry)
            added.append(org.id)

    db.commit()

    log_action(
        db, current_user.organization_id, current_user.id,
        action="compliance.master_suppress", target_type="suppression_entry", target_id=normalized,
        details={"phone": normalized, "orgs_added": added, "total_orgs": len(orgs)},
    )

    return {"phone": normalized, "added_to_orgs": len(added), "already_suppressed_in": len(orgs) - len(added)}
