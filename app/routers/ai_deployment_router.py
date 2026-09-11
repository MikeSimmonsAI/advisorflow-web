"""MY AI WORKFORCE - the customer's own hiring and deployment surface.

EVERY ROUTE ANSWERS FOR THE CALLER'S OWN ORGANIZATION AND TAKES NO
ORGANIZATION ID. That is the property `workforce_router` and `support_router`
already rely on: the tenant boundary is a fact about the signatures rather
than a filter each route has to remember, and a route that cannot name another
customer cannot leak one. There is no `?organization_id=` on any route here
and there must never be one.

BUSINESS LANGUAGE ONLY. What comes back is "Ready to start", "Needs
attention", "who takes over", "what it can do". What does not come back is a
system prompt, a model name, a temperature, a tool schema or a template key
dressed up as a product name.

WRITES ARE ADMIN-ONLY AND REFUSED IN OBSERVATION MODE. Hiring an AI employee,
configuring one and asking for it to be switched on are administrative acts;
looking at one is not. A brand executive observing a customer must never
change one, which is what `require_not_observation` is for.

THIS ROUTER SWITCHES NOTHING ON BY ITSELF. `POST .../activation` asks
`ai_deployment.activation.request`, which asks T6, which still resolves the
minimum across four scopes. A customer administrator's request for live
operation is RECORDED and completed by an authorised platform operator - the
same division T6's own customer surface already draws.
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import (get_db, require_admin, require_not_observation,
                      require_tenant_user)
from app.models.ai_deployment_models import AIEmployeeDeployment
from app.models.models import Organization, User
from app.services import lead_scope
from app.services.ai_deployment import activation as t8_activation
from app.services.ai_deployment import catalog as t8_catalog
from app.services.ai_deployment import configuration as t8_config
from app.services.ai_deployment import constants as D
from app.services.ai_deployment import deprovision as t8_deprovision
from app.services.ai_deployment import lifecycle as t8_lifecycle
from app.services.ai_deployment import readiness as t8_readiness
from app.services.ai_deployment import views as t8_views

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/ai-workforce", tags=["ai-workforce-deployment"])


def _org_id(user: User, db: Session, request: Request) -> str:
    """The workspace this request is standing in. Never an argument."""
    org_id = lead_scope.active_workspace_org_id(user, db, request)
    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Select a customer workspace to see its AI workforce.")
    return org_id


def _org(db: Session, org_id: str) -> Organization:
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if org is None:
        raise HTTPException(status_code=404, detail="No such workspace.")
    return org


def _deployment(db: Session, org_id: str,
                deployment_id: str) -> AIEmployeeDeployment:
    """Load INSIDE this tenant, or 404.

    The answer for another customer's deployment is the same 404 as for one
    that does not exist, so this cannot be used to find out which ids are real.
    """
    row = t8_lifecycle.get(db, org_id, deployment_id)
    if row is None:
        raise HTTPException(status_code=404,
                            detail="No such AI employee.")
    return row


def _refused(exc: t8_lifecycle.DeploymentRefused) -> HTTPException:
    """Turn a refusal into the status code that actually describes it.

    402 for a commercial answer - the same code `require_feature` uses for the
    same kind of refusal. 409 for a race. 403 for authority. 400 for the rest,
    with the machine-readable code alongside the sentence so a screen can act
    on it without parsing prose.
    """
    code_map = {
        D.R_NOT_ENTITLED: 402, D.R_ENTITLEMENT_PENDING: 402,
        D.R_PACKAGE_INELIGIBLE: 402, D.R_CAPACITY_REACHED: 402,
        D.R_NOT_OFFERED: 404, D.R_UNKNOWN_TEMPLATE: 404,
        D.R_STALE_VIEW: 409, D.R_ILLEGAL_TRANSITION: 409,
        D.R_NOT_AUTHORIZED: 403, D.R_TENANT_MISMATCH: 404,
    }
    return HTTPException(status_code=code_map.get(exc.code, 400),
                         detail={"code": exc.code, "message": str(exc)})


# ---------------------------------------------------------------------------
# READING
# ---------------------------------------------------------------------------

@router.get("/overview")
def overview(request: Request, db: Session = Depends(get_db),
             user: User = Depends(require_tenant_user)) -> Dict[str, Any]:
    """The whole MY AI WORKFORCE screen, in one call."""
    org_id = _org_id(user, db, request)
    return t8_views.workforce_summary(db, org_id)


@router.get("/catalog")
def get_catalog(request: Request, db: Session = Depends(get_db),
                user: User = Depends(require_tenant_user)) -> Dict[str, Any]:
    """What this business could hire, and honestly whether they can."""
    org_id = _org_id(user, db, request)
    return {"available": t8_catalog.customer_catalog(db, org_id)}


@router.get("/catalog/{template_key}/questions")
def get_questions(template_key: str, request: Request,
                  db: Session = Depends(get_db),
                  user: User = Depends(require_tenant_user)) -> Dict[str, Any]:
    """The business interview for one job, with the customer's own options."""
    org_id = _org_id(user, db, request)
    try:
        return t8_config.schema_for(db, org_id, template_key)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/deployments")
