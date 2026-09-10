"""THE BUTTONS. Every one of them does something, and none of them do anything real.

THE STANDARD THIS FILE IS HELD TO

    "If the Demo shows an action, it should perform an appropriate safe
     action. Do not create fake controls that silently do nothing."

So each action below writes to the demonstration tenant's REAL tables — the
same `leads`, `messages`, `booking_links` and `opportunities` rows the product
reads everywhere else. Moving a deal moves the deal. Booking an appointment
creates the appointment. Completing a next action clears it and writes a
timeline entry. The panels then render the changed world because they are
reading the same rows.

WHAT IS SIMULATED, AND WHERE THE LINE IS

Exactly one thing: the provider call. A demonstration send writes the message
row a real send would write, marked `SIMULATED-DEMO-…` in the column the
carrier's own id would occupy, and never constructs a Twilio or Resend client.
The outbound services refuse a demonstration organisation outright, on the
server, so this is belt and braces rather than the only guard.

That line is drawn at the provider and nowhere else on purpose. Simulating more
than the provider — faking the state change, faking the queue — would be
demonstrating a mock, and the prospect would be watching software that does not
exist. Simulating less would make a stranger's phone ring during a sales
meeting.

TARGETS ARE SEED KEYS, NOT IDS

An action names `terrence` or `cordova`, and this module resolves that to the
deterministic seeded id inside THIS environment. A caller therefore cannot
address a row by id at all, which means no demo action can be pointed at a real
customer's record even by a caller who has read the schema. The demo guard on
the tenant is the second answer to the same question; this is the first.
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.demo_suite_models import DemoActionEvent, DemoEnvironment
from app.models.models import (BookingLink, EmailMessage, EngagementTemperature,
                               Lead, Message, Organization, User)
from app.models.sales_models import (ALL_STAGES, OPPORTUNITY_STAGES,
                                     STAGE_LABELS, BrandSalesOrg, Membership,
                                     Opportunity, OpportunityEvent,
                                     SCOPE_BRAND_SALES_ORG)
from app.services import demo_environment as denv
from app.services import demo_guard

log = logging.getLogger(__name__)

# Every action the Demo Suite exposes. A request naming anything else is
# refused rather than ignored — a control that posts an unknown action and gets
# a 200 back is the "button that silently does nothing" this file exists to
# prevent, wearing a network request as a disguise.
ACTIONS = (
    "qualify_lead", "qualify_all", "send_sms", "simulate_reply",
    "ai_follow_up", "approve_draft", "book_appointment", "move_stage",
    "complete_task",
)


def _new_id(env: DemoEnvironment, kind: str) -> str:
    """A prefixed id for a row created DURING a demonstration.

    Prefixed for the same reason the seed's ids are: reset finds it. Not
    deterministic, because two presenters may legitimately send two messages to
    the same demo lead and neither should overwrite the other.
    """
    return denv.canonical_id(env, kind, secrets.token_hex(5))


# ─────────────────────────────────────────────────────────────────────────────
# RESOLUTION — a target is a seed key, never an id
# ─────────────────────────────────────────────────────────────────────────────

def _lead(db: Session, env: DemoEnvironment, org: Organization,
          key: str) -> Lead:
    if not key:
        raise HTTPException(status_code=400, detail="No lead was named.")
    row = (db.query(Lead)
           .filter(Lead.id == denv.canonical_id(env, "lead", key),
                   Lead.organization_id == org.id)
           .first())
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="That record is not part of this demonstration.")
    return row


def _opportunity(db: Session, env: DemoEnvironment, brand: BrandSalesOrg,
                 key: str) -> Opportunity:
    if not key:
        raise HTTPException(status_code=400, detail="No deal was named.")
    row = (db.query(Opportunity)
           .filter(Opportunity.id == denv.canonical_id(env, "opp", key),
                   Opportunity.brand_sales_org_id == brand.id)
           .first())
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="That deal is not part of this demonstration.")
    return row


def _owner(db: Session, lead: Lead, org: Organization) -> User:
    if lead.assigned_to_id:
        u = db.query(User).filter(User.id == lead.assigned_to_id).first()
        if u is not None:
            return u
    u = (db.query(User)
         .filter(User.organization_id == org.id, User.role == "advisor")
         .order_by(User.full_name).first())
    if u is None:
        raise HTTPException(
            status_code=409,
            detail="This demonstration environment has no advisor to act as. "
                   "Rebuild it from God Mode → Demo Suite.")
    return u


# ─────────────────────────────────────────────────────────────────────────────
# QUALIFICATION
#
# HONEST ABOUT WHAT IT IS. The production engine — `app/services/qualification`
# — scores against the organisation's own configured rules and is driven from
# an authorised lead query belonging to the person asking. A presenter is not a
# member of the demonstration workspace (and must not be, or demo access would
# be workspace access), so that query would correctly return nothing for them.
#
# So this is a demonstration-scoped evaluation that follows the SAME ORDER the
# real engine follows — hard exclusions first, then signal, then a band — over
# the same real lead rows, and reports its exclusions rather than hiding them.
# It is labelled `demo` in its own response so no screen can present it as the
# production engine's output.
# ─────────────────────────────────────────────────────────────────────────────

BAND_URGENT = "urgent"
BAND_HIGH = "high"
BAND_STANDARD = "standard"
BAND_NURTURE = "nurture"
BAND_EXCLUDED = "excluded"

BAND_ORDER = {BAND_URGENT: 0, BAND_HIGH: 1, BAND_STANDARD: 2,
              BAND_NURTURE: 3, BAND_EXCLUDED: 9}


def evaluate_lead(lead: Lead, now: Optional[datetime] = None) -> Dict[str, Any]:
    """One lead's decision. Exclusions BEFORE scoring, always.

    The order is the point. A lead that must not be contacted can never be
    scored past a compliance rule into the top of somebody's queue, because
    scoring never runs for it.
    """
    now = now or datetime.utcnow()

    # ── hard exclusions ──
    if (lead.status or "").lower() == "dnc":
        return {"band": BAND_EXCLUDED, "score": None,
                "reason": "Do-not-contact — this person replied STOP.",
                "excluded": True, "factors": []}
    if not lead.phone and not lead.email:
        return {"band": BAND_EXCLUDED, "score": None,
                "reason": "No usable phone number or email address on record.",
                "excluded": True, "factors": []}
    if (lead.contact_channel or "") == "email_only" and not lead.email:
        return {"band": BAND_EXCLUDED, "score": None,
                "reason": "Email-only contact with no email address.",
                "excluded": True, "factors": []}

    # ── signal ──
    score = 40
    factors: List[str] = []

    tier = (lead.tier or "").lower()
    if tier in ("imminent", "at_need"):
        score += 30
        factors.append("Urgent enquiry type (%s)" % tier.replace("_", "-"))
    elif tier == "pre_need":
        score += 5
        factors.append("Planning enquiry")

    status = (lead.status or "").lower()
    if status == "replied":
        score += 25
        factors.append("Has replied")
    elif status == "new":
        score += 15
        factors.append("Never contacted")
    elif status == "booked":
        score -= 20
        factors.append("Already booked")

    last = lead.last_contact_date
    if last is None:
        score += 10
        factors.append("No contact on record — first response outstanding")
    else:
        age_days = max((now - last).days, 0)
        if age_days <= 2:
            score += 10
            factors.append("Active in the last 48 hours")
        elif age_days >= 365:
            score -= 10
            factors.append("Dormant for over a year")
        elif age_days >= 30:
            score -= 5
            factors.append("Quiet for over a month")

    temp = getattr(lead.engagement_temperature, "value",
                   lead.engagement_temperature)
    if temp == "hot":
        score += 15
        factors.append("Engagement is hot")
    elif temp == "cold":
        score -= 5
        factors.append("Engagement is cold")

    score = max(0, min(100, score))
    if score >= 80:
        band, reason = BAND_URGENT, "Needs a person today."
    elif score >= 60:
        band, reason = BAND_HIGH, "Worth a call this week."
    elif score >= 40:
        band, reason = BAND_STANDARD, "Keep in the working queue."
    else:
        band, reason = BAND_NURTURE, "Long-term nurture."

    return {"band": band, "score": score, "reason": reason,
            "excluded": False, "factors": factors}


def _apply_evaluation(lead: Lead, decision: Dict[str, Any]) -> None:
    """Write the decision back onto the real lead row.

    This is what makes qualification an ACTION rather than a report: the record
    itself carries the conclusion afterwards, so the queue, the lead detail and
    anybody who opens it later all agree.
    """
    if decision["excluded"]:
        lead.ai_lead_quality_note = "Excluded: %s" % decision["reason"]
        return
    lead.ai_lead_quality_note = "%s (%d/100) — %s%s" % (
        decision["band"].title(), decision["score"], decision["reason"],
        ("  Signals: " + "; ".join(decision["factors"])
         if decision["factors"] else ""))
    if decision["band"] == BAND_URGENT:
        lead.engagement_temperature = EngagementTemperature.HOT
    elif decision["band"] == BAND_NURTURE:
        lead.engagement_temperature = EngagementTemperature.COLD


# ─────────────────────────────────────────────────────────────────────────────
# THE ACTIONS
# ─────────────────────────────────────────────────────────────────────────────

def _event(db: Session, env: DemoEnvironment, user: User, action: str,
           scenario_key: Optional[str], step_key: Optional[str],
           target_type: str, target_id: str, provider: Optional[str],
           detail: str) -> DemoActionEvent:
    return denv.record_event(
        db, env, user, action, scenario_key=scenario_key, step_key=step_key,
        target_type=target_type, target_id=target_id,
        simulated_provider=provider, detail=detail, commit=False)


def qualify_lead(db, env, org, brand, user, params, scenario_key, step_key):
    lead = _lead(db, env, org, params.get("target"))
    decision = evaluate_lead(lead)
    _apply_evaluation(lead, decision)
    _event(db, env, user, "qualify_lead", scenario_key, step_key, "lead",
           lead.id, "ai",
           "%s → %s" % (lead.first_name, decision["band"]))
    return {"engine": "demo", "lead": lead.id,
            "name": "%s %s" % (lead.first_name, lead.last_name),
            "decision": decision,
            "narration": ("%s %s is %s — %s"
                          % (lead.first_name, lead.last_name,
                             decision["band"], decision["reason"]))}


def qualify_all(db, env, org, brand, user, params, scenario_key, step_key):
    leads = (db.query(Lead)
             .filter(Lead.organization_id == org.id)
             .order_by(Lead.last_name).all())
    bands: Dict[str, int] = {}
    excluded: List[Dict[str, str]] = []
    for lead in leads:
        decision = evaluate_lead(lead)
        _apply_evaluation(lead, decision)
        bands[decision["band"]] = bands.get(decision["band"], 0) + 1
        if decision["excluded"]:
            excluded.append({
                "name": "%s %s" % (lead.first_name, lead.last_name),
                "reason": decision["reason"]})
    _event(db, env, user, "qualify_all", scenario_key, step_key, "workspace",
           org.id, "ai", "%d leads evaluated" % len(leads))
    return {"engine": "demo", "evaluated": len(leads), "bands": bands,
            # REPORTED, NEVER HIDDEN. The exclusion list is the part of this
            # that builds trust in the room.
            "excluded": excluded,
            "narration": ("%d leads evaluated. %d need a person today. %d were "
                          "excluded and every one of them says why."
                          % (len(leads), bands.get(BAND_URGENT, 0),
                             len(excluded)))}


_FIRST_RESPONSE = (
    "Hi {first}, this is {advisor} at {org}. I saw your enquiry come through — "
    "would a short call today be helpful, or would you rather I send the "
    "options across first?")

_REENGAGE = (
    "Hi {first}, {advisor} here at {org}. It has been a while since we last "
    "spoke, and I wanted to check whether planning is something you are still "
    "thinking about. No obligation either way.")


def send_sms(db, env, org, brand, user, params, scenario_key, step_key):
    lead = _lead(db, env, org, params.get("target"))
    # THE GUARD, AGAIN, HERE. The outbound service already refuses a demo
    # organisation — but this function does not call the outbound service, so
    # the refusal that protects a real tenant would never run. Asserting the
    # tenant is a demo tenant at the point of writing is what makes this
    # function safe on its own terms rather than by reference to another one.
    demo_guard.assert_demo_org(org)

    advisor = _owner(db, lead, org)
    dormant = (lead.last_contact_date is not None
               and (datetime.utcnow() - lead.last_contact_date).days >= 180)
    template = _REENGAGE if dormant else _FIRST_RESPONSE
    body = template.format(first=lead.first_name,
                           advisor=(advisor.full_name or "").split(" ")[0],
                           org=org.name)

    msg = Message(
        id=_new_id(env, "msg"),
        lead_id=lead.id, sender_id=advisor.id, body=body,
        # THE SIMULATION IS DECLARED IN THE PROVIDER'S OWN COLUMN. Anybody
        # reading this row in a database shell can see no carrier saw it.
        twilio_sid="SIMULATED-DEMO-%s" % secrets.token_hex(6),
        twilio_status="delivered", delivery_status="delivered",
        send_state="delivered", sent_at=datetime.utcnow(),
    )
    db.add(msg)
    if (lead.status or "") in ("new", ""):
        lead.status = "sent"
    lead.last_contact_date = datetime.utcnow()
    _event(db, env, user, "send_sms", scenario_key, step_key, "lead", lead.id,
           "sms", body[:200])
    return {"simulated": True, "channel": "sms", "lead": lead.id,
            "body": body, "sent_at": msg.sent_at.isoformat(),
            "narration": ("The first response is on the thread and %s is now "
                          "Contacted. No carrier was called."
                          % lead.first_name)}


_REPLIES = {
    "terrence": "Thank you for getting back to me so quickly. Yes please — "
                "this afternoon would work.",
    "yusuf": "I had actually started looking at this again. What would the "
             "next step be?",
}
_DEFAULT_REPLY = "Thanks for reaching out. Yes, I would like to know more."


def simulate_reply(db, env, org, brand, user, params, scenario_key, step_key):
    lead = _lead(db, env, org, params.get("target"))
    demo_guard.assert_demo_org(org)
    advisor = _owner(db, lead, org)
    key = (lead.id or "").rsplit("-", 1)[-1]
    body = _REPLIES.get(key, _DEFAULT_REPLY)

    db.add(Message(
        id=_new_id(env, "msg"),
        lead_id=lead.id, sender_id=advisor.id,
        # Inbound messages are marked in the body because `messages` has no
        # direction column — the same shape the existing demo scenarios use,
        # rather than adding a column to a live table for a demonstration.
        body="[FROM %s] %s" % (lead.first_name, body),
        twilio_sid="SIMULATED-DEMO-INBOUND-%s" % secrets.token_hex(5),
        twilio_status="received", delivery_status="delivered",
        send_state="delivered", sent_at=datetime.utcnow(),
    ))
    lead.status = "replied"
    lead.engagement_temperature = EngagementTemperature.HOT
    lead.last_contact_date = datetime.utcnow()
    _event(db, env, user, "simulate_reply", scenario_key, step_key, "lead",
           lead.id, "sms", body[:200])
    return {"simulated": True, "lead": lead.id, "body": body,
            "narration": ("%s replied. The record went hot on its own and it "
                          "is now at the top of the queue."
                          % lead.first_name)}


_DRAFTS = {
    "terrence": "Thank you — I have 2pm and 4pm free today. I will hold 2pm "
                "for you unless you tell me otherwise, and I will bring the "
                "two options we can talk through.",
    "alice": "Good question. A pre-need plan is priced by what is included "
             "rather than by a single figure, so the honest answer is a "
             "fifteen-minute call where I can show you the three levels and "
             "what each one covers. Would Thursday morning suit?",
    "yusuf": "Of course. The next step is a short call — no obligation — "
             "where I can talk you through what has changed and what it "
             "would cost today. Would later this week work?",
}
_DEFAULT_DRAFT = ("Thank you for coming back to me. The next step is a short "
                  "call at whatever time suits you — would later this week "
                  "work?")


def ai_follow_up(db, env, org, brand, user, params, scenario_key, step_key):
    """Draft a reply and STOP. The gate is the product decision being shown.

    The draft is recorded as a demo event rather than as a `Message` row on
    purpose: a draft is not a message, and writing one into the messages table
    would make the demonstration claim something the product does not do. The
    conversation panel renders pending drafts from these events, and
    `approve_draft` is what turns one into a message.
    """
    lead = _lead(db, env, org, params.get("target"))
    demo_guard.assert_demo_org(org)
    key = (lead.id or "").rsplit("-", 1)[-1]
    draft = _DRAFTS.get(key, _DEFAULT_DRAFT)
    _event(db, env, user, "ai_follow_up", scenario_key, step_key, "lead",
           lead.id, "ai", draft)
    return {"simulated": True, "lead": lead.id, "draft": draft,
            "requires_approval": True,
            "narration": ("A reply is drafted and waiting for the advisor. "
                          "Nothing has been sent — the confirmation gate is "
                          "deliberate.")}


def approve_draft(db, env, org, brand, user, params, scenario_key, step_key):
    """The advisor agrees, and only then does the draft become a message."""
    lead = _lead(db, env, org, params.get("target"))
    demo_guard.assert_demo_org(org)
    pending = (db.query(DemoActionEvent)
               .filter(DemoActionEvent.environment_id == env.id,
                       DemoActionEvent.action == "ai_follow_up",
                       DemoActionEvent.target_id == lead.id)
               .order_by(DemoActionEvent.occurred_at.desc())
               .first())
    if pending is None:
        raise HTTPException(
            status_code=409,
            detail="There is no drafted reply waiting on this record. Draft "
                   "one first.")
    advisor = _owner(db, lead, org)
    db.add(Message(
        id=_new_id(env, "msg"),
        lead_id=lead.id, sender_id=advisor.id, body=pending.detail or "",
        twilio_sid="SIMULATED-DEMO-%s" % secrets.token_hex(6),
        twilio_status="delivered", delivery_status="delivered",
        send_state="delivered", sent_at=datetime.utcnow(),
    ))
    lead.last_contact_date = datetime.utcnow()
    # The draft has been consumed. Deleting the event rather than leaving it
    # is what stops the panel showing a draft that was already sent.
    db.delete(pending)
    _event(db, env, user, "approve_draft", scenario_key, step_key, "lead",
           lead.id, "sms", (pending.detail or "")[:200])
    return {"simulated": True, "lead": lead.id,
            "narration": "Approved and on the thread. That is the gate."}


def book_appointment(db, env, org, brand, user, params, scenario_key, step_key):
    lead = _lead(db, env, org, params.get("target"))
    demo_guard.assert_demo_org(org)
    advisor = _owner(db, lead, org)

    # The next weekday at 10am local. Deliberately not "now plus an hour": an
    # appointment at 19:40 on a Sunday reads as fake to anybody in the room.
    when = (datetime.utcnow() + timedelta(days=1)).replace(
        hour=10, minute=0, second=0, microsecond=0)
    while when.weekday() >= 5:
        when += timedelta(days=1)

    link = BookingLink(
        id=_new_id(env, "appt"),
        token=_new_id(env, "token"),
        lead_id=lead.id, user_id=advisor.id,
        status="booked", booked_time=when,
        appt_label="Pre-Need Planning Consultation", appt_duration=60,
        expires_at=datetime.utcnow() + timedelta(days=14),
    )
    db.add(link)
    lead.status = "booked"
    _event(db, env, user, "book_appointment", scenario_key, step_key, "lead",
           lead.id, "calendar",
           "%s at %s" % (lead.first_name, when.isoformat()))
    return {"lead": lead.id, "appointment_id": link.id,
            "when": when.isoformat(), "advisor": advisor.full_name,
            "narration": ("Consultation booked with %s for %s. That is the "
                          "outcome the whole path exists for."
                          % (advisor.full_name, when.strftime("%A at %I:%M %p")))}


def move_stage(db, env, org, brand, user, params, scenario_key, step_key):
    opp = _opportunity(db, env, brand, params.get("target"))
    demo_guard.assert_demo_brand(brand)
    to_stage = (params.get("to_stage") or "").strip()
    if to_stage not in ALL_STAGES:
        raise HTTPException(
            status_code=400,
            detail="Unknown stage. Valid stages: %s." % ", ".join(ALL_STAGES))
    before = opp.stage
    if before == to_stage:
        return {"unchanged": True, "stage": to_stage,
                "narration": "%s is already in %s."
                             % (opp.company_name, STAGE_LABELS.get(to_stage))}
    opp.stage = to_stage
    opp.stage_changed_at = datetime.utcnow()
    db.add(OpportunityEvent(
        id=_new_id(env, "oppev"),
        opportunity_id=opp.id, event_type="stage_changed",
        summary="Moved to %s" % STAGE_LABELS.get(to_stage, to_stage),
        detail="Moved during a product demonstration.",
        actor_user_id=opp.owner_user_id, occurred_at=datetime.utcnow(),
    ))
    _event(db, env, user, "move_stage", scenario_key, step_key, "opportunity",
           opp.id, None, "%s: %s → %s" % (opp.company_name, before, to_stage))
    return {"opportunity": opp.id, "from": before, "to": to_stage,
            "narration": ("%s moved from %s to %s. The clock reset and the "
                          "timeline recorded it — the record was not "
                          "re-created."
                          % (opp.company_name, STAGE_LABELS.get(before, before),
                             STAGE_LABELS.get(to_stage, to_stage)))}


def complete_task(db, env, org, brand, user, params, scenario_key, step_key):
    opp = _opportunity(db, env, brand, params.get("target"))
    demo_guard.assert_demo_brand(brand)
    done = opp.next_action
    if not done:
        return {"unchanged": True,
                "narration": "%s has no outstanding next action."
                             % opp.company_name}
    opp.next_action = None
    opp.next_action_due_at = None
    db.add(OpportunityEvent(
        id=_new_id(env, "oppev"),
        opportunity_id=opp.id, event_type="next_action_completed",
        summary="Completed: %s" % done,
        actor_user_id=opp.owner_user_id, occurred_at=datetime.utcnow(),
    ))
    _event(db, env, user, "complete_task", scenario_key, step_key,
           "opportunity", opp.id, None, done)
    return {"opportunity": opp.id, "completed": done,
            "narration": ("Done, and off the overdue list. The deal carries "
                          "the next thing that has to happen — that is what "
                          "makes it a queue.")}


_DISPATCH = {
    "qualify_lead": qualify_lead,
    "qualify_all": qualify_all,
    "send_sms": send_sms,
    "simulate_reply": simulate_reply,
    "ai_follow_up": ai_follow_up,
    "approve_draft": approve_draft,
    "book_appointment": book_appointment,
    "move_stage": move_stage,
    "complete_task": complete_task,
}


def run(db: Session, env: DemoEnvironment, org: Organization,
        brand: BrandSalesOrg, user: User, action: str,
        params: Optional[Dict[str, Any]] = None,
        scenario_key: Optional[str] = None,
        step_key: Optional[str] = None) -> Dict[str, Any]:
    """Perform one demo action. Refuses anything not in the dispatch table.

    THE REFUSAL MATTERS. A control that posts an unrecognised action and is
    answered 200 is a dead button with a network request attached, which is
    exactly the failure this whole surface is judged on.
    """
    fn = _DISPATCH.get((action or "").strip())
    if fn is None:
        raise HTTPException(
            status_code=400,
            detail="Unknown demo action %r. Available: %s."
                   % (action, ", ".join(sorted(_DISPATCH))))
    # BOTH TENANTS, PROVED, ON EVERY ACTION. Not once at build time — a row
    # that named a real organisation must be unusable at the moment of use.
    demo_guard.assert_demo_org(org)
    demo_guard.assert_demo_brand(brand)

    out = fn(db, env, org, brand, user, params or {}, scenario_key, step_key)
    db.commit()
    out["action"] = action
    return out
