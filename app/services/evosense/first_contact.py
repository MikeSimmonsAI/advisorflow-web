"""The first conversation with an owner - what may be said, on which channel,
and when nothing may be said at all.

A PUBLIC FORECLOSURE NOTICE IS NOT PERMISSION TO TEXT. The channels:

    CALL    a person (caller / VA) calls; the talk track below. Calling-time,
            do-not-call and the platform's DNC list apply (eligibility.py).
    SMS     only to a number that holds the owner's own consent of record
            (the permission link, or the owner texting first). The opener is
            the existing outreach.OPENER - a follow-up, never a cold message.
    EMAIL   only to an address the owner gave us or confirmed.

Every piece of wording here passes `guard()`, and `guard()` is what any AI- or
person-drafted first message must pass before it is used: it refuses wording
that pretends to be human, promises money, claims to stop a foreclosure,
gives legal advice, invents urgency or pressures the owner.

Nothing in this module sends anything.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.services.evosense import common as C

CALL_TRACK = [
    "Hi, is this {owner_first}? My name is {caller} with {business}.",
    "I'm calling about the property at {address}. Is this a good time for a quick minute?",
    "Are you the right person to talk to about that property?",
    "We buy houses in the area, as-is. Would you be open to talking about selling it, now or later?",
    "[If yes] What's the situation with the house - is anyone living there, and what kind of shape is it in?",
    "[If yes] Do you have a timeline in mind?",
    "[If they want a number] I can't give you a fair number until we've looked at the house and recent sales - "
    "can we set a time to see it?",
    "[If no or not now] Understood - thank you for your time. Would it be all right if we checked back later?",
    "[If they ask us to stop] Of course. I'll make sure nobody contacts you again.",
]
CALL_RULES = [
    "Say who you are and why you are calling in the first sentence.",
    "Do not say or imply that you can stop or delay a foreclosure.",
    "Do not give legal or tax advice; suggest they ask an attorney.",
    "Do not name a price or promise a cash offer before the deal is underwritten and approved.",
    "Do not mention the foreclosure unless they do - and never misstate its status.",
    "If they ask to stop, mark do-not-contact before you hang up.",
    "Texas calling hours (their local time): after 9 AM and before 9 PM Monday-Saturday, after noon and "
    "before 9 PM Sunday. Never a number on a do-not-call or suppression list.",
]
FIRST_EMAIL = {
    "subject": "Your property at {address}",
    "body": ("Hi {owner_first},\n\nThis is {caller} with {business}. Thank you for confirming we could reach you "
             "about {address}. If you are open to talking about selling - now or later - just reply with a good "
             "time to call, or tell us a little about the house and your timeline.\n\nIf you'd rather we didn't "
             "contact you again, reply STOP and we won't.\n\n{caller}\n{business}"),
}
AI_DISCLOSURE = "This is an automated assistant for {business}; a person on our team will follow up."

# Wording a first message must never contain.
FORBIDDEN = (
    ("pretends_human", re.compile(r"\b(i'?m|i am|this is) (a )?(real|actual|live) (person|human)\b|"
                                  r"\bnot a (bot|robot|machine)\b", re.I),
     "Do not claim to be a person"),
    ("promises_money", re.compile(r"\b(guarantee[ds]?|guaranteed (offer|price|cash)|cash offer of \$?\d|"
                                  r"we('ll| will) (pay|give) you \$?\d|offer you \$?\d)", re.I),
     "No offer or price before underwriting and approval"),
    ("stops_foreclosure", re.compile(r"\b(stop|prevent|save you from|avoid|halt|delay) (the |your )?"
                                     r"(foreclosure|auction|sale)\b", re.I),
     "Never claim to stop or delay a foreclosure"),
    ("legal_advice", re.compile(r"\b(you (should|must|need to) (file|declare)( for)? bankruptcy|legally you|"
                                r"your legal (rights|options) are|as your (lawyer|attorney))\b", re.I),
     "No legal advice"),
    ("false_urgency", re.compile(r"\b(act now|last chance|final notice|today only|expires (today|tonight)|"
                                 r"before it'?s too late|time is running out|urgent(ly)?)\b", re.I),
     "No invented urgency"),
    ("pressure", re.compile(r"\b(you will lose (everything|your home)|you('ll| will) regret|"
                            r"bank will take|evict(ed|ion) (you|soon)|don'?t miss out)\b", re.I),
     "No threats or pressure"),
    ("claims_records", re.compile(r"\b(our records show you owe|we know you('re| are) (behind|in default)|"
                                  r"you are being foreclosed)\b", re.I),
     "Do not state foreclosure or debt facts to the owner"),
)


def guard(text: str) -> Dict[str, Any]:
    problems = [{"code": code, "rule": rule, "matched": m.group(0)}
                for code, rx, rule in FORBIDDEN for m in [rx.search(text or "")] if m]
    return {"ok": not problems, "problems": problems}


def _fill(t: str, ctx: Dict[str, str]) -> str:
    return t.format(**{k: (v or "") for k, v in ctx.items()})


def approved_wording(ctx: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Every first-contact text the platform ships, filled in for a property."""
    from app.services.evosense import outreach as OU
    from app.services.evosense import permission as PM
    ctx = {"owner_first": "there", "caller": "[your name]", "business": "[company]", "address": "[address]",
           **(ctx or {})}
    return {"call_track": [_fill(x, ctx) for x in CALL_TRACK], "call_rules": CALL_RULES,
            "sms_opener": OU.OPENER.format(sender=ctx["business"], address=ctx["address"]),
            "permission_link": PM.message_text(ctx["business"], None if ctx["owner_first"] == "there"
                                               else ctx["owner_first"], "[link]")["text"],
            "email": {"subject": _fill(FIRST_EMAIL["subject"], ctx), "body": _fill(FIRST_EMAIL["body"], ctx)},
            "ai_disclosure": _fill(AI_DISCLOSURE, ctx)}


