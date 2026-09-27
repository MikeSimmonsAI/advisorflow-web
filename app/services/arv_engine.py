"""THE ARV ENGINE - an evidence-backed after-repair value, or an honest "no".

    ARV = median price per sq ft of the ELIGIBLE closed sales x subject sq ft
    (median sale price when the subject's size is unknown - a cruder method,
    and the confidence says so)

Only comps that pass comp_rules count. Fewer eligible comps than the
workspace's minimum gives:

    status = INSUFFICIENT, value = None, label "INSUFFICIENT COMPARABLE SALES"

which is a valid, stored result - never a fallback number. The engine NEVER
reads a tax/appraisal value, an assessed value, a provider AVM or a list
price. Those are shown beside the ARV, each labelled with what it is
(`other_valuations`), and never become the ARV.

CONFIDENCE is explained, not asserted: each factor that raised or lowered it is
listed (count, recency, distance, similarity knowns, dispersion, source
quality, missing subject facts). Labels: high | medium | low | insufficient.
"""
from __future__ import annotations

from datetime import date, datetime
from statistics import median
from typing import Any, Dict, List, Optional

from app.services import comp_rules as CR

VERSION = "arv/v1"
VALUATION_TYPE = "ARV_SOLD_COMPS"
INSUFFICIENT = "INSUFFICIENT"
ESTIMATED = "ESTIMATED"
INSUFFICIENT_LABEL = "INSUFFICIENT COMPARABLE SALES"


def _num(v):
    return CR._num(v)


def _conf(eligible: List[Dict[str, Any]], subject, method: str, rules) -> Dict[str, Any]:
    factors: List[Dict[str, Any]] = []
    score = 0
    n = len(eligible)
    need = int(rules.get("min_comps") or 3)
    pts = 30 if n >= need + 2 else 22 if n >= need + 1 else 15
    score += pts
    factors.append({"points": pts, "label": "%s eligible closed sales (minimum %s)" % (n, need)})

    ages = [e["_age_months"] for e in eligible if e.get("_age_months") is not None]
    if ages:
        avg = sum(ages) / len(ages)
        p = 15 if avg <= 6 else 8 if avg <= 12 else 0
        score += p
        factors.append({"points": p, "label": "Average sale age %.0f months" % avg})
    dists = [e["distance_miles"] for e in eligible if e.get("distance_miles") is not None]
    if dists and len(dists) == n:
        avg = sum(dists) / n
        p = 15 if avg <= 0.5 else 8 if avg <= 1.0 else 3
        score += p
        factors.append({"points": p, "label": "Average distance %.2f miles" % avg})
    else:
        factors.append({"points": 0, "label": "Distance unknown for %s comp(s)" % (n - len(dists))})

    unknowns = sum(len(e["unknown"]) for e in eligible)
    if unknowns == 0:
        score += 15
        factors.append({"points": 15, "label": "Every similarity fact known"})
    else:
        p = max(0, 15 - 3 * unknowns)
        score += p
        factors.append({"points": p, "label": "%s unknown similarity fact(s) across the comps" % unknowns})

    psfs = [e["price_per_sqft"] for e in eligible if e.get("price_per_sqft")]
    if len(psfs) >= 2:
        mean = sum(psfs) / len(psfs)
        sd = (sum((x - mean) ** 2 for x in psfs) / (len(psfs) - 1)) ** 0.5
        cv = sd / mean if mean else 1
        p = 15 if cv <= 0.10 else 8 if cv <= 0.20 else -10
        score += p
        factors.append({"points": p, "label": "Price-per-sqft spread %.0f%%" % (cv * 100)})

    manual = sum(1 for e in eligible if e["manual"])
    verified = sum(1 for e in eligible if e.get("_verified"))
    if manual == 0:
        score += 10
        factors.append({"points": 10, "label": "All comps provider-sourced"})
    elif verified >= manual:
        score += 6
        factors.append({"points": 6, "label": "Manual comps verified by a person"})
    else:
        factors.append({"points": 0, "label": "%s manual comp(s) not yet verified by a person" % (manual - verified)})

    missing = [f for f in ("square_feet", "bedrooms", "bathrooms", "year_built")
               if _num(getattr(subject, f, None)) is None]
    if missing:
        p = -5 * len(missing)
        score += p
        factors.append({"points": p, "label": "Subject facts missing: %s" % ", ".join(
            m.replace("_", " ") for m in missing)})
    if method == "median_price":
        score -= 15
        factors.append({"points": -15, "label": "No size adjustment (subject or comp sq ft unknown)"})

    score = max(0, min(100, score))
    label = "high" if score >= 70 else "medium" if score >= 45 else "low"
    return {"score": score, "label": label,
            "factors": sorted(factors, key=lambda f: -f["points"])}


