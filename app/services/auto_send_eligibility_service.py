"""
Auto-Send Eligibility Service

The actual brain of the auto-send queue. Per the explicit, careful
design agreed on for this feature: a reply only ever becomes eligible
for auto-drafting if it passes a check built SPECIFICALLY to answer
"is it safe to send something back with zero human review" - never
the general reply_classification_service alone, which was built to
answer a different question ("what does this reply mean") and was
never designed or tuned for this much higher-stakes decision.

PHASE 2 ELIGIBILITY RULES:
  The following general classifications can qualify:
    - "question"    — simple scheduling/logistics question
    - "interested"  — lead is clearly interested, asking about next steps
    - "callback"    — lead is asking to be called back at a specific time

  Hard-excluded classifications (no exceptions):
    - "dnc"            — legal/compliance, never auto-reply
    - "not_interested" — respect the opt-out; a human should handle any response
    - "wrong_number"   — auto-reply would go to the wrong person
    - "neutral"        — ambiguous; a human should decide

  Additional gates (all must pass):
  1. This dedicated classifier must ALSO independently confirm the
     message is genuinely simple and safe to respond to without a human.
     "what time works" is simple; "why haven't you called my mother
     back, what's going on" is also technically a question but is NOT
     eligible. An "interested" reply that actually contains grief or
     distress is also not eligible.
  2. The reply must NOT be the lead's first-ever reply - there must be
     at least one prior reply already on record, so there's established
     real context, not a cold first contact getting an unsupervised
     AI response with nothing to go on.
  3. Confidence must be HIGH. "Probably fine" is not a permitted basis
     for an unsupervised send - this is the one place in the whole app
     where "high confidence only" is a hard gate, not a soft preference.

Any one of these failing means NOT eligible - there are no partial
overrides, no "close enough."
"""

import os
import json
from openai import OpenAI

_client = None

ELIGIBLE_CLASSIFICATIONS = {"question", "interested", "callback"}
HARD_EXCLUDED_CLASSIFICATIONS = {"dnc", "not_interested", "wrong_number", "neutral"}


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    return _client


ELIGIBILITY_PROMPT = """You are deciding whether a text message reply from a funeral pre-planning sales lead is safe to auto-draft a response to with ZERO human review before it sends. This is a high-stakes decision - default to NOT eligible whenever there is any real doubt.

The lead's general intent has been classified as: {classification}

Prior conversation context (most recent exchanges, oldest first):
{conversation_history}

Current reply from the lead: "{body}"

A reply is ONLY eligible if it is clearly LOW-STAKES and the appropriate response is obvious. Eligible examples by classification:
- "question": "what time works", "where is your office", "is this still available", "do you have anything earlier"
- "interested": "yes I'd like to learn more", "sounds good, how do we get started", "I'd like to set up a time"
- "callback": "can you call me tomorrow at 2pm", "call me back when you get a chance", "reach me after 5pm"

A reply is NOT eligible if it contains ANY of the following, regardless of classification:
- Emotional content, grief, distress, or anything sensitive
- Genuine ambiguity about what the person actually wants
- Any hint of a complaint, frustration, or dissatisfaction
- Complex questions that require judgment, not just information
- Anything that a reasonable advisor would want to read before responding

Respond with ONLY a JSON object (no markdown, no preamble):
{{
  "eligible": true | false,
  "confidence": "high" | "medium" | "low",
  "reasoning": "<one short sentence>"
}}
"""


def check_auto_send_eligibility(
    body: str,
    general_classification: str,
    is_first_reply: bool,
    conversation_history: str = "",
) -> dict:
    """
    Returns {"eligible": bool, "confidence": str, "reasoning": str}.

    The general_classification gate is checked here in Python BEFORE
    any AI call is made at all — if it's hard-excluded, this returns
    ineligible immediately, with no API cost. The AI call below only
    ever runs for the classifications that have any real chance of
    qualifying.

    Never raises - any failure (API error, malformed response) returns
    eligible=False, since a failure to confidently determine
    eligibility is itself a reason NOT to auto-send, never a reason to
    proceed in the absence of a clear answer.
    """
    if general_classification in HARD_EXCLUDED_CLASSIFICATIONS:
        return {
            "eligible": False,
            "confidence": "high",
            "reasoning": f"Classification is '{general_classification}' — hard-excluded, never auto-reply.",
        }

    if general_classification not in ELIGIBLE_CLASSIFICATIONS:
        return {
            "eligible": False,
            "confidence": "high",
            "reasoning": f"Classification is '{general_classification}' — not in eligible set.",
        }

    if is_first_reply:
        return {
            "eligible": False,
            "confidence": "high",
            "reasoning": "This is the lead's first-ever reply — no established context yet.",
        }

    history_text = conversation_history.strip() if conversation_history else "(no prior messages)"

    try:
        response = _get_client().chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": ELIGIBILITY_PROMPT.format(
                    body=body,
                    classification=general_classification,
                    conversation_history=history_text,
                ),
            }],
            temperature=0.1,
            max_tokens=150,
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.strip("`").lstrip("json").strip()
        parsed = json.loads(raw)

        eligible = bool(parsed.get("eligible")) and parsed.get("confidence") == "high"
        return {
            "eligible": eligible,
            "confidence": parsed.get("confidence", "low"),
            "reasoning": parsed.get("reasoning", ""),
        }
    except Exception as e:
        return {
            "eligible": False,
            "confidence": "low",
            "reasoning": f"Eligibility check failed, defaulting to not eligible: {e}",
        }
