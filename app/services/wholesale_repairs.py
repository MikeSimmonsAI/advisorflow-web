"""REPAIRS - what we actually know about the cost to fix the house.

    UNKNOWN              nothing credible yet                 (the default)
    SELLER_REPORTED      the seller said so - their account, often no number
    MANUAL_ESTIMATE      a person on the team estimated it
    INSPECTION_ESTIMATE  from an inspection / walkthrough / contractor bid
    SYSTEM_ESTIMATE      the platform's rough band from stated condition x sq ft
    VERIFIED             confirmed against a primary document by a person

Every estimate is kept (append-only), with who or what supplied it, when, the
range, the confidence and the notes. The deal carries the CURRENT one. A
seller's own account never becomes an inspection by being edited.

Nothing here assumes $0. "No repair number" is UNKNOWN, and the MAO gate
(mao_gate) refuses to calculate an offer on UNKNOWN when the workspace's
formula requires repairs.
"""
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from app.models.wholesale_models import WholesaleRepairEstimate

UNKNOWN = "UNKNOWN"
SELLER_REPORTED = "SELLER_REPORTED"
MANUAL_ESTIMATE = "MANUAL_ESTIMATE"
INSPECTION_ESTIMATE = "INSPECTION_ESTIMATE"
SYSTEM_ESTIMATE = "SYSTEM_ESTIMATE"
VERIFIED = "VERIFIED"
STATUSES = (UNKNOWN, SELLER_REPORTED, MANUAL_ESTIMATE, INSPECTION_ESTIMATE, SYSTEM_ESTIMATE,
            VERIFIED)
LABELS = {UNKNOWN: "Unknown", SELLER_REPORTED: "Seller reported", MANUAL_ESTIMATE: "Manual estimate",
          INSPECTION_ESTIMATE: "Inspection estimate", SYSTEM_ESTIMATE: "System estimate",
          VERIFIED: "Verified"}
# The legacy deal vocabulary (VALUE_SOURCES) mapped onto repair status, for
# deals whose estimate predates this module.
_LEGACY = {"manual": MANUAL_ESTIMATE, "verified": VERIFIED, "estimated": SYSTEM_ESTIMATE,
           "imported": MANUAL_ESTIMATE}
REPAIR_PER_SQFT = {"excellent": 0, "good": 5, "fair": 12, "poor": 25, "distressed": 45}


def _d(v) -> Optional[Decimal]:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except Exception:  # noqa: BLE001
        return None


def status_of(deal) -> str:
    s = getattr(deal, "repair_status", None)
    if s in STATUSES:
        return s
    if getattr(deal, "repair_estimate", None) is not None:
        return _LEGACY.get(getattr(deal, "repair_estimate_source", None) or "manual", MANUAL_ESTIMATE)
    return UNKNOWN


def history(db, org_id: str, deal_id: str) -> List[WholesaleRepairEstimate]:
    return (db.query(WholesaleRepairEstimate)
            .filter(WholesaleRepairEstimate.organization_id == org_id,
                    WholesaleRepairEstimate.deal_id == deal_id)
            .order_by(WholesaleRepairEstimate.created_at.asc()).all())


def record(db, org_id: str, deal, *, status: str, amount=None, low=None, high=None,
           source: Optional[str] = None, user=None, supplied_by_label: Optional[str] = None,
           confidence: Optional[str] = None, notes: Optional[str] = None,
           provenance: Optional[Dict[str, Any]] = None, make_current: bool = True
           ) -> WholesaleRepairEstimate:
    """Append one estimate; optionally make it the deal's current one.
    Raises ValueError with a sentence on bad input."""
    if status not in STATUSES or status == UNKNOWN:
        raise ValueError("Repair status must be one of: %s." % ", ".join(STATUSES[1:]))
    a, lo, hi = _d(amount), _d(low), _d(high)
    for v in (a, lo, hi):
        if v is not None and v < 0:
            raise ValueError("Repair amounts cannot be negative.")
    if lo is not None and hi is not None and lo > hi:
        raise ValueError("The low end of the range is above the high end.")
    if a is None and lo is not None and hi is not None:
        a = (lo + hi) / 2
    if status in (MANUAL_ESTIMATE, INSPECTION_ESTIMATE, VERIFIED, SYSTEM_ESTIMATE) and a is None:
        raise ValueError("An estimate needs an amount or a range.")
    if confidence is not None and confidence not in ("high", "medium", "low"):
        raise ValueError("Confidence must be high, medium or low.")
    row = WholesaleRepairEstimate(
        organization_id=org_id, deal_id=deal.id, status=status, amount=a, low=lo, high=hi,
        source=(source or "")[:120] or None, supplied_by_id=getattr(user, "id", None),
        supplied_by_label=supplied_by_label or getattr(user, "full_name", None),
        confidence=confidence, notes=(notes or "")[:2000] or None,
        provenance=json.dumps(provenance, default=str) if provenance else None,
        is_test=bool(getattr(deal, "is_test", False)))
    db.add(row)
    if make_current:
        for old in history(db, org_id, deal.id):
            if old.id != row.id:
                old.superseded = True
        deal.repair_status = status
        deal.repair_estimate = a
        deal.repair_low, deal.repair_high = lo, hi
        # the legacy source column keeps meaning what it meant
        deal.repair_estimate_source = {VERIFIED: "verified", SYSTEM_ESTIMATE: "estimated"}.get(status, "manual")
        if notes:
            deal.repair_notes = notes
    db.flush()
    return row


def system_estimate(condition: Optional[str], square_feet) -> Optional[Dict[str, Any]]:
    """The platform's rough band from a STATED condition x living area. A
    SYSTEM ESTIMATE, labelled as such; None when either input is missing."""
    rate = REPAIR_PER_SQFT.get((condition or "").strip().lower())
    sq = _d(square_feet)
    if rate is None or not sq:
        return None
    mid = Decimal(rate) * sq
    return {"amount": mid, "low": mid * Decimal("0.7"), "high": mid * Decimal("1.4"),
            "basis": "%s condition x %s sq ft at $%s/sq ft" % (condition, int(sq), rate)}


def view(db, org_id: str, deal) -> Dict[str, Any]:
    st = status_of(deal)
    rows = history(db, org_id, deal.id)
    return {"status": st, "label": LABELS[st],
            "amount": float(deal.repair_estimate) if deal.repair_estimate is not None else None,
            "low": float(deal.repair_low) if getattr(deal, "repair_low", None) is not None else None,
            "high": float(deal.repair_high) if getattr(deal, "repair_high", None) is not None else None,
            "notes": deal.repair_notes,
            "history": [{"id": r.id, "status": r.status, "label": LABELS.get(r.status, r.status),
                         "amount": float(r.amount) if r.amount is not None else None,
                         "low": float(r.low) if r.low is not None else None,
                         "high": float(r.high) if r.high is not None else None,
                         "source": r.source, "supplied_by": r.supplied_by_label,
                         "confidence": r.confidence, "notes": r.notes,
                         "provenance": json.loads(r.provenance) if r.provenance else None,
                         "current": not r.superseded,
                         "at": r.created_at.isoformat() + "Z" if r.created_at else None}
                        for r in rows]}
