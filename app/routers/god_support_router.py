"""GOD MODE → SUPPORT. The command centre, inside the shell that already exists.

NOT A SECOND ROOT
-----------------
This router extends God Mode. There is no Support Superadmin, no separate
support root and no second permission architecture. Two guards, both built
from what was already here:

    require_support_operator      god_admin, or a brand operator holding the
                                  brand-scoped `support_console` capability.
                                  Sees only their own brands.
    require_god_for_configuration god_admin only. Entitlements, support hours,
                                  fix policy, the service catalogue, and
                                  APPROVING a remediation.

The split is the point. Running a queue and changing what the platform is
allowed to do to a customer by itself are different powers, and a brand
operator gets the first without the second.

WHY APPROVAL IS GOD-ONLY EVEN THOUGH OPERATORS RUN THE QUEUE
--------------------------------------------------------------
`RiskClass.GOD_APPROVAL` exists for changes that touch money, permissions,
credentials or customer data. If a brand support operator could approve one,
the class would be decorative and its name would be a lie. So approval sits
behind `require_god_for_configuration`, and `support_remediation` refuses
anyway — two independent gates, because this is the one place where being
wrong is expensive.

EVERY LIST GOES THROUGH `scope_query`
--------------------------------------
Brand isolation is applied by one function, in `support_authority`. A new
list endpoint that forgets it has to deliberately not call it.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.deps import get_db, require_god
from app.models.models import Organization, Platform, User
from app.models.support_models import (
    Cause, FixStatus, IncidentStatus, Queue, RiskClass, Severity, SlaState,
    SupportFixPolicy, SupportFixRun, SupportIncident, SupportIssueSignature,
    SupportServiceOffering, SupportTicket, TicketCategory, TicketStatus,
)
from app.services import (
    support_authority, support_branding, support_brief, support_diagnostics,
    support_entitlements, support_incidents, support_knowledge,
    support_remediation, support_sla, support_tickets,
)
from app.services.support_authority import (
    require_god_for_configuration, require_support_operator,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/god/support", tags=["god-support"])


def _ticket_in_scope(db: Session, user: User, ticket_id: str) -> SupportTicket:
    ticket = db.query(SupportTicket).filter(SupportTicket.id == ticket_id).first()
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found.")
    support_authority.assert_visible(db, user, platform_id=ticket.platform_id)
    return ticket


# ══════════════════════════════════════════════════════════════════════════
# THE BOARD
# ══════════════════════════════════════════════════════════════════════════

@router.get("/overview")
def overview(user: User = Depends(require_support_operator),
             db: Session = Depends(get_db)) -> Dict[str, Any]:
    """The numbers a support lead needs before they decide what to do first.

    EVERY COUNTER IS A LINK TO A FILTERED LIST, which is why each one is a
    field name the list endpoint also accepts. A dashboard whose numbers are
    not clickable is a dashboard people screenshot and then ignore.
    """
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    open_q = support_authority.scope_query(
        db.query(SupportTicket).filter(
            SupportTicket.status.in_(list(TicketStatus.OPEN))),
        SupportTicket, db, user)
    open_tickets = open_q.all()

    waiting = support_authority.scope_query(
        db.query(SupportTicket).filter(
            SupportTicket.status == TicketStatus.WAITING_ON_CUSTOMER),
        SupportTicket, db, user).count()

    resolved_today = support_authority.scope_query(
        db.query(SupportTicket).filter(SupportTicket.resolved_at >= today),
        SupportTicket, db, user).count()

    fixes_today = support_authority.scope_query(
        db.query(SupportFixRun).filter(SupportFixRun.created_at >= today),
        SupportFixRun, db, user).all()

    incidents = (db.query(SupportIncident)
                 .filter(SupportIncident.status.in_(list(IncidentStatus.OPEN)))
                 .order_by(SupportIncident.last_observed_at.desc()).all())
    # Cross-brand aggregates are god-only. A brand operator sees the count of
    # incidents touching THEIR brand and nothing about the others.
    if not support_authority.is_god(user):
        allowed = set(support_authority.visible_platform_ids(db, user) or [])
        incidents = [i for i in incidents
                     if _incident_touches(db, i, allowed)]

    first_response_times = [
        t for t in open_tickets if t.first_response_at and t.created_at]

    return {
        "open_tickets": len(open_tickets),
        "critical": sum(1 for t in open_tickets if t.severity == Severity.P1),
        "high": sum(1 for t in open_tickets if t.severity == Severity.P2),
        "sla_at_risk": sum(1 for t in open_tickets
                           if t.sla_state == SlaState.AT_RISK),
        "sla_breached": sum(1 for t in open_tickets
                            if t.sla_state == SlaState.BREACHED),
        "waiting_on_customer": waiting,
        "unassigned": sum(1 for t in open_tickets if not t.assigned_to),
        "resolved_today": resolved_today,
        "auto_fixed_today": sum(1 for f in fixes_today if f.verified),
        "auto_fix_failed_today": sum(1 for f in fixes_today
                                     if f.status == FixStatus.FAILED),
        "awaiting_approval": sum(1 for f in fixes_today
                                 if f.status == FixStatus.APPROVAL_REQUIRED),
        "platform_incidents": len(incidents),
        "recurring_issues": _recurring_count(db),
        # NULL, not zero, when nothing has been answered — an average of
        # nothing is not "instant".
        "average_first_response_minutes": (
            support_brief.average_first_response_minutes(db, first_response_times)
            if first_response_times else None),
        "queues": [{"key": q, "label": Queue.LABELS[q],
                    "count": sum(1 for t in open_tickets if t.queue == q)}
                   for q in Queue.ALL],
        "scope": _scope_description(db, user),
    }


def _incident_touches(db: Session, incident: SupportIncident,
                      platform_ids: set) -> bool:
    from app.models.support_models import SupportIncidentLink
    if not platform_ids:
        return False
    links = (db.query(SupportIncidentLink)
             .filter(SupportIncidentLink.incident_id == incident.id,
                     SupportIncidentLink.link_type == "ticket").all())
    ticket_ids = [l.link_id for l in links]
    if not ticket_ids:
        return False
    return (db.query(SupportTicket)
            .filter(SupportTicket.id.in_(ticket_ids),
                    SupportTicket.platform_id.in_(list(platform_ids)))
            .count() > 0)


def _recurring_count(db: Session) -> int:
    since = datetime.utcnow() - timedelta(days=7)
    return (db.query(SupportIssueSignature)
            .filter(SupportIssueSignature.last_seen_at >= since,
                    SupportIssueSignature.occurrence_count >= 3).count())


def _scope_description(db: Session, user: User) -> Dict[str, Any]:
    platform_ids = support_authority.visible_platform_ids(db, user)
    if platform_ids is None:
        return {"level": "platform", "label": "Every brand",
                "platform_ids": None}
    names = [p.name for p in db.query(Platform)
             .filter(Platform.id.in_(platform_ids)).all()] if platform_ids else []
    return {"level": "brand", "label": ", ".join(names) or "No brands",
            "platform_ids": platform_ids}


@router.get("/tickets")
def list_tickets(status: Optional[str] = Query(None),
                 severity: Optional[str] = Query(None),
                 queue: Optional[str] = Query(None),
                 category: Optional[str] = Query(None),
                 platform_id: Optional[str] = Query(None),
                 organization_id: Optional[str] = Query(None),
                 assigned_to: Optional[str] = Query(None),
                 sla_state: Optional[str] = Query(None),
                 incident_id: Optional[str] = Query(None),
                 unassigned: bool = Query(False),
                 days: Optional[int] = Query(None, ge=1, le=365),
                 limit: int = Query(100, ge=1, le=500),
                 user: User = Depends(require_support_operator),
                 db: Session = Depends(get_db)) -> Dict[str, Any]:
    """The queue, filtered. Every filter on the God screen is one of these.

    `platform_id` here NARROWS an operator's scope; it can never widen it —
    `scope_query` is applied regardless, so passing another brand's id
    returns nothing rather than that brand's tickets.
    """
    q = support_authority.scope_query(db.query(SupportTicket), SupportTicket,
                                      db, user)
    if status == "open":
        q = q.filter(SupportTicket.status.in_(list(TicketStatus.OPEN)))
    elif status == "closed":
        q = q.filter(SupportTicket.status.in_(list(TicketStatus.TERMINAL)))
    elif status in TicketStatus.ALL:
        q = q.filter(SupportTicket.status == status)
    if severity in Severity.ALL:
        q = q.filter(SupportTicket.severity == severity)
    if queue in Queue.ALL:
        q = q.filter(SupportTicket.queue == queue)
    if category in TicketCategory.ALL:
        q = q.filter(SupportTicket.category == category)
    if platform_id:
        q = q.filter(SupportTicket.platform_id == platform_id)
    if organization_id:
        q = q.filter(SupportTicket.organization_id == organization_id)
    if assigned_to:
        q = q.filter(SupportTicket.assigned_to == assigned_to)
    if unassigned:
        q = q.filter(SupportTicket.assigned_to.is_(None))
    if sla_state in SlaState.ALL:
        q = q.filter(SupportTicket.sla_state == sla_state)
    if incident_id:
        q = q.filter(SupportTicket.incident_id == incident_id)
    if days:
        q = q.filter(SupportTicket.created_at >=
                     datetime.utcnow() - timedelta(days=days))

    rows = q.order_by(SupportTicket.created_at.desc()).limit(limit).all()
    org_names = _org_names(db, [r.organization_id for r in rows])
    out = []
    for ticket in rows:
        summary = support_tickets.list_summary(db, ticket)
        summary["organization_name"] = org_names.get(ticket.organization_id)
        out.append(summary)
    return {"tickets": out, "count": len(out)}


def _org_names(db: Session, org_ids: List[str]) -> Dict[str, str]:
    ids = [i for i in set(org_ids) if i]
    if not ids:
        return {}
    return {o.id: o.name for o in db.query(Organization)
            .filter(Organization.id.in_(ids)).all()}


@router.get("/tickets/{ticket_id}")
def read_ticket(ticket_id: str, user: User = Depends(require_support_operator),
                db: Session = Depends(get_db)) -> Dict[str, Any]:
    """The operator view: evidence, AI reasoning, remediation history, incident.

    `agent_view` is a SUPERSET of `customer_view`, so the two can never
    disagree about the facts they share, and the extra material has no path
    back into the customer's own screen.
    """
    ticket = _ticket_in_scope(db, user, ticket_id)
    view = support_tickets.agent_view(db, ticket)
    view["available_repairs"] = support_remediation.list_registry()
    return view


class ReplyRequest(BaseModel):
    body: str = Field(..., min_length=1, max_length=20000)
    internal: bool = False


@router.post("/tickets/{ticket_id}/reply")
def reply(ticket_id: str, req: ReplyRequest,
          user: User = Depends(require_support_operator),
          db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Reply to the customer, or leave an internal note.

    `internal` is an explicit field with a default of False. The default
    matters: a note somebody meant to keep internal that goes out is worse
    than a reply somebody meant to send that sits in the notes, and defaults
    should fail in the recoverable direction.
    """
    ticket = _ticket_in_scope(db, user, ticket_id)
    support_tickets.add_message(db, ticket, author_kind="agent", user=user,
                                body=req.body, is_internal=req.internal)
    if not req.internal:
        support_tickets.notify_ticket_update(
            db, ticket, headline="There's an update on your request",
            body=req.body)
    db.commit()
    return support_tickets.agent_view(db, ticket)


