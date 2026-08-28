"""WHERE TWILIO SENDS DELIVERY RECEIPTS — resolved in exactly one place.

── THE FAILURE THIS FILE EXISTS TO END ────────────────────────────────────────
Production held 3,583 outbound messages over 30 days and ZERO delivery receipts.
Not one message had ever moved off `delivery_status='pending'`. The cause was not
Twilio, the network, the webhook endpoint or the signature check — it was that
Twilio was never told where to send a receipt in the first place:

  1. `sms_service` asked for a callback URL with
     `os.environ.get("API_BASE_URL", "")` and attached `status_callback` only if
     that came back non-empty. `API_BASE_URL` is declared NOWHERE in
     render.yaml for the backend service — only `VITE_API_BASE_URL` is, which is
     a build-time variable for the static FRONTEND and a different service. So
     the value was always "", the `if` was always false, and the parameter was
     silently dropped on every send.

  2. `cadence_service` — which sends the bulk of the volume — never passed a
     status callback at all, under any configuration.

Both failed SILENTLY. A missing callback URL is indistinguishable from a healthy
platform whose messages simply have not been reported on yet, which is why this
survived in production long enough to accumulate thousands of rows.

── SO THIS MODULE FAILS LOUDLY ────────────────────────────────────────────────
`status_callback_url()` logs an ERROR when it cannot resolve a base URL, rather
than returning "" and letting the caller quietly skip the parameter. A platform
that cannot receive delivery receipts should say so in its own logs.

── WHY THERE ARE FALLBACKS ────────────────────────────────────────────────────
The same idea already had three names in this codebase: `API_BASE_URL` (SMS),
`BACKEND_URL` (the voice service) and `VITE_API_BASE_URL` (the frontend build).
Reading only the one name that happened not to be set is precisely how this
broke. The resolver therefore accepts any of the backend-side spellings, and
falls back to deriving the origin from `GOOGLE_REDIRECT_URI`, which render.yaml
declares with a literal value on the backend service
(https://advisorflow-backend.onrender.com/calendar/oauth/callback) and is
therefore present wherever OAuth works at all.

Setting `API_BASE_URL` explicitly is still the correct configuration. The
fallbacks exist so that a missing env var degrades to "works" instead of to
"looks fine and collects nothing".
"""

import logging
import os
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

STATUS_CALLBACK_PATH = "/sms/webhook/status-callback"

# Checked in order. The first that yields an https origin wins.
_BASE_ENV_VARS = ("API_BASE_URL", "BACKEND_URL", "PUBLIC_API_BASE_URL")
# Not a base URL itself — an absolute URL whose ORIGIN is the backend's.
_DERIVE_FROM = ("GOOGLE_REDIRECT_URI", "MICROSOFT_REDIRECT_URI")


def _origin_of(value: str) -> str:
    """The scheme+host of an absolute URL, or "" if it is not one."""
    try:
        parts = urlsplit(value.strip())
    except Exception:
        return ""
    if not parts.scheme or not parts.netloc:
        return ""
    return "%s://%s" % (parts.scheme, parts.netloc)


def public_api_base() -> str:
    """The backend's own public origin, or "" if it cannot be determined."""
    for var in _BASE_ENV_VARS:
        raw = (os.environ.get(var) or "").strip()
        if not raw:
            continue
        origin = _origin_of(raw) or _origin_of("https://" + raw)
        if origin:
            return origin
    for var in _DERIVE_FROM:
        origin = _origin_of(os.environ.get(var) or "")
        if origin:
            logger.info(
                "twilio_callbacks: derived the public API base from %s. Set "
                "API_BASE_URL explicitly to make this deliberate.", var)
            return origin
    return ""


def status_callback_url() -> str | None:
    """The absolute URL Twilio should POST delivery receipts to.

    Returns None only when the backend's public origin cannot be determined at
    all — and says so at ERROR level, because in that state no delivery receipt
    can ever arrive and every message will sit on 'pending' forever.
    """
    base = public_api_base()
    if not base:
        logger.error(
            "twilio_callbacks: NO PUBLIC API BASE URL. Delivery receipts cannot "
            "be requested, so every outbound message will remain "
            "delivery_status='pending' permanently. Set API_BASE_URL on this "
            "service to its own public https origin.")
        return None
    if not base.startswith("https://"):
        # Twilio will POST to http, and the signature check reconstructs an
        # https URL behind Render's proxy — the two would never match.
        logger.warning(
            "twilio_callbacks: public API base is not https (%s). Delivery "
            "receipts may be rejected by signature validation.", base)
    return base.rstrip("/") + STATUS_CALLBACK_PATH


def apply_status_callback(kwargs: dict) -> dict:
    """Attach `status_callback` to a Twilio `messages.create(**kwargs)` payload.

    Every send path that records a `Message` row must go through this. A row
    written without a callback requested is a row that can never leave
    'pending', which is a permanently wrong number on the dashboard.
    """
    url = status_callback_url()
    if url:
        kwargs["status_callback"] = url
    return kwargs
