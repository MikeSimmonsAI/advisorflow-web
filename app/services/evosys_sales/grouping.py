"""EvoSys Sales: which industry + market group a lead belongs to.

DETERMINISTIC FIRST. A lead is grouped by rule only when it is clear:
  industry  - one configured industry's keywords appear in its industry /
              company / notes text (two different industries = unclear);
  market    - its city is one of exactly one configured market's cities, or
              its text names exactly one market ("DFW", "San Antonio").
A state alone ("TX") or two markets ("serves DFW and Houston") is NOT clear:
the lead is marked unclear_market and a person decides, optionally with an AI
suggestion (ai.py) that shows its reason and the text it used.

A person's choice (group_source "manual") is never overwritten by a rule or a
re-import. Industries and markets are configurable on the team settings
(EvoSysOutreachSettings.industries / .markets); these are the defaults.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.evosys_sales_models import EvoSysLead
from app.models.sales_models import Opportunity, OpportunityEvent

DEFAULT_INDUSTRIES: List[Dict[str, Any]] = [
    {"key": "commercial_cleaning", "label": "Commercial cleaning",
     "keywords": ["cleaning", "janitorial", "custodial", "maid", "housekeeping", "sanitiz", "disinfect"]},
    {"key": "window_washing", "label": "Window washing", "keywords": ["window"]},
    {"key": "pressure_washing", "label": "Pressure washing", "keywords": ["pressure wash", "power wash", "soft wash"]},
    {"key": "carpet_cleaning", "label": "Carpet cleaning", "keywords": ["carpet", "upholstery"]},
    {"key": "landscaping", "label": "Landscaping", "keywords": ["landscap", "lawn", "mowing"]},
    {"key": "pest_control", "label": "Pest control", "keywords": ["pest", "extermin", "termite"]},
    {"key": "hvac", "label": "HVAC", "keywords": ["hvac", "air condition", "heating"]},
]
# More specific industries win over the general "cleaning" words they contain
# ("window cleaning", "carpet cleaning").
SPECIFIC_FIRST = ["window_washing", "carpet_cleaning", "pressure_washing"]

DEFAULT_MARKETS: List[Dict[str, Any]] = [
    {"key": "dfw", "label": "DFW", "state": "TX", "aliases": ["dfw", "dallas-fort worth", "dallas fort worth", "metroplex"],
     "cities": ["dallas", "fort worth", "arlington", "plano", "irving", "garland", "frisco", "mckinney", "grand prairie",
                "mesquite", "carrollton", "denton", "richardson", "lewisville", "allen", "flower mound", "mansfield",
                "rowlett", "euless", "desoto", "grapevine", "bedford", "cedar hill", "keller", "wylie", "coppell",
                "rockwall", "burleson", "the colony", "little elm", "prosper", "southlake", "duncanville",
                "lancaster", "haltom city", "north richland hills", "hurst", "colleyville", "addison", "farmers branch",
                "forney", "midlothian", "waxahachie", "weatherford", "celina", "sachse", "murphy", "anna", "princeton"]},
    {"key": "houston", "label": "Houston", "state": "TX", "aliases": ["houston", "htx"],
     "cities": ["houston", "pasadena", "pearland", "sugar land", "the woodlands", "katy", "baytown", "conroe",
                "league city", "missouri city", "spring", "cypress", "humble", "friendswood", "tomball", "richmond"]},
    {"key": "san_antonio", "label": "San Antonio", "state": "TX", "aliases": ["san antonio", "satx"],
     "cities": ["san antonio", "new braunfels", "schertz", "seguin", "boerne", "converse", "live oak", "universal city"]},
    {"key": "austin", "label": "Austin", "state": "TX", "aliases": ["atx"],
     "cities": ["austin", "round rock", "cedar park", "georgetown", "pflugerville", "san marcos", "leander", "kyle", "buda"]},
]

SOURCE_CHANNELS = [
    ("facebook_ads", "Facebook ads"), ("facebook_groups", "Facebook groups"), ("tiktok", "TikTok"),
    ("google", "Google"), ("instagram_meta", "Instagram / Meta"), ("website", "Website / demo request"),
    ("cold_email_research", "Cold-email research"), ("existing_list", "Existing list"),
    ("networking", "Networking / business cards"), ("referral", "Referral"), ("other", "Other"),
]
SOURCE_LABEL = dict(SOURCE_CHANNELS)
# How each channel reaches EvoSys today. "import" = a file you upload; nothing
# here claims a live connection that has not been verified.
SOURCE_ACCESS = {k: "import" for k, _ in SOURCE_CHANNELS}
SOURCE_ACCESS["website"] = "connected"   # the evosyspro.live request-demo form (public_booking)
# Typed in by a person (Add contacts) - nothing to connect.
SOURCE_ACCESS["networking"] = "connected"
SOURCE_ACCESS["referral"] = "connected"

REVIEW_LABEL = {"ok": "Grouped", "unclear_market": "Unclear market", "possible_duplicate": "Possible duplicate",
                "held": "Held", "excluded": "Excluded"}
# Review statuses the follow-up may email. An unclear market still fits the
# default "All leads" campaign (its audience is any market), never a
# market-specific one (campaigns.campaign_for needs the market to match).
SENDABLE = ("ok", "unclear_market")

US_STATES = {"AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY",
             "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND",
             "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC"}


def industries(settings) -> List[Dict[str, Any]]:
    return (getattr(settings, "industries", None) or DEFAULT_INDUSTRIES)


def markets(settings) -> List[Dict[str, Any]]:
    return (getattr(settings, "markets", None) or DEFAULT_MARKETS)


def label_of(items: List[Dict[str, Any]], key: Optional[str]) -> Optional[str]:
    for i in items:
        if i["key"] == key:
            return i["label"]
    return None


def group_label(settings, industry_key, market_key) -> str:
    ind = label_of(industries(settings), industry_key) or ("Any industry" if not industry_key else industry_key)
    mk = label_of(markets(settings), market_key) or ("any market" if not market_key else market_key)
    return "%s · %s" % (ind, mk)


# ── facts from the existing record ────────────────────────────────────────────

def channel_of(source_system: Optional[str], source: Optional[str]) -> str:
    t = ("%s %s" % (source_system or "", source or "")).lower()
    for key, words in (("networking", ["networking", "business card"]), ("referral", ["referral", "referred"]),
                       ("facebook_groups", ["facebook group", "fb group"]),
                       ("facebook_ads", ["facebook", "fb-", "fb_ad", "facebook_ad"]),
                       ("tiktok", ["tiktok"]), ("instagram_meta", ["instagram", "meta ads"]),
                       ("google", ["google"]), ("website", ["website", "discovery + demo", "request-demo"]),
                       ("cold_email_research", ["cold", "research"]), ("existing_list", ["list", "csv"])):
        if any(w in t for w in words):
            return key
    return "other"


_LOC_NOTE = re.compile(r"Location:\s*([^.\n]+)", re.I)


def location_text(db: Session, opp: Opportunity) -> Tuple[Optional[str], Optional[str], str]:
    """(city, state, evidence) from what the record already says: its source
    text ("Facebook ad | FB-CLEAN-004 | Houston TX") and its import notes
    ("Location: Houston, TX."). Nothing is looked up or invented."""
    texts = [opp.source or ""]
    for e in (db.query(OpportunityEvent.detail).filter(OpportunityEvent.opportunity_id == opp.id,
                                                       OpportunityEvent.event_type.in_(("created", "note"))).limit(10).all()):
        m = _LOC_NOTE.search(e[0] or "")
        if m:
            texts.insert(0, m.group(1))
    tail = (opp.source or "").split("|")[-1].strip() if "|" in (opp.source or "") else ""
    for t in [texts[0], tail] if texts else [tail]:
        city, state = parse_place(t)
        if state or city:
            return city, state, t.strip()
    return None, None, ""


def parse_place(text: str) -> Tuple[Optional[str], Optional[str]]:
    t = (text or "").strip().strip(".")
    if not t:
        return None, None
    m = re.match(r"^(.*?)[,\s]+([A-Za-z]{2})$", t)
    if m and m.group(2).upper() in US_STATES:
        city = m.group(1).strip(" ,") or None
        return (city.title() if city else None), m.group(2).upper()
    if t.upper() in US_STATES:
        return None, t.upper()
    return t.title(), None


# ── the rules ─────────────────────────────────────────────────────────────────

def match_industry(settings, text: str) -> Tuple[Optional[str], str]:
    t = (text or "").lower()
    hits = [i["key"] for i in industries(settings) if any(k in t for k in i.get("keywords") or [])]
    if not hits:
        return None, "no industry words found"
    for k in SPECIFIC_FIRST:
        if k in hits:
            hits = [h for h in hits if h != "commercial_cleaning"]
            break
    if len(set(hits)) == 1:
        return hits[0], "industry words for %s" % label_of(industries(settings), hits[0])
    return None, "words for more than one industry (%s)" % ", ".join(sorted(set(hits)))


def match_market(settings, city: Optional[str], state: Optional[str], text: str) -> Tuple[Optional[str], str]:
    found = []
    c = (city or "").strip().lower()
    t = " %s " % re.sub(r"[^a-z0-9 -]", " ", (text or "").lower())
    for m in markets(settings):
        if state and m.get("state") and m["state"] != state:
            continue
        if c and c in (m.get("cities") or []):
            found.append((m["key"], "city %s is in %s" % (city, m["label"])))
            continue
        names = (m.get("aliases") or []) + [m["label"].lower()]
        if any(re.search(r"\b%s\b" % re.escape(n), t) for n in names):
            found.append((m["key"], "text names %s" % m["label"]))
    keys = sorted({k for k, _ in found})
    if len(keys) == 1:
        return keys[0], next(r for k, r in found if k == keys[0])
    if len(keys) > 1:
        return None, "more than one market (%s)" % ", ".join(label_of(markets(settings), k) for k in keys)
    if state and not c:
        return None, "only the state (%s) is known" % state
    if c:
        return None, "%s%s is not in a configured market" % (city, (", " + state) if state else "")
    return None, "no location on the record"


def classify(db: Session, settings, opp: Opportunity, city: Optional[str] = None, state: Optional[str] = None,
             extra_text: str = "") -> Dict[str, Any]:
    if city is None and state is None:
        city, state, _ = location_text(db, opp)
    text = " ".join(x for x in (opp.industry, opp.company_name, opp.source, extra_text) if x)
    ind, ind_why = match_industry(settings, text)
    mk, mk_why = match_market(settings, city, state, " ".join([extra_text, opp.source or "", city or ""]))
    return {"industry_key": ind, "market_key": mk, "city": city, "state": state,
            "clear": bool(ind and mk), "reason": "%s; %s" % (ind_why, mk_why)}


def ensure(db: Session, settings, opp: Opportunity, *, city=None, state=None, extra_text: str = "",
           review: Optional[str] = None, duplicate_of: Optional[str] = None) -> EvoSysLead:
    """Create or refresh the lead's group by rule. A manual choice, a hold or
    an exclusion made by a person is never overwritten."""
    row = db.get(EvoSysLead, opp.id)
    if row is None:
        row = EvoSysLead(opportunity_id=opp.id, brand_sales_org_id=opp.brand_sales_org_id, review_status="ok")
        db.add(row)
    if not row.source_channel:
        row.source_channel = channel_of(opp.source_system, opp.source)
    if row.group_source in ("manual", "ai_accepted"):
        return row
    c = classify(db, settings, opp, city, state, extra_text)
    row.city, row.state = c["city"] or row.city, c["state"] or row.state
    row.industry_key, row.market_key, row.group_reason = c["industry_key"], c["market_key"], c["reason"]
    row.group_source = "rule"
    if row.review_status not in ("held", "excluded"):
        if review:
            row.review_status = review
        elif duplicate_of:
            row.review_status, row.duplicate_of_id = "possible_duplicate", duplicate_of
        elif row.review_status == "possible_duplicate":
            pass
        else:
            row.review_status = "ok" if c["clear"] else "unclear_market"
    row.updated_at = datetime.utcnow()
    db.flush()
    return row


def backfill(db: Session, settings, bso_id: str) -> int:
    """Give every open lead on the team a group by rule (existing FB-CLEAN
    records included). Safe to run again; manual choices are kept."""
    have = {r[0] for r in db.query(EvoSysLead.opportunity_id).filter(EvoSysLead.brand_sales_org_id == bso_id).all()}
    n = 0
    for o in db.query(Opportunity).filter(Opportunity.brand_sales_org_id == bso_id, Opportunity.status == "open").all():
        if o.id in have:
            continue
        ensure(db, settings, o)
        n += 1
    db.flush()
    return n


_FREE_MAIL = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com", "aol.com", "live.com",
              "msn.com", "comcast.net", "att.net", "me.com", "protonmail.com", "ymail.com"}
_NAME_NOISE = re.compile(r"\b(llc|inc|co|corp|company|the|services?|cleaning|janitorial|solutions|group|and|&)\b")


def core_name(name: Optional[str]) -> str:
    t = re.sub(r"[^a-z0-9& ]", " ", (name or "").lower())
    return re.sub(r"\s+", " ", _NAME_NOISE.sub(" ", t)).strip()


def possible_duplicate(db: Session, opp: Opportunity) -> Optional[str]:
    """Another open lead on the team that is probably the same business but was
    not an exact match (exact ID / phone / website / name matches never get
    created - the importer reports them as already there): the same company
    name once 'LLC', 'Cleaning Services' etc. are removed, or the same email
    domain (free mail providers excluded). A person decides."""
    q = db.query(Opportunity).filter(Opportunity.brand_sales_org_id == opp.brand_sales_org_id,
                                     Opportunity.id != opp.id, Opportunity.status == "open")
    core = core_name(opp.company_name)
    dom = (opp.email or "").split("@")[-1].lower() if "@" in (opp.email or "") else ""
    for o in q.all():
        if core and len(core) >= 4 and core_name(o.company_name) == core:
            return o.id
        if dom and dom not in _FREE_MAIL and (o.email or "").lower().endswith("@" + dom):
            return o.id
    return None


def set_group(db: Session, opp: Opportunity, user_id: str, *, industry_key: Optional[str], market_key: Optional[str],
              source: str = "manual", note: Optional[str] = None) -> EvoSysLead:
    row = db.get(EvoSysLead, opp.id) or EvoSysLead(opportunity_id=opp.id, brand_sales_org_id=opp.brand_sales_org_id)
    db.add(row)
    row.industry_key, row.market_key = industry_key, market_key
    row.group_source = source
    row.group_reason = note or ("Set by a person" if source == "manual" else "AI suggestion accepted by a person")
    if row.review_status == "unclear_market":
        row.review_status = "ok"
    row.updated_at, row.updated_by = datetime.utcnow(), user_id
    db.add(OpportunityEvent(opportunity_id=opp.id, event_type="note", actor_user_id=user_id,
                            summary="Group set: %s / %s" % (industry_key or "any industry", market_key or "any market"),
                            detail=row.group_reason, occurred_at=datetime.utcnow()))
    db.flush()
    return row


def set_review(db: Session, opp: Opportunity, user_id: str, status: str, note: Optional[str] = None,
               duplicate_of: Optional[str] = None) -> EvoSysLead:
    row = db.get(EvoSysLead, opp.id) or EvoSysLead(opportunity_id=opp.id, brand_sales_org_id=opp.brand_sales_org_id)
    db.add(row)
    row.review_status, row.review_note = status, note
    if duplicate_of is not None:
        row.duplicate_of_id = duplicate_of or None
    row.updated_at, row.updated_by = datetime.utcnow(), user_id
    db.add(OpportunityEvent(opportunity_id=opp.id, event_type="note", actor_user_id=user_id,
                            summary="Lead review: %s" % REVIEW_LABEL.get(status, status),
                            detail=note, occurred_at=datetime.utcnow()))
    db.flush()
    return row
