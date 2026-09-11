"""THE CUSTOMER'S HELP & SUPPORT SURFACE.

    /support/me                  who they are talking to, and what they have
    /support/plan                package, queue, targets, assistance balance
    /support/ask                 Ask [Brand]
    /support/tickets             raise, read, reply, reopen
    /support/status              live system status, customer-safe
    /support/knowledge           the help centre
    /support/services            what can be bought
    /support/assistance          book scheduled live help

EVERY ROUTE ANSWERS FOR ONE ORGANIZATION: THE CALLER'S OWN.
------------------------------------------------------------
`_org()` resolves it from the authenticated user and nothing else. No route
here accepts an organization id, a platform id or a brand from a request —
not in a path, not in a query string, not in a body. That is why the tenant
tests in `tests/test_support_security.py` can be short: there is no parameter
to attack.

A god_admin inside a customer context (X-Org-Override) is treated as that
customer here, deliberately: the owner needs to be able to SEE what the
customer sees. What they cannot do from these routes is anything god-only —
those live on /god/support and are guarded there.

WHY THE READ MODELS ARE FUNCTIONS AND NOT SERIALIZERS
------------------------------------------------------
`support_tickets.customer_view` is the one place the internal/external
boundary is applied. These routes call it and return what it returns. A route
that assembled its own dict would be a second boundary, and the failure mode
of the second one is an internal note in a customer's browser.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import (
    APIRouter, Body, Depends, File, HTTPException, Query, Response, UploadFile,
)
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.deps import get_db, require_not_observation, require_tenant_user
from app.models.models import Organization, User
from app.models.support_models import (
    Queue, Severity, SupportServiceOffering, SupportTicket,
    SupportTicketAttachment, TicketCategory, TicketStatus,
)
from app.services import (
    support_ai, support_branding, support_diagnostics, support_entitlements,
    support_knowledge, support_remediation, support_sla, support_tickets,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/support", tags=["support"])


def _org(db: Session, user: User) -> Organization:
    """The caller's organization, or a clean refusal.

    Uses `platform_owner.selected_org_id`, which returns None for the neutral
    owner rather than the platform pseudo-org — the distinction that module
    exists to enforce. An owner with no customer selected gets a 409 telling
    them to pick one, not somebody else's support queue.
    """
    from app.services import platform_owner
    org_id = platform_owner.selected_org_id(user)
    if not org_id:
        raise HTTPException(
            status_code=409,
            detail="No customer workspace is selected. Support answers for one "
                   "customer at a time — enter a customer context first.")
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if org is None:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    return org


def _ticket(db: Session, org: Organization, ticket_id: str) -> SupportTicket:
    """One ticket, and it must be theirs.

    404 rather than 403 for another organization's ticket: `deps.load_org_in_
    scope` already established that refusing with "not found" is what keeps a
    guessable id from confirming anything.
    """
    ticket = (db.query(SupportTicket)
              .filter(SupportTicket.id == ticket_id,
                      SupportTicket.organization_id == org.id).first())
    if ticket is None:
        raise HTTPException(status_code=404, detail="Request not found.")
    return ticket


# ══════════════════════════════════════════════════════════════════════════
# WHO AM I TALKING TO, AND WHAT DO I HAVE
# ══════════════════════════════════════════════════════════════════════════

@router.get("/me")
def support_home(user: User = Depends(require_tenant_user),
                 db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Everything the Help & Support shell needs to render itself, branded."""
    org = _org(db, user)
    greeting = support_ai.brand_greeting(db, org)
    entitlement = support_entitlements.resolve(db, org)
    open_count = (db.query(SupportTicket)
                  .filter(SupportTicket.organization_id == org.id,
                          SupportTicket.status.in_(
                              list(TicketStatus.OPEN) +
                              [TicketStatus.WAITING_ON_CUSTOMER])).count())
    return {
        **greeting,
        "support_plan": entitlement["display_name"],
        "features": entitlement["features"],
        "open_tickets": open_count,
        "hours": support_sla.hours_summary(db, org.platform_id),
    }


@router.get("/plan")
def support_plan(user: User = Depends(require_tenant_user),
                 db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Support Plan & SLA — what was promised, and what is left of the allowance.

    RENDERED FROM THE SAME ENTITLEMENT THE ENGINE USES, so this page cannot
    advertise a response target the clock does not honour. The assistance
    figures are recomputed from the ledger for the current period; there is no
    stored balance to drift.
    """
    org = _org(db, user)
    entitlement = support_entitlements.resolve(db, org)
    assistance = support_entitlements.assistance_summary(db, org, entitlement)
    brand = support_branding.brand_for_org(db, org)

    return {
        "plan": {
            "name": entitlement["display_name"],
            "plan_key": entitlement["plan_key"],
            "queue": entitlement["queue"],
            "queue_label": entitlement["queue_label"],
            "features": [{"key": k, "label": support_entitlements.SUPPORT_FEATURES[k]}
                         for k in entitlement["features"]],
        },
        "response_targets": support_sla.queue_targets(db, entitlement,
                                                      org.platform_id),
        "hours": support_sla.hours_summary(db, org.platform_id),
        "assistance": assistance,
        # SAID OUT LOUD ON THE PAGE, because it is the promise customers most
        # often assume they have and most often do not.
        "commitment_note": ("These are first-response targets — when you will "
                            "hear from a person. We don't publish resolution "
                            "times."),
        "emergency_note": ("A confirmed outage goes to our emergency queue "
                           "regardless of your package."),
        "support_email": brand["support_email"],
    }


@router.get("/services")
def support_services(user: User = Depends(require_tenant_user),
                     db: Session = Depends(get_db)) -> Dict[str, Any]:
    """What this brand sells beyond what the package includes.

    A price is shown ONLY where somebody configured one. Everything else says
    "quoted", because inventing a number here would be inventing revenue and
    the customer would be told it.
    """
    org = _org(db, user)
    rows = (db.query(SupportServiceOffering)
            .filter(SupportServiceOffering.platform_id == org.platform_id,
                    SupportServiceOffering.is_active.is_(True))
            .order_by(SupportServiceOffering.sort_order.asc()).all())
    return {
        "offerings": [
            {"key": r.key, "name": r.name, "description": r.description,
             "category": r.category,
             "category_label": TicketCategory.LABELS.get(r.category, r.category),
             "pricing_mode": r.pricing_mode,
             "price_cents": r.price_cents, "currency": r.currency,
             "unit": r.unit,
             "price_text": (None if r.price_cents is None
                            else "$%.2f%s" % (r.price_cents / 100.0,
                                              " / %s" % r.unit if r.unit else ""))}
            for r in rows],
        "note": ("Anything without a price is scoped and quoted before it "
                 "starts."),
    }


# ══════════════════════════════════════════════════════════════════════════
# ASK [BRAND]
# ══════════════════════════════════════════════════════════════════════════

class AskRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    conversation_id: Optional[str] = None


@router.post("/ask")
def ask(req: AskRequest, user: User = Depends(require_tenant_user),
        _guard: User = Depends(require_not_observation),
        db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Ask the brand's assistant. It can look at this customer's own account.

    `require_not_observation` because this turn can EXECUTE a repair. An
    executive observing a customer read-only must not be able to change their
    state through a chat box — the observation guard already exists for
    exactly this, and a support surface is not an exception to it.

    The conversation is loaded with an organization filter
    (`support_ai.load_conversation`), so a `conversation_id` from a request
    body cannot reach another customer's thread.
    """
    org = _org(db, user)
    conversation = None
    if req.conversation_id:
        conversation = support_ai.load_conversation(
            db, org=org, conversation_id=req.conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found.")
    if conversation is None:
        conversation = support_ai.start_conversation(db, org=org, user=user)

    result = support_ai.ask(db, org=org, user=user, conversation=conversation,
                            message=req.message)
    db.commit()
    return result


@router.get("/conversations/{conversation_id}")
def read_conversation(conversation_id: str,
                      user: User = Depends(require_tenant_user),
                      db: Session = Depends(get_db)) -> Dict[str, Any]:
    org = _org(db, user)
    conversation = support_ai.load_conversation(db, org=org,
                                                conversation_id=conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    turns = support_ai.history(db, conversation, limit=100)
    return {
        "id": conversation.id,
        "assistant_name": conversation.assistant_name,
        "title": conversation.title,
        "status": conversation.status,
        "escalated_ticket_id": conversation.escalated_ticket_id,
        "turns": [{"role": t.role, "content": t.content, "at": t.created_at,
                   "source": t.source} for t in turns],
    }


# ══════════════════════════════════════════════════════════════════════════
# TICKETS
# ══════════════════════════════════════════════════════════════════════════

class CreateTicketRequest(BaseModel):
    subject: str = Field(..., min_length=1, max_length=300)
    body: str = Field(..., min_length=1, max_length=20000)
    category: str = TicketCategory.TECHNICAL_PRODUCT_SUPPORT
    # HOW URGENT IT FEELS. Recorded, shown to the engineer, and deliberately
    # NOT used to set priority — see support_tickets.resolve_severity.
    urgency: Optional[str] = None
    conversation_id: Optional[str] = None
    diagnostic_run_id: Optional[str] = None


@router.post("/tickets", status_code=201)
def create_ticket(req: CreateTicketRequest,
                  user: User = Depends(require_tenant_user),
                  _guard: User = Depends(require_not_observation),
                  db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Raise a request. Diagnostics run first, so a person inherits the evidence.

    If the customer came from a conversation, its diagnosis and signature are
    carried over rather than re-derived — which is what stops the ticket
    asking them to explain it all again.
    """
    org = _org(db, user)

    ai_summary = None
    cause = None
    confidence = None
    recommendation = None
    signature = None
    diagnostic_run_id = req.diagnostic_run_id
    recommended_severity = None

    conversation = None
    if req.conversation_id:
        conversation = support_ai.load_conversation(
            db, org=org, conversation_id=req.conversation_id)
        if conversation is not None:
            cause = conversation.suspected_cause
            signature = conversation.issue_signature

    # A ticket raised straight from the form still gets diagnosed. The
    # customer typed a sentence; the engineer should not have to start from
    # one.
    if diagnostic_run_id is None:
        summary = support_diagnostics.run_checks(
            db, org=org, user=user,
            keys=support_ai.checks_for_text("%s %s" % (req.subject, req.body)),
            is_god=False)
        run = support_diagnostics.persist_run(
            db, summary, org=org, requested_by=user.id,
            requested_by_kind="customer")
        diagnostic_run_id = run.id
        cause = cause or summary.get("suspected_cause")
        ai_summary = " ".join(
            i["headline"] for i in summary.get("customer_view", [])
            if i.get("severity") in ("action_required", "attention")) or None
        signature = signature or _signature(summary, cause)
        recommended_severity = _severity_from_summary(summary)

    ticket = support_tickets.create_ticket(
        db, org=org, user=user, subject=req.subject, body=req.body,
        category=req.category,
        customer_reported_severity=req.urgency,
        ai_recommended_severity=recommended_severity,
        ai_summary=ai_summary, ai_suspected_cause=cause,
        ai_confidence=confidence, ai_recommendation=recommendation,
        diagnostic_run_id=diagnostic_run_id, signature=signature,
        conversation_id=req.conversation_id)
    db.commit()
    return support_tickets.customer_view(db, ticket)


def _signature(summary: Dict[str, Any], cause: Optional[str]) -> Optional[str]:
    try:
        from app.services import support_incidents
        service = None
        for item in summary.get("customer_view", []):
            if item.get("severity") in ("action_required", "attention"):
                service = item.get("service")
                break
        return support_incidents.signature_for(service, summary.get("signals"),
                                               cause)
    except Exception:                                          # noqa: BLE001
        log.exception("support_router: signature computation failed")
        return None


def _severity_from_summary(summary: Dict[str, Any]) -> Optional[str]:
    overall = summary.get("overall_severity")
    if overall == "action_required":
        return Severity.P2
    if overall == "attention":
        return Severity.P3
    return None


@router.get("/tickets")
def list_tickets(status: Optional[str] = Query(None),
                 limit: int = Query(50, ge=1, le=200),
                 offset: int = Query(0, ge=0),
                 user: User = Depends(require_tenant_user),
                 db: Session = Depends(get_db)) -> Dict[str, Any]:
    org = _org(db, user)
    rows = support_tickets.list_for_org(db, org, status=status, limit=limit,
                                        offset=offset)
    return {"tickets": [support_tickets.list_summary(db, t) for t in rows],
            "count": len(rows)}


@router.get("/tickets/{ticket_id}")
def read_ticket(ticket_id: str, user: User = Depends(require_tenant_user),
                db: Session = Depends(get_db)) -> Dict[str, Any]:
    org = _org(db, user)
    return support_tickets.customer_view(db, _ticket(db, org, ticket_id))


class ReplyRequest(BaseModel):
    body: str = Field(..., min_length=1, max_length=20000)


@router.post("/tickets/{ticket_id}/reply")
def reply(ticket_id: str, req: ReplyRequest,
          user: User = Depends(require_tenant_user),
          _guard: User = Depends(require_not_observation),
          db: Session = Depends(get_db)) -> Dict[str, Any]:
    """A customer replies. `is_internal=False` is hard-coded and not a parameter.

    There is no request shape in which a customer can write an internal note,
    which is a property of this signature rather than of a validation rule.
    """
    org = _org(db, user)
    ticket = _ticket(db, org, ticket_id)
    if ticket.status == TicketStatus.CLOSED:
        raise HTTPException(
            status_code=409,
            detail="This request is closed. Reopen it to add a reply.")
    support_tickets.add_message(db, ticket, author_kind="customer", user=user,
                                body=req.body, is_internal=False)
    db.commit()
    return support_tickets.customer_view(db, ticket)


class ReopenRequest(BaseModel):
    reason: Optional[str] = Field(None, max_length=2000)


@router.post("/tickets/{ticket_id}/reopen")
def reopen(ticket_id: str, req: ReopenRequest = Body(default=ReopenRequest()),
           user: User = Depends(require_tenant_user),
           _guard: User = Depends(require_not_observation),
           db: Session = Depends(get_db)) -> Dict[str, Any]:
    org = _org(db, user)
    ticket = _ticket(db, org, ticket_id)
    if ticket.status not in (TicketStatus.RESOLVED, TicketStatus.CLOSED):
        raise HTTPException(status_code=409,
                            detail="This request is still open.")
    support_tickets.reopen(db, ticket, actor=user, reason=req.reason)
    if req.reason:
        support_tickets.add_message(db, ticket, author_kind="customer",
                                    user=user, body=req.reason, is_internal=False)
    db.commit()
    return support_tickets.customer_view(db, ticket)


class CloseRequest(BaseModel):
    satisfaction: Optional[int] = Field(None, ge=1, le=5)


@router.post("/tickets/{ticket_id}/close")
def close(ticket_id: str, req: CloseRequest = Body(default=CloseRequest()),
          user: User = Depends(require_tenant_user),
          _guard: User = Depends(require_not_observation),
          db: Session = Depends(get_db)) -> Dict[str, Any]:
    """The customer says they are done. Their answer, not ours.

    A satisfaction score is only ever stored when the customer supplies one —
    there is no default, because an unanswered survey that stores a 5 is a
    metric measuring nothing.
    """
    org = _org(db, user)
    ticket = _ticket(db, org, ticket_id)
    if req.satisfaction is not None:
        ticket.satisfaction_score = req.satisfaction
    support_tickets.set_status(db, ticket, TicketStatus.CLOSED, actor=user,
                               actor_kind="customer",
                               detail="Closed by the customer.")
    db.commit()
    return support_tickets.customer_view(db, ticket)


@router.post("/tickets/{ticket_id}/attachments", status_code=201)
def upload_attachment(ticket_id: str, file: UploadFile = File(...),
                      user: User = Depends(require_tenant_user),
                      _guard: User = Depends(require_not_observation),
                      db: Session = Depends(get_db)) -> Dict[str, Any]:
    org = _org(db, user)
    ticket = _ticket(db, org, ticket_id)
    data = file.file.read()
    attachment = support_tickets.add_attachment(
        db, ticket, filename=file.filename or "attachment",
        content_type=file.content_type or "application/octet-stream",
        data=data, user=user)
    db.commit()
    return {"id": attachment.id, "filename": attachment.filename,
            "size": attachment.file_size}


@router.get("/tickets/{ticket_id}/attachments/{attachment_id}")
def download_attachment(ticket_id: str, attachment_id: str,
                        user: User = Depends(require_tenant_user),
                        db: Session = Depends(get_db)):
    """Both ids are checked against the caller's organization.

    The attachment is filtered by BOTH its ticket and its organization. An
    attachment id from another tenant that happened to be attached to a
    ticket id from this one would still not match.
    """
    org = _org(db, user)
    ticket = _ticket(db, org, ticket_id)
    attachment = (db.query(SupportTicketAttachment)
                  .filter(SupportTicketAttachment.id == attachment_id,
                          SupportTicketAttachment.ticket_id == ticket.id,
                          SupportTicketAttachment.organization_id == org.id)
                  .first())
    if attachment is None:
        raise HTTPException(status_code=404, detail="Attachment not found.")
    return Response(
        content=attachment.file_data, media_type=attachment.content_type,
        headers={"Content-Disposition":
                 'attachment; filename="%s"' % attachment.filename})


@router.post("/tickets/{ticket_id}/retry-fix")
def retry_fix(ticket_id: str, action_key: str = Body(..., embed=True),
              user: User = Depends(require_tenant_user),
              _guard: User = Depends(require_not_observation),
              db: Session = Depends(get_db)) -> Dict[str, Any]:
    """"Try that again" on the customer's own ticket.

    ONLY SAFE_AUTO REPAIRS ARE REACHABLE FROM HERE, and that is enforced by
    `support_remediation.authorization_for`, not by this route: a customer
    requesting a CONTROLLED or GOD_APPROVAL action gets a prepared proposal
    and a message saying a person will look — never an execution.
    """
    org = _org(db, user)
    ticket = _ticket(db, org, ticket_id)
    if action_key not in support_remediation.REGISTRY:
        raise HTTPException(status_code=404, detail="Unknown repair.")

    ctx = support_remediation.FixContext(db, org=org, actor=user, ticket=ticket,
                                          is_god=False)
    run = support_remediation.execute_fix(
        db, action_key, ctx, requested_by_kind="customer",
        detection_source="customer_retry",
        signature=ticket.issue_signature)
    db.commit()
    return support_remediation.run_summary(run, technical=False)


# ══════════════════════════════════════════════════════════════════════════
# STATUS, KNOWLEDGE, ASSISTANCE
# ══════════════════════════════════════════════════════════════════════════

@router.get("/status")
def system_status(user: User = Depends(require_tenant_user),
                  db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Live status of the services THIS customer depends on.

    Platform-scope checks are excluded by construction in
    `support_diagnostics.system_status`. A known incident contributes a
    sanitized sentence and nothing else — no counts, no other brands, no
    admission that other customers exist.
    """
    org = _org(db, user)
    return support_diagnostics.system_status(db, org=org, user=user)


@router.get("/knowledge")
def knowledge_list(q: Optional[str] = Query(None, max_length=200),
                   user: User = Depends(require_tenant_user),
                   db: Session = Depends(get_db)) -> Dict[str, Any]:
    org = _org(db, user)
    if q:
        rows = support_knowledge.search(db, platform_id=org.platform_id,
                                        query=q, limit=20)
    else:
        rows = support_knowledge.list_articles(db, platform_id=org.platform_id)
    return {"articles": [support_knowledge.article_view(a, full=False)
                         for a in rows],
            "count": len(rows)}


@router.get("/knowledge/{slug}")
def knowledge_article(slug: str, user: User = Depends(require_tenant_user),
                      db: Session = Depends(get_db)) -> Dict[str, Any]:
    org = _org(db, user)
    article = support_knowledge.get_by_slug(db, platform_id=org.platform_id,
                                            slug=slug)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found.")
    support_knowledge.record_view(db, article)
    db.commit()
    return support_knowledge.article_view(article, full=True)


@router.post("/knowledge/{slug}/feedback")
def knowledge_feedback(slug: str, helpful: bool = Body(..., embed=True),
                       user: User = Depends(require_tenant_user),
                       db: Session = Depends(get_db)) -> Dict[str, Any]:
    org = _org(db, user)
    article = support_knowledge.get_by_slug(db, platform_id=org.platform_id,
                                            slug=slug)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found.")
    support_knowledge.record_feedback(db, article, helpful)
    db.commit()
    return {"recorded": True}


class AssistanceRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=2000)
    minutes: int = Field(30, ge=15, le=240)
    category: str = TicketCategory.CUSTOMER_ASSISTANCE


@router.post("/assistance", status_code=201)
def request_assistance(req: AssistanceRequest,
                       user: User = Depends(require_tenant_user),
                       _guard: User = Depends(require_not_observation),
                       db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Ask for scheduled live help, and be told up front what it costs.

    THE ANSWER IS COMPUTED, NOT PROMISED. The response says how many of the
    requested minutes are covered by the included allowance and how many are
    not, before anything is scheduled — so nobody discovers the bill
    afterwards.

    A request for TECHNICAL PRODUCT SUPPORT is accepted and recorded as
    consuming nothing: our defect is not their allowance.
    """
    org = _org(db, user)
    entitlement = support_entitlements.resolve(db, org)
    before = support_entitlements.assistance_summary(db, org, entitlement)
    rules = support_entitlements.consumption_for(req.category)

    covered = 0
    billable = 0
    if rules["counts_against_allowance"]:
        covered = min(req.minutes, before["remaining_minutes"])
        billable = req.minutes - covered
    elif rules["billable"]:
        billable = req.minutes

    entry = support_entitlements.record_assistance(
        db, org=org, category=req.category, minutes=req.minutes,
        status="requested", requested_by=user.id, topic=req.topic)

    ticket = support_tickets.create_ticket(
        db, org=org, user=user,
        subject="Scheduled assistance: %s" % req.topic[:80],
        body=req.topic, category=req.category,
        ai_summary="Request for %d minutes of scheduled assistance."
                   % req.minutes)
    entry.ticket_id = ticket.id
    db.commit()

    return {
        "request_id": entry.id,
        "ticket_number": ticket.ticket_number,
        "minutes_requested": req.minutes,
        "minutes_covered_by_plan": covered,
        "minutes_billable": billable,
        "allowance": before,
        "note": ("Technical support for a problem with the product is always "
                 "included and never uses your assistance minutes."
                 if not rules["counts_against_allowance"] and not rules["billable"]
                 else None),
    }


@router.get("/assistance")
def assistance_history(user: User = Depends(require_tenant_user),
                       db: Session = Depends(get_db)) -> Dict[str, Any]:
    from app.models.support_models import SupportAssistanceEntry
    org = _org(db, user)
    entitlement = support_entitlements.resolve(db, org)
    rows = (db.query(SupportAssistanceEntry)
            .filter(SupportAssistanceEntry.organization_id == org.id)
            .order_by(SupportAssistanceEntry.created_at.desc()).limit(50).all())
    return {
        "summary": support_entitlements.assistance_summary(db, org, entitlement),
        "entries": [
            {"id": r.id, "status": r.status, "topic": r.requested_topic,
             "minutes": r.minutes, "scheduled_for": r.scheduled_for,
             "delivered_at": r.delivered_at,
             "counts_against_allowance": bool(r.counts_against_allowance),
             "billable": bool(r.billable), "ticket_id": r.ticket_id,
             "created_at": r.created_at}
            for r in rows],
    }
