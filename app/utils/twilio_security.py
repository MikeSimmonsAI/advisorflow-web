"""
Twilio Webhook Signature Validation
------------------------------------
All Twilio webhook endpoints (voice TwiML, call status, SMS inbound/status)
must validate the X-Twilio-Signature header before processing the request.
This prevents attackers from sending fake Twilio callbacks to:
  - Inject fake SMS replies
  - Mark leads as booked
  - Trigger AI conversation flows
  - Waste Twilio credits

How it works:
  Twilio signs each request using HMAC-SHA1 over the full callback URL +
  sorted POST params, using the account's auth token as the key.
  We re-compute the signature and reject any request that doesn't match.

Reference: https://www.twilio.com/docs/usage/webhooks/webhooks-security

Usage in a router:
    from app.utils.twilio_security import validate_twilio_webhook

    @router.post("/webhook/inbound")
    async def inbound(request: Request, db: Session = Depends(get_db)):
        await validate_twilio_webhook(request)
        ...

If validation fails, a 403 is raised and the request is dropped.
If TWILIO_AUTH_TOKEN is not configured, validation is skipped with a warning
(keeps local dev working without Twilio credentials).
"""

import base64
import hashlib
import hmac
import logging
import os
from urllib.parse import urlencode

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

# Platform-level Twilio auth token — used for org-level account validation.
# Per-advisor tokens are stored encrypted in the DB (twilio_auth_token_encrypted).
_PLATFORM_TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")


def _compute_signature(auth_token: str, url: str, params: dict) -> str:
    """
    Compute the expected Twilio signature for this request.
    Algorithm: HMAC-SHA1(auth_token, url + sorted(k+v for k,v in params))
    Output: base64-encoded digest.
    """
    # Sort params by key, concatenate key+value (no separator) then append to URL
    sorted_params = "".join(f"{k}{v}" for k, v in sorted(params.items()))
    s = url + sorted_params
    mac = hmac.new(auth_token.encode("utf-8"), s.encode("utf-8"), hashlib.sha1)
    return base64.b64encode(mac.digest()).decode("utf-8")


async def validate_twilio_webhook(
    request: Request,
    auth_token: str | None = None,
) -> None:
    """
    Validate the X-Twilio-Signature header on an inbound Twilio webhook.

    Args:
        request:    The FastAPI Request object.
        auth_token: Optional per-advisor auth token (for advisor-scoped webhooks).
                    Falls back to TWILIO_AUTH_TOKEN env var (platform-level).

    Raises:
        HTTPException(403) if the signature is invalid or missing.
        Does nothing (but logs a warning) if no auth token is configured at all.
    """
    token = auth_token or _PLATFORM_TWILIO_AUTH_TOKEN

    if not token:
        # No token configured — skip validation but warn so it shows in logs
        logger.warning(
            "twilio_security: TWILIO_AUTH_TOKEN not set — skipping signature "
            "validation on %s (configure env var to enable)",
            request.url.path,
        )
        return

    # Get the signature Twilio sent
    twilio_sig = request.headers.get("X-Twilio-Signature", "")
    if not twilio_sig:
        logger.warning(
            "twilio_security: missing X-Twilio-Signature on %s from %s",
            request.url.path,
            request.client.host if request.client else "unknown",
        )
        raise HTTPException(status_code=403, detail="Forbidden")

    # Reconstruct the full callback URL Twilio used
    # Twilio uses the URL exactly as configured in the console (https, no trailing slash)
    url = str(request.url)
    # Strip query string — Twilio includes query params in the URL itself for GET params
    # but for POST the body params go into the signature, not the query string

    # Parse the POST body params
    try:
        body = await request.body()
        # Twilio sends application/x-www-form-urlencoded
        from urllib.parse import parse_qs, unquote_plus
        raw_params = {}
        if body:
            for part in body.decode("utf-8").split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    raw_params[unquote_plus(k)] = unquote_plus(v)
    except Exception:
        raw_params = {}

    expected = _compute_signature(token, url, raw_params)

    if not hmac.compare_digest(expected, twilio_sig):
        logger.warning(
            "twilio_security: signature mismatch on %s from %s — possible spoofed webhook",
            request.url.path,
            request.client.host if request.client else "unknown",
        )
        raise HTTPException(status_code=403, detail="Forbidden")
