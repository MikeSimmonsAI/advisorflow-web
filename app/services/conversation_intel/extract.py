"""Read ONE message: what it says, what it asks, what it objects to, when it wants us back.

DETERMINISTIC ON PURPOSE. This runs on every inbound and outbound message, on
every workspace, whether or not AI is switched on, and its output becomes
durable memory. A model call here would make memory depend on a provider being
up, would cost money per message, and could not be regression-tested phrase by
phrase. The patterns below are explicit and the corpus in
tests/conversation_corpus pins them. The AI layer READS this memory; it never
writes facts into it.

FACT vs INFERENCE. A finding is a FACT only when the customer said it in so
many words ("my wife and two kids" -> spouse yes, children 2). Anything we
conclude from it ("family protection is probably the need") is an INFERENCE and
is returned separately; callers store it under a different category and never
present it as something the customer said.
"""
from __future__ import annotations

import calendar
import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

# ── verticals ───────────────────────────────────────────────────────────────

GENERAL = "general"
INSURANCE = "insurance"
ENERGY = "energy"
WHOLESALE = "wholesale"


def vertical_for_industry(industry: Optional[str], features: Optional[str] = None) -> str:
    text = ("%s %s" % (industry or "", features or "")).lower()
    if "wholesale" in text or "real_estate" in text:
        return WHOLESALE
    if "insurance" in text or "agency" in text:
        return INSURANCE
    if "energy" in text or "electric" in text:
        return ENERGY
    return GENERAL


# ── small helpers ───────────────────────────────────────────────────────────

_NUM = {"one": 1, "a": 1, "an": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
        "seven": 7, "eight": 8, "nine": 9, "ten": 10, "couple": 2, "both": 2}
_WEEKDAYS = {name.lower(): i for i, name in enumerate(calendar.day_name)}
_WEEKDAYS.update({name.lower()[:3]: i for i, name in enumerate(calendar.day_name)})
_MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
_MONTHS.update({name.lower()[:3]: i for i, name in enumerate(calendar.month_name) if name})

STOPWORDS = set("""a an the and or but if then so to of in on at for with from by about as is are was were be
been being i you he she it we they me my your our their this that these those do does did doing have has had
having can could would should will shall may might must just not no yes ok okay hi hello hey there here what
when where who why how which any some all more most very really also still please thanks thank like get got
want wants need needs let lets im i'm it's its that's dont don't doesn't can't cant won't wont am""".split())


def _clean(text: str) -> str:
    return " ".join((text or "").replace("’", "'").replace("‘", "'").split())


def _quote(text: str, limit: int = 300) -> str:
    t = _clean(text)
    return t if len(t) <= limit else t[:limit - 1] + "…"


def content_words(text: str) -> List[str]:
    return [w for w in re.findall(r"[a-z][a-z'-]+", (text or "").lower())
            if w not in STOPWORDS and len(w) > 2]


def short_hash(text: str) -> str:
    return hashlib.sha1(_clean(text).lower().encode("utf-8")).hexdigest()[:12]


def _num(word: str) -> Optional[int]:
    word = word.lower()
    if word.isdigit():
        return int(word)
    return _NUM.get(word)


# ── the result of reading one message ───────────────────────────────────────

@dataclass
class Finding:
    key: str
    value: str
    quote: str
    certainty: str = "fact"           # fact | inference


@dataclass
class TimingRequest:
    label: str                        # what they said: "Friday", "next month", "after tax season"
    when: Optional[date]              # the workspace-local day it resolves to, if it resolves
    kind: str                         # date | condition (e.g. after talking to spouse)
    quote: str
    is_correction: bool = False


@dataclass
class Analysis:
    direction: str
    intents: List[str] = field(default_factory=list)          # most specific first
    vertical_intents: List[str] = field(default_factory=list)
    questions: List[Tuple[str, List[str]]] = field(default_factory=list)   # (question text, topic words)
    objections: List[Finding] = field(default_factory=list)
    facts: List[Finding] = field(default_factory=list)
    inferences: List[Finding] = field(default_factory=list)
    preferences: List[Finding] = field(default_factory=list)
    timing: Optional[TimingRequest] = None
    commitments: List[Finding] = field(default_factory=list)  # outbound promises
    flags: Dict[str, str] = field(default_factory=dict)       # stop, human_request, legal, complaint, ...
    is_correction: bool = False
    is_bare_ack: bool = False                                  # "yes", "ok", "maybe" with nothing else

    @property
    def primary_intent(self) -> Optional[str]:
        return self.intents[0] if self.intents else None


