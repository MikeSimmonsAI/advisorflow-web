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


