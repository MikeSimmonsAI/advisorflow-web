"""Pure reply-handling rules for a location outreach program. Stdlib only.

Classification (HOT / ACTIVE / LOW / OPT-OUT / BAD DATA / WRONG PERSON),
intents, urgency, the DRAFT suggested reply, and the small pure decisions that
responses.py acts on (cadence, alert routing, staff-alert gating). Kept free of
framework imports so the same code runs in the app and in the stdlib readiness
harness (scripts/sci_readiness_harness.py). Nothing here sends anything.
"""
import re
from typing import Dict, List, Optional, Tuple

HOT, ACTIVE, LOW, OPT_OUT, BAD_DATA = "hot", "active", "low", "opt_out", "bad_data"
WRONG_PERSON = "wrong_person"
LABELS = {HOT: "HOT", ACTIVE: "ACTIVE", LOW: "LOW", OPT_OUT: "OPT-OUT", BAD_DATA: "BAD DATA",
          WRONG_PERSON: "WRONG PERSON"}
# WHAT the person is asking for / telling us (several can apply). Kept apart
# from the class above, which is the URGENCY / handling lane: HOT, ACTIVE, LOW,
# or a terminal lane (OPT-OUT, BAD DATA, WRONG PERSON).
APPOINTMENT, PRICING, INFORMATION = "appointment_intent", "pricing_question", "information_request"
BENEFITS, VETERAN, CEMETERY = "benefits_question", "veteran_planning_question", "cemetery_interest"
CREMATION, GENERAL_PLANNING, OBJECTION = "cremation_interest", "general_planning", "objection"
NOT_INTERESTED, ACKNOWLEDGMENT = "not_interested", "simple_acknowledgment"
I_OPT_OUT, I_WRONG_PERSON, I_BAD_DATA = "opt_out", "wrong_person", "bad_data"
INTENT_LABELS = {
    APPOINTMENT: "APPOINTMENT INTENT", INFORMATION: "INFORMATION REQUEST", PRICING: "PRICING QUESTION",
    BENEFITS: "BENEFITS QUESTION", VETERAN: "VETERAN PLANNING QUESTION", CEMETERY: "CEMETERY INTEREST",
    CREMATION: "CREMATION INTEREST", GENERAL_PLANNING: "GENERAL PLANNING", OBJECTION: "OBJECTION",
    NOT_INTERESTED: "NOT INTERESTED", I_WRONG_PERSON: "WRONG PERSON", I_OPT_OUT: "OPT-OUT",
    I_BAD_DATA: "BAD DATA", ACKNOWLEDGMENT: "SIMPLE ACKNOWLEDGMENT",
    "missed_call": "MISSED CALL",
}
_INTENT_PATTERNS = [
    (APPOINTMENT, r"\b(appointment|appt|schedule|book|meet(ing)?|visit|tour|come (by|in|out)|stop by|"
                  r"set up a time|a time to|available|availability|this week|next week)\b"),
    (PRICING, r"\b(price|prices|pricing|cost|costs|how much|payment|payments|afford|financ\w*|plan options|discount)\b"),
    (INFORMATION, r"\b(more info|information|details|brochure|guide|packet|flyer|send (me|it|the|over)|"
                  r"what (does|is|are)|how does|include|includes|explain|learn more)\b"),
    (BENEFITS, r"\b(benefit|benefits|eligib\w*|entitled|qualify|qualifies|allowance|va (pays|covers|will)|"
               r"covered|coverage|insurance)\b"),
    (VETERAN, r"\b(veteran|veterans|va|military|served|service member|army|navy|marine|marines|air force|"
              r"coast guard|dd ?214|honorable discharge|national cemetery|flag)\b"),
    (CEMETERY, r"\b(cemetery|plot|plots|grave|graves|burial|bury|lot|lots|mausoleum|crypt|niche|headstone|"
               r"marker|monument|space|spaces)\b"),
    (CREMATION, r"\b(cremat\w*|urn|urns|ashes|scatter\w*|columbarium)\b"),
    (GENERAL_PLANNING, r"\b(pre-?plan\w*|plan ahead|planning ahead|arrangements?|pre-?arrang\w*|"
                       r"pre-?need|final wishes|funeral plan\w*|my wishes|prepaid|pre-?pay\w*)\b"),
    (OBJECTION, r"\b(too expensive|can'?t afford|not (right )?now|not ready|maybe later|later on|"
                r"already (have|has|bought|made|got)|already taken care|think about it|talk (to|with) my|"
                r"not sure|busy right now|bad time)\b"),
]
SLA_BREACH_LABEL = "HOT RESPONSE — NOT YET HANDLED"

