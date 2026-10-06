"""Physical campuses: one local phone number per campus, not per named entity.

A funeral home and the cemetery beside it are two LOCATIONS (separate names,
aliases, folders, reporting, campaigns, sign-offs) on one CAMPUS (one street
address, one local number). A text or call to the campus number is resolved
to the caller's own location when the caller is a known contact at any
location on that campus; otherwise it is kept for review at the campus.

scripts/sci_campuses.csv holds the SCI grouping, derived from the locations'
street addresses (research - to be confirmed with SCI before any address is
used in mail). The grouping is data; this module is generic.
"""
from __future__ import annotations

import csv
import os
from collections import OrderedDict
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.program_models import LocationProfile

_CSV = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
                    "scripts", "sci_campuses.csv")


def load_grouping(path: Optional[str] = None) -> List[Dict]:
    with open(path or _CSV, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def assign(db: Session, organization_id: str, rows: List[Dict]) -> Dict:
    """Set campus_key/label on each named location. Unknown names are reported,
    never created. Returns counts."""
    profs = {p.official_name: p for p in db.query(LocationProfile).filter(
        LocationProfile.organization_id == organization_id, LocationProfile.is_review_bucket.is_(False))}
    missing, set_ = [], 0
    for r in rows:
        p = profs.get(r["Location"])
        if p is None:
            missing.append(r["Location"])
            continue
        p.campus_key, p.campus_label = r["Campus"], r.get("Campus Label") or r["Campus"]
        set_ += 1
    db.flush()
    return {"locations_assigned": set_, "campuses": len({r["Campus"] for r in rows}), "unknown_locations": missing}


def campus_of(db: Session, organization_id: str, location_id: Optional[str]) -> Optional[str]:
    if not location_id:
        return None
    p = (db.query(LocationProfile).filter(LocationProfile.organization_id == organization_id,
                                          LocationProfile.location_id == location_id).first())
    return (p.campus_key or ("loc:%s" % p.location_id)) if p else None


def same_campus(db: Session, organization_id: str, a: Optional[str], b: Optional[str]) -> bool:
    if not a or not b:
        return False
    return a == b or campus_of(db, organization_id, a) == campus_of(db, organization_id, b)


def plan(db: Session, organization_id: str) -> List[Dict]:
    """One row per campus: the entities it serves, its contacts and its number."""
    from app.models.models import Lead
    from app.models.program_models import ProgramSourceRecord
    from app.models.telephony_models import PhoneNumber
    profs = (db.query(LocationProfile).filter(LocationProfile.organization_id == organization_id,
                                              LocationProfile.is_review_bucket.is_(False))
             .order_by(LocationProfile.official_name).all())
    numbers: Dict[str, List[str]] = {}
    for n in db.query(PhoneNumber).filter(PhoneNumber.organization_id == organization_id,
                                          PhoneNumber.is_active.is_(True)):
        if n.workspace_id:
            numbers.setdefault(n.workspace_id, []).append(n.e164)
    from app.services.programs import regional_pools
    pool_numbers: Dict[str, List[str]] = {}          # pool id -> active pool-number e164s
    for n in db.query(PhoneNumber).filter(PhoneNumber.organization_id == organization_id,
                                          PhoneNumber.is_active.is_(True)):
        pool = regional_pools.pool_for_phone_number(n)
        if pool and n.e164 not in pool_numbers.setdefault(pool["pool_id"], []):
            pool_numbers[pool["pool_id"]].append(n.e164)
    try:
        area_by_name = {r["Location"]: (r.get("Area Code") or "").strip() for r in load_grouping()}
    except OSError:
        area_by_name = {}
    counts: Dict[str, int] = {}
    for loc_id, lead_id in db.query(ProgramSourceRecord.location_id, ProgramSourceRecord.lead_id).filter(
            ProgramSourceRecord.organization_id == organization_id, ProgramSourceRecord.lead_id.isnot(None),
            ProgramSourceRecord.on_hold.is_(False)).distinct():
        counts[loc_id] = counts.get(loc_id, 0) + 1
    out: "OrderedDict[str, Dict]" = OrderedDict()
    for p in profs:
        key = p.campus_key or "loc:%s" % p.location_id
        row = out.setdefault(key, {"campus": key, "label": p.campus_label or p.official_name,
                                   "entities": [], "contacts": 0, "numbers": [],
                                   "area_code": None, "pool_id": None, "pool_label": None})
        row["entities"].append(p.official_name)
        pool = regional_pools.pool_for_area_code(area_by_name.get(p.official_name))
        if pool and row["pool_id"] is None:
            row["area_code"] = area_by_name[p.official_name]
            row["pool_id"], row["pool_label"] = pool["pool_id"], pool["label"]
        row["contacts"] += counts.get(p.location_id, 0)
        for e in numbers.get(p.location_id, []):
            if e not in row["numbers"]:
                row["numbers"].append(e)
    for row in out.values():
        row["pool_numbers"] = pool_numbers.get(row["pool_id"], []) if row["pool_id"] else []
        if row["numbers"]:
            row["number_status"] = "assigned"
        elif row["pool_numbers"]:
            row["number_status"] = "pooled"                 # sends from its regional pool number
        elif row["pool_id"]:
            row["number_status"] = "pool number not yet provisioned"
        else:
            row["number_status"] = "no verified area code"  # never silently falls to the 844 backup
    return list(out.values())
