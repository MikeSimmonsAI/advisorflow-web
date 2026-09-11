"""INBOUND — WHOSE MESSAGE IS THIS, AND WHO IS ALLOWED TO SEE IT.

THE MOST DANGEROUS OBJECT IN A MULTI-TENANT COMMUNICATION SYSTEM IS AN
INBOUND MESSAGE NOBODY CAN PLACE. Every other failure here is an
inconvenience; this one is a private reply from one customer's family
appearing in another customer's inbox. So the rule is absolute:

    IF THE TENANT CANNOT BE ESTABLISHED FROM SERVER-SIDE FACTS, THE MESSAGE
    IS NOT ROUTED. It is recorded as unrouted, with a reason, and surfaced to
    an operator. It is never matched by best effort, never matched by a
    model's opinion of who it sounds like, and never dropped silently.

WHAT COUNTS AS A SERVER-SIDE FACT. The number or address the message arrived
ON — which this platform assigned — resolves the tenant. The number or
address it came FROM resolves the contact, but only inside that tenant. A
model's reading of the body is never part of identity resolution; it may
classify what was said once we already know who said it.

REDELIVERY IS NORMAL, NOT EXCEPTIONAL. Twilio retries. Pollers re-read. The
existing inbound SMS webhook has no idempotency at all — a redelivered POST
creates a second `Reply`. `(provider, provider_event_id)` is unique here, so
the second delivery of the same event is a no-op regardless of what the rest
of the pipeline does with it.

OUT OF ORDER IS ALSO NORMAL. A "delivered" callback can arrive after the
conversation has been stopped by a reply that came in first. That is not an
illegal transition, it is ordinary network reality, and it is handled with
`strict=False` rather than by raising.
"""

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.models.ai_operations_models import (AICommunication,
                                             AIConversationThread,
                                             AIInboundEvent)
from app.services.ai_operations import audit, comm_state, continuity
from app.services.ai_operations import constants as C
from app.services.ai_operations import contracts, idempotency

_log = logging.getLogger(__name__)


# ── the words that end a conversation, whatever else they are wrapped in ────
#
# Checked here as well as by the platform's own classifier, because an
# opt-out that depends on a language model being available is an opt-out that
# fails when the model does.
_HARD_STOP_WORDS = frozenset({
    "stop", "stopall", "unsubscribe", "cancel", "end", "quit", "optout",
    "opt-out", "remove me", "do not contact", "dont contact",
})


def looks_like_opt_out(body: str) -> bool:
    text = " ".join((body or "").lower().split())
    if not text:
        return False
    if text in _HARD_STOP_WORDS:
        return True
    stripped = text.strip(".!? ")
    if stripped in _HARD_STOP_WORDS:
        return True
    return any(phrase in text for phrase in
               ("unsubscribe", "stop texting", "stop contacting",
                "take me off", "remove me from", "do not contact me"))


def _digits(value: Optional[str]) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit())


def resolve_tenant(db: Session, *, channel: str, to_address: str
                   ) -> Optional[str]:
    """Which organization owns the address this arrived on. None = unknown.

    Mirrors the resolution ladder the existing SMS webhook uses — an
    advisor's own number first, then the organization's shared number —
    because two ladders would eventually disagree about who owns a number.
    """
    if channel == C.CHANNEL_SMS or channel == C.CHANNEL_VOICE:
        digits = _digits(to_address)
        if not digits:
            return None
        candidates = {digits, "+" + digits, to_address or ""}
        try:
            from app.models.models import Organization, User
            advisor = (db.query(User)
                       .filter(User.twilio_phone_number.in_(list(candidates)))
                       .first())
            if advisor is not None and advisor.organization_id:
                return advisor.organization_id
            org = (db.query(Organization)
                   .filter(Organization.org_twilio_phone_number
                           .in_(list(candidates)))
                   .first())
            if org is not None:
                return org.id
        except Exception as exc:                             # noqa: BLE001
            _log.warning("ai_operations: tenant resolution failed (%s)", exc)
        return None

    if channel == C.CHANNEL_EMAIL:
        address = (to_address or "").strip().lower()
        if not address:
            return None
        try:
            from app.models.models import Organization
            org = (db.query(Organization)
                   .filter(Organization.from_email.isnot(None))
                   .filter(Organization.from_email.ilike(address))
                   .first())
            if org is not None:
                return org.id
        except Exception as exc:                             # noqa: BLE001
            _log.warning("ai_operations: email tenant resolution failed (%s)",
                         exc)
        return None
    return None