# ── patterns ────────────────────────────────────────────────────────────────

STOP_RE = re.compile(r"^\s*(stop|stopall|unsubscribe|cancel|end|quit|revoke|optout|opt out)\s*[.!]*\s*$|"
                     r"\b(stop (texting|messaging|contacting|calling|emailing) me|do not (text|contact|call|email) me|"
                     r"don'?t (text|contact|call|email) me( again)?|remove me|take me off|unsubscribe|opt me out|"
                     r"lose my number)\b", re.I)
HUMAN_RE = re.compile(r"\b(real person|a human|human being|talk to (someone|somebody|a person|an agent|your manager|a manager)|"
                      r"speak (to|with) (someone|somebody|a person|an agent|a manager)|are you a (bot|robot|machine|ai)|"
                      r"is this (a bot|automated|ai)|stop (the )?(bot|automated)|let me talk to (someone|a person|\w+ directly))\b", re.I)
CALL_RE = re.compile(r"\b(call me|give me a call|can (someone|somebody|you) call( me)?|have (someone|him|her|\w+) call( me)?|"
                     r"phone me|ring me)\b", re.I)
LEGAL_RE = re.compile(r"\b(lawyer|attorney|sue|suing|lawsuit|legal action|report you|attorney general|ftc|fcc|"
                      r"harass(ment|ing)?|cease and desist)\b", re.I)
COMPLAINT_RE = re.compile(r"\b(complain(t)?|ripped off|scam|scammer|unacceptable|furious|ridiculous|terrible service|"
                          r"worst|never (got|received)|overcharged|billing error|double charged|you people)\b", re.I)
WRONG_PERSON_RE = re.compile(r"\b(wrong (number|person)|not (me|him|her)|you have the wrong|no one by that name|"
                             r"nobody (here )?by that name|i'?m not \w+|this isn'?t \w+'?s? (number|phone))\b", re.I)
WHO_RE = re.compile(r"\b(who is this|who'?s this|who are you|how did you get (my|this) (number|email)|"
                    r"no idea what you'?re talking about|what is this (about|regarding)|i don'?t know what this is)\b", re.I)
NO_RE = re.compile(r"^\s*(no|nope|nah|no thanks|no thank you)\s*[.!]*\s*$|\b(not interested|no longer interested|"
                   r"don'?t need (it|this|that|anything)|we'?re (all )?set|already (bought|purchased|signed)|"
                   r"pass on (this|that))\b", re.I)
YES_RE = re.compile(r"^\s*(yes|yeah|yep|yup|sure|ok|okay|sounds good|definitely|absolutely|please do|"
                    r"i'?m interested|interested)\s*[.!]*\s*$", re.I)
MAYBE_RE = re.compile(r"^\s*(maybe|possibly|not sure|i don'?t know|idk|we'?ll see|perhaps)\s*[.!?]*\s*$", re.I)
INTERESTED_RE = re.compile(r"\b(interested|sounds good|tell me more|i'?d like (to|that|more)|let'?s do it|"
                           r"i want (to|that|it)|sign me up|how do i (start|sign up|get started))\b", re.I)
APPT_RE = re.compile(r"\b(appointment|meet(ing)?|schedule|book (a|an)|set up a (call|time)|zoom|come (by|out)|"
                     r"what times?|available (times?|on)|can we (talk|meet))\b", re.I)
PRICE_RE = re.compile(r"\b(cost|price|pricing|how much|premium|rate|rates|monthly payment|afford|expensive|fee|fees|"
                      r"quote)\b", re.I)
CORRECTION_RE = re.compile(r"\b(actually|i meant|sorry,? i meant|correction|change (that|it) to|make (that|it)|"
                           r"instead|that'?s not what i said|not \w+,? (but )?\w+day)\b", re.I)
BEREAVEMENT_RE = re.compile(r"\b(passed away|passed (last|this|on|recently|in)|(he|she|they) passed|died|"
                            r"my late (wife|husband|mother|mom|father|dad|son|daughter|spouse|partner)|widow(ed|er)?|"
                            r"death (claim|certificate))\b", re.I)
