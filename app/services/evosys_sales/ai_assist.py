"""AI help for EvoSys Business Development (PR #663).

Two jobs, both started by a person and both only proposals:
  * draft_steps   - campaign messages from the campaign's own description.
  * draft_reply   - a short summary of a contact's reply and a draft answer.

What AI is never allowed to do here: switch a campaign on, approve or send
anything, invent a price, a meeting, a referral or a relationship, override an
opt-out, or confirm a booking. The prompts say so and the output is checked:
a campaign draft may only use the known placeholders, and a reply draft that
mentions a dollar figure while no offer is approved is refused.
"""
from __future__ import annotations

import json
import re
from typing import List, Optional

from app.services.evosys_sales import campaigns as C


class AIUnavailable(Exception):
    pass


def _client():
    return None  # test seam: tests patch this to return a fake client


def _call(feature: str, capability: str, actor: str, org: str, messages: list, max_tokens: int = 900) -> str:
    from app.services import ai_gateway
    try:
        resp = ai_gateway.chat_completion(feature=feature, capability=capability, mode=ai_gateway.MANUAL,
                                          actor=actor, org_id=org, client=_client(), messages=messages,
                                          temperature=0.4, max_tokens=max_tokens)
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:                                       # noqa: BLE001
        raise AIUnavailable(type(e).__name__)


_ALLOWED = set(C.PLACEHOLDERS)


def _check_placeholders(text: str) -> None:
    for k in re.findall(r"\{([a-z_]+)\}", text or ""):
        if k not in _ALLOWED:
            raise AIUnavailable("used an unknown placeholder {%s}" % k)


def draft_steps(camp, actor: str, org: str) -> List[dict]:
    warm = camp.tone == "warm"
    desc = {"name": camp.name, "kind": C.KIND_LABELS.get(camp.kind, camp.kind), "about": camp.context,
            "industry": camp.industry, "market": camp.market, "relationship": "warm" if warm else "none"}
    sys = ("You write short, plain, friendly outreach emails for Mike Simmons at EvoSys Pro. "
           "Return JSON only: {\"steps\":[{\"day\":int,\"subject\":str,\"body\":str}]} with 3 or 4 steps. "
           "Use ONLY these placeholders where a fact is needed: " + ", ".join("{%s}" % p for p in C.PLACEHOLDERS) +
           ". Never state a fact about the contact that is not a placeholder. Never invent a meeting, a referral, "
           "an ad, a price or a relationship. " +
           ("The contacts are people Mike met or was introduced to: the first email may use {event}, {referrer} "
            "or {personal_note} only if the campaign is about that. " if warm else
            "The contacts have no relationship with Mike: do not say you met them or were referred. ") +
           "Every email asks for a short call with {booking_link}. Sign as {sender_name}. Under 110 words each.")
    raw = _call("evosys_bd.campaign_draft", "campaign_copy", actor, org,
                [{"role": "system", "content": sys}, {"role": "user", "content": json.dumps(desc)}])
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise AIUnavailable("no draft returned")
    try:
        data = json.loads(m.group(0))
    except ValueError:
        raise AIUnavailable("unreadable draft")
    out = []
    for x in (data.get("steps") or [])[:6]:
        subj, body = str(x.get("subject") or "")[:200], str(x.get("body") or "")[:5000]
        _check_placeholders(subj + " " + body)
        out.append({"day": max(0, min(int(x.get("day") or 0), 90)), "channel": "email",
                    "subject": subj, "body": body})
    if not out:
        raise AIUnavailable("empty draft")
    return out


def draft_reply(opp, camp, s, reply_text: str, actor: str, org: str, offer_ready: bool) -> dict:
    facts = C.facts_for(opp, camp, s)
    known = {k: v["value"] for k, v in facts.items() if v.get("value") and k not in ("booking_link",)}
    sys = ("You help Mike Simmons at EvoSys Pro answer a reply from a contact. Return JSON only: "
           "{\"summary\": one sentence on what they want, \"draft\": a short reply Mike can edit}. "
           "Use only the known facts given. Never confirm a booking or a time: offer the booking link instead. "
           "Never invent a meeting, referral or relationship. " +
           ("Only describe pricing using the approved offer text exactly. " if offer_ready else
            "There is NO approved offer: do not mention any price, discount or figure; say Mike will follow up "
            "with details. ") +
           "Plain, warm, under 90 words.")
    user = json.dumps({"their_reply": (reply_text or "")[:3000], "known_facts": known,
                       "booking_link": facts["booking_link"]["value"]})
    raw = _call("evosys_bd.reply_draft", "draft_reply", actor, org,
                [{"role": "system", "content": sys}, {"role": "user", "content": user}], max_tokens=400)
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        data = json.loads(m.group(0)) if m else {}
    except ValueError:
        data = {}
    draft = str(data.get("draft") or "").strip()
    if not draft:
        raise AIUnavailable("no draft returned")
    if not offer_ready and re.search(r"\$\s?\d|\d+\s?(dollars|/mo|per month)", draft, re.I):
        raise AIUnavailable("the draft mentioned a price with no approved offer")
    return {"summary": str(data.get("summary") or "").strip()[:300], "draft": draft[:2000],
            "facts_used": [{"key": k, "value": v} for k, v in known.items()]}
