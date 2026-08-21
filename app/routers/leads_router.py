import os
import shutil
import tempfile
import json as _json
from fastapi import APIRouter, Depends, UploadFile, File, Form, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, distinct
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timedelta, time, timezone

from app.deps import get_db, get_current_user
from app.models.models import User, Lead, Reply, ReplyClassification, CadenceState, BookingLink, EngagementTemperature, CRMContact, VoiceCall
from app.services.import_service import import_leads_from_excel
from app.services.dedup_service import normalize_phone
from app.routers.audit_log_router import log_action

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
    current_user: User = Depends(get_current_user),
):
    """
    Step 1: advisor uploads an Excel file, we run the REAL import logic
    (tier routing, dedup, compliance flags) in dry_run mode so the preview
    numbers always match what confirm_upload will actually do.

    source_year and force_new_inquiry are explicitly marked as Form(...)
    fields, not bare params - without that marker FastAPI treats them as
    query parameters when mixed with a File(...) upload, which silently
    ignored the frontend's multipart form value for source_year (a real,
    pre-existing bug found and fixed while wiring up force_new_inquiry,
    which would have had the exact same problem).

    force_new_inquiry: manual override for batches of brand-new web/cold
    leads - tags every row as New Inquiry regardless of auto-detection
    from a source column. See import_service.import_leads_from_excel for
    the full reasoning.
    """
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
    current_user: User = Depends(get_current_user),
):
    """Step 2: advisor confirms - actually import and persist the leads. See preview_upload above for why source_year/force_new_inquiry use Form(...)."""
    import os as _os
    original_ext = _os.path.splitext(file.filename or "upload.xlsx")[1].lower() or ".xlsx"
    if original_ext not in (".xlsx", ".xls", ".csv"):
        raise HTTPException(status_code=400, detail="Only .xlsx, .xls, and .csv files are accepted.")

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
                raise HTTPException(status_code=413, detail="File too large. Maximum upload size is 50 MB.")
            tmp.write(chunk)
        tmp_path = tmp.name

    try:
        result = import_leads_from_excel(
            db,
            file_path=tmp_path,
            organization_id=current_user.organization_id,
            uploading_user_id=current_user.id,
            source_year=source_year,
            source_filename=file.filename,
            force_new_inquiry=force_new_inquiry,
            relationship_type=relationship_type or "cold_lead",
            import_list_name=import_list_name,
            campaign_purpose=campaign_purpose,
            offer_hook=offer_hook,
            imported_by_name=current_user.full_name or current_user.email,
        )
    finally:
        os.unlink(tmp_path)

    return result


