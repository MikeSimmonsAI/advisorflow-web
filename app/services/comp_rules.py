"""COMP ELIGIBILITY - may this sale support an ARV for this subject?

Deterministic, versioned, explainable, tenant-configurable. Every comp gets an
answer and every "no" has a reason a person can read. The rules never change
a comp's facts; they only decide whether the comp COUNTS.

Evidence required of every comp:
  * a CLOSED sale: a sale price and a sale date (a list price is not a sale)
  * provenance: a provider record, or - for a MANUAL comp - a source
    reference (MLS #, recorded document, URL) a second person could check
Similarity (each configurable; a missing fact on either side is "unknown",
never a silent pass, and lowers ARV confidence instead of excluding):
  distance, recency, property type, living area, beds, baths, year built,
  lot size (off by default)
Hard exclusions: non-arm's-length sale types, duplicate transactions,
obviously invalid prices, and a person's explicit exclusion (with reason).

Defaults are sensible for a suburban single-family market. They are defaults,
not DFW constants: each workspace sets its own (WholesaleSettings.comp_rules).
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

VERSION = "comp_eligibility/v1"

# WHERE A PRICE CAME FROM. Only a closed sale is ARV evidence:
#   MLS_CLOSED          the MLS closed/sold price, with its close date
#   PUBLIC_RECORD       a recorded deed price (not available in non-disclosure
#                       states such as Texas)
#   UNVERIFIED_RECORD   a "last sale price" from records in a non-disclosure
#                       state - its origin is unknown, so it is NOT evidence
#   ESTIMATED           a modelled / estimated sale price
#   LIST                a list (asking) price
#   AVM                 an automated valuation
# A comp typed by a person carries no price_source: it is MANUAL, needs a
# source reference, and is labelled as such everywhere.
PRICE_MLS_CLOSED = "MLS_CLOSED"
PRICE_PUBLIC_RECORD = "PUBLIC_RECORD"
PRICE_UNVERIFIED_RECORD = "UNVERIFIED_RECORD"
PRICE_ESTIMATED = "ESTIMATED"
PRICE_LIST = "LIST"
PRICE_AVM = "AVM"
CLOSED_PRICE_SOURCES = (PRICE_MLS_CLOSED, PRICE_PUBLIC_RECORD)
PRICE_SOURCE_LABELS = {PRICE_MLS_CLOSED: "MLS closed price", PRICE_PUBLIC_RECORD: "Public-record sale price",
                       PRICE_UNVERIFIED_RECORD: "Record price of unknown origin (non-disclosure state)",
                       PRICE_ESTIMATED: "Estimated sale price", PRICE_LIST: "List price",
                       PRICE_AVM: "AVM"}

DEFAULT_RULES: Dict[str, Any] = {
    "max_distance_miles": 1.0,
    "max_age_months": 12,
    "require_same_property_type": True,
    "sqft_tolerance_pct": 25,
    "max_bed_difference": 1,
    "max_bath_difference": 1.0,
    "max_year_built_difference": 20,
    "lot_tolerance_pct": None,           # off unless a workspace turns it on
    "min_price": 10000,
    "max_price_per_sqft": 2000,
    "excluded_sale_types": ["foreclosure", "reo", "auction", "short_sale", "family",
                            "non_arms_length", "quitclaim", "partial_interest"],
    "require_manual_reference": True,
    "min_comps": 3,
}

RULE_LABELS = {
    "max_distance_miles": "Maximum distance (miles)",
    "max_age_months": "Maximum sale age (months)",
    "require_same_property_type": "Same property type",
    "sqft_tolerance_pct": "Living area within ±%",
    "max_bed_difference": "Bedrooms within",
    "max_bath_difference": "Bathrooms within",
    "max_year_built_difference": "Year built within",
    "lot_tolerance_pct": "Lot size within ±% (blank = ignore)",
    "min_price": "Minimum credible sale price",
    "max_price_per_sqft": "Maximum credible $/sqft",
    "excluded_sale_types": "Sale types that never count",
    "require_manual_reference": "Manual comps need a source reference",
    "min_comps": "Eligible comps needed for an ARV",
}


def rules_for(settings) -> Dict[str, Any]:
    out = dict(DEFAULT_RULES)
    raw = getattr(settings, "comp_rules", None)
    if raw:
        try:
            given = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(given, dict):
                for k, v in given.items():
                    if k in DEFAULT_RULES:
                        out[k] = v
        except (ValueError, TypeError):
            pass
    return out


def validate_rules(value: Any) -> Optional[Dict[str, Any]]:
    """Clean a workspace's rules for storage. Raises ValueError with a sentence."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("Comp rules must be an object.")
    clean: Dict[str, Any] = {}
    for k, v in value.items():
        if k not in DEFAULT_RULES:
            raise ValueError("Unknown comp rule: %s." % k)
        if k in ("require_same_property_type", "require_manual_reference"):
            clean[k] = bool(v)
        elif k == "excluded_sale_types":
            if not isinstance(v, list):
                raise ValueError("Excluded sale types must be a list.")
            clean[k] = [str(x).strip().lower() for x in v if str(x).strip()]
        elif v is None and k == "lot_tolerance_pct":
            clean[k] = None
        else:
            try:
                n = float(v)
            except (TypeError, ValueError):
                raise ValueError("%s must be a number." % RULE_LABELS[k])
            if n < 0:
                raise ValueError("%s cannot be negative." % RULE_LABELS[k])
            if k == "min_comps" and not 1 <= n <= 20:
                raise ValueError("Comps needed for an ARV must be 1-20.")
            clean[k] = int(n) if k in ("max_age_months", "sqft_tolerance_pct", "max_bed_difference",
                                       "max_year_built_difference", "min_price", "min_comps",
                                       "lot_tolerance_pct", "max_price_per_sqft") else n
    return clean


