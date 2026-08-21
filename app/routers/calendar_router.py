from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from datetime import datetime
from urllib.parse import quote
import logging
import os

from app.deps import get_db, get_current_user
from app.models.models import User, BookingLink
from app.services.platform_utils import get_brand_name
from app.services.calendar_service import (
    get_authorization_url, handle_oauth_callback,
    create_calendar_event_for_booking, cancel_calendar_event,
)

router = APIRouter(prefix="/calendar", tags=["calendar"])
logger = logging.getLogger(__name__)

# Where to send the advisor back to in the frontend once OAuth completes -
# the Settings page, since that's where the "Connect Google Calendar" button lives.
FRONTEND_SETTINGS_URL = os.environ.get("FRONTEND_URL", "http://localhost:5173") + "/settings"
FRONTEND_SETUP_URL = os.environ.get("FRONTEND_URL", "http://localhost:5173") + "/setup-integrations"


@router.get("/connect")
def connect_google_calendar(current_user: User = Depends(get_current_user)):
    """Returns the URL the advisor should visit to grant Google Calendar access."""
    try:
        url = get_authorization_url(current_user.id)
    except RuntimeError as e:
        logger.error("Google Calendar OAuth URL error for user %s: %s", current_user.id, e)
        raise HTTPException(status_code=500, detail="Google Calendar integration is not configured. Contact support.")
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
    # Detect the flow type early so error redirects also land on the right page.
    is_setup_flow = isinstance(state, str) and state.startswith("setup:")
    redirect_base = FRONTEND_SETUP_URL if is_setup_flow else FRONTEND_SETTINGS_URL

    if error:
        # Advisor denied access or something went wrong on Google's side -
        # redirect back with a query param the frontend can show as an error toast.
        return RedirectResponse(url=f"{redirect_base}?calendar_error={quote(str(error))}")

    if not code:
        return RedirectResponse(url=f"{redirect_base}?calendar_error=missing_code")

    real_user_id = state[6:] if is_setup_flow else state

    try:
        # Pass the full incoming URL (including ?code=...&state=...) to the
        # OAuth flow, which is what google-auth-oauthlib's fetch_token expects.
        full_callback_url = str(request.url)
        handle_oauth_callback(db, advisor_user_id=real_user_id, authorization_response_url=full_callback_url)
    except Exception as e:
        logger.error("Google Calendar OAuth callback error for user %s: %s", real_user_id, e)
        return RedirectResponse(url=f"{redirect_base}?calendar_error=connection_failed")

    return RedirectResponse(url=f"{redirect_base}?calendar_connected=true")


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
    # Look up by token only — do NOT filter by status.
    # The AI cadence may have issued multiple links for the same lead; all remain
    # valid until a booking is confirmed. The booking app checks `status` in the
    # response to decide whether to show "already booked" vs. the booking form.
    booking = db.query(BookingLink).filter(
        BookingLink.token == token,
    ).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking link not found")

    lead = db.query(Lead).filter(Lead.id == booking.lead_id).first()
    advisor = db.query(User).filter(User.id == booking.user_id).first()
    org = db.query(Organization).filter(Organization.id == booking.organization_id).first() if hasattr(booking, 'organization_id') else None

    return {
        "token": token,
        "booking_id": booking.id,
        "lead_name": f"{lead.first_name or ''} {lead.last_name or ''}".strip() if lead else "Guest",
        "advisor_name": advisor.full_name if advisor else "Your Advisor",
        "org_name": org.name if org else "",
        "org_address": org.org_address if org and hasattr(org, 'org_address') else "",
        "org_phone": org.org_phone if org and hasattr(org, 'org_phone') else "",
        "status": booking.status,
        "created_at": booking.created_at,
    }


