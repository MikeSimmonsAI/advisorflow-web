"""Preliminary deal economics — honest about what is NOT known yet.

`wholesale_analysis.calculate_offer` is the only MAO formula in the platform.
EvoSense calls it only when BOTH of its load-bearing inputs are verified:

    ARV       from verified comparable CLOSED sales (valuation.arv). An
              appraisal district TAX VALUE is shown for reference and is never
              an ARV; neither is a modelled market estimate. With no verified
              comps the ARV is "Insufficient comparable sales".
    REPAIRS   an explicit repair assumption. A per-square-foot band from the
              SELLER-STATED condition is shown as a SYSTEM ESTIMATE; with no
              stated condition there is no repair number.

Without a verified ARV there is NO actionable MAO - not a number with a
warning under it, no number at all. These figures are internal. They never
leave for a seller, a buyer or the Investor Deal Room, and EvoSense never
sends an offer.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from app.models.evosense_models import EvoSenseFact
from app.services import wholesale_analysis
from app.services.evosense import common as C
from app.services.evosense import valuation as VAL

REPAIR_PER_SQFT = {"excellent": 0, "good": 5, "fair": 12, "poor": 25, "distressed": 45}

MAO_BLOCKED_LABEL = "NOT CALCULATED — requires a verified ARV and repair assumptions"


def _fact(db, prop, ftype) -> Optional[EvoSenseFact]:
    return (db.query(EvoSenseFact)
            .filter(EvoSenseFact.organization_id == prop.organization_id,
                    EvoSenseFact.property_id == prop.id, EvoSenseFact.fact_type == ftype,
                    EvoSenseFact.superseded.is_(False))
            .order_by(EvoSenseFact.created_at.desc()).first())


def preliminary(db, prop) -> Dict[str, Any]:
    from app.services.wholesale_service import resolve_settings
    settings = resolve_settings(db, prop.organization_id, commit=False)
    cond = _fact(db, prop, "condition")
    asking = _fact(db, prop, "asking_price")
    vals = VAL.view(prop)
    appraisal = vals["appraisal"]
    arv = VAL.arv(db, prop)
    repairs = None
    repairs_basis = None
    if cond is not None and prop.square_feet and cond.value in REPAIR_PER_SQFT:
        repairs = REPAIR_PER_SQFT[cond.value] * int(prop.square_feet)
        repairs_basis = "$%s/sq ft × %s sq ft for condition “%s” (seller stated)" % (
            REPAIR_PER_SQFT[cond.value], format(int(prop.square_feet), ","), cond.value)

    mao = None
    steps, warnings, blocked = [], [], None
    if arv["value"] is None:
        blocked = "no_verified_arv"
        warnings.append("ARV: %s. No MAO is calculated without a verified ARV." % VAL.ARV_INSUFFICIENT)
    elif repairs is None:
        blocked = "no_repair_assumption"
        warnings.append("No repair assumption. No MAO is calculated until repairs are estimated.")
    else:
        calc = wholesale_analysis.calculate_offer(
            arv["value"], repairs, settings.investor_percentage, settings.default_wholesale_fee,
            getattr(settings, "transaction_cost_percent", 0), getattr(settings, "transaction_cost_flat", 0))
        mao, steps, blocked = calc.get("mao"), calc.get("steps") or [], calc.get("blocked")
        warnings.extend(calc.get("warnings") or [])

    ask = int(float(asking.value)) if asking is not None and asking.value else None
    spread = (int(mao) - ask) if (mao is not None and ask is not None) else None
    lines = [
        {"label": "Seller asking", "value": ask, "truth": C.T_SELLER_STATED if ask else C.T_MISSING,
         "truth_label": "SELLER STATED" if ask else "NOT STATED",
         "source": asking.quote if asking is not None else None},
        {"label": VAL.APPRAISAL_LABEL, "key": "appraisal_tax_value",
         "value": appraisal["value"] if appraisal else None,
         "truth": C.T_PROVIDER if appraisal else C.T_MISSING,
         "truth_label": ("APPRAISAL DISTRICT TAX VALUE — reference only, never ARV" if appraisal
                         else "NOT ON FILE"),
         "source": (appraisal or {}).get("source")},
        {"label": "ARV", "key": "arv", "value": arv["value"],
         "truth": C.T_INSUFFICIENT if arv["value"] is None else C.T_ESTIMATE,
         "truth_label": ("INSUFFICIENT COMPARABLE SALES" if arv["value"] is None
                         else "VERIFIED COMPARABLE SALES"),
         "display": VAL.ARV_INSUFFICIENT if arv["value"] is None else None,
         "source": arv["why"]},
        {"label": "Repairs", "value": repairs, "truth": C.T_ESTIMATE if repairs is not None else C.T_MISSING,
         "truth_label": "SYSTEM ESTIMATE" if repairs is not None else "MISSING — no condition stated",
         "source": repairs_basis},
        {"label": "Investor %", "value": float(settings.investor_percentage), "truth": C.T_KNOWN,
         "truth_label": "WHOLESALE SETTINGS", "source": "organization settings"},
        {"label": "Wholesale fee", "value": float(settings.default_wholesale_fee), "truth": C.T_KNOWN,
         "truth_label": "WHOLESALE SETTINGS", "source": "organization settings"},
        {"label": "Preliminary MAO", "key": "mao", "value": mao,
         "truth": C.T_ESTIMATE if mao is not None else C.T_INSUFFICIENT,
         "truth_label": "SYSTEM ESTIMATE" if mao is not None else MAO_BLOCKED_LABEL,
         "display": "Not calculated" if mao is None else None,
         "source": "wholesale_analysis.calculate_offer" if mao is not None else None},
    ]
    if spread is not None:
        verdict = ("The seller's number is inside the preliminary MAO by $%s. Verify ARV and repairs before any offer."
                   % format(spread, ",")) if spread >= 0 else \
            "The seller's number is $%s above the preliminary MAO." % format(-spread, ",")
    elif arv["value"] is None:
        verdict = "Not enough to judge: no verified ARV (%s)." % VAL.ARV_INSUFFICIENT.lower()
    else:
        verdict = "Not enough to judge yet."
    return {"lines": lines, "mao": mao, "asking": ask, "spread": spread, "verdict": verdict,
            "arv": arv, "appraisal": appraisal and {k: appraisal[k] for k in ("value", "year", "label",
                                                                             "source", "district")},
            "market_estimate": ({"value": vals["market_value"], "source": vals["market_value_source"],
                                 "truth_label": "MARKET ESTIMATE (modelled) — not ARV"}
                                if vals["market_value"] is not None else None),
            "steps": steps, "warnings": warnings, "blocked": blocked,
            "notice": "Preliminary and internal. EvoSense never sends an offer; offers are made "
                      "by a person from the Wholesale deal after comps."}


def acquisition_cost(db, prop) -> Dict[str, Any]:
    """"How much did it cost EvoSys to discover and qualify this opportunity?"

    Every cent comes from the cost ledger (evosense_cost_ledger), which is the
    only place money is recorded: charged lookups count; refunded failures do
    not. Each paid decision also says what it did to Contact Confidence."""
    from app.models.evosense_models import EvoSenseCostEntry, EvoSenseEnrichmentDecision
    rows = (db.query(EvoSenseCostEntry)
            .filter(EvoSenseCostEntry.organization_id == prop.organization_id,
                    EvoSenseCostEntry.property_id == prop.id).all())
    charged = [r for r in rows if r.status in ("charged", "failed_charged")]
    by_cap: Dict[str, int] = {}
    by_provider: Dict[str, int] = {}
    for r in charged:
        by_cap[r.capability] = by_cap.get(r.capability, 0) + (r.total_cents or 0)
        by_provider[r.provider_key] = by_provider.get(r.provider_key, 0) + (r.total_cents or 0)
    decisions = (db.query(EvoSenseEnrichmentDecision)
                 .filter(EvoSenseEnrichmentDecision.organization_id == prop.organization_id,
                         EvoSenseEnrichmentDecision.property_id == prop.id)
                 .order_by(EvoSenseEnrichmentDecision.created_at.asc()).all())
    paid = [d for d in decisions if d.decision == C.D_PAID]
    total = sum(r.total_cents or 0 for r in charged)
    return {
        "total_cents": total,
        "total": C.money(total),
        "charged_lookups": len(charged),
        "refunded_lookups": len([r for r in rows if r.status == "failed_refunded"]),
        "reserved_cents": sum(r.total_cents or 0 for r in rows if r.status == "reserved"),
        "by_capability": by_cap,
        "by_provider": by_provider,
        "decisions": len(decisions),
        "paid_decisions": [{"at": d.created_at.isoformat() if d.created_at else None,
                            "policy": d.policy, "provider": d.provider_key,
                            "estimated_cents": d.estimated_cost_cents, "outcome": d.outcome,
                            "opportunity_score": d.opportunity_score,
                            "confidence_before": d.confidence_before,
                            "confidence_after": d.confidence_after} for d in paid],
        "is_test": bool(prop.is_test),
    }
