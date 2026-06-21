"""
Google Calendar Booking Sync
Per Mike's Phase 1 priority list: "auto calendar booking from reply
detection with Google Calendar integration."

Flow:
  1. Advisor connects their Google Calendar once via OAuth (connect_google_calendar
     starts the flow, oauth_callback completes it and stores an encrypted
     refresh token on the User record).
  2. When a lead books a slot via the stateless booking link (the existing
     advisorflow-booking.vercel.app flow), this service's
     create_calendar_event_for_booking() is called to actually put the
     appointment on the advisor's calendar.
  3. The BookingLink.calendar_event_id field stores the resulting Google
     Calendar event ID so it can be looked up/cancelled later.

IMPORTANT: this requires a Google Cloud OAuth client (Client ID + Secret)
set up in Google Cloud Console under Mike's own Google account or
NSMG's, with the Calendar API enabled and the redirect URI pointed at
this backend's /calendar/oauth/callback route. That setup step needs to
happen in Mike's Google Cloud Console - it's not something achievable
purely from code, similar to the Twilio CNAM/Trust Hub registration.
"""

import os
import json
from datetime import datetime, timedelta
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from sqlalchemy.orm import Session
from app.models.models import User, BookingLink, Lead
from app.utils.crypto import encrypt_value, decrypt_value

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", "https://<your-domain>/calendar/oauth/callback")
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

DEFAULT_APPOINTMENT_DURATION_MINUTES = 30


def get_oauth_flow() -> Flow:
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise RuntimeError("GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET not configured.")
    client_config = {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [GOOGLE_REDIRECT_URI],
        }
    }
    return Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=GOOGLE_REDIRECT_URI)


def get_authorization_url(advisor_user_id: str) -> str:
    """
    Step 1 of OAuth: returns the URL the advisor visits to grant calendar
    access. `state` carries the advisor's user_id so the callback knows
    whose account to attach the token to.
    """
    flow = get_oauth_flow()
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",  # force refresh_token issuance even on repeat connects
        state=advisor_user_id,
    )
    return auth_url


def handle_oauth_callback(db: Session, advisor_user_id: str, authorization_response_url: str) -> User:
    """
    Step 2: Google redirects back here with a code. Exchange it for tokens,
    store the refresh token encrypted on the advisor's User record.
    """
    flow = get_oauth_flow()
    flow.fetch_token(authorization_response=authorization_response_url)
    creds = flow.credentials

    advisor = db.query(User).filter(User.id == advisor_user_id).first()
    if not advisor:
        raise ValueError("Advisor not found")

    advisor.google_oauth_refresh_token_encrypted = encrypt_value(creds.refresh_token)
    advisor.google_calendar_id = "primary"
    advisor.google_calendar_connected = True
    db.commit()
    return advisor


def _get_calendar_service(advisor: User):
    if not advisor.google_oauth_refresh_token_encrypted:
        raise ValueError(f"Advisor {advisor.full_name} has not connected Google Calendar.")

    refresh_token = decrypt_value(advisor.google_oauth_refresh_token_encrypted)
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=SCOPES,
    )
    return build("calendar", "v3", credentials=creds)


def create_calendar_event_for_booking(
    db: Session,
    booking: BookingLink,
    booked_datetime: datetime,
    duration_minutes: int = DEFAULT_APPOINTMENT_DURATION_MINUTES,
) -> dict:
    """
    Called once a lead picks a time via the booking link. Creates the
    actual Google Calendar event on the advisor's calendar and stores the
    event ID on the BookingLink for later lookup/cancellation.
    """
    advisor = db.query(User).filter(User.id == booking.user_id).first()
    lead = db.query(Lead).filter(Lead.id == booking.lead_id).first()

    if not advisor.google_calendar_connected:
        return {"success": False, "error": "Advisor has not connected Google Calendar yet."}

    service = _get_calendar_service(advisor)
    end_time = booked_datetime + timedelta(minutes=duration_minutes)

    lead_name = f"{lead.first_name or ''} {lead.last_name or ''}".strip() or "Lead"
    event_body = {
        "summary": f"Appointment: {lead_name}",
        "description": (
            f"Lead phone: {lead.phone or 'N/A'}\n"
            f"Lead email: {lead.email or 'N/A'}\n"
            f"Tier: {lead.tier.value if lead.tier else 'N/A'}\n"
            f"Booked via AdvisorFlow booking link."
        ),
        "start": {"dateTime": booked_datetime.isoformat(), "timeZone": "America/Chicago"},
        "end": {"dateTime": end_time.isoformat(), "timeZone": "America/Chicago"},
    }

    try:
        created_event = service.events().insert(calendarId=advisor.google_calendar_id, body=event_body).execute()
        booking.calendar_event_id = created_event.get("id")
        booking.status = "booked"
        booking.booked_time = booked_datetime
        lead.status = "booked"
        db.commit()
        return {"success": True, "event_id": created_event.get("id"), "event_link": created_event.get("htmlLink")}
    except Exception as e:
        return {"success": False, "error": str(e)}


def cancel_calendar_event(db: Session, booking: BookingLink) -> dict:
    """Cancels a previously created calendar event (e.g. lead reschedules/cancels)."""
    if not booking.calendar_event_id:
        return {"success": False, "error": "No calendar event associated with this booking."}

    advisor = db.query(User).filter(User.id == booking.user_id).first()
    service = _get_calendar_service(advisor)

    try:
        service.events().delete(calendarId=advisor.google_calendar_id, eventId=booking.calendar_event_id).execute()
        booking.status = "cancelled"
        db.commit()
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}
