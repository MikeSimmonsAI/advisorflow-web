"""Is this reply fit to send? Checked BEFORE anything automated goes out.

A failure here never edits the text and never sends a "fixed" version: the
reply is held for a person, with the reasons, exactly like an escalation. The
checks are about the conversation, not grammar:

    answered the question they actually asked?
    re-asked something they already told us?
    contradicted what they asked for (they said Monday, we said Friday)?
    stated a fact that is not on the record (wrong number of kids)?
    made a commitment this workspace may not make (price, offer, approval)?
    allowed at all (stop, consent, human review)?
    right length for the channel?
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from app.services.conversation_intel import extract as X

SMS_MAX = 480
EMAIL_MAX = 3000

_REASK = [
    # (fact key that answers it, pattern of a question asking for it)
    ("household.children", re.compile(r"\bhow many (kids|children)\b|\bdo you have (any )?(kids|children)\b", re.I)),
    ("household.spouse", re.compile(r"\bare you married\b|\bdo you have a (spouse|wife|husband|partner)\b", re.I)),
    ("intent.vertical", re.compile(r"\bwhat (type|kind) of (coverage|insurance|policy|plan)\b|\bwhat are you (looking|interested) (for|in)\b", re.I)),
    ("location.city", re.compile(r"\bwhat city\b|\bwhere (are you|do you live)\b", re.I)),
    ("person.age", re.compile(r"\bhow old are you\b|\bwhat is your age\b|\bmay i ask your age\b", re.I)),
    ("coverage.existing", re.compile(r"\bdo you (currently )?have (any )?(coverage|insurance|a policy)\b", re.I)),
    ("property.condition", re.compile(r"\b(what is|what'?s|how is) the condition\b|\bany (big |major )?repairs\b", re.I)),
    ("property.occupancy", re.compile(r"\bis anyone living\b|\bis it (vacant|occupied|rented)\b|\bany tenants\b", re.I)),
    ("energy.contract_end", re.compile(r"\bwhen does your (current )?(contract|plan) (end|expire)\b", re.I)),
]

_PRICE_COMMIT = re.compile(r"\$\s?\d|(\d+\s?(k|thousand))\b|\b(we('| wi)ll (buy|pay|offer)|our offer is|i can offer|"
                           r"guarantee(d)? (to )?(buy|close)|close in \d+ days)\b", re.I)
_INSURANCE_COMMIT = re.compile(r"\b(you('| a)re approved|guaranteed (approval|issue|acceptance)|your premium (will be|is) \$?|"
                               r"\$\s?\d+(\.\d\d)?\s?(/|a|per)\s?(mo|month))\b", re.I)
_ENERGY_COMMIT = re.compile(r"\b(lock(ed)? in at|your rate (will be|is))\s+\d", re.I)


def check_reply(ctx: Dict[str, Any], text: str, channel: str = "email") -> Dict[str, Any]:
    failures: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []
    body = X._clean(text)

    def fail(code, msg):
        failures.append({"code": code, "message": msg})

    if not body:
        fail("empty", "The reply is empty.")
        return {"passed": False, "failures": failures, "warnings": warnings}

    decision = ctx.get("ai_decision", {}).get(channel) or {}
    if decision and not decision.get("allowed", True) and decision.get("code") in (
            "stop", "human_review", "no_sms_consent", "no_email"):
        fail(decision["code"], decision["reason"])

    # 1. the question they asked
    for q in ctx.get("open_questions", []):
        words = X.content_words(q.get("value") or "")
        from app.services.conversation_intel.engine import _answers
        if not _answers(words, body):
            fail("ignores_open_question", "Does not address their question: “%s”" % q.get("value"))

    # 2. re-asking what they already told us
    known = {f["key"] for f in ctx.get("known_facts", [])}
    if ctx.get("vertical_intents"):
        known.add("intent.vertical")
    for key, rx in _REASK:
        if key in known and rx.search(body):
            label = next((f["label"] + ": " + str(f["value"]) for f in ctx.get("known_facts", []) if f["key"] == key),
                         key)
            fail("repeats_known_question", "Asks for something they already told us (%s)." % label)

    # 3. contradicting their requested day
    fu = ctx.get("follow_up") or {}
    if fu.get("date") and fu.get("value"):
        days_mentioned = set(re.findall(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", body.lower()))
        from datetime import date
        try:
            req = date.fromisoformat(fu["date"]).strftime("%A").lower()
        except ValueError:
            req = None
        if req and days_mentioned and req not in days_mentioned:
            fail("contradicts_follow_up", "They asked for %s (%s); the reply names %s." % (
                fu["value"], fu["date"], ", ".join(sorted(d.capitalize() for d in days_mentioned))))

    # 4. facts that are not on the record
    kids = next((f["value"] for f in ctx.get("known_facts", []) if f["key"] == "household.children"), None)
    m = re.search(r"\b(\d+|one|two|three|four|five|six)\s+(kids|children)\b", body.lower())
    if m and kids and kids.isdigit() and X._num(m.group(1)) != int(kids):
        fail("invented_fact", "Says %s %s; they told us %s." % (m.group(1), m.group(2), kids))

    # 5. commitments this vertical may not make in an automated message
    vertical = (ctx.get("lead") or {}).get("vertical")
    if vertical == X.WHOLESALE and _PRICE_COMMIT.search(body):
        fail("price_commitment", "Names a price, offer or closing commitment - owner judgment required.")
    if vertical == X.INSURANCE and _INSURANCE_COMMIT.search(body):
        fail("insurance_commitment", "Promises approval or quotes a premium - a licensed agent must handle this.")
    if vertical == X.ENERGY and _ENERGY_COMMIT.search(body):
        fail("rate_commitment", "States a locked rate - only quote rates from an actual rate request.")

    # 6. channel length
    if channel == "sms" and len(body) > SMS_MAX:
        fail("too_long", "%d characters is too long for a text (max %d)." % (len(body), SMS_MAX))
    if channel == "email" and len(body) > EMAIL_MAX:
        warnings.append({"code": "long", "message": "Long for an email reply (%d characters)." % len(body)})

    # 7. asked "who is this" / wrong person -> a sales reply is the wrong reply
    flags = {f["key"] for f in ctx.get("flags", [])}
    if "flag.identity" in flags and not re.search(r"\b(this is|my name is|i'?m)\b", body.lower()):
        fail("ignores_identity", "They asked who we are / said wrong person; the reply does not say who is writing.")

    return {"passed": not failures, "failures": failures, "warnings": warnings}
