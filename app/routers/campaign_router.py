"""
Campaign Builder router.

A Campaign is a saved admin-defined lead filter plus an optional message-track
assignment. It lets managers preview and apply track/cadence changes to cohorts
without touching leads one-by-one.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.deps import get_db, require_admin
from app.models.models import Campaign, Lead, LeadStatus, User
from app.services.cadence_service import start_cadence
from app.routers.audit_log_router import log_action

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

SUPPORTED_FILTER_KEYS = {"tier", "source_year", "status"}


class CampaignCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    filter_criteria: dict[str, Any] = Field(default_factory=dict)
    message_track: Optional[str] = None  # validated against this org's real TierDefinition.track_key values, not the old hardcoded MessageTrack enum


class CampaignApplyRequest(BaseModel):
    start_cadence: bool = False


def _normalize_filter_criteria(db: Session, organization_id: str, criteria: dict[str, Any] | None) -> dict[str, Any]:
    """
    Keep Campaign filters intentionally small and explicit.

    Supported today:
    - tier: a real tier_key for this organization (see tier_config_service.py)
    - source_year: int
    - status: LeadStatus value
    """
    cleaned: dict[str, Any] = {}
    criteria = criteria or {}

    for key, value in criteria.items():
        if value in (None, ""):
            continue
        if key not in SUPPORTED_FILTER_KEYS:
            raise HTTPException(status_code=400, detail=f"Unsupported campaign filter: {key}")

        if key == "tier":
            # Real, per-org validation - replaces the old LeadTier(value)
            # enum construction, which would have incorrectly rejected
            # any tier key that isn't one of Restland's original 8
            # hardcoded values, exactly the problem this whole
            # configurable-tier system exists to solve.
            from app.services.tier_config_service import validate_tier_key
            try:
                tier_definition = validate_tier_key(db, organization_id, value)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
            cleaned[key] = tier_definition.tier_key

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
        "message_track": campaign.message_track,
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
        "tier": lead.tier,
        "status": lead.status.value if lead.status else None,
        "source_year": lead.source_year,
        "message_track": lead.message_track,
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

    # Real, per-org tier filter - Lead.tier is a plain string now,
    # validated against this org's actual TierDefinition rows rather
    # than the old hardcoded LeadTier enum (which would have incorrectly
    # rejected any tier key that isn't one of Restland's original 8
    # values - exactly the problem this whole system exists to fix).
    # An invalid/unknown tier_key for this org simply matches nothing,
    # rather than raising - a campaign filter with a typo'd or stale
    # tier should return zero leads, not crash the preview/apply call.
    tier = filter_criteria.get("tier")
    if tier:
        query = query.filter(Lead.tier == tier)

    source_year = filter_criteria.get("source_year")
    if source_year is not None:
        query = query.filter(Lead.source_year == int(source_year))

    status = filter_criteria.get("status")
    if status:
        query = query.filter(Lead.status == LeadStatus(status))

    return query


@router.post("")
def create_campaign(
    payload: CampaignCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    criteria = _normalize_filter_criteria(db, current_user.organization_id, payload.filter_criteria)

    # Real, per-org track validation - replaces the old MessageTrack
    # Pydantic enum type, which previously rejected an invalid value
    # automatically before the handler ever ran. Now that the field is
    # a plain string (so a non-Restland org's real track keys are
    # actually accepted), this explicit check is what does that job.
    if payload.message_track is not None:
        from app.services.tier_config_service import list_tier_definitions
        valid_tracks = {d.track_key for d in list_tier_definitions(db, current_user.organization_id)}
        if payload.message_track not in valid_tracks:
            raise HTTPException(status_code=400, detail=f"Invalid message_track. Valid tracks for this organization: {', '.join(sorted(valid_tracks))}")

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
    dnc_count = query.filter(Lead.status == LeadStatus.DNC).count()
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
        if lead.status == LeadStatus.DNC:
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
