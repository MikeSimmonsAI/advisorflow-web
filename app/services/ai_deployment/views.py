"""THE PAYLOADS - one shape per audience, built once.

TWO ROUTERS RENDER THE SAME FACTS AND MUST NOT DISAGREE ABOUT THEM. A customer
screen that says "Ready" beside a God screen that says "Not entitled" is a
support call, so both read from here.

BUSINESS LANGUAGE ON THE CUSTOMER SIDE. What comes back is "Working", "Needs
attention", "who takes over", "what it can do". What does NOT come back is a
tool key, a schema, an activation chain or a template key dressed up as a
product name - `describe` renders capabilities as human labels, and the raw
keys stay on the God payload where an operator needs them.
"""

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.ai_deployment_models import (AIDeploymentEvent,
                                             AIEmployeeDeployment)
from app.models.models import Organization
from app.services.ai_deployment import commerce
from app.services.ai_deployment import configuration as cfg_mod
from app.services.ai_deployment import constants as D
from app.services.ai_deployment import readiness as t8_readiness

_log = logging.getLogger(__name__)


def describe(db: Session, deployment: AIEmployeeDeployment, *,
             for_operator: bool = False,
             refresh_readiness: bool = False) -> Dict[str, Any]:
    """One deployment, as a customer reads it.

    `refresh_readiness` recomputes rather than reading the stored snapshot. It
    is off by default because this payload is rendered in lists, and a list of
    thirty employees recomputing thirty readiness verdicts is a list that takes
    a second to load for information nobody asked to be that fresh.
    """
    org = (db.query(Organization)
           .filter(Organization.id == deployment.organization_id).first())
    config = cfg_mod.config_of(deployment)
    offer = commerce.resolve_offer(db, org, deployment.template_key) \
        if org is not None else None

    if refresh_readiness:
        ready = t8_readiness.refresh(db, deployment).as_dict()
    else:
        ready = t8_readiness.stored(deployment)

    out: Dict[str, Any] = {
        "id": deployment.id,
        "name": deployment.display_name,
        "job": _job_label(deployment, offer),
        "state": deployment.state,
        "state_label": D.STATE_LABELS.get(deployment.state, deployment.state),
        "why": deployment.state_reason,
        "created_at": (deployment.created_at.isoformat()
                       if deployment.created_at else None),
        "activated_at": (deployment.activated_at.isoformat()
                         if deployment.activated_at else None),
        "configuration": config,
        "configuration_version": deployment.config_version,
        "readiness": ready,
        "commerce": offer.as_dict() if offer is not None else {
            "commercial_state": deployment.commercial_state,
            "detail": deployment.commercial_detail},
        "channels": list(config.get("channels") or []),
        "handoff": {
            "user_id": config.get("handoff_user_id") or config.get("handoff_to"),
            "team": config.get("handoff_team"),
            "backup_user_id": config.get("backup_owner"),
            "next_ai_employee": config.get("handoff_to_employee_id"),
            "escalation_conditions": list(
                config.get("escalation_conditions") or []),
        },
        "can": _capabilities(db, deployment),
        "employee_id": deployment.employee_id if for_operator else None,
    }
    if for_operator:
        out["template_key"] = deployment.template_key
        out["provisioning_key"] = deployment.provisioning_key
        out["organization_id"] = deployment.organization_id
        out["platform_id"] = deployment.platform_id
        out["entitlement_key"] = deployment.entitlement_key
        out["catalog_item_key"] = deployment.catalog_item_key
        out["purchase_id"] = deployment.purchase_id
        out["requested_stage"] = deployment.requested_stage
        out["stage_before_pause"] = deployment.stage_before_pause
    return out


def _job_label(deployment: AIEmployeeDeployment, offer) -> str:
    """What this job is called HERE. The brand's word, never the platform's."""
    brand_name = getattr(getattr(offer, "offering", None), "display_name", None)
    if brand_name:
        return brand_name
    from app.services.workforce import registry as wf_registry
    spec = wf_registry.template(deployment.template_key)
    return spec.name if spec else deployment.template_key


def _capabilities(db: Session, deployment: AIEmployeeDeployment
                  ) -> List[Dict[str, str]]:
    """What this employee can do, in words. Tool keys stay out of this list."""
    if not deployment.employee_id:
        from app.services.workforce import registry as wf_registry
        spec = wf_registry.template(deployment.template_key)
        if spec is None:
            return []
        return [{"label": wf_registry.tool(k).label,
                 "category": wf_registry.tool(k).category}
                for k in spec.tool_keys if wf_registry.tool(k) is not None]
    from app.models.workforce_models import AIEmployee
    from app.services.workforce import policy as wf_policy
    from app.services.workforce import registry as wf_registry
    emp = (db.query(AIEmployee)
           .filter(AIEmployee.id == deployment.employee_id,
                   AIEmployee.organization_id == deployment.organization_id)
           .first())
    if emp is None:
        return []
    pol = wf_policy.resolve(db, emp)
    return [{"label": wf_registry.tool(k).label,
             "category": wf_registry.tool(k).category}
            for k in sorted(pol.tool_keys) if wf_registry.tool(k) is not None]


