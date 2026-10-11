"""Get a value estimate for an EvoSense property: RentCast inside its hard free
budget when connected, and - always, for free - a clearly labelled figure from
the county's own tax values in the same ZIP. Neither is a sale price."""
from __future__ import annotations

import json
from statistics import median
from typing import Any, Dict, Optional

from app.services.evosense import common as C
from app.services.evosense.sources import base as SB
from app.services.evosense.sources import rentcast as RC

AUTO_MIN_SCORE = 65
AUTO_PER_HUNT = 5


def value_property(db, prop, *, user=None, auto: bool = False) -> Dict[str, Any]:
    if not RC.configured():
        return {"ok": False, "reason": "RentCast is not connected (RENTCAST_API_KEY is not set)."}
    if RC.fresh(prop):
        return {"ok": True, "cached": True, "detail": C.jload(prop.valuation_detail, {})}
    if auto and (prop.opportunity_score or 0) < AUTO_MIN_SCORE:
        return {"ok": False, "reason": "Automatic value estimates start at Property Opportunity %s." % AUTO_MIN_SCORE}
    if not (prop.street_address and prop.city and prop.state):
        return {"ok": False, "reason": "The property needs a street address, city and state."}
    if not RC.reserve(db):
        u = RC.usage(db)
        return {"ok": False, "reason": "This month's free RentCast budget is used up (%s of %s). Nothing was "
                                       "sent, so nothing can be billed." % (u["used"], u["limit"])}
    address = "%s, %s, %s %s" % (prop.street_address, prop.city, prop.state, prop.zip_code or "")
    try:
        body = RC.fetch(address.strip(), prop_type=prop.property_type, beds=prop.bedrooms,
                        baths=prop.bathrooms, sqft=prop.square_feet)
    except SB.SourceError as exc:
        C.log_event(db, prop.organization_id, "valuation.failed", property_id=prop.id, user=user,
                    is_test=bool(prop.is_test), summary="RentCast value estimate failed: %s" % exc.code)
        db.commit()
        return {"ok": False, "reason": "RentCast did not answer (%s)." % exc.code}
    detail = RC.to_detail(body)
    prop.valuation_detail = C.jdump(detail)
    if detail.get("estimate"):
        prop.estimated_value = int(detail["estimate"])
        prop.estimated_value_source = "rentcast (automated estimate from nearby listings)"
        prop.estimated_value_at = C.now()
    C.log_event(db, prop.organization_id, "valuation.estimated", property_id=prop.id, user=user,
                actor_type=C.ACTOR_USER if user else C.ACTOR_AUTOMATION, is_test=bool(prop.is_test),
                summary="Value estimate %s (RentCast, %s listing comparables)" % (
                    detail.get("estimate"), len(detail.get("comparables") or [])))
    db.commit()
    return {"ok": True, "cached": False, "detail": detail}


def neighborhood_tax_estimate(db, prop) -> Optional[Dict[str, Any]]:
    """A FREE, always-available figure: the median appraisal-district tax value
    per square foot of houses in the same ZIP that EvoSense holds records for,
    times this house's living area. An assessment-based number - labelled so."""
    from app.models.evosense_models import EvoSenseProperty
    if not (prop.zip_code and prop.square_feet):
        return None
    rows = (db.query(EvoSenseProperty.appraisal_value, EvoSenseProperty.square_feet)
            .filter(EvoSenseProperty.organization_id == prop.organization_id,
                    EvoSenseProperty.zip_code == prop.zip_code,
                    EvoSenseProperty.id != prop.id,
                    EvoSenseProperty.appraisal_value.isnot(None),
                    EvoSenseProperty.square_feet > 300,
                    EvoSenseProperty.is_test.is_(bool(prop.is_test))).limit(2000).all())
    ppsf = [float(v) / float(sq) for v, sq in rows if v and sq]
    if len(ppsf) < 5:
        return None
    m = median(ppsf)
    return {"value": int(round(m * int(prop.square_feet), -2)), "per_sq_ft": round(m, 2), "sample": len(ppsf),
            "zip": prop.zip_code,
            "truth": "TAX-VALUE BASED - median appraisal-district tax value per sq ft of %s houses in ZIP %s "
                     "x this living area. An assessment, not a sale price or ARV." % (len(ppsf), prop.zip_code)}
