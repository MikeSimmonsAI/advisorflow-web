"""Property identity: one real-world property -> one canonical row per organization.

Match outcomes:
    EXACT      same APN in the same county, or the same normalized street +
               unit + ZIP5. Merged by deterministic policy.
    PROBABLE   same normalized street + unit + city/state, one side missing a
               ZIP, exactly one candidate, nothing that contradicts it
               (different APN / different ZIP). Merged, and recorded as probable.
    AMBIGUOUS  anything that could be the same property but might not be:
               several candidates, same house number and street with a unit on
               one side only, an APN that points one way and an address that
               points another. NEVER merged - it goes to identity review.
    NEW        nothing close.

Every lookup is scoped to one organization. There is no global property table
and no cross-tenant merge: two tenants discovering the same house each get
their own canonical row.

This is PROPERTY identity. Person/company identity (email, phone, surname) is
Universal Intake's matcher; see the convergence section of the Phase 7 report.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from app.models.evosense_models import EvoSenseProperty

EXACT = "exact"
PROBABLE = "probable"
AMBIGUOUS = "ambiguous"
NEW = "new"

_SUFFIX = {
    "ROAD": "RD", "STREET": "ST", "AVENUE": "AVE", "AV": "AVE", "DRIVE": "DR", "LANE": "LN",
    "BOULEVARD": "BLVD", "COURT": "CT", "CIRCLE": "CIR", "PLACE": "PL", "PARKWAY": "PKWY",
    "HIGHWAY": "HWY", "TRAIL": "TRL", "TERRACE": "TER", "WAY": "WAY", "SQUARE": "SQ",
    "EXPRESSWAY": "EXPY", "FREEWAY": "FWY", "CROSSING": "XING", "POINT": "PT",
    "COVE": "CV", "BEND": "BND", "RIDGE": "RDG", "HOLLOW": "HOLW", "LOOP": "LOOP",
}
_DIR = {"NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W", "NORTHEAST": "NE",
        "NORTHWEST": "NW", "SOUTHEAST": "SE", "SOUTHWEST": "SW"}
_UNIT = re.compile(r"\s(?:APT|APARTMENT|UNIT|STE|SUITE|#|BLDG|LOT)\s*#?\s*([A-Z0-9-]+)\s*$")


def normalize_street(street: Optional[str]) -> Tuple[str, Optional[str]]:
    """'1418 Cedar Springs Road, Apt 4' -> ('1418 CEDAR SPRINGS RD', '4')."""
    if not street:
        return "", None
    s = street.upper().split(",")[0]
    s = re.sub(r"[.]", "", s)
    s = re.sub(r"#\s*", " # ", s)
    s = re.sub(r"\s+", " ", s).strip()
    unit = None
    m = _UNIT.search(" " + s)
    if m:
        unit = m.group(1)
        s = (" " + s)[:m.start()].strip()
    toks = [_DIR.get(t, t) for t in s.split()]
    if toks:
        toks[-1] = _SUFFIX.get(toks[-1], toks[-1])
        if len(toks) >= 2 and toks[-1] in _DIR.values() and toks[-2] in _SUFFIX:
            toks[-2] = _SUFFIX[toks[-2]]
    return " ".join(toks), unit


_SUFFIX_ABBR = set(_SUFFIX.values())


def _split_suffix(norm: str) -> Tuple[str, Optional[str]]:
    """'2427 LILLIAN ST' -> ('2427 LILLIAN', 'ST'); '2427 LILLIAN' -> ('2427 LILLIAN', None).
    A trailing direction after the suffix ('... ST N') keeps both."""
    toks = norm.split()
    if len(toks) >= 3 and toks[-1] in _SUFFIX_ABBR:
        return " ".join(toks[:-1]), toks[-1]
    if len(toks) >= 4 and toks[-1] in _DIR.values() and toks[-2] in _SUFFIX_ABBR:
        return " ".join(toks[:-2] + [toks[-1]]), toks[-2]
    return norm, None


def same_address(street_a: Optional[str], street_b: Optional[str],
                 zip_a: Optional[str] = None, zip_b: Optional[str] = None) -> Optional[bool]:
    """Is mailing address A the same place as street address B?

        True    the same normalized street; or the same house number and
                street name where ONE side simply omits the suffix
                ("2427 LILLIAN" vs "2427 LILLIAN ST") and no ZIP disagrees
        False   a different house number, a different street name, a
                different suffix on both sides, or a different ZIP
        None    UNKNOWN - either side is missing or has no house number.
                UNKNOWN is never read as "different": it produces no signal.
    """
    a, ua = normalize_street(street_a)
    b, ub = normalize_street(street_b)
    if not a or not b:
        return None
    za = re.sub(r"\D", "", str(zip_a or ""))[:5]
    zb = re.sub(r"\D", "", str(zip_b or ""))[:5]
    zips_differ = bool(za and zb and za != zb)
    na, nb = a.split()[0], b.split()[0]
    if not re.match(r"^\d", na) or not re.match(r"^\d", nb):
        return None
    if (ua or None) != (ub or None) and ua and ub:
        return False
    if a == b:
        return not zips_differ
    if na != nb:
        return False
    core_a, suf_a = _split_suffix(a)
    core_b, suf_b = _split_suffix(b)
    if core_a == core_b and (suf_a is None or suf_b is None):
        return not zips_differ
    return False


def normalize_apn(apn: Optional[str]) -> Optional[str]:
    if not apn:
        return None
    k = re.sub(r"[^0-9A-Z]", "", apn.upper())
    return k or None


def county_key(county: Optional[str]) -> str:
    return re.sub(r"\s+COUNTY$", "", (county or "").strip().upper())


def keys(record: Dict) -> Dict[str, Optional[str]]:
    street, unit = normalize_street(record.get("street_address"))
    unit = (record.get("unit") or unit or "")
    unit = re.sub(r"[^0-9A-Z-]", "", str(unit).upper()) or None
    zip5 = re.sub(r"\D", "", str(record.get("zip_code") or ""))[:5] or None
    city = (record.get("city") or "").strip().upper() or None
    state = (record.get("state") or "").strip().upper()[:2] or None
    apn = normalize_apn(record.get("parcel_apn"))
    ck = county_key(record.get("county"))
    return {
        "street": street or None, "unit": unit, "zip5": zip5, "city": city, "state": state,
        "address_key": "%s|%s|%s" % (street, unit or "", zip5) if street and zip5 else None,
        "street_key": "%s|%s|%s|%s" % (street, unit or "", city or "", state or "")
        if street and (city or state) else None,
        "apn_key": "%s|%s|%s" % (apn, ck, state or "") if apn and ck else None,
        "number_street": street,
    }


def resolve(db, org_id: str, record: Dict) -> Tuple[str, Optional[EvoSenseProperty],
                                                   List[EvoSenseProperty], List[str]]:
    """Return (match_type, property_or_None, candidates, keys_matched)."""
    k = keys(record)
    base = db.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == org_id)

    by_apn = base.filter(EvoSenseProperty.apn_key == k["apn_key"]).all() if k["apn_key"] else []
    by_addr = base.filter(EvoSenseProperty.address_key == k["address_key"]).all() \
        if k["address_key"] else []

    if by_apn:
        if len(by_apn) == 1:
            p = by_apn[0]
            # An APN pointing one way and an address pointing another is not
            # something a machine should settle.
            if k["address_key"] and p.address_key and p.address_key != k["address_key"] \
                    and not (by_addr and by_addr[0].id == p.id):
                return AMBIGUOUS, None, [p] + [x for x in by_addr if x.id != p.id], ["apn"]
            return EXACT, p, [p], ["apn"]
        return AMBIGUOUS, None, by_apn, ["apn"]

    if by_addr:
        if len(by_addr) == 1:
            p = by_addr[0]
            if k["apn_key"] and p.apn_key and p.apn_key != k["apn_key"]:
                return AMBIGUOUS, None, [p], ["address"]
            return EXACT, p, [p], ["address"]
        return AMBIGUOUS, None, by_addr, ["address"]

    if k["street_key"]:
        by_street = base.filter(EvoSenseProperty.street_key == k["street_key"]).all()
        if len(by_street) == 1:
            p = by_street[0]
            zip_conflict = bool(k["zip5"] and p.zip_code and p.zip_code[:5] != k["zip5"])
            apn_conflict = bool(k["apn_key"] and p.apn_key and p.apn_key != k["apn_key"])
            if not zip_conflict and not apn_conflict:
                return PROBABLE, p, [p], ["street", "city/state"]
            return AMBIGUOUS, None, [p], ["street", "city/state"]
        if len(by_street) > 1:
            return AMBIGUOUS, None, by_street, ["street"]

    # Same house number + street, but a unit on one side only (or a different
    # unit with no ZIP to separate them): could be the building or one unit.
    if k["number_street"] and (k["zip5"] or k["city"]):
        like = base.filter(EvoSenseProperty.address_key.like(k["number_street"] + "|%")).all() \
            if k["zip5"] else []
        near = [p for p in like
                if (p.zip_code or "")[:5] == k["zip5"] and (p.unit or None) != k["unit"]
                and (p.unit is None or k["unit"] is None)]
        if near:
            return AMBIGUOUS, None, near, ["street", "unit differs"]
    return NEW, None, [], []


def apply_keys(prop: EvoSenseProperty, record: Dict) -> None:
    k = keys(record)
    prop.address_key = k["address_key"] or prop.address_key
    prop.street_key = k["street_key"] or prop.street_key
    prop.apn_key = k["apn_key"] or prop.apn_key
