"""What a property's dollar figures ARE — kept apart so one can never pose as another.

    APPRAISAL DISTRICT TAX VALUE   what a county appraisal district certified for
                                   property TAX (DCAD, TAD). A public record, but
                                   not a market value, not an estimate of what a
                                   buyer would pay, and NEVER an ARV.
    MARKET ESTIMATE                a modelled value from a valuation provider
                                   (an AVM). An estimate, labelled as one. Also
                                   never an ARV.
    ARV (after-repair value)       ONLY from verified comparable CLOSED sales.
                                   EvoSense has no closed-sale source connected,
                                   so every ARV here is INSUFFICIENT COMPARABLE
                                   SALES - stated, never manufactured.

Properties ingested before the split carried a DCAD value in
`estimated_value` with a source string saying exactly what it was
("DCAD 2026 appraised value (appraisal district, not a market estimate)").
`view()` reads those rows correctly TODAY, before they are re-derived, so no
screen and no calculation ever treats one as a market value in the meantime.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

APPRAISAL_LABEL = "Appraisal District Tax Value"
ARV_INSUFFICIENT = "Insufficient comparable sales"
_APPRAISAL_HINT = re.compile(r"apprais(al|ed)\s+(district|value)|appraisal district", re.I)


def is_appraisal_basis(text: Optional[str]) -> bool:
    return bool(text and _APPRAISAL_HINT.search(text))


def _year_from(text: Optional[str]) -> Optional[int]:
    m = re.search(r"\b(19|20)\d{2}\b", text or "")
    return int(m.group(0)) if m else None


DISTRICTS = {"dcad": "DCAD", "tad": "TAD"}


def _district(text: Optional[str]) -> Optional[str]:
    """Source strings are "<provider key> (<basis>)"; the key names the district."""
    key = (text or "").split(" (")[0].strip().lower()
    if key in DISTRICTS:
        return DISTRICTS[key]
    for k, name in DISTRICTS.items():
        if re.search(r"\b%s\b" % name, text or ""):
            return name
    return None


def view(prop) -> Dict[str, Any]:
    """{market_value, market_value_source, appraisal: {...} | None}."""
    appraisal = None
    av = getattr(prop, "appraisal_value", None)
    if av is not None:
        appraisal = {"value": int(av), "year": getattr(prop, "appraisal_year", None),
                     "land": getattr(prop, "appraisal_land_value", None),
                     "improvements": getattr(prop, "appraisal_improvement_value", None),
                     "source": getattr(prop, "appraisal_source", None),
                     "district": _district(getattr(prop, "appraisal_source", None)),
                     "at": getattr(prop, "appraisal_at", None)}
    market, market_src = prop.estimated_value, prop.estimated_value_source
    if market is not None and is_appraisal_basis(market_src):
        # A pre-split row: the "estimated value" IS an appraisal district value.
        if appraisal is None:
            appraisal = {"value": int(market), "year": _year_from(market_src), "land": None,
                         "improvements": None, "source": market_src, "district": _district(market_src),
                         "at": getattr(prop, "estimated_value_at", None)}
        market, market_src = None, None
    if appraisal is not None:
        appraisal["label"] = "%s%s" % (APPRAISAL_LABEL, (" (%s %s)" % (appraisal["district"], appraisal["year"])
                                                        if appraisal.get("district") and appraisal.get("year") else ""))
    return {"market_value": market, "market_value_source": market_src, "appraisal": appraisal}


def market_value(prop) -> Optional[int]:
    return view(prop)["market_value"]


def appraisal_value(prop) -> Optional[int]:
    a = view(prop)["appraisal"]
    return a["value"] if a else None


def arv(db, prop) -> Dict[str, Any]:
    """EvoSense's ARV: the ARV ENGINE over the ELIGIBLE closed sales on file
    for this property (wholesale_comps with evosense_property_id), under the
    workspace's comp rules. Neither the appraisal district's tax value nor a
    modelled estimate is ever an ARV; with too few eligible closed sales the
    answer is INSUFFICIENT COMPARABLE SALES."""
    insufficient = {"value": None, "status": "insufficient", "label": ARV_INSUFFICIENT,
                    "method": None, "comp_count": 0,
                    "why": "No verified comparable closed sales are on file for this property. "
                           "An appraisal district tax value or a modelled estimate is never used as ARV."}
    try:
        from app.models.wholesale_models import WholesaleComp
        from app.services import arv_engine, comp_rules
        from app.services import wholesale_sms as WS
        comps = (db.query(WholesaleComp)
                 .filter(WholesaleComp.organization_id == prop.organization_id,
                         WholesaleComp.evosense_property_id == prop.id).all())
        if not comps:
            return insufficient
        rules = comp_rules.rules_for(WS.settings_row(db, prop.organization_id))
        res = arv_engine.compute(prop, comps, rules)
    except Exception:  # noqa: BLE001 - an ARV question never breaks a page
        return insufficient
    if res["status"] != arv_engine.ESTIMATED:
        return dict(insufficient, comp_count=len(res["comps_used"]), engine=res,
                    why="%s eligible closed sale(s) on file; %s needed. %s" % (
                        len(res["comps_used"]), res["rules"].get("min_comps"), insufficient["why"]))
    return {"value": res["value"], "status": "estimated", "label": res["label"],
            "method": "%s:%s" % (res["version"], res["method"]),
            "comp_count": len(res["comps_used"]), "confidence": res["confidence"]["label"],
            "why": "; ".join(res["limitations"]), "engine": res}
