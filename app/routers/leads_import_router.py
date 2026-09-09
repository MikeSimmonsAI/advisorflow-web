import logging
import os
import shutil
import tempfile
import json as _json
from fastapi import (
    APIRouter, Depends, UploadFile, File, Form, Query, HTTPException, Request, Response,
)
from sqlalchemy.orm import Session
from sqlalchemy import func, distinct
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timedelta, time, timezone

from app.deps import get_db, require_tenant_user, require_tenant_or_observer
from app.limiter import limiter
from app.services.platform_owner import require_tenant_context
from app.models.models import User, Lead, Reply, ReplyClassification, CadenceState, BookingLink, EngagementTemperature, CRMContact, VoiceCall
from app.services.import_service import import_leads_from_excel
from app.services.import_permissions import require_import_stage, require_import_commit
from app.services.import_staging_service import stage_batch as _stage_batch
from app.services.import_commit_service import commit_batch as _commit_batch_svc
from app.models.import_models import (
    ImportBatch, ImportBatchStatus, ImportStagedRow,
    ImportRowReviewStatus, ImportDuplicateStatus, ImportValidationStatus,
)
from app.models.models import gen_uuid
from app.services.dedup_service import normalize_phone
from app.routers.audit_log_router import log_action
# THE ONE AUTHORIZED LEAD SCOPE. Every list, count, search, export and
# single-record fetch in this file goes through it, so the advisor boundary is
# stated once instead of re-derived per route.
from app.services import lead_scope
from app.services.lead_scope import (authorized_lead_query, load_lead_in_scope, assert_leads_in_scope, reject_ownership_fields)

router = APIRouter()


def _is_suppressed(db: Session, lead: Lead) -> bool:
    """Lazy import to avoid a circular import (compliance_service -> compliance_router -> ... )."""
    from app.services.compliance_service import is_phone_suppressed
    return is_phone_suppressed(db, lead.organization_id, lead.phone)



@router.post("/upload/preview")
def preview_upload(
    file: UploadFile = File(...),
    source_year: Optional[int] = Form(None),
    force_new_inquiry: bool = Form(False),
    relationship_type: Optional[str] = Form(None),  # applied to all leads in this import
    import_list_name: Optional[str] = Form(None),
    campaign_purpose: Optional[str] = Form(None),   # why we're reaching out
    offer_hook: Optional[str] = Form(None),          # what we're offering
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_context),
    _response: Response = None,
):
    # DEPRECATED — retained for backward compatibility with the existing advisor
    # upload flow.  New callers should POST to POST /import-batches which routes
    # through the full Lead Import Intelligence pipeline (stage → review → commit).
    # This endpoint still calls import_leads_from_excel in dry_run=True mode and
    # creates no live Leads, so it is safe to keep running indefinitely, but it
    # bypasses the staging review gate.
    if _response is not None:
        _response.headers["Deprecation"] = "true"
        _response.headers["Sunset"] = "2027-01-01"
        _response.headers["Link"] = '</import-batches>; rel="successor-version"'
    import os as _os
    original_ext = _os.path.splitext(file.filename or "upload.xlsx")[1].lower() or ".xlsx"
    if original_ext not in (".xlsx", ".xls", ".csv"):
        raise HTTPException(status_code=400, detail="Only .xlsx, .xls, and .csv files are accepted.")

    with tempfile.NamedTemporaryFile(delete=False, suffix=original_ext) as tmp:
        # Stream with size cap — reject files over 50MB to protect against memory DoS
        MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB
        written = 0
        chunk_size = 1024 * 64  # 64 KB
        while True:
            chunk = file.file.read(chunk_size)
            if not chunk:
                break
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                tmp.close()
                os.unlink(tmp.name)
                raise HTTPException(status_code=413, detail="File too large. Maximum upload size is 50 MB.")
            tmp.write(chunk)
        tmp_path = tmp.name

    try:
        summary = import_leads_from_excel(
            db,
            file_path=tmp_path,
            organization_id=current_user.organization_id,
            uploading_user_id=current_user.id,
            source_year=source_year,
            source_filename=file.filename,
            dry_run=True,
            force_new_inquiry=force_new_inquiry,
            relationship_type=relationship_type or "cold_lead",
            import_list_name=import_list_name,
            campaign_purpose=campaign_purpose,
            offer_hook=offer_hook,
        )
    except ValueError as exc:
        # A file we cannot read is a 400 the uploader can act on - the wrong
        # column, the wrong sheet - not a 500. Raised as a real HTTP error so
        # the browser gets a CORS-headed response and shows the reason instead
        # of reporting a network failure.
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        os.unlink(tmp_path)

    return summary


