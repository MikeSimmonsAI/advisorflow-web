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


@router.post("/confirm-booking")
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
