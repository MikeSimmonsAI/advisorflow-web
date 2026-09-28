"""
Tier Definitions Router

CRUD for TierDefinition rows — per-org tier/track configuration.
org_admin can manage their own (active workspace) org's tiers.
super_admin can manage the tiers of orgs on their own platform (brand).
god_admin can manage any org's tiers.

TENANT BOUNDARY. `?org_id=` / `body.org_id` used to be honoured for any
super_admin with no second question, and update/delete skipped the org check
for super_admin entirely - so one brand's operator could read, rewrite, seed
over or wipe (reset-defaults) another brand's customer tier configuration.
Every client-supplied org id now goes through `deps.load_org_in_scope`, and a
tier loaded by id is re-checked against the caller's scope. Out of scope is a
404, never a 403, so tier ids and org ids cannot be enumerated.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional

from app.deps import get_db, get_current_user, require_admin, load_org_in_scope
from app.models.models import TierDefinition, User
from app.services.lead_scope import active_workspace_org_id
from app.services.tier_config_service import seed_default_tier_definitions, clear_and_reseed_tier_definitions
from app.routers.audit_log_router import log_action

router = APIRouter(prefix="/tier-definitions", tags=["tier-definitions"])

_ELEVATED = ("super_admin", "god_admin")


def _target_org_id(db: Session, current_user: User, org_id: Optional[str],
                   request: Optional[Request] = None):
    """The org this request operates on.

    A client-supplied org id is honoured only for platform operators, and only
    after `load_org_in_scope` confirms it is inside their scope (god: any org;
    super_admin: their own platform). Anything else is a 404.

    With no org id, the org is the ACTIVE workspace - the same one require_admin
    evaluated the caller's admin role in - rather than the home column.
    """
    if org_id and current_user.role in _ELEVATED:
        return str(load_org_in_scope(db, current_user, org_id).id)
    return active_workspace_org_id(current_user, db, request)


def _check_tier_in_scope(db: Session, current_user: User, tier: TierDefinition,
                         request: Optional[Request] = None) -> None:
    """Refuse (404) a tier the caller may not act on."""
    if current_user.role in _ELEVATED:
        # god passes inside load_org_in_scope; super_admin is confined to its
        # own platform. HTTPException(404) propagates on failure.
        try:
            load_org_in_scope(db, current_user, tier.organization_id)
        except HTTPException:
            raise HTTPException(404, detail="Tier definition not found")
        return
    active = active_workspace_org_id(current_user, db, request)
    if active is None or str(tier.organization_id) != str(active):
        raise HTTPException(403, detail="Not authorized")


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
    request: Request,
    org_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get all tiers for the current org. super_admin can pass ?org_id= to view
    another org on its own platform; god_admin any org."""
    target_org_id = _target_org_id(db, current_user, org_id, request)
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
    org_id: Optional[str] = None  # super_admin (own platform) / god_admin only


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
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    target_org_id = _target_org_id(db, current_user, body.org_id, request)
    if not target_org_id:
        raise HTTPException(400, detail="No organization selected")

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
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    tier = db.query(TierDefinition).filter(TierDefinition.id == tier_id).first()
    if not tier:
        raise HTTPException(404, detail="Tier definition not found")
    _check_tier_in_scope(db, current_user, tier, request)

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
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    tier = db.query(TierDefinition).filter(TierDefinition.id == tier_id).first()
    if not tier:
        raise HTTPException(404, detail="Tier definition not found")
    _check_tier_in_scope(db, current_user, tier, request)

    org_id = tier.organization_id
    db.delete(tier)
    db.commit()
    log_action(db, org_id, current_user.id,
               action="tier_definition.deleted",
               target_type="tier_definition", target_id=tier_id)
    return {"deleted": True}


@router.post("/seed-defaults")
def seed_default_tiers(
    request: Request,
    org_id: Optional[str] = None,
    industry: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Seed industry-appropriate default tiers. Idempotent — no-op if tiers already exist."""
    target_org_id = _target_org_id(db, current_user, org_id, request)
    if not target_org_id:
        raise HTTPException(400, detail="No organization selected")
    # Resolve industry: caller can pass it explicitly; otherwise fall back to org settings
    if not industry:
        from app.models.models import Organization
        org = db.query(Organization).filter(Organization.id == target_org_id).first()
        industry = org.industry if org else None
    # No funeral fallback. An unstated industry resolves through the industry
    # template registry to a neutral set — see tier_config_service.
    created = seed_default_tier_definitions(db, target_org_id, industry=industry)
    if created:
        return {"seeded": len(created), "message": f"Created {len(created)} {industry} tier definitions."}
    return {"seeded": 0, "message": "Tiers already configured — no changes made."}


@router.post("/reset-defaults")
def reset_default_tiers(
    request: Request,
    org_id: Optional[str] = None,
    industry: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """DESTRUCTIVE: wipes all tiers for the org and reseeds from industry defaults."""
    target_org_id = _target_org_id(db, current_user, org_id, request)
    if not target_org_id:
        raise HTTPException(400, detail="No organization selected")
    if not industry:
        from app.models.models import Organization
        org = db.query(Organization).filter(Organization.id == target_org_id).first()
        industry = org.industry if org else None
    created = clear_and_reseed_tier_definitions(db, target_org_id, industry)
    return {"reset": len(created), "industry": industry, "message": f"Reset to {len(created)} {industry} industry defaults."}
