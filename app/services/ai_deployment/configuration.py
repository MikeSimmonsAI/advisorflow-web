"""GUIDED CONFIGURATION - the customer describes their business, not the AI.

SECTION 4 IS THE WHOLE DESIGN. A customer answers questions about how their
business works. They never see a system prompt, a model name, a temperature, a
tool key, a provider credential or anything resembling a query. That is not
achieved by declining to render those fields - it is achieved by REFUSING THEM
ON THE WAY IN, because a field that is never displayed and is still accepted is
a field somebody reaches with curl.

WHERE THE QUESTIONS COME FROM. Two places, merged:

    the platform template   T6's registry already declares the business
                            questions each job needs - the goal, the audience,
                            who receives handoffs. Those are the job's own, and
                            they are read rather than restated.
    this layer              the questions that are about DEPLOYING an employee
                            rather than about the job: which location, which
                            territory, which appointment type, who covers the
                            handoff owner, how persistent follow-up should be.

WHAT CONFIGURATION DOES NOT DO. It does not activate anything, it does not
grant a tool, and it does not widen a channel. Channels written here are
intersected against the employee's effective policy by T6 on every use; this
layer narrows the stored value too, so the screen shows what is in force rather
than what somebody asked for.

LOOPS ARE DETECTED, NOT DOCUMENTED. Section 8 asks for AI-to-AI handoff and
then says do not allow arbitrary circular chains. `detect_loop` walks the chain
before the write lands, so a cycle is a refusal with the path in it rather than
two employees passing a family back and forth.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.ai_deployment_models import AIEmployeeDeployment
from app.models.models import Organization, User
from app.services.ai_deployment import constants as D

_log = logging.getLogger(__name__)

MAX_CONFIG_BYTES = 8000
MAX_LIST_ITEMS = 25
MAX_TEXT_CHARS = 500


class ConfigurationRefused(ValueError):
    """A configuration was refused, and `problems` says everything wrong.

    A list rather than the first failure, so somebody setting an employee up is
    told everything at once instead of discovering the problems one save at a
    time - the same shape `brand_catalog.validate` already uses.
    """

    def __init__(self, problems: List[Dict[str, Any]]):
        self.problems = list(problems)
        super().__init__("; ".join(p.get("message", "") for p in self.problems))


# ---------------------------------------------------------------------------
# THE SCHEMA A CUSTOMER IS SHOWN
# ---------------------------------------------------------------------------

def schema_for(db: Session, organization_id: str,
               template_key: str) -> Dict[str, Any]:
    """The questions for this job, in this workspace, with real options.

    OPTIONS ARE RESOLVED FROM THE CUSTOMER'S OWN DATA. A "who receives
    handoffs" question whose options are a free-text box is a question that
    gets answered with a name nobody can route to.
    """
    from app.services.workforce import registry as wf_registry
    from app.services.ai_deployment import catalog

    spec = wf_registry.template(template_key)
    if spec is None:
        raise ValueError("Unknown AI employee job.")

    org = (db.query(Organization)
           .filter(Organization.id == organization_id).first())
    reqs = catalog.requirements_for(template_key)

    users = [{"value": u.id, "label": u.full_name or u.email}
             for u in (db.query(User)
                       .filter(User.organization_id == organization_id,
                               User.is_active.is_(True))
                       .order_by(User.full_name.asc()).limit(200).all())]

    appointment_types: List[Dict[str, str]] = []
    try:
        raw = json.loads(getattr(org, "appointment_types", None) or "[]")
        for entry in (raw if isinstance(raw, list) else []):
            label = entry.get("label") if isinstance(entry, dict) else entry
            if label:
                appointment_types.append({"value": str(label),
                                          "label": str(label)})
    except (ValueError, TypeError):
        appointment_types = []

    knowledge_kinds = [
        {"value": "organization_profile", "label": "Your business details"},
        {"value": "tier_definitions", "label": "How you describe each enquiry"},
        {"value": "message_templates", "label": "Your approved wording"},
    ]

    peers = [{"value": r.id, "label": r.display_name or r.template_key}
             for r in (db.query(AIEmployeeDeployment)
                       .filter(AIEmployeeDeployment.organization_id
                               == organization_id,
                               AIEmployeeDeployment.state != D.RETIRED)
                       .all())]

    options = {
        "user": users,
        "appointment_type": appointment_types,
        "knowledge": knowledge_kinds,
        "ai_employee": peers,
        "channels": [{"value": c, "label": c.upper()}
                     for c in reqs["channels"]],
        "followup": [
            {"value": "gentle", "label": "Gentle - a couple of tries"},
            {"value": "standard", "label": "Standard"},
            {"value": "persistent", "label": "Persistent, within your limits"},
        ],
    }

    fields = []
    # The job's own questions first: they are what the job needs to do its
    # work, and a customer reading top to bottom should meet those before the
    # deployment details.
    for q in spec.questions:
        fields.append({
            "key": q.get("key"),
            "label": q.get("label"),
            "type": q.get("type", "text"),
            "required": bool(q.get("required")),
            "source": "job",
            "options": options.get(q.get("type"), []),
        })
    seen = {f["key"] for f in fields}
    for key, label, kind, required in D.BUSINESS_FIELDS:
        if key in seen:
            continue
        # A question about something this job cannot do is noise. An employee
        # with no calendar tools is not asked whose calendar to book into.
        if key in ("appointment_type", "booking_owner") \
                and not reqs["needs_calendar"]:
            continue
        if key == "voice_enabled" and "voice" not in reqs["channels"]:
            continue
        if key in ("channels",) and not reqs["channels"]:
            continue
        fields.append({
            "key": key, "label": label, "type": kind,
            "required": bool(required), "source": "deployment",
            "options": options.get(kind, []),
        })

    return {
        "template_key": template_key,
        "fields": fields,
        "requirements": reqs,
        # SAID OUT LOUD on the screen, because the promise is part of the
        # product: this is a business interview, not a model configuration.
        "note": ("These are questions about your business. This screen never "
                 "asks you to configure the AI itself."),
    }


# ---------------------------------------------------------------------------
# VALIDATION
# ---------------------------------------------------------------------------

def _clean_scalar(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip()[:MAX_TEXT_CHARS]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    return None


def _clean_list(value: Any) -> List[str]:
    if not isinstance(value, (list, tuple)):
        return []
    out = []
    for item in value:
        if isinstance(item, str) and item.strip():
            out.append(item.strip()[:MAX_TEXT_CHARS])
        if len(out) >= MAX_LIST_ITEMS:
            break
    return out


def _problem(key: str, code: str, message: str) -> Dict[str, Any]:
    return {"field": key, "code": code, "message": message}


def validate(db: Session, *, organization_id: str, template_key: str,
             answers: Dict[str, Any],
             deployment_id: Optional[str] = None
             ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Clean the answers and list everything wrong with them.

    Returns (cleaned, problems). A caller that wants an exception raises
    `ConfigurationRefused` itself; validation that raised would make it
    impossible to save a partially-complete configuration, and saving
    incomplete work is exactly what the CONFIGURING state is for.
    """
    problems: List[Dict[str, Any]] = []
    cleaned: Dict[str, Any] = {}

    schema = schema_for(db, organization_id, template_key)
    known = {f["key"]: f for f in schema["fields"]}

    for raw_key, raw_value in (answers or {}).items():
        key = (raw_key or "").strip()
        fragment = D.forbidden_reason(key)
        if fragment is not None:
            # REFUSED, AND NAMED. The person fixing this is usually an
            # integrator who typed something reasonable-looking; telling them
            # which word was the problem is the difference between a fix and a
            # support ticket.
            problems.append(_problem(
                key, D.R_FORBIDDEN_CONFIG,
                "%r is not something a customer configures. AI employees are "
                "described in business terms; %r is an internal setting."
                % (key, fragment)))
            continue
        field = known.get(key)
        if field is None:
            # Unknown keys are DROPPED rather than refused. T6's own `hire`
            # already reads only the keys a template declared, so an extra key
            # configures nothing - and refusing would break a brand's screen
            # every time the platform added a question.
            continue
        kind = field["type"]
        if kind in ("text_list", "knowledge", "channels"):
            cleaned[key] = _clean_list(raw_value)
        elif kind in ("hours", "audience", "followup_policy"):
            cleaned[key] = raw_value if isinstance(raw_value, dict) else {}
        elif kind == "boolean":
            cleaned[key] = bool(raw_value)
        else:
            value = _clean_scalar(raw_value)
            if value is not None:
                cleaned[key] = value

    # --- Cross-field business rules ----------------------------------------
    reqs = schema["requirements"]

    hours = cleaned.get("working_hours") or cleaned.get("hours")
    if isinstance(hours, dict) and hours:
        start, end = hours.get("start"), hours.get("end")
        if not start or not end:
            problems.append(_problem(
                "working_hours", D.R_MISSING_CONFIG,
                "Working hours need a start and an end time."))
        days = hours.get("days")
        if days is not None and (not isinstance(days, list) or not days):
            problems.append(_problem(
                "working_hours", D.R_MISSING_CONFIG,
                "Choose at least one day this employee may work."))

    channels = cleaned.get("channels")
    if isinstance(channels, list) and channels:
        outside = [c for c in channels if c not in reqs["channels"]]
        if outside:
            problems.append(_problem(
                "channels", D.R_FORBIDDEN_CONFIG,
                "This job cannot use %s." % ", ".join(sorted(outside))))

    handoff_user = cleaned.get("handoff_user_id") or cleaned.get("handoff_to")
    if handoff_user:
        if not _user_in_org(db, organization_id, handoff_user):
            problems.append(_problem(
                "handoff_user_id", D.R_TENANT_MISMATCH,
                "That person is not in this workspace."))
    for key in ("booking_owner", "backup_owner"):
        if cleaned.get(key) and not _user_in_org(db, organization_id,
                                                 cleaned[key]):
            problems.append(_problem(key, D.R_TENANT_MISMATCH,
                                     "That person is not in this workspace."))

    peer = cleaned.get("handoff_to_employee_id")
    if peer:
        loop = detect_loop(db, organization_id=organization_id,
                           from_deployment_id=deployment_id,
                           to_deployment_id=peer)
        if loop:
            problems.append(_problem(
                "handoff_to_employee_id", D.R_HANDOFF_LOOP,
                "That would send work in a circle: %s." % " -> ".join(loop)))

    return cleaned, problems