# NOT "funeral" (a funeral home's prospects say it in every pre-planning
# message) and NOT "death benefit" (an insurance prospect asking what the
# policy pays) - neither is news of a death.
_MONEY_RE = re.compile(r"\$?\s?(\d{2,3}(?:,\d{3})+|\d{2,3}(?:\.\d)?\s?k)\b", re.I)
ALREADY_ASKED_RE = re.compile(r"\b(you already asked|already told you|i told you|i already said|asked me that)\b", re.I)
ALREADY_TALKED_RE = re.compile(r"\b(already (talked|spoke|spoken) (to|with) (\w+))\b", re.I)

OBJECTIONS = [
    ("too_expensive", re.compile(r"\b(too (expensive|much|high|pricey)|can'?t afford|out of (my|our) budget|"
                                 r"costs? too much)\b", re.I)),
    ("already_covered", re.compile(r"\b(already have (coverage|insurance|a policy|life insurance|a plan|a provider|"
                                   r"an agent|someone)|covered through (work|my job|my employer)|i'?m (already )?covered)\b", re.I)),
    ("spouse_decision", re.compile(r"\b((talk|check|discuss|speak) (to|with) my (wife|husband|spouse|partner)|"
                                   r"ask my (wife|husband|spouse|partner)|my (wife|husband|spouse|partner) (decides|handles|needs to))\b", re.I)),
    ("offer_too_low", re.compile(r"\b(offer is (too )?low|lowball|low ?ball|worth (a lot )?more( than that)?|"
                                 r"not (taking|accepting) (that|less)|that'?s insulting)\b", re.I)),
    ("send_info", re.compile(r"\b(just send (me )?(some )?(info|information|details|something)|send it (by|in|via) email|"
                             r"email me (the )?(info|details))\b", re.I)),
    ("not_now", re.compile(r"\b(not (right )?now|bad time|busy( right now| this week)?|maybe later|not at this time|"
                           r"(text|call|message|email) me later|i'?m driving|(i'?m )?at work( right now)?|"
                           r"in a meeting|can'?t talk( right now)?)\b", re.I)),
    ("trust", re.compile(r"\b(sounds like a scam|is this legit|is this real|how do i know)\b", re.I)),
    ("happy_with_current", re.compile(r"\b(happy with (my|our) (current )?(provider|plan|supplier|agent|company)|"
                                      r"staying with (my|our))\b", re.I)),
]

VERTICAL_INTENTS = {
    INSURANCE: [
        ("family_protection", re.compile(r"\b(wife|husband|spouse|kids|children|family|son|daughter|"
                                         r"protect (my|our)|if something happens to me|life insurance|term life)\b", re.I)),
        ("living_benefits", re.compile(r"\b(living benefits?|critical illness|chronic illness|terminal illness|"
                                       r"while i'?m (alive|living))\b", re.I)),
        ("retirement", re.compile(r"\b(retire|retirement|ira|401k|401\(k\)|annuit(y|ies)|nest egg|income for life)\b", re.I)),
        ("business_owner", re.compile(r"\b(my business|business owner|key person|buy-?sell|my company|partners? in the business)\b", re.I)),
        ("existing_coverage", re.compile(r"\b(already have (a )?(policy|coverage|life insurance)|through (my )?work|"
                                         r"group (life|coverage)|existing (policy|coverage))\b", re.I)),
        ("application_question", re.compile(r"\b(application|underwriting|medical exam|paramed|approved|approval|"
                                            r"policy number|when will i (hear|know))\b", re.I)),
    ],
    ENERGY: [
        ("moving", re.compile(r"\b(moving|move (in|out)|new (house|home|apartment|place|address)|relocat)\w*", re.I)),
        ("renewal", re.compile(r"\b(renew(al)?|contract (ends|expires|is up|ending)|expir(es|ing))\b", re.I)),
        ("enrollment", re.compile(r"\b(sign (me )?up|enroll|switch (me|over|providers?)|lock (in|it in))\b", re.I)),
        ("service_issue", re.compile(r"\b(power (is )?out|outage|no power|bill (is )?(wrong|high)|meter|disconnect(ed|ion)?|"
                                     r"service (issue|problem))\b", re.I)),
        ("previous_customer", re.compile(r"\b(used to (be|have) (with )?you|was (a )?customer|came back|back with you)\b", re.I)),
        ("wants_rate", re.compile(r"\b(rate|rates|price per kwh|kwh|cents|cheaper|lower (my )?bill|plan options?)\b", re.I)),
    ],
    WHOLESALE: [
        ("inherited_property", re.compile(r"\b(inherit(ed)?|estate|probate|passed away and left|left (it|the house) to)\b", re.I)),
        ("tenants", re.compile(r"\b(tenants?|renters?|rented out|it'?s rented|lease|occupied)\b", re.I)),
        ("condition", re.compile(r"\b(needs (a lot of )?work|fixer|roof|foundation|repairs?|water damage|mold|gutted|"
                                 r"as[- ]is|condition)\b", re.I)),
        ("wants_offer", re.compile(r"\b(make (me )?an offer|what (would|will|can) you (pay|offer|give)|cash offer|"
                                   r"your offer|how much (would|will|can) you)\b", re.I)),
        ("price_objection", re.compile(r"\b(too low|lowball|worth more|zillow says|not taking less)\b", re.I)),
        ("not_ready", re.compile(r"\b(not ready|not (selling|looking to sell) (yet|right now)|maybe (next|in) (year|spring|summer)|"
                                 r"thinking about it)\b", re.I)),
        ("callback_request", re.compile(r"\b(call me|give me a call|call back|reach me)\b", re.I)),
        ("wants_to_sell", re.compile(r"\b(want(ing)? to sell|need to sell|looking to sell|sell (my|the|this) (house|home|property)|"
                                     r"ready to sell)\b", re.I)),
        ("curious", re.compile(r"\b(just curious|just wondering|what'?s it worth|what is it worth|ballpark)\b", re.I)),
    ],
}