_HOT_PATTERNS = [
    (r"\b(appointment|appt|schedule|book|meet(ing)?|visit|tour|come (by|in|out)|stop by)\b", "wants a meeting or visit"),
    (r"\b(price|pricing|cost|costs|how much|payment|plan options|financ)", "asked about price"),
    (r"(?<!not )(?<!no longer )\b(interested|yes please|sounds good|i('| a)m in|sign me up|let'?s do)\b", "interested"),
    (r"\b(call me|reach (out|me)|get back to me|follow ?up|contact me|text me)\b", "requests follow-up"),
]
# OPT-OUT only on an unmistakable request: the whole message is a carrier stop
# word, or an explicit phrase. "Can I stop by Tuesday?" is a visit, not a STOP.
_STOP_WORDS = r"^\s*(stop|stopall|unsubscribe|end|quit|cancel|optout|opt out|remove)\s*[.!]*\s*$"
_OPT_OUT_PHRASES = (r"\b(remove me|take me off|unsubscribe me|opt me out|do not (contact|text|email|call) me|"
                    r"don'?t (contact|text|email|call) me|stop (texting|emailing|contacting) me|leave me alone|"
                    r"not interested|no thank(s| you)|lose my number)\b")
# BAD DATA is about the RECORD (wrong person / number), never about a death in
# the family - for a funeral home a bereaved reply is the most important one.
_BAD_DATA_PATTERNS = (r"\b(wrong (number|person|email|address)|this is not (him|her|me)|not (him|her)|"
                      r"no one (here )?by that name|don'?t know (who|what) (this|you|that)|who is this)\b")
# Of those, the ones about the PERSON (not the number/address on file).
_WRONG_PERSON_PATTERNS = (r"\b(wrong person|this is not (him|her|me)|not (him|her)|no one (here )?by that name|"
                          r"(he|she) (doesn'?t|does not) live here|you have the wrong(?! (?:number|email|address))|not the right person)\b")
_BEREAVEMENT = r"\b(passed away|passed on|died|deceased|funeral for|lost my (husband|wife|mother|father|son|daughter))\b"
_LOW_PATTERNS = r"^\s*(ok(ay)?|k|thanks?( you)?|ok thanks?|thank you|thx|got it|received|👍|🙏)[\s.!]*$"

_CLASS_FROM_REPLY = {
    "interested": HOT, "callback": HOT, "question": ACTIVE, "neutral": LOW,
    "not_interested": OPT_OUT, "dnc": OPT_OUT, "wrong_number": BAD_DATA,
}


