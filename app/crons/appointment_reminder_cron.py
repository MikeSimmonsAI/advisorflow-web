"""
Appointment Reminder Cron — v3.0.0
Fires 24-hour and 1-hour reminders via SMS + email to the lead before
their booked appointment. Runs every 15 minutes from the main.py asyncio loop.

Windows:  23–25 h window  → 24hr reminder
          45–75 min window → 1hr reminder

Uses org-level Twilio (falls back to advisor-level) and org-level Resend
(falls back to RESEND_API_KEY env var) — same pattern as the booking-confirmed
confirmation block in calendar_router.py.
"""

import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ── SMS helper ────────────────────────────────────────────────────────────────

def _send_sms(to_number: str, body: str, org, advisor) -> bool:
    """Send SMS via org Twilio creds (falls back to advisor creds). Returns True on success."""
    try:
        from twilio.rest import Client as TwilioClient
        from app.utils.crypto import decrypt_value

        # Prefer org-level creds
        acct_sid = getattr(org, "org_twilio_account_sid", None)
        auth_tok_enc = getattr(org, "org_twilio_auth_token_encrypted", None)
        from_num = getattr(org, "org_twilio_phone_number", None)

        if not (acct_sid and auth_tok_enc and from_num):
            # Fall back to advisor creds
            acct_sid = getattr(advisor, "twilio_account_sid", None)
            auth_tok_enc = getattr(advisor, "twilio_auth_token_encrypted", None)
            from_num = getattr(advisor, "twilio_phone_number", None)

        if not (acct_sid and auth_tok_enc and from_num):
            logger.warning("appointment_reminder_cron: no Twilio creds for org=%s", org.id)
            return False

        auth_tok = decrypt_value(auth_tok_enc)
        client = TwilioClient(acct_sid, auth_tok)
        client.messages.create(body=body, from_=from_num, to=to_number)
        return True
    except Exception as exc:
        logger.error("appointment_reminder_cron: SMS error: %s", exc)
        return False


# ── Email helper ──────────────────────────────────────────────────────────────

def _send_email(to_email: str, subject: str, html_body: str, org, advisor) -> bool:
    """Send email via org Resend key (falls back to env var). Returns True on success."""
    try:
        import resend

        api_key = getattr(org, "resend_api_key", None) or os.environ.get("RESEND_API_KEY", "")
        from_addr = getattr(org, "from_email", None) or os.environ.get("EMAIL_FROM_ADDRESS", "noreply@bookaboost.com")
        brand = getattr(org, "name", None) or getattr(advisor, "full_name", "Your Advisor")

        if not api_key:
            logger.warning("appointment_reminder_cron: no Resend API key for org=%s", org.id)
            return False

        resend.api_key = api_key
        resend.Emails.send({
            "from": from_addr,
            "to": [to_email],
            "subject": subject,
            "html": html_body,
        })
        return True
    except Exception as exc:
        logger.error("appointment_reminder_cron: email error: %s", exc)
        return False


# ── Email templates ───────────────────────────────────────────────────────────

def _reminder_email_html(first_name: str, slot_display: str, appt_label: str, brand: str, hours: int) -> str:
    timing = "tomorrow" if hours == 24 else "in about 1 hour"
    color = "#1565c0"
    return f"""
<div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;padding:32px 24px;color:#222;">
  <h2 style="color:{color};margin-bottom:8px;">Appointment Reminder</h2>
  <p style="font-size:16px;">Hi {first_name},</p>
  <p style="font-size:16px;">
    This is a friendly reminder that your <strong>{appt_label}</strong>
    is scheduled for <strong>{slot_display}</strong> — {timing}.
  </p>
  <div style="background:#f0f4ff;border-left:4px solid {color};padding:16px 20px;
              border-radius:4px;margin:24px 0;font-size:15px;">
    <strong>When:</strong> {slot_display}<br>
    <strong>What:</strong> {appt_label}
  </div>
  <p style="font-size:14px;color:#555;">
    If you need to reschedule or have questions, please contact us as soon as possible.
  </p>
  <p style="font-size:14px;color:#888;margin-top:32px;">
    — {brand}
  </p>
</div>
"""


