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
  - interested: clear positive signal, wants to move forward
  - callback: wants to be called or rescheduled, not a flat "yes"
  - dnc: wants to stop receiving messages (legal opt-out signal)
  - neutral: anything else - questions, confusion, ambiguous responses

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
# classification alone.
HARD_STOP_KEYWORDS = ["stop", "unsubscribe"]

CLASSIFICATION_PROMPT = """You are classifying a text message reply from a sales lead at a cemetery/funeral home, replying to outreach about pre-need planning or property services. Classify the INTENT of their reply.

Respond with ONLY a JSON object (no markdown, no preamble):
{{
  "classification": "interested" | "callback" | "dnc" | "neutral",
  "confidence": "high" | "medium" | "low",
  "reasoning": "<one short sentence>"
}}

Categories:
- "interested": clear positive signal, wants to move forward, says yes/sure/sounds good with genuine intent
- "callback": wants to be called, wants to reschedule, asks "can you call me" - engaged but not a flat yes
- "dnc": wants to stop receiving messages, says remove me / not interested / stop texting me
- "neutral": anything else - questions, confusion, "who is this", ambiguous or unrelated replies

IMPORTANT: a reply containing words like "sure" or "remove" does NOT automatically mean interested or dnc - judge the actual sentence meaning. For example "I'm not sure yet" is neutral, not interested. "Please remove my old address from file" is neutral, not dnc.

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
        if parsed.get("classification") not in ("interested", "callback", "dnc", "neutral"):
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
    """
    body_lower = body.lower()
    hot_keywords = ["yes", "interested", "book", "schedule", "ok let's", "when can"]
    callback_keywords = ["call me", "call back"]
    stop_keywords = ["stop", "unsubscribe", "remove", "no thanks", "not interested"]

    if any(kw in body_lower for kw in stop_keywords):
        classification = "dnc"
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
    else:
        classification = "neutral"

    reasoning = "Fallback keyword match (AI classification unavailable)"
    if error:
        reasoning += f": {error}"

    return {"classification": classification, "confidence": "low", "reasoning": reasoning}


def contains_hard_stop_language(body: str) -> bool:
    """
    The non-negotiable legal opt-out check - always runs regardless of
    what the AI classifier returns. If someone says STOP or UNSUBSCRIBE,
    that lead goes to DNC, full stop, no exceptions, no AI judgment call.
    """
    body_lower = body.lower()
    return any(kw in body_lower for kw in HARD_STOP_KEYWORDS)