def classify(body: str, reply_classification: Optional[str] = None) -> Dict:
    """Deterministic and explainable. A stated need beats a stray keyword."""
    text = (body or "").strip().lower()
    hot = [why for pat, why in _HOT_PATTERNS if re.search(pat, text)]
    bereaved = bool(re.search(_BEREAVEMENT, text))
    if re.search(_STOP_WORDS, text):
        return {"class": OPT_OUT, "reasons": ["asked to stop"]}
    if re.search(_WRONG_PERSON_PATTERNS, text) and not hot:
        return {"class": WRONG_PERSON, "reasons": ["says we have the wrong person"]}
    if re.search(_BAD_DATA_PATTERNS, text) and not hot:
        return {"class": BAD_DATA, "reasons": ["says the number / address is wrong"]}
    weak = {"interested", "requests follow-up"}
    if hot and re.search(_OPT_OUT_PHRASES, text) and set(hot) <= weak:
        return {"class": OPT_OUT, "reasons": ["opted out or not interested"]}
    if hot:
        if bereaved:
            hot = ["bereavement mentioned"] + hot
        return {"class": HOT, "reasons": hot}
    if re.search(_OPT_OUT_PHRASES, text):
        return {"class": OPT_OUT, "reasons": ["opted out or not interested"]}
    if bereaved:
        return {"class": HOT, "reasons": ["bereavement mentioned - needs a personal reply"]}
    upstream = _CLASS_FROM_REPLY.get((reply_classification or "").lower())
    if upstream in (OPT_OUT, BAD_DATA, HOT):
        return {"class": upstream, "reasons": ["classified as %s on arrival" % reply_classification]}
    if re.search(_LOW_PATTERNS, text) or not text:
        return {"class": LOW, "reasons": ["acknowledgement only"]}
    if re.search(_INFO_ASK, text):
        return {"class": ACTIVE, "reasons": ["wants more information"]}
    if "?" in text or upstream == ACTIVE:
        return {"class": ACTIVE, "reasons": ["asked a question"]}
    return {"class": ACTIVE if len(text.split()) >= 4 else LOW,
            "reasons": ["engaged reply" if len(text.split()) >= 4 else "short reply"]}


_INFO_ASK = r"\b(more info|information|details|send (me|it|the)|brochure|guide|packet|flyer|mail me)\b"


def intents(body: str, cls: Optional[str] = None) -> List[str]:
    """Every intent the text shows, plus the one its class implies (opt-out,
    wrong person, bad data, a bare acknowledgement, not interested)."""
    text = (body or "").strip().lower()
    found = [k for k, pat in _INTENT_PATTERNS if re.search(pat, text)]
    if re.search(_OPT_OUT_PHRASES, text) and re.search(r"\b(not interested|no thank(s| you))\b", text):
        found.append(NOT_INTERESTED)
    implied = {OPT_OUT: I_OPT_OUT, WRONG_PERSON: I_WRONG_PERSON, BAD_DATA: I_BAD_DATA}.get(cls)
    if implied:
        found.append(implied)
    if cls == LOW and not found and (re.search(_LOW_PATTERNS, text) or not text):
        found.append(ACKNOWLEDGMENT)
    out = []
    for k in found:
        if k not in out:
            out.append(k)
    return out


def urgency(cls: str) -> str:
    """HOT / ACTIVE / LOW, separate from WHAT they said. Terminal lanes
    (opt-out, wrong person, bad data) are LOW: logged and actioned, never an
    emergency page."""
    return {HOT: "HOT", ACTIVE: "ACTIVE"}.get(cls, "LOW")


