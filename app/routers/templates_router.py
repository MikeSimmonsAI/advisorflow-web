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


class TemplateAIRewriteRequest(BaseModel):
    message_track: str
    channel: str  # "sms" or "email"
    current_body: str
    current_subject: Optional[str] = None
    instruction: str  # required - this is what makes it a rewrite, not a generate


def _validate_track(db: Session, organization_id: str, message_track: str) -> str:
    """A track this ORGANIZATION sends on, or one of the platform's own.

    `MessageTrack(message_track)` alone rejected every track a non-deathcare
    customer actually has — Atlantis Light & Power could read its templates
    page and then not save one, because `energy_intro` is not in an enum built
    from one customer's pipeline. The column is a String; the enum was only
    ever the validation list, and the organization's own configured tracks
    belong in it.

    Still validated, and still against a list: an arbitrary string is refused,
    so this is not a hole — it is the right list.
    """
    from app.services.template_service import org_tracks

    allowed = {key for key, _ in org_tracks(db, organization_id)}
    allowed.update(t.value for t in MessageTrack)
    if message_track not in allowed:
        raise HTTPException(status_code=400,
                            detail=f"Invalid message_track: {message_track}")
    return message_track


def _validate_track_and_channel(db: Session, organization_id: str,
                                message_track: str, channel: str) -> str:
    track = _validate_track(db, organization_id, message_track)
    if channel not in ("sms", "email"):
        raise HTTPException(status_code=400, detail="channel must be 'sms' or 'email'")
    return track


def _tone(db: Session, user: User, track: str) -> Optional[str]:
    """What this ORGANIZATION says this track is for, in its own words.

    The AI writer described the situation from a platform-side map whose every
    entry is deathcare — "the lead is planning ahead for future cemetery/funeral
    arrangements" — and fell back to "General outreach." for anything else, so a
    non-deathcare customer's generated drafts were written with no situational
    understanding at all.

    `ai_tone_context` is a column on the organization's own tier definitions,
    written when its industry was configured, and it is already what the cadence
    engine uses to set tone. Returning None here leaves the AI service on its
    existing behaviour, so nothing regresses for an organization that has no
    tone context stored.
    """
    from app.services import tier_config_service
    try:
        tone = tier_config_service.get_tone_context_for_track(
            db, user.organization_id, track)
    except Exception:                                        # noqa: BLE001
        return None
    if not tone or tone == "General outreach.":
        return None
    return tone


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
    track = _validate_track_and_channel(
        db, current_user.organization_id, req.message_track, req.channel)

    if req.channel == "email" and not req.email_subject_template:
        raise HTTPException(status_code=400, detail="email_subject_template is required for email templates")

    upsert_template(
        db, current_user.organization_id, track, req.channel,
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
    """Reverts a customized template back to the default."""
    track = _validate_track(db, current_user.organization_id, message_track)

    deleted = reset_template_to_default(db, current_user.organization_id, track, channel)

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
    """
    track = _validate_track_and_channel(
        db, current_user.organization_id, req.message_track, req.channel)
    try:
        return generate_template(track, req.channel, req.instruction,
                                 track_context=_tone(db, current_user, track),
                                 actor=current_user.id)
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
    track = _validate_track_and_channel(
        db, current_user.organization_id, req.message_track, req.channel)
    if req.channel == "email" and not req.current_subject:
        raise HTTPException(status_code=400, detail="current_subject is required when rewriting an email template")
    try:
        return rewrite_template(
            track, req.channel, req.current_body, req.current_subject, req.instruction,
            track_context=_tone(db, current_user, track),
            actor=current_user.id,
        )
    except TemplateAIError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
