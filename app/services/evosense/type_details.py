"""PROPERTY DETAILS AND ANALYSIS BY PROPERTY TYPE.

One property record, one set of facts - the screen shows the fields that matter
for the property's category, each with WHERE it came from:

    county record   a column the appraisal district filled (area, year built,
                    tax value, land value)
    imported list   the list's claim (unverified)
    entered         a person entered it, optionally with the evidence
                    (a rent roll, a lease, a survey, an MLS sold record)
    not on record   nothing - shown as missing, never filled in

Analysis follows the category, never the residential formula by default:

    residential   the EXISTING Deal Analysis (ARV from closed comps, repairs,
                  MAO, spread) - not duplicated here
    commercial / industrial / multifamily / mixed-use / specialty
                  income approach: gross income -> vacancy -> effective gross
                  income -> operating expenses -> NOI -> value = NOI / cap rate
                  (plus per unit / per sq ft)
    land          comparable price per acre x acreage, minus development,
                  utility, access and holding costs
    agricultural  comparable price per acre x acreage as MARKET value, kept
                  apart from the productivity (ag-use) tax value, with the
                  rollback-tax exposure a person records

A number is computed only from inputs that exist; every result lists its
inputs and their sources. Missing inputs -> "NEEDS FINANCIAL INFORMATION" with
the exact list. Stage: PRELIMINARY (some input has no evidence), SUPPORTED
(every input has written evidence); CONTRACTED / REALIZED come from the deal.
Nothing is invented: no default vacancy, expense ratio, cap rate or price.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.services.evosense import common as C

# key: (label, kind, min, max)   kind: money | number | pct | int | text | bool
FIELDS: Dict[str, Tuple[str, str, float, float]] = {
    "bedrooms": ("Bedrooms", "number", 0, 50), "bathrooms": ("Bathrooms", "number", 0, 50),
    "square_feet": ("Living area (sq ft)", "int", 0, 5_000_000), "lot_acres": ("Lot / land area (acres)", "number", 0, 1_000_000),
    "year_built": ("Year built", "int", 1700, 2100), "condition": ("Condition", "text", 0, 0),
    "repairs": ("Known repairs ($)", "money", 0, 1e9),
    "unit_count": ("Units", "int", 1, 100_000), "unit_mix": ("Unit mix", "text", 0, 0),
    "occupancy_pct": ("Occupancy (%)", "pct", 0, 100), "rent_roll": ("Rent roll on file", "text", 0, 0),
    "gross_income": ("Gross annual income ($)", "money", 0, 1e11), "vacancy_pct": ("Vacancy allowance (%)", "pct", 0, 100),
    "operating_expenses": ("Annual operating expenses ($)", "money", 0, 1e11), "noi": ("NOI - stated ($)", "money", -1e11, 1e11),
    "cap_rate": ("Cap rate (%)", "pct", 0.1, 30), "deferred_maintenance": ("Deferred maintenance ($)", "money", 0, 1e10),
    "building_sqft": ("Building area (sq ft)", "int", 0, 50_000_000), "property_class_grade": ("Building class (A/B/C)", "text", 0, 0),
    "tenants": ("Tenants", "text", 0, 0), "lease_terms": ("Lease terms", "text", 0, 0), "zoning": ("Zoning", "text", 0, 0),
    "clear_height_ft": ("Clear height (ft)", "number", 0, 200), "loading_docks": ("Loading docks", "int", 0, 1000),
    "yard_acres": ("Yard space (acres)", "number", 0, 10000), "power": ("Power", "text", 0, 0),
    "acreage": ("Acreage", "number", 0, 10_000_000), "usable_acres": ("Usable acreage", "number", 0, 10_000_000),
    "dimensions": ("Parcel dimensions", "text", 0, 0), "legal_access": ("Legal access", "text", 0, 0),
    "road_frontage_ft": ("Road frontage (ft)", "number", 0, 1_000_000), "utilities": ("Utilities", "text", 0, 0),
    "floodplain": ("Floodplain", "text", 0, 0), "easements": ("Easements", "text", 0, 0),
    "restrictions": ("Development restrictions", "text", 0, 0), "buildability": ("Buildability", "text", 0, 0),
    "ag_classification": ("Agricultural classification", "text", 0, 0), "land_use": ("Current land use", "text", 0, 0),
    "water": ("Water availability", "text", 0, 0), "water_rights": ("Water rights evidence", "text", 0, 0),
    "mineral_rights": ("Mineral rights evidence", "text", 0, 0), "improvements": ("Improvements", "text", 0, 0),
    "fencing": ("Fencing", "text", 0, 0), "soil": ("Soil characteristics", "text", 0, 0),
    "ag_exemption": ("Agricultural exemption", "text", 0, 0), "rollback_tax": ("Rollback-tax exposure ($)", "money", 0, 1e10),
    "residential_units": ("Residential units", "int", 0, 100_000), "commercial_units": ("Commercial units", "int", 0, 100_000),
    "income_streams": ("Separate income streams", "text", 0, 0),
    # analysis inputs (market / comparable evidence a person brings)
    "comp_price_per_acre": ("Comparable sales - price per acre ($)", "money", 0, 1e9),
    "development_costs": ("Development costs ($)", "money", 0, 1e11), "utility_costs": ("Utility costs ($)", "money", 0, 1e10),
    "access_costs": ("Access costs ($)", "money", 0, 1e10), "holding_costs": ("Holding costs ($)", "money", 0, 1e10),
    "renovation_costs": ("Renovation costs ($)", "money", 0, 1e11),
    "acquisition_expenses": ("Acquisition expenses ($)", "money", 0, 1e10),
    "purchase_price": ("Contract / purchase price ($)", "money", 0, 1e11),
    "buyer_price": ("Investor (buyer) price ($)", "money", 0, 1e11),
}

SECTIONS = {
    "residential": ["bedrooms", "bathrooms", "square_feet", "lot_acres", "year_built", "condition", "repairs"],
    "multifamily": ["unit_count", "unit_mix", "occupancy_pct", "rent_roll", "gross_income", "vacancy_pct",
                    "operating_expenses", "noi", "cap_rate", "deferred_maintenance", "renovation_costs", "year_built"],
    "commercial": ["building_sqft", "lot_acres", "property_class_grade", "occupancy_pct", "tenants", "lease_terms",
                   "rent_roll", "gross_income", "vacancy_pct", "operating_expenses", "noi", "cap_rate", "zoning",
                   "deferred_maintenance"],
    "industrial": ["building_sqft", "clear_height_ft", "loading_docks", "yard_acres", "power", "occupancy_pct",
                   "gross_income", "vacancy_pct", "operating_expenses", "noi", "cap_rate", "zoning",
                   "deferred_maintenance"],
    "land": ["acreage", "dimensions", "zoning", "legal_access", "road_frontage_ft", "utilities", "floodplain",
             "easements", "restrictions", "buildability", "comp_price_per_acre", "development_costs", "utility_costs",
             "access_costs", "holding_costs"],
    "agricultural": ["acreage", "usable_acres", "ag_classification", "land_use", "water", "water_rights",
                     "mineral_rights", "improvements", "fencing", "soil", "legal_access", "ag_exemption",
                     "rollback_tax", "comp_price_per_acre"],
    "mixed_use": ["residential_units", "commercial_units", "income_streams", "gross_income", "vacancy_pct",
                  "operating_expenses", "noi", "cap_rate", "zoning"],
    "specialty": ["unit_count", "occupancy_pct", "gross_income", "vacancy_pct", "operating_expenses", "noi",
                  "cap_rate", "building_sqft", "acreage", "zoning"],
    "unknown": ["building_sqft", "square_feet", "acreage", "zoning", "year_built"],
}
DEAL_FIELDS = ["acquisition_expenses", "purchase_price", "buyer_price"]
INCOME_TYPES = ("commercial", "industrial", "multifamily", "mixed_use", "specialty")

QUESTIONS = {
    "commercial": ["Is the building occupied, and by whom?", "What leases are in place - terms and rents?",
                   "What does it bring in each year, and what does it cost to run?",
                   "What condition is it in - roof, HVAC, parking?", "Why are you considering selling?"],
    "industrial": ["Is it leased - to whom, for how long?", "Clear height, docks and yard space?",
                   "Annual income and operating costs?", "Any deferred maintenance?", "Why sell now?"],
    "multifamily": ["How many units, and what is the mix?", "How many are occupied right now?",
                    "What are the current rents?", "What are the annual expenses?", "What repairs does it need?"],
    "land": ["Does it have legal road access?", "Which utilities reach the property?",
             "What is it zoned, and any restrictions or easements?", "Is any of it in a floodplain?",
             "Who is on the title, and can they all sign?"],
    "agricultural": ["How many acres, and how many are usable?", "What water is available - wells, tanks, rights?",
                     "What improvements are on it (house, barns, fencing)?", "How is it used today, and is there an ag exemption?",
                     "Any title, mineral-rights or estate questions?"],
}


def facts(prop) -> Dict[str, Any]:
    return C.jload(getattr(prop, "type_facts", None), {}) or {}


def _county_value(prop, key) -> Optional[Any]:
    if key in ("bedrooms", "bathrooms", "square_feet", "year_built"):
        v = getattr(prop, key, None)
        if v is None:
            return None
        return float(v) if key in ("bedrooms", "bathrooms") else int(v)
    return None


def _clean(key: str, value):
    label, kind, lo, hi = FIELDS[key]
    if value in (None, ""):
        return None
    if kind in ("text",):
        return str(value).strip()[:500]
    if kind == "bool":
        return bool(value)
    try:
        v = float(str(value).replace(",", "").replace("$", "").replace("%", ""))
    except ValueError:
        raise ValueError("%s must be a number." % label)
    if kind == "int":
        if v != int(v):
            raise ValueError("%s must be a whole number." % label)
        v = int(v)
    if not (lo <= v <= hi):
        raise ValueError("%s must be between %s and %s." % (label, lo, hi))
    return v


def set_facts(db, prop, user, values: Dict[str, Any], evidence: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """A person enters type facts (with optional written evidence per fact).
    None removes the person's entry. Audited with before / after."""
    mine = facts(prop)
    before = {k: (mine.get(k) or {}).get("value") for k in values}
    for key, raw in values.items():
        if key not in FIELDS:
            raise ValueError("Unknown field: %s" % key)
        v = _clean(key, raw)
        if v is None:
            mine.pop(key, None)
            continue
        mine[key] = {"value": v, "source": "entered", "by": getattr(user, "email", None),
                     "evidence": ((evidence or {}).get(key) or "").strip()[:300] or None, "at": C.now().isoformat() + "Z"}
    prop.type_facts = C.jdump(mine) if mine else None
    C.log_event(db, prop.organization_id, "property.type_facts", property_id=prop.id, user=user,
                actor_type=C.ACTOR_USER, is_test=bool(prop.is_test),
                summary="Property facts entered: %s" % ", ".join(FIELDS[k][0] for k in values if k in FIELDS)[:200],
                details={"before": before, "after": {k: (mine.get(k) or {}).get("value") for k in values}})
    db.flush()
    return mine


