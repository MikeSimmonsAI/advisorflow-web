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


# ── THE CLOCK THESE TESTS RUN AT ────────────────────────────────────────────
#
# The engine now refuses to text a family outside their own permitted contact
# hours, so every test that expects a send has to run at a moment that permits
# one. Reading the wall clock would make this suite pass during the working
# day and fail overnight - which is precisely the failure the workforce
# simulator's hardcoded Tuesday produced, and it is not being repeated here.
#
# So: the next 15:00 UTC at or after this moment. That is 09:00-10:00 in
# America/Chicago in either half of the year, inside the window, and always
# later than the due times `_due` writes - so a due touch is still due.
def _in_hours(now=None):
    now = now or datetime.utcnow()
    at = now.replace(hour=15, minute=0, second=0, microsecond=0)
    return at if at >= now else at + timedelta(days=1)


AT = _in_hours()

# 06:00 UTC the following day - 01:00 in Chicago. For the tests that ARE about
# the clock. It has to be LATER than AT, not earlier the same day: `_due`
# writes due times from the real clock, and a moment before them is not a
# moment when anything is due, so the run would find nothing and the test
# would pass by doing nothing at all.
OUT_OF_HOURS = AT.replace(hour=6) + timedelta(days=1)


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
        result = cs.run_due_cadences(db_session, now=AT)

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
        result = cs.run_due_cadences(db_session, now=AT)

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
        result = cs.run_due_cadences(db_session, now=AT)

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
        result = cs.run_due_cadences(db_session, now=AT)

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
            cs.run_due_cadences(db_session, now=AT)

        # One more run: attempts are exhausted, so the touch is abandoned.
        db_session.refresh(state)
        state.next_touch_due_at = datetime.utcnow() - timedelta(minutes=1)
        db_session.commit()
        cs.run_due_cadences(db_session, now=AT)

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
        result = cs.run_due_cadences(db_session, now=AT)

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
        cs.run_due_cadences(db_session, now=AT)

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

    cs.run_due_cadences(db_session, now=AT)

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

    cs.run_due_cadences(db_session, now=AT)

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
        cs.run_due_cadences(db_session, now=AT)

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
        result = cs.run_due_cadences(db_session, now=AT)

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
        cs.run_due_cadences(db_session, now=AT)

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
        cs.run_due_cadences(db_session, now=AT)
        db_session.refresh(state)
        state.next_touch_due_at = datetime.utcnow() - timedelta(minutes=1)
        db_session.commit()
        cs.run_due_cadences(db_session, now=AT)

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


# ═══════════════════════════════════════════════════════════════════════════
# PERMITTED CONTACT HOURS
#
# The engine had no clock at all. `touch_def["send_hour"]` was read into the
# schedule and never used, so a touch fired whenever the runner came round -
# which on a UTC server is the middle of the night across most of the country.
# It never actually sent anything, so nobody was woken up. It is about to be
# able to, which is why the clock goes in before the switch does.
# ═══════════════════════════════════════════════════════════════════════════

def test_a_touch_due_in_the_middle_of_the_night_is_refused(
        db_session, sample_org, sample_advisor, monkeypatch):
    _sending_on(monkeypatch)
    lead = _lead(db_session, sample_org, sample_advisor)   # 214 -> Chicago
    state = _due(db_session, lead)

    with patch("app.services.sms_service._resolve_twilio_creds") as creds:
        result = cs.run_due_cadences(db_session, now=OUT_OF_HOURS)

    creds.assert_not_called()
    assert result["sent"] == 0
    assert result["blocked"] == 1
    log = _logs(db_session, lead)[-1]
    assert log.outcome == cs.OUTCOME_BLOCKED
    assert "quiet hours" in log.reason
    db_session.refresh(state)
    assert state.current_touch_number == 0, "a refused touch must not advance"


def test_a_refused_touch_is_re_dated_to_a_decent_hour_not_retried_all_night(
        db_session, sample_org, sample_advisor, monkeypatch):
    """Backing off an hour at 01:00 just means refusing again at 02:00."""
    _sending_on(monkeypatch)
    lead = _lead(db_session, sample_org, sample_advisor)
    state = _due(db_session, lead)

    with patch("app.services.sms_service._resolve_twilio_creds"):
        cs.run_due_cadences(db_session, now=OUT_OF_HOURS)

    db_session.refresh(state)
    from app.services import contact_hours
    assert state.next_touch_due_at == contact_hours.next_permitted_utc(
        lead, OUT_OF_HOURS)
    assert contact_hours.is_permitted(lead, state.next_touch_due_at)


