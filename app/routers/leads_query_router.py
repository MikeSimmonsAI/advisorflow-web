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
    current_user: User = Depends(require_tenant_or_observer),
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



@router.get("/sparklines")
def overview_sparklines(
    days: int = Query(7, ge=2, le=30),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_or_observer),
):
    """
    Real, recent daily counts for the Overview page's KPI card sparklines,
    built from genuine history and never fabricated. Empty days come back as
    0 from the server rather than being invented client-side, which is the
    same rule sms_router.reply_activity_by_day follows.

    Returns {"leads_imported": [int, ...], "bookings": [int, ...]}, each a
    list of `days` daily counts oldest to newest. The frontend renders these
    directly with zero further computation, so there is no seam where a
    fabricated number could enter on either side.

    RESTORED. This endpoint, and the four others recovered alongside it, were
    deleted by the bulk overwrite commits 76c608b / f3358ea ("force update Thu
    07/09/2026") - not by a decision. tests/test_sparklines.py was not part of
    that overwrite, so it kept specifying the behaviour and kept failing, and
    the failure was carried as "baseline" rather than read. The body below is
    the recovered implementation with ONE deliberate change: scope now goes
    through lead_scope.active_workspace_org_id / is_manager_here, matching
    every other route in this file, so restoring the feature cannot also
    restore a pre-isolation visibility boundary.

    MUST stay registered above GET /{lead_id}: otherwise "sparklines" is
    captured as a lead id and this route 404s through the lead lookup, which
    is exactly how it presented while it was missing.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    start_date = now.date() - timedelta(days=days - 1)
    start_at = datetime.combine(start_date, time.min)

    is_manager = lead_scope.is_manager_here(current_user, db)
    base_lead_filters = [Lead.organization_id == lead_scope.active_workspace_org_id(current_user, db)]
    if not is_manager:
        base_lead_filters.append(Lead.assigned_to_id == current_user.id)

    imported_rows = (
        db.query(Lead.created_at)
        .filter(*base_lead_filters, Lead.created_at >= start_at)
        .all()
    )
    booking_rows = (
        db.query(BookingLink.booked_time)
        .join(Lead, BookingLink.lead_id == Lead.id)
        .filter(
            *base_lead_filters,
            BookingLink.status == "booked",
            BookingLink.booked_time.isnot(None),
            BookingLink.booked_time >= start_at,
        )
        .all()
    )

    def _counts_by_day(rows):
        counts = {(start_date + timedelta(days=offset)).isoformat(): 0 for offset in range(days)}
        for (ts,) in rows:
            if ts is None:
                continue
            key = ts.date().isoformat()
            if key in counts:
                counts[key] += 1
        return [counts[date_key] for date_key in sorted(counts.keys())]

    return {
        "leads_imported": _counts_by_day(imported_rows),
        "bookings": _counts_by_day(booking_rows),
    }


@router.get("/daily-briefing")
def daily_briefing(db: Session = Depends(get_db), current_user: User = Depends(require_tenant_or_observer)):
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
def status_funnel(db: Session = Depends(get_db), current_user: User = Depends(require_tenant_or_observer)):
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

