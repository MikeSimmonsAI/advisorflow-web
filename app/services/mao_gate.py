"""THE MAO GATE - an actionable maximum allowable offer only on real evidence.

An ARV field having a number is not enough. Before calculating, the gate
requires (each configurable per workspace, WholesaleSettings.mao_policy):

  * an EVIDENCE-BACKED ARV: from the ARV engine at or above the minimum
    confidence, or entered/verified by a person (their responsibility, and
    labelled so) - never a tax value, an AVM or a list price
  * a REPAIR ESTIMATE whose status the workspace accepts - never an assumed $0
  * the investor formula: investor percentage and wholesale fee configured

Otherwise:  status = NOT_CALCULATED, mao = None, with every reason listed.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from app.services import wholesale_repairs as R

DEFAULT_POLICY = {
    "min_arv_confidence": "medium",               # high | medium | low
    "allow_person_entered_arv": True,
    "require_repairs": True,
    "accepted_repair_statuses": [R.MANUAL_ESTIMATE, R.INSPECTION_ESTIMATE, R.VERIFIED],
}
_RANK = {"low": 1, "medium": 2, "high": 3}

CALCULATED, NOT_CALCULATED = "CALCULATED", "NOT_CALCULATED"


def policy_for(settings) -> Dict[str, Any]:
    out = dict(DEFAULT_POLICY)
    raw = getattr(settings, "mao_policy", None)
    if raw:
        try:
            given = json.loads(raw)
            if isinstance(given, dict):
                out.update({k: v for k, v in given.items() if k in DEFAULT_POLICY})
        except (ValueError, TypeError):
            pass
    return out


def validate_policy(value) -> Dict[str, Any]:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("The MAO policy must be an object.")
    clean = {}
    for k, v in value.items():
        if k not in DEFAULT_POLICY:
            raise ValueError("Unknown MAO policy setting: %s." % k)
        if k == "min_arv_confidence":
            if v not in _RANK:
                raise ValueError("Minimum ARV confidence must be high, medium or low.")
            clean[k] = v
        elif k == "accepted_repair_statuses":
            if not isinstance(v, list) or any(s not in R.STATUSES or s == R.UNKNOWN for s in v):
                raise ValueError("Accepted repair statuses must be from: %s." % ", ".join(R.STATUSES[1:]))
            clean[k] = v
        else:
            clean[k] = bool(v)
    return clean


def evaluate(deal, settings) -> Dict[str, Any]:
    pol = policy_for(settings)
    reasons: List[str] = []
    checks: List[Dict[str, Any]] = []

    # 1. ARV evidence
    src = getattr(deal, "arv_source", None)
    conf = getattr(deal, "arv_confidence", None)
    if getattr(deal, "arv", None) is None:
        reasons.append("No ARV: %s" % ("insufficient comparable sales"
                                       if conf == "insufficient" else "not established"))
        checks.append({"code": "ARV", "ok": False})
    elif src in ("manual", "verified"):
        ok = bool(pol["allow_person_entered_arv"])
        checks.append({"code": "ARV", "ok": ok, "basis": "entered by a person (%s)" % src})
        if not ok:
            reasons.append("This workspace does not accept a person-entered ARV for an MAO")
    elif src == "estimated" and (getattr(deal, "arv_version", None) or "").startswith("arv/"):
        ok = _RANK.get(conf or "", 0) >= _RANK[pol["min_arv_confidence"]]
        checks.append({"code": "ARV", "ok": ok, "basis": "comparable sales, %s confidence" % conf})
        if not ok:
            reasons.append("ARV confidence is %s; the workspace requires %s"
                           % (conf or "unknown", pol["min_arv_confidence"]))
    else:
        checks.append({"code": "ARV", "ok": False, "basis": src})
        reasons.append("The ARV is not evidence-backed (source: %s)" % (src or "unknown"))

    # 2. Repairs
    rst = R.status_of(deal)
    if pol["require_repairs"]:
        ok = rst in pol["accepted_repair_statuses"] and getattr(deal, "repair_estimate", None) is not None
        checks.append({"code": "REPAIRS", "ok": ok, "basis": R.LABELS[rst]})
        if not ok:
            reasons.append("Repairs are %s; the formula needs one of: %s" % (
                R.LABELS[rst].lower(), ", ".join(R.LABELS[s].lower()
                                                 for s in pol["accepted_repair_statuses"])))

    # 3. Formula
    pct = getattr(deal, "investor_percentage_used", None) or getattr(settings, "investor_percentage", None)
    fee = getattr(deal, "desired_wholesale_fee", None)
    if fee is None:
        fee = getattr(settings, "default_wholesale_fee", None)
    ok = pct is not None and fee is not None
    checks.append({"code": "FORMULA", "ok": ok})
    if not ok:
        reasons.append("The investor percentage or wholesale fee is not configured")

    status = CALCULATED if not reasons else NOT_CALCULATED
    return {"status": status, "reasons": reasons, "checks": checks, "policy": pol,
            "label": "MAO calculated" if status == CALCULATED else
            "MAO NOT CALCULATED - " + "; ".join(reasons)}
