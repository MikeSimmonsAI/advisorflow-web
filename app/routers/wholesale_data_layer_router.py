"""Unified data view (R5) - read-only.

    GET /wholesale/data-sources              every source: confidence, cost class, state, errors, retry, caching
    GET /wholesale/deals/{id}/provenance     every number on the deal with its source and as-of time
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.deps import get_db, require_tenant_or_observer
from app.models.models import User
from app.services import wholesale_data_layer as DL
from app.services import wholesale_service as svc
from app.services.entitlements import require_feature

router = APIRouter(prefix="/wholesale", tags=["wholesale-data-layer"],
                   dependencies=[Depends(require_feature("wholesale_real_estate"))])


def _org(db, user):
    org_id = svc.read_org_id(db, user)
    if not org_id:
        raise HTTPException(status_code=409, detail="No customer organization selected.")
    return org_id


@router.get("/data-sources")
def data_sources(db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    return DL.sources(db, _org(db, user))


@router.get("/deals/{deal_id}/provenance")
def deal_provenance(deal_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    org_id = _org(db, user)
    return DL.deal_provenance(db, org_id, svc.get_deal(db, org_id, deal_id))
