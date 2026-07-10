from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from datetime import datetime
import os

from app.deps import get_db, get_current_user
from app.models.models import User, BookingLink
from app.services.calendar_service import (
    get_authorization_url, handle_oauth_callback,
    create_calendar_event_for_booking, cancel_calendar_event,
)

router = APIRouter(prefix="/calendar", tags=["calendar"])

# Where to send the advisor back to in the frontend once OAuth completes -
# the Settings page, since that's where the "Connect Google Calendar" button lives.
FRONTEND_SETTINGS_URL = os.environ.get("FRONTEND_URL", "http://localhost:5173") + "/settings"


@router.get("/connect")
def connect_google_calendar(current_user: User = Depends(get_current_user)):
    """Returns the URL the advisor should visit to grant Google Calendar access."""
    try:
        url = get_authorization_url(current_user.id)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"authorization_url": url}


@router.get("/oauth/callback")
def oauth_callback(
    request: Request,
    state: str = Query(...),  # the advisor's user_id, passed through by Google
    code: str = Query(None),
    error: str = Query(None),
    db: Session = Depends(get_db),
):
    """
    Google redirects here after the advisor grants (or denies) access.
    `state` carries the advisor's user_id from the original /calendar/connect
    call. No auth dependency on this route - it's hit directly by Google's
    redirect, not by an authenticated frontend call - `state` is what ties
    it back to the right advisor, and the OAuth `code` itself is the proof
    of consent.

    On success, stores the encrypted refresh token on the advisor's User
    record (via handle_oauth_callback) and redirects back to the Settings
    page so the advisor sees a clear confirmation in the UI, rather than
    a bare JSON response on a page they didn't navigate to themselves.
    """
    if error:
        # Advisor denied access or something went wrong on Google's side -
        # redirect back with a query param the frontend can show as an error toast.
        return RedirectResponse(url=f"{FRONTEND_SETTINGS_URL}?calendar_error={error}")

    if not code:
        return RedirectResponse(url=f"{FRONTEND_SETTINGS_URL}?calendar_error=missing_code")

    try:
        # Pass the full incoming URL (including ?code=...&state=...) to the
        # OAuth flow, which is what google-auth-oauthlib's fetch_token expects.
        full_callback_url = str(request.url)
        handle_oauth_callback(db, advisor_user_id=state, authorization_response_url=full_callback_url)
    except Exception as e:
        return RedirectResponse(url=f"{FRONTEND_SETTINGS_URL}?calendar_error={str(e)}")

    return RedirectResponse(url=f"{FRONTEND_SETTINGS_URL}?calendar_connected=true")


class BookingConfirmRequest(BaseModel):
    booking_token: str
    booked_datetime: datetime
    duration_minutes: int = 30


@router.get("/booking/{token}")
def get_booking_by_token(token: str, db: Session = Depends(get_db)):
    """
    Public endpoint — no auth required.
    The Vercel booking frontend calls this to get booking details by token.
    """
    from app.models.models import Lead, Organization
    booking = db.query(BookingLink).filter(
        BookingLink.token == token,
        BookingLink.status == "pending",
    ).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking link not found or already used")

    lead = db.query(Lead).filter(Lead.id == booking.lead_id).first()
    advisor = db.query(User).filter(User.id == booking.user_id).first()
    org = db.query(Organization).filter(Organization.id == booking.organization_id).first() if hasattr(booking, 'organization_id') else None

    return {
        "token": token,
        "booking_id": booking.id,
        "lead_name": f"{lead.first_name or ''} {lead.last_name or ''}".strip() if lead else "Guest",
        "advisor_name": advisor.full_name if advisor else "Your Advisor",
        "org_name": org.name if org else "Restland Cemetery & Funeral Home",
        "org_address": "13005 Greenville Ave, Dallas, TX 75243",
        "org_phone": "214-550-1234",
        "status": booking.status,
        "created_at": booking.created_at,
    }



def confirm_booking(req: BookingConfirmRequest, db: Session = Depends(get_db)):
    """
    Called by the stateless booking backend (advisorflow-booking.vercel.app)
    once a lead picks a time slot. Creates the actual Google Calendar event
    and fires appointment confirmation messages to lead + advisor.
    """
    booking = db.query(BookingLink).filter(BookingLink.token == req.booking_token).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking link not found or expired")

    result = create_calendar_event_for_booking(db, booking, req.booked_datetime, req.duration_minutes)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])

    # Store appointment time and fire confirmation messages
    try:
        booking.status = "confirmed"
        db.commit()
        db.refresh(booking)

        from app.models.models import Lead as LeadModel
        lead = db.query(LeadModel).filter(LeadModel.id == booking.lead_id).first()
        advisor = db.query(User).filter(User.id == booking.user_id).first()
        if lead and advisor:
            from app.services.appointment_flow_service import on_booking_confirmed
            on_booking_confirmed(db, lead, advisor, booking)
    except Exception as e:
        import logging
        logging.getLogger(__name__).error("Appointment flow error: %s", e)

    return result


