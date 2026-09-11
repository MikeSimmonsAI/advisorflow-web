"""GOD MODE - AI WORKFORCE DEPLOYMENT. AN EXTENSION OF GOD MODE, NOT A SECOND
ROOT.

Every route is `require_god`, the same guard `god_router`, `god_billing_router`,
`god_catalog_router` and `god_workforce_router` use. There is no platform
superadmin here, no "AI workforce owner", no separate control plane, and no
route that introduces a new way of being root.

WHAT GOD ADMINISTERS HERE

    the platform library's deployability   what each job needs before anybody
                                           can run it, and how proven it is
    brand offering terms                   which brand sells which job, under
                                           what commercial arrangement, to
                                           which packages
    deployment state across organizations  who has what, in what state, and
                                           what is wrong with any of it
    activation                             completing a customer's request to
                                           start live operation
    reconciliation and the orphan sweep    stopping what should have stopped

WHAT IT DELIBERATELY DOES NOT DO is operate every customer's business
configuration from here. A customer's hours, audience and handoff recipient
belong to the customer's own screen; duplicating them would create two places
where the same setting is edited and one place where it is wrong.

NO PRICE IS SET ON ANY ROUTE IN THIS FILE. Commercial terms name a catalogue
item that God Mode's own Billing screens own. An amount typed here would be a
second catalogue, which is the thing the brief forbids twice.
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, require_god
from app.models.ai_deployment_models import (AIEmployeeDeployment,
                                             AIOfferingTerms)
from app.models.models import Organization, Platform, User
from app.services.ai_deployment import activation as t8_activation
from app.services.ai_deployment import capacity as t8_capacity
from app.services.ai_deployment import catalog as t8_catalog
from app.services.ai_deployment import commerce as t8_commerce
from app.services.ai_deployment import constants as D
from app.services.ai_deployment import deprovision as t8_deprovision
from app.services.ai_deployment import lifecycle as t8_lifecycle
from app.services.ai_deployment import readiness as t8_readiness
from app.services.ai_deployment import views as t8_views

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/god/ai-workforce",
                   tags=["god-ai-workforce-deployment"],
                   dependencies=[Depends(require_god)])


# ---------------------------------------------------------------------------
# OVERVIEW
# ---------------------------------------------------------------------------

@router.get("/overview")
def overview(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """One screen: what exists, what is running, and what is wrong."""
    rows = db.query(AIEmployeeDeployment).all()
    by_state: Dict[str, int] = {}
    for row in rows:
        by_state[row.state] = by_state.get(row.state, 0) + 1
    live = [r for r in rows if r.state in D.LIVE_STATES]
    return {
        "deployments_total": len(rows),
        "by_state": by_state,
        "live": len(live),
        "customers_with_deployments": len({r.organization_id for r in rows}),
        "brands_with_terms": db.query(AIOfferingTerms.platform_id).distinct()
        .count(),
        # THE HEADLINE. An operator opening this screen should be able to read
        # the dark-launch state in one line without interpreting anything.
        "platform": t8_activation.operational_capability(db),
        "orphans": t8_deprovision.orphan_scan(db),
        "templates": len(t8_catalog.platform_catalog()),
    }


@router.get("/templates")
def templates(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """The platform library, with what each job needs in order to be hired."""
    return {"templates": t8_catalog.platform_catalog(db),
            "note": ("The job library lives in code. This screen shows what "
                     "each job requires before any customer can run it; it "
                     "does not price anything.")}


# ---------------------------------------------------------------------------
# BRAND TERMS
# ---------------------------------------------------------------------------

@router.get("/brands/{platform_id}/catalog")
def brand_catalog(platform_id: str, db: Session = Depends(get_db)
                  ) -> Dict[str, Any]:
    brand = db.query(Platform).filter(Platform.id == platform_id).first()
    if brand is None:
        raise HTTPException(status_code=404, detail="No such brand.")
    return {
        "brand": {"id": brand.id, "name": brand.name, "slug": brand.slug},
        "catalog": t8_catalog.brand_catalog(db, platform_id),
        "note": ("A brand may narrow channels, name a catalogue item and say "
                 "which packages may hold a job. It cannot widen what the "
                 "platform template permits and it sets no price here."),
    }


class TermsWrite(BaseModel):
    template_key: str
    commercial_mode: Optional[str] = None
    catalog_item_key: Optional[str] = None
    included_plan_keys: Optional[List[str]] = None
    eligible_plan_keys: Optional[List[str]] = None
    max_per_customer: Optional[int] = None
    requires_controlled_first: Optional[bool] = None
    allowed_channels: Optional[List[str]] = None
    is_available: Optional[bool] = None
    notes: Optional[str] = None


@router.put("/brands/{platform_id}/terms")
def set_terms(platform_id: str, payload: TermsWrite,
              db: Session = Depends(get_db),
              user: User = Depends(require_god)) -> Dict[str, Any]:
    """Configure how one brand sells one job. NARROWING ONLY, AND NO PRICE.

    A catalogue item key is VALIDATED against this brand's own catalogue before
    it is stored. An item key that resolves to nothing would make the job look
    configured and be unbuyable, and the person who found out would be a
    customer clicking Hire.
    """
    import json

    if db.query(Platform).filter(Platform.id == platform_id).first() is None:
        raise HTTPException(status_code=404, detail="No such brand.")
    from app.services.workforce import registry as wf_registry
    spec = wf_registry.template(payload.template_key)
    if spec is None:
        raise HTTPException(status_code=404,
                            detail="No such AI employee job.")

    if payload.commercial_mode is not None \
            and payload.commercial_mode not in D.COMMERCIAL_MODES:
        raise HTTPException(
            status_code=400,
            detail="Commercial mode must be one of: %s."
                   % ", ".join(D.COMMERCIAL_MODES))

    row = t8_commerce.terms_for(db, platform_id, payload.template_key)
    if row is None:
        row = AIOfferingTerms(platform_id=platform_id,
                              template_key=payload.template_key)
        db.add(row)

    if payload.commercial_mode is not None:
        row.commercial_mode = payload.commercial_mode
    if payload.catalog_item_key is not None:
        key = payload.catalog_item_key.strip() or None
        if key is not None:
            from app.services import brand_catalog as t2_catalog
            item = t2_catalog.resolve(db, platform_id, key)
            if item is None:
                raise HTTPException(
                    status_code=400,
                    detail="This brand has no catalogue item %r. Create it in "
                           "Billing first - a price is a brand decision and "
                           "nothing here invents one." % key)
        row.catalog_item_key = key
    if payload.included_plan_keys is not None:
        row.included_plan_keys = json.dumps(
            [k for k in payload.included_plan_keys if isinstance(k, str)])
    if payload.eligible_plan_keys is not None:
        row.eligible_plan_keys = json.dumps(
            [k for k in payload.eligible_plan_keys if isinstance(k, str)])
    if payload.max_per_customer is not None:
        row.max_per_customer = (int(payload.max_per_customer)
                                if int(payload.max_per_customer) > 0 else None)
    if payload.requires_controlled_first is not None:
        row.requires_controlled_first = bool(payload.requires_controlled_first)
    if payload.allowed_channels is not None:
        from app.services.workforce import registry as reg
        row.allowed_channels = json.dumps(
            reg.normalize_channels(payload.allowed_channels,
                                   bound=spec.channels))
    if payload.is_available is not None:
        row.is_available = bool(payload.is_available)
    if payload.notes is not None:
        row.notes = payload.notes[:2000] or None
    row.updated_by = user.id
    db.commit()
    return {"terms": t8_catalog.terms_out(
        t8_commerce.terms_for(db, platform_id, payload.template_key))}


# ---------------------------------------------------------------------------
# DEPLOYMENTS ACROSS ORGANIZATIONS
# ---------------------------------------------------------------------------

@router.get("/deployments")
def list_deployments(db: Session = Depends(get_db),
                     organization_id: Optional[str] = Query(None),
                     state: Optional[str] = Query(None),
                     limit: int = Query(200, ge=1, le=1000)) -> Dict[str, Any]:
    q = db.query(AIEmployeeDeployment)
    if organization_id:
        q = q.filter(AIEmployeeDeployment.organization_id == organization_id)
    if state:
        q = q.filter(AIEmployeeDeployment.state == state)
    rows = q.order_by(AIEmployeeDeployment.created_at.desc()).limit(limit).all()
    org_names = {o.id: o.name for o in db.query(Organization).filter(
        Organization.id.in_([r.organization_id for r in rows])).all()} \
        if rows else {}
    return {"deployments": [
        dict(t8_views.describe(db, r, for_operator=True),
             organization_name=org_names.get(r.organization_id))
        for r in rows]}


@router.get("/deployments/{deployment_id}")
def deployment_detail(deployment_id: str,
                      db: Session = Depends(get_db)) -> Dict[str, Any]:
    row = (db.query(AIEmployeeDeployment)
           .filter(AIEmployeeDeployment.id == deployment_id).first())
    if row is None:
        raise HTTPException(status_code=404, detail="No such deployment.")
    out = t8_views.describe(db, row, for_operator=True, refresh_readiness=True)
    out["activity"] = t8_views.recent_activity(db, row, limit=50)
    out["preconditions"] = {
        stage: t8_activation.preconditions(db, row, stage)
        for stage in (D.CONTROLLED, D.ACTIVE)}
    db.commit()
    return out


class OperatorActivation(BaseModel):
    stage: str
    reason: str
    expected_state: Optional[str] = None


@router.post("/deployments/{deployment_id}/activation")
def operator_activate(deployment_id: str, payload: OperatorActivation,
                      db: Session = Depends(get_db),
                      user: User = Depends(require_god)) -> Dict[str, Any]:
    """Complete an activation. A REASON IS REQUIRED, and that is deliberate.

    Tightening needs no justification - it is always safe. Starting real
    operation without saying why leaves nobody able to answer "who switched
    this on, for whom, and what for" three months later.
    """
    row = (db.query(AIEmployeeDeployment)
           .filter(AIEmployeeDeployment.id == deployment_id).first())
    if row is None:
        raise HTTPException(status_code=404, detail="No such deployment.")
    if not (payload.reason or "").strip():
        raise HTTPException(
            status_code=400,
            detail="Say why this AI employee is being switched on.")
    try:
        result = t8_activation.request(
            db, row, (payload.stage or "").strip().lower(), actor=user,
            reason=payload.reason, expected_state=payload.expected_state)
    except t8_lifecycle.DeploymentRefused as exc:
        db.rollback()
        raise HTTPException(status_code=409 if exc.code == D.R_STALE_VIEW
                            else 400,
                            detail={"code": exc.code, "message": str(exc)})
    db.commit()
    result["deployment"] = t8_views.describe(db, row, for_operator=True)
    return result


class AcknowledgeReview(BaseModel):
    note: Optional[str] = None


@router.post("/deployments/{deployment_id}/acknowledge-review")
def acknowledge_review(deployment_id: str, payload: AcknowledgeReview,
                       db: Session = Depends(get_db),
                       user: User = Depends(require_god)) -> Dict[str, Any]:
    """Record that an operator has SEEN this employee's review items.

    Acknowledging changes no state and switches nothing on. It records a
    signature against exactly the items that were showing, so a review item
    that appears afterwards is not waved through by a signature given for a
    different one.
    """
    row = (db.query(AIEmployeeDeployment)
           .filter(AIEmployeeDeployment.id == deployment_id).first())
    if row is None:
        raise HTTPException(status_code=404, detail="No such deployment.")
    try:
        out = t8_activation.acknowledge_review(db, row, actor=user,
                                               note=payload.note or "")
    except t8_lifecycle.DeploymentRefused as exc:
        db.rollback()
        raise HTTPException(status_code=400,
                            detail={"code": exc.code, "message": str(exc)})
    db.commit()
    return out


class StandDownRequest(BaseModel):
    reason: str


@router.post("/deployments/{deployment_id}/stand-down")
def operator_stand_down(deployment_id: str, payload: StandDownRequest,
                        db: Session = Depends(get_db),
                        user: User = Depends(require_god)) -> Dict[str, Any]:
    row = (db.query(AIEmployeeDeployment)
           .filter(AIEmployeeDeployment.id == deployment_id).first())
    if row is None:
        raise HTTPException(status_code=404, detail="No such deployment.")
    try:
        t8_activation.stand_down(db, row, actor=user, reason=payload.reason)
    except t8_lifecycle.DeploymentRefused as exc:
        db.rollback()
        raise HTTPException(status_code=400,
                            detail={"code": exc.code, "message": str(exc)})
    db.commit()
    return t8_views.describe(db, row, for_operator=True)


# ---------------------------------------------------------------------------
# RECONCILIATION AND THE SWEEP
# ---------------------------------------------------------------------------

@router.post("/reconcile")
def reconcile(db: Session = Depends(get_db),
              organization_id: Optional[str] = Query(None)) -> Dict[str, Any]:
    """Re-read T2's answer for every deployment and stop anything unentitled.

    Only ever stops. Entitlement arriving never starts anything, which is what
    makes this safe to run on demand and safe to wire into a webhook.
    """
    if organization_id:
        out = t8_commerce.reconcile_organization(
            db, organization_id, reason="operator reconcile")
        db.commit()
        return out
    org_ids = [r[0] for r in
               db.query(AIEmployeeDeployment.organization_id).distinct().all()]
    results = [t8_commerce.reconcile_organization(
        db, oid, reason="operator reconcile") for oid in org_ids]
    db.commit()
    return {"organizations": len(results),
            "suspended": sum(r.get("suspended", 0) for r in results),
            "restored": sum(r.get("restored", 0) for r in results),
            "results": results}


@router.get("/orphans")
def orphans(db: Session = Depends(get_db),
            organization_id: Optional[str] = Query(None)) -> Dict[str, Any]:
    return t8_deprovision.orphan_scan(db, organization_id=organization_id)


@router.post("/orphans/repair")
def repair(db: Session = Depends(get_db),
           user: User = Depends(require_god),
           organization_id: Optional[str] = Query(None)) -> Dict[str, Any]:
    """Stop what the scan found. STOPPING ONLY - nothing is started or deleted."""
    out = t8_deprovision.repair_orphans(db, organization_id=organization_id,
                                        actor=user)
    db.commit()
    return out


# ---------------------------------------------------------------------------
# PROOF
# ---------------------------------------------------------------------------

class ProofRequest(BaseModel):
    # A deliberate, typed confirmation rather than a default, because this
    # builds synthetic organizations and a button that does that by accident
    # is a button that fills a production database with fictional customers.
    confirm_synthetic_data: bool = False
    lifecycle: str = "all"


@router.post("/proof/run")
def run_proof(payload: ProofRequest, db: Session = Depends(get_db)
              ) -> Dict[str, Any]:
    """Run the synthetic deployment lifecycles against the REAL engine.

    Every scenario builds its own synthetic organization inside a savepoint and
    rolls it back, so this reads and writes nothing belonging to a real
    customer - and it exercises the shipping code rather than a copy of it.
    NO REAL OUTREACH IS POSSIBLE: every contact is a reserved fictional number
    on an unresolvable domain, and no scenario reaches an executing stage with
    a live adapter.
    """
    if not payload.confirm_synthetic_data:
        raise HTTPException(
            status_code=400,
            detail="Confirm that synthetic data may be created for this run.")
    from app.services.ai_deployment import simulation
    savepoint = db.begin_nested()
    try:
        report = simulation.run(db, which=payload.lifecycle)
    finally:
        # NOTHING A PROOF RUN CREATES SURVIVES IT.
        savepoint.rollback()
    return report


@router.post("/proof/attack")
def run_attack(payload: ProofRequest, db: Session = Depends(get_db)
               ) -> Dict[str, Any]:
    """Run the adversarial harness: isolation, races, and commercial safety."""
    if not payload.confirm_synthetic_data:
        raise HTTPException(
            status_code=400,
            detail="Confirm that synthetic data may be created for this run.")
    from app.services.ai_deployment import evaluation
    savepoint = db.begin_nested()
    try:
        report = evaluation.run(db)
    finally:
        savepoint.rollback()
    return report


@router.get("/capacity/{organization_id}")
def capacity_report(organization_id: str,
                    db: Session = Depends(get_db)) -> Dict[str, Any]:
    org = (db.query(Organization)
           .filter(Organization.id == organization_id).first())
    if org is None:
        raise HTTPException(status_code=404, detail="No such organization.")
    return t8_capacity.report(db, org)


@router.get("/readiness/{deployment_id}")
def readiness_detail(deployment_id: str,
                     db: Session = Depends(get_db)) -> Dict[str, Any]:
    row = (db.query(AIEmployeeDeployment)
           .filter(AIEmployeeDeployment.id == deployment_id).first())
    if row is None:
        raise HTTPException(status_code=404, detail="No such deployment.")
    result = t8_readiness.refresh(db, row)
    db.commit()
    return result.as_dict()
