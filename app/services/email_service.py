"""
Email Outreach Service
For leads with contact_channel="email_only" (no phone in the source
data) - these can't go through the SMS cadence, so they get a separate
nurture flow via email instead.

Uses SendGrid by default (simple, generous free tier - 100 emails/day
free, good fit for a 5-advisor proof of concept). Swap the send_email()
internals for AWS SES or another provider later without touching the
calling code, since everything routes through this one function.

Per Mike's June 19 2026 correction: email-only leads are NOT excluded.
They get imported and queued here, even though full content per track
isn't fully fleshed out yet - Phase 2 ships the pipe, Phase 3 refines
the actual email copy per track.
"""

import os
from sqlalchemy.orm import Session
from app.models.models import Lead, User, EmailMessage, MessageTrack

SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")
FROM_EMAIL = os.environ.get("EMAIL_FROM_ADDRESS", "noreply@restland-advisorflow.com")

# One subject+body template per track, matching the same tier-based
# message-track logic used for SMS, so email-only leads still get the
# right OFFER for their tier rather than a generic blast.
EMAIL_TEMPLATES = {
    "pre_need_lock_price": {
        "subject": "Lock in today's pricing - {first_name}, let's talk",
        "body_html": """
            <p>Hi {first_name},</p>
            <p>This is {advisor_name} with Restland Cemetery & Funeral Home. I wanted to reach out
            because planning ahead lets you lock in today's pricing on cemetery and funeral
            arrangements, before future increases.</p>
            <p>If you'd like to learn more or book a time to talk, here's my booking link:
            <a href="{booking_link}">{booking_link}</a></p>
            <p>Best,<br>{advisor_name}</p>
        """,
    },
    "at_need_support": {
        "subject": "{first_name}, I'm here to help",
        "body_html": """
            <p>Hi {first_name},</p>
            <p>This is {advisor_name} with Restland. I wanted to reach out in case your family
            needs support with arrangements right now. I'm happy to help walk through your options
            whenever is convenient.</p>
            <p>You can reach me directly at {advisor_cell}, or book a time here:
            <a href="{booking_link}">{booking_link}</a></p>
            <p>Best,<br>{advisor_name}</p>
        """,
    },
    "imminent_support": {
        "subject": "{first_name}, please reach out",
        "body_html": """
            <p>Hi {first_name},</p>
            <p>This is {advisor_name} with Restland. Please don't hesitate to call me directly
            at {advisor_cell} - I want to make sure your family has the support you need right now.</p>
            <p>Best,<br>{advisor_name}</p>
        """,
    },
    "upsell_existing": {
        "subject": "Additional options for your family, {first_name}",
        "body_html": """
            <p>Hi {first_name},</p>
            <p>This is {advisor_name} with Restland. Since you've already worked with us, I wanted
            to let you know about additional options available - memorials, markers, and added plots
            or services for your family.</p>
            <p>Let's talk: <a href="{booking_link}">{booking_link}</a></p>
            <p>Best,<br>{advisor_name}</p>
        """,
    },
    "email_only_nurture": {
        "subject": "{first_name}, a quick note from Restland",
        "body_html": """
            <p>Hi {first_name},</p>
            <p>This is {advisor_name} with Restland Cemetery & Funeral Home. I wanted to introduce
            myself and let you know I'm available if you ever have questions about cemetery or
            funeral planning - no pressure, just here when you need me.</p>
            <p>Feel free to reach out: <a href="{booking_link}">{booking_link}</a></p>
            <p>Best,<br>{advisor_name}</p>
        """,
    },
    "new_inquiry_intro": {
        "subject": "Hi {first_name}, a note from Restland",
        "body_html": """
            <p>Hi {first_name},</p>
            <p>My name is {advisor_name} with Restland Cemetery & Funeral Home here in the Dallas
            area. I noticed you'd shown some interest in learning more, so I wanted to reach out
            directly and introduce myself.</p>
            <p>There's no obligation here - I'm just available if and when you'd like to talk
            through options or have any questions, whenever that might be.</p>
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
    }
    subject = template["subject"]
    body = template["body_html"]
    for key, val in subs.items():
        subject = subject.replace(key, val)
        body = body.replace(key, val)
    return {"subject": subject, "body_html": body}


def send_email_via_provider(to_email: str, subject: str, body_html: str, attachments: list = None) -> dict:
    """
    Sends via SendGrid. Returns {"success": bool, "provider_message_id": str|None, "error": str|None}.
    attachments: list of dicts with keys: filename, content (base64 string), content_type
    """
    if not SENDGRID_API_KEY:
        return {"success": False, "provider_message_id": None, "error": "SENDGRID_API_KEY not configured"}

    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail, Attachment, FileContent, FileName, FileType, Disposition

        sg = sendgrid.SendGridAPIClient(api_key=SENDGRID_API_KEY)
        message = Mail(from_email=FROM_EMAIL, to_emails=to_email, subject=subject, html_content=body_html)

        if attachments:
            for att in attachments:
                attachment = Attachment(
                    FileContent(att["content"]),
                    FileName(att["filename"]),
                    FileType(att.get("content_type", "application/octet-stream")),
                    Disposition("attachment"),
                )
                message.add_attachment(attachment)

        response = sg.send(message)
        message_id = response.headers.get("X-Message-Id") if hasattr(response, "headers") else None
        return {"success": response.status_code in (200, 201, 202), "provider_message_id": message_id, "error": None}
    except Exception as e:
        return {"success": False, "provider_message_id": None, "error": str(e)}


def send_email_to_lead(db: Session, advisor: User, lead: Lead) -> EmailMessage:
    """Sends one email to a lead and logs it. Raises ValueError if the lead has no email."""
    if not lead.email:
        raise ValueError(f"Lead {lead.id} has no email address.")

    from app.services.sms_service import create_booking_link
    import os as _os
    booking = create_booking_link(db, lead, advisor)
    booking_url = f"{_os.environ.get('BOOKING_BASE_URL', '')}/book/{booking.token}"

    track = lead.message_track or "email_only_nurture"
    rendered = render_email(db, track, lead, advisor, booking_url)

    # Provider selection: send through the advisor's real Microsoft 365
    # mailbox if they've connected it (per Mike's explicit request - real
    # company email, not a generic SendGrid sender), falling back to the
    # shared SendGrid sender for advisors who haven't connected Microsoft
    # 365 yet. This is what actually makes the Microsoft integration
    # usable - the OAuth flow alone does nothing if nothing ever calls it.
    if advisor.microsoft_365_connected:
        from app.services.microsoft_email_service import send_email_via_microsoft_graph
        result = send_email_via_microsoft_graph(advisor, lead.email, rendered["subject"], rendered["body_html"])
    else:
        result = send_email_via_provider(lead.email, rendered["subject"], rendered["body_html"])

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