@router.post("/upload/confirm")
def confirm_upload(
    file: UploadFile = File(...),
    source_year: Optional[int] = Form(None),
    force_new_inquiry: bool = Form(False),
    relationship_type: Optional[str] = Form(None),
    import_list_name: Optional[str] = Form(None),
    campaign_purpose: Optional[str] = Form(None),
    offer_hook: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    # Legacy surface; canonical pipeline authority enforced.
    # Caller must hold BOTH lead_import_stage AND lead_import_commit — the adapter
    # performs both operations on behalf of the caller.
    current_user: User = Depends(require_import_stage),
    _commit_check=Depends(require_import_commit),
    _response: Response = None,
):
    """Legacy compatibility adapter for POST /leads/upload/confirm.

    IMPLEMENTATION NOTE: This endpoint is DEPRECATED as an API surface but is
    NOT deprecated as an implementation. It routes every request through the
    canonical Lead Import Intelligence pipeline (stage → compliance → dedup →
    review classification → commit). There is NO path here that creates a live
    Lead without passing through import_staging_service and import_commit_service.

    Backward-compatible response contract:
    - All rows clean   → { review_required: False, import_batch_id, committed_count, ... }
    - Any row flagged  → { review_required: True,  import_batch_id, batch_status,
                           ready_count, review_required_count, excluded_count }
      In this case NO rows are committed. The batch remains open for human review
      at /import-batches/{import_batch_id}.
    """
    if _response is not None:
        _response.headers["Deprecation"] = "true"
        _response.headers["Sunset"] = "2027-01-01"
        _response.headers["Link"] = '</import-batches>; rel="successor-version"'

    import os as _os
    original_ext = _os.path.splitext(file.filename or "upload.xlsx")[1].lower() or ".xlsx"
    if original_ext not in (".xlsx", ".xls", ".csv"):
        raise HTTPException(status_code=400, detail="Only .xlsx, .xls, and .csv files are accepted.")

    # ── 1. Stream to temp file ────────────────────────────────────────────────
    tmp_path = None
    batch_id = gen_uuid()
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=original_ext) as tmp:
            MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB
            written = 0
            chunk_size = 1024 * 64
            while True:
                chunk = file.file.read(chunk_size)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    tmp.close()
                    os.unlink(tmp.name)
                    raise HTTPException(status_code=413,
                                        detail="File too large. Maximum upload size is 50 MB.")
                tmp.write(chunk)
            tmp_path = tmp.name

        # ── 2. Create ImportBatch record ──────────────────────────────────────
        display_name = (import_list_name or
                        os.path.splitext(file.filename or "upload")[0] or
                        "Legacy Upload")
        batch = ImportBatch(
            id=batch_id,
            organization_id=current_user.organization_id,
            display_name=display_name,
            source_type=original_ext.lstrip("."),
            source_filename=file.filename,
            status=ImportBatchStatus.UPLOADING,
            created_by_id=current_user.id,
            created_at=datetime.now(timezone.utc),
            # The upload form's own choices, recorded on the batch so the
            # commit step can apply them. They are stored HERE rather than
            # passed down the call chain because stage_batch and commit_batch
            # both already load this row - the batch is the one thing every
            # stage of the pipeline can see, and a value on it cannot go
            # missing between two function signatures the way these four just
            # did. Every one of them was declared as a Form field on this
            # endpoint and then never handed to the pipeline at all, so a
            # "Source year" the user typed was parsed correctly and discarded.
            source_year=source_year,
            force_new_inquiry=force_new_inquiry,
            relationship_type=relationship_type,
            import_list_name=import_list_name,
        )
        db.add(batch)
        db.commit()

        # ── 3. Canonical staging — compliance, bulk dedup, provenance ─────────
        try:
            _stage_batch(batch_id, current_user.organization_id, tmp_path,
                         original_ext.lstrip("."), db)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    # ── 4. Classify staged rows ───────────────────────────────────────────────
    # A row needs human review if:
    #   a) any consent channel was ambiguous (consent_review_required=True), OR
    #   b) it is a possible duplicate requiring merge decision
    # Invalid rows are auto-rejected. All others are auto-accepted for commit.
    rows = db.query(ImportStagedRow).filter(
        ImportStagedRow.batch_id == batch_id
    ).all()

    review_required_count = 0
    excluded_count = 0

    for row in rows:
        needs_review = (
            row.consent_review_required
            or row.duplicate_status == ImportDuplicateStatus.POSSIBLE_DUPLICATE
        )
        if needs_review:
            review_required_count += 1
            # Leave row.review_status as PENDING — visible in review UI
        elif row.validation_status == ImportValidationStatus.INVALID:
            row.review_status = ImportRowReviewStatus.REJECTED
            excluded_count += 1
        else:
            row.review_status = ImportRowReviewStatus.ACCEPTED

    db.commit()
    batch.recount(db)

    ready_count = (batch.total_rows or 0) - review_required_count - excluded_count

    # ── 5. If any row requires human review: stop here ────────────────────────
    # NEVER auto-commit a batch that contains ambiguous consent or duplicate
    # conflicts. Return a review-required response so the frontend can direct
    # the user to /import-batches/{batch_id}.
    if review_required_count > 0:
        batch.status = ImportBatchStatus.READY_FOR_REVIEW
        db.commit()
        return {
            "review_required": True,
            "import_batch_id": batch.id,
            "batch_status": batch.status,
            "ready_count": ready_count,
            "review_required_count": review_required_count,
            "excluded_count": excluded_count,
            "message": (
                f"{review_required_count} record(s) need review before they can be imported. "
                f"Open the import batch to review them."
            ),
        }

    # ── 6. All rows are clean — commit through canonical service ──────────────
    try:
        committed_batch = _commit_batch_svc(
            batch_id, current_user.organization_id, db, current_user.id
        )
    except Exception as exc:
        raise HTTPException(status_code=500,
                            detail=f"Commit failed: {str(exc)[:200]}")

    committed_batch_refreshed = db.query(ImportBatch).filter(
        ImportBatch.id == batch_id
    ).first()

    # tier_breakdown, counted from the LEADS THAT WERE ACTUALLY WRITTEN.
    #
    # Deliberately not recomputed from the staged rows' inferred tier: a
    # batch-level force_new_inquiry overrides that at commit time, so counting
    # the staged rows would report what the file said rather than what the
    # import did. Counting the committed rows is the only version that cannot
    # lie about the outcome.
    committed_row_ids = [
        r.committed_lead_id for r in
        db.query(ImportStagedRow).filter(
            ImportStagedRow.batch_id == batch_id,
            ImportStagedRow.review_status == ImportRowReviewStatus.COMMITTED,
        ).all()
        if getattr(r, "committed_lead_id", None)
    ]
    tier_breakdown = {}
    if committed_row_ids:
        for (tier_value, count) in (
            db.query(Lead.tier, func.count(Lead.id))
            .filter(Lead.id.in_(committed_row_ids))
            .group_by(Lead.tier)
            .all()
        ):
            tier_breakdown[tier_value or "unknown"] = count

    return {
        "review_required": False,
        "import_batch_id": batch_id,
        "batch_status": committed_batch_refreshed.status if committed_batch_refreshed else "committed",
        "committed_count": committed_batch_refreshed.committed_rows if committed_batch_refreshed else ready_count,
        "excluded_count": excluded_count,
        "tier_breakdown": tier_breakdown,
        # Backward-compatible fields the old UI may have checked
        "created": committed_batch_refreshed.committed_rows if committed_batch_refreshed else ready_count,
        "updated": 0,
        "skipped": excluded_count,
    }


