"""
AI Auto-Conversation Router
Endpoints for the one-button AI conversation feature.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional

from app.deps import get_db, get_current_user
from app.models.models import User, Lead
from app.services.ai_conversation_service import (
    start_ai_conversation,
    pause_ai_conversation,
    resume_ai_conversation,
    get_conversation_status,
    generate_auto_reply,
    process_scheduled_touches,
)
from app.routers.audit_log_router import log_action
from app.services.lead_scope import (authorized_lead_query, load_lead_in_scope, assert_leads_in_scope, reject_ownership_fields)

router = APIRouter(prefix="/ai-conversation", tags=["ai-conversation"])


class StartConversationRequest(BaseModel):
    lead_id: str
    channel: str = "email"


class BulkStartRequest(BaseModel):
    lead_ids: list[str]
    channel: str = "email"   # "sms", "email", or "auto" (picks by contact_channel)


class PauseRequest(BaseModel):
    lead_id: str
    reason: Optional[str] = "Advisor paused"


class ResumeRequest(BaseModel):
    lead_id: str


class AutoReplyRequest(BaseModel):
    lead_ids: list[str]
    tone: str = "warm"
    auto_send: bool = False
    channel: str = "email"
    ai_direction: Optional[str] = None  # User's custom AI instructions (overrides defaults)
    relationship_type: Optional[str] = None  # Override relationship context for this batch


class SingleReplyRequest(BaseModel):
    lead_id: str
    tone: str = "warm"


class ApproveRequest(BaseModel):
    lead_id: str
    message: str
    include_booking_link: bool = False


@router.post("/start")
def start_conversation(
    req: StartConversationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    lead = authorized_lead_query(db, current_user).filter(Lead.id == req.lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    result = start_ai_conversation(db, lead, current_user, channel=req.channel)
    if result.get("success"):
        log_action(db, current_user.organization_id, current_user.id, action="ai_conversation.started", target_type="lead", target_id=req.lead_id)
    return result


@router.post("/bulk-start")
def bulk_start_conversations(
    req: BulkStartRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Start AI conversations on up to 500 leads at once.
    channel="auto" routes each lead by its contact_channel field (sms vs email).
    Returns a summary: started, skipped (already active or DNC), errors.
    """
    if len(req.lead_ids) > 500:
        raise HTTPException(status_code=400, detail="Maximum 500 leads per bulk start request.")

    leads = authorized_lead_query(db, current_user).filter(Lead.id.in_(req.lead_ids)).all()

    lead_map = {str(l.id): l for l in leads}

    started = []
    skipped = []
    errors = []

    for lead_id in req.lead_ids:
        lead = lead_map.get(str(lead_id))
        if not lead:
            errors.append({"lead_id": lead_id, "reason": "Not found"})
            continue

        # Auto-channel: use lead's contact_channel if caller passes "auto"
        if req.channel == "auto":
            channel = lead.contact_channel if lead.contact_channel in ("sms", "email", "email_only") else "email"
            if channel == "email_only":
                channel = "email"
        else:
            channel = req.channel

        try:
            result = start_ai_conversation(db, lead, current_user, channel=channel)
            if result.get("success"):
                started.append(lead_id)
                log_action(
                    db, current_user.organization_id, current_user.id,
                    action="ai_conversation.bulk_started",
                    target_type="lead", target_id=str(lead_id),
                )
            elif result.get("already_active"):
                skipped.append({"lead_id": lead_id, "reason": "Already active"})
            else:
                skipped.append({"lead_id": lead_id, "reason": result.get("error", "Skipped")})
        except Exception as exc:
            errors.append({"lead_id": lead_id, "reason": str(exc)})

    return {
        "started": len(started),
        "skipped": len(skipped),
        "errors": len(errors),
        "started_ids": started,
        "skipped_detail": skipped,
        "error_detail": errors,
    }


