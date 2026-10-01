"""/intake — the universal importer's API.

    GET    /intake/context                          who, and INTO WHICH organization
    GET    /intake/fields                           platform fields, kinds, classifications
    GET    /intake/classifications                  org catalog (defaults + custom)
    POST   /intake/classifications                  add an org classification
    GET    /intake/batches                          the Import Batch Ledger
    POST   /intake/batches                          upload -> batch (status: mapping)
    POST   /intake/batches/google-contacts          Google Contacts -> batch (status: mapping)
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
    GET    /intake/contacts/{id}                    one contact: provenance, contactability
    POST   /intake/contacts/{id}/promote            explicit human action: contact -> lead

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

from app.deps import get_db, require_not_observation
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
from app.services.platform_owner import require_tenant_context

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


_DONE = ("committing", "committed", "partially_committed", "rolled_back",
         "partially_rolled_back")


def _workflow(status: str, analyzed: bool, classified: bool) -> dict:
    """Where this batch is in the 7-step wizard, decided HERE from persisted
    state, so a refresh, a Back, or opening it from the ledger lands on the
    same step. Steps: 1 Upload, 2 Map, 3 Analyze, 4 Classify, 5 Review
    Problems, 6 Approve, 7 Results."""
    if status in _DONE:
        step = 7
    elif status == "staged":
        step = 6
    elif status in ("processing", "interrupted"):
        # The wizard shows progress on whichever step asked for the run; the
        # step it resumes on afterwards is the one below.
        step = 5 if classified else 3
    elif status in ("mapping", "uploading") or (status == "failed" and not analyzed):
        step = 4 if (classified and analyzed) else 2
    elif analyzed:
        step = 5 if classified else 3
    else:
        step = 2
    return {"step": step, "analyzed": analyzed, "classified": classified}


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
    out["workflow"] = _workflow(status, bool(a.get("status")),
                                bool(_j(b.classification_json, {}).get("confirmed_at")))
    # Step 6 -> 7 gate, decided HERE so the Approve button and POST /commit
    # can never disagree (the wizard used to keep its own list of statuses and
    # silently disable the button for any batch outside it).
    ok, why = commit_gate(b, status, bool(a.get("status")))
    out["commit_gate"] = {"can_commit": ok, "reason": why}
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


class GoogleImportIn(BaseModel):
    list_name: Optional[str] = None


@router.post("/batches/google-contacts")
def upload_google_contacts(payload: Optional[GoogleImportIn] = None, db: Session = Depends(get_db),
                           user=Depends(require_import_stage)):
    """Google Contacts as a SOURCE of the canonical importer: the contacts are
    staged as a batch and go through the same mapping, analysis, review and
    commit as any file. Nothing is written as a contact or lead here."""
    from app.services import google_contacts_service as GC
    ctx = CTX.resolve(db, user)
    try:
        b = GC.create_intake_batch_from_google(db, user, ctx,
                                               list_name=(payload.list_name if payload else None))
    except (ValueError, ENG.IntakeError) as exc:
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


def _commit_failed_retryable(b: ImportBatch, analyzed: bool) -> bool:
    """A batch whose COMMIT (not its analysis) died is retryable: run_commit
    skips rows already IMPORTED, so a retry finishes the job instead of
    duplicating it. Before this, a single failed background commit left the
    batch 'failed' forever - Step 6 showed a permanently disabled button and
    POST /commit answered 409 "Analyze it first"."""
    return (b.status == ImportBatchStatus.FAILED and analyzed
            and bool(b.commit_mode) and b.commit_mode != CommitMode.STAGE_ONLY)


def commit_gate(b: ImportBatch, status: str, analyzed: bool):
    """(can_commit, reason) - the single answer to "may Step 6 commit this?"."""
    if status in _COMMITTABLE or _commit_failed_retryable(b, analyzed):
        return True, None
    if status == ImportBatchStatus.COMMITTING:
        return False, "This batch is already importing. Its results appear on Step 7."
    if status == "interrupted" and b.status == ImportBatchStatus.COMMITTING:
        return True, None                     # stale commit: safe to resume
    if status in ("processing", "interrupted"):
        return False, "Analysis is still running (or was interrupted). Finish it on Step 3 first."
    if status in (ImportBatchStatus.MAPPING, ImportBatchStatus.UPLOADING):
        return False, ("The field mapping or classification changed since the last analysis. "
                       "Re-run the analysis (Step 2 or 3) before importing.")
    if status == ImportBatchStatus.FAILED:
        return False, "The analysis failed. Fix the mapping and analyze again."
    if status == ImportBatchStatus.COMMITTED:
        return False, "Everything selected in this batch is already imported."
    if status in (ImportBatchStatus.ROLLED_BACK, ImportBatchStatus.PARTIALLY_ROLLED_BACK):
        return False, "This batch was rolled back. Upload the file again to re-import it."
    if status == ImportBatchStatus.CANCELLED:
        return False, "This batch was cancelled."
    return False, f"A '{status}' batch cannot be imported."


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
    analyzed = bool(_j(b.analysis_json, {}).get("status"))
    if b.status not in _COMMITTABLE and not (b.status == ImportBatchStatus.COMMITTING
                                             and ENG.is_stale(b)) \
            and not _commit_failed_retryable(b, analyzed):
        _ok, why = commit_gate(b, b.status, analyzed)
        raise HTTPException(409, why or f"A '{b.status}' batch cannot be committed.")
    if body.mode != CommitMode.STAGE_ONLY:
        # Same normalization as the page (importSteps.normalizeOrgName): case,
        # spacing and stray periods/commas do not decide a confirmation.
        def _norm(v):
            return " ".join((v or "").lower().replace(".", " ").replace(",", " ").split())
        typed = _norm(body.confirm_organization_name)
        if not typed or typed != _norm(ctx.org_name):
            raise HTTPException(400, "Type the organization's name exactly to confirm this "
                                     "import writes into it.")
    if body.mode == CommitMode.STAGE_ONLY:
        CM.run_commit(db, b.id, ctx.org_id, ctx, body.mode, body.include_enrichment)
        return _batch_payload(db, b, detail=True)
    # CLAIM THE BATCH ATOMICALLY. The status check above and this write used to
    # be two steps, and runner's lock is per process: two web instances (or a
    # double-click landing on both) could each see a committable batch and
    # both start a commit - duplicate leads. Only the request whose
    # conditional UPDATE still matches the state it observed proceeds.
    observed_status, observed_heartbeat = b.status, b.heartbeat_at
    claim = db.query(ImportBatch).filter(ImportBatch.id == b.id,
                                         ImportBatch.status == observed_status)
    claim = (claim.filter(ImportBatch.heartbeat_at.is_(None)) if observed_heartbeat is None
             else claim.filter(ImportBatch.heartbeat_at == observed_heartbeat))
    claimed = claim.update({ImportBatch.status: ImportBatchStatus.COMMITTING,
                            ImportBatch.error_message: None,  # a retried commit starts clean
                            ImportBatch.heartbeat_at: datetime.utcnow()},
                           synchronize_session=False)
    db.commit()
    if not claimed:
        raise HTTPException(409, "This import is already being committed.")
    db.refresh(b)
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
#
# ROUTE ORDER MATTERS: /contacts/summary is registered before
# /contacts/{contact_id}, or "summary" would be read as a contact id.

def _has_text(col):
    return col.isnot(None) & (col != "")


def _phone_present():
    return _has_text(OrgContact.phone) | _has_text(OrgContact.mobile_phone)


def _is_mobile():
    # What the model can say truthfully: a number that arrived in a dedicated
    # mobile column, or a phone whose line type was determined to be mobile.
    return _has_text(OrgContact.mobile_phone) | (
        _has_text(OrgContact.phone) & (OrgContact.phone_line_type == "mobile"))


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
        # ── added for the workspace contacts screen ──
        "with_email": base.filter(_has_text(OrgContact.email)).count(),
        "with_phone": base.filter(_phone_present()).count(),
        "valid_phones": base.filter(
            _phone_present(),
            (OrgContact.sms_status.is_(None)) | (OrgContact.sms_status != "invalid")).count(),
        "mobile": base.filter(_is_mobile()).count(),
        "promoted": base.filter(OrgContact.lead_id.isnot(None)).count(),
    }


def _contact_row(c: OrgContact, batch_codes: dict) -> dict:
    return {
        "id": c.id, "first_name": c.first_name, "last_name": c.last_name,
        "full_name": c.full_name or (" ".join(p for p in (c.first_name, c.last_name) if p)
                                     or None),
        "company": c.company, "email": c.email,
        "phone": c.phone, "mobile_phone": c.mobile_phone,
        "street_address": c.street_address, "city": c.city, "state": c.state,
        "zip_code": c.zip_code,
        "record_class": c.record_class, "classification": c.classification,
        "lifecycle": c.lifecycle,
        "needs_enrichment": c.lifecycle == ContactLifecycle.NEEDS_ENRICHMENT,
        "sms_status": c.sms_status, "email_status": c.email_status,
        "historical_customer": c.historical_customer, "lead_id": c.lead_id,
        "source": c.source, "source_detail": c.source_detail,
        "source_system": c.source_system, "source_record_id": c.source_record_id,
        "import_batch_id": c.import_batch_id,
        "batch_code": batch_codes.get(c.import_batch_id),
        "created_at": _iso(c.created_at),
        # added: the phone line type as determined at import, when known
        "phone_line_type": c.phone_line_type,
    }


def _batch_codes(db: Session, org_id: str, ids) -> dict:
    ids = sorted({i for i in ids if i})
    if not ids:
        return {}
    return dict(db.query(ImportBatch.id, ImportBatch.batch_code)
                .filter(ImportBatch.organization_id == org_id, ImportBatch.id.in_(ids)).all())


_SORTS = ("recent", "name", "company")


def _contacts_query(db: Session, ctx, *, record_class=None, classification=None,
                    lifecycle=None, batch_id=None, search=None, has_email=None,
                    has_phone=None, email_ready=None, needs_enrichment=None,
                    historical_customer=None, promoted=None):
    """The Contacts screen's filter, shared by the list and the bulk selection
    (so "select all matching" selects exactly what the list shows)."""
    import re
    from sqlalchemy import or_
    q = db.query(OrgContact).filter(OrgContact.organization_id == ctx.org_id)
    if record_class:
        q = q.filter(OrgContact.record_class == record_class)
    if classification:
        q = q.filter(OrgContact.classification == classification)
    if lifecycle:
        q = q.filter(OrgContact.lifecycle == lifecycle)
    else:
        q = q.filter(OrgContact.archived_at.is_(None))
    if batch_id:
        q = q.filter(OrgContact.import_batch_id == batch_id)
    if has_email is not None:
        q = q.filter(_has_text(OrgContact.email) if has_email
                     else ~_has_text(OrgContact.email))
    if has_phone is not None:
        q = q.filter(_phone_present() if has_phone else ~_phone_present())
    if email_ready is not None:
        q = q.filter(OrgContact.email_status == "ready" if email_ready
                     else (OrgContact.email_status.is_(None))
                     | (OrgContact.email_status != "ready"))
    if needs_enrichment is not None:
        q = q.filter(OrgContact.lifecycle == ContactLifecycle.NEEDS_ENRICHMENT
                     if needs_enrichment
                     else OrgContact.lifecycle != ContactLifecycle.NEEDS_ENRICHMENT)
    if historical_customer is not None:
        q = q.filter(OrgContact.historical_customer.is_(True) if historical_customer
                     else OrgContact.historical_customer.isnot(True))
    if promoted is not None:
        q = q.filter(OrgContact.lead_id.isnot(None) if promoted
                     else OrgContact.lead_id.is_(None))
    term = (search or "").strip()
    if term:
        low = term.lower()
        full = (func.coalesce(OrgContact.first_name, "") + " "
                + func.coalesce(OrgContact.last_name, ""))
        conds = [func.lower(col).contains(low, autoescape=True) for col in (
            OrgContact.first_name, OrgContact.last_name, OrgContact.full_name,
            OrgContact.company, OrgContact.email, OrgContact.phone,
            OrgContact.mobile_phone, OrgContact.phone_raw, OrgContact.mobile_phone_raw)]
        conds.append(func.lower(full).contains(low, autoescape=True))
        digits = re.sub(r"\D", "", term)
        if len(digits) >= 3:
            # Stored numbers are E.164 ("+12145550101"); "(214) 555-0101" or
            # "214.555" match on their digits.
            conds.append(OrgContact.phone.contains(digits, autoescape=True))
            conds.append(OrgContact.mobile_phone.contains(digits, autoescape=True))
        q = q.filter(or_(*conds))
    return q


@router.get("/contacts")
def list_contacts(record_class: Optional[str] = Query(None),
                  classification: Optional[str] = Query(None),
                  lifecycle: Optional[str] = Query(None),
                  batch_id: Optional[str] = Query(None), search: Optional[str] = Query(None),
                  has_email: Optional[bool] = Query(None),
                  has_phone: Optional[bool] = Query(None),
                  email_ready: Optional[bool] = Query(None),
                  needs_enrichment: Optional[bool] = Query(None),
                  historical_customer: Optional[bool] = Query(None),
                  promoted: Optional[bool] = Query(None),
                  sort: str = Query("recent"),
                  page: int = Query(1, ge=1), per_page: int = Query(50, ge=1, le=200),
                  db: Session = Depends(get_db), user=Depends(require_import_review)):
    if sort not in _SORTS:
        raise HTTPException(422, f"sort must be one of: {', '.join(_SORTS)}")
    ctx = CTX.resolve(db, user)
    q = _contacts_query(db, ctx, record_class=record_class, classification=classification,
                        lifecycle=lifecycle, batch_id=batch_id, search=search,
                        has_email=has_email, has_phone=has_phone, email_ready=email_ready,
                        needs_enrichment=needs_enrichment,
                        historical_customer=historical_customer, promoted=promoted)
    total = q.count()
    if sort == "name":
        # Plain column order (NULLS LAST) so ix_org_contacts_org_name
        # (organization_id, last_name, first_name) can serve it.
        order = (OrgContact.last_name.asc().nullslast(),
                 OrgContact.first_name.asc().nullslast(), OrgContact.id.asc())
    elif sort == "company":
        order = (OrgContact.company_norm.is_(None), OrgContact.company_norm.asc(),
                 OrgContact.last_name.asc(), OrgContact.id.asc())
    else:
        order = (OrgContact.created_at.desc(), OrgContact.id.desc())
    rows = q.order_by(*order).offset((page - 1) * per_page).limit(per_page).all()
    codes = _batch_codes(db, ctx.org_id, [c.import_batch_id for c in rows])
    return {"total": total, "page": page, "per_page": per_page, "sort": sort,
            "contacts": [_contact_row(c, codes) for c in rows]}


_SELECT_ALL_MAX = 25000


@router.get("/contacts/ids")
def contact_ids(record_class: Optional[str] = Query(None),
                classification: Optional[str] = Query(None),
                lifecycle: Optional[str] = Query(None),
                batch_id: Optional[str] = Query(None), search: Optional[str] = Query(None),
                has_email: Optional[bool] = Query(None),
                has_phone: Optional[bool] = Query(None),
                email_ready: Optional[bool] = Query(None),
                needs_enrichment: Optional[bool] = Query(None),
                historical_customer: Optional[bool] = Query(None),
                promoted: Optional[bool] = Query(None),
                db: Session = Depends(get_db), user=Depends(require_import_review)):
    """Every contact id matching the Contacts filter - "select all N matching"."""
    ctx = CTX.resolve(db, user)
    q = _contacts_query(db, ctx, record_class=record_class, classification=classification,
                        lifecycle=lifecycle, batch_id=batch_id, search=search,
                        has_email=has_email, has_phone=has_phone, email_ready=email_ready,
                        needs_enrichment=needs_enrichment,
                        historical_customer=historical_customer, promoted=promoted)
    ids = [r[0] for r in q.with_entities(OrgContact.id)
           .order_by(OrgContact.created_at.desc(), OrgContact.id.desc())
           .limit(_SELECT_ALL_MAX + 1).all()]
    return {"ids": ids[:_SELECT_ALL_MAX], "total": len(ids[:_SELECT_ALL_MAX]),
            "truncated": len(ids) > _SELECT_ALL_MAX, "max": _SELECT_ALL_MAX}


def _contact_or_404(db: Session, ctx, contact_id: str) -> OrgContact:
    c = (db.query(OrgContact)
         .filter(OrgContact.id == contact_id, OrgContact.organization_id == ctx.org_id)
         .first())
    if c is None:
        raise HTTPException(404, "Contact not found.")
    return c


_SMS_CONSENT_NOTE = "Having a phone number is not SMS permission."


@router.get("/contacts/{contact_id}")
def contact_detail(contact_id: str, db: Session = Depends(get_db),
                   user=Depends(require_import_review)):
    from app.models.intake_models import ImportRecordVersion, OrgContactSourceId
    from app.models.models import AuditLogEntry, Lead
    ctx = CTX.resolve(db, user)
    c = _contact_or_404(db, ctx, contact_id)
    batch = None
    if c.import_batch_id:
        batch = (db.query(ImportBatch)
                 .filter(ImportBatch.id == c.import_batch_id,
                         ImportBatch.organization_id == ctx.org_id).first())
    codes = {batch.id: batch.batch_code} if batch else {}

    lead = None
    if c.lead_id:
        lead = (db.query(Lead).filter(Lead.id == c.lead_id,
                                      Lead.organization_id == ctx.org_id).first())

    alt = (db.query(OrgContactSourceId)
           .filter(OrgContactSourceId.organization_id == ctx.org_id,
                   OrgContactSourceId.org_contact_id == c.id)
           .order_by(OrgContactSourceId.created_at.asc()).all())

    contact = _contact_row(c, codes)
    contact.update({
        # Not modelled on org_contacts: a second address line has no column.
        "address_line2": None,
        "country": c.country, "job_title": c.job_title, "owner_name": c.owner_name,
        "last_activity_date": _iso(c.last_activity_at),
        "custom_fields": _j(c.custom_fields, {}),
        "vertical_fields": _j(c.vertical_fields, {}),
        "source_fields": _j(c.source_fields, {}),
        "tags": _j(c.tags, []),
        "alternate_source_ids": [{"source_system": s.source_system,
                                  "source_record_id": s.source_record_id,
                                  "import_batch_id": s.import_batch_id,
                                  "is_primary": bool(s.is_primary)} for s in alt],
        "archived_at": _iso(c.archived_at), "archived_reason": c.archived_reason,
        "updated_at": _iso(c.updated_at),
    })

    imported_at = None
    if batch is not None:
        v = (db.query(ImportRecordVersion.applied_at)
             .filter(ImportRecordVersion.organization_id == ctx.org_id,
                     ImportRecordVersion.batch_id == batch.id,
                     ImportRecordVersion.target_type == "org_contact",
                     ImportRecordVersion.target_id == c.id,
                     ImportRecordVersion.action == "created").limit(1).scalar())
        imported_at = _iso(v or c.created_at)
    provenance = {
        "source": c.source, "source_detail": c.source_detail,
        "source_system": c.source_system, "source_record_id": c.source_record_id,
        "import_batch_id": c.import_batch_id,
        "batch_code": batch.batch_code if batch else None,
        "batch_filename": batch.source_filename if batch else None,
        "imported_at": imported_at,
        "imported_by_name": ((batch.acting_user_name or batch.created_by_name)
                             if batch else None),
        "source_row_number": c.source_row_number,
    }

    # CONSENT IS EVIDENCE, NEVER INFERENCE. The contact row has no consent
    # column at all; the only real evidence the platform keeps is a recorded
    # opt-in (flag AND timestamp) on the linked Lead. Anything else is False.
    consent = bool(lead is not None and lead.sms_consent is True
                   and lead.sms_consent_timestamp is not None)
    contactability = {
        "sms_status": c.sms_status,
        "sms_consent": consent,
        "sms_consent_source": "lead_opt_in_record" if consent else None,
        "email_status": c.email_status,
        "phone_present": bool(c.phone or c.mobile_phone),
        "email_present": bool(c.email),
        "phone_line_type": c.phone_line_type,
        "outreach_reasons": _j(c.outreach_reasons, []),
        "note": _SMS_CONSENT_NOTE,
    }

    lead_payload = None
    if lead is not None:
        lead_payload = {"id": lead.id, "status": lead.status, "tier": lead.tier,
                        "assigned_to_id": lead.assigned_to_id,
                        "created_at": _iso(lead.created_at),
                        "held_over_capacity": lead.capacity_state == "over_capacity"}

    events = (db.query(AuditLogEntry)
              .filter(AuditLogEntry.organization_id == ctx.org_id,
                      AuditLogEntry.target_type == "org_contact",
                      AuditLogEntry.target_id == c.id)
              .order_by(AuditLogEntry.created_at.desc(), AuditLogEntry.id.desc())
              .limit(50).all())
    history = [{"action": e.action, "at": _iso(e.created_at),
                "actor_user_id": e.actor_user_id,
                "details": _j(e.details, e.details)} for e in events]
    # The import writes to a contact are recorded as record versions, not as
    # per-row audit entries (one audit event per batch). They are this
    # contact's history too.
    vers = (db.query(ImportRecordVersion, ImportBatch.batch_code)
            .outerjoin(ImportBatch, ImportBatch.id == ImportRecordVersion.batch_id)
            .filter(ImportRecordVersion.organization_id == ctx.org_id,
                    ImportRecordVersion.target_type == "org_contact",
                    ImportRecordVersion.target_id == c.id)
            .order_by(ImportRecordVersion.applied_at.desc()).limit(50).all())
    for v, code in vers:
        history.append({"action": f"import.{v.action}", "at": _iso(v.applied_at),
                        "actor_user_id": None,
                        "details": {"import_batch_id": v.batch_id, "batch_code": code,
                                    "rolled_back_at": _iso(v.rolled_back_at),
                                    "rollback_outcome": v.rollback_outcome}})
    history.sort(key=lambda h: h["at"] or "", reverse=True)

    return {"contact": contact, "provenance": provenance,
            "contactability": contactability, "lead": lead_payload,
            "history": history[:50]}


class PromoteBody(BaseModel):
    tier: Optional[str] = None
    assigned_to_id: Optional[str] = None
    note: Optional[str] = None


# The roles that may create a lead in a workspace - the lead scope's own
# allow-list (lead_scope: managers, advisors, god). Anything else, "viewer"
# included, is refused: deny by default.
def _may_create_leads(user, db) -> bool:
    from app.services import lead_scope
    role = lead_scope.effective_role(user, db)
    return role in (lead_scope.MANAGER_ROLES + lead_scope.OWNER_SCOPED_ROLES
                    + (lead_scope.GOD_ROLE,))


def _user_in_workspace(db: Session, user_id: str, org_id: str) -> bool:
    from app.models.models import User
    from app.services import workspace_access
    u = db.query(User).filter(User.id == user_id).first()
    if u is None or getattr(u, "is_active", True) is False:
        return False
    if u.organization_id == org_id:
        return True
    try:
        return bool(workspace_access.has_workspace(u, db, org_id))
    except Exception:  # noqa: BLE001 - unknown membership is not membership
        return False


@router.post("/contacts/{contact_id}/promote", status_code=201,
             dependencies=[Depends(require_not_observation),
                           # the same capability list/detail require: promotion
                           # returns (and acts on) the contact's identity.
                           Depends(require_import_review)])
def promote_contact(contact_id: str, body: PromoteBody = PromoteBody(),
                    db: Session = Depends(get_db), user=Depends(require_tenant_context)):
    """EXPLICIT HUMAN ACTION: make this contact a lead. Sends nothing, grants
    no consent, enrolls nothing (app/services/intake/promote.py)."""
    from app.services import industry_templates, lead_scope
    from app.services.intake import promote as PR
    from app.models.models import Organization
    ctx = CTX.resolve(db, user)
    if not _may_create_leads(user, db):
        lead_scope.log_denial(user, "contact promote: role may not create leads here",
                              contact_id)
        raise HTTPException(403, "You do not have permission to create leads in this workspace.")
    c = _contact_or_404(db, ctx, contact_id)

    prior = PR.existing_lead_id(db, ctx.org_id, c)
    if prior:
        return _already_a_lead(db, user, prior)
    if c.archived_at is not None:
        raise HTTPException(409, "An archived contact cannot be promoted.")
    if c.sms_status == "dnc":
        # The import refuses a DNC contact a lead (commit.may_activate_lead);
        # so does a person's click.
        raise HTTPException(409, "do_not_contact")

    org = db.query(Organization).filter(Organization.id == ctx.org_id).first()
    tiers = industry_templates.org_lead_tiers(org)
    valid = [t["value"] for t in tiers if t.get("value")]
    tier = (body.tier or "").strip() or (valid[0] if valid else None)
    if tier is None or tier not in valid:
        raise HTTPException(400, {"message": f"'{body.tier}' is not a lead tier for this "
                                             "organization.", "valid_tiers": valid})

    is_manager = lead_scope.is_manager_here(user, db)
    assigned = (body.assigned_to_id or "").strip() or None
    if assigned is not None:
        if not is_manager and assigned != user.id:
            # An advisor creates leads for themself; ownership is a manager's call.
            raise HTTPException(403, "Only a workspace manager can assign a lead to someone else.")
        if not _user_in_workspace(db, assigned, ctx.org_id):
            raise HTTPException(400, "assigned_to_id is not a user in this workspace.")
    elif not lead_scope.is_god(user):
        assigned = user.id

    try:
        out = PR.promote(db, ctx, c, tier=tier, assigned_to_id=assigned,
                         note=(body.note or "").strip() or None)
    except PR.AlreadyPromoted as e:
        return _already_a_lead(db, user, e.lead_id)
    except PR.DoNotContact:
        raise HTTPException(409, "do_not_contact")
    return out



# ── Delete (explicit human action) ──────────────────────────────────────────
# Same permission as archiving import batches (org admins by role, others by
# grant). The contact is removed; a linked lead is KEPT and detached; a DNC /
# opted-out number is written to suppression first
# (app/services/contact_deletion.py).

class ContactBulkDelete(BaseModel):
    ids: List[str]


_BULK_DELETE_MAX = 500


@router.delete("/contacts/{contact_id}",
               dependencies=[Depends(require_not_observation), Depends(require_import_manage)])
def delete_contact(contact_id: str, db: Session = Depends(get_db),
                   user=Depends(require_tenant_context)):
    from app.services import contact_deletion
    ctx = CTX.resolve(db, user)
    c = _contact_or_404(db, ctx, contact_id)
    return contact_deletion.delete_contact(db, c, getattr(user, "id", None))


@router.post("/contacts/bulk-delete",
             dependencies=[Depends(require_not_observation), Depends(require_import_manage)])
def bulk_delete_contacts(body: ContactBulkDelete, db: Session = Depends(get_db),
                         user=Depends(require_tenant_context)):
    """Delete up to 500 selected contacts in this workspace. Ids that are not
    this workspace's contacts are reported as not found - never touched."""
    from app.services import contact_deletion
    ids = list(dict.fromkeys(i for i in (body.ids or []) if i))
    if not ids:
        raise HTTPException(400, "Select at least one contact.")
    if len(ids) > _BULK_DELETE_MAX:
        raise HTTPException(400, f"Delete at most {_BULK_DELETE_MAX} contacts at a time.")
    ctx = CTX.resolve(db, user)
    found = (db.query(OrgContact)
             .filter(OrgContact.organization_id == ctx.org_id, OrgContact.id.in_(ids)).all())
    deleted, failed = [], []
    for c in found:
        cid = c.id
        try:
            contact_deletion.delete_contact(db, c, getattr(user, "id", None))
            deleted.append(cid)
        except Exception as e:  # noqa: BLE001 - one bad row must not sink the batch
            db.rollback()
            log.exception("contact delete failed for %s", cid)
            failed.append({"id": cid, "reason": "Could not delete this contact (%s)."
                           % type(e).__name__})
    have = {c for c in deleted} | {f["id"] for f in failed}
    not_found = [i for i in ids if i not in have]
    return {"deleted": len(deleted), "deleted_ids": deleted, "failed": failed,
            "not_found": not_found, "requested": len(ids)}