@router.get("/import-batches")
def list_import_batches(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Returns all import batches for this org, newest first. Includes import_list_name for display."""
    rows = (
        db.query(
            Lead.source_file,
            Lead.import_list_name,
            Lead.imported_by_name,
            func.count(Lead.id).label("lead_count"),
            func.min(Lead.created_at).label("imported_at"),
        )
        .filter(
            Lead.organization_id == current_user.organization_id,
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
    current_user: User = Depends(get_current_user),
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
    status_filter: Optional[str] = Query(None, alias="status"),
    tier: Optional[str] = Query(None),
    message_track: Optional[str] = Query(None),
    temperature: Optional[str] = Query(None),
    import_list_name: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(500, ge=1, le=2000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Advisors see only their own leads. org_admin/super_admin see all org leads.
    Returns a lean payload — only columns needed by the list view, no large
    text blobs (notes, ai_quality_note, custom_fields). This keeps the Leads
    page fast even with thousands of leads.
    """
    is_manager = current_user.role in ("org_admin", "super_admin", "god_admin")

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
        "last_messaged_at",
    ]

    query = db.query(*COLS).filter(Lead.organization_id == current_user.organization_id)
    if not is_manager:
        query = query.filter(Lead.assigned_to_id == current_user.id)
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
    current_user: User = Depends(get_current_user),
):
    """Return all manually flagged leads for this org (both bad_email and remove_all)."""
    is_manager = current_user.role in ("org_admin", "super_admin", "god_admin")
    query = db.query(Lead).filter(
        Lead.organization_id == current_user.organization_id,
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
    current_user: User = Depends(get_current_user),
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
        Lead.organization_id == current_user.organization_id,
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
    current_user: User = Depends(get_current_user),
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

    lead = db.query(Lead).filter(
        Lead.id == lead_id, Lead.organization_id == current_user.organization_id
    ).first()
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
def daily_briefing(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
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

    is_manager = current_user.role in ("org_admin", "super_admin", "god_admin")
    base_lead_filters = [Lead.organization_id == current_user.organization_id]
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
def engagement_breakdown(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Advisor-scoped engagement temperature counts for the Overview chart.
    Uses the real Lead.engagement_temperature field; no client-side guesses.
    """
    is_manager = current_user.role in ("org_admin", "super_admin", "god_admin")
    eng_filters = [Lead.organization_id == current_user.organization_id]
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
def status_funnel(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
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
    is_manager = current_user.role in ("org_admin", "super_admin", "god_admin")
    funnel_filters = [
        Lead.organization_id == current_user.organization_id,
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
def get_lead(lead_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Returns full contact-card detail for a single lead.

    Advisors can only access leads assigned to them. Org admins and above
    can access any lead in their organization.
    """
    is_manager = current_user.role in ("org_admin", "super_admin", "god_admin")
    q = db.query(Lead).filter(
        Lead.id == lead_id,
        Lead.organization_id == current_user.organization_id,
    )
    if not is_manager:
        q = q.filter(Lead.assigned_to_id == current_user.id)
    lead = q.first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


@router.get("/{lead_id}/timeline")
def get_lead_timeline(lead_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
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

    is_manager_tl = current_user.role in ("org_admin", "super_admin", "god_admin")
    q_tl = db.query(Lead).filter(Lead.id == lead_id, Lead.organization_id == current_user.organization_id)
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
    for m in messages:
        events.append({
            "type": "outbound",
            "channel": "sms",
            "body": m.body,
            "timestamp": m.sent_at,
            "status": m.twilio_status,
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
    current_user: User = Depends(get_current_user),
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

    leads = db.query(Lead).filter(
        Lead.id.in_(req.lead_ids), Lead.organization_id == current_user.organization_id
    ).all()
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
            placeholder_booking_url = f"{BOOKING_BASE_URL}/book/preview"
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
    current_user: User = Depends(get_current_user),
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
        lead = db.query(Lead).filter(
            Lead.id == item.lead_id, Lead.organization_id == current_user.organization_id
        ).first()
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


@router.delete("/duplicates/bulk-delete")
def bulk_delete_duplicate_leads(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
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
        Lead.organization_id == current_user.organization_id,
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
    current_user: User = Depends(get_current_user),
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
            Lead.organization_id == current_user.organization_id,
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
                lead.status = "dnc"
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
    current_user: User = Depends(get_current_user),
):
    """
    Create a single lead manually from the Leads page UI.
    Runs through dedup check against existing org leads.
    """
    import uuid
    from app.services.dedup_service import normalize_phone, normalize_last_name

    phone_normalized = normalize_phone(payload.phone or "")
    last_name_normalized = normalize_last_name(payload.last_name or "")  # was: return value discarded

    # Check for duplicate by phone
    is_dup = False
    if phone_normalized:
        existing = db.query(Lead).filter(
            Lead.organization_id == current_user.organization_id,
            Lead.phone == phone_normalized,
            Lead.is_duplicate == False,
        ).first()
        if existing:
            is_dup = True

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
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Edit basic contact fields on a lead. Advisors can edit their own; admins can edit any."""
    lead = db.query(Lead).filter(
        Lead.id == lead_id,
        Lead.organization_id == current_user.organization_id,
    ).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    if current_user.role == "advisor" and lead.assigned_to_id != current_user.id:
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
    current_user: User = Depends(get_current_user),
):
    """Permanently delete a single lead. Advisors can delete their own leads; admins can delete any."""
    lead = db.query(Lead).filter(
        Lead.id == lead_id,
        Lead.organization_id == current_user.organization_id,
    ).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    if current_user.role == "advisor" and lead.assigned_to_id != current_user.id:
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
    current_user: User = Depends(get_current_user),
):
    """Set the lead type and/or AI direction override for a lead."""
    lead = db.query(Lead).filter(
        Lead.id == lead_id,
        Lead.organization_id == current_user.organization_id,
    ).first()
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
    current_user: User = Depends(get_current_user),
):
    """
    Any advisor in any org can manually flag a lead the auto-detection missed.
    flag_type = "bad_email"   → hide from email queue + email campaigns, SMS still ok
    flag_type = "remove_all"  → hide from all outreach lists everywhere
    flag_type = None          → unflag, fully restore to all lists
    """
    # Advisors can only flag leads in their own org for security
    lead = db.query(Lead).filter(
        Lead.id == lead_id,
        Lead.organization_id == current_user.organization_id,
    ).first()
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
