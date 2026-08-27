"""
Fiber Lead Capture — Internal API (door knocker use)
-----------------------------------------------------
Used by logged-in BookaBoost reps in the field to create a new fiber
prospect from their phone. Auth comes from their JWT — no separate
identification needed, rep identity is captured automatically.

POST /fiber-leads   — create a new fiber lead (auth required)
GET  /fiber-leads   — list fiber leads for this org (auth required)
"""

import json
import logging
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user, require_tenant_user
from app.services.platform_owner import require_tenant_context
from app.models.models import Lead, gen_uuid

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/fiber-leads", tags=["fiber-leads"])


class FiberLeadCreate(BaseModel):
    first_name: str
    last_name: str
    phone: str
    email: Optional[str] = None
    service_address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    current_provider: Optional[str] = None
    current_speed: Optional[str] = None
    interested_tier: Optional[str] = None
    notes: Optional[str] = None
    verbal_sms_consent: bool = False


@router.post("")
def create_fiber_lead(
    payload: FiberLeadCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_tenant_context),
):
    """Create a new fiber prospect lead. Must be called by a logged-in rep."""
    if not payload.verbal_sms_consent:
        raise HTTPException(
            status_code=422,
            detail="Verbal SMS consent is required before creating a lead.",
        )

    # Normalize phone — strip spaces and dashes for dedup
    phone_clean = payload.phone.strip().replace(" ", "").replace("-", "")
    if not phone_clean:
        raise HTTPException(status_code=422, detail="Phone number is required.")

    # Deduplicate by phone within org
    existing = db.query(Lead).filter(
        Lead.phone == phone_clean,
        Lead.organization_id == current_user.organization_id,
    ).first()
    if existing:
        return {
            "lead_id": existing.id,
            "status": "existing",
            "message": f"Lead already exists for {phone_clean}",
            "first_name": existing.first_name,
            "last_name": existing.last_name,
        }

    extra = {
        "current_provider": payload.current_provider or "",
        "current_speed": payload.current_speed or "",
        "interested_tier": payload.interested_tier or "",
        "verbal_consent": True,
        "consent_timestamp": datetime.utcnow().isoformat(),
        "captured_by_id": str(current_user.id),
        "captured_by_name": current_user.full_name or current_user.email,
        "source_detail": "door_knocker",
    }
    if payload.notes:
        extra["notes"] = payload.notes

    lead = Lead(
        id=gen_uuid(),
        first_name=payload.first_name.strip(),
        last_name=payload.last_name.strip(),
        phone=phone_clean,
        email=payload.email.strip() if payload.email else None,
        status="new",
        tier="prospect",
        message_track="new_inquiry_intro",
        source="fiber_field",
        organization_id=current_user.organization_id,
        street_address=payload.service_address,
        city=payload.city.strip() if payload.city else None,
        state=payload.state.strip() if payload.state else None,
        zip_code=payload.zip_code.strip() if payload.zip_code else None,
        extra_data=json.dumps(extra),
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)

    logger.info(
        "fiber_lead created id=%s by rep=%s org=%s",
        lead.id, current_user.id, current_user.organization_id,
    )
    return {
        "lead_id": lead.id,
        "status": "created",
        "first_name": lead.first_name,
        "last_name": lead.last_name,
        "phone": lead.phone,
    }


@router.get("")
def list_fiber_leads(
    db: Session = Depends(get_db),
    current_user=Depends(require_tenant_user),
):
    """Return recent fiber_field leads for this org (last 100)."""
    leads = (
        db.query(Lead)
        .filter(
            Lead.organization_id == current_user.organization_id,
            Lead.source == "fiber_field",
        )
        .order_by(Lead.created_at.desc())
        .limit(100)
        .all()
    )
    return [
        {
            "lead_id": l.id,
            "first_name": l.first_name,
            "last_name": l.last_name,
            "phone": l.phone,
            "email": l.email,
            "service_address": l.service_address,
            "tier": l.tier,
            "status": l.status,
            "created_at": l.created_at.isoformat() if l.created_at else None,
        }
        for l in leads
    ]
