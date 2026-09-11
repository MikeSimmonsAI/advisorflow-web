"""
Advisor integration setup flow.

Allows an org admin to generate a time-limited link for an advisor so they
can connect their Google Calendar and/or Microsoft 365 account without
needing to be logged into BookaBoost. The admin copies the link and sends
it via email, SMS, Slack, etc. — the advisor just clicks it and authenticates.

Token format: signed HS256 JWT with purpose="integration_setup", exp=48h,
signed with a DERIVED key that is not the access-token key (see
SETUP_SIGNING_KEY below for the takeover this closes).

OAuth state: an opaque single-use handle issued by
`app/services/oauth_state_service.py`. It used to be the string
"setup:{user_id}", which the callbacks parsed back into a user id — so the
browser named whose integration the resulting grant became. It does not any
more; the identity and the "redirect to /setup-integrations rather than
/settings" decision both live on the transaction row.

Endpoints:
  POST /admin/setup-link/{user_id}  — admin generates the link
  GET  /setup/verify                — public; validate token, return advisor info
  GET  /setup/google-connect        — public; return Google OAuth URL
  GET  /setup/microsoft-connect     — public; return Microsoft OAuth URL
"""

import hashlib
import hmac
import logging
import os
import jwt
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user
from app.models.oauth_models import (FLOW_SETUP, PROVIDER_GOOGLE,
                                     PROVIDER_MICROSOFT)
from app.models.models import User
from app.services import oauth_state_service
from app.services.calendar_service import get_authorization_url
from app.services.microsoft_email_service import get_microsoft_authorization_url

_log = logging.getLogger(__name__)

router = APIRouter(tags=["setup"])

_BASE_SECRET = os.environ.get("SECRET_KEY") or os.environ.get("JWT_SECRET") or ""
if not _BASE_SECRET or len(_BASE_SECRET) < 32:
    raise RuntimeError(
        "setup_router requires SECRET_KEY or JWT_SECRET env var (≥32 chars). "
        "Set it in your environment before starting the server."
    )

# ══ A SETUP TOKEN MUST NOT BE SIGNED BY THE ACCESS-TOKEN KEY ═══════════════
#
# THE DEFECT. `SECRET_KEY` is not set in production, so the line above resolved
# to JWT_SECRET — the key `auth_service` signs ACCESS tokens with. The setup
# token below carries `sub`, so `auth_service.decode_access_token` verified it
# and `deps.get_current_user` served the request as that advisor. It carries no
# `jti`, so session enforcement was skipped and the token still worked after
# the advisor's sessions had all been revoked. A "connect your calendar" link
# was a 48-hour bearer credential for every protected route in the platform.
#
# TWO DEFENCES, NOT ONE. `auth_service` now refuses any token that does not
# carry purpose="access", which alone closes the crossing. This is the second,
# independent one, and it is the one that does not depend on anybody remembering
# to check a claim: with a DIFFERENT KEY, a setup token does not verify as an
# access token at all. Signature verification fails before any claim is read.
#
# DERIVED, NOT CONFIGURED. `SETUP_TOKEN_SECRET` is honoured if an operator sets
# one, but the default is HMAC(base secret, a fixed label) rather than the base
# secret itself. That means the separation exists on every environment — local,
# CI, staging, production — from this deploy, with no new environment variable
# to set and nothing to forget. Changing the label rotates every outstanding
# setup link, which is a feature, not a hazard.
#
# WHAT THIS COSTS. Setup links issued before this deploy no longer verify, so
# an admin re-sends any that are still in flight. A 48-hour link is cheap to
# reissue; an account-takeover primitive is not cheap to leave open.
SETUP_KEY_LABEL = b"advisorflow:integration-setup-token:v1"
SETUP_SIGNING_KEY = os.environ.get("SETUP_TOKEN_SECRET") or hmac.new(
    _BASE_SECRET.encode("utf-8"), SETUP_KEY_LABEL, hashlib.sha256).hexdigest()

ALGORITHM = "HS256"
EXPIRY_HOURS = 48
TOKEN_PURPOSE = "integration_setup"
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:5173")


def _generate_token(user_id: int) -> str:
    payload = {
        "sub": str(user_id),
        "purpose": TOKEN_PURPOSE,
        "exp": datetime.now(timezone.utc) + timedelta(hours=EXPIRY_HOURS),
    }
    return jwt.encode(payload, SETUP_SIGNING_KEY, algorithm=ALGORITHM)


