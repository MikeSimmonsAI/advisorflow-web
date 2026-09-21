"""
Post-appointment follow-up service.

Called by the email poller cron every 2 minutes. Finds booked appointments
whose scheduled time has passed (within the last 3 hours, not yet followed
up), sends an AI-personalized thank-you message + survey link via SMS or
email, and records a BookingFollowup row so it never fires twice.

Survey URL: {BACKEND_URL}/survey/{followup.survey_token}
"""

import logging
import os
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.models import BookingFollowup, BookingLink, Lead, User, Organization
from app.services import ai_gateway
from app.services import outbound_email_gate
from app.services import send_source

logger = logging.getLogger(__name__)

# Survey links are now resolved per organization by
# app.services.public_identity. This constant sent every brand's families to
# the same Render hostname; it has no remaining use in this module.
BACKEND_URL = os.environ.get("BACKEND_URL", "")
_openai_client = None


def _get_openai():
    """Test seam only. None in production means "the gateway's own client"."""
    return _openai_client


def _get_org_name(db: Session, advisor: User) -> str:
    try:
        org = db.query(Organization).filter(Organization.id == advisor.organization_id).first()
        return org.name if org else "our organization"
    except Exception:
        return "our organization"


def _build_thank_you(lead: Lead, advisor: User, org_name: str, survey_url: str) -> str:
    """Use GPT to generate a warm, personalized thank-you SMS (under 320 chars)."""
    first = lead.first_name or "there"
    advisor_name = advisor.full_name or "Your Advisor"

    prompt = f"""Write a short, warm thank-you SMS from {advisor_name} at {org_name} to {first},
who just had an appointment with them.

Rules:
- Under 200 characters (NOT counting the survey link)
- Sound like a real person, not a form letter
- Reference that they met today / recently
- Don't be pushy or salesy
- End with: "We'd love your feedback: {survey_url}"
- NEVER use [Your Name] or any placeholder — use "{advisor_name}" directly
- Respond with ONLY the message text, no quotes, no JSON"""

    try:
        resp = ai_gateway.chat_completion(
            feature="post_appointment.thank_you", capability="post_appointment",
            mode=ai_gateway.BACKGROUND, org_id=getattr(lead, "organization_id", None),
            client=_get_openai(),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=120,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.warning("GPT thank-you generation failed: %s", e)
        return (
            f"Hi {first}, thank you for meeting with {advisor_name} today! "
            f"We truly appreciate your time. We'd love your feedback: {survey_url}"
        )


def _send_sms(advisor: User, lead: Lead, body: str) -> bool:
    """Send via Twilio. Returns True on success."""
    try:
        from twilio.rest import Client
        from app.utils.crypto import decrypt_value
        auth_token = decrypt_value(advisor.twilio_auth_token_encrypted)
        client = Client(advisor.twilio_account_sid, auth_token)
        client.messages.create(
            body=body,
            from_=advisor.twilio_phone_number,
            to=lead.phone,
        )
        return True
    except Exception as e:
        logger.error("Post-appt SMS failed lead=%s: %s", lead.id, e)
        return False


def _send_email(db: Session, advisor: User, lead: Lead, body: str, org_name: str) -> bool:
    """Run the compliance gate, then decline to send. Returns False.

    `db` was added to the signature because the gate needs it. The whole body
    of this function was previously dead: _send_email_via_graph was imported
    in the same statement as _build_email_html and _strip_signoff, so the
    ImportError fired on the import line and nothing ran. It returned False
    every time, which _send_followup faithfully recorded as
    thank_you_sent=False with error "No reachable channel or send failed".

    That means this is the ONE of the five dead paths whose historical damage
    is countable:
        SELECT count(*) FROM booking_followups
         WHERE thank_you_sent = false AND channel = 'email';
    """
    try:
        from app.services.ai_conversation_service import (
            _build_email_html, _strip_signoff
        )
        advisor_name = advisor.full_name or "Your Advisor"
        subject = f"Thank you for meeting with us, {lead.first_name or 'there'}!"
        html = _build_email_html(_strip_signoff(body), advisor_name, org_name)
        outbound_email_gate.send_lead_email(
            db, lead,
            advisor=advisor,
            subject=subject,
            body_html=html,
            send_source=send_source.APPOINTMENT_FOLLOWUP,
            actor_user_id=None,  # cron sweep: no authenticated human
        )
        # Reached only on a confirmed send. Every refusal and every provider
        # failure raises, so `_send_followup` cannot record thank_you_sent for
        # a message that did not go.
        return True
    except Exception as e:
        logger.error("Post-appt email failed lead=%s: %s", lead.id, e)
        return False


def _send_followup(db: Session, booking: BookingLink, lead: Lead, advisor: User) -> None:
    """Create BookingFollowup row and send thank-you + survey link."""
    import uuid
    survey_token = str(uuid.uuid4())
    followup = BookingFollowup(
        booking_link_id=booking.id,
        lead_id=lead.id,
        advisor_id=advisor.id,
        survey_token=survey_token,
    )
    db.add(followup)
    db.flush()  # get the id

    org_name = _get_org_name(db, advisor)
    from app.services.public_identity import survey_url as public_survey_url
    survey_url = public_survey_url(db, advisor.organization_id, survey_token)
    message = _build_thank_you(lead, advisor, org_name, survey_url)

    sent = False
    channel = "none"

    # Prefer SMS; fall back to email
    if lead.phone and advisor.twilio_phone_number and advisor.twilio_auth_token_encrypted:
        sent = _send_sms(advisor, lead, message)
        channel = "sms"

    if not sent and lead.email and advisor.microsoft_365_connected:
        sent = _send_email(db, advisor, lead, message, org_name)
        channel = "email"

    followup.channel = channel
    followup.thank_you_sent = sent
    followup.survey_link_sent = sent
    if not sent:
        followup.error = "No reachable channel or send failed"

    db.commit()
    logger.info("Post-appt followup lead=%s channel=%s sent=%s", lead.id, channel, sent)


def check_and_send_followups(db: Session) -> int:
    """
    Main entry point called by the email poller cron.
    Finds bookings that:
      - status == 'booked'
      - booked_time is in the past (appointment has happened)
      - booked_time is within the last 3 hours (don't chase super old ones)
      - no BookingFollowup row yet

    Returns the number of followups sent.

    AN AI-PERSONALISED BACKGROUND SEND. With AI_BACKGROUND_AUTOMATION_ENABLED
    off (the default) this returns 0 before it queries anything: no provider
    request, no BookingFollowup row, no SMS, no email - and in particular not
    the canned thank-you the AI-failure path would otherwise have sent.
    """
    if not ai_gateway.check_background("post_appointment_followup"):
        return 0
    now = datetime.utcnow()
    window_start = now - timedelta(hours=3)

    # Find eligible bookings
    sent_booking_ids = {
        row[0] for row in db.query(BookingFollowup.booking_link_id).all()
    }

    eligible = (
        db.query(BookingLink)
        .filter(
            BookingLink.status == "booked",
            BookingLink.booked_time <= now,
            BookingLink.booked_time >= window_start,
        )
        .all()
    )

    count = 0
    for booking in eligible:
        if booking.id in sent_booking_ids:
            continue

        lead = db.query(Lead).filter(Lead.id == booking.lead_id).first()
        advisor = db.query(User).filter(User.id == booking.user_id).first()

        if not lead or not advisor:
            continue

        try:
            _send_followup(db, booking, lead, advisor)
            count += 1
        except Exception as e:
            logger.error("Failed to send post-appt followup booking=%s: %s", booking.id, e)

    if count:
        logger.info("[post_appointment] Sent %d post-appointment followup(s).", count)

    return count
