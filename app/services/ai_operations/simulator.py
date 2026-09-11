"""WHOLE LIFECYCLES, DRIVEN THROUGH THE REAL ENGINE.

WHAT MAKES THIS A PROOF RATHER THAN A DEMO. Every step below calls the same
functions a live employee would: `orchestrator.send_message`,
`inbound.route`, `appointments.book_appointment`, `followup.run_due_actions`,
`handoff.transfer_to_human`. Nothing is stubbed except the last inch — the
provider — and even that is reached through the normal resolution path. A
scenario that bypassed a gate to make a story work would be a scenario that
proves the story rather than the system.

TIME IS PASSED EXPLICITLY. Follow-ups scheduled for tomorrow are executed by
handing the worker a `now` in the future rather than by sleeping or by
rewriting the row. The gate chain re-runs at that simulated instant, which is
precisely the behaviour worth proving: eligibility is re-decided then, not
when the follow-up was queued.

EVERY STEP RECORDS WHAT ACTUALLY HAPPENED, including its refusal code. A
scenario "passes" when the lifecycle reached the ending it was written to
reach — not when nothing errored. `no_response` ending in `exhausted` is a
pass; `opt_out` ending in an appointment would be a catastrophic failure that
a green test run must not hide.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.ai_operations_models import AIConversationThread
from app.services.ai_operations import (appointments, channels, continuity,
                                        followup, handoff, inbound,
                                        orchestrator, profiles)
from app.services.ai_operations import constants as C
from app.services.ai_operations import stop as stop_controls

_log = logging.getLogger(__name__)


class Run:
    """A scenario's transcript: what was attempted and what came back."""

    def __init__(self, key: str, profile: profiles.Profile):
        self.key = key
        self.profile = profile
        self.steps: List[Dict[str, Any]] = []
        self.thread: Optional[AIConversationThread] = None

    def step(self, name: str, result=None, **extra) -> "Run":
        entry: Dict[str, Any] = {"step": name}
        if result is not None:
            entry["ok"] = bool(getattr(result, "ok", False))
            gate = getattr(result, "gate", None)
            if gate is not None:
                entry["denial_code"] = gate.denial_code
                entry["denial_reason"] = gate.denial_reason
                entry["decided_by"] = gate.decided_by
                entry["simulated"] = gate.simulated
            comm = getattr(result, "communication", None)
            if comm is not None:
                entry["communication_state"] = comm.state
                entry["channel"] = comm.channel
                entry["provider"] = comm.provider
        entry.update(extra)
        self.steps.append(entry)
        return self

    def report(self) -> Dict[str, Any]:
        thread = self.thread
        return {
            "scenario": self.key,
            "profile": self.profile.key,
            "organization_id": self.profile.organization.id,
            "steps": self.steps,
            "thread": ({
                "thread_id": thread.id,
                "state": thread.state,
                "status": thread.status,
                "stop_reason": thread.stop_reason,
                "channels_used": continuity.channels_used(thread),
                "outbound": int(thread.outbound_count or 0),
                "inbound": int(thread.inbound_count or 0),
                "appointment_ref": thread.appointment_ref,
                "human_owner_user_id": thread.human_owner_user_id,
            } if thread is not None else None),
        }


def _inbound(db: Session, profile: profiles.Profile, lead, body: str, *,
             channel: str = C.CHANNEL_SMS, event_id: Optional[str] = None
             ) -> Dict[str, Any]:
    """A reply arrives, through the real inbound router."""
    to_address = (profile.advisor.twilio_phone_number
                  if channel == C.CHANNEL_SMS
                  else profile.organization.from_email)
    from_address = (lead.phone if channel == C.CHANNEL_SMS else lead.email)
    return inbound.route(
        db, provider="simulated", channel=channel,
        provider_event_id=(event_id or "sim-%s-%s"
                           % (lead.id, len(body or ""))),
        from_address=from_address or "", to_address=to_address or "",
        body=body)


# ═══════════════════════════════════════════════════════════════════════════
# REACTIVATION — the first operational proof
# ═══════════════════════════════════════════════════════════════════════════

