"""AI seller qualification — structured extraction, with a real fallback.

WHAT THE AI IS FOR HERE
-----------------------
One job: read what a seller actually said and turn it into the structured
answers the qualification engine reads — intent, asking price, timeline,
occupancy, condition, repairs, motivation, decision makers, callback time. It
does not decide whether to make an offer, it does not send anything, and it
never writes to a contract.

IT GOES THROUGH THE GATEWAY, LIKE EVERYTHING ELSE.
`app/services/ai_gateway.py` is the single seam every OpenAI call passes
through: approved models only, background/manual switches, spend caps, circuit
breaker, one log line per call. This module never constructs an OpenAI client.
The capability name is `wholesale_seller_qualify`, registered in the gateway's
CAPABILITY_MODELS beside every other capability.

THE FALLBACK IS NOT A STUB.
When the gateway refuses — background automation off, spend cap reached, no API
key, provider down — `extract_from_message` still returns an answer, from
`_deterministic_read`: a keyword and pattern pass that recognises STOP and
opt-out language, "not interested", "already sold", "wrong number", a price, a
timeline, and an occupancy word. It is plainly worse than the model at nuance
and plainly better than nothing at the things that matter most, which are the
refusals. Its results are marked `source="rules"` and any ambiguity sets
`needs_human`, so a deal never advances on a weak reading pretending to be a
strong one.

NOTHING HERE IS EVER INVENTED. Every field the reader cannot establish comes
back as None. The prompt says so, the parser drops anything that is not a
recognised value, and the deterministic pass only ever reports what it matched.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# What a seller's message means, as far as this module is concerned. These are
# the only values `intent` ever takes; anything else the model returns is
# discarded and the message is routed to a person.
INTENT_INTERESTED = "interested"
INTENT_MAYBE_LATER = "maybe_later"
INTENT_NOT_INTERESTED = "not_interested"
INTENT_WRONG_PERSON = "wrong_person"
INTENT_ALREADY_SOLD = "already_sold"
INTENT_DO_NOT_CONTACT = "do_not_contact"
INTENT_QUALIFIED = "qualified_opportunity"
INTENT_NEEDS_HUMAN = "needs_human"
INTENTS = (INTENT_INTERESTED, INTENT_MAYBE_LATER, INTENT_NOT_INTERESTED,
           INTENT_WRONG_PERSON, INTENT_ALREADY_SOLD, INTENT_DO_NOT_CONTACT,
           INTENT_QUALIFIED, INTENT_NEEDS_HUMAN)

# Intents that must stop outreach. The caller acts on these — see
# wholesale_service.apply_seller_reply — and DO_NOT_CONTACT additionally writes
# the platform's real suppression, not just a flag in this module.
STOP_INTENTS = (INTENT_NOT_INTERESTED, INTENT_DO_NOT_CONTACT,
                INTENT_ALREADY_SOLD, INTENT_WRONG_PERSON)

TIMELINES = ("asap", "30_days", "60_days", "90_days", "6_months", "no_rush")
CONDITIONS = ("excellent", "good", "fair", "poor", "distressed")
OCCUPANCIES = ("owner_occupied", "tenant", "vacant", "unknown")

_SYSTEM_PROMPT = """You read one message from a property owner and report only \
what they actually said.

Return a single JSON object with these keys. Use null for anything the message \
does not establish. NEVER guess, infer a number that was not stated, or fill a \
field to be helpful — a null is the correct and expected answer for most fields \
in most messages.

  intent: one of interested, maybe_later, not_interested, wrong_person, \
already_sold, do_not_contact, qualified_opportunity, needs_human
  is_available: true/false/null    (is the property still theirs and unsold)
  considering_selling: true/false/null
  asking_price: number or null     (only if they stated a price)
  timeline: one of asap, 30_days, 60_days, 90_days, 6_months, no_rush, or null
  property_condition: one of excellent, good, fair, poor, distressed, or null
  major_repairs: short string or null
  occupancy: one of owner_occupied, tenant, vacant, unknown, or null
  motivation: short string or null
  reason_for_selling: short string or null
  mortgage_note: short string or null   (only if they volunteered it)
  decision_makers: short string or null
  best_callback_time: short string or null
  summary: one sentence describing what they said
  confidence: 0-100, how confident you are in the intent

