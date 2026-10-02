"""THE EMERGENCY BRAKE: one switch that stops every message and call leaving.

WHY THIS EXISTS
---------------
Outbound is gated in many careful places - consent, DNC/STOP, suppression,
per-source email gates, CADENCE_SMS_SENDING, the AI background switch, per-org
wholesale kill switches. Each answers "should THIS send happen?". None answers
the question asked at 2am when something is going wrong: "stop EVERYTHING,
now, whatever code path it comes from". There are a dozen-odd places that
build a Twilio client and call messages.create / calls.create, three that post
to Microsoft Graph sendMail, a few Resend sends and the Retell call API. A
switch threaded through each of those is a switch that misses the next one.

So the brake sits underneath them, at the transport each provider SDK uses:

    Twilio      TwilioHttpClient.request    POST .../Messages(.json) | .../Calls(.json)
    Resend      resend.Emails.send
    Microsoft   httpx Client.send           POST graph.microsoft.com .../sendMail
    Retell      httpx Client.send           POST api.retellai.com .../create-phone-call

ENGAGED when the environment variable OUTBOUND_EMERGENCY_STOP is truthy
("1", "true", "yes", "on"). Setting it on Render restarts the service with the
brake on; unsetting it releases it. Default: released - with the variable
absent nothing here changes behaviour (the wrappers call straight through).

ENGAGED MEANS: the provider call raises OutboundStopped before any network
I/O. Every caller already treats a provider exception as "not sent" (the
message is recorded failed / the send endpoint reports the error), which is
the honest outcome. Reads - Twilio lookups, Graph mailbox polling, delivery
status - are untouched, so inbound replies and STOP keep being processed.

Installed once at startup (app.main). Idempotent.
"""
from __future__ import annotations

import logging
import os
import re

log = logging.getLogger(__name__)

ENV = "OUTBOUND_EMERGENCY_STOP"
_TRUE = {"1", "true", "yes", "on"}

_TWILIO_SEND = re.compile(r"/(Messages|Calls)(\.json)?/?$", re.IGNORECASE)
_HTTPX_SENDS = (
    ("graph.microsoft.com", re.compile(r"/sendMail$", re.IGNORECASE)),
    ("api.retellai.com", re.compile(r"/create-phone-call$", re.IGNORECASE)),
)


class OutboundStopped(RuntimeError):
    """Raised instead of sending while the emergency brake is engaged."""


def engaged() -> bool:
    return (os.environ.get(ENV) or "").strip().lower() in _TRUE


def _stop(what: str):
    log.warning("outbound_brake: %s refused - %s is engaged", what, ENV)
    raise OutboundStopped("Outbound messaging is paused platform-wide (%s). Nothing was sent." % ENV)


def twilio_blocks(method: str, url: str) -> bool:
    return (method or "").upper() == "POST" and bool(_TWILIO_SEND.search((url or "").split("?", 1)[0]))


def httpx_blocks(method: str, url) -> bool:
    if (method or "").upper() != "POST":
        return False
    try:
        host = (url.host or "").lower()
        path = url.path or ""
    except AttributeError:
        from urllib.parse import urlparse
        u = urlparse(str(url))
        host, path = (u.hostname or "").lower(), u.path or ""
    return any(host == h and rx.search(path) for h, rx in _HTTPX_SENDS)


_installed = False


def install() -> bool:
    """Wrap the provider transports. Safe to call more than once."""
    global _installed
    if _installed:
        return True
    try:
        from twilio.http.http_client import TwilioHttpClient
        _orig_tw = TwilioHttpClient.request

        def _tw_request(self, method, url, *a, **kw):
            if engaged() and twilio_blocks(method, url):
                _stop("Twilio %s %s" % (method, url.rsplit("/", 1)[-1]))
            return _orig_tw(self, method, url, *a, **kw)
        _tw_request._brake = True
        TwilioHttpClient.request = _tw_request
    except Exception as exc:                                     # noqa: BLE001
        log.info("outbound_brake: twilio not wrapped (%s)", exc)
    try:
        import httpx
        for cls in (httpx.Client, httpx.AsyncClient):
            _orig = cls.send
            if getattr(_orig, "_brake", False):
                continue
            if cls is httpx.AsyncClient:
                async def _send(self, request, *a, __orig=_orig, **kw):
                    if engaged() and httpx_blocks(request.method, request.url):
                        _stop("%s %s" % (request.method, request.url.host))
                    return await __orig(self, request, *a, **kw)
            else:
                def _send(self, request, *a, __orig=_orig, **kw):
                    if engaged() and httpx_blocks(request.method, request.url):
                        _stop("%s %s" % (request.method, request.url.host))
                    return __orig(self, request, *a, **kw)
            _send._brake = True
            cls.send = _send
    except Exception as exc:                                     # noqa: BLE001
        log.info("outbound_brake: httpx not wrapped (%s)", exc)
    try:
        import resend
        _orig_rs = resend.Emails.send

        def _rs_send(*a, **kw):
            if engaged():
                _stop("Resend email")
            return _orig_rs(*a, **kw)
        _rs_send._brake = True
        resend.Emails.send = staticmethod(_rs_send) if isinstance(
            resend.Emails.__dict__.get("send"), staticmethod) else _rs_send
    except Exception as exc:                                     # noqa: BLE001
        log.info("outbound_brake: resend not wrapped (%s)", exc)
    _installed = True
    if engaged():
        log.warning("outbound_brake: %s is ENGAGED - no SMS, call or email will leave this process", ENV)
    return True


def status() -> dict:
    return {"variable": ENV, "engaged": engaged(), "installed": _installed,
            "covers": ["Twilio SMS/MMS and calls", "Resend email", "Microsoft 365 sendMail",
                       "Retell AI calls"],
            "note": "Engaged: every provider send raises before any network I/O; inbound "
                    "processing (replies, STOP, delivery receipts) continues."}
