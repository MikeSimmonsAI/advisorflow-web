"""PROPERTY CLASSIFICATION - one normalized type per property, from the
county's OWN category code wherever one is on record.

Texas appraisal districts tag every account with the Comptroller's state
property category (Property Tax Assistance Division, "Property Value Study"
categories), and the districts' files carry it: DCAD SPTD_CODE, TAD
State_Use_Code, Collin propcategorycode, Denton state_cd, the Dallas tax roll's
category, HCAD / the statewide parcel layer STAT_LAND_. Those codes are the
evidence here:

    A  single-family residential        -> residential
    B  multifamily residential          -> multifamily (B2 duplex-fourplex: residential)
    C  vacant lots and tracts           -> land
    D  qualified open-space (ag) land   -> agricultural (D2: farm / ranch improvements)
    E  rural land not ag-qualified      -> land (undeveloped acreage)
    F1 commercial real property         -> commercial
    F2 industrial real property         -> industrial
    M  mobile / manufactured homes      -> residential (manufactured home)
    O  residential inventory            -> residential
    G J L N S X (minerals, utilities, business personal property, exempt)
                                        -> unknown: not a property we can classify for acquisition

Precedence: an appraisal-district code > a tax-roll code > the statewide layer
> a list's claim > the coarse type an older adapter stored. Two official codes
that disagree on the CATEGORY are recorded as a conflict and the property reads
"Property type needs review" - the first code is not silently chosen. No code
at all -> UNKNOWN (never "residential" by default). Mixed-use and specialty
types have no state code: they come only from a person or an import, labelled so.

Nothing here changes a property's identity, owner, notices or money - it adds
the classification columns and their evidence.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

CATEGORIES = ("residential", "multifamily", "commercial", "industrial", "land", "agricultural", "mixed_use",
              "specialty", "unknown")
CATEGORY_LABELS = {"residential": "Residential", "multifamily": "Multifamily", "commercial": "Commercial",
                   "industrial": "Industrial", "land": "Land", "agricultural": "Agricultural",
                   "mixed_use": "Mixed-use", "specialty": "Specialty", "unknown": "Property type needs review"}
SUBTYPES = {
    "residential": ("single_family", "condominium", "townhouse", "manufactured_home", "duplex", "triplex",
                    "fourplex", "duplex_fourplex"),
    "multifamily": ("small_apartment", "large_apartment", "student_housing", "senior_housing", "apartments"),
    "commercial": ("retail", "office", "medical_office", "shopping_center", "restaurant", "hospitality"),
    "industrial": ("warehouse", "manufacturing", "distribution", "flex_industrial"),
    "land": ("residential_lot", "commercial_land", "development_land", "infill_lot", "undeveloped_acreage",
             "vacant_lot"),
    "agricultural": ("farm", "ranch", "pasture", "timberland", "ag_acreage", "ag_improvements"),
    "mixed_use": ("residential_commercial", "retail_with_apartments", "live_work"),
    "specialty": ("self_storage", "mobile_home_park", "rv_park", "hotel", "other_investment"),
    "unknown": (),
}
SUBTYPE_LABELS = {s: s.replace("_", " ").capitalize() for subs in SUBTYPES.values() for s in subs}
SUBTYPE_LABELS.update({"duplex_fourplex": "Duplex to fourplex", "ag_acreage": "Agricultural acreage",
                       "ag_improvements": "Farm / ranch improvements", "rv_park": "RV park",
                       "flex_industrial": "Flex industrial"})

CONF_RECORD, CONF_ROLL, CONF_STATE, CONF_ADAPTER, CONF_CLAIM, CONF_PERSON = (
    "county_record", "tax_roll", "statewide_layer", "source_type", "list_claim", "person")
CONF_LABELS = {CONF_RECORD: "Appraisal-district category code", CONF_ROLL: "Tax-roll category code",
               CONF_STATE: "Statewide parcel layer code", CONF_ADAPTER: "Source's coarse type (no code kept)",
               CONF_CLAIM: "Imported list's claim - unverified", CONF_PERSON: "Entered by a person"}
RANK = {CONF_PERSON: 6, CONF_RECORD: 5, CONF_ROLL: 4, CONF_STATE: 3, CONF_CLAIM: 2, CONF_ADAPTER: 1}

# where each provider keeps its state category code, and how much it is worth
PROVIDER_CODE = {
    "dcad": (CONF_RECORD, ("SPTD_CODE",)), "tad": (CONF_RECORD, ("state_use_code", "property_class")),
    "collin_cad": (CONF_RECORD, ("propcategorycode", "category")), "denton_gis": (CONF_RECORD, ("state_cd",)),
    "johnson_cad": (CONF_RECORD, ("state_cd", "STATE_CD", "PROP_TYPE_CD")),
    "hcad_roll": (CONF_RECORD, ("STAT_LAND_", "land_use", "state_class")),
    "dallas_trw": (CONF_ROLL, ("category",)), "travis_delinquent": (CONF_ROLL, ("property_class", "State Code")),
    "tarrant_tax_roll": (CONF_ROLL, ("state_code",)),
    "txgio_roll": (CONF_STATE, ("STAT_LAND_", "land_use")), "txgio_parcels": (CONF_STATE, ("STAT_LAND_",)),
}

LEGACY_TYPES = {
    "single_family": ("residential", "single_family"), "condo": ("residential", "condominium"),
    "townhouse": ("residential", "townhouse"), "mobile_home": ("residential", "manufactured_home"),
    "duplex": ("residential", "duplex"), "triplex": ("residential", "triplex"),
    "fourplex": ("residential", "fourplex"), "multifamily": ("multifamily", None),
    "multi_family": ("multifamily", None), "land": ("land", None), "commercial": ("commercial", None),
    "industrial": ("industrial", None), "agricultural": ("agricultural", None), "farm": ("agricultural", "farm"),
    "ranch": ("agricultural", "ranch"), "mixed_use": ("mixed_use", None),
}


def from_state_code(code: Optional[str]) -> Optional[Tuple[str, Optional[str]]]:
    """Texas state property category code -> (category, subtype); None when
    the code is missing or not one we can read."""
    c = re.sub(r"[^A-Z0-9]", "", (code or "").upper())
    if not c or not c[0].isalpha():
        return None
    head, digit = c[0], (c[1] if len(c) > 1 and c[1].isdigit() else "")
    if head == "A":
        return ("residential", "manufactured_home" if digit == "2" else "single_family" if digit in ("", "1") else None)
    if head == "B":
        return ("residential", "duplex_fourplex") if digit == "2" else ("multifamily", "apartments" if digit == "1" else None)
    if head == "C":
        return ("land", "vacant_lot")
    if head == "D":
        return ("agricultural", "ag_improvements" if digit == "2" else "ag_acreage")
    if head == "E":
        return ("land", "undeveloped_acreage")
    if head == "F":
        return ("industrial", None) if digit == "2" else ("commercial", None)
    if head == "M":
        return ("residential", "manufactured_home")
    if head == "O":
        return ("residential", None)
    if head in ("G", "J", "L", "N", "S", "X", "H", "K"):
        return ("unknown", None)        # minerals / utilities / business personal / exempt: not a target type
    return None


# ── what a county READER may bring into Discovery ──────────────────────────
# The kinds of property EvoSense acquires. G/J/L/N/S/X accounts (minerals,
# utilities, business personal property, exempt) are never imported.
TARGET_CATEGORIES = ("residential", "multifamily", "commercial", "industrial", "land", "agricultural")
# a strategy's legacy type words -> the category they ask for
_TYPE_TO_CATEGORY = {"single_family": "residential", "duplex": "residential", "triplex": "residential",
                     "fourplex": "residential", "condo": "residential", "townhouse": "residential",
                     "mobile_home": "residential", "multifamily": "multifamily", "land": "land",
                     "commercial": "commercial", "industrial": "industrial", "agricultural": "agricultural"}


def reader_category(code: Optional[str]) -> Tuple[Optional[str], Optional[str], str]:
    """A county row's own category code -> (category, subtype, outcome).
    outcome: "target" (a kind EvoSense acquires), "not_target" (minerals,
    utilities, personal property, exempt), or "no_code" (missing / unreadable -
    never assumed to be a house)."""
    m = from_state_code(code)
    if m is None:
        return None, None, "no_code"
    if m[0] not in TARGET_CATEGORIES:
        return None, None, "not_target"
    return m[0], m[1], "target"


def legacy_type_for(code: Optional[str]) -> Optional[str]:
    """The older single `property_type` word a reader stores beside the code -
    only where the code says it plainly (A1 a house, B1 apartments, C land, F1
    commercial). Anything else is left empty; the code is the evidence."""
    c = re.sub(r"[^A-Z0-9]", "", (code or "").upper())
    if c[:2] == "A1" or c == "A":
        return "single_family"
    if c[:2] == "A2" or c[:1] == "M":
        return "mobile_home"
    if c[:2] == "B1":
        return "multifamily"
    if c[:1] in ("C", "E"):
        return "land"
    if c[:2] == "F1":
        return "commercial"
    if c[:2] == "F2":
        return "industrial"
    if c[:1] == "D":
        return "agricultural"
    return None


def query_categories(query: Dict[str, Any]) -> Tuple[str, ...]:
    """Which categories a hunt's strategy asks for. "Houses only" -> residential;
    a type list -> the categories those types are; no list -> every target type."""
    if query.get("houses_only"):
        return ("residential",)
    types = [t for t in (query.get("property_types") or []) if t]
    if not types:
        return TARGET_CATEGORIES
    cats = {_TYPE_TO_CATEGORY[t] for t in types if t in _TYPE_TO_CATEGORY}
    return tuple(c for c in TARGET_CATEGORIES if c in cats) or ("residential",)


def strategy_type_key(prop) -> Optional[str]:
    """The strategy-type word to test this property against: the stored type
    when there is one, else the one its county category says (unknown -> None)."""
    t = getattr(prop, "property_type", None)
    if t:
        return t
    cat = getattr(prop, "property_category", None)
    if cat in ("multifamily", "commercial", "industrial", "land", "agricultural"):
        return cat
    if cat == "residential":
        sub = getattr(prop, "property_subtype", None)
        return {"manufactured_home": "mobile_home", "condominium": "condo", "townhouse": "townhouse",
                "duplex": "duplex", "triplex": "triplex", "fourplex": "fourplex",
                "duplex_fourplex": "fourplex"}.get(sub, "single_family")
    return None


def count_category(stats, prefix: str, category: Optional[str], outcome: str) -> None:
    """Per-category run counters a reader reports, e.g. found_commercial /
    kept_commercial, plus found_not_target / found_no_code - the evidence the
    coverage-by-type report reads (a category is "checked" only when a run counted it)."""
    if outcome != "target":
        stats["%s_%s" % (prefix, outcome)] += 1
    else:
        stats["%s_%s" % (prefix, category)] += 1


def from_text(value: Optional[str]) -> Optional[Tuple[str, Optional[str]]]:
    """A person's or a list's words ("Commercial", "Office", "Ranch", "Mixed use",
    "Self storage", "Single Family") -> (category, subtype). Unclear -> None."""
    t = re.sub(r"[^a-z]+", " ", (value or "").lower()).strip()
    if not t:
        return None
    if t.replace(" ", "_") in LEGACY_TYPES:
        return LEGACY_TYPES[t.replace(" ", "_")]
    for cat, subs in SUBTYPES.items():
        for s in subs:
            if t == s.replace("_", " ") or t == SUBTYPE_LABELS.get(s, "").lower():
                return cat, s
    words = [
        (r"\bmixed\s*use|live\s*work|retail with apartments", ("mixed_use", None)),
        (r"self\s*storage|mini\s*storage", ("specialty", "self_storage")),
        (r"mobile\s*home\s*park|manufactured\s*home\s*community", ("specialty", "mobile_home_park")),
        (r"\brv\b.*park", ("specialty", "rv_park")),
        (r"hotel|motel", ("specialty", "hotel")),
        (r"apartment|multi\s*family|multifamily", ("multifamily", None)),
        (r"duplex", ("residential", "duplex")), (r"triplex", ("residential", "triplex")),
        (r"fourplex|quadplex", ("residential", "fourplex")),
        (r"condo", ("residential", "condominium")), (r"town\s*house|townhome", ("residential", "townhouse")),
        (r"mobile|manufactured", ("residential", "manufactured_home")),
        (r"single\s*family|\bsfr\b|residential|house", ("residential", "single_family")),
        (r"warehouse", ("industrial", "warehouse")), (r"manufactur", ("industrial", "manufacturing")),
        (r"distribution", ("industrial", "distribution")), (r"industrial|flex", ("industrial", None)),
        (r"medical", ("commercial", "medical_office")), (r"office", ("commercial", "office")),
        (r"shopping|strip center", ("commercial", "shopping_center")), (r"restaurant", ("commercial", "restaurant")),
        (r"retail", ("commercial", "retail")), (r"commercial", ("commercial", None)),
        (r"ranch", ("agricultural", "ranch")), (r"farm", ("agricultural", "farm")),
        (r"pasture", ("agricultural", "pasture")), (r"timber", ("agricultural", "timberland")),
        (r"agricultur", ("agricultural", None)),
        (r"acreage|acres", ("land", "undeveloped_acreage")), (r"vacant|lot|land", ("land", None)),
    ]
    for rx, out in words:
        if re.search(rx, t):
            return out
    return None


def _code_from(provider: str, payload: Dict[str, Any], raw: Optional[str]) -> Optional[str]:
    keys = PROVIDER_CODE.get(provider, (None, ()))[1]
    ev = payload.get("evidence") or {}
    for k in keys:
        for src in (ev, payload, (payload.get("_raw") or {}) if isinstance(payload.get("_raw"), dict) else {}):
            v = src.get(k) if isinstance(src, dict) else None
            if v not in (None, "", "Null"):
                return str(v).split(",")[0].strip()
    if raw:
        for k in keys:
            m = re.search(r'"%s"\s*:\s*"([^"]+)"' % re.escape(k), raw)
            if m and m.group(1).strip() not in ("", "Null"):
                return m.group(1).split(",")[0].strip()
    return None


def classify(evidence: List[Dict[str, Any]], *, legacy_type: Optional[str] = None,
             person: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """`evidence`: [{"provider", "payload" (dict), "raw" (str)}] for one property.
    Returns {category, subtype, confidence, source, code, conflict, candidates}."""
    cands: List[Dict[str, Any]] = []
    for e in evidence:
        prov = e.get("provider") or ""
        payload = e.get("payload") or {}
        if prov in PROVIDER_CODE:
            code = _code_from(prov, payload, e.get("raw"))
            mapped = from_state_code(code)
            if mapped:
                cands.append({"category": mapped[0], "subtype": mapped[1], "confidence": PROVIDER_CODE[prov][0],
                              "source": prov, "code": code})
        elif prov == "csv_import":
            claims = ((payload.get("evidence") or {}).get("list_claims") or {})
            words = claims.get("property_type") or claims.get("property_subtype") or payload.get("property_type")
            mapped = from_text(words) if words else None
            if mapped:
                cands.append({"category": mapped[0], "subtype": mapped[1], "confidence": CONF_CLAIM,
                              "source": "imported list", "code": words})
    if person and person.get("category") in CATEGORIES:
        cands.append({"category": person["category"], "subtype": person.get("subtype"), "confidence": CONF_PERSON,
                      "source": "entered by %s" % (person.get("by") or "a person"), "code": None})
    if not cands and legacy_type and legacy_type in LEGACY_TYPES:
        cat, sub = LEGACY_TYPES[legacy_type]
        cands.append({"category": cat, "subtype": sub, "confidence": CONF_ADAPTER, "source": "existing type field",
                      "code": legacy_type})
    if not cands:
        return {"category": "unknown", "subtype": None, "confidence": None, "source": None, "code": None,
                "conflict": None, "candidates": []}
    cands.sort(key=lambda c: -RANK[c["confidence"]])
    best = cands[0]
    if best["confidence"] == CONF_PERSON:
        return dict(best, conflict=None, candidates=cands)
    official = [c for c in cands if c["confidence"] in (CONF_RECORD, CONF_ROLL, CONF_STATE)]
    cats = sorted({c["category"] for c in official})
    if len(cats) > 1:
        return {"category": "unknown", "subtype": None, "confidence": best["confidence"], "source": best["source"],
                "code": best["code"],
                "conflict": "County records disagree: %s" % "; ".join(
                    "%s says %s (%s)" % (c["source"], CATEGORY_LABELS[c["category"]], c["code"]) for c in official),
                "candidates": cands}
    return dict(best, conflict=None, candidates=cands)


def apply(prop, result: Dict[str, Any]) -> bool:
    """Write the classification columns. Returns True when anything changed.
    Never touches the legacy property_type, identity, owner or money fields."""
    new = (result["category"], result.get("subtype"), result.get("confidence"), result.get("source"),
           (result.get("code") or None) and str(result["code"])[:40], (result.get("conflict") or None)
           and result["conflict"][:250])
    old = (prop.property_category, prop.property_subtype, prop.property_class_confidence,
           prop.property_class_source, prop.property_class_code, prop.property_class_conflict)
    if new == old:
        return False
    (prop.property_category, prop.property_subtype, prop.property_class_confidence, prop.property_class_source,
     prop.property_class_code, prop.property_class_conflict) = new
    return True


def view(prop) -> Dict[str, Any]:
    cat = getattr(prop, "property_category", None) or "unknown"
    sub = getattr(prop, "property_subtype", None)
    conf = getattr(prop, "property_class_confidence", None)
    return {"category": cat, "category_label": CATEGORY_LABELS.get(cat, cat), "subtype": sub,
            "subtype_label": SUBTYPE_LABELS.get(sub) if sub else None,
            "confidence": conf, "confidence_label": CONF_LABELS.get(conf) if conf else "No classification on record",
            "source": getattr(prop, "property_class_source", None), "code": getattr(prop, "property_class_code", None),
            "conflict": getattr(prop, "property_class_conflict", None), "needs_review": cat == "unknown"}


def _evidence_for(db, org_id: str, prop_ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    from app.models.evosense_models import EvoSenseObservation
    out: Dict[str, List[Dict[str, Any]]] = {}
    providers = list(PROVIDER_CODE) + ["csv_import"]
    for pid, prov, payload, raw in (db.query(EvoSenseObservation.property_id, EvoSenseObservation.provider_key,
                                             EvoSenseObservation.payload, EvoSenseObservation.raw_payload)
                                    .filter(EvoSenseObservation.organization_id == org_id,
                                            EvoSenseObservation.property_id.in_(prop_ids or ["-"]),
                                            EvoSenseObservation.provider_key.in_(providers)).all()):
        try:
            p = json.loads(payload) if payload else {}
        except ValueError:
            p = {}
        out.setdefault(pid, []).append({"provider": prov, "payload": p if isinstance(p, dict) else {},
                                        "raw": raw if isinstance(raw, str) and raw.startswith("{") else None})
    return out


def classify_properties(db, org_id: str, props) -> int:
    """Classify these properties from their stored evidence; returns how many changed."""
    from app.services.evosense import common as C
    ev = _evidence_for(db, org_id, [p.id for p in props])
    changed = 0
    for p in props:
        person = C.jload(getattr(p, "property_class_person", None), None)
        if apply(p, classify(ev.get(p.id, []), legacy_type=p.property_type, person=person)):
            changed += 1
    return changed


BACKFILL_BATCH = 3000
# Rows held in memory at once. The pass used to load all BACKFILL_BATCH full
# property rows (each carries several KB of JSON detail columns) in one list:
# ~70 MB of RSS measured locally for 3,000 rows, kept after the pass, on a
# 512 MiB backend that idles near 450 MB. Same rows, same classification, same
# return value - just a bounded number in memory at a time. (P0, 2026-10-29)
BACKFILL_CHUNK = 250


def backfill(db, org_id: Optional[str] = None, *, limit: int = BACKFILL_BATCH) -> int:
    """Classify properties that have never been classified (NULL category),
    a batch per scheduler pass. Idempotent; never deletes or merges anything."""
    from app.models.evosense_models import EvoSenseProperty
    q = db.query(EvoSenseProperty.id, EvoSenseProperty.organization_id).filter(
        EvoSenseProperty.property_category.is_(None))
    if org_id:
        q = q.filter(EvoSenseProperty.organization_id == org_id)
    by_org: Dict[str, list] = {}
    total = 0
    for pid, oid in q.limit(limit).all():
        by_org.setdefault(oid, []).append(pid)
        total += 1
    for o, ids in by_org.items():
        for i in range(0, len(ids), BACKFILL_CHUNK):
            ps = db.query(EvoSenseProperty).filter(EvoSenseProperty.id.in_(ids[i:i + BACKFILL_CHUNK])).all()
            classify_properties(db, o, ps)
            db.flush()
            # Flushed: the changes live in the transaction the caller commits.
            # Release this chunk's rows before loading the next one.
            for p in ps:
                if p in db and p not in db.dirty:
                    db.expunge(p)
    return total


def set_by_person(db, prop, category: str, subtype: Optional[str], *, user, note: str = "") -> Dict[str, Any]:
    """A person states the type (e.g. mixed-use, self-storage - no state code
    exists for them). Kept with who said it; the county's codes stay as evidence."""
    from app.services.evosense import common as C
    if category not in CATEGORIES:
        raise ValueError("Choose a property category.")
    if subtype and subtype not in SUBTYPES.get(category, ()):
        raise ValueError("That subtype does not belong to %s." % CATEGORY_LABELS[category])
    before = view(prop)
    prop.property_class_person = C.jdump({"category": category, "subtype": subtype,
                                          "by": getattr(user, "email", None), "note": (note or "")[:200],
                                          "at": C.now().isoformat() + "Z"}) if category != "unknown" else None
    classify_properties(db, prop.organization_id, [prop])
    C.log_event(db, prop.organization_id, "property.type_set", property_id=prop.id, user=user,
                actor_type=C.ACTOR_USER, is_test=bool(prop.is_test),
                summary="Property type set to %s%s" % (CATEGORY_LABELS[category],
                                                        (" / %s" % SUBTYPE_LABELS.get(subtype, subtype)) if subtype else ""),
                details={"before": before, "after": view(prop), "note": note})
    return view(prop)


