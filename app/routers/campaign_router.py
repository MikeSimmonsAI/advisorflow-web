"""
Campaign Builder router.

A Campaign is a saved admin-defined lead filter plus an optional message-track
assignment. It lets managers preview and apply track/cadence changes to cohorts
without touching leads one-by-one.

Also includes the new Campaign Builder wizard endpoints:
  GET  /campaigns/builder/preview  — filter leads, return matching list
  POST /campaigns/builder/send     — send SMS to a filtered lead list
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.deps import get_db, require_admin, get_current_user
from app.models.models import Campaign, Lead, LeadStatus, LeadTier, MessageTrack, User
from app.services.cadence_service import start_cadence
from app.routers.audit_log_router import log_action

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

SUPPORTED_FILTER_KEYS = {"tier", "source_year", "status"}


# ── Original Campaign schemas ─────────────────────────────────────────────

class CampaignCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    filter_criteria: dict[str, Any] = Field(default_factory=dict)
    message_track: Optional[MessageTrack] = None


class CampaignApplyRequest(BaseModel):
    start_cadence: bool = False


# ── New Campaign Builder schemas ──────────────────────────────────────────

class CampaignBuilderFilters(BaseModel):
    tier: Optional[str] = None
    status: Optional[str] = None
    source_year_min: Optional[int] = None
    source_year_max: Optional[int] = None
    assigned_to_id: Optional[str] = None
    no_contact_days: Optional[int] = None
    has_phone: bool = True
    exclude_dnc: bool = True
    exclude_duplicates: bool = True


class CampaignBuilderSendRequest(BaseModel):
    name: str
    message_template: str
    include_booking_link: bool = True
    lead_ids: List[str]
    schedule_type: str = "now"
    scheduled_at: Optional[datetime] = None
    filters: Optional[CampaignBuilderFilters] = None


# ── Helpers ───────────────────────────────────────────────────────────────

def _normalize_filter_criteria(criteria: dict[str, Any] | None) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    criteria = criteria or {}

    for key, value in criteria.items():
        if value in (None, ""):
            continue
        if key not in SUPPORTED_FILTER_KEYS:
            raise HTTPException(status_code=400, detail=f"Unsupported campaign filter: {key}")

        if key == "tier":
            try:
                cleaned[key] = LeadTier(value).value
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid tier filter: {value}")

        elif key == "status":
            try:
                cleaned[key] = LeadStatus(value).value
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid status filter: {value}")

        elif key == "source_year":
            try:
                year = int(value)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="source_year must be an integer")
            cleaned[key] = year

    return cleaned


def _load_filter_criteria(campaign: Campaign) -> dict[str, Any]:
    if not campaign.filter_criteria:
        return {}
    try:
        data = json.loads(campaign.filter_criteria)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _campaign_to_dict(campaign: Campaign) -> dict[str, Any]:
    return {
        "id": campaign.id,
        "organization_id": campaign.organization_id,
        "name": campaign.name,
        "created_by_id": campaign.created_by_id,
        "filter_criteria": _load_filter_criteria(campaign),
        "message_track": campaign.message_track if campaign.message_track else None,
        "created_at": campaign.created_at,
    }


def _lead_sample(lead: Lead) -> dict[str, Any]:
    name = " ".join(part for part in [lead.first_name, lead.last_name] if part).strip() or "Unnamed lead"
    return {
        "id": lead.id,
        "name": name,
        "first_name": lead.first_name,
        "last_name": lead.last_name,
        "phone": lead.phone,
        "email": lead.email,
        "tier": lead.tier if lead.tier else None,
        "status": lead.status if lead.status else None,
        "source_year": lead.source_year,
        "message_track": lead.message_track if lead.message_track else None,
        "assigned_to_name": None,
    }


def _campaign_or_404(db: Session, campaign_id: str, organization_id: str) -> Campaign:
    campaign = db.query(Campaign).filter(
        Campaign.id == campaign_id,
        Campaign.organization_id == organization_id,
    ).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return campaign


def _matching_leads_query(db: Session, organization_id: str, filter_criteria: dict[str, Any]):
    query = db.query(Lead).filter(Lead.organization_id == organization_id)

    tier = filter_criteria.get("tier")
    if tier:
        query = query.filter(Lead.tier == LeadTier(tier))

    source_year = filter_criteria.get("source_year")
    if source_year is not None:
        query = query.filter(Lead.source_year == int(source_year))

    status = filter_criteria.get("status")
    if status:
        query = query.filter(Lead.status == LeadStatus(status))

    return query


# ── Original Campaign routes ──────────────────────────────────────────────

@router.post("")
def create_campaign(
    payload: CampaignCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    criteria = _normalize_filter_criteria(payload.filter_criteria)
    campaign = Campaign(
        organization_id=current_user.organization_id,
        name=payload.name.strip(),
        created_by_id=current_user.id,
        filter_criteria=json.dumps(criteria, sort_keys=True),
        message_track=payload.message_track,
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    return _campaign_to_dict(campaign)


@router.get("")
def list_campaigns(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    campaigns = db.query(Campaign).filter(
        Campaign.organization_id == current_user.organization_id,
    ).order_by(Campaign.created_at.desc()).all()
    return [_campaign_to_dict(campaign) for campaign in campaigns]


@router.post("/{campaign_id}/preview")
def preview_campaign(
    campaign_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    campaign = _campaign_or_404(db, campaign_id, current_user.organization_id)
    criteria = _load_filter_criteria(campaign)
    query = _matching_leads_query(db, current_user.organization_id, criteria)

    matching_count = query.count()
    dnc_count = query.filter(Lead.status == "dnc").count()
    sample = query.order_by(Lead.created_at.desc()).limit(10).all()

    return {
        "campaign_id": campaign.id,
        "matching_count": matching_count,
        "eligible_count": matching_count - dnc_count,
        "skipped_dnc_count": dnc_count,
        "sample": [_lead_sample(lead) for lead in sample],
    }


@router.post("/{campaign_id}/apply")
def apply_campaign(
    campaign_id: str,
    payload: CampaignApplyRequest | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    payload = payload or CampaignApplyRequest()
    campaign = _campaign_or_404(db, campaign_id, current_user.organization_id)
    criteria = _load_filter_criteria(campaign)
    matching_leads = _matching_leads_query(db, current_user.organization_id, criteria).all()

    matched_count = len(matching_leads)
    updated_count = 0
    skipped_dnc_count = 0
    cadence_started_count = 0

    for lead in matching_leads:
        if lead.status == "dnc":
            skipped_dnc_count += 1
            continue

        if campaign.message_track:
            lead.message_track = campaign.message_track
        updated_count += 1

        if payload.start_cadence:
            before_state = lead.cadence_state
            state = start_cadence(db, lead)
            if state is not None and before_state is None:
                cadence_started_count += 1

    db.commit()

    log_action(
        db, current_user.organization_id, current_user.id,
        action="campaign.apply", target_type="campaign", target_id=campaign.id,
        details={
            "campaign_name": campaign.name,
            "matched_count": matched_count,
            "updated_count": updated_count,
            "skipped_dnc_count": skipped_dnc_count,
            "cadence_started_count": cadence_started_count,
            "start_cadence": payload.start_cadence,
        },
    )

    return {
        "campaign_id": campaign.id,
        "matched_count": matched_count,
        "updated_count": updated_count,
        "skipped_dnc_count": skipped_dnc_count,
        "cadence_started_count": cadence_started_count,
    }


# ── Campaign Builder wizard routes ────────────────────────────────────────

@router.get("/builder/preview")
def builder_preview(
    tier: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    source_year_min: Optional[int] = Query(None),
    source_year_max: Optional[int] = Query(None),
    assigned_to_id: Optional[str] = Query(None),
    no_contact_days: Optional[int] = Query(None),
    has_phone: bool = Query(True),
    exclude_dnc: bool = Query(True),
    exclude_duplicates: bool = Query(True),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return leads matching campaign builder filters without sending anything."""
    query = db.query(Lead).filter(Lead.organization_id == current_user.organization_id)

    if tier:
        try:
            query = query.filter(Lead.tier == LeadTier(tier))
        except ValueError:
            pass
    if status:
        try:
            query = query.filter(Lead.status == LeadStatus(status))
        except ValueError:
            pass
    if source_year_min:
        query = query.filter(Lead.source_year >= source_year_min)
    if source_year_max:
        query = query.filter(Lead.source_year <= source_year_max)
    if assigned_to_id == "unassigned":
        query = query.filter(Lead.assigned_to_id.is_(None))
    elif assigned_to_id:
        query = query.filter(Lead.assigned_to_id == assigned_to_id)
    if has_phone:
        query = query.filter(Lead.phone.isnot(None), Lead.phone != "")
    if exclude_dnc:
        query = query.filter(Lead.status != "dnc")
    if exclude_duplicates:
        query = query.filter(Lead.is_duplicate == False)
    if no_contact_days:
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(days=no_contact_days)
        query = query.filter(
            or_(Lead.last_action_at.is_(None), Lead.last_action_at <= cutoff)
        )

    leads = query.order_by(Lead.created_at.desc()).limit(500).all()

    advisor_ids = [l.assigned_to_id for l in leads if l.assigned_to_id]
    advisor_map = {}
    if advisor_ids:
        advisors = db.query(User).filter(User.id.in_(advisor_ids)).all()
        advisor_map = {str(a.id): a.full_name for a in advisors}

    return [
        {
            "id": str(l.id),
            "first_name": l.first_name,
            "last_name": l.last_name,
            "phone": l.phone,
            "email": l.email,
            "tier": l.tier if l.tier else None,
            "status": l.status if l.status else None,
            "source_year": l.source_year,
            "assigned_to_name": advisor_map.get(str(l.assigned_to_id)),
        }
        for l in leads
    ]