class ContactBulkPromote(BaseModel):
    ids: List[str]
    tier: Optional[str] = None
    assigned_to_id: Optional[str] = None


_BULK_PROMOTE_MAX = 200


@router.post("/contacts/bulk-promote",
             dependencies=[Depends(require_not_observation), Depends(require_import_review)])
def bulk_promote_contacts(body: ContactBulkPromote, db: Session = Depends(get_db),
                          user=Depends(require_tenant_context)):
    """EXPLICIT HUMAN ACTION on the contacts a person selected: each becomes one
    lead, by the same rules as the single "Promote to Lead" (app/services/
    intake/promote.py) - no consent granted, nothing enrolled, nothing sent,
    plan capacity HOLDS rather than drops. Contacts that cannot become a useful
    lead are skipped and reported with the reason, never silently."""
    from app.services import industry_templates, lead_scope
    from app.services.intake import promote as PR
    from app.models.models import Organization
    ids = list(dict.fromkeys(i for i in (body.ids or []) if i))
    if not ids:
        raise HTTPException(400, "Select at least one contact.")
    if len(ids) > _BULK_PROMOTE_MAX:
        raise HTTPException(400, f"Promote at most {_BULK_PROMOTE_MAX} contacts per request.")
    ctx = CTX.resolve(db, user)
    if not _may_create_leads(user, db):
        lead_scope.log_denial(user, "contact bulk promote: role may not create leads here", None)
        raise HTTPException(403, "You do not have permission to create leads in this workspace.")
    org = db.query(Organization).filter(Organization.id == ctx.org_id).first()
    valid = [t["value"] for t in industry_templates.org_lead_tiers(org) if t.get("value")]
    tier = (body.tier or "").strip() or (valid[0] if valid else None)
    if tier is None or tier not in valid:
        raise HTTPException(400, {"message": f"'{body.tier}' is not a lead tier for this "
                                             "organization.", "valid_tiers": valid})
    is_manager = lead_scope.is_manager_here(user, db)
    assigned = (body.assigned_to_id or "").strip() or None
    if assigned is not None:
        if not is_manager and assigned != user.id:
            raise HTTPException(403, "Only a workspace manager can assign leads to someone else.")
        if not _user_in_workspace(db, assigned, ctx.org_id):
            raise HTTPException(400, "assigned_to_id is not a user in this workspace.")
    elif not lead_scope.is_god(user):
        assigned = user.id

    from app.services.intake import commit as CM
    found = {c.id: c for c in db.query(OrgContact).filter(
        OrgContact.organization_id == ctx.org_id, OrgContact.id.in_(ids)).all()}
    promoted, held, skipped = [], 0, []
    reasons = {}

    def skip(cid, why):
        skipped.append({"id": cid, "reason": why})
        reasons[why] = reasons.get(why, 0) + 1

    for cid in ids:
        c = found.get(cid)
        if c is None:
            skip(cid, "Not a contact in this workspace")
            continue
        if c.archived_at is not None:
            skip(cid, "Archived")
            continue
        if c.sms_status == "dnc":
            skip(cid, "Do not contact")
            continue
        has_phone = bool(CM._lead_phone(c.mobile_phone or c.phone))
        has_email = bool(c.email) and c.email_status not in CM._BAD_EMAIL
        if not has_phone and not has_email:
            skip(cid, "No phone and no working email")
            continue
        try:
            out = PR.promote(db, ctx, c, tier=tier, assigned_to_id=assigned)
            promoted.append(out["lead_id"])
            held += 1 if out.get("held_over_capacity") else 0
        except PR.AlreadyPromoted:
            skip(cid, "Already a lead")
        except PR.DoNotContact:
            skip(cid, "Do not contact")
        except Exception:  # noqa: BLE001 - one bad row must not sink the batch
            db.rollback()
            log.exception("bulk promote failed for contact %s", cid)
            skip(cid, "Could not be promoted (server error)")
    return {"requested": len(ids), "promoted": len(promoted), "lead_ids": promoted,
            "held_over_capacity": held, "skipped": skipped, "skipped_by_reason": reasons,
            "tier": tier}

def _already_a_lead(db: Session, user, lead_id: Optional[str]):
    """409, naming the lead only if the caller may see it (an advisor is not
    told the id of a colleague's lead)."""
    from app.models.models import Lead
    from app.services import lead_scope
    visible = None
    if lead_id:
        try:
            visible = (lead_scope.authorized_lead_query(db, user, Lead.id)
                       .filter(Lead.id == lead_id).scalar())
        except HTTPException:
            visible = None
    content = {"detail": "This contact is already a lead."}
    if visible:
        content["lead_id"] = visible
    return JSONResponse(status_code=409, content=content)
