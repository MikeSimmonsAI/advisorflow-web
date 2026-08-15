"""
Branding Router
----------------
Public endpoint — no auth required.
Returns the correct brand theme config based on the request's Host header.

Used by:
  - Frontend on load (to confirm the hostname-detected theme matches backend config)
  - White-label login pages (to render the correct logo/colors before auth)

The frontend does hostname detection client-side (see theme.js) for zero-flash
rendering. This endpoint exists as the server-authoritative source of truth and
for any server-side rendering or health checks.

GET /branding
  Returns: { brand, displayName, supportEmail, accentColor, bgColor }

CORS: allowed from any origin (public endpoint, no sensitive data)
"""

import os
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/branding", tags=["branding"])

# Brand configs keyed by hostname substring
_BRAND_MAP = {
    "evosyspro": {
        "brand": "evosyspro",
        "displayName": "EvoSys Pro",
        "supportEmail": "support@evosyspro.live",
        "accentColor": "#087cff",
        "accentColor2": "#22a3ff",
        "greenColor": "#19d67c",
        "bgColor": "#040812",
        "logoInitial": "E",
        "theme": "evosyspro",
    },
    "harmonyhustle": {
        "brand": "harmonyhustle",
        "displayName": "Harmony Hustle",
        "supportEmail": "support@harmonyhustle.com",
        "accentColor": "#10b981",
        "accentColor2": "#34d399",
        "greenColor": "#10b981",
        "bgColor": "#030b07",
        "logoInitial": "HH",
        "theme": "harmonyhustle",
    },
}

_DEFAULT_BRAND = {
    "brand": "bookaboost",
    "displayName": "BookaBoost",
    "supportEmail": "support@bookaboost.live",
    "accentColor": "#2fb6ff",
    "accentColor2": "#1ef0a8",
    "greenColor": "#1ef0a8",
    "bgColor": "#03060f",
    "logoInitial": "BB",
    "theme": "bookaboost",
}


@router.get("")
def get_branding(request: Request):
    """Return the brand config for the requesting hostname."""
    host = request.headers.get("host", "").lower()

    # Allow PLATFORM_SLUG env override (useful for Render service config)
    platform_slug = os.environ.get("PLATFORM_SLUG", "").lower()

    for key, config in _BRAND_MAP.items():
        if key in host or key in platform_slug:
            return JSONResponse(content=config)

    return JSONResponse(content=_DEFAULT_BRAND)
