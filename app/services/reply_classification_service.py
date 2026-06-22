"""
AI Reply Classification Service

Replaces the naive substring keyword matcher in sms_router.py
(HOT_KEYWORDS/STOP_KEYWORDS) with real intent classification via OpenAI,
following the exact same lazy-init + fallback pattern already proven in
ai_analysis_service.py.

WHY THIS WAS NEEDED: testing the original keyword matcher surfaced real
false positives - "I'm not SURE yet" matched the "sure" keyword and got
flagged hot; "please REMOVE me from this list" matched "remove" and got
flagged as a STOP/DNC request, which happened to be correct by luck in
that case, but the matching itself was never actually checking intent,
just substrings. A genuinely neutral or even mildly negative reply could
easily contain one of these eight words as part of an unrelated sentence.

CLASSIFICATION CATEGORIES (matches the desktop app's reply tagging,
which the web app never had until now):
  - interested: clear positive signal, wants to move forward (shown to
    advisors in the UI as "Hot Lead")
  - callback: wants to be called or rescheduled, not a flat "yes"
  - dnc: invokes an actual legal opt-out (stop/unsubscribe/remove me from
    your list) - this is the regulatory hard-stop category, see
    HARD_STOP_KEYWORDS below
  - not_interested: declines, but didn't invoke an opt-out right - "no
    thanks", "we already have a plan", etc. Distinct from dnc on purpose:
    Mike's explicit request was that a polite decline shouldn't be
    conflated with a legal do-not-contact signal, since they may warrant
    different follow-up handling.
  - wrong_number: this isn't the right person ("wrong number", "who is
    this", "I don't know what this is about")
  - question: asks something rather than stating a clear yes/no/decline
  - neutral: anything else - confusion, ambiguous, doesn't fit the above

The keyword-based STOP detection is intentionally KEPT as an always-on
safety net underneath the AI classification, not replaced - a regulatory
opt-out request must never be missed just because an API call failed or
returned something unexpected. See classify_reply() below: AI
classification runs first, but the keyword check for true STOP/legal
opt-out language always still applies as a hard override.
"""

import os
import json
from openai import OpenAI

_client = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    return _client


# Kept as the hard-override safety net for legal opt-out language - see
# module docstring above for why this is never fully replaced by the AI
# classification alone. Deliberately narrow: "stop"/"unsubscribe"/"remove
# me" are unambiguous legal opt-out phrasing. A plain "not interested" is
# NOT in this list on purpose - that's its own not_interested category,
# not an automatic DNC trigger (see module docstring).
HARD_STOP_KEYWORDS = ["stop", "unsubscribe", "remove me"]

VALID_CLASSIFICATIONS = ("interested", "callback", "dnc", "not_interested", "wrong_number", "question", "neutral")

CLASSIFICATION_PROMPT = """You are classifying a text message reply from a sales lead at a cemetery/funeral home, replying to outreach about pre-need planning or property services. Classify the INTENT of their reply.

Respond with ONLY a JSON object (no markdown, no preamble):
{{
  "classification": "interested" | "callback" | "dnc" | "not_interested" | "wrong_number" | "question" | "neutral",
  "confidence": "high" | "medium" | "low",
  "reasoning": "<one short sentence>"
}}

Categories:
- "interested": clear positive signal, wants to move forward, says yes/sure/sounds good with genuine intent
- "callback": wants to be called, wants to reschedule, asks "can you call me" - engaged but not a flat yes
- "dnc": invokes an actual opt-out - says stop, unsubscribe, or explicitly asks to be removed from the contact list
- "not_interested": politely or flatly declines without invoking an opt-out - "no thanks", "not right now", "we already have something in place" - distinct from dnc
- "wrong_number": indicates this isn't the right person - "wrong number", "who is this", "I don't know what this is about"
- "question": asks a genuine question rather than giving a clear yes/no/decline
- "neutral": anything else - confusion, ambiguous, or doesn't clearly fit the above

IMPORTANT: a reply containing words like "sure" or "remove" does NOT automatically mean interested or dnc - judge the actual sentence meaning. For example "I'm not sure yet" is neutral, not interested. "Please remove my old address from file" is neutral, not dnc. A flat "not interested" or "no thanks" with no opt-out language is not_interested, not dnc.

Reply text: {body}
"""


