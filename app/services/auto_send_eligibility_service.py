"""
Auto-Send Eligibility Service

The actual brain of the auto-send queue. Per the explicit, careful
design agreed on for this feature: a reply only ever becomes eligible
for auto-drafting if it passes a check built SPECIFICALLY to answer
"is it safe to send something back with zero human review" - never
the general reply_classification_service alone, which was built to
answer a different question ("what does this reply mean") and was
never designed or tuned for this much higher-stakes decision.

THE FULL ELIGIBILITY RULE, exactly as agreed:
  1. The reply's general classification must be "question" - a simple
     scheduling/logistics question, e.g. "what time," "where's your
     office," "is this still available." Every OTHER classification
     (interested/hot, callback, dnc, not_interested, wrong_number,
     neutral) is hard-excluded, no exceptions - those all carry either
     emotional weight, ambiguity, or legal/compliance significance that
     a human must see first.
  2. This dedicated classifier must ALSO independently confirm the
     question is genuinely simple/logistical, not an emotionally
     loaded or ambiguous question that happens to end in a "?" -
     "what time works" is simple; "why haven't you called my mother
     back, what's going on" is also technically a question but is NOT
     eligible. This is the real, separate judgment call this service
     exists to make - the general classifier never tried to distinguish
     between these two cases at all.
  3. The reply must NOT be the lead's first-ever reply - there must be
     at least one prior reply already on record, so there's established
     real context, not a cold first contact getting an unsupervised
     AI response with nothing to go on.
  4. Confidence must be HIGH, not medium or low. "Probably fine" is not
     a permitted basis for an unsupervised send - this is the one place
     in the whole app where "high confidence only" is a hard gate, not
     a soft preference.

Any one of these failing means NOT eligible - there are no partial
overrides, no "close enough." A reply that isn't eligible simply
behaves exactly as it does today: it shows up in the normal Replies
inbox, with no special treatment at all.
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


ELIGIBILITY_PROMPT = """You are deciding whether a text message reply from a sales lead is safe to auto-draft a response to with ZERO human review before it sends. This is a high-stakes decision - default to NOT eligible whenever there is any real doubt.

A reply is ONLY eligible if it is a simple, low-stakes SCHEDULING OR LOGISTICS question - nothing else. Examples of ELIGIBLE replies: "what time works", "where is your office located", "is this still available", "do you have anything earlier in the week".

A reply is NOT eligible if it contains ANY of the following, even if phrased as a question:
- Emotional content, grief, distress, or anything sensitive (e.g. "why hasn't anyone called my mother back")
- Genuine ambiguity about what the person actually wants
- Any hint of a complaint, frustration, or dissatisfaction
- Anything that isn't purely about scheduling/logistics

Respond with ONLY a JSON object (no markdown, no preamble):
{{
  "eligible": true | false,
  "confidence": "high" | "medium" | "low",
  "reasoning": "<one short sentence>"
}}

Reply text: {body}
"""


def check_auto_send_eligibility(body: str, general_classification: str, is_first_reply: bool) -> dict:
    """
    Returns {"eligible": bool, "confidence": str, "reasoning": str}.

    The general_classification gate (rule 1 above) is checked here in
    Python BEFORE any AI call is made at all - if it's not "question",
    this returns ineligible immediately, with no API cost and no risk
    of an AI call somehow overriding a hard-excluded category. The AI
    call below only ever runs for the one category that has any real
    chance of qualifying at all.

    Never raises - any failure (API error, malformed response) returns
    eligible=False, since a failure to confidently determine
    eligibility is itself a reason NOT to auto-send, never a reason to
    proceed in the absence of a clear answer.
    """
    if general_classification != "question":
        return {"eligible": False, "confidence": "high", "reasoning": f"Classification is '{general_classification}', not 'question' - hard-excluded."}

    if is_first_reply:
        return {"eligible": False, "confidence": "high", "reasoning": "This is the lead's first-ever reply - no established context yet."}

    try:
        response = _get_client().chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": ELIGIBILITY_PROMPT.format(body=body)}],
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
        return {"eligible": False, "confidence": "low", "reasoning": f"Eligibility check failed, defaulting to not eligible: {e}"}
