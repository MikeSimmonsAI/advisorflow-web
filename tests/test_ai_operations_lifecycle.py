"""WHOLE LIFECYCLES, DRIVEN THROUGH THE REAL ENGINE.

A SCENARIO PASSES WHEN IT REACHED THE ENDING IT WAS WRITTEN TO REACH — not
when nothing errored. `no_response` ending in `stopped: attempts_exhausted`
is a pass; `opt_out` ending in an appointment would be a catastrophic failure
that a green test run must not hide. So every assertion below names the
expected THREAD STATE and, where there is one, the stop reason.

Nothing here is stubbed except the last inch: the profiles build synthetic
organizations with invented contacts, their employee contexts are DECLARED
rather than loaded from the workforce engine, and a declared context is
forced onto the simulated adapter by `channels.resolve` whatever any flag
says. Real gates, real state machine, real audit, no provider.
"""

from datetime import datetime, timedelta

import pytest

from app.models.ai_operations_models import (AIConversationThread,
                                             AIScheduledAction)
from app.services.ai_operations import (channels, constants as C, continuity,
                                        contracts, followup, orchestrator,
                                        profiles, simulator)
from app.services.ai_operations import stop as stop_controls


def business_hours():
    """A deterministic business-hours instant.

    15:00 UTC is 10:00 in the profiles' America/Chicago. Run these scenarios
    at two in the morning and every outbound step is correctly refused by the
    contact window — right behaviour, wrong default for a lifecycle proof,
    and the curfew has its own test in test_ai_operations_gates.py.
    """
    return datetime.utcnow().replace(hour=15, minute=0, second=0,
                                     microsecond=0)


@pytest.fixture()
def ops_enabled(monkeypatch):
    monkeypatch.setenv("AI_OPERATIONS_ENABLED", "1")
    monkeypatch.delenv("AI_OPERATIONS_LIVE_SEND", raising=False)
    monkeypatch.delenv("AI_OPERATIONS_KILL", raising=False)
    monkeypatch.delenv("AI_WORKFORCE_KILL", raising=False)
    yield
    # Both guards, every time: a profile registers a declared context with
    # `contracts` so the follow-up worker can resolve it, and a context left
    # registered is an employee that exists in the next test for no reason.
    channels.reset_adapters()
    profiles.forget_declared()


def declare(org, **overrides):
    kwargs = {
        "employee_id": "lifecycle-employee",
        "organization_id": org.id,
        "name": "Lifecycle Test Employee",
        "tool_keys": set(profiles.REACTIVATION_TOOLS),
        "channels": {C.CHANNEL_SMS, C.CHANNEL_EMAIL},
        "activation_state": "simulation",
        "status": "active",
        "timezone": "America/Chicago",
    }
    kwargs.update(overrides)
    return contracts.declare_employee_context(**kwargs)


def step(report, name):
    for entry in report["steps"]:
        if entry["step"] == name:
            return entry
    raise AssertionError("scenario never reached step %r; it ran %s"
                         % (name, [s["step"] for s in report["steps"]]))


# ═══════════════════════════════════════════════════════════════════════════
# THE REACTIVATION ENDINGS
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("scenario,expected_state,expected_stop", [
    ("appointment", C.APPOINTMENT_BOOKED, None),
    ("opt_out", C.STOPPED, C.STOP_OPT_OUT),
    ("no_response", C.STOPPED, C.STOP_EXHAUSTED),
    ("handoff", C.HUMAN_OWNED, None),
])
def test_reactivation_scenarios_reach_their_expected_endings(
        ops_enabled, db_session, scenario, expected_state, expected_stop):
    """Four endings, all reachable, each landing exactly where it should."""
    profile = profiles.build_reactivation(db_session,
                                          allow_synthetic_data=True)
    report = simulator.reactivation(db_session, profile, scenario=scenario,
                                    now=business_hours())

    thread = report["thread"]
    assert thread is not None
    assert thread["state"] == expected_state
    assert thread["stop_reason"] == expected_stop
    assert step(report, "first_outreach")["ok"] is True


