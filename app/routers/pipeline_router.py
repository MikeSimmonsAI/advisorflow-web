"""
Pipeline Router — Full AI conversation pipeline endpoints.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime

from sqlalchemy import func
from app.deps import get_db, require_tenant_user, require_tenant_or_observer
from app.models.models import User, Lead, PipelineConversation, Organization
from app.services.pipeline_service import (
    launch_pipeline, get_pipeline_stats, get_ai_forecast
)
from app.routers.audit_log_router import log_action
from app.services.lead_scope import (authorized_lead_query, load_lead_in_scope, assert_leads_in_scope, reject_ownership_fields)
from app.services import lead_scope


def _get_org_ids(db: Session, current_user: User) -> list:
    """Return org IDs to scope queries to.

    THE NEUTRAL OWNER sees all orgs. An owner STANDING INSIDE A CUSTOMER sees
    that customer — role is permission, not scope. See
    lead_scope.god_sees_all_orgs.
    """
    if lead_scope.god_sees_all_orgs(current_user):
        return [str(row[0]) for row in db.query(Organization.id).all()]
    return [str(lead_scope.active_workspace_org_id(current_user, db)
                or current_user.organization_id)]

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
        actor=current_user.id,
    )
    _ws = _org_id(db, current_user)
    log_action(db, _ws, current_user.id,
               action="pipeline.launched", target_type="batch",
               target_id=_ws)
    return result


def _is_elevated(user: User, db: Session = None) -> bool:
    """True for a manager IN THE WORKSPACE BEING WORKED IN (is_manager_here).

    This read `users.role`, so an org_admin of A who is only an advisor of B
    saw - and could approve / dismiss - every advisor's pipeline in B, and B's
    real admin (an advisor at home) saw only their own. Without a workspace
    header this is `users.role` exactly as before.
    """
    return lead_scope.is_manager_here(user, db)


def _org_id(db: Session, user: User):
    """The ACTIVE workspace org (X-Workspace-Id backed by a membership, else
    the home column) - the same workspace `_is_elevated` is evaluated in and
    that authorized_lead_query already scopes the lead names to."""
    return lead_scope.active_workspace_org_id(user, db)


@router.get("/stats")
def pipeline_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """Pipeline engagement stats for THIS workspace.

    Advisors see only their own; admins see org-wide; the NEUTRAL owner — one
    standing outside every customer — sees all orgs. An owner inside a
    customer sees that customer, because entering a workspace is the act that
    narrows the question. This used to test `role == "god_admin"` alone, which
    put every organization's pipeline on a client's own Reports page under a
    banner reading "God View — All Organizations". See
    lead_scope.god_sees_all_orgs.
    """
    advisor_id = None if _is_elevated(current_user, db) else current_user.id
    is_god = lead_scope.god_sees_all_orgs(current_user)

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

    # THE ACTIVE WORKSPACE, NOT THE HOME COLUMN. `users.organization_id` is
    # where a person lives, which is not where they are standing: a member of
    # two customers reading it gets the first one's pipeline inside the second.
    return get_pipeline_stats(
        db,
        lead_scope.active_workspace_org_id(current_user, db)
        or current_user.organization_id,
        advisor_id=advisor_id)


@router.get("/forecast")
def forecast(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_or_observer),
):
    """Get AI forecast and alerts for overview dashboard."""
    advisor_id = None if _is_elevated(current_user, db) else current_user.id
    # Use active_workspace_org_id so executive observers get the observed org,
    # not current_user.organization_id which is None for brand executives.
    org_id = lead_scope.active_workspace_org_id(current_user, db)
    return get_ai_forecast(db, org_id, advisor_id=advisor_id)


@router.get("/flagged")
def get_flagged(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """Get conversations flagged for human review. Advisors see only their own."""
    q = db.query(PipelineConversation).filter(
        PipelineConversation.organization_id == _org_id(db, current_user),
        PipelineConversation.flagged == True,
        PipelineConversation.reviewed_at == None,
    )
    if not _is_elevated(current_user, db):
        q = q.filter(PipelineConversation.advisor_id == current_user.id)
    flagged = q.order_by(PipelineConversation.flagged_at.desc()).limit(200).all()

    leads = _authorized_leads_by_id(db, current_user, flagged)
    result = []
    for p in flagged:
        lead = leads.get(p.lead_id)
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
    pipeline = db.query(PipelineConversation).filter(
        PipelineConversation.id == pipeline_id,
        PipelineConversation.organization_id == _org_id(db, current_user),
    )
    # Owner-only for an advisor IN THIS WORKSPACE - own_records_only's rule,
    # judged on the workspace role (it read users.role, so a home org_admin
    # who is an advisor here passed).
    if lead_scope.effective_role(current_user, db) in lead_scope.OWNER_SCOPED_ROLES:
        pipeline = pipeline.filter(PipelineConversation.advisor_id == current_user.id)
    pipeline = pipeline.first()
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
    log_action(db, pipeline.organization_id, current_user.id,
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
    pipeline = db.query(PipelineConversation).filter(
        PipelineConversation.id == pipeline_id,
        PipelineConversation.organization_id == _org_id(db, current_user),
    )
    # Owner-only for an advisor IN THIS WORKSPACE - own_records_only's rule,
    # judged on the workspace role (it read users.role, so a home org_admin
    # who is an advisor here passed).
    if lead_scope.effective_role(current_user, db) in lead_scope.OWNER_SCOPED_ROLES:
        pipeline = pipeline.filter(PipelineConversation.advisor_id == current_user.id)
    pipeline = pipeline.first()
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    pipeline.reviewed_at = datetime.utcnow()
    pipeline.flagged = False
    db.commit()
    return {"dismissed": True}


def _authorized_leads_by_id(db: Session, user: User, pipelines) -> dict:
    """One scoped query for every lead on the page instead of one per row.
    Leads outside the caller's scope are simply absent (rendered "Unknown",
    exactly as the per-row lookup did)."""
    ids = {p.lead_id for p in pipelines if p.lead_id}
    if not ids:
        return {}
    return {l.id: l for l in authorized_lead_query(db, user).filter(Lead.id.in_(ids)).all()}


@router.get("/conversations")
def get_conversations(
    stage: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """Get pipeline conversations. Advisors see only their own; admins see org-wide."""
    query = db.query(PipelineConversation).filter(
        PipelineConversation.organization_id == _org_id(db, current_user),
    )
    if not _is_elevated(current_user, db):
        query = query.filter(PipelineConversation.advisor_id == current_user.id)
    if stage:
        query = query.filter(PipelineConversation.stage == stage)

    pipelines = query.order_by(PipelineConversation.updated_at.desc()).limit(200).all()

    leads = _authorized_leads_by_id(db, current_user, pipelines)
    result = []
    for p in pipelines:
        lead = leads.get(p.lead_id)
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