@router.get("/import-batches")
def list_import_batches(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """Import batch inventory for this organization. ADMINS ONLY.

    THIS ENDPOINT WAS THE REPORTED BREACH. It carried `require_tenant_user` and
    grouped over the whole organization, so any plain advisor received the
    complete import inventory: every source filename, every import list name,
    the name of the person who imported each one, and a lead count per batch -
    `Restland_Dallas.csv`, `garden memories.csv`, `All Active Leads (2012).xlsx`,
    `google_contacts_restland_...`, `voice:Taffiney`. None of that is data an
    advisor is authorized to see, and the filenames alone disclose the
    organization's data sources, its acquisition history and its other staff.

    An advisor has no use for batch inventory: their leads are the ones assigned
    to them, and which file a lead arrived in is operational provenance for
    whoever runs imports. So this is refused outright rather than filtered down
    to a sanitized subset - a narrowed inventory is still an inventory, and it
    would still leak filenames through whichever batches happen to contain one
    of the advisor's leads.

    The DELETE beside this endpoint was already admin-only. The read was not.
    """
    if not lead_scope.is_manager(current_user):
        lead_scope.log_denial(current_user, "advisor requested org import inventory",
                              None, request)
        raise HTTPException(
            status_code=403,
            detail="Import batches are managed by an administrator.")
    rows = (
        db.query(
            Lead.source_file,
            Lead.import_list_name,
            Lead.imported_by_name,
            func.count(Lead.id).label("lead_count"),
            func.min(Lead.created_at).label("imported_at"),
        )
        .filter(
            Lead.organization_id == lead_scope.active_workspace_org_id(current_user, db),
            Lead.source_file.isnot(None),
        )
        .group_by(Lead.source_file, Lead.import_list_name, Lead.imported_by_name)
        .order_by(func.min(Lead.created_at).desc())
        .all()
    )
    return [
        {
            "source_file": r.source_file,
            "import_list_name": r.import_list_name,
            "imported_by_name": r.imported_by_name,
            "lead_count": r.lead_count,
            "imported_at": r.imported_at.isoformat() if r.imported_at else None,
        }
        for r in rows
    ]


@router.delete("/import-batches")
def delete_import_batch(
    source_file: str = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """
    Delete all leads from a specific import batch + every dependent record
    + their contact registry entries so a clean re-import works without
    duplicate flags. Restricted to org_admin / super_admin.
    """
    if current_user.role not in ("org_admin", "super_admin", "god_admin"):
        raise HTTPException(status_code=403, detail="Only admins can delete import batches.")

    org_id = current_user.organization_id

    # Collect lead IDs for this batch using a subquery so we hit the DB once.
    from sqlalchemy import text as sa_text
    lead_ids_result = db.execute(
        sa_text(
            "SELECT id FROM leads WHERE organization_id = :org AND source_file = :sf"
        ),
        {"org": org_id, "sf": source_file},
    ).fetchall()
    lead_ids = [r[0] for r in lead_ids_result]

    if not lead_ids:
        raise HTTPException(status_code=404, detail="No leads found for that batch.")

    # Use subquery deletes to avoid huge IN clauses on large batches.
    # ORDER MATTERS — children must be deleted before their parents.
    # survey_responses.booking_followup_id → booking_followups (so surveys first)
    # booking_followups.lead_id → leads
    # booking_links.lead_id → leads (booking_followups also FK to booking_links, so links last)
    batch_subq = (
        "SELECT id FROM leads WHERE organization_id = :org AND source_file = :sf"
    )
    p = {"org": org_id, "sf": source_file}

    # Correct FK-safe deletion order
    dependents_by_lead_id = [
        "cadence_states",
        "email_messages",
        "messages",
        "replies",
        "pipeline_conversations",
        "lead_outcomes",
        "notifications",
        "voice_calls",
        # survey_responses must come BEFORE booking_followups (FK: survey → followup)
        "survey_responses",
        "booking_followups",
    ]

    deleted = {}

    # NULL out duplicate_of_lead_id on any leads OUTSIDE this batch that
    # point INTO it — otherwise the leads delete will hit a self-FK violation
    try:
        db.execute(
            sa_text(
                f"UPDATE leads SET duplicate_of_lead_id = NULL "
                f"WHERE duplicate_of_lead_id IN ({batch_subq})"
            ),
            p,
        )
    except Exception:
        pass  # column may not exist in older schemas

    for table in dependents_by_lead_id:
        try:
            r = db.execute(
                sa_text(f"DELETE FROM {table} WHERE lead_id IN ({batch_subq})"), p
            )
            if r.rowcount:
                deleted[table] = r.rowcount
        except Exception:
            # Table may not exist yet in this deployment — skip and continue
            db.rollback()
            db.begin()

    # booking_links last (booking_followups.booking_link_id → booking_links)
    try:
        r = db.execute(
            sa_text(f"DELETE FROM booking_links WHERE lead_id IN ({batch_subq})"), p
        )
        if r.rowcount:
            deleted["booking_links"] = r.rowcount
    except Exception:
        db.rollback()
        db.begin()

    # contact_registry: remove entries whose first_seen_lead_id is in this batch
    # so re-import doesn't flag every lead as a duplicate
    try:
        r = db.execute(
            sa_text(
                f"DELETE FROM contact_registry WHERE organization_id = :org "
                f"AND first_seen_lead_id IN ({batch_subq})"
            ),
            p,
        )
        if r.rowcount:
            deleted["contact_registry"] = r.rowcount
    except Exception:
        pass

    # Finally delete the leads themselves
    r = db.execute(
        sa_text(
            "DELETE FROM leads WHERE organization_id = :org AND source_file = :sf"
        ),
        p,
    )
    deleted["leads"] = r.rowcount

    db.commit()

    log_action(
        db,
        actor_user_id=current_user.id,
        organization_id=org_id,
        action="import_batch_deleted",
        target_type="lead_batch",
        target_id=source_file,
        details=f"Deleted {deleted.get('leads', 0)} leads from batch '{source_file}'",
    )

    return {"deleted": deleted, "source_file": source_file}