def import_claims(prop, claims: Dict[str, Any]) -> int:
    """Values an imported list states (acreage, units, NOI ...) become the LIST's
    claim - never over a person's entry. Returns how many were written."""
    mine = facts(prop)
    n = 0
    for key, raw in (claims or {}).items():
        if key not in FIELDS or (mine.get(key) or {}).get("source") == "entered":
            continue
        try:
            v = _clean(key, raw)
        except ValueError:
            continue
        if v is None:
            continue
        mine[key] = {"value": v, "source": "imported list", "at": C.now().isoformat() + "Z"}
        n += 1
    prop.type_facts = C.jdump(mine) if mine else None
    return n


def _field(prop, mine, key) -> Dict[str, Any]:
    label, kind, _lo, _hi = FIELDS[key]
    e = mine.get(key)
    if e:
        return {"key": key, "label": label, "kind": kind, "value": e["value"],
                "source": "Entered by %s" % (e.get("by") or "a person") if e["source"] == "entered" else "Imported list (claim)",
                "evidence": e.get("evidence"), "verified": e["source"] == "entered" and bool(e.get("evidence"))}
    cv = _county_value(prop, key)
    if cv is not None:
        return {"key": key, "label": label, "kind": kind, "value": cv, "source": "County record", "evidence": None,
                "verified": True}
    return {"key": key, "label": label, "kind": kind, "value": None, "source": "Not on record", "evidence": None,
            "verified": False}


