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
from app.models.models import User, Lead, LeadStatus, Reply, ReplyClassification, CadenceState, CadenceStatus, BookingLink, EngagementTemperature
from app.services.import_service import import_leads_from_excel, parse_excel_file
from app.services.dedup_service import bulk_dedup_check
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
    with tempfile.NamedTemporaryFile(delete=False, suffix=original_ext) as tmp:
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
    """Step 2: advisor confirms - actually import and persist the leads. See preview_upload above for why source_year/force_new_inquiry use Form(...)."""
    import os as _os
    original_ext = _os.path.splitext(file.filename or "upload.xlsx")[1].lower() or ".xlsx"
    with tempfile.NamedTemporaryFile(delete=False, suffix=original_ext) as tmp:
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
    leads = query.order_by(Lead.created_at.desc()).all()
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
        Lead.status == "needs_tier_review",
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
        "new",
        "sent",
        "replied",
        "hot",
        "booked",
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
    from app.models.models import Message, Reply, BookingLink, EmailMessage, CadenceState

    lead = db.query(Lead).filter(
        Lead.id == lead_id, Lead.organization_id == current_user.organization_id
    ).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    messages = db.query(Message).filter(Message.lead_id == lead_id).all()
    replies = db.query(Reply).filter(Reply.lead_id == lead_id).all()
    email_messages = db.query(EmailMessage).filter(EmailMessage.lead_id == lead_id).all()

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
        events.append({
            "type": "outbound",
            "channel": "email",
            "body": e.subject,
            "body_preview": e.body_html[:200] if e.body_html else "",
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
    from app.deps import require_admin
    if current_user.role not in ("org_admin", "super_admin"):
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

    return {"deleted": count, "message": f"Permanently deleted {count} duplicate leads."}


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
    last_normalized = normalize_last_name(payload.last_name or "")

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

    return {
        "id": lead.id,
        "name": f"{lead.first_name} {lead.last_name}",
        "is_duplicate": is_dup,
        "status": "created",
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
