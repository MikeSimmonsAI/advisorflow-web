"""
Advisor integration setup flow.

Allows an org admin to generate a time-limited link for an advisor so they
can connect their Google Calendar and/or Microsoft 365 account without
needing to be logged into BookaBoost. The admin copies the link and sends
it via email, SMS, Slack, etc. — the advisor just clicks it and authenticates.

Token format: signed HS256 JWT with purpose="integration_setup", exp=48h.
OAuth state format: "setup:{user_id}" — the Google/Microsoft callbacks detect
this prefix and redirect back to the /setup-integrations frontend page instead
of the normal /settings page.

Endpoints:
  POST /admin/setup-link/{user_id}  — admin generates the link
  GET  /setup/verify                — public; validate token, return advisor info
  GET  /setup/google-connect        — public; return Google OAuth URL
  GET  /setup/microsoft-connect     — public; return Microsoft OAuth URL
"""

import os
import jwt
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user
from app.models.models import User
from app.services.calendar_service import get_authorization_url
from app.services.microsoft_email_service import get_microsoft_authorization_url

router = APIRouter(tags=["setup"])

SECRET_KEY = os.environ.get("SECRET_KEY") or os.environ.get("JWT_SECRET") or ""
if not SECRET_KEY or len(SECRET_KEY) < 32:
    raise RuntimeError(
        "setup_router requires SECRET_KEY or JWT_SECRET env var (≥32 chars). "
        "Set it in your environment before starting the server."
    )
ALGORITHM = "HS256"
EXPIRY_HOURS = 48
TOKEN_PURPOSE = "integration_setup"
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:5173")


def _generate_token(user_id: int) -> str:
    payload = {
        "sub": str(user_id),
        "purpose": TOKEN_PURPOSE,
        "exp": datetime.utcnow() + timedelta(hours=EXPIRY_HOURS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _verify_token(token: str) -> str:
    """Returns user_id or raises HTTPException."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
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
    token: str = Query(...),
    db: Session = Depends(get_db),
):
    """Public — validates the setup token and returns the Google OAuth URL."""
    user_id = _verify_token(token)
    advisor = db.query(User).filter(User.id == user_id).first()
    if not advisor:
        raise HTTPException(status_code=404, detail="Advisor not found.")
    try:
        # "setup:{user_id}" prefix tells the callback to redirect to the setup page
        url = get_authorization_url(f"setup:{user_id}")
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"authorization_url": url}


# ── Public: start Microsoft OAuth ────────────────────────────────────────────

@router.get("/setup/microsoft-connect")
def setup_microsoft_connect(
    token: str = Query(...),
    db: Session = Depends(get_db),
):
    """Public — validates the setup token and returns the Microsoft OAuth URL."""
    user_id = _verify_token(token)
    advisor = db.query(User).filter(User.id == user_id).first()
    if not advisor:
        raise HTTPException(status_code=404, detail="Advisor not found.")
    try:
        url = get_microsoft_authorization_url(f"setup:{user_id}")
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"authorization_url": url}
