"""
Email Outreach Service
For leads with contact_channel="email_only" (no phone in the source
data) - these can't go through the SMS cadence, so they get a separate
nurture flow via email instead.

Uses Resend by default (simple, generous free tier - 100 emails/day
free, good fit for a 5-advisor proof of concept). Swap the send_email()
internals for AWS SES or another provider later without touching the
calling code, since everything routes through this one function.

Per Mike's June 19 2026 correction: email-only leads are NOT excluded.
They get imported and queued here, even though full content per track
isn't fully fleshed out yet - Phase 2 ships the pipe, Phase 3 refines
the actual email copy per track.

Org-level sender (Task 108): each Organization can store its own
from_email and resend_api_key. When set, send_email_via_provider()
uses those instead of the global env vars, so BookaBoost sends from
support@bookaboost.live and EvoSys Pro from support@evosyspro.live —
each from their own verified Resend domain, no cross-contamination.
"""

import os
from sqlalchemy.orm import Session
from app.models.models import Lead, User, EmailMessage, MessageTrack

RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
FROM_EMAIL = os.environ.get("EMAIL_FROM_ADDRESS", "noreply@bookaboost.com")

# One subject+body template per track, matching the same tier-based
# message-track logic used for SMS, so email-only leads still get the
# right OFFER for their tier rather than a generic blast.
EMAIL_TEMPLATES = {
    "pre_need_lock_price": {
        "subject": "Lock in today's pricing - {first_name}, let's talk",
        "body_html": """
            <p>Hi {first_name},</p>
            <p>This is {advisor_name} with {org_name}. I wanted to reach out
            because planning ahead lets you lock in today's pricing before future increases.</p>
            <p>If you'd like to learn more or book a time to talk, here's my booking link:
            <a href="{booking_link}">{booking_link}</a></p>
            <p>Best,<br>{advisor_name}</p>
        """,
    },
    "at_need_support": {
        "subject": "{first_name}, I'm here to help",
        "body_html": """
            <p>Hi {first_name},</p>
            <p>This is {advisor_name} with {org_name}. I wanted to reach out in case your family
            needs support right now. I'm happy to help walk through your options whenever is convenient.</p>
            <p>You can reach me directly at {advisor_cell}, or book a time here:
            <a href="{booking_link}">{booking_link}</a></p>
            <p>Best,<br>{advisor_name}</p>
        """,
    },
    "imminent_support": {
        "subject": "{first_name}, please reach out",
        "body_html": """
            <p>Hi {first_name},</p>
            <p>This is {advisor_name} with {org_name}. Please don't hesitate to call me directly
            at {advisor_cell} - I want to make sure your family has the support you need right now.</p>
            <p>Best,<br>{advisor_name}</p>
        """,
    },
    "upsell_existing": {
        "subject": "Additional options for your family, {first_name}",
        "body_html": """
            <p>Hi {first_name},</p>
            <p>This is {advisor_name} with {org_name}. I wanted to let you know about additional
            options available for your family.</p>
            <p>Let's talk: <a href="{booking_link}">{booking_link}</a></p>
            <p>Best,<br>{advisor_name}</p>
        """,
    },
    "email_only_nurture": {
        "subject": "{first_name}, a quick note from {org_name}",
        "body_html": """
            <p>Hi {first_name},</p>
            <p>This is {advisor_name} with {org_name}. I wanted to introduce myself and let you
            know I'm available if you ever have questions - no pressure, just here when you need me.</p>
            <p>Feel free to reach out: <a href="{booking_link}">{booking_link}</a></p>
            <p>Best,<br>{advisor_name}</p>
        """,
    },
    "new_inquiry_intro": {
        "subject": "Hi {first_name}, a note from {org_name}",
        "body_html": """
            <p>Hi {first_name},</p>
            <p>My name is {advisor_name} with {org_name}. I noticed you'd shown some interest
            in learning more, so I wanted to reach out and introduce myself.</p>
            <p>There's no obligation here - I'm just available if and when you'd like to talk
            through options or have any questions.</p>
            <p>You're welcome to reach out anytime: <a href="{booking_link}">{booking_link}</a></p>
            <p>Best,<br>{advisor_name}</p>
        """,
    },
}