def plan(db, org_id: str, prop) -> Dict[str, Any]:
    """Which first contact is permitted for this property RIGHT NOW, and why
    the others are not. Read-only; nothing is sent."""
    from app.services.evosense import contacts as CT
    from app.services.evosense import eligibility as EL
    from app.services.evosense import evaluate as EV
    from app.services.evosense import permission as PM
    owner = CT.primary_owner(db, prop)
    strategy = EV.strategy_for(db, prop)
    cp, cc = CT.best_contact(db, prop)
    business = PM.business_name(db, org_id)
    first = (owner.display_name or "").split()[-1].title() if owner is not None and owner.display_name else None
    ctx = {"business": business, "address": prop.street_address or "", "owner_first": first or "there"}
    channels = []
    if cp is None:
        channels.append({"channel": "call", "permitted": False, "why": ["No phone number for the owner yet"]})
        channels.append({"channel": "sms", "permitted": False, "why": ["No phone number for the owner yet"]})
    else:
        try:
            sms = EL.check(db, prop, cp, strategy, mark=False) if cp.kind == "phone" else None
        except Exception as exc:  # noqa: BLE001
            sms = {"eligible": False, "blocks": ["ELIGIBILITY_CHECK_FAILED"], "summary": str(exc)[:120]}
        dnc = bool(sms and any(b in ("DNC", "SUPPRESSED", "OPTED_OUT", "PLATFORM_DNC", "DO_NOT_CONTACT")
                               for b in sms.get("blocks") or []))
        channels.append({"channel": "call", "permitted": cp.kind == "phone" and not dnc and cp.status == "active",
                         "why": ([] if cp.kind == "phone" and not dnc and cp.status == "active" else
                                 ["Number is on a do-not-contact list or not active"]),
                         "note": "A person calls, using the talk track; calling hours and DNC apply."})
        channels.append({"channel": "sms", "permitted": bool(sms and sms.get("eligible")),
                         "why": (sms or {}).get("blocks") or [],
                         "summary": (sms or {}).get("summary"),
                         "note": "Only with the owner's own consent of record (send a permission link after a call)."})
    status = PM.status_for(db, org_id, prop, [cp.value] if cp is not None and cp.kind == "phone" else [])
    return {"owner_known": owner is not None, "contact_confidence": (cc or {}).get("value"),
            "channels": channels, "permission": status, "wording": approved_wording(ctx),
            "notice": "A public foreclosure notice is not permission to text or auto-dial. Nothing is sent from here."}
