from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from app.deps import get_db, require_admin
from app.models.models import User, MessageTrack
from app.services.template_service import (
    list_all_templates_with_defaults, upsert_template, reset_template_to_default,
)
from app.services.template_ai_service import (
    TemplateAIError, generate_template, rewrite_template,
)
from app.routers.audit_log_router import log_action

router = APIRouter(prefix="/templates", tags=["templates"])


class TemplateUpdateRequest(BaseModel):
    message_track: str
    channel: str  # "sms" or "email"
    body_template: str
    email_subject_template: Optional[str] = None


class TemplateAIGenerateRequest(BaseModel):
    message_track: str
    channel: str  # "sms" or "email"
    instruction: Optional[str] = None  # optional extra guidance for a from-scratch generation
    tone: str = "standard"  # "soft" | "standard" | "urgent" | "direct" - same 4 tones as the SMS reply tone selector


class TemplateAIRewriteRequest(BaseModel):
    message_track: str
    channel: str  # "sms" or "email"
    current_body: str
    current_subject: Optional[str] = None
    instruction: str  # required - this is what makes it a rewrite, not a generate
    tone: str = "standard"


def _validate_track_and_channel(db: Session, organization_id: str, message_track: str, channel: str) -> str:
    from app.services.tier_config_service import list_tier_definitions
    valid_tracks = {d.track_key for d in list_tier_definitions(db, organization_id)}
    if message_track not in valid_tracks:
        raise HTTPException(status_code=400, detail=f"Invalid message_track: {message_track}")
    if channel not in ("sms", "email"):
        raise HTTPException(status_code=400, detail="channel must be 'sms' or 'email'")
    return message_track


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
    track_key = _validate_track_and_channel(db, current_user.organization_id, req.message_track, req.channel)

    if req.channel == "email" and not req.email_subject_template:
        raise HTTPException(status_code=400, detail="email_subject_template is required for email templates")

    upsert_template(
        db, current_user.organization_id, track_key, req.channel,
        req.body_template, current_user.id, req.email_subject_template,
    )

    log_action(
        db, current_user.organization_id, current_user.id,
        action="template.update", target_type="template", target_id=f"{req.message_track}:{req.channel}",
        details={"message_track": req.message_track, "channel": req.channel},
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
    track_key = _validate_track_and_channel(db, current_user.organization_id, message_track, channel)

    deleted = reset_template_to_default(db, current_user.organization_id, track_key, channel)

    if deleted:
        log_action(
            db, current_user.organization_id, current_user.id,
            action="template.reset_to_default", target_type="template", target_id=f"{message_track}:{channel}",
            details={"message_track": message_track, "channel": channel},
        )

    return {"reset": deleted}


@router.post("/ai/generate")
def ai_generate_template(
    req: TemplateAIGenerateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """
    Generates a fresh template draft from scratch for a track+channel. The
    admin still reviews and explicitly saves via PUT /templates/ - this only
    fills the editor box, it never writes to the database itself.

    tone (soft/standard/urgent/direct) is Mike's explicit request for
    "more control over the tone of the email... before generating or
    sending it" - same 4 tones as the SMS reply tone selector.
    """
    from app.services.template_ai_service import VALID_TONES
    if req.tone not in VALID_TONES:
        raise HTTPException(status_code=400, detail=f"tone must be one of: {', '.join(VALID_TONES)}")

    track_key = _validate_track_and_channel(db, current_user.organization_id, req.message_track, req.channel)
    try:
        return generate_template(db, current_user.organization_id, track_key, req.channel, req.instruction, req.tone)
    except TemplateAIError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/ai/rewrite")
def ai_rewrite_template(
    req: TemplateAIRewriteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """
    Rewrites the admin's current in-progress draft per a free-text
    instruction (e.g. "make this warmer", "shorter", "add urgency"). Like
    generate, this only returns a new draft for the editor - it does not
    save anything until the admin clicks Save.
    """
    from app.services.template_ai_service import VALID_TONES
    if req.tone not in VALID_TONES:
        raise HTTPException(status_code=400, detail=f"tone must be one of: {', '.join(VALID_TONES)}")

    track_key = _validate_track_and_channel(db, current_user.organization_id, req.message_track, req.channel)
    if req.channel == "email" and not req.current_subject:
        raise HTTPException(status_code=400, detail="current_subject is required when rewriting an email template")
    try:
        return rewrite_template(
            db, current_user.organization_id, track_key, req.channel, req.current_body, req.current_subject, req.instruction, req.tone,
        )
    except TemplateAIError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
