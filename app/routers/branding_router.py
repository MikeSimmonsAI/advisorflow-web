"""
Branding Router
----------------
Public endpoint — no auth required.
Returns the correct brand theme config based on the request's Host header.

Used by:
  - Frontend on load (to confirm the hostname-detected theme matches backend config)
  - White-label login pages (to render the correct logo/colors before auth)

This endpoint IS the source of truth. The frontend still detects a hostname
client-side for the very first paint - a fetch cannot beat the first frame, and
a flash of the wrong brand is worse than a bootstrap literal - but it then
fetches this and caches the answer, so every load after the first is driven by
the platform row. See app/services/brand_config.py and frontend/src/theme.js.

GET /branding
  Returns: { brand, displayName, supportEmail, accentColor, bgColor }

CORS: allowed from any origin (public endpoint, no sensitive data)
"""

import os
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user
from app.models.models import User, Organization

router = APIRouter(prefix="/branding", tags=["branding"])

# The hostname->brand table that used to live here is gone. It was one of four
# unsynchronised copies of the same brand data, none of which read the database,
# and its BookaBoost accent (#2fb6ff) had already drifted from the frontend's
# (#c9973d) with no consumer to notice. app/services/brand_config.py is the one
# resolver now: platform row first, frozen literals only as a fallback for a
# deployment whose columns are not backfilled yet.


@router.get("")
@router.get("/")
def get_branding(request: Request, db: Session = Depends(get_db)):
    """Public, unauthenticated: which brand this hostname is, and how it looks.

    The frontend themes itself from this and caches the answer, so a brand's
    name, colours, logo, favicon and tab title come from its platform row rather
    than from a literal compiled into the bundle.
    """
    from app.services.brand_config import public_payload
    host = (request.headers.get("host") or "").split(":")[0].lower()
    slug = os.environ.get("PLATFORM_SLUG", "").strip().lower() or None
    payload = public_payload(db, host, slug=None)
    # An explicit PLATFORM_SLUG wins only when the host told us nothing useful.
    if slug and payload.get("source") == "frozen" and payload.get("brand") != slug:
        payload = public_payload(db, host, slug=slug)
    return payload


@router.get("/org")
def get_org_branding(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Return the authenticated user's org branding from the database.
    Called by the frontend on login to apply per-org white-label customization.
    Falls back to None values if the org has no custom branding set.
    """
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org:
        return {
            "brand_name": None,
            "brand_logo_url": None,
            "brand_color_primary": None,
            "brand_color_accent": None,
            "favicon_url": None,
            "tagline": None,
            "support_email": None,
            "email_sender_name": None,
        }
    return {
        "brand_name": org.brand_name,
        "brand_logo_url": org.brand_logo_url,
        "brand_color_primary": org.brand_color_primary,
        "brand_color_accent": org.brand_color_accent,
        "favicon_url": getattr(org, "favicon_url", None),
        "tagline": getattr(org, "tagline", None),
        "support_email": getattr(org, "support_email", None),
        "email_sender_name": getattr(org, "email_sender_name", None),
    }
