"""THE SAME THING MUST NOT HAPPEN TWICE.

ASSUME EVERY WORKER RETRIES AND EVERY EVENT ARRIVES MORE THAN ONCE. That is
not pessimism: Twilio redelivers a webhook after a timeout, a job host
restarts a worker mid-run, and two loops overlap when one is slow. A system
that is correct only when each message arrives exactly once is a system that
is correct on quiet days.

Each test below drives one of those realities through the real engine and
asserts the ROW COUNT, not just the refusal — because "the second call
returned an error" is also what a broken engine does, and the fact worth
proving is that the family got one text.
"""

from datetime import datetime, timedelta

import pytest

from app.models.ai_operations_models import (AICommunication,
                                             AIConversationThread,
                                             AIScheduledAction)
from app.services.ai_operations import (appointments, audit, channels,
                                        comm_state, constants as C,
                                        continuity, contracts, followup,
                                        idempotency, inbound, orchestrator,
                                        profiles)
from app.services.ai_operations import stop as stop_controls
from app.services.ai_operations.channels.simulated import TimingOutAdapter


def business_hours():
    return datetime.utcnow().replace(hour=15, minute=0, second=0,
                                     microsecond=0)


@pytest.fixture()
def ops_enabled(monkeypatch):
    """AI Operations ON, live sending never.

    The teardown clears the adapter registry and any declared context: this
    file installs a TimingOutAdapter, and one left behind would make the next
    test in the process fail for a reason that has nothing to do with it.
    """
    monkeypatch.setenv("AI_OPERATIONS_ENABLED", "1")
    monkeypatch.delenv("AI_OPERATIONS_LIVE_SEND", raising=False)
    monkeypatch.delenv("AI_OPERATIONS_KILL", raising=False)
    monkeypatch.delenv("AI_WORKFORCE_KILL", raising=False)
    yield
    channels.reset_adapters()
    profiles.forget_declared()


def declare(org, **overrides):
    kwargs = {
        "employee_id": "idempotency-employee",
        "organization_id": org.id,
        "name": "Idempotency Test Employee",
        "tool_keys": set(profiles.REACTIVATION_TOOLS),
        "channels": {C.CHANNEL_SMS, C.CHANNEL_EMAIL},
        "activation_state": "simulation",
        "status": "active",
        "timezone": "America/Chicago",
    }
    kwargs.update(overrides)
    return contracts.declare_employee_context(**kwargs)


# ═══════════════════════════════════════════════════════════════════════════
# OUTBOUND
# ═══════════════════════════════════════════════════════════════════════════

def test_the_same_message_twice_on_one_thread_is_one_communication(
        ops_enabled, db_session, sample_org, sample_lead):
    """Two workers with the same instruction produce one text.

    The key is (thread, channel, content digest), so "send this exact text to
    this person on this conversation" twice is a duplicate — while different
    words are a follow-up and are allowed through.
    """
    ctx = declare(sample_org)
    body = "Exactly the same words, twice."

    first = orchestrator.send_message(db_session, ctx,
                                      subject_id=sample_lead.id, body=body,
                                      now=business_hours())
    second = orchestrator.send_message(db_session, ctx,
                                       subject_id=sample_lead.id,
                                       thread=first.gate.thread, body=body,
                                       now=business_hours())

    assert first.ok is True
    assert second.ok is False
    assert second.gate.duplicate is True
    assert second.gate.denial_code == C.D_DUPLICATE
    assert second.communication.id == first.communication.id

    rows = (db_session.query(AICommunication)
            .filter(AICommunication.thread_id == first.gate.thread.id,
                    AICommunication.direction == C.OUTBOUND,
                    AICommunication.body_digest == audit.digest(body))
            .count())
    assert rows == 1

    # Different words on the same thread are NOT a duplicate — otherwise the
    # guard would have stopped the conversation rather than the repeat.
    follow_up = orchestrator.send_message(db_session, ctx,
                                          subject_id=sample_lead.id,
                                          thread=first.gate.thread,
                                          body="Different words entirely.",
                                          now=business_hours())
    assert follow_up.ok is True


def test_the_ready_to_sending_claim_is_won_exactly_once(
        ops_enabled, db_session, sample_org, sample_lead):
    """THE UPDATE IS THE LOCK, and only one worker can win it.

    `claim_for_send` moves READY -> SENDING with a WHERE clause naming the
    state the row must still be in. Two workers holding the same
    communication both call it; the loser's UPDATE matches zero rows and it
    learns it lost, without a SELECT-then-UPDATE gap to lose in.
    """
    ctx = declare(sample_org)
    thread = continuity.open_thread(db_session, ctx, subject_type="lead",
                                    subject_id=sample_lead.id)
    comm = AICommunication(
        organization_id=sample_org.id, thread_id=thread.id,
        employee_id=ctx.employee_id, subject_type="lead",
        subject_id=sample_lead.id, direction=C.OUTBOUND,
        channel=C.CHANNEL_SMS, state=C.READY,
        to_address=sample_lead.phone, body_digest=audit.digest("claim race"),
        body_length=11, idempotency_key="claim-race-key", attempts=0)
    db_session.add(comm)
    db_session.flush()

    assert comm_state.claim_for_send(db_session, comm) is True
    assert comm.state == C.SENDING
    # The second worker arrives a microsecond later and is told no.
    assert comm_state.claim_for_send(db_session, comm) is False
    assert comm.state == C.SENDING