def test_reactivation_blocked_contact_is_refused_before_anything_is_composed(
        ops_enabled, db_session):
    """The contact who opted out at the source is refused outright.

    No message row, no thread progress, and the reason is the ELIGIBILITY
    gate rather than an accident of ordering.
    """
    profile = profiles.build_reactivation(db_session,
                                          allow_synthetic_data=True)
    report = simulator.reactivation(db_session, profile, scenario="blocked",
                                    now=business_hours())

    first = step(report, "first_outreach")
    assert first["ok"] is False
    assert first["denial_code"] == C.D_INELIGIBLE
    assert report["thread"]["state"] == C.QUEUED
    assert report["thread"]["outbound"] == 0


def test_opt_out_stops_the_conversation_and_the_next_send(
        ops_enabled, db_session):
    """STOP stops the next send, and every one after it.

    A stopped conversation stays stopped: resuming one is a NEW thread, so
    "this family was worked twice" is visible rather than hidden inside one
    row's history.
    """
    profile = profiles.build_reactivation(db_session,
                                          allow_synthetic_data=True)
    report = simulator.reactivation(db_session, profile, scenario="opt_out",
                                    now=business_hours())

    after = step(report, "attempt_after_stop")
    assert after["ok"] is False
    # A HARD STOP IS REPORTED AS INELIGIBILITY, NOT AS COMPLETION. The two
    # refuse identically and read completely differently in the denial
    # histogram: this one is a family who asked us to stop, and grouping it
    # under "the objective is finished" would hide exactly the number an
    # operator most needs to see.
    assert after["denial_code"] == C.D_INELIGIBLE
    assert report["thread"]["stop_reason"] == C.STOP_OPT_OUT
    assert report["thread"]["status"] == "closed"


# ═══════════════════════════════════════════════════════════════════════════
# THE FULL LIFECYCLE, IN BOTH SEGMENTS
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("segment", ["b2b", "residential"])
def test_full_lifecycle_runs_the_same_engine_for_both_segments(
        ops_enabled, db_session, segment):
    """B2B AND RESIDENTIAL ARE THE SAME ENGINE WITH DIFFERENT DATA.

    `simulator.full_lifecycle` branches on the segment nowhere, and this test
    is the claim being checked: identical step outcomes, identical ending.
    """
    profile = profiles.build_full_lifecycle(db_session, segment=segment,
                                            allow_synthetic_data=True)
    report = simulator.full_lifecycle(db_session, profile,
                                      now=business_hours())

    for name in ("new_lead_first_touch", "qualify", "switch_to_email",
                 "book", "pipeline_update", "dormant_touch",
                 "dormant_followup_scheduled", "inbound_answered",
                 "human_transfer"):
        assert step(report, name)["ok"] is True, name

    assert report["thread"]["state"] == C.APPOINTMENT_BOOKED
    assert report["thread"]["appointment_ref"]
    # An inbound arriving before any AI conversation exists is recorded and
    # left to the platform's own pipeline rather than claimed by this layer.
    cold = step(report, "inbound_with_no_conversation")["routed"]
    assert cold["routed"] is False
    assert cold["reason"] == "no_ai_conversation"
    # The follow-up queued for five days later actually executed when the
    # clock was handed to the worker.
    assert step(report, "dormant_followup_executed")["summary"]["executed"] == 1


