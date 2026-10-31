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
AUTO_REPLY = "auto_reply"
LABELS = {HOT: "HOT", ACTIVE: "ACTIVE", LOW: "LOW", OPT_OUT: "OPT-OUT", BAD_DATA: "BAD DATA",
          WRONG_PERSON: "WRONG PERSON", AUTO_REPLY: "AUTO-REPLY"}

# AN AUTOMATIC REPLY IS NOT A PERSON. "I'm driving with Do Not Disturb on" /
# out-of-office / "this is an automated message" are the phone or mailbox
# answering for them. Never treated as interest (Oct 14: "call me later" in a
# driving auto-reply scored HOT), never answered (that is how message loops
# start), and the regular, spaced follow-up continues.
_AUTO_REPLY_PATTERNS = (
    r"(i'?m|i am|currently) driving|driving (right now|with do not disturb)|do not disturb while driving|"
    r"(do not disturb|dnd) (is )?(on|enabled)|i('| wi)ll see your message when i|"
    r"\b(auto(matic|mated)?[- ]?(reply|response|message))\b|this is an automated|"
    r"out of (the )?office|away from (the|my) (office|phone|desk)|on vacation (until|through)|"
    r"(reply|replying|respond(ing)?) (when|once) i('| a)m (able|free|available|back)|"
    r"sent from my car|i('| a)m in a meeting and will"
)


def is_auto_reply(body: str) -> bool:
    return bool(re.search(_AUTO_REPLY_PATTERNS, (body or "").strip().lower()))
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

# Days and times a family names when they are choosing or shaping a visit
# (overnight Oct 19: "Saturday works better" and "Maybe next week" were LOW).
_DAYS = r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday|weekend|tomorrow|today|tonight)"
_WEEKDAYS = r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday|weekend)"
_TIMING = (r"\b(%s|next week|this week|(this|in the|tomorrow) (morning|afternoon|evening)|mornings|afternoons|"
           r"evenings|(after|before) (lunch|work|church|noon|school|\d{1,2})|(until|till|til) (\d{1,2}|noon|one|two|"
           r"three|four|five|six|seven|eight))\b" % _WEEKDAYS)
_SCHEDULE_PICK = (r"(?<!n't )(?<!not )(?<!no )\b(works (better|best|for (me|us))|(is|would be) (better|best) for (me|us)|"
                  r"(i'?m|i am|we'?re|we are) (free|available)|%s\b(?![^.?!]{0,25}?\b(not|n'?t|no)\b)[^.?!]{0,25}?"
                  r"\b(works|is good|is fine|good for|better|best))\b" % _DAYS)

# "Nothing works for us" / "neither works" turns the times DOWN; a greeting
# ("Happy Monday!", "have a blessed Sunday") names a day without meaning a visit.
_NONE_WORKS = r"\b(nothing|neither|none|no (day|time|times|days))\b"
_DAY_GREETING = r"\b(happy|blessed|good|great|nice|enjoy (your|the)|have a( good| great| nice| blessed)?)\s+%s\b" % _WEEKDAYS
_TIME_PICK_WHY = "picked or suggested a time"


def _names_a_time(text: str) -> bool:
    return bool(re.search(_TIMING, text)) and not re.search(_DAY_GREETING, text)


def _picks_a_time(text: str) -> bool:
    return bool(re.search(_SCHEDULE_PICK, text)) and not re.search(_NONE_WORKS, text) \
        and not re.search(_DAY_GREETING, text)


