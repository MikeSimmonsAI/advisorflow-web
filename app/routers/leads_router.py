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

from app.deps import get_db, require_tenant_user
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

router = APIRouter(prefix="/leads", tags=["leads"])


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

    return {
        "review_required": False,
        "import_batch_id": batch_id,
        "batch_status": committed_batch_refreshed.status if committed_batch_refreshed else "committed",
        "committed_count": committed_batch_refreshed.committed_rows if committed_batch_refreshed else ready_count,
        "excluded_count": excluded_count,
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


@router.get("/")
def list_leads(
    request: Request,
    status_filter: Optional[str] = Query(None, alias="status"),
    tier: Optional[str] = Query(None),
    message_track: Optional[str] = Query(None),
    temperature: Optional[str] = Query(None),
    import_list_name: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(500, ge=1, le=2000),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """
    Advisors see only their own leads. org_admin/super_admin see all org leads.
    Returns a lean payload — only columns needed by the list view, no large
    text blobs (notes, ai_quality_note, custom_fields). This keeps the Leads
    page fast even with thousands of leads.
    """
    is_manager = lead_scope.is_manager_here(current_user, db)

    # Select only the columns the list view needs — avoids loading large text
    # fields (notes, ai_lead_quality_note, custom_fields, extra_data) and
    # prevents SQLAlchemy lazy-loading relationships causing N+1 queries.
    COLS = [
        Lead.id, Lead.first_name, Lead.last_name, Lead.phone, Lead.email,
        Lead.status, Lead.tier, Lead.message_track, Lead.source_file,
        Lead.source_year, Lead.is_duplicate, Lead.assigned_to_id,
        Lead.engagement_temperature, Lead.relationship_type,
        Lead.contact_channel, Lead.import_list_name, Lead.imported_by_name,
        Lead.created_at, Lead.organization_id, Lead.case_status,
        Lead.manual_flag, Lead.manual_flag_reason,
        # Why a duplicate flag is set, so the Duplicates tab can say more than
        # the word "duplicate" and offer a resolution instead of deletion.
        Lead.duplicate_reason, Lead.duplicate_match_field,
        Lead.duplicate_match_value, Lead.duplicate_of_lead_id,
        Lead.last_messaged_at,
    ]
    COL_NAMES = [
        "id", "first_name", "last_name", "phone", "email",
        "status", "tier", "message_track", "source_file",
        "source_year", "is_duplicate", "assigned_to_id",
        "engagement_temperature", "relationship_type",
        "contact_channel", "import_list_name", "imported_by_name",
        "created_at", "organization_id", "case_status",
        "manual_flag", "manual_flag_reason",
        "duplicate_reason", "duplicate_match_field",
        "duplicate_match_value", "duplicate_of_lead_id",
        "last_messaged_at",
    ]

    # THE ONE AUTHORIZED SCOPE. This route used to build its own: an inline
    # `_god_all_orgs` branch, an organization filter, and `if not is_manager:
    # filter(assigned_to_id)`. That was correct - and it was correct in
    # ISOLATION, which is why the counts, the timeline, the AI hub, the email
    # queue and eighty other routes each had their own version and most of them
    # were wrong. Sharing the function is what stops the list and the tiles
    # above it from ever disagreeing about who this advisor is.
    query = authorized_lead_query(db, current_user, *COLS, request=request)
    if status_filter:
        query = query.filter(Lead.status == status_filter)
    if tier:
        query = query.filter(Lead.tier == tier)
    if message_track:
        query = query.filter(Lead.message_track == message_track)
    if temperature:
        query = query.filter(Lead.engagement_temperature == temperature)
    if import_list_name:
        query = query.filter(Lead.import_list_name == import_list_name)
    # Default: exclude remove_all flagged leads from main list (they appear in flagged section)
    # bad_email flagged leads remain in the main list (still contactable by SMS)
    query = query.filter(
        (Lead.manual_flag == None) | (Lead.manual_flag == "bad_email")
    )

    total = query.count()
    rows = (
        query
        .order_by(Lead.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = []
    for row in rows:
        d = dict(zip(COL_NAMES, row))
        if d.get("created_at"):
            d["created_at"] = d["created_at"].isoformat()
        items.append(d)

    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/flagged")
def list_flagged_leads(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """Return all manually flagged leads for this org (both bad_email and remove_all)."""
    is_manager = lead_scope.is_manager_here(current_user, db)
    query = db.query(Lead).filter(
        Lead.organization_id == lead_scope.active_workspace_org_id(current_user, db),
        Lead.manual_flag != None,
    )
    if not is_manager:
        query = query.filter(Lead.assigned_to_id == current_user.id)
    leads = query.order_by(Lead.updated_at.desc()).limit(500).all()
    return [
        {
            "id": l.id,
            "first_name": l.first_name,
            "last_name": l.last_name,
            "email": l.email,
            "phone": l.phone,
            "manual_flag": l.manual_flag,
            "manual_flag_reason": l.manual_flag_reason,
            "tier": l.tier,
            "status": l.status,
            "contact_channel": l.contact_channel,
        }
        for l in leads
    ]


@router.get("/needs-review")
def leads_needing_tier_review(
    page: int = Query(1, ge=1),
    page_size: int = Query(500, ge=1, le=2000),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """
    Leads imported with no Lead Type set in the source file (untyped/blank).
    These are held out of any SMS queue until a real tier is assigned -
    they are NOT defaulted to Pre-Need.
    """
    COLS = [
        Lead.id, Lead.first_name, Lead.last_name, Lead.phone, Lead.email,
        Lead.status, Lead.tier, Lead.message_track, Lead.source_file,
        Lead.source_year, Lead.is_duplicate, Lead.assigned_to_id,
        Lead.engagement_temperature, Lead.relationship_type,
        Lead.contact_channel, Lead.import_list_name, Lead.created_at,
        Lead.organization_id, Lead.case_status,
    ]
    COL_NAMES = [
        "id", "first_name", "last_name", "phone", "email",
        "status", "tier", "message_track", "source_file",
        "source_year", "is_duplicate", "assigned_to_id",
        "engagement_temperature", "relationship_type",
        "contact_channel", "import_list_name", "created_at",
        "organization_id", "case_status",
    ]
    query = db.query(*COLS).filter(
        Lead.organization_id == lead_scope.active_workspace_org_id(current_user, db),
        Lead.assigned_to_id == current_user.id,
        Lead.status == "needs_tier_review",
    )
    total = query.count()
    rows = query.order_by(Lead.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    items = []
    for row in rows:
        d = dict(zip(COL_NAMES, row))
        if d.get("created_at"):
            d["created_at"] = d["created_at"].isoformat()
        items.append(d)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.patch("/{lead_id}/tier")
def set_lead_tier(
    lead_id: str,
    new_tier: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """
    Manually assign a tier to a needs-review lead, which also sets its
    message_track and unlocks it for the SMS queue.

    Scope note: intentionally org-wide rather than restricted to leads
    assigned to current_user, unlike GET /needs-review above which only
    lists the calling advisor's own needs-review leads. Re-tiering is a
    reversible data-correction action (similar to the Lead Cleanup
    contact-info fixes), and any advisor noticing a teammate's
    obviously-mistagged lead should be able to fix it rather than waiting
    on that specific advisor. Logged below so there's still a clear trail
    of who changed what.
    """
    from app.models.models import LeadTier
    from app.services.import_service import TIER_TO_TRACK

    lead = authorized_lead_query(db, current_user).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    try:
        tier_enum = LeadTier(new_tier)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid tier: {new_tier}")

    previous_tier = lead.tier if lead.tier else None

    lead.tier = tier_enum
    lead.message_track = TIER_TO_TRACK.get(tier_enum)
    lead.status = "new"
    db.commit()

    log_action(
        db, current_user.organization_id, current_user.id,
        action="lead.set_tier", target_type="lead", target_id=lead.id,
        details={"from": previous_tier, "to": tier_enum.value, "lead_assigned_to_id": lead.assigned_to_id},
    )

    return lead



@router.get("/daily-briefing")
def daily_briefing(db: Session = Depends(get_db), current_user: User = Depends(require_tenant_user)):
    """
    Advisor-scoped daily briefing data for the Overview page.

    This deliberately mirrors the existing needs_attention behavior from
    GET /sms/replies?needs_attention=true: Interested + Callback replies on
    leads owned by the logged-in advisor. It does not introduce a separate
    definition that could drift from the Replies inbox.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    start_24h = now - timedelta(hours=24)
    end_of_today = datetime.combine(now.date(), time.max)
    start_7d = now - timedelta(days=7)

    is_manager = lead_scope.is_manager_here(current_user, db)
    base_lead_filters = [Lead.organization_id == lead_scope.active_workspace_org_id(current_user, db)]
    if not is_manager:
        base_lead_filters.append(Lead.assigned_to_id == current_user.id)

    replies_needing_attention = (
        db.query(func.count(Reply.id))
        .join(Lead, Reply.lead_id == Lead.id)
        .filter(
            *base_lead_filters,
            Reply.classification.in_([ReplyClassification.INTERESTED, ReplyClassification.CALLBACK]),
        )
        .scalar()
        or 0
    )

    cadence_touches_due_today = (
        db.query(func.count(CadenceState.id))
        .join(Lead, CadenceState.lead_id == Lead.id)
        .filter(
            *base_lead_filters,
            CadenceState.status == "active",
            CadenceState.next_touch_due_at.isnot(None),
            CadenceState.next_touch_due_at <= end_of_today,
        )
        .scalar()
        or 0
    )

    leads_imported_last_24h = (
        db.query(func.count(Lead.id))
        .filter(
            *base_lead_filters,
            Lead.created_at >= start_24h,
        )
        .scalar()
        or 0
    )

    bookings_last_7_days = (
        db.query(func.count(distinct(BookingLink.lead_id)))
        .join(Lead, BookingLink.lead_id == Lead.id)
        .filter(
            *base_lead_filters,
            BookingLink.status == "booked",
            BookingLink.booked_time.isnot(None),
            BookingLink.booked_time >= start_7d,
        )
        .scalar()
        or 0
    )

    # Appointments that are booked or confirmed and still pending outcome
    certified_appointments_waiting = (
        db.query(func.count(distinct(BookingLink.lead_id)))
        .join(Lead, BookingLink.lead_id == Lead.id)
        .filter(
            *base_lead_filters,
            BookingLink.status.in_(["booked", "confirmed"]),
        )
        .scalar()
        or 0
    )

    return {
        "replies_needing_attention": replies_needing_attention,
        "cadence_touches_due_today": cadence_touches_due_today,
        "leads_imported_last_24h": leads_imported_last_24h,
        "bookings_last_7_days": bookings_last_7_days,
        "certified_appointments_waiting": certified_appointments_waiting,
    }


@router.get("/engagement-breakdown")
def engagement_breakdown(db: Session = Depends(get_db), current_user: User = Depends(require_tenant_user)):
    """
    Advisor-scoped engagement temperature counts for the Overview chart.
    Uses the real Lead.engagement_temperature field; no client-side guesses.
    """
    is_manager = lead_scope.is_manager_here(current_user, db)
    eng_filters = [Lead.organization_id == lead_scope.active_workspace_org_id(current_user, db)]
    if not is_manager:
        eng_filters.append(Lead.assigned_to_id == current_user.id)
    rows = (
        db.query(Lead.engagement_temperature, func.count(Lead.id))
        .filter(*eng_filters)
        .group_by(Lead.engagement_temperature)
        .all()
    )
    counts = {temperature.value: 0 for temperature in EngagementTemperature}
    for temperature, count in rows:
        key = temperature.value if temperature else EngagementTemperature.UNKNOWN.value
        counts[key] = int(count or 0)
    return counts


@router.get("/status-funnel")
def status_funnel(db: Session = Depends(get_db), current_user: User = Depends(require_tenant_user)):
    """
    Advisor-scoped real lead status funnel for Overview.
    Only returns the stages displayed in the dashboard funnel.
    """
    stages = [
        "new",
        "sent",
        "replied",
        "hot",
        "booked",
    ]
    is_manager = lead_scope.is_manager_here(current_user, db)
    funnel_filters = [
        Lead.organization_id == lead_scope.active_workspace_org_id(current_user, db),
        Lead.status.in_(stages),
    ]
    if not is_manager:
        funnel_filters.append(Lead.assigned_to_id == current_user.id)
    rows = (
        db.query(Lead.status, func.count(Lead.id))
        .filter(*funnel_filters)
        .group_by(Lead.status)
        .all()
    )
    counts = {stage: 0 for stage in stages}
    for status, count in rows:
        if status and status in counts:
            counts[status] = int(count or 0)
    return [
        {"status": stage, "label": stage.replace("_", " ").title(), "count": counts[stage]}
        for stage in stages
    ]

@router.get("/{lead_id}")
def get_lead(lead_id: str, db: Session = Depends(get_db), current_user: User = Depends(require_tenant_user)):
    """Returns full contact-card detail for a single lead.

    A P0 MISS, FOUND BY THE WORKSPACE GATE.

    This route wrote the authorization rule out by hand - its own manager role
    list, its own filter on current_user.organization_id, its own owner filter -
    and because that hand-written rule happened to be CORRECT, the P0 sweep left
    it alone and the P0 gate passed on it. A fourth copy of a rule is still a
    fourth copy: it cannot be reached by the one function, so it does not
    inherit anything the one function learns.

    It learned two things this round, and this route had neither. The workspace
    a request is in can now come from a validated membership rather than the
    column, and the role that decides scope inside a workspace is the
    MEMBERSHIP's role, not `users.role`. Standing on the column and the column
    alone, this refused Michael his own lead the moment he entered through a
    membership - a 404 on a record he owns.

    Routed through load_lead_in_scope, which is the same 404 for an
    out-of-scope lead and the same reason: a 403 here confirms the record
    exists.
    """
    return load_lead_in_scope(db, current_user, lead_id)


@router.get("/{lead_id}/timeline")
def get_lead_timeline(lead_id: str, db: Session = Depends(get_db), current_user: User = Depends(require_tenant_user)):
    """
    Returns the full conversation thread for one lead: every outbound
    message and every inbound reply, merged into one chronological feed,
    plus the AI lead-quality note if one exists, plus their most recent
    booking link status. Built for the lead detail page so an advisor
    can see everything about one person in one place instead of hunting
    across the Leads and Replies screens separately.

    Booking info was a real gap: the BookingLink table (whether a lead
    booked, what time, whether a Google Calendar event was created) was
    tracked on the backend the whole time but never surfaced anywhere in
    the UI - an advisor had no way to see if someone actually booked.
    """
    from app.models.models import Message, Reply, BookingLink, EmailMessage, CadenceState, VoiceCall as _VoiceCall

    is_manager_tl = lead_scope.is_manager_here(current_user, db)
    q_tl = db.query(Lead).filter(Lead.id == lead_id, Lead.organization_id == lead_scope.active_workspace_org_id(current_user, db))
    if not is_manager_tl:
        q_tl = q_tl.filter(Lead.assigned_to_id == current_user.id)
    lead = q_tl.first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    # Limit to 200 most recent events per channel — enough for any real conversation.
    # The new indexes on (lead_id, sent_at DESC) / (lead_id, received_at DESC) make these fast.
    from sqlalchemy import desc as _desc
    messages = (db.query(Message)
                .filter(Message.lead_id == lead_id)
                .order_by(_desc(Message.sent_at))
                .limit(200).all())
    replies = (db.query(Reply)
               .filter(Reply.lead_id == lead_id)
               .order_by(_desc(Reply.received_at))
               .limit(200).all())
    email_messages = (db.query(EmailMessage)
                      .filter(EmailMessage.lead_id == lead_id)
                      .order_by(_desc(EmailMessage.sent_at))
                      .limit(200).all())

    events = []
    from app.services.message_state import describe as _describe_delivery
    for m in messages:
        # `delivery` carries the explicit outcome. The transcript used to show
        # only the body and a timestamp, so an undelivered message was visually
        # identical to a delivered one — the operator had no way to know the
        # family never got it. See app/services/message_state.py.
        events.append({
            "type": "outbound",
            "channel": "sms",
            "body": m.body,
            "timestamp": m.sent_at,
            "status": m.twilio_status,
            "delivery": _describe_delivery(m),
            "delivery_status_at": (m.delivery_status_at.isoformat()
                                   if getattr(m, "delivery_status_at", None) else None),
        })
    for r in replies:
        events.append({
            "type": "inbound",
            "channel": "sms",
            "body": r.body,
            "timestamp": r.received_at,
            "is_hot": r.is_hot,
        })
    for e in email_messages:
        import re as _re
        raw_html = e.body_html or ""
        plain_body = _re.sub(r'<[^>]+>', ' ', raw_html)
        plain_body = _re.sub(r'\s+', ' ', plain_body).strip()
        if len(plain_body) > 600:
            plain_body = plain_body[:600] + "\u2026"
        events.append({
            "type": "outbound",
            "channel": "email",
            "subject": e.subject,
            "body": plain_body,
            "body_preview": plain_body[:120] if plain_body else "",
            "timestamp": e.sent_at,
            "status": e.status,
        })

    # Add cadence milestones
    cadence = db.query(CadenceState).filter(CadenceState.lead_id == lead_id).first()
    if cadence and cadence.cadence_started_at:
        events.append({
            "type": "system",
            "channel": "cadence",
            "body": f"Cadence started — {cadence.current_touch_number} of 9 touches sent",
            "timestamp": cadence.cadence_started_at,
            "status": cadence.status,
        })

    events.sort(key=lambda e: (e["timestamp"] is None, e["timestamp"] or ""))

    ai_note = None
    if lead.ai_lead_quality_note:
        try:
            ai_note = _json.loads(lead.ai_lead_quality_note)
        except Exception:
            ai_note = {"raw": lead.ai_lead_quality_note}

    latest_booking = (
        db.query(BookingLink)
        .filter(BookingLink.lead_id == lead_id)
        .order_by(BookingLink.created_at.desc())
        .first()
    )
    booking_info = None
    if latest_booking:
        booking_info = {
            "id": latest_booking.id,
            "status": latest_booking.status,
            "booked_time": latest_booking.booked_time,
            "calendar_event_id": latest_booking.calendar_event_id,
            "created_at": latest_booking.created_at,
            "expires_at": latest_booking.expires_at,
        }

    # Voice call records — shown on the Calls tab
    voice_calls_raw = (
        db.query(_VoiceCall)
        .filter(_VoiceCall.lead_id == lead_id)
        .order_by(_VoiceCall.created_at.desc())
        .limit(50)
        .all()
    )
    voice_call_list = []
    for vc in voice_calls_raw:
        voice_call_list.append({
            "id": vc.id,
            "outcome": vc.outcome,
            "status": vc.status,
            "duration_seconds": vc.duration_seconds,
            "transcript": vc.transcript,
            "voicemail_transcript": vc.voicemail_transcript,
            "voicemail_left": vc.voicemail_left,
            "call_number": vc.call_number,
            "recording_url": vc.recording_url,
            "started_at": vc.started_at.isoformat() if vc.started_at else None,
            "created_at": vc.created_at.isoformat() if vc.created_at else None,
        })

    return {
        "lead": lead,
        "events": events,
        "ai_quality": ai_note,
        "booking": booking_info,
        "voice_calls": voice_call_list,
    }


# ---------------------------------------------------------------------------
# Message review/confirm flow - the "AI drafts, I confirm, then it sends"
# workflow Mike specifically asked for. Reuses the EXACT SAME template
# resolution logic the real cadence engine uses (render_cadence_message),
# so what's shown in this preview is genuinely what would be sent, not an
# approximation that could drift out of sync with the real send path.
# ---------------------------------------------------------------------------

class MessagePreviewRequest(BaseModel):
    lead_ids: list[str]


class MessagePreviewItem(BaseModel):
    lead_id: str
    lead_name: str
    phone: str | None
    tier: str | None
    message_track: str | None
    draft_message: str
    skip_reason: str | None = None  # set if this lead can't actually be sent to (DNC, no phone, etc.)


@router.post("/preview-messages", response_model=list[MessagePreviewItem])
def preview_messages_for_leads(
    req: MessagePreviewRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """
    Given a batch of lead IDs (e.g. everything just created by an
    import), returns the actual AI/template-drafted first message for
    each one - WITHOUT sending anything. This is the review step: the
    advisor sees exactly what would go out, per lead, and can edit or
    skip individual ones before calling /leads/confirm-send-batch below.
    """
    from app.services.cadence_service import render_cadence_message
    from app.services.sms_service import BOOKING_BASE_URL

    leads = authorized_lead_query(db, current_user).filter(Lead.id.in_(req.lead_ids)).all()
    found_by_id = {l.id: l for l in leads}

    results = []
    for lead_id in req.lead_ids:
        lead = found_by_id.get(lead_id)
        if not lead:
            continue  # silently skip IDs that don't belong to this org - same pattern as reassign_leads

        lead_name = f"{lead.first_name or ''} {lead.last_name or ''}".strip() or "(no name)"
        skip_reason = None
        draft = ""

        if lead.status == "dnc":
            skip_reason = "DNC - excluded from outreach"
        elif lead.is_duplicate:
            skip_reason = "Duplicate - already owned by another lead record"
        elif lead.contact_channel == "email_only":
            skip_reason = "Email-only lead - not part of the SMS preview"
        elif not lead.phone:
            skip_reason = "No phone number on file"
        elif _is_suppressed(db, lead):
            # REAL GAP CLOSED HERE: this preview previously only checked
            # Lead.status/is_duplicate, never the actual suppression
            # list - confirmed by testing that a manually suppressed
            # number still came back with skip_reason=None and a full
            # draft message ready to send.
            skip_reason = "Phone number is on the suppression list"
        else:
            # Booking link URL isn't actually created yet at preview time
            # (that only happens on real send, to avoid generating dead
            # links for messages that get edited or skipped) - use a
            # placeholder so the draft still reads naturally.
            from app.services.public_identity import booking_url as public_booking_url
            placeholder_booking_url = public_booking_url(
                db, current_user.organization_id, "preview")
            draft = render_cadence_message(db, lead, current_user, touch_number=1, booking_url=placeholder_booking_url)

        results.append(MessagePreviewItem(
            lead_id=lead.id, lead_name=lead_name, phone=lead.phone,
            tier=lead.tier if lead.tier else None,
            message_track=lead.message_track if lead.message_track else None,
            draft_message=draft, skip_reason=skip_reason,
        ))

    return results


class ConfirmSendItem(BaseModel):
    lead_id: str
    message: str  # the (possibly edited) final message text for this lead


class ConfirmSendBatchRequest(BaseModel):
    items: list[ConfirmSendItem]
    include_booking_link: bool = True


@router.post("/confirm-send-batch")
def confirm_send_batch(
    req: ConfirmSendBatchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """
    The actual send step, AFTER the advisor has reviewed (and possibly
    edited) the drafted messages from /preview-messages. Each item
    carries its own final message text, since the advisor may have
    edited individual ones rather than accepting every AI draft as-is.
    """
    from app.services.sms_service import send_sms

    sent_ids = []
    skipped = []
    for item in req.items:
        lead = authorized_lead_query(db, current_user).filter(Lead.id == item.lead_id).first()
        if not lead:
            skipped.append({"lead_id": item.lead_id, "reason": "not_found"})
            continue
        try:
            msg = send_sms(db, current_user, lead, item.message, include_booking_link=req.include_booking_link)
            sent_ids.append(msg.id)
            # Start the cadence now that touch 1 has actually gone out -
            # this is what the import flow was missing: leads sat at
            # status=NEW with no cadence ever started unless something
            # else explicitly called start_cadence.
            from app.services.cadence_service import start_cadence
            start_cadence(db, lead)
        except Exception as e:
            skipped.append({"lead_id": item.lead_id, "reason": str(e)})

    return {"sent_count": len(sent_ids), "skipped_count": len(skipped), "sent_ids": sent_ids, "skipped": skipped}


@router.post("/{lead_id}/not-duplicate")
def keep_lead_separate(
    lead_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_context),
):
    """KEEP SEPARATE. This lead is its own person - resolve the flag, keep both.

    Until this existed the only endpoint that touched `is_duplicate` DELETED
    the row. A lead wrongly flagged - and the flag is applied inconsistently
    enough that "wrongly" is common - could be removed from the Duplicates tab
    only by destroying it. That is not a choice anyone should have to make
    about a real family's record.

    Nothing is deleted and nothing is merged. The flag is resolved, the
    resolution is stamped with who and when, and the row returns to normal
    outreach. `duplicate_of_lead_id` is deliberately KEPT: it is the evidence
    of what was matched, and a resolved pair should stay explainable.

    A resolved lead is not re-flagged. `duplicate_resolved_at` is what the
    importer and the maintenance passes check, so re-running an import over
    the same identifying data will not undo a human's decision. Materially
    changing the identifying data is a different lead and gets re-evaluated.
    """
    lead = authorized_lead_query(db, current_user).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    was = {
        "is_duplicate": bool(lead.is_duplicate),
        "status": lead.status,
        "duplicate_of_lead_id": getattr(lead, "duplicate_of_lead_id", None),
        "duplicate_reason": getattr(lead, "duplicate_reason", None),
    }

    lead.is_duplicate = False
    lead.duplicate_resolved_at = datetime.utcnow()
    lead.duplicate_resolved_by = current_user.id

    # A lead pushed to DNC by the duplicate-import bug comes back. A lead that
    # is DNC for a REAL reason - a STOP, a suppression, an admin decision -
    # stays exactly where it is: resolving a data-quality flag must never
    # silently re-open contact with someone who asked not to be contacted.
    restored_status = None
    if lead.status == "dnc" and was["is_duplicate"] and not _has_real_dnc_reason(db, lead):
        # An unclassified lead goes back to the review queue, NOT to "new".
        # `tier == "partial"` is truthy, so a naive check would have released
        # it straight into outreach unreviewed.
        lead.status = "new" if _has_real_tier(lead) else "needs_tier_review"
        restored_status = lead.status

    db.commit()

    log_action(
        db,
        organization_id=current_user.organization_id,
        actor_user_id=current_user.id,
        action="lead.duplicate_resolved_keep_separate",
        target_type="lead",
        target_id=lead.id,
        details={"before": was, "restored_status": restored_status,
                 "name": ((lead.first_name or "") + " " + (lead.last_name or "")).strip(),
                 "phone": lead.phone, "email": lead.email},
    )

    return {
        "lead_id": lead.id,
        "is_duplicate": False,
        "status": lead.status,
        "restored_status": restored_status,
        "resolved_at": lead.duplicate_resolved_at.isoformat(),
        "message": "Kept as a separate record. Nothing was deleted.",
    }


def _has_real_tier(lead: Lead) -> bool:
    """Has a human actually classified this lead?

    `partial` is the IMPORTER'S PLACEHOLDER, not a tier. It means the upload
    could not work out what kind of lead this is, so a person must. It is a
    non-empty string, which is the trap: a truthiness check reads it as "tier
    present" and would release thousands of unclassified leads straight into
    outreach - the exact opposite of what `needs_tier_review` is for.
    """
    tier = (str(lead.tier).strip().lower() if lead.tier else "")
    return bool(tier) and tier not in ("partial", "none", "unknown")


def _has_real_dnc_reason(db: Session, lead: Lead) -> bool:
    """Is this lead DNC for a reason OTHER than the duplicate-import bug?

    Checked before any repair restores a status. A STOP reply, a suppression
    entry an admin added, or a manual flag are all real and must survive.
    Fails CLOSED: anything unexpected counts as a real reason, because leaving
    a lead suppressed is recoverable and texting someone who opted out is not.
    """
    try:
        if getattr(lead, "manual_flag", None):
            return True
        # A DNC classification on any inbound reply is a legal opt-out.
        from app.models.models import Reply, ReplyClassification
        stopped = (db.query(Reply)
                   .filter(Reply.lead_id == lead.id,
                           Reply.classification == ReplyClassification.DNC)
                   .first())
        if stopped is not None:
            return True
        # The org-wide suppression list is the other source of truth.
        if _is_suppressed(db, lead):
            return True
    except Exception:
        return True
    return False


@router.get("/{lead_id}/duplicate-explain")
def explain_duplicate(
    lead_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_context),
):
    """WHY is this lead flagged, and what did it match?

    Rows flagged before traceability existed carry no reason and, on the
    in-file path, no parent either - they say "duplicate" and nothing more.
    This reconstructs the answer live from the same two sources the dedup
    engine consults, so an old flag can still be explained rather than being
    an unaccountable mark on somebody's record.
    """
    from app.services.dedup_service import (normalize_phone, normalize_last_name,
                                            PLACEHOLDER_LAST_NAME)
    from app.models.models import ContactRegistry

    lead = authorized_lead_query(db, current_user).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    norm_phone = normalize_phone(lead.phone) if lead.phone else None
    norm_last = normalize_last_name(lead.last_name) if lead.last_name else None

    stored = {
        "is_duplicate": bool(lead.is_duplicate),
        "reason": getattr(lead, "duplicate_reason", None),
        "match_field": getattr(lead, "duplicate_match_field", None),
        "match_value": getattr(lead, "duplicate_match_value", None),
        "duplicate_of_lead_id": getattr(lead, "duplicate_of_lead_id", None),
        "resolved_at": (lead.duplicate_resolved_at.isoformat()
                        if getattr(lead, "duplicate_resolved_at", None) else None),
    }

    # What the registry holds for this phone, and which rule it would fire.
    registry = []
    if norm_phone:
        for e in (db.query(ContactRegistry)
                  .filter(ContactRegistry.organization_id == lead.organization_id,
                          ContactRegistry.normalized_phone == norm_phone).all()):
            is_placeholder = e.normalized_last_name == PLACEHOLDER_LAST_NAME
            registry.append({
                "registry_last_name": e.normalized_last_name,
                "is_placeholder_from_historical_sent_log": is_placeholder,
                "matches_this_lead": (e.normalized_last_name == norm_last) or is_placeholder,
                "first_seen_lead_id": e.first_seen_lead_id,
                "owning_user_id": e.owning_user_id,
            })

    # Other LEADS sharing the identifying data, so the UI can name the sibling.
    siblings = []
    if norm_phone:
        for other in (db.query(Lead)
                      .filter(Lead.organization_id == lead.organization_id,
                              Lead.id != lead.id).all()):
            if other.phone and normalize_phone(other.phone) == norm_phone:
                siblings.append({
                    "id": other.id,
                    "name": ((other.first_name or "") + " " + (other.last_name or "")).strip(),
                    "email": other.email, "status": other.status,
                    "is_duplicate": bool(other.is_duplicate),
                    "same_last_name": normalize_last_name(other.last_name or "") == norm_last,
                    "created_at": str(other.created_at or ""),
                })

    parent = None
    if stored["duplicate_of_lead_id"]:
        p = db.query(Lead).filter(Lead.id == stored["duplicate_of_lead_id"]).first()
        if p:
            parent = {"id": p.id,
                      "name": ((p.first_name or "") + " " + (p.last_name or "")).strip(),
                      "phone": p.phone, "email": p.email}

    return {
        "lead": {"id": lead.id,
                 "name": ((lead.first_name or "") + " " + (lead.last_name or "")).strip(),
                 "phone": lead.phone, "email": lead.email, "status": lead.status,
                 "normalized_phone": norm_phone, "normalized_last_name": norm_last},
        "stored_flag": stored,
        "registry_entries_for_this_phone": registry,
        "other_leads_sharing_this_phone": siblings,
        "parent_lead": parent,
    }


@router.post("/maintenance/duplicate-dnc-repair")
def repair_duplicate_dnc(
    apply: bool = Query(False, description="False (default) reports only."),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_context),
):
    """Undo the duplicate->DNC coupling on rows the importer already wrote.

    DRY RUN BY DEFAULT. `apply=false` counts and returns a sample, changes
    nothing. Nothing here deletes, merges, or clears a duplicate flag - it only
    lifts a `status = "dnc"` that the import bug applied for a bookkeeping
    reason, and only where no REAL suppression exists.

    A row is repaired only when ALL of these hold:
      - status == "dnc"
      - is_duplicate is true            (the bug's signature)
      - `_has_real_dnc_reason` is false (no STOP, no suppression, no manual flag)

    Everything else is left alone. The lead stays flagged as a duplicate - that
    is a separate, honest fact, and resolving it is a human decision made
    through /not-duplicate.
    """
    if current_user.role not in ("org_admin", "super_admin", "god_admin"):
        raise HTTPException(status_code=403, detail="Admin role required.")

    candidates = db.query(Lead).filter(
        Lead.organization_id == lead_scope.active_workspace_org_id(current_user, db),
        Lead.status == "dnc",
        Lead.is_duplicate == True,
    ).all()

    repairable, protected = [], []
    for lead in candidates:
        (protected if _has_real_dnc_reason(db, lead) else repairable).append(lead)

    if apply:
        for lead in repairable:
            # Unclassified leads land in the review queue, not in outreach.
            lead.status = "new" if _has_real_tier(lead) else "needs_tier_review"
        db.commit()
        log_action(
            db,
            organization_id=current_user.organization_id,
            actor_user_id=current_user.id,
            action="lead.duplicate_dnc_repaired",
            target_type="organization",
            target_id=current_user.organization_id,
            details={"repaired": len(repairable), "protected": len(protected)},
        )

    def _brief(l):
        return {"id": l.id,
                "name": ((l.first_name or "") + " " + (l.last_name or "")).strip(),
                "phone": l.phone, "reason": getattr(l, "duplicate_reason", None)}

    # What this repair would actually add to SMS READY. A repaired lead only
    # becomes sendable if it has a REAL tier and a phone; the rest return to
    # the review queue, which is where they belong.
    to_outreach = [l for l in repairable if _has_real_tier(l) and l.phone]
    to_review = [l for l in repairable if l not in to_outreach]

    return {
        "dry_run": not apply,
        "dnc_and_duplicate": len(candidates),
        "repairable": len(repairable),
        "protected_real_dnc": len(protected),
        "would_become_sendable": len(to_outreach),
        "would_return_to_tier_review": len(to_review),
        "sample_repairable": [_brief(l) for l in repairable[:10]],
        "sample_protected": [_brief(l) for l in protected[:10]],
    }


@router.post("/maintenance/tier-status-repair")
def repair_stale_tier_review(
    apply: bool = Query(False, description="False (default) reports only."),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_context),
):
    """Release leads stuck in `needs_tier_review` that already HAVE a tier.

    DRY RUN BY DEFAULT.

    `needs_tier_review` means "somebody must classify this lead before we
    contact them". Once a tier is set that is answered, but nothing moved the
    status on, so the leads stayed parked - which is why SMS READY reads 0
    against ten thousand leads with phone numbers.

    Only rows whose question has actually been answered are released:
      - status == "needs_tier_review"
      - a tier is present and is not the "partial" placeholder
      - not flagged duplicate, not suppressed, has a phone

    A lead with no tier still needs a human. It is reported, not touched.
    """
    if current_user.role not in ("org_admin", "super_admin", "god_admin"):
        raise HTTPException(status_code=403, detail="Admin role required.")

    parked = db.query(Lead).filter(
        Lead.organization_id == lead_scope.active_workspace_org_id(current_user, db),
        Lead.status == "needs_tier_review",
    ).all()

    releasable, no_tier, blocked = [], [], []
    for lead in parked:
        if not _has_real_tier(lead):
            no_tier.append(lead)
        elif lead.is_duplicate or not lead.phone or _has_real_dnc_reason(db, lead):
            blocked.append(lead)
        else:
            releasable.append(lead)

    if apply:
        for lead in releasable:
            lead.status = "new"
        db.commit()
        log_action(
            db,
            organization_id=current_user.organization_id,
            actor_user_id=current_user.id,
            action="lead.tier_status_repaired",
            target_type="organization",
            target_id=current_user.organization_id,
            details={"released": len(releasable), "still_need_a_tier": len(no_tier),
                     "blocked_for_another_reason": len(blocked)},
        )

    return {
        "dry_run": not apply,
        "total_needs_tier_review": len(parked),
        "releasable_have_a_valid_tier": len(releasable),
        "still_need_a_tier": len(no_tier),
        "blocked_for_another_reason": len(blocked),
        "blocked_breakdown": {
            "duplicate": sum(1 for l in blocked if l.is_duplicate),
            "no_phone": sum(1 for l in blocked if not l.phone),
        },
    }


@router.delete("/duplicates/bulk-delete")
def bulk_delete_duplicate_leads(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_context),
):
    """
    Permanently deletes all leads flagged as duplicates (is_duplicate=True)
    for this organization. These leads were already blocked from all
    outreach by the dedup engine - this just removes them from the
    database entirely for a clean list.

    Requires org_admin or super_admin role - advisors cannot bulk delete.
    """
    if current_user.role not in ("org_admin", "super_admin", "god_admin"):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Admin role required to bulk delete leads.")

    duplicates = db.query(Lead).filter(
        Lead.organization_id == lead_scope.active_workspace_org_id(current_user, db),
        Lead.is_duplicate == True,
    ).all()

    count = len(duplicates)
    for lead in duplicates:
        db.delete(lead)

    db.commit()

    log_action(
        db,
        organization_id=current_user.organization_id,
        actor_user_id=current_user.id,
        action="lead.bulk_delete_duplicates",
        target_type="organization",
        target_id=current_user.organization_id,
        details={"deleted_count": count},
    )

    return {"deleted": count, "message": f"Permanently deleted {count} duplicate leads."}


# ── Deduplicate email-only leads that slipped past the original dedup ──────
@router.post("/deduplicate-email-leads")
def deduplicate_email_leads(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """
    One-time (and safe to re-run) cleanup: finds email-only leads in this
    org where the same (email + last_name) pair appears more than once, keeps
    the oldest record, and marks all later duplicates as is_duplicate=True
    with status=dnc — exactly what the importer now does on new uploads.

    Does NOT delete anything — just flags. After reviewing, the advisor can
    call DELETE /leads/duplicates/bulk-delete to permanently remove them.
    Requires org_admin or super_admin.
    """
    if current_user.role not in ("org_admin", "super_admin", "god_admin"):
        raise HTTPException(status_code=403, detail="Admin role required.")

    from sqlalchemy import func as sqlfunc

    # Pull all email-only leads for this org that have a real email and last name
    email_leads = (
        db.query(Lead)
        .filter(
            Lead.organization_id == lead_scope.active_workspace_org_id(current_user, db),
            Lead.contact_channel == "email_only",
            Lead.email.isnot(None),
            Lead.last_name.isnot(None),
        )
        .order_by(Lead.created_at.asc())  # oldest first → we keep the first one
        .all()
    )

    # Group by (normalized_email, normalized_last_name)
    seen: dict = {}  # key -> first (oldest) lead id
    flagged_ids = []

    for lead in email_leads:
        norm_email = (lead.email or "").strip().lower()
        norm_last  = "".join(c for c in (lead.last_name or "").lower() if c.isalpha())
        key = (norm_email, norm_last)

        if not norm_email or not norm_last:
            continue

        if key in seen:
            # This is a duplicate of the first record we saw for this key
            if not lead.is_duplicate:
                lead.is_duplicate = True
                lead.duplicate_of_lead_id = seen[key]
                lead.duplicate_reason = "existing_email"
                lead.duplicate_match_field = "email+last_name"
                lead.duplicate_match_value = norm_email
                # NOT dnc. Same rule as the importer: a duplicate is a
                # data-quality flag. A cleanup sweep has no business moving
                # anybody into the do-not-contact population.
                flagged_ids.append(lead.id)
        else:
            seen[key] = lead.id

    db.commit()

    return {
        "scanned": len(email_leads),
        "newly_flagged": len(flagged_ids),
        "message": (
            f"Flagged {len(flagged_ids)} email-only duplicate leads. "
            "Call DELETE /leads/duplicates/bulk-delete to permanently remove them."
            if flagged_ids else
            "No new email-only duplicates found — list is already clean."
        ),
    }


# ── PUBLIC: Landing page demo request (no auth required) ──────────────────
class DemoRequestCreate(BaseModel):
    first_name: str
    last_name: str
    phone: str
    email: Optional[str] = None
    notes: Optional[str] = None
    source: str = "landing_page"
    tier: Optional[str] = "demo_request"


@router.post("/demo-request", status_code=201)
def create_demo_request(
    payload: DemoRequestCreate,
    db: Session = Depends(get_db),
):
    """Public endpoint — no auth required. Called by bookaboost.com landing page."""
    from app.models.models import Organization
    import uuid
    from datetime import datetime

    bookaboost_org = db.query(Organization).filter(
        Organization.name.ilike('%bookaboost%')
    ).first()
    if not bookaboost_org:
        bookaboost_org = db.query(Organization).first()
    if not bookaboost_org:
        return {"status": "received", "message": "Demo request received."}

    existing = None
    if payload.phone:
        existing = db.query(Lead).filter(
            Lead.organization_id == bookaboost_org.id,
            Lead.phone == payload.phone,
        ).first()
    if not existing and payload.email:
        existing = db.query(Lead).filter(
            Lead.organization_id == bookaboost_org.id,
            Lead.email == payload.email,
        ).first()

    if existing:
        existing.notes = f"{existing.notes or ''}\n[New demo request {datetime.utcnow().strftime('%Y-%m-%d')}] {payload.notes or ''}".strip()
        db.commit()
        return {"status": "updated", "message": "Demo request received."}

    lead = Lead(
        id=str(uuid.uuid4()),
        organization_id=bookaboost_org.id,
        first_name=payload.first_name.strip(),
        last_name=payload.last_name.strip(),
        phone=payload.phone.strip() if payload.phone else None,
        email=payload.email.strip() if payload.email else None,
        notes=payload.notes,
        source_file=payload.source,
        tier=payload.tier,
        status='new',
        created_at=datetime.utcnow(),
    )
    db.add(lead)
    db.commit()
    return {"status": "created", "message": "Demo request received.", "id": str(lead.id)}


class ManualLeadCreate(BaseModel):
    first_name: str
    last_name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    tier: Optional[str] = "pre_need"
    source_year: Optional[int] = None
    notes: Optional[str] = None


@router.post("/create", status_code=201)
def create_lead_manually(
    payload: ManualLeadCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_context),
):
    """
    Create a single lead manually from the Leads page UI.
    Runs through dedup check against existing org leads.
    """
    import uuid
    from app.services.dedup_service import normalize_phone, normalize_last_name

    phone_normalized = normalize_phone(payload.phone or "")
    last_name_normalized = normalize_last_name(payload.last_name or "")  # was: return value discarded

    # DEDUP ON PHONE **AND LAST NAME**, not on phone alone.
    #
    # This matched on phone only. dedup_service is explicit that phone-only
    # matching is wrong - "a phone number can represent two different real
    # people in the same household (e.g. father and son sharing a landline)" -
    # and the registry it owns keys on phone + last name for exactly that
    # reason. This endpoint quietly did the opposite.
    #
    # The result: every manually added lead on a number the org had ever used
    # was flagged a duplicate of an unrelated person. Ashton Jamon was flagged
    # against Jennifer Breeder purely because they share a phone number, and
    # any new test lead on a previously-texted number was unusable on creation.
    #
    # A lead a human has already resolved with "keep separate" is not re-matched.
    is_dup = False
    dup_of = None
    if phone_normalized and last_name_normalized:
        for existing in db.query(Lead).filter(
            Lead.organization_id == lead_scope.active_workspace_org_id(current_user, db),
            Lead.phone == phone_normalized,
            Lead.is_duplicate == False,
            Lead.duplicate_resolved_at.is_(None),
        ).all():
            if normalize_last_name(existing.last_name or "") == last_name_normalized:
                is_dup = True
                dup_of = existing.id
                break

    lead = Lead(
        id=str(uuid.uuid4()),
        organization_id=current_user.organization_id,
        assigned_to_id=current_user.id,
        first_name=payload.first_name.strip(),
        last_name=payload.last_name.strip(),
        phone=phone_normalized or payload.phone,
        phone_raw=payload.phone,
        email=payload.email,
        tier=payload.tier,
        status="new",
        contact_channel="sms" if payload.phone else "email_only",
        source_year=payload.source_year,
        source_file="manual",
        is_duplicate=is_dup,
        duplicate_of_lead_id=dup_of,
        duplicate_reason="manual_add_phone_last_name" if is_dup else None,
        duplicate_match_field="phone+last_name" if is_dup else None,
        duplicate_match_value=phone_normalized if is_dup else None,
        notes=payload.notes,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)

    log_action(db, current_user.organization_id, current_user.id, action="lead.create_manual", target_type="lead", target_id=lead.id)

    # Auto-create a CRM contact so this lead shows up in the CRM immediately.
    # Silently skip if one already exists (shouldn't happen for a brand-new lead, but defensive).
    try:
        already_in_crm = db.query(CRMContact).filter(
            CRMContact.lead_id == lead.id,
            CRMContact.organization_id == current_user.organization_id,
            CRMContact.is_archived == False,
        ).first()
        if not already_in_crm:
            crm_contact = CRMContact(
                organization_id=current_user.organization_id,
                first_name=lead.first_name,
                last_name=lead.last_name,
                phone=lead.phone,
                email=lead.email,
                stage="inquiry",
                lead_id=lead.id,
                assigned_to_id=lead.assigned_to_id or current_user.id,
            )
            db.add(crm_contact)
            db.commit()
    except Exception:
        pass  # CRM creation is best-effort; lead was already committed

    return {
        "id": lead.id,
        "name": f"{lead.first_name} {lead.last_name}",
        "is_duplicate": is_dup,
        "status": "created",
    }


# ── Edit basic lead fields ────────────────────────────────────────────────────

class LeadFieldUpdate(BaseModel):
    # EXTRAS ARE ACCEPTED SO THEY CAN BE REFUSED.
    #
    # By default pydantic DISCARDS a field the model does not declare, which
    # meant `{"assigned_to_id": "<other advisor>"}` was silently dropped: the
    # lead did not move, but nothing was reported either. Silent success is the
    # wrong answer to an attempt to reassign somebody else's lead - it looks
    # identical to a normal edit in the logs, and it leaves the caller believing
    # the field is simply not implemented yet rather than forbidden.
    #
    # Allowing extras puts them in `model_fields_set`, which is exactly what
    # `reject_ownership_fields` inspects. Undeclared fields that are NOT
    # ownership fields keep their old behaviour: ignored.
    model_config = {"extra": "allow"}

    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    notes: Optional[str] = None
    tier: Optional[str] = None
    street_address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    relationship_type: Optional[str] = None  # AI familiarity guardrail


@router.patch("/{lead_id}")
def update_lead_fields(
    lead_id: str,
    payload: LeadFieldUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """Edit basic contact fields on a lead. Advisors can edit their own; admins can edit any."""
    # OWNERSHIP IS NOT AN EDITABLE FIELD. Checked BEFORE the lead is loaded, so
    # an advisor probing another advisor's id with a reassignment payload gets
    # the same answer whether or not that lead exists.
    reject_ownership_fields(current_user, payload, request)

    lead = authorized_lead_query(db, current_user).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    # ALLOWLIST, not a denylist. This was `role == "advisor"`, which meant any
    # role outside the ladder (e.g. the grantable-but-unguarded "viewer", or any
    # future role) silently skipped the ownership check and could edit every
    # lead in the org. Name the roles allowed to bypass; everyone else is owner-only.
    if current_user.role not in ("org_admin", "super_admin", "god_admin") \
            and lead.assigned_to_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only edit your own leads")

    if payload.first_name is not None:
        lead.first_name = payload.first_name.strip() or lead.first_name
    if payload.last_name is not None:
        lead.last_name = payload.last_name.strip() or lead.last_name
    if payload.phone is not None:
        normalized = normalize_phone(payload.phone.strip())
        lead.phone = normalized or payload.phone.strip() or None
        try:
            lead.phone_raw = payload.phone.strip() or None
        except Exception:
            pass
        try:
            lead.contact_channel = "sms" if lead.phone else ("email_only" if lead.email else "unknown")
        except Exception:
            pass
    if payload.email is not None:
        lead.email = payload.email.strip() or None
    if payload.notes is not None:
        lead.notes = payload.notes
    if payload.tier is not None:
        lead.tier = payload.tier
    if payload.street_address is not None:
        lead.street_address = payload.street_address.strip() or None
    if payload.city is not None:
        lead.city = payload.city.strip() or None
    if payload.state is not None:
        lead.state = payload.state.strip() or None
    if payload.zip_code is not None:
        lead.zip_code = payload.zip_code.strip() or None
    if payload.relationship_type is not None:
        valid_rel_types = {"cold_lead", "warm_lead", "re_engagement", "previous_prospect", "past_customer", "existing_customer"}
        if payload.relationship_type in valid_rel_types:
            lead.relationship_type = payload.relationship_type

    try:
        lead.updated_at = datetime.utcnow()
    except Exception:
        pass

    db.commit()
    db.refresh(lead)

    try:
        log_action(db, current_user.organization_id, current_user.id,
                   action="lead.update", target_type="lead", target_id=lead_id)
    except Exception:
        pass

    return {
        "id": lead.id,
        "first_name": lead.first_name,
        "last_name": lead.last_name,
        "phone": lead.phone,
        "email": lead.email,
        "notes": getattr(lead, "notes", None),
        "tier": lead.tier,
        "street_address": getattr(lead, "street_address", None),
        "city": getattr(lead, "city", None),
        "state": getattr(lead, "state", None),
        "zip_code": getattr(lead, "zip_code", None),
        "relationship_type": getattr(lead, "relationship_type", "cold_lead"),
    }


# ── Delete a single lead ──────────────────────────────────────────────────────

@router.delete("/{lead_id}")
def delete_lead(
    lead_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """Permanently delete a single lead. Advisors can delete their own leads; admins can delete any."""
    lead = authorized_lead_query(db, current_user).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    # ALLOWLIST, not a denylist — same reasoning as the edit guard above.
    if current_user.role not in ("org_admin", "super_admin", "god_admin") \
            and lead.assigned_to_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only delete your own leads")

    log_action(db, current_user.organization_id, current_user.id, action="lead.delete", target_type="lead", target_id=lead_id)
    db.delete(lead)
    db.commit()
    return {"deleted": True, "id": lead_id}


# ── Update lead type / AI direction ──────────────────────────────────────────

class LeadTypeUpdate(BaseModel):
    lead_type: Optional[str] = None   # file_check, code_lead, new_inquiry, referral, web_lead, etc.
    ai_direction: Optional[str] = None  # free-text instruction for AI messaging this lead

@router.patch("/{lead_id}/lead-type")
def update_lead_type(
    lead_id: str,
    payload: LeadTypeUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """Set the lead type and/or AI direction override for a lead."""
    lead = authorized_lead_query(db, current_user).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    if payload.lead_type is not None:
        lead.message_track = payload.lead_type
    if payload.ai_direction is not None:
        lead.notes = (lead.notes or "") + f"\n[AI Direction]: {payload.ai_direction}"
    lead.updated_at = datetime.utcnow()
    db.commit()
    log_action(db, current_user.organization_id, current_user.id, action="lead.update_type", target_type="lead", target_id=lead_id)
    return {"updated": True}


class FlagLeadRequest(BaseModel):
    flag_type: Optional[str] = None   # "bad_email" | "remove_all" | null (unflag)
    reason: Optional[str] = None      # optional note from advisor


@router.patch("/{lead_id}/flag")
def flag_lead(
    lead_id: str,
    payload: FlagLeadRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """
    Any advisor in any org can manually flag a lead the auto-detection missed.
    flag_type = "bad_email"   → hide from email queue + email campaigns, SMS still ok
    flag_type = "remove_all"  → hide from all outreach lists everywhere
    flag_type = None          → unflag, fully restore to all lists
    """
    # Advisors can only flag leads in their own org for security
    lead = authorized_lead_query(db, current_user).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    valid_flags = {None, "bad_email", "remove_all"}
    if payload.flag_type not in valid_flags:
        raise HTTPException(status_code=400, detail=f"flag_type must be one of: bad_email, remove_all, or null to unflag")

    lead.manual_flag = payload.flag_type
    lead.manual_flag_reason = payload.reason if payload.flag_type else None
    lead.updated_at = datetime.utcnow()
    db.commit()

    action = "lead.unflag" if not payload.flag_type else f"lead.flag.{payload.flag_type}"
    log_action(db, current_user.organization_id, current_user.id, action=action, target_type="lead", target_id=lead_id)
    return {"flagged": bool(payload.flag_type), "flag_type": payload.flag_type}


# ── PUBLIC: Demo request from bookaboost.live (no auth required) ─────────────
# Called by the "Request a Demo" form on the bookaboost.live marketing site.
# Stores a lead record in the default org and fires a notification email to
# every address in the LEAD_NOTIFY_EMAILS env var (comma-separated, set in Render).

class DemoRequestPayload(BaseModel):
    first_name: str
    last_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    company: Optional[str] = None
    industry: Optional[str] = None
    message: Optional[str] = None


_DEMO_CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


@router.options("/demo-request")
def demo_request_preflight():
    from fastapi.responses import JSONResponse
    return JSONResponse(content={}, headers=_DEMO_CORS)


@router.post("/demo-request", status_code=201)
def demo_request(payload: DemoRequestPayload, db: Session = Depends(get_db)):
    """
    Public endpoint — no auth required. CORS open to any origin.
    Accepts a demo request from bookaboost.live and fires notification
    emails to LEAD_NOTIFY_EMAILS (comma-separated env var).
    """
    import uuid as _uuid
    from fastapi.responses import JSONResponse
    from app.models.models import Organization

    # Store in first active org as a web_lead
    org = db.query(Organization).filter(Organization.is_active == True).first()
    if org:
        lead = Lead(
            id=str(_uuid.uuid4()),
            organization_id=org.id,
            first_name=(payload.first_name or "").strip(),
            last_name=(payload.last_name or "").strip() or None,
            email=payload.email,
            phone=payload.phone,
            status="new",
            tier="web_lead",
            source_file="demo_request",
            message_track="new_inquiry_intro",
            notes=(
                f"[Demo Request]\n"
                f"Company: {payload.company or 'n/a'}\n"
                f"Industry: {payload.industry or 'n/a'}\n"
                f"Message: {payload.message or 'n/a'}"
            ),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(lead)
        try:
            db.commit()
        except Exception:
            db.rollback()

    # Fire notification emails
    notify_raw = os.environ.get("LEAD_NOTIFY_EMAILS", "")
    notify_addrs = [e.strip() for e in notify_raw.split(",") if e.strip()]
    if notify_addrs:
        try:
            import resend
            api_key = os.environ.get("RESEND_API_KEY", "")
            from_addr = os.environ.get("EMAIL_FROM_ADDRESS", "noreply@bookaboost.com")
            if api_key:
                resend.api_key = api_key
                html_body = f"""
<div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;padding:24px;color:#222;">
  <h2 style="color:#1565c0;margin-bottom:16px;">New Demo Request — BookaBoost</h2>
  <table style="width:100%;border-collapse:collapse;font-size:14px;">
    <tr><td style="padding:6px 12px 6px 0;font-weight:700;color:#555;width:120px;">Name</td>
        <td>{payload.first_name} {payload.last_name or ''}</td></tr>
    <tr><td style="padding:6px 12px 6px 0;font-weight:700;color:#555;">Email</td>
        <td>{payload.email or '—'}</td></tr>
    <tr><td style="padding:6px 12px 6px 0;font-weight:700;color:#555;">Phone</td>
        <td>{payload.phone or '—'}</td></tr>
    <tr><td style="padding:6px 12px 6px 0;font-weight:700;color:#555;">Company</td>
        <td>{payload.company or '—'}</td></tr>
    <tr><td style="padding:6px 12px 6px 0;font-weight:700;color:#555;">Industry</td>
        <td>{payload.industry or '—'}</td></tr>
    <tr><td style="padding:6px 12px 6px 0;font-weight:700;color:#555;">Message</td>
        <td>{payload.message or '—'}</td></tr>
  </table>
  <p style="margin-top:20px;font-size:12px;color:#888;">
    Sent from bookaboost.live demo request form.
  </p>
</div>"""
                resend.Emails.send({
                    "from": from_addr,
                    "to": notify_addrs,
                    "subject": f"New Demo Request: {payload.first_name} {payload.last_name or ''} ({payload.company or payload.email or 'unknown'})",
                    "html": html_body,
                })
        except Exception as exc:
            import logging as _log
            _log.getLogger(__name__).error("demo_request notify email failed: %s", exc)

    from fastapi.responses import JSONResponse
    return JSONResponse(
        content={"success": True, "message": "Demo request received. We'll be in touch soon!"},
        headers=_DEMO_CORS,
    )


# ── PUBLIC: SMS opt-in form submission (no auth required) ─────────────────────
# Called by advisorflow-booking.vercel.app/optin when a lead submits the
# SMS consent form. Required for Twilio A2P 10DLC carrier verification.

class SmsOptinRequest(BaseModel):
    first_name: str
    last_name: Optional[str] = None
    phone: str
    consent: bool
    source: Optional[str] = "optin_page"
    optin_url: Optional[str] = None
    optin_timestamp: Optional[str] = None


@router.post("/sms-optin", status_code=201)
def sms_optin(
    payload: SmsOptinRequest,
    db: Session = Depends(get_db),
):
    """
    Public endpoint — no auth required.
    Records SMS consent from the /optin page on the Vercel booking app.
    Checks suppression list before creating lead record.
    Used as evidence of opt-in for Twilio A2P 10DLC campaign verification.
    """
    import uuid
    from app.models.models import Organization
    from app.services.dedup_service import normalize_phone

    if not payload.consent:
        raise HTTPException(status_code=400, detail="SMS consent is required.")

    phone_normalized = normalize_phone(payload.phone or "")
    if not phone_normalized:
        raise HTTPException(status_code=400, detail="A valid phone number is required.")

    # Route to the first active organization (Restland).
    # When multi-tenant billing is active, this can be org-scoped via a query param.
    org = db.query(Organization).filter(Organization.is_active == True).first()
    if not org:
        raise HTTPException(status_code=500, detail="No active organization found.")

    # Check suppression / DNC list before creating record
    try:
        from app.services.compliance_service import is_phone_suppressed
        if is_phone_suppressed(db, org.id, phone_normalized):
            raise HTTPException(
                status_code=409,
                detail="This phone number is on the do-not-contact list and cannot be added.",
            )
    except HTTPException:
        raise
    except Exception:
        pass  # If suppression check fails, proceed — don't block opt-in

    # Deduplicate: if lead already exists for this phone, update notes
    existing = db.query(Lead).filter(
        Lead.organization_id == org.id,
        Lead.phone == phone_normalized,
    ).first()

    if existing:
        note_entry = (
            f"\n[SMS Opt-In {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC] "
            f"Re-confirmed consent via {payload.source or 'optin_page'}. "
            f"URL: {payload.optin_url or 'n/a'}"
        )
        existing.notes = (existing.notes or "") + note_entry
        db.commit()
        return {"success": True, "lead_id": existing.id, "action": "updated"}

    lead = Lead(
        id=str(uuid.uuid4()),
        organization_id=org.id,
        first_name=payload.first_name.strip(),
        last_name=(payload.last_name or "").strip() or None,
        phone=phone_normalized,
        phone_raw=payload.phone,
        contact_channel="sms",
        status="new",
        source_file="optin_page",
        tier="web_lead",
        notes=(
            f"[SMS Opt-In {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC] "
            f"Consent given via {payload.source or 'optin_page'}. "
            f"URL: {payload.optin_url or 'n/a'}. "
            f"Timestamp: {payload.optin_timestamp or 'n/a'}"
        ),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)

    return {"success": True, "lead_id": lead.id, "action": "created"}


# ── Resend booking link ───────────────────────────────────────────────────────

@router.post("/{lead_id}/resend-booking-link")
def resend_booking_link(
    lead_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """
    Generate a fresh booking link for a lead and email it to them.

    Expires any existing pending booking links so there's always exactly
    one active link per lead. Works regardless of the current AI conversation
    state — advisors can resend manually at any time.
    """
    import uuid as _uuid
    from app.models.models import EmailMessage, Organization
    from app.services.sms_service import create_booking_link, BOOKING_BASE_URL
    from app.services.email_service import send_email_via_provider
    from app.services.ai_conversation_service import _build_email_html

    lead = authorized_lead_query(db, current_user).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    if lead.status == "dnc":
        raise HTTPException(status_code=400, detail="Lead is on the Do Not Contact list")
    if not lead.email:
        raise HTTPException(status_code=400, detail="Lead has no email address on file")

    # Expire any stale pending links so there's only one active at a time
    stale = db.query(BookingLink).filter(
        BookingLink.lead_id == lead.id,
        BookingLink.status == "pending",
    ).all()
    for s in stale:
        s.status = "expired"
    db.flush()

    # Create fresh link — owned by the lead's ADVISOR, not by whoever pressed
    # the button. A resend issued while the platform owner had the lead open
    # would otherwise send the family to the OWNER's calendar. Same helper as
    # the composer and the email sender, so the three cannot drift apart.
    from app.routers.compose_router import acting_advisor
    _advisor = acting_advisor(db, lead, current_user)
    link = create_booking_link(db, lead, _advisor)
    from app.services.public_identity import booking_url as public_booking_url
    booking_url = public_booking_url(db, lead.organization_id, link.token)

    # Build the email
    org = db.query(Organization).filter_by(id=current_user.organization_id).first()
    org_name = org.name if org else "our organization"
    advisor_name = _advisor.full_name or "Your Advisor"
    first_name = lead.first_name or "there"

    body_text = (
        f"Hi {first_name}, I wanted to make sure you have a convenient way to schedule "
        f"your appointment with us. Use the button below to pick a time that works for you — "
        f"it only takes a minute."
    )

    booking_btn = (
        f'<br><br>'
        f'<a href="{booking_url}" '
        f'style="display:inline-block;background:#1a5fa8;color:#ffffff;padding:12px 28px;'
        f'border-radius:6px;text-decoration:none;font-weight:700;font-size:15px;">'
        f'Schedule a Time &rarr;</a>'
    )

    html_body = _build_email_html(body_text, advisor_name, org_name, extra_html=booking_btn)
    subject = f"Your scheduling link, {first_name}"

    result = send_email_via_provider(lead.email, subject, html_body, org=org)
    if not result["success"]:
        raise HTTPException(
            status_code=500,
            detail=f"Email send failed: {result.get('error', 'unknown error')}",
        )

    # Log in email history
    msg = EmailMessage(
        id=str(_uuid.uuid4()),
        lead_id=lead.id,
        sender_id=current_user.id,
        subject=subject,
        body_html=html_body,
        status="sent",
        provider_message_id=result.get("provider_message_id"),
        sent_at=datetime.utcnow(),
    )
    db.add(msg)

    # Bump lead status to "sent" if still "new"
    if lead.status == "new":
        lead.status = "sent"
    lead.last_messaged_at = datetime.utcnow()

    db.commit()

    return {
        "success": True,
        "booking_url": booking_url,
        "email_sent_to": lead.email,
        "link_id": link.id,
    }