def _user_in_org(db: Session, organization_id: str, user_id: str) -> bool:
    """Tenancy checked in the QUERY, never on a row already loaded."""
    return bool(db.query(User)
                .filter(User.id == user_id,
                        User.organization_id == organization_id)
                .first())


def detect_loop(db: Session, *, organization_id: str,
                from_deployment_id: Optional[str],
                to_deployment_id: str) -> Optional[List[str]]:
    """Would routing work from one employee to another close a circle?

    Walks the existing chain from the target, bounded by the number of
    deployments this customer has, and returns the path when it arrives back
    where it started. A bound rather than a visited-set alone because the data
    is customer-supplied: a walk that trusts the graph to be acyclic is a walk
    that hangs on the first cycle somebody manages to write.
    """
    if not to_deployment_id:
        return None
    if from_deployment_id and to_deployment_id == from_deployment_id:
        return [_name(db, from_deployment_id), _name(db, to_deployment_id)]

    rows = {r.id: r for r in
            db.query(AIEmployeeDeployment)
            .filter(AIEmployeeDeployment.organization_id == organization_id,
                    AIEmployeeDeployment.state != D.RETIRED).all()}
    if to_deployment_id not in rows:
        # Not in this tenant. Not a loop - a refusal the caller makes for a
        # different reason, and reporting it as a loop would be a lie.
        return None

    path = [from_deployment_id] if from_deployment_id else []
    cursor = to_deployment_id
    for _ in range(len(rows) + 1):
        path.append(cursor)
        row = rows.get(cursor)
        if row is None:
            return None
        nxt = (config_of(row) or {}).get("handoff_to_employee_id")
        if not nxt:
            return None
        if nxt in path:
            path.append(nxt)
            return [_name(db, pid) for pid in path]
        cursor = nxt
    return [_name(db, pid) for pid in path]


