"""EMPLOYEE LIFECYCLE — seeding the library, offering it, hiring, pausing.

THE FOUR LAYERS, EACH WITH ITS OWN VERB:

    platform   `sync_templates`  mirrors the CODE registry into the database so
                                 God Mode can list, version and disable a job
                                 without a deploy. The code stays the source.
    brand      `set_offering`    which approved jobs a brand sells, under what
                                 name, on which channels. Narrowing only.
    customer   `hire`            instantiate an entitled job in a workspace,
                                 with the business answers and the explicit
                                 tool grants that make it able to act.
    operator   `pause` / `resume` / `disable`

HIRING GRANTS AUTHORITY EXPLICITLY. `hire` writes one `ai_employee_authorities`
row per tool, because the employee-level gate is DEFAULT DENY — see the header
of policy.py. It would have been fewer lines to treat the template's list as
the grant; it would also have meant "this employee may text people" was a
consequence of which template was picked rather than a decision somebody made,
with a name and a timestamp on it.

NOTHING CREATED HERE IS SWITCHED ON. A new employee is `status="draft"` and
`activation_state="off"`, and the enclosing scopes are off as well, so the
first thing a new AI employee does is nothing. Section 33.
"""

import json
import logging
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.models import Organization, User
from app.models.workforce_models import (AIBrandOffering, AIEmployee,
                                         AIEmployeeAuthority,
                                         AIEmployeeTemplate)
from app.services.workforce import activation as wf_activation
from app.services.workforce import constants as C
from app.services.workforce import entitlement as wf_entitlement
from app.services.workforce import policy as wf_policy
from app.services.workforce import registry

_log = logging.getLogger(__name__)


# ── PLATFORM ────────────────────────────────────────────────────────────────

def sync_templates(db: Session, *, actor_user_id: Optional[str] = None) -> Dict:
    """Mirror the code registry into `ai_employee_templates`. Idempotent.

    UPDATES IN PLACE rather than deleting and recreating: employees reference a
    template by id, and recreating rows would orphan every hired employee on
    the platform. A template that disappears from the code registry is
    DEACTIVATED, never deleted, for the same reason.
    """
    created, updated, deactivated = 0, 0, 0
    for key in registry.ALL_TEMPLATE_KEYS:
        spec = registry.template(key)
        row = (db.query(AIEmployeeTemplate)
               .filter(AIEmployeeTemplate.key == key).first())
        if row is None:
            row = AIEmployeeTemplate(key=key)
            db.add(row)
            created += 1
        else:
            updated += 1
        row.name = spec.name
        row.job_role = spec.job_role
        row.summary = spec.summary
        row.description = spec.description
        row.default_objective = spec.objective
        row.allowed_tool_keys = json.dumps(spec.tool_keys)
        row.allowed_channels = json.dumps(spec.channels)
        row.default_policy = json.dumps(dict(spec.policy, depth=spec.depth))
        row.config_questions = json.dumps(spec.questions)
        row.entitlement_key = spec.entitlement_key
        row.required_feature = spec.required_feature
        row.is_active = True
        row.updated_at = datetime.utcnow()
    for row in db.query(AIEmployeeTemplate).all():
        if row.key not in registry.TEMPLATES and row.is_active:
            row.is_active = False
            deactivated += 1
    wf_activation.ensure_platform_row(db)
    db.flush()
    _log.info("workforce: templates synced (created=%d updated=%d "
              "deactivated=%d) by %s", created, updated, deactivated,
              actor_user_id)
    return {"created": created, "updated": updated,
            "deactivated": deactivated,
            "total": len(registry.ALL_TEMPLATE_KEYS)}


def template_row(db: Session, key: str) -> Optional[AIEmployeeTemplate]:
    return (db.query(AIEmployeeTemplate)
            .filter(AIEmployeeTemplate.key == key).first())


# ── BRAND ───────────────────────────────────────────────────────────────────