_HOT_PATTERNS = [
    (r"\b(appointment|appt|schedule|book|meet(ing)?|visit|tour|come (by|in|out)|stop by)\b", "wants a meeting or visit"),
    (r"\b(price|pricing|cost|costs|how much|payment|plan options|financ)", "asked about price"),
    # Spanish: "cita" is a visit (#11).
    (r"(?<!no )(?<!más )(?<!mas )(?<!una )\bcitas?\b", "asked about a cita"),
    # "I'm in" counts only when it ENDS the message (punctuation / emoji may
    # follow) or is followed by "for" / "!" - never "I'm in the hospital" or
    # "I'm in Houston" (#13). Misspelt "interested" and Spanish interest count
    # too; "no estoy interesado" is an opt-out phrase below, and a weak reason
    # like this one never outranks an opt-out phrase.
    (r"(?<!not )(?<!no longer )\b(interested|intrested|intersted|interessted|yes please|sounds good|sign me up|"
     r"let'?s do)\b|\bi('|’| a)m in(?=\s+for\b|\s*!|[^\w]*$)|(?<!no )\bme interesa\b|\binteresad[oa]s?\b|"
     r"(?<!no )\bquiero (ver|hacer|una)\b", "interested"),
    (r"\b(call me|reach (out|me)|get back to me|follow ?up|contact me|text me)\b|(?<!no me )(?<!no )\bll[aá]m(e|eme)\b",
     "requests follow-up"),
]
# OPT-OUT only on an unmistakable request: the whole message is a carrier stop
# word, or an explicit phrase. "Can I stop by Tuesday?" is a visit, not a STOP.
_STOP_WORDS = r"^\s*(stop|stopall|unsubscribe|end|quit|cancel|optout|opt out|remove)\s*[.!]*\s*$"
_OPT_OUT_PHRASES = (r"\b(remove me|take me off|unsubscribe me|opt me out|do not (contact|text|email|call) me|"
                    r"(don'?t|do not|never)( ever)? (contact|text|email|call)( or (text|call|email))? me|"
                    r"stop (texting|emailing|contacting) me|leave me alone|"
                    r"not interested|no longer interested|no thank(s| you)|lose my number|"
                    r"(don'?t|do not) (wish|want) to be contacted|"
                    r"(don'?t|do not) want (any )?more (emails?|texts?|messages?|calls?)|"
                    # Spanish (#11)
                    r"no gracias|no quiero (una )?cita|(no|nunca) me (manden|mande|env[ií]en|escriban|llame|llamen)|ya no (me )?(manden|escriban)|"
                    r"no (estoy|estamos) interesad[oa]s?|no (me|nos) interesa|b[oó]rr(a|e)me|"
                    r"dejen de (escribir|mandar))\b")

# #450: other plain ways of saying no (George, Oct 31: "No longer interested."
# went to the AI and came back "needs a human"). Only a PLAIN no counts: a
# question, a day or time, or a "but ..." clause ("It's not for me but my
# sister may want it", "We changed our minds about Thursday - Friday at 2?")
# is a conversation, not a no.
_PLAIN_NO = (r"\b((i'?ve |i have |we'?ve |we have )?changed (my|our) minds?|"
             r"(this|it|that)('s| is) not for (me|us)|"
             r"no longer (need|want) (it|this|that|them|the (guide|certificate|information|info))|"
             r"(don'?t|do not) want (it|this|that|any of (it|this|that)|anything)( any ?more| anymore)?)\b")
_NOT_PLAIN = r"\?|\b(but|except|however|though|although|unless|until|till|instead)\b"


def plain_no(text: str) -> bool:
    t = (text or "").strip().lower().replace("’", "'")
    return bool(t) and len(t) <= 200 and bool(re.search(_PLAIN_NO, t)) \
        and not re.search(_NOT_PLAIN, t) and not _names_a_time(t) and not _picks_a_time(t)


_NO_TEXTS = r"\b((don'?t|do not|never|stop|quit|no (more )?)\s*(text|texting|sms|txt)(ing)?( me)?|no (more )?texts)\b"
_NO_EMAIL = r"\b((don'?t|do not|never|stop|quit|no (more )?)\s*(e-?mail)(ing)?( me)?|no (more )?e-?mails)\b"
_WANTS_EMAIL = r"(?<!don't )(?<!do not )(?<!never )(?<!stop )\b(e-?mail (me|it)|send (it|me|the \w+) (by|via|through|to my) e-?mail|by e-?mail)\b"
_WANTS_TEXT = r"(?<!don't )(?<!do not )(?<!never )(?<!stop )\b(text me|send (it|me) by text|by text)\b"


def opt_out_scope(body: str) -> str:
    """#450: which channel an opt-out closes. "Please don't text me, but email
    me the information" closes TEXTS only - the email stays open for the
    information a person sends. Anything else closes the channel it came in on
    (as before)."""
    t = (body or "").strip().lower().replace("’", "'")
    if re.search(_NO_TEXTS, t) and not re.search(_NO_EMAIL, t) and re.search(_WANTS_EMAIL, t):
        return "sms"
    if re.search(_NO_EMAIL, t) and not re.search(_NO_TEXTS, t) and re.search(_WANTS_TEXT, t):
        return "email"
    return "all"