def suggested_reply(cls: str, found: List[str], *, first_name: Optional[str], contact: Optional[str],
                    location: Optional[str], channel: str, bereaved: bool = False) -> Optional[str]:
    """A DRAFT for the person to edit and send. Never sent by EvoSys. Asks the
    family to reply - never to call."""
    if cls in (OPT_OUT, LOW):
        return None
    name = (first_name or "").strip().title()
    hi = "Hi %s," % name if name else "Hello,"
    who = contact or "I"
    if cls == WRONG_PERSON:
        core = "thank you for letting us know. We'll update our records and you won't hear from us again."
    elif cls == BAD_DATA:
        core = "thank you for letting us know. We'll correct our records."
    elif bereaved:
        core = ("I'm so sorry for your loss. I'm here to help with whatever you need - just reply "
                "and tell me what would help most right now.")
    else:
        parts = []
        if APPOINTMENT in found:
            parts.append("I'd be glad to set a time with you. What day and time work best for you?")
        if PRICING in found:
            parts.append("I'm happy to go over pricing and options with you%s." % (
                " at %s" % location if location else ""))
        if INFORMATION in found:
            parts.append("I'll send that information over for you.")
        if (BENEFITS in found or VETERAN in found) and APPOINTMENT not in found:
            parts.append("Good question - I'd like to make sure you get an accurate answer for your situation "
                         "rather than a general one. Could you tell me a little more about what you'd like to know?")
        if (CEMETERY in found or CREMATION in found or GENERAL_PLANNING in found) and not parts:
            parts.append("I'd be glad to walk you through the options%s. What matters most to you as you "
                         "think about this?" % (" at %s" % location if location else ""))
        if OBJECTION in found and APPOINTMENT not in found:
            parts.append("I understand completely, and there's no pressure at all. If it would help, I can "
                         "send something you can look over whenever the time is right.")
        if not parts:
            parts.append("thank you for your message - I'll get back to you with an answer shortly."
                         if cls == ACTIVE else "thank you - I'd be glad to help.")
        core = " ".join(parts)
    body = "%s %s" % (hi, core)
    sign = ("- %s, %s" % (contact, location)) if (contact and location) else ""
    if channel in ("sms", "voicemail", "call"):         # a phone contact: the follow-up is a text
        return ("%s %s" % (body, sign)).strip()
    return "%s\n\n%s\n%s" % (body, contact or "", location or "")


RECOMMENDED = {
    HOT: "Reply personally now - they asked for something. Offer a time or send what they asked for.",
    ACTIVE: "Answer their question and keep the conversation going.",
    LOW: "No action needed beyond a courtesy reply if appropriate.",
    OPT_OUT: "Do not contact again. Automation has been stopped for this person.",
    BAD_DATA: "Fix the contact record in Data Review before any further outreach.",
    WRONG_PERSON: "Wrong person: the contact is held in Data Review; confirm the right person before anything else.",
}


# ── pure decisions responses.py acts on ──────────────────────────────────────

def cadence_action(cls: str, status: Optional[str]) -> Dict:
    """What a reply does to the lead's cadence. `status` is the cadence's
    current status (None = no cadence). Any reply pauses an ACTIVE cadence
    (never resumed automatically); an opt-out ENDS it, active or paused."""
    st = (str(status).lower() if status is not None else None)
    out = {"new_status": st, "paused": False}
    if st == "active":
        out["new_status"] = "paused"
    out["paused"] = bool(st is not None)      # active -> paused now; paused/stopped already not running
    if cls == OPT_OUT and st in ("active", "paused"):
        out["new_status"] = "stopped_dnc"
    return out


def alert_plan(cls: str) -> Optional[Dict]:
    """Which alert a response class raises: (kind, management, external)."""
    if cls == HOT:
        return {"kind": HOT, "management": True, "external": True}
    if cls == ACTIVE:
        return {"kind": ACTIVE, "management": False, "external": True}
    if cls == LOW:
        return {"kind": LOW, "management": False, "external": False}
    if cls in (BAD_DATA, WRONG_PERSON):
        return {"kind": cls, "management": False, "external": False}
    return None                                # OPT_OUT: closed, no alert


def staff_alert_gate(to: Optional[str], channel: str, audience: str,
                     alerts_enabled: bool) -> Tuple[bool, Optional[str]]:
    """May an external staff alert be attempted? (ok, reason-if-not). Default
    is off; a missing recipient is recorded, never guessed."""
    if not to:
        return False, "no %s configured for %s alerts" % ("phone" if channel == "sms" else "email", audience)
    if not alerts_enabled:
        return False, "staff alerts are switched off for this program"
    return True, None


def duplicate_inbound_result(message_sid: Optional[str], existing_reply_id: Optional[str]) -> Optional[Dict]:
    """ONE MESSAGE, ONE REPLY: a provider retry of an already-stored message
    is answered as a duplicate and runs no second classification or pipeline.
    None means the message is new (or has no id to dedupe on)."""
    if message_sid and existing_reply_id:
        return {"status": "duplicate", "reply_id": existing_reply_id}
    return None
