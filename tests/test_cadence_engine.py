"""
SS6 — the cadence engine: transactions, history, compliance, concurrency.

THE FINDING THAT DROVE THIS. The runner called `get_twilio_client(advisor, db)`
and unpacked three values from it. That function returns a single Client, so
every touch raised TypeError - AFTER the counter had been advanced and
committed. The cadence has never sent one message. It walked leads from touch 1
to touch 9, marked them `sent`, wrote no `messages` row, and reported an error
count nobody read.

So these tests are about a sender that is being switched on for the first time,
not one being repaired. CADENCE_SMS_SENDING defaults off and every test that
wants a send turns it on explicitly; the last test asserts the shipped default.

No test here reaches Twilio - conftest forbids it and the client is a mock.
"""

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.models.models import (
    CadenceState, CadenceTouchLog, Lead, Message, Organization, Reply,
    SuppressionEntry, User,
)
from app.services import cadence_service as cs
from app.services import send_source
from app.services.auth_service import create_access_token, hash_password


def _lead(db_session, org, advisor, *, phone="12145552001", status="new"):
    lead = Lead(organization_id=org.id, assigned_to_id=advisor.id,
                first_name="Cad", last_name="Ence", phone=phone, phone_raw=phone,
                status=status)
    db_session.add(lead)
    db_session.commit()
    return lead


def _due(db_session, lead, *, touch=0, minutes_ago=5):
    now = datetime.utcnow()
    state = CadenceState(lead_id=lead.id, status="active",
                         current_touch_number=touch,
                         cadence_started_at=now - timedelta(days=1),
                         next_touch_due_at=now - timedelta(minutes=minutes_ago))
    db_session.add(state)
    db_session.commit()
    return state


def _twilio():
    client = MagicMock()
    client.messages.create.return_value = MagicMock(
        sid="SM_cadence", status="queued", error_code=None, error_message=None)
    return client


def _sending_on(monkeypatch):
    monkeypatch.setenv("CADENCE_SMS_SENDING", "true")


def _logs(db_session, lead):
    return (db_session.query(CadenceTouchLog)
            .filter(CadenceTouchLog.lead_id == lead.id)
            .order_by(CadenceTouchLog.touch_number, CadenceTouchLog.attempt_seq)
            .all())


# ═══════════════════════════════════════════════════════════════════════════
# The switch
# ═══════════════════════════════════════════════════════════════════════════

def test_this_build_does_not_send_cadence_sms(monkeypatch):
    monkeypatch.delenv("CADENCE_SMS_SENDING", raising=False)
    assert cs._sending_enabled() is False


def test_with_the_switch_off_nothing_reaches_the_provider(
        db_session, sample_org, sample_advisor, monkeypatch):
    monkeypatch.delenv("CADENCE_SMS_SENDING", raising=False)
    lead = _lead(db_session, sample_org, sample_advisor)
    state = _due(db_session, lead)

    with patch("app.services.sms_service._resolve_twilio_creds") as creds:
        result = cs.run_due_cadences(db_session)

    creds.assert_not_called()
    assert result["sent"] == 0
    assert result["sending_enabled"] is False
    db_session.refresh(state)
    assert state.current_touch_number == 0, "a skipped touch must not advance"
    log = _logs(db_session, lead)[-1]
    assert log.outcome == cs.OUTCOME_SKIPPED
    assert "disabled for this deployment" in log.reason


def test_a_customer_without_the_cadences_feature_is_skipped(
        db_session, sample_org, sample_advisor, monkeypatch):
    """Per-customer control is the entitlement that already exists."""
    _sending_on(monkeypatch)
    sample_org.enabled_features = json.dumps(["leads", "sms"])  # no "cadences"
    db_session.commit()
    lead = _lead(db_session, sample_org, sample_advisor)
    _due(db_session, lead)

    with patch("app.services.sms_service._resolve_twilio_creds") as creds:
        result = cs.run_due_cadences(db_session)

    creds.assert_not_called()
    assert result["sent"] == 0
    assert "not entitled to cadences" in _logs(db_session, lead)[-1].reason


