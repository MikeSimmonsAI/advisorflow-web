import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
import os

from app.deps import get_db, get_current_user
from app.models.models import User
from app.services.microsoft_email_service import get_microsoft_authorization_url, handle_microsoft_oauth_callback

router = APIRouter(prefix="/microsoft", tags=["microsoft"])
logger = logging.getLogger(__name__)

# Same destination as the Google Calendar OAuth flow - the Settings page,
# since that's where both "Connect Google Calendar" and "Connect
# Microsoft 365" buttons live, as two independent connection options.
FRONTEND_SETTINGS_URL = os.environ.get("FRONTEND_URL", "http://localhost:5173") + "/settings"
FRONTEND_SETUP_URL = os.environ.get("FRONTEND_URL", "http://localhost:5173") + "/setup-integrations"


@router.get("/connect")
def connect_microsoft_365(current_user: User = Depends(get_current_user)):
    """Returns the URL the advisor visits to grant Microsoft 365 email-send permission."""
    try:
        url = get_microsoft_authorization_url(current_user.id)
    except RuntimeError as e:
        logger.error("Microsoft OAuth URL error for user %s: %s", current_user.id, e)
        raise HTTPException(status_code=500, detail="Microsoft integration is not configured. Contact support.")
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
    # Detect setup-link flow ("setup:{user_id}") vs normal logged-in flow.
    is_setup_flow = isinstance(state, str) and state.startswith("setup:")
    real_user_id = state[6:] if is_setup_flow else state
    redirect_base = FRONTEND_SETUP_URL if is_setup_flow else FRONTEND_SETTINGS_URL

    if error:
        return RedirectResponse(url=f"{redirect_base}?microsoft_error={quote(str(error))}")

    if not code:
        return RedirectResponse(url=f"{redirect_base}?microsoft_error=missing_code")

    try:
        handle_microsoft_oauth_callback(db, advisor_user_id=real_user_id, authorization_code=code)
    except Exception as e:
        logger.error("Microsoft OAuth callback error for user %s: %s", real_user_id, e)
        return RedirectResponse(url=f"{redirect_base}?microsoft_error=connection_failed")

    return RedirectResponse(url=f"{redirect_base}?microsoft_connected=true")
