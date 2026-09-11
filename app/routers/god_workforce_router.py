"""GOD MODE — AI WORKFORCE. AN EXTENSION OF GOD MODE, NOT A SECOND ROOT.

Section 1 is the first non-negotiable in the brief and this router is where it
would most easily have been broken. There is NO platform superadmin here, no
"AI workforce owner" role, no separate control plane. Every route is
`require_god`, the same guard `god_router`, `god_billing_router` and
`god_support_router` use, and no route introduces a new way of being root.

WHAT GOD ADMINISTERS HERE IS PLATFORM CAPABILITY:

    the job library            what AI employees can exist at all
    the tool registry          what any of them can ever do
    activation                 how far anything may go, at every scope
    the kill switch            stopping it, at every scope
    brand offerings            which brand sells which job
    evaluation and simulation  is it behaving, and can we prove it
    health                     stalled work, failures, refusals, cost

WHAT IT DELIBERATELY DOES NOT DO is operate every customer's business
configuration from here. Section 22: a customer's objective, audience, hours
and handoff recipient belong to the customer's own screen, and duplicating
them here would create two places where the same setting is edited.
"""

import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, require_god
from app.models.models import Organization, Platform, User
from app.models.workforce_models import (AIEmployee, AISupervisorEvent,
                                         AIToolExecution)
from app.services.workforce import activation as wf_activation
from app.services.workforce import constants as C
from app.services.workforce import evaluation as wf_evaluation
from app.services.workforce import model_router as wf_model_router
from app.services.workforce import outbound as wf_outbound
from app.services.workforce import profiles as wf_profiles
from app.services.workforce import queue as wf_queue
from app.services.workforce import registry as wf_registry
from app.services.workforce import service as wf_service
from app.services.workforce import simulator as wf_simulator
from app.services.workforce import supervisor as wf_supervisor

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/god/workforce", tags=["god-ai-workforce"],
                   dependencies=[Depends(require_god)])


# ── OVERVIEW ────────────────────────────────────────────────────────────────

@router.get("/overview")
def overview(db: Session = Depends(get_db),
             days: int = Query(7, ge=1, le=90)) -> Dict[str, Any]:
    """One screen: is anything on, is anything running, is anything wrong."""
    platform_row = wf_activation.scope_report(db, wf_activation.SCOPE_PLATFORM,
                                              "")
    employees = db.query(AIEmployee).all()
    by_org: Dict[str, int] = {}
    for emp in employees:
        by_org[emp.organization_id] = by_org.get(emp.organization_id, 0) + 1
    return {
        "platform_activation": platform_row,
        # THE HEADLINE. An operator opening this screen should be able to read
        # the dark-launch state in one line without interpreting anything.
        "dark_launch": {
            "platform_stage": platform_row["state"],
            "kill_switch": platform_row["kill_switch"],
            "environment_kill": wf_activation.env_kill_engaged(),
            "live_voice_enabled": wf_activation.live_voice_enabled(),
            "outbound_adapters": wf_outbound.adapter_report(),
            "customers_with_employees": len(by_org),
            "employees_total": len(employees),
            "employees_that_could_execute": sum(
                1 for e in employees
                if wf_activation.resolve(db, employee=e).may_execute),
        },
        "supervisor": wf_supervisor.overview(db, days=days),
        "templates": len(wf_registry.ALL_TEMPLATE_KEYS),
        "tools": len(wf_registry.ALL_TOOL_KEYS),
        "executing_tools": list(wf_registry.EXECUTING_TOOL_KEYS),
    }