# AN OPT-OUT THAT ALSO ASKS FOR SOMETHING (#A). "Don't text me, call me",
# "Not interested in the guide, can you just call me?", "Not right now",
# "we already have plots but we need funeral arrangements". It stays an
# opt-out - closed, cadence and conversation stopped, never another automated
# text - but ONE staff alert goes out so a person decides (call / email).
# A bare "STOP" / "unsubscribe" / "remove me" / "leave me alone" never pages.
_NEG_LEAD = r"(?<!don't )(?<!dont )(?<!do not )(?<!never )(?<!stop )(?<!no more )"
_OPT_OUT_ASK = re.compile(
    r"\b(" + _NEG_LEAD + r"call me|" + _NEG_LEAD + r"phone me|" + _NEG_LEAD + r"email me|"
    + _NEG_LEAD + r"send|we (need|want)|but (can|could|please|call|email|send|we|i'?d|maybe)|maybe|"
    r"(?<!call me )(?<!me )later|next (week|month|year)|right now|after the|instead|"
    # Spanish
    r"(?<!no me )(?<!nunca me )ll[aá]m(e|eme|enme)|pero (puede|pueden|llame|me|necesit)|quiz[aá]s?|tal vez|m[aá]s (tarde|adelante)|despu[eé]s|"
    r"(el )?pr[oó]ximo (a[ñn]o|mes)|(ahora|ahorita) no|necesit(o|amos)|queremos)\b")
# Negated asks in the same clause ("please do not email me or call me").
_NEG_CLAUSE = re.compile(r"\b(don'?t|do not|never|no)\b[^.,;!?]*$")
OPT_OUT_ASK_LABEL = "Opted out of texts but asked for something - a person should decide (call / email)"


def opt_out_needs_person(body: str) -> bool:
    """An opt-out whose words also carry a request or soft timing. Pure; the
    caller still treats the reply as an opt-out in every other way."""
    text = (body or "").strip().lower().replace("’", "'")
    if not text or re.search(_STOP_WORDS, text):
        return False
    # "Stop texting me right now" / "leave me alone right now" is emphasis,
    # not timing.
    text = re.sub(r"\b(alone|me) right now\b", r"\1", text)
    for m in _OPT_OUT_ASK.finditer(text):
        if re.match(r"(call|phone|email) me|send", m.group(0)) and _NEG_CLAUSE.search(text[:m.start()]):
            continue                                     # "do not email me or call me"
        return True
    return False
# BAD DATA is about the RECORD (wrong person / number), never about a death in
# the family - for a funeral home a bereaved reply is the most important one.
_BAD_DATA_PATTERNS = (r"\b(wrong (number|person|email|address)|this is not (him|her|me)|not (him|her)|"
                      r"no one (here )?by that name|don'?t know (who|what) (this|you|that))\b")
# Of those, the ones about the PERSON (not the number/address on file).
_WRONG_PERSON_PATTERNS = (r"\b(wrong person|this is not (him|her|me)|not (him|her)|no one (here )?by that name|"
                          r"(he|she) (doesn'?t|does not) live here|you have the wrong(?! (?:number|email|address))|not the right person)\b")
_BEREAVEMENT = (r"\b(passed away|passed on|died|deceased|funeral for|lost my (husband|wife|mother|father|son|daughter)|"
                r"buried (my|our|him|her|mom|dad|mama|daddy|grand\w+)|laid (\w+ ){1,3}to rest|"
                r"funeral (is|was|will be)|memorial (is|was|will be))\b")
_LOW_PATTERNS = r"^\s*(ok(ay)?|k|thanks?( you)?|ok thanks?|thank you|thx|got it|received|👍|🙏)[\s.!]*$"

_CLASS_FROM_REPLY = {
    "interested": HOT, "callback": HOT, "question": ACTIVE, "neutral": LOW,
    "not_interested": OPT_OUT, "dnc": OPT_OUT, "wrong_number": BAD_DATA,
}


def _hard_stop(body: str) -> bool:
    try:
        from app.services.optout_parser import contains_hard_stop_language
        return bool(contains_hard_stop_language(body or ""))
    except Exception:                                    # noqa: BLE001
        return False


