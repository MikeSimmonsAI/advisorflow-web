"""EvoSys Business Development (PR #663): what a message may say about a
contact, and where each piece comes from.

Fill-ins come ONLY from what is saved. {event}, {referrer}, {personal_note},
{industry} and {market} have no fallback: a contact without that fact is
reported as missing and the email is held for a person - the sentence is
never guessed. {first_name} and {company} keep the existing neutral
fallbacks ("there", "your company"), which claim nothing.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

PLACEHOLDERS = ("first_name", "company", "event", "referrer", "personal_note", "industry", "market",
                "offer", "booking_link", "sender_name")
STRICT = ("event", "referrer", "personal_note", "industry", "market", "offer", "booking_link")
_RE = re.compile(r"\{([a-z_]+)\}")


def placeholders_in(text: str) -> List[str]:
    return _RE.findall(text or "")


def facts_for(opp, s, camp=None, lead=None) -> Dict[str, Dict[str, Any]]:
    """Each fill-in with its value (None when not saved) and where it came from."""
    from app.services.evosys_sales import campaigns as CA, grouping as G
    first = (opp.contact_name or "").strip().split()
    ch = (lead.source_channel if lead is not None else None) or G.channel_of(opp.source_system, opp.source)
    detail = (getattr(opp, "source_detail", None) or "").strip() or None
    ind = G.label_of(G.industries(s), lead.industry_key) if lead is not None and lead.industry_key else None
    mk = G.label_of(G.markets(s), lead.market_key) if lead is not None and lead.market_key else None
    src = camp if camp is not None else s
    return {
        "first_name": {"value": first[0].title() if first else None, "fallback": "there", "from": "contact name"},
        "company": {"value": (opp.company_name or "").strip() or None, "fallback": "your company", "from": "company"},
        "event": {"value": detail if ch == "networking" else None, "from": "where you met (saved on the contact)"},
        "referrer": {"value": detail if ch == "referral" else None, "from": "who referred them (saved on the contact)"},
        "personal_note": {"value": (getattr(opp, "intro_note", None) or "").strip() or None,
                          "from": "personal note (saved on the contact)"},
        "industry": {"value": ind, "from": "lead group"},
        "market": {"value": mk, "from": "lead group"},
        "offer": {"value": CA.real_offer(getattr(src, "offer", None)), "from": "campaign offer"},
        "booking_link": {"value": (getattr(src, "booking_url", None) or "").strip() or None, "from": "booking link"},
        "sender_name": {"value": (s.sender_name or "").strip() or None, "fallback": "", "from": "team settings"},
    }


def fill(text: str, facts: Dict[str, Dict[str, Any]]) -> Tuple[str, List[dict], List[str]]:
    """(filled text, facts used, missing strict facts). Unknown fill-ins are
    left as typed so a person sees them in the preview."""
    used, missing = [], []

    def sub(m):
        k = m.group(1)
        f = facts.get(k)
        if f is None:
            return m.group(0)
        v = f.get("value")
        if v:
            if k not in [u["key"] for u in used]:
                used.append({"key": k, "value": v, "from": f.get("from")})
            return str(v)
        if k in STRICT:
            if k not in missing:
                missing.append(k)
            return m.group(0)
        return f.get("fallback") or ""
    return _RE.sub(sub, text or ""), used, missing
