"""Oct 16 owner's decision: only a landline / not-a-mobile stops texts.
An unreachable or inactive number keeps its schedule (the phone may be off)."""
from datetime import timedelta

from app.models.models import Message
from app.services import cadence_service as cs
from tests.test_cadence_engine import AT, _due, _lead, _logs, _sending_on, _twilio
from unittest.mock import patch


def _sent(db, lead, adv, hours_ago, status, code):
    db.add(Message(lead_id=lead.id, sender_id=adv.id, body="x", sent_at=AT - timedelta(hours=hours_ago),
                   delivery_status=status, error_code=code))
    db.commit()


def test_two_unreachable_texts_keep_the_schedule(db_session, sample_org, sample_advisor, monkeypatch):
    _sending_on(monkeypatch)
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145552501")
    state = _due(db_session, lead, touch=2)
    _sent(db_session, lead, sample_advisor, 50, "undelivered", "30003")
    _sent(db_session, lead, sample_advisor, 30, "undelivered", "30003")
    tw = _twilio()
    with patch("app.services.sms_service._resolve_twilio_creds", return_value=(tw, "+19998887777", None)):
        r = cs.run_due_cadences(db_session, now=AT)
    db_session.refresh(state)
    assert state.status == "active"
    assert cs._unreachable_reason(db_session, lead) is None


def test_one_unreachable_text_is_not_enough(db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145552502")
    _sent(db_session, lead, sample_advisor, 50, "delivered", None)
    _sent(db_session, lead, sample_advisor, 30, "undelivered", "30003")
    assert cs._unreachable_reason(db_session, lead) is None


def test_a_landline_stops_at_once(db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145552503")
    _sent(db_session, lead, sample_advisor, 30, "undelivered", "30006")
    assert "30006" in cs._unreachable_reason(db_session, lead)


def test_an_inactive_number_keeps_the_schedule(db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145552504")
    _sent(db_session, lead, sample_advisor, 30, "undelivered", "30005")
    assert cs._unreachable_reason(db_session, lead) is None