def test_a_lead_with_no_determinable_time_zone_is_refused_not_guessed(
        db_session, sample_org, sample_advisor, monkeypatch):
    """UNKNOWN MEANS NO. There is no state and no recognisable area code, so
    there is no way to know whether it is 3am where this family is."""
    _sending_on(monkeypatch)
    # A perfectly valid, dialable number whose area code is not one this
    # platform can place. Not a malformed one - that is refused earlier, by
    # compliance, for a different and equally correct reason.
    lead = _lead(db_session, sample_org, sample_advisor, phone="12995551212")
    db_session.commit()
    _due(db_session, lead)

    with patch("app.services.sms_service._resolve_twilio_creds") as creds:
        result = cs.run_due_cadences(db_session, now=AT)

    creds.assert_not_called()
    assert result["blocked"] == 1
    assert "No time zone can be determined" in _logs(db_session, lead)[-1].reason


def test_a_lead_in_a_split_state_is_permitted_only_when_both_halves_are(
        db_session, sample_org, sample_advisor, monkeypatch):
    """Texas spans Central and Mountain. Rather than guessing which half a
    family is in, a moment is permitted only if it is permitted in both."""
    from app.services import contact_hours
    _sending_on(monkeypatch)
    lead = _lead(db_session, sample_org, sample_advisor)
    lead.state = "TX"
    db_session.commit()

    # 14:00 UTC is 09:00 Central - fine there, 08:00 Mountain - too early.
    assert contact_hours.is_permitted(lead, AT.replace(hour=14)) is False
    assert contact_hours.is_permitted(lead, AT.replace(hour=15)) is True


def test_the_state_is_preferred_over_the_area_code(db_session, sample_org,
                                                   sample_advisor):
    """A number is where somebody got their phone. An address is where they
    are."""
    from app.services import contact_hours
    lead = _lead(db_session, sample_org, sample_advisor)   # 214, Central
    lead.state = "CA"
    db_session.commit()
    resolved = contact_hours.zones_for_lead(lead)
    assert resolved["basis"] == "state"
    assert resolved["zones"] == ["America/Los_Angeles"]


# ═══════════════════════════════════════════════════════════════════════════
# STOPS THE STATUS COLUMN DOES NOT CATCH
# ═══════════════════════════════════════════════════════════════════════════

def test_a_real_booking_stops_the_cadence_even_if_the_status_never_flipped(
        db_session, sample_org, sample_advisor, monkeypatch):
    from app.models.models import BookingLink
    _sending_on(monkeypatch)
    lead = _lead(db_session, sample_org, sample_advisor, status="sent")
    state = _due(db_session, lead)
    db_session.add(BookingLink(token="cad-book-1", lead_id=lead.id,
                               user_id=sample_advisor.id, status="booked",
                               booked_time=AT + timedelta(days=2)))
    db_session.commit()

    with patch("app.services.sms_service._resolve_twilio_creds") as creds:
        cs.run_due_cadences(db_session, now=AT)

    creds.assert_not_called()
    db_session.refresh(state)
    assert state.status != "active"
    assert "already booked" in _logs(db_session, lead)[-1].reason


def test_a_human_taking_the_conversation_over_stops_the_cadence(
        db_session, sample_org, sample_advisor, monkeypatch):
    """The machine does not talk over its own colleague. Only answerable now
    that send_source records who initiated each message."""
    _sending_on(monkeypatch)
    lead = _lead(db_session, sample_org, sample_advisor, status="sent")
    state = _due(db_session, lead)
    db_session.add(Message(lead_id=lead.id, sender_id=sample_advisor.id,
                           body="Hi, this is Mike - following up personally.",
                           sent_at=state.cadence_started_at + timedelta(hours=2),
                           send_source=send_source.MANUAL,
                           sent_by_user_id=sample_advisor.id))
    db_session.commit()

    with patch("app.services.sms_service._resolve_twilio_creds") as creds:
        cs.run_due_cadences(db_session, now=AT)

    creds.assert_not_called()
    db_session.refresh(state)
    assert state.status != "active"
    assert "taken this conversation over" in _logs(db_session, lead)[-1].reason


def test_the_cadences_own_sends_do_not_stop_the_cadence(
        db_session, sample_org, sample_advisor, monkeypatch):
    """A takeover check that counted the engine's own messages would stop
    every cadence after its first touch."""
    _sending_on(monkeypatch)
    lead = _lead(db_session, sample_org, sample_advisor, status="sent")
    state = _due(db_session, lead)
    db_session.add(Message(lead_id=lead.id, sender_id=sample_advisor.id,
                           body="touch 1",
                           sent_at=state.cadence_started_at + timedelta(hours=1),
                           send_source=send_source.CADENCE,
                           sent_by_user_id=None))
    db_session.commit()

    with patch("app.services.sms_service._resolve_twilio_creds",
               return_value=(_twilio(), "+19998887777", None)):
        result = cs.run_due_cadences(db_session, now=AT)

    assert result["sent"] == 1
    db_session.refresh(state)
    assert state.status == "active"