# ── Core reminder sender ──────────────────────────────────────────────────────

def _send_reminder(booking, org, advisor, hours: int, db) -> None:
    """Send SMS + email reminder for one booking. Updates sent flag in-place."""
    from app.models.models import Lead

    lead = db.query(Lead).filter(Lead.id == booking.lead_id).first()
    if not lead:
        return

    first_name = getattr(lead, "first_name", "") or "there"
    appt_label = getattr(booking, "appointment_type", None) or "appointment"
    brand = getattr(org, "name", None) or "Your Advisor"

    # Format slot display
    booked_time: datetime = booking.booked_time
    try:
        slot_display = booked_time.strftime("%-I:%M %p on %A, %B %-d")
    except ValueError:
        slot_display = booked_time.strftime("%I:%M %p on %A, %B %d").lstrip("0")

    timing_word = "tomorrow" if hours == 24 else "in about 1 hour"
    sms_body = (
        f"Hi {first_name}, just a reminder — your {appt_label} is {timing_word} "
        f"({slot_display}). See you then! Reply STOP to opt out. — {brand}"
    )

    lead_phone = getattr(lead, "phone", None)
    if lead_phone:
        _send_sms(lead_phone, sms_body, org, advisor)

    lead_email = getattr(lead, "email", None)
    if lead_email:
        subj = f"Reminder: Your {appt_label} is {timing_word}"
        html = _reminder_email_html(first_name, slot_display, appt_label, brand, hours)
        _send_email(lead_email, subj, html, org, advisor)

    # Mark as sent
    if hours == 24:
        booking.reminder_24hr_sent = True
    else:
        booking.reminder_1hr_sent = True


# ── Main entry point ──────────────────────────────────────────────────────────

def run_appointment_reminder_cron(engine) -> int:
    """
    Query BookingLinks with upcoming appointments and send reminders.
    Returns total number of reminders sent.
    Called every 15 minutes from main.py's asyncio loop.
    """
    from sqlalchemy.orm import Session
    from app.models.models import BookingLink, Organization, User

    sent_count = 0
    now = datetime.now(timezone.utc)

    with Session(engine) as db:
        # ── 24-hour window: booked_time between now+23h and now+25h ──────────
        window_24_start = now + timedelta(hours=23)
        window_24_end   = now + timedelta(hours=25)

        due_24 = (
            db.query(BookingLink)
            .filter(
                BookingLink.status == "booked",
                BookingLink.reminder_24hr_sent == False,
                BookingLink.booked_time >= window_24_start,
                BookingLink.booked_time <= window_24_end,
            )
            .all()
        )

        for booking in due_24:
            try:
                org = db.query(Organization).filter(
                    Organization.id == booking.organization_id
                ).first()
                advisor = db.query(User).filter(
                    User.id == booking.advisor_id
                ).first() if booking.advisor_id else None
                if org and advisor:
                    _send_reminder(booking, org, advisor, 24, db)
                    sent_count += 1
            except Exception as exc:
                logger.error("appointment_reminder_cron 24hr: booking=%s error=%s", booking.id, exc)

        # ── 1-hour window: booked_time between now+45min and now+75min ───────
        window_1h_start = now + timedelta(minutes=45)
        window_1h_end   = now + timedelta(minutes=75)

        due_1h = (
            db.query(BookingLink)
            .filter(
                BookingLink.status == "booked",
                BookingLink.reminder_1hr_sent == False,
                BookingLink.booked_time >= window_1h_start,
                BookingLink.booked_time <= window_1h_end,
            )
            .all()
        )

        for booking in due_1h:
            try:
                org = db.query(Organization).filter(
                    Organization.id == booking.organization_id
                ).first()
                advisor = db.query(User).filter(
                    User.id == booking.advisor_id
                ).first() if booking.advisor_id else None
                if org and advisor:
                    _send_reminder(booking, org, advisor, 1, db)
                    sent_count += 1
            except Exception as exc:
                logger.error("appointment_reminder_cron 1hr: booking=%s error=%s", booking.id, exc)

        try:
            db.commit()
        except Exception as exc:
            logger.error("appointment_reminder_cron: commit error: %s", exc)
            db.rollback()

    return sent_count
