"""
Tier Definitions Router

CRUD for TierDefinition rows — per-org tier/track configuration.
org_admin can manage their own org's tiers.
super_admin can manage any org's tiers.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional

from app.deps import get_db, get_current_user, require_admin
from app.models.models import TierDefinition, User
from app.services.tier_config_service import seed_default_tier_definitions, clear_and_reseed_tier_definitions
from app.routers.audit_log_router import log_action

router = APIRouter(prefix="/tier-definitions", tags=["tier-definitions"])


def _serialize(t: TierDefinition) -> dict:
    return {
        "id": t.id,
        "organization_id": t.organization_id,
        "tier_key": t.tier_key,
        "tier_label": t.tier_label,
        "track_key": t.track_key,
        "track_label": t.track_label,
        "ai_tone_context": t.ai_tone_context,
        "is_manual_selectable": t.is_manual_selectable,
        "is_active": t.is_active,
        "sort_order": t.sort_order,
    }


@router.get("")
def get_tier_definitions(
    org_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get all tiers for the current org. super_admin can pass ?org_id= to view another org."""
    target_org_id = (
        org_id if (current_user.role == "super_admin" and org_id)
        else current_user.organization_id
    )
    tiers = (
        db.query(TierDefinition)
        .filter(TierDefinition.organization_id == target_org_id)
        .order_by(TierDefinition.sort_order.asc())
        .all()
    )
    return [_serialize(t) for t in tiers]


class TierDefinitionCreate(BaseModel):
    tier_key: str
    tier_label: str
    track_key: str
    track_label: str
    ai_tone_context: Optional[str] = None
    is_manual_selectable: bool = True
    sort_order: int = 0
    org_id: Optional[str] = None  # super_admin only


class TierDefinitionUpdate(BaseModel):
    tier_label: Optional[str] = None
    track_key: Optional[str] = None
    track_label: Optional[str] = None
    ai_tone_context: Optional[str] = None
    is_manual_selectable: Optional[bool] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None


@router.post("")
def create_tier_definition(
    body: TierDefinitionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    target_org_id = (
        body.org_id if (current_user.role == "super_admin" and body.org_id)
        else current_user.organization_id
    )

    existing = db.query(TierDefinition).filter(
        TierDefinition.organization_id == target_org_id,
        TierDefinition.tier_key == body.tier_key.strip().lower(),
    ).first()
    if existing:
        raise HTTPException(400, detail=f"tier_key '{body.tier_key}' already exists for this organization")

    tier = TierDefinition(
        organization_id=target_org_id,
        tier_key=body.tier_key.strip().lower(),
        tier_label=body.tier_label.strip(),
        track_key=body.track_key.strip().lower(),
        track_label=body.track_label.strip(),
        ai_tone_context=body.ai_tone_context,
        is_manual_selectable=body.is_manual_selectable,
        sort_order=body.sort_order,
    )
    db.add(tier)
    db.commit()
    db.refresh(tier)
    log_action(db, target_org_id, current_user.id,
               action="tier_definition.created",
               target_type="tier_definition", target_id=tier.id)
    return _serialize(tier)


@router.put("/{tier_id}")
def update_tier_definition(
    tier_id: str,
    body: TierDefinitionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    tier = db.query(TierDefinition).filter(TierDefinition.id == tier_id).first()
    if not tier:
        raise HTTPException(404, detail="Tier definition not found")
    if current_user.role != "super_admin" and tier.organization_id != current_user.organization_id:
        raise HTTPException(403, detail="Not authorized")

    if body.tier_label is not None:
        tier.tier_label = body.tier_label.strip()
    if body.track_key is not None:
        tier.track_key = body.track_key.strip().lower()
    if body.track_label is not None:
        tier.track_label = body.track_label.strip()
    if body.ai_tone_context is not None:
        tier.ai_tone_context = body.ai_tone_context
    if body.is_manual_selectable is not None:
        tier.is_manual_selectable = body.is_manual_selectable
    if body.is_active is not None:
        tier.is_active = body.is_active
    if body.sort_order is not None:
        tier.sort_order = body.sort_order

    db.commit()
    db.refresh(tier)
    log_action(db, tier.organization_id, current_user.id,
               action="tier_definition.updated",
               target_type="tier_definition", target_id=tier.id)
    return _serialize(tier)


@router.delete("/{tier_id}")
def delete_tier_definition(
    tier_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    tier = db.query(TierDefinition).filter(TierDefinition.id == tier_id).first()
    if not tier:
        raise HTTPException(404, detail="Tier definition not found")
    if current_user.role != "super_admin" and tier.organization_id != current_user.organization_id:
        raise HTTPException(403, detail="Not authorized")

    org_id = tier.organization_id
    db.delete(tier)
    db.commit()
    log_action(db, org_id, current_user.id,
               action="tier_definition.deleted",
               target_type="tier_definition", target_id=tier_id)
    return {"deleted": True}


@router.post("/seed-defaults")
def seed_default_tiers(
    org_id: Optional[str] = None,
    industry: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Seed industry-appropriate default tiers. Idempotent — no-op if tiers already exist."""
    target_org_id = (
        org_id if (current_user.role == "super_admin" and org_id)
        else current_user.organization_id
    )
    # Resolve industry: caller can pass it explicitly; otherwise fall back to org settings
    if not industry:
        from app.models.models import Organization
        org = db.query(Organization).filter(Organization.id == target_org_id).first()
        industry = org.industry if org else "funeral"
    created = seed_default_tier_definitions(db, target_org_id, industry=industry or "funeral")
    if created:
        return {"seeded": len(created), "message": f"Created {len(created)} {industry} tier definitions."}
    return {"seeded": 0, "message": "Tiers already configured — no changes made."}


@router.post("/reset-defaults")
def reset_default_tiers(
    org_id: Optional[str] = None,
    industry: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """DESTRUCTIVE: wipes all tiers for the org and reseeds from industry defaults."""
    target_org_id = (
        org_id if (current_user.role == "super_admin" and org_id)
        else current_user.organization_id
    )
    if not industry:
        from app.models.models import Organization
        org = db.query(Organization).filter(Organization.id == target_org_id).first()
        industry = org.industry if org else "funeral"
    created = clear_and_reseed_tier_definitions(db, target_org_id, industry or "funeral")
    return {"reset": len(created), "industry": industry, "message": f"Reset to {len(created)} {industry} industry defaults."}