def classify(body: str, reply_classification: Optional[str] = None) -> Dict:
    """Deterministic and explainable. A stated need beats a stray keyword."""
    text = (body or "").strip().lower()
    hot = [why for pat, why in _HOT_PATTERNS if re.search(pat, text)]
    if _picks_a_time(text):
        hot.insert(1 if hot and hot[0] == "wants a meeting or visit" else 0, _TIME_PICK_WHY)
    bereaved = bool(re.search(_BEREAVEMENT, text))
    if re.search(_STOP_WORDS, text):
        return {"class": OPT_OUT, "reasons": ["asked to stop"]}
    # The platform's own stop decision is final: a family who opted out is
    # never paged as hot because the same text mentions a visit or a price
    # ("Not interested in a visit, stop texting me.").
    if (reply_classification or "").lower() == "dnc" or _hard_stop(body):
        return {"class": OPT_OUT, "reasons": ["asked to stop"]}
    if is_auto_reply(text) and not re.search(_OPT_OUT_PHRASES, text):
        return {"class": AUTO_REPLY, "reasons": ["automatic reply (driving / away), not a person"]}
    if re.search(_WRONG_PERSON_PATTERNS, text) and not hot:
        return {"class": WRONG_PERSON, "reasons": ["says we have the wrong person"]}
    if re.search(_BAD_DATA_PATTERNS, text) and not hot:
        return {"class": BAD_DATA, "reasons": ["says the number / address is wrong"]}
    weak = {"interested", "requests follow-up", _TIME_PICK_WHY, "asked about a cita"}
    if hot and re.search(_OPT_OUT_PHRASES, text) and set(hot) <= weak:
        return {"class": OPT_OUT, "reasons": ["opted out or not interested"]}
    if hot:
        if bereaved:
            hot = ["bereavement mentioned"] + hot
        return {"class": HOT, "reasons": hot}
    if re.search(_OPT_OUT_PHRASES, text):
        return {"class": OPT_OUT, "reasons": ["opted out or not interested"]}
    if not bereaved and plain_no(text):
        return {"class": OPT_OUT, "reasons": ["said no - not interested"]}
    if bereaved:
        return {"class": HOT, "reasons": ["bereavement mentioned - needs a personal reply"]}
    upstream = _CLASS_FROM_REPLY.get((reply_classification or "").lower())
    if upstream == BAD_DATA and re.search(_IDENTITY_ASK, text) and not re.search(_WRONG_SAID, text):
        # "Who is this?" is a question about US, not a statement that the
        # number is wrong (#7): it is answered, not sent to Data Review.
        return {"class": ACTIVE, "reasons": ["asked who we are"]}
    if upstream in (OPT_OUT, BAD_DATA, HOT):
        return {"class": upstream, "reasons": ["classified as %s on arrival" % reply_classification]}
    if re.search(_LOW_PATTERNS, text) or not text:
        return {"class": LOW, "reasons": ["acknowledgement only"]}
    if _names_a_time(text):
        return {"class": ACTIVE, "reasons": ["talked about when"]}
    if re.search(_INFO_ASK, text):
        return {"class": ACTIVE, "reasons": ["wants more information"]}
    if "?" in text or upstream == ACTIVE:
        return {"class": ACTIVE, "reasons": ["asked a question"]}
    return {"class": ACTIVE if len(text.split()) >= 4 else LOW,
            "reasons": ["engaged reply" if len(text.split()) >= 4 else "short reply"]}


_IDENTITY_ASK = r"\b(who is this|who's this|who are you|what is this (about|regarding)|qui[eé]n es)\b"
# An explicit wrong-number / wrong-person statement keeps the upstream BAD DATA.
_WRONG_SAID = r"\b(wrong|not (him|her|me|my)|no one|nobody|doesn'?t live|does not live|don'?t know (him|her)|" \
              r"never heard of|n[uú]mero equivocado|equivocad[oa])\b"


_INFO_ASK = r"\b(more info|information|details|send (me|it|the)|brochure|guide|packet|flyer|mail me)\b"


def intents(body: str, cls: Optional[str] = None) -> List[str]:
    """Every intent the text shows, plus the one its class implies (opt-out,
    wrong person, bad data, a bare acknowledgement, not interested)."""
    text = (body or "").strip().lower()
    found = [k for k, pat in _INTENT_PATTERNS if re.search(pat, text)]
    if APPOINTMENT not in found and not re.search(_BEREAVEMENT, text) \
            and cls not in (OPT_OUT, WRONG_PERSON, BAD_DATA) and (_names_a_time(text) or _picks_a_time(text)):
        found.insert(0, APPOINTMENT)                     # a day / time named is about a visit
    if (re.search(_OPT_OUT_PHRASES, text) and re.search(r"\b(not interested|no longer interested|no thank(s| you))\b", text)) \
            or (cls == OPT_OUT and plain_no(text)):
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
    if cls in (OPT_OUT, LOW, AUTO_REPLY):
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
    AUTO_REPLY: "Automatic reply from their phone - not answered; the regular, spaced follow-up continues.",
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
    # Every human answer reaches the team's phones and inboxes (Oct 14: "I'll
    # call you" was a LOW reply and nobody was told). Only opt-outs are quiet.
    if cls == LOW:
        return {"kind": LOW, "management": False, "external": True}
    if cls in (BAD_DATA, WRONG_PERSON):
        return {"kind": cls, "management": False, "external": True}
    if cls == AUTO_REPLY:
        # Logged in the app; nobody's phone buzzes for a car's auto-text.
        return {"kind": LOW, "management": False, "external": False}
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
