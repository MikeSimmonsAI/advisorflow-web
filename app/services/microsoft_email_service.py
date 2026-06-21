"""
Microsoft 365 Email Service

Per Mike's explicit instruction: the calendar stays Google (see
calendar_service.py, already built and working), but real outgoing
email should send AS the advisor's actual Restland Outlook/Microsoft
365 address - not a generic SendGrid sender. This is a SEPARATE
integration from Google Calendar; both coexist independently per
advisor, neither replaces the other.

Flow (mirrors the existing Google Calendar OAuth pattern in
calendar_service.py for consistency):
  1. Advisor connects their Microsoft 365 account once via OAuth
     (get_microsoft_authorization_url starts the flow,
     handle_microsoft_oauth_callback completes it and stores an
     encrypted refresh token on the User record).
  2. When sending an email, send_email_via_microsoft_graph() exchanges
     the refresh token for a fresh access token, then calls Microsoft
     Graph's /me/sendMail endpoint - the email genuinely originates
     from the advisor's real mailbox, replies land in their real inbox.

IMPORTANT: this requires an Azure App Registration (Client ID + Secret)
set up in Mike's Microsoft 365 admin / Azure portal, with Mail.Send and
offline_access delegated permissions, and the redirect URI pointed at
this backend's /microsoft/oauth/callback route. That setup step needs
to happen in Mike's Azure portal - not achievable purely from code,
same category of manual setup as the Google Cloud Console step and the
Twilio CNAM/Trust Hub registration.

CRITICAL: the offline_access scope is required below, or Microsoft will
not issue a refresh token at all - confirmed against Microsoft's own
current documentation before building this, since without it every
advisor would be forced to re-authenticate every 60-90 minutes instead
of once.
"""

import os
import httpx
from urllib.parse import urlencode
from datetime import datetime
from sqlalchemy.orm import Session
from app.models.models import User
from app.utils.crypto import encrypt_value, decrypt_value

MICROSOFT_CLIENT_ID = os.environ.get("MICROSOFT_CLIENT_ID")
MICROSOFT_CLIENT_SECRET = os.environ.get("MICROSOFT_CLIENT_SECRET")
MICROSOFT_REDIRECT_URI = os.environ.get("MICROSOFT_REDIRECT_URI", "https://<your-domain>/microsoft/oauth/callback")

# /common allows both personal Microsoft accounts and work/school
# Microsoft 365 accounts to authenticate through the same app
# registration - appropriate here since Restland advisors use work
# accounts, but this keeps the door open without requiring a
# tenant-specific config.
AUTHORITY = "https://login.microsoftonline.com/common"
SCOPES = "offline_access Mail.Send User.Read"  # offline_access is REQUIRED for a refresh token to be issued at all


def get_microsoft_authorization_url(advisor_user_id: str) -> str:
    """
    Step 1 of OAuth: returns the URL the advisor visits to grant email
    send permission. advisor_user_id is passed through as `state` so
    the callback knows which advisor to attach the resulting token to -
    same pattern as the existing Google Calendar flow.
    """
    if not MICROSOFT_CLIENT_ID:
        raise RuntimeError("MICROSOFT_CLIENT_ID / MICROSOFT_CLIENT_SECRET not configured.")
    params = {
        "client_id": MICROSOFT_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": MICROSOFT_REDIRECT_URI,
        "response_mode": "query",
        "scope": SCOPES,
        "state": advisor_user_id,
        "prompt": "consent",  # forces the consent screen every time, ensuring a refresh token is always re-issued
    }
    return f"{AUTHORITY}/oauth2/v2.0/authorize?{urlencode(params)}"