def _name(db: Session, deployment_id: Optional[str]) -> str:
    if not deployment_id:
        return "this employee"
    row = (db.query(AIEmployeeDeployment)
           .filter(AIEmployeeDeployment.id == deployment_id).first())
    return (getattr(row, "display_name", None) or getattr(row, "template_key",
                                                          None)
            or deployment_id)


def config_of(deployment) -> Dict[str, Any]:
    raw = getattr(deployment, "config", None)
    if not raw:
        return {}
    try:
        val = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return val if isinstance(val, dict) else {}


# ---------------------------------------------------------------------------
# APPLYING
# ---------------------------------------------------------------------------

def apply(db: Session, deployment: AIEmployeeDeployment,
          answers: Dict[str, Any], *, actor: Optional[User] = None,
          merge: bool = True) -> Dict[str, Any]:
    """Store the business answers. Raises only on a forbidden field.

    A configuration with MISSING answers is saved and leaves the deployment in
    a state that says so - that is what CONFIGURING and VALIDATION_REQUIRED are
    for, and refusing a partial save would mean a customer had to finish an
    interview in one sitting. A configuration with a FORBIDDEN field is
    refused outright, because the correct response to "set the model
    temperature" is not to save the rest of it quietly.
    """
    cleaned, problems = validate(
        db, organization_id=deployment.organization_id,
        template_key=deployment.template_key, answers=answers,
        deployment_id=deployment.id)

    fatal = [p for p in problems
             if p["code"] in (D.R_FORBIDDEN_CONFIG, D.R_TENANT_MISMATCH,
                              D.R_HANDOFF_LOOP)]
    if fatal:
        raise ConfigurationRefused(fatal)

    current = config_of(deployment) if merge else {}
    current.update(cleaned)
    deployment.config = json.dumps(current)[:MAX_CONFIG_BYTES]
    deployment.config_version = int(deployment.config_version or 0) + 1
    deployment.configured_at = datetime.utcnow()
    deployment.configured_by = getattr(actor, "id", None)
    db.flush()

    # PUSH IT DOWN TO THE ACTOR, when one exists. The employee row is what the
    # engine reads; leaving the two apart would mean a customer editing their
    # hours changed a screen and nothing else.
    if deployment.employee_id:
        sync_to_employee(db, deployment)

    return {"config": current, "problems": problems,
            "version": deployment.config_version}