def set_offering(db: Session, *, platform_id: str, template_key: str,
                 enabled: bool = True, display_name: Optional[str] = None,
                 description: Optional[str] = None,
                 channels: Optional[List[str]] = None,
                 tool_keys: Optional[List[str]] = None,
                 brand_policy: Optional[Dict] = None,
                 entitlement_key: Optional[str] = None,
                 actor_user_id: Optional[str] = None) -> AIBrandOffering:
    """Configure what one brand offers. NARROWING ONLY.

    `registry.normalize_*` takes the template's list as the bound, so a brand
    asking for a tool the job does not carry gets it dropped rather than
    granted. That is a property of the code rather than a rule somebody has to
    follow when editing a brand.
    """
    row_tpl = template_row(db, template_key)
    if row_tpl is None:
        raise ValueError("Unknown AI employee template: %s" % template_key)
    spec = registry.template(template_key)
    bound_tools = (spec.tool_keys if spec else
                   wf_policy.json_list(row_tpl.allowed_tool_keys, []) or [])
    bound_channels = (spec.channels if spec else
                      wf_policy.json_list(row_tpl.allowed_channels, []) or [])

    row = (db.query(AIBrandOffering)
           .filter(AIBrandOffering.platform_id == platform_id,
                   AIBrandOffering.template_id == row_tpl.id).first())
    if row is None:
        row = AIBrandOffering(platform_id=platform_id, template_id=row_tpl.id)
        db.add(row)
    row.is_enabled = bool(enabled)
    row.display_name = (display_name or row.display_name or row_tpl.name)
    row.description = description or row.description or row_tpl.summary
    if channels is not None:
        row.allowed_channels = json.dumps(
            registry.normalize_channels(channels, bound=bound_channels))
    if tool_keys is not None:
        row.allowed_tool_keys = json.dumps(
            registry.normalize_tool_keys(tool_keys, bound=bound_tools))
    if brand_policy is not None:
        row.brand_policy = json.dumps(brand_policy)[:8000]
    if entitlement_key is not None:
        row.entitlement_key = entitlement_key or None
    row.updated_by = actor_user_id
    row.updated_at = datetime.utcnow()
    db.flush()
    return row


def offerings_for_brand(db: Session, platform_id: str,
                        include_disabled: bool = True) -> List[Dict]:
    q = (db.query(AIBrandOffering, AIEmployeeTemplate)
         .join(AIEmployeeTemplate,
               AIBrandOffering.template_id == AIEmployeeTemplate.id)
         .filter(AIBrandOffering.platform_id == platform_id))
    if not include_disabled:
        q = q.filter(AIBrandOffering.is_enabled.is_(True))
    out = []
    for offering, tpl in q.order_by(AIBrandOffering.sort_order.asc()).all():
        out.append({
            "offering_id": offering.id,
            "template_key": tpl.key,
            "job_role": tpl.job_role,
            "display_name": offering.display_name or tpl.name,
            "description": offering.description or tpl.summary,
            "enabled": bool(offering.is_enabled),
            "channels": wf_policy.json_list(offering.allowed_channels)
            or wf_policy.json_list(tpl.allowed_channels, []),
            "entitlement_key": offering.entitlement_key or tpl.entitlement_key,
            "required_feature": tpl.required_feature,
        })
    return out


def catalogue_for_customer(db: Session, organization_id: str) -> List[Dict]:
    """What this customer could hire, and whether they are entitled to it.

    Shows unentitled jobs rather than hiding them — a customer who cannot see
    that an AI Appointment Setter exists cannot ask for one. What they get is
    the job, its description, and an honest `available` flag with a reason.
    """
    org = (db.query(Organization)
           .filter(Organization.id == organization_id).first())
    if org is None:
        return []
    offerings = (offerings_for_brand(db, org.platform_id, include_disabled=False)
                 if org.platform_id else [])
    if not offerings:
        # NO BRAND CONFIGURATION IS NOT AN EMPTY PRODUCT. Fall back to the
        # platform library so a customer on a brand nobody has configured yet
        # sees the jobs that exist, all correctly marked unavailable.
        offerings = [{"offering_id": None, "template_key": k,
                      "job_role": registry.template(k).job_role,
                      "display_name": registry.template(k).name,
                      "description": registry.template(k).summary,
                      "enabled": False,
                      "channels": registry.template(k).channels,
                      "entitlement_key": registry.template(k).entitlement_key,
                      "required_feature": registry.template(k).required_feature}
                     for k in registry.ALL_TEMPLATE_KEYS]
    out = []
    for item in offerings:
        ent = wf_entitlement.entitlement_state(db, organization_id,
                                               item["entitlement_key"])
        feat = wf_entitlement.feature_state(db, organization_id,
                                            item["required_feature"])
        spec = registry.template(item["template_key"])
        out.append(dict(item, **{
            "available": bool(item["enabled"] and ent["satisfied"]
                              and feat["satisfied"]),
            "entitlement": ent,
            "feature": feat,
            "depth": spec.depth if spec else "architected",
            "questions": spec.questions if spec else [],
        }))
    return out


