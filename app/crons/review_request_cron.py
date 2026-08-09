"""
Post-Appointment Follow-up Cron
--------------------------------
After a booked appointment time passes, automatically sends a satisfaction
survey SMS to the lead. The survey collects a 1-5 star rating + optional
comment; if they rate 4-5 stars, the thank-you page shows links to leave
a Google review and connect on social media.

Flow:
  1. Query bookings that finished > 1 hour ago, survey not yet sent
  2. Create a BookingFollowup record with a unique survey_token
  3. Send SMS with survey URL: {backend_url}/survey/{survey_token}
  4. Mark booking_links.review_request_sent_at = NOW()

Called every 30 minutes from main.py's asyncio background loop.

Requirements for a survey SMS to fire:
  - booking_links.status = 'booked'
  - booking_links.booked_time < NOW() - 1 hour
  - booking_links.review_request_sent_at IS NULL  (never sent before)
  - lead.status != 'dnc'
  - lead.phone is not null
  - advisor has Twilio credentials configured
"""

import logging
import os
import uuid
from datetime import datetime

from sqlalchemy import text

logger = logging.getLogger(__name__)

BACKEND_BASE_URL = os.getenv(
    "BOOKING_BASE_URL", "https://advisorflow-backend.onrender.com"
)

SURVEY_SMS = (
    "Hi {first_name}, thanks for choosing {org_name}! "
    "We'd love your feedback — it only takes 30 seconds: {survey_url} "
    "Reply STOP to opt out."
)


def run_review_request_cron(engine) -> int:
    """
    Scan for eligible bookings and send post-appointment survey SMS.
    Returns the number of messages sent.
    """
    from sqlalchemy.orm import Session
    from app.models.models import BookingFollowup, gen_uuid

    sent_count = 0
    with Session(engine) as db:
        rows = db.execute(text("""
            SELECT
                bl.id              AS booking_id,
                bl.lead_id,
                bl.user_id,
                l.first_name,
                l.phone,
                l.status           AS lead_status,
                l.organization_id,
                o.name             AS org_name,
                o.google_review_url,
                u.twilio_account_sid,
                u.twilio_auth_token_encrypted,
                u.twilio_phone_number,
                o.twilio_messaging_service_sid
            FROM booking_links bl
            JOIN leads l         ON l.id = bl.lead_id
            JOIN organizations o ON o.id = l.organization_id
            JOIN users u         ON u.id = bl.user_id
            WHERE bl.status = 'booked'
              AND bl.booked_time IS NOT NULL
              AND bl.booked_time < NOW() - INTERVAL '1 hour'
              AND bl.review_request_sent_at IS NULL
              AND l.status != 'dnc'
              AND l.phone IS NOT NULL
              AND u.twilio_account_sid IS NOT NULL
              AND u.twilio_auth_token_encrypted IS NOT NULL
              AND u.twilio_phone_number IS NOT NULL
        """)).fetchall()

        if not rows:
            return 0

        from twilio.rest import Client
        from app.utils.crypto import decrypt_value

        for row in rows:
            try:
                # Generate a unique survey token for this booking
                survey_token = str(uuid.uuid4())
                survey_url = f"{BACKEND_BASE_URL}/survey/{survey_token}"

                body = SURVEY_SMS.format(
                    first_name=row.first_name or "there",
                    org_name=row.org_name or "our team",
                    survey_url=survey_url,
                )

                auth_token = decrypt_value(row.twilio_auth_token_encrypted)
                client = Client(row.twilio_account_sid, auth_token)

                msg_kwargs = {"body": body, "to": row.phone}
                if row.twilio_messaging_service_sid:
                    msg_kwargs["messaging_service_sid"] = row.twilio_messaging_service_sid
                else:
                    msg_kwargs["from_"] = row.twilio_phone_number

                client.messages.create(**msg_kwargs)

                # Create BookingFollowup record so survey_router can look it up
                followup = BookingFollowup(
                    id=gen_uuid(),
                    booking_link_id=row.booking_id,
                    lead_id=row.lead_id,
                    advisor_id=row.user_id,
                    channel="sms",
                    survey_token=survey_token,
                    survey_link_sent=True,
                )
                db.add(followup)

                # Mark as sent so cron doesn't re-fire for this booking
                db.execute(
                    text("UPDATE booking_links SET review_request_sent_at = NOW() WHERE id = :id"),
                    {"id": row.booking_id},
                )
                db.commit()
                sent_count += 1
                logger.info(
                    "survey_sms sent to %s (booking %s token %s)",
                    row.phone, row.booking_id, survey_token,
                )

            except Exception as exc:
                logger.error(
                    "review_request_cron: failed for booking %s — %s",
                    row.booking_id, exc,
                )

    return sent_count
