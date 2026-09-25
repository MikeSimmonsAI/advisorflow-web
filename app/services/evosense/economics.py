"""Preliminary deal economics — through the EXISTING wholesale analyzer.

`wholesale_analysis.calculate_offer` is the only MAO formula in the platform,
and the organization's own Wholesale Settings (investor %, fee, transaction
costs) are its inputs. EvoSense adds nothing to the formula. What it adds is
honesty about the inputs, before any comps exist:

    ARV      the provider's value estimate, labelled SYSTEM ESTIMATE /
             PROVIDER REPORTED — it is not an ARV from comps, and says so
    REPAIRS  a per-square-foot band from the SELLER-STATED condition, labelled
             SYSTEM ESTIMATE; with no condition stated there is no repair number
             and the analyzer's own "repairs unknown" warning shows
    ASKING   SELLER STATED, with the quote

These numbers are internal. They never leave for a seller, a buyer or the
Investor Deal Room, and EvoSense never sends an offer.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from app.models.evosense_models import EvoSenseFact
from app.services import wholesale_analysis
from app.services.evosense import common as C

REPAIR_PER_SQFT = {"excellent": 0, "good": 5, "fair": 12, "poor": 25, "distressed": 45}


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
    arv = prop.estimated_value
    repairs = None
    repairs_basis = None
    if cond is not None and prop.square_feet and cond.value in REPAIR_PER_SQFT:
        repairs = REPAIR_PER_SQFT[cond.value] * int(prop.square_feet)
        repairs_basis = "$%s/sq ft × %s sq ft for condition “%s” (seller stated)" % (
            REPAIR_PER_SQFT[cond.value], format(int(prop.square_feet), ","), cond.value)
    calc = wholesale_analysis.calculate_offer(
        arv, repairs, settings.investor_percentage, settings.default_wholesale_fee,
        getattr(settings, "transaction_cost_percent", 0), getattr(settings, "transaction_cost_flat", 0))
    mao = calc.get("mao")
    ask = int(float(asking.value)) if asking is not None and asking.value else None
    spread = (int(mao) - ask) if (mao is not None and ask is not None) else None
    lines = [
        {"label": "Value (used as ARV)", "value": arv, "truth": C.T_PROVIDER if arv else C.T_MISSING,
         "truth_label": ("PROVIDER REPORTED — not an ARV from comps" if arv else "MISSING"),
         "source": prop.estimated_value_source},
        {"label": "Repairs", "value": repairs, "truth": C.T_ESTIMATE if repairs is not None else C.T_MISSING,
         "truth_label": "SYSTEM ESTIMATE" if repairs is not None else "MISSING — no condition stated",
         "source": repairs_basis},
        {"label": "Investor %", "value": float(settings.investor_percentage), "truth": C.T_KNOWN,
         "truth_label": "WHOLESALE SETTINGS", "source": "organization settings"},
        {"label": "Wholesale fee", "value": float(settings.default_wholesale_fee), "truth": C.T_KNOWN,
         "truth_label": "WHOLESALE SETTINGS", "source": "organization settings"},
        {"label": "Preliminary MAO", "value": mao, "truth": C.T_ESTIMATE if mao is not None else C.T_INSUFFICIENT,
         "truth_label": "SYSTEM ESTIMATE" if mao is not None else "INSUFFICIENT EVIDENCE",
         "source": "wholesale_analysis.calculate_offer"},
        {"label": "Seller asking", "value": ask, "truth": C.T_SELLER_STATED if ask else C.T_MISSING,
         "truth_label": "SELLER STATED" if ask else "NOT STATED",
         "source": asking.quote if asking is not None else None},
    ]
    if spread is None:
        verdict = "Not enough to judge yet."
    elif spread >= 0:
        verdict = "The seller's number is inside the preliminary MAO by $%s. Verify ARV and repairs before any offer." % format(spread, ",")
    else:
        verdict = "The seller's number is $%s above the preliminary MAO." % format(-spread, ",")
    return {"lines": lines, "mao": mao, "asking": ask, "spread": spread, "verdict": verdict,
            "steps": calc.get("steps"), "warnings": calc.get("warnings") or [],
            "blocked": calc.get("blocked"),
            "notice": "Preliminary and internal. EvoSense never sends an offer; offers are made "
                      "by a person from the Wholesale deal after comps."}