def sync_to_employee(db: Session, deployment: AIEmployeeDeployment) -> bool:
    """Write the business answers onto the T6 employee row.

    NARROWED ON THE WAY IN. Channels are intersected against the employee's
    effective policy before they are stored, so the stored value is what is
    actually in force. T6 narrows again on every use regardless - this is about
    the screen telling the truth, not about safety, which is already handled.

    Returns False when there is no actor to write to, which is a normal state
    for a deployment that has not been provisioned yet.
    """
    from app.models.workforce_models import AIEmployee
    from app.services.workforce import policy as wf_policy
    from app.services.workforce import registry as wf_registry

    emp = (db.query(AIEmployee)
           .filter(AIEmployee.id == deployment.employee_id,
                   AIEmployee.organization_id == deployment.organization_id)
           .first())
    if emp is None:
        return False

    cfg = config_of(deployment)
    pol = wf_policy.resolve(db, emp)

    channels = cfg.get("channels")
    if isinstance(channels, list) and channels:
        emp.allowed_channels = json.dumps(
            wf_registry.normalize_channels(channels,
                                           bound=sorted(pol.channels) or None))

    hours = cfg.get("working_hours") or cfg.get("hours")
    if isinstance(hours, dict) and hours:
        emp.operating_hours = json.dumps(hours)[:4000]
    if cfg.get("timezone"):
        emp.timezone = str(cfg["timezone"])[:60]

    handoff = cfg.get("handoff_user_id") or cfg.get("handoff_to")
    if handoff and _user_in_org(db, deployment.organization_id, handoff):
        emp.handoff_user_id = handoff
    if cfg.get("handoff_team"):
        emp.handoff_queue = str(cfg["handoff_team"])[:120]

    audience = cfg.get("audience")
    if isinstance(audience, dict):
        emp.audience_criteria = json.dumps(audience)[:8000]

    kinds = cfg.get("knowledge_kinds") or cfg.get("knowledge")
    if isinstance(kinds, list) and kinds:
        emp.knowledge_binding = json.dumps({"kinds": kinds})[:4000]

    if cfg.get("goal") or cfg.get("goals"):
        goal = cfg.get("goal") or "; ".join(cfg.get("goals") or [])
        if goal:
            emp.objective = ("%s\n\nWhat this business asked for: %s"
                             % (emp.objective or "", goal))[:4000]

    if deployment.display_name:
        emp.name = deployment.display_name[:120]

    # The business answers themselves, so the employee screen and the
    # deployment screen never disagree about what was asked for.
    emp.config = json.dumps(cfg)[:MAX_CONFIG_BYTES]
    emp.updated_at = datetime.utcnow()
    db.flush()
    return True