@router.post("/cancel-booking/{booking_id}")
def cancel_booking(booking_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Cancels a booking's calendar event and fires cancellation messages.
    """
    from app.models.models import Lead as LeadModel

    booking = (
        db.query(BookingLink)
        .join(LeadModel, BookingLink.lead_id == LeadModel.id)
        .filter(BookingLink.id == booking_id, LeadModel.organization_id == current_user.organization_id)
        .first()
    )
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    result = cancel_calendar_event(db, booking)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])

    # Fire cancellation messages
    try:
        lead = db.query(LeadModel).filter(LeadModel.id == booking.lead_id).first()
        if lead:
            from app.services.appointment_flow_service import on_booking_cancelled
            on_booking_cancelled(db, lead, current_user, booking)
    except Exception as e:
        import logging
        logging.getLogger(__name__).error("Cancellation flow error: %s", e)

    return result


@router.post("/send-reminders")
def send_reminders(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Manually trigger appointment reminders (24h and 2h).
    In production, call this from a cron job every 15-30 minutes.
    """
    from app.services.appointment_flow_service import send_appointment_reminders
    result = send_appointment_reminders(db)
    return result


@router.post("/booking-confirmed")
async def booking_confirmed_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Called by Vercel booking app after lead confirms appointment.
    Creates Microsoft 365 Outlook calendar event and sends FSA SMS notification.
    No auth required — called by Vercel serverless function.
    """
    from app.models.models import BookingLink, Lead
    from datetime import datetime

    body = await request.json()
    token = body.get("booking_token", "")
    slot_display = body.get("slot_display", "")
    lead_name = body.get("lead_name", "")
    lead_phone = body.get("lead_phone", "")
    appt_label = body.get("appt_label", "Family File Review")
    advisor_name = body.get("advisor_name", "")

    # Find the booking link and advisor
    booking = db.query(BookingLink).filter(BookingLink.token == token).first()
    advisor = None
    lead = None

    if booking:
        from app.models.models import User
        advisor = db.query(User).filter(User.id == booking.user_id).first()
        lead = db.query(Lead).filter(Lead.id == booking.lead_id).first()
        booking.status = "booked"
        if lead:
            lead.status = "booked"

    # Create Microsoft 365 Outlook calendar event
    calendar_result = {"success": False, "note": "No advisor found"}
    if advisor and advisor.microsoft_365_connected and advisor.microsoft_oauth_refresh_token_encrypted:
        try:
            from app.services.microsoft_email_service import _get_fresh_access_token
            import httpx, os
            access_token = _get_fresh_access_token(advisor)

            # Parse slot time from display string
            event_start = None
            try:
                from dateutil import parser as dateparser
                event_start = dateparser.parse(slot_display)
            except Exception:
                pass

            if event_start:
                from datetime import timedelta
                event_end = event_start + timedelta(minutes=30)
                event_body = {
                    "subject": f"{appt_label} — {lead_name or lead_phone}",
                    "body": {
                        "contentType": "HTML",
                        "content": f"<p>Appointment with {lead_name or 'Lead'}</p><p>Phone: {lead_phone}</p><p>Booked via BookaBoost</p>"
                    },
                    "start": {
                        "dateTime": event_start.strftime("%Y-%m-%dT%H:%M:%S"),
                        "timeZone": "America/Chicago"
                    },
                    "end": {
                        "dateTime": event_end.strftime("%Y-%m-%dT%H:%M:%S"),
                        "timeZone": "America/Chicago"
                    },
                    "location": {
                        "displayName": "13005 Greenville Ave, Dallas, TX 75243"
                    },
                }
                cal_response = httpx.post(
                    "https://graph.microsoft.com/v1.0/me/events",
                    headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
                    json=event_body,
                    timeout=15,
                )
                calendar_result = {"success": cal_response.status_code in (200, 201), "status": cal_response.status_code}
        except Exception as e:
            calendar_result = {"success": False, "error": str(e)}

    # Send FSA SMS notification
    sms_result = {"success": False}
    if advisor:
        try:
            notification_phone = getattr(advisor, 'notification_phone', None) or getattr(advisor, 'twilio_phone_number', None)
            if notification_phone and advisor.twilio_account_sid and advisor.twilio_auth_token_encrypted:
                from twilio.rest import Client
                from app.utils.crypto import decrypt_value
                auth_token = decrypt_value(advisor.twilio_auth_token_encrypted)
                client = Client(advisor.twilio_account_sid, auth_token)
                msg = f"📅 BookaBoost: {lead_name or 'A lead'} just booked a {appt_label} for {slot_display}. Check your Outlook calendar. — BookaBoost"
                client.messages.create(
                    body=msg,
                    from_=advisor.twilio_phone_number,
                    to=notification_phone,
                )
                sms_result = {"success": True}
        except Exception as e:
            sms_result = {"success": False, "error": str(e)}

    db.commit()

    return {
        "received": True,
        "calendar": calendar_result,
        "sms": sms_result,
        "lead_name": lead_name,
        "slot": slot_display,
    }