# ═══════════════════════════════════════════════════════════════════════════
# THE BACKLOG DIAGNOSTIC
# ═══════════════════════════════════════════════════════════════════════════

def test_the_backlog_scan_writes_nothing():
    import ast, inspect
    from app.services import cadence_backlog
    tree = ast.parse(inspect.getsource(cadence_backlog))
    offenders = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call) or not isinstance(n.func, ast.Attribute):
            continue
        recv = getattr(n.func.value, "id", None) or getattr(n.func.value, "attr", None)
        if recv in ("db", "session") and n.func.attr in (
                "commit", "flush", "add", "add_all", "delete", "merge", "execute"):
            offenders.append(n.func.attr)
    assert not offenders, "cadence_backlog writes: %s" % offenders


def test_the_scan_counts_what_the_counters_claim_without_evidence(
        db_session, sample_org, sample_advisor, monkeypatch):
    """The whole reason this exists: current_touch_number says nine, and
    cadence_touch_logs - which did not exist until this week - says nothing."""
    from app.services import cadence_backlog
    _sending_on(monkeypatch)
    lead = _lead(db_session, sample_org, sample_advisor, status="sent")
    state = _due(db_session, lead, touch=5)

    report = cadence_backlog.scan(db_session, now=AT)
    assert report["read_only"] is True
    assert report["active_enrollments"] == 1
    assert report["due_now"] == 1
    assert report["touches"]["claimed_by_counters"] == 5
    assert report["touches"]["with_a_recorded_send"] == 0
    assert report["touches"]["advanced_without_evidence"] == 5


def test_the_scan_predicts_the_same_outcome_the_engine_produces(
        db_session, sample_org, sample_advisor, monkeypatch):
    from app.services import cadence_backlog
    _sending_on(monkeypatch)
    ok = _lead(db_session, sample_org, sample_advisor, status="sent")
    _due(db_session, ok)
    night = _lead(db_session, sample_org, sample_advisor, phone="12145559999",
                  status="sent")
    _due(db_session, night)
    night.state = "CA"           # 15:00 UTC is 08:00 there - too early
    db_session.commit()

    report = cadence_backlog.scan(db_session, now=AT)
    assert report["if_enabled_now"][cadence_backlog.WOULD_SEND] == 1
    assert report["if_enabled_now"][cadence_backlog.WOULD_BLOCK_HOURS] == 1

    with patch("app.services.sms_service._resolve_twilio_creds",
               return_value=(_twilio(), "+19998887777", None)):
        result = cs.run_due_cadences(db_session, now=AT)
    assert result["sent"] == 1
    assert result["blocked"] == 1


def test_the_scan_breaks_down_by_organization(db_session, sample_org,
                                              sample_advisor, monkeypatch):
    from app.services import cadence_backlog
    _sending_on(monkeypatch)
    lead = _lead(db_session, sample_org, sample_advisor, status="sent")
    _due(db_session, lead)
    rows = cadence_backlog.scan(db_session, now=AT)["by_organization"]
    assert len(rows) == 1
    assert rows[0]["organization"] == sample_org.name
    assert rows[0]["due"] == 1


def test_the_activation_plan_describes_and_does_not_execute(
        db_session, sample_org, sample_advisor):
    from app.services import cadence_backlog
    plan = cadence_backlog.activation_plan(db_session, now=AT)
    assert plan["executed"] is False
    assert plan["read_only"] is True
    assert len(plan["recommended_sequence"]) >= 4
    # The one sentence that has to be in here.
    joined = " ".join(s["why"] for s in plan["recommended_sequence"]).lower()
    assert "backlog" in joined


def test_an_unreadable_phone_number_is_blocked_rather_than_thrown(
        db_session, sample_org, sample_advisor, monkeypatch):
    """`compliance_service.is_phone_suppressed` used to call the COMPLIANCE
    ROUTER'S validator, which raises HTTPException(422). One imported row with
    a seven-digit phone would have thrown an HTTP exception out of this cron
    and ended the whole run, leaving every later lead untouched and unlogged."""
    _sending_on(monkeypatch)
    good = _lead(db_session, sample_org, sample_advisor, status="sent")
    _due(db_session, good)
    bad = _lead(db_session, sample_org, sample_advisor, phone="5551212",
                status="sent")
    bad.phone_raw = None
    db_session.commit()
    _due(db_session, bad)

    with patch("app.services.sms_service._resolve_twilio_creds",
               return_value=(_twilio(), "+19998887777", None)):
        result = cs.run_due_cadences(db_session, now=AT)

    # The run completed. The bad row was refused with a reason; the good one
    # was still reached, which is the part that was at risk.
    assert result["blocked"] == 1
    assert result["sent"] == 1
    assert "not a usable US number" in _logs(db_session, bad)[-1].reason