@router.post("/pause")
def pause_conversation(
    req: PauseRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    lead = authorized_lead_query(db, current_user).filter(Lead.id == req.lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    result = pause_ai_conversation(db, req.lead_id, current_user.id, req.reason or "Advisor paused")
    log_action(db, current_user.organization_id, current_user.id, action="ai_conversation.paused", target_type="lead", target_id=req.lead_id)
    return result


@router.post("/resume")
def resume_conversation(
    req: ResumeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    lead = authorized_lead_query(db, current_user).filter(Lead.id == req.lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    result = resume_ai_conversation(db, req.lead_id, current_user.id)
    log_action(db, current_user.organization_id, current_user.id, action="ai_conversation.resumed", target_type="lead", target_id=req.lead_id)
    return result


@router.get("/status/{lead_id}")
def conversation_status(
    lead_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    lead = authorized_lead_query(db, current_user).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return get_conversation_status(db, lead_id, current_user.id)


@router.post("/process-scheduled")
def process_scheduled(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role not in ("super_admin", "god_admin"):
        raise HTTPException(status_code=403, detail="Super admin only")
    return process_scheduled_touches(db)


@router.post("/preview")
def preview_auto_reply(
    req: SingleReplyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    lead = authorized_lead_query(db, current_user).filter(Lead.id == req.lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    result = generate_auto_reply(db, lead, current_user, tone=req.tone)
    return {"lead_id": lead.id, "lead_name": f"{lead.first_name or ''} {lead.last_name or ''}".strip(), "phone": lead.phone, **result}


@router.post("/generate-batch")
def generate_batch_replies(
    req: AutoReplyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    leads = authorized_lead_query(db, current_user).filter(Lead.id.in_(req.lead_ids)).all()
    results = []
    sent = skipped = queued = errors = 0
    for lead in leads:
        try:
            ai_result = generate_auto_reply(
                db, lead, current_user,
                tone=req.tone,
                ai_direction=req.ai_direction,
                relationship_type=req.relationship_type,
            )
            if ai_result["should_stop"]:
                skipped += 1
                results.append({"lead_id": lead.id, "action": "skipped", "reason": ai_result["reason"], "reply": ""})
                continue
            if req.auto_send and ai_result["reply"] and lead.email:
                try:
                    from app.services.ai_conversation_service import _send_email_via_graph
                    _send_email_via_graph(current_user, lead.email, ai_result.get("subject", f"Following up, {lead.first_name or 'there'}"), ai_result["reply"])
                    sent += 1
                    log_action(db, current_user.organization_id, current_user.id, action="ai_conversation.auto_sent", target_type="lead", target_id=lead.id)
                    results.append({"lead_id": lead.id, "action": "sent", "reply": ai_result["reply"]})
                except Exception as e:
                    errors += 1
                    results.append({"lead_id": lead.id, "action": "error", "reason": str(e)})
            else:
                queued += 1
                results.append({"lead_id": lead.id, "action": "queued", "reply": ai_result["reply"], "booking_url": ai_result.get("booking_url", "")})
        except Exception as e:
            errors += 1
            results.append({"lead_id": lead.id, "action": "error", "reason": str(e)})
    return {"total": len(leads), "sent": sent, "queued": queued, "skipped": skipped, "errors": errors, "results": results}


@router.post("/send-approved")
def send_approved_reply(
    req: ApproveRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    lead = authorized_lead_query(db, current_user).filter(Lead.id == req.lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    if not lead.phone:
        raise HTTPException(status_code=400, detail="Lead has no phone number")
    if lead.status == "dnc":
        raise HTTPException(status_code=400, detail="Lead is DNC")
    from app.services.sms_service import send_sms
    send_sms(db=db, lead=lead, advisor=current_user, template=req.message, include_booking_link=req.include_booking_link)
    log_action(db, current_user.organization_id, current_user.id, action="ai_conversation.approved_sent", target_type="lead", target_id=req.lead_id)
    return {"sent": True, "lead_id": lead.id}
