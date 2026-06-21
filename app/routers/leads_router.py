import os
import shutil
import tempfile
from fastapi import APIRouter, Depends, UploadFile, File, Query, HTTPException
from sqlalchemy.orm import Session
from typing import Optional

from app.deps import get_db, get_current_user
from app.models.models import User, Lead, LeadStatus
from app.services.import_service import import_leads_from_excel, parse_excel_file
from app.services.dedup_service import bulk_dedup_check

router = APIRouter(prefix="/leads", tags=["leads"])


@router.post("/upload/preview")
def preview_upload(
    file: UploadFile = File(...),
    source_year: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Step 1: advisor uploads an Excel file, we run the REAL import logic
    (tier routing, dedup, compliance flags) in dry_run mode so the preview
    numbers always match what confirm_upload will actually do.
    """
    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
        shutil.copyfileobj(file.file, tmp)
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
        )
    finally:
        os.unlink(tmp_path)

    return summary


@router.post("/upload/confirm")
def confirm_upload(
    file: UploadFile = File(...),
    source_year: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Step 2: advisor confirms - actually import and persist the leads."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        result = import_leads_from_excel(
            db,
            file_path=tmp_path,
            organization_id=current_user.organization_id,
            uploading_user_id=current_user.id,
            source_year=source_year,
            source_filename=file.filename,
        )
    finally:
        os.unlink(tmp_path)

    return result


@router.get("/")
def list_leads(
    status_filter: Optional[str] = Query(None, alias="status"),
    tier: Optional[str] = Query(None),
    message_track: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Advisors see only their own leads. org_admin/super_admin (Mike) can
    use /admin/leads instead for the cross-advisor view.
    Filter by tier or message_track to work one queue at a time, e.g.
    ?message_track=upsell_existing to pull just the Contract Sold upsell list.
    """
    query = db.query(Lead).filter(
        Lead.organization_id == current_user.organization_id,
        Lead.assigned_to_id == current_user.id,
    )
    if status_filter:
        query = query.filter(Lead.status == status_filter)
    if tier:
        query = query.filter(Lead.tier == tier)
    if message_track:
        query = query.filter(Lead.message_track == message_track)
    leads = query.order_by(Lead.created_at.desc()).limit(500).all()
    return leads


@router.get("/needs-review")
def leads_needing_tier_review(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Leads imported with no Lead Type set in the source file (untyped/blank).
    These are held out of any SMS queue until a real tier is assigned -
    they are NOT defaulted to Pre-Need.
    """
    leads = db.query(Lead).filter(
        Lead.organization_id == current_user.organization_id,
        Lead.assigned_to_id == current_user.id,
        Lead.status == LeadStatus.NEEDS_TIER_REVIEW,
    ).order_by(Lead.created_at.desc()).all()
    return leads


@router.patch("/{lead_id}/tier")
def set_lead_tier(
    lead_id: str,
    new_tier: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Manually assign a tier to a needs-review lead, which also sets its message_track and unlocks it for the SMS queue."""
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

    lead.tier = tier_enum
    lead.message_track = TIER_TO_TRACK.get(tier_enum)
    lead.status = LeadStatus.NEW
    db.commit()
    return lead


@router.get("/{lead_id}")
def get_lead(lead_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Returns full contact-card detail for a single lead."""
    lead = db.query(Lead).filter(
        Lead.id == lead_id,
        Lead.organization_id == current_user.organization_id,
    ).first()
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
    from app.models.models import Message, Reply, BookingLink
    import json as _json

    lead = db.query(Lead).filter(
        Lead.id == lead_id, Lead.organization_id == current_user.organization_id
    ).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    messages = db.query(Message).filter(Message.lead_id == lead_id).all()
    replies = db.query(Reply).filter(Reply.lead_id == lead_id).all()

    events = []
    for m in messages:
        events.append({
            "type": "outbound",
            "body": m.body,
            "timestamp": m.sent_at,
            "status": m.twilio_status,
        })
    for r in replies:
        events.append({
            "type": "inbound",
            "body": r.body,
            "timestamp": r.received_at,
            "is_hot": r.is_hot,
        })
    events.sort(key=lambda e: e["timestamp"] or "")

    ai_note = None
    if lead.ai_lead_quality_note:
        try:
            ai_note = _json.loads(lead.ai_lead_quality_note)
        except Exception:
            ai_note = {"raw": lead.ai_lead_quality_note}

    # NOTE: created_at has only second-level precision on some databases
    # (confirmed during testing - two BookingLinks created in the same
    # second get identical timestamps). For the real-world case (one
    # booking link per lead, occasionally resent days/weeks apart) this
    # is a non-issue. It only matters if two links are created for the
    # same lead within the same second, which doesn't happen in normal
    # usage (a human re-sending a link takes longer than that). Not
    # adding a UUID tie-breaker since UUID4 ordering has no relationship
    # to creation order and would be actively misleading.
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

    return {
        "lead": lead,
        "events": events,
        "ai_quality": ai_note,
        "booking": booking_info,
    }