def list_deployments(request: Request, db: Session = Depends(get_db),
                     user: User = Depends(require_tenant_user),
                     include_retired: bool = Query(False)) -> Dict[str, Any]:
    org_id = _org_id(user, db, request)
    rows = t8_lifecycle.list_for_org(db, org_id,
                                     include_retired=include_retired)
    return {"deployments": [t8_views.describe(db, r) for r in rows]}


@router.get("/deployments/{deployment_id}")
def get_deployment(deployment_id: str, request: Request,
                   db: Session = Depends(get_db),
                   user: User = Depends(require_tenant_user)) -> Dict[str, Any]:
    """One employee, with readiness recomputed now rather than remembered."""
    org_id = _org_id(user, db, request)
    row = _deployment(db, org_id, deployment_id)
    out = t8_views.describe(db, row, refresh_readiness=True)
    out["activity"] = t8_views.recent_activity(db, row)
    out["questions"] = t8_config.schema_for(db, org_id, row.template_key)
    db.commit()
    return out


@router.get("/deployments/{deployment_id}/activity")
def get_activity(deployment_id: str, request: Request,
                 db: Session = Depends(get_db),
                 user: User = Depends(require_tenant_user),
                 limit: int = Query(25, ge=1, le=100)) -> Dict[str, Any]:
    org_id = _org_id(user, db, request)
    row = _deployment(db, org_id, deployment_id)
    return {"activity": t8_views.recent_activity(db, row, limit=limit)}


@router.get("/deployments/{deployment_id}/readiness")
def get_readiness(deployment_id: str, request: Request,
                  db: Session = Depends(get_db),
                  user: User = Depends(require_tenant_user)) -> Dict[str, Any]:
    """Every check, named, with what to do about the ones that failed."""
    org_id = _org_id(user, db, request)
    row = _deployment(db, org_id, deployment_id)
    result = t8_readiness.refresh(db, row)
    db.commit()
    return result.as_dict()


# ---------------------------------------------------------------------------
# WRITING
# ---------------------------------------------------------------------------

class HireRequest(BaseModel):
    template_key: str
    name: Optional[str] = None
    # THE CLIENT'S OWN IDEMPOTENCY KEY. A browser that retries a hire sends the
    # same one, and the second request returns the first one's deployment
    # rather than creating a second AI employee on one entitlement.
    provisioning_key: Optional[str] = None
    configuration: Optional[Dict[str, Any]] = None


@router.post("/deployments", status_code=status.HTTP_201_CREATED)
def hire(payload: HireRequest, request: Request,
         db: Session = Depends(get_db),
         user: User = Depends(require_admin),
         _guard: User = Depends(require_not_observation)) -> Dict[str, Any]:
    """Hire an AI employee. IT STARTS SWITCHED OFF AND DOES NOTHING.

    Hiring records an arrangement and creates nothing that can act until the
    business questions are answered. Switching it on is a separate, recorded
    decision made by somebody authorised to make it.
    """
    org_id = _org_id(user, db, request)
    org = _org(db, org_id)
    try:
        row = t8_lifecycle.select(
            db, org=org, template_key=payload.template_key, actor=user,
            provisioning_key=payload.provisioning_key,
            display_name=payload.name)
        if payload.configuration:
            t8_lifecycle.configure(db, row, payload.configuration, actor=user)
    except t8_lifecycle.DeploymentRefused as exc:
        db.rollback()
        raise _refused(exc)
    except t8_config.ConfigurationRefused as exc:
        db.rollback()
        raise HTTPException(status_code=400,
                            detail={"code": D.R_FORBIDDEN_CONFIG,
                                    "problems": exc.problems})
    db.commit()
    return t8_views.describe(db, row, refresh_readiness=True)


class ConfigureRequest(BaseModel):
    name: Optional[str] = None
    # BUSINESS ANSWERS ONLY. A key that names anything internal is refused
    # rather than ignored - see constants.FORBIDDEN_CONFIG_FRAGMENTS.
    configuration: Dict[str, Any]
    replace: bool = False