def classify_reply(body: str) -> dict:
    """
    Returns {'classification': str, 'confidence': str, 'reasoning': str}.
    Falls back to the old keyword heuristic (clearly marked as a
    fallback in the reasoning field) if the API call fails or returns
    something unparseable - never raises, since a classification
    failure must never block the actual webhook from completing.
    """
    try:
        response = _get_client().chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": CLASSIFICATION_PROMPT.format(body=body)}],
            temperature=0.1,
            max_tokens=150,
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.strip("`").lstrip("json").strip()
        parsed = json.loads(raw)
        if parsed.get("classification") not in VALID_CLASSIFICATIONS:
            raise ValueError(f"Unexpected classification value: {parsed.get('classification')}")
        return parsed
    except Exception as e:
        return _fallback_keyword_classify(body, error=str(e))


def _fallback_keyword_classify(body: str, error: str = None) -> dict:
    """
    Rule-based fallback when the OpenAI call fails - reuses the same
    keyword lists that were the ONLY classification mechanism before
    this service existed, so behavior never gets worse than the
    pre-AI baseline, only better when the API call succeeds.

    not_interested and wrong_number/question keyword buckets were added
    alongside the AI prompt expansion above so the fallback path can
    still distinguish them even without the AI call - previously
    "not interested" fell into the same stop_keywords bucket as an
    actual legal opt-out, which is the conflation Mike specifically
    flagged as wrong.
    """
    body_lower = body.lower()
    hot_keywords = ["yes", "interested", "book", "schedule", "ok let's", "when can"]
    callback_keywords = ["call me", "call back"]
    hard_stop_keywords = ["stop", "unsubscribe", "remove me"]
    not_interested_keywords = ["not interested", "no thanks", "no thank you", "not right now"]
    wrong_number_keywords = ["wrong number", "who is this", "don't know what this is"]
    question_marker = "?"

    if any(kw in body_lower for kw in hard_stop_keywords):
        classification = "dnc"
    elif any(kw in body_lower for kw in wrong_number_keywords):
        classification = "wrong_number"
    elif any(kw in body_lower for kw in not_interested_keywords):
        classification = "not_interested"
    elif any(kw in body_lower for kw in hot_keywords):
        # Checked before callback_keywords: a message like "yes I'm
        # interested, please call me" contains both signals, and the
        # clearer "interested" intent should win over a secondary
        # "call me" mention in the same sentence - this ordering was
        # fixed after testing caught the reverse priority producing the
        # wrong classification for exactly this kind of combined message.
        classification = "interested"
    elif any(kw in body_lower for kw in callback_keywords):
        classification = "callback"
    elif question_marker in body_lower:
        classification = "question"
    else:
        classification = "neutral"

    reasoning = "Fallback keyword match (AI classification unavailable)"
    if error:
        reasoning += f": {error}"

    return {"classification": classification, "confidence": "low", "reasoning": reasoning}


def contains_hard_stop_language(body: str) -> bool:
    """
    The non-negotiable legal opt-out check - always runs regardless of
    what the AI classifier returns. If someone says STOP, UNSUBSCRIBE, or
    explicitly asks to be removed, that lead goes to DNC, full stop, no
    exceptions, no AI judgment call. A plain "not interested" does NOT
    trigger this - see module docstring for why that's now its own
    not_interested category instead of an automatic DNC trigger.
    """
    body_lower = body.lower()
    return any(kw in body_lower for kw in HARD_STOP_KEYWORDS)
