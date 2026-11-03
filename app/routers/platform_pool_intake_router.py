"""God-only Universal Intake destination: platform_acquisition_pool (#470 Stage B).

Upload -> map -> analyze -> review (reject/restore) -> commit -> (rollback).
Everything lands in EvoSys's own master prospect pool (lead_intel_prospects);
nothing is assigned to a client, sent, or marked outreach-ready here.
"""
from typing import Dict, List, Optional

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.services.acquisition.admins import require_acq_master
from app.deps import get_db, require_god
from app.models.import_models import ImportBatch, ImportStagedRow
from app.models.models import User
from app.services.intake import platform_pool as PP

router = APIRouter(prefix="/intake/platform-pool", tags=["intake"])


def _audit(db, user, action, batch_id, details=None):
    try:
        from app.routers.audit_log_router import log_action
        log_action(db, PP.PLATFORM_ORG_ID, user.id, action, "import_batch", batch_id, details=details)
    except Exception:  # noqa: BLE001 - an audit hiccup never undoes the import step
        pass


def _load(db, batch_id):
    try:
        return PP.load(db, batch_id)
    except PP.PoolImportError as exc:
        raise HTTPException(404, str(exc))


@router.get("/batches")
def list_batches(db: Session = Depends(get_db), god: User = Depends(require_acq_master)):
    rows = (db.query(ImportBatch).filter(ImportBatch.pipeline == PP.PIPELINE,
                                         ImportBatch.organization_id == PP.PLATFORM_ORG_ID)
            .order_by(ImportBatch.created_at.desc()).limit(50).all())
    return {"items": [PP.batch_payload(db, b) for b in rows], "destination": PP.DESTINATION}


@router.post("/batches")
async def upload(file: UploadFile = File(...), source_system: str = Form(...),
                 display_name: Optional[str] = Form(None),
                 db: Session = Depends(get_db), god: User = Depends(require_acq_master)):
    from app.services.intake.engine import MAX_UPLOAD_BYTES
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "The file is too large")
    try:
        b = PP.create_batch(db, god, content=content, filename=file.filename or "upload.csv",
                            source_system=source_system, display_name=display_name)
    except PP.PoolImportError as exc:
        db.rollback()
        raise HTTPException(400, str(exc))
    _audit(db, god, "platform_pool.uploaded", b.id, {"rows": b.total_rows, "source": b.source_system})
    db.commit()
    return PP.batch_payload(db, b)


@router.get("/batches/{batch_id}")
def get_batch(batch_id: str, db: Session = Depends(get_db), god: User = Depends(require_acq_master)):
    return PP.batch_payload(db, _load(db, batch_id))


@router.put("/batches/{batch_id}/mapping")
def put_mapping(batch_id: str, mapping: Dict[str, Optional[str]] = Body(...),
                db: Session = Depends(get_db), god: User = Depends(require_acq_master)):
    b = _load(db, batch_id)
    try:
        PP.save_mapping(db, b, mapping)
    except PP.PoolImportError as exc:
        raise HTTPException(422, str(exc))
    return PP.batch_payload(db, b)


@router.post("/batches/{batch_id}/analyze")
def analyze(batch_id: str, db: Session = Depends(get_db), god: User = Depends(require_acq_master)):
    b = _load(db, batch_id)
    try:
        counts = PP.analyze(db, b)
    except PP.PoolImportError as exc:
        raise HTTPException(409, str(exc))
    _audit(db, god, "platform_pool.analyzed", b.id, counts)
    db.commit()
    return PP.batch_payload(db, b)


@router.get("/batches/{batch_id}/rows")
def rows(batch_id: str, filter: Optional[str] = Query(None), limit: int = Query(200, le=1000),
         offset: int = Query(0, ge=0), db: Session = Depends(get_db), god: User = Depends(require_acq_master)):
    b = _load(db, batch_id)
    q = db.query(ImportStagedRow).filter(ImportStagedRow.batch_id == b.id)
    if filter == "invalid":
        q = q.filter(ImportStagedRow.validation_status == "invalid")
    elif filter == "duplicate":
        q = q.filter(ImportStagedRow.duplicate_status != "new")
    elif filter == "rejected":
        q = q.filter(ImportStagedRow.review_status == "rejected")
    total = q.count()
    items = q.order_by(ImportStagedRow.row_number).offset(offset).limit(limit).all()
    return {"total": total, "items": [PP.row_payload(r) for r in items]}


@router.post("/batches/{batch_id}/review")
def review(batch_id: str, payload: dict = Body(...), db: Session = Depends(get_db),
           god: User = Depends(require_acq_master)):
    b = _load(db, batch_id)
    try:
        n = PP.review(db, b, god, payload.get("row_ids") or [], payload.get("decision") or "", payload.get("note"))
    except PP.PoolImportError as exc:
        raise HTTPException(409, str(exc))
    _audit(db, god, "platform_pool.reviewed", b.id, {"rows": n, "decision": payload.get("decision")})
    db.commit()
    return {"updated": n}


@router.post("/batches/{batch_id}/commit")
def commit(batch_id: str, db: Session = Depends(get_db), god: User = Depends(require_acq_master)):
    b = _load(db, batch_id)
    try:
        report = PP.commit(db, b, god)
    except PP.PoolImportError as exc:
        db.rollback()
        raise HTTPException(409, str(exc))
    _audit(db, god, "platform_pool.committed", b.id,
           {k: report.get(k) for k in ("committed", "skipped_duplicate", "skipped_invalid", "skipped_rejected")})
    db.commit()
    return report


@router.post("/batches/{batch_id}/rollback")
def rollback(batch_id: str, db: Session = Depends(get_db), god: User = Depends(require_acq_master)):
    b = _load(db, batch_id)
    try:
        report = PP.rollback(db, b, god)
    except PP.PoolImportError as exc:
        raise HTTPException(409, str(exc))
    _audit(db, god, "platform_pool.rolled_back", b.id, report)
    db.commit()
    return report