def resolve_contact(db: Session, *, organization_id: str, channel: str,
                    from_address: str):
    """Which contact, INSIDE the tenant we already established.

    The tenant filter is in the query. An inbound number that matches leads
    in two organizations must resolve to the one that owns the receiving
    number, not to whichever row the database returned first.
    """
    try:
        from app.models.models import Lead
        q = db.query(Lead).filter(Lead.organization_id == organization_id)
        if channel == C.CHANNEL_EMAIL:
            address = (from_address or "").strip()
            if not address:
                return None
            return q.filter(Lead.email.ilike(address)).order_by(
                Lead.updated_at.desc()).first()
        digits = _digits(from_address)
        if not digits:
            return None
        return (q.filter(Lead.phone.in_([digits, "+" + digits,
                                         digits[-10:]]))
                .order_by(Lead.updated_at.desc()).first())
    except Exception as exc:                                 # noqa: BLE001
        _log.warning("ai_operations: contact resolution failed (%s)", exc)
        return None


def route(db: Session, *, provider: str, provider_event_id: str,
          channel: str, from_address: str, to_address: str, body: str = "",
          occurred_at: Optional[datetime] = None,
          classification: Optional[str] = None) -> Dict[str, Any]:
    """Place an inbound message, or record honestly that it could not be.

    Returns a dict describing the routing decision. Never raises: this is
    called from a webhook path where an exception is a 500 to a provider that
    will retry, and a retry storm is not an improvement on an unrouted
    message.
    """
    key = idempotency.key_for_inbound(provider=provider,
                                      provider_event_id=provider_event_id)
    existing = (db.query(AIInboundEvent)
                .filter(AIInboundEvent.provider == provider,
                        AIInboundEvent.provider_event_id == provider_event_id)
                .first())
    if existing is not None:
        # REDELIVERY. Not an error and not a second reply.
        return {"routed": bool(existing.routed), "duplicate": True,
                "event_id": existing.id, "thread_id": existing.thread_id,
                "reason": existing.route_reason}

    event = AIInboundEvent(
        provider=provider or "unknown", provider_event_id=provider_event_id,
        channel=channel, from_address=from_address, to_address=to_address,
        body_preview=audit.preview(body), body_digest=audit.digest(body),
        body_length=len(body or ""), routed=False,
        occurred_at=occurred_at, classification=classification,
        is_opt_out=looks_like_opt_out(body))

    organization_id = resolve_tenant(db, channel=channel,
                                     to_address=to_address)
    if organization_id is None:
        event.route_reason = "tenant_unresolved"
        db.add(event)
        db.flush()
        audit.record(db, event_code="ops.inbound_unrouted",
                     actor_kind=C.ACTOR_SYSTEM, severity="warning",
                     channel=channel, provider=provider,
                     message=("An inbound message could not be attributed to "
                              "any organization and was not routed."),
                     detail={"to_address_digest": audit.digest(to_address),
                             "provider_event_id": provider_event_id})
        contracts.mirror_supervisor_event(
            db, None, event_code=C.SUP_INBOUND_UNROUTABLE, severity="warning",
            message="An inbound message could not be attributed to a tenant.",
            detail={"channel": channel, "provider": provider},
            recommended_action="Check which number or address this arrived "
                               "on and who owns it.")
        return {"routed": False, "duplicate": False, "event_id": event.id,
                "reason": "tenant_unresolved"}

    event.organization_id = organization_id
    lead = resolve_contact(db, organization_id=organization_id,
                           channel=channel, from_address=from_address)
    if lead is None:
        event.route_reason = "contact_unresolved"
        db.add(event)
        db.flush()
        audit.record(db, event_code="ops.inbound_unrouted",
                     organization_id=organization_id,
                     actor_kind=C.ACTOR_SYSTEM, severity="info",
                     channel=channel, provider=provider,
                     message=("An inbound message reached a known "
                              "organization from an unknown contact."),
                     detail={"provider_event_id": provider_event_id})
        return {"routed": False, "duplicate": False, "event_id": event.id,
                "organization_id": organization_id,
                "reason": "contact_unresolved"}

    event.subject_type = "lead"
    event.subject_id = lead.id

    thread = continuity.find_open_thread(
        db, organization_id=organization_id, subject_type="lead",
        subject_id=lead.id)
    if thread is None:
        # A REPLY WITH NO AI CONVERSATION IS NOT THIS LAYER'S TO ANSWER. The
        # platform's own inbound pipeline handles it exactly as it does
        # today; recording it here gives an operator the full picture without
        # this layer claiming work it was never given.
        event.route_reason = "no_ai_conversation"
        db.add(event)
        db.flush()
        return {"routed": False, "duplicate": False, "event_id": event.id,
                "organization_id": organization_id, "subject_id": lead.id,
                "reason": "no_ai_conversation"}

    event.thread_id = thread.id
    event.employee_id = thread.employee_id
    event.work_item_id = thread.work_item_id
    event.human_owner_user_id = thread.human_owner_user_id
    event.routed = True
    event.route_reason = "matched_open_conversation"
    db.add(event)
    db.flush()

    _apply_to_thread(db, event, thread, lead, body=body)
    return {"routed": True, "duplicate": False, "event_id": event.id,
            "organization_id": organization_id, "subject_id": lead.id,
            "thread_id": thread.id, "employee_id": thread.employee_id,
            "human_owned": bool(thread.human_owner_user_id),
            "is_opt_out": bool(event.is_opt_out),
            "reason": event.route_reason}