QUESTION_START = re.compile(r"^(what|how|when|where|why|who|which|can|could|would|will|do|does|did|is|are|"
                            r"should|may|am i|is there|are there)\b", re.I)

# ── timing ──────────────────────────────────────────────────────────────────

TIMING_HINT = re.compile(r"\b(call|text|email|reach|contact|follow up|get back|talk|check back|try|hit me up|"
                         r"circle back|touch base|reach out)\b", re.I)


def _next_weekday(today: date, wd: int, force_next_week: bool = False) -> date:
    days = (wd - today.weekday()) % 7
    if days == 0:
        days = 7
    if force_next_week and days < 7:
        start_next = today + timedelta(days=(7 - today.weekday()))
        return start_next + timedelta(days=wd)
    return today + timedelta(days=days)


def _first_of_next_month(today: date) -> date:
    return (today.replace(day=1) + timedelta(days=32)).replace(day=1)


def parse_timing(text: str, today: date) -> Optional[TimingRequest]:
    """A request about WHEN to come back, resolved to a workspace-local day where possible."""
    t = _clean(text)
    low = t.lower()
    correction = bool(CORRECTION_RE.search(t))
    q = _quote(t)

    m = re.search(r"\b(after|once) (i|we) (talk|speak|check|discuss)\w* (to|with) (my )?(wife|husband|spouse|partner|accountant|lawyer|family|kids)\b", low)
    cond = None
    if m:
        cond = TimingRequest(label="after talking to %s" % m.group(6), when=None, kind="condition", quote=q,
                             is_correction=correction)

    def mk(label, when):
        return TimingRequest(label=label, when=when, kind="date", quote=q, is_correction=correction)

    if re.search(r"\bday after tomorrow\b", low):
        return mk("day after tomorrow", today + timedelta(days=2))
    if re.search(r"\btomorrow\b", low):
        return mk("tomorrow", today + timedelta(days=1))
    if re.search(r"\b(later today|this afternoon|this evening|tonight)\b", low):
        return mk("later today", today)
    m = re.search(r"\b(next|this|on|until|after)?\s*(monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?\b", low)
    if m:
        wd = _WEEKDAYS[m.group(2)]
        return mk(m.group(2).capitalize() if m.group(1) != "next" else "next " + m.group(2).capitalize(),
                  _next_weekday(today, wd, force_next_week=(m.group(1) == "next")))
    if re.search(r"\bnext week\b", low):
        return mk("next week", _next_weekday(today, 0))
    if re.search(r"\b(end of (the|this) month)\b", low):
        last = calendar.monthrange(today.year, today.month)[1]
        return mk("end of the month", today.replace(day=max(today.day, last - 2)))
    if re.search(r"\bnext month\b", low):
        return mk("next month", _first_of_next_month(today))
    if re.search(r"\bafter (the )?tax season\b|\bafter (april|tax day)\b", low):
        y = today.year if today < date(today.year, 4, 16) else today.year + 1
        return mk("after tax season", date(y, 4, 16))
    if re.search(r"\bafter (the )?holidays\b|\bafter (new year'?s?|christmas)\b", low):
        jan6 = date(today.year, 1, 6)
        return mk("after the holidays", jan6 if today < jan6 else date(today.year + 1, 1, 6))
    m = re.search(r"\bin (\w+) (day|week|month)s?\b", low)
    if m and _num(m.group(1)):
        n = _num(m.group(1))
        unit = m.group(2)
        when = today + (timedelta(days=n) if unit == "day" else timedelta(weeks=n) if unit == "week" else timedelta(days=30 * n))
        return mk("in %s %s%s" % (m.group(1), unit, "s" if n != 1 else ""), when)
    m = re.search(r"\b(in|after|around|until|come|by|early|mid|late)\s+(january|february|march|april|may|june|july|august|"
                  r"september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\b", low)
    if m:
        name = m.group(2)[:3] if m.group(2) != "sept" else "sep"
        mo = _MONTHS.get(m.group(2), _MONTHS.get(name))
        y = today.year if mo > today.month else today.year + 1
        if m.group(1) == "after":
            mo2 = mo % 12 + 1
            y2 = y if mo2 > mo else y + 1
            return mk("after %s" % calendar.month_name[mo], date(y2, mo2, 1))
        day = 15 if m.group(1) == "mid" else 24 if m.group(1) == "late" else 1
        return mk(calendar.month_name[mo], date(y, mo, day))
    if re.search(r"\b(next year)\b", low):
        return mk("next year", date(today.year + 1, 1, 6))
    if re.search(r"\b(this weekend)\b", low):
        return mk("this weekend", _next_weekday(today, 5) if today.weekday() != 5 else today)
    return cond


