"""
Pipeline Router — Full AI conversation pipeline endpoints.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime

from app.deps import get_db, get_current_user
from app.models.models import User, Lead, PipelineConversation
from app.services.pipeline_service import (
    launch_pipeline, get_pipeline_stats, get_ai_forecast, analyze_and_respond
)
from app.routers.audit_log_router import log_action

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


class LaunchRequest(BaseModel):
    lead_ids: list[str]
    lead_type: str = "general"
    tone: str = "warm"
    ai_direction: str = ""
    channel: str = "sms"
    auto_respond: bool = True


class ApproveRequest(BaseModel):
    pipeline_id: str
    message: str
    send: bool = True


class ForecastRequest(BaseModel):
    pass


@router.post("/launch")
def launch(
    req: LaunchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Launch AI pipeline for selected leads."""
    leads = db.query(Lead).filter(
        Lead.id.in_(req.lead_ids),
        Lead.organization_id == current_user.organization_id,
    ).all()

    if not leads:
        raise HTTPException(status_code=404, detail="No leads found")

    result = launch_pipeline(
        db=db,
        leads=leads,
        advisor=current_user,
        lead_type=req.lead_type,
        tone=req.tone,
        ai_direction=req.ai_direction,
        channel=req.channel,
        auto_respond=req.auto_respond,
    )
    log_action(db, current_user.organization_id, current_user.id,
               action="pipeline.launched", target_type="batch",
               target_id=current_user.organization_id)
    return result


@router.get("/stats")
def pipeline_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get pipeline engagement stats."""
    return get_pipeline_stats(db, current_user.organization_id)


@router.get("/forecast")
def forecast(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get AI forecast and alerts for overview dashboard."""
    return get_ai_forecast(db, current_user.organization_id)


@router.get("/flagged")
def get_flagged(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get all conversations flagged for human review."""
    flagged = db.query(PipelineConversation).filter(
        PipelineConversation.organization_id == current_user.organization_id,
        PipelineConversation.flagged == True,
        PipelineConversation.reviewed_at == None,
    ).order_by(PipelineConversation.flagged_at.desc()).all()

    result = []
    for p in flagged:
        lead = db.query(Lead).filter(Lead.id == p.lead_id).first()
        result.append({
            "pipeline_id": p.id,
            "lead_id": p.lead_id,
            "lead_name": f"{lead.first_name or ''} {lead.last_name or ''}".strip() if lead else "Unknown",
            "lead_phone": lead.phone if lead else None,
            "lead_tier": lead.tier if lead else None,
            "flag_reason": p.flag_reason,
            "flagged_reply": p.flagged_reply_body,
            "suggested_response": p.flagged_suggested_response,
            "flagged_at": p.flagged_at,
            "stage": p.stage,
            "tone": p.tone,
            "lead_type": p.lead_type,
            "messages_sent": p.messages_sent,
            "replies_received": p.replies_received,
        })
    return result


@router.post("/approve/{pipeline_id}")
def approve_flagged(
    pipeline_id: str,
    req: ApproveRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Approve and optionally send the suggested response for a flagged conversation."""
    pipeline = db.query(PipelineConversation).filter(
        PipelineConversation.id == pipeline_id,
        PipelineConversation.organization_id == current_user.organization_id,
    ).first()
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    pipeline.reviewed_at = datetime.utcnow()
    pipeline.flagged = False

    if req.send:
        lead = db.query(Lead).filter(Lead.id == pipeline.lead_id).first()
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found")
        try:
            from app.services.sms_service import send_sms
            send_sms(db=db, lead=lead, advisor=current_user,
                     template=req.message, include_booking_link=False)
            pipeline.messages_sent = (pipeline.messages_sent or 0) + 1
            pipeline.stage = "ai_responding"
            pipeline.last_outbound_at = datetime.utcnow()
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    db.commit()
    log_action(db, current_user.organization_id, current_user.id,
               action="pipeline.approved", target_type="pipeline", target_id=pipeline_id)
    return {"approved": True, "sent": req.send}


@router.post("/dismiss/{pipeline_id}")
def dismiss_flagged(
    pipeline_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Dismiss a flagged conversation without sending — advisor will handle manually."""
    pipeline = db.query(PipelineConversation).filter(
        PipelineConversation.id == pipeline_id,
        PipelineConversation.organization_id == current_user.organization_id,
    ).first()
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    pipeline.reviewed_at = datetime.utcnow()
    pipeline.flagged = False
    db.commit()
    return {"dismissed": True}


@router.get("/conversations")
def get_conversations(
    stage: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get all pipeline conversations, optionally filtered by stage."""
    query = db.query(PipelineConversation).filter(
        PipelineConversation.organization_id == current_user.organization_id,
    )
    if stage:
        query = query.filter(PipelineConversation.stage == stage)

    pipelines = query.order_by(PipelineConversation.updated_at.desc()).limit(200).all()

    result = []
    for p in pipelines:
        lead = db.query(Lead).filter(Lead.id == p.lead_id).first()
        result.append({
            "pipeline_id": p.id,
            "lead_id": p.lead_id,
            "lead_name": f"{lead.first_name or ''} {lead.last_name or ''}".strip() if lead else "Unknown",
            "lead_phone": lead.phone if lead else None,
            "lead_tier": lead.tier if lead else None,
            "stage": p.stage,
            "flagged": p.flagged,
            "tone": p.tone,
            "lead_type": p.lead_type,
            "channel": p.channel,
            "messages_sent": p.messages_sent,
            "replies_received": p.replies_received,
            "ai_responses_sent": p.ai_responses_sent,
            "ai_responses_flagged": p.ai_responses_flagged,
            "last_outbound_at": p.last_outbound_at,
            "last_inbound_at": p.last_inbound_at,
            "booked_at": p.booked_at,
            "confirmed_at": p.confirmed_at,
            "created_at": p.created_at,
        })
    return result