# ── CUSTOMER ────────────────────────────────────────────────────────────────

def hire(db: Session, *, organization_id: str, template_key: str,
         name: Optional[str] = None, actor: Optional[User] = None,
         config: Optional[Dict] = None, channels: Optional[List[str]] = None,
         objective: Optional[str] = None,
         operating_hours: Optional[Dict] = None,
         timezone: Optional[str] = None,
         handoff_user_id: Optional[str] = None,
         handoff_queue: Optional[str] = None,
         audience_criteria: Optional[Dict] = None,
         knowledge_binding: Optional[Dict] = None,
         daily_work_cap: Optional[int] = None,
         tool_keys: Optional[List[str]] = None) -> AIEmployee:
    """Instantiate a job in a customer workspace. Starts OFF and in draft."""
    org = (db.query(Organization)
           .filter(Organization.id == organization_id).first())
    if org is None:
        raise ValueError("Unknown organization.")
    tpl = template_row(db, template_key)
    if tpl is None or not tpl.is_active:
        raise ValueError("Unknown or inactive AI employee template.")
    spec = registry.template(template_key)

    offering = None
    if org.platform_id:
        offering = (db.query(AIBrandOffering)
                    .filter(AIBrandOffering.platform_id == org.platform_id,
                            AIBrandOffering.template_id == tpl.id).first())

    bound_tools = (spec.tool_keys if spec else
                   wf_policy.json_list(tpl.allowed_tool_keys, []) or [])
    bound_channels = (spec.channels if spec else
                      wf_policy.json_list(tpl.allowed_channels, []) or [])
    if offering is not None:
        brand_tools = wf_policy.json_list(offering.allowed_tool_keys)
        if brand_tools is not None:
            bound_tools = registry.normalize_tool_keys(brand_tools,
                                                       bound=bound_tools)
        brand_channels = wf_policy.json_list(offering.allowed_channels)
        if brand_channels is not None:
            bound_channels = registry.normalize_channels(brand_channels,
                                                         bound=bound_channels)

    granted = registry.normalize_tool_keys(
        tool_keys if tool_keys is not None else bound_tools, bound=bound_tools)
    employee_channels = registry.normalize_channels(
        channels if channels is not None else bound_channels,
        bound=bound_channels)

    emp = AIEmployee(
        organization_id=organization_id,
        platform_id=org.platform_id,
        template_id=tpl.id,
        offering_id=getattr(offering, "id", None),
        name=(name or (offering.display_name if offering else None)
              or tpl.name)[:120],
        job_role=tpl.job_role,
        status="draft",
        activation_state=C.DEFAULT_ACTIVATION,
        objective=objective or tpl.default_objective,
        allowed_channels=json.dumps(employee_channels),
        config=json.dumps(config or {})[:8000],
        operating_hours=json.dumps(operating_hours or {}),
        timezone=timezone or "America/Chicago",
        daily_work_cap=daily_work_cap,
        handoff_user_id=handoff_user_id,
        handoff_queue=(handoff_queue or None),
        audience_criteria=json.dumps(audience_criteria or {})[:8000],
        knowledge_binding=json.dumps(knowledge_binding or {})[:4000],
        created_by=getattr(actor, "id", None),
    )
    db.add(emp)
    db.flush()

    for key in granted:
        db.add(AIEmployeeAuthority(
            employee_id=emp.id, organization_id=organization_id,
            tool_key=key, is_allowed=True,
            granted_by=getattr(actor, "id", None)))
    db.flush()

    from app.services.workforce import audit as wf_audit
    wf_audit.write_platform_audit(
        db, emp, action="ai_workforce.employee_hired",
        target_type="ai_employee", target_id=emp.id,
        details={"template_key": template_key, "tools": granted,
                 "channels": employee_channels},
        note="AI employee created. It starts disabled and does nothing until "
             "an operator activates it.")
    return emp