# ═══════════════════════════════════════════════════════════════════════════
# The send, and the state that follows it
# ═══════════════════════════════════════════════════════════════════════════

def test_a_successful_touch_sends_records_and_advances(
        db_session, sample_org, sample_advisor, monkeypatch):
    _sending_on(monkeypatch)
    lead = _lead(db_session, sample_org, sample_advisor)
    state = _due(db_session, lead)

    with patch("app.services.sms_service._resolve_twilio_creds",
               return_value=(_twilio(), "+19998887777", None)):
        result = cs.run_due_cadences(db_session)

    assert result["sent"] == 1
    db_session.refresh(state)
    assert state.current_touch_number == 1
    assert state.last_touch_sent_at is not None

    # The message the cadence has never written before now exists, attributed.
    message = db_session.query(Message).filter(Message.lead_id == lead.id).one()
    assert message.send_source == send_source.CADENCE
    assert message.sent_by_user_id is None, "a cron has no authenticated human"
    assert message.twilio_sid == "SM_cadence"

    log = _logs(db_session, lead)[-1]
    assert log.outcome == cs.OUTCOME_SENT
    assert log.touch_number == 1 and log.attempt_seq == 1
    assert log.message_id == message.id
    assert log.provider_message_id == "SM_cadence"
    assert log.attempted_at is not None


def test_a_provider_failure_records_the_failure_and_advances_nothing(
        db_session, sample_org, sample_advisor, monkeypatch):
    _sending_on(monkeypatch)
    lead = _lead(db_session, sample_org, sample_advisor)
    state = _due(db_session, lead)

    client = _twilio()
    client.messages.create.side_effect = RuntimeError("Twilio said 30007")

    with patch("app.services.sms_service._resolve_twilio_creds",
               return_value=(client, "+19998887777", None)):
        result = cs.run_due_cadences(db_session)

    assert result["sent"] == 0 and result["errors"] == 1
    db_session.refresh(state)
    assert state.current_touch_number == 0, "the counter must not move on failure"
    assert state.last_touch_sent_at is None
    assert db_session.query(Message).filter(Message.lead_id == lead.id).count() == 0

    log = _logs(db_session, lead)[-1]
    assert log.outcome == cs.OUTCOME_FAILED
    assert "30007" in log.reason, "the provider's own words are kept"


def test_a_failed_touch_is_retried_then_abandoned_so_the_cadence_continues(
        db_session, sample_org, sample_advisor, monkeypatch):
    _sending_on(monkeypatch)
    lead = _lead(db_session, sample_org, sample_advisor)
    state = _due(db_session, lead)

    client = _twilio()
    client.messages.create.side_effect = RuntimeError("carrier rejected")

    with patch("app.services.sms_service._resolve_twilio_creds",
               return_value=(client, "+19998887777", None)):
        for _ in range(cs.MAX_ATTEMPTS_PER_TOUCH):
            db_session.refresh(state)
            state.next_touch_due_at = datetime.utcnow() - timedelta(minutes=1)
            db_session.commit()
            cs.run_due_cadences(db_session)

        # One more run: attempts are exhausted, so the touch is abandoned.
        db_session.refresh(state)
        state.next_touch_due_at = datetime.utcnow() - timedelta(minutes=1)
        db_session.commit()
        cs.run_due_cadences(db_session)

    logs = _logs(db_session, lead)
    assert sum(1 for l in logs if l.outcome == cs.OUTCOME_FAILED) == cs.MAX_ATTEMPTS_PER_TOUCH
    assert logs[-1].outcome == cs.OUTCOME_ABANDONED
    db_session.refresh(state)
    assert state.current_touch_number == 1, "the cadence moves past a dead touch"


# ═══════════════════════════════════════════════════════════════════════════
# Compliance — the bypass that texted suppressed numbers nine times
# ═══════════════════════════════════════════════════════════════════════════