def _apply_to_thread(db: Session, event: AIInboundEvent,
                     thread: AIConversationThread, lead,
                     *, body: str) -> None:
    """Record the inbound on the conversation and move what it moves."""
    continuity.record_inbound(db, thread, event.channel)

    comm = AICommunication(
        organization_id=thread.organization_id, thread_id=thread.id,
        employee_id=thread.employee_id, work_item_id=thread.work_item_id,
        subject_type=thread.subject_type, subject_id=thread.subject_id,
        direction=C.INBOUND, channel=event.channel, state=C.RESPONSE_RECEIVED,
        from_address=event.from_address, to_address=event.to_address,
        body_preview=event.body_preview, body_digest=event.body_digest,
        body_length=event.body_length,
        idempotency_key=idempotency.key_for_inbound(
            provider=event.provider, provider_event_id=event.provider_event_id),
        correlation_kind=C.CORR_INBOUND, provider=event.provider,
        provider_message_id=event.provider_event_id, simulated=False,
        responded_at=datetime.utcnow())
    is_new, comm = idempotency.claim(
        db, comm, organization_id=thread.organization_id,
        key=comm.idempotency_key, finder=idempotency.find_communication)
    event.communication_id = comm.id
    db.flush()

    # The outbound message this answers moves out of "waiting".
    waiting = (db.query(AICommunication)
               .filter(AICommunication.thread_id == thread.id,
                       AICommunication.direction == C.OUTBOUND,
                       AICommunication.state.in_((C.WAITING_FOR_RESPONSE,
                                                  C.SENT, C.DELIVERED)))
               .order_by(AICommunication.created_at.desc())
               .first())
    if waiting is not None:
        comm_state.transition(db, waiting, C.RESPONSE_RECEIVED,
                              reason="the contact replied",
                              actor_kind=C.ACTOR_HUMAN, strict=False)

    # ── AN OPT-OUT STOPS EVERYTHING, IMMEDIATELY AND WITHOUT A MODEL ──────
    if event.is_opt_out:
        from app.services.ai_operations import stop as stop_controls
        stop_controls.stop_thread(
            db, None, thread, reason=C.STOP_OPT_OUT,
            actor_kind=C.ACTOR_HUMAN, actor_id=None,
            detail={"inbound_event_id": event.id})
        _record_platform_suppression(db, thread, lead, event)
        audit.record(db, event_code="ops.inbound_opt_out",
                     organization_id=thread.organization_id,
                     actor_kind=C.ACTOR_HUMAN, thread_id=thread.id,
                     subject_type=thread.subject_type,
                     subject_id=thread.subject_id, channel=event.channel,
                     communication_id=comm.id, outcome=C.STOP_OPT_OUT,
                     severity="warning", human_involved=True,
                     message="The contact asked to stop. Work stopped.",
                     next_action="none")
        return

    if thread.human_owner_user_id:
        # A person owns this. The reply is recorded for them and the AI does
        # not act on it — including not "helpfully" drafting a response.
        audit.record(db, event_code="ops.inbound_to_human",
                     organization_id=thread.organization_id,
                     actor_kind=C.ACTOR_SYSTEM, thread_id=thread.id,
                     subject_type=thread.subject_type,
                     subject_id=thread.subject_id, channel=event.channel,
                     communication_id=comm.id, human_involved=True,
                     message="A reply arrived on a conversation a person "
                             "owns.",
                     next_action="human")
        return

    comm_state.thread_state(db, thread, C.RESPONSE_RECEIVED,
                            reason="inbound reply")
    thread.next_action_at = None
    db.flush()
    audit.record(db, event_code="ops.inbound_routed",
                 organization_id=thread.organization_id,
                 actor_kind=C.ACTOR_SYSTEM, thread_id=thread.id,
                 subject_type=thread.subject_type,
                 subject_id=thread.subject_id, channel=event.channel,
                 communication_id=comm.id, state_to=C.RESPONSE_RECEIVED,
                 message="A reply was routed to an AI conversation.",
                 next_action="employee_processes_response")


