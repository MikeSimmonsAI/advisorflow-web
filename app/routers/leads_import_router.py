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
from app.utils.time_fmt import iso_utc  # S19: explicit-UTC timestamps

router = APIRouter()
logger = logging.getLogger(__name__)


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
    """RETIRED (#470 Stage D). Kept so old clients get a clear answer instead of a 404."""
    raise HTTPException(status_code=410, detail='The old lead upload is retired. Use Import Center (/imports/start): choose where the records go, then upload. Nothing is imported or sent from here.')


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
    """RETIRED (#470 Stage D). Kept so old clients get a clear answer instead of a 404."""
    raise HTTPException(status_code=410, detail='The old lead upload is retired. Use Import Center (/imports/start): choose where the records go, then upload. Nothing is imported or sent from here.')


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
    # Manager IN THE WORKSPACE BEING READ (the query below is the active
    # workspace). `lead_scope.is_manager` read users.role, so an org_admin of A
    # who is only an advisor of B received B's whole import inventory - the
    # exact disclosure this refusal exists to prevent.
    if not (current_user.role in ("super_admin", "god_admin")
            or lead_scope.is_manager_here(current_user, db)):
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
            "imported_at": iso_utc(r.imported_at),
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
    # Admin IN THE WORKSPACE BEING WORKED IN (is_manager_here, as
    # deps.require_admin), and the batch is looked up in that same workspace.
    # Without a workspace header both are the home org, exactly as before.
    # Platform operators (super_admin / god_admin) keep their platform-role pass.
    if not (current_user.role in ("super_admin", "god_admin")
            or lead_scope.is_manager_here(current_user, db)):
        raise HTTPException(status_code=403, detail="Only admins can delete import batches.")

    org_id = lead_scope.active_workspace_org_id(current_user, db)

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

    # EVERY OPTIONAL STEP RUNS IN ITS OWN SAVEPOINT.
    #
    # The "table may not exist in this deployment" tolerance used to be
    # `except: db.rollback(); db.begin()`. A full rollback does not skip one
    # statement - it throws away the WHOLE transaction: the duplicate_of_lead_id
    # un-linking and every child delete that had already succeeded. The loop
    # then carried on, so the batch was "deleted" with its earlier children
    # silently restored (orphans / FK failures on the final leads delete). And
    # the two `except: pass` steps were worse on Postgres: a failed statement
    # aborts the transaction, so every later statement would have failed too.
    #
    # A savepoint undoes only the step that failed; everything else still
    # commits - or fails - together at the single db.commit() below.
    def _optional(sql: str, key: Optional[str] = None) -> None:
        try:
            with db.begin_nested():
                r = db.execute(sa_text(sql), p)
            if key and r.rowcount:
                deleted[key] = r.rowcount
        except Exception:
            # Table/column absent in this deployment - skip just this step.
            logger.info("delete_import_batch: skipped optional step %s", key or sql[:40])

    # NULL out duplicate_of_lead_id on any leads OUTSIDE this batch that
    # point INTO it — otherwise the leads delete will hit a self-FK violation
    _optional(
        f"UPDATE leads SET duplicate_of_lead_id = NULL "
        f"WHERE duplicate_of_lead_id IN ({batch_subq})"
    )

    for table in dependents_by_lead_id:
        _optional(f"DELETE FROM {table} WHERE lead_id IN ({batch_subq})", table)

    # booking_links last (booking_followups.booking_link_id → booking_links)
    _optional(f"DELETE FROM booking_links WHERE lead_id IN ({batch_subq})", "booking_links")

    # contact_registry: remove entries whose first_seen_lead_id is in this batch
    # so re-import doesn't flag every lead as a duplicate
    _optional(
        f"DELETE FROM contact_registry WHERE organization_id = :org "
        f"AND first_seen_lead_id IN ({batch_subq})",
        "contact_registry",
    )

    # Finally delete the leads themselves. NOT optional: if this fails the
    # whole request fails and nothing above is committed.
    try:
        r = db.execute(
            sa_text(
                "DELETE FROM leads WHERE organization_id = :org AND source_file = :sf"
            ),
            p,
        )
    except Exception:
        db.rollback()
        logger.exception("delete_import_batch: lead delete failed; batch left intact")
        raise HTTPException(status_code=500,
                            detail="Could not delete the import batch; nothing was changed.")
    deleted["leads"] = r.rowcount

    log_action(
        db,
        actor_user_id=current_user.id,
        organization_id=org_id,
        action="import_batch_deleted",
        target_type="lead_batch",
        target_id=source_file,
        details=f"Deleted {deleted.get('leads', 0)} leads from batch '{source_file}'",
        commit=False,
    )
    db.commit()

    return {"deleted": deleted, "source_file": source_file}