# ── source coverage by property type ───────────────────────────────────────
# What each connected source can actually tell us, per property type. Written
# from the adapters' own code (which accounts they read), not from what the
# source holds in general: a district file has every account, but an adapter
# that keeps only single-family rows covers only single-family.
COVERAGE = {
    "dcad": {"types": ["residential", "multifamily", "commercial", "industrial", "land", "agricultural"],
             "geo": "Dallas County",
             "fields": "owner, mailing, tax value, beds/baths/area/year, deed date, state code",
             "limit": "Discovery finds residential buildings rated poor condition (CDU) - every residential code "
                      "since 2026-10-25, not only A11. Commercial, industrial and land have no condition rating, "
                      "so DCAD only looks them up (owner, value, code) when the tax roll or a notice finds them."},
    "tad": {"types": ["residential", "multifamily", "land", "agricultural", "commercial", "industrial"],
            "geo": "Tarrant County", "fields": "owner, mailing, tax value, area/year, deed date, state use code",
            "limit": "Accounts matched to properties already found; the state use code says the type."},
    "collin_cad": {"types": ["residential", "multifamily", "land", "commercial"], "geo": "Collin County",
                   "fields": "owner, mailing, tax value, area/year, category code",
                   "limit": "Industrial (F2) is not separated from commercial by the older adapter type; the code is."},
    "denton_gis": {"types": ["residential", "multifamily", "land", "agricultural", "commercial", "industrial"],
                   "geo": "Denton County", "fields": "owner, mailing, tax value, state code", "limit": "Account lookup."},
    "txgio_roll": {"types": ["residential", "multifamily", "commercial", "industrial", "land", "agricultural"],
                   "geo": "Texas counties without their own discovery source (two per hunt)",
                   "fields": "owner, mailing, tax value, year built, state land code",
                   "limit": "Residential parcels with an absentee or estate owner; other types only when the owner "
                            "is an estate (an owner mailing elsewhere is normal for land and commercial). "
                            "No state code -> left out."},
    "txgio_parcels": {"types": ["residential", "multifamily", "commercial", "industrial", "land", "agricultural"],
                      "geo": "Texas (statewide parcel layer)", "fields": "owner, mailing, tax value, year built",
                      "limit": "One address at a time; the state land code is not kept by this lookup."},
    "dallas_trw": {"types": ["residential", "multifamily", "commercial", "industrial", "land", "agricultural"],
                   "geo": "Dallas County tax roll", "fields": "delinquent taxes, category code",
                   "limit": "Tax delinquency only. Every real-property category since 2026-10-25 (A1 houses "
                            "only before); the hunt's strategy decides which types are imported."},
    "travis_delinquent": {"types": ["residential", "multifamily", "commercial", "industrial", "land", "agricultural"],
                          "geo": "Travis County delinquent-tax file", "fields": "delinquent taxes, lawsuit, "
                          "appraised and ag values, property type code",
                          "limit": "Tax delinquency only. Every real-property code since 2026-10-25 (A1 houses "
                                   "only before); the hunt's strategy decides which types are imported."},
    "tarrant_tax_roll": {"types": ["residential", "multifamily", "commercial", "industrial", "land", "agricultural"],
                         "geo": "Tarrant County tax roll", "fields": "delinquent taxes", "limit": "Tax delinquency only."},
    "lgbs_tax_sales": {"types": ["residential", "commercial", "land", "agricultural"], "geo": "Texas counties served by Linebarger",
                       "fields": "tax suits and tax sales, minimum bid", "limit": "No property type field: type comes from the county record."},
    "tx_clerk_foreclosure": {"types": ["residential", "multifamily", "commercial", "industrial", "land", "agricultural"],
                             "geo": "Dallas, Tarrant, Denton, Johnson county clerks",
                             "fields": "foreclosure notices: document, sale date, trustee",
                             "limit": "Notices name an address or legal description, not a type; type comes from the county record."},
    "fw_code_violations": {"types": ["residential", "commercial"], "geo": "Fort Worth", "fields": "code cases",
                           "limit": "City code cases; no type field."},
    "dallas_311_code": {"types": ["residential", "commercial"], "geo": "Dallas", "fields": "311 code requests",
                        "limit": "No type field."},
    "rentcast": {"types": ["residential", "multifamily"], "geo": "United States", "fields": "AVM, listing comparables",
                 "limit": "Hard free budget; residential AVM - not for commercial, land or agricultural value."},
    "attom_comps": {"types": ["residential", "multifamily", "commercial", "industrial", "land", "agricultural"],
                    "geo": "United States", "fields": "property profile, sales, AVM, mortgage, assessment",
                    "limit": "Paid; evaluation only and off. Texas sale amounts unverified."},
    "csv_import": {"types": list(CATEGORIES), "geo": "Any (the file)", "fields": "whatever the file carries (PropStream export recognised)",
                   "limit": "The list's claims - unverified until a county record agrees."},
}


