"""Which A2P campaign a text goes out under, and what that campaign may carry.

WHY THIS EXISTS
---------------
`sms_content_policy` strips every URL and phone number from every SMS, because
the only campaign in service (CO3YNIF) is registered `has_embedded_links=false`
and `has_embedded_phone=false`. That is a statement about ONE campaign. A
second campaign - registered with links and phone numbers - is being filed for
the funeral home / cemetery proof of concept, and the two must never be
confused: a link sent from a CO3YNIF number is filtered by the carrier (30007)
no matter what any other campaign allows.

So this module answers, per sender, "which campaign is this, is it approved,
and may it carry a link or a phone number?" - and `assert_content_allowed` is
the last check before the provider call: a body carrying something the sending
campaign is not approved for is REFUSED (never silently sent).

FAIL CLOSED
-----------
A sender this registry does not know is treated as the most restrictive
campaign there is: no links, no phone numbers. A campaign whose status is
anything but VERIFIED permits neither, whatever its registration says, because
an unapproved campaign's features are a request, not a permission.

CONFIGURATION WITHOUT A DEPLOY
------------------------------
`SMS_CAMPAIGN_REGISTRY_JSON` (a JSON list of entries, same shape as
`_BUILTIN`) adds a campaign or overrides a built-in one by `key`. That is where
the new campaign goes once Twilio assigns it - and where its status changes to
VERIFIED once it is approved. Nothing here calls Twilio.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

ENV_REGISTRY = "SMS_CAMPAIGN_REGISTRY_JSON"
STATUS_VERIFIED = "VERIFIED"

# Reason codes (stable strings).
LINKS_NOT_APPROVED = "CAMPAIGN_LINKS_NOT_APPROVED"
PHONE_NOT_APPROVED = "CAMPAIGN_PHONE_NOT_APPROVED"

# The campaign in service today. Facts read from the Twilio console on
# 2026-10-08 (read-only review): Low Volume Mixed, VERIFIED, links NO, phone NO.
_BUILTIN: List[Dict[str, Any]] = [
    {
        "key": "CO3YNIF",
        "campaign_id": "CO3YNIF",
        "messaging_service_sid": "MG37d057536564f3228788425b0f83ec92",
        "numbers": ["+14692241155"],
        "status": STATUS_VERIFIED,
        "has_embedded_links": False,
        "has_embedded_phone": False,
        "programs": ["general"],
        "note": "EvoSys Pro Low Volume Mixed - links and phone numbers NOT registered",
    },
]


class CampaignContentBlocked(ValueError):
    """A body the sending campaign is not approved to carry. `.reasons` holds codes."""

    def __init__(self, reasons: List[str], campaign_key: Optional[str]):
        self.reasons = list(reasons)
        self.campaign_key = campaign_key
        super().__init__("SMS_CAMPAIGN_CONTENT_BLOCKED: %s (campaign %s)" % (
            ",".join(self.reasons), campaign_key or "unregistered"))


def _norm(number: Optional[str]) -> Optional[str]:
    d = re.sub(r"\D", "", str(number or ""))
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return "+1" + d if len(d) == 10 else None


def registry() -> List[Dict[str, Any]]:
    """Built-ins, then env entries (an env entry with a built-in key replaces it)."""
    entries = {e["key"]: dict(e) for e in _BUILTIN}
    raw = (os.environ.get(ENV_REGISTRY) or "").strip()
    if raw:
        try:
            extra = json.loads(raw)
            if isinstance(extra, dict):
                extra = [extra]
            for e in extra or []:
                if isinstance(e, dict) and e.get("key"):
                    entries[str(e["key"])] = dict(e)
        except ValueError:
            # A malformed registry must not widen anything: log and keep built-ins.
            log.error("%s is not valid JSON - ignoring it (built-ins only)", ENV_REGISTRY)
    return list(entries.values())


def for_sender(from_number: Optional[str] = None,
               messaging_service_sid: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The campaign a sender belongs to, or None (unregistered)."""
    mg = (messaging_service_sid or "").strip()
    num = _norm(from_number)
    for e in registry():
        if mg and (e.get("messaging_service_sid") or "").strip() == mg:
            return e
        if num and num in {_norm(n) for n in (e.get("numbers") or [])}:
            return e
    return None


def is_approved(entry: Optional[Dict[str, Any]]) -> bool:
    return bool(entry) and str(entry.get("status") or "").upper() == STATUS_VERIFIED


def features(entry: Optional[Dict[str, Any]]) -> Dict[str, bool]:
    """What this campaign may carry. Unregistered or unapproved: nothing."""
    ok = is_approved(entry)
    return {"links": ok and bool(entry.get("has_embedded_links")),
            "phone": ok and bool(entry.get("has_embedded_phone"))}


def content_refusal(body: str, entry: Optional[Dict[str, Any]]) -> List[str]:
    """Reason codes for what `body` carries that `entry` may not. [] = fine."""
    from app.services import sms_content_policy as scp
    f = features(entry)
    reasons = []
    if not f["links"] and scp.contains_url(body):
        reasons.append(LINKS_NOT_APPROVED)
    if not f["phone"] and scp.contains_phone_number(body):
        reasons.append(PHONE_NOT_APPROVED)
    return reasons


def assert_content_allowed(body: str, *, from_number: Optional[str] = None,
                           messaging_service_sid: Optional[str] = None,
                           path: str = "send") -> Optional[Dict[str, Any]]:
    """Raise CampaignContentBlocked, or return the campaign entry (may be None)."""
    entry = for_sender(from_number, messaging_service_sid)
    reasons = content_refusal(body, entry)
    if reasons:
        log.warning("sms_campaigns BLOCKED %s", json.dumps({
            "path": path, "campaign": (entry or {}).get("key"), "reasons": reasons}))
        raise CampaignContentBlocked(reasons, (entry or {}).get("key"))
    return entry


def public_view() -> List[Dict[str, Any]]:
    """The registry for operators. Holds no secrets (SIDs are identifiers)."""
    out = []
    for e in registry():
        f = features(e)
        out.append({
            "key": e.get("key"), "campaign_id": e.get("campaign_id"),
            "messaging_service_sid": e.get("messaging_service_sid"),
            "numbers": list(e.get("numbers") or []), "status": e.get("status"),
            "registered_links": bool(e.get("has_embedded_links")),
            "registered_phone": bool(e.get("has_embedded_phone")),
            "links_allowed_now": f["links"], "phone_allowed_now": f["phone"],
            "programs": list(e.get("programs") or []), "note": e.get("note"),
        })
    return out
