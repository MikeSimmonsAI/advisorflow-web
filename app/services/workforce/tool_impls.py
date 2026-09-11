"""THE REGISTERED TOOLS THEMSELVES.

EVERY FUNCTION HERE RUNS ONLY AFTER `tools.authorize` HAS SAID YES. None of
them re-checks tenancy, authority, activation, eligibility or entitlement —
not because those do not matter, but because a check repeated in twenty-eight
places is a check that is wrong in one of them. The gateway is the boundary;
these are the actions behind it.

WHAT A HANDLER MAY DO:
  * read and write records the gateway has already proven are in scope
  * raise `ToolRefusal(code, reason)` for a BUSINESS refusal the employee
    should reason about ("that slot is taken", "no email address on record")
  * return a plain dict, which becomes the observation the model sees

WHAT A HANDLER MAY NOT DO:
  * reach outside except through `outbound.adapter(...)`
  * widen its own scope from an argument
  * decide that somebody may be contacted

"AN OPPORTUNITY" IN A CUSTOMER WORKSPACE IS THE CRM CONTACT, NOT THE BRAND'S
`opportunities` TABLE. That table belongs to the brand SELLING AdvisorFlow —
it is where a rep tracks the deal to sign the funeral home up. The funeral
home's own pipeline is `crm_contacts` and its `stage`. Pointing a customer's AI
employee at the brand's opportunities would let a customer's employee write
into the vendor's sales pipeline, which is a tenancy violation wearing a
familiar word. See section 8: reuse the right existing structure rather than
the one with the matching name.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from app.models.models import (BookingLink, CRMContact, EmailMessage, Lead,
                               Message, Reply, SuppressionEntry,
                               SuppressionSource)
from app.services.workforce import booking as wf_booking
from app.services.workforce import constants as C
from app.services.workforce import handoff as wf_handoff
from app.services.workforce import knowledge as wf_knowledge
from app.services.workforce import memory as wf_memory
from app.services.workforce import outbound
from app.services.workforce import performance
from app.services.workforce import policy as wf_policy
from app.services.workforce import queue as wf_queue
from app.services.workforce.tools import ToolRefusal, register

_log = logging.getLogger(__name__)


def _lead(ctx, args) -> Lead:
    """The record this call is about. The gateway already proved it is ours."""
    lead = (ctx.db.query(Lead)
            .filter(Lead.id == args.get("lead_id"),
                    Lead.organization_id == ctx.organization_id).first())
    if lead is None:
        raise ToolRefusal(C.DENY_RECORD_NOT_FOUND,
                          "No such record in this organization.")
    return lead


def _work_item(ctx):
    if ctx.work_item is None:
        raise ToolRefusal(C.DENY_WORK_ITEM_MISMATCH,
                          "There is no assigned record to act on.")
    return ctx.work_item


def _terminal(ctx, state: str, reason: str, detail: Optional[str] = None):
    """Close the work item through the state machine, never by assignment."""
    item = _work_item(ctx)
    try:
        # `advance_to`, not `transition`: a tool that concludes the work must
        # be able to do so from wherever the item legitimately is, walking real
        # edges. See the header of queue.advance_to for the defect that came
        # from using a single hop here.
        wf_queue.advance_to(ctx.db, item, state, reason=reason,
                            actor_kind=C.ACTOR_AI_EMPLOYEE,
                            actor_id=ctx.employee.id,
                            run_id=getattr(ctx.run, "id", None),
                            outcome_detail=detail,
                            claim_token=ctx.claim_token)
    except wf_queue.IllegalTransition as exc:
        raise ToolRefusal(C.DENY_BUSINESS_RULE, str(exc))
    performance.bump_outcome(ctx.db, ctx.employee, item.outcome)
    return {"work_item_id": item.id, "state": item.state,
            "outcome": item.outcome}


# ═══════════════════════════════════════════════════════════════════════════
# READ
# ═══════════════════════════════════════════════════════════════════════════

@register("lead.get")
def lead_get(ctx, args, auth) -> Dict:
    lead = _lead(ctx, args)
    return {
        "lead_id": lead.id,
        "first_name": lead.first_name,
        "last_name": lead.last_name,
        # THE ADDRESSES THEMSELVES ARE NOT RETURNED. The employee never needs
        # to know the phone number — it asks the platform to send, and the
        # platform resolves the address of record. Handing a model a phone
        # number invites it to put one in a message body.
        "has_phone": bool(lead.phone),
        "has_email": bool(lead.email),
        "tier": lead.tier,
        "status": lead.status,
        "relationship": lead.relationship_type,
        "source": lead.source,
        "city": lead.city,
        "state": lead.state,
        "created_at": lead.created_at.isoformat() if lead.created_at else None,
        "last_contacted_at": (lead.last_messaged_at.isoformat()
                              if lead.last_messaged_at else None),
        "_target_type": "lead", "_target_id": lead.id,
    }


@register("lead.get_context")
def lead_get_context(ctx, args, auth) -> Dict:
    from app.services import qualification
    lead = _lead(ctx, args)
    out = {
        "lead_id": lead.id,
        "notes": (lead.notes or "")[:1000] or None,
        "tier": lead.tier,
        "message_track": lead.message_track,
        "engagement_temperature": getattr(lead.engagement_temperature, "value",
                                          lead.engagement_temperature),
        "permission": {
            "sms": lead.allow_sms, "email": lead.allow_email,
            "voice": lead.allow_voice, "sms_consent": bool(lead.sms_consent),
        },
        "eligibility": {},
        "_target_type": "lead", "_target_id": lead.id,
    }
    # The platform's OWN verdict for each channel, so the employee plans with
    # the same answer the gateway will give it rather than discovering the
    # refusal after composing a message.
    from app.services.workforce import eligibility as wf_eligibility
    for channel in sorted(ctx.policy.channels):
        verdict = wf_eligibility.evaluate(
            ctx.db, employee=ctx.employee, lead=lead, channel=channel,
            pol=ctx.policy, now=ctx.now, enforce_hours=False,
            enforce_frequency=False)
        out["eligibility"][channel] = {
            "result": verdict.result,
            "reasons": [r.get("label") for r in verdict.reasons][:4],
        }
    _ = qualification  # imported for the module-level contract, read above
    return out


@register("conversation.get_history")
def conversation_get_history(ctx, args, auth) -> Dict:
    """The shared customer conversation, every channel, oldest first.

    ONE HISTORY, NOT A BOT TRANSCRIPT. Section 16: employees share the
    authoritative record rather than keeping isolated histories. What a human
    advisor sent last year is in here, and so is what another AI employee sent
    this morning.
    """
    lead = _lead(ctx, args)
    limit = int(args.get("limit") or 30)
    limit = max(1, min(limit, 100))

    events: List[Dict] = []
    for m in (ctx.db.query(Message).filter(Message.lead_id == lead.id)
              .order_by(Message.sent_at.desc()).limit(limit).all()):
        events.append({"direction": "outbound", "channel": "sms",
                       "at": m.sent_at.isoformat() if m.sent_at else None,
                       "body": m.body, "status": m.delivery_status})
    for e in (ctx.db.query(EmailMessage)
              .filter(EmailMessage.lead_id == lead.id)
              .order_by(EmailMessage.sent_at.desc()).limit(limit).all()):
        events.append({"direction": "outbound", "channel": "email",
                       "at": e.sent_at.isoformat() if e.sent_at else None,
                       "body": e.body_html, "subject": e.subject,
                       "status": e.status})
    for r in (ctx.db.query(Reply).filter(Reply.lead_id == lead.id)
              .order_by(Reply.received_at.desc()).limit(limit).all()):
        events.append({"direction": "inbound", "channel": r.source,
                       "at": r.received_at.isoformat() if r.received_at else None,
                       "body": r.body,
                       "classification": getattr(r.classification, "value",
                                                 r.classification)})
    events.sort(key=lambda d: d.get("at") or "")
    return {"lead_id": lead.id, "count": len(events),
            "messages": events[-limit:],
            "_target_type": "lead", "_target_id": lead.id}


@register("calendar.get_availability")
def calendar_get_availability(ctx, args, auth) -> Dict:
    lead = _lead(ctx, args) if args.get("lead_id") else None
    if lead is None and ctx.work_item is not None:
        lead = (ctx.db.query(Lead)
                .filter(Lead.id == ctx.work_item.subject_id,
                        Lead.organization_id == ctx.organization_id).first())
    if lead is None:
        raise ToolRefusal(C.DENY_BAD_ARGUMENTS,
                          "Availability is read for a specific record.")
    result = wf_booking.available_slots(
        ctx.db, employee=ctx.employee, lead=lead,
        days_ahead=int(args.get("days_ahead") or wf_booking.DEFAULT_DAYS_AHEAD),
        now=ctx.now)
    result["note"] = ("Offer ONLY these times, and pass `starts_at` back "
                      "exactly as written. Any other time will be refused.")
    result["_target_type"] = "lead"
    result["_target_id"] = lead.id
    return result


@register("knowledge.search")
def knowledge_search(ctx, args, auth) -> Dict:
    binding = wf_policy.json_obj(ctx.employee.knowledge_binding)
    return wf_knowledge.search(ctx.db, ctx.organization_id,
                               args.get("query") or "",
                               limit=int(args.get("limit") or 5),
                               binding=binding)


@register("customer.get_context")
def customer_get_context(ctx, args, auth) -> Dict:
    from app.models.models import Organization
    org = (ctx.db.query(Organization)
           .filter(Organization.id == ctx.organization_id).first())
    if org is None:
        raise ToolRefusal(C.DENY_RECORD_NOT_FOUND,
                          "The organization could not be read.")
    return {
        "business_name": org.brand_name or org.name,
        "industry": org.industry,
        "address": org.org_address,
        "phone": org.org_phone,
        # Deliberately absent: credentials, billing, Stripe ids, API keys,
        # internal counts. An employee has no business knowing any of them.
    }


@register("opportunity.get")
def opportunity_get(ctx, args, auth) -> Dict:
    row = (ctx.db.query(CRMContact)
           .filter(CRMContact.id == args.get("opportunity_id"),
                   CRMContact.organization_id == ctx.organization_id).first())
    if row is None:
        raise ToolRefusal(C.DENY_RECORD_NOT_FOUND,
                          "No such pipeline record in this organization.")
    return {"opportunity_id": row.id, "stage": row.stage,
            "lead_id": row.lead_id, "tags": row.tags,
            "last_contacted_at": (row.last_contacted_at.isoformat()
                                  if row.last_contacted_at else None),
            "_target_type": "crm_contact", "_target_id": row.id}


# ═══════════════════════════════════════════════════════════════════════════
# RECORD
# ═══════════════════════════════════════════════════════════════════════════

@register("lead.add_note")
def lead_add_note(ctx, args, auth) -> Dict:
    lead = _lead(ctx, args)
    stamp = ctx.now.strftime("%Y-%m-%d %H:%M")
    entry = "[%s — %s (AI)] %s" % (stamp, ctx.employee.name,
                                   wf_memory.sanitize(args.get("note"))[:1000])
    lead.notes = ((lead.notes or "") + ("\n" if lead.notes else "") + entry)[:20000]
    ctx.db.flush()
    return {"lead_id": lead.id, "note_added": True,
            "_target_type": "lead", "_target_id": lead.id}


@register("lead.update_qualification")
def lead_update_qualification(ctx, args, auth) -> Dict:
    """Record the FACTS, not a verdict.

    Section 46: deterministic platform qualification stays authoritative. The
    employee may gather and recommend; it does not rewrite the rules. The facts
    land in employee memory and in the record's notes, where the platform's own
    qualification can and does read some of them.
    """
    lead = _lead(ctx, args)
    facts = args.get("facts") or {}
    if not isinstance(facts, dict) or not facts:
        raise ToolRefusal(C.DENY_BAD_ARGUMENTS, "No facts were supplied.")
    stored = 0
    for key, value in list(facts.items())[:25]:
        wf_memory.remember(ctx.db, ctx.employee, str(key)[:120],
                           str(value)[:500], scope=wf_memory.SCOPE_LEAD,
                           scope_id=lead.id,
                           source=wf_memory.SOURCE_CONTACT)
        stored += 1
    band = (args.get("recommended_band") or "").strip()[:40]
    summary = "; ".join("%s: %s" % (k, v) for k, v in list(facts.items())[:8])
    lead_add_note(ctx, {"lead_id": lead.id,
                        "note": "Qualification facts — %s%s"
                                % (summary,
                                   (" (recommended: %s)" % band) if band else "")},
                  auth)
    return {"lead_id": lead.id, "facts_recorded": stored,
            "recommended_band": band or None,
            "note": "Recorded. The platform's own qualification engine remains "
                    "the authority on whether this record qualifies.",
            "_target_type": "lead", "_target_id": lead.id}


@register("lead.mark_not_interested")
def lead_mark_not_interested(ctx, args, auth) -> Dict:
    lead = _lead(ctx, args)
    detail = wf_memory.sanitize(args.get("detail"))[:255]
    # NOT AN OPT-OUT. "No thanks" is not "never contact me again", and writing
    # a suppression record for it would claim a legal instruction the person
    # did not give. The record status stops THIS work; the platform's DNC
    # machinery is untouched.
    lead.status = "not_interested"
    ctx.db.flush()
    out = _terminal(ctx, C.NOT_INTERESTED, "contact said they are not interested",
                    detail)
    out["lead_id"] = lead.id
    out["_target_type"] = "lead"
    out["_target_id"] = lead.id
    return out


@register("lead.mark_do_not_contact")
def lead_mark_do_not_contact(ctx, args, auth) -> Dict:
    """An opt-out, written where the WHOLE PLATFORM will honour it.

    Not a flag on the work item and not a note. `Lead.status = 'dnc'` is what
    `qualification.qualify_one` excludes on, and a suppression entry is what
    `sms_service` and `compliance_service` check independently. An opt-out that
    only this employee respects is not an opt-out.
    """
    lead = _lead(ctx, args)
    detail = wf_memory.sanitize(args.get("detail"))[:255]
    lead.status = "dnc"
    lead.allow_sms = False
    lead.allow_email = False
    lead.allow_voice = False
    if lead.phone:
        existing = (ctx.db.query(SuppressionEntry)
                    .filter(SuppressionEntry.organization_id == ctx.organization_id,
                            SuppressionEntry.phone == lead.phone).first())
        if existing is None:
            ctx.db.add(SuppressionEntry(
                organization_id=ctx.organization_id, phone=lead.phone,
                reason="Opt-out recorded by AI employee %s%s"
                       % (ctx.employee.name, (": " + detail) if detail else ""),
                source=SuppressionSource.MANUAL))
    ctx.db.flush()
    from app.services.workforce import audit as wf_audit
    wf_audit.write_platform_audit(
        ctx.db, ctx.employee, action="ai_workforce.opt_out_recorded",
        target_type="lead", target_id=lead.id,
        details={"detail": detail}, run_id=getattr(ctx.run, "id", None),
        work_item_id=getattr(ctx.work_item, "id", None))
    out = _terminal(ctx, C.DO_NOT_CONTACT, "contact opted out", detail)
    out["lead_id"] = lead.id
    out["suppressed"] = bool(lead.phone)
    out["_target_type"] = "lead"
    out["_target_id"] = lead.id
    return out


@register("lead.mark_bad_contact")
def lead_mark_bad_contact(ctx, args, auth) -> Dict:
    lead = _lead(ctx, args)
    channel = (args.get("channel") or "").lower()
    detail = wf_memory.sanitize(args.get("detail"))[:255]
    if channel == C.CHANNEL_EMAIL:
        lead.manual_flag = "bad_email"
    elif channel in (C.CHANNEL_SMS, C.CHANNEL_VOICE):
        lead.manual_flag = "bad_phone"
    else:
        raise ToolRefusal(C.DENY_BAD_ARGUMENTS,
                          "Say which channel is unusable.")
    lead.manual_flag_reason = ("Flagged by AI employee %s%s"
                               % (ctx.employee.name,
                                  (": " + detail) if detail else ""))[:255]
    ctx.db.flush()
    out = _terminal(ctx, C.BAD_CONTACT, "no usable %s address" % channel, detail)
    out["lead_id"] = lead.id
    out["_target_type"] = "lead"
    out["_target_id"] = lead.id
    return out


@register("memory.remember")
def memory_remember(ctx, args, auth) -> Dict:
    scope = (args.get("scope") or wf_memory.SCOPE_LEAD).lower()
    scope_id = ""
    if scope == wf_memory.SCOPE_LEAD:
        scope_id = getattr(ctx.work_item, "subject_id", "") or ""
    source = (args.get("source") or wf_memory.SOURCE_EMPLOYEE)
    row = wf_memory.remember(ctx.db, ctx.employee, args.get("key"),
                             args.get("value"), scope=scope, scope_id=scope_id,
                             source=source)
    return {"remembered": row.key, "scope": row.scope, "source": row.source}


# ═══════════════════════════════════════════════════════════════════════════
# COMMUNICATION
# ═══════════════════════════════════════════════════════════════════════════

MAX_SMS_CHARS = 1200
MAX_EMAIL_CHARS = 20000


def _check_body(body: str, limit: int, what: str) -> str:
    text = (body or "").strip()
    if not text:
        raise ToolRefusal(C.DENY_BAD_ARGUMENTS, "The %s was empty." % what)
    if len(text) > limit:
        raise ToolRefusal(C.DENY_BAD_ARGUMENTS,
                          "That %s is too long (%d characters, limit %d)."
                          % (what, len(text), limit))
    return text


@register("conversation.prepare_sms")
def conversation_prepare_sms(ctx, args, auth) -> Dict:
    """Compose without sending. Reaches nobody, ever.

    In SHADOW this is where the recommendation is captured — the employee
    genuinely composes what it would send, that composition is recorded, and
    the send is refused by the gateway. See section 48.
    """
    lead = _lead(ctx, args)
    body = _check_body(args.get("body"), MAX_SMS_CHARS, "message")
    _record_shadow(ctx, lead, "conversation.send_sms",
                   {"channel": C.CHANNEL_SMS, "chars": len(body)},
                   "Composed an SMS for this contact.")
    return {"lead_id": lead.id, "channel": C.CHANNEL_SMS,
            "chars": len(body), "prepared": True,
            "note": "Nothing has been sent. Use the send tool to deliver it.",
            "_target_type": "lead", "_target_id": lead.id}


@register("conversation.prepare_email")
def conversation_prepare_email(ctx, args, auth) -> Dict:
    lead = _lead(ctx, args)
    subject = _check_body(args.get("subject"), 300, "subject")
    body = _check_body(args.get("body"), MAX_EMAIL_CHARS, "message")
    _record_shadow(ctx, lead, "conversation.send_email",
                   {"channel": C.CHANNEL_EMAIL, "chars": len(body),
                    "subject_chars": len(subject)},
                   "Composed an email for this contact.")
    return {"lead_id": lead.id, "channel": C.CHANNEL_EMAIL,
            "chars": len(body), "prepared": True,
            "note": "Nothing has been sent. Use the send tool to deliver it.",
            "_target_type": "lead", "_target_id": lead.id}


def _record_shadow(ctx, lead, would_call: str, payload: Dict,
                   rationale: str) -> None:
    """In SHADOW, a composed message is the recommendation. Best effort."""
    from app.models.workforce_models import AIShadowRecommendation
    from app.services.workforce import activation as wf_activation
    import json
    try:
        resolved = wf_activation.resolve(ctx.db, employee=ctx.employee)
        if not resolved.is_shadow:
            return
        ctx.db.add(AIShadowRecommendation(
            organization_id=ctx.organization_id, employee_id=ctx.employee.id,
            work_item_id=getattr(ctx.work_item, "id", None),
            subject_type="lead", subject_id=lead.id,
            trigger_event=ctx.trigger, recommended_tool=would_call,
            recommended_payload=json.dumps(payload)[:4000],
            rationale=rationale[:1000]))
        ctx.db.flush()
    except Exception:                                        # noqa: BLE001
        _log.exception("workforce: could not record shadow recommendation")


@register("conversation.send_sms")
def conversation_send_sms(ctx, args, auth) -> Dict:
    lead = _lead(ctx, args)
    body = _check_body(args.get("body"), MAX_SMS_CHARS, "message")
    try:
        result = outbound.adapter(C.CHANNEL_SMS).send(
            ctx.db, ctx.employee, lead, body)
    except outbound.OutboundRefused as refused:
        raise ToolRefusal(refused.code, refused.reason)
    except ValueError as exc:
        # The platform's own send path refusing — DNC, suppression, capacity.
        # It is a refusal, not a crash, and the employee is told plainly.
        raise ToolRefusal(C.DENY_BUSINESS_RULE, str(exc)[:255])
    _after_send(ctx, lead, C.CHANNEL_SMS)
    return {"lead_id": lead.id, "channel": C.CHANNEL_SMS,
            "message_id": result.get("message_id"),
            "simulated": bool(result.get("simulated")),
            "_target_type": "lead", "_target_id": lead.id}


@register("conversation.send_email")
def conversation_send_email(ctx, args, auth) -> Dict:
    lead = _lead(ctx, args)
    subject = _check_body(args.get("subject"), 300, "subject")
    body = _check_body(args.get("body"), MAX_EMAIL_CHARS, "message")
    try:
        result = outbound.adapter(C.CHANNEL_EMAIL).send(
            ctx.db, ctx.employee, lead, subject, body)
    except outbound.OutboundRefused as refused:
        raise ToolRefusal(refused.code, refused.reason)
    except ValueError as exc:
        raise ToolRefusal(C.DENY_BUSINESS_RULE, str(exc)[:255])
    _after_send(ctx, lead, C.CHANNEL_EMAIL)
    return {"lead_id": lead.id, "channel": C.CHANNEL_EMAIL,
            "message_id": result.get("message_id"),
            "simulated": bool(result.get("simulated")),
            "_target_type": "lead", "_target_id": lead.id}


@register("conversation.place_call")
def conversation_place_call(ctx, args, auth) -> Dict:
    """UNREACHABLE IN THIS BUILD, and present so the architecture is real.

    `tools.authorize` refuses this key at gate 7 whenever live voice is
    disabled, which it is. The adapter refuses it again. The handler exists so
    the path — eligibility for the voice channel, the disposition shape, the
    work-item bookkeeping — is exercised by the simulator against the fake
    adapter rather than being a hole the day voice is switched on.
    """
    lead = _lead(ctx, args)
    try:
        result = outbound.adapter(C.CHANNEL_VOICE).place_call(
            ctx.db, ctx.employee, lead, args.get("purpose") or "")
    except outbound.OutboundRefused as refused:
        raise ToolRefusal(refused.code, refused.reason)
    _after_send(ctx, lead, C.CHANNEL_VOICE)
    return {"lead_id": lead.id, "channel": C.CHANNEL_VOICE,
            "call_id": result.get("call_id"),
            "disposition": result.get("disposition"),
            "answered_by": result.get("answered_by"),
            "simulated": bool(result.get("simulated")),
            "_target_type": "lead", "_target_id": lead.id}


def _after_send(ctx, lead: Lead, channel: str) -> None:
    """Bookkeeping every outward touch shares.

    Counting the touch and scheduling the wait live HERE rather than in each
    send handler, so an employee cannot send without the touch being counted —
    which is what the exhaustion ceiling depends on.
    """
    performance.bump(ctx.db, ctx.employee, "messages_sent", now=ctx.now)
    performance.bump(ctx.db, ctx.employee, "attempts", now=ctx.now)
    item = ctx.work_item
    if item is None:
        return
    exhausted = wf_queue.record_touch(ctx.db, item, now=ctx.now,
                                      max_touches=ctx.policy.max_touches)
    try:
        if exhausted:
            wf_queue.transition(ctx.db, item, C.EXHAUSTED,
                                reason="every permitted touch has been used",
                                actor_kind=C.ACTOR_AI_EMPLOYEE,
                                actor_id=ctx.employee.id,
                                run_id=getattr(ctx.run, "id", None),
                                claim_token=ctx.claim_token)
            performance.bump_outcome(ctx.db, ctx.employee, item.outcome)
        elif item.state != C.WAITING_FOR_RESPONSE:
            wf_queue.transition(ctx.db, item, C.WAITING_FOR_RESPONSE,
                                reason="message sent; waiting for a reply",
                                actor_kind=C.ACTOR_AI_EMPLOYEE,
                                actor_id=ctx.employee.id,
                                run_id=getattr(ctx.run, "id", None),
                                claim_token=ctx.claim_token)
    except wf_queue.IllegalTransition:
        # The record moved underneath us — a reply arrived, a human closed it.
        # The send happened and is counted; forcing a state now would overwrite
        # whatever the more recent thing was.
        _log.info("workforce: could not move item %s after send", item.id)
    cfg = wf_policy.json_obj(ctx.employee.config)
    try:
        wait_hours = int(cfg.get("wait_hours_between_touches", 48))
    except (TypeError, ValueError):
        wait_hours = 48
    wf_queue.schedule_next(ctx.db, item, wait_hours * 60, now=ctx.now)


# ═══════════════════════════════════════════════════════════════════════════
# APPOINTMENTS
# ═══════════════════════════════════════════════════════════════════════════

@register("appointment.book")
def appointment_book(ctx, args, auth) -> Dict:
    lead = _lead(ctx, args)
    try:
        result = wf_booking.book_slot(
            ctx.db, employee=ctx.employee, lead=lead,
            starts_at=args.get("start_at"),
            appt_label=args.get("appt_label"), now=ctx.now)
    except ValueError as exc:
        # THE CALENDAR REFUSING IS AN ANSWER, not a failure. The employee is
        # told exactly why so it can offer another time instead of retrying.
        raise ToolRefusal(C.DENY_BUSINESS_RULE, str(exc)[:255])
    if not result.get("already_booked"):
        out = _terminal(ctx, C.APPOINTMENT_BOOKED,
                        "appointment booked", result.get("starts_at_local"))
        result.update(out)
    result["_target_type"] = "booking_link"
    result["_target_id"] = result.get("booking_id")
    from app.services.workforce import audit as wf_audit
    wf_audit.write_platform_audit(
        ctx.db, ctx.employee, action="ai_workforce.appointment_booked",
        target_type="booking_link", target_id=result.get("booking_id") or "",
        details={"lead_id": lead.id, "starts_at": result.get("starts_at")},
        run_id=getattr(ctx.run, "id", None),
        work_item_id=getattr(ctx.work_item, "id", None))
    return result


@register("appointment.reschedule")
def appointment_reschedule(ctx, args, auth) -> Dict:
    item = _work_item(ctx)
    lead = (ctx.db.query(Lead)
            .filter(Lead.id == item.subject_id,
                    Lead.organization_id == ctx.organization_id).first())
    if lead is None:
        raise ToolRefusal(C.DENY_RECORD_NOT_FOUND, "No such record.")
    try:
        return dict(wf_booking.reschedule(
            ctx.db, employee=ctx.employee, lead=lead,
            booking_link_id=args.get("booking_link_id"),
            starts_at=args.get("start_at"), now=ctx.now),
            _target_type="booking_link",
            _target_id=args.get("booking_link_id"))
    except ValueError as exc:
        raise ToolRefusal(C.DENY_BUSINESS_RULE, str(exc)[:255])


# ═══════════════════════════════════════════════════════════════════════════
# PIPELINE
# ═══════════════════════════════════════════════════════════════════════════

@register("opportunity.create")
def opportunity_create(ctx, args, auth) -> Dict:
    """Open the CUSTOMER's own pipeline record. See the module header.

    Idempotent on the lead: a record that already has a pipeline row gets that
    row back rather than a second one. Two pipeline entries for one family is
    the duplicate-opportunity failure section 36 asks to be proven against.
    """
    lead = _lead(ctx, args)
    existing = (ctx.db.query(CRMContact)
                .filter(CRMContact.organization_id == ctx.organization_id,
                        CRMContact.lead_id == lead.id).first())
    if existing is not None:
        return {"opportunity_id": existing.id, "stage": existing.stage,
                "already_existed": True,
                "_target_type": "crm_contact", "_target_id": existing.id}
    row = CRMContact(
        organization_id=ctx.organization_id, lead_id=lead.id,
        first_name=lead.first_name, last_name=lead.last_name,
        phone=lead.phone, email=lead.email,
        assigned_to_id=lead.assigned_to_id, stage="inquiry",
        notes=wf_memory.sanitize(args.get("summary"))[:2000] or None)
    ctx.db.add(row)
    ctx.db.flush()
    return {"opportunity_id": row.id, "stage": row.stage,
            "already_existed": False,
            "_target_type": "crm_contact", "_target_id": row.id}


@register("opportunity.update")
def opportunity_update(ctx, args, auth) -> Dict:
    """Annotate, and move the stage ONLY where the customer's own rules allow.

    The stage vocabulary is the customer's (`Organization.crm_stages`), not a
    list this engine invented. An employee asking for a stage the customer has
    not configured is refused rather than silently creating one — a pipeline
    with a stage nobody defined is a report nobody can read.
    """
    from app.models.models import Organization
    row = (ctx.db.query(CRMContact)
           .filter(CRMContact.id == args.get("opportunity_id"),
                   CRMContact.organization_id == ctx.organization_id).first())
    if row is None:
        raise ToolRefusal(C.DENY_RECORD_NOT_FOUND,
                          "No such pipeline record in this organization.")
    stage = (args.get("stage") or "").strip()
    if stage:
        org = (ctx.db.query(Organization)
               .filter(Organization.id == ctx.organization_id).first())
        allowed = _configured_stages(org)
        if allowed and stage not in allowed:
            raise ToolRefusal(
                C.DENY_BUSINESS_RULE,
                "'%s' is not one of this business's pipeline stages (%s)."
                % (stage, ", ".join(allowed[:8])))
        row.stage = stage
    note = wf_memory.sanitize(args.get("note"))[:1000]
    if note:
        row.notes = ((row.notes or "") + ("\n" if row.notes else "")
                     + "[%s — %s (AI)] %s"
                     % (ctx.now.strftime("%Y-%m-%d %H:%M"), ctx.employee.name,
                        note))[:8000]
    row.updated_at = datetime.utcnow()
    ctx.db.flush()
    return {"opportunity_id": row.id, "stage": row.stage, "updated": True,
            "_target_type": "crm_contact", "_target_id": row.id}


def _configured_stages(org) -> List[str]:
    import json
    try:
        val = json.loads(getattr(org, "crm_stages", None) or "[]")
    except (ValueError, TypeError):
        return []
    out = []
    for item in val if isinstance(val, list) else []:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict) and item.get("key"):
            out.append(str(item["key"]))
    return out


# ═══════════════════════════════════════════════════════════════════════════
# WORKFLOW — how an employee stops, escalates, or asks for a person
# ═══════════════════════════════════════════════════════════════════════════

@register("handoff.create")
def handoff_create(ctx, args, auth) -> Dict:
    item = _work_item(ctx)
    lead = (ctx.db.query(Lead)
            .filter(Lead.id == item.subject_id,
                    Lead.organization_id == ctx.organization_id).first())
    row = wf_handoff.create(
        ctx.db, employee=ctx.employee, lead=lead, work_item=item,
        reason_code=args.get("reason_code"),
        summary=wf_memory.sanitize(args.get("summary")),
        recommended_action=wf_memory.sanitize(args.get("recommended_action")),
        known_facts=[wf_memory.sanitize(f)
                     for f in (args.get("known_facts") or [])],
        open_questions=[wf_memory.sanitize(q)
                        for q in (args.get("open_questions") or [])],
        priority=args.get("priority") or "normal",
        run_id=getattr(ctx.run, "id", None))
    out = _terminal(ctx, C.HUMAN_HANDOFF,
                    "handed to a person: %s" % row.reason_code,
                    row.recommended_action)
    out.update({"handoff_id": row.id, "priority": row.priority,
                "assigned_to_user_id": row.assigned_to_user_id,
                "assigned_queue": row.assigned_queue,
                "_target_type": "handoff", "_target_id": row.id})
    return out


@register("employee.request_review")
def employee_request_review(ctx, args, auth) -> Dict:
    """The employee is not confident enough. Parking it is the right answer.

    Section 41 and section 14: uncertainty must have somewhere to go that is
    not a guess. This is deliberately cheap to call — an employee that asks for
    review too often is a configuration problem an operator can see on a
    screen, where one that guesses is a problem a family finds out about.
    """
    reason = wf_memory.sanitize(args.get("reason"))[:255]
    # NO EXPLICIT COUNTER BUMP HERE. `_terminal` records the outcome, and
    # `OUTCOME_METRIC` maps `needs_review` to `records_review` — an extra bump
    # here counted every review twice, which made the ledger's review rate
    # exactly double the number of records actually in the queue.
    out = _terminal(ctx, C.NEEDS_REVIEW, reason or "employee asked for review")
    out["reason"] = reason
    return out


@register("employee.pause_work_item")
def employee_pause_work_item(ctx, args, auth) -> Dict:
    item = _work_item(ctx)
    reason = wf_memory.sanitize(args.get("reason"))[:255]
    try:
        wf_queue.transition(ctx.db, item, C.PAUSED, reason=reason,
                            actor_kind=C.ACTOR_AI_EMPLOYEE,
                            actor_id=ctx.employee.id,
                            run_id=getattr(ctx.run, "id", None),
                            claim_token=ctx.claim_token)
    except wf_queue.IllegalTransition as exc:
        raise ToolRefusal(C.DENY_BUSINESS_RULE, str(exc))
    minutes = args.get("resume_after_minutes")
    if minutes:
        wf_queue.schedule_next(ctx.db, item, int(minutes), now=ctx.now)
    return {"work_item_id": item.id, "state": item.state, "reason": reason}


@register("employee.wait_for_response")
def employee_wait(ctx, args, auth) -> Dict:
    item = _work_item(ctx)
    minutes = args.get("wait_minutes")
    if minutes is None:
        cfg = wf_policy.json_obj(ctx.employee.config)
        try:
            minutes = int(cfg.get("wait_hours_between_touches", 48)) * 60
        except (TypeError, ValueError):
            minutes = 48 * 60
    minutes = max(5, min(int(minutes), 60 * 24 * 30))
    try:
        wf_queue.transition(ctx.db, item, C.WAITING_FOR_RESPONSE,
                            reason=wf_memory.sanitize(args.get("note"))[:255]
                            or "waiting for a reply",
                            actor_kind=C.ACTOR_AI_EMPLOYEE,
                            actor_id=ctx.employee.id,
                            run_id=getattr(ctx.run, "id", None),
                            claim_token=ctx.claim_token)
    except wf_queue.IllegalTransition as exc:
        raise ToolRefusal(C.DENY_BUSINESS_RULE, str(exc))
    wf_queue.schedule_next(ctx.db, item, minutes, now=ctx.now)
    return {"work_item_id": item.id, "state": item.state,
            "next_action_in_minutes": minutes}


@register("employee.mark_exhausted")
def employee_mark_exhausted(ctx, args, auth) -> Dict:
    detail = wf_memory.sanitize(args.get("detail"))[:255]
    return _terminal(ctx, C.EXHAUSTED, "no response after every permitted touch",
                     detail)


@register("employee.mark_qualified")
def employee_mark_qualified(ctx, args, auth) -> Dict:
    """Terminal FOR THIS EMPLOYEE. The relationship carries on elsewhere.

    Which is why a qualified record raises a handoff as well: closing the work
    item without telling anybody would be a qualified lead sitting in a
    terminal state that nobody is watching.
    """
    item = _work_item(ctx)
    lead = (ctx.db.query(Lead)
            .filter(Lead.id == item.subject_id,
                    Lead.organization_id == ctx.organization_id).first())
    summary = wf_memory.sanitize(args.get("summary"))[:2000]
    facts = args.get("facts") or {}
    if isinstance(facts, dict) and lead is not None:
        for key, value in list(facts.items())[:25]:
            wf_memory.remember(ctx.db, ctx.employee, str(key)[:120],
                               str(value)[:500], scope=wf_memory.SCOPE_LEAD,
                               scope_id=lead.id,
                               source=wf_memory.SOURCE_CONTACT)
    row = wf_handoff.create(
        ctx.db, employee=ctx.employee, lead=lead, work_item=item,
        reason_code="qualification_threshold", summary=summary,
        recommended_action="Pick this qualified record up.",
        known_facts=["%s: %s" % (k, v) for k, v in list(facts.items())[:10]]
        if isinstance(facts, dict) else [],
        priority="high", run_id=getattr(ctx.run, "id", None))
    out = _terminal(ctx, C.QUALIFIED, "qualified", summary[:255])
    out.update({"handoff_id": row.id, "_target_type": "lead",
                "_target_id": getattr(lead, "id", None)})
    return out
