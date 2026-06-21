"""
Template Service
Lets an org_admin (Mike) customize the per-tier message copy for both
SMS and email without a code deploy. If no customization exists for a
given org+track+channel, falls back to the hardcoded defaults already
in cadence_service.py (SMS) and email_service.py (email) - so the
system works out of the box on day one, and customization is additive,
not required.
"""

from sqlalchemy.orm import Session
from app.models.models import MessageTemplate, MessageTrack


def get_sms_template(db: Session, organization_id: str, track: MessageTrack) -> str | None:
    """Returns the org's custom SMS template for this track, or None if no override exists."""
    override = db.query(MessageTemplate).filter(
        MessageTemplate.organization_id == organization_id,
        MessageTemplate.message_track == track,
        MessageTemplate.channel == "sms",
    ).first()
    return override.body_template if override else None


def get_email_template(db: Session, organization_id: str, track: MessageTrack) -> dict | None:
    """Returns {'subject': ..., 'body_html': ...} for this org's custom email template, or None."""
    override = db.query(MessageTemplate).filter(
        MessageTemplate.organization_id == organization_id,
        MessageTemplate.message_track == track,
        MessageTemplate.channel == "email",
    ).first()
    if not override:
        return None
    return {"subject": override.email_subject_template or "", "body_html": override.body_template}


def upsert_template(
    db: Session,
    organization_id: str,
    track: MessageTrack,
    channel: str,
    body_template: str,
    updated_by_user_id: str,
    email_subject_template: str = None,
) -> MessageTemplate:
    """Creates or updates the org's custom template for this track+channel."""
    existing = db.query(MessageTemplate).filter(
        MessageTemplate.organization_id == organization_id,
        MessageTemplate.message_track == track,
        MessageTemplate.channel == channel,
    ).first()

    if existing:
        existing.body_template = body_template
        existing.email_subject_template = email_subject_template
        existing.updated_by_user_id = updated_by_user_id
        db.commit()
        return existing

    new_template = MessageTemplate(
        organization_id=organization_id,
        message_track=track,
        channel=channel,
        body_template=body_template,
        email_subject_template=email_subject_template,
        updated_by_user_id=updated_by_user_id,
    )
    db.add(new_template)
    db.commit()
    return new_template


def reset_template_to_default(db: Session, organization_id: str, track: MessageTrack, channel: str) -> bool:
    """Deletes the org's override, reverting to the hardcoded default."""
    existing = db.query(MessageTemplate).filter(
        MessageTemplate.organization_id == organization_id,
        MessageTemplate.message_track == track,
        MessageTemplate.channel == channel,
    ).first()
    if not existing:
        return False
    db.delete(existing)
    db.commit()
    return True


def list_all_templates_with_defaults(db: Session, organization_id: str) -> list[dict]:
    """
    Returns every track+channel combination, showing the org's override if
    one exists, or the hardcoded default text otherwise - so the editor UI
    always has something to display and edit, even on day one before any
    customization has happened.
    """
    from app.services.cadence_service import TRACK_BASE_TEMPLATES
    from app.services.email_service import EMAIL_TEMPLATES

    overrides = {
        (o.message_track, o.channel): o
        for o in db.query(MessageTemplate).filter(MessageTemplate.organization_id == organization_id).all()
    }

    results = []
    for track in MessageTrack:
        sms_override = overrides.get((track, "sms"))
        results.append({
            "message_track": track.value,
            "channel": "sms",
            "body_template": sms_override.body_template if sms_override else TRACK_BASE_TEMPLATES.get(track, ""),
            "email_subject_template": None,
            "is_customized": sms_override is not None,
        })

        email_override = overrides.get((track, "email"))
        default_email = EMAIL_TEMPLATES.get(track, {})
        results.append({
            "message_track": track.value,
            "channel": "email",
            "body_template": email_override.body_template if email_override else default_email.get("body_html", ""),
            "email_subject_template": email_override.email_subject_template if email_override else default_email.get("subject", ""),
            "is_customized": email_override is not None,
        })

    return results
