"""WHAT THE PIPELINE IS WORTH, AND WHAT IT WOULD COST TO PAY FOR IT.

A PROJECTION IS NOT PAYROLL. Every number here describes deals that have NOT
closed. Nothing in this module writes a row, and the payload says `projected`
on every total so a screen cannot render it as money owed without deliberately
stripping the label off first.

ONE ENGINE, NOT TWO. Compensation comes from `compensation.compute()` - the
same function that pays a real commission when funds are collected. A separate
"projection formula" is exactly how a forecast and a payroll run end up
disagreeing about the same deal with nobody able to say which is wrong, so
there is not one here, and the frontend is given totals rather than rates so it
cannot grow one either.

WEIGHTED REVENUE IS UNAVAILABLE UNTIL SOMEBODY CONFIGURES IT. Stage
probabilities are a commercial judgement this codebase has never held. Rather
than apply invented odds to real money, an unconfigured brand gets `None` and a
screen that says so.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy.orm import Session

from app.models.compensation_models import (PROJ_PENDING_APPROVAL,
                                            PROJ_UNCONFIGURED,
                                            StageProbability)
from app.models.sales_models import Opportunity
from app.services import compensation as comp

ZERO = Decimal("0")


def _money(v: Optional[Decimal]) -> Optional[float]:
    return None if v is None else float(v.quantize(Decimal("0.01")))


# ── Stage probabilities ──────────────────────────────────────────────────────

def probabilities_for(db: Session, brand_sales_org_id: Optional[str]) -> Dict[str, Decimal]:
    """Configured win probabilities by stage. Empty when nobody has set any.

    A brand's own row beats the platform default for the same stage; a stage
    with no row anywhere is simply absent, and a deal at that stage contributes
    nothing to weighted revenue rather than contributing a guess.
    """
    rows = (db.query(StageProbability)
            .filter((StageProbability.brand_sales_org_id == brand_sales_org_id) |
                    (StageProbability.brand_sales_org_id.is_(None)))
            .all())
    out: Dict[str, Decimal] = {}
    for r in sorted(rows, key=lambda x: 0 if x.brand_sales_org_id is None else 1):
        out[r.stage] = Decimal(str(r.probability_pct))
    return out


# ── The rollup ───────────────────────────────────────────────────────────────

def project(db: Session, opportunities: Iterable[Opportunity], *,
            brand_sales_org_id: Optional[str] = None,
            include_deals: bool = False) -> Dict[str, Any]:
    """Roll a set of OPEN opportunities into a financial projection.

    The caller supplies the opportunities, already scoped. That is deliberate:
    scoping is a tenancy question with one existing answer per screen
    (`_scoped_opportunities`), and re-deriving it here would be a second place
    for a brand boundary to be got wrong.
    """
    probs = probabilities_for(db, brand_sales_org_id)

    pipeline_setup = ZERO
    pipeline_recurring = ZERO
    pipeline_tcv = ZERO
    weighted_tcv = ZERO
    weighted_known = False

    direct_comp = ZERO
    override_comp = ZERO
    pending_approval_comp = ZERO

    unconfigured_plan = False
    counted = 0
    pending_deals = 0
    deals: List[Dict[str, Any]] = []

    for opp in opportunities:
        econ = comp.deal_economics(db, opp)
        q = econ["quote"]
        setup = econ["implementation_fee"] or ZERO
        tcv = econ["tcv"]
        rcv = Decimal(str(q.get("recurring_contract_value") or 0))

        # A month-to-month deal has no TCV. Its contribution to "pipeline value"
        # is the one-time fee only - the same refusal to invent a contract total
        # that package_pricing makes, carried through to the rollup so a
        # forecast cannot quietly book revenue nobody committed to.
        deal_value = tcv if tcv is not None else setup

        pipeline_setup += setup
        pipeline_recurring += rcv
        pipeline_tcv += deal_value
        counted += 1

        pct = probs.get(opp.stage)
        if pct is not None:
            weighted_known = True
            weighted_tcv += deal_value * (pct / Decimal(100))

        c = comp.compute(db, opp)
        if c["status"] == PROJ_UNCONFIGURED:
            unconfigured_plan = True
        else:
            seller = c["seller_total"] or ZERO
            override = c["override_total"] or ZERO
            if c["status"] == PROJ_PENDING_APPROVAL:
                # NOT counted as expected compensation. An unapproved deal is
                # not a promise, and folding it into the headline would let a
                # rep move the company's projected payroll by typing a number.
                pending_approval_comp += (seller + override)
                pending_deals += 1
            else:
                direct_comp += seller
                override_comp += override

        if include_deals:
            deals.append({
                "opportunity_id": opp.id,
                "company_name": opp.company_name,
                "owner_user_id": opp.owner_user_id,
                "stage": opp.stage,
                "deal_kind": econ["deal_kind"],
                "implementation_fee": _money(setup),
                "mrr": _money(econ["mrr"]),
                "term_months": econ["term_months"],
                "total_contract_value": _money(tcv),
                "has_fixed_tcv": tcv is not None,
                "probability_pct": float(pct) if pct is not None else None,
                "comp_status": c["status"],
                "projected_compensation": _money(c["total"]) if c["total"] is not None else None,
            })

    total_comp = direct_comp + override_comp
    after_comp = pipeline_tcv - total_comp

    return {
        # Every consumer of this payload is looking at UNCLOSED business.
        "basis": "projected",
        "disclaimer": ("Projected from open opportunities on their current "
                       "terms. Not earned, not payable, and not owed."),
        "opportunity_count": counted,

        "pipeline_implementation": _money(pipeline_setup),
        "pipeline_recurring_contract_value": _money(pipeline_recurring),
        "pipeline_value": _money(pipeline_tcv),

        # None, not zero, when nobody has configured probabilities. A zero here
        # would read as "this pipeline is worth nothing".
        "weighted_pipeline_value": _money(weighted_tcv) if weighted_known else None,
        "weighted_available": weighted_known,

        "projected_direct_commissions": _money(direct_comp),
        "projected_manager_overrides": _money(override_comp),
        "projected_total_compensation": _money(total_comp),
        "projected_revenue_after_compensation": _money(after_comp),

        # Held out of the totals above, reported separately and loudly.
        "pending_approval_compensation": _money(pending_approval_comp),
        "pending_approval_deal_count": pending_deals,

        "compensation_plan_configured": not unconfigured_plan,
        "deals": deals if include_deals else None,
    }
