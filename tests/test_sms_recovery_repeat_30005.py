"""Twilio #30222553: pause repeated 30005 outreach, without retrying a text."""
import json
from unittest.mock import patch

import pytest

from app.models.sms_recovery_models import SmsRecoveryEvent, SmsRecoveryPolicy
from app.services import sms_recovery as R
from app.services import sms_recovery_policy as P
from app.services import cadence_service as cs
from tests.test_cadence_engine import AT, _due, _lead, _sending_on, _twilio
from tests.test_sms_recovery_482 import _callback, _items, _msg


def test_second_distinct_failure_holds_and_duplicate_receipts_do_not_count(
        client, db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145554901")
    first = _msg(db_session, lead, sample_advisor, code=None, status="sent", sid="SMrepeat1", hours_ago=26)
    for _ in range(3):
        assert _callback(client, first.twilio_sid, "undelivered", "30005").status_code == 200
    assert _items(db_session, lead) == []
    second = _msg(db_session, lead, sample_advisor, code=None, status="sent", sid="SMrepeat2", hours_ago=1)
    for _ in range(3):
        assert _callback(client, second.twilio_sid, "undelivered", "30005").status_code == 200
    [item] = _items(db_session, lead)
    assert item.failures == 2 and item.trigger_message_id == second.id
    assert item.error_code == "30005" and item.open_lead_id == lead.id
    assert R.send_refusal(db_session, lead).startswith(R.HOLD_TAG)
    assert db_session.query(SmsRecoveryEvent).filter_by(lead_id=lead.id, kind="failure").count() == 2
    assert lead.status == "new" and lead.phone_line_type is None


@pytest.mark.parametrize("gate", [R.ensure_hold_from_history, R.send_refusal])
def test_history_opens_hold_even_when_old_failures_were_already_recorded(
        db_session, sample_org, sample_advisor, gate):
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145554902")
    for n, hours in enumerate((50, 25)):
        msg = _msg(db_session, lead, sample_advisor, code="30005", sid="SMhistorical%s" % n, hours_ago=hours)
        R._event(db_session, sample_org.id, lead.id, "failure", message=msg, code="30005",
                 dedupe="failure:%s" % msg.id)
    db_session.commit()
    assert gate(db_session, lead)
    [item] = _items(db_session, lead)
    assert item.failures == 2
    assert gate(db_session, lead)
    assert len(_items(db_session, lead)) == 1 and item.failures == 2


@pytest.mark.parametrize("latest_status", ["delivered", "sent", "queued"])
def test_old_callback_does_not_hold_a_newer_text(db_session, sample_org, sample_advisor, latest_status):
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145554903")
    _msg(db_session, lead, sample_advisor, code="30005", sid="SMold1", hours_ago=50)
    old = _msg(db_session, lead, sample_advisor, code="30005", sid="SMold2", hours_ago=25)
    _msg(db_session, lead, sample_advisor, code=None, status=latest_status, sid="SMnewer", hours_ago=1)
    assert R.record_failure(db_session, old, source="callback") is None
    assert R.ensure_hold_from_history(db_session, lead) is None


def test_delivered_text_resets_failure_count(db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145554904")
    _msg(db_session, lead, sample_advisor, code="30005", sid="SMbefore", hours_ago=72)
    _msg(db_session, lead, sample_advisor, code=None, status="delivered", sid="SMdelivered", hours_ago=48)
    first = _msg(db_session, lead, sample_advisor, code="30005", sid="SMafter1", hours_ago=24)
    assert R.record_failure(db_session, first, source="callback") is None
    second = _msg(db_session, lead, sample_advisor, code="30005", sid="SMafter2", hours_ago=1)
    assert R.record_failure(db_session, second, source="callback").failures == 2


def test_verified_release_resets_count_and_history_does_not_reopen_it(db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145554905")
    _msg(db_session, lead, sample_advisor, code="30005", sid="SMreview1", hours_ago=50)
    msg = _msg(db_session, lead, sample_advisor, code="30005", sid="SMreview2", hours_ago=25)
    item = R.record_failure(db_session, msg, source="callback")
    R.mark_capable(db_session, item, sample_advisor.id, "Recipient independently confirmed active SMS")
    R.release(db_session, item, sample_advisor.id)
    db_session.commit()
    assert R.record_failure(db_session, msg, source="callback") is None
    assert R.send_refusal(db_session, lead) is None
    new = _msg(db_session, lead, sample_advisor, code="30005", sid="SMreview3", hours_ago=-1)
    assert R.record_failure(db_session, new, source="callback") is None


def test_workspace_override_is_respected(db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145554906")
    db_session.add(SmsRecoveryPolicy(organization_id=sample_org.id, version="explicit-continue", active=True,
                                    rules=json.dumps({"30005": {"action": P.CONTINUE_SCHEDULE}})))
    db_session.commit()
    _msg(db_session, lead, sample_advisor, code="30005", sid="SMoverride1", hours_ago=26)
    latest = _msg(db_session, lead, sample_advisor, code="30005", sid="SMoverride2", hours_ago=1)
    assert R.record_failure(db_session, latest, source="callback") is None


def test_other_failures_and_other_families_do_not_count(db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145554907")
    other = _lead(db_session, sample_org, sample_advisor, phone="12145554908")
    _msg(db_session, other, sample_advisor, code="30005", sid="SMotherlead", hours_ago=26)
    _msg(db_session, lead, sample_advisor, code="30003", sid="SMothercode", hours_ago=25)
    latest = _msg(db_session, lead, sample_advisor, code="30005", sid="SMonlyone", hours_ago=1)
    assert R.record_failure(db_session, latest, source="callback") is None


def test_cadence_holds_repeated_failures_before_provider_send(db_session, sample_org, sample_advisor, monkeypatch):
    _sending_on(monkeypatch)
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145554909")
    state = _due(db_session, lead, touch=1)
    _msg(db_session, lead, sample_advisor, code="30005", sid="SMcadence1", hours_ago=50)
    _msg(db_session, lead, sample_advisor, code="30005", sid="SMcadence2", hours_ago=25)
    tw = _twilio()
    with patch("app.services.sms_service._resolve_twilio_creds", return_value=(tw, "+19998887777", None)):
        cs.run_due_cadences(db_session, now=AT)
    assert tw.messages.create.call_count == 0
    db_session.refresh(state)
    assert state.status == "active" and state.current_touch_number == 1
    [item] = _items(db_session, lead)
    assert item.prevented == 1 and item.failures == 2


def test_manual_send_also_checks_historical_failures(db_session, sample_org, sample_advisor):
    from app.services.sms_service import send_sms
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145554910")
    _msg(db_session, lead, sample_advisor, code="30005", sid="SMmanual1", hours_ago=26)
    _msg(db_session, lead, sample_advisor, code="30005", sid="SMmanual2", hours_ago=1)
    with pytest.raises(ValueError, match="SMS HOLD"):
        send_sms(db=db_session, advisor=sample_advisor, lead=lead, template="Hi", include_booking_link=False,
                 send_source="manual")


@pytest.mark.parametrize("threshold", [True, 1, 2.5, 11, "2"])
def test_policy_rejects_invalid_thresholds(threshold):
    with pytest.raises(ValueError, match="hold_after_failures"):
        P.validate({"30005": {"action": P.HOLD_FOR_REVIEW, "hold_after_failures": threshold}})


def test_policy_round_trip_keeps_threshold():
    assert P.validate(P.DEFAULT_RULES)["30005"]["hold_after_failures"] == 2
    with pytest.raises(ValueError, match="hold_after_failures"):
        P.validate({"30005": {"action": P.CONTINUE_SCHEDULE, "hold_after_failures": 2}})
