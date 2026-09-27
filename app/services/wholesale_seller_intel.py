"""SELLER INTELLIGENCE for Wholesale - facts with provenance, Seller Intent,
the qualification outcome, and nurture.

THREE SCORES, NEVER ONE
  Property Opportunity   is the house worth pursuing        (EvoSense / deal analysis)
  Contact Confidence     is this the right person/number     (EvoSense contacts)
  Seller Intent          does this person want to sell       (HERE, from what they said)
A distressed property with a verified number and a seller who said "not for
years" is HIGH / HIGH / LOW - and nurtures, it does not scream HOT LEAD.

FACTS (WholesaleSellerFact)
  One row per thing the seller told us, with the words it was read from, the
  message it came from and how it was read. The reader is EvoSense's
  deterministic classifier (exact quotes) plus the Wholesale AI reading
  (structured fields); both read the SELLER'S OWN WORDS, so both produce
  `seller_stated` facts - never verified ones. Only a person verifies.

SELLER INTENT reuses EvoSense's `scoring.seller_intent` unchanged: the same
facts in, the same explainable number out, whichever module heard the seller.

QUALIFICATION OUTCOME
  QUALIFIED               the configured criteria are met
  NEEDS_MORE_INFORMATION  nothing rules them out; named things are still unknown
  NURTURE                 not now - a later date, a long timeline, "call me later"
  NOT_INTERESTED          said no, or listed with an agent
  DISQUALIFIED            opted out, wrong person, not the owner, already sold
  HUMAN_REVIEW            ownership complication, low reading confidence, or a
                          reader that could not run
An unknown field is a question to ask, not a rejection.

Nothing here sends anything or changes consent.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.models.wholesale_models import WholesaleSellerFact, WholesaleSellerProfile

QUALIFIED = "QUALIFIED"
NEEDS_MORE_INFORMATION = "NEEDS_MORE_INFORMATION"
NURTURE = "NURTURE"
NOT_INTERESTED = "NOT_INTERESTED"
DISQUALIFIED = "DISQUALIFIED"
HUMAN_REVIEW = "HUMAN_REVIEW"
QUALIFICATION_OUTCOMES = (QUALIFIED, NEEDS_MORE_INFORMATION, NURTURE, NOT_INTERESTED,
                          DISQUALIFIED, HUMAN_REVIEW)
LABELS = {QUALIFIED: "Qualified", NEEDS_MORE_INFORMATION: "Needs more information",
          NURTURE: "Nurture", NOT_INTERESTED: "Not interested", DISQUALIFIED: "Disqualified",
          HUMAN_REVIEW: "Human review"}

# What a seller must have told us before they are QUALIFIED. Tenant-configurable
# (WholesaleSettings.qualification_criteria); NULL means these.
CRITERIA = {
    "selling_interest": "They said they want to sell",
    "timeline": "When they want to sell",
    "decision_maker": "Who decides / who is on title",
    "asking_price": "What price they expect",
    "condition": "The property's condition",
    "occupancy": "Who lives there",
}
DEFAULT_CRITERIA = {"require": ["selling_interest", "timeline"],
                    "max_timeline": "6_months", "min_seller_intent": 30}
TIMELINE_ORDER = ["asap", "30_days", "60_days", "90_days", "6_months", "no_rush"]
DEFAULT_NURTURE_DAYS = 60

# AI reading field -> fact type (EvoSense's fact vocabulary where it has one).
_READING_FACTS = {
    "asking_price": "asking_price", "timeline": "timeline", "property_condition": "condition",
    "major_repairs": "repairs", "occupancy": "occupancy", "motivation": "motivation",
    "reason_for_selling": "motivation", "mortgage_note": "mortgage",
    "decision_makers": "decision_makers", "best_callback_time": "callback_request",
}
# EvoSense outcome -> the fact it establishes (quote = the whole message).
_OUTCOME_FACTS = {
    "WANTS_OFFER": ("offer_request", "yes"), "APPOINTMENT": ("appointment_request", "yes"),
    "CALL_LATER": ("callback_request", "later"), "PRICE_TOO_HIGH": ("objection", "price"),
    "LISTED_WITH_AGENT": ("objection", "listed with an agent"),
    "ESTATE_HANDLING": ("ownership_complication", "estate"),
    "FAMILY_DECIDING": ("ownership_complication", "family deciding"),
    "NOT_OWNER": ("ownership_complication", "not the owner"),
    "TENANT_ISSUE": ("occupancy", "tenant issue"),
}
DISQUALIFYING = {"DO_NOT_CONTACT": "Asked not to be contacted", "WRONG_PERSON": "Wrong person",
                 "NOT_OWNER": "Not the owner", "ALREADY_SOLD": "Already sold"}
NOT_INTERESTED_OUTCOMES = {"HARD_NO": "Said no", "LISTED_WITH_AGENT": "Listed with an agent"}
NURTURE_OUTCOMES = {"NOT_NOW": "Not now", "CALL_LATER": "Asked to be contacted later",
                    "MAYBE": "Undecided"}
# wholesale_ai intents that map onto the same outcomes
_AI_INTENT_OUTCOME = {"do_not_contact": "DO_NOT_CONTACT", "wrong_person": "WRONG_PERSON",
                      "already_sold": "ALREADY_SOLD", "not_interested": "HARD_NO",
                      "maybe_later": "NOT_NOW", "interested": "INTERESTED",
                      "qualified_opportunity": "INTERESTED"}


def _now():
    return datetime.utcnow()


def criteria(settings) -> Dict[str, Any]:
    out = dict(DEFAULT_CRITERIA)
    raw = getattr(settings, "qualification_criteria", None)
    if raw:
        try:
            given = json.loads(raw)
            if isinstance(given, dict):
                if isinstance(given.get("require"), list):
                    out["require"] = [c for c in given["require"] if c in CRITERIA]
                if given.get("max_timeline") in TIMELINE_ORDER:
                    out["max_timeline"] = given["max_timeline"]
                if isinstance(given.get("min_seller_intent"), int):
                    out["min_seller_intent"] = max(0, min(100, given["min_seller_intent"]))
        except (ValueError, TypeError):
            pass
    return out


def validate_criteria(value: Any) -> Dict[str, Any]:
    """Clean a criteria object for storage. Raises ValueError with a sentence."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("Qualification criteria must be an object.")
    req = value.get("require", DEFAULT_CRITERIA["require"])
    if not isinstance(req, list) or any(r not in CRITERIA for r in req):
        raise ValueError("Required criteria must be from: %s." % ", ".join(CRITERIA))
    mt = value.get("max_timeline", DEFAULT_CRITERIA["max_timeline"])
    if mt not in TIMELINE_ORDER:
        raise ValueError("Maximum timeline must be one of: %s." % ", ".join(TIMELINE_ORDER))
    mi = value.get("min_seller_intent", DEFAULT_CRITERIA["min_seller_intent"])
    if not isinstance(mi, int) or not 0 <= mi <= 100:
        raise ValueError("Minimum seller intent must be 0-100.")
    return {"require": req, "max_timeline": mt, "min_seller_intent": mi}


