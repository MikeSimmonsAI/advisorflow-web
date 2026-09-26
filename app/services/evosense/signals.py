"""Property signals: evidence, freshness, derivation and stacking.

A signal is EVIDENCE that a property may be interesting. It is never truth
merely because a provider returned it. Every signal keeps its source, when it
was observed, how confident the source was, and it ages:

    CURRENT  -> AGING (counts half) -> STALE (counts nothing, stays visible)

History is never erased when a signal goes stale.

DERIVATION RULES v3 (derive/v3). Missing, failed, ambiguous or unavailable
evidence NEVER becomes affirmative evidence:
  * absentee owner needs a KNOWN mailing address that is KNOWN to differ from
    the property (a suffix-less "2427 LILLIAN" is the same place as "2427
    LILLIAN ST"); an ambiguous comparison, a PO box or a homestead exemption
    (the owner's declared residence) produces no signal
  * a CLOSED code case / 311 request is CODE_HISTORY (0 points), not a
    current violation or complaint
  * an appraisal district CDU rating is labelled as exactly that and dated to
    its appraisal year; UNDESIRABLE is not "distressed"
  * a deed transfer is a deed transfer ("sold" is never implied); one inside
    the last year is RECENT_DEED_TRANSFER, which subtracts
  * a tax balance past a NON-standard delinquency date is TAX_TERMS_UNVERIFIED
    (0 points) until a person verifies the terms
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.services.evosense import common as C

CURRENT = "current"
AGING = "aging"
STALE = "stale"

# points = contribution to Property Opportunity when CURRENT (scoring.py)
# current_days / stale_days = the freshness policy for that kind of evidence
CATALOG: Dict[str, Dict[str, Any]] = {
    "VACANT":               {"label": "Vacant", "points": 18, "current_days": 90, "stale_days": 180},
    "ABSENTEE_OWNER":       {"label": "Absentee owner", "points": 15, "current_days": 365, "stale_days": 730, "derived": True},
    "OUT_OF_STATE_OWNER":   {"label": "Out-of-state owner", "points": 8, "current_days": 365, "stale_days": 730, "derived": True},
    "HIGH_EQUITY":          {"label": "High equity", "points": 0, "current_days": 180, "stale_days": 365, "derived": True},
    "FREE_AND_CLEAR":       {"label": "Free and clear", "points": 6, "current_days": 180, "stale_days": 365, "derived": True},
    "LONG_OWNERSHIP":       {"label": "Long ownership", "points": 10, "current_days": 3650, "stale_days": 7300, "derived": True},
    "TAX_DELINQUENT":       {"label": "Tax delinquent", "points": 12, "current_days": 365, "stale_days": 730},
    "PRE_FORECLOSURE":      {"label": "Pre-foreclosure", "points": 16, "current_days": 120, "stale_days": 240},
    "PROBATE":              {"label": "Probate", "points": 12, "current_days": 365, "stale_days": 730},
    "ESTATE":               {"label": "Estate", "points": 10, "current_days": 365, "stale_days": 730},
    "CODE_VIOLATION":       {"label": "Code violation", "points": 8, "current_days": 180, "stale_days": 365},
    "LIEN":                 {"label": "Lien", "points": 6, "current_days": 365, "stale_days": 730},
    "DISTRESSED_CONDITION": {"label": "Distressed condition", "points": 8, "current_days": 180, "stale_days": 365},
    "EXPIRED_LISTING":      {"label": "Expired listing", "points": 6, "current_days": 120, "stale_days": 240},
    "FAILED_LISTING":       {"label": "Failed listing", "points": 6, "current_days": 120, "stale_days": 240},
    "PRICE_REDUCTION":      {"label": "Price reduction", "points": 4, "current_days": 60, "stale_days": 120},
    "TIRED_LANDLORD":       {"label": "Tired landlord", "points": 8, "current_days": 365, "stale_days": 730},
    "MANUAL_OPERATOR_SIGNAL": {"label": "Operator flag", "points": 5, "current_days": 180, "stale_days": 365},
    # DFW public-record additions (property_opportunity/v2)
    "TAX_SUIT":             {"label": "Tax suit / litigation", "points": 8, "current_days": 365, "stale_days": 730},
    "CODE_COMPLAINT":       {"label": "Code complaint (311)", "points": 3, "current_days": 180, "stale_days": 365},
    "ACTIVE_LISTING":       {"label": "Active listing", "points": -12, "current_days": 60, "stale_days": 120},
    "RECENT_SALE":          {"label": "Recent sale", "points": -10, "current_days": 365, "stale_days": 730},
    "OTHER":                {"label": "Other evidence", "points": 0, "current_days": 180, "stale_days": 365},
    # derive/v3 additions (property_opportunity/v3 platform defaults; each tenant may override)
    "CDU_POOR":             {"label": "Appraisal district CDU rating: poor", "points": 4,
                             "current_days": 365, "stale_days": 730},
    "CDU_UNDESIRABLE":      {"label": "Appraisal district CDU rating: undesirable", "points": 0,
                             "current_days": 365, "stale_days": 730},
    "CODE_HISTORY":         {"label": "Code history (closed)", "points": 0, "current_days": 365, "stale_days": 730},
    "TAX_TERMS_UNVERIFIED": {"label": "Tax balance — terms unverified", "points": 0,
                             "current_days": 365, "stale_days": 730},
    "RECENT_DEED_TRANSFER": {"label": "Recent deed transfer", "points": -10, "current_days": 365,
                             "stale_days": 730, "derived": True},
}

# The acquisition spec's signal families. EvoSense's stored keys predate the
# spec and are kept (renaming stored evidence is an unnecessary rewrite);
# every key reports the family it belongs to.
FAMILY = {
    "TAX_DELINQUENT": "TAX_DELINQUENT", "TAX_SUIT": "TAX_DELINQUENT", "LIEN": "OTHER",
    "PRE_FORECLOSURE": "PRE_FORECLOSURE", "ABSENTEE_OWNER": "ABSENTEE_OWNER",
    "OUT_OF_STATE_OWNER": "OUT_OF_STATE_OWNER", "VACANT": "VACANT",
    "CODE_VIOLATION": "CODE_VIOLATION", "CODE_COMPLAINT": "CODE_VIOLATION",
    "PROBATE": "PROBATE_OR_ESTATE", "ESTATE": "PROBATE_OR_ESTATE",
    "LONG_OWNERSHIP": "LONG_TERM_OWNERSHIP", "HIGH_EQUITY": "HIGH_EQUITY",
    "FREE_AND_CLEAR": "FREE_AND_CLEAR", "TIRED_LANDLORD": "TIRED_LANDLORD",
    "DISTRESSED_CONDITION": "DISTRESSED_PROPERTY", "EXPIRED_LISTING": "RECENT_FAILED_LISTING",
    "FAILED_LISTING": "RECENT_FAILED_LISTING", "PRICE_REDUCTION": "ACTIVE_LISTING",
    "ACTIVE_LISTING": "ACTIVE_LISTING", "RECENT_SALE": "RECENT_SALE",
    "MANUAL_OPERATOR_SIGNAL": "OTHER", "OTHER": "OTHER",
    "CDU_POOR": "APPRAISER_RATING", "CDU_UNDESIRABLE": "APPRAISER_RATING", "CODE_HISTORY": "CODE_HISTORY",
    "TAX_TERMS_UNVERIFIED": "TAX_DELINQUENT", "RECENT_DEED_TRANSFER": "RECENT_SALE",
}
# Evidence that is SHOWN but never scored as current distress, whatever a
# tenant's weights say: it is history or unverified, by definition.
NEVER_SCORED = ("CODE_HISTORY", "TAX_TERMS_UNVERIFIED")
DERIVED_RULE_VERSION = "derive/v3"
DERIVED_LIMITATIONS = {
    "ABSENTEE_OWNER": "Mailing address differs from the property. It does not prove the house is vacant "
                      "or rented; a PO box never counts.",
    "OUT_OF_STATE_OWNER": "Mailing state differs from the property state.",
    "HIGH_EQUITY": "ESTIMATED from a reported value and a reported mortgage. Never computed when the "
                   "mortgage is unknown.",
    "FREE_AND_CLEAR": "Only when a source reports a zero mortgage. Never inferred from a missing mortgage.",
    "LONG_OWNERSHIP": "From the last recorded DEED TRANSFER date. A transfer is not necessarily a sale, "
                      "and Texas deed records carry no price.",
    "RECENT_DEED_TRANSFER": "A deed transfer inside the last year - often a recent purchase (possibly by an "
                            "investor), an inheritance or a refinance-related transfer. Counts against the property.",
}
DERIVED_SOURCE = "evosense_derived"


def freshness(sig, when: Optional[datetime] = None) -> str:
    when = when or C.now()
    if sig.stale_at and when >= sig.stale_at:
        return STALE
    policy = CATALOG.get(sig.signal_type, {"current_days": 180, "stale_days": 365})
    base = sig.effective_at or sig.observed_at or when
    age = (when - base).days
    if age <= policy["current_days"]:
        return CURRENT
    if age <= policy["stale_days"]:
        return AGING
    return STALE


def stack(signals, when: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Group active signals by type: best evidence, all sources, freshness."""
    when = when or C.now()
    groups: Dict[str, List] = defaultdict(list)
    for s in signals:
        if s.active:
            groups[s.signal_type].append(s)
    rank = {CURRENT: 2, AGING: 1, STALE: 0}
    out = []
    for stype, items in groups.items():
        items = sorted(items, key=lambda s: (rank[freshness(s, when)], s.confidence or 0,
                                             s.observed_at or when), reverse=True)
        best = items[0]
        fr = freshness(best, when)
        out.append({
            "signal_type": stype,
            "label": CATALOG.get(stype, {}).get("label", stype.replace("_", " ").title()),
            "freshness": fr,
            "confidence": best.confidence,
            "value": best.normalized_value,
            "sources": sorted({s.source for s in items}),
            "independent_sources": len({s.source for s in items if s.source != DERIVED_SOURCE}),
            "observed_at": best.observed_at.isoformat() + "Z" if best.observed_at else None,
            "derived": best.source == DERIVED_SOURCE,
            "family": FAMILY.get(stype, "OTHER"),
            "scored": stype not in NEVER_SCORED,
            # the SOURCE's own date for the evidence, as a plain date - never
            # shifted by a time zone - and what that date is
            "evidence_date": _date_str(getattr(best, "effective_at", None)),
            "evidence_basis": getattr(best, "evidence_basis", None),
            "case_status": getattr(best, "case_status", None),
            "record_sources": sorted({record_source(s) for s in items}),
            "evidence": [{
                "id": s.id, "source": s.source, "connector": s.connector_kind,
                "source_reference": s.source_reference,
                "observed_at": s.observed_at.isoformat() + "Z" if s.observed_at else None,
                "evidence_date": _date_str(getattr(s, "effective_at", None)),
                "evidence_basis": getattr(s, "evidence_basis", None),
                "case_status": getattr(s, "case_status", None),
                "rule_version": getattr(s, "rule_version", None),
                "freshness": freshness(s, when), "confidence": s.confidence,
                "value": s.normalized_value, "raw": s.raw_value,
                "provenance": C.jload(s.provenance, None), "cost_cents": s.cost_cents,
            } for s in items],
        })
    order = {k: i for i, k in enumerate(CATALOG)}
    out.sort(key=lambda g: (rank[g["freshness"]] * -1, order.get(g["signal_type"], 99)))
    return out


