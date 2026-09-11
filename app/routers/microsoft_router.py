import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
import os

from app.deps import get_db, get_current_user
from app.models.models import User
from app.models.oauth_models import FLOW_SETUP, PROVIDER_MICROSOFT
from app.services import oauth_state_service
from app.services.oauth_state_service import OAuthStateError
from app.services.microsoft_email_service import get_microsoft_authorization_url, handle_microsoft_oauth_callback

router = APIRouter(prefix="/microsoft", tags=["microsoft"])
logger = logging.getLogger(__name__)

# Same destination as the Google Calendar OAuth flow - the Settings page,
# since that's where both "Connect Google Calendar" and "Connect
# Microsoft 365" buttons live, as two independent connection options.
FRONTEND_SETTINGS_URL = os.environ.get("FRONTEND_URL", "http://localhost:5173") + "/settings"
FRONTEND_SETUP_URL = os.environ.get("FRONTEND_URL", "http://localhost:5173") + "/setup-integrations"


@router.get("/connect")
def connect_microsoft_365(request: Request,
                          current_user: User = Depends(get_current_user),
                          db: Session = Depends(get_db)):
    """Returns the URL the advisor visits to grant Microsoft 365 email-send permission.

    Identity is bound to a server-side authorization transaction here, where
    the session proves it. Same reasoning as the Google flow — see
    `app/services/oauth_state_service.py`.
    """
    state = oauth_state_service.issue_state(
        db, provider=PROVIDER_MICROSOFT, subject=current_user,
        client_ip=(request.client.host if request.client else None))
    try:
        url = get_microsoft_authorization_url(state)
    except RuntimeError as e:
        logger.error("Microsoft OAuth URL error for user %s: %s", current_user.id, e)
        raise HTTPException(status_code=500, detail="Microsoft integration is not configured. Contact support.")
    return {"authorization_url": url}


@router.get("/oauth/callback")
def microsoft_oauth_callback(
    state: str = Query(...),  # opaque handle to the authorization transaction
    code: str = Query(None),
    error: str = Query(None),
    db: Session = Depends(get_db),
):
    """
    Microsoft redirects here after the advisor grants (or denies) access.

    Same pattern as calendar_router.py's Google callback, and the same defect
    closed: `state` used to be the advisor's user_id (optionally behind a
    "setup:" prefix) and this route stored the resulting refresh token against
    whatever id came back, so a valid Microsoft authorization could be
    redirected into somebody else's account and replace their mail-send
    credential. The identity now comes from the transaction row written at
    initiation; the browser only carries a single-use handle to it.

    The transaction is issued for PROVIDER_MICROSOFT, so a Google state
    presented here is refused before anything else happens — and vice versa.

    NOTE: unlike the Google flow (which hands google-auth-oauthlib the
    full callback URL), Microsoft's token exchange just needs the raw
    `code` query parameter directly - passed straight through to
    handle_microsoft_oauth_callback below.
    """
    try:
        txn = oauth_state_service.consume_state(db, state,
                                                provider=PROVIDER_MICROSOFT)
    except OAuthStateError as e:
        logger.warning(
            "Microsoft OAuth callback refused an authorization state: %s", e)
        return RedirectResponse(
            url=f"{FRONTEND_SETTINGS_URL}?microsoft_error=invalid_state")

    is_setup_flow = (txn.flow == FLOW_SETUP)
    real_user_id = txn.user_id
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