def test_a_channel_switch_keeps_one_thread_carrying_both_channels(
        ops_enabled, db_session, sample_org, sample_lead):
    """ONE OBJECTIVE, ONE HISTORY, ACROSS EVERY PERMITTED CHANNEL.

    Read per channel, an SMS and the email that follows it are two unrelated
    conversations and the employee arriving at the second has no idea what it
    said in the first. Read through the thread it is one conversation, which
    is what it actually was.
    """
    ctx = declare(sample_org)
    now = business_hours()

    texted = orchestrator.send_message(db_session, ctx,
                                       subject_id=sample_lead.id,
                                       body="By text first.", now=now)
    assert texted.ok is True
    thread = texted.gate.thread

    emailed = orchestrator.send_email(db_session, ctx,
                                      subject_id=sample_lead.id,
                                      thread=thread,
                                      subject_line="And by email",
                                      body="Same conversation.", now=now)
    assert emailed.ok is True

    assert sorted(continuity.channels_used(thread)) == ["email", "sms"]
    assert thread.last_channel == C.CHANNEL_EMAIL
    # ONE thread, not two — the continuity guarantee itself.
    assert (db_session.query(AIConversationThread)
            .filter(AIConversationThread.organization_id == sample_org.id,
                    AIConversationThread.subject_id == sample_lead.id)
            .count()) == 1
    history = continuity.history(db_session, thread, limit=20)
    assert len([h for h in history if h["source"] == "ai_operations"]) == 2


# ═══════════════════════════════════════════════════════════════════════════
# CONTROL — people can always stop the machine
# ═══════════════════════════════════════════════════════════════════════════

def test_human_takeover_stops_the_ai_at_its_very_next_gate(
        ops_enabled, db_session, sample_org, sample_advisor, sample_lead):
    """IMMEDIATELY MEANS AT THE NEXT GATE, NOT AT THE NEXT RUN.

    Ownership is written on the thread and `orchestrator.begin` reads it on
    every single operation, so a worker that is mid-run when somebody takes
    over is refused on its next action rather than finishing its sequence.
    """
    ctx = declare(sample_org)
    now = business_hours()
    first = orchestrator.send_message(db_session, ctx,
                                      subject_id=sample_lead.id,
                                      body="First touch.", now=now)
    assert first.ok is True
    thread = first.gate.thread

    ownership = stop_controls.take_over(db_session, thread,
                                        user_id=sample_advisor.id,
                                        reason_code="human_requested")
    assert ownership.is_active is True
    assert thread.human_owner_user_id == sample_advisor.id
    assert thread.state == C.HUMAN_OWNED

    refused = orchestrator.send_message(db_session, ctx,
                                        subject_id=sample_lead.id,
                                        thread=thread,
                                        body="While you wait, here is a link.",
                                        now=now)
    assert refused.ok is False
    assert refused.gate.denial_code == C.D_HUMAN_OWNED
    assert refused.gate.decided_by == "human_ownership"


def test_stop_all_for_organization_stops_every_open_thread(
        ops_enabled, db_session, sample_org, sample_advisor, sample_lead):
    """The tenant-level stop, and the tenant boundary around it.

    An operator inside ONE customer can stop that customer's entire AI
    workforce without holding any platform authority — and without being able
    to touch anybody else's. That boundary is why the function takes an
    organization id rather than being a global switch with a filter.
    """
    from app.models.models import Lead, LeadStatus, LeadTier, Organization

    ctx = declare(sample_org)
    now = business_hours()
    second_lead = Lead(organization_id=sample_org.id,
                       assigned_to_id=sample_advisor.id, first_name="Second",
                       last_name="Family", phone="12145558888",
                       email="second@example.com", tier=LeadTier.PRE_NEED,
                       status=LeadStatus.NEW)
    db_session.add(second_lead)
    db_session.commit()

    a = orchestrator.send_message(db_session, ctx, subject_id=sample_lead.id,
                                  body="One.", now=now)
    b = orchestrator.send_message(db_session, ctx, subject_id=second_lead.id,
                                  body="Two.", now=now)
    assert a.ok and b.ok

    # A conversation belonging to a different customer, which must survive.
    other_org = Organization(name="Untouched Home",
                             slug="other-org-t7-stop-all", plan="standard")
    db_session.add(other_org)
    db_session.commit()
    other_lead = Lead(organization_id=other_org.id, first_name="Other",
                      last_name="Family", phone="19725557777",
                      email="other@example.com", tier=LeadTier.PRE_NEED,
                      status=LeadStatus.NEW)
    db_session.add(other_lead)
    db_session.commit()
    other_thread = continuity.open_thread(
        db_session, declare(other_org, employee_id="other-employee"),
        subject_type="lead", subject_id=other_lead.id)
    db_session.commit()

    result = stop_controls.stop_all_for_organization(
        db_session, organization_id=sample_org.id,
        user_id=sample_advisor.id, reason=C.STOP_SUPERVISOR)

    assert result["threads_seen"] == 2
    assert result["threads_stopped"] == 2
    for thread in (a.gate.thread, b.gate.thread):
        db_session.refresh(thread)
        assert thread.stop_reason == C.STOP_SUPERVISOR
        assert thread.status == "closed"
        assert thread.state == C.STOPPED
    db_session.refresh(other_thread)
    assert other_thread.stop_reason is None
    assert other_thread.status == "open"


