"""THE TICKET — one problem, one thread, one clock, one audience boundary.

THE BOUNDARY THIS FILE OWNS
---------------------------
`SupportTicketMessage.is_internal` decides whether a note is a note or a
message to a customer. THERE IS EXACTLY ONE READ PATH THAT FILTERS IT
(`customer_view`), and every customer-facing endpoint goes through that
function. A second place that assembles a thread is a second place that can
forget, and the consequence of forgetting is an internal note in front of a
customer — which is not a bug you get to fix afterwards.

WHAT A CUSTOMER MAY ASSERT, AND WHAT THEY MAY NOT
--------------------------------------------------
They may say what happened, what they were doing and how urgent it FEELS.
That lands in `customer_reported_severity`, beside — never instead of — the
severity the platform decided. A severity a customer can set is a severity
every customer sets to P1, and a queue where everything is critical is a queue
with no priority at all. `severity` itself is set from evidence: the AI's
recommendation, the category, and whether a real platform incident is in
progress.

DUPLICATES
----------
`create_ticket` looks for an open ticket with the same signature for the same
organization before opening a new one. It ASSOCIATES rather than refusing: the
customer still gets an acknowledgement and a number, and the support engineer
gets one investigation instead of four. Refusing to create the ticket would
just make the customer email somebody.

FIRST RESPONSE IS AN EVENT, NOT A FIELD SOMEBODY SETS
------------------------------------------------------
`add_message` is the only thing that stops the clock, and only for a
non-internal message from an agent or the AI. A ticket cannot be marked as
responded-to by changing its status, which is how first-response metrics
usually become fiction.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.models import Organization, User
from app.models.support_models import (
    Cause, Queue, Severity, SlaState, SupportTicket, SupportTicketAttachment,
    SupportTicketEvent, SupportTicketMessage, TicketCategory, TicketStatus,
)
from app.services import support_entitlements, support_sla

log = logging.getLogger(__name__)

# What a ticket opens as when nothing else is known. P3 rather than P2: a
# default that is one notch too high floods the priority queue and makes the
# notch above it meaningless.
DEFAULT_SEVERITY = Severity.P3

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENTS_PER_TICKET = 10


def ticket_number(now: Optional[datetime] = None) -> str:
    """SUP-YYYYMMDD-XXXXXX. Readable on a phone call, unique without a sequence.

    A per-day counter would need a lock or a sequence to be safe under
    concurrency; a random suffix over a 36^6 space needs neither and collides
    with probability that rounds to zero at this volume. The unique constraint
    on the column is the backstop rather than the mechanism.
    """
    now = now or datetime.utcnow()
    suffix = uuid.uuid4().hex[:6].upper()
    return "SUP-%s-%s" % (now.strftime("%Y%m%d"), suffix)


def _event(db: Session, ticket: SupportTicket, event_type: str, *,
           actor_kind: str = "system", actor_user_id: Optional[str] = None,
           from_value: Optional[str] = None, to_value: Optional[str] = None,
           detail: Optional[str] = None, customer_visible: bool = False) -> None:
    db.add(SupportTicketEvent(
        ticket_id=ticket.id, organization_id=ticket.organization_id,
        event_type=event_type, actor_kind=actor_kind, actor_user_id=actor_user_id,
        from_value=from_value, to_value=to_value, detail=detail,
        customer_visible=customer_visible))


def _severity_from_category(category: str) -> str:
    """A floor, not a decision.

    A professional-services request is a P4 by definition — it is work
    somebody wants done, not something broken — and letting it open as P3
    would put it in front of a customer whose product is actually misbehaving.
    """
    if category == TicketCategory.PROFESSIONAL_SERVICES:
        return Severity.P4
    if category == TicketCategory.CUSTOMER_ASSISTANCE:
        return Severity.P4
    return DEFAULT_SEVERITY


def resolve_severity(*, category: str, ai_recommended: Optional[str],
                     customer_reported: Optional[str],
                     platform_emergency: bool) -> str:
    """What the platform decides this is.

    ORDER OF AUTHORITY:
      1. A verified platform emergency. Our own evidence outranks everything.
      2. The AI's recommendation, when it is a valid value.
      3. The category floor.

    The customer's own view is deliberately absent from this function. It is
    recorded on the ticket and it is visible to the support engineer, but it
    does not set priority — see the module docstring.
    """
    if platform_emergency:
        return Severity.P1
    if ai_recommended in Severity.ALL:
        return ai_recommended
    return _severity_from_category(category)


def find_related_open(db: Session, *, org: Organization,
                      signature: Optional[str]) -> Optional[SupportTicket]:
    """An open ticket for this organization about the same thing, if there is one."""
    if not signature or org is None:
        return None
    return (db.query(SupportTicket)
            .filter(SupportTicket.organization_id == org.id,
                    SupportTicket.issue_signature == signature,
                    SupportTicket.status.in_(list(TicketStatus.OPEN) +
                                             [TicketStatus.WAITING_ON_CUSTOMER]))
            .order_by(SupportTicket.created_at.desc()).first())


def create_ticket(db: Session, *, org: Organization, user: Optional[User],
                  subject: str, body: str,
                  category: str = TicketCategory.TECHNICAL_PRODUCT_SUPPORT,
                  customer_reported_severity: Optional[str] = None,
                  ai_recommended_severity: Optional[str] = None,
                  ai_summary: Optional[str] = None,
                  ai_suspected_cause: Optional[str] = None,
                  ai_confidence: Optional[str] = None,
                  ai_recommendation: Optional[str] = None,
                  diagnostic_run_id: Optional[str] = None,
                  signature: Optional[str] = None,
                  conversation_id: Optional[str] = None,
                  now: Optional[datetime] = None) -> SupportTicket:
    """Open a ticket with its entitlement, queue and clock already decided.

    Everything commercial is resolved HERE and snapshotted onto the row, so a
    catalogue edit tomorrow cannot retroactively change what we promised
    today. See `SupportTicket.entitlement_json`.
    """
    now = now or datetime.utcnow()
    if category not in TicketCategory.ALL:
        category = TicketCategory.TECHNICAL_PRODUCT_SUPPORT

    entitlement = support_entitlements.resolve(db, org)
    # THE BRAND'S NAME GOES INTO THE SNAPSHOT, not into a lookup at read time.
    # See `_brand_name`: the thread must keep saying what it said.
    from app.services import support_branding
    brand = support_branding.brand_for_org(db, org)
    entitlement["brand_display_name"] = brand["display_name"]
    entitlement["assistant_name"] = brand["assistant_name"]
    entitlement["support_display_name"] = brand["support_display_name"]

    # A VERIFIED emergency, from our own evidence — never from the customer's
    # description. `matching_open_incident` only returns incidents the
    # platform correlated across organizations.
    platform_emergency = False
    incident = None
    try:
        from app.services import support_incidents
        incident = support_incidents.matching_open_incident(db, signature)
        platform_emergency = incident is not None and incident.organizations_affected >= 2
    except Exception:                                          # noqa: BLE001
        log.exception("support_tickets: incident lookup failed while opening a ticket")

    severity = resolve_severity(
        category=category, ai_recommended=ai_recommended_severity,
        customer_reported=customer_reported_severity,
        platform_emergency=platform_emergency)
    queue = support_entitlements.queue_for(entitlement, severity,
                                           platform_emergency=platform_emergency)
    due = support_sla.compute_first_response_due(
        db, platform_id=getattr(org, "platform_id", None), entitlement=entitlement,
        severity=severity, queue=queue, started_at=now)

    ticket = SupportTicket(
        ticket_number=ticket_number(now),
        organization_id=org.id,
        platform_id=getattr(org, "platform_id", None),
        submitted_by=getattr(user, "id", None),
        subject=(subject or "Support request").strip()[:300],
        category=category,
        severity=severity,
        customer_reported_severity=(customer_reported_severity
                                    if customer_reported_severity in Severity.ALL
                                    else None),
        status=TicketStatus.AI_TRIAGE if ai_summary else TicketStatus.NEW,
        queue=queue,
        entitlement_json=json.dumps(entitlement, default=str),
        first_response_due_at=due,
        sla_state=SlaState.WITHIN if due else SlaState.NOT_APPLICABLE,
        sla_clock_started_at=now,
        ai_summary=ai_summary,
        ai_suspected_cause=ai_suspected_cause,
        ai_confidence=ai_confidence,
        ai_recommendation=ai_recommendation,
        diagnostic_run_id=diagnostic_run_id,
        issue_signature=signature,
        incident_id=getattr(incident, "id", None),
        created_at=now,
    )

    related = find_related_open(db, org=org, signature=signature)
    if related is not None:
        # ASSOCIATE, DO NOT REFUSE. The customer gets their acknowledgement and
        # their number; the engineer gets one investigation.
        ticket.related_ticket_id = related.id

    db.add(ticket)
    db.flush()

    add_message(db, ticket, author_kind="customer", user=user, body=body,
                is_internal=False, now=now, stop_clock=False)

    _event(db, ticket, "created", actor_kind="customer",
           actor_user_id=getattr(user, "id", None),
           to_value=ticket.status, customer_visible=True,
           detail="Request received.")
    if related is not None:
        _event(db, ticket, "related_ticket_linked", to_value=related.ticket_number,
               detail="Linked to an existing open request about the same problem.",
               customer_visible=True)
    if incident is not None:
        _event(db, ticket, "incident_linked", to_value=incident.incident_number,
               detail="Matched a known platform incident.")

    try:
        from app.services import support_incidents
        support_incidents.observe(db, signature=signature,
                                  service=_service_for(signature),
                                  cause=ai_suspected_cause, org=org,
                                  platform_id=getattr(org, "platform_id", None),
                                  now=now)
    except Exception:                                          # noqa: BLE001
        log.exception("support_tickets: could not record the issue signature")

    if conversation_id:
        _link_conversation(db, conversation_id, ticket)

    notify_new_ticket(db, ticket, org=org, user=user)
    db.flush()
    return ticket


def _service_for(signature: Optional[str]) -> Optional[str]:
    if not signature or "." not in signature:
        return None
    return signature.split(".", 1)[0]


def _link_conversation(db: Session, conversation_id: str,
                       ticket: SupportTicket) -> None:
    from app.models.support_models import SupportConversation
    convo = (db.query(SupportConversation)
             .filter(SupportConversation.id == conversation_id).first())
    if convo is None:
        return
    # TENANT CHECK ON A LINK, because a conversation id is a value that
    # arrives in a request body. A conversation belonging to another customer
    # must not be able to attach itself to this ticket.
    if convo.organization_id != ticket.organization_id:
        log.warning("support_tickets: refused to link conversation %s from a "
                    "different organization to ticket %s", conversation_id,
                    ticket.id)
        return
    convo.status = "escalated"
    convo.escalated_ticket_id = ticket.id
    db.flush()


def add_message(db: Session, ticket: SupportTicket, *, author_kind: str,
                body: str, user: Optional[User] = None,
                is_internal: bool = False, author_display: Optional[str] = None,
                now: Optional[datetime] = None,
                stop_clock: bool = True) -> SupportTicketMessage:
    """Append one turn, and let it move the clock if that is what it is.

    THE CLOCK STOPS HERE OR NOWHERE. A non-internal message from an agent or
    the AI is the first response; nothing else is, including a status change,
    an assignment, or somebody marking the ticket in progress.
    """
    now = now or datetime.utcnow()
    message = SupportTicketMessage(
        ticket_id=ticket.id, organization_id=ticket.organization_id,
        author_kind=author_kind, author_user_id=getattr(user, "id", None),
        author_display=author_display or _display_for(author_kind, user, ticket),
        body=body or "", is_internal=bool(is_internal), created_at=now)
    db.add(message)

    if author_kind == "customer":
        ticket.last_customer_reply_at = now
        # A CUSTOMER REPLY IS THE BALL COMING BACK. A ticket sitting in
        # Waiting-on-Customer whose customer has replied is ours again, and
        # the clock restarts — otherwise the queue quietly parks work.
        if ticket.status == TicketStatus.WAITING_ON_CUSTOMER:
            previous = ticket.status
            ticket.status = TicketStatus.IN_PROGRESS
            support_sla.resume(db, ticket, now=now)
            _event(db, ticket, "customer_replied", actor_kind="customer",
                   actor_user_id=getattr(user, "id", None),
                   from_value=previous, to_value=ticket.status,
                   detail="The response clock restarted.", customer_visible=True)
    elif author_kind in ("agent", "ai") and not is_internal:
        ticket.last_agent_reply_at = now
        if stop_clock and ticket.first_response_at is None:
            ticket.first_response_at = now
            message.is_first_response = True
            support_sla.refresh(db, ticket, now=now)
            _event(db, ticket, "first_response", actor_kind=author_kind,
                   actor_user_id=getattr(user, "id", None),
                   to_value=ticket.sla_state, customer_visible=True,
                   detail="First response sent.")

    ticket.updated_at = now
    db.flush()
    return message


def _display_for(author_kind: str, user: Optional[User],
                 ticket: SupportTicket) -> str:
    """WHOSE NAME A CUSTOMER SEES.

    The AI wears the BRAND's name, never AdvisorFlow's — the brand owns the
    face. An agent shows as the brand's support team rather than as a named
    individual, so a customer's expectation attaches to the company rather
    than to whoever happened to pick it up.
    """
    if author_kind == "customer":
        return getattr(user, "full_name", None) or "You"
    snapshot = _entitlement_of(ticket)
    brand = _brand_name(ticket)
    if author_kind == "ai":
        return snapshot.get("assistant_name") or ("Ask %s" % brand)
    if author_kind == "agent":
        return snapshot.get("support_display_name") or ("%s Support" % brand)
    return brand


def _brand_name(ticket: SupportTicket) -> str:
    """The brand's own display name, taken from the ticket's snapshot.

    Read from `entitlement_json` rather than looked up live, deliberately: a
    transcript read a year later should say what the customer was actually
    talking to, and a brand that renames itself must not rewrite the history
    of every conversation it ever had. `create_ticket` writes the name into
    the snapshot at the moment the ticket opens.
    """
    try:
        entitlement = json.loads(ticket.entitlement_json or "{}")
    except (TypeError, ValueError):
        entitlement = {}
    return entitlement.get("brand_display_name") or "Support"


# ══════════════════════════════════════════════════════════════════════════
# LIFECYCLE
# ══════════════════════════════════════════════════════════════════════════

def assign(db: Session, ticket: SupportTicket, *, agent: Optional[User],
           actor: User, team: Optional[str] = None) -> SupportTicket:
    before = ticket.assigned_to
    ticket.assigned_to = getattr(agent, "id", None)
    ticket.assigned_team = team
    if ticket.status in (TicketStatus.AI_TRIAGE, TicketStatus.NEW):
        ticket.status = TicketStatus.ASSIGNED
    _event(db, ticket, "assigned", actor_kind="agent", actor_user_id=actor.id,
           from_value=before, to_value=ticket.assigned_to,
           detail=team or None)
    db.flush()
    return ticket


def reclassify(db: Session, ticket: SupportTicket, *, severity: str, actor: User,
               reason: Optional[str] = None,
               now: Optional[datetime] = None) -> SupportTicket:
    """An authorized person changes the severity, and the clock follows.

    RECOMPUTES THE TARGET. A ticket promoted to critical whose due date still
    reflects the old severity would look compliant while being urgent, which
    is the worst of both. The audit trail keeps the old value and the reason,
    so a reclassification is always answerable for.
    """
    if severity not in Severity.ALL:
        raise ValueError("Unknown severity %r" % severity)
    now = now or datetime.utcnow()
    before = ticket.severity
    if before == severity:
        return ticket

    ticket.severity = severity
    entitlement = _entitlement_of(ticket)
    ticket.queue = support_entitlements.queue_for(
        entitlement, severity,
        platform_emergency=(ticket.queue == Queue.EMERGENCY))
    if ticket.first_response_at is None:
        started = ticket.sla_clock_started_at or ticket.created_at or now
        ticket.first_response_due_at = support_sla.compute_first_response_due(
            db, platform_id=ticket.platform_id, entitlement=entitlement,
            severity=severity, queue=ticket.queue, started_at=started)
    support_sla.refresh(db, ticket, now=now)

    _event(db, ticket, "severity_changed", actor_kind="agent",
           actor_user_id=actor.id, from_value=before, to_value=severity,
           detail=reason, customer_visible=True)

    # ALSO the security ledger. A severity change moves a commercial promise,
    # so it belongs where an administrator audits promises, not only on the
    # ticket's own timeline.
    try:
        from app.routers.audit_log_router import log_action
        log_action(db, ticket.organization_id, actor.id,
                   action="support.ticket_severity_changed",
                   target_type="support_ticket", target_id=ticket.id,
                   platform_id=ticket.platform_id,
                   before={"severity": before}, after={"severity": severity},
                   note=reason, commit=False)
    except Exception:                                          # noqa: BLE001
        log.exception("support_tickets: audit write failed for reclassification")

    db.flush()
    return ticket


def _entitlement_of(ticket: SupportTicket) -> Dict[str, Any]:
    try:
        parsed = json.loads(ticket.entitlement_json or "{}")
    except (TypeError, ValueError):
        parsed = {}
    if not isinstance(parsed, dict) or "first_response_minutes" not in parsed:
        return {"queue": ticket.queue, "first_response_minutes": {},
                "included_assistance_minutes": 0, "features": [],
                "emergency_override": True}
    return parsed


def set_status(db: Session, ticket: SupportTicket, status: str, *,
               actor: Optional[User] = None, actor_kind: str = "agent",
               detail: Optional[str] = None,
               now: Optional[datetime] = None) -> SupportTicket:
    """Move a ticket, and move its clock the way that status means.

    WAITING_ON_CUSTOMER pauses; leaving it resumes. Both are audited by
    `support_sla`, which refuses to pause a clock that has already stopped —
    so a ticket that has had its first response can be parked without the
    pause bookkeeping pretending to matter.
    """
    if status not in TicketStatus.ALL:
        raise ValueError("Unknown ticket status %r" % status)
    now = now or datetime.utcnow()
    before = ticket.status
    if before == status:
        return ticket

    ticket.status = status
    if status == TicketStatus.WAITING_ON_CUSTOMER:
        if support_sla.pause(db, ticket, now=now):
            _event(db, ticket, "sla_paused", actor_kind=actor_kind,
                   actor_user_id=getattr(actor, "id", None),
                   detail="Waiting on the customer.", customer_visible=True)
    elif before == TicketStatus.WAITING_ON_CUSTOMER:
        if support_sla.resume(db, ticket, now=now):
            _event(db, ticket, "sla_resumed", actor_kind=actor_kind,
                   actor_user_id=getattr(actor, "id", None),
                   detail="The response clock restarted.", customer_visible=True)

    if status == TicketStatus.RESOLVED:
        ticket.resolved_at = now
    if status == TicketStatus.CLOSED:
        ticket.closed_at = now
        ticket.resolved_at = ticket.resolved_at or now

    support_sla.refresh(db, ticket, now=now)
    _event(db, ticket, "status_changed", actor_kind=actor_kind,
           actor_user_id=getattr(actor, "id", None), from_value=before,
           to_value=status, detail=detail, customer_visible=True)
    ticket.updated_at = now
    db.flush()
    return ticket


def resolve_ticket(db: Session, ticket: SupportTicket, *, resolution: str,
                   resolution_code: Optional[str] = None,
                   actor: Optional[User] = None, actor_kind: str = "agent",
                   now: Optional[datetime] = None) -> SupportTicket:
    ticket.resolution = resolution
    ticket.resolution_code = resolution_code
    set_status(db, ticket, TicketStatus.RESOLVED, actor=actor,
               actor_kind=actor_kind, detail=resolution, now=now)
    notify_ticket_update(db, ticket, headline="Your request has been resolved.",
                         body=resolution)
    return ticket


def reopen(db: Session, ticket: SupportTicket, *, actor: Optional[User],
           reason: Optional[str] = None,
           now: Optional[datetime] = None) -> SupportTicket:
    """Reopening restarts the clock from now, and says so.

    NOT the original due date: the original promise was kept or broken
    already, and reusing it would either report an instant breach or a
    meaningless compliance. A reopened ticket is a new commitment.
    """
    now = now or datetime.utcnow()
    ticket.first_response_at = None
    ticket.resolved_at = None
    ticket.closed_at = None
    ticket.sla_paused_at = None
    ticket.sla_clock_started_at = now
    entitlement = _entitlement_of(ticket)
    ticket.first_response_due_at = support_sla.compute_first_response_due(
        db, platform_id=ticket.platform_id, entitlement=entitlement,
        severity=ticket.severity, queue=ticket.queue, started_at=now)
    set_status(db, ticket, TicketStatus.NEW, actor=actor,
               actor_kind="customer" if actor is not None else "system",
               detail=reason, now=now)
    _event(db, ticket, "reopened", actor_kind="customer",
           actor_user_id=getattr(actor, "id", None), detail=reason,
           customer_visible=True)
    return ticket


def add_attachment(db: Session, ticket: SupportTicket, *, filename: str,
                   content_type: str, data: bytes,
                   user: Optional[User] = None,
                   message_id: Optional[str] = None) -> SupportTicketAttachment:
    """A screenshot or a log, with limits that are refusals rather than truncations.

    An oversized upload is REFUSED, not trimmed: half a log file is evidence
    that misleads, and a customer who thinks they sent us the file has stopped
    looking for another way to.
    """
    from fastapi import HTTPException
    if not data:
        raise HTTPException(status_code=400, detail="That file is empty.")
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(
            status_code=413,
            detail="That file is larger than %d MB. Please attach a smaller "
                   "file or a screenshot of the relevant part."
                   % (MAX_ATTACHMENT_BYTES // (1024 * 1024)))
    existing = (db.query(SupportTicketAttachment)
                .filter(SupportTicketAttachment.ticket_id == ticket.id).count())
    if existing >= MAX_ATTACHMENTS_PER_TICKET:
        raise HTTPException(
            status_code=400,
            detail="This request already has %d attachments."
                   % MAX_ATTACHMENTS_PER_TICKET)

    attachment = SupportTicketAttachment(
        ticket_id=ticket.id, organization_id=ticket.organization_id,
        message_id=message_id, filename=(filename or "attachment")[:200],
        content_type=content_type or "application/octet-stream",
        file_size=len(data), file_data=data,
        uploaded_by=getattr(user, "id", None))
    db.add(attachment)
    _event(db, ticket, "attachment_added", actor_kind="customer",
           actor_user_id=getattr(user, "id", None), to_value=attachment.filename,
           customer_visible=True)
    db.flush()
    return attachment


# ══════════════════════════════════════════════════════════════════════════
# NOTIFICATIONS — reuse the platform's sender, never a second one
# ══════════════════════════════════════════════════════════════════════════

def _notify(db: Session, ticket: SupportTicket, *, to_email: Optional[str],
            subject: str, body_html: str) -> bool:
    """Send one support email through the EXISTING provider, AS THIS TICKET'S
    BRAND.

    `email_service.send_email_via_provider` is what every other outbound
    email in this platform goes through. A support mailer with its own SMTP
    settings would be a second set of credentials, a second deliverability
    reputation and a second thing to configure per brand — all to send the
    same kind of message.

    ══════════════════════════════════════════════════════════════════════
    THE WHITE-LABEL LEAK THIS LINE USED TO BE
    ══════════════════════════════════════════════════════════════════════

    This called `send_email_via_provider(to_email, subject, body_html)` with
    NO `org=` argument. That function reads its from-address off whatever it
    is handed, and with nothing handed to it falls through to the module-level
    `FROM_EMAIL` — one value for a deployment serving three brands, defaulting
    to `noreply@bookaboost.com`. So a ticket raised inside EvoSys Pro produced
    an email that announced itself as BookaBoost. The BODY was already correct
    (it reads the brand's name out of the ticket's own snapshot); it was the
    ENVELOPE that belonged to somebody else, which is the half a customer's
    mail client shows them first.

    The fix is not a special case here. It is the same shape as every other
    identity in this codebase: resolve the brand from authoritative context —
    THIS ticket's platform — hand the resolved identity to the sender, and let
    an unresolved brand REFUSE rather than be filled in by a default.

    NEVER FAILS THE CALLER. A ticket that exists and was not emailed about is
    recoverable; a ticket that was refused because a mail server was down is
    a customer with nowhere to go. A brand that cannot be resolved is logged
    as an error and sends nothing, which is visible on the God console and is
    strictly better than reaching the customer under the wrong name.
    """
    if not to_email:
        return False
    try:
        from app.services import support_branding
        from app.services.email_service import send_email_via_provider

        identity = support_branding.sending_identity_for_ticket(db, ticket)
        if not identity.from_email:
            log.error(
                "support_tickets: refusing to email about ticket %s — no "
                "support sender is configured for its brand (platform=%s). "
                "Set the platform's support email; nothing was sent under "
                "another brand's address.",
                ticket.ticket_number, identity.platform_id)
            return False

        result = send_email_via_provider(to_email, subject, body_html,
                                         org=identity)
        if not (result and result.get("success")):
            log.warning("support_tickets: notification for ticket %s was not "
                        "sent: %s", ticket.ticket_number,
                        (result or {}).get("error"))
        return bool(result and result.get("success"))
    except Exception:                                          # noqa: BLE001
        log.exception("support_tickets: notification email failed for ticket %s",
                      ticket.ticket_number)
        return False


def _email_shell(ticket: SupportTicket, headline: str, body: str,
                 *, footer: Optional[str] = None,
                 brand: Optional[Dict[str, Any]] = None) -> str:
    """The customer-facing support email, wearing ONE brand's face.

    `brand` is the resolved block from `support_branding`; when it is absent
    the name still comes from the ticket's own snapshot, which is what a
    transcript read a year later should say. Everything visual — the accent
    rule, the logo, the link home, the footer address — is the brand's and is
    OMITTED where the brand has not configured it. A missing logo renders as
    no logo; it never falls back to another brand's, and it never falls back
    to AdvisorFlow's.
    """
    name = (brand or {}).get("display_name") or _brand_name(ticket)
    accent = (brand or {}).get("accent_color") or "#1c2430"
    logo = (brand or {}).get("logo_url")
    app_url = (brand or {}).get("app_base_url")
    support_email = (brand or {}).get("support_email")

    head = ""
    if logo:
        head = ('<img src="%s" alt="%s" style="max-height:34px;display:block;'
                'margin:0 0 12px">' % (logo, name))

    default_footer = "Reply to this request in your workspace under Help &amp; Support."
    if app_url:
        default_footer = (
            'Reply to this request in your workspace under Help &amp; Support '
            '— <a href="%s/help" style="color:%s">open %s</a>.'
            % (str(app_url).rstrip("/"), accent, name))

    tail = footer or default_footer
    if support_email:
        tail += (' <span style="color:#9aa7b5">You can also reply to this '
                 'email; it reaches %s.</span>' % support_email)

    return (
        '<div style="font-family:Arial,Helvetica,sans-serif;color:#1c2430">'
        '%s'
        '<p style="margin:0 0 4px;font-size:12px;color:#6b7a8c">%s &middot; %s</p>'
        '<div style="height:3px;width:44px;background:%s;margin:0 0 12px"></div>'
        '<h2 style="margin:0 0 12px;font-size:18px">%s</h2>'
        '<p style="margin:0 0 12px;white-space:pre-wrap">%s</p>'
        '<p style="margin:16px 0 0;font-size:12px;color:#6b7a8c">%s</p>'
        '</div>'
    ) % (head, name, ticket.ticket_number, accent, headline,
         (body or "").strip(), tail)


def notify_new_ticket(db: Session, ticket: SupportTicket, *,
                      org: Organization, user: Optional[User]) -> None:
    """Acknowledge to the customer, and tell the brand a request arrived.

    THE ACKNOWLEDGEMENT CARRIES THE TARGET WE ACTUALLY PROMISED, computed
    from this ticket's own snapshot. A generic "we'll be in touch soon" is
    what makes a support product feel like a void; a specific target is a
    commitment the SLA engine is already measuring us against.
    """
    entitlement = _entitlement_of(ticket)
    from app.services import support_branding
    # THE TICKET'S OWN BRAND, not the org's. They agree in every normal case;
    # where they do not, the ticket is the record of which product the
    # customer was actually inside when they asked for help, and the
    # organization row can have been moved between brands since.
    brand = support_branding.brand_for_ticket(db, ticket)

    target = None
    if ticket.first_response_due_at is not None:
        cfg = support_sla.hours_for(db, ticket.platform_id)
        minutes = entitlement.get("first_response_minutes", {}).get(ticket.severity)
        target = support_sla._humanize(minutes, cfg) if minutes else None

    promise = ("We aim to respond within %s." % target if target
               else "We'll come back to you on this.")
    customer_email = getattr(user, "notification_email", None) or getattr(user, "email", None)
    _notify(db, ticket, to_email=customer_email,
            subject="[%s] We've got your request" % ticket.ticket_number,
            body_html=_email_shell(
                ticket, "We've received your request",
                "%s\n\n%s" % (ticket.subject, promise), brand=brand))

    # The brand's own support inbox. Not a hardcoded address anywhere: an
    # unconfigured brand simply gets no internal email, which is visible on
    # the God console rather than silently landing in somebody's personal
    # mailbox.
    if brand.get("support_email"):
        _notify(db, ticket, to_email=brand["support_email"],
                subject="[%s] %s — %s" % (ticket.ticket_number,
                                          Severity.LABELS[ticket.severity],
                                          ticket.subject),
                body_html=_email_shell(
                    ticket, "New support request",
                    "%s\n\nOrganization: %s\nQueue: %s\nCategory: %s"
                    % (ticket.subject, getattr(org, "name", "—"),
                       Queue.LABELS.get(ticket.queue, ticket.queue),
                       TicketCategory.LABELS.get(ticket.category, ticket.category)),
                    footer="Open this in God Mode → Support.", brand=brand))


def notify_ticket_update(db: Session, ticket: SupportTicket, *, headline: str,
                         body: str) -> None:
    """Tell the customer something changed. Customer-safe text only.

    Callers pass the words. Nothing here reads an internal note, and there is
    no code path from `is_internal=True` content into an email — the two are
    never in the same function.
    """
    submitter = None
    if ticket.submitted_by:
        submitter = db.query(User).filter(User.id == ticket.submitted_by).first()
    to_email = (getattr(submitter, "notification_email", None)
                or getattr(submitter, "email", None))
    from app.services import support_branding
    brand = support_branding.brand_for_ticket(db, ticket)
    _notify(db, ticket, to_email=to_email,
            subject="[%s] %s" % (ticket.ticket_number, headline),
            body_html=_email_shell(ticket, headline, body, brand=brand))


# ══════════════════════════════════════════════════════════════════════════
# VIEWS — the audience boundary, in exactly two functions
# ══════════════════════════════════════════════════════════════════════════

def customer_view(db: Session, ticket: SupportTicket) -> Dict[str, Any]:
    """EVERYTHING A CUSTOMER MAY SEE ABOUT THEIR OWN TICKET, AND NOTHING ELSE.

    THE ONE FILTER. `is_internal == False` is applied here and only here, and
    every customer-facing endpoint returns this function's output. A route
    that assembled its own thread would be a second filter to maintain, and
    the failure mode of forgetting is an internal note in front of a customer.

    Also absent by construction: the diagnostic run's technical view, the
    incident's cross-tenant aggregates, the AI's raw confidence in its own
    guess, and any other organization's existence.
    """
    messages = (db.query(SupportTicketMessage)
                .filter(SupportTicketMessage.ticket_id == ticket.id,
                        SupportTicketMessage.is_internal.is_(False))
                .order_by(SupportTicketMessage.created_at.asc()).all())
    events = (db.query(SupportTicketEvent)
              .filter(SupportTicketEvent.ticket_id == ticket.id,
                      SupportTicketEvent.customer_visible.is_(True))
              .order_by(SupportTicketEvent.created_at.asc()).all())
    attachments = (db.query(SupportTicketAttachment)
                   .filter(SupportTicketAttachment.ticket_id == ticket.id).all())

    sla = support_sla.describe(support_sla.evaluate(db, ticket))
    entitlement = _entitlement_of(ticket)

    return {
        "id": ticket.id,
        "ticket_number": ticket.ticket_number,
        "subject": ticket.subject,
        "category": ticket.category,
        "category_label": TicketCategory.LABELS.get(ticket.category, ticket.category),
        "severity": ticket.severity,
        "severity_label": Severity.LABELS.get(ticket.severity, ticket.severity),
        "severity_meaning": Severity.MEANINGS.get(ticket.severity),
        "status": ticket.status,
        "status_label": TicketStatus.LABELS.get(ticket.status, ticket.status),
        "queue_label": Queue.LABELS.get(ticket.queue, ticket.queue),
        "support_plan": entitlement.get("display_name"),
        "created_at": ticket.created_at,
        "updated_at": ticket.updated_at,
        "resolved_at": ticket.resolved_at,
        "resolution": ticket.resolution,
        "first_response_due_at": ticket.first_response_due_at,
        "first_response_at": ticket.first_response_at,
        "sla": {"state": sla["state"], "label": sla["label"],
                "reason": sla.get("reason"), "due_at": sla.get("due_at")},
        "messages": [
            {"id": m.id, "author": m.author_display, "author_kind": m.author_kind,
             "body": m.body, "created_at": m.created_at,
             "is_first_response": bool(m.is_first_response)}
            for m in messages],
        "timeline": [
            {"event": e.event_type, "detail": e.detail, "at": e.created_at,
             "from": e.from_value, "to": e.to_value}
            for e in events],
        "attachments": [
            {"id": a.id, "filename": a.filename, "size": a.file_size,
             "content_type": a.content_type, "uploaded_at": a.uploaded_at}
            for a in attachments],
        "can_reply": ticket.status != TicketStatus.CLOSED,
        "can_reopen": ticket.status in (TicketStatus.RESOLVED, TicketStatus.CLOSED),
    }


def agent_view(db: Session, ticket: SupportTicket) -> Dict[str, Any]:
    """The customer view plus everything an operator needs, as a superset.

    A SUPERSET BY CONSTRUCTION so the two views can never disagree about the
    facts they share. What is added is internal notes, the technical
    diagnostic evidence, the AI's own reasoning, the remediation history and
    the incident link — none of which has a path back into `customer_view`,
    because that function does not read any of it.
    """
    out = customer_view(db, ticket)
    internal = (db.query(SupportTicketMessage)
                .filter(SupportTicketMessage.ticket_id == ticket.id,
                        SupportTicketMessage.is_internal.is_(True))
                .order_by(SupportTicketMessage.created_at.asc()).all())
    events = (db.query(SupportTicketEvent)
              .filter(SupportTicketEvent.ticket_id == ticket.id)
              .order_by(SupportTicketEvent.created_at.asc()).all())

    org = db.query(Organization).filter(
        Organization.id == ticket.organization_id).first()

    diagnostic = None
    if ticket.diagnostic_run_id:
        from app.models.support_models import SupportDiagnosticRun
        row = (db.query(SupportDiagnosticRun)
               .filter(SupportDiagnosticRun.id == ticket.diagnostic_run_id).first())
        if row is not None:
            try:
                diagnostic = json.loads(row.technical_summary_json or "{}")
            except (TypeError, ValueError):
                diagnostic = {"unparseable": True}

    from app.models.support_models import SupportFixRun
    from app.services import support_remediation
    fixes = (db.query(SupportFixRun)
             .filter(SupportFixRun.ticket_id == ticket.id)
             .order_by(SupportFixRun.created_at.desc()).all())

    incident = None
    if ticket.incident_id:
        from app.models.support_models import SupportIncident
        row = (db.query(SupportIncident)
               .filter(SupportIncident.id == ticket.incident_id).first())
        if row is not None:
            incident = {"id": row.id, "incident_number": row.incident_number,
                        "title": row.title, "status": row.status,
                        "organizations_affected": row.organizations_affected}

    out.update({
        "organization_id": ticket.organization_id,
        "organization_name": getattr(org, "name", None),
        "platform_id": ticket.platform_id,
        "submitted_by": ticket.submitted_by,
        "assigned_to": ticket.assigned_to,
        "assigned_team": ticket.assigned_team,
        "customer_reported_severity": ticket.customer_reported_severity,
        "entitlement": _entitlement_of(ticket),
        "ai_summary": ticket.ai_summary,
        "ai_suspected_cause": ticket.ai_suspected_cause,
        "ai_suspected_cause_label": Cause.LABELS.get(ticket.ai_suspected_cause or "",
                                                     ticket.ai_suspected_cause),
        "ai_confidence": ticket.ai_confidence,
        "ai_recommendation": ticket.ai_recommendation,
        "issue_signature": ticket.issue_signature,
        "incident": incident,
        "related_ticket_id": ticket.related_ticket_id,
        "diagnostic": diagnostic,
        "internal_notes": [
            {"id": m.id, "author": m.author_display, "body": m.body,
             "created_at": m.created_at} for m in internal],
        "full_timeline": [
            {"event": e.event_type, "detail": e.detail, "at": e.created_at,
             "actor_kind": e.actor_kind, "from": e.from_value, "to": e.to_value,
             "customer_visible": bool(e.customer_visible)} for e in events],
        "fix_runs": [support_remediation.run_summary(f, technical=True)
                     for f in fixes],
        "assistance": {
            "included_minutes_used": ticket.assistance_minutes_included,
            "billable_minutes": ticket.assistance_minutes_billable,
        },
    })
    return out


def list_summary(db: Session, ticket: SupportTicket) -> Dict[str, Any]:
    """One row of a list. Cheap: no messages, no events, no evidence."""
    sla = support_sla.describe(support_sla.evaluate(db, ticket))
    return {
        "id": ticket.id,
        "ticket_number": ticket.ticket_number,
        "subject": ticket.subject,
        "status": ticket.status,
        "status_label": TicketStatus.LABELS.get(ticket.status, ticket.status),
        "severity": ticket.severity,
        "severity_label": Severity.LABELS.get(ticket.severity, ticket.severity),
        "category": ticket.category,
        "queue": ticket.queue,
        "queue_label": Queue.LABELS.get(ticket.queue, ticket.queue),
        "organization_id": ticket.organization_id,
        "platform_id": ticket.platform_id,
        "assigned_to": ticket.assigned_to,
        "created_at": ticket.created_at,
        "updated_at": ticket.updated_at,
        "first_response_due_at": ticket.first_response_due_at,
        "first_response_at": ticket.first_response_at,
        "sla_state": sla["state"],
        "sla_label": sla["label"],
        "incident_id": ticket.incident_id,
        "issue_signature": ticket.issue_signature,
    }


def list_for_org(db: Session, org: Organization, *, status: Optional[str] = None,
                 limit: int = 50, offset: int = 0) -> List[SupportTicket]:
    """This organization's tickets. The filter is not optional and not a parameter.

    `organization_id ==` is applied here rather than by the router, so a route
    cannot forget it. There is no variant of this function that takes an
    organization id from a request.
    """
    q = db.query(SupportTicket).filter(SupportTicket.organization_id == org.id)
    if status == "open":
        q = q.filter(SupportTicket.status.in_(
            list(TicketStatus.OPEN) + [TicketStatus.WAITING_ON_CUSTOMER]))
    elif status == "closed":
        q = q.filter(SupportTicket.status.in_(list(TicketStatus.TERMINAL)))
    elif status in TicketStatus.ALL:
        q = q.filter(SupportTicket.status == status)
    return (q.order_by(SupportTicket.created_at.desc())
            .offset(max(offset, 0)).limit(min(max(limit, 1), 200)).all())


def refresh_open_sla_states(db: Session, *, platform_ids: Optional[List[str]] = None,
                            limit: int = 500,
                            now: Optional[datetime] = None) -> Dict[str, int]:
    """Recompute the cached SLA state for open tickets.

    The cache is what a queue filters on, so a ticket that quietly crossed its
    target overnight would not appear in an "at risk" filter until somebody
    opened it. This is the sweep that stops the board being stale, and it is
    called by the daily brief rather than on every page load.
    """
    now = now or datetime.utcnow()
    q = (db.query(SupportTicket)
         .filter(SupportTicket.status.in_(list(TicketStatus.OPEN))))
    if platform_ids:
        q = q.filter(SupportTicket.platform_id.in_(platform_ids))
    changed = 0
    counts: Dict[str, int] = {}
    for ticket in q.limit(limit).all():
        before = ticket.sla_state
        state = support_sla.refresh(db, ticket, now=now)["state"]
        counts[state] = counts.get(state, 0) + 1
        if before != state:
            changed += 1
    db.flush()
    counts["changed"] = changed
    return counts
