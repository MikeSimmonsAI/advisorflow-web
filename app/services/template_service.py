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


def org_tracks(db: Session, organization_id: str) -> list[tuple]:
    """The message tracks THIS organization actually sends on.

    WHY NOT THE `MessageTrack` ENUM. That enum is one customer's pipeline
    frozen into the platform: pre_need_lock_price, at_need_support,
    imminent_support, upsell_existing. Listing it is why Atlantis Light & Power
    — configured as energy procurement, with its own tracks — opened Message
    Templates and was shown "Pre Need Lock Price" ("locking in today's
    pricing") and "At Need (support)" ("any arrangements you need", "in case
    your family needs support"). Not a mislabel: a funeral home's outreach copy
    presented to an energy broker as its own.

    `TierDefinition` already carries `track_key` and `track_label` PER
    ORGANIZATION, seeded from the industry template registry, and it is what
    the tier filter, the tier editor and the cadence tone lookup already read.
    This is that same answer, not a second one.

    An organization with no tier definitions yet — a legacy tenant that predates
    them — falls back to the platform enum, which is exactly what it sees today.
    Nothing is taken away from anybody.
    """
    from app.models.models import TierDefinition

    rows = (db.query(TierDefinition)
            .filter(TierDefinition.organization_id == organization_id,
                    TierDefinition.is_active == True)      # noqa: E712
            .order_by(TierDefinition.sort_order.asc())
            .all())

    seen: set = set()
    out: list = []
    for row in rows:
        key = (row.track_key or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append((key, (row.track_label or "").strip() or None))

    if out:
        return out
    return [(track.value, None) for track in MessageTrack]


# A STARTING DRAFT THAT BELONGS TO NO INDUSTRY.
#
# Only used for a track the platform has no copy for — which, before org_tracks
# above, could not happen, because the only tracks ever listed were the ones
# with funeral copy attached. The alternative to a neutral draft is an empty
# editor, and an empty editor on day one is how a customer ends up sending
# nothing at all.
#
# `OPT_OUT_SUFFIX` is imported rather than retyped: the carrier registration
# requires it and a second copy of it is a second thing to forget.
_NEUTRAL_EMAIL_SUBJECT = "{first_name}, a quick note from {org_name}"
_NEUTRAL_EMAIL_BODY = """
            <p>Hi {first_name},</p>
            <p>This is {advisor_name} with {org_name}. I wanted to reach out and
            see whether now is a good time to talk.</p>
            <p>If it is, here's my booking link:
            <a href="{booking_link}">{booking_link}</a></p>
            <p>Best,<br>{advisor_name}</p>
        """


def _neutral_sms() -> str:
    from app.services.cadence_service import OPT_OUT_SUFFIX
    return ("Hi {first_name}, this is {advisor_name} with {org_name}. "
            "{tone_phrase} I wanted to follow up and see whether now is a good "
            "time to talk." + OPT_OUT_SUFFIX)


def list_all_templates_with_defaults(db: Session, organization_id: str) -> list[dict]:
    """
    Returns every track+channel combination THIS ORGANIZATION sends on, showing
    its override if one exists, or a default otherwise - so the editor UI always
    has something to display and edit, even on day one before any customization
    has happened.
    """
    from app.services.cadence_service import TRACK_BASE_TEMPLATES
    from app.services.email_service import EMAIL_TEMPLATES

    overrides = {
        (o.message_track, o.channel): o
        for o in db.query(MessageTemplate).filter(MessageTemplate.organization_id == organization_id).all()
    }

    results = []
    for track, label in org_tracks(db, organization_id):
        sms_override = overrides.get((track, "sms"))
        results.append({
            "message_track": track,
            # The organization's own name for this track, when it has one. The
            # browser humanises the key when it does not, which is grammar and
            # works for any business; a platform-side dictionary of one
            # vertical's track names does not.
            "track_label": label,
            "channel": "sms",
            "body_template": (sms_override.body_template if sms_override
                              else TRACK_BASE_TEMPLATES.get(track) or _neutral_sms()),
            "email_subject_template": None,
            "is_customized": sms_override is not None,
        })

        email_override = overrides.get((track, "email"))
        default_email = EMAIL_TEMPLATES.get(track) or {}
        results.append({
            "message_track": track,
            "track_label": label,
            "channel": "email",
            "body_template": (email_override.body_template if email_override
                              else default_email.get("body_html") or _NEUTRAL_EMAIL_BODY),
            "email_subject_template": (email_override.email_subject_template if email_override
                                       else default_email.get("subject") or _NEUTRAL_EMAIL_SUBJECT),
            "is_customized": email_override is not None,
        })

    return results
