"""UNIFIED DATA VIEW (R5) - one read of every data source and every fact on a deal.

Nothing new is fetched and nothing is bought. This normalizes what the existing
layers already record so the Wholesale screens can show, for any number:
source, as-of time, confidence, cost class, error state, retry, caching.

The pieces it reads (all pre-existing):
  evosense.providers   registry, health state, costs, last success/failure, degraded_until
  evosense.ingest      FACT PRECEDENCE: a value is replaced only by a HIGHER-ranked source
                       (person 100 > county/assessor records 60 > provider feed 40 > file 30);
                       an equal or lower source with a different value becomes a CONFLICT
  evosense.truth_gate  sold-comps provider gate (no provider comps until every criterion is met)
  evosense.sources.base  bulk files cached on disk, size-capped; rate limits surfaced, never looped
  wholesale_buyer_finder county-roll scans cached per county with their run time
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

COST_CLASS = {"real": None, "sandbox": "sandbox (no charge)", "manual": "free (a person)",
              "import": "free (your file)", "interface_only": "not connected", "not_built": "not built"}
CONFIDENCE = {100: "high (entered or checked by a person)", 60: "high (official county / assessor record)",
              40: "medium (provider feed)", 30: "medium (imported file)"}


def _rank(p) -> int:
    from app.services.evosense import common as C
    from app.services.evosense import ingest as ING
    if p.connector_kind == C.MANUAL:
        return ING.RANK_HUMAN
    if p.connector_kind == C.IMPORT:
        return ING.RANK_IMPORT
    if set(p.capabilities) & {C.PROPERTY_SEARCH, C.ASSESSOR, C.PARCEL, C.OWNERSHIP}:
        return ING.RANK_RECORDS
    return ING.RANK_FEED


def sources(db, org_id: str) -> Dict[str, Any]:
    from app.services import wholesale_buyer_finder as finder
    from app.services.evosense import providers as PR
    out: List[Dict[str, Any]] = []
    for key, p in PR.PROVIDERS.items():
        try:
            row = PR._provider_row(db, org_id, key)
        except Exception as exc:  # one broken row must not hide the rest
            out.append({"key": key, "label": getattr(p, "label", key), "error_state": "REPORT_FAILED: %s" % exc})
            continue
        paid = bool(row.get("costs"))
        rank = _rank(p)
        out.append({
            "key": key, "label": row["label"], "kind": row["connector_label"],
            "capabilities": row["capabilities"], "coverage": row["coverage"],
            "state": row["state"], "why": row["why"],
            "confidence_rank": rank, "confidence": CONFIDENCE.get(rank, "medium"),
            "cost_class": ("paid - %s" % row["cost"]) if paid and p.connector_kind == "real"
                          else (COST_CLASS.get(p.connector_kind) or ("free public record" if not paid else row["cost"])),
            "needs_approval_to_spend": paid and p.connector_kind == "real",
            "last_success_at": row["last_success_at"], "last_attempt_at": row["last_attempt_at"],
            "error_state": ({"reason": row["last_failure_reason"], "at": row["last_failure_at"],
                             "consecutive_failures": row["consecutive_failures"]}
                            if row["consecutive_failures"] else None),
            "retry": ({"after": row["degraded_until"], "rule": "paused until then; never retried in a loop"}
                      if row["degraded_until"] else None),
            "caching": {"fresh_for_days": row["freshness_days"],
                        "bulk_files": "cached on disk, size-capped (EVOSENSE_SOURCE_MAX_BYTES)"},
            "records_last_run": row["last_record_count"],
        })
    rolls = []
    for county in finder.COUNTIES:
        try:
            st = finder.status(county) or {}
        except Exception as exc:
            st = {"state": "error", "error": str(exc)}
        rolls.append({"key": "county_roll:%s" % county, "label": "%s County appraisal roll (buyer finder)" % county.title(),
                      "kind": "PUBLIC RECORD", "confidence": CONFIDENCE[60], "cost_class": "free public record",
                      "state": st.get("state") or "idle", "last_success_at": st.get("last_result_at"),
                      "records_last_run": st.get("last_count"), "error_state": st.get("error"),
                      "caching": {"cached_result": bool(st.get("last_result_at"))}})
    return {"sources": out, "buyer_rolls": rolls,
            "precedence": [{"rank": 100, "who": "A person (manual entry or correction)"},
                           {"rank": 60, "who": "County / assessor / parcel / ownership records"},
                           {"rank": 40, "who": "Other provider feeds"},
                           {"rank": 30, "who": "Imported files"}],
            "rule": "A value is replaced only by a higher-ranked source. A different value from an equal or "
                    "lower source is kept as a conflict for a person to resolve - never silently overwritten.",
            "spend_rule": "No paid lookup runs without an approval; none is configured to run on its own."}


def deal_provenance(db, org_id: str, deal) -> Dict[str, Any]:
    """Every number a decision on this deal rests on, with where it came from."""
    from app.models.wholesale_models import WholesaleComp
    from app.services import wholesale_underwriting as UW
    from app.utils.time_fmt import iso_utc
    from app.models.wholesale_models import WholesaleProperty
    prop = db.query(WholesaleProperty).filter(WholesaleProperty.id == deal.property_id).first()
    prof = UW._profile(db, org_id, deal, prop)
    facts: List[Dict[str, Any]] = []

    def add(label, value, source, as_of=None, confidence=None, verified=None, note=None):
        facts.append({"fact": label, "value": value, "source": source or "not on file", "as_of": as_of,
                      "confidence": confidence, "verified": verified, "note": note})

    add("After-repair value", float(deal.arv) if deal.arv is not None else None, deal.arv_source,
        iso_utc(getattr(deal, "analysis_updated_at", None)), deal.arv_method,
        verified=(deal.arv_source == "verified"),
        note="Tax values, AVMs and list prices are never used as the ARV.")
    add("Repair estimate", float(deal.repair_estimate) if deal.repair_estimate is not None else None,
        getattr(deal, "repair_status", None) or deal.repair_estimate_source, None, None,
        verified=(getattr(deal, "repair_status", None) == "VERIFIED"))
    add("Max offer (MAO)", float(deal.max_allowable_offer) if deal.max_allowable_offer is not None else None,
        "calculated from the ARV and repairs above", None, None, verified=None)
    comps = db.query(WholesaleComp).filter(WholesaleComp.organization_id == org_id, WholesaleComp.deal_id == deal.id).all()
    by_state: Dict[str, int] = {}
    for c in comps:
        by_state[c.verification_state or "unknown"] = by_state.get(c.verification_state or "unknown", 0) + 1
    add("Comparable sales", len(comps), ", ".join("%d %s" % (n, k) for k, n in sorted(by_state.items())) or None,
        None, None, verified=bool(comps) and set(by_state) <= {"human_verified", "verified"})
    for label, key, src_key in (("Tax value", "tax_value", "tax_value_source"),
                                ("Mortgage balance", "mortgage_balance", "mortgage_source"),
                                ("Owner of record", "owner", None), ("Living area", "square_feet", None),
                                ("Year built", "year_built", None)):
        v = prof.get(key)
        add(label, v, prof.get(src_key) if src_key else ", ".join(prof.get("sources") or []) or None,
            prof.get("last_verified"), None, verified=None)
    fc = prof.get("foreclosure") or {}
    add("Foreclosure", fc.get("status"), ("county clerk notice %s" % fc["document"]) if fc.get("document") else None,
        None, None, verified=bool(fc.get("document")))
    return {"deal_id": deal.id, "facts": facts,
            "rule": "Verified facts are never overwritten by a lower-confidence source; disagreements are kept as conflicts."}