@router.post("/builder/send")
async def builder_send(
    request: CampaignBuilderSendRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Send or schedule a campaign to a list of lead IDs."""
    if not request.lead_ids:
        raise HTTPException(status_code=400, detail="No leads specified.")
    if not request.message_template.strip():
        raise HTTPException(status_code=400, detail="Message template is required.")

    leads = db.query(Lead).filter(
        Lead.id.in_(request.lead_ids),
        Lead.organization_id == current_user.organization_id,
    ).all()

    if not leads:
        raise HTTPException(status_code=404, detail="No matching leads found.")

    if request.schedule_type == "scheduled":
        return {
            "queued_count": len(leads),
            "skipped_count": 0,
            "failed_count": 0,
            "status": "scheduled",
            "scheduled_at": request.scheduled_at.isoformat() if request.scheduled_at else None,
        }

    try:
        from app.services.sms_service import send_sms_to_lead
    except ImportError:
        raise HTTPException(status_code=500, detail="SMS service not available.")

    sent_count = 0
    failed_count = 0

    for lead in leads:
        try:
            first_name = lead.first_name or "there"
            advisor = db.query(User).filter(User.id == lead.assigned_to_id).first() if lead.assigned_to_id else current_user
            advisor_name = advisor.full_name if advisor else current_user.full_name

            message = request.message_template \
                .replace("{first_name}", first_name) \
                .replace("{advisor_name}", advisor_name)

            await send_sms_to_lead(
                lead=lead,
                message=message,
                include_booking_link=request.include_booking_link,
                db=db,
                sending_user=advisor or current_user,
            )
            sent_count += 1
        except Exception:
            failed_count += 1

    try:
        db.commit()
    except Exception:
        db.rollback()

    return {
        "sent_count": sent_count,
        "skipped_count": 0,
        "failed_count": failed_count,
        "status": "completed",
    }
