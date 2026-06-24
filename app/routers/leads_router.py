import os
import shutil
import tempfile
from fastapi import APIRouter, Depends, UploadFile, File, Form, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, distinct
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timedelta, time, timezone

from app.deps import get_db, get_current_user
from app.models.models import User, Lead, LeadStatus, Reply, ReplyClassification, CadenceState, CadenceStatus, BookingLink, EngagementTemperature
from app.services.import_service import import_leads_from_excel, parse_excel_file
from app.services.dedup_service import bulk_dedup_check
from app.routers.audit_log_router import log_action

router = APIRouter(prefix="/leads", tags=["leads"])


def _is_suppressed(db: Session, lead: Lead) -> bool:
    """Lazy import to avoid a circular import (compliance_service -> compliance_router -> ... )."""
    from app.services.compliance_service import is_phone_suppressed
    return is_phone_suppressed(db, lead.organization_id, lead.phone)


def _require_import_access(current_user: User) -> None:
    """
    Lead import (Excel upload) is admin-only by default per Mike's
    explicit request, but with a per-advisor override
    (User.can_import_leads) an admin can grant individually - not
    all-or-nothing. Checked first, before any file is even written to
    disk, so a rejected request doesn't waste effort on temp-file I/O.
    """
    is_admin = current_user.role in ("org_admin", "super_admin")
    if not is_admin and not current_user.can_import_leads:
        raise HTTPException(
            status_code=403,
            detail="You don't have permission to import leads. Ask an admin to grant you access in Users.",
        )


@router.post("/upload/preview")
def preview_upload(
    file: UploadFile = File(...),
    source_year: Optional[int] = Form(None),
    force_new_inquiry: bool = Form(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Step 1: advisor uploads an Excel file, we run the REAL import logic
    (tier routing, dedup, compliance flags) in dry_run mode so the preview
    numbers always match what confirm_upload will actually do.

    Admin-only by default, with a per-advisor override - see
    _require_import_access above.

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
    _require_import_access(current_user)

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
            force_new_inquiry=force_new_inquiry,
        )
    finally:
        os.unlink(tmp_path)

    return summary


@router.post("/upload/confirm")
def confirm_upload(
    file: UploadFile = File(...),
    source_year: Optional[int] = Form(None),
    force_new_inquiry: bool = Form(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Step 2: advisor confirms - actually import and persist the leads.
    Admin-only by default, with a per-advisor override - see
    _require_import_access above. See preview_upload above for why
    source_year/force_new_inquiry use Form(...).
    """
    _require_import_access(current_user)

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
            force_new_inquiry=force_new_inquiry,
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

    previous_tier = lead.tier.value if lead.tier else None

    lead.tier = tier_enum
    lead.message_track = TIER_TO_TRACK.get(tier_enum)
    lead.status = LeadStatus.NEW
    db.commit()

    log_action(
        db, current_user.organization_id, current_user.id,
        action="lead.set_tier", target_type="lead", target_id=lead.id,
        details={"from": previous_tier, "to": tier_enum.value, "lead_assigned_to_id": lead.assigned_to_id},
    )

    return lead


class MarkDNCRequest(BaseModel):
    reason: str | None = None  # optional - e.g. a snippet of the reply that prompted this


@router.patch("/{lead_id}/mark-dnc")
def mark_lead_dnc(
    lead_id: str,
    req: MarkDNCRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Quick-action DNC flag for when an advisor reads a reply themselves
    and spots STOP/do-not-contact language the automatic keyword/AI
    classification missed. Per Mike's explicit request: any advisor
    should be able to act on this immediately, not just an admin, since
    a missed STOP only gets worse the longer it sits unactioned.

    Deliberately mirrors the automatic DNC path in sms_router.py's
    inbound webhook exactly - same three things happen, in the same
    order, for the same reason: a manually-flagged DNC must behave
    IDENTICALLY to an automatically-detected one, or this lead would
    still get cadence touches sent after being marked DNC, which would
    defeat the entire point of this button.
      1. lead.status = "dnc"
      2. stop_cadence_for_lead(..., CadenceStatus.STOPPED_DNC) - so no
         further scheduled touches go out
      3. added to the org-wide suppression list (source=ADVISOR_FLAGGED,
         distinguishable from an admin's manual Compliance Center entry
         and from the automatic keyword match) - so this phone number is
         blocked from ANY future send, not just this one lead's cadence

    Restricted to leads that HAVE a phone - a lead with no phone can't
    be added to a phone-keyed suppression list; still flips lead.status
    to "dnc" in that case, just skips the suppression-list step.
    """
    from app.services.cadence_service import stop_cadence_for_lead
    from app.models.models import CadenceStatus

    lead = db.query(Lead).filter(
        Lead.id == lead_id, Lead.organization_id == current_user.organization_id
    ).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    already_dnc = lead.status == LeadStatus.DNC

    lead.status = LeadStatus.DNC
    stop_cadence_for_lead(db, lead.id, CadenceStatus.STOPPED_DNC)

    suppression_entry_id = None
    if lead.phone:
        from app.services.compliance_service import add_suppression_entry_from_reply
        from app.models.models import SuppressionSource
        reason = req.reason.strip() if req and req.reason and req.reason.strip() else f"Flagged DNC by {current_user.full_name} from lead detail"
        entry = add_suppression_entry_from_reply(
            db, lead.organization_id, lead.phone, reason=reason, source=SuppressionSource.ADVISOR_FLAGGED,
        )
        suppression_entry_id = entry.id

    db.commit()

    log_action(
        db, current_user.organization_id, current_user.id,
        action="lead.mark_dnc", target_type="lead", target_id=lead.id,
        details={
            "was_already_dnc": already_dnc,
            "phone": lead.phone,
            "reason": req.reason if req else None,
            "suppression_entry_id": suppression_entry_id,
        },
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

    base_lead_filters = (
        Lead.organization_id == current_user.organization_id,
        Lead.assigned_to_id == current_user.id,
    )

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
            CadenceState.status == CadenceStatus.ACTIVE,
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

    return {
        "replies_needing_attention": replies_needing_attention,
        "cadence_touches_due_today": cadence_touches_due_today,
        "leads_imported_last_24h": leads_imported_last_24h,
        "bookings_last_7_days": bookings_last_7_days,
    }


@router.get("/engagement-breakdown")
def engagement_breakdown(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Advisor-scoped engagement temperature counts for the Overview chart.
    Uses the real Lead.engagement_temperature field; no client-side guesses.
    """
    rows = (
        db.query(Lead.engagement_temperature, func.count(Lead.id))
        .filter(
            Lead.organization_id == current_user.organization_id,
            Lead.assigned_to_id == current_user.id,
        )
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
        LeadStatus.NEW,
        LeadStatus.SENT,
        LeadStatus.REPLIED,
        LeadStatus.HOT,
        LeadStatus.BOOKED,
    ]
    rows = (
        db.query(Lead.status, func.count(Lead.id))
        .filter(
            Lead.organization_id == current_user.organization_id,
            Lead.assigned_to_id == current_user.id,
            Lead.status.in_(stages),
        )
        .group_by(Lead.status)
        .all()
    )
    counts = {stage.value: 0 for stage in stages}
    for status, count in rows:
        if status:
            counts[status.value] = int(count or 0)
    return [
        {"status": stage.value, "label": stage.value.replace("_", " ").title(), "count": counts[stage.value]}
        for stage in stages
    ]

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