Use do_not_contact for STOP, "remove me", "don't text me again" or any explicit \
request to stop. Use needs_human when the message is angry, confusing, legally \
sensitive, mentions a death or a dispute, or you are not confident."""


def _get_client():
    """Test seam, same convention as every other AI caller in this codebase.

    Returns None in production so the gateway uses its own client; a test
    monkeypatches this to return a fake. Every gateway check still runs first.
    """
    return None


# Phase 6. HOW THE ASSISTANT PHRASES ITS OWN SUMMARY, and nothing else.
#
# Read the scope carefully, because it is the whole point of this setting:
# the assistant does not write to owners — nothing in this module does — so a
# "tone" cannot change any message anybody receives. What it CAN change is the
# one sentence the assistant writes back to the operator describing what the
# owner said, and that is the only thing it is allowed to touch.
#
# It is appended AFTER the system prompt, so it cannot displace any of the
# extraction rules, and it is never consulted at all on the deterministic
# opt-out path below, which returns before a model is involved.
TONES = ("professional", "conversational", "direct")
_TONE_INSTRUCTION = {
    "professional": "Write the `summary` field in plain professional English.",
    "conversational": "Write the `summary` field the way you would say it to a "
                      "colleague — natural and unstuffy.",
    "direct": "Write the `summary` field as briefly as it can be written "
              "without losing a fact.",
}


def extract_from_message(message_text: str, *, org_id: Optional[str] = None,
                         actor: Optional[str] = None, mode: str = "background",
                         direction: Optional[str] = None,
                         tone: Optional[str] = None,
                         client: Any = None) -> Dict[str, Any]:
    """Read one seller message into structured fields.

    Always returns a dict with the keys the profile columns expect plus
    `source` ("ai" or "rules"), `needs_human`, `needs_human_reason` and
    `confidence`. It never raises: an AI failure becomes a rules reading marked
    for review, because a qualification path that can throw is a qualification
    path that silently stops running.
    """
    text = (message_text or "").strip()
    if not text:
        return _blank("Empty message — nothing to read.")

    rules = _deterministic_read(text)

    # A deterministic opt-out is not a judgement call and is not sent to a model.
    # Recognising STOP is a compliance act, and it must not depend on a provider
    # being up, a switch being on, or a spend cap having room.
    if rules.get("intent") == INTENT_DO_NOT_CONTACT:
        rules["source"] = "rules"
        rules["summary"] = "The owner asked not to be contacted again."
        return rules

    try:
        from app.services import ai_gateway
        messages = [{"role": "system", "content": _SYSTEM_PROMPT}]
        # Scoped to the `summary` field by its own wording, and appended after
        # the extraction rules so it cannot displace one of them.
        if tone in _TONE_INSTRUCTION:
            messages.append({"role": "system",
                             "content": _TONE_INSTRUCTION[tone]})
        if direction:
            messages.append({"role": "system",
                             "content": "Additional instruction from this "
                                        "organization: " + str(direction)[:1000]})
        messages.append({"role": "user", "content": text[:4000]})
        resp = ai_gateway.chat_completion(
            feature="wholesale_seller_qualify",
            capability="wholesale_seller_qualify",
            mode=mode, actor=actor, org_id=org_id,
            messages=messages,
            client=client if client is not None else _get_client(),
            temperature=0,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content
        parsed = _parse(raw)
        if parsed is None:
            rules["source"] = "rules"
            rules["needs_human"] = True
            rules["needs_human_reason"] = ("The AI reply could not be read as "
                                           "structured data; a rules pass was used.")
            return rules
        parsed["source"] = "ai"
        # A low-confidence AI reading is routed to a person rather than acted on.
        if (parsed.get("confidence") or 0) < 50 or parsed.get("intent") == INTENT_NEEDS_HUMAN:
            parsed["needs_human"] = True
            parsed.setdefault("needs_human_reason",
                              "The AI was not confident about what this message means.")
        # The rules pass wins on refusals, always. A model that misses a STOP is
        # the one failure mode with a legal consequence, so the cheaper reader
        # gets the final word in that direction and only that direction.
        if rules.get("intent") in (INTENT_DO_NOT_CONTACT,) and \
                parsed.get("intent") != INTENT_DO_NOT_CONTACT:
            parsed["intent"] = INTENT_DO_NOT_CONTACT
            parsed["needs_human"] = True
            parsed["needs_human_reason"] = ("An opt-out phrase was detected that the "
                                            "AI did not report. Treated as an opt-out.")
        return parsed
    except Exception as exc:                                   # noqa: BLE001
        # Includes every ai_gateway.AIRefused subclass — switch off, model not
        # approved, spend cap, circuit open — and any provider error.
        log.info("wholesale AI unavailable (%s: %s); using the rules reader",
                 type(exc).__name__, str(exc)[:160])
        rules["source"] = "rules"
        rules["ai_unavailable_reason"] = "%s: %s" % (type(exc).__name__, str(exc)[:160])
        if rules.get("intent") in (None, INTENT_NEEDS_HUMAN):
            rules["needs_human"] = True
            rules["needs_human_reason"] = (
                "AI qualification is unavailable and the message could not be read "
                "confidently by pattern matching. A person should read it.")
        return rules


def _blank(reason: str) -> Dict[str, Any]:
    out = {k: None for k in (
        "intent", "is_available", "considering_selling", "asking_price", "timeline",
        "property_condition", "major_repairs", "occupancy", "motivation",
        "reason_for_selling", "mortgage_note", "decision_makers",
        "best_callback_time", "summary")}
    out.update({"confidence": 0, "source": "rules", "needs_human": True,
                "needs_human_reason": reason})
    return out


def _parse(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    """Read the model's JSON, discarding anything not on the allowed lists.

    A value outside the enumerations becomes None rather than being stored. The
    columns downstream are read by the qualification engine and by the UI, and
    an unrecognised string in one of them is a silent wrong answer.
    """
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?|```$", "", text).strip()
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    def enum(key, allowed):
        v = data.get(key)
        v = str(v).strip().lower() if v is not None else None
        return v if v in allowed else None

    def boolean(key):
        v = data.get(key)
        return v if isinstance(v, bool) else None

    def number(key):
        v = data.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
        if isinstance(v, str):
            cleaned = v.replace("$", "").replace(",", "").strip()
            try:
                return float(cleaned)
            except ValueError:
                return None
        return None

    def text_field(key, limit=500):
        v = data.get(key)
        if v is None:
            return None
        v = str(v).strip()
        return v[:limit] or None

    confidence = data.get("confidence")
    try:
        confidence = max(0, min(100, int(confidence)))
    except (TypeError, ValueError):
        confidence = 0

    return {
        "intent": enum("intent", INTENTS),
        "is_available": boolean("is_available"),
        "considering_selling": boolean("considering_selling"),
        "asking_price": number("asking_price"),
        "timeline": enum("timeline", TIMELINES),
        "property_condition": enum("property_condition", CONDITIONS),
        "major_repairs": text_field("major_repairs"),
        "occupancy": enum("occupancy", OCCUPANCIES),
        "motivation": text_field("motivation"),
        "reason_for_selling": text_field("reason_for_selling", 200),
        "mortgage_note": text_field("mortgage_note"),
        "decision_makers": text_field("decision_makers", 200),
        "best_callback_time": text_field("best_callback_time", 120),
        "summary": text_field("summary", 400),
        "confidence": confidence,
        "needs_human": False,
        "needs_human_reason": None,
    }


# ── The deterministic reader ────────────────────────────────────────────────
#
# Ordered most-decisive first. Everything it reports, it matched; it never fills
# a field from context.

_OPT_OUT = re.compile(
    r"\b(stop|stopall|unsubscribe|quit|cancel|end|opt\s*out|"
    r"remove me|take me off|do not (text|contact|call)|don'?t (text|contact|call) me)\b",
    re.I)
_NOT_INTERESTED = re.compile(
    r"\b(not interested|no thanks?|no thank you|not selling|never selling|"
    r"not for sale|leave me alone)\b", re.I)
_ALREADY_SOLD = re.compile(
    r"\b(already sold|it sold|we sold|sold it|sold last|under contract with|"
    r"no longer own|don'?t own)\b", re.I)
_WRONG_PERSON = re.compile(
    r"\b(wrong (number|person)|who is this|you have the wrong|"
    r"that'?s not me|i don'?t own)\b", re.I)
_INTERESTED = re.compile(
    r"\b(interested|tell me more|how much|what.{0,12}offer|make an offer|"
    r"i(')?m listening|what do you|send me|call me|yes)\b", re.I)
_LATER = re.compile(r"\b(maybe later|not right now|next year|in a few months|"
                    r"check back|call me in)\b", re.I)
_PRICE = re.compile(r"\$\s?([0-9][0-9,]{2,})(?:\s*(k|000))?|\b([0-9]{2,3})\s?k\b", re.I)
_TIMELINE_PATTERNS = (
    (re.compile(r"\b(asap|right away|immediately|as soon as possible|this week)\b", re.I), "asap"),
    (re.compile(r"\b(30 days|this month|within a month|next month)\b", re.I), "30_days"),
    (re.compile(r"\b(60 days|two months|2 months)\b", re.I), "60_days"),
    (re.compile(r"\b(90 days|three months|3 months)\b", re.I), "90_days"),
    (re.compile(r"\b(6 months|six months|half a year)\b", re.I), "6_months"),
    (re.compile(r"\b(no rush|no hurry|not in a hurry|whenever)\b", re.I), "no_rush"),
)
_OCCUPANCY_PATTERNS = (
    (re.compile(r"\b(vacant|empty|nobody lives|no one lives|sitting empty)\b", re.I), "vacant"),
    (re.compile(r"\b(tenant|renter|rented|renting it|leased)\b", re.I), "tenant"),
    (re.compile(r"\b(i live|we live|my home|our home|live there)\b", re.I), "owner_occupied"),
)
_CONDITION_PATTERNS = (
    (re.compile(r"\b(gutted|condemned|fire damage|falling apart|tear ?down|"
                r"needs everything)\b", re.I), "distressed"),
    (re.compile(r"\b(bad shape|rough shape|needs a lot|needs work|poor condition|"
                r"fixer)\b", re.I), "poor"),
    (re.compile(r"\b(needs some work|dated|could use|average|okay shape|ok shape)\b", re.I), "fair"),
    (re.compile(r"\b(good (shape|condition)|well maintained|move in ready)\b", re.I), "good"),
    (re.compile(r"\b(excellent|like new|fully (remodeled|renovated)|brand new)\b", re.I), "excellent"),
)


def _deterministic_read(text: str) -> Dict[str, Any]:
    """Pattern-match a message. Reports only what it matched, with reasons."""
    out = _blank("Read by pattern matching rather than by the AI.")
    out["needs_human"] = False
    out["needs_human_reason"] = None
    matched: List[str] = []

    if _OPT_OUT.search(text):
        out["intent"] = INTENT_DO_NOT_CONTACT
        out["confidence"] = 95
        out["summary"] = "The owner asked not to be contacted again."
        return out
    if _WRONG_PERSON.search(text):
        out["intent"] = INTENT_WRONG_PERSON
        out["confidence"] = 75
        matched.append("wrong person")
    elif _ALREADY_SOLD.search(text):
        out["intent"] = INTENT_ALREADY_SOLD
        out["is_available"] = False
        out["confidence"] = 75
        matched.append("already sold")
    elif _NOT_INTERESTED.search(text):
        out["intent"] = INTENT_NOT_INTERESTED
        out["considering_selling"] = False
        out["confidence"] = 75
        matched.append("not interested")
    elif _LATER.search(text):
        out["intent"] = INTENT_MAYBE_LATER
        out["confidence"] = 60
        matched.append("maybe later")
    elif _INTERESTED.search(text):
        out["intent"] = INTENT_INTERESTED
        out["considering_selling"] = True
        out["confidence"] = 60
        matched.append("interest")
    else:
        out["intent"] = INTENT_NEEDS_HUMAN
        out["confidence"] = 20
        out["needs_human"] = True
        out["needs_human_reason"] = ("No recognisable intent in this message and AI "
                                     "qualification did not run.")

    m = _PRICE.search(text)
    if m:
        try:
            if m.group(3):                       # "150k"
                out["asking_price"] = float(m.group(3)) * 1000
            else:
                value = float(m.group(1).replace(",", ""))
                if (m.group(2) or "").lower() == "k":
                    value *= 1000
                out["asking_price"] = value
            matched.append("a stated price")
        except (TypeError, ValueError):
            pass

    for pattern, value in _TIMELINE_PATTERNS:
        if pattern.search(text):
            out["timeline"] = value
            matched.append("a timeline")
            break
    for pattern, value in _OCCUPANCY_PATTERNS:
        if pattern.search(text):
            out["occupancy"] = value
            matched.append("occupancy")
            break
    for pattern, value in _CONDITION_PATTERNS:
        if pattern.search(text):
            out["property_condition"] = value
            matched.append("condition")
            break

    out["summary"] = ("Pattern match found: %s." % ", ".join(matched)) if matched \
        else "Nothing recognisable was matched in this message."
    return out