def _verify_token(token: str) -> str:
    """Returns user_id or raises HTTPException.

    Narrow by construction: this key signs nothing but setup tokens, and the
    purpose claim is still checked so that a future token minted under the same
    key cannot be presented here either.
    """
    try:
        payload = jwt.decode(token, SETUP_SIGNING_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="This setup link has expired. Ask your admin to send a new one.")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid setup link.")
    if payload.get("purpose") != TOKEN_PURPOSE:
        raise HTTPException(status_code=400, detail="Invalid setup link.")
    return str(payload["sub"])


# ── Admin: generate a setup link ─────────────────────────────────────────────

@router.post("/admin/setup-link/{user_id}")
def generate_setup_link(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Admin-only. Returns a 48-hour link the advisor can use to connect integrations."""
    if current_user.role not in ("org_admin", "super_admin", "god_admin"):
        raise HTTPException(status_code=403, detail="Admin only.")

    advisor = db.query(User).filter(User.id == user_id).first()
    if not advisor:
        raise HTTPException(status_code=404, detail="User not found.")
    # Org admin can only generate links for advisors in their own org
    if current_user.role == "org_admin" and advisor.organization_id != current_user.organization_id:
        raise HTTPException(status_code=403, detail="Cannot generate a setup link for a user outside your organization.")

    token = _generate_token(user_id)
    link = f"{FRONTEND_URL}/setup-integrations?token={token}"
    return {
        "link": link,
        "advisor_name": advisor.full_name,
        "expires_in_hours": EXPIRY_HOURS,
    }


# ── Public: token verification ───────────────────────────────────────────────

@router.get("/setup/verify")
def verify_setup_token(
    token: str = Query(...),
    db: Session = Depends(get_db),
):
    """Public — validates the setup token and returns advisor display info."""
    user_id = _verify_token(token)
    advisor = db.query(User).filter(User.id == user_id).first()
    if not advisor:
        raise HTTPException(status_code=404, detail="Advisor not found.")
    return {
        "user_id": advisor.id,
        "full_name": advisor.full_name,
        "email": advisor.email,
        "google_calendar_connected": bool(getattr(advisor, "google_refresh_token", None)),
        "microsoft_365_connected": bool(getattr(advisor, "microsoft_refresh_token", None)),
    }


# ── Public: start Google OAuth ───────────────────────────────────────────────

@router.get("/setup/google-connect")
def setup_google_connect(
    request: Request,
    token: str = Query(...),
    db: Session = Depends(get_db),
):
    """Public — validates the setup token and returns the Google OAuth URL.

    THE SETUP TOKEN IS WHAT PROVES WHOSE ACCOUNT THIS IS, and it is proved
    HERE, at initiation, while the token is in hand. What travels onward to
    Google is an opaque single-use handle to a transaction row that already
    names this advisor.

    What it used to be was `"setup:{user_id}"` — a string the callback parsed
    back into a user id, with nothing between the two ends but the browser. The
    prefix that told the callback "redirect to the setup page" is now the
    transaction's `flow` column, read from the database instead of from the URL.
    """
    user_id = _verify_token(token)
    advisor = db.query(User).filter(User.id == user_id).first()
    if not advisor or not advisor.is_active:
        raise HTTPException(status_code=404, detail="Advisor not found.")
    state = oauth_state_service.issue_state(
        db, provider=PROVIDER_GOOGLE, subject=advisor, flow=FLOW_SETUP,
        client_ip=(request.client.host if request.client else None))
    try:
        url = get_authorization_url(state)
    except RuntimeError as e:
        _log.error("Google OAuth URL generation failed for user %s: %s", user_id, e)
        raise HTTPException(status_code=500, detail="Calendar integration is not configured. Contact support.")
    return {"authorization_url": url}


# ── Public: start Microsoft OAuth ────────────────────────────────────────────

@router.get("/setup/microsoft-connect")
def setup_microsoft_connect(
    request: Request,
    token: str = Query(...),
    db: Session = Depends(get_db),
):
    """Public — validates the setup token and returns the Microsoft OAuth URL.

    Same shape as the Google route above, and for the same reason. The
    transaction is issued for PROVIDER_MICROSOFT specifically, so a state
    minted here cannot be presented to the Google callback.
    """
    user_id = _verify_token(token)
    advisor = db.query(User).filter(User.id == user_id).first()
    if not advisor or not advisor.is_active:
        raise HTTPException(status_code=404, detail="Advisor not found.")
    state = oauth_state_service.issue_state(
        db, provider=PROVIDER_MICROSOFT, subject=advisor, flow=FLOW_SETUP,
        client_ip=(request.client.host if request.client else None))
    try:
        url = get_microsoft_authorization_url(state)
    except RuntimeError as e:
        _log.error("Microsoft OAuth URL generation failed for user %s: %s", user_id, e)
        raise HTTPException(status_code=500, detail="Microsoft integration is not configured. Contact support.")
    return {"authorization_url": url}