# ── facts ───────────────────────────────────────────────────────────────────

def current_facts(db, profile) -> List[WholesaleSellerFact]:
    return (db.query(WholesaleSellerFact)
            .filter(WholesaleSellerFact.organization_id == profile.organization_id,
                    WholesaleSellerFact.profile_id == profile.id,
                    WholesaleSellerFact.superseded.is_(False))
            .order_by(WholesaleSellerFact.created_at.asc()).all())


def _add(db, profile, deal_id, lead_id, fact_type, value, quote, *, message_ref, channel,
         extracted_by, confidence=None, truth_state="seller_stated") -> WholesaleSellerFact:
    for old in (db.query(WholesaleSellerFact)
                .filter(WholesaleSellerFact.organization_id == profile.organization_id,
                        WholesaleSellerFact.profile_id == profile.id,
                        WholesaleSellerFact.fact_type == fact_type,
                        WholesaleSellerFact.superseded.is_(False)).all()):
        old.superseded = True
    f = WholesaleSellerFact(organization_id=profile.organization_id, profile_id=profile.id,
                            deal_id=deal_id, lead_id=lead_id, fact_type=fact_type,
                            value=None if value is None else str(value)[:500],
                            quote=(quote or "")[:600] or None, message_ref=message_ref,
                            source_channel=channel, extracted_by=extracted_by,
                            truth_state=truth_state, confidence=confidence,
                            is_test=bool(profile.is_test))
    db.add(f)
    return f


