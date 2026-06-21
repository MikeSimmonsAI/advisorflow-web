from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from app.deps import get_db, require_admin
from app.models.models import User, MessageTrack
from app.services.template_service import (
    list_all_templates_with_defaults, upsert_template, reset_template_to_default,
)

router = APIRouter(prefix="/templates", tags=["templates"])


class TemplateUpdateRequest(BaseModel):
    message_track: str
    channel: str  # "sms" or "email"
    body_template: str
    email_subject_template: Optional[str] = None


@router.get("/")
def list_templates(db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    """
    Returns every message track + channel combination with its current
    text - either the org's customization or the hardcoded default if
    nothing's been customized yet. Restricted to org_admin/super_admin
    since template wording affects every advisor's outreach.
    """
    return list_all_templates_with_defaults(db, current_user.organization_id)


@router.put("/")
def update_template(
    req: TemplateUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    try:
        track_enum = MessageTrack(req.message_track)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid message_track: {req.message_track}")

    if req.channel not in ("sms", "email"):
        raise HTTPException(status_code=400, detail="channel must be 'sms' or 'email'")

    if req.channel == "email" and not req.email_subject_template:
        raise HTTPException(status_code=400, detail="email_subject_template is required for email templates")

    upsert_template(
        db, current_user.organization_id, track_enum, req.channel,
        req.body_template, current_user.id, req.email_subject_template,
    )
    return {"success": True}


@router.delete("/{message_track}/{channel}")
def reset_template(
    message_track: str,
    channel: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Reverts a customized template back to the hardcoded default."""
    try:
        track_enum = MessageTrack(message_track)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid message_track: {message_track}")

    deleted = reset_template_to_default(db, current_user.organization_id, track_enum, channel)
    return {"reset": deleted}