def _record_platform_suppression(db: Session, thread: AIConversationThread,
                                 lead, event: AIInboundEvent) -> None:
    """An opt-out belongs to the PLATFORM, not to this layer.

    Written through `compliance_service.add_suppression_entry`, which is the
    one write path into the suppression authority — so the family's STOP
    stops the cadence engine, the auto-send queue, the voice bridge and
    everything else, not merely the AI employee that happened to receive it.
    """
    try:
        from app.services import compliance_service
        phone = getattr(lead, "phone", None)
        if phone and event.channel in (C.CHANNEL_SMS, C.CHANNEL_VOICE):
            compliance_service.add_suppression_entry_from_reply(
                db, thread.organization_id, phone,
                reason="Replied STOP to an AI conversation")
        lead.status = "dnc"
        db.flush()
    except Exception as exc:                                 # noqa: BLE001
        # The thread is already stopped; failing to write the platform-wide
        # suppression is serious but must not undo that.
        _log.error("ai_operations: OPT-OUT NOT WRITTEN TO THE PLATFORM "
                   "SUPPRESSION LIST for lead %s (%s)",
                   getattr(lead, "id", "?"), exc)


# ═══════════════════════════════════════════════════════════════════════════
# DELIVERY STATUS
# ═══════════════════════════════════════════════════════════════════════════

def delivery_status(db: Session, *, provider: str, provider_message_id: str,
                    status: str, error: Optional[str] = None
                    ) -> Dict[str, Any]:
    """A provider says what became of a message we sent.

    OUT-OF-ORDER SAFE. A "delivered" arriving after the conversation was
    stopped, or after the family already replied, is applied with
    `strict=False`: the transition is skipped and logged rather than raising.
    The provider is not wrong and neither are we; the message simply moved on.
    """
    comm = (db.query(AICommunication)
            .filter(AICommunication.provider_message_id == provider_message_id)
            .order_by(AICommunication.created_at.desc())
            .first())
    if comm is None:
        return {"matched": False, "reason": "unknown provider message id"}

    normalized = (status or "").strip().lower()
    comm.provider_status = normalized
    if normalized in ("delivered", "read"):
        comm_state.transition(db, comm, C.DELIVERED, reason=normalized,
                              actor_kind=C.ACTOR_PROVIDER, strict=False)
    elif normalized in ("failed", "undelivered", "bounced"):
        comm.provider_error = (error or normalized)[:480]
        comm_state.transition(db, comm, C.FAILED, reason=normalized,
                              actor_kind=C.ACTOR_PROVIDER, strict=False)
    db.flush()
    audit.record(db, event_code="ops.delivery_status",
                 organization_id=comm.organization_id,
                 actor_kind=C.ACTOR_PROVIDER, thread_id=comm.thread_id,
                 communication_id=comm.id, channel=comm.channel,
                 provider=provider, state_to=comm.state, outcome=normalized,
                 message="Provider reported '%s'." % normalized)
    return {"matched": True, "communication_id": comm.id,
            "state": comm.state}