# What each widened DISCOVERY reader can bring in, by type, and its county.
DISCOVERY_TYPES = {
    "dcad": ("Dallas", ("residential",)),
    "dallas_trw": ("Dallas", TARGET_CATEGORIES),
    "travis_delinquent": ("Travis", TARGET_CATEGORIES),
    "txgio_roll": (None, TARGET_CATEGORIES),
}
CHECK_NO_MATCH, CHECK_NOT_CHECKED, CHECK_BLOCKED, CHECK_UNSUPPORTED, CHECK_OK = (
    "NO MATCHING RECORDS", "SOURCE NOT CHECKED", "SOURCE BLOCKED", "SOURCE DOES NOT SUPPORT TYPE",
    "SOURCE SUCCESSFULLY CHECKED")


def checked_coverage(db, org_id: str, *, runs: int = 60) -> List[Dict[str, Any]]:
    """Coverage by source and property type FROM WHAT THE READERS ACTUALLY
    COUNTED in this workspace's recent hunts - never from what a source could
    in theory carry. One row per (discovery source, category)."""
    from app.models.evosense_models import EvoSenseRun
    from app.services.evosense import common as C
    from app.services.evosense import providers as PV
    latest: Dict[str, Dict[str, Any]] = {}
    for started, counts in (db.query(EvoSenseRun.started_at, EvoSenseRun.counts)
                            .filter(EvoSenseRun.organization_id == org_id, EvoSenseRun.counts.isnot(None))
                            .order_by(EvoSenseRun.started_at.desc()).limit(runs).all()):
        for key, st in ((C.jload(counts, {}) or {}).get("sources") or {}).items():
            if key in DISCOVERY_TYPES and key not in latest and isinstance(st, dict) and \
                    any(k.startswith(("found_", "excluded_rows_")) for k in st):
                latest[key] = dict(st, _at=started)
    out = []
    for key, (county, supported) in DISCOVERY_TYPES.items():
        st = latest.get(key)
        p = PV.PROVIDERS.get(key)
        cfg = PV.config(db, org_id, key) if p is not None else None
        canon = PV.canonical(p, cfg, blocked=PV.platform_blocked(db, key, cfg)) if p is not None else {}
        blocked = canon.get("state") in (PV.S_BLOCKED, PV.S_FAILED, PV.S_DISABLED, PV.S_RATE_LIMITED)
        for cat in TARGET_CATEGORIES:
            found = kept = imported = 0
            if st:
                found = int(st.get("found_%s" % cat) or 0) + int(st.get("excluded_rows_%s" % cat) or 0)
                kept = int(st.get("kept_%s" % cat) or 0)
                imported = int(st.get("imported_%s" % cat) or 0)
            if cat not in supported:
                status, why = CHECK_UNSUPPORTED, COVERAGE.get(key, {}).get("limit")
            elif blocked:
                status, why = CHECK_BLOCKED, canon.get("why") or "source switched off"
            elif not st:
                status, why = CHECK_NOT_CHECKED, "no hunt has read this source since the reader was widened"
            elif found:
                status, why = CHECK_OK, (None if kept else "found, but no hunting strategy asks for this type, "
                                                          "or none passed the source's filters")
            else:
                status, why = CHECK_NO_MATCH, "the source was read; it held no record of this type that qualified"
            out.append({"source": key, "county": county or (st or {}).get("counties_checked") or "statewide rotation",
                        "category": cat, "category_label": CATEGORY_LABELS[cat], "status": status,
                        "identified": found, "eligible": kept,
                        "imported_last_run": imported,
                        "excluded": max(0, found - kept), "reason": why,
                        "last_checked": ((st or {}).get("_at").isoformat() + "Z") if st and st.get("_at") else None})
    return out