def test_a_suppressed_number_is_blocked_and_the_provider_is_never_reached(
        db_session, sample_org, sample_advisor, monkeypatch):
    """The cadence called Twilio directly and consulted no suppression list.
    A number on it whose Lead.status was never flipped to DNC would have been
    texted on every one of nine touches."""
    _sending_on(monkeypatch)
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145552044")
    db_session.add(SuppressionEntry(organization_id=sample_org.id,
                                    phone="12145552044", reason="asked to stop"))
    db_session.commit()
    state = _due(db_session, lead)

    with patch("app.services.sms_service._resolve_twilio_creds") as creds:
        result = cs.run_due_cadences(db_session)

    creds.assert_not_called()
    assert result["blocked"] == 1 and result["sent"] == 0
    db_session.refresh(state)
    assert state.current_touch_number == 0
    log = _logs(db_session, lead)[-1]
    assert log.outcome == cs.OUTCOME_BLOCKED
    assert "suppression" in (log.reason or "").lower()


def test_a_dnc_lead_stops_the_cadence_and_the_stop_is_recorded(
        db_session, sample_org, sample_advisor, monkeypatch):
    _sending_on(monkeypatch)
    lead = _lead(db_session, sample_org, sample_advisor, status="dnc")
    state = _due(db_session, lead)

    with patch("app.services.sms_service._resolve_twilio_creds") as creds:
        cs.run_due_cadences(db_session)

    creds.assert_not_called()
    db_session.refresh(state)
    assert state.status == "stopped_dnc"
    log = _logs(db_session, lead)[-1]
    assert log.outcome == cs.OUTCOME_STOPPED
    assert "dnc" in log.reason


def test_a_reply_stops_the_cadence_and_the_stop_is_recorded(
        db_session, sample_org, sample_advisor, monkeypatch):
    _sending_on(monkeypatch)
    lead = _lead(db_session, sample_org, sample_advisor)
    db_session.add(Reply(lead_id=lead.id, body="please call me"))
    db_session.commit()
    state = _due(db_session, lead)

    cs.run_due_cadences(db_session)

    db_session.refresh(state)
    assert state.status == "stopped_replied"
    assert _logs(db_session, lead)[-1].outcome == cs.OUTCOME_STOPPED


def test_a_capacity_hold_is_skipped_visibly_rather_than_silently(
        db_session, sample_org, sample_advisor, monkeypatch):
    _sending_on(monkeypatch)
    lead = _lead(db_session, sample_org, sample_advisor)
    lead.capacity_state = "over_capacity"
    db_session.commit()
    state = _due(db_session, lead)

    cs.run_due_cadences(db_session)

    db_session.refresh(state)
    assert state.status == "active", "a hold pauses, it does not tear down"
    assert state.current_touch_number == 0
    log = _logs(db_session, lead)[-1]
    assert log.outcome == cs.OUTCOME_SKIPPED
    assert "capacity" in log.reason


def test_a_demo_tenant_is_suppressed_not_texted(
        db_session, sample_org, sample_advisor, monkeypatch):
    _sending_on(monkeypatch)
    sample_org.is_demo = True
    db_session.commit()
    lead = _lead(db_session, sample_org, sample_advisor)
    _due(db_session, lead)

    with patch("app.services.sms_service._resolve_twilio_creds") as creds:
        cs.run_due_cadences(db_session)

    creds.assert_not_called()
    log = _logs(db_session, lead)[-1]
    assert log.outcome == cs.OUTCOME_SUPPRESSED


# ═══════════════════════════════════════════════════════════════════════════
# Concurrency
# ═══════════════════════════════════════════════════════════════════════════

