import os
import shutil
import tempfile
from fastapi import APIRouter, Depends, UploadFile, File, Query, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from app.deps import get_db, get_current_user
from app.models.models import User, Lead, LeadStatus
from app.services.import_service import import_leads_from_excel, parse_excel_file
from app.services.dedup_service import bulk_dedup_check

router = APIRouter(prefix="/leads", tags=["leads"])


def _is_suppressed(db: Session, lead: Lead) -> bool:
    """Lazy import to avoid a circular import (compliance_service -> compliance_router -> ... )."""
    from app.services.compliance_service import is_phone_suppressed
    return is_phone_suppressed(db, lead.organization_id, lead.phone)


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
    # Use the real file extension so CSV/Google Contacts files are parsed correctly
    orig_name = file.filename or "upload.xlsx"
    ext = ".csv" if orig_name.lower().endswith(".csv") else ".xlsx"
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
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
    orig_name = file.filename or "upload.xlsx"
    ext = ".csv" if orig_name.lower().endswith(".csv") else ".xlsx"
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
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

        if lead.status == LeadStatus.DNC:
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
            tier=lead.tier.value if lead.tier else None,
            message_track=lead.message_track.value if lead.message_track else None,
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