def reactivation(db: Session, profile: profiles.Profile, *,
                 scenario: str = "appointment",
                 now: Optional[datetime] = None) -> Dict[str, Any]:
    """One dormant contact, from first touch to an ending."""
    now = now or datetime.utcnow()
    ctx = profile.ctx
    run = Run("reactivation:%s" % scenario, profile)

    lead = {
        "appointment": profile.lead_by_key("books"),
        "opt_out": profile.lead_by_key("optout"),
        "no_response": profile.lead_by_key("silent"),
        "handoff": profile.lead_by_key("human"),
        "blocked": profile.lead_by_key("blocked"),
    }.get(scenario)
    if lead is None:
        raise ValueError("unknown reactivation scenario %r" % scenario)

    objective = profile.notes.get("objective")
    first = orchestrator.send_message(
        db, ctx, subject_id=lead.id, objective=objective, now=now,
        body=("Hi %s, this is %s at %s. We spoke a while ago about planning "
              "ahead. Is that still something you'd like to look at?"
              % (lead.first_name or "there", ctx.name,
                 profile.organization.name)))
    run.step("first_outreach", first)
    run.thread = first.gate.thread if first.gate else None

    # ── the record the engine must refuse ─────────────────────────────────
    if scenario == "blocked":
        run.step("assert_refused", None,
                 refused=not first.ok,
                 denial_code=(first.gate.denial_code if first.gate else None),
                 eligibility=(first.gate.eligibility.as_dict()
                              if first.gate and first.gate.eligibility
                              else None))
        return run.report()

    thread = run.thread
    if thread is None:
        return run.report()

    # ── they opt out ──────────────────────────────────────────────────────
    if scenario == "opt_out":
        routed = _inbound(db, profile, lead, "STOP")
        run.step("inbound_stop", None, routed=routed)
        db.refresh(thread)
        again = orchestrator.send_message(
            db, ctx, subject_id=lead.id, thread=thread, now=now,
            body="Just checking in once more.")
        run.step("attempt_after_stop", again)
        run.step("assert_stopped", None,
                 stop_reason=thread.stop_reason,
                 refused_after_stop=not again.ok,
                 denial_code=(again.gate.denial_code if again.gate else None))
        return run.report()

    # ── they never answer ─────────────────────────────────────────────────
    if scenario == "no_response":
        scheduled = followup.schedule(
            db, ctx, thread=thread, operation=C.OP_SEND_MESSAGE,
            when=now + timedelta(days=3), channel=C.CHANNEL_SMS,
            payload={"body": "Following up in case the timing is better now."},
            reason="no response to the first touch")
        run.step("schedule_followup", scheduled)
        later = now + timedelta(days=3, minutes=1)
        summary = followup.run_due_actions(db, now=later, limit=10,
                                           organization_id=ctx.organization_id)
        run.step("run_due_followups", None, summary=summary)
        closed = orchestrator.record_outcome(
            db, ctx, subject_id=lead.id, thread=thread, outcome="exhausted",
            summary="No response after every permitted touch.",
            stop_reason=C.STOP_EXHAUSTED)
        run.step("close_exhausted", closed)
        db.refresh(thread)
        run.step("assert_exhausted", None, stop_reason=thread.stop_reason,
                 state=thread.state)
        return run.report()

    # ── they ask for a person ─────────────────────────────────────────────
    if scenario == "handoff":
        routed = _inbound(db, profile, lead,
                          "Can someone actually call me about this?")
        run.step("inbound_request_human", None, routed=routed)
        db.refresh(thread)
        handed = handoff.transfer_to_human(
            db, ctx, thread=thread, reason_code="human_requested",
            summary=("%s asked to speak to a person about planning ahead."
                     % (lead.first_name or "The contact")),
            known_facts=["Dormant record, last contact over a year ago",
                         "Replied to the first outreach within minutes"],
            open_questions=["What time of day suits them for a call?"],
            recommended_action="Call them back today.")
        run.step("transfer_to_human", handed)
        owner = stop_controls.take_over(db, thread,
                                        user_id=profile.advisor.id,
                                        reason_code="human_requested",
                                        handoff_ref=thread.handoff_ref)
        run.step("human_takes_over", None, ownership_id=owner.id)
        blocked = orchestrator.send_message(
            db, ctx, subject_id=lead.id, thread=thread, now=now,
            body="While you wait, here is a link.")
        run.step("attempt_while_human_owns", blocked)
        run.step("assert_human_owned", None,
                 refused=not blocked.ok,
                 denial_code=(blocked.gate.denial_code if blocked.gate
                              else None),
                 handoff_ref=thread.handoff_ref)
        return run.report()

    # ── they reply, qualify and book ──────────────────────────────────────
    routed = _inbound(db, profile, lead,
                      "Yes, still interested. What times do you have?")
    run.step("inbound_interested", None, routed=routed)
    db.refresh(thread)

    replied = orchestrator.respond_to_inbound(
        db, ctx, thread=thread, now=now,
        body="Good to hear. I can offer a couple of times this week.")
    run.step("reply_on_same_channel", replied)

    qualified = orchestrator.update_lead(
        db, ctx, subject_id=lead.id, thread=thread,
        facts={"ai_lead_quality_note":
               "Confirmed still interested; asked for appointment times."})
    run.step("record_qualification", qualified)

    availability = appointments.list_availability(db, ctx, subject_id=lead.id,
                                                  days_ahead=10,
                                                  now_utc=now)
    offered = appointments.offered_times(availability, limit=3)
    run.step("read_availability", None,
             status=availability.get("availability_status"),
             slot_count=len(availability.get("slots") or []),
             offered=offered, reason=availability.get("reason"))

    if offered:
        booked = appointments.book_appointment(
            db, ctx, thread=thread, starts_at=offered[0]["starts_at"],
            now_utc=now)
        run.step("book_appointment", booked)
        # THE DUPLICATE-BOOKING PROOF, inline in the lifecycle rather than
        # only in a unit test: the same slot, asked for twice.
        again = appointments.book_appointment(
            db, ctx, thread=thread, starts_at=offered[0]["starts_at"],
            now_utc=now)
        run.step("book_same_slot_again", again,
                 duplicate_suppressed=(not again.ok
                                       and again.gate is not None
                                       and again.gate.duplicate))
        confirmation = orchestrator.send_email(
            db, ctx, subject_id=lead.id, thread=thread, now=now,
            subject_line="Your appointment is confirmed",
            body="Confirming %s. Reply here if you need to change it."
                 % offered[0]["label"], expect_reply=False)
        run.step("confirmation_email_channel_switch", confirmation)
    else:
        handed = handoff.transfer_to_human(
            db, ctx, thread=thread,
            reason_code="appointment_requires_human",
            summary="Interested, but no calendar availability was offered.",
            recommended_action="Offer times by hand.")
        run.step("handoff_no_availability", handed)

    db.refresh(thread)
    run.step("assert_outcome", None, state=thread.state,
             appointment_ref=thread.appointment_ref,
             channels_used=continuity.channels_used(thread))
    return run.report()


