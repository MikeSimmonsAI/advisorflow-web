"""Property signals: evidence, freshness, derivation and stacking.

A signal is EVIDENCE that a property may be interesting. It is never truth
merely because a provider returned it. Every signal keeps its source, when it
was observed, how confident the source was, and it ages:

    CURRENT  -> AGING (counts half) -> STALE (counts nothing, stays visible)

History is never erased when a signal goes stale.
"""
from __future__ import annotations

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
            "evidence": [{
                "id": s.id, "source": s.source, "connector": s.connector_kind,
                "source_reference": s.source_reference,
                "observed_at": s.observed_at.isoformat() + "Z" if s.observed_at else None,
                "freshness": freshness(s, when), "confidence": s.confidence,
                "value": s.normalized_value, "raw": s.raw_value,
                "provenance": C.jload(s.provenance, None), "cost_cents": s.cost_cents,
            } for s in items],
        })
    order = {k: i for i, k in enumerate(CATALOG)}
    out.sort(key=lambda g: (rank[g["freshness"]] * -1, order.get(g["signal_type"], 99)))
    return out


def upsert(db, prop, signal_type: str, *, source: str, connector_kind: str = None,
           source_reference: str = None, observation_id: str = None,
           observed_at: datetime = None, effective_at: datetime = None,
           confidence: int = None, strength: int = None, raw_value: Any = None,
           normalized_value: Any = None, provenance: Dict = None, cost_cents: int = 0,
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
    row.active = True
    row.created_by_id = getattr(user, "id", None) or row.created_by_id
    return row


def derive(db, prop, owner=None) -> List[str]:
    """Signals computed from facts, with the facts' sources as provenance.

    Derived signals are only as good as the facts under them, so their
    confidence is the lowest of their inputs, and they carry those inputs."""
    made: List[str] = []
    from app.models.evosense_models import EvoSenseSignal

    def retract(stype):
        for s in (db.query(EvoSenseSignal)
                  .filter(EvoSenseSignal.organization_id == prop.organization_id,
                          EvoSenseSignal.property_id == prop.id,
                          EvoSenseSignal.signal_type == stype,
                          EvoSenseSignal.source == DERIVED_SOURCE).all()):
            s.active = False

    if owner is not None and owner.mailing_street:
        same_zip = (owner.mailing_zip or "")[:5] == (prop.zip_code or "")[:5]
        from app.services.evosense.identity import normalize_street
        same_street = normalize_street(owner.mailing_street)[0] == normalize_street(prop.street_address)[0]
        prov = {"owner_mailing": ", ".join(x for x in (owner.mailing_street, owner.mailing_city,
                                                        owner.mailing_state, owner.mailing_zip) if x),
                "property": prop.street_address, "rule": "mailing address differs from property"}
        if not (same_zip and same_street):
            upsert(db, prop, "ABSENTEE_OWNER", source=DERIVED_SOURCE, confidence=80,
                   normalized_value="mailing address differs", provenance=prov)
            made.append("ABSENTEE_OWNER")
        else:
            retract("ABSENTEE_OWNER")
        if owner.mailing_state and prop.state and owner.mailing_state.upper() != prop.state.upper():
            upsert(db, prop, "OUT_OF_STATE_OWNER", source=DERIVED_SOURCE, confidence=85,
                   normalized_value="mailing state %s" % owner.mailing_state.upper(),
                   provenance={**prov, "rule": "mailing state differs from property state"})
            made.append("OUT_OF_STATE_OWNER")
        else:
            retract("OUT_OF_STATE_OWNER")
    if prop.equity_pct is not None:
        if prop.equity_pct >= 50:
            upsert(db, prop, "HIGH_EQUITY", source=DERIVED_SOURCE,
                   confidence=70 if prop.equity_basis == "computed" else 75,
                   normalized_value="%s%%" % prop.equity_pct,
                   provenance={"equity_basis": prop.equity_basis,
                               "value_source": prop.estimated_value_source,
                               "mortgage_source": prop.mortgage_source})
            made.append("HIGH_EQUITY")
        else:
            retract("HIGH_EQUITY")
        if prop.mortgage_balance == 0 and prop.mortgage_source:
            upsert(db, prop, "FREE_AND_CLEAR", source=DERIVED_SOURCE, confidence=70,
                   normalized_value="no mortgage reported",
                   provenance={"mortgage_source": prop.mortgage_source})
            made.append("FREE_AND_CLEAR")
    if prop.ownership_years is not None:
        if prop.ownership_years >= 10:
            upsert(db, prop, "LONG_OWNERSHIP", source=DERIVED_SOURCE, confidence=85,
                   normalized_value="%s years" % prop.ownership_years,
                   provenance={"last_sale_date": prop.last_sale_date.isoformat()
                               if prop.last_sale_date else None})
            made.append("LONG_OWNERSHIP")
        else:
            retract("LONG_OWNERSHIP")
    return made