def test_a_timeout_then_a_retry_does_not_create_a_second_message(
        ops_enabled, db_session, sample_org, sample_lead):
    """A TIMEOUT DOES NOT MEAN IT WAS NOT SENT. It means we do not know.

    The riskiest retry in the system. The communication row is written under
    its idempotency key BEFORE the adapter is called, so the retry is refused
    as a duplicate rather than sending a second text to a family that already
    had the first one.
    """
    channels.register_simulated(C.CHANNEL_SMS, TimingOutAdapter(C.CHANNEL_SMS))
    ctx = declare(sample_org)
    body = "Timeout, then retry."

    first = orchestrator.send_message(db_session, ctx,
                                      subject_id=sample_lead.id, body=body,
                                      now=business_hours())
    assert first.ok is False
    assert first.provider_outcome == C.P_TIMEOUT
    assert first.communication.state == C.FAILED

    retry = orchestrator.send_message(db_session, ctx,
                                      subject_id=sample_lead.id,
                                      thread=first.gate.thread, body=body,
                                      now=business_hours())
    assert retry.ok is False
    assert retry.gate.denial_code == C.D_DUPLICATE

    rows = (db_session.query(AICommunication)
            .filter(AICommunication.thread_id == first.gate.thread.id,
                    AICommunication.body_digest == audit.digest(body))
            .count())
    assert rows == 1


# ═══════════════════════════════════════════════════════════════════════════
# INBOUND
# ═══════════════════════════════════════════════════════════════════════════

def test_a_redelivered_inbound_webhook_creates_one_inbound_communication(
        ops_enabled, db_session, sample_org, sample_advisor, sample_lead):
    """Providers retry. `(provider, provider_event_id)` is unique, so the
    second delivery of the same event is a no-op rather than a second reply
    in the family's conversation."""
    ctx = declare(sample_org)
    orchestrator.send_message(db_session, ctx, subject_id=sample_lead.id,
                              body="First touch.", now=business_hours())

    payload = dict(provider="simulated", provider_event_id="redelivered-1",
                   channel=C.CHANNEL_SMS, from_address=sample_lead.phone,
                   to_address=sample_advisor.twilio_phone_number,
                   body="Yes, still interested.")
    first = inbound.route(db_session, **payload)
    second = inbound.route(db_session, **payload)

    assert first["routed"] is True
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert second["event_id"] == first["event_id"]

    inbound_rows = (db_session.query(AICommunication)
                    .filter(AICommunication.thread_id == first["thread_id"],
                            AICommunication.direction == C.INBOUND)
                    .count())
    assert inbound_rows == 1


def test_a_delivery_callback_after_the_stop_neither_raises_nor_resurrects(
        ops_enabled, db_session, sample_org, sample_lead):
    """OUT OF ORDER IS ORDINARY NETWORK REALITY, not a defect.

    A "delivered" callback can arrive after the conversation has been
    stopped. That must not raise (the webhook would 500 and the provider
    would retry forever) and it must not drag a stopped conversation back
    into play.
    """
    ctx = declare(sample_org)
    sent = orchestrator.send_message(db_session, ctx,
                                     subject_id=sample_lead.id,
                                     body="Before the stop.",
                                     now=business_hours())
    thread = sent.gate.thread
    comm = sent.communication

    stop_controls.stop_thread(db_session, ctx, thread,
                              reason=C.STOP_OBJECTIVE_COMPLETE)
    assert thread.stop_reason == C.STOP_OBJECTIVE_COMPLETE
    state_before = comm.state

    out = inbound.delivery_status(
        db_session, provider="simulated",
        provider_message_id=comm.provider_message_id, status="delivered")

    assert out["matched"] is True
    db_session.refresh(comm)
    db_session.refresh(thread)
    # The illegal move was skipped rather than taken, and nothing about the
    # stop was undone.
    assert comm.state == state_before
    assert thread.stop_reason == C.STOP_OBJECTIVE_COMPLETE
    assert thread.status == "closed"


# ═══════════════════════════════════════════════════════════════════════════
# THE OTHER CONSEQUENTIAL ACTIONS
# ═══════════════════════════════════════════════════════════════════════════