def record_message_facts(db, profile, deal, lead, text: str, reading: Dict[str, Any], *,
                         message_ref: Optional[str], channel: str = "sms") -> Dict[str, Any]:
    """Every fact one seller message established, with its provenance.
    Returns {"outcome", "facts": [types]} - the outcome is EvoSense's reading
    of the message, or the AI intent mapped onto it when the rules were silent."""
    from app.services.evosense import conversation as EVC
    ev = EVC.classify(text or "")
    outcome = ev.get("outcome")
    if outcome in (None, "UNKNOWN") and reading.get("intent") in _AI_INTENT_OUTCOME:
        outcome = _AI_INTENT_OUTCOME[reading["intent"]]
    deal_id, lead_id = getattr(deal, "id", None), getattr(lead, "id", None)
    added: Dict[str, WholesaleSellerFact] = {}
    for f in ev.get("facts") or []:
        added[f["fact_type"]] = _add(db, profile, deal_id, lead_id, f["fact_type"], f.get("value"),
                                     f.get("quote"), message_ref=message_ref, channel=channel,
                                     extracted_by="rules")
    if outcome in _OUTCOME_FACTS:
        ft, val = _OUTCOME_FACTS[outcome]
        if ft not in added:
            added[ft] = _add(db, profile, deal_id, lead_id, ft, val, text, message_ref=message_ref,
                             channel=channel, extracted_by="rules")
    src = "ai" if reading.get("source") == "ai" else "rules"
    conf = reading.get("confidence") if src == "ai" else None
    if reading.get("considering_selling") is True and "willing_to_sell" not in added:
        added["willing_to_sell"] = _add(db, profile, deal_id, lead_id, "willing_to_sell", "yes", text,
                                        message_ref=message_ref, channel=channel,
                                        extracted_by=src, confidence=conf)
    if reading.get("considering_selling") is False and "not_selling" not in added:
        added["not_selling"] = _add(db, profile, deal_id, lead_id, "not_selling", "no", text,
                                    message_ref=message_ref, channel=channel, extracted_by=src,
                                    confidence=conf)
    for field, ft in _READING_FACTS.items():
        v = reading.get(field)
        if v is None or ft in added:
            continue
        added[ft] = _add(db, profile, deal_id, lead_id, ft, v, text, message_ref=message_ref,
                         channel=channel, extracted_by=src, confidence=conf)
    if reading.get("timeline") in ("asap", "30_days") and "wants_quick_close" not in added:
        added["wants_quick_close"] = _add(db, profile, deal_id, lead_id, "wants_quick_close",
                                          reading["timeline"], text, message_ref=message_ref,
                                          channel=channel, extracted_by=src, confidence=conf)
    db.flush()
    for ft in ("repairs", "condition"):
        if ft in added:
            seller_reported_repairs(db, deal, added[ft])
    return {"outcome": outcome, "facts": sorted(added)}


def seller_reported_repairs(db, deal, fact) -> None:
    """What the SELLER said about repairs/condition goes into the deal's repair
    history as SELLER_REPORTED - with the quote and the fact it came from. It
    becomes the current status only if nothing better (an estimate) exists,
    and it never carries an invented dollar amount."""
    if deal is None:
        return
    from app.services import wholesale_repairs as REP
    try:
        REP.record(db, fact.organization_id, deal, status=REP.SELLER_REPORTED,
                   source="seller", supplied_by_label="Seller (own words)",
                   notes="%s: %s" % (fact.fact_type.replace("_", " "), fact.quote or fact.value),
                   provenance={"fact_id": fact.id, "message_ref": fact.message_ref,
                               "extracted_by": fact.extracted_by},
                   make_current=REP.status_of(deal) in (REP.UNKNOWN, REP.SELLER_REPORTED))
    except ValueError:
        pass


FORM_FIELDS = {"timeline": "timeline", "property_condition": "condition",
               "reason_for_selling": "motivation", "asking_price": "asking_price",
               "preferred_contact_method": "preferred_contact"}


