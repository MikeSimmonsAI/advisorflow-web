"""The prompt block every AI reply reads, and the Smart Composer's suggestion.

`prompt_block(ctx)` is the precise context package rendered for a model: facts
(stated) separated from inferences (not confirmed) and unknowns, the open
questions it MUST answer, what it must NOT ask again, the follow-up the
customer asked for (current value only - history is labelled as changed), the
objections on record and the workspace's voice. It is a few hundred words, not
the account's history.

`suggest(...)` never sends. With AI available (and the gateway allowing a
person-initiated call) it asks the model for a draft against that block; with
AI off it builds a plain template from the same memory and says so. Either
way the draft is run through the quality gate and the result is returned with
it, so the person sees what is wrong before they use it.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from app.services.conversation_intel import quality

log = logging.getLogger(__name__)

STYLES = {
    "default": "",
    "shorter": "Make it noticeably shorter - two or three sentences.",
    "warmer": "Make it warmer and more personal, without adding claims.",
    "more_direct": "Make it more direct: answer, then one clear next step.",
}


def prompt_block(ctx: Dict[str, Any]) -> str:
    lines = ["CONVERSATION MEMORY (from the record - do not contradict it):"]
    if ctx["known_facts"]:
        lines.append("Facts the customer stated:")
        lines += ["  - %s: %s (they said: “%s”)" % (f["label"], f["value"], (f["quote"] or "")[:120])
                  for f in ctx["known_facts"]]
    if ctx["inferences"]:
        lines.append("Inferences (NOT confirmed - never present these as facts):")
        lines += ["  - %s" % f["value"] for f in ctx["inferences"]]
    if ctx["unknowns"]:
        lines.append("Still unknown: " + "; ".join(u["slot"] for u in ctx["unknowns"]))
    if ctx["open_questions"]:
        lines.append("Questions they asked that have NOT been answered - your reply must address each:")
        lines += ["  - “%s”" % q["value"] for q in ctx["open_questions"]]
    known_keys = [f["label"] for f in ctx["known_facts"]]
    if known_keys:
        lines.append("Do NOT ask again about: " + ", ".join(known_keys))
    active_obj = [o for o in ctx["objections"] if o["status"] == "active"]
    if active_obj:
        lines.append("Concerns they raised (acknowledge, do not argue): " +
                     "; ".join("“%s”" % (o["quote"] or o["value"])[:120] for o in active_obj))
    fu = ctx.get("follow_up")
    if fu:
        lines.append("They asked us to follow up: %s%s. Use this, not any earlier day." % (
            fu["value"], " (%s)" % fu["date"] if fu.get("date") else ""))
    if ctx["commitments"]:
        lines.append("What we already promised: " + "; ".join(c["value"] for c in ctx["commitments"]))
    if ctx.get("next_best_question"):
        lines.append("If you ask anything, ask at most this one question: " + ctx["next_best_question"])
    lines.append("Voice for this workspace: " + ctx["voice"])
    return "\n".join(lines)


def _template(ctx: Dict[str, Any]) -> str:
    """A plain draft from memory alone. Honest about what it cannot answer."""
    first = (ctx["lead"]["name"] or "").split(" ")[0] or "there"
    parts = ["Hi %s," % first]
    vertical = ctx["lead"]["vertical"]
    facts = {f["key"]: f["value"] for f in ctx["known_facts"]}
    for q in ctx["open_questions"][:2]:
        text = q["value"]
        low = text.lower()
        if any(w in low for w in ("cost", "price", "how much", "premium", "rate")):
            who = []
            if facts.get("household.spouse"):
                who.append("your %s" % facts["household.spouse"])
            if facts.get("household.children"):
                c = facts["household.children"]
                who.append("your %s kids" % c if c.isdigit() else "your kids")
            if vertical == "insurance":
                parts.append("Great question on cost%s. It depends on age, health and how much coverage makes sense, "
                             "so the honest answer is a short review where I can show you real numbers." %
                             (" for you%s" % ("".join(", " + w for w in who)) if who else ""))
            elif vertical == "wholesale":
                parts.append("On price - I won't guess at a number by text. The owner reviews the property details "
                             "and gets back to you directly.")
            else:
                parts.append("On cost - I'll get you accurate numbers rather than a guess.")
        else:
            parts.append("About your question (“%s”) - I'll confirm the details and get back to you "
                         "with a straight answer." % text[:120])
    fu = ctx.get("follow_up")
    if fu and fu.get("date"):
        parts.append("I'll reach out %s as you asked." % fu["value"])
    elif ctx.get("next_best_question") and not ctx["open_questions"]:
        parts.append(ctx["next_best_question"])
    elif ctx["open_questions"] and ctx.get("next_best_question"):
        parts.append(ctx["next_best_question"])
    return " ".join(parts)


def suggest(db, lead, ctx: Dict[str, Any], *, channel: str = "email", style: str = "default",
            user=None) -> Dict[str, Any]:
    style = style if style in STYLES else "default"
    text, source, note = None, "template", "Built from conversation memory; AI drafting is off or unavailable."
    try:
        from app.services import ai_gateway
        from app.services.ai_gateway import MANUAL
        system = ("You draft a reply for a team member to review and send. Write ONLY the message body. "
                  "Answer every open question you can from the record; where the record does not hold the answer, "
                  "say you will confirm it - never invent prices, approvals, dates or facts. Ask at most one question. "
                  "%s\n\n%s" % ("Keep it under 320 characters - this is a text message." if channel == "sms" else
                                "Keep it under 120 words.", prompt_block(ctx)))
        convo = "\n".join("%s (%s): %s" % ("Customer" if m["direction"] == "inbound" else "Us", m["channel"],
                                           m["text"][:400]) for m in ctx["recent_messages"])
        msgs = [{"role": "system", "content": system},
                {"role": "user", "content": "Recent messages:\n%s\n\n%s Draft the reply." % (convo, STYLES[style])}]
        resp = ai_gateway.chat_completion(feature="conversation_intel.compose", capability="reply_draft",
                                          mode=MANUAL, org_id=lead.organization_id, messages=msgs,
                                          temperature=0.5, max_tokens=300)
        text = (resp.choices[0].message.content or "").strip().strip('"')
        source, note = "ai", "Drafted by AI from conversation memory."
    except Exception as exc:                                     # noqa: BLE001
        log.info("composer using template (%s)", type(exc).__name__)
        text = _template(ctx)
        if style == "shorter":
            text = " ".join(text.split(". ")[:2])
    gate = quality.check_reply(ctx, text, channel)
    return {"suggestion": text, "source": source, "note": note, "style": style, "channel": channel,
            "quality": gate}
