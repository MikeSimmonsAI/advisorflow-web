"""
import_batch_router.py — REST API for lead import batches.

GET  /import-batches                         list batches (paginated)
POST /import-batches                         upload CSV/XLSX → stage
GET  /import-batches/{batch_id}              batch detail
GET  /import-batches/{batch_id}/rows         staged rows (filtered, paginated)
PATCH /import-batches/{batch_id}/rows/{rid}  set row decision
POST /import-batches/{batch_id}/rows/bulk-review  bulk decisions
POST /import-batches/{batch_id}/ready        advance to ready_to_commit
POST /import-batches/{batch_id}/commit       commit all accepted/merged
POST /import-batches/{batch_id}/archive      archive batch
"""
import json, logging, os, shutil, tempfile
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.import_models import (
    ImportBatch, ImportBatchStatus, ImportRowReviewStatus, ImportStagedRow,
)
from app.models.models import gen_uuid
from app.services.import_permissions import (
    require_import_leads, require_import_review,
    require_import_commit, require_import_admin,
)
from app.services.import_staging_service import stage_batch
from app.services.import_commit_service import commit_batch as _commit_batch

router = APIRouter(prefix="/import-batches", tags=["import-batches"])
log = logging.getLogger(__name__)

ALLOWED_EXT = {".csv", ".xlsx"}

def _batch_dict(b: ImportBatch) -> dict:
    return {
        "id": b.id,
        "display_name": b.display_name,
        "source_type": b.source_type,
        "source_filename": b.source_filename,
        "status": b.status,
        "total_rows": b.total_rows,
        "new_rows": b.new_rows,
        "matched_rows": b.matched_rows,
        "warning_rows": b.warning_rows,
        "rejected_rows": b.rejected_rows,
        "committed_rows": b.committed_rows,
        "merged_rows": b.merged_rows,
        "pending_rows": b.pending_rows,
        "error_message": b.error_message,
        "created_at": b.created_at.isoformat() if b.created_at else None,
        "committed_at": b.committed_at.isoformat() if b.committed_at else None,
        "created_by_id": b.created_by_id,
        "committed_by_id": b.committed_by_id,
    }

def _row_dict(r: ImportStagedRow) -> dict:
    errors = []
    if r.validation_errors:
        try: errors = json.loads(r.validation_errors)
        except Exception: errors = [r.validation_errors]
    return {
        "id": r.id, "batch_id": r.batch_id, "row_number": r.row_number,
        "first_name": r.first_name, "last_name": r.last_name,
        "phone_raw": r.phone_raw, "phone_normalized": r.phone_normalized,
        "email_normalized": r.email_normalized,
        "street_address": r.street_address, "city": r.city,
        "state": r.state, "zip_code": r.zip_code,
        "source_category": r.source_category, "tier": r.tier,
        "validation_status": r.validation_status, "validation_errors": errors,
        "duplicate_status": r.duplicate_status, "match_confidence": r.match_confidence,
        "matched_lead_id": r.matched_lead_id,
        "review_status": r.review_status, "review_note": r.review_note,
        "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
        "committed_at": r.committed_at.isoformat() if r.committed_at else None,
    }

# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("")
def list_batches(
    status: str = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user=Depends(require_import_leads),
):
    q = db.query(ImportBatch).filter(ImportBatch.organization_id == user.organization_id)
    if status:
        q = q.filter(ImportBatch.status == status)
    total = q.count()
    batches = q.order_by(ImportBatch.created_at.desc()).offset((page - 1) * per_page).limit(per_page).all()
    return {"total": total, "page": page, "per_page": per_page, "batches": [_batch_dict(b) for b in batches]}


@router.post("")
async def upload_batch(
    file: UploadFile = File(...),
    display_name: str = Form(...),
    db: Session = Depends(get_db),
    user=Depends(require_import_leads),
):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"Unsupported file type '{ext}'. Allowed: {', '.join(ALLOWED_EXT)}")
    batch_id = gen_uuid()
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            shutil.copyfileobj(file.file, tmp)
            tmp_path = tmp.name
        batch = ImportBatch(
            id=batch_id, organization_id=user.organization_id,
            display_name=display_name, source_type=ext.lstrip("."),
            source_filename=file.filename,
            status=ImportBatchStatus.UPLOADING,
            created_by_id=user.id, created_at=datetime.now(timezone.utc),
        )
        db.add(batch)
        db.commit()
        stage_batch(batch_id, user.organization_id, tmp_path, ext.lstrip("."), db)
        db.refresh(batch)
        return _batch_dict(batch)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


@router.get("/{batch_id}")
def get_batch(batch_id: str, db: Session = Depends(get_db), user=Depends(require_import_review)):
    b = db.query(ImportBatch).filter(ImportBatch.id == batch_id,
                                     ImportBatch.organization_id == user.organization_id).first()
    if not b: raise HTTPException(404, "Batch not found")
    return _batch_dict(b)


@router.get("/{batch_id}/rows")
def list_rows(
    batch_id: str,
    review_status: str = Query(None), duplicate_status: str = Query(None),
    validation_status: str = Query(None), search: str = Query(None),
    page: int = Query(1, ge=1), per_page: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db), user=Depends(require_import_review),
):
    b = db.query(ImportBatch).filter(ImportBatch.id == batch_id,
                                     ImportBatch.organization_id == user.organization_id).first()
    if not b: raise HTTPException(404, "Batch not found")
    q = db.query(ImportStagedRow).filter(ImportStagedRow.batch_id == batch_id)
    if review_status: q = q.filter(ImportStagedRow.review_status == review_status)
    if duplicate_status: q = q.filter(ImportStagedRow.duplicate_status == duplicate_status)
    if validation_status: q = q.filter(ImportStagedRow.validation_status == validation_status)
    if search:
        s = f"%{search}%"
        q = q.filter(
            ImportStagedRow.first_name.ilike(s) |
            ImportStagedRow.last_name.ilike(s) |
            ImportStagedRow.phone_raw.ilike(s) |
            ImportStagedRow.email_normalized.ilike(s)
        )
    total = q.count()
    rows = q.order_by(ImportStagedRow.row_number).offset((page-1)*per_page).limit(per_page).all()
    return {"total": total, "page": page, "per_page": per_page,
            "rows": [_row_dict(r) for r in rows], "batch": _batch_dict(b)}