def record_form_facts(db, profile, deal, lead, form: Dict[str, Any], *, submission_id=None) -> None:
    """What a seller typed into the inquiry form - their own words, so
    seller_stated, with the field as the quote."""
    ref = "form:%s" % (submission_id or "inquiry")
    _add(db, profile, getattr(deal, "id", None), getattr(lead, "id", None), "willing_to_sell",
         "inquired", "Submitted the seller inquiry form", message_ref=ref, channel="form",
         extracted_by="form")
    for key, ft in FORM_FIELDS.items():
        v = form.get(key)
        if v in (None, ""):
            continue
        _add(db, profile, getattr(deal, "id", None), getattr(lead, "id", None), ft, v,
             "%s: %s" % (key.replace("_", " "), v), message_ref=ref, channel="form",
             extracted_by="form")
    if form.get("notes"):
        _add(db, profile, getattr(deal, "id", None), getattr(lead, "id", None), "seller_notes",
             form["notes"][:500], form["notes"], message_ref=ref, channel="form",
             extracted_by="form")
    db.flush()
    if form.get("property_condition"):
        cond = [f for f in current_facts(db, profile) if f.fact_type == "condition"]
        if cond:
            seller_reported_repairs(db, deal, cond[-1])


# ── seller intent ───────────────────────────────────────────────────────────

def seller_intent(db, profile, outcome: Optional[str], lead=None) -> Dict[str, Any]:
    """EvoSense's Seller Intent, computed from THIS seller's facts."""
    from types import SimpleNamespace
    from app.models.models import Reply
    from app.services.evosense import scoring as SC
    facts = [SimpleNamespace(id=f.id, fact_type=f.fact_type, truth_state=f.truth_state,
                             superseded=f.superseded, quote=f.quote, message_id=f.message_ref)
             for f in current_facts(db, profile)]
    inbound = 0
    if lead is not None:
        inbound = db.query(Reply.id).filter(Reply.lead_id == lead.id).count()
    return SC.seller_intent(facts, outcome, inbound)


# ── qualification outcome ───────────────────────────────────────────────────

def _known(profile, facts_by_type, key) -> bool:
    if key == "selling_interest":
        return profile.considering_selling is True or "willing_to_sell" in facts_by_type
    if key == "timeline":
        return bool(profile.timeline) or "timeline" in facts_by_type
    if key == "decision_maker":
        return bool(profile.decision_makers) or "decision_makers" in facts_by_type
    if key == "asking_price":
        return profile.asking_price is not None or "asking_price" in facts_by_type
    if key == "condition":
        return bool(profile.property_condition) or "condition" in facts_by_type
    if key == "occupancy":
        return bool(profile.occupancy) or "occupancy" in facts_by_type
    return False