def compute(subject, comps, rules: Dict[str, Any], *, today: Optional[date] = None,
            other_valuations: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """`subject`: attributes square_feet, bedrooms, bathrooms, year_built,
    lot_size_sqft, property_type, latitude, longitude. `comps`: rows with
    WholesaleComp attributes. Pure: reads nothing, writes nothing."""
    today = today or date.today()
    seen: set = set()
    used, excluded = [], []
    for c in sorted(comps, key=lambda x: str(getattr(x, "created_at", "") or "")):
        ev = CR.evaluate(c, subject, rules, today=today, seen=seen)
        sd = CR._date(getattr(c, "sale_date", None))
        row = {"comp_id": getattr(c, "id", None),
               "address": getattr(c, "street_address", None),
               "sale_price": _num(getattr(c, "sale_price", None)),
               "sale_date": ev["sale_date"], "distance_miles": ev["distance_miles"],
               "price_per_sqft": ev["price_per_sqft"], "manual": ev["manual"],
               "origin": "MANUAL" if ev["manual"] else "PROVIDER",
               "verification": getattr(c, "verification_state", None) or ("manual" if ev["manual"] else "provider"),
               "unknown": ev["unknown"], "passed": ev["passed"], "excluded": ev["excluded"],
               "_age_months": ((today.year - sd.year) * 12 + (today.month - sd.month)) if sd else None,
               "_verified": getattr(c, "verification_state", None) in ("human_verified", "provider_verified")}
        (used if ev["eligible"] else excluded).append(row)

    need = int(rules.get("min_comps") or 3)
    subject_sqft = _num(getattr(subject, "square_feet", None))
    base = {"version": VERSION, "rules_version": CR.VERSION, "valuation_type": VALUATION_TYPE,
            "calculated_at": datetime.utcnow().isoformat() + "Z", "rules": rules,
            "comps_used": [_public(r) for r in used], "comps_excluded": [_public(r) for r in excluded],
            "other_valuations": other_valuations or {},
            "other_valuations_note": "Tax/appraisal values, assessed values, AVMs and list prices are "
                                     "shown for reference only and never used as the ARV."}
    limitations: List[str] = []
    if len(used) < need:
        return dict(base, status=INSUFFICIENT, value=None, low=None, high=None, method=None,
                    label=INSUFFICIENT_LABEL,
                    confidence={"score": None, "label": "insufficient",
                                "factors": [{"points": 0, "label": "%s eligible closed sale%s; %s needed"
                                             % (len(used), "" if len(used) == 1 else "s", need)}]},
                    limitations=["Not enough eligible closed sales to support an ARV."])

    psf = [r["price_per_sqft"] for r in used if r["price_per_sqft"]]
    if subject_sqft and len(psf) >= need:
        method = "median_psf"
        mid = median(psf)
        value = round(mid * subject_sqft, -2)
        low, high = round(min(psf) * subject_sqft, -2), round(max(psf) * subject_sqft, -2)
    else:
        method = "median_price"
        prices = [r["sale_price"] for r in used]
        value = round(median(prices), -2)
        low, high = round(min(prices), -2), round(max(prices), -2)
        limitations.append("No size adjustment: %s." % (
            "the subject's square footage is unknown" if not subject_sqft
            else "too few comps carry a square footage"))
    if any(r["manual"] for r in used):
        limitations.append("Includes MANUAL comps entered by a person; they are labelled and never "
                           "treated as provider-verified.")
    if any(r["unknown"] for r in used):
        limitations.append("Some similarity facts are unknown (see each comp).")
    limitations.append("Market-level comparable-sales estimate - not an appraisal, not an "
                       "inspection, no condition adjustments.")
    conf = _conf(used, subject, method, rules)
    return dict(base, status=ESTIMATED, value=value, low=low, high=high, method=method,
                label="ARV %s (%s confidence)" % ("${:,.0f}".format(value), conf["label"]),
                confidence=conf, limitations=limitations)


def _public(r):
    return {k: v for k, v in r.items() if not k.startswith("_")}
