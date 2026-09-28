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
    """
    Scan for eligible bookings and send post-appointment survey SMS.
    Returns the number of messages sent.
    """
    from sqlalchemy.orm import Session
    from app.models.models import BookingFollowup, gen_uuid

    sent_count = 0
    with Session(engine) as db:
        rows = _eligible_rows(db)

        if not rows:
            return 0

        from twilio.rest import Client
        from app.utils.crypto import decrypt_value

        for row in rows:
            # ── Phase 1: prepare and CLAIM (no side effects outside the DB) ──
            #
            # The booking is claimed with a conditional UPDATE that only wins
            # while review_request_sent_at is still NULL, and the claim is
            # committed BEFORE the SMS goes out. Two runners cannot both win the
            # claim, and a commit that fails after a send can no longer leave
            # the booking unmarked for the next run to text the family again.
            # The BookingFollowup row rides in the same commit (survey token
            # must exist before the link reaches the family) and is flipped to
            # survey_link_sent=True only after the provider accepts the send.
            try:
                survey_token = str(uuid.uuid4())
                # Branded per organization. The query already selects
                # l.organization_id, so the row carries everything the
                # resolver needs - the old constant sent every brand's
                # families to the same Render hostname.
                from app.services.public_identity import (
                    survey_url as public_survey_url)
                survey_url = public_survey_url(db, row.organization_id,
                                               survey_token)

                body = SURVEY_SMS.format(
                    first_name=row.first_name or "there",
                    org_name=row.org_name or "our team",
                    survey_url=survey_url,
                )

                auth_token = decrypt_value(row.twilio_auth_token_encrypted)

                msg_kwargs = {"body": body, "to": row.phone}
                if row.twilio_messaging_service_sid:
                    msg_kwargs["messaging_service_sid"] = row.twilio_messaging_service_sid
                else:
                    msg_kwargs["from_"] = row.twilio_phone_number

                from app.services.sms_content_policy import enforce_sms_content_policy
                # The survey URL is still a URL to a carrier filter.
                msg_kwargs["body"] = enforce_sms_content_policy(msg_kwargs["body"])
                # A Wholesale seller is texted only by the seller SMS program,
                # never from here. See app/services/wholesale_sms.py.
                from app.services import wholesale_sms
                if wholesale_sms.refusal_for_phone(db, row.organization_id, row.phone,
                                                   path="review_request"):
                    continue

                claim = db.execute(
                    text(
                        "UPDATE booking_links SET review_request_sent_at = :now "
                        "WHERE id = :id AND review_request_sent_at IS NULL"
                    ),
                    {"id": row.booking_id, "now": datetime.utcnow()},
                )
                if claim.rowcount != 1:
                    # Another runner claimed it first (or it was already sent).
                    db.rollback()
                    continue

                # Create BookingFollowup record so survey_router can look it up
                followup = BookingFollowup(
                    id=gen_uuid(),
                    booking_link_id=row.booking_id,
                    lead_id=row.lead_id,
                    advisor_id=row.user_id,
                    channel="sms",
                    survey_token=survey_token,
                    survey_link_sent=False,
                )
                db.add(followup)
                db.commit()
            except Exception as exc:
                # One bad row must not poison the shared session for the rest.
                db.rollback()
                logger.error(
                    "review_request_cron: failed to claim booking %s — %s",
                    row.booking_id, exc,
                )
                continue

            # ── Phase 2: send. The claim stays either way - a failed send is
            # recorded on the followup, never unclaimed into a resend loop. ──
            try:
                client = Client(row.twilio_account_sid, auth_token)
                client.messages.create(**msg_kwargs)
            except Exception as exc:
                logger.error(
                    "review_request_cron: send failed for booking %s — %s",
                    row.booking_id, exc,
                )
                try:
                    followup.error = str(exc)[:500]
                    db.commit()
                except Exception as rec_exc:
                    db.rollback()
                    logger.error(
                        "review_request_cron: could not record send failure "
                        "for booking %s — %s", row.booking_id, rec_exc,
                    )
                continue

            sent_count += 1
            logger.info(
                "survey_sms sent to %s (booking %s token %s)",
                row.phone, row.booking_id, survey_token,
            )
            try:
                followup.survey_link_sent = True
                db.commit()
            except Exception as exc:
                # The SMS is out and the booking is claimed; only the
                # bookkeeping flag is lost. Nothing will resend.
                db.rollback()
                logger.error(
                    "review_request_cron: sent but could not flag followup "
                    "for booking %s — %s", row.booking_id, exc,
                )

    return sent_count


def _eligible_rows(db):
    """Bookings due a survey SMS. Split out so the send loop can be tested
    without Postgres-only SQL (NOW() - INTERVAL)."""
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
              AND u.twilio_account_sid IS NOT NULL
              AND u.twilio_auth_token_encrypted IS NOT NULL
              AND u.twilio_phone_number IS NOT NULL
        """)).fetchall()