# ── facts ───────────────────────────────────────────────────────────────────

def _facts(t: str, vertical: str) -> Tuple[List[Finding], List[Finding], List[Finding]]:
    facts, infs, prefs = [], [], []
    low = t.lower()
    q = _quote(t)
    bereaved = BEREAVEMENT_RE.search(t)
    if bereaved:
        # A death in the family is not a household fact to sell against.
        # Recorded so nobody asks "who would the coverage protect?" next, and
        # so a person - not a template - answers (engine: needs human).
        m = re.search(r"\bmy (late )?(wife|husband|spouse|partner|mother|mom|father|dad|son|daughter)\b", low)
        facts.append(Finding("household.bereavement", "lost %s" % (("their " + m.group(2)) if m else "a family member"), q))
    m = re.search(r"\bmy (wife|husband|spouse|partner)\b", low)
    if m and not bereaved:
        facts.append(Finding("household.spouse", m.group(1), q))
    m = re.search(r"\b(\d+|one|two|three|four|five|six|a couple of|both)\s+(?:little\s+|young\s+|grown\s+)?(kids|children|daughters|sons|boys|girls)\b", low)
    if m:
        n = _num(m.group(1).replace("a couple of", "couple"))
        if n:
            facts.append(Finding("household.children", str(n), q))
    elif re.search(r"\bmy (kids|children|son|daughter)\b", low):
        facts.append(Finding("household.children", "yes (count not stated)", q))
    m = re.search(r"\b(?:i'?m|i am)\s+(\d{2})\b(?:\s*(?:years? old|yo))?", low)
    if m and 18 <= int(m.group(1)) <= 100 and re.search(r"\b(years? old|yo|age|turn(ed|ing)?)\b|\bi'?m \d{2}\b", low):
        facts.append(Finding("person.age", m.group(1), q))
    m = re.search(r"\b(?:i live in|i'?m in|we'?re in|located in|based in)\s+([A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+)?)", t)
    if m and m.group(1).lower() not in ("the", "a"):
        facts.append(Finding("location.city", m.group(1), q))
    m = re.search(r"\bmy name is ([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)|\bthis is ([A-Z][a-z]+)\b(?! (is|about))", t)
    if m:
        facts.append(Finding("person.name_stated", m.group(1) or m.group(2), q))
    m = ALREADY_TALKED_RE.search(t)
    if m:
        facts.append(Finding("prior_contact.with", m.group(4), q))
    if re.search(r"\b(through (my )?(work|job|employer)|group (life|coverage))\b", low):
        facts.append(Finding("coverage.existing", "yes, through work", q))
    elif re.search(r"\balready have (a )?(policy|coverage|life insurance|insurance)\b", low):
        facts.append(Finding("coverage.existing", "yes", q))
    if re.search(r"\b(morning|mornings)\b", low) and re.search(r"\b(best|prefer|better|works?)\b", low):
        prefs.append(Finding("pref.time_of_day", "morning", q))
    if re.search(r"\b(afternoon|afternoons)\b", low):
        prefs.append(Finding("pref.time_of_day", "afternoon", q))
    if re.search(r"\b(evening|evenings|after work|after \d ?(pm)?)\b", low) and not re.search(r"\bthis evening\b", low):
        prefs.append(Finding("pref.time_of_day", "evening", q))
    if re.search(r"\b(text me|prefer text|texting is better|text is (best|better))\b", low):
        prefs.append(Finding("pref.channel", "text", q))
    elif re.search(r"\b(email me|prefer email|email is (best|better))\b", low):
        prefs.append(Finding("pref.channel", "email", q))
    elif re.search(r"\b(call me|prefer (a )?call|phone is (best|better))\b", low):
        prefs.append(Finding("pref.channel", "phone", q))

    if vertical == WHOLESALE:
        if re.search(r"\binherit|\bprobate\b|\bestate\b|passed away and left", low):
            facts.append(Finding("property.inherited", "yes", q))
        if re.search(r"\b(tenants?|renters?|it'?s rented|rented out)\b", low):
            facts.append(Finding("property.occupancy", "tenant-occupied", q))
        elif re.search(r"\b(vacant|empty|nobody lives)\b", low):
            facts.append(Finding("property.occupancy", "vacant", q))
        m = re.search(r"\b(needs (a lot of )?work|needs a new roof|foundation (issues?|problems?)|water damage|mold|fixer[- ]upper|as[- ]is)\b", low)
        if m:
            facts.append(Finding("property.condition", m.group(0), q))
        m = re.search(r"\bmy (wife|husband|mom|mother|dad|father|brother|sister) (is|owns|is on) (the )?(owner|title|deed)?", low)
        if m and re.search(r"\b(owner|title|deed|owns)\b", low):
            facts.append(Finding("property.owner_stated", "the customer's %s" % m.group(1), q))
        if re.search(r"\bnot this (property|house|one),? the other\b|\bthe other (property|house)\b", low):
            facts.append(Finding("property.subject_correction", "the other property", q))
        # "I want 250k", "asking 180,000", "won't take less than $200k": the
        # seller named a price. Recorded verbatim; negotiating is a person's job.
        m = _MONEY_RE.search(t)
        if m and re.search(r"\b(want|asking|ask|need|take|less than|at least|firm|bottom|price|list(ed|ing)?)\b", low):
            facts.append(Finding("property.asking_price", m.group(1).replace(" ", ""), q))
    if vertical == ENERGY:
        if re.search(r"\b(moving|move (in|out)|new (house|home|apartment|address))\b", low):
            facts.append(Finding("energy.moving", "yes", q))
        m = re.search(r"\bcontract (ends|expires|is up|ending)\s+(in|on|at the end of)?\s*([a-z]+)", low)
        if m and (m.group(3)[:3] in _MONTHS):
            facts.append(Finding("energy.contract_end", m.group(3).capitalize(), q))
    if vertical == INSURANCE:
        if any(f.key in ("household.spouse", "household.children") for f in facts):
            infs.append(Finding("inferred.need", "family protection may be important", q, certainty="inference"))
        if re.search(r"\b(retire|retirement)\b", low):
            infs.append(Finding("inferred.need", "retirement income may be a goal", q, certainty="inference"))
    return facts, infs, prefs


