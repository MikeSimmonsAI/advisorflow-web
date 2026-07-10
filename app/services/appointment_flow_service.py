"""
Appointment Flow Service
Fires automated messages at each stage of the appointment lifecycle:
  - On booking confirmed → lead gets confirmation SMS + email; advisor gets SMS alert
  - 24 hours before    → lead gets reminder SMS
  - 2 hours before     → lead gets final reminder SMS
  - On cancellation    → lead gets cancellation SMS; cadence reopened
"""

import os
import logging
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from app.models.models import Lead, User, BookingLink

logger = logging.getLogger(__name__)

BOOKING_BASE_URL = os.environ.get("BOOKING_BASE_URL", "https://advisorflow-booking.vercel.app")


def _send_sms_safe(advisor: User, to_phone: str, body: str) -> None:
    """Send an SMS, swallowing errors so flow never breaks on a messaging failure."""
    try:
        from twilio.rest import Client
        from app.utils.crypto import decrypt_value
        if not advisor.twilio_account_sid or not advisor.twilio_auth_token_encrypted:
            logger.warning("Advisor %s has no Twilio credentials — skipping SMS", advisor.id)
            return
        auth_token = decrypt_value(advisor.twilio_auth_token_encrypted)
        client = Client(advisor.twilio_account_sid, auth_token)
        client.messages.create(body=body, from_=advisor.twilio_phone_number, to=to_phone)
    except Exception as e:
        logger.error("appointment_flow SMS failed: %s", e)


def _send_email_safe(to_email: str, subject: str, body_html: str) -> None:
    """Send an email, swallowing errors."""
    try:
        from app.services.email_service import send_email_via_provider
        send_email_via_provider(to_email, subject, body_html)
    except Exception as e:
        logger.error("appointment_flow email failed: %s", e)


def on_booking_confirmed(db: Session, lead: Lead, advisor: User, booking_link: BookingLink) -> None:
    """
    Fires immediately when a lead books an appointment.
    1. Sends lead a confirmation SMS
    2. Sends lead a confirmation email (if they have one)
    3. Sends advisor an SMS alert
    """
    appt_time = booking_link.booked_time.strftime("%A, %B %d at %I:%M %p") if booking_link.booked_time else "your scheduled time"
    lead_name = f"{lead.first_name or ''} {lead.last_name or ''}".strip() or "there"
    advisor_name = advisor.full_name or "your advisor"
    org_name = "Restland Cemetery & Funeral Home"  # TODO: pull from org

    # 1. Confirmation SMS to lead
    if lead.phone:
        body = (
            f"Hi {lead.first_name or lead_name}, your appointment with {advisor_name} at {org_name} "
            f"is confirmed for {appt_time}. Reply STOP to opt out."
        )
        _send_sms_safe(advisor, lead.phone, body)

    # 2. Confirmation email to lead
    if lead.email:
        subject = f"Your appointment is confirmed — {appt_time}"
        body_html = f"""
        <div style="font-family: sans-serif; max-width: 520px; margin: 0 auto; padding: 24px;">
          <h2 style="color: #1a1a2e;">Appointment Confirmed ✓</h2>
          <p>Hi {lead_name},</p>
          <p>Your appointment with <strong>{advisor_name}</strong> at <strong>{org_name}</strong> is confirmed for:</p>
          <div style="background: #f5f5f5; border-left: 4px solid #2fb6ff; padding: 16px; margin: 16px 0; font-size: 18px; font-weight: bold;">
            {appt_time}
          </div>
          <p>If you need to reschedule or have questions, reply to this email or contact us directly.</p>
          <p style="color: #666; font-size: 12px;">— {advisor_name}, {org_name}</p>
        </div>
        """
        _send_email_safe(lead.email, subject, body_html)

    # 3. Alert SMS to advisor
    if advisor.twilio_phone_number:
        alert = f"📅 BookaBoost: {lead_name} just booked for {appt_time}. Check your calendar."
        notification_phone = getattr(advisor, "notification_phone", None) or advisor.twilio_phone_number
        _send_sms_safe(advisor, notification_phone, alert)


def on_booking_cancelled(db: Session, lead: Lead, advisor: User, booking_link: BookingLink) -> None:
    """
    Fires when a booking is cancelled.
    Sends lead a cancellation notice and reopens their cadence.
    """
    lead_name = f"{lead.first_name or ''} {lead.last_name or ''}".strip() or "there"
    advisor_name = advisor.full_name or "your advisor"

    if lead.phone:
        body = (
            f"Hi {lead.first_name or lead_name}, your appointment with {advisor_name} has been cancelled. "
            f"Reply or call us to reschedule."
        )
        _send_sms_safe(advisor, lead.phone, body)

    # Reopen cadence if it was paused/stopped due to booking
    try:
        from app.models.models import CadenceState, CadenceStatus
        cadence = db.query(CadenceState).filter(CadenceState.lead_id == lead.id).first()
        if cadence and cadence.status in ("completed", "booked"):
            cadence.status = "active"
            cadence.next_touch_due_at = datetime.utcnow() + timedelta(days=1)
            db.commit()
    except Exception as e:
        logger.error("appointment_flow cadence reopen failed: %s", e)


def send_appointment_reminders(db: Session) -> dict:
    """
    Placeholder — reminder columns (appointment_at, reminder_24h_sent, reminder_2h_sent)
    need a DB migration before this can run. Returns 0 sent safely.
    """
    return {"reminders_24h_sent": 0, "reminders_2h_sent": 0, "note": "Pending DB migration for reminder columns"}