def render_email(db, track: MessageTrack, lead: Lead, advisor: User, booking_url: str) -> dict:
    """
    Checks for an org-customized email template first; falls back to the
    hardcoded default if the org hasn't customized this track. Mirrors
    the same override pattern used for SMS in cadence_service.py.
    """
    from app.services.template_service import get_email_template
    from app.models.models import Organization
    org = db.query(Organization).filter_by(id=lead.organization_id).first()
    org_name = (org.brand_name or org.name) if org else "our team"

    custom = get_email_template(db, lead.organization_id, track)
    if custom:
        template = {"subject": custom["subject"], "body_html": custom["body_html"]}
    else:
        template = EMAIL_TEMPLATES.get(track, EMAIL_TEMPLATES["email_only_nurture"])

    subs = {
        "{first_name}": lead.first_name or "there",
        "{advisor_name}": advisor.full_name,
        "{advisor_cell}": advisor.twilio_phone_number or "",
        "{booking_link}": booking_url,
        "{org_name}": org_name,
    }
    subject = template["subject"]
    body = template["body_html"]
    for key, val in subs.items():
        subject = subject.replace(key, val)
        body = body.replace(key, val)
    return {"subject": subject, "body_html": body}


def send_email_via_provider(
    to_email: str,
    subject: str,
    body_html: str,
    attachments: list = None,
    org=None,
) -> dict:
    """
    Sends via Resend. Returns {"success": bool, "provider_message_id": str|None, "error": str|None}.
    attachments: list of dicts with keys: filename, content (base64 string), content_type

    org: optional Organization object — when provided and the org has its own
    resend_api_key / from_email set, those override the global env vars so each
    brand sends from its own verified domain. Falls back gracefully to the global
    env vars when org fields are not set (e.g. during system-check calls).
    """
    # Resolve which API key and from address to use — org-level beats env var.
    api_key = (getattr(org, "resend_api_key", None) or RESEND_API_KEY) if org else RESEND_API_KEY
    from_addr = (getattr(org, "from_email", None) or FROM_EMAIL) if org else FROM_EMAIL

    if not api_key:
        return {"success": False, "provider_message_id": None, "error": "RESEND_API_KEY not configured"}

    try:
        import resend
        resend.api_key = api_key

        params = {
            "from": from_addr,
            "to": [to_email],
            "subject": subject,
            "html": body_html,
        }

        if attachments:
            params["attachments"] = [
                {"filename": att["filename"], "content": att["content"], "content_type": att.get("content_type", "application/octet-stream")}
                for att in attachments
            ]

        response = resend.emails.send(params)
        message_id = response.get("id") if isinstance(response, dict) else getattr(response, "id", None)
        return {"success": True, "provider_message_id": message_id, "error": None}
    except Exception as e:
        return {"success": False, "provider_message_id": None, "error": str(e)}


def send_email_to_lead(db: Session, advisor: User, lead: Lead) -> EmailMessage:
    """Sends one email to a lead and logs it. Raises ValueError if the lead has no email."""
    if not lead.email:
        raise ValueError(f"Lead {lead.id} has no email address.")

    from app.services.sms_service import create_booking_link
    from app.models.models import Organization
    import os as _os

    booking = create_booking_link(db, lead, advisor)
    booking_url = f"{_os.environ.get('BOOKING_BASE_URL', '')}/book/{booking.token}"

    track = lead.message_track or "email_only_nurture"
    rendered = render_email(db, track, lead, advisor, booking_url)

    # Look up the org so we can pass it to the provider (org-level sender).
    org = db.query(Organization).filter_by(id=lead.organization_id).first()

    # Provider selection: Resend using the org's own API key + from address when
    # configured; falls back to global env vars for orgs that haven't set them yet.
    # Microsoft 365 per-advisor sending is no longer the primary path — it hit
    # anti-spam quota limits (WASCL RefuseQuota) during bulk sends. Resend via the
    # org's verified domain is cleaner, more reliable, and scales properly.
    result = send_email_via_provider(lead.email, rendered["subject"], rendered["body_html"], org=org)

    email_msg = EmailMessage(
        lead_id=lead.id,
        sender_id=advisor.id,
        subject=rendered["subject"],
        body_html=rendered["body_html"],
        provider_message_id=result.get("provider_message_id"),
        status="sent" if result["success"] else "failed",
    )
    db.add(email_msg)

    if result["success"]:
        lead.status = "sent"
    db.commit()
    return email_msg


def send_email_batch(db: Session, advisor: User, leads: list[Lead]) -> dict:
    """Sends to a batch of email-only leads, skipping any without an email."""
    sent, failed, skipped = [], [], []
    for lead in leads:
        if not lead.email:
            skipped.append(lead.id)
            continue
        try:
            msg = send_email_to_lead(db, advisor, lead)
            (sent if msg.status == "sent" else failed).append(lead.id)
        except Exception:
            failed.append(lead.id)
    return {"sent_count": len(sent), "failed_count": len(failed), "skipped_count": len(skipped)}