def set_authority(db: Session, employee: AIEmployee, tool_keys: List[str], *,
                  actor: Optional[User] = None) -> List[str]:
    """Replace the explicit grants. Still bounded by template and brand."""
    pol = wf_policy.resolve(db, employee)
    spec = registry.template(pol.template_key or "")
    bound = spec.tool_keys if spec else []
    if pol.offering is not None:
        brand_tools = wf_policy.json_list(pol.offering.allowed_tool_keys)
        if brand_tools is not None:
            bound = registry.normalize_tool_keys(brand_tools, bound=bound)
    wanted = set(registry.normalize_tool_keys(tool_keys, bound=bound))

    existing = {g.tool_key: g for g in
                db.query(AIEmployeeAuthority)
                .filter(AIEmployeeAuthority.employee_id == employee.id).all()}
    for key, row in existing.items():
        row.is_allowed = key in wanted
        row.updated_at = datetime.utcnow()
    for key in wanted - set(existing):
        db.add(AIEmployeeAuthority(
            employee_id=employee.id, organization_id=employee.organization_id,
            tool_key=key, is_allowed=True, granted_by=getattr(actor, "id", None)))
    db.flush()
    return sorted(wanted)


def activate(db: Session, employee: AIEmployee, stage: str, *,
             actor: Optional[User] = None,
             reason: Optional[str] = None) -> AIEmployee:
    """Move an employee's own activation stage.

    Does NOT check the enclosing scopes, and does not need to: resolution takes
    the minimum, so an employee set to `active` under a platform set to `off`
    is off. Refusing the write would stop an operator staging a customer's
    configuration ahead of a platform switch-on.
    """
    if stage not in C.ACTIVATION_RANK:
        raise ValueError("Unknown activation stage: %s" % stage)
    employee.activation_state = stage
    if stage != C.OFF and employee.status == "draft":
        employee.status = "active"
    employee.updated_at = datetime.utcnow()
    db.flush()
    from app.services.workforce import audit as wf_audit
    wf_audit.write_platform_audit(
        db, employee, action="ai_workforce.employee_activation_changed",
        target_type="ai_employee", target_id=employee.id,
        details={"stage": stage, "reason": reason,
                 "changed_by": getattr(actor, "id", None)})
    return employee


def pause(db: Session, employee: AIEmployee, *, reason: str,
          actor: Optional[User] = None, pause_queue: bool = True) -> AIEmployee:
    """Stop this employee now, and stop its queued work too.

    Pausing the ITEMS as well as the employee is what makes the screen honest:
    `activation.resolve` already refuses the next tool call, and without the
    second half the queue would keep showing records as "working" while nothing
    was happening.
    """
    employee.paused_at = datetime.utcnow()
    employee.paused_by = getattr(actor, "id", None)
    employee.pause_reason = (reason or "")[:255] or None
    db.flush()
    paused_items = 0
    if pause_queue:
        from app.services.workforce import queue as wf_queue
        paused_items = wf_queue.pause_all(db, employee_id=employee.id,
                                          reason="employee paused: %s" % reason,
                                          actor_id=getattr(actor, "id", None))
    from app.services.workforce import audit as wf_audit
    wf_audit.write_platform_audit(
        db, employee, action="ai_workforce.employee_paused",
        target_type="ai_employee", target_id=employee.id,
        details={"reason": reason, "work_items_paused": paused_items})
    return employee


def resume(db: Session, employee: AIEmployee, *, actor: Optional[User] = None,
           reason: str = "resumed by operator") -> AIEmployee:
    employee.paused_at = None
    employee.paused_by = None
    employee.pause_reason = None
    db.flush()
    from app.services.workforce import queue as wf_queue
    resumed = wf_queue.resume_all(db, employee_id=employee.id, reason=reason,
                                  actor_id=getattr(actor, "id", None))
    from app.services.workforce import audit as wf_audit
    wf_audit.write_platform_audit(
        db, employee, action="ai_workforce.employee_resumed",
        target_type="ai_employee", target_id=employee.id,
        details={"work_items_resumed": resumed})
    return employee