# ═══════════════════════════════════════════════════════════════════════════
# THE FOLLOW-UP WORKER
# ═══════════════════════════════════════════════════════════════════════════

def test_the_followup_worker_executes_a_due_action_and_refuses_a_stale_one(
        ops_enabled, db_session):
    """A SCHEDULE CREATED WHILE ELIGIBLE DOES NOT GUARANTEE ELIGIBILITY LATER.

    Both halves in one test, because they are one property. The due action
    runs — proving the worker is not simply inert. The stale action, on a
    conversation that was stopped after it was queued and WITHOUT its
    cancellation being written (simulating a row that escaped the cancel), is
    refused at execution time by the re-evaluation that is the second
    defence.

    A profile is used rather than a bare declared context because the worker
    comes back holding only an employee id and correctly refuses to act
    without an authority answer; a profile registers its declared context so
    that answer exists for synthetic runs — and a declared context still
    cannot reach anybody.
    """
    profile = profiles.build_reactivation(db_session,
                                          allow_synthetic_data=True)
    ctx = profile.ctx
    now = business_hours()

    # ── the due action ────────────────────────────────────────────────────
    live_lead = profile.lead_by_key("books")
    first = orchestrator.send_message(db_session, ctx,
                                      subject_id=live_lead.id,
                                      body="First touch.", now=now)
    assert first.ok is True
    due_when = now + timedelta(days=2)
    scheduled = followup.schedule(db_session, ctx, thread=first.gate.thread,
                                  operation=C.OP_SEND_MESSAGE, when=due_when,
                                  channel=C.CHANNEL_SMS,
                                  payload={"body": "Following up as promised."},
                                  reason="no response yet")
    assert scheduled.ok is True

    summary = followup.run_due_actions(
        db_session, now=due_when + timedelta(minutes=1), limit=10,
        organization_id=profile.organization.id)
    assert summary["seen"] == 1
    assert summary["executed"] == 1
    assert summary["errors"] == []

    # ── the stale action ──────────────────────────────────────────────────
    stale_lead = profile.lead_by_key("silent")
    stale_thread = continuity.open_thread(db_session, ctx,
                                          subject_type="lead",
                                          subject_id=stale_lead.id)
    stale_when = now + timedelta(days=4)
    followup.schedule(db_session, ctx, thread=stale_thread,
                      operation=C.OP_SEND_MESSAGE, when=stale_when,
                      channel=C.CHANNEL_SMS,
                      payload={"body": "Should never send."},
                      reason="stale case")
    # Stopped WITHOUT cancelling, so the row survives to be re-evaluated.
    stale_thread.stop_reason = C.STOP_OPT_OUT
    stale_thread.stopped_at = datetime.utcnow()
    db_session.flush()

    action = (db_session.query(AIScheduledAction)
              .filter(AIScheduledAction.thread_id == stale_thread.id,
                      AIScheduledAction.status == "pending").first())
    assert action is not None
    outcome = followup.execute(db_session, action,
                               now=stale_when + timedelta(minutes=1))
    assert outcome["status"] == "cancelled"
    assert outcome["reason"] == C.STOP_OPT_OUT
    db_session.refresh(action)
    assert action.status == "cancelled"
