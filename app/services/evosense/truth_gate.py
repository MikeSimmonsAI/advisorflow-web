"""THE DFW SOLD-COMPS TRUTH GATE.

A comps provider does not become production-ready because its API returns a
field named soldPrice, lastSaleAmount or mlsSoldPrice. Before a provider can
turn SOLD_COMPS operational, EvoSys must be able to establish every one of:

    PRICE_MEANING   what the Texas price actually represents           attested
    CLOSED_SALE     that it is an actual closed-sale price              attested
    ORIGIN          where it comes from: MLS or public record          attested
                    (a modelled / estimated price fails the gate)
    COVERAGE        acceptable Dallas + Tarrant coverage                measured
    FRESHNESS       acceptable freshness                                measured
    STORAGE_RIGHT   our contractual right to store it                   attested
    DISPLAY_RIGHT   our contractual right to display / use it inside    attested
                    the multi-tenant, white-label product

ATTESTED criteria are recorded by a workspace admin WITH EVIDENCE (a written
vendor statement, contract clause or document reference) - a checkbox without
evidence is refused. MEASURED criteria come only from a completed REAL (never
synthetic) comps evaluation of that provider on Dallas / Tarrant properties.

Until every criterion is met, route() never sends a COMPS call to the
provider and the Capability Registry shows SOLD_COMPS as not operational -
MANUAL ONLY / NOT CONFIGURED. Passing the gate does not activate anything by
itself: the vendor adapter also stays EVALUATION ONLY until Mike approves it.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from app.services.evosense import common as C

ATTESTED = {
    "PRICE_MEANING": "What the Texas price represents, in the vendor's own written words",
    "CLOSED_SALE": "The price is an actual closed-sale price (not estimated, list or AVM)",
    "ORIGIN": "Where the price comes from (MLS or public record)",
    "STORAGE_RIGHT": "Our contractual right to store the data",
    "DISPLAY_RIGHT": "Our contractual right to display / use it in the multi-tenant white-label product",
}
MEASURED = {
    "COVERAGE": "Dallas + Tarrant coverage: >= %d%% of subjects reach 3 eligible comps and >= %d%% of "
                "comps carry a closed price",
    "FRESHNESS": "Median closed-sale age <= %d days",
}
ORIGINS_ALLOWED = ("MLS", "PUBLIC_RECORD")
MIN_SUBJECTS_3 = 70          # percent
MIN_CLOSED_SHARE = 80        # percent
MAX_MEDIAN_AGE_DAYS = 270
MIN_SUBJECTS = 10            # a coverage claim needs at least this many DFW subjects
DFW_COUNTIES = ("DALLAS", "TARRANT")


def _state(cfg) -> Dict[str, Any]:
    return C.jload(getattr(cfg, "truth_gate", None), {}) or {}


def attest(db, org_id: str, key: str, criterion: str, *, met: bool, evidence: str,
           value: Optional[str] = None, user=None) -> Dict[str, Any]:
    from app.services.evosense import providers as PV
    if criterion not in ATTESTED:
        raise ValueError("Unknown criterion. Attested criteria: %s." % ", ".join(ATTESTED))
    p = PV.PROVIDERS.get(key)
    if p is None or C.COMPS not in (p.capabilities or ()):
        raise ValueError("That is not a comps provider.")
    ev = (evidence or "").strip()
    if met and len(ev) < 15:
        raise ValueError("Attach the evidence: the written statement, contract clause or document reference.")
    if criterion == "ORIGIN" and met and (value or "").upper() not in ORIGINS_ALLOWED:
        raise ValueError("ORIGIN is met only by MLS or PUBLIC_RECORD; a modelled price fails the gate.")
    cfg = PV.config(db, org_id, key)
    st = _state(cfg)
    st[criterion] = {"met": bool(met), "evidence": ev[:1000], "value": (value or None),
                     "by": getattr(user, "id", None), "by_name": getattr(user, "full_name", None),
                     "at": datetime.utcnow().isoformat() + "Z"}
    cfg.truth_gate = C.jdump(st)
    C.log_event(db, org_id, "truth_gate.attested", user=user, actor_type=C.ACTOR_USER,
                summary="Truth gate %s for %s: %s" % (criterion, p.label, "MET" if met else "NOT MET"),
                details={"provider": key, "criterion": criterion, "evidence": ev[:200]})
    db.flush()
    return evaluate(db, org_id, key)


def _latest_real_evaluation(db, org_id, key) -> Optional[Dict[str, Any]]:
    from app.models.evosense_models import EvoSenseProperty, EvoSenseProviderEvaluation
    rows = (db.query(EvoSenseProviderEvaluation)
            .filter(EvoSenseProviderEvaluation.organization_id == org_id,
                    EvoSenseProviderEvaluation.synthetic.is_(False),
                    EvoSenseProviderEvaluation.status == "completed")
            .order_by(EvoSenseProviderEvaluation.finished_at.desc()).limit(20).all())
    for ev in rows:
        res = C.jload(ev.results, {}) or {}
        if res.get("mode") != "comps" or key not in (res.get("providers") or {}):
            continue
        ids = (C.jload(ev.sample, {}) or {}).get("property_ids") or []
        dfw = [p for p in db.query(EvoSenseProperty).filter(EvoSenseProperty.id.in_(ids)).all()
               if (p.county or "").upper().replace(" COUNTY", "") in DFW_COUNTIES]
        return {"id": ev.id, "at": ev.finished_at.isoformat() + "Z" if ev.finished_at else None,
                "metrics": res["providers"][key], "dfw_subjects": len(dfw)}
    return None


def evaluate(db, org_id: str, key: str) -> Dict[str, Any]:
    """Every criterion with met / not met and why; `passed` only when all are met."""
    from app.services.evosense import providers as PV
    cfg = PV.config(db, org_id, key)
    st = _state(cfg)
    crits: List[Dict[str, Any]] = []
    for code, label in ATTESTED.items():
        a = st.get(code) or {}
        crits.append({"code": code, "label": label, "kind": "attested", "met": bool(a.get("met")),
                      "evidence": a.get("evidence"), "value": a.get("value"), "by": a.get("by_name"),
                      "at": a.get("at"),
                      "why": ("attested with evidence" if a.get("met") else
                              "recorded as NOT met" if a else "no written confirmation recorded")})
    latest = _latest_real_evaluation(db, org_id, key)
    m = (latest or {}).get("metrics") or {}
    enough = bool(latest) and latest["dfw_subjects"] >= MIN_SUBJECTS
    s3, cs = m.get("subjects_with_3_eligible"), m.get("closed_price_share")
    cov_met = enough and s3 is not None and cs is not None and s3 * 100 >= MIN_SUBJECTS_3 \
        and cs * 100 >= MIN_CLOSED_SHARE
    age = m.get("median_close_age_days")
    fresh_met = enough and age is not None and age <= MAX_MEDIAN_AGE_DAYS
    base = ("from REAL evaluation %s (%s Dallas/Tarrant subjects)" % (latest["id"], latest["dfw_subjects"])
            if latest else "no completed REAL comps evaluation of this provider")
    crits.append({"code": "COVERAGE", "kind": "measured", "met": cov_met,
                  "label": MEASURED["COVERAGE"] % (MIN_SUBJECTS_3, MIN_CLOSED_SHARE),
                  "why": (base + ("; fewer than %s DFW subjects" % MIN_SUBJECTS if latest and not enough else "")
                          + ("; %s reach 3 eligible, %s closed-price" % (s3, cs) if latest else ""))})
    crits.append({"code": "FRESHNESS", "kind": "measured", "met": fresh_met,
                  "label": MEASURED["FRESHNESS"] % MAX_MEDIAN_AGE_DAYS,
                  "why": base + ("; median close age %s days" % age if latest else "")})
    passed = all(c["met"] for c in crits)
    return {"provider": key, "passed": passed, "criteria": crits,
            "missing": [c["code"] for c in crits if not c["met"]],
            "note": ("All criteria met. SOLD_COMPS can become operational for this provider only when Mike "
                     "also approves it for production." if passed else
                     "SOLD_COMPS stays MANUAL ONLY / NOT CONFIGURED for this provider until every "
                     "criterion is met.")}


def passes(db, org_id: str, key: str) -> bool:
    try:
        return evaluate(db, org_id, key)["passed"]
    except Exception:  # noqa: BLE001 - a gate that cannot be evaluated is closed
        return False
