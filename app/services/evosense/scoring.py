"""THE THREE SCORES — deterministic, versioned, explainable. No LLM decides a number.

    PROPERTY OPPORTUNITY  how attractive is this property under THIS strategy?
    CONTACT CONFIDENCE    how sure are we this contact is the owner / decision maker?
    SELLER INTENT         how strongly is this person signalling a real sale?

Plus DATA CONFIDENCE, which is deliberately separate from opportunity: a
property can score 92 on opportunity with LOW data confidence, and saying so
is better than pretending uncertain evidence is true.

Each function is pure (inputs in, result out) and returns:
    {"value": int|None, "label": str, "factors": [{"points", "label", "evidence"}],
     "inputs": {...}, "version": "..."}
`value` is None when the honest answer is INSUFFICIENT EVIDENCE.

CHANGING A FORMULA: bump the version string. `record()` stores every result
with its version and inputs, so history computed under v1 stays v1.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.evosense import common as C
from app.services.evosense import signals as SIG
from app.services.evosense import strategy as ST

PO_VERSION = "property_opportunity/v2"
# v2 = v1 + organization-configurable signal weights (fingerprinted into the
# version string when present) + the DFW public-record signals; signals with
# negative weight (active listing, recent sale) subtract and never count
# toward the multiple-signal bonus.
DC_VERSION = "data_confidence/v1"
CC_VERSION = "contact_confidence/v1"
SI_VERSION = "seller_intent/v1"


def _f(points: int, label: str, evidence: Any = None) -> Dict[str, Any]:
    return {"points": int(points), "label": label, "evidence": evidence}


def _clamp(v: int) -> int:
    return max(0, min(100, int(v)))


def band(value: Optional[int]) -> str:
    if value is None:
        return "insufficient"
    if value >= 80:
        return "hot"
    if value >= 65:
        return "high"
    if value >= 45:
        return "medium"
    return "low"


# ── PROPERTY OPPORTUNITY ────────────────────────────────────────────────────

def _equity_points(eq: Optional[int]) -> int:
    if eq is None:
        return 0
    if eq >= 60:
        return 14
    if eq >= 45:
        return 11
    if eq >= 35:
        return 8
    if eq >= 20:
        return 4
    return 0


def weights_fingerprint(weights: Optional[Dict[str, int]]) -> Optional[str]:
    if not weights:
        return None
    import hashlib
    import json
    return hashlib.sha1(json.dumps(weights, sort_keys=True).encode()).hexdigest()[:8]


def clean_weights(raw: Any) -> Dict[str, int]:
    """Only known signals, integers in [-30, 30]."""
    out: Dict[str, int] = {}
    for k, v in (raw or {}).items():
        if k in SIG.CATALOG:
            try:
                out[k] = max(-30, min(30, int(v)))
            except (TypeError, ValueError):
                continue
    return out


def property_opportunity(prop, stacked: List[Dict[str, Any]], strategy,
                         weights: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    weights = clean_weights(weights)
    fp = weights_fingerprint(weights)
    PO_VERSION = globals()["PO_VERSION"] + ("+w" + fp if fp else "")
    factors: List[Dict[str, Any]] = []
    inputs = {"signals": {s["signal_type"]: s["freshness"] for s in stacked},
              "equity_pct": prop.equity_pct, "estimated_value": prop.estimated_value,
              "ownership_years": prop.ownership_years, "property_type": prop.property_type,
              "strategy_id": getattr(strategy, "id", None),
              "strategy_version": getattr(strategy, "version", None),
              "weights": weights or None}
    live = {s["signal_type"]: s for s in stacked if s["freshness"] != SIG.STALE}

    if strategy is not None:
        inside, why = ST.geography_match(strategy, prop)
        if not inside:
            return {"value": 0, "label": "outside strategy", "version": PO_VERSION,
                    "inputs": inputs, "factors": [_f(0, "Outside hunting ground: " + why)]}
        excluded = [s for s in ST.lst(strategy, "excluded_signals") if s in live]
        if excluded:
            return {"value": 0, "label": "excluded", "version": PO_VERSION, "inputs": inputs,
                    "factors": [_f(0, "Excluded by strategy: %s" % ", ".join(
                        SIG.CATALOG[s]["label"] for s in excluded))]}

    if not live and prop.estimated_value is None and prop.equity_pct is None:
        return {"value": None, "label": "insufficient", "version": PO_VERSION, "inputs": inputs,
                "factors": [_f(0, "Insufficient evidence: no current signal, no value, no equity")]}

    total = 0
    for s in stacked:
        stype = s["signal_type"]
        spec = SIG.CATALOG.get(stype, {})
        pts = weights.get(stype, spec.get("points", 0))
        if stype == "LONG_OWNERSHIP":
            pts = 10 if (prop.ownership_years or 0) >= 15 else 6
            label = "Owned %s years" % prop.ownership_years if prop.ownership_years else spec["label"]
        elif stype == "HIGH_EQUITY":
            continue                       # equity is scored once, below, from the number
        else:
            label = spec.get("label", stype)
        if s["freshness"] == SIG.AGING:
            pts = int(pts / 2)
            label += " (aging evidence, half weight)"
        elif s["freshness"] == SIG.STALE:
            factors.append(_f(0, label + " — stale evidence, not counted",
                              {"signal": stype, "observed_at": s["observed_at"]}))
            continue
        if pts:
            total += pts
            factors.append(_f(pts, label, {"signal": stype, "sources": s["sources"],
                                           "observed_at": s["observed_at"]}))

    eq_pts = _equity_points(prop.equity_pct)
    if prop.equity_pct is not None:
        if eq_pts:
            total += eq_pts
            factors.append(_f(eq_pts, "Estimated equity %s%%" % prop.equity_pct,
                              {"basis": prop.equity_basis, "value_source": prop.estimated_value_source}))
        if strategy is not None and strategy.min_equity_pct and prop.equity_pct < strategy.min_equity_pct:
            total -= 15
            factors.append(_f(-15, "Equity below strategy minimum (%s%% < %s%%)" % (
                prop.equity_pct, strategy.min_equity_pct)))

    if strategy is not None:
        types = ST.lst(strategy, "property_types")
        if types and prop.property_type and prop.property_type not in types:
            total -= 25
            factors.append(_f(-25, "Property type %s is not in this strategy" %
                              prop.property_type.replace("_", "-")))
        v = prop.estimated_value
        if v is not None and (strategy.min_value is not None or strategy.max_value is not None):
            lo = strategy.min_value if strategy.min_value is not None else 0
            hi = strategy.max_value if strategy.max_value is not None else 10 ** 12
            if lo <= v <= hi:
                total += 7
                factors.append(_f(7, "Strategy value match ($%s)" % format(v, ",")))
            else:
                total -= 20
                factors.append(_f(-20, "Value $%s outside strategy range" % format(v, ",")))
        pref = [s for s in ST.lst(strategy, "preferred_signals") if s in live]
        if pref:
            bonus = min(6, 2 * len(pref))
            total += bonus
            factors.append(_f(bonus, "Preferred signals present: %s" % ", ".join(
                SIG.CATALOG[s]["label"] for s in pref)))
        geo = getattr(strategy, "owner_geography", None) or "any"
        if geo == "absentee" and "ABSENTEE_OWNER" not in live:
            total -= 10
            factors.append(_f(-10, "Strategy wants absentee owners; this owner's mailing address is the property"))
        elif geo == "out_of_state" and "OUT_OF_STATE_OWNER" not in live:
            total -= 10
            factors.append(_f(-10, "Strategy wants out-of-state owners"))
        if strategy.min_ownership_years and (prop.ownership_years or 0) < strategy.min_ownership_years:
            total -= 10
            factors.append(_f(-10, "Owned fewer than %s years" % strategy.min_ownership_years))

    def _positive(t):
        return weights.get(t, SIG.CATALOG.get(t, {}).get("points", 0)) > 0 or t == "LONG_OWNERSHIP"
    independent = {s["signal_type"] for s in stacked if s["freshness"] == SIG.CURRENT
                   and not s["derived"] and _positive(s["signal_type"])}
    derived_live = {s["signal_type"] for s in stacked if s["freshness"] == SIG.CURRENT and s["derived"]
                    and s["signal_type"] != "HIGH_EQUITY"}
    distinct = len(independent) + len(derived_live)
    if distinct >= 3 and len(independent) >= 2:
        total += 7
        factors.append(_f(7, "Multiple independent signals (%s)" % distinct))
    elif distinct >= 2:
        total += 3
        factors.append(_f(3, "Two signals"))

    value = _clamp(total)
    if strategy is not None:
        required = [s for s in ST.lst(strategy, "required_signals") if s not in live]
        if required:
            value = min(value, 40)
            factors.append(_f(0, "Capped at 40: required signal missing — %s" % ", ".join(
                SIG.CATALOG[s]["label"] for s in required)))
    factors.sort(key=lambda f: -f["points"])
    return {"value": value, "label": band(value), "version": PO_VERSION,
            "inputs": inputs, "factors": factors}


# ── DATA CONFIDENCE ─────────────────────────────────────────────────────────

def data_confidence(prop, stacked: List[Dict[str, Any]], owner_known: bool) -> Dict[str, Any]:
    factors = []
    pts = 0
    if prop.estimated_value is not None:
        pts += 20
        factors.append(_f(20, "Value reported (%s)" % (prop.estimated_value_source or "source")))
    else:
        factors.append(_f(0, "No value"))
    if prop.equity_pct is not None:
        pts += 15 if prop.equity_basis == "provider" else 10
        factors.append(_f(10, "Equity %s from value and mortgage" % (
            "reported" if prop.equity_basis == "provider" else "computed")))
    if owner_known:
        pts += 15
        factors.append(_f(15, "Owner of record known"))
    current = [s for s in stacked if s["freshness"] == SIG.CURRENT]
    stale = [s for s in stacked if s["freshness"] == SIG.STALE]
    if current:
        pts += min(25, 10 * len(current))
        factors.append(_f(min(25, 10 * len(current)), "%s current signal(s)" % len(current)))
    if stale:
        pts -= 10
        factors.append(_f(-10, "%s stale signal(s)" % len(stale)))
    sources = set()
    for s in stacked:
        sources.update(x for x in s["sources"] if x != SIG.DERIVED_SOURCE)
    if len(sources) >= 2:
        pts += 15
        factors.append(_f(15, "%s independent sources" % len(sources)))
    if prop.has_conflicts:
        pts -= 20
        factors.append(_f(-20, "Sources conflict"))
    if any(x == C.SANDBOX for s in stacked for x in [e.get("connector") for e in s["evidence"]]):
        factors.append(_f(0, "SANDBOX data — not a live source"))
    if prop.estimated_value is None and not current:
        label = "insufficient"
    elif pts >= 70:
        label = "high"
    elif pts >= 40:
        label = "medium"
    else:
        label = "low"
    return {"value": _clamp(pts), "label": label, "version": DC_VERSION,
            "inputs": {"sources": sorted(sources), "conflicts": bool(prop.has_conflicts)},
            "factors": factors}


# ── CONTACT CONFIDENCE ──────────────────────────────────────────────────────

def contact_confidence(cp, person, owner, *, mailing_agrees: Optional[bool],
                       when=None) -> Dict[str, Any]:
    when = when or C.now()
    factors = []
    total = 0
    from app.services.evosense.ingest import name_key
    if person is not None and owner is not None:
        if person.role in ("owner", "co_owner") and person.name_key and \
                person.name_key == name_key(owner.display_name):
            total += 25
            factors.append(_f(25, "Owner name match"))
        elif person.role in ("owner", "co_owner"):
            total += 15
            factors.append(_f(15, "Named co-owner of record"))
        elif person.role in ("heir", "executor"):
            total += 12
            factors.append(_f(12, "Source names this person as %s of the estate" % person.role))
        elif person.role == "agent":
            factors.append(_f(0, "Registered agent — not a decision maker"))
    if mailing_agrees:
        total += 20
        factors.append(_f(20, "Mailing address match"))
    if (cp.agreeing_sources or 1) >= 2:
        total += 18
        factors.append(_f(18, "%s sources agree" % ("Two" if cp.agreeing_sources == 2
                                                    else cp.agreeing_sources)))
    if cp.validation == "valid":
        if cp.kind == "phone" and cp.line_type == "mobile":
            total += 15
            factors.append(_f(15, "Mobile validated"))
        else:
            total += 8
            factors.append(_f(8, "%s validated" % (cp.line_type or cp.kind).title()))
    elif cp.validation == "invalid":
        total -= 40
        factors.append(_f(-40, "Failed validation"))
    else:
        factors.append(_f(0, "Not yet validated"))
    age_days = (when - (cp.first_seen_at or when)).days
    if age_days <= 365:
        total += 10
        factors.append(_f(10, "Recent record"))
    elif age_days > 3 * 365:
        total -= 20
        factors.append(_f(-20, "Stale source (%s years)" % (age_days // 365)))
    if cp.positive_response:
        total += 6
        factors.append(_f(6, "Prior successful response"))
    if cp.status == "wrong_party":
        total -= 30
        factors.append(_f(-30, "Wrong-party response"))
    if owner is not None and owner.resolution == "unresolved":
        total -= 12
        factors.append(_f(-12, "Unresolved %s relationship" % (owner.owner_type or "entity").upper()))
    if cp.provider_confidence is not None and not factors:
        total += cp.provider_confidence // 10
        factors.append(_f(cp.provider_confidence // 10, "Provider confidence %s" % cp.provider_confidence))
    value = _clamp(total)
    return {"value": value, "label": band(value), "version": CC_VERSION,
            "inputs": {"contact_point": cp.id, "status": cp.status, "validation": cp.validation,
                       "line_type": cp.line_type, "agreeing_sources": cp.agreeing_sources,
                       "provider_confidence": cp.provider_confidence},
            "factors": sorted(factors, key=lambda f: -f["points"])}


# ── SELLER INTENT ───────────────────────────────────────────────────────────

INTENT_POSITIVE = [
    ("willing_to_sell", 25, "Said willing to sell"),
    ("asking_price", 18, "Provided asking price"),
    ("wants_quick_close", 15, "Wants quick close"),
    ("condition", 12, "Discussed property condition"),
    ("appointment_request", 12, "Asked for an appointment"),
    ("callback_request", 10, "Requested call"),
    ("offer_request", 10, "Requested an offer"),
    ("estate_context", 6, "Explained estate / inherited situation"),
    ("occupancy", 5, "Confirmed occupancy"),
]


def seller_intent(facts, outcome: Optional[str], inbound_count: int) -> Dict[str, Any]:
    """From SELLER-STATED facts and the classified outcome only. Sentiment is
    not an input, and provider data never raises seller intent."""
    stated = [f for f in facts if f.truth_state == C.T_SELLER_STATED and not f.superseded]
    kinds = {f.fact_type: f for f in stated}
    inputs = {"outcome": outcome, "facts": sorted(kinds), "inbound_messages": inbound_count}

    zero = {C.O_DNC: "Asked not to be contacted", C.O_WRONG_PERSON: "Wrong person — no seller here",
            C.O_NOT_OWNER: "Not the owner", C.O_ALREADY_SOLD: "Already sold",
            C.O_HARD_NO: "Said no"}
    if outcome in zero:
        return {"value": 0, "label": "none", "version": SI_VERSION, "inputs": inputs,
                "factors": [_f(0, zero[outcome])]}
    if not stated and outcome in (None, C.O_UNKNOWN):
        return {"value": None, "label": "insufficient", "version": SI_VERSION, "inputs": inputs,
                "factors": [_f(0, "No seller statement yet")]}

    total = 0
    factors = []
    for key, pts, label in INTENT_POSITIVE:
        f = kinds.get(key)
        if f is not None:
            total += pts
            factors.append(_f(pts, label, {"fact": f.id, "quote": f.quote, "message": f.message_id}))
    if outcome in (C.O_INTERESTED, C.O_WANTS_OFFER, C.O_APPOINTMENT) and "willing_to_sell" not in kinds:
        total += 15
        factors.append(_f(15, "Responded with interest"))
    if inbound_count >= 2:
        total += 8
        factors.append(_f(8, "Continued conversation (%s replies)" % inbound_count))
    if outcome == C.O_LISTED:
        total -= 30
        factors.append(_f(-30, "Listed with an agent"))
    if outcome == C.O_PRICE_TOO_HIGH:
        total -= 5
        factors.append(_f(-5, "Price expectation above offer range"))
    value = _clamp(total)
    if outcome in (C.O_NOT_NOW, C.O_CALL_LATER, C.O_FAMILY, C.O_MAYBE):
        value = min(value, 25 if outcome == C.O_NOT_NOW else 40)
        factors.append(_f(0, "Capped: %s" % outcome.replace("_", " ").lower()))
    return {"value": value, "label": band(value), "version": SI_VERSION, "inputs": inputs,
            "factors": sorted(factors, key=lambda f: -f["points"])}


# ── persistence ─────────────────────────────────────────────────────────────

def record(db, prop, score_type: str, result: Dict[str, Any], *, subject_type="property",
           subject_id=None, strategy_id=None):
    """Store a score. The previous current score of the same kind for the same
    subject is kept, marked not-current — never overwritten."""
    from app.models.evosense_models import EvoSenseScore
    subject_id = subject_id or prop.id
    db.flush()          # sessions here do not autoflush; read what was just written
    prev = (db.query(EvoSenseScore)
            .filter(EvoSenseScore.organization_id == prop.organization_id,
                    EvoSenseScore.property_id == prop.id,
                    EvoSenseScore.score_type == score_type,
                    EvoSenseScore.subject_id == subject_id,
                    EvoSenseScore.is_current.is_(True)).first())
    if prev is not None and prev.value == result["value"] and prev.version == result["version"] \
            and prev.factors == C.jdump(result["factors"]):
        return prev                   # unchanged: no new history row
    if prev is not None:
        prev.is_current = False
    row = EvoSenseScore(organization_id=prop.organization_id, property_id=prop.id,
                        subject_type=subject_type, subject_id=subject_id,
                        score_type=score_type, version=result["version"],
                        value=result["value"], label=result.get("label"),
                        inputs=C.jdump(result.get("inputs")),
                        factors=C.jdump(result["factors"]), strategy_id=strategy_id)
    db.add(row)
    db.flush()
    return row
