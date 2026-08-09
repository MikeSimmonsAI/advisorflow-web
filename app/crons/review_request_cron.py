"""
Review Request SMS Cron
-----------------------
After a booked appointment time passes, automatically sends a one-time SMS
asking the lead to leave a Google review. Fires from the advisor's own
Twilio number (same as all outbound SMS in this system).

Requirements for a review request to fire:
  - booking_links.status = 'booked'
  - booking_links.booked_time < NOW() - 1 hour  (give the meeting room to finish)
  - booking_links.review_request_sent_at IS NULL  (never sent before)
  - organizations.google_review_url is set        (org admin configured it)
  - lead.status != 'dnc'                          (compliance)
  - lead.phone is not null
  - advisor has Twilio credentials configured

Called from main.py's on_startup asyncio background loop every 30 minutes.
"""

import logging
from datetime import datetime

from sqlalchemy import text

logger = logging.getLogger(__name__)

REVIEW_SMS = (
    "Hi {first_name}, thanks for meeting with us at {org_name}! "
    "We'd really appreciate a quick Google review — it only takes a minute "
    "and means the world to our team: {review_url} "
    "Reply STOP to opt out."
)


def run_review_request_cron(engine) -> int:
    """
    Scan for eligible bookings and send review request SMS.
    Returns the number of messages sent.
    """
    from sqlalchemy.orm import Session

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
              AND o.google_review_url IS NOT NULL
              AND o.google_review_url != ''
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
                body = REVIEW_SMS.format(
                    first_name=row.first_name or "there",
                    org_name=row.org_name or "our team",
                    review_url=row.google_review_url,
                )

                auth_token = decrypt_value(row.twilio_auth_token_encrypted)
                client = Client(row.twilio_account_sid, auth_token)

                msg_kwargs = {
                    "body": body,
                    "to": row.phone,
                }
                # Prefer messaging service SID for A2P compliance if available
                if row.twilio_messaging_service_sid:
                    msg_kwargs["messaging_service_sid"] = row.twilio_messaging_service_sid
                else:
                    msg_kwargs["from_"] = row.twilio_phone_number

                client.messages.create(**msg_kwargs)

                db.execute(
                    text("UPDATE booking_links SET review_request_sent_at = NOW() WHERE id = :id"),
                    {"id": row.booking_id},
                )
                db.commit()
                sent_count += 1
                logger.info(
                    "review_request_cron: sent to %s (booking %s)",
                    row.phone, row.booking_id,
                )

            except Exception as exc:
                logger.error(
                    "review_request_cron: failed for booking %s — %s",
                    row.booking_id, exc,
                )

    return sent_count