@router.post("/booking-confirmed")
async def booking_confirmed_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Called by Vercel booking app after lead confirms appointment.
    Creates Microsoft 365 Outlook calendar event and sends FSA SMS notification.
    No auth required — called by Vercel serverless function.
    """
    from app.models.models import Lead

    body = await request.json()
    logger.info("booking-confirmed received: %s", body)

    token = body.get("booking_token", "")
    slot_display = body.get("slot_display", "")
    lead_name = body.get("lead_name", "")
    lead_phone = body.get("lead_phone", "")
    appt_label = body.get("appt_label", "Family File Review")
    if not token:
        logger.error("booking-confirmed: no booking_token in payload")
        raise HTTPException(status_code=400, detail="booking_token is required")

    # Find the booking link and related records
    booking = db.query(BookingLink).filter(BookingLink.token == token).first()
    advisor = None
    lead = None

    org = None
    if booking:
        advisor = db.query(User).filter(User.id == booking.user_id).first()
        lead = db.query(Lead).filter(Lead.id == booking.lead_id).first()
        booking.status = "booked"
        if lead:
            lead.status = "booked"
        if advisor:
            from app.models.models import Organization
            org = db.query(Organization).filter(Organization.id == advisor.organization_id).first()
        logger.info("booking-confirmed: found booking=%s advisor=%s lead=%s", booking.id, advisor.id if advisor else None, lead.id if lead else None)
    else:
        logger.warning("booking-confirmed: no booking found for token=%s", token)

    # ── Create Microsoft 365 Outlook calendar event ──────────────────────────
    calendar_result = {"success": False, "note": "No advisor found"}
    event_start = None  # shared between MS365 and Google Calendar blocks

    if advisor and advisor.microsoft_365_connected and advisor.microsoft_oauth_refresh_token_encrypted:
        try:
            from app.services.microsoft_email_service import _get_fresh_access_token
            import httpx

            access_token = _get_fresh_access_token(advisor)
            logger.info("booking-confirmed: got MS access token for advisor=%s", advisor.id)

            # Parse slot_display into a datetime. Vercel sends ISO 8601 when available;
            # fall back to human-readable formats.
            event_start = None

            # Try ISO 8601 first (cleanest)
            for fmt in [
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
            ]:
                try:
                    event_start = datetime.strptime(slot_display.strip(), fmt)
                    break
                except Exception:
                    continue

            # Try human-readable formats as fallback
            if not event_start:
                for fmt in [
                    "%A, %B %d, %Y at %I:%M %p",
                    "%A, %B %d, %Y at %I:%M%p",
                    "%A, %B %d at %I:%M %p",
                    "%A, %B %d at %I:%M%p",
                    "%m/%d/%Y at %I:%M %p",
                    "%m/%d/%Y %I:%M %p",
                    "%B %d, %Y at %I:%M %p",
                    "%B %d, %Y %I:%M %p",
                ]:
                    try:
                        cleaned = slot_display.replace(" at ", " at ").strip()
                        event_start = datetime.strptime(cleaned, fmt)
                        if event_start.year == 1900:
                            event_start = event_start.replace(year=datetime.now().year)
                        break
                    except Exception:
                        continue

            if not event_start:
                logger.error("booking-confirmed: could not parse slot_display=%r", slot_display)
                calendar_result = {"success": False, "error": f"Could not parse slot time: {slot_display!r}"}
            else:
                from datetime import timedelta
                event_end = event_start + timedelta(minutes=30)
                event_body_payload = {
                    "subject": f"{appt_label} — {lead_name or lead_phone}",
                    "body": {
                        "contentType": "HTML",
                        "content": (
                            f"<p>Appointment with {lead_name or 'Lead'}</p>"
                            f"<p>Phone: {lead_phone}</p>"
                            f"<p>Booked via {get_brand_name(db, str(advisor.organization_id))}</p>"
                        ),
                    },
                    "start": {
                        "dateTime": event_start.strftime("%Y-%m-%dT%H:%M:%S"),
                        "timeZone": "America/Chicago",
                    },
                    "end": {
                        "dateTime": event_end.strftime("%Y-%m-%dT%H:%M:%S"),
                        "timeZone": "America/Chicago",
                    },
                    "location": {
                        "displayName": (
                            org.org_address
                            if (org and getattr(org, "org_address", None))
                            else "13005 Greenville Ave, Dallas, TX 75243"
                        ),
                    },
                }
                cal_response = httpx.post(
                    "https://graph.microsoft.com/v1.0/me/events",
                    headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
                    json=event_body_payload,
                    timeout=15,
                )
                logger.info("Graph calendar POST status=%s body=%s", cal_response.status_code, cal_response.text[:300])
                if cal_response.status_code in (200, 201):
                    calendar_result = {"success": True, "status": cal_response.status_code}
                    # Store the Graph event ID on the booking for future cancellation
                    if booking:
                        event_data = cal_response.json()
                        booking.calendar_event_id = event_data.get("id")
                else:
                    calendar_result = {
                        "success": False,
                        "status": cal_response.status_code,
                        "error": cal_response.text[:500],
                    }

        except Exception as e:
            logger.exception("booking-confirmed: calendar error: %s", e)
            calendar_result = {"success": False, "error": str(e)}
    else:
        if advisor:
            logger.warning(
                "booking-confirmed: advisor=%s not M365 connected (connected=%s has_token=%s)",
                advisor.id,
                advisor.microsoft_365_connected,
                bool(advisor.microsoft_oauth_refresh_token_encrypted),
            )

    # ── Create Google Calendar event (if advisor has Google Calendar connected) ─
    google_calendar_result = {"success": False, "note": "Not connected"}
    if advisor and getattr(advisor, 'google_calendar_connected', False) and getattr(advisor, 'google_oauth_refresh_token_encrypted', None):
        try:
            # Reuse the datetime we already parsed for the MS365 block above.
            # If that block didn't run (MS365 not connected), parse now.
            if event_start is None:
                event_start = None
                for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"]:
                    try:
                        event_start = datetime.strptime(slot_display.strip(), fmt)
                        break
                    except Exception:
                        continue
                if not event_start:
                    for fmt in ["%A, %B %d, %Y at %I:%M %p", "%A, %B %d, %Y at %I:%M%p",
                                "%B %d, %Y at %I:%M %p", "%B %d, %Y %I:%M %p",
                                "%m/%d/%Y at %I:%M %p", "%m/%d/%Y %I:%M %p"]:
                        try:
                            cleaned = slot_display.replace(" at ", " at ").strip()
                            event_start = datetime.strptime(cleaned, fmt)
                            if event_start.year == 1900:
                                event_start = event_start.replace(year=datetime.now().year)
                            break
                        except Exception:
                            continue

            if event_start and booking:
                from app.services.calendar_service import create_calendar_event_for_booking as _gcal_create
                gcal_result = _gcal_create(db, booking, event_start, 30)
                google_calendar_result = gcal_result
                logger.info("booking-confirmed: Google Calendar result=%s", gcal_result)
            else:
                google_calendar_result = {"success": False, "error": f"Could not parse slot time: {slot_display!r}"}
        except Exception as e:
            logger.exception("booking-confirmed: Google Calendar error: %s", e)
            google_calendar_result = {"success": False, "error": str(e)}
    else:
        if advisor and not getattr(advisor, 'google_calendar_connected', False):
            logger.info("booking-confirmed: advisor=%s Google Calendar not connected — skipping", advisor.id if advisor else "none")

    # ── Send FSA SMS notification ─────────────────────────────────────────────
    sms_result = {"success": False}
    if advisor:
        try:
            notification_phone = getattr(advisor, 'notification_phone', None) or getattr(advisor, 'twilio_phone_number', None)
            if notification_phone and advisor.twilio_account_sid and advisor.twilio_auth_token_encrypted:
                from twilio.rest import Client
                from app.utils.crypto import decrypt_value
                auth_token = decrypt_value(advisor.twilio_auth_token_encrypted)
                client = Client(advisor.twilio_account_sid, auth_token)
                _sms_brand = get_brand_name(db, str(advisor.organization_id))
                msg_body = f"📅 {_sms_brand}: {lead_name or 'A lead'} just confirmed a {appt_label} for {slot_display}. Check your Outlook calendar."
                client.messages.create(
                    body=msg_body,
                    from_=advisor.twilio_phone_number,
                    to=notification_phone,
                )
                sms_result = {"success": True}
                logger.info("booking-confirmed: FSA SMS sent to %s", notification_phone)
            else:
                logger.warning(
                    "booking-confirmed: cannot send FSA SMS — notification_phone=%s sid=%s",
                    notification_phone,
                    advisor.twilio_account_sid,
                )
        except Exception as e:
            logger.exception("booking-confirmed: SMS error: %s", e)
            sms_result = {"success": False, "error": str(e)}

    # ── Send advisor notification email ──────────────────────────────────────
    email_result = {"success": False}
    if advisor and lead:
        try:
            _send_booking_notification_email(advisor, lead, appt_label, slot_display, db)
            email_result = {"success": True}
            logger.info("booking-confirmed: notification email sent")
        except Exception as e:
            logger.exception("booking-confirmed: notification email error: %s", e)
            email_result = {"success": False, "error": str(e)}

    db.commit()

    response_payload = {
        "received": True,
        "calendar": calendar_result,
        "google_calendar": google_calendar_result,
        "sms": sms_result,
        "email": email_result,
        "lead_name": lead_name,
        "slot": slot_display,
    }
    logger.info("booking-confirmed response: %s", response_payload)
    return response_payload


URGENT_TIERS = {"at_need", "atneed", "at-need", "imminent", "urgent"}
NOTIFICATION_EMAIL = "michael.simmons@nsmg.com"  # advisor notification address


def _send_booking_notification_email(advisor, lead, appt_label: str, slot_display: str, db=None):
    """
    Send a professional booking notification email to the advisor.
    Uses 🔥 urgent subject for hot/at-need/imminent tiers.
    Sends via Microsoft Graph using the connected bookaboost@outlook.com inbox.
    """
    import httpx
    import os
    from app.services.microsoft_email_service import _get_fresh_access_token

    if not advisor.microsoft_365_connected or not advisor.microsoft_oauth_refresh_token_encrypted:
        logger.warning("Cannot send notification email — M365 not connected")
        return

    access_token = _get_fresh_access_token(advisor)

    # Determine urgency
    tier = (lead.tier or "").lower()
    is_urgent = tier in URGENT_TIERS
    lead_name = f"{lead.first_name or ''} {lead.last_name or ''}".strip() or "A lead"
    lead_url = f"{os.environ.get('FRONTEND_URL', 'https://advisorflow-frontend.onrender.com')}/leads/{lead.id}"
    brand = get_brand_name(db, str(advisor.organization_id)) if db else "BookaBoost"

    if is_urgent:
        subject = f"🔥 URGENT Booking — {lead_name} Needs Immediate Attention"
        header_color = "#c0392b"
        urgency_banner = f"""
        <tr>
          <td style="background:#fff3cd;border:1px solid #ffc107;border-radius:6px;padding:12px 16px;margin-bottom:16px;">
            <strong style="color:#856404;">⚡ URGENT:</strong> This lead is flagged as <strong>{tier.upper()}</strong>.
            Respond immediately.
          </td>
        </tr>"""
    else:
        subject = f"📅 New Booking Confirmed — {lead_name}"
        header_color = "#1a5fa8"
        urgency_banner = ""

    body_html = f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f0f4f8;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="padding:32px 16px;">
  <tr><td align="center">
    <table width="600" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.08);">

      <!-- Header -->
      <tr>
        <td style="background:{header_color};padding:28px 32px;">
          <p style="margin:0;color:#ffffff;font-size:13px;font-weight:600;text-transform:uppercase;letter-spacing:0.08em;opacity:0.8;">{brand}</p>
          <h1 style="margin:8px 0 0;color:#ffffff;font-size:24px;font-weight:800;letter-spacing:-0.02em;">
            {'🔥 Urgent Booking Alert' if is_urgent else '📅 New Booking Confirmed'}
          </h1>
        </td>
      </tr>

      <!-- Body -->
      <tr><td style="padding:32px;">
        <table width="100%" cellpadding="0" cellspacing="0">

          {urgency_banner}

          <!-- Lead info -->
          <tr><td style="padding-bottom:24px;">
            <h2 style="margin:0 0 16px;font-size:18px;color:#1a2a4a;">Appointment Details</h2>
            <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e2e8f0;border-radius:8px;overflow:hidden;">
              <tr style="background:#f8fafc;">
                <td style="padding:12px 16px;font-weight:700;color:#64748b;font-size:12px;text-transform:uppercase;width:140px;">Lead</td>
                <td style="padding:12px 16px;color:#1a2a4a;font-weight:600;">{lead_name}</td>
              </tr>
              <tr>
                <td style="padding:12px 16px;font-weight:700;color:#64748b;font-size:12px;text-transform:uppercase;border-top:1px solid #e2e8f0;">Phone</td>
                <td style="padding:12px 16px;color:#1a2a4a;border-top:1px solid #e2e8f0;">{lead.phone or 'N/A'}</td>
              </tr>
              <tr style="background:#f8fafc;">
                <td style="padding:12px 16px;font-weight:700;color:#64748b;font-size:12px;text-transform:uppercase;border-top:1px solid #e2e8f0;">Email</td>
                <td style="padding:12px 16px;color:#1a2a4a;border-top:1px solid #e2e8f0;">{lead.email or 'N/A'}</td>
              </tr>
              <tr>
                <td style="padding:12px 16px;font-weight:700;color:#64748b;font-size:12px;text-transform:uppercase;border-top:1px solid #e2e8f0;">Appointment</td>
                <td style="padding:12px 16px;color:#1a2a4a;border-top:1px solid #e2e8f0;">{appt_label}</td>
              </tr>
              <tr style="background:#f8fafc;">
                <td style="padding:12px 16px;font-weight:700;color:#64748b;font-size:12px;text-transform:uppercase;border-top:1px solid #e2e8f0;">Date & Time</td>
                <td style="padding:12px 16px;color:#1a2a4a;font-weight:700;border-top:1px solid #e2e8f0;">{slot_display}</td>
              </tr>
              <tr>
                <td style="padding:12px 16px;font-weight:700;color:#64748b;font-size:12px;text-transform:uppercase;border-top:1px solid #e2e8f0;">Lead Type</td>
                <td style="padding:12px 16px;color:#1a2a4a;border-top:1px solid #e2e8f0;">{(lead.tier or 'Unknown').replace('_', ' ').title()}</td>
              </tr>
              <tr style="background:#f8fafc;">
                <td style="padding:12px 16px;font-weight:700;color:#64748b;font-size:12px;text-transform:uppercase;border-top:1px solid #e2e8f0;">Source</td>
                <td style="padding:12px 16px;color:#1a2a4a;border-top:1px solid #e2e8f0;">{lead.source_file or 'N/A'} {('(' + str(lead.source_year) + ')') if lead.source_year else ''}</td>
              </tr>
            </table>
          </td></tr>

          <!-- CTA button -->
          <tr><td style="padding-bottom:24px;text-align:center;">
            <a href="{lead_url}"
               style="display:inline-block;background:{header_color};color:#ffffff;padding:14px 32px;border-radius:8px;text-decoration:none;font-weight:700;font-size:15px;">
              View Lead in {brand} →
            </a>
          </td></tr>

          <!-- Calendar reminder -->
          <tr><td style="background:#f0f9ff;border:1px solid #bae6fd;border-radius:8px;padding:16px;">
            <p style="margin:0;color:#0369a1;font-size:13px;">
              📅 <strong>Check your Outlook calendar</strong> — the appointment has been added automatically.
              Make sure there are no conflicts for <strong>{slot_display}</strong>.
            </p>
          </td></tr>

        </table>
      </td></tr>

      <!-- Footer -->
      <tr>
        <td style="background:#f8fafc;padding:20px 32px;border-top:1px solid #e2e8f0;">
          <p style="margin:0;color:#94a3b8;font-size:12px;text-align:center;">
            {brand} · Appointment Scheduling Platform · Dallas, TX<br>
            This is an automated notification. Do not reply to this email.
          </p>
        </td>
      </tr>

    </table>
  </td></tr>
</table>
</body>
</html>"""

    notification_to = getattr(advisor, 'notification_email', None) or NOTIFICATION_EMAIL
    logger.info("Sending booking notification email to %s", notification_to)
    resp = httpx.post(
        "https://graph.microsoft.com/v1.0/me/sendMail",
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        json={
            "message": {
                "subject": subject,
                "body": {"contentType": "HTML", "content": body_html},
                "toRecipients": [{"emailAddress": {"address": notification_to}}],
            },
            "saveToSentItems": False,
        },
        timeout=15,
    )
    if resp.status_code not in (200, 201, 202):
        logger.error("sendMail failed status=%s body=%s", resp.status_code, resp.text[:500])
        raise Exception(f"Graph sendMail failed: {resp.status_code} {resp.text[:200]}")
    logger.info("Booking notification email sent successfully to %s", notification_to)
    logger.info("Notification email sent to %s subject=%r", notification_to, subject)