# ═══════════════════════════════════════════════════════════════════════════
# FULL-LIFECYCLE ENERGY — one engine, two segments
# ═══════════════════════════════════════════════════════════════════════════

def full_lifecycle(db: Session, profile: profiles.Profile, *,
                   now: Optional[datetime] = None) -> Dict[str, Any]:
    """New lead, dormant lead and inbound, through one employee.

    The same function runs for B2B and for residential. It does not branch on
    the segment anywhere, which is the claim being proven.
    """
    now = now or datetime.utcnow()
    ctx = profile.ctx
    run = Run("full_lifecycle:%s" % profile.notes.get("segment", "?"), profile)

    new_lead = profile.lead_by_key("new")
    dormant = profile.lead_by_key("dormant")
    inbound_lead = profile.lead_by_key("inbound")

    # ── 1. a new enquiry, by SMS ──────────────────────────────────────────
    first = orchestrator.send_message(
        db, ctx, subject_id=new_lead.id, now=now,
        objective=profile.notes.get("objective"),
        body="Thanks for getting in touch — can I ask two quick questions to "
             "point you the right way?")
    run.step("new_lead_first_touch", first)
    thread = first.gate.thread if first.gate else None
    run.thread = thread

    if thread is not None:
        routed = _inbound(db, profile, new_lead,
                          "Sure. We're looking at options for next quarter.")
        run.step("new_lead_reply", None, routed=routed)
        db.refresh(thread)
        qualified = orchestrator.update_lead(
            db, ctx, subject_id=new_lead.id, thread=thread,
            facts={"ai_lead_quality_note": "Timeline: next quarter."})
        run.step("qualify", qualified)

        # ── 2. the channel switch, with continuity ───────────────────────
        emailed = orchestrator.send_email(
            db, ctx, subject_id=new_lead.id, thread=thread, now=now,
            subject_line="The options we discussed",
            body="Here is a summary of what we talked about.")
        run.step("switch_to_email", emailed,
                 channels_used=continuity.channels_used(thread))

        # ── 3. voice: authorized, and still refused or simulated ─────────
        called = orchestrator.initiate_call(
            db, ctx, subject_id=new_lead.id, thread=thread, now=now,
            purpose="Answer the pricing question by phone")
        run.step("voice_call", called,
                 live_voice=channels.describe(db).get("live_voice_enabled"))

        # ── 4. booking ───────────────────────────────────────────────────
        availability = appointments.list_availability(
            db, ctx, subject_id=new_lead.id, days_ahead=10, now_utc=now)
        offered = appointments.offered_times(availability, limit=2)
        run.step("availability", None,
                 status=availability.get("availability_status"),
                 offered=offered)
        if offered:
            booked = appointments.book_appointment(
                db, ctx, thread=thread, starts_at=offered[0]["starts_at"],
                now_utc=now)
            run.step("book", booked)

        # ── 5. pipeline update and transfer ──────────────────────────────
        outcome = orchestrator.record_outcome(
            db, ctx, subject_id=new_lead.id, thread=thread,
            outcome="qualified", summary="Qualified and booked.")
        run.step("pipeline_update", outcome)

    # ── 6. a dormant record, worked on its own thread ─────────────────────
    dormant_touch = orchestrator.send_message(
        db, ctx, subject_id=dormant.id, now=now,
        objective="Re-engage a dormant account",
        body="It has been a while — is this still on your list for this year?")
    run.step("dormant_touch", dormant_touch)
    dormant_thread = dormant_touch.gate.thread if dormant_touch.gate else None
    if dormant_thread is not None:
        scheduled = followup.schedule(
            db, ctx, thread=dormant_thread, operation=C.OP_SEND_MESSAGE,
            when=now + timedelta(days=5), channel=C.CHANNEL_SMS,
            payload={"body": "Checking back as promised."},
            reason="dormant cadence")
        run.step("dormant_followup_scheduled", scheduled)
        summary = followup.run_due_actions(
            db, now=now + timedelta(days=5, minutes=1), limit=10,
            organization_id=ctx.organization_id)
        run.step("dormant_followup_executed", None, summary=summary)

    # ── 7. an inbound-first conversation ──────────────────────────────────
    cold_inbound = _inbound(db, profile, inbound_lead,
                            "Hi — saw your note, can you send details?",
                            event_id="sim-inbound-first-%s" % inbound_lead.id)
    run.step("inbound_with_no_conversation", None, routed=cold_inbound)
    opened = orchestrator.send_message(
        db, ctx, subject_id=inbound_lead.id, now=now,
        objective="Answer an inbound enquiry",
        body="Happy to help — what is the best address to send them to?")
    run.step("inbound_answered", opened)
    inbound_thread = opened.gate.thread if opened.gate else None
    if inbound_thread is not None:
        second = _inbound(db, profile, inbound_lead,
                          "Actually, please have someone call me.",
                          event_id="sim-inbound-second-%s" % inbound_lead.id)
        run.step("inbound_asks_for_person", None, routed=second)
        db.refresh(inbound_thread)
        handed = handoff.transfer_to_human(
            db, ctx, thread=inbound_thread, reason_code="human_requested",
            summary="Asked for a person after an inbound enquiry.",
            recommended_action="Call them back.")
        run.step("human_transfer", handed)

    return run.report()


