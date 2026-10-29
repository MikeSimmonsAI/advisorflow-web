"""AI BUYER QUALIFIER - learn what an investor buys and whether they can buy now.

WHAT IT DOES
  Asks the qualification questions one at a time, reads each answer into
  structured facts (each kept WITH the quote it came from), updates the buyer
  record and a "stated in qualification" buy box, records objections and
  follow-up commitments, and hands the conversation to a person when it should.

WHAT IT NEVER DOES
  * It SENDS NOTHING. There is no channel in this module. Real investor
    messages go only through the existing disposition send path, and only after
    the owner approves outreach.
  * Practice mode works only on practice buyers (sandbox records with fiction
    contact details). Recorded mode is a person typing what a REAL buyer said on
    a call or in person - the AI reads it; nobody is contacted by the system.
  * A stated budget is never documented financing; "I pay cash" is never proof
    of funds. AI readings are marked stated, with the source (ai / rules).
  * Opt-outs win: STOP / remove me ends the conversation and sets the buyer's
    own do-not-contact; "not interested" ends it without a suppression.

AI THROUGH THE GATEWAY, WITH A REAL FALLBACK
  `ai_gateway.chat_completion(capability="wholesale_buyer_qualify")` like every
  other AI call. When the gateway refuses (switch off, no key, spend cap), the
  deterministic reader below answers - plainly worse at nuance, never silent.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from app.models.wholesale_buyer_engine_models import (CONV_DONE, CONV_HANDOFF, CONV_OPEN, CONV_PAUSED,
                                                      CONV_STOPPED, WholesaleBuyerConversation)
from app.models.wholesale_models import WholesaleBuyBox, WholesaleBuyer

log = logging.getLogger(__name__)

# (field, question, ask-only-if) - asked in this order, skipping what is known
QUESTIONS: List[Tuple[str, str, Optional[str]]] = [
    ("buying_now", "Are you actively buying investment property right now?", None),
    ("property_types", "What kinds of property are you buying - houses, multifamily, land, commercial, "
                       "industrial?", None),
    ("markets", "Which areas do you buy in - counties, cities or ZIP codes?", None),
    ("price", "What purchase price range works for you?", None),
    ("units", "For apartments, what unit-count range do you want?", "multifamily"),
    ("acres", "For land, what acreage range do you want?", "land"),
    ("strategy", "What is the plan for these - flip, hold as rentals, value-add or development?", None),
    ("rehab", "How much rehab will you take on - light, moderate, heavy or a full gut?", None),
    ("funding", "How do you fund purchases - cash, hard money, private money or a bank loan?", None),
    ("close_days", "How quickly can you usually close?", None),
    ("capacity", "How many deals could you take on over the next few months?", None),
    ("decision_maker", "Are you the person who makes the buying decision, or does someone else sign off?", None),
    ("review_deals", "Would you like us to send you deals that fit what you described?", None),
]
FIELDS = [q[0] for q in QUESTIONS]
QUALIFIED_NEEDS = ("buying_now", "property_types", "markets", "price", "funding")

# ── deterministic reader ───────────────────────────────────────────────────
_OPT_OUT = re.compile(r"\b(stop|unsubscribe|remove me|take me off|do not (contact|text|call|email)|"
                      r"don'?t (contact|text|call|email) me|opt.?out)\b", re.I)
_NOT_INTERESTED = re.compile(r"\b(not interested|no thanks|no thank you|not buying anymore|out of the business)\b",
                             re.I)
_HUMAN = re.compile(r"\b(speak|talk) (to|with) (a |an )?(person|human|real person|someone|manager)\b|"
                    r"\b(attorney|lawyer|lawsuit|sue)\b", re.I)
_YES_BUYING = re.compile(r"\b(yes|yeah|yep|absolutely|actively|still buying|we are buying|i am buying|i'm buying|"
                         r"buying now|always buying|definitely)\b", re.I)
_NOT_BUYING = re.compile(r"\b(not (currently |right now )?buying|paused|on (a )?pause|taking a break|"
                         r"not right now|sitting out|holding off)\b", re.I)
_TYPE_WORDS = [("single_family", r"single[- ]family|\bsfr?h?\b|houses?|homes?"),
               ("multifamily", r"multi[- ]?family|apartments?|\bmf\b|complex(es)?"),
               ("duplex", r"duplex(es)?|triplex|fourplex|quadplex"),
               ("land", r"\bland\b|\blots?\b|acreage|raw land|vacant"),
               ("commercial", r"commercial"), ("retail", r"retail|strip (center|mall)"),
               ("office", r"office"), ("industrial", r"industrial|warehouse|flex"),
               ("self_storage", r"self[- ]storage|storage"), ("mixed_use", r"mixed[- ]use"),
               ("hospitality", r"hotel|motel|hospitality"), ("mobile_home_park", r"mobile home park|mhp")]
_DFW_COUNTIES = ("dallas", "tarrant", "collin", "denton", "ellis", "johnson", "kaufman", "rockwall", "parker",
                 "wise", "hunt", "hood")
_CITIES = ("dallas", "fort worth", "arlington", "plano", "garland", "irving", "grand prairie", "mckinney", "frisco",
           "mesquite", "denton", "carrollton", "richardson", "lewisville", "allen", "flower mound", "mansfield",
           "rowlett", "desoto", "cedar hill", "duncanville", "lancaster", "waxahachie", "cleburne", "burleson",
           "weatherford", "rockwall", "forney", "terrell", "euless", "bedford", "hurst", "north richland hills",
           "keller", "haltom city", "balch springs", "wylie", "sachse", "midlothian", "red oak", "ennis")
_STRATEGY = [("flip", r"\bflip|fix (and|&|n) flip|renovat|rehab and sell"),
             ("rental", r"\brent(al|als)?\b|buy (and|&) hold|\bhold\b|long[- ]term"),
             ("brrrr", r"\bbrrrr\b"), ("value_add", r"value[- ]add"), ("development", r"develop|build|new construction"),
             ("portfolio", r"portfolio")]
_REHAB = [("full_gut", r"full gut|gut job|any condition|tear ?down|heavy or full"), ("heavy", r"\bheavy\b"),
          ("moderate", r"moderate|medium"), ("light", r"\blight\b|cosmetic|turn ?key|move[- ]in")]
_FUND = [("hard_money", r"hard money|\bhml\b"), ("private", r"private (money|lender|capital)"),
         ("conventional", r"\bbank\b|conventional|\bdscr\b|loan|mortgage|financ"), ("cash", r"\bcash\b")]
_OBJECTION = re.compile(r"(assignment fee|your fee|too (high|expensive)|overpriced|don'?t (work|deal) with "
                        r"wholesalers|bad experience|waste of time|numbers never work)", re.I)


def _money(token: str, unit: str) -> Optional[float]:
    try:
        v = float(token.replace(",", ""))
    except ValueError:
        return None
    unit = (unit or "").lower()
    if unit in ("k", "thousand"):
        v *= 1_000
    elif unit in ("m", "mm", "mil", "million"):
        v *= 1_000_000
    return v


_PRICE_RANGE = re.compile(r"\$?\s*([\d.,]+)\s*(k|m|mm|mil|million|thousand)?\s*(?:-|to|and|–)\s*\$?\s*([\d.,]+)\s*"
                          r"(k|m|mm|mil|million|thousand)?", re.I)
_PRICE_MAX = re.compile(r"(?:up to|under|below|max(?:imum)?|no more than|less than)\s*\$?\s*([\d.,]+)\s*"
                        r"(k|m|mm|mil|million|thousand)?", re.I)
_PRICE_MIN = re.compile(r"(?:over|above|at least|min(?:imum)?|starting at|from)\s*\$?\s*([\d.,]+)\s*"
                        r"(k|m|mm|mil|million|thousand)?", re.I)


def _price(text: str) -> Optional[Dict[str, Optional[float]]]:
    t = text.lower()
    if not re.search(r"\$|\d\s*(k|m|mm|mil|million|thousand)\b|\d{3},\d{3}|price|budget|range", t):
        return None
    m = _PRICE_RANGE.search(t)
    if m and not re.search(r"units?|acres?|days?|deals?", t[m.end():m.end() + 8]):
        lo, hi = _money(m.group(1), m.group(2) or m.group(4)), _money(m.group(3), m.group(4))
        if lo and hi and lo < 1000 and hi >= 1000:            # "150 to 300k" -> both thousands
            lo *= 1000 if (m.group(4) or "").lower() in ("k", "thousand") else 1
        if lo and hi and lo >= 1000 and hi >= 1000:
            return {"min": min(lo, hi), "max": max(lo, hi)}
    mx, mn = _PRICE_MAX.search(t), _PRICE_MIN.search(t)
    out = {"min": None, "max": None}
    if mx:
        out["max"] = _money(mx.group(1), mx.group(2))
    if mn and not re.search(r"units?|acres?", t[mn.end():mn.end() + 8]):
        out["min"] = _money(mn.group(1), mn.group(2))
    out = {k: v for k, v in out.items() if v and v >= 1000}
    return {"min": out.get("min"), "max": out.get("max")} if out else None


def _range(text: str, unit: str) -> Optional[Dict[str, Optional[float]]]:
    t = text.lower()
    m = re.search(r"(\d[\d,.]*)\s*(?:-|to|–)\s*(\d[\d,.]*)\s*%s" % unit, t)
    if m:
        return {"min": float(m.group(1).replace(",", "")), "max": float(m.group(2).replace(",", ""))}
    m = re.search(r"(\d[\d,.]*)\s*\+\s*%s|(?:over|more than|at least)\s*(\d[\d,.]*)\s*%s" % (unit, unit), t)
    if m:
        return {"min": float((m.group(1) or m.group(2)).replace(",", "")), "max": None}
    m = re.search(r"(?:under|up to|less than|below)\s*(\d[\d,.]*)\s*%s" % unit, t)
    if m:
        return {"min": None, "max": float(m.group(1).replace(",", ""))}
    return None


def _when(text: str, now: datetime) -> Optional[Dict[str, Any]]:
    t = text.lower()
    days = None
    if "tomorrow" in t:
        days = 1
    elif re.search(r"next week", t):
        days = 7
    elif re.search(r"next month", t):
        days = 30
    m = re.search(r"in (\d+) (day|week|month)s?", t)
    if m:
        days = int(m.group(1)) * {"day": 1, "week": 7, "month": 30}[m.group(2)]
    for i, wd in enumerate(("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")):
        if re.search(r"\b%s\b" % wd, t):
            days = ((i - now.weekday()) % 7) or 7
    if days is None:
        return None
    return {"due": (now + timedelta(days=days)).replace(hour=15, minute=0, second=0, microsecond=0)}


def rules_read(text: str, now: Optional[datetime] = None, context: Optional[str] = None) -> Dict[str, Any]:
    """Pattern reading of one buyer message. `context` is the field the last
    question asked about, so a bare "yes" or "cash" is read against it."""
    now = now or datetime.utcnow()
    t = (text or "").strip()
    low = t.lower()
    out: Dict[str, Any] = {"facts": {}, "source": "rules", "objections": [], "commitments": [],
                           "stop": None, "handoff": None}
    if _OPT_OUT.search(t):
        out["stop"] = "opt_out"
        return out
    if _NOT_INTERESTED.search(t):
        out["stop"] = "not_interested"
        return out
    if _HUMAN.search(t):
        out["handoff"] = "Asked for a person"
    f = out["facts"]
    if _NOT_BUYING.search(t):
        f["buying_now"] = "paused" if re.search(r"pause|break|holding off|sitting out|not right now", low) \
            else "not_buying"
    elif _YES_BUYING.search(t) and (context in (None, "buying_now") or re.search(r"buying", low)):
        f["buying_now"] = "buying"
    types = [k for k, rx in _TYPE_WORDS if re.search(rx, low)]
    if types:
        f["property_types"] = types
    counties = [c for c in _DFW_COUNTIES if re.search(r"\b%s\b( county)?" % c, low)
                and (re.search(r"\b%s county\b" % c, low) or c not in _CITIES)]
    cities = [c for c in _CITIES if re.search(r"\b%s\b" % re.escape(c), low)
              and not re.search(r"\b%s county\b" % re.escape(c), low)]
    zips = re.findall(r"\b(7[5-9]\d{3})\b", t)
    markets = ["DFW"] if re.search(r"\bdfw\b|metroplex|north texas", low) else []
    if counties or cities or zips or markets:
        f["markets"] = {"counties": [c.title() for c in counties], "cities": [c.title() for c in cities],
                        "zips": zips, "markets": markets}
    p = _price(t)
    if p:
        f["price"] = p
    u = _range(t, r"units?")
    if u:
        f["units"] = u
    a = _range(t, r"acres?")
    if a:
        f["acres"] = a
    st = [k for k, rx in _STRATEGY if re.search(rx, low)]
    if st:
        f["strategy"] = st
    for k, rx in _REHAB:
        if re.search(rx, low):
            f["rehab"] = k
            break
    for k, rx in _FUND:
        if re.search(rx, low):
            f["funding"] = k
            break
    m = re.search(r"(\d+)\s*(?:-|to)?\s*(\d+)?\s*(day|week)s?\b", low)
    if m and (context == "close_days" or re.search(r"close|closing", low)):
        n = int(m.group(2) or m.group(1))
        f["close_days"] = n * (7 if m.group(3) == "week" else 1)
    m = re.search(r"(\d+)\s*(?:-|to)?\s*(\d+)?\s*(deals?|houses?|properties|doors|homes)\s*(a|per|each)\s*(month|year|quarter)",
                  low)
    if m:
        f["capacity"] = m.group(0)
    elif context == "capacity" and re.search(r"\d", low):
        f["capacity"] = t[:80]
    if context == "decision_maker" or re.search(r"decision|sign off|my partner|partners?", low):
        if re.search(r"\b(i do|i am|i'm|me|myself|i make|i decide)\b", low):
            f["decision_maker"] = {"self": True, "name": None}
        else:
            m = re.search(r"(?:partner|boss|wife|husband|investor|principal|ceo|owner)\s+(?:is\s+)?([A-Z][a-z]+(?: [A-Z][a-z]+)?)",
                          t)
            if m:
                f["decision_maker"] = {"self": False, "name": m.group(1)}
    if context == "review_deals" or re.search(r"send (me|us) (deals|them|it|over)|happy to (look|review)", low):
        if re.search(r"\b(yes|sure|please|send|happy|ok|okay)\b", low):
            f["review_deals"] = True
        elif re.search(r"\b(no|not now)\b", low):
            f["review_deals"] = False
    if context == "buying_now" and "buying_now" not in f:
        if re.search(r"^\s*(yes|yeah|yep|sure|absolutely|we are|i am)\b", low):
            f["buying_now"] = "buying"
        elif re.search(r"^\s*(no|nope|not)\b", low):
            f["buying_now"] = "not_buying"
    for m in _OBJECTION.finditer(t):
        out["objections"].append(t[max(0, m.start() - 40):m.end() + 40].strip())
    if re.search(r"call me|follow up|check back|reach out|get back|touch base|talk (next|on|tomorrow)|schedule",
                 low):
        w = _when(t, now)
        out["commitments"].append({"what": "Follow up with the buyer", "quote": t[:200],
                                   "due": w["due"].isoformat() if w else None})
    if re.search(r"walk ?through|see (the|it) in person|tour|showing|meet (at|on)", low):
        out["commitments"].append({"what": "Buyer asked for a walkthrough", "quote": t[:200], "due": None,
                                   "walkthrough": True})
    return out


# ── the AI reader ──────────────────────────────────────────────────────────
_SYSTEM = """You read ONE message from a real-estate investor (a BUYER) answering a qualification question.
Return ONLY JSON: {"facts": {...}, "objections": [quote...], "commitments": [{"what","quote","when"}],
"stop": null|"opt_out"|"not_interested", "handoff": null|"reason", "confidence": 0-100}.
facts keys (include ONLY what the message states; never guess):
 buying_now: "buying"|"paused"|"not_buying"
 property_types: list of single_family, multifamily, duplex, land, commercial, retail, office, industrial,
   self_storage, mixed_use, hospitality, mobile_home_park
 markets: {"counties":[], "cities":[], "zips":[], "markets":[]}
 price: {"min": number|null, "max": number|null}   (dollars)
 units: {"min","max"}   acres: {"min","max"}
 strategy: list of flip, rental, brrrr, value_add, development, portfolio
 rehab: light|moderate|heavy|full_gut
 funding: cash|hard_money|private|conventional|mixed
 close_days: integer   capacity: short text as said
 decision_maker: {"self": bool, "name": string|null}
 review_deals: bool
