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

THE MONEY IS REPORTED IN THE SHAPES IT TAKES, NOT AS ONE NUMBER. This module
used to return a single `pipeline_value`, and in production that figure was
identical to the sum of the setup fees while recurring revenue read $0 — not
because the pipeline had none, but because a month-to-month deal has no
contract total and there was nowhere else to put its monthly rate. One-time
cash, committed recurring revenue and an open-ended monthly rate are now three
separate figures, with a fourth count for the deals whose terms nobody can
establish. See `deal_pricing` for how each deal is resolved and why unknown is
never reported as zero.
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
from app.services import deal_pricing as dp

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
    pipeline_monthly = ZERO
    pipeline_tcv = ZERO
    weighted_tcv = ZERO
    weighted_known = False

    direct_comp = ZERO
    override_comp = ZERO
    pending_approval_comp = ZERO

    unconfigured_plan = False
    counted = 0
    pending_deals = 0
    incomplete_deals = 0
    m2m_deals = 0
    term_deals = 0
    deals: List[Dict[str, Any]] = []

    for opp in opportunities:
        econ = comp.deal_economics(db, opp)
        res = econ["pricing"]

        # FOUR SEPARATE BUCKETS, because they are four different promises and
        # adding them together is what produced a headline nobody could act on.
        #
        #   one_time   billed once, and committed the moment the deal is won
        #   rcv        committed monthly revenue, and only where a TERM exists
        #   mrr        a monthly rate with NO end date - real revenue, but not
        #              a total, so it is reported per month and never
        #              multiplied by a term nobody agreed to
        #   unknown    a deal whose recurring terms cannot be established. Held
        #              OUT of the recurring totals and counted separately,
        #              rather than folded in at zero, which would read as "this
        #              deal has no recurring revenue" when the truth is "nobody
        #              can say what it has".
        one_time = dp.one_time_value(res) or ZERO
        rcv = res["recurring_contract_value"] or ZERO
        mrr = res["mrr"]

        pipeline_setup += one_time
        pipeline_recurring += rcv

        if res["structure"] == dp.STRUCTURE_TERM:
            term_deals += 1
        elif res["structure"] == dp.STRUCTURE_M2M and mrr is not None:
            pipeline_monthly += mrr
            m2m_deals += 1

        if not res["pricing_complete"]:
            incomplete_deals += 1

        # The only figure that is a contractual TOTAL: what is owed if the deal
        # closes on today's terms. A month-to-month deal contributes its setup
        # fee and no more.
        fixed_value = dp.fixed_contract_value(res) or ZERO
        pipeline_tcv += fixed_value
        counted += 1

        pct = probs.get(opp.stage)
        if pct is not None:
            weighted_known = True
            weighted_tcv += fixed_value * (pct / Decimal(100))

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
            # THE AUDIT ROW. Every figure in the headline is the sum of this
            # column across these rows, and each row names the source it was
            # derived from — so a total somebody disputes can be traced to the
            # deal and the evidence behind it without opening the database.
            deals.append({
                "opportunity_id": opp.id,
                "company_name": opp.company_name,
                "owner_user_id": opp.owner_user_id,
                "stage": opp.stage,
                "deal_kind": econ["deal_kind"],
                "package_name": res["package_name"],
                "pricing_source": res["source"],
                "pricing_source_label": res["source_label"],
                "proposal_number": res["proposal_number"],
                "structure": res["structure"],
                "structure_label": res["structure_label"],
                "implementation_fee": _money(res["implementation_fee"]),
                "legacy_one_time_value": _money(res["legacy_one_time_value"]),
                "one_time_value": _money(dp.one_time_value(res)),
                "mrr": _money(res["mrr"]),
                "term_months": res["term_months"],
                "recurring_contract_value": _money(res["recurring_contract_value"]),
                "total_contract_value": _money(res["total_contract_value"]),
                "fixed_contract_value": _money(dp.fixed_contract_value(res)),
                "has_fixed_tcv": res["recurring_contract_value"] is not None,
                "pricing_complete": res["pricing_complete"],
                "incomplete_reason": res["incomplete_reason"],
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

        # ── The money, in the shapes it actually takes ───────────────────────
        # There is deliberately no single "pipeline value" that management can
        # read without knowing which of these it is looking at. One-time cash,
        # committed recurring revenue and an open-ended monthly rate are three
        # different promises with three different risks, and the previous
        # payload collapsed them into one figure that was, in practice, just
        # the sum of the setup fees.
        "pipeline_implementation": _money(pipeline_setup),
        "pipeline_recurring_contract_value": _money(pipeline_recurring),
        "pipeline_monthly_recurring": _money(pipeline_monthly),
        "month_to_month_deal_count": m2m_deals,
        "fixed_term_deal_count": term_deals,

        # Setup + committed recurring. The whole of what is contractually owed
        # if every open deal closes on today's terms, and the successor to the
        # old `pipeline_value` — kept under both names so nothing that already
        # reads the old key breaks.
        "pipeline_total_fixed_contract_value": _money(pipeline_tcv),
        "pipeline_value": _money(pipeline_tcv),

        # Deals whose recurring terms could not be established. Their one-time
        # value is counted; their recurring value is EXCLUDED rather than
        # assumed to be zero, and this count is how a reader knows the
        # recurring total is a floor rather than a fact.
        "pricing_incomplete_count": incomplete_deals,

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
