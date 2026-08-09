"""
Org Settings Router — white labeling, tier config, industry settings.
Super admin can pass ?org_id= to manage any org's settings.
"""
import json
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
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
    "fiber": [
        {"value": "prospect", "label": "Prospect", "color": "blue", "description": "New inquiry, not yet contacted"},
        {"value": "quoted", "label": "Quoted", "color": "amber", "description": "Service options presented"},
        {"value": "scheduled_install", "label": "Scheduled Install", "color": "orange", "description": "Install date set"},
        {"value": "active_customer", "label": "Active Customer", "color": "green", "description": "Service live"},
        {"value": "churned", "label": "Churned", "color": "red", "description": "Cancelled or lost"},
        {"value": "email_only", "label": "Email Only", "color": "purple", "description": "No phone"},
    ],
}


def _resolve_org(current_user: User, org_id: Optional[str], db: Session) -> Organization:
    """
    Resolve which org to operate on.
    Super admin can pass ?org_id= to manage any org's settings.
    Everyone else always gets their own org.
    """
    if org_id and current_user.role == "super_admin":
        org = db.query(Organization).filter(Organization.id == org_id).first()
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")
        return org
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org


class SocialLinksUpdate(BaseModel):
    facebook_url: Optional[str] = None
    google_review_url: Optional[str] = None
    instagram_url: Optional[str] = None
    linkedin_url: Optional[str] = None


class BrandingUpdate(BaseModel):
    brand_name: Optional[str] = None
    brand_logo_url: Optional[str] = None
    brand_color_primary: Optional[str] = None
    brand_color_accent: Optional[str] = None
    member_label: Optional[str] = None   # singular e.g. "Agent"
    members_label: Optional[str] = None  # plural   e.g. "Agents"


class IndustryUpdate(BaseModel):
    industry: str


class TierConfigUpdate(BaseModel):
    tiers: list[dict]


@router.get("/")
def get_org_settings(
    org_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    org = _resolve_org(current_user, org_id, db)

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
        "member_label": getattr(org, "member_label", None),
        "members_label": getattr(org, "members_label", None),
        "tier_config": tier_config,
        "facebook_url": getattr(org, "facebook_url", None),
        "google_review_url": getattr(org, "google_review_url", None),
        "instagram_url": getattr(org, "instagram_url", None),
        "linkedin_url": getattr(org, "linkedin_url", None),
        "enabled_features": json.loads(org.enabled_features) if getattr(org, "enabled_features", None) else None,
    }


@router.get("/default-tiers")
def get_default_tiers():
    return DEFAULT_TIERS


@router.patch("/branding")
def update_branding(
    req: BrandingUpdate,
    org_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    org = _resolve_org(current_user, org_id, db)
    if req.brand_name is not None: org.brand_name = req.brand_name
    if req.brand_logo_url is not None: org.brand_logo_url = req.brand_logo_url
    if req.brand_color_primary is not None: org.brand_color_primary = req.brand_color_primary
    if req.brand_color_accent is not None: org.brand_color_accent = req.brand_color_accent
    # Empty string = clear the override (fall back to industry default in the UI)
    if req.member_label is not None: org.member_label = req.member_label or None
    if req.members_label is not None: org.members_label = req.members_label or None
    db.commit()
    return {"updated": True}


@router.patch("/industry")
def update_industry(
    req: IndustryUpdate,
    org_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    org = _resolve_org(current_user, org_id, db)
    org.industry = req.industry
    org.tier_config = json.dumps(DEFAULT_TIERS.get(req.industry, DEFAULT_TIERS["custom"]))
    db.commit()
    return {"updated": True, "tiers": json.loads(org.tier_config)}


@router.patch("/tiers")
def update_tier_config(
    req: TierConfigUpdate,
    org_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    org = _resolve_org(current_user, org_id, db)
    org.tier_config = json.dumps(req.tiers)
    db.commit()
    return {"updated": True, "tiers": req.tiers}


@router.patch("/social-links")
def update_social_links(
    req: SocialLinksUpdate,
    org_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Save organization-level social media / review page URLs."""
    org = _resolve_org(current_user, org_id, db)
    org.facebook_url = req.facebook_url or None
    org.google_review_url = req.google_review_url or None
    org.instagram_url = req.instagram_url or None
    org.linkedin_url = req.linkedin_url or None
    db.commit()
    return {"updated": True}


class FeaturesUpdate(BaseModel):
    enabled_features: list[str] | None = None  # None = all enabled; [] = none


@router.patch("/features")
def update_enabled_features(
    req: FeaturesUpdate,
    org_id: str = Query(..., description="Organization ID (required, super admin only)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Super admin only: set which admin features an org can access.
    Pass enabled_features=null to restore all-enabled state.
    Pass enabled_features=[] to disable all optional features.
    Pass enabled_features=["campaigns","reports",...] to restrict to a subset.
    """
    if current_user.role != "super_admin":
        raise HTTPException(status_code=403, detail="Super admin only")
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    if req.enabled_features is None:
        org.enabled_features = None
    else:
        org.enabled_features = json.dumps(req.enabled_features)
    db.commit()
    return {"updated": True, "enabled_features": req.enabled_features}