def _num(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v if not isinstance(v, Decimal) else float(v))
    except (TypeError, ValueError):
        return None


def _date(v) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return datetime.fromisoformat(str(v)[:10]).date()
    except ValueError:
        return None


def distance_miles(lat1, lon1, lat2, lon2) -> Optional[float]:
    vals = [_num(x) for x in (lat1, lon1, lat2, lon2)]
    if any(v is None for v in vals):
        return None
    la1, lo1, la2, lo2 = [math.radians(v) for v in vals]
    a = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return round(3958.8 * 2 * math.asin(math.sqrt(a)), 2)


def _c(code, label, detail=None):
    return {"code": code, "label": label, "detail": detail}


def evaluate(comp, subject, rules: Dict[str, Any], *, today: Optional[date] = None,
             seen: Optional[set] = None) -> Dict[str, Any]:
    """One comp against one subject. `subject` needs: square_feet, bedrooms,
    bathrooms, year_built, lot_size_sqft, property_type, latitude, longitude
    (any may be None). `seen` is the set of transaction keys already counted,
    for duplicate detection. Returns {"eligible", "excluded", "unknown",
    "checks", "distance_miles", "price_per_sqft", "transaction_key"}."""
    today = today or date.today()
    excluded: List[Dict[str, Any]] = []
    unknown: List[Dict[str, Any]] = []
    passed: List[Dict[str, Any]] = []

    price = _num(getattr(comp, "sale_price", None))
    sdate = _date(getattr(comp, "sale_date", None))
    sqft = _num(getattr(comp, "square_feet", None))

    # 0. A person's explicit decision stands first, with their reason.
    if getattr(comp, "included", True) is False:
        excluded.append(_c("EXCLUDED_BY_PERSON", "Excluded by a person",
                           getattr(comp, "exclusion_reason", None) or "no reason given"))

    # 1. Closed-sale evidence. A price that is not a closed sale - an
    #    estimate, a list price, an AVM, or a record price of unknown origin
    #    in a non-disclosure state - never counts, however plausible.
    ps = getattr(comp, "price_source", None)
    if ps and ps not in CLOSED_PRICE_SOURCES:
        excluded.append(_c("PRICE_NOT_CLOSED_SALE", "Not a closed sale price",
                           PRICE_SOURCE_LABELS.get(ps, ps)))
    if price is None or price <= 0:
        excluded.append(_c("NO_SALE_PRICE", "No closed sale price"))
    if sdate is None:
        excluded.append(_c("NO_SALE_DATE", "No sale date - a price without a date is not closed-sale evidence"))
    elif sdate > today:
        excluded.append(_c("FUTURE_SALE_DATE", "Sale date is in the future"))

    # 2. Provenance.
    is_manual = not getattr(comp, "provider_key", None)
    if is_manual and rules.get("require_manual_reference") and not (
            getattr(comp, "source_reference", None) or "").strip():
        excluded.append(_c("NO_SOURCE_REFERENCE",
                           "Manual comp without a source reference (MLS #, deed, URL) - nobody could check it"))

    # 3. Sale type.
    st = (getattr(comp, "sale_type", None) or "").strip().lower()
    if st and st in [x.lower() for x in rules.get("excluded_sale_types") or []]:
        excluded.append(_c("SALE_TYPE", "Not an arm's-length market sale", st.replace("_", " ")))
    elif not st:
        unknown.append(_c("SALE_TYPE_UNKNOWN", "Sale type not stated"))

    # 4. Credible price.
    if price is not None and price > 0 and price < (rules.get("min_price") or 0):
        excluded.append(_c("PRICE_NOT_CREDIBLE", "Price below a credible market sale",
                           "$%s" % format(int(price), ",")))
    psf = (price / sqft) if (price and sqft and sqft > 0) else None
    if psf is not None and rules.get("max_price_per_sqft") and psf > rules["max_price_per_sqft"]:
        excluded.append(_c("PSF_NOT_CREDIBLE", "Price per sq ft not credible", "$%.0f/sqft" % psf))

    # 5. Duplicate transaction.
    key = "%s|%s|%s" % ((getattr(comp, "street_address", None) or "").strip().lower(),
                        sdate.isoformat() if sdate else "", int(price) if price else "")
    if seen is not None and sdate and price:
        if key in seen:
            excluded.append(_c("DUPLICATE_TRANSACTION", "Same sale already counted"))
        else:
            seen.add(key)

    # 6. Recency.
    if sdate is not None and rules.get("max_age_months") is not None:
        months = (today.year - sdate.year) * 12 + (today.month - sdate.month)
        if months > rules["max_age_months"]:
            excluded.append(_c("TOO_OLD", "Sale older than %s months" % rules["max_age_months"],
                               "%s months ago" % months))
        else:
            passed.append(_c("RECENT", "Sold %s month%s ago" % (months, "" if months == 1 else "s")))

    # 7. Distance: the stored distance, else computed from coordinates.
    dist = _num(getattr(comp, "distance_miles", None))
    if dist is None:
        dist = distance_miles(getattr(subject, "latitude", None), getattr(subject, "longitude", None),
                              getattr(comp, "latitude", None), getattr(comp, "longitude", None))
    if dist is None:
        unknown.append(_c("DISTANCE_UNKNOWN", "Distance to the subject unknown"))
    elif rules.get("max_distance_miles") is not None and dist > float(rules["max_distance_miles"]):
        excluded.append(_c("TOO_FAR", "Farther than %s miles" % rules["max_distance_miles"],
                           "%.2f miles" % dist))
    else:
        passed.append(_c("NEAR", "%.2f miles away" % dist))

    # 8. Similarity.
    def compare(code, label, a, b, limit, pct=False):
        a, b = _num(a), _num(b)
        if limit is None:
            return
        if a is None or b is None:
            unknown.append(_c(code + "_UNKNOWN", "%s unknown for %s" % (label, "subject" if a is None else "comp")))
            return
        diff = abs(a - b) / a * 100 if pct and a else abs(a - b)
        if diff > float(limit):
            excluded.append(_c(code, "%s differs too much" % label,
                               "%s vs %s" % (_fmt(b), _fmt(a))))
        else:
            passed.append(_c(code + "_OK", "%s similar" % label))

    if rules.get("require_same_property_type"):
        spt = (getattr(subject, "property_type", None) or "").strip().lower()
        cpt = (getattr(comp, "property_type", None) or "").strip().lower()
        if not spt or not cpt:
            unknown.append(_c("TYPE_UNKNOWN", "Property type unknown"))
        elif spt != cpt:
            excluded.append(_c("PROPERTY_TYPE", "Different property type", "%s vs %s" % (cpt, spt)))
    compare("SQFT", "Living area", getattr(subject, "square_feet", None), sqft,
            rules.get("sqft_tolerance_pct"), pct=True)
    compare("BEDS", "Bedrooms", getattr(subject, "bedrooms", None), getattr(comp, "bedrooms", None),
            rules.get("max_bed_difference"))
    compare("BATHS", "Bathrooms", getattr(subject, "bathrooms", None), getattr(comp, "bathrooms", None),
            rules.get("max_bath_difference"))
    compare("YEAR", "Year built", getattr(subject, "year_built", None), getattr(comp, "year_built", None),
            rules.get("max_year_built_difference"))
    if rules.get("lot_tolerance_pct") is not None:
        compare("LOT", "Lot size", getattr(subject, "lot_size_sqft", None),
                getattr(comp, "lot_size_sqft", None), rules["lot_tolerance_pct"], pct=True)

    return {"eligible": not excluded, "excluded": excluded, "unknown": unknown, "passed": passed,
            "distance_miles": dist, "price_per_sqft": round(psf, 2) if psf else None,
            "sale_date": sdate.isoformat() if sdate else None, "transaction_key": key,
            "manual": is_manual, "version": VERSION}


def _fmt(v):
    if v is None:
        return "?"
    return ("%d" % v) if float(v).is_integer() else ("%.1f" % v)
