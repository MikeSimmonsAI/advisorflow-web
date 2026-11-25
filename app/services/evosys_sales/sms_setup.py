"""EvoSys BookIt texting number: check it and connect its replies.

Uses the platform Twilio account (TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN). The
number is looked up by its exact value; nothing is listed without one (Twilio
would otherwise hand back the account's first number, which may be another
program's). No credential is returned or logged.
"""
from __future__ import annotations

from typing import Dict
from urllib.parse import urlparse

INBOUND_PATH = "/sms/webhook/inbound"


def _client():
    from app.utils.twilio_webhook_guard import platform_account
    acct = platform_account()
    if not acct:
        return None
    from twilio.rest import Client
    return Client(acct[0], acct[1])


def _number(client, s):
    from app.services.evosys_sales.outreach import e164
    n = e164(s.sms_from_number)
    if not n:
        return None, "No texting number is saved for EvoSys BookIt."
    found = [x for x in client.incoming_phone_numbers.list(phone_number=n, limit=2)
             if (x.phone_number or "") == n]
    if not found:
        return None, "%s is not on the platform Twilio account." % n
    return found[0], None


def _inbound_url() -> str:
    from app.services.twilio_callbacks import public_api_base
    base = public_api_base()
    return (base + INBOUND_PATH) if base else ""


def check(s) -> Dict:
    client = _client()
    if client is None:
        return {"ok": False, "error": "The platform Twilio account is not configured."}
    try:
        num, err = _number(client, s)
        if err:
            return {"ok": False, "error": err}
        want = _inbound_url()
        caps = num.capabilities or {}
        return {"ok": True, "number": num.phone_number, "number_sid": num.sid,
                "sms_capable": bool(caps.get("sms")) if isinstance(caps, dict) else None,
                "replies_go_to": num.sms_url or None,
                "replies_connected": bool(want) and (num.sms_url or "") == want,
                "messaging_service": getattr(num, "messaging_service_sid", None)}
    except Exception as e:                       # noqa: BLE001
        return {"ok": False, "error": str(e)[:200]}


def connect_replies(s) -> Dict:
    client = _client()
    if client is None:
        return {"error": "The platform Twilio account is not configured."}
    want = _inbound_url()
    if not urlparse(want).scheme == "https":
        return {"error": "This server does not know its own public address."}
    try:
        num, err = _number(client, s)
        if err:
            return {"error": err}
        before = num.sms_url or None
        client.incoming_phone_numbers(num.sid).update(sms_url=want, sms_method="POST")
        return {"ok": True, "number": num.phone_number, "replies_went_to": before, "replies_go_to": want}
    except Exception as e:                       # noqa: BLE001
        return {"error": str(e)[:200]}
