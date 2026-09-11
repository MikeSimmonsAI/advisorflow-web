"""YOUR AI TEAM — the customer's own surface.

EVERY ROUTE ANSWERS FOR THE CALLER'S OWN ORGANIZATION AND TAKES NO
ORGANIZATION ID. That is the same property `support_router` relies on: the
tenant boundary is a fact about the signatures rather than a filter each route
has to remember, and a route that cannot name another customer cannot leak one.

The owner reaches a customer's team the way they reach everything else — by
entering the customer, which `get_current_user` resolves through
X-Org-Override. There is no `?organization_id=` on any route here and there
must never be one.

BUSINESS LANGUAGE ONLY (section 51). What comes back is "Working", "Waiting for
Response", "Appointments Booked", "what this employee can do". What does NOT
come back is a system prompt, a model name, a temperature, a tool schema or an
agent graph — `service.describe` renders tool keys as human labels and the raw
keys stay on the God surface.

WRITES ARE ADMIN-ONLY AND REFUSED IN OBSERVATION MODE. Hiring an AI employee,
changing what it may do, and switching it on are administrative acts; reading
its queue is not. `require_not_observation` is on every mutation because a
brand executive observing a customer must never change one.
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import (get_current_user, get_db, require_admin,
                      require_not_observation, require_tenant_user)
from app.models.models import Lead, User
from app.models.workforce_models import (AIEmployee, AIHandoff, AIWorkItem)
from app.services import lead_scope
from app.services.workforce import activation as wf_activation
from app.services.workforce import constants as C
from app.services.workforce import handoff as wf_handoff
from app.services.workforce import performance as wf_performance
from app.services.workforce import queue as wf_queue
from app.services.workforce import registry as wf_registry
from app.services.workforce import service as wf_service

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/workforce", tags=["ai-workforce"])


def _org_id(user: User, db: Session, request: Request) -> str:
    """The workspace this request is standing in. Never an argument.

    `lead_scope.active_workspace_org_id` is the platform's single answer to
    that question — the same one every lead query uses — so an AI employee is
    scoped exactly as a lead list is.
    """
    org_id = lead_scope.active_workspace_org_id(user, db, request)
    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Select a customer workspace to see its AI team.")
    return org_id


def _employee(db: Session, org_id: str, employee_id: str) -> AIEmployee:
    """Load an employee INSIDE this tenant, or 404.

    The tenant filter is in the query rather than in a check afterwards, and
    the answer for another customer's employee is the same 404 as for one that
    does not exist — so this cannot be used to find out which ids are real.
    """
    row = (db.query(AIEmployee)
           .filter(AIEmployee.id == employee_id,
                   AIEmployee.organization_id == org_id).first())
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No such AI employee.")
    return row


# ── READING ─────────────────────────────────────────────────────────────────

@router.get("/team")
def get_team(request: Request, db: Session = Depends(get_db),
             user: User = Depends(require_tenant_user)) -> Dict[str, Any]:
    """The team, its state, and whether anything is actually running."""
    org_id = _org_id(user, db, request)
    resolved = wf_activation.resolve(db, organization_id=org_id)
    employees = wf_service.list_for_org(db, org_id)
    return {
        "organization_id": org_id,
        "activation": resolved.as_dict(),
        # SAID PLAINLY, because "why is nothing happening" is the first
        # question a customer asks and the answer is almost always this.
        "status_line": _status_line(resolved, employees),
        "employees": employees,
        "queue": wf_queue.grouped_counts(db, organization_id=org_id),
        "open_handoffs": (db.query(AIHandoff)
                          .filter(AIHandoff.organization_id == org_id,
                                  AIHandoff.status == "open").count()),
    }


def _status_line(resolved, employees: List[Dict]) -> str:
    if resolved.killed:
        return ("Your AI team is stopped. An operator engaged the kill switch "
                "at the %s level." % (resolved.killed_by_scope or "platform"))
    if resolved.state == C.OFF:
        return ("Your AI team is switched off. Nothing is being contacted and "
                "no work is being done.")
    if resolved.state == C.SIMULATION:
        return ("Your AI team is in simulation. It works through the real "
                "process against test messages; nobody is contacted.")
    if resolved.state == C.SHADOW:
        return ("Your AI team is watching and recording what it would do. "
                "Nobody is contacted.")
    live = sum(1 for e in employees if e["running"])
    return "%d of %d AI employees are working." % (live, len(employees))


@router.get("/catalogue")
def get_catalogue(request: Request, db: Session = Depends(get_db),
                  user: User = Depends(require_tenant_user)) -> Dict[str, Any]:
    """What this business could hire, and honestly whether they can yet."""
    org_id = _org_id(user, db, request)
    return {"organization_id": org_id,
            "available": wf_service.catalogue_for_customer(db, org_id)}


@router.get("/employees/{employee_id}")
def get_employee(employee_id: str, request: Request,
                 db: Session = Depends(get_db),
                 user: User = Depends(require_tenant_user)) -> Dict[str, Any]:
    org_id = _org_id(user, db, request)
    emp = _employee(db, org_id, employee_id)
    detail = wf_service.describe(db, emp)
    detail["performance"] = wf_performance.report(
        db, organization_id=org_id, employee_id=emp.id, days=30)
    detail["recent_activity"] = _recent_activity(db, emp)
    return detail


def _recent_activity(db: Session, emp: AIEmployee, limit: int = 25
                     ) -> List[Dict]:
    """WHAT IS IT DOING? — answered without exposing tool internals.

    Section 25: a customer should be able to answer that without asking
    support. Each row is one action in plain words, with its outcome.
    """
    from app.models.workforce_models import AIToolExecution
    rows = (db.query(AIToolExecution)
            .filter(AIToolExecution.employee_id == emp.id,
                    AIToolExecution.organization_id == emp.organization_id)
            .order_by(AIToolExecution.created_at.desc()).limit(limit).all())
    out = []
    for r in rows:
        spec = wf_registry.tool(r.tool_key)
        out.append({
            "at": r.created_at.isoformat() if r.created_at else None,
            "what": spec.label if spec else r.tool_key,
            "outcome": ("Done" if r.decision == "allowed" and r.status != "error"
                        else ("Not allowed" if r.decision == "denied"
                              else "Failed")),
            "why_not": r.denial_reason,
            "simulated": bool(r.simulated),
        })
    return out


@router.get("/queue")
def get_queue(request: Request, db: Session = Depends(get_db),
              user: User = Depends(require_tenant_user),
              group: Optional[str] = Query(None),
              employee_id: Optional[str] = Query(None),
              limit: int = Query(50, ge=1, le=200)) -> Dict[str, Any]:
    """The work queue, grouped the way a person thinks about it."""
    org_id = _org_id(user, db, request)
    if employee_id:
        _employee(db, org_id, employee_id)

    groups = wf_queue.grouped_counts(db, organization_id=org_id,
                                     employee_id=employee_id)
    states = None
    for key, _label, group_states in C.QUEUE_GROUPS:
        if key == group:
            states = list(group_states)
    q = (db.query(AIWorkItem)
         .filter(AIWorkItem.organization_id == org_id))
    if employee_id:
        q = q.filter(AIWorkItem.employee_id == employee_id)
    if states:
        q = q.filter(AIWorkItem.state.in_(states))
    items = q.order_by(AIWorkItem.updated_at.desc()).limit(limit).all()

    lead_ids = [i.subject_id for i in items if i.subject_type == "lead"]
    names = {}
    if lead_ids:
        for lead in (db.query(Lead)
                     .filter(Lead.id.in_(lead_ids),
                             Lead.organization_id == org_id).all()):
            names[lead.id] = ("%s %s" % (lead.first_name or "",
                                         lead.last_name or "")).strip() \
                or "Unnamed contact"
    return {
        "organization_id": org_id,
        "groups": groups,
        "group": group,
        "items": [_item_out(i, names) for i in items],
    }


def _item_out(item: AIWorkItem, names: Dict[str, str]) -> Dict[str, Any]:
    return {
        "id": item.id,
        "contact": names.get(item.subject_id, "Contact"),
        "lead_id": item.subject_id,
        "employee_id": item.employee_id,
        "state": item.state,
        "state_label": _state_label(item.state),
        "why": item.state_reason,
        "touches": item.touches,
        "outcome": item.outcome,
        "next_action_at": (item.next_action_at.isoformat()
                           if item.next_action_at else None),
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


_STATE_LABELS = {
    C.ASSIGNED: "Assigned", C.ELIGIBILITY_PENDING: "Checking",
    C.ELIGIBLE: "Ready", C.WORKING: "Working",
    C.WAITING_FOR_RESPONSE: "Waiting for response", C.QUALIFIED: "Qualified",
    C.APPOINTMENT_BOOKED: "Appointment booked",
    C.HUMAN_HANDOFF: "With a person", C.NOT_INTERESTED: "Not interested",
    C.DO_NOT_CONTACT: "Opted out", C.BAD_CONTACT: "Cannot be reached",
    C.EXHAUSTED: "No response", C.NEEDS_REVIEW: "Needs review",
    C.PAUSED: "Paused", C.FAILED: "Failed",
}


def _state_label(state: str) -> str:
    return _STATE_LABELS.get(state, state.replace("_", " ").title())


@router.get("/queue/{item_id}")
def get_work_item(item_id: str, request: Request,
                  db: Session = Depends(get_db),
                  user: User = Depends(require_tenant_user)) -> Dict[str, Any]:
    org_id = _org_id(user, db, request)
    item = (db.query(AIWorkItem)
            .filter(AIWorkItem.id == item_id,
                    AIWorkItem.organization_id == org_id).first())
    if item is None:
        raise HTTPException(status_code=404, detail="No such queue item.")
    lead = (db.query(Lead).filter(Lead.id == item.subject_id,
                                  Lead.organization_id == org_id).first())
    events = wf_queue.timeline(db, item.id)
    import json as _json
    reasons = []
    try:
        reasons = _json.loads(item.eligibility_reasons or "{}")
    except (ValueError, TypeError):
        reasons = {}
    return {
        "item": _item_out(item, {item.subject_id:
                                 ("%s %s" % (getattr(lead, "first_name", "") or "",
                                             getattr(lead, "last_name", "") or "")
                                  ).strip() or "Contact"}),
        "eligibility": {"result": item.eligibility_state, "reasons": reasons},
        "timeline": [
            {"at": e.created_at.isoformat() if e.created_at else None,
             "from": _state_label(e.from_state) if e.from_state else None,
             "to": _state_label(e.to_state), "why": e.reason,
             "by": e.actor_kind}
            for e in events
        ],
    }


@router.get("/handoffs")
def get_handoffs(request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_tenant_user),
                 employee_id: Optional[str] = Query(None)
                 ) -> Dict[str, Any]:
    org_id = _org_id(user, db, request)
    rows = wf_handoff.open_for_org(db, org_id, employee_id=employee_id)
    return {"organization_id": org_id,
            "handoffs": [wf_handoff.as_dict(r) for r in rows]}


@router.get("/performance")
def get_performance(request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_user),
                    employee_id: Optional[str] = Query(None),
                    days: int = Query(30, ge=1, le=365)) -> Dict[str, Any]:
    org_id = _org_id(user, db, request)
    if employee_id:
        _employee(db, org_id, employee_id)
    return {
        "organization_id": org_id,
        "report": wf_performance.report(db, organization_id=org_id,
                                        employee_id=employee_id, days=days),
        "team": wf_performance.leaderboard(db, org_id, days=days),
    }


# ═══════════════════════════════════════════════════════════════════════════
# WRITING — admin only, refused in observation mode
# ═══════════════════════════════════════════════════════════════════════════

class HireRequest(BaseModel):
    template_key: str
    name: Optional[str] = None
    channels: Optional[List[str]] = None
    # THE BUSINESS ANSWERS. Not a prompt, not a model, not a temperature —
    # `service.hire` reads only the keys the template declared as questions,
    # so an unexpected key here configures nothing.
    config: Optional[Dict[str, Any]] = None
    operating_hours: Optional[Dict[str, Any]] = None
    timezone: Optional[str] = None
    handoff_user_id: Optional[str] = None
    handoff_queue: Optional[str] = None
    daily_work_cap: Optional[int] = None
    audience_criteria: Optional[Dict[str, Any]] = None
    knowledge_binding: Optional[Dict[str, Any]] = None
    tool_keys: Optional[List[str]] = None


@router.post("/team", status_code=status.HTTP_201_CREATED)
def hire_employee(payload: HireRequest, request: Request,
                  db: Session = Depends(get_db),
                  user: User = Depends(require_admin),
                  _guard: User = Depends(require_not_observation)
                  ) -> Dict[str, Any]:
    """Hire an AI employee. IT STARTS SWITCHED OFF AND DOES NOTHING.

    Section 33. A new employee is `draft` / `off`, and the enclosing scopes are
    off as well, so the first thing it does is nothing at all. Switching it on
    is a separate, recorded decision.
    """
    org_id = _org_id(user, db, request)

    entitled = {e["template_key"]: e
                for e in wf_service.catalogue_for_customer(db, org_id)}
    offer = entitled.get(payload.template_key)
    if offer is None:
        raise HTTPException(status_code=404,
                            detail="That AI employee is not offered here.")
    if not offer["available"] and getattr(user, "role", None) != "god_admin":
        # 402, not 403: this is a commercial answer, and it is the same code
        # `require_feature` uses for the same kind of refusal.
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(offer["entitlement"].get("detail")
                    or offer["feature"].get("detail")
                    or "This AI employee is not available on your plan."))
    try:
        emp = wf_service.hire(
            db, organization_id=org_id, template_key=payload.template_key,
            name=payload.name, actor=user, config=payload.config,
            channels=payload.channels,
            operating_hours=payload.operating_hours,
            timezone=payload.timezone,
            handoff_user_id=payload.handoff_user_id,
            handoff_queue=payload.handoff_queue,
            daily_work_cap=payload.daily_work_cap,
            audience_criteria=payload.audience_criteria,
            knowledge_binding=payload.knowledge_binding,
            tool_keys=payload.tool_keys)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.commit()
    return wf_service.describe(db, emp)


class ConfigureRequest(BaseModel):
    name: Optional[str] = None
    objective: Optional[str] = None
    channels: Optional[List[str]] = None
    config: Optional[Dict[str, Any]] = None
    operating_hours: Optional[Dict[str, Any]] = None
    timezone: Optional[str] = None
    handoff_user_id: Optional[str] = None
    handoff_queue: Optional[str] = None
    daily_work_cap: Optional[int] = None
    max_touches: Optional[int] = None
    audience_criteria: Optional[Dict[str, Any]] = None
    knowledge_binding: Optional[Dict[str, Any]] = None
    tool_keys: Optional[List[str]] = None


@router.patch("/employees/{employee_id}")
def configure_employee(employee_id: str, payload: ConfigureRequest,
                       request: Request, db: Session = Depends(get_db),
                       user: User = Depends(require_admin),
                       _guard: User = Depends(require_not_observation)
                       ) -> Dict[str, Any]:
    import json
    org_id = _org_id(user, db, request)
    emp = _employee(db, org_id, employee_id)

    if payload.name is not None:
        emp.name = payload.name[:120]
    if payload.objective is not None:
        emp.objective = payload.objective[:4000]
    if payload.channels is not None:
        # Narrowed against the template and the brand offering. `policy.resolve`
        # would narrow it again on every use regardless — this just stops a
        # wider value being STORED, so the configuration screen shows what is
        # actually in force rather than what somebody asked for.
        from app.services.workforce import policy as wf_policy
        pol = wf_policy.resolve(db, emp)
        emp.allowed_channels = json.dumps(
            wf_registry.normalize_channels(payload.channels,
                                           bound=sorted(pol.channels) or None))
    for field, value in (("config", payload.config),
                         ("operating_hours", payload.operating_hours),
                         ("audience_criteria", payload.audience_criteria),
                         ("knowledge_binding", payload.knowledge_binding)):
        if value is not None:
            setattr(emp, field, json.dumps(value)[:8000])
    if payload.timezone is not None:
        emp.timezone = payload.timezone[:60]
    if payload.handoff_user_id is not None:
        emp.handoff_user_id = payload.handoff_user_id or None
    if payload.handoff_queue is not None:
        emp.handoff_queue = payload.handoff_queue or None
    if payload.daily_work_cap is not None:
        emp.daily_work_cap = max(0, int(payload.daily_work_cap)) or None
    if payload.max_touches is not None:
        emp.max_touches = max(1, int(payload.max_touches))
    if payload.tool_keys is not None:
        wf_service.set_authority(db, emp, payload.tool_keys, actor=user)
    db.commit()
    return wf_service.describe(db, emp)


class ActivationRequest(BaseModel):
    state: str
    reason: Optional[str] = None


# WHAT A CUSTOMER MAY SWITCH ON FOR THEMSELVES.
#
# `off`, `simulation` and `shadow` reach nobody. `controlled` and `active` do,
# and a customer promoting their own employee into them would be a customer
# deciding when real outreach starts — which is an operator decision with a
# cohort, a cap and somebody watching (section 49). God can set any stage.
CUSTOMER_SETTABLE_STAGES = (C.OFF, C.SIMULATION, C.SHADOW)


@router.post("/employees/{employee_id}/activation")
def set_employee_activation(employee_id: str, payload: ActivationRequest,
                            request: Request, db: Session = Depends(get_db),
                            user: User = Depends(require_admin),
                            _guard: User = Depends(require_not_observation)
                            ) -> Dict[str, Any]:
    org_id = _org_id(user, db, request)
    emp = _employee(db, org_id, employee_id)
    stage = (payload.state or "").strip().lower()
    if stage not in C.ACTIVATION_RANK:
        raise HTTPException(status_code=400,
                            detail="Unknown activation stage.")
    if stage not in CUSTOMER_SETTABLE_STAGES \
            and getattr(user, "role", None) != "god_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Live operation is switched on by AdvisorFlow, with you, "
                   "on an agreed group of records. You can switch this "
                   "employee off, or run it in simulation or shadow.")
    wf_service.activate(db, emp, stage, actor=user, reason=payload.reason)
    db.commit()
    return {"employee_id": emp.id,
            "activation": wf_activation.resolve(db, employee=emp).as_dict()}


class PauseRequest(BaseModel):
    reason: Optional[str] = None


@router.post("/employees/{employee_id}/pause")
def pause_employee(employee_id: str, payload: PauseRequest, request: Request,
                   db: Session = Depends(get_db),
                   user: User = Depends(require_admin),
                   _guard: User = Depends(require_not_observation)
                   ) -> Dict[str, Any]:
    """Stop this employee now. Its queued work stops with it."""
    org_id = _org_id(user, db, request)
    emp = _employee(db, org_id, employee_id)
    wf_service.pause(db, emp, reason=payload.reason or "paused by an admin",
                     actor=user)
    db.commit()
    return wf_service.describe(db, emp)


@router.post("/employees/{employee_id}/resume")
def resume_employee(employee_id: str, request: Request,
                    db: Session = Depends(get_db),
                    user: User = Depends(require_admin),
                    _guard: User = Depends(require_not_observation)
                    ) -> Dict[str, Any]:
    org_id = _org_id(user, db, request)
    emp = _employee(db, org_id, employee_id)
    wf_service.resume(db, emp, actor=user)
    db.commit()
    return wf_service.describe(db, emp)


class AssignRequest(BaseModel):
    lead_ids: Optional[List[str]] = None
    criteria: Optional[Dict[str, Any]] = None
    limit: int = 500


@router.post("/employees/{employee_id}/assign")
def assign_records(employee_id: str, payload: AssignRequest, request: Request,
                   db: Session = Depends(get_db),
                   user: User = Depends(require_admin),
                   _guard: User = Depends(require_not_observation)
                   ) -> Dict[str, Any]:
    """Give an employee records to work.

    THE POPULATION COMES FROM `authorized_lead_query`, not from the request.
    That is the same function the leads list and the qualification preview use,
    so an admin can only assign records they can already see — and a lead id
    from another tenant simply is not in the set rather than being refused
    after the fact.
    """
    from app.services import qualification
    org_id = _org_id(user, db, request)
    emp = _employee(db, org_id, employee_id)

    query = lead_scope.authorized_lead_query(db, user, request=request)
    if payload.lead_ids:
        lead_scope.assert_leads_in_scope(db, user, list(payload.lead_ids),
                                         request=request)
        query = query.filter(Lead.id.in_(list(payload.lead_ids)))
    criteria = payload.criteria
    if criteria is None:
        import json
        try:
            criteria = json.loads(emp.audience_criteria or "{}")
        except (ValueError, TypeError):
            criteria = {}
    query = qualification.apply_selection_filters(query, criteria)
    limit = max(1, min(int(payload.limit or 500), 5000))
    leads = query.limit(limit).all()

    result = wf_queue.enqueue(db, emp, [l.id for l in leads],
                              job_key=emp.job_role,
                              actor_kind=C.ACTOR_HUMAN, actor_id=user.id)
    db.commit()
    result["considered"] = len(leads)
    return result


class ReviewRequest(BaseModel):
    decision: str            # return_to_queue | close_not_interested |
    #                          close_do_not_contact | close_bad_contact
    note: Optional[str] = None


_REVIEW_DECISIONS = {
    "return_to_queue": (C.ELIGIBILITY_PENDING, "reviewed and returned"),
    "close_not_interested": (C.NOT_INTERESTED, "closed by a person"),
    "close_do_not_contact": (C.DO_NOT_CONTACT, "opted out, recorded by a person"),
    "close_bad_contact": (C.BAD_CONTACT, "unreachable, recorded by a person"),
}


@router.post("/queue/{item_id}/review")
def review_work_item(item_id: str, payload: ReviewRequest, request: Request,
                     db: Session = Depends(get_db),
                     user: User = Depends(require_admin),
                     _guard: User = Depends(require_not_observation)
                     ) -> Dict[str, Any]:
    """A person clears a review.

    `return_to_queue` re-enters at ELIGIBILITY_PENDING, never at WORKING — so
    clearing a review cannot skip a suppression that arrived while the item was
    sitting there. That is enforced by the transition table, not by this route.
    """
    org_id = _org_id(user, db, request)
    item = (db.query(AIWorkItem)
            .filter(AIWorkItem.id == item_id,
                    AIWorkItem.organization_id == org_id).first())
    if item is None:
        raise HTTPException(status_code=404, detail="No such queue item.")
    target = _REVIEW_DECISIONS.get((payload.decision or "").strip())
    if target is None:
        raise HTTPException(status_code=400,
                            detail="Unknown review decision.")
    state, reason = target
    try:
        wf_queue.advance_to(db, item, state,
                            reason=(payload.note or reason)[:255],
                            actor_kind=C.ACTOR_HUMAN, actor_id=user.id)
    except wf_queue.IllegalTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if state == C.ELIGIBILITY_PENDING:
        from datetime import datetime as _dt
        item.next_action_at = _dt.utcnow()
    db.commit()
    return {"item_id": item.id, "state": item.state,
            "state_label": _state_label(item.state)}


class HandoffActionRequest(BaseModel):
    note: Optional[str] = None


@router.post("/handoffs/{handoff_id}/accept")
def accept_handoff(handoff_id: str, request: Request,
                   db: Session = Depends(get_db),
                   user: User = Depends(get_current_user),
                   _guard: User = Depends(require_not_observation)
                   ) -> Dict[str, Any]:
    """Anyone in the workspace may take a handoff. Deliberately not admin-only:
    the person who should pick up a conversation is whoever is free."""
    org_id = _org_id(user, db, request)
    row = (db.query(AIHandoff)
           .filter(AIHandoff.id == handoff_id,
                   AIHandoff.organization_id == org_id).first())
    if row is None:
        raise HTTPException(status_code=404, detail="No such handoff.")
    wf_handoff.accept(db, row, user)
    db.commit()
    return wf_handoff.as_dict(row)


@router.post("/handoffs/{handoff_id}/resolve")
def resolve_handoff(handoff_id: str, payload: HandoffActionRequest,
                    request: Request, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user),
                    _guard: User = Depends(require_not_observation)
                    ) -> Dict[str, Any]:
    org_id = _org_id(user, db, request)
    row = (db.query(AIHandoff)
           .filter(AIHandoff.id == handoff_id,
                   AIHandoff.organization_id == org_id).first())
    if row is None:
        raise HTTPException(status_code=404, detail="No such handoff.")
    wf_handoff.resolve(db, row, user, note=payload.note)
    db.commit()
    return wf_handoff.as_dict(row)