def test_two_runners_on_the_same_touch_produce_one_send(
        db_session, sample_org, sample_advisor, monkeypatch):
    """with_for_update(skip_locked=True) was documented as preventing this and
    does not: its locks are released by the first commit inside the loop, and
    there were three commits per iteration and three concurrent triggers. The
    unique index on (state, touch, attempt) does hold."""
    _sending_on(monkeypatch)
    lead = _lead(db_session, sample_org, sample_advisor)
    state = _due(db_session, lead)

    # The claim the first runner already won.
    db_session.add(CadenceTouchLog(
        cadence_state_id=state.id, lead_id=lead.id,
        organization_id=sample_org.id, touch_number=1, attempt_seq=1,
        outcome=cs.OUTCOME_SENT, channel="sms", attempted_at=datetime.utcnow()))
    db_session.commit()

    with patch("app.services.sms_service._resolve_twilio_creds") as creds:
        result = cs.run_due_cadences(db_session)

    creds.assert_not_called(), "the second runner must not send"
    assert result["sent"] == 0
    assert len([l for l in _logs(db_session, lead) if l.touch_number == 1]) == 1


def test_the_claim_is_written_before_the_provider_is_called(
        db_session, sample_org, sample_advisor, monkeypatch):
    """A provider that times out may still have sent. The row has to exist
    first, or a retry becomes a coin flip."""
    _sending_on(monkeypatch)
    lead = _lead(db_session, sample_org, sample_advisor)
    state = _due(db_session, lead)
    seen = {}

    client = _twilio()

    def _record_then_send(*a, **k):
        seen["claims_at_send_time"] = (
            db_session.query(CadenceTouchLog)
            .filter(CadenceTouchLog.cadence_state_id == state.id).count())
        return MagicMock(sid="SM_x", status="queued",
                         error_code=None, error_message=None)

    client.messages.create.side_effect = _record_then_send

    with patch("app.services.sms_service._resolve_twilio_creds",
               return_value=(client, "+19998887777", None)):
        cs.run_due_cadences(db_session)

    assert seen["claims_at_send_time"] == 1


# ═══════════════════════════════════════════════════════════════════════════
# History
# ═══════════════════════════════════════════════════════════════════════════

def test_the_history_reconstructs_what_actually_happened(
        db_session, sample_org, sample_advisor, monkeypatch):
    _sending_on(monkeypatch)
    lead = _lead(db_session, sample_org, sample_advisor)
    state = _due(db_session, lead)

    with patch("app.services.sms_service._resolve_twilio_creds",
               return_value=(_twilio(), "+19998887777", None)):
        cs.run_due_cadences(db_session)
        db_session.refresh(state)
        state.next_touch_due_at = datetime.utcnow() - timedelta(minutes=1)
        db_session.commit()
        cs.run_due_cadences(db_session)

    history = cs.get_cadence_history(db_session, lead.id)
    assert [h["touch_number"] for h in history] == [1, 2]
    assert all(h["outcome"] == cs.OUTCOME_SENT for h in history)
    assert all(h["provider_message_id"] == "SM_cadence" for h in history)
    assert all(h["attempted_at"] is not None for h in history)


def test_the_history_endpoint_is_lead_scoped(
        client, auth_headers, db_session, sample_org, sample_advisor):
    other = Organization(name="Other Cad Co", slug="other-cad", plan="trial")
    db_session.add(other)
    db_session.flush()
    outsider = User(organization_id=other.id, email="c@other.test",
                    password_hash=hash_password("CPass123!"), full_name="C",
                    role="advisor", must_change_password=False)
    db_session.add(outsider)
    db_session.commit()
    foreign = _lead(db_session, other, outsider, phone="12145552099")

    response = client.get(f"/cadence/lead/{foreign.id}/history", headers=auth_headers)
    assert response.status_code in (403, 404)


def test_total_touches_comes_from_the_org_schedule_not_a_literal(
        client, auth_headers, db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor)
    _due(db_session, lead)
    response = client.get(f"/cadence/lead/{lead.id}/history", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["total_touches"] == len(cs.CADENCE_SCHEDULE_DAYS)


def test_no_test_here_left_cadence_sending_enabled():
    assert cs._sending_enabled() is False
