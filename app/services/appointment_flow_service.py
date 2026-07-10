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
    appt_time = booking_link.appointment_at.strftime("%A, %B %d at %I:%M %p") if booking_link.appointment_at else "your scheduled time"
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
    Call this from a cron job or the /calendar/send-reminders endpoint.
    Finds bookings due in ~24h and ~2h and sends reminder SMS to leads.
    """
    from app.models.models import BookingLink
    now = datetime.utcnow()
    window_24h_start = now + timedelta(hours=23)
    window_24h_end = now + timedelta(hours=25)
    window_2h_start = now + timedelta(hours=1, minutes=45)
    window_2h_end = now + timedelta(hours=2, minutes=15)

    sent_24h = 0
    sent_2h = 0

    # 24-hour reminders
    bookings_24h = db.query(BookingLink).filter(
        BookingLink.status == "confirmed",
        BookingLink.appointment_at >= window_24h_start,
        BookingLink.appointment_at <= window_24h_end,
        BookingLink.reminder_24h_sent == False,
    ).all()

    for booking in bookings_24h:
        lead = db.query(Lead).filter(Lead.id == booking.lead_id).first()
        advisor = db.query(User).filter(User.id == booking.user_id).first()
        if lead and advisor and lead.phone:
            appt_time = booking.appointment_at.strftime("%A at %I:%M %p")
            body = f"Hi {lead.first_name or 'there'}, reminder: you have an appointment tomorrow {appt_time}. Reply to reschedule."
            _send_sms_safe(advisor, lead.phone, body)
            booking.reminder_24h_sent = True
            sent_24h += 1

    # 2-hour reminders
    bookings_2h = db.query(BookingLink).filter(
        BookingLink.status == "confirmed",
        BookingLink.appointment_at >= window_2h_start,
        BookingLink.appointment_at <= window_2h_end,
        BookingLink.reminder_2h_sent == False,
    ).all()

    for booking in bookings_2h:
        lead = db.query(Lead).filter(Lead.id == booking.lead_id).first()
        advisor = db.query(User).filter(User.id == booking.user_id).first()
        if lead and advisor and lead.phone:
            appt_time = booking.appointment_at.strftime("%I:%M %p")
            body = f"Hi {lead.first_name or 'there'}, your appointment is in about 2 hours at {appt_time}. See you soon!"
            _send_sms_safe(advisor, lead.phone, body)
            booking.reminder_2h_sent = True
            sent_2h += 1

    db.commit()
    return {"reminders_24h_sent": sent_24h, "reminders_2h_sent": sent_2h}