def disable(db: Session, employee: AIEmployee, *, actor: Optional[User] = None,
            reason: str = "") -> AIEmployee:
    employee.status = "disabled"
    employee.activation_state = C.OFF
    employee.updated_at = datetime.utcnow()
    db.flush()
    from app.services.workforce import queue as wf_queue
    wf_queue.pause_all(db, employee_id=employee.id,
                       reason="employee disabled", actor_id=getattr(actor, "id", None))
    from app.services.workforce import audit as wf_audit
    wf_audit.write_platform_audit(
        db, employee, action="ai_workforce.employee_disabled",
        target_type="ai_employee", target_id=employee.id,
        details={"reason": reason})
    return employee


# ── READING ─────────────────────────────────────────────────────────────────

def describe(db: Session, employee: AIEmployee, *, include_policy: bool = True
             ) -> Dict:
    """The employee detail payload. BUSINESS LANGUAGE, not internals.

    Section 51: a customer reads "Working", "Waiting for Response", "Allowed
    Channels". They never read a system prompt, a token budget or a tool JSON
    schema — `tool_keys` is rendered as human labels, and the raw keys are
    present only for the God surface.
    """
    from app.services.workforce import knowledge as wf_knowledge
    from app.services.workforce import queue as wf_queue
    pol = wf_policy.resolve(db, employee)
    resolved = wf_activation.resolve(db, employee=employee)
    tpl = pol.template
    spec = registry.template(pol.template_key or "")

    out = {
        "id": employee.id,
        "name": employee.name,
        "job_role": employee.job_role,
        "job_title": (spec.name if spec else (tpl.name if tpl else
                                              employee.job_role)),
        "what_it_does": (spec.summary if spec else
                         (tpl.summary if tpl else None)),
        "objective": employee.objective,
        "status": employee.status,
        "paused": employee.paused_at is not None,
        "pause_reason": employee.pause_reason,
        "activation": resolved.as_dict(),
        "channels": sorted(pol.channels),
        "operating_hours": pol.operating_hours,
        "timezone": pol.timezone,
        "daily_work_cap": employee.daily_work_cap,
        "handoff_user_id": employee.handoff_user_id,
        "handoff_queue": employee.handoff_queue,
        "config": wf_policy.json_obj(employee.config),
        "questions": (spec.questions if spec else
                      wf_policy.json_list(getattr(tpl, "config_questions", None),
                                          []) or []),
        "can_do": [
            {"key": k, "label": registry.tool(k).label,
             "category": registry.tool(k).category}
            for k in sorted(pol.tool_keys) if registry.tool(k) is not None
        ],
        "knowledge": wf_knowledge.describe_binding(
            db, employee.organization_id,
            wf_policy.json_obj(employee.knowledge_binding)),
        "queue": wf_queue.grouped_counts(db,
                                         organization_id=employee.organization_id,
                                         employee_id=employee.id),
        "created_at": (employee.created_at.isoformat()
                       if employee.created_at else None),
    }
    if include_policy:
        out["limits"] = {
            "max_touches": pol.max_touches,
            "max_iterations": pol.max_iterations,
            "max_tool_calls": pol.max_tool_calls,
        }
        out["entitlement_key"] = pol.entitlement_key
        out["required_feature"] = pol.required_feature
    return out


def list_for_org(db: Session, organization_id: str) -> List[Dict]:
    from app.services.workforce import queue as wf_queue
    employees = (db.query(AIEmployee)
                 .filter(AIEmployee.organization_id == organization_id)
                 .order_by(AIEmployee.created_at.asc()).all())
    counts = wf_queue.counts_by_state(db, organization_id=organization_id)
    _ = counts
    out = []
    for emp in employees:
        resolved = wf_activation.resolve(db, employee=emp)
        groups = wf_queue.grouped_counts(db, organization_id=organization_id,
                                         employee_id=emp.id)
        spec = registry.template("")
        _ = spec
        out.append({
            "id": emp.id, "name": emp.name, "job_role": emp.job_role,
            "status": emp.status, "paused": emp.paused_at is not None,
            "activation_state": resolved.state,
            "running": resolved.may_run,
            "channels": wf_policy.json_list(emp.allowed_channels, []) or [],
            "queue": {g["key"]: g["count"] for g in groups},
        })
    return out
