"""Disposition (R4) - read-only views over the existing buyer CRM.

    GET /wholesale/deals/{id}/disposition-summary   engagement funnel, offers side by side,
                                                    follow-ups due, closing checklist
    GET /wholesale/disposition/analytics            disposition numbers across deals
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.deps import get_db, require_tenant_or_observer
from app.models.models import User
from app.services import wholesale_disposition_intel as DI
from app.services import wholesale_service as svc
from app.services.entitlements import require_feature

router = APIRouter(prefix="/wholesale", tags=["wholesale-disposition-intel"],
                   dependencies=[Depends(require_feature("wholesale_real_estate"))])


def _org(db, user):
    org_id = svc.read_org_id(db, user)
    if not org_id:
        raise HTTPException(status_code=409, detail="No customer organization selected.")
    return org_id


@router.get("/deals/{deal_id}/disposition-summary")
def disposition_summary(deal_id: str, db: Session = Depends(get_db),
                        user: User = Depends(require_tenant_or_observer)):
    org_id = _org(db, user)
    return DI.summary(db, org_id, svc.get_deal(db, org_id, deal_id))


@router.get("/disposition/analytics")
def disposition_analytics(include_test: bool = False, days: int = Query(180, ge=7, le=730),
                          db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    return DI.analytics(db, _org(db, user), include_test=include_test, days=days)
