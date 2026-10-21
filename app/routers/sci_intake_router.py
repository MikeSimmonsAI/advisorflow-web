"""/program/intake - the Intelligent Lead Importer's steps 1-5 for an SCI workspace.

Same engine, same handlers as /intake (intake_router.py): this router only
decides WHO may reach them and WHICH batches. It exists so an SCI manager -
including a program-only login with no CRM import grant and a workspace whose
plan has no "imports" feature - can map, analyze and review an SCI file
without /imports being widened for anyone.

    GET    /program/intake/context                    who, into which workspace
    GET    /program/intake/fields                     the mapping catalog
    GET    /program/intake/batches                    this workspace's SCI batches
    POST   /program/intake/batches                    upload -> SCI batch (mapping)
    GET    /program/intake/batches/{id}               batch detail
    GET    /program/intake/batches/{id}/preview       headers, samples, mapping
    PUT    /program/intake/batches/{id}/mapping       save mapping
    GET    /program/intake/batches/{id}/classification-values
    POST   /program/intake/batches/{id}/analyze       stage + analyze (202)
    GET    /program/intake/batches/{id}/rows          drill into any number
    PATCH  /program/intake/batches/{id}/rows/{row}    override one row
    POST   /program/intake/batches/{id}/rows/bulk     resolve a category
    POST   /program/intake/batches/{id}/cancel        cancel
    GET    /program/intake/batches/{id}/audit         the batch's audit events

The last step is SCI's own: /program/leads/import/batches/{id}/sci-preview
and /load (program_router.py). There is no commit route here.

WHO: a manager of the SCI workspace the request is in (org_admin / manager,
or the platform owner inside it). Spectators and advisors are refused, and
writes by a spectator are refused centrally before any of this runs.
WHICH: only batches made here (source_system "program") of THIS workspace;
any other batch id is "not found".
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.deps import get_db, require_tenant_user
from app.models.import_models import ImportBatch, ImportStagedRow
from app.models.intake_models import DuplicateResolution, IntakeStatus
from app.models.models import User
from app.routers import intake_router as IR
from app.routers.program_router import _sci_batch, _sci_importer, sci_batch_frozen
from app.services.intake import audit
from app.services.intake import context as CTX
from app.services.intake import engine as ENG

router = APIRouter(prefix="/program/intake", tags=["program-intake"])


def _guard(db: Session, user: User):
    prog, _org = _sci_importer(db, user)
    ctx = CTX.resolve(db, user)
    if ctx.org_id != prog.organization_id:
        raise HTTPException(status_code=409, detail="Select the SCI workspace first.")
    return prog, ctx


def _batch(db: Session, user: User, batch_id: str, *, edit: bool = False) -> ImportBatch:
    prog, _ctx = _guard(db, user)
    b = _sci_batch(db, prog, batch_id)
    if edit and sci_batch_frozen(b):
        # What SCI holds is what was staged; changing the rows here afterwards
        # would make this batch say something SCI never received.
        raise HTTPException(status_code=409,
                            detail="This file is already staged in SCI - upload again to change it.")
    return b


MAX_SCI_UPLOAD = 10 * 1024 * 1024          # SCI's own limit (POST /program/leads/import)


@router.get("/context")
def context(db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    _prog, ctx = _guard(db, user)
    return ctx.payload() | {"sources": [{"key": ENG.SCI_SOURCE_SYSTEM, "label": ENG.SCI_SOURCE_LABEL}],
                            "destination": "sci"}


@router.get("/fields")
def fields(db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    _guard(db, user)
    with IR.sci_call():
        return IR.get_fields(db=db, user=user)


@router.get("/batches")
def batches(page: int = Query(1, ge=1), per_page: int = Query(25, ge=1, le=100),
            db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    prog, ctx = _guard(db, user)
    q = (db.query(ImportBatch)
         .filter(ImportBatch.organization_id == prog.organization_id,
                 ImportBatch.pipeline == ENG.PIPELINE,
                 ImportBatch.source_system == ENG.SCI_SOURCE_SYSTEM))
    total = q.count()
    rows = (q.order_by(ImportBatch.created_at.desc())
            .offset((page - 1) * per_page).limit(per_page).all())
    return {"organization": ctx.payload(), "total": total, "page": page,
            "batches": [IR._batch_payload(db, b) for b in rows]}


@router.post("/batches")
async def upload(file: UploadFile = File(...), list_name: Optional[str] = Form(None),
                 source_detail: Optional[str] = Form(None),
                 db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    _prog, ctx = _guard(db, user)
    content = await file.read(MAX_SCI_UPLOAD + 1)
    if len(content) > MAX_SCI_UPLOAD:
        raise HTTPException(status_code=413, detail="Files up to 10 MB.")
    try:
        b = ENG.create_batch(db, ctx, content=content, filename=file.filename or "upload.csv",
                             source=ENG.SCI_SOURCE_SYSTEM, source_detail=source_detail,
                             list_name=list_name, destination=ENG.SCI_DESTINATION)
    except ENG.IntakeError as exc:
        db.rollback()
        raise HTTPException(400, str(exc))
    audit.record(db, ctx, "intake.file_uploaded", b.id,
                 {"filename": b.source_filename, "rows": b.original_row_count,
                  "columns": len(IR._j(b.headers_json, [])), "source": b.source_label,
                  "source_detail": b.source_detail, "batch_code": b.batch_code,
                  "destination": "sci"})
    db.commit()
    return IR._batch_payload(db, b, detail=True)


@router.get("/batches/{batch_id}")
def get_batch(batch_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    _batch(db, user, batch_id)
    with IR.sci_call():
        return IR.get_batch(batch_id, db=db, user=user)


@router.get("/batches/{batch_id}/preview")
def preview(batch_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    _batch(db, user, batch_id)
    with IR.sci_call():
        return IR.preview(batch_id, db=db, user=user)


@router.put("/batches/{batch_id}/mapping")
def save_mapping(batch_id: str, body: IR.MappingIn, db: Session = Depends(get_db),
                 user: User = Depends(require_tenant_user)):
    _batch(db, user, batch_id, edit=True)
    with IR.sci_call():
        return IR.save_mapping(batch_id, body, db=db, user=user)


@router.get("/batches/{batch_id}/classification-values")
def classification_values(batch_id: str, db: Session = Depends(get_db),
                          user: User = Depends(require_tenant_user)):
    _batch(db, user, batch_id)
    with IR.sci_call():
        return IR.classification_values(batch_id, db=db, user=user)


@router.post("/batches/{batch_id}/analyze", status_code=202)
def analyze(batch_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    _batch(db, user, batch_id, edit=True)
    with IR.sci_call():
        return IR.analyze(batch_id, db=db, user=user)


@router.get("/batches/{batch_id}/rows")
def rows(batch_id: str, category: Optional[str] = Query(None), search: Optional[str] = Query(None),
         page: int = Query(1, ge=1), per_page: int = Query(50, ge=1, le=200),
         db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    _batch(db, user, batch_id)
    with IR.sci_call():
        return IR.list_rows(batch_id, category=category, search=search, page=page,
                            per_page=per_page, db=db, user=user)


# A POSSIBLE DUPLICATE A PERSON DECIDED IS DECIDED. In the CRM importer a
# decision on a duplicate leaves the row in "Needs review" until it is also
# approved; for an SCI file the decision (keep separate / merge / update) IS
# the review - SCI links people itself - so such a row is settled as approved
# unless something else on it still needs a look.
_DUP_CODES = {"possible_existing_match", "possible_duplicate_in_file"}
_REVIEW_CODES = _DUP_CODES | {"unrecognized_classification", "ambiguous_consent", "ambiguous_date"}
_DECIDED = (DuplicateResolution.KEEP_SEPARATE, DuplicateResolution.MERGE,
            DuplicateResolution.UPDATE_EXISTING)


def _settle_decided(db: Session, b: ImportBatch) -> int:
    n = 0
    for r in (db.query(ImportStagedRow)
              .filter(ImportStagedRow.batch_id == b.id,
                      ImportStagedRow.organization_id == b.organization_id,
                      ImportStagedRow.intake_status == IntakeStatus.NEEDS_REVIEW,
                      ImportStagedRow.duplicate_resolution.in_(_DECIDED)).all()):
        codes = {x.get("code") for x in IR._j(r.status_reasons, []) if isinstance(x, dict)}
        if codes & (_REVIEW_CODES - _DUP_CODES):
            continue
        r.intake_status = IntakeStatus.APPROVED
        n += 1
    # "Decide later" again: back to open review (the importer never approves a
    # possible duplicate nobody decided).
    for r in (db.query(ImportStagedRow)
              .filter(ImportStagedRow.batch_id == b.id,
                      ImportStagedRow.organization_id == b.organization_id,
                      ImportStagedRow.intake_status == IntakeStatus.APPROVED,
                      ImportStagedRow.duplicate_resolution == DuplicateResolution.REVIEW).all()):
        r.intake_status = IntakeStatus.NEEDS_REVIEW
        n += 1
    if n:
        IR._refresh_analysis(db, b)
        db.commit()
    return n


@router.patch("/batches/{batch_id}/rows/{row_id}")
def override_row(batch_id: str, row_id: str, body: IR.RowOverride, db: Session = Depends(get_db),
                 user: User = Depends(require_tenant_user)):
    b = _batch(db, user, batch_id, edit=True)
    with IR.sci_call():
        out = IR.override_row(batch_id, row_id, body, db=db, user=user)
    if body.duplicate_resolution is not None and _settle_decided(db, b):
        row = db.query(ImportStagedRow).filter(ImportStagedRow.id == row_id,
                                               ImportStagedRow.batch_id == b.id).first()
        out = ENG.row_payload(row) if row is not None else out
    return out


@router.post("/batches/{batch_id}/rows/bulk")
def bulk(batch_id: str, body: IR.BulkIn, db: Session = Depends(get_db),
         user: User = Depends(require_tenant_user)):
    b = _batch(db, user, batch_id, edit=True)
    with IR.sci_call():
        out = IR.bulk(batch_id, body, db=db, user=user)
    if body.duplicate_resolution is not None:
        out["settled"] = _settle_decided(db, b)
    return out


@router.post("/batches/{batch_id}/cancel")
def cancel(batch_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    _batch(db, user, batch_id, edit=True)
    with IR.sci_call():
        return IR.cancel(batch_id, db=db, user=user)


@router.get("/batches/{batch_id}/audit")
def batch_audit(batch_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    _batch(db, user, batch_id)
    with IR.sci_call():
        return IR.batch_audit(batch_id, db=db, user=user)
