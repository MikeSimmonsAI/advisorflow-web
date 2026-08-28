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


def _candidate_urls(request: Request) -> list:
    """Every URL string Twilio could plausibly have signed for this request.

    Built only from values the SERVER controls — the proxy's X-Forwarded-Proto
    and this service's own configured public origin. The request path is taken
    from the request itself; the scheme and host are not trusted from it.
    """
    path = request.url.path
    query = request.url.query
    suffix = path + (("?" + query) if query else "")

    seen, out = set(), []

    def add(u):
        if u and u not in seen:
            seen.add(u)
            out.append(u)

    # 1. The proxy's own statement about the original scheme.
    proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    host = (request.headers.get("x-forwarded-host")
            or request.headers.get("host") or "").split(",")[0].strip()
    if proto and host:
        add("%s://%s%s" % (proto, host, suffix))
    # 2. https on the Host header, for proxies that forward no proto header.
    if host:
        add("https://%s%s" % (host, suffix))
    # 3. The origin this service was configured with — the same one used to
    #    build the callback URL handed to Twilio in the first place.
    try:
        from app.services.twilio_callbacks import public_api_base
        base = public_api_base()
        if base:
            add(base.rstrip("/") + suffix)
    except Exception:                                        # pragma: no cover
        pass
    # 4. Whatever the app itself thinks, last — correct only without a proxy.
    add(str(request.url))
    return out


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

    # Reconstruct the full callback URL Twilio signed.
    #
    # ── WHY THIS IS NOT JUST str(request.url) ──────────────────────────────
    # Twilio signs the URL IT REQUESTED, which is https. This service runs
    # behind Render's TLS-terminating proxy and speaks plain http to it, so
    # `request.url` reports http://... unless uvicorn is told to trust the
    # proxy's forwarding headers — and it is not: the start command in
    # render.yaml is a bare `uvicorn app.main:app --host 0.0.0.0 --port $PORT`,
    # and uvicorn's `forwarded_allow_ips` defaults to 127.0.0.1, which Render's
    # proxy is not.
    #
    # The HMAC covers the URL string, so http vs https is a total mismatch:
    # every genuine Twilio callback would have been rejected with 403 the
    # moment TWILIO_AUTH_TOKEN was configured. It has not been on this service,
    # so validation was being skipped entirely and this never surfaced — the
    # bug was sitting behind a switch nobody had turned on yet.
    #
    # Candidates are built from SERVER-SIDE trust only: the proxy's own
    # X-Forwarded-Proto, and the public origin this service was configured
    # with. A signature must match one of them. Offering both does not weaken
    # anything — the HMAC still has to verify against the account's auth token.
    candidates = _candidate_urls(request)

    # The POST params Twilio signed.
    #
    # ── WHY request.form() AND NOT request.body() ──────────────────────────
    # These endpoints declare their fields as `Form(...)`, so FastAPI parses
    # the form BEFORE the endpoint body runs — which consumes the request
    # stream. A later `await request.body()` then returns b"", because Starlette
    # only caches `_body` when body() was what read the stream in the first
    # place. So `raw_params` came back EMPTY on every real callback and the
    # signature was computed over the bare URL with no parameters appended.
    #
    # Twilio always signs URL + sorted params, so that could never match. Every
    # genuine delivery receipt would have been rejected 403 the moment
    # TWILIO_AUTH_TOKEN was configured. `request.form()` IS cached
    # (Starlette keeps `_form`), so it returns the same values FastAPI parsed.
    raw_params = {}
    try:
        form = await request.form()
        raw_params = {k: str(v) for k, v in form.items()}
    except Exception:                                        # pragma: no cover
        pass
    if not raw_params:
        # Endpoints that read the raw body themselves rather than via Form().
        try:
            body = await request.body()
            from urllib.parse import unquote_plus
            if body:
                for part in body.decode("utf-8").split("&"):
                    if "=" in part:
                        k, v = part.split("=", 1)
                        raw_params[unquote_plus(k)] = unquote_plus(v)
        except Exception:
            raw_params = {}

    for candidate in candidates:
        if hmac.compare_digest(_compute_signature(token, candidate, raw_params),
                               twilio_sig):
            return

    logger.warning(
        "twilio_security: signature mismatch on %s from %s — tried %d candidate "
        "URL(s): %s. Possible spoofed webhook, or the public URL Twilio was "
        "given does not match this service's configured origin.",
        request.url.path,
        request.client.host if request.client else "unknown",
        len(candidates), candidates,
    )
    raise HTTPException(status_code=403, detail="Forbidden")