@router.patch("/deployments/{deployment_id}/configuration")
def configure(deployment_id: str, payload: ConfigureRequest, request: Request,
              db: Session = Depends(get_db),
              user: User = Depends(require_admin),
              _guard: User = Depends(require_not_observation)
              ) -> Dict[str, Any]:
    org_id = _org_id(user, db, request)
    row = _deployment(db, org_id, deployment_id)
    if payload.name:
        row.display_name = payload.name[:120]
    try:
        result = t8_lifecycle.configure(db, row, payload.configuration,
                                        actor=user,
                                        merge=not payload.replace)
    except t8_config.ConfigurationRefused as exc:
        db.rollback()
        raise HTTPException(status_code=400,
                            detail={"code": D.R_FORBIDDEN_CONFIG,
                                    "problems": exc.problems})
    except t8_lifecycle.DeploymentRefused as exc:
        db.rollback()
        raise _refused(exc)
    db.commit()
    out = t8_views.describe(db, row)
    out["result"] = result
    return out


class ActivationRequest(BaseModel):
    stage: str                       # controlled | active
    reason: Optional[str] = None
    # The state the screen believed this employee was in. A tab left open
    # through somebody else's change is refused rather than applied on top.
    expected_state: Optional[str] = None


@router.post("/deployments/{deployment_id}/activation")
def activate(deployment_id: str, payload: ActivationRequest, request: Request,
             db: Session = Depends(get_db),
             user: User = Depends(require_admin),
             _guard: User = Depends(require_not_observation)) -> Dict[str, Any]:
    """Ask for this employee to start working.

    A customer administrator's request is RECORDED and the employee does not
    move. Live operation starts on an agreed group of records, with a cap and
    somebody watching, and the platform operator completes it.
    """
    org_id = _org_id(user, db, request)
    row = _deployment(db, org_id, deployment_id)
    stage = (payload.stage or "").strip().lower()
    try:
        result = t8_activation.request(
            db, row, stage, actor=user, reason=payload.reason or "",
            expected_state=payload.expected_state)
    except t8_lifecycle.DeploymentRefused as exc:
        db.rollback()
        raise _refused(exc)
    db.commit()
    result["deployment"] = t8_views.describe(db, row)
    return result


class PauseRequest(BaseModel):
    reason: Optional[str] = None
    expected_state: Optional[str] = None


@router.post("/deployments/{deployment_id}/pause")
def pause(deployment_id: str, payload: PauseRequest, request: Request,
          db: Session = Depends(get_db),
          user: User = Depends(require_admin),
          _guard: User = Depends(require_not_observation)) -> Dict[str, Any]:
    """Stop this employee now. Its queued work stops with it, and nothing is
    lost - a pause is not a cancellation."""
    org_id = _org_id(user, db, request)
    row = _deployment(db, org_id, deployment_id)
    try:
        t8_lifecycle.pause(db, row, reason=payload.reason or "Paused.",
                           actor=user,
                           expected_state=payload.expected_state)
    except t8_lifecycle.DeploymentRefused as exc:
        db.rollback()
        raise _refused(exc)
    db.commit()
    return t8_views.describe(db, row)


@router.post("/deployments/{deployment_id}/resume")
def resume(deployment_id: str, request: Request, db: Session = Depends(get_db),
           user: User = Depends(require_admin),
           _guard: User = Depends(require_not_observation)) -> Dict[str, Any]:
    """Bring a paused employee back, if it still may come back."""
    org_id = _org_id(user, db, request)
    row = _deployment(db, org_id, deployment_id)
    try:
        result = t8_lifecycle.resume(db, row, actor=user)
    except t8_lifecycle.DeploymentRefused as exc:
        db.rollback()
        raise _refused(exc)
    db.commit()
    out = t8_views.describe(db, row)
    out["result"] = result
    return out


class RetireRequest(BaseModel):
    reason: Optional[str] = None
    expected_state: Optional[str] = None


@router.post("/deployments/{deployment_id}/retire")
def retire(deployment_id: str, payload: RetireRequest, request: Request,
           db: Session = Depends(get_db),
           user: User = Depends(require_admin),
           _guard: User = Depends(require_not_observation)) -> Dict[str, Any]:
    """Give this AI employee back. Everything it did stays.

    RETIRING DOES NOT CANCEL ANYTHING COMMERCIAL. Removing an add-on is a
    separate act with its own authority, on the Billing screen, because a
    customer tidying up their workforce must not cancel a subscription by
    accident.
    """
    org_id = _org_id(user, db, request)
    row = _deployment(db, org_id, deployment_id)
    try:
        result = t8_deprovision.retire(db, row, actor=user,
                                       reason=payload.reason or "",
                                       expected_state=payload.expected_state)
    except t8_lifecycle.DeploymentRefused as exc:
        db.rollback()
        raise _refused(exc)
    db.commit()
    out = t8_views.describe(db, row)
    out["result"] = result
    return out