def _date_str(dt) -> Optional[str]:
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%d") if hasattr(dt, "strftime") else str(dt)[:10]


def record_source(sig) -> str:
    """Which SOURCE a piece of evidence ultimately rests on. A derived signal
    rests on the record that supplied its facts (the assessor / tax record
    naming the owner), so absentee + long ownership read from ONE county row
    count as one source, not two independent ones."""
    if sig.source != DERIVED_SOURCE:
        return sig.source
    prov = C.jload(sig.provenance, {}) if isinstance(sig.provenance, str) else (sig.provenance or {})
    return (prov or {}).get("record_source") or DERIVED_SOURCE


def upsert(db, prop, signal_type: str, *, source: str, connector_kind: str = None,
           source_reference: str = None, observation_id: str = None,
           observed_at: datetime = None, effective_at: datetime = None,
           confidence: int = None, strength: int = None, raw_value: Any = None,
           normalized_value: Any = None, provenance: Dict = None, cost_cents: int = 0,
           evidence_basis: str = None, case_status: str = None, rule_version: str = None,
           user=None):
    """Create or refresh one signal. The same source re-reporting the same
    signal refreshes it; a different source adds independent evidence."""
    from app.models.evosense_models import EvoSenseSignal
    if signal_type not in CATALOG:
        raise ValueError("Unknown signal type %s" % signal_type)
    q = db.query(EvoSenseSignal).filter(
        EvoSenseSignal.organization_id == prop.organization_id,
        EvoSenseSignal.property_id == prop.id,
        EvoSenseSignal.signal_type == signal_type,
        EvoSenseSignal.source == source)
    if source_reference:
        q = q.filter(EvoSenseSignal.source_reference == source_reference)
    row = q.first()
    if observed_at is not None:
        obs = observed_at
    elif row is not None and row.active and row.normalized_value == (
            None if normalized_value is None else str(normalized_value)[:200]):
        obs = row.observed_at          # same evidence re-derived: it is not newer evidence
    else:
        obs = C.now()
    if row is None:
        row = EvoSenseSignal(organization_id=prop.organization_id, property_id=prop.id,
                             signal_type=signal_type, source=source)
        db.add(row)
    row.connector_kind = connector_kind
    row.source_reference = source_reference
    row.observation_id = observation_id or row.observation_id
    row.observed_at = obs
    row.effective_at = effective_at
    row.confidence = confidence
    row.strength = strength
    row.raw_value = None if raw_value is None else str(raw_value)[:500]
    row.normalized_value = None if normalized_value is None else str(normalized_value)[:200]
    row.provenance = C.jdump(provenance)
    row.cost_cents = cost_cents or 0
    row.evidence_basis = evidence_basis
    row.case_status = case_status
    row.rule_version = rule_version or (DERIVED_RULE_VERSION if source == DERIVED_SOURCE else row.rule_version)
    row.retracted_at = row.retracted_reason = None
    row.active = True
    row.created_by_id = getattr(user, "id", None) or row.created_by_id
    return row


