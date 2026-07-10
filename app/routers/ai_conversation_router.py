"""
AI Auto-Conversation Router
Endpoints for the AI-managed conversation queue.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional

from app.deps import get_db, get_current_user
from app.models.models import User, Lead, Message
from app.services.ai_conversation_service import generate_auto_reply
from app.routers.audit_log_router import log_action
from datetime import datetime

router = APIRouter(prefix="/ai-conversation", tags=["ai-conversation"])


class AutoReplyRequest(BaseModel):
    lead_ids: list[str]
    tone: str = "warm"
    auto_send: bool = False
    channel: str = "sms"  # sms | email | both


class SingleReplyRequest(BaseModel):
    lead_id: str
    tone: str = "warm"


class ApproveRequest(BaseModel):
    lead_id: str
    message: str
    include_booking_link: bool = False


@router.post("/preview")
def preview_auto_replies(
    req: SingleReplyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Generate a preview of the AI reply for one lead without sending."""
    lead = db.query(Lead).filter(
        Lead.id == req.lead_id,
        Lead.organization_id == current_user.organization_id,
    ).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    result = generate_auto_reply(db, lead, current_user, tone=req.tone)
    return {
        "lead_id": lead.id,
        "lead_name": f"{lead.first_name or ''} {lead.last_name or ''}".strip(),
        "phone": lead.phone,
        **result,
    }


@router.post("/generate-batch")
def generate_batch_replies(
    req: AutoReplyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Generate AI replies for a batch of leads.
    If auto_send=True, sends immediately.
    If auto_send=False, returns drafts for advisor review.
    """
    from app.routers.sms_router import _send_sms_to_lead

    leads = db.query(Lead).filter(
        Lead.id.in_(req.lead_ids),
        Lead.organization_id == current_user.organization_id,
    ).all()

    results = []
    sent = 0
    skipped = 0
    queued = 0
    errors = 0

    for lead in leads:
        try:
            ai_result = generate_auto_reply(db, lead, current_user, tone=req.tone)

            if ai_result["should_stop"]:
                skipped += 1
                results.append({
                    "lead_id": lead.id,
                    "lead_name": f"{lead.first_name or ''} {lead.last_name or ''}".strip(),
                    "action": "skipped",
                    "reason": ai_result["reason"],
                    "reply": "",
                })
                continue

            if req.auto_send and ai_result["reply"]:
                channel = req.channel
                send_errors = []
                did_send = False

                if channel in ("sms", "both") and lead.phone and lead.status != "dnc":
                    try:
                        from app.services.sms_service import send_sms
                        send_sms(db=db, lead=lead, advisor=current_user, template=ai_result["reply"], include_booking_link=False)
                        did_send = True
                    except Exception as e:
                        send_errors.append(f"SMS: {e}")

                if channel in ("email", "both") and lead.email:
                    try:
                        from app.services.email_service import send_email
                        send_email(db=db, lead=lead, advisor=current_user,
                            subject=f"Following up, {lead.first_name or 'there'}",
                            body=ai_result["reply"])
                        did_send = True
                    except Exception as e:
                        send_errors.append(f"Email: {e}")

                if did_send:
                    sent += 1
                    log_action(db, current_user.organization_id, current_user.id, action="ai_conversation.auto_sent", target_type="lead", target_id=lead.id)
                    results.append({
                        "lead_id": lead.id,
                        "lead_name": f"{lead.first_name or ''} {lead.last_name or ''}".strip(),
                        "action": "sent",
                        "reply": ai_result["reply"],
                        "reason": ai_result["reason"],
                    })
                else:
                    errors += 1
                    results.append({
                        "lead_id": lead.id,
                        "lead_name": f"{lead.first_name or ''} {lead.last_name or ''}".strip(),
                        "action": "error",
                        "reply": "",
                        "reason": "; ".join(send_errors) or "No valid channel",
                    })
            else:
                queued += 1
                results.append({
                    "lead_id": lead.id,
                    "lead_name": f"{lead.first_name or ''} {lead.last_name or ''}".strip(),
                    "action": "queued",
                    "reply": ai_result["reply"],
                    "reason": ai_result["reason"],
                    "booking_url": ai_result["booking_url"],
                    "source": ai_result["source"],
                })
        except Exception as e:
            errors += 1
            results.append({
                "lead_id": lead.id,
                "lead_name": f"{lead.first_name or ''} {lead.last_name or ''}".strip(),
                "action": "error",
                "reply": "",
                "reason": str(e),
            })

    return {
        "total": len(leads),
        "sent": sent,
        "queued": queued,
        "skipped": skipped,
        "errors": errors,
        "results": results,
    }


@router.post("/send-approved")
def send_approved_reply(
    req: ApproveRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Send an advisor-approved AI-drafted message."""
    lead = db.query(Lead).filter(
        Lead.id == req.lead_id,
        Lead.organization_id == current_user.organization_id,
    ).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    if not lead.phone:
        raise HTTPException(status_code=400, detail="Lead has no phone number")
    if lead.status == "dnc":
        raise HTTPException(status_code=400, detail="Lead is DNC")

    from app.services.sms_service import send_sms
    result = send_sms(
        db=db,
        lead=lead,
        advisor=current_user,
        template=req.message,
        include_booking_link=req.include_booking_link,
    )
    log_action(db, current_user.organization_id, current_user.id, action="ai_conversation.approved_sent", target_type="lead", target_id=lead.id)
    return {"sent": True, "lead_id": lead.id}