def handle_microsoft_oauth_callback(db: Session, advisor_user_id: str, authorization_code: str) -> User:
    """
    Step 2 of OAuth: exchanges the authorization code for tokens, stores
    the encrypted refresh token, and records the real mailbox address
    (via Microsoft Graph's /me endpoint) so the UI can show which
    account is connected without needing a separate lookup later.
    """
    if not MICROSOFT_CLIENT_ID or not MICROSOFT_CLIENT_SECRET:
        raise RuntimeError("MICROSOFT_CLIENT_ID / MICROSOFT_CLIENT_SECRET not configured.")

    token_response = httpx.post(
        f"{AUTHORITY}/oauth2/v2.0/token",
        data={
            "client_id": MICROSOFT_CLIENT_ID,
            "client_secret": MICROSOFT_CLIENT_SECRET,
            "code": authorization_code,
            "redirect_uri": MICROSOFT_REDIRECT_URI,
            "grant_type": "authorization_code",
            "scope": SCOPES,
        },
        timeout=15,
    )
    token_response.raise_for_status()
    token_data = token_response.json()

    refresh_token = token_data.get("refresh_token")
    access_token = token_data.get("access_token")
    if not refresh_token:
        # This should never happen given offline_access is requested above,
        # but fail loudly rather than silently storing nothing - a missing
        # refresh token here means every future send would fail anyway.
        raise RuntimeError("Microsoft did not return a refresh token - check that offline_access scope was granted.")

    # Look up the advisor's real mailbox address so it can be displayed
    # in Settings without a second round-trip later.
    profile_response = httpx.get(
        "https://graph.microsoft.com/v1.0/me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    profile_response.raise_for_status()
    profile_data = profile_response.json()
    mailbox_address = profile_data.get("mail") or profile_data.get("userPrincipalName")

    advisor = db.query(User).filter(User.id == advisor_user_id).first()
    if not advisor:
        raise RuntimeError(f"Advisor {advisor_user_id} not found.")

    advisor.microsoft_oauth_refresh_token_encrypted = encrypt_value(refresh_token)
    advisor.microsoft_email_address = mailbox_address
    advisor.microsoft_365_connected = True
    db.commit()
    return advisor


def _get_fresh_access_token(advisor: User) -> str:
    """Exchanges the stored refresh token for a new short-lived access token, on demand for each send."""
    if not advisor.microsoft_oauth_refresh_token_encrypted:
        raise ValueError(f"Advisor {advisor.id} has not connected Microsoft 365.")

    refresh_token = decrypt_value(advisor.microsoft_oauth_refresh_token_encrypted)
    response = httpx.post(
        f"{AUTHORITY}/oauth2/v2.0/token",
        data={
            "client_id": MICROSOFT_CLIENT_ID,
            "client_secret": MICROSOFT_CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
            "scope": SCOPES,
        },
        timeout=15,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def send_email_via_microsoft_graph(advisor: User, to_email: str, subject: str, body_html: str) -> dict:
    """
    Sends an email through Microsoft Graph's /me/sendMail endpoint -
    this genuinely originates from the advisor's real Outlook mailbox,
    not a third-party sender address. Returns a result dict matching the
    same {success, provider_message_id, error} shape email_service.py's
    SendGrid path already uses, so callers can treat both providers
    interchangeably.
    """
    try:
        access_token = _get_fresh_access_token(advisor)
        response = httpx.post(
            "https://graph.microsoft.com/v1.0/me/sendMail",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json={
                "message": {
                    "subject": subject,
                    "body": {"contentType": "HTML", "content": body_html},
                    "toRecipients": [{"emailAddress": {"address": to_email}}],
                },
                "saveToSentItems": True,
            },
            timeout=20,
        )
        # Graph's sendMail returns 202 Accepted with an empty body on success -
        # there is no message ID to capture, unlike SendGrid.
        if response.status_code == 202:
            return {"success": True, "provider_message_id": None, "error": None}
        return {"success": False, "provider_message_id": None, "error": f"Graph API returned {response.status_code}: {response.text}"}
    except Exception as e:
        return {"success": False, "provider_message_id": None, "error": str(e)}
