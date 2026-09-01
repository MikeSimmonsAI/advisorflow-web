"""
Pipeline Router — Full AI conversation pipeline endpoints.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime

from sqlalchemy import func
from app.deps import get_db, require_tenant_user
from app.models.models import User, Lead, PipelineConversation, Organization
from app.services.pipeline_service import (
    launch_pipeline, get_pipeline_stats, get_ai_forecast
)
from app.routers.audit_log_router import log_action
from app.services.lead_scope import (authorized_lead_query, load_lead_in_scope, assert_leads_in_scope, reject_ownership_fields)
from app.services import lead_scope


def _get_org_ids(db: Session, current_user: User) -> list:
    """Return org IDs to scope queries to. god_admin sees ALL orgs."""
    if current_user.role == "god_admin":
        return [str(row[0]) for row in db.query(Organization.id).all()]
    return [str(current_user.organization_id)]

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
    current_user: User = Depends(require_tenant_user),
):
    """Launch AI pipeline for selected leads."""
    leads = db.query(Lead).filter(
        Lead.id.in_(req.lead_ids),
        Lead.organization_id == lead_scope.active_workspace_org_id(current_user, db),
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


def _is_elevated(user: User) -> bool:
    """Returns True for roles that can see all org-wide pipeline data."""
    return user.role in ("org_admin", "super_admin", "god_admin")


@router.get("/stats")
def pipeline_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """Get pipeline engagement stats. Advisors see only their own; admins see org-wide; god sees all orgs."""
    advisor_id = None if _is_elevated(current_user) else current_user.id
    is_god = current_user.role == "god_admin"

    if is_god:
        # Aggregate across all orgs
        org_ids = _get_org_ids(db, current_user)
        combined = {
            "total_in_pipeline": 0, "by_stage": {}, "flagged_count": 0, "flagged": [],
            "total_messages_sent": 0, "total_replies_received": 0, "total_booked": 0,
            "ai_auto_sent": 0, "ai_flagged": 0, "is_god_view": True,
        }
        for oid in org_ids:
            s = get_pipeline_stats(db, oid)
            combined["total_in_pipeline"] += s["total_in_pipeline"]
            combined["flagged_count"] += s["flagged_count"]
            combined["flagged"].extend(s.get("flagged", []))
            combined["total_messages_sent"] += s["total_messages_sent"]
            combined["total_replies_received"] += s["total_replies_received"]
            combined["total_booked"] += s["total_booked"]
            combined["ai_auto_sent"] += s["ai_auto_sent"]
            combined["ai_flagged"] += s["ai_flagged"]
            for stage, cnt in s["by_stage"].items():
                combined["by_stage"][stage] = combined["by_stage"].get(stage, 0) + cnt
        combined["flagged"] = combined["flagged"][:10]  # cap at 10
        return combined

    return get_pipeline_stats(db, current_user.organization_id, advisor_id=advisor_id)


@router.get("/forecast")
def forecast(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """Get AI forecast and alerts for overview dashboard."""
    advisor_id = None if _is_elevated(current_user) else current_user.id
    return get_ai_forecast(db, current_user.organization_id, advisor_id=advisor_id)


@router.get("/flagged")
def get_flagged(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """Get conversations flagged for human review. Advisors see only their own."""
    q = db.query(PipelineConversation).filter(
        PipelineConversation.organization_id == current_user.organization_id,
        PipelineConversation.flagged == True,
        PipelineConversation.reviewed_at == None,
    )
    if not _is_elevated(current_user):
        q = q.filter(PipelineConversation.advisor_id == current_user.id)
    flagged = q.order_by(PipelineConversation.flagged_at.desc()).all()

    result = []
    for p in flagged:
        lead = authorized_lead_query(db, current_user).filter(Lead.id == p.lead_id).first()
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
    current_user: User = Depends(require_tenant_user),
):
    """Approve and optionally send the suggested response for a flagged conversation."""
    # OWN CONVERSATION ONLY. /pipeline/flagged and /pipeline/conversations both
    # scope the READ to the advisor; these two WRITES did not, so an advisor
    # could clear a colleague's review flag - marking handled a conversation
    # nobody had handled, on a queue that exists specifically for human review.
    pipeline = lead_scope.own_records_only(
        db.query(PipelineConversation).filter(
            PipelineConversation.id == pipeline_id,
            PipelineConversation.organization_id == current_user.organization_id,
        ),
        PipelineConversation.advisor_id, current_user,
    ).first()
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    pipeline.reviewed_at = datetime.utcnow()
    pipeline.flagged = False

    if req.send:
        lead = authorized_lead_query(db, current_user).filter(Lead.id == pipeline.lead_id).first()
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
            import logging
            logging.getLogger(__name__).error("pipeline approve send failed: %s", e)
            raise HTTPException(status_code=500, detail="Failed to send message. Please try again.")

    db.commit()
    log_action(db, current_user.organization_id, current_user.id,
               action="pipeline.approved", target_type="pipeline", target_id=pipeline_id)
    return {"approved": True, "sent": req.send}


@router.post("/dismiss/{pipeline_id}")
def dismiss_flagged(
    pipeline_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """Dismiss a flagged conversation without sending — advisor will handle manually."""
    # Own conversation only - same reason as approve above.
    pipeline = lead_scope.own_records_only(
        db.query(PipelineConversation).filter(
            PipelineConversation.id == pipeline_id,
            PipelineConversation.organization_id == current_user.organization_id,
        ),
        PipelineConversation.advisor_id, current_user,
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
    current_user: User = Depends(require_tenant_user),
):
    """Get pipeline conversations. Advisors see only their own; admins see org-wide."""
    query = db.query(PipelineConversation).filter(
        PipelineConversation.organization_id == current_user.organization_id,
    )
    if not _is_elevated(current_user):
        query = query.filter(PipelineConversation.advisor_id == current_user.id)
    if stage:
        query = query.filter(PipelineConversation.stage == stage)

    pipelines = query.order_by(PipelineConversation.updated_at.desc()).limit(200).all()

    result = []
    for p in pipelines:
        lead = authorized_lead_query(db, current_user).filter(Lead.id == p.lead_id).first()
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
