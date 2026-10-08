"""Gathers already-scoped facts for `compensation_projection_truth.decide`.

READ-ONLY. No commit, no add. The brand is passed in by the router after the
authority check; every query below is filtered by it, so a cross-tenant id
cannot match a row.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.models.compensation_models import CompensationEntry
from app.models.sales_models import Opportunity
from app.services import compensation as comp
from app.services import compensation_projection_truth as truth
from app.services import deal_pricing as dp
from app.services import pipeline_projection as pp


def build(db: Session, brand_sales_org_id: str, *,
          payee_user_id: Optional[str] = None,
          stage: Optional[str] = None,
          now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.utcnow()
    opps = (db.query(Opportunity)
            .filter(Opportunity.brand_sales_org_id == brand_sales_org_id,
                    Opportunity.status == "open")
            .order_by(Opportunity.id.asc()).all())
    deals = []
    for opp in opps:
        econ = comp.deal_economics(db, opp)
        res = econ["pricing"]
        c = comp.compute(db, opp)
        deals.append({
            "opportunity_id": opp.id,
            "brand_sales_org_id": opp.brand_sales_org_id,
            "owner_user_id": opp.owner_user_id,
            "company_name": opp.company_name,
            "stage": opp.stage,
            "fixed_value": dp.fixed_contract_value(res),
            "pricing_complete": bool(res["pricing_complete"]),
            "comp_status": c["status"],
            "plan_configured": c["status"] != truth.COMP_UNCONFIGURED,
            "plan_name": c.get("plan_name"),
            "capped": c.get("capped", False),
            "cap_amount": c.get("cap_amount"),
            "payouts": [{"payee_user_id": p["payee_user_id"],
                         "payee_kind": p["payee_kind"],
                         "amount": p["amount"], "rule_id": p["rule_id"],
                         "basis": p["basis"],
                         "rate_percent": p["rate_percent"]}
                        for p in c["payouts"]],
        })
    rows = (db.query(CompensationEntry)
            .filter(CompensationEntry.brand_sales_org_id == brand_sales_org_id)
            .order_by(CompensationEntry.id.asc()).all())
    entries = [{"entry_id": e.id,
                "brand_sales_org_id": e.brand_sales_org_id,
                "opportunity_id": e.opportunity_id,
                "payee_user_id": e.payee_user_id,
                "amount": e.amount, "state": e.state,
                "payable_at": e.payable_at,
                "collection_reference": e.collection_reference}
               for e in rows]
    return truth.decide(brand_sales_org_id=brand_sales_org_id, deals=deals,
                        entries=entries,
                        probabilities=pp.probabilities_for(db, brand_sales_org_id),
                        now=now, payee_user_id=payee_user_id, stage=stage)