@router.get("/templates")
def list_templates(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """The job library — the CODE registry, with its database mirror's state."""
    from app.models.workforce_models import AIEmployeeTemplate
    rows = {r.key: r for r in db.query(AIEmployeeTemplate).all()}
    out = []
    for key in wf_registry.ALL_TEMPLATE_KEYS:
        spec = wf_registry.template(key)
        row = rows.get(key)
        out.append(dict(spec.as_dict(), **{
            "id": getattr(row, "id", None),
            "synced": row is not None,
            "active": bool(getattr(row, "is_active", False)),
            "in_use": (db.query(AIEmployee)
                       .filter(AIEmployee.template_id == getattr(row, "id", ""))
                       .count() if row else 0),
        }))
    orphans = [k for k in rows if k not in wf_registry.TEMPLATES]
    return {"templates": out, "synced_count": len(rows),
            "orphaned_rows": orphans}


@router.post("/templates/sync")
def sync_templates(db: Session = Depends(get_db),
                   user: User = Depends(require_god)) -> Dict[str, Any]:
    result = wf_service.sync_templates(db, actor_user_id=user.id)
    db.commit()
    return result


@router.get("/tools")
def list_tools() -> Dict[str, Any]:
    """THE REGISTERED TOOL GATEWAY, in full. Nothing else can ever be called."""
    return {
        "tools": [wf_registry.tool(k).as_dict()
                  for k in wf_registry.ALL_TOOL_KEYS],
        "executing": list(wf_registry.EXECUTING_TOOL_KEYS),
        "read_only": list(wf_registry.READ_ONLY_TOOL_KEYS),
        "gates": [
            "1. registered tool", "2. employee may run (kill/pause/stage)",
            "3. work item ownership", "4. argument schema",
            "5. template ∩ brand ∩ explicit grant", "6. channel enabled",
            "7. live voice", "8. activation stage vs outward reach",
            "9. customer feature + commercial entitlement",
            "10. run budget", "11. record tenancy and ownership",
            "12. contact eligibility", "13. idempotency",
        ],
        "denial_codes": sorted(
            v for k, v in vars(C).items()
            if k.startswith("DENY_") and isinstance(v, str)),
    }


@router.get("/providers")
def list_providers() -> Dict[str, Any]:
    """Provider-neutral routing, and which provider actually answers today."""
    return {
        "capabilities": wf_model_router.CAPABILITIES,
        "providers": wf_model_router.providers_report(),
        "resolved": {cap: wf_model_router.resolve(cap).key
                     for cap in sorted(wf_model_router.CAPABILITIES)},
        "note": ("No live model provider is enabled in this deployment. The "
                 "deterministic planner serves every capability, and runs "
                 "record which provider answered."),
    }


# ── ACTIVATION AND THE KILL SWITCH ──────────────────────────────────────────

@router.get("/activation")
def get_activation(db: Session = Depends(get_db),
                   platform_id: Optional[str] = Query(None),
                   organization_id: Optional[str] = Query(None),
                   employee_id: Optional[str] = Query(None)
                   ) -> Dict[str, Any]:
    """Every scope's own setting, and the effective answer with its working."""
    scopes = [wf_activation.scope_report(db, wf_activation.SCOPE_PLATFORM, "")]
    if platform_id:
        scopes.append(wf_activation.scope_report(db, wf_activation.SCOPE_BRAND,
                                                 platform_id))
    if organization_id:
        scopes.append(wf_activation.scope_report(
            db, wf_activation.SCOPE_CUSTOMER, organization_id))
    employee = None
    if employee_id:
        employee = (db.query(AIEmployee)
                    .filter(AIEmployee.id == employee_id).first())
        if employee is None:
            raise HTTPException(status_code=404, detail="No such AI employee.")
        scopes.append(wf_activation.scope_report(
            db, wf_activation.SCOPE_EMPLOYEE, employee_id))
    resolved = wf_activation.resolve(db, employee=employee,
                                     organization_id=organization_id,
                                     platform_id=platform_id)
    return {"scopes": scopes, "effective": resolved.as_dict(),
            "stages": list(C.ACTIVATION_STAGES),
            "stages_that_reach_people": sorted(C.EXECUTING_STAGES),
            "environment_kill": wf_activation.env_kill_engaged()}


class ActivationWrite(BaseModel):
    scope_type: str
    scope_id: Optional[str] = ""
    state: Optional[str] = None
    kill_switch: Optional[bool] = None
    daily_cap: Optional[int] = None
    cohort_limit: Optional[int] = None
    reason: Optional[str] = None


@router.put("/activation")
def set_activation(payload: ActivationWrite, db: Session = Depends(get_db),
                   user: User = Depends(require_god)) -> Dict[str, Any]:
    """Set a stage, a cap, or the kill switch, at one scope.

    A REASON IS REQUIRED FOR ANYTHING THAT LOOSENS. Tightening needs no
    justification — it is always safe — but promoting a scope toward live
    execution without saying why leaves nobody able to answer "who turned this
    on and what for" three months later.
    """
    if payload.scope_type not in wf_activation.SCOPE_TYPES:
        raise HTTPException(status_code=400, detail="Unknown activation scope.")
    scope_id = payload.scope_id or ""
    before = wf_activation.scope_report(db, payload.scope_type, scope_id)

    if payload.state is not None:
        if payload.state not in C.ACTIVATION_RANK:
            raise HTTPException(status_code=400,
                                detail="Unknown activation stage.")
        loosening = (C.ACTIVATION_RANK[payload.state]
                     > C.ACTIVATION_RANK.get(before["state"], 0))
        if loosening and not (payload.reason or "").strip():
            raise HTTPException(
                status_code=400,
                detail="Say why this scope is being moved to '%s'."
                       % payload.state)
        wf_activation.set_state(db, payload.scope_type, scope_id,
                                payload.state, actor_user_id=user.id,
                                reason=payload.reason)
    if payload.kill_switch is not None:
        wf_activation.set_kill_switch(db, payload.scope_type, scope_id,
                                      bool(payload.kill_switch),
                                      actor_user_id=user.id,
                                      reason=payload.reason)
        if payload.kill_switch:
            # A KILL STOPS THE QUEUES TOO. `resolve` already refuses the next
            # tool call; pausing the items stops the screens showing work that
            # is not happening.
            employees = _employees_in_scope(db, payload.scope_type, scope_id)
            for emp in employees:
                wf_queue.pause_all(db, employee_id=emp.id,
                                   reason="kill switch engaged at %s scope"
                                          % payload.scope_type,
                                   actor_id=user.id)
    for field in ("daily_cap", "cohort_limit"):
        value = getattr(payload, field)
        if value is not None:
            row = wf_activation.set_state(
                db, payload.scope_type, scope_id,
                payload.state or before["state"], actor_user_id=user.id,
                reason=payload.reason)
            setattr(row, field, int(value) if int(value) > 0 else None)
    db.commit()
    return {"before": before,
            "after": wf_activation.scope_report(db, payload.scope_type,
                                                scope_id)}


def _employees_in_scope(db: Session, scope_type: str,
                        scope_id: str) -> List[AIEmployee]:
    q = db.query(AIEmployee)
    if scope_type == wf_activation.SCOPE_BRAND:
        q = q.filter(AIEmployee.platform_id == scope_id)
    elif scope_type == wf_activation.SCOPE_CUSTOMER:
        q = q.filter(AIEmployee.organization_id == scope_id)
    elif scope_type == wf_activation.SCOPE_EMPLOYEE:
        q = q.filter(AIEmployee.id == scope_id)
    return q.all()


# ── BRAND OFFERINGS ─────────────────────────────────────────────────────────

@router.get("/brands/{platform_id}/offerings")
def get_brand_offerings(platform_id: str,
                        db: Session = Depends(get_db)) -> Dict[str, Any]:
    brand = db.query(Platform).filter(Platform.id == platform_id).first()
    if brand is None:
        raise HTTPException(status_code=404, detail="No such brand.")
    offered = {o["template_key"]: o
               for o in wf_service.offerings_for_brand(db, platform_id)}
    rows = []
    for key in wf_registry.ALL_TEMPLATE_KEYS:
        spec = wf_registry.template(key)
        current = offered.get(key)
        rows.append({
            "template_key": key,
            "platform_name": spec.name,
            "job_role": spec.job_role,
            "summary": spec.summary,
            "depth": spec.depth,
            "platform_channels": spec.channels,
            "offered": current is not None,
            "enabled": bool(current and current["enabled"]),
            "display_name": (current or {}).get("display_name") or spec.name,
            "channels": (current or {}).get("channels") or spec.channels,
            "entitlement_key": ((current or {}).get("entitlement_key")
                                or spec.entitlement_key),
            "required_feature": spec.required_feature,
        })
    return {"brand": {"id": brand.id, "name": brand.name, "slug": brand.slug},
            "offerings": rows,
            "note": ("A brand may narrow channels and tools. It can never "
                     "widen them past the platform template, and no price is "
                     "set here — the catalogue owns commercial terms.")}


class OfferingWrite(BaseModel):
    template_key: str
    enabled: bool = True
    display_name: Optional[str] = None
    description: Optional[str] = None
    channels: Optional[List[str]] = None
    tool_keys: Optional[List[str]] = None
    entitlement_key: Optional[str] = None
    brand_policy: Optional[Dict[str, Any]] = None


@router.put("/brands/{platform_id}/offerings")
def set_brand_offering(platform_id: str, payload: OfferingWrite,
                      db: Session = Depends(get_db),
                      user: User = Depends(require_god)) -> Dict[str, Any]:
    if db.query(Platform).filter(Platform.id == platform_id).first() is None:
        raise HTTPException(status_code=404, detail="No such brand.")
    try:
        wf_service.set_offering(
            db, platform_id=platform_id, template_key=payload.template_key,
            enabled=payload.enabled, display_name=payload.display_name,
            description=payload.description, channels=payload.channels,
            tool_keys=payload.tool_keys, brand_policy=payload.brand_policy,
            entitlement_key=payload.entitlement_key, actor_user_id=user.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.commit()
    return {"offerings": wf_service.offerings_for_brand(db, platform_id)}


# ── CUSTOMERS ───────────────────────────────────────────────────────────────

@router.get("/customers")
def list_customers_with_workforce(db: Session = Depends(get_db)
                                  ) -> Dict[str, Any]:
    """Every customer that has an AI employee, and what state it is in."""
    employees = db.query(AIEmployee).all()
    org_ids = sorted({e.organization_id for e in employees})
    orgs = {o.id: o for o in db.query(Organization)
            .filter(Organization.id.in_(org_ids)).all()} if org_ids else {}
    out = []
    for org_id in org_ids:
        org = orgs.get(org_id)
        mine = [e for e in employees if e.organization_id == org_id]
        resolved = wf_activation.resolve(db, organization_id=org_id,
                                         platform_id=getattr(org, "platform_id",
                                                             None))
        out.append({
            "organization_id": org_id,
            "name": getattr(org, "name", "(unknown)"),
            "is_demo": bool(getattr(org, "is_demo", False)),
            "platform_id": getattr(org, "platform_id", None),
            "employees": len(mine),
            "active": sum(1 for e in mine if e.status == "active"),
            "paused": sum(1 for e in mine if e.paused_at is not None),
            "effective_stage": resolved.state,
            "may_execute": resolved.may_execute,
            "queue": wf_queue.grouped_counts(db, organization_id=org_id),
        })
    return {"customers": out}


@router.get("/customers/{organization_id}")
def customer_detail(organization_id: str,
                    db: Session = Depends(get_db)) -> Dict[str, Any]:
    org = (db.query(Organization)
           .filter(Organization.id == organization_id).first())
    if org is None:
        raise HTTPException(status_code=404, detail="No such organization.")
    employees = (db.query(AIEmployee)
                 .filter(AIEmployee.organization_id == organization_id).all())
    from app.services.workforce import eligibility as wf_eligibility
    from app.services.workforce import performance as wf_performance
    return {
        "organization": {"id": org.id, "name": org.name,
                         "platform_id": org.platform_id,
                         "is_demo": bool(org.is_demo)},
        "activation": wf_activation.resolve(
            db, organization_id=organization_id,
            platform_id=org.platform_id).as_dict(),
        "employees": [wf_service.describe(db, e) for e in employees],
        "eligibility": wf_eligibility.summarize_org(db, organization_id),
        "performance": wf_performance.report(db,
                                             organization_id=organization_id),
        "supervisor": wf_supervisor.overview(db,
                                             organization_id=organization_id),
    }


# ── HEALTH ──────────────────────────────────────────────────────────────────

@router.get("/health")
def health(db: Session = Depends(get_db),
           days: int = Query(7, ge=1, le=60)) -> Dict[str, Any]:
    """Stalled work, failures, refusals — the things that need somebody."""
    from datetime import datetime, timedelta
    from sqlalchemy import func
    since = datetime.utcnow() - timedelta(days=days)
    stalled = wf_queue.stalled(db)
    denials = (db.query(AIToolExecution.denial_code,
                        func.count(AIToolExecution.id))
               .filter(AIToolExecution.decision == "denied",
                       AIToolExecution.created_at >= since)
               .group_by(AIToolExecution.denial_code).all())
    errors = (db.query(AIToolExecution)
              .filter(AIToolExecution.status == "error",
                      AIToolExecution.created_at >= since).count())
    events = (db.query(AISupervisorEvent)
              .filter(AISupervisorEvent.created_at >= since,
                      AISupervisorEvent.acknowledged_at.is_(None))
              .order_by(AISupervisorEvent.created_at.desc()).limit(50).all())
    return {
        "window_days": days,
        "stalled_work_items": len(stalled),
        "stalled_sample": [{"id": i.id, "employee_id": i.employee_id,
                            "state": i.state,
                            "updated_at": (i.updated_at.isoformat()
                                           if i.updated_at else None)}
                           for i in stalled[:20]],
        "tool_errors": errors,
        "denials": sorted([{"code": c or "unknown", "count": int(n)}
                           for c, n in denials],
                          key=lambda d: -d["count"]),
        "unacknowledged_events": [
            {"id": e.id, "severity": e.severity, "code": e.event_code,
             "message": e.message, "recommended_action": e.recommended_action,
             "employee_id": e.employee_id,
             "created_at": e.created_at.isoformat() if e.created_at else None}
            for e in events
        ],
    }


@router.post("/health/supervise")
def run_supervision(db: Session = Depends(get_db),
                    organization_id: Optional[str] = Query(None)
                    ) -> Dict[str, Any]:
    """Run a supervision sweep now. Releases dead leases; changes nothing else."""
    out = wf_supervisor.run_pass(db, organization_id=organization_id)
    db.commit()
    return out


class AcknowledgeRequest(BaseModel):
    event_id: str


@router.post("/health/acknowledge")
def acknowledge_event(payload: AcknowledgeRequest,
                      db: Session = Depends(get_db),
                      user: User = Depends(require_god)) -> Dict[str, Any]:
    row = (db.query(AISupervisorEvent)
           .filter(AISupervisorEvent.id == payload.event_id).first())
    if row is None:
        raise HTTPException(status_code=404, detail="No such event.")
    wf_supervisor.acknowledge(db, row, user)
    db.commit()
    return {"event_id": row.id, "acknowledged": True}


# ── EVALUATION AND SIMULATION ───────────────────────────────────────────────

@router.get("/evaluation/suites")
def evaluation_suites() -> Dict[str, Any]:
    return {"suites": wf_evaluation.describe_suites(),
            "dimensions": list(wf_simulator.DIMENSIONS),
            "scenarios": wf_simulator.catalogue()}


class EvaluationRequest(BaseModel):
    suite: str = "full"


@router.post("/evaluation/run")
def run_evaluation(payload: EvaluationRequest, db: Session = Depends(get_db),
                   user: User = Depends(require_god)) -> Dict[str, Any]:
    """Run an evaluation suite against the REAL engine, right here.

    Every scenario builds its own synthetic organization inside a savepoint and
    rolls back, so this reads and writes nothing belonging to a real customer
    — and it exercises the shipping gateway rather than a copy of it.
    """
    try:
        report = wf_evaluation.run(
            db, suite_key=payload.suite,
            environment=os.environ.get("APP_ENV", "production"),
            commit_ref=os.environ.get("RENDER_GIT_COMMIT")
            or os.environ.get("GIT_COMMIT"),
            triggered_by=user.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.commit()
    # The full result set is large; the caller gets the score and the failures.
    report.pop("results", None)
    return report


@router.get("/evaluation/history")
def evaluation_history(db: Session = Depends(get_db),
                       suite: Optional[str] = Query(None),
                       limit: int = Query(25, ge=1, le=100)) -> Dict[str, Any]:
    return {"runs": wf_evaluation.history(db, suite_key=suite, limit=limit)}


@router.get("/evaluation/runs/{run_id}")
def evaluation_detail(run_id: str,
                      db: Session = Depends(get_db)) -> Dict[str, Any]:
    out = wf_evaluation.detail(db, run_id)
    if not out:
        raise HTTPException(status_code=404, detail="No such evaluation run.")
    return out


@router.get("/simulation/profiles")
def list_profiles(db: Session = Depends(get_db)) -> Dict[str, Any]:
    described = wf_profiles.describe_all()
    for item in described:
        org = (db.query(Organization)
               .filter(Organization.slug ==
                       ("t6-dormant-database"
                        if item["key"] == wf_profiles.REACTIVATION_PROFILE
                        else "t6-energy-lifecycle")).first())
        item["built"] = org is not None
        item["organization_id"] = getattr(org, "id", None)
    return {"profiles": described,
            "note": ("Synthetic data only. Every contact is on a reserved "
                     "fictional number and an unresolvable email domain, and "
                     "the organizations are flagged as demonstrations.")}


class ProfileRequest(BaseModel):
    profile: str
    leads: int = 200
    raise_platform: bool = False
    seed_replies: bool = True


@router.post("/simulation/profiles/build")
def build_profile(payload: ProfileRequest, db: Session = Depends(get_db),
                  user: User = Depends(require_god)) -> Dict[str, Any]:
    """Build a synthetic proving profile.

    `raise_platform` moves the PLATFORM activation row to `simulation`, which
    reaches nobody and still leaves every real customer at `off` — a customer
    with no activation row resolves to off rather than inheriting. It is an
    explicit, recorded operator decision because it is the one setting here
    that changes something outside the synthetic organization.
    """
    try:
        result = wf_profiles.build(db, payload.profile,
                                   leads=max(1, min(int(payload.leads), 5000)),
                                   actor=user,
                                   raise_platform=bool(payload.raise_platform))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if payload.seed_replies:
        result["replies"] = wf_profiles.seed_replies(
            db, result["organization_id"])
    db.commit()
    return result


@router.post("/simulation/profiles/teardown")
def teardown_profile(payload: ProfileRequest, db: Session = Depends(get_db)
                     ) -> Dict[str, Any]:
    try:
        result = wf_profiles.teardown(db, payload.profile)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.commit()
    return result


class ScaleRequest(BaseModel):
    leads: int = 500
    employees: int = 2
    per_employee: int = 100


@router.post("/simulation/scale")
def run_scale(payload: ScaleRequest,
              db: Session = Depends(get_db)) -> Dict[str, Any]:
    """A load run through the real queue, against simulated adapters.

    Bounded hard: this runs inside a web request, and a scale test that times
    out a worker is a scale test that takes the platform down to measure it.
    """
    leads = max(10, min(int(payload.leads), 2000))
    employees = max(1, min(int(payload.employees), 5))
    per = max(10, min(int(payload.per_employee), 500))
    savepoint = db.begin_nested()
    try:
        report = wf_simulator.run_scale(db, leads=leads, employees=employees,
                                        per_employee=per)
    finally:
        # NOTHING A SCALE RUN CREATES SURVIVES IT. Thousands of synthetic
        # leads left in a production database would be a mess somebody else
        # has to clean up, and they would appear in every count.
        savepoint.rollback()
    return report
