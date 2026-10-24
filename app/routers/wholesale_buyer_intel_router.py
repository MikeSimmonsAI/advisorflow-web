"""Cash buyer intelligence (R2) - read-only views over the existing buyer CRM.

    GET /wholesale/deals/{id}/buyer-intel   every eligible buyer for this deal: buy-box fit,
                                            verification tier, what their actions showed,
                                            priority with each point explained
    GET /wholesale/buyers/{id}/intel        one buyer's tier, evidence and learned preferences

Nothing here writes, sends, or buys a lookup. Sending a deal still goes through
the existing disposition path (POST /wholesale/deals/{id}/disposition) with its
consent and approval rules; contact lookups still go through the existing
enrichment approval.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.deps import get_db, require_tenant_or_observer
from app.models.models import User
from app.models.wholesale_models import WholesaleBuyer
from app.services import wholesale_buyer_intel as BI
from app.services import wholesale_service as svc
from app.services.entitlements import require_feature

router = APIRouter(prefix="/wholesale", tags=["wholesale-buyer-intel"],
                   dependencies=[Depends(require_feature("wholesale_real_estate"))])


def _org(db: Session, user: User) -> str:
    org_id = svc.read_org_id(db, user)
    if not org_id:
        raise HTTPException(status_code=409, detail="No customer organization selected.")
    return org_id


@router.get("/deals/{deal_id}/buyer-intel")
def deal_buyer_intel(deal_id: str, db: Session = Depends(get_db),
                     user: User = Depends(require_tenant_or_observer)):
    org_id = _org(db, user)
    deal = svc.get_deal(db, org_id, deal_id)
    return BI.for_deal(db, org_id, deal)


@router.get("/buyers/{buyer_id}/intel")
def buyer_intel(buyer_id: str, db: Session = Depends(get_db),
                user: User = Depends(require_tenant_or_observer)):
    from app.routers.wholesale_buyers_router import _buyer_activity
    org_id = _org(db, user)
    b = db.query(WholesaleBuyer).filter(WholesaleBuyer.id == buyer_id,
                                        WholesaleBuyer.organization_id == org_id).first()
    if b is None:
        raise HTTPException(status_code=404, detail="Buyer not found.")
    activity = _buyer_activity(db, org_id, [b.id]).get(b.id)
    rows = BI.history_rows(db, org_id, [b.id]).get(b.id) or []
    return {"buyer_id": b.id, "name": b.company_name or b.contact_name or "(unnamed buyer)",
            "tier": BI.tier(b, activity), "activity": activity, "learned": BI.revealed(rows),
            "history": rows, "county": BI.county_evidence(b),
            "do_not_contact": bool(b.do_not_contact)}
