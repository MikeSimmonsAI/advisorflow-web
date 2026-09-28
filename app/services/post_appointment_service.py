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


_CLAIM_MARKER = "claimed; send in progress"


def _claim_followup(db: Session, booking: BookingLink, lead: Lead,
                    advisor: User, survey_token: str):
    """Claim the booking by committing its BookingFollowup row BEFORE any send.

    Returns the committed followup, or None when this booking is already
    followed up (by another runner, or by review_request_cron).

    No unique index backs this: a booking legitimately carries more than one
    BookingFollowup row (review_request_cron writes its own survey row for the
    same booking), so UNIQUE(booking_link_id) would be wrong. Instead the
    booking_links row is locked (SELECT ... FOR UPDATE; a no-op on SQLite,
    whose writers are serialised anyway) and the "any followup yet?" check is
    re-run under that lock. A second runner blocks on the lock until the first
    commits, then its fresh re-check sees the committed row and backs off.
    """
    locked = (
        db.query(BookingLink)
        .filter(BookingLink.id == booking.id)
        .with_for_update()
        .first()
    )
    if locked is None:
        db.rollback()
        return None
    already = (
        db.query(BookingFollowup.id)
        .filter(BookingFollowup.booking_link_id == booking.id)
        .first()
    )
    if already is not None:
        db.rollback()
        return None

    followup = BookingFollowup(
        booking_link_id=booking.id,
        lead_id=lead.id,
        advisor_id=advisor.id,
        survey_token=survey_token,
        channel="none",
        thank_you_sent=False,
        survey_link_sent=False,
        # Left in place if the process dies mid-send: the row stays as a
        # permanent exclusion (at-most-once), never a resend.
        error=_CLAIM_MARKER,
    )
    db.add(followup)
    db.commit()
    return followup


def _send_followup(db: Session, booking: BookingLink, lead: Lead, advisor: User) -> bool:
    """Claim, then send thank-you + survey link, then record the outcome.

    Returns False when the booking was already claimed (nothing sent), True
    when this runner owned the claim (whether or not the send succeeded).

    Order matters. The BookingFollowup row is committed BEFORE the provider is
    called, so a commit that fails after the send can no longer roll the row
    away and let the next poll text/email the family a second time. A failed
    send is recorded on the claimed row; it is never unclaimed into a resend
    loop. Same shape as app/crons/review_request_cron.py.
    """
    import uuid
    survey_token = str(uuid.uuid4())

    # ── Phase 1: claim (no side effects outside the DB) ──
    followup = _claim_followup(db, booking, lead, advisor, survey_token)
    if followup is None:
        logger.info("Post-appt followup booking=%s already claimed; skipping", booking.id)
        return False
    followup_id = followup.id
    lead_id = lead.id

    # ── Phase 2: build and send ──
    sent = False
    channel = "none"
    send_error = None
    try:
        org_name = _get_org_name(db, advisor)
        from app.services.public_identity import survey_url as public_survey_url
        survey_url = public_survey_url(db, advisor.organization_id, survey_token)
        message = _build_thank_you(lead, advisor, org_name, survey_url)

        # Prefer SMS; fall back to email
        # A Wholesale seller is texted only by the seller SMS program, never from
        # an advisor's number here. See app/services/wholesale_sms.py.
        from app.services import wholesale_sms
        program_refused = bool(lead.phone) and bool(wholesale_sms.refusal_for_phone(
            db, lead.organization_id, lead.phone, path="post_appointment"))
        if (lead.phone and not program_refused and advisor.twilio_phone_number
                and advisor.twilio_auth_token_encrypted):
            sent = _send_sms(advisor, lead, message)
            channel = "sms"

        if not sent and lead.email and advisor.microsoft_365_connected:
            sent = _send_email(db, advisor, lead, message, org_name)
            channel = "email"
    except Exception as e:
        # The claim stays: whatever happened, this booking is not retried.
        logger.error("Post-appt followup send path failed lead=%s: %s", lead_id, e)
        send_error = ("Send path failed: %s" % e)[:500]
        try:
            db.rollback()
        except Exception:
            pass

    # ── Phase 3: record the outcome on the claimed row ──
    try:
        row = db.query(BookingFollowup).filter(BookingFollowup.id == followup_id).first()
        if row is not None:
            row.channel = channel
            row.thank_you_sent = sent
            row.survey_link_sent = sent
            if sent:
                row.error = None
            else:
                row.error = send_error or "No reachable channel or send failed"
            db.commit()
    except Exception as e:
        # The message (if any) is out and the booking is claimed; only the
        # bookkeeping is lost. Nothing will resend.
        db.rollback()
        logger.error("Post-appt followup sent=%s but could not record outcome "
                     "followup=%s: %s", sent, followup_id, e)

    logger.info("Post-appt followup lead=%s channel=%s sent=%s", lead_id, channel, sent)
    return True


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

        booking_id = booking.id
        try:
            lead = db.query(Lead).filter(Lead.id == booking.lead_id).first()
            advisor = db.query(User).filter(User.id == booking.user_id).first()

            if not lead or not advisor:
                continue

            # False means another runner already claimed this booking.
            if _send_followup(db, booking, lead, advisor) is not False:
                count += 1
        except Exception as e:
            # One bad booking must not poison the session for the rest.
            db.rollback()
            logger.error("Failed to send post-appt followup booking=%s: %s", booking_id, e)

    if count:
        logger.info("[post_appointment] Sent %d post-appointment followup(s).", count)

    return count