A stated budget is NOT proof of funds. Opt-out words (stop, remove me, do not contact) => stop "opt_out"."""


def _get_client():
    return None


def read(text: str, *, org_id: Optional[str], actor: Optional[str], context: Optional[str] = None,
         client: Any = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    rules = rules_read(text, now=now, context=context)
    if rules["stop"] == "opt_out":                    # compliance never waits on a model
        return rules
    try:
        from app.services import ai_gateway
        msgs = [{"role": "system", "content": _SYSTEM}]
        if context:
            msgs.append({"role": "system", "content": "The question just asked was about: %s" % context})
        msgs.append({"role": "user", "content": (text or "")[:3000]})
        resp = ai_gateway.chat_completion(feature="wholesale_buyer_qualify", capability="wholesale_buyer_qualify",
                                          mode="manual", actor=actor, org_id=org_id, messages=msgs,
                                          client=client if client is not None else _get_client(), temperature=0,
                                          response_format={"type": "json_object"})
        parsed = json.loads(resp.choices[0].message.content or "{}")
        if not isinstance(parsed, dict) or not isinstance(parsed.get("facts", {}), dict):
            raise ValueError("unreadable AI reply")
        facts = {k: v for k, v in (parsed.get("facts") or {}).items() if k in FIELDS and v not in (None, "", [], {})}
        out = {"facts": facts, "source": "ai", "objections": [str(x)[:300] for x in parsed.get("objections") or []],
               "commitments": [c for c in parsed.get("commitments") or [] if isinstance(c, dict)],
               "stop": parsed.get("stop") if parsed.get("stop") in ("opt_out", "not_interested") else None,
               "handoff": parsed.get("handoff") or rules["handoff"]}
        if (parsed.get("confidence") or 0) < 50:
            out["handoff"] = out["handoff"] or "The AI was not confident about this answer"
        # rules fill what the model skipped; commitments get a computed due date
        for k, v in rules["facts"].items():
            out["facts"].setdefault(k, v)
        for c in out["commitments"]:
            w = _when(str(c.get("when") or c.get("quote") or ""), now or datetime.utcnow())
            c["due"] = w["due"].isoformat() if w else None
        if not out["commitments"]:
            out["commitments"] = rules["commitments"]
        if rules["stop"] and not out["stop"]:
            out["stop"] = rules["stop"]
        return out
    except Exception as exc:                          # noqa: BLE001 - gateway refusal or provider error
        rules["ai_unavailable"] = "%s: %s" % (type(exc).__name__, str(exc)[:120])
        return rules


# ── conversation flow ──────────────────────────────────────────────────────

def _jl(raw, default):
    try:
        return json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default


def known_fields(buyer: WholesaleBuyer, answers: Dict[str, Any]) -> set:
    k = set(answers)
    if buyer.stated_funding:
        k.add("funding")
    if buyer.stated_close_days:
        k.add("close_days")
    if buyer.decision_maker_name or buyer.decision_maker_confirmed:
        k.add("decision_maker")
    return k


def next_question(buyer: WholesaleBuyer, answers: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    known = known_fields(buyer, answers)
    types = set((answers.get("property_types") or {}).get("value") or [])
    for field, q, only_if in QUESTIONS:
        if field in known:
            continue
        if only_if == "multifamily" and not types & {"multifamily"}:
            continue
        if only_if == "land" and not types & {"land"}:
            continue
        return field, q
    return None


def is_practice_buyer(b: WholesaleBuyer) -> bool:
    phone = re.sub(r"\D", "", b.phone or "")
    email = (b.email or "").lower()
    fiction_phone = (not phone) or phone[-10:].startswith("2145550")
    fiction_email = (not email) or email.endswith(".invalid") or email.endswith("@example.com")
    return bool(b.is_test) and fiction_phone and fiction_email and bool(phone or email)


def create_practice_buyer(db, org_id: str, user) -> WholesaleBuyer:
    import random
    n = random.randint(100, 199)
    b = WholesaleBuyer(organization_id=org_id, entity_type="company", company_name="Practice Investor %d LLC" % n,
                       contact_name="Pat Practice", phone="+1214555%04d" % n, email="practice%d@buyer.invalid" % n,
                       preferred_channel="email", source="practice", is_test=True,
                       notes="PRACTICE BUYER - fictional record for testing the AI buyer qualifier. "
                             "Reserved 555-01xx phone and .invalid email: nothing can reach it.")
    db.add(b)
    db.flush()
    return b


def _buyer(db, org_id: str, buyer_id: str) -> WholesaleBuyer:
    b = db.query(WholesaleBuyer).filter(WholesaleBuyer.id == buyer_id,
                                        WholesaleBuyer.organization_id == org_id).first()
    if b is None:
        raise HTTPException(status_code=404, detail="Buyer not found")
    return b


def _conv(db, org_id: str, conv_id: str) -> WholesaleBuyerConversation:
    c = db.query(WholesaleBuyerConversation).filter(WholesaleBuyerConversation.id == conv_id,
                                                    WholesaleBuyerConversation.organization_id == org_id).first()
    if c is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return c


def _turn(c, role: str, text: str, **extra):
    tr = _jl(c.transcript, [])
    tr.append({"role": role, "text": text, "at": datetime.utcnow().isoformat(), **extra})
    c.transcript = json.dumps(tr)


def start(db, org_id: str, buyer_id: str, user, mode: str = "practice") -> WholesaleBuyerConversation:
    b = _buyer(db, org_id, buyer_id)
    if mode == "live":
        raise HTTPException(status_code=409, detail="Live AI messaging to investors is owner-controlled and not "
                                                    "switched on. Use practice mode, or record a real call.")
    if mode not in ("practice", "recorded"):
        raise HTTPException(status_code=400, detail="mode must be practice or recorded")
    if b.do_not_contact:
        raise HTTPException(status_code=409, detail="This buyer asked not to be contacted.")
    if mode == "practice" and not is_practice_buyer(b):
        raise HTTPException(status_code=409, detail="Practice mode runs only on a practice buyer (a sandbox record "
                                                    "with fictional contact details). Use 'recorded' to log what a "
                                                    "real buyer said on a call.")
    if b.ai_paused and mode == "practice":
        raise HTTPException(status_code=409, detail="AI is paused for this buyer.")
    open_c = db.query(WholesaleBuyerConversation).filter(
        WholesaleBuyerConversation.organization_id == org_id, WholesaleBuyerConversation.buyer_id == b.id,
        WholesaleBuyerConversation.status == CONV_OPEN).first()
    if open_c is not None:
        return open_c
    c = WholesaleBuyerConversation(organization_id=org_id, buyer_id=b.id, mode=mode,
                                   channel="practice" if mode == "practice" else "recorded_call",
                                   status=CONV_OPEN, transcript="[]", answers="{}", objections="[]",
                                   commitments="[]", started_by_id=getattr(user, "id", None),
                                   is_test=bool(b.is_test))
    db.add(c)
    db.flush()
    _turn(c, "system", "Practice conversation - nothing is sent to anybody." if mode == "practice" else
          "Recorded conversation - a person enters what the buyer said; nothing is sent.")
    nq = next_question(b, {})
    if nq:
        _turn(c, "ai", nq[1], field=nq[0])
    b.qualification_status = "in_progress"
    b.qualification_at = datetime.utcnow()
    db.flush()
    return c


def _last_field(c) -> Optional[str]:
    for t in reversed(_jl(c.transcript, [])):
        if t.get("role") == "ai" and t.get("field"):
            return t["field"]
    return None


def _apply(db, org_id: str, b: WholesaleBuyer, c, facts: Dict[str, Any], source: str, quote: str, user):
    """Write what the buyer said onto the buyer record and a STATED (unconfirmed) buy box."""
    now = datetime.utcnow()
    answers = _jl(c.answers, {})
    for k, v in facts.items():
        answers[k] = {"value": v, "quote": quote[:300], "source": source, "at": now.isoformat()}
    c.answers = json.dumps(answers)
    if "buying_now" in facts:
        b.stated_buying_status, b.stated_buying_at = facts["buying_now"], now
    if "price" in facts and isinstance(facts["price"], dict):
        b.stated_budget_min = facts["price"].get("min") or b.stated_budget_min
        b.stated_budget_max = facts["price"].get("max") or b.stated_budget_max
    if "funding" in facts:
        b.stated_funding = facts["funding"]
    if "close_days" in facts:
        try:
            b.stated_close_days = int(facts["close_days"])
        except (TypeError, ValueError):
            pass
    if "capacity" in facts:
        b.stated_capacity = str(facts["capacity"])[:120]
    if "decision_maker" in facts and isinstance(facts["decision_maker"], dict):
        dm = facts["decision_maker"]
        b.decision_maker_name = dm.get("name") or (b.contact_name if dm.get("self") else b.decision_maker_name)
    b.last_interaction_at = now
    if c.mode == "recorded":
        # a person spoke with them and recorded it: the contact demonstrably works
        b.contact_verified_at, b.contact_verified_how = now, "Spoke with them (recorded by your team)"
    box_keys = {"property_types", "markets", "price", "units", "acres", "strategy", "rehab"}
    if box_keys & set(facts):
        box = db.query(WholesaleBuyBox).filter(WholesaleBuyBox.organization_id == org_id,
                                               WholesaleBuyBox.buyer_id == b.id,
                                               WholesaleBuyBox.source == "ai_conversation",
                                               WholesaleBuyBox.is_active.is_(True)).first()
        if box is None:
            box = WholesaleBuyBox(organization_id=org_id, buyer_id=b.id, label="Stated in qualification",
                                  source="ai_conversation", is_active=True)
            db.add(box)
        if "property_types" in facts:
            box.property_types = json.dumps(sorted(set(_jl(box.property_types, []) + list(facts["property_types"]))))
        if "markets" in facts and isinstance(facts["markets"], dict):
            for col in ("counties", "cities", "zips", "markets"):
                vals = facts["markets"].get(col) or []
                if vals:
                    setattr(box, col, json.dumps(sorted(set(_jl(getattr(box, col), []) + list(vals)))))
        if "price" in facts and isinstance(facts["price"], dict):
            box.min_price = facts["price"].get("min") or box.min_price
            box.max_price = facts["price"].get("max") or box.max_price
        for key, lo, hi in (("units", "min_units", "max_units"), ("acres", "min_acres", "max_acres")):
            if key in facts and isinstance(facts[key], dict):
                if facts[key].get("min") is not None:
                    setattr(box, lo, facts[key]["min"] if key == "acres" else int(facts[key]["min"]))
                if facts[key].get("max") is not None:
                    setattr(box, hi, facts[key]["max"] if key == "acres" else int(facts[key]["max"]))
        if "strategy" in facts:
            box.strategies = json.dumps(sorted(set(_jl(box.strategies, []) + list(facts["strategy"]))))
        if "rehab" in facts:
            box.rehab_tolerance = facts["rehab"]
        box.confirmed_at = None              # changed criteria are stated, not confirmed
        box.notes = "From a %s qualification conversation (%s reading). Stated by the buyer - confirm before " \
                    "relying on it." % (c.mode, source)
    db.flush()


def _handoff(db, org_id: str, b, c, reason: str, user):
    from app.services import wholesale_exceptions as EX
    ex, _new = EX.raise_exception(db, org_id, kind="buyer_handoff", subject_type="buyer", subject_id=b.id,
                                  title="Buyer conversation needs a person: %s" % (b.company_name or b.contact_name),
                                  detail=reason, priority=20, source="buyer_qualification", user=user,
                                  is_test=bool(b.is_test))
    c.status, c.handoff_reason, c.handoff_exception_id = CONV_HANDOFF, reason[:250], ex.id
    b.qualification_status = "needs_human"
    _turn(c, "system", "Handed to a person: %s" % reason)
    return ex


def followup(db, org_id: str, b, c, what: str, due: Optional[datetime], user, quote: Optional[str] = None):
    from app.services import wholesale_exceptions as EX
    from app.models.wholesale_models import WholesaleWorkException
    ex, new = EX.raise_exception(db, org_id, kind="buyer_followup", subject_type="buyer", subject_id=b.id,
                                 title="%s: %s" % (what, b.company_name or b.contact_name),
                                 detail=quote, priority=30, source="buyer_qualification", user=user,
                                 is_test=bool(b.is_test), assigned_to_id=getattr(user, "id", None))
    if due is not None and isinstance(ex, WholesaleWorkException):
        ex.due_at = due
    c.followup_exception_id = ex.id
    return ex


def reply(db, org_id: str, conv_id: str, text: str, user, *, client: Any = None,
          now: Optional[datetime] = None) -> Dict[str, Any]:
    """One buyer answer: read it, record it, act on it, ask the next question."""
    c = _conv(db, org_id, conv_id)
    if c.status != CONV_OPEN:
        raise HTTPException(status_code=409, detail="This conversation is %s." % c.status.replace("_", " "))
    b = _buyer(db, org_id, c.buyer_id)
    if b.do_not_contact:
        c.status = CONV_STOPPED
        db.flush()
        raise HTTPException(status_code=409, detail="This buyer asked not to be contacted.")
    text = (text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Type what the buyer said.")
    ctx = _last_field(c)
    r = read(text, org_id=org_id, actor=getattr(user, "id", None), context=ctx, client=client, now=now)
    _turn(c, "buyer", text, reader=r["source"], extracted=r["facts"])
    result: Dict[str, Any] = {"reader": r["source"], "facts": r["facts"], "ai_unavailable": r.get("ai_unavailable")}
    if r["stop"] == "opt_out":
        b.do_not_contact, b.do_not_contact_reason = True, "Asked to stop during qualification (%s)" % \
            datetime.utcnow().date()
        b.stated_buying_status = b.stated_buying_status
        c.status = CONV_STOPPED
        b.qualification_status = "declined"
        _turn(c, "system", "Buyer opted out. Marked do-not-contact; nothing further will be asked or sent.")
        db.flush()
        return {**result, "status": c.status, "stopped": "opt_out", "next": None}
    if r["stop"] == "not_interested":
        c.status = CONV_STOPPED
        b.stated_buying_status, b.stated_buying_at = "not_buying", datetime.utcnow()
        b.qualification_status = "declined"
        _turn(c, "system", "Buyer said they are not interested. Conversation closed (not an opt-out).")
        db.flush()
        return {**result, "status": c.status, "stopped": "not_interested", "next": None}
    if r["facts"]:
        _apply(db, org_id, b, c, r["facts"], r["source"], text, user)
    if r["objections"]:
        c.objections = json.dumps(_jl(c.objections, []) + r["objections"])
    commits = []
    for cm in r["commitments"]:
        due = None
        if cm.get("due"):
            try:
                due = datetime.fromisoformat(str(cm["due"])[:19])
            except ValueError:
                due = None
        ex = followup(db, org_id, b, c, cm.get("what") or "Follow up", due, user, quote=cm.get("quote"))
        commits.append({**cm, "exception_id": ex.id})
    if commits:
        c.commitments = json.dumps(_jl(c.commitments, []) + commits)
    result["commitments"] = commits
    if r["handoff"] or r["objections"]:
        ex = _handoff(db, org_id, b, c, r["handoff"] or "Raised an objection: %s" % r["objections"][0][:150], user)
        db.flush()
        return {**result, "status": c.status, "handoff_exception_id": ex.id, "next": None}
    answers = _jl(c.answers, {})
    if ctx and ctx not in r["facts"] and not r["facts"]:
        # nothing usable in the answer: ask once more, then move on and leave it for a person
        tries = sum(1 for t in _jl(c.transcript, []) if t.get("role") == "ai" and t.get("field") == ctx)
        if tries < 2:
            _turn(c, "ai", "Sorry - I didn't catch that. %s" % dict((q[0], q[1]) for q in QUESTIONS)[ctx], field=ctx)
            db.flush()
            return {**result, "status": c.status, "next": {"field": ctx, "question": _jl(c.transcript, [])[-1]["text"]}}
        answers[ctx] = {"value": None, "quote": text[:300], "source": "unanswered", "at": datetime.utcnow().isoformat()}
        c.answers = json.dumps(answers)
    nq = next_question(b, answers)
    if nq:
        _turn(c, "ai", nq[1], field=nq[0])
        db.flush()
        return {**result, "status": c.status, "next": {"field": nq[0], "question": nq[1]}}
    return {**result, **finish(db, org_id, c, user)}


def finish(db, org_id: str, c, user) -> Dict[str, Any]:
    b = _buyer(db, org_id, c.buyer_id)
    answers = _jl(c.answers, {})
    have = [k for k in QUALIFIED_NEEDS if (answers.get(k) or {}).get("value") not in (None, "", [], {})]
    buying = ((answers.get("buying_now") or {}).get("value") == "buying")
    qualified = buying and len(have) == len(QUALIFIED_NEEDS)
    b.qualification_status = "qualified" if qualified else ("declined" if not buying and "buying_now" in have
                                                            else "needs_human")
    b.qualification_at = datetime.utcnow()
    parts = []
    for k, label in (("buying_now", "Buying"), ("property_types", "Types"), ("markets", "Areas"), ("price", "Price"),
                     ("units", "Units"), ("acres", "Acres"), ("strategy", "Strategy"), ("rehab", "Rehab"),
                     ("funding", "Funding (stated)"), ("close_days", "Close days"), ("capacity", "Capacity"),
                     ("decision_maker", "Decision-maker"), ("review_deals", "Wants deals")):
        v = (answers.get(k) or {}).get("value")
        if v not in (None, "", [], {}):
            if isinstance(v, dict):
                v = ", ".join("%s %s" % (kk, vv if not isinstance(vv, list) else "/".join(map(str, vv)))
                              for kk, vv in v.items() if vv not in (None, [], ""))
            elif isinstance(v, list):
                v = ", ".join(map(str, v))
            parts.append("%s: %s" % (label, v))
    c.summary = ("Qualified on stated answers. " if qualified else "Not fully qualified. ") + "; ".join(parts) + \
        ". Stated answers - confirm the buy box and request proof of funds before relying on them."
    c.status = CONV_DONE
    _turn(c, "ai", "Thank you - that's everything I needed. Someone from the team will follow up.")
    if not qualified:
        _handoff(db, org_id, b, c, "Qualification incomplete - a person should finish it (%s missing)"
                 % ", ".join(k for k in QUALIFIED_NEEDS if k not in have), user)
        c.status = CONV_DONE
    db.flush()
    return {"status": c.status, "qualified": qualified, "summary": c.summary, "next": None}


def pause(db, org_id: str, conv_id: str, user) -> Dict[str, Any]:
    c = _conv(db, org_id, conv_id)
    if c.status == CONV_OPEN:
        c.status = CONV_PAUSED
        _turn(c, "system", "Paused by a person.")
    db.flush()
    return {"status": c.status}


def resume(db, org_id: str, conv_id: str, user) -> Dict[str, Any]:
    c = _conv(db, org_id, conv_id)
    b = _buyer(db, org_id, c.buyer_id)
    if b.do_not_contact:
        raise HTTPException(status_code=409, detail="This buyer asked not to be contacted.")
    if c.status in (CONV_PAUSED, CONV_HANDOFF):
        c.status = CONV_OPEN
        _turn(c, "system", "Resumed by a person.")
    db.flush()
    return {"status": c.status}


def takeover(db, org_id: str, conv_id: str, user, note: Optional[str] = None) -> Dict[str, Any]:
    """A person takes the conversation from the AI."""
    c = _conv(db, org_id, conv_id)
    b = _buyer(db, org_id, c.buyer_id)
    ex = _handoff(db, org_id, b, c, note or "Taken over by %s" % (getattr(user, "full_name", None) or "a person"),
                  user)
    db.flush()
    return {"status": c.status, "handoff_exception_id": ex.id}


def staff_note(db, org_id: str, conv_id: str, text: str, user) -> Dict[str, Any]:
    c = _conv(db, org_id, conv_id)
    _turn(c, "staff", (text or "").strip()[:2000], by=getattr(user, "id", None))
    db.flush()
    return {"status": c.status}