def _num(fs: Dict[str, Dict[str, Any]], k) -> Optional[float]:
    v = (fs.get(k) or {}).get("value")
    return float(v) if isinstance(v, (int, float)) else None


def analysis(category: str, fs: Dict[str, Dict[str, Any]], *, appraisal: Optional[Dict[str, Any]] = None
             ) -> Dict[str, Any]:
    """Pure: the category's own method over the fields shown (with sources)."""
    used: List[str] = []
    missing: List[str] = []
    out: Dict[str, Any] = {"category": category, "lines": [], "values": {}}

    def need(*keys):
        miss = [FIELDS[k][0] for k in keys if _num(fs, k) is None]
        missing.extend(m for m in miss if m not in missing)
        return not miss

    def line(label, value, how, inputs):
        used.extend(i for i in inputs if i not in used)
        out["lines"].append({"label": label, "value": round(value, 2) if isinstance(value, float) else value,
                             "how": how, "inputs": [{"key": i, "label": FIELDS[i][0], "value": (fs.get(i) or {}).get("value"),
                                                      "source": (fs.get(i) or {}).get("source")} for i in inputs]})
        out["values"][label] = value

    if category == "residential":
        out["method"] = "Residential: ARV from closed comparable sales, repairs, MAO and spread - in Deal Analysis"
        out["delegate"] = "deal_analysis"
    elif category in INCOME_TYPES:
        out["method"] = "Income approach: NOI / cap rate (never a residential ARV formula)"
        noi = None
        if need("gross_income") and _num(fs, "vacancy_pct") is not None and _num(fs, "operating_expenses") is not None:
            egi = _num(fs, "gross_income") * (1 - _num(fs, "vacancy_pct") / 100.0)
            line("Effective gross income", egi, "gross income x (1 - vacancy)", ["gross_income", "vacancy_pct"])
            noi = egi - _num(fs, "operating_expenses")
            line("NOI", noi, "effective gross income - operating expenses", ["gross_income", "vacancy_pct",
                                                                             "operating_expenses"])
        elif _num(fs, "noi") is not None:
            noi = _num(fs, "noi")
            line("NOI", noi, "NOI as stated (not built from income and expenses)", ["noi"])
            missing[:] = [m for m in missing if m != FIELDS["gross_income"][0]]
        else:
            need("vacancy_pct", "operating_expenses")
        if noi is not None and need("cap_rate"):
            val = noi / (_num(fs, "cap_rate") / 100.0)
            line("Income value", val, "NOI / cap rate", ["cap_rate"])
            if _num(fs, "unit_count"):
                line("Value per unit", val / _num(fs, "unit_count"), "income value / units", ["unit_count"])
            if _num(fs, "building_sqft"):
                line("Value per building sq ft", val / _num(fs, "building_sqft"), "income value / building area",
                     ["building_sqft"])
            costs = sum(_num(fs, k) or 0 for k in ("renovation_costs", "deferred_maintenance"))
            if costs:
                line("Value after known costs", val - costs, "income value - renovation - deferred maintenance",
                     [k for k in ("renovation_costs", "deferred_maintenance") if _num(fs, k)])
    elif category in ("land", "agricultural"):
        out["method"] = ("Comparable price per acre x acreage (market)" +
                         ("; the productivity (ag-use) tax value is shown apart and is never market value"
                          if category == "agricultural" else ""))
        if need("acreage", "comp_price_per_acre"):
            mv = _num(fs, "acreage") * _num(fs, "comp_price_per_acre")
            line("Market value from comparables", mv, "acreage x comparable price per acre",
                 ["acreage", "comp_price_per_acre"])
            costs = [k for k in ("development_costs", "utility_costs", "access_costs", "holding_costs")
                     if _num(fs, k) is not None]
            if costs:
                line("Exit value after costs", mv - sum(_num(fs, k) for k in costs), "market value - listed costs", costs)
        if category == "agricultural":
            if appraisal and appraisal.get("value"):
                out["tax_value"] = {"value": appraisal["value"], "label": "Appraisal district value (tax) - for an "
                                    "ag-exempt tract this is the productivity value, NOT market value"}
            if _num(fs, "rollback_tax") is not None:
                line("Rollback-tax exposure", _num(fs, "rollback_tax"), "as recorded", ["rollback_tax"])
            else:
                missing.append(FIELDS["rollback_tax"][0])
    else:
        out["method"] = "Property type needs review before an analysis method can be chosen"
        missing.append("Property type")
    # the deal: spread only from a contract price AND an investor price
    if _num(fs, "purchase_price") is not None and _num(fs, "buyer_price") is not None:
        spread = _num(fs, "buyer_price") - _num(fs, "purchase_price") - (_num(fs, "acquisition_expenses") or 0)
        line("Potential assignment spread", spread, "investor price - contract price - acquisition expenses",
             [k for k in ("buyer_price", "purchase_price", "acquisition_expenses") if _num(fs, k) is not None])
    computed = bool(out["lines"])
    evidence_ok = computed and all((fs.get(k) or {}).get("verified") for k in used)
    out["inputs_used"] = used
    out["missing"] = missing
    out["stage"] = "supported" if evidence_ok else "preliminary" if computed else "needs_information"
    out["stage_label"] = {"supported": "SUPPORTED - every input has written evidence",
                          "preliminary": "PRELIMINARY - some inputs have no evidence yet",
                          "needs_information": "NEEDS FINANCIAL INFORMATION"}[out["stage"]]
    return out


def view(db, prop) -> Dict[str, Any]:
    from app.services.evosense import property_class as PCL
    from app.services.evosense import valuation as VAL
    pc = PCL.view(prop)
    cat = pc["category"]
    mine = facts(prop)
    keys = SECTIONS.get(cat, SECTIONS["unknown"])
    fields = [_field(prop, mine, k) for k in keys + DEAL_FIELDS]
    fs = {f["key"]: f for f in fields}
    for k in FIELDS:                         # analysis may use a fact entered outside the section
        if k not in fs and k in mine:
            fs[k] = _field(prop, mine, k)
    appraisal = (VAL.view(prop) or {}).get("appraisal")
    return {"property_class": pc, "section": [f for f in fields if f["key"] not in DEAL_FIELDS],
            "deal": [f for f in fields if f["key"] in DEAL_FIELDS],
            "analysis": analysis(cat, fs, appraisal=appraisal),
            "questions": QUESTIONS.get(cat, []),
            "fields": {k: {"label": v[0], "kind": v[1]} for k, v in FIELDS.items()},
            "note": "Each value shows where it came from. Missing values are shown as missing, never filled in."}
