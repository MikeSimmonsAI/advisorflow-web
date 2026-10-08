"""Gathers brand-scoped facts for `approval_queue_truth`. READ-ONLY.

No add, no commit. Every query filters on the brand the router already
authorised, so another brand's request id can never match a row.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.models import Proposal, User
from app.models.sales_models import Opportunity, PricingApprovalRequest


def user_names(db: Session, rows) -> Dict[str, Optional[str]]:
    ids = {x for r in rows for x in (r.requested_by, r.decided_by) if x}
    if not ids:
        return {}
    return {u.id: u.full_name for u in db.query(User).filter(User.id.in_(ids)).all()}


def facts_for(db: Session, rows, users: Dict[str, Optional[str]]) -> List[dict]:
    prop_ids = [r.proposal_id for r in rows if r.proposal_id]
    opp_ids = [r.opportunity_id for r in rows]
    props = ({p.id: p for p in db.query(Proposal).filter(Proposal.id.in_(prop_ids)).all()}
             if prop_ids else {})
    opps = ({o.id: o for o in db.query(Opportunity).filter(Opportunity.id.in_(opp_ids)).all()}
            if opp_ids else {})
    out = []
    for r in rows:
        p, o = props.get(r.proposal_id), opps.get(r.opportunity_id)
        p_ok = p is not None and p.deleted_at is None
        out.append({
            "id": r.id, "brand_sales_org_id": r.brand_sales_org_id,
            "opportunity_id": r.opportunity_id, "proposal_id": r.proposal_id,
            "kind": r.request_kind, "status": r.status,
            "requested_by": r.requested_by,
            "requested_by_name": users.get(r.requested_by),
            "requested_at": r.requested_at.isoformat() if r.requested_at else None,
            "base_amount": r.base_amount, "current_adjustment": r.current_adjustment,
            "requested_adjustment": r.requested_adjustment, "currency": r.currency,
            "reason": r.reason,
            "requested_unit_price": r.requested_unit_price,
            "requested_min_units": r.requested_min_units,
            "requested_term_months": r.requested_term_months,
            "requested_implementation_fee": r.requested_implementation_fee,
            "floor_breach_detail": r.floor_breach_detail,
            "decided_by": r.decided_by, "decided_by_name": users.get(r.decided_by),
            "decided_at": r.decided_at.isoformat() if r.decided_at else None,
            "decision_note": r.decision_note,
            "proposal": {"exists": p_ok,
                         "sales_status": p.sales_status if p_ok else None,
                         "base_amount": p.base_amount if p_ok else None,
                         "adjustment": p.adjustment if p_ok else None},
            "opportunity": {"exists": o is not None,
                            "status": o.status if o else None,
                            "company_name": o.company_name if o else None,
                            "stage": o.stage if o else None,
                            "custom_unit_price": o.custom_unit_price if o else None,
                            "custom_min_units": o.custom_min_units if o else None},
        })
    return out


def build(db: Session, brand_sales_org_id: str, *, history_limit: int = 10) -> List[dict]:
    """Facts for every pending row plus recent non-pending rows of ONE brand."""
    q = db.query(PricingApprovalRequest).filter(
        PricingApprovalRequest.brand_sales_org_id == brand_sales_org_id)
    pending = q.filter(PricingApprovalRequest.status == "pending").all()
    recent = (q.filter(PricingApprovalRequest.status != "pending")
              .order_by(PricingApprovalRequest.decided_at.desc(),
                        PricingApprovalRequest.id.desc())
              .limit(history_limit).all())
    rows = pending + recent
    return facts_for(db, rows, user_names(db, rows))


def one(db: Session, request_id: str, *, lock: bool = False):
    q = db.query(PricingApprovalRequest).filter(PricingApprovalRequest.id == request_id)
    return (q.with_for_update() if lock else q).first()
