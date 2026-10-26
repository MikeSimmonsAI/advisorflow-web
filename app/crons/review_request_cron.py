"""
Post-Appointment Follow-up Cron  (#405: now a thin caller of survey_dispatch)
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

from sqlalchemy import text

logger = logging.getLogger(__name__)

_REMOVED_BACKEND_BASE_URL = (
    # Was: os.getenv("BOOKING_BASE_URL", "https://advisorflow-backend.onrender.com")
    # One hostname for every brand, sent to every family in a survey SMS.
    # Survey links now come from app.services.public_identity, resolved per
    # organization. Left as a named tombstone rather than deleted so the next
    # person to look for BACKEND_BASE_URL finds the reason instead of silence.
    None
)

SURVEY_SMS = (
    "Hi {first_name}, thanks for choosing {org_name}! "
    "We'd love your feedback — it only takes 30 seconds: {survey_url} "
    "Reply STOP to opt out."
)


def run_review_request_cron(engine) -> int:
    """#405: the ONE survey path is app/services/survey_dispatch.py.

    This job used to send its own survey texts - straight to Twilio for most
    organizations (no message history, no STOP/suppression gate) and compared
    booked_time (local wall time) with NOW() in UTC, so surveys arrived hours
    BEFORE the visit. It now only runs that one path. Returns surveys sent."""
    from app.services import survey_dispatch
    return survey_dispatch.run_with_engine(engine)


def _eligible_rows(db):  # pragma: no cover - retired (#405); kept for reference only
    """RETIRED (#405): compared local booked_time with UTC NOW(). Not called."""
    return db.execute(text("""
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
              AND (
                    (u.twilio_account_sid IS NOT NULL
                     AND u.twilio_auth_token_encrypted IS NOT NULL
                     AND u.twilio_phone_number IS NOT NULL)
                    OR EXISTS (SELECT 1 FROM outreach_programs op
                               WHERE op.organization_id = l.organization_id
                                 AND op.is_active = TRUE)
              )
        """)).fetchall()