class AssignRequest(BaseModel):
    user_id: Optional[str] = None
    team: Optional[str] = None


@router.post("/tickets/{ticket_id}/assign")
def assign(ticket_id: str, req: AssignRequest,
           user: User = Depends(require_support_operator),
           db: Session = Depends(get_db)) -> Dict[str, Any]:
    ticket = _ticket_in_scope(db, user, ticket_id)
    agent = None
    if req.user_id:
        agent = db.query(User).filter(User.id == req.user_id).first()
        if agent is None:
            raise HTTPException(status_code=404, detail="That user was not found.")
    support_tickets.assign(db, ticket, agent=agent, actor=user, team=req.team)
    db.commit()
    return support_tickets.agent_view(db, ticket)


class SeverityRequest(BaseModel):
    severity: str
    reason: Optional[str] = Field(None, max_length=1000)


@router.post("/tickets/{ticket_id}/severity")
def change_severity(ticket_id: str, req: SeverityRequest,
                    user: User = Depends(require_support_operator),
                    db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Reclassify, with history. The clock is recomputed, not left behind."""
    if req.severity not in Severity.ALL:
        raise HTTPException(status_code=400, detail="Unknown severity.")
    ticket = _ticket_in_scope(db, user, ticket_id)
    support_tickets.reclassify(db, ticket, severity=req.severity, actor=user,
                               reason=req.reason)
    db.commit()
    return support_tickets.agent_view(db, ticket)


class StatusRequest(BaseModel):
    status: str
    detail: Optional[str] = Field(None, max_length=2000)


@router.post("/tickets/{ticket_id}/status")
def change_status(ticket_id: str, req: StatusRequest,
                  user: User = Depends(require_support_operator),
                  db: Session = Depends(get_db)) -> Dict[str, Any]:
    if req.status not in TicketStatus.ALL:
        raise HTTPException(status_code=400, detail="Unknown status.")
    ticket = _ticket_in_scope(db, user, ticket_id)
    support_tickets.set_status(db, ticket, req.status, actor=user,
                               detail=req.detail)
    db.commit()
    return support_tickets.agent_view(db, ticket)


class ResolveRequest(BaseModel):
    resolution: str = Field(..., min_length=1, max_length=5000)
    resolution_code: Optional[str] = None


@router.post("/tickets/{ticket_id}/resolve")
def resolve(ticket_id: str, req: ResolveRequest,
            user: User = Depends(require_support_operator),
            db: Session = Depends(get_db)) -> Dict[str, Any]:
    ticket = _ticket_in_scope(db, user, ticket_id)
    support_tickets.resolve_ticket(db, ticket, resolution=req.resolution,
                                   resolution_code=req.resolution_code,
                                   actor=user)
    db.commit()
    return support_tickets.agent_view(db, ticket)


class AssistanceLogRequest(BaseModel):
    minutes: int = Field(..., ge=1, le=600)
    category: str
    note: Optional[str] = Field(None, max_length=2000)


@router.post("/tickets/{ticket_id}/assistance")
def log_assistance(ticket_id: str, req: AssistanceLogRequest,
                   user: User = Depends(require_support_operator),
                   db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Record live time spent. THE CATEGORY DECIDES WHO PAYS, NOT THE OPERATOR.

    `support_entitlements.consumption_for` is the only thing that decides
    whether these minutes are included, billable, or neither — so an operator
    cannot accidentally bill a customer's allowance for time spent on our own
    defect, which is the mistake this whole distinction exists to prevent.
    """
    if req.category not in TicketCategory.ALL:
        raise HTTPException(status_code=400, detail="Unknown category.")
    ticket = _ticket_in_scope(db, user, ticket_id)
    org = db.query(Organization).filter(
        Organization.id == ticket.organization_id).first()
    if org is None:
        raise HTTPException(status_code=404, detail="Customer not found.")

    entry = support_entitlements.record_assistance(
        db, org=org, category=req.category, minutes=req.minutes,
        ticket_id=ticket.id, status="delivered", requested_by=user.id,
        note=req.note)
    if entry.counts_against_allowance:
        ticket.assistance_minutes_included = (
            int(ticket.assistance_minutes_included or 0) + req.minutes)
    elif entry.billable:
        ticket.assistance_minutes_billable = (
            int(ticket.assistance_minutes_billable or 0) + req.minutes)
    db.commit()

    return {
        "minutes": req.minutes,
        "counts_against_allowance": bool(entry.counts_against_allowance),
        "billable": bool(entry.billable),
        "allowance": support_entitlements.assistance_summary(db, org),
    }


@router.post("/tickets/{ticket_id}/diagnose")
def rerun_diagnostics(ticket_id: str,
                      user: User = Depends(require_support_operator),
                      db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Run the checks again, now, with God's deeper view.

    `is_god=True` here unlocks the PLATFORM-scope checks (background jobs) as
    well as the technical view of every organization-scope one. That is the
    whole difference between what a customer is shown and what an operator
    needs to see.
    """
    ticket = _ticket_in_scope(db, user, ticket_id)
    org = db.query(Organization).filter(
        Organization.id == ticket.organization_id).first()
    submitter = None
    if ticket.submitted_by:
        submitter = db.query(User).filter(User.id == ticket.submitted_by).first()

    summary = support_diagnostics.run_checks(
        db, org=org, user=submitter,
        is_god=support_authority.is_god(user))
    run = support_diagnostics.persist_run(
        db, summary, org=org, requested_by=user.id, requested_by_kind="god",
        ticket_id=ticket.id)
    ticket.diagnostic_run_id = run.id
    db.commit()
    return {
        "diagnostic_run_id": run.id,
        "overall_severity": summary["overall_severity"],
        "suspected_cause": summary["suspected_cause"],
        "technical": summary["technical_view"],
        "signals": summary["signals"],
        "available_repairs": support_remediation.available_for_signals(
            summary["signals"], summary["remediation_hints"]),
    }


# ══════════════════════════════════════════════════════════════════════════
# THE FIXER
# ══════════════════════════════════════════════════════════════════════════

@router.get("/repairs")
def repair_registry(user: User = Depends(require_support_operator)
                    ) -> Dict[str, Any]:
    """Every registered remediation, with its risk class and its explanations.

    THIS IS WHAT MAKES "NO DEAD BUTTONS" CHECKABLE. Every repair the God UI
    can offer is one of these rows; a button with no registry entry has
    nothing to call, and `tests/test_support_fixer.py` asserts the screen's
    action list against this endpoint.
    """
    return {
        "repairs": support_remediation.list_registry(),
        "risk_classes": [{"key": k, "label": RiskClass.LABELS[k]}
                         for k in RiskClass.ALL],
    }


@router.get("/fix-runs")
def list_fix_runs(status: Optional[str] = Query(None),
                  action_key: Optional[str] = Query(None),
                  organization_id: Optional[str] = Query(None),
                  days: int = Query(7, ge=1, le=90),
                  limit: int = Query(100, ge=1, le=500),
                  user: User = Depends(require_support_operator),
                  db: Session = Depends(get_db)) -> Dict[str, Any]:
    q = support_authority.scope_query(
        db.query(SupportFixRun).filter(
            SupportFixRun.created_at >= datetime.utcnow() - timedelta(days=days)),
        SupportFixRun, db, user)
    if status in FixStatus.ALL:
        q = q.filter(SupportFixRun.status == status)
    if action_key:
        q = q.filter(SupportFixRun.action_key == action_key)
    if organization_id:
        q = q.filter(SupportFixRun.organization_id == organization_id)
    rows = q.order_by(SupportFixRun.created_at.desc()).limit(limit).all()
    return {"runs": [support_remediation.run_summary(r, technical=True)
                     for r in rows],
            "count": len(rows)}


@router.get("/fix-runs/{run_id}")
def read_fix_run(run_id: str, user: User = Depends(require_support_operator),
                 db: Session = Depends(get_db)) -> Dict[str, Any]:
    run = db.query(SupportFixRun).filter(SupportFixRun.id == run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Repair run not found.")
    support_authority.assert_visible(db, user, platform_id=run.platform_id)
    return support_remediation.run_summary(run, technical=True)


class ApprovalRequest(BaseModel):
    note: Optional[str] = Field(None, max_length=2000)


@router.post("/fix-runs/{run_id}/approve")
def approve_fix(run_id: str,
                req: ApprovalRequest = Body(default=ApprovalRequest()),
                user: User = Depends(require_god_for_configuration),
                db: Session = Depends(get_db)) -> Dict[str, Any]:
    """GOD ONLY. Authorize a prepared remediation and run it.

    Two independent gates, deliberately: this route refuses a non-god caller,
    AND `support_remediation.authorization_for` refuses a non-god approval
    even if it were reached another way. A single gate on the one operation
    that can change billing or permissions is one review away from being
    removed by accident.
    """
    run = db.query(SupportFixRun).filter(SupportFixRun.id == run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Repair run not found.")
    try:
        result = support_remediation.approve_and_execute(
            db, run, god_user=user, note=req.note)
    except support_remediation.FixRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    db.commit()
    return support_remediation.run_summary(result, technical=True)


@router.post("/fix-runs/{run_id}/reject")
def reject_fix(run_id: str,
               req: ApprovalRequest = Body(default=ApprovalRequest()),
               user: User = Depends(require_god_for_configuration),
               db: Session = Depends(get_db)) -> Dict[str, Any]:
    run = db.query(SupportFixRun).filter(SupportFixRun.id == run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Repair run not found.")
    try:
        result = support_remediation.reject_proposal(db, run, god_user=user,
                                                     note=req.note)
    except support_remediation.FixRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    db.commit()
    return support_remediation.run_summary(result, technical=True)


class RunFixRequest(BaseModel):
    action_key: str
    organization_id: Optional[str] = None
    ticket_id: Optional[str] = None
    note: Optional[str] = Field(None, max_length=2000)


@router.post("/fix-runs")
def run_fix(req: RunFixRequest, user: User = Depends(require_god),
            db: Session = Depends(get_db)) -> Dict[str, Any]:
    """God runs a registered repair directly. Root authority, fully audited.

    `require_god` rather than the operator guard: running a repair against a
    customer on purpose is the owner's action. The registry still validates,
    snapshots, verifies and audits exactly as it does for an automatic run —
    root authority skips the POLICY gate, never the CONTRACT.
    """
    remediation = support_remediation.REGISTRY.get(req.action_key)
    if remediation is None:
        raise HTTPException(status_code=404, detail="Unknown repair.")

    org = None
    if req.organization_id:
        org = db.query(Organization).filter(
            Organization.id == req.organization_id).first()
        if org is None:
            raise HTTPException(status_code=404, detail="Customer not found.")
    ticket = None
    if req.ticket_id:
        ticket = _ticket_in_scope(db, user, req.ticket_id)
        if org is None:
            org = db.query(Organization).filter(
                Organization.id == ticket.organization_id).first()

    ctx = support_remediation.FixContext(db, org=org, actor=user, ticket=ticket,
                                          is_god=True)
    run = support_remediation.execute_fix(
        db, req.action_key, ctx, requested_by_kind="god",
        detection_source="god", diagnosis=req.note,
        signature=getattr(ticket, "issue_signature", None))
    db.commit()
    return support_remediation.run_summary(run, technical=True)


# ── Fix policy: what may run without a person ───────────────────────────────

@router.get("/policies")
def list_policies(user: User = Depends(require_god_for_configuration),
                  db: Session = Depends(get_db)) -> Dict[str, Any]:
    rows = db.query(SupportFixPolicy).all()
    return {
        "policies": [
            {"id": r.id, "action_key": r.action_key,
             "platform_id": r.platform_id, "organization_id": r.organization_id,
             "auto_execute": bool(r.auto_execute),
             "max_runs_per_day": r.max_runs_per_day, "reason": r.reason,
             "is_active": bool(r.is_active), "created_at": r.created_at}
            for r in rows],
        "controlled_repairs": support_remediation.list_registry(
            risk_classes=[RiskClass.CONTROLLED]),
        "note": ("Only CONTROLLED repairs are policy-gated. Safe repairs need "
                 "no policy; approval-class repairs cannot be delegated to "
                 "one."),
    }


class PolicyRequest(BaseModel):
    action_key: str
    platform_id: Optional[str] = None
    organization_id: Optional[str] = None
    auto_execute: bool = False
    max_runs_per_day: Optional[int] = Field(None, ge=1, le=1000)
    reason: Optional[str] = Field(None, max_length=2000)


@router.post("/policies", status_code=201)
def set_policy(req: PolicyRequest, user: User = Depends(require_god_for_configuration),
               db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Enable a CONTROLLED repair for one scope.

    REFUSES A POLICY WITH NO SCOPE. A row with neither a platform nor an
    organization would have to be read as "everywhere", and a permission that
    means everything by omission is the one people write by accident.

    REFUSES AN APPROVAL-CLASS REPAIR. Policy-gating a GOD_APPROVAL action
    would be a way of pre-approving every future instance of it, which is
    exactly what that class exists to prevent.
    """
    remediation = support_remediation.REGISTRY.get(req.action_key)
    if remediation is None:
        raise HTTPException(status_code=404, detail="Unknown repair.")
    if remediation.risk_class != RiskClass.CONTROLLED:
        raise HTTPException(
            status_code=400,
            detail="Only controlled repairs are policy-gated. '%s' is %s."
                   % (req.action_key, RiskClass.LABELS[remediation.risk_class]))
    if not req.platform_id and not req.organization_id:
        raise HTTPException(
            status_code=400,
            detail="A policy needs a scope: one brand or one customer. A "
                   "policy with neither would apply everywhere.")

    row = (db.query(SupportFixPolicy)
           .filter(SupportFixPolicy.action_key == req.action_key,
                   SupportFixPolicy.platform_id == req.platform_id,
                   SupportFixPolicy.organization_id == req.organization_id)
           .first())
    if row is None:
        row = SupportFixPolicy(action_key=req.action_key,
                               platform_id=req.platform_id,
                               organization_id=req.organization_id,
                               created_by=user.id)
        db.add(row)
    row.auto_execute = req.auto_execute
    row.max_runs_per_day = req.max_runs_per_day
    row.reason = req.reason
    row.is_active = True
    db.flush()

    from app.routers.audit_log_router import log_action
    log_action(db, req.organization_id, user.id,
               action="support.fix_policy_set", target_type="support_fix_policy",
               target_id=row.id, platform_id=req.platform_id,
               after={"action_key": req.action_key,
                      "auto_execute": req.auto_execute,
                      "max_runs_per_day": req.max_runs_per_day},
               note=req.reason, commit=False)
    db.commit()
    return {"id": row.id, "action_key": row.action_key,
            "auto_execute": bool(row.auto_execute)}


@router.delete("/policies/{policy_id}")
def delete_policy(policy_id: str,
                  user: User = Depends(require_god_for_configuration),
                  db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Deactivates rather than deletes. The history of what was permitted, and
    when, is part of the audit trail for anything that ran under it."""
    row = db.query(SupportFixPolicy).filter(
        SupportFixPolicy.id == policy_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Policy not found.")
    row.is_active = False
    row.auto_execute = False
    db.commit()
    return {"id": policy_id, "is_active": False}


# ══════════════════════════════════════════════════════════════════════════
# INCIDENTS AND RECURRENCE
# ══════════════════════════════════════════════════════════════════════════

@router.get("/incidents")
def list_incidents(status: Optional[str] = Query(None),
                   limit: int = Query(50, ge=1, le=200),
                   user: User = Depends(require_god),
                   db: Session = Depends(get_db)) -> Dict[str, Any]:
    """GOD ONLY, and that is structural rather than cautious.

    Every field on an incident is an aggregate ACROSS customers and across
    brands. There is no version of this list that could be shown to a brand
    operator without either lying about the numbers or disclosing another
    brand's, so it is not offered.
    """
    q = db.query(SupportIncident)
    if status == "open":
        q = q.filter(SupportIncident.status.in_(list(IncidentStatus.OPEN)))
    elif status in IncidentStatus.ALL:
        q = q.filter(SupportIncident.status == status)
    rows = q.order_by(SupportIncident.last_observed_at.desc()).limit(limit).all()
    return {"incidents": [support_incidents.incident_view(db, i) for i in rows],
            "count": len(rows)}


@router.get("/incidents/{incident_id}")
def read_incident(incident_id: str, user: User = Depends(require_god),
                  db: Session = Depends(get_db)) -> Dict[str, Any]:
    incident = db.query(SupportIncident).filter(
        SupportIncident.id == incident_id).first()
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found.")
    view = support_incidents.incident_view(db, incident)
    view["tickets"] = [support_tickets.list_summary(db, t)
                       for t in support_incidents.tickets_for_incident(
                           db, incident.id)]
    return view


class AcknowledgeRequest(BaseModel):
    customer_statement: Optional[str] = Field(None, max_length=1000)


@router.post("/incidents/{incident_id}/acknowledge")
def acknowledge_incident(incident_id: str,
                         req: AcknowledgeRequest = Body(default=AcknowledgeRequest()),
                         user: User = Depends(require_god),
                         db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Take ownership, and optionally write the one sentence customers see.

    The statement is OPTIONAL and stays empty unless a person writes it.
    Generating customer-facing prose about an incident nobody has understood
    yet is how a status page starts saying things that turn out to be untrue.
    """
    incident = db.query(SupportIncident).filter(
        SupportIncident.id == incident_id).first()
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found.")
    support_incidents.acknowledge(db, incident, user=user,
                                  statement=req.customer_statement)
    db.commit()
    return support_incidents.incident_view(db, incident)


class IncidentStatusRequest(BaseModel):
    status: str
    note: Optional[str] = Field(None, max_length=2000)


@router.post("/incidents/{incident_id}/status")
def set_incident_status(incident_id: str, req: IncidentStatusRequest,
                        user: User = Depends(require_god),
                        db: Session = Depends(get_db)) -> Dict[str, Any]:
    incident = db.query(SupportIncident).filter(
        SupportIncident.id == incident_id).first()
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found.")
    try:
        support_incidents.set_status(db, incident, req.status, note=req.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.commit()
    return support_incidents.incident_view(db, incident)


@router.post("/correlate")
def run_correlation(hours: int = Query(6, ge=1, le=168),
                    user: User = Depends(require_god),
                    db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Run correlation now, rather than waiting for the nightly pass."""
    result = support_incidents.correlate(db, window_hours=hours)
    db.commit()
    return result


@router.get("/recurring")
def recurring(days: int = Query(7, ge=1, le=90),
              user: User = Depends(require_support_operator),
              db: Session = Depends(get_db)) -> Dict[str, Any]:
    """What keeps coming back, with the sentence that says what it means."""
    return support_incidents.recurring_report(db, days=days)


# ══════════════════════════════════════════════════════════════════════════
# THE DAILY BRIEF
# ══════════════════════════════════════════════════════════════════════════

@router.get("/brief")
def read_brief(date: Optional[str] = Query(None),
               platform_id: Optional[str] = Query(None),
               user: User = Depends(require_god),
               db: Session = Depends(get_db)) -> Dict[str, Any]:
    """The persisted daily intelligence. Not generated on read.

    A brief that regenerated itself every time somebody opened it would
    report today's state under yesterday's date, and the trend line would
    move whenever anyone looked at it.
    """
    from app.models.support_models import SupportDailyBrief
    if date:
        brief = (db.query(SupportDailyBrief)
                 .filter(SupportDailyBrief.brief_date == date,
                         SupportDailyBrief.platform_id == platform_id).first())
    else:
        brief = support_brief.latest(db, platform_id=platform_id)
    if brief is None:
        return {"brief": None,
                "note": "No brief has been generated for that day yet."}
    return {"brief": support_brief.brief_view(brief)}


@router.post("/brief/generate")
def generate_brief(date: Optional[str] = Body(None, embed=True),
                   user: User = Depends(require_god),
                   db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Run the whole nightly intelligence pass on demand.

    Refresh SLA states, correlate, scan for learning candidates, then write
    the briefs — in that order, because a brief generated first would be a
    document about yesterday's understanding of yesterday.
    """
    return support_brief.run_daily_intelligence(db, day=date)


@router.get("/learning")
def learning_candidates(user: User = Depends(require_support_operator),
                        db: Session = Depends(get_db)) -> Dict[str, Any]:
    """What the platform noticed and wants a person to decide about."""
    platform_ids = support_authority.visible_platform_ids(db, user)
    scope = platform_ids[0] if platform_ids else None
    rows = support_knowledge.open_candidates(db, platform_id=scope)
    return {"candidates": [
        {"id": r.id, "kind": r.kind, "title": r.title, "signature": r.signature,
         "rationale": r.rationale, "occurrences": r.occurrence_count,
         "organizations": r.organizations_affected, "created_at": r.created_at}
        for r in rows]}


class CandidateDecision(BaseModel):
    accept: bool
    note: Optional[str] = Field(None, max_length=2000)


@router.post("/learning/{candidate_id}")
def decide_candidate(candidate_id: str, req: CandidateDecision,
                     user: User = Depends(require_god),
                     db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Accept or reject a proposal.

    ACCEPTING PUBLISHES NOTHING AND REGISTERS NOTHING. It records the
    decision. Writing the article is a person's job and registering a
    remediation is an engineering change — a proposal that could turn itself
    into either would be the uncontrolled self-training this design exists to
    avoid.
    """
    from app.models.support_models import SupportKnowledgeCandidate
    row = (db.query(SupportKnowledgeCandidate)
           .filter(SupportKnowledgeCandidate.id == candidate_id).first())
    if row is None:
        raise HTTPException(status_code=404, detail="Candidate not found.")
    support_knowledge.decide(db, row, accept=req.accept, actor_id=user.id,
                             note=req.note)
    db.commit()
    return {"id": row.id, "status": row.status}


# ══════════════════════════════════════════════════════════════════════════
# CONFIGURATION — the support PRODUCT, per brand. God only.
# ══════════════════════════════════════════════════════════════════════════

@router.get("/config/{platform_id}")
def read_config(platform_id: str,
                user: User = Depends(require_god_for_configuration),
                db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Everything one brand's support product promises.

    `unconfigured_count` is the field worth looking at: it counts packages
    running on the frozen defaults because nobody ever configured them. From
    the customer's SLA page that is indistinguishable from a decision, which
    is precisely why it needs to be visible here.
    """
    platform = db.query(Platform).filter(Platform.id == platform_id).first()
    if platform is None:
        raise HTTPException(status_code=404, detail="Brand not found.")
    return {
        "platform": {"id": platform.id, "name": platform.name,
                     "slug": platform.slug},
        "branding": support_branding.brand_for_platform(db, platform_id),
        "entitlements": support_entitlements.config_report(db, platform_id),
        "hours": support_sla.hours_summary(db, platform_id),
        "offerings": [
            {"id": r.id, "key": r.key, "name": r.name, "category": r.category,
             "pricing_mode": r.pricing_mode, "price_cents": r.price_cents,
             "unit": r.unit, "stripe_price_id": r.stripe_price_id,
             "is_active": bool(r.is_active)}
            for r in db.query(SupportServiceOffering)
            .filter(SupportServiceOffering.platform_id == platform_id)
            .order_by(SupportServiceOffering.sort_order.asc()).all()],
    }


class EntitlementRequest(BaseModel):
    plan_key: str
    display_name: Optional[str] = None
    queue: Optional[str] = None
    first_response_normal_minutes: Optional[int] = Field(None, ge=1, le=100000)
    first_response_high_minutes: Optional[int] = Field(None, ge=1, le=100000)
    first_response_critical_minutes: Optional[int] = Field(None, ge=1, le=100000)
    included_assistance_minutes: Optional[int] = Field(None, ge=0, le=100000)
    emergency_override: Optional[bool] = None
    features: Optional[List[str]] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None


@router.put("/config/{platform_id}/entitlements")
def set_entitlement(platform_id: str, req: EntitlementRequest,
                    user: User = Depends(require_god_for_configuration),
                    db: Session = Depends(get_db)) -> Dict[str, Any]:
    """What one package of one brand includes.

    MINUTES ARE BUSINESS MINUTES. The field names say so, and
    `support_sla._humanize` renders them back to the customer in the brand's
    own working day — so "480" reads as "1 business day" for a brand that
    works eight hours and as something else for a brand that does not.
    """
    if db.query(Platform).filter(Platform.id == platform_id).first() is None:
        raise HTTPException(status_code=404, detail="Brand not found.")
    if req.queue is not None and req.queue not in Queue.ALL:
        raise HTTPException(status_code=400, detail="Unknown queue.")

    values = {k: v for k, v in req.dict().items()
              if v is not None and k != "plan_key"}
    row = support_entitlements.upsert_config(db, platform_id=platform_id,
                                             plan_key=req.plan_key, values=values)
    from app.routers.audit_log_router import log_action
    log_action(db, None, user.id, action="support.entitlement_configured",
               target_type="support_entitlement_config", target_id=row.id,
               platform_id=platform_id, after=values, commit=False)
    db.commit()
    return support_entitlements.config_report(db, platform_id)


class BrandSettingsRequest(BaseModel):
    assistant_name: Optional[str] = Field(None, max_length=80)
    help_center_name: Optional[str] = Field(None, max_length=120)
    support_display_name: Optional[str] = Field(None, max_length=120)
    greeting: Optional[str] = Field(None, max_length=1000)
    timezone: Optional[str] = Field(None, max_length=64)
    business_days: Optional[str] = Field(None, max_length=32)
    business_start: Optional[str] = Field(None, max_length=5)
    business_end: Optional[str] = Field(None, max_length=5)
    holidays: Optional[List[str]] = None
    emergency_is_24x7: Optional[bool] = None


@router.put("/config/{platform_id}/brand")
def set_brand_settings(platform_id: str, req: BrandSettingsRequest,
                       user: User = Depends(require_god_for_configuration),
                       db: Session = Depends(get_db)) -> Dict[str, Any]:
    """The brand's support name and its working week.

    `holidays` is an explicit list of ISO dates rather than a country code:
    a platform serving several countries has no single holiday calendar, and
    an inferred one closes support on a day the team is working.
    """
    import json as _json
    if db.query(Platform).filter(Platform.id == platform_id).first() is None:
        raise HTTPException(status_code=404, detail="Brand not found.")

    values = {k: v for k, v in req.dict().items()
              if v is not None and k != "holidays"}
    if req.holidays is not None:
        from datetime import date as _date
        cleaned = []
        for item in req.holidays:
            try:
                cleaned.append(_date.fromisoformat(str(item)).isoformat())
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail="Holidays must be ISO dates (YYYY-MM-DD). %r is not."
                           % item)
        values["holidays_json"] = _json.dumps(sorted(set(cleaned)))

    support_branding.upsert_settings(db, platform_id=platform_id, values=values)
    from app.routers.audit_log_router import log_action
    log_action(db, None, user.id, action="support.brand_settings_configured",
               target_type="platform", target_id=platform_id,
               platform_id=platform_id, after=values, commit=False)
    db.commit()
    return {"branding": support_branding.brand_for_platform(db, platform_id),
            "hours": support_sla.hours_summary(db, platform_id)}


class OfferingRequest(BaseModel):
    key: str = Field(..., max_length=80)
    name: str = Field(..., max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    category: str = TicketCategory.PROFESSIONAL_SERVICES
    pricing_mode: str = "quoted"
    price_cents: Optional[int] = Field(None, ge=0)
    currency: str = "usd"
    unit: Optional[str] = Field(None, max_length=40)
    stripe_price_id: Optional[str] = Field(None, max_length=120)
    sort_order: int = 100
    is_active: bool = True


@router.put("/config/{platform_id}/offerings")
def set_offering(platform_id: str, req: OfferingRequest,
                 user: User = Depends(require_god_for_configuration),
                 db: Session = Depends(get_db)) -> Dict[str, Any]:
    """A support or professional-service product this brand sells.

    NO PRICE IS INVENTED HERE OR ANYWHERE. `pricing_mode='quoted'` with a
    NULL amount is the normal case, and the customer-facing list renders it
    as "quoted" rather than as a number nobody agreed to. `stripe_price_id`
    is the hook into the EXISTING catalogue for the few that are a fixed SKU;
    nothing in this module charges anything.
    """
    if db.query(Platform).filter(Platform.id == platform_id).first() is None:
        raise HTTPException(status_code=404, detail="Brand not found.")
    if req.category not in TicketCategory.ALL:
        raise HTTPException(status_code=400, detail="Unknown category.")
    if req.pricing_mode not in ("quoted", "fixed", "hourly", "included"):
        raise HTTPException(status_code=400, detail="Unknown pricing mode.")
    if req.pricing_mode in ("fixed", "hourly") and req.price_cents is None:
        raise HTTPException(
            status_code=400,
            detail="A fixed or hourly offering needs a price. Use 'quoted' if "
                   "the amount is decided per customer.")

    row = (db.query(SupportServiceOffering)
           .filter(SupportServiceOffering.platform_id == platform_id,
                   SupportServiceOffering.key == req.key).first())
    if row is None:
        row = SupportServiceOffering(platform_id=platform_id, key=req.key)
        db.add(row)
    for field, value in req.dict().items():
        if field != "key":
            setattr(row, field, value)
    db.flush()

    from app.routers.audit_log_router import log_action
    log_action(db, None, user.id, action="support.offering_configured",
               target_type="support_service_offering", target_id=row.id,
               platform_id=platform_id,
               after={"key": row.key, "pricing_mode": row.pricing_mode,
                      "price_cents": row.price_cents}, commit=False)
    db.commit()
    return {"id": row.id, "key": row.key}


# ── Knowledge base administration ───────────────────────────────────────────

@router.get("/knowledge")
def admin_knowledge(platform_id: Optional[str] = Query(None),
                    user: User = Depends(require_support_operator),
                    db: Session = Depends(get_db)) -> Dict[str, Any]:
    rows = support_knowledge.list_articles(db, platform_id=platform_id,
                                           include_unpublished=True)
    return {"articles": [
        {"id": a.id, "slug": a.slug, "title": a.title,
         "platform_id": a.platform_id, "category": a.category,
         "is_published": bool(a.is_published), "views": a.view_count,
         "helpful": a.helpful_count, "unhelpful": a.unhelpful_count,
         "updated_at": a.updated_at}
        for a in rows]}


class ArticleRequest(BaseModel):
    slug: str = Field(..., max_length=120)
    title: str = Field(..., max_length=250)
    body: str = Field(..., min_length=1)
    summary: Optional[str] = Field(None, max_length=500)
    category: Optional[str] = Field(None, max_length=80)
    keywords: Optional[str] = Field(None, max_length=500)
    platform_id: Optional[str] = None
    is_published: bool = False


@router.put("/knowledge")
def upsert_article(req: ArticleRequest,
                   user: User = Depends(require_god_for_configuration),
                   db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Write or update an article.

    `is_published` defaults to False. Writing and publishing are two
    decisions, and a draft that publishes itself is a draft in front of every
    customer.
    """
    values = req.dict()
    values.pop("slug")
    platform_id = values.pop("platform_id")
    row = support_knowledge.upsert_article(db, platform_id=platform_id,
                                           slug=req.slug, values=values,
                                           actor_id=user.id)
    db.commit()
    return {"id": row.id, "slug": row.slug,
            "is_published": bool(row.is_published)}


@router.post("/knowledge/seed")
def seed_knowledge(publish: bool = Body(True, embed=True),
                   user: User = Depends(require_god_for_configuration),
                   db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Install the platform-wide starter articles. Explicit, never on deploy.

    A help centre that repopulated itself at startup would overwrite a
    brand's edits on every restart.
    """
    result = support_knowledge.seed_starter_articles(db, actor_id=user.id,
                                                     publish=publish)
    db.commit()
    return result


@router.get("/brands")
def list_brands(user: User = Depends(require_support_operator),
                db: Session = Depends(get_db)) -> Dict[str, Any]:
    """The brands this operator may act for, with their support configuration
    state. God sees every brand; an operator sees theirs."""
    platform_ids = support_authority.visible_platform_ids(db, user)
    q = db.query(Platform)
    if platform_ids is not None:
        if not platform_ids:
            return {"brands": []}
        q = q.filter(Platform.id.in_(platform_ids))
    out = []
    for platform in q.order_by(Platform.name.asc()).all():
        report = support_entitlements.config_report(db, platform.id)
        out.append({
            "id": platform.id, "name": platform.name, "slug": platform.slug,
            "assistant_name": support_branding.brand_for_platform(
                db, platform.id)["assistant_name"],
            "packages_configured": len(report["packages"]) - report["unconfigured_count"],
            "packages_unconfigured": report["unconfigured_count"],
            "hours_source": support_sla.hours_summary(db, platform.id)["source"],
        })
    return {"brands": out}
