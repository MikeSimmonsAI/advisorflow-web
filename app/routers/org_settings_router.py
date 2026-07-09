"""
Org Settings Router — white labeling, tier config, industry settings.
"""
import json
import uuid
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user, require_admin
from app.models.models import Organization, User

router = APIRouter(prefix="/org-settings", tags=["org-settings"])

DEFAULT_TIERS = {
    "funeral": [
        {"value": "pre_need", "label": "Pre-Need", "color": "blue", "description": "Planning ahead"},
        {"value": "at_need", "label": "At-Need", "color": "red", "description": "Immediate need"},
        {"value": "imminent", "label": "Imminent", "color": "red", "description": "Within 90 days"},
        {"value": "contract_sold", "label": "Contract Sold", "color": "green", "description": "Closed"},
        {"value": "email_only", "label": "Email Only", "color": "purple", "description": "No phone"},
        {"value": "partial", "label": "Needs Review", "color": "amber", "description": "Incomplete info"},
    ],
    "roofing": [
        {"value": "estimate_requested", "label": "Estimate Requested", "color": "blue", "description": "New lead"},
        {"value": "estimate_given", "label": "Estimate Given", "color": "amber", "description": "Quote sent"},
        {"value": "follow_up", "label": "Follow Up", "color": "amber", "description": "Waiting on decision"},
        {"value": "contract_signed", "label": "Contract Signed", "color": "green", "description": "Closed"},
        {"value": "email_only", "label": "Email Only", "color": "purple", "description": "No phone"},
    ],
    "insurance": [
        {"value": "prospect", "label": "Prospect", "color": "blue", "description": "Initial contact"},
        {"value": "quoted", "label": "Quoted", "color": "amber", "description": "Quote sent"},
        {"value": "application", "label": "Application", "color": "amber", "description": "App in progress"},
        {"value": "policy_sold", "label": "Policy Sold", "color": "green", "description": "Closed"},
        {"value": "email_only", "label": "Email Only", "color": "purple", "description": "No phone"},
    ],
    "real_estate": [
        {"value": "buyer_lead", "label": "Buyer Lead", "color": "blue", "description": "Looking to buy"},
        {"value": "seller_lead", "label": "Seller Lead", "color": "amber", "description": "Looking to sell"},
        {"value": "showing_scheduled", "label": "Showing Scheduled", "color": "amber", "description": "Active"},
        {"value": "under_contract", "label": "Under Contract", "color": "green", "description": "Pending close"},
        {"value": "closed", "label": "Closed", "color": "green", "description": "Deal done"},
        {"value": "email_only", "label": "Email Only", "color": "purple", "description": "No phone"},
    ],
    "dental": [
        {"value": "new_patient", "label": "New Patient", "color": "blue", "description": "First contact"},
        {"value": "consultation", "label": "Consultation", "color": "amber", "description": "Consult booked"},
        {"value": "treatment_plan", "label": "Treatment Plan", "color": "amber", "description": "Plan presented"},
        {"value": "active_patient", "label": "Active Patient", "color": "green", "description": "Ongoing care"},
        {"value": "email_only", "label": "Email Only", "color": "purple", "description": "No phone"},
    ],
    "custom": [
        {"value": "tier_1", "label": "Tier 1", "color": "blue", "description": ""},
        {"value": "tier_2", "label": "Tier 2", "color": "amber", "description": ""},
        {"value": "tier_3", "label": "Tier 3", "color": "green", "description": ""},
        {"value": "email_only", "label": "Email Only", "color": "purple", "description": "No phone"},
    ],
}


class BrandingUpdate(BaseModel):
    brand_name: Optional[str] = None
    brand_logo_url: Optional[str] = None
    brand_color_primary: Optional[str] = None
    brand_color_accent: Optional[str] = None


class IndustryUpdate(BaseModel):
    industry: str


class TierConfigUpdate(BaseModel):
    tiers: list[dict]


@router.get("/")
def get_org_settings(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    tier_config = []
    if org.tier_config:
        try:
            tier_config = json.loads(org.tier_config)
        except Exception:
            pass
    if not tier_config:
        tier_config = DEFAULT_TIERS.get(org.industry or "funeral", DEFAULT_TIERS["funeral"])

    return {
        "id": org.id,
        "name": org.name,
        "slug": org.slug,
        "plan": org.plan,
        "industry": org.industry or "funeral",
        "brand_name": org.brand_name,
        "brand_logo_url": org.brand_logo_url,
        "brand_color_primary": org.brand_color_primary,
        "brand_color_accent": org.brand_color_accent,
        "tier_config": tier_config,
    }


@router.get("/default-tiers")
def get_default_tiers():
    return DEFAULT_TIERS


@router.patch("/branding")
def update_branding(
    req: BrandingUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    if req.brand_name is not None: org.brand_name = req.brand_name
    if req.brand_logo_url is not None: org.brand_logo_url = req.brand_logo_url
    if req.brand_color_primary is not None: org.brand_color_primary = req.brand_color_primary
    if req.brand_color_accent is not None: org.brand_color_accent = req.brand_color_accent
    db.commit()
    return {"updated": True}


@router.patch("/industry")
def update_industry(
    req: IndustryUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    org.industry = req.industry
    # Reset tier config to industry defaults
    org.tier_config = json.dumps(DEFAULT_TIERS.get(req.industry, DEFAULT_TIERS["custom"]))
    db.commit()
    return {"updated": True, "tiers": json.loads(org.tier_config)}


@router.patch("/tiers")
def update_tier_config(
    req: TierConfigUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    org.tier_config = json.dumps(req.tiers)
    db.commit()
    return {"updated": True, "tiers": req.tiers}
