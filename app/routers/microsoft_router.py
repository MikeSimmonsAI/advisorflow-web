from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
import os

from app.deps import get_db, get_current_user
from app.models.models import User
from app.services.microsoft_email_service import get_microsoft_authorization_url, handle_microsoft_oauth_callback

router = APIRouter(prefix="/microsoft", tags=["microsoft"])

# Same destination as the Google Calendar OAuth flow - the Settings page,
# since that's where both "Connect Google Calendar" and "Connect
# Microsoft 365" buttons live, as two independent connection options.
FRONTEND_SETTINGS_URL = os.environ.get("FRONTEND_URL", "http://localhost:5173") + "/settings"


@router.get("/connect")
def connect_microsoft_365(current_user: User = Depends(get_current_user)):
    """Returns the URL the advisor visits to grant Microsoft 365 email-send permission."""
    try:
        url = get_microsoft_authorization_url(current_user.id)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"authorization_url": url}


@router.get("/oauth/callback")
def microsoft_oauth_callback(
    state: str = Query(...),  # the advisor's user_id, passed through by Microsoft
    code: str = Query(None),
    error: str = Query(None),
    db: Session = Depends(get_db),
):
    """
    Microsoft redirects here after the advisor grants (or denies) access.
    Same pattern as calendar_router.py's Google callback: no auth
    dependency since this is hit directly by Microsoft's redirect, not
    an authenticated frontend call - `state` ties it back to the right
    advisor, and the OAuth `code` is the proof of consent.

    NOTE: unlike the Google flow (which hands google-auth-oauthlib the
    full callback URL), Microsoft's token exchange just needs the raw
    `code` query parameter directly - passed straight through to
    handle_microsoft_oauth_callback below.
    """
    if error:
        return RedirectResponse(url=f"{FRONTEND_SETTINGS_URL}?microsoft_error={error}")

    if not code:
        return RedirectResponse(url=f"{FRONTEND_SETTINGS_URL}?microsoft_error=missing_code")

    try:
        handle_microsoft_oauth_callback(db, advisor_user_id=state, authorization_code=code)
    except Exception as e:
        return RedirectResponse(url=f"{FRONTEND_SETTINGS_URL}?microsoft_error={str(e)}")

    return RedirectResponse(url=f"{FRONTEND_SETTINGS_URL}?microsoft_connected=true")