US_STATES = frozenset((
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND "
    "OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY PR GU VI").split())
_PO_BOX = re.compile(r"^(P\s*O\s*BOX|POST OFFICE BOX|BOX)\b")


def derive_specs(prop, owner=None, *, owner_source: Optional[str] = None,
                 when: Optional[datetime] = None) -> Dict[str, Any]:
    """PURE derivation (derive/v3): from facts to signal specs, no database.
    Returns {"make": {type: spec}, "retract": [types], "unknown": {type: why}}.
    The live engine (`derive`) and the reprocessor call this same function,
    so a dry run shows exactly what the engine would write."""
    from app.services.evosense.identity import normalize_street, same_address
    when = when or C.now()
    make: Dict[str, Dict[str, Any]] = {}
    unknown: Dict[str, str] = {}
    base_prov = {"rule_version": DERIVED_RULE_VERSION, "record_source": owner_source}

    if owner is not None and owner.mailing_street:
        mail_street = normalize_street(owner.mailing_street)[0]
        prov = dict(base_prov, **{
            "owner_mailing": ", ".join(x for x in (owner.mailing_street, owner.mailing_city,
                                                   owner.mailing_state, owner.mailing_zip) if x),
            "property": ", ".join(x for x in (prop.street_address, prop.city, prop.zip_code) if x),
            "rule": "mailing address differs from property",
            "limitations": DERIVED_LIMITATIONS["ABSENTEE_OWNER"]})
        same = same_address(owner.mailing_street, prop.street_address, owner.mailing_zip, prop.zip_code)
        if _PO_BOX.match(mail_street or ""):
            unknown["ABSENTEE_OWNER"] = "The owner's mailing address is a PO box; it says nothing about residence."
        elif getattr(prop, "occupancy", None) == "owner_occupied":
            unknown["ABSENTEE_OWNER"] = ("A homestead exemption is on file (the owner's declared residence); "
                                         "a different mailing address does not make the owner absentee.")
        elif same is None:
            unknown["ABSENTEE_OWNER"] = "The mailing and property addresses cannot be compared reliably."
        elif same is False:
            make["ABSENTEE_OWNER"] = {"confidence": 80, "value": "mailing address differs", "provenance": prov}
        mstate = (owner.mailing_state or "").strip().upper()
        if mstate in US_STATES and prop.state and mstate != prop.state.upper():
            make["OUT_OF_STATE_OWNER"] = {
                "confidence": 85, "value": "mailing state %s" % mstate,
                "provenance": dict(prov, rule="mailing state differs from property state",
                                   limitations=DERIVED_LIMITATIONS["OUT_OF_STATE_OWNER"])}
        elif mstate and mstate not in US_STATES:
            unknown["OUT_OF_STATE_OWNER"] = "The mailing state '%s' is not a recognised U.S. state." % mstate
    else:
        unknown["ABSENTEE_OWNER"] = "No owner mailing address is known."

    if prop.equity_pct is not None and prop.equity_pct >= 50:
        make["HIGH_EQUITY"] = {
            "confidence": 70 if prop.equity_basis == "computed" else 75,
            "value": "%s%% (ESTIMATED)" % prop.equity_pct,
            "provenance": dict(base_prov, equity_basis=prop.equity_basis,
                               value_source=prop.estimated_value_source,
                               mortgage_source=prop.mortgage_source, truth="ESTIMATED",
                               limitations=DERIVED_LIMITATIONS["HIGH_EQUITY"])}
    if prop.mortgage_balance == 0 and prop.mortgage_source and prop.equity_pct is not None:
        make["FREE_AND_CLEAR"] = {
            "confidence": 70, "value": "source reports a $0 mortgage",
            "provenance": dict(base_prov, mortgage_source=prop.mortgage_source,
                               limitations=DERIVED_LIMITATIONS["FREE_AND_CLEAR"])}

    deed = getattr(prop, "last_deed_transfer_date", None)
    if deed is not None:
        deed_dt = datetime(deed.year, deed.month, deed.day)
        years = max(0, int((when - deed_dt).days // 365.25))
        dprov = dict(base_prov, deed_transfer_date=deed.strftime("%Y-%m-%d"))
        if years >= 10:
            make["LONG_OWNERSHIP"] = {
                "confidence": 85, "value": "%s years (deed transfer %s)" % (years, deed.strftime("%m/%d/%Y")),
                "effective_at": deed_dt, "evidence_basis": "deed transfer date",
                "provenance": dict(dprov, limitations=DERIVED_LIMITATIONS["LONG_OWNERSHIP"])}
        if (when - deed_dt).days <= 365:
            make["RECENT_DEED_TRANSFER"] = {
                "confidence": 85, "value": "deed transfer %s (price not public)" % deed.strftime("%m/%d/%Y"),
                "effective_at": deed_dt, "evidence_basis": "deed transfer date",
                "provenance": dict(dprov, limitations=DERIVED_LIMITATIONS["RECENT_DEED_TRANSFER"])}
    elif prop.ownership_years is not None and getattr(prop, "last_sale_date", None) is not None:
        unknown["LONG_OWNERSHIP"] = "Only a legacy date is on file; re-derive to read the deed transfer date."

    derivable = ("ABSENTEE_OWNER", "OUT_OF_STATE_OWNER", "HIGH_EQUITY", "FREE_AND_CLEAR", "LONG_OWNERSHIP",
                 "RECENT_DEED_TRANSFER")
    return {"make": make, "retract": [t for t in derivable if t not in make], "unknown": unknown}


def derive(db, prop, owner=None) -> List[str]:
    """Signals computed from facts, with the facts' sources as provenance.

    Derived signals are only as good as the facts under them, so their
    confidence is the lowest of their inputs, and they carry those inputs."""
    from app.models.evosense_models import EvoSenseOwnership, EvoSenseSignal
    owner_source = None
    if owner is not None:
        link = (db.query(EvoSenseOwnership)
                .filter(EvoSenseOwnership.organization_id == prop.organization_id,
                        EvoSenseOwnership.property_id == prop.id,
                        EvoSenseOwnership.owner_id == owner.id,
                        EvoSenseOwnership.is_current.is_(True)).first())
        owner_source = link.source if link is not None else None
    spec = derive_specs(prop, owner, owner_source=owner_source)
    for stype in spec["retract"]:
        for s in (db.query(EvoSenseSignal)
                  .filter(EvoSenseSignal.organization_id == prop.organization_id,
                          EvoSenseSignal.property_id == prop.id,
                          EvoSenseSignal.signal_type == stype,
                          EvoSenseSignal.source == DERIVED_SOURCE,
                          EvoSenseSignal.active.is_(True)).all()):
            s.active = False
            s.retracted_at = C.now()
            s.retracted_reason = (spec["unknown"].get(stype) or "no longer supported by the facts") + \
                " (%s)" % DERIVED_RULE_VERSION
    for stype, m in spec["make"].items():
        upsert(db, prop, stype, source=DERIVED_SOURCE, confidence=m["confidence"],
               normalized_value=m["value"], provenance=m["provenance"],
               effective_at=m.get("effective_at"), evidence_basis=m.get("evidence_basis"),
               rule_version=DERIVED_RULE_VERSION)
    return list(spec["make"])
