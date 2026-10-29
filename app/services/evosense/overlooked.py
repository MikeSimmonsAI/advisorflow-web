"""Read-only Opportunity Radar for tenant-scoped, evidence-backed property discovery.

A private professional-app capability, NOT the public bookaboost.live website.
Reuse the EvoSense property class, cached opportunity score and supporting signals.
No new scoring formula, external lookups, DB writes, or outbound communication.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.evosense_models import EvoSenseProperty, EvoSenseSignal
from app.services.evosense import property_class as PCL
from app.services.evosense import scoring as SC
from app.services.evosense import signals as SIG

MARKET_CLASSES = {
    "residential": ("residential", "multifamily"),
    "land": ("land", "agricultural"),
    "commercial": ("commercial", "industrial", "mixed_use", "specialty"),
}
MARKETS = ("all", "residential", "wholesale", "land", "commercial")
# Only existing facts; "overlooked" is a research workflow, not a claim that
# a property is for sale, off-market, distressed or available for acquisition.
FRESHNESS_ORDER = {SIG.CURRENT: 2, SIG.AGING: 1, SIG.STALE: 0}


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.isoformat() + "Z"


def _evidence(sig_rows: List[EvoSenseSignal], prop: EvoSenseProperty,
              *, now: datetime) -> List[Dict[str, Any]]:
    """Only fresh, positive, type-applicable signals can be called reasons.

    The underlying engine already prohibits household-specific vacancy/absentee
    signals from being scored as evidence of agricultural or land distress.
    Preserve that rule in the human-readable explanation as well.
    """
    best: Dict[str, tuple] = {}
    for s in sig_rows:
        if not s.active:
            continue
        kind = s.signal_type
        policy = SIG.CATALOG.get(kind)
        if not policy or policy.get("points", 0) <= 0 or kind in SIG.NEVER_SCORED:
            continue
        if SC.type_excluded(kind, prop.property_category):
            continue
        age = SIG.freshness(s, now)
        if age == SIG.STALE:
            continue
        rank = (FRESHNESS_ORDER[age], s.confidence if s.confidence is not None else -1,
                s.observed_at or datetime.min)
        if kind not in best or rank > best[kind][0]:
            best[kind] = (rank, s, age)
    results = []
    for kind, (_, s, freshness) in best.items():
        results.append({
            "type": kind, "label": SIG.CATALOG[kind]["label"],
            "source": s.source, "observed_at": _iso(s.observed_at),
            "effective_at": _iso(s.effective_at),
            "freshness": freshness, "confidence": s.confidence,
            "basis": s.evidence_basis,
        })
    return sorted(results, key=lambda v: (v["freshness"] != SIG.CURRENT, v["type"]))


def _human(code: Optional[str]) -> Optional[str]:
    if not code:
        return None
    return str(code).replace("_", " ").strip().capitalize()


def _missing(p: EvoSenseProperty, reasons: List[Dict[str, Any]]) -> List[str]:
    """What a professional still has to establish before relying on this record.
    Only facts the record genuinely lacks; nothing is inferred."""
    out = []
    if not p.street_address:
        out.append("Verified street address")
    if not p.parcel_apn:
        out.append("Parcel number (APN)")
    if not p.county:
        out.append("County")
    if p.property_category in (None, "unknown") or p.property_class_conflict:
        out.append("Confirmed property type")
    if p.opportunity_score is None:
        out.append("Opportunity score (not yet evaluated)")
    if p.appraisal_value is None and p.estimated_value is None:
        out.append("Assessed or estimated value")
    if p.property_category in ("residential", "multifamily") and p.year_built is None:
        out.append("Year built")
    if not reasons:
        out.append("Current supporting evidence")
    return out


def _next_step(p: EvoSenseProperty, reasons: List[Dict[str, Any]]) -> Dict[str, Optional[str]]:
    """The engine's own recommendation when it has one; otherwise the honest
    default for an unconfirmed record. Never an outreach or a purchase."""
    if p.promoted_deal_id:
        return {"code": "open_deal", "label": "Continue in the existing deal", "detail": None}
    code = p.next_best_action or p.next_action
    if code:
        return {"code": code, "label": _human(code), "detail": p.next_action_detail}
    if not reasons:
        return {"code": "research", "label": "Confirm the source records in property research", "detail": None}
    return {"code": "review", "label": "Review the evidence and decide whether to pursue", "detail": None}


def _facts(p: EvoSenseProperty) -> Dict[str, Any]:
    value = p.appraisal_value if p.appraisal_value is not None else p.estimated_value
    value_basis = (("assessed %s" % p.appraisal_year if p.appraisal_year else "assessed")
                   if p.appraisal_value is not None else
                   ("estimate" if p.estimated_value is not None else None))
    return {
        "zip": p.zip_code, "year_built": p.year_built, "square_feet": p.square_feet,
        "value": value, "value_basis": value_basis,
        "value_source": p.appraisal_source if p.appraisal_value is not None else p.estimated_value_source,
        "occupancy": p.occupancy if p.occupancy and p.occupancy != "unknown" else None,
        "occupancy_source": p.occupancy_source if p.occupancy and p.occupancy != "unknown" else None,
        "ownership_years": p.ownership_years,
    }


def coverage(db: Session, org_id: str, *, market: str = "all", state: Optional[str] = None,
             include_test: bool = False) -> Dict[str, Any]:
    """Where this workspace ACTUALLY has records (never a claim of national data):
    record counts per state, and per county inside the chosen state."""
    q = db.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == org_id,
                                          EvoSenseProperty.archived_at.is_(None))
    if not include_test:
        q = q.filter(EvoSenseProperty.is_test.is_(False))
    if market in MARKET_CLASSES:
        q = q.filter(EvoSenseProperty.property_category.in_(MARKET_CLASSES[market]))
    st = func.upper(EvoSenseProperty.state)
    states = [{"state": s, "count": int(n)} for s, n in
              q.with_entities(st, func.count(EvoSenseProperty.id)).filter(EvoSenseProperty.state.isnot(None))
              .group_by(st).order_by(st).all() if s]
    counties = []
    if state:
        counties = [{"county": c, "count": int(n)} for c, n in
                    q.filter(st == state.upper(), EvoSenseProperty.county.isnot(None))
                    .with_entities(EvoSenseProperty.county, func.count(EvoSenseProperty.id))
                    .group_by(EvoSenseProperty.county).order_by(EvoSenseProperty.county).all() if c]
    return {"states": states, "counties": counties}


def search(db: Session, org_id: str, *, market: str = "all",
           state: Optional[str] = None, county: Optional[str] = None,
           min_score: Optional[int] = None,
           limit: int = 25, offset: int = 0,
           include_test: bool = False) -> Dict[str, Any]:
    """Bounded, stable and strictly tenant-scoped private read model.

    Unknown classifications stay visible in 'all' with review required.
    No work is initiated. A missing score is NULL, not an invented zero.
    """
    if not org_id:
        raise ValueError("An organization scope is required.")
    if market not in MARKETS:
        raise ValueError("Unsupported market.")
    if min_score is not None and not 0 <= min_score <= 100:
        raise ValueError("min_score must be between 0 and 100.")
    if not 1 <= limit <= 50 or not 0 <= offset:
        raise ValueError("Out-of-bounds pagination.")
    if state is not None:
        state = state.strip().upper()
        if len(state) != 2 or not state.isalpha():
            raise ValueError("State must be a two-letter code.")
    county = county.strip() if county else None

    base = db.query(EvoSenseProperty).filter(
        EvoSenseProperty.organization_id == org_id,
        EvoSenseProperty.archived_at.is_(None),
    )
    if not include_test:
        base = base.filter(EvoSenseProperty.is_test.is_(False))
    if state:
        base = base.filter(func.upper(EvoSenseProperty.state) == state)
    if county:
        base = base.filter(func.lower(EvoSenseProperty.county) == county.lower().removesuffix(" county"))
    if market in MARKET_CLASSES:
        base = base.filter(EvoSenseProperty.property_category.in_(MARKET_CLASSES[market]))
    if market == "wholesale":
        # Wholesale is a deal STRATEGY, not a parcel land-use classification.
        # Require an existing score and supporting evidence in the current
        # engine; never label any parcel "distressed" just for being land.
        base = base.filter(EvoSenseProperty.opportunity_score.isnot(None),
                           EvoSenseProperty.opportunity_score > 0)
    if min_score is not None:
        base = base.filter(EvoSenseProperty.opportunity_score >= min_score)

    total = int(base.with_entities(func.count(EvoSenseProperty.id)).scalar() or 0)
    properties = (base.order_by(EvoSenseProperty.opportunity_score.desc().nullslast(),
                                EvoSenseProperty.id.asc())
                  .offset(offset).limit(limit).all())
    ids = [p.id for p in properties]
    signals = defaultdict(list)
    if ids:
        for s in (db.query(EvoSenseSignal)
                  .filter(EvoSenseSignal.organization_id == org_id,
                          EvoSenseSignal.property_id.in_(ids),
                          EvoSenseSignal.active.is_(True)).all()):
            signals[s.property_id].append(s)

    now = datetime.utcnow()
    items = []
    for p in properties:
        reasons = _evidence(signals[p.id], p, now=now)
        item = {
            "id": p.id,
            "address": p.street_address,
            "city": p.city, "state": p.state, "county": p.county,
            "parcel_apn": p.parcel_apn,
            "property_category": p.property_category or "unknown",
            "property_subtype": p.property_subtype,
            "property_class_source": p.property_class_source,
            "classification_review_required": (
                p.property_category in (None, "unknown") or
                bool(p.property_class_conflict) or p.identity_status == "review"),
            "existing_opportunity_score": p.opportunity_score,
            "score_name": "Property opportunity score",
            "data_confidence": p.data_confidence,
            "last_observed_at": _iso(p.last_observed_at),
            "why_review": reasons,
            "evidence_status": "supported" if reasons else "needs_research",
            "promoted_deal_id": p.promoted_deal_id,
            "next_action": p.next_action,
            "recommended_next_action": _next_step(p, reasons),
            "missing_information": _missing(p, reasons),
            "facts": _facts(p),
        }
        if market == "wholesale" and not reasons:
            # Do not pretend that a historical/cached score is independently
            # supported by fresh evidence in this new Radar view.
            item["evidence_status"] = "needs_research"
        items.append(item)

    return {
        "total": total, "offset": offset, "limit": limit,
        "market": market, "state": state, "county": county,
        "score_definition": ("The existing property opportunity score used across Acquisition; "
                             "Radar adds no score of its own."),
        "coverage": "Only records actually present in this authorized workspace.",
        "markets": list(MARKETS),
        "available": coverage(db, org_id, market=market, state=state, include_test=include_test),
        "items": items,
    }
