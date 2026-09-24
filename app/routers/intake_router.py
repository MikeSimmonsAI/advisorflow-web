"""/intake — the universal importer's API.

    GET    /intake/context                          who, and INTO WHICH organization
    GET    /intake/fields                           platform fields, kinds, classifications
    GET    /intake/classifications                  org catalog (defaults + custom)
    POST   /intake/classifications                  add an org classification
    GET    /intake/batches                          the Import Batch Ledger
    POST   /intake/batches                          upload -> batch (status: mapping)
    GET    /intake/batches/{id}                     batch detail + analysis + progress
    GET    /intake/batches/{id}/preview             headers, samples, suggested mapping
    PUT    /intake/batches/{id}/mapping             mapping / classification / update policy
    GET    /intake/batches/{id}/classification-values  distinct values of the class column
    POST   /intake/batches/{id}/analyze             background staging + analysis (202)
    GET    /intake/batches/{id}/rows?category=      drill into any preview number
    PATCH  /intake/batches/{id}/rows/{row_id}       override one row
    POST   /intake/batches/{id}/rows/bulk           resolve a whole category
    GET    /intake/batches/{id}/commit-preview      what a decision would do
    POST   /intake/batches/{id}/commit              the decision (default: stage only)
    GET    /intake/batches/{id}/rollback-plan       what a rollback would do
    POST   /intake/batches/{id}/rollback            do it
    POST   /intake/batches/{id}/cancel              cancel an uncommitted batch
    GET    /intake/batches/{id}/audit               every audit event for the batch
    GET    /intake/contacts/summary                 CONTACTS / LEADS / CUSTOMERS ... counts
    GET    /intake/contacts                         browse the org contact database

TENANT RULE: every route resolves `context.resolve()` first. There is no route
here that reads or writes without a selected organization, and every query
filters on that organization's id.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.deps import get_db
from app.models.import_models import ImportBatch, ImportBatchStatus, ImportStagedRow
from app.models.intake_models import (CommitMode, ContactLifecycle, DuplicateResolution,
                                      IntakeClassification, IntakeStatus, OrgContact,
                                      RecordClass)
from app.services.import_permissions import (require_import_commit, require_import_manage,
                                             require_import_review, require_import_stage)
from app.services.intake import audit, runner
from app.services.intake import classification as C
from app.services.intake import commit as CM
from app.services.intake import context as CTX
from app.services.intake import engine as ENG
from app.services.intake import fields as F
from app.services.intake import rollback as RB

router = APIRouter(prefix="/intake", tags=["intake"])
log = logging.getLogger(__name__)


def _j(s, d):
    try:
        return json.loads(s) if s else d
    except Exception:  # noqa: BLE001
        return d


def _iso(dt):
    """Naive timestamps in this schema are UTC; say so, or a browser reads
    them as local time."""
    if dt is None:
        return None
    s = dt.isoformat()
    return s if dt.tzinfo else s + "Z"


def _batch_or_404(db: Session, ctx, batch_id: str) -> ImportBatch:
    b = (db.query(ImportBatch)
         .filter(ImportBatch.id == batch_id, ImportBatch.organization_id == ctx.org_id,
                 ImportBatch.pipeline == ENG.PIPELINE).first())
    if b is None:
        raise HTTPException(404, "Import batch not found in this organization.")
    return b


def _batch_payload(db: Session, b: ImportBatch, detail: bool = False) -> dict:
    status = b.status
    if ENG.is_stale(b) and not runner.is_running_here(b.id):
        status = "interrupted"
    rep = _j(b.commit_report_json, {})
    out = {
        "id": b.id, "batch_code": b.batch_code, "display_name": b.display_name,
        "status": status, "stage": b.stage, "progress_pct": b.progress_pct,
        "filename": b.source_filename, "source": b.source_label,
        "source_system": b.source_system, "source_detail": b.source_detail,
        "source_year": b.source_year, "list_name": b.import_list_name,
        "campaign_purpose": b.campaign_purpose, "offer_hook": b.offer_hook,
        "tags": _j(b.tags_json, []),
        "organization_id": b.organization_id,
        "imported_by": b.acting_user_name or b.created_by_name, "role": b.acting_role,
        "acted_as_platform_owner": bool(b.acted_as_platform_owner),
        "rows_submitted": b.original_row_count, "rows_staged": b.total_rows,
        "created_at": _iso(b.created_at),
        "committed_at": _iso(b.committed_at),
        "committed_by": b.committed_by_name,
        "commit_mode": b.commit_mode,
        "rolled_back_at": _iso(b.rolled_back_at),
        "error": b.error_message,
        "counts": {
            "imported": (rep.get("contacts_created", 0) if rep else 0),
            "existing_updated": (rep.get("contacts_updated", 0) if rep else 0),
            "leads_created": (rep.get("leads_created", 0) if rep else 0),
            "skipped": (rep.get("skipped", 0) if rep else 0),
            "failed": (rep.get("failed", 0) if rep else 0),
        },
    }
    a = _j(b.analysis_json, {})
    if a.get("status"):
        st = a["status"]
        out["counts"].update({
            "ready": st.get("ready", 0), "review": st.get("needs_review", 0),
            "enrichment": a.get("needs_enrichment", 0),
            "blocked": st.get("blocked", 0), "duplicates": st.get("duplicate", 0),
            "existing_matches": a.get("match", {}).get("existing_exact", 0),
            "invalid": st.get("invalid", 0),
        })
    if detail:
        out["analysis"] = a
        out["commit_report"] = rep or None
        out["rollback_report"] = _j(b.rollback_report_json, None)
        out["mapping"] = _j(b.mapping_json, {})
        out["classification"] = _j(b.classification_json, {})
        out["update_policy"] = _j(b.update_policy_json, ENG.DEFAULT_UPDATE_POLICY)
        out["headers"] = _j(b.headers_json, [])
    return out


# ── context / catalog ────────────────────────────────────────────────────────

@router.get("/context")
def get_context(db: Session = Depends(get_db), user=Depends(require_import_stage)):
    ctx = CTX.resolve(db, user)
    return ctx.payload() | {"sources": [{"key": k, "label": v} for k, v in ENG.SOURCE_OPTIONS]}


@router.get("/fields")
def get_fields(db: Session = Depends(get_db), user=Depends(require_import_stage)):
    ctx = CTX.resolve(db, user)
    customs = ENG.org_custom_field_keys(db, ctx.org_id)
    return {"standard": F.registry_payload(), "kinds": list(F.KINDS),
            "custom_fields": sorted(set(customs.values())),
            "classifications": C.payload(ENG.org_catalog(db, ctx.org_id)),
            "updatable_fields": ENG.UPDATABLE_FIELDS}


@router.get("/classifications")
def list_classifications(db: Session = Depends(get_db), user=Depends(require_import_review)):
    ctx = CTX.resolve(db, user)
    return {"classifications": C.payload(ENG.org_catalog(db, ctx.org_id)),
            "record_classes": list(RecordClass.ALL)}


class ClassificationIn(BaseModel):
    key: str
    label: str
    record_class: str = RecordClass.CONTACT
    creates_lead: bool = False
    aliases: List[str] = []


@router.post("/classifications")
def add_classification(body: ClassificationIn, db: Session = Depends(get_db),
                       user=Depends(require_import_manage)):
    import re
    ctx = CTX.resolve(db, user)
    key = re.sub(r"[^a-z0-9_]", "_", body.key.strip().lower())[:40].strip("_")
    if not key or body.record_class not in RecordClass.ALL:
        raise HTTPException(400, "A key and a valid record class are required.")
    if key in {c.key for c in C.DEFAULT_CLASSIFICATIONS}:
        raise HTTPException(409, f"'{key}' is a platform classification.")
    row = (db.query(IntakeClassification)
           .filter(IntakeClassification.organization_id == ctx.org_id,
                   IntakeClassification.key == key).first())
    if row is None:
        row = IntakeClassification(organization_id=ctx.org_id, key=key)
        db.add(row)
    row.label = body.label.strip()[:80] or key
    row.record_class = body.record_class
    row.creates_lead = bool(body.creates_lead)
    row.aliases = json.dumps([a for a in body.aliases if a][:50])
    row.is_active = True
    audit.record(db, ctx, "intake.classification_defined", "-",
                 {"key": key, "record_class": body.record_class,
                  "creates_lead": bool(body.creates_lead)})
    db.commit()
    return {"key": key}


# ── ledger / upload ──────────────────────────────────────────────────────────

@router.get("/batches")
def list_batches(page: int = Query(1, ge=1), per_page: int = Query(25, ge=1, le=100),
                 db: Session = Depends(get_db), user=Depends(require_import_review)):
    ctx = CTX.resolve(db, user)
    q = (db.query(ImportBatch)
         .filter(ImportBatch.organization_id == ctx.org_id,
                 ImportBatch.pipeline == ENG.PIPELINE))
    total = q.count()
    rows = (q.order_by(ImportBatch.created_at.desc())
            .offset((page - 1) * per_page).limit(per_page).all())
    return {"organization": ctx.payload(), "total": total, "page": page,
            "batches": [_batch_payload(db, b) for b in rows]}


@router.post("/batches")
async def upload(file: UploadFile = File(...),
                 source: str = Form("csv"),
                 source_detail: Optional[str] = Form(None),
                 source_year: Optional[int] = Form(None),
                 list_name: Optional[str] = Form(None),
                 campaign_purpose: Optional[str] = Form(None),
                 offer_hook: Optional[str] = Form(None),
                 tags: Optional[str] = Form(None),
                 db: Session = Depends(get_db), user=Depends(require_import_stage)):
    ctx = CTX.resolve(db, user)
    content = await file.read(ENG.MAX_UPLOAD_BYTES + 1)
    try:
        b = ENG.create_batch(
            db, ctx, content=content, filename=file.filename or "upload.csv", source=source,
            source_detail=source_detail, source_year=source_year, list_name=list_name,
            campaign_purpose=campaign_purpose, offer_hook=offer_hook,
            tags=[t.strip() for t in (tags or "").split(",") if t.strip()])
    except ENG.IntakeError as exc:
        db.rollback()
        raise HTTPException(400, str(exc))
    audit.record(db, ctx, "intake.file_uploaded", b.id,
                 {"filename": b.source_filename, "rows": b.original_row_count,
                  "columns": len(_j(b.headers_json, [])), "source": b.source_label,
                  "source_detail": b.source_detail, "batch_code": b.batch_code})
    db.commit()
    return _batch_payload(db, b, detail=True)


@router.get("/batches/{batch_id}")
def get_batch(batch_id: str, db: Session = Depends(get_db),
              user=Depends(require_import_review)):
    ctx = CTX.resolve(db, user)
    return _batch_payload(db, _batch_or_404(db, ctx, batch_id), detail=True) | {
        "context": ctx.payload()}


@router.get("/batches/{batch_id}/preview")
def preview(batch_id: str, db: Session = Depends(get_db), user=Depends(require_import_stage)):
    ctx = CTX.resolve(db, user)
    b = _batch_or_404(db, ctx, batch_id)
    try:
        return ENG.preview_payload(db, b)
    except ENG.IntakeError as exc:
        raise HTTPException(400, str(exc))


class MappingIn(BaseModel):
    mapping: Optional[dict] = None
    classification: Optional[dict] = None
    update_policy: Optional[dict] = None


_EDITABLE = (ImportBatchStatus.MAPPING, ImportBatchStatus.READY_FOR_REVIEW,
             ImportBatchStatus.FAILED, ImportBatchStatus.STAGED)


@router.put("/batches/{batch_id}/mapping")
def save_mapping(batch_id: str, body: MappingIn, db: Session = Depends(get_db),
                 user=Depends(require_import_stage)):
    ctx = CTX.resolve(db, user)
    b = _batch_or_404(db, ctx, batch_id)
    if b.status not in _EDITABLE:
        raise HTTPException(409, f"Mapping cannot change while the batch is '{b.status}'.")
    before = {"mapping": _j(b.mapping_json, {}), "classification": _j(b.classification_json, {}),
              "update_policy": _j(b.update_policy_json, {})}
    try:
        problems = ENG.save_mapping(db, b, mapping=body.mapping,
                                    classification=body.classification,
                                    update_policy=body.update_policy)
    except ENG.IntakeError as exc:
        raise HTTPException(400, str(exc))
    if problems:
        db.rollback()
        return JSONResponse(status_code=422, content={"detail": {"problems": problems, "message": " ".join(problems)}})
    after = {"mapping": _j(b.mapping_json, {}), "classification": _j(b.classification_json, {}),
             "update_policy": _j(b.update_policy_json, {})}
    changed = [k for k in after if after[k] != before[k]]
    if changed:
        audit.record(db, ctx, "intake.mapping_saved"
                     if "mapping" in changed else "intake.classification_saved", b.id,
                     {"changed": changed}, before=before, after=after)
    if b.status in (ImportBatchStatus.READY_FOR_REVIEW, ImportBatchStatus.STAGED) and changed:
        # The analysis no longer describes this mapping: it must be re-run
        # before anything can be committed.
        b.status = ImportBatchStatus.MAPPING
        b.stage = "mapping"
    db.commit()
    return _batch_payload(db, b, detail=True)


@router.get("/batches/{batch_id}/classification-values")
def classification_values(batch_id: str, db: Session = Depends(get_db),
                          user=Depends(require_import_stage)):
    ctx = CTX.resolve(db, user)
    try:
        return ENG.classification_values(db, _batch_or_404(db, ctx, batch_id))
    except ENG.IntakeError as exc:
        raise HTTPException(400, str(exc))


def _analysis_job(session, batch_id, org_id, ctx):
    try:
        ENG.run_analysis(session, batch_id, org_id)
        b = session.query(ImportBatch).filter(ImportBatch.id == batch_id).first()
        a = _j(b.analysis_json, {})
        audit.record(session, ctx, "intake.analysis_completed", batch_id,
                     {"total": a.get("total_rows"), "status": a.get("status"),
                      "seconds": a.get("timing_seconds")}, commit=True)
    except Exception as exc:  # noqa: BLE001
        audit.record(session, ctx, "intake.analysis_failed", batch_id,
                     {"error": str(exc)[:300]}, commit=True)
        if not runner.inline():
            return
        raise


@router.post("/batches/{batch_id}/analyze", status_code=202)
def analyze(batch_id: str, db: Session = Depends(get_db), user=Depends(require_import_stage)):
    ctx = CTX.resolve(db, user)
    b = _batch_or_404(db, ctx, batch_id)
    if b.status in (ImportBatchStatus.PROCESSING, ImportBatchStatus.COMMITTING) \
            and not ENG.is_stale(b):
        raise HTTPException(409, f"This batch is already {b.status}.")
    if b.status not in _EDITABLE + (ImportBatchStatus.PROCESSING,):
        raise HTTPException(409, f"A '{b.status}' batch cannot be re-analyzed.")
    problems = F.validate_mapping(_j(b.headers_json, []), _j(b.mapping_json, {}))
    if problems:
        return JSONResponse(status_code=422, content={"detail": {"problems": problems, "message": " ".join(problems)}})
    b.status = ImportBatchStatus.PROCESSING
    b.stage = "parsing"
    b.progress_pct = 0
    b.heartbeat_at = datetime.utcnow()
    audit.record(db, ctx, "intake.analysis_started", b.id, {"rows": b.original_row_count})
    db.commit()
    try:
        runner.launch(b.id, _analysis_job, b.id, ctx.org_id, ctx, db=db)
    except ENG.IntakeError as exc:
        raise HTTPException(400, str(exc))
    db.refresh(b)
    return _batch_payload(db, b, detail=True)


# ── rows ─────────────────────────────────────────────────────────────────────

@router.get("/batches/{batch_id}/rows")
def list_rows(batch_id: str, category: Optional[str] = Query(None),
              search: Optional[str] = Query(None),
              page: int = Query(1, ge=1), per_page: int = Query(50, ge=1, le=200),
              db: Session = Depends(get_db), user=Depends(require_import_review)):
    ctx = CTX.resolve(db, user)
    b = _batch_or_404(db, ctx, batch_id)
    q = db.query(ImportStagedRow).filter(ImportStagedRow.batch_id == b.id,
                                         ImportStagedRow.organization_id == ctx.org_id)
    try:
        q = ENG.category_filter(q, category)
    except ENG.IntakeError as exc:
        raise HTTPException(400, str(exc))
    if search:
        s = f"%{search.strip()}%"
        R = ImportStagedRow
        q = q.filter(R.first_name.ilike(s) | R.last_name.ilike(s) | R.company.ilike(s)
                     | R.email_normalized.ilike(s) | R.phone_normalized.ilike(s)
                     | R.source_record_id.ilike(s))
    total = q.count()
    rows = q.order_by(ImportStagedRow.row_number).offset((page - 1) * per_page) \
        .limit(per_page).all()
    return {"total": total, "page": page, "per_page": per_page, "category": category,
            "rows": [ENG.row_payload(r) for r in rows]}


class RowOverride(BaseModel):
    classification: Optional[str] = None
    duplicate_resolution: Optional[str] = None
    approve: Optional[bool] = None
    skip: Optional[bool] = None
    note: Optional[str] = None


_FROZEN = (IntakeStatus.IMPORTED,)


def _apply_override(row: ImportStagedRow, body: RowOverride, cat) -> List[str]:
    changed = []
    if row.intake_status in _FROZEN:
        raise HTTPException(409, "This row has already been imported.")
    if body.classification is not None:
        cd = cat.get(body.classification)
        if cd is None:
            raise HTTPException(400, f"Unknown classification '{body.classification}'.")
        row.classification, row.record_class = cd.key, cd.record_class
        row.creates_lead, row.classification_source = cd.creates_lead, "manual"
        reasons = [r for r in _j(row.status_reasons, [])
                   if r.get("code") != "unrecognized_classification"]
        row.status_reasons = json.dumps(reasons)
        changed.append("classification")
    if body.duplicate_resolution is not None:
        if body.duplicate_resolution not in DuplicateResolution.ALL:
            raise HTTPException(400, "Unknown duplicate resolution.")
        row.duplicate_resolution = body.duplicate_resolution
        changed.append("duplicate_resolution")
    if body.skip:
        row.intake_status = IntakeStatus.SKIPPED
        changed.append("skipped")
    elif body.approve:
        if row.intake_status == IntakeStatus.INVALID:
            raise HTTPException(409, "An invalid row cannot be approved; fix the source.")
        if (row.intake_status == IntakeStatus.NEEDS_REVIEW
                and row.duplicate_resolution == DuplicateResolution.REVIEW):
            raise HTTPException(409, "Choose what to do with the possible duplicate first "
                                     "(update existing, keep separate or skip).")
        row.intake_status = IntakeStatus.APPROVED
        changed.append("approved")
    elif body.approve is False and row.intake_status in (IntakeStatus.APPROVED,
                                                         IntakeStatus.SKIPPED):
        row.intake_status = IntakeStatus.NEEDS_REVIEW
        changed.append("returned_to_review")
    if body.note is not None:
        row.review_note = body.note[:500]
    row.reviewed_at = datetime.utcnow()
    return changed


@router.patch("/batches/{batch_id}/rows/{row_id}")
def override_row(batch_id: str, row_id: str, body: RowOverride,
                 db: Session = Depends(get_db), user=Depends(require_import_review)):
    ctx = CTX.resolve(db, user)
    b = _batch_or_404(db, ctx, batch_id)
    if b.status in (ImportBatchStatus.PROCESSING, ImportBatchStatus.COMMITTING):
        raise HTTPException(409, f"The batch is {b.status}.")
    row = (db.query(ImportStagedRow)
           .filter(ImportStagedRow.id == row_id, ImportStagedRow.batch_id == b.id,
                   ImportStagedRow.organization_id == ctx.org_id).first())
    if row is None:
        raise HTTPException(404, "Row not found.")
    before = ENG.row_payload(row)
    changed = _apply_override(row, body, ENG.org_catalog(db, ctx.org_id))
    row.reviewed_by_id = ctx.actor_id
    audit.record(db, ctx, "intake.row_overridden", b.id,
                 {"row": row.row_number, "changed": changed},
                 before={k: before[k] for k in ("intake_status", "classification",
                                                "duplicate_resolution")},
                 after={"intake_status": row.intake_status,
                        "classification": row.classification,
                        "duplicate_resolution": row.duplicate_resolution})
    _refresh_analysis(db, b)
    db.commit()
    return ENG.row_payload(row)


class BulkIn(BaseModel):
    category: str
    classification: Optional[str] = None
    duplicate_resolution: Optional[str] = None
    approve: Optional[bool] = None
    skip: Optional[bool] = None


@router.post("/batches/{batch_id}/rows/bulk")
def bulk(batch_id: str, body: BulkIn, db: Session = Depends(get_db),
         user=Depends(require_import_review)):
    ctx = CTX.resolve(db, user)
    b = _batch_or_404(db, ctx, batch_id)
    if b.status in (ImportBatchStatus.PROCESSING, ImportBatchStatus.COMMITTING):
        raise HTTPException(409, f"The batch is {b.status}.")
    q = db.query(ImportStagedRow).filter(ImportStagedRow.batch_id == b.id,
                                         ImportStagedRow.organization_id == ctx.org_id,
                                         ImportStagedRow.intake_status.notin_(_FROZEN))
    try:
        q = ENG.category_filter(q, body.category)
    except ENG.IntakeError as exc:
        raise HTTPException(400, str(exc))
    cat = ENG.org_catalog(db, ctx.org_id)
    ov = RowOverride(classification=body.classification,
                     duplicate_resolution=body.duplicate_resolution,
                     approve=body.approve, skip=body.skip)
    n, refused = 0, 0
    for row in q.all():
        try:
            _apply_override(row, ov, cat)
            row.reviewed_by_id = ctx.actor_id
            n += 1
        except HTTPException:
            refused += 1
    audit.record(db, ctx, "intake.rows_bulk_resolved", b.id,
                 {"category": body.category, "updated": n, "refused": refused,
                  "classification": body.classification,
                  "duplicate_resolution": body.duplicate_resolution,
                  "approve": body.approve, "skip": body.skip})
    _refresh_analysis(db, b)
    db.commit()
    return {"updated": n, "refused": refused}


def _refresh_analysis(db, b):
    db.flush()
    a = _j(b.analysis_json, {})
    fresh = ENG.compute_analysis(db, b)
    for k in ("read", "timing_seconds"):
        if k in a:
            fresh[k] = a[k]
    b.analysis_json = json.dumps(fresh, default=str)


# ── commit ───────────────────────────────────────────────────────────────────

@router.get("/batches/{batch_id}/commit-preview")
def commit_preview(batch_id: str, mode: str = Query(CommitMode.STAGE_ONLY),
                   include_enrichment: bool = Query(True),
                   db: Session = Depends(get_db), user=Depends(require_import_review)):
    ctx = CTX.resolve(db, user)
    b = _batch_or_404(db, ctx, batch_id)
    if mode not in CommitMode.ALL:
        raise HTTPException(400, "Unknown mode.")
    return CM.commit_preview(db, b, mode, include_enrichment) | {
        "organization_name": ctx.org_name}


class CommitIn(BaseModel):
    mode: str = CommitMode.STAGE_ONLY
    include_enrichment: bool = True
    confirm_organization_name: Optional[str] = None


_COMMITTABLE = (ImportBatchStatus.READY_FOR_REVIEW, ImportBatchStatus.STAGED,
                ImportBatchStatus.PARTIALLY_COMMITTED)


def _commit_job(session, batch_id, org_id, ctx, mode, include_enrichment):
    try:
        CM.run_commit(session, batch_id, org_id, ctx, mode, include_enrichment)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        b = session.query(ImportBatch).filter(ImportBatch.id == batch_id).first()
        if b is not None:
            b.status = ImportBatchStatus.FAILED
            b.error_message = f"Commit failed: {type(exc).__name__}: {str(exc)[:300]}"
            session.commit()
        audit.record(session, ctx, "intake.commit_failed", batch_id,
                     {"error": str(exc)[:300]}, commit=True)
        if runner.inline():
            raise


@router.post("/batches/{batch_id}/commit", status_code=202)
def commit(batch_id: str, body: CommitIn, db: Session = Depends(get_db),
           user=Depends(require_import_commit)):
    ctx = CTX.resolve(db, user)
    b = _batch_or_404(db, ctx, batch_id)
    if body.mode not in CommitMode.ALL:
        raise HTTPException(400, "Unknown mode.")
    if b.status not in _COMMITTABLE and not (b.status == ImportBatchStatus.COMMITTING
                                             and ENG.is_stale(b)):
        raise HTTPException(409, f"A '{b.status}' batch cannot be committed. "
                                 "Analyze it first.")
    if body.mode != CommitMode.STAGE_ONLY:
        typed = (body.confirm_organization_name or "").strip().lower()
        if typed != (ctx.org_name or "").strip().lower():
            raise HTTPException(400, "Type the organization's name exactly to confirm this "
                                     "import writes into it.")
    if body.mode == CommitMode.STAGE_ONLY:
        CM.run_commit(db, b.id, ctx.org_id, ctx, body.mode, body.include_enrichment)
        return _batch_payload(db, b, detail=True)
    b.status = ImportBatchStatus.COMMITTING
    b.heartbeat_at = datetime.utcnow()
    db.commit()
    runner.launch(b.id, _commit_job, b.id, ctx.org_id, ctx, body.mode,
                  body.include_enrichment, db=db)
    db.refresh(b)
    return _batch_payload(db, b, detail=True)


# ── rollback / cancel / audit ────────────────────────────────────────────────

_ROLLBACKABLE = (ImportBatchStatus.COMMITTED, ImportBatchStatus.PARTIALLY_COMMITTED,
                 ImportBatchStatus.PARTIALLY_ROLLED_BACK)


@router.get("/batches/{batch_id}/rollback-plan")
def rollback_plan(batch_id: str, db: Session = Depends(get_db),
                  user=Depends(require_import_manage)):
    ctx = CTX.resolve(db, user)
    b = _batch_or_404(db, ctx, batch_id)
    p = RB.plan(db, b)
    p["items"] = p["items"][:500]
    return p


class RollbackIn(BaseModel):
    confirm_batch_code: str


@router.post("/batches/{batch_id}/rollback")
def rollback(batch_id: str, body: RollbackIn, db: Session = Depends(get_db),
             user=Depends(require_import_manage)):
    ctx = CTX.resolve(db, user)
    b = _batch_or_404(db, ctx, batch_id)
    if b.status not in _ROLLBACKABLE:
        raise HTTPException(409, f"A '{b.status}' batch has nothing to roll back.")
    if body.confirm_batch_code.strip() != (b.batch_code or ""):
        raise HTTPException(400, "Type the batch ID exactly to confirm the rollback.")
    report = RB.execute(db, b, ctx)
    return {"batch": _batch_payload(db, b, detail=True), "report": report}


@router.post("/batches/{batch_id}/cancel")
def cancel(batch_id: str, db: Session = Depends(get_db), user=Depends(require_import_stage)):
    ctx = CTX.resolve(db, user)
    b = _batch_or_404(db, ctx, batch_id)
    if b.status in (ImportBatchStatus.COMMITTED, ImportBatchStatus.PARTIALLY_COMMITTED,
                    ImportBatchStatus.COMMITTING):
        raise HTTPException(409, "A committed batch is undone with a rollback, not a cancel.")
    b.status = ImportBatchStatus.CANCELLED
    b.stage = "cancelled"
    audit.record(db, ctx, "intake.batch_cancelled", b.id, {})
    db.commit()
    return _batch_payload(db, b)


@router.get("/batches/{batch_id}/audit")
def batch_audit(batch_id: str, db: Session = Depends(get_db),
                user=Depends(require_import_review)):
    from app.models.models import AuditLogEntry
    ctx = CTX.resolve(db, user)
    b = _batch_or_404(db, ctx, batch_id)
    rows = (db.query(AuditLogEntry)
            .filter(AuditLogEntry.organization_id == ctx.org_id,
                    AuditLogEntry.target_type == "import_batch",
                    AuditLogEntry.target_id == b.id)
            .order_by(AuditLogEntry.created_at.asc(), AuditLogEntry.id.asc()).limit(500).all())
    return {"events": [{"action": r.action, "at": _iso(r.created_at),
                        "actor_user_id": r.actor_user_id,
                        "details": _j(r.details, r.details)} for r in rows]}


# ── contacts ─────────────────────────────────────────────────────────────────

@router.get("/contacts/summary")
def contacts_summary(db: Session = Depends(get_db), user=Depends(require_import_review)):
    from app.models.models import Lead
    ctx = CTX.resolve(db, user)
    base = db.query(OrgContact).filter(OrgContact.organization_id == ctx.org_id,
                                       OrgContact.archived_at.is_(None))
    by_class = dict(base.with_entities(OrgContact.record_class, func.count())
                    .group_by(OrgContact.record_class).all())
    return {
        "organization": ctx.payload(),
        "contacts": base.count(),
        "active_leads": db.query(Lead).filter(Lead.organization_id == ctx.org_id).count(),
        "customers": by_class.get(RecordClass.CUSTOMER, 0),
        "previous_customers": by_class.get(RecordClass.PREVIOUS_CUSTOMER, 0),
        "renewals": by_class.get(RecordClass.RENEWAL, 0),
        "by_record_class": by_class,
        "sms_ready": base.filter(OrgContact.sms_status == "ready").count(),
        "email_ready": base.filter(OrgContact.email_status == "ready").count(),
        "needs_enrichment": base.filter(
            OrgContact.lifecycle == ContactLifecycle.NEEDS_ENRICHMENT).count(),
        "historical_customers": base.filter(OrgContact.historical_customer.is_(True)).count(),
    }


@router.get("/contacts")
def list_contacts(record_class: Optional[str] = Query(None),
                  lifecycle: Optional[str] = Query(None),
                  batch_id: Optional[str] = Query(None), search: Optional[str] = Query(None),
                  page: int = Query(1, ge=1), per_page: int = Query(50, ge=1, le=200),
                  db: Session = Depends(get_db), user=Depends(require_import_review)):
    ctx = CTX.resolve(db, user)
    q = db.query(OrgContact).filter(OrgContact.organization_id == ctx.org_id)
    if record_class:
        q = q.filter(OrgContact.record_class == record_class)
    if lifecycle:
        q = q.filter(OrgContact.lifecycle == lifecycle)
    else:
        q = q.filter(OrgContact.archived_at.is_(None))
    if batch_id:
        q = q.filter(OrgContact.import_batch_id == batch_id)
    if search:
        s = f"%{search.strip()}%"
        q = q.filter(OrgContact.first_name.ilike(s) | OrgContact.last_name.ilike(s)
                     | OrgContact.company.ilike(s) | OrgContact.email.ilike(s)
                     | OrgContact.phone.ilike(s) | OrgContact.mobile_phone.ilike(s))
    total = q.count()
    rows = q.order_by(OrgContact.created_at.desc()).offset((page - 1) * per_page) \
        .limit(per_page).all()
    return {"total": total, "contacts": [{
        "id": c.id, "first_name": c.first_name, "last_name": c.last_name,
        "company": c.company, "email": c.email, "phone": c.phone or c.mobile_phone,
        "record_class": c.record_class, "classification": c.classification,
        "lifecycle": c.lifecycle, "sms_status": c.sms_status, "email_status": c.email_status,
        "historical_customer": c.historical_customer, "lead_id": c.lead_id,
        "source": c.source, "source_detail": c.source_detail,
        "import_batch_id": c.import_batch_id, "source_record_id": c.source_record_id,
    } for c in rows]}
