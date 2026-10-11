"""A person's own building facts on an EvoSense property (beds, baths, living
area, year built) - kept apart from the county record, never overwritten by a
county refresh, and copied to the deal when the property is promoted.

The county value is not lost: it stays in the property's observations and is
remembered next to the person's entry ("County record" vs "Entered by you").
Clearing an entry puts the county value back.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import HTTPException

from app.services.evosense import common as C
from app.services.evosense.ingest import RANK_HUMAN, RANK_RECORDS

EDITABLE = {
    "bedrooms": (0, 20, "Bedrooms"),
    "bathrooms": (0, 20, "Bathrooms"),
    "square_feet": (100, 50000, "Square feet"),
    "year_built": (1800, datetime.utcnow().year + 1, "Year built"),
}


def entries(prop) -> Dict[str, Any]:
    return C.jload(getattr(prop, "user_facts", None), {}) or {}


def _clean(field: str, value) -> Optional[float]:
    lo, hi, label = EDITABLE[field]
    if value in (None, ""):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="%s must be a number." % label)
    if field in ("square_feet", "year_built"):
        if v != int(v):
            raise HTTPException(status_code=422, detail="%s must be a whole number." % label)
        v = int(v)
    elif field == "bathrooms" and (v * 2) != int(v * 2):
        raise HTTPException(status_code=422, detail="Bathrooms go in halves (2, 2.5, 3).")
    elif field == "bedrooms" and v != int(v):
        raise HTTPException(status_code=422, detail="Bedrooms must be a whole number.")
    if not (lo <= v <= hi):
        raise HTTPException(status_code=422, detail="%s must be between %s and %s." % (label, lo, hi))
    return v


def set_facts(db, prop, user, data: Dict[str, Any]) -> Dict[str, Any]:
    """`data` maps a field to a value, or to None to remove the person's entry."""
    ranks = C.jload(prop.fact_ranks, {}) or {}
    mine = entries(prop)
    changed = {}
    for field in [f for f in data if f in EDITABLE]:
        value = _clean(field, data[field])
        if value is None:
            if field in mine:
                setattr(prop, field, mine[field].get("county_value"))
                ranks[field] = RANK_RECORDS
                changed[field] = {"from": mine.pop(field).get("value"), "to": getattr(prop, field),
                                  "action": "back to the county record"}
            continue
        county = mine[field]["county_value"] if field in mine else (
            getattr(prop, field) if ranks.get(field, 0) < RANK_HUMAN else None)
        if county is not None:
            county = float(county) if field in ("bedrooms", "bathrooms") else int(county)
        before = getattr(prop, field)
        setattr(prop, field, value)
        ranks[field] = RANK_HUMAN
        mine[field] = {"value": value, "county_value": county, "by_id": getattr(user, "id", None),
                       "by_name": getattr(user, "full_name", None) or getattr(user, "email", None),
                       "at": C.now().isoformat() + "Z"}
        changed[field] = {"from": float(before) if before is not None and field in ("bedrooms", "bathrooms")
                          else before, "to": value, "action": "entered by you"}
    prop.fact_ranks = C.jdump(ranks)
    prop.user_facts = C.jdump(mine) if mine else None
    if changed:
        C.log_event(db, prop.organization_id, "property.facts_entered", property_id=prop.id, user=user,
                    actor_type=C.ACTOR_USER, is_test=bool(prop.is_test),
                    summary="Building facts entered: %s" % ", ".join(EDITABLE[f][2] for f in changed),
                    details={"changed": changed})
    return {"changed": changed, "entered_by_you": mine}


def note_county_refresh(prop, field: str, value) -> None:
    """A county refresh never replaces a person's entry; it only updates the
    county value remembered next to it."""
    mine = entries(prop)
    if field in mine and value not in (None, ""):
        mine[field]["county_value"] = float(value) if field in ("bedrooms", "bathrooms") else value
        prop.user_facts = C.jdump(mine)