def test_the_same_slot_booked_twice_is_one_appointment(
        ops_enabled, db_session, sample_org, sample_advisor, sample_lead):
    """One family, one slot, one appointment — whichever employee asked.

    The booking key is (subject, start time, thread) and deliberately does
    NOT include the employee: two employees booking the same person into the
    same slot is one duplicate appointment for that family.
    """
    ctx = declare(sample_org, handoff_user_id=sample_advisor.id)
    thread = continuity.open_thread(db_session, ctx, subject_type="lead",
                                    subject_id=sample_lead.id)
    now = business_hours()

    availability = appointments.list_availability(
        db_session, ctx, subject_id=sample_lead.id, days_ahead=10,
        now_utc=now)
    offered = appointments.offered_times(availability, limit=1)
    assert offered, ("The platform's own calendar offered no times, so the "
                     "duplicate-booking guard cannot be exercised: %s"
                     % availability.get("reason"))

    first = appointments.book_appointment(db_session, ctx, thread=thread,
                                          starts_at=offered[0]["starts_at"],
                                          now_utc=now)
    second = appointments.book_appointment(db_session, ctx, thread=thread,
                                           starts_at=offered[0]["starts_at"],
                                           now_utc=now)

    assert first.ok is True
    assert thread.state == C.APPOINTMENT_BOOKED
    assert second.ok is False
    assert second.gate.duplicate is True
    assert second.gate.denial_code == C.D_DUPLICATE
    # The reference did not change, so nothing was booked a second time.
    assert thread.appointment_ref == first.detail["appointment_ref"]


def test_scheduling_the_same_followup_twice_creates_one_row(
        ops_enabled, db_session, sample_org, sample_lead):
    """Two follow-ups at the same minute on one thread are a double-booking.

    The scheduled TIME is in this key, unlike everywhere else, and for the
    mirror-image reason: the same follow-up moved to tomorrow is a
    legitimately different intent and must be allowed to exist.
    """
    ctx = declare(sample_org)
    sent = orchestrator.send_message(db_session, ctx,
                                     subject_id=sample_lead.id,
                                     body="First touch.",
                                     now=business_hours())
    thread = sent.gate.thread
    when = business_hours() + timedelta(days=3)

    first = followup.schedule(db_session, ctx, thread=thread,
                              operation=C.OP_SEND_MESSAGE, when=when,
                              channel=C.CHANNEL_SMS,
                              payload={"body": "Checking back."},
                              reason="no response")
    second = followup.schedule(db_session, ctx, thread=thread,
                               operation=C.OP_SEND_MESSAGE, when=when,
                               channel=C.CHANNEL_SMS,
                               payload={"body": "Checking back."},
                               reason="no response")

    assert first.ok is True
    assert second.ok is False
    assert second.gate.duplicate is True
    assert second.gate.denial_code == C.D_DUPLICATE

    rows = (db_session.query(AIScheduledAction)
            .filter(AIScheduledAction.thread_id == thread.id,
                    AIScheduledAction.operation == C.OP_SEND_MESSAGE)
            .count())
    assert rows == 1

    # The same intent on a DIFFERENT day is a different action.
    tomorrow = followup.schedule(db_session, ctx, thread=thread,
                                 operation=C.OP_SEND_MESSAGE,
                                 when=when + timedelta(days=1),
                                 channel=C.CHANNEL_SMS,
                                 payload={"body": "Checking back."},
                                 reason="no response")
    assert tomorrow.ok is True
    assert (db_session.query(AIScheduledAction)
            .filter(AIScheduledAction.thread_id == thread.id,
                    AIScheduledAction.operation == C.OP_SEND_MESSAGE)
            .count()) == 2


def test_the_idempotency_keys_themselves_are_stable_across_retries(
        ops_enabled, db_session, sample_org):
    """A KEY NEVER CONTAINS A TIMESTAMP — that is the whole guarantee.

    Asserted directly rather than only through behaviour, because a key that
    varied between two attempts at the same action would make every retry a
    new action, which is precisely the bug the keys exist to prevent.
    """
    a = idempotency.key_for_send(thread_id="t1", channel=C.CHANNEL_SMS,
                                 body="hello")
    b = idempotency.key_for_send(thread_id="t1", channel=C.CHANNEL_SMS,
                                 body="hello")
    assert a == b
    assert a != idempotency.key_for_send(thread_id="t1",
                                         channel=C.CHANNEL_SMS, body="hello!")
    assert a != idempotency.key_for_send(thread_id="t2",
                                         channel=C.CHANNEL_SMS, body="hello")
    # A deliberate re-send of the same words belongs to its own attempt group.
    assert a != idempotency.key_for_send(thread_id="t1",
                                         channel=C.CHANNEL_SMS, body="hello",
                                         attempt_group="scheduled-action-7")