def coverage(db, org_id: str) -> List[Dict[str, Any]]:
    from app.services.evosense import providers as PV
    out = []
    for key, d in COVERAGE.items():
        p = PV.PROVIDERS.get(key)
        state, why, last_ok, cost = "NOT INSTALLED", "", None, "free"
        if p is not None:
            cfg = PV.config(db, org_id, key)
            canon = PV.canonical(p, cfg, blocked=PV.platform_blocked(db, key, cfg))
            state, why = canon.get("state"), canon.get("why")
            last_ok = cfg.last_success_at.isoformat() + "Z" if getattr(cfg, "last_success_at", None) else None
            cost = "paid" if (getattr(p, "costs", None) and any((getattr(p, "costs") or {}).values())) or \
                getattr(p, "evaluation_only", False) or key in ("rentcast",) else "free"
        if key == "rentcast":
            from app.services.evosense.sources import rentcast as RC
            state = "CONNECTED (free budget)" if RC.configured() else "NOT CONFIGURED"
            cost = "free tier, hard monthly cap"
        level = ("Official county record" if key in ("dcad", "tad", "collin_cad", "denton_gis", "txgio_roll",
                                                     "txgio_parcels", "dallas_trw", "tarrant_tax_roll",
                                                     "tx_clerk_foreclosure")
                 else "Public record" if key in ("lgbs_tax_sales", "fw_code_violations", "dallas_311_code")
                 else "Estimate / listing data" if key == "rentcast"
                 else "Vendor data (unverified for Texas prices)" if key == "attom_comps" else "The file's claim")
        status = ("PAID ACCESS REQUIRED" if key == "attom_comps"
                  else "SOURCE CONNECTED" if state in ("HEALTHY", "CONNECTED (free budget)", "MANUAL ONLY", "UNVERIFIED")
                  else "NOT CONNECTED" if state in ("DISABLED", "NOT CONFIGURED", "NOT PURCHASED", "NOT INSTALLED")
                  else "SOURCE CONNECTED - NEEDS ATTENTION")
        out.append({"source": key, "label": getattr(p, "label", key), "property_types": d["types"],
                    "geography": d["geo"], "fields": d["fields"], "verification": level, "last_success": last_ok,
                    "cost": cost, "state": state, "status": status, "why": why, "limitations": d["limit"]})
    return out