def recent_activity(db: Session, deployment: AIEmployeeDeployment,
                    limit: int = 20) -> List[Dict[str, Any]]:
    """What happened to this employee lately, in plain words.

    DEPLOYMENT EVENTS AND ENGINE ACTIVITY, MERGED AND LABELLED. A customer
    asking "what has it been doing" means both "somebody paused it" and "it
    tried to text somebody and was refused", and splitting those across two
    screens is how the answer stops being findable.
    """
    out: List[Dict[str, Any]] = []
    for row in (db.query(AIDeploymentEvent)
                .filter(AIDeploymentEvent.deployment_id == deployment.id)
                .order_by(AIDeploymentEvent.created_at.desc())
                .limit(limit).all()):
        out.append({
            "at": row.created_at.isoformat() if row.created_at else None,
            "kind": "deployment",
            "what": "%s -> %s" % (D.STATE_LABELS.get(row.from_state,
                                                     row.from_state or "new"),
                                  D.STATE_LABELS.get(row.to_state,
                                                     row.to_state)),
            "why": row.reason,
            "by": row.actor_kind,
        })
    if deployment.employee_id:
        try:
            from app.models.workforce_models import AIToolExecution
            from app.services.workforce import registry as wf_registry
            for row in (db.query(AIToolExecution)
                        .filter(AIToolExecution.employee_id
                                == deployment.employee_id,
                                AIToolExecution.organization_id
                                == deployment.organization_id)
                        .order_by(AIToolExecution.created_at.desc())
                        .limit(limit).all()):
                spec = wf_registry.tool(row.tool_key)
                out.append({
                    "at": (row.created_at.isoformat() if row.created_at
                           else None),
                    "kind": "work",
                    "what": spec.label if spec else row.tool_key,
                    "why": row.denial_reason,
                    "by": ("Refused" if row.decision == "denied"
                           else ("Simulated" if row.simulated else "Done")),
                })
        except Exception as exc:                              # noqa: BLE001
            _log.info("ai_deployment: activity merge skipped (%s)", exc)
    out.sort(key=lambda r: r["at"] or "", reverse=True)
    return out[:limit]


def workforce_summary(db: Session, organization_id: str) -> Dict[str, Any]:
    """MY AI WORKFORCE - the whole screen's data in one call.

    ONE CALL ON PURPOSE. The alternative is a screen that renders the header
    from one answer and the rows from another, taken a moment apart, and then
    explains to a customer why the count disagrees with the list.
    """
    from app.services.ai_deployment import activation as t8_activation
    from app.services.ai_deployment import capacity, catalog, lifecycle

    rows = lifecycle.list_for_org(db, organization_id, include_retired=True)
    mine = [r for r in rows if r.state != D.RETIRED]
    live = [r for r in mine if r.state in D.LIVE_STATES]

    return {
        "organization_id": organization_id,
        "status_line": _status_line(mine, live),
        "workforce": [describe(db, r) for r in mine],
        "retired": [{"id": r.id, "name": r.display_name,
                     "retired_at": (r.retired_at.isoformat()
                                    if r.retired_at else None),
                     "reason": r.retire_reason}
                    for r in rows if r.state == D.RETIRED],
        "available": catalog.customer_catalog(db, organization_id),
        "capacity": capacity.report(
            db, db.query(Organization)
            .filter(Organization.id == organization_id).first()),
        "platform": t8_activation.operational_capability(db),
    }


def _status_line(mine: List[AIEmployeeDeployment],
                 live: List[AIEmployeeDeployment]) -> str:
    """Said plainly, because "why is nothing happening" is the first question.

    The answer is almost always one of these four sentences, and a customer
    should not have to interpret a status badge to get it.
    """
    if not mine:
        return ("You have not hired any AI employees yet.")
    suspended = [r for r in mine if r.state == D.SUSPENDED]
    if suspended and not live:
        return ("%d of your AI employees are stopped because of their "
                "account standing." % len(suspended))
    if not live:
        return ("You have %d AI employee%s set up. None of them is working "
                "yet." % (len(mine), "" if len(mine) == 1 else "s"))
    return ("%d of your %d AI employees %s working."
            % (len(live), len(mine), "is" if len(live) == 1 else "are"))