@router.post("/confirm-booking")
def confirm_booking(req: BookingConfirmRequest, db: Session = Depends(get_db)):
    """
    Legacy endpoint — accepts structured datetime directly.
    Called from internal tools, not the Vercel app.
    Creates the Google Calendar event and fires appointment confirmation messages.
    """
    booking = db.query(BookingLink).filter(BookingLink.token == req.booking_token).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking link not found or expired")

    result = create_calendar_event_for_booking(db, booking, req.booked_datetime, req.duration_minutes)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])

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
        logger.error("Appointment flow error: %s", e)

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

    # cancel_calendar_event always returns success=True — it marks the booking
    # cancelled and handles a missing/already-deleted calendar event gracefully.
    result = cancel_calendar_event(db, booking)

    try:
        lead = db.query(LeadModel).filter(LeadModel.id == booking.lead_id).first()
        if lead:
            from app.services.appointment_flow_service import on_booking_cancelled
            on_booking_cancelled(db, lead, current_user, booking)
    except Exception as e:
        logger.error("Cancellation flow error: %s", e)

    return result


@router.get("/events")
def list_calendar_events(
    days_ahead: int = Query(60, ge=1, le=365),
    org_wide: bool = Query(False),
    advisor_id: str = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return upcoming confirmed/booked appointments formatted for the embedded
    mini-calendar view in Availability.jsx.

    - Default: returns the current user's own events.
    - org_wide=true (admins only): returns ALL advisors in the org (god_admin
      sees across all orgs on their platform).
    - advisor_id=<id> (admins only): returns a specific advisor's events.

    Pulls from BookingLink rather than Google Calendar so it works regardless
    of whether the advisor has a calendar integration connected.
    """
    from app.models.models import Lead
    from datetime import timedelta, timezone

    ADMIN_ROLES = ("org_admin", "super_admin", "god_admin")
    is_admin = current_user.role in ADMIN_ROLES

    cutoff = datetime.now(timezone.utc) + timedelta(days=days_ahead)
    now = datetime.now(timezone.utc)

    # Build the query filter based on access level
    if org_wide and is_admin:
        # God admin: all orgs on the same platform; others: own org only
        if current_user.role == "god_admin":
            q = db.query(BookingLink)
        else:
            # Collect all user IDs in this org
            org_user_ids = [
                u.id for u in db.query(User).filter(
                    User.organization_id == current_user.organization_id,
                    User.is_active == True,
                ).all()
            ]
            q = db.query(BookingLink).filter(BookingLink.user_id.in_(org_user_ids))
    elif advisor_id and is_admin:
        # Single specific advisor (admin viewing another advisor's calendar)
        q = db.query(BookingLink).filter(BookingLink.user_id == advisor_id)
    else:
        q = db.query(BookingLink).filter(BookingLink.user_id == current_user.id)

    bookings = q.filter(
        BookingLink.status.in_(["booked", "confirmed"])
    ).all()

    # Cache advisor names to avoid N+1 queries in org_wide mode
    advisor_cache = {}
    def get_advisor_name(user_id):
        if user_id not in advisor_cache:
            u = db.query(User).filter(User.id == user_id).first()
            advisor_cache[user_id] = u.full_name if u else "Advisor"
        return advisor_cache[user_id]

    events = []
    for b in bookings:
        if not b.booked_time:
            continue
        bt = b.booked_time
        if bt.tzinfo is None:
            bt = bt.replace(tzinfo=timezone.utc)
        if bt < now or bt > cutoff:
            continue

        lead = db.query(Lead).filter(Lead.id == b.lead_id).first() if b.lead_id else None
        lead_name = f"{lead.first_name or ''} {lead.last_name or ''}".strip() if lead else "Lead"

        event = {
            "id": b.id,
            "booked_time": bt.isoformat(),
            "lead_name": lead_name,
            "lead_id": b.lead_id,
            "status": b.status,
            "advisor_id": b.user_id,
        }
        if org_wide and is_admin:
            event["advisor_name"] = get_advisor_name(b.user_id)
        events.append(event)

    events.sort(key=lambda e: e["booked_time"])
    return events


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