# ── questions ───────────────────────────────────────────────────────────────

def _questions(t: str) -> List[Tuple[str, List[str]]]:
    out = []
    for raw in re.split(r"(?<=[?.!])\s+|\n+", t):
        s = raw.strip()
        if not s:
            continue
        if s.endswith("?") or QUESTION_START.match(s) and len(s.split()) >= 3 and not s.endswith("."):
            if WHO_RE.search(s):
                continue          # "who is this?" is identity confusion, handled as a flag
            out.append((_quote(s, 240), content_words(s)))
    return out


# ── outbound promises ───────────────────────────────────────────────────────

COMMIT_RE = re.compile(r"\b(i'?ll|i will|we'?ll|we will|i'?m going to|let me)\s+(send|call|email|text|get back|follow up|"
                       r"check|look into|find out|have \w+ (call|reach out))[^.!?]{0,80}", re.I)


def analyze(text: str, *, direction: str, vertical: str = GENERAL, today: Optional[date] = None) -> Analysis:
    """Everything one message establishes. `today` is the WORKSPACE's local date."""
    today = today or date.today()
    t = _clean(text)
    a = Analysis(direction=direction)
    if not t:
        return a
    if direction == "outbound":
        for m in COMMIT_RE.finditer(t):
            a.commitments.append(Finding("commitment." + short_hash(m.group(0)), _quote(m.group(0), 160), _quote(t)))
        a.questions = _questions(t)       # what WE asked - used to stop re-asking
        return a

    low = t.lower()
    if STOP_RE.search(t):
        a.flags["stop"] = "Customer asked us to stop contacting them"
    if LEGAL_RE.search(t):
        a.flags["legal"] = "Mentions legal action or a regulator"
    if COMPLAINT_RE.search(t):
        a.flags["complaint"] = "Complaint or strong dissatisfaction"
    if HUMAN_RE.search(t):
        a.flags["human_request"] = "Asked for a person / a call"
    if WRONG_PERSON_RE.search(t):
        a.flags["wrong_person"] = "Says we have the wrong person"
    if WHO_RE.search(t):
        a.flags["identity_confusion"] = "Does not know who we are or why we are writing"
    if ALREADY_ASKED_RE.search(t):
        a.flags["repeated_question"] = "Customer says we already asked this"
    a.is_correction = bool(CORRECTION_RE.search(t))
    if re.search(r"\bthat'?s not what i said\b", low):
        a.flags["disputes_record"] = "Customer says our record of what they said is wrong"

    a.facts, a.inferences, a.preferences = _facts(t, vertical)
    for key, rx in OBJECTIONS:
        m = rx.search(t)
        if m:
            a.objections.append(Finding("objection." + key, key.replace("_", " "), _quote(t)))
    a.timing = parse_timing(t, today)
    a.questions = _questions(t)
    for key, rx in VERTICAL_INTENTS.get(vertical, []):
        if rx.search(t):
            a.vertical_intents.append(key)

    # general intent, most decisive first
    intents = []
    if "stop" in a.flags:
        intents.append("stop")
    if "legal" in a.flags or "complaint" in a.flags:
        intents.append("complaint")
    if "wrong_person" in a.flags:
        intents.append("wrong_person")
    if "human_request" in a.flags:
        intents.append("wants_human")
    if NO_RE.search(t) and not a.timing:
        intents.append("not_interested")
    if a.timing and (a.timing.kind == "date" or a.timing.kind == "condition") and not APPT_RE.search(t):
        intents.append("follow_up_later")
    if any(o.key == "objection.not_now" for o in a.objections) and "follow_up_later" not in intents:
        intents.append("not_now")
    if APPT_RE.search(t):
        intents.append("wants_appointment")
    if PRICE_RE.search(t) and (a.questions or "?" in t):
        intents.append("pricing")
    if a.questions and "pricing" not in intents:
        intents.append("question")
    if CALL_RE.search(t):
        intents.append("wants_call")
        a.preferences.append(Finding("pref.channel", "phone", _quote(t)))
        # "Can you call me after 5pm tomorrow?" is a REQUEST, kept as the
        # channel preference, the follow-up time and the wants_call intent.
        # Listing it again as an unanswered question duplicated it on screen.
        a.questions = [(q, w) for (q, w) in a.questions if not CALL_RE.search(q)]
        if not a.questions and "question" in intents:
            intents.remove("question")
    yes_sentence = any(YES_RE.match(x.strip()) for x in re.split(r"(?<=[.!?])\s+", t))
    if (INTERESTED_RE.search(t) and not re.search(r"\bnot (really )?interested\b|\bno longer interested\b", low)) \
            or yes_sentence:
        intents.append("interested")
    if "identity_confusion" in a.flags:
        intents.append("confused")
    if MAYBE_RE.search(t):
        intents.append("ambiguous")
    if not intents:
        intents.append("engaged")
    a.intents = intents
    a.is_bare_ack = bool(YES_RE.search(t) or MAYBE_RE.search(t) or re.match(r"^\s*no\s*[.!]*\s*$", low))
    return a