# ═══════════════════════════════════════════════════════════════════════════
# THE DRIVER
# ═══════════════════════════════════════════════════════════════════════════

REACTIVATION_SCENARIOS = ("appointment", "opt_out", "no_response", "handoff",
                          "blocked")


def run_all(db: Session, *, allow_synthetic_data: bool = False,
            now: Optional[datetime] = None) -> Dict[str, Any]:
    """Every lifecycle, both segments, in one pass. Returns the whole record.

    Used by the evaluation harness and by the God console's "run the proofs"
    action. Each scenario gets a fresh look at the profile so an earlier
    scenario's stop cannot mask a later one's refusal.
    """
    # A DETERMINISTIC BUSINESS-HOURS INSTANT, not `utcnow()`.
    #
    # The contact window is a real gate and it does its job: run these
    # scenarios at two in the morning and every outbound step is correctly
    # refused. That is the right behaviour and the wrong default for a proof
    # run, because "everything was refused" would read as a broken engine
    # rather than as a working curfew. 15:00 UTC is 10:00 in the profiles'
    # America/Chicago, and the curfew is proven deliberately by its own case
    # in the evaluation harness instead of accidentally by the clock.
    now = now or datetime.utcnow().replace(hour=15, minute=0, second=0,
                                           microsecond=0)
    out: Dict[str, Any] = {"generated_at": now, "reactivation": [],
                           "lifecycle": []}

    reactivation_profile = profiles.build_reactivation(
        db, allow_synthetic_data=allow_synthetic_data)
    for scenario in REACTIVATION_SCENARIOS:
        out["reactivation"].append(
            reactivation(db, reactivation_profile, scenario=scenario,
                         now=now))

    for segment in ("b2b", "residential"):
        profile = profiles.build_full_lifecycle(
            db, segment=segment, allow_synthetic_data=allow_synthetic_data)
        out["lifecycle"].append(full_lifecycle(db, profile, now=now))

    return out