def qualification(db, profile, deal, settings, lead, *, outcome: Optional[str] = None,
                  intent: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The decision a person acts on, and exactly why."""
    crit = criteria(settings)
    facts = {f.fact_type: f for f in current_facts(db, profile)}
    reasons: List[str] = []
    missing = [k for k in crit["require"] if not _known(profile, facts, k)]
    unknowns = [k for k in CRITERIA if k not in crit["require"] and not _known(profile, facts, k)]
    iv = (intent or {}).get("value")

    def done(status, why):
        return {"status": status, "label": LABELS[status], "reasons": why + reasons,
                "missing": missing, "known_unknowns": unknowns, "criteria": crit,
                "seller_intent": iv, "outcome": outcome}

    if lead is not None and (lead.status or "") == "dnc":
        return done(DISQUALIFIED, ["Asked not to be contacted"])
    if outcome in DISQUALIFYING:
        return done(DISQUALIFIED, [DISQUALIFYING[outcome]])
    if profile.considering_selling is False or "not_selling" in facts:
        return done(NOT_INTERESTED, ["Said they are not selling"])
    if outcome in NOT_INTERESTED_OUTCOMES:
        return done(NOT_INTERESTED, [NOT_INTERESTED_OUTCOMES[outcome]])
    if profile.nurture_until and profile.nurture_until > _now():
        return done(NURTURE, ["In nurture until %s%s" % (profile.nurture_until.strftime("%b %d, %Y"),
                                                          " — %s" % profile.nurture_reason
                                                          if profile.nurture_reason else "")])
    if outcome in NURTURE_OUTCOMES:
        return done(NURTURE, [NURTURE_OUTCOMES[outcome]])
    if profile.needs_human:
        return done(HUMAN_REVIEW, [profile.needs_human_reason or "The reader asked for a person"])
    if "ownership_complication" in facts:
        return done(HUMAN_REVIEW, ["Ownership complication: %s — confirm who can sell"
                                   % facts["ownership_complication"].value])
    if profile.timeline and profile.timeline in TIMELINE_ORDER and \
            TIMELINE_ORDER.index(profile.timeline) > TIMELINE_ORDER.index(crit["max_timeline"]):
        return done(NURTURE, ["Timeline %s is beyond %s" % (profile.timeline.replace("_", " "),
                                                            crit["max_timeline"].replace("_", " "))])
    if missing:
        return done(NEEDS_MORE_INFORMATION,
                    ["Still unknown: %s" % ", ".join(CRITERIA[m].lower() for m in missing)])
    if iv is not None and iv < crit["min_seller_intent"]:
        return done(NEEDS_MORE_INFORMATION, ["Seller Intent %s is below %s"
                                             % (iv, crit["min_seller_intent"])])
    return done(QUALIFIED, ["Meets the workspace's criteria: %s"
                            % ", ".join(CRITERIA[c].lower() for c in crit["require"])])


def store(profile, qual: Dict[str, Any], intent: Optional[Dict[str, Any]]) -> None:
    profile.qualification_status = qual["status"]
    profile.qualification_detail = json.dumps(qual, default=str)
    if qual["status"] == QUALIFIED and getattr(profile, "qualified_at", None) is None:
        profile.qualified_at = datetime.utcnow()
    if intent is not None:
        profile.seller_intent = intent.get("value")
        profile.seller_intent_detail = json.dumps(intent, default=str)


def refresh(db, profile, deal, settings, lead, *, outcome: Optional[str] = None) -> Dict[str, Any]:
    intent = seller_intent(db, profile, outcome, lead)
    qual = qualification(db, profile, deal, settings, lead, outcome=outcome, intent=intent)
    store(profile, qual, intent)
    return {"intent": intent, "qualification": qual}


# ── nurture ─────────────────────────────────────────────────────────────────

_TIMELINE_DAYS = {"6_months": 150, "no_rush": 180, "90_days": 75, "60_days": 45}


def nurture_days(settings, profile=None, outcome=None) -> int:
    base = getattr(settings, "nurture_default_days", None) or DEFAULT_NURTURE_DAYS
    if profile is not None and profile.timeline in _TIMELINE_DAYS:
        return _TIMELINE_DAYS[profile.timeline]
    return base


def set_nurture(profile, *, days: Optional[int] = None, until: Optional[datetime] = None,
                reason: Optional[str] = None) -> None:
    profile.nurture_until = until or (_now() + timedelta(days=days or DEFAULT_NURTURE_DAYS))
    profile.nurture_reason = (reason or "")[:200] or None


def clear_nurture(profile) -> None:
    profile.nurture_until = None
    profile.nurture_reason = None


def nurture_due(db, org_id: str, *, now: Optional[datetime] = None) -> List[WholesaleSellerProfile]:
    """Sellers whose nurture date has arrived. Re-engagement uses the SAME
    person and property; a DNC seller is never due."""
    from app.models.models import Lead
    now = now or _now()
    rows = (db.query(WholesaleSellerProfile)
            .join(Lead, Lead.id == WholesaleSellerProfile.lead_id)
            .filter(WholesaleSellerProfile.organization_id == org_id,
                    WholesaleSellerProfile.nurture_until.isnot(None),
                    WholesaleSellerProfile.nurture_until <= now,
                    WholesaleSellerProfile.is_test.isnot(True),
                    Lead.status != "dnc").all())
    return rows


def fact_json(f: WholesaleSellerFact) -> Dict[str, Any]:
    return {"id": f.id, "fact_type": f.fact_type, "value": f.value, "quote": f.quote,
            "message_ref": f.message_ref, "source_channel": f.source_channel,
            "extracted_by": f.extracted_by, "truth_state": f.truth_state,
            "verified": f.verified_at is not None,
            "verified_at": f.verified_at.isoformat() + "Z" if f.verified_at else None,
            "confidence": f.confidence,
            "at": f.created_at.isoformat() + "Z" if f.created_at else None}
