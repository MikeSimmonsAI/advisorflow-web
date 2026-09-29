"""/wholesale/skip-trace - skip-trace economics: ESTIMATED TOTAL COST before
any paid run.

    GET  /wholesale/skip-trace/providers                    price catalogue + current integration facts
    POST /wholesale/skip-trace/estimate                     estimate a batch (count or property ids; dedupe)
    GET  /wholesale/skip-trace/compare?records=N[&hit_rate] every product ranked by TOTAL cost for N
    GET  /wholesale/skip-trace/estimates/{estimate_id}      one estimate (this workspace only)
    POST /wholesale/skip-trace/estimates/{estimate_id}/confirm   admin types "APPROVE SKIP TRACE <id>"

Every route: require_feature("wholesale_real_estate") and the ACTING
workspace; another workspace's estimate is a 404. Writes refuse Executive
Observation Mode. NOTHING HERE CALLS A VENDOR OR QUEUES A RUN: confirming an
estimate records a person's approval of the total; a trigger path must still
pass skiptrace_costing.gate_paid_run() (which consumes the approval once).
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, require_not_observation, require_tenant_or_observer, require_tenant_user
from app.models.models import User
from app.services import skiptrace_costing as SC
from app.services import wholesale_service as svc
from app.services.entitlements import require_feature

FEATURE = "wholesale_real_estate"

router = APIRouter(prefix="/wholesale/skip-trace", tags=["wholesale-skip-trace"],
                   dependencies=[Depends(require_feature(FEATURE))])


def _read_org(db: Session, user: User) -> str:
    org = svc.read_org_id(db, user)
    if not org:
        raise HTTPException(status_code=409, detail="Choose a workspace first.")
    return org


def _is_admin(db: Session, user: User, org_id: str) -> bool:
    """May this person approve paid spend IN org_id? The WORKSPACE role only:
    the membership held in that org (never `users.role` of a home org, so a
    super_admin elsewhere with an advisor membership here cannot approve).
    A home org with no membership row falls back to the workspace-effective
    role. god_admin (platform owner) keeps its shortcut."""
    from app.services import lead_scope, workspace_access
    if lead_scope.is_god(user):
        return True
    role = workspace_access.workspace_role(user, db, org_id)
    if role is None and org_id == getattr(user, "organization_id", None):
        role = lead_scope.effective_role(user, db)
    return (role or "").lower() in ("org_admin", "super_admin")


@router.get("/providers")
def providers(db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    _read_org(db, user)
    from app.services import skiptrace_adapters as SA
    items = SC.list_products(db)
    db.commit()
    return {"items": items, "integration": SC.integration_facts(), "adapters": SA.adapters_report(),
            "catalogue_version": SC.CATALOGUE_VERSION,
            "default_hit_rate_assumption": float(SC.DEFAULT_HIT_RATE),
            "freshness_days": SC.FRESHNESS_DAYS}


class EstimateIn(BaseModel):
    product_key: str = "tracerfy"            # "tracerfy" = the configured Tracerfy default (Normal batch)
    record_count: Optional[int] = None
    property_ids: Optional[List[str]] = None
    property_kind: str = "wholesale"          # wholesale | evosense
    dedupe: bool = True
    hit_rate: Optional[float] = None


@router.post("/estimate")
def estimate(payload: EstimateIn, db: Session = Depends(get_db),
             user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    try:
        out = SC.estimate_batch(db, org_id, product_key=payload.product_key,
                                record_count=payload.record_count, property_ids=payload.property_ids,
                                property_kind=payload.property_kind, dedupe=payload.dedupe,
                                hit_rate=payload.hit_rate, user=user)
    except (SC.EstimateRefused, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc))
    db.commit()
    return out


@router.get("/compare")
def compare(records: int = Query(..., ge=0, le=500000), hit_rate: Optional[float] = Query(None, ge=0, le=1),
            db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    _read_org(db, user)
    try:
        out = SC.compare_providers(db, records, hit_rate=hit_rate)
    except (SC.EstimateRefused, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    db.commit()
    return out


@router.get("/estimates/{estimate_id}")
def get_estimate(estimate_id: str, db: Session = Depends(get_db),
                 user: User = Depends(require_tenant_or_observer)):
    org_id = _read_org(db, user)
    e = SC._load(db, org_id, estimate_id)
    if e is None:
        raise HTTPException(status_code=404, detail="Estimate not found")
    return SC.estimate_payload(e)


class ConfirmIn(BaseModel):
    confirm: Optional[str] = None


@router.post("/estimates/{estimate_id}/confirm")
def confirm(estimate_id: str, payload: ConfirmIn, db: Session = Depends(get_db),
            user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    if SC._load(db, org_id, estimate_id) is None:
        raise HTTPException(status_code=404, detail="Estimate not found")
    if not _is_admin(db, user, org_id):
        raise HTTPException(status_code=403, detail="Only a workspace admin can approve paid skip-trace spend.")
    try:
        e = SC.confirm_estimate(db, org_id, estimate_id, payload.confirm, user)
    except SC.ConfirmationRequired as exc:
        db.commit()                 # an expiry is recorded
        raise HTTPException(status_code=409, detail=str(exc))
    db.commit()
    out = SC.estimate_payload(e)
    out["note"] = ("Approved. Nothing was run or queued: a paid run must present this estimate id and is "
                   "refused for any other provider, a larger batch, or records not in this estimate.")
    return out