@router.patch("/{batch_id}/rows/{row_id}")
def update_row(
    batch_id: str, row_id: str, body: dict,
    db: Session = Depends(get_db), user=Depends(require_import_review),
):
    b = db.query(ImportBatch).filter(ImportBatch.id == batch_id,
                                     ImportBatch.organization_id == user.organization_id).first()
    if not b: raise HTTPException(404, "Batch not found")
    row = db.query(ImportStagedRow).filter(ImportStagedRow.id == row_id,
                                            ImportStagedRow.batch_id == batch_id).first()
    if not row: raise HTTPException(404, "Row not found")
    if row.review_status == ImportRowReviewStatus.COMMITTED:
        raise HTTPException(409, "Row already committed")
    allowed = {"accepted", "merged", "rejected", "pending"}
    if "review_status" in body:
        if body["review_status"] not in allowed:
            raise HTTPException(400, f"review_status must be one of: {allowed}")
        row.review_status = body["review_status"]
        row.reviewed_at = datetime.now(timezone.utc)
        row.reviewed_by_id = user.id
    if "review_note" in body:
        row.review_note = str(body["review_note"])[:500]
    db.commit()
    b.recount(db)
    db.commit()
    return _row_dict(row)


@router.post("/{batch_id}/rows/bulk-review")
def bulk_review(
    batch_id: str, body: dict,
    db: Session = Depends(get_db), user=Depends(require_import_review),
):
    b = db.query(ImportBatch).filter(ImportBatch.id == batch_id,
                                     ImportBatch.organization_id == user.organization_id).first()
    if not b: raise HTTPException(404, "Batch not found")
    new_status = body.get("review_status")
    if new_status not in {"accepted", "merged", "rejected", "pending"}:
        raise HTTPException(400, "Invalid review_status")
    row_ids = body.get("row_ids", [])
    if not row_ids: raise HTTPException(400, "row_ids required")
    rows = db.query(ImportStagedRow).filter(
        ImportStagedRow.batch_id == batch_id,
        ImportStagedRow.id.in_(row_ids),
        ImportStagedRow.review_status != ImportRowReviewStatus.COMMITTED,
    ).all()
    for row in rows:
        row.review_status = new_status
        row.reviewed_at = datetime.now(timezone.utc)
        row.reviewed_by_id = user.id
    db.commit()
    b.recount(db)
    db.commit()
    return {"updated": len(rows), "batch": _batch_dict(b)}


@router.post("/{batch_id}/ready")
def mark_ready(batch_id: str, db: Session = Depends(get_db), user=Depends(require_import_review)):
    b = db.query(ImportBatch).filter(ImportBatch.id == batch_id,
                                     ImportBatch.organization_id == user.organization_id).first()
    if not b: raise HTTPException(404, "Batch not found")
    if b.status not in (ImportBatchStatus.READY_FOR_REVIEW, ImportBatchStatus.REVIEWING):
        raise HTTPException(409, f"Cannot advance from status '{b.status}'")
    b.status = ImportBatchStatus.READY_TO_COMMIT
    db.commit()
    return _batch_dict(b)


@router.post("/{batch_id}/commit")
def commit_batch_route(batch_id: str, db: Session = Depends(get_db), user=Depends(require_import_commit)):
    b = db.query(ImportBatch).filter(ImportBatch.id == batch_id,
                                     ImportBatch.organization_id == user.organization_id).first()
    if not b: raise HTTPException(404, "Batch not found")
    if b.status in (ImportBatchStatus.COMMITTING, ImportBatchStatus.COMMITTED):
        raise HTTPException(409, f"Batch is already {b.status}")
    if b.status == ImportBatchStatus.ARCHIVED:
        raise HTTPException(409, "Cannot commit an archived batch")
    result = _commit_batch(batch_id, user.organization_id, db, user.id)
    return _batch_dict(result)


@router.post("/{batch_id}/archive")
def archive_batch(batch_id: str, db: Session = Depends(get_db), user=Depends(require_import_admin)):
    b = db.query(ImportBatch).filter(ImportBatch.id == batch_id,
                                     ImportBatch.organization_id == user.organization_id).first()
    if not b: raise HTTPException(404, "Batch not found")
    if b.status == ImportBatchStatus.COMMITTING:
        raise HTTPException(409, "Cannot archive a batch that is currently committing")
    b.status = ImportBatchStatus.ARCHIVED
    db.commit()
    return _batch_dict(b)


@router.delete("/{batch_id}")
def delete_batch(batch_id: str, db: Session = Depends(get_db), user=Depends(require_import_admin)):
    b = db.query(ImportBatch).filter(ImportBatch.id == batch_id,
                                     ImportBatch.organization_id == user.organization_id).first()
    if not b: raise HTTPException(404, "Batch not found")
    if b.status in (ImportBatchStatus.COMMITTING, ImportBatchStatus.COMMITTED,
                    ImportBatchStatus.PARTIALLY_COMMITTED):
        raise HTTPException(409, "Cannot delete a committed or in-progress batch")
    db.query(ImportStagedRow).filter(ImportStagedRow.batch_id == batch_id).delete()
    db.delete(b)
    db.commit()
    return {"deleted": True, "id": batch_id}
