"""A late touch never pulls the next one in behind it (Oct 14: touch 1 held
overnight went at 8:16, touch 2 - already "due" on the start-day schedule -
went at 10:38). Each text waits the schedule's gap after the last text that
actually went out."""
from datetime import timedelta
from unittest.mock import patch

from app.models.models import CadenceState, Message
from app.services import cadence_service as cs
from tests.test_cadence_engine import AT, _due, _lead, _logs, _sending_on, _twilio


def test_second_touch_waits_its_gap_after_a_late_first(db_session, sample_org, sample_advisor, monkeypatch):
    _sending_on(monkeypatch)
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145552301")
    state = _due(db_session, lead)
    # Started 3 days ago, so on the start-day schedule touch 2 is long overdue.
    state.cadence_started_at = AT - timedelta(days=3)
    db_session.commit()
    with patch("app.services.sms_service._resolve_twilio_creds",
               return_value=(_twilio(), "+19998887777", None)):
        r1 = cs.run_due_cadences(db_session, now=AT)
        assert r1["sent"] == 1
        db_session.refresh(state)
        gap = cs._touch_gap(cs._get_org_cadence_schedule(db_session, lead.organization_id), 2)
        assert state.next_touch_due_at >= AT + gap - timedelta(minutes=1)
        # Even if something forces it due again two hours later, it is held.
        state.next_touch_due_at = AT + timedelta(minutes=5)
        db_session.commit()
        r2 = cs.run_due_cadences(db_session, now=AT + timedelta(hours=2))
    assert r2["sent"] == 0
    assert db_session.query(Message).filter(Message.lead_id == lead.id).count() == 1
    db_session.refresh(state)
    assert state.next_touch_due_at >= AT + timedelta(hours=20)


def test_gap_is_never_under_twenty_hours():
    sched = [{"touch_number": 1, "day_offset": 0}, {"touch_number": 2, "day_offset": 0},
             {"touch_number": 3, "day_offset": 3}]
    assert cs._touch_gap(sched, 2) == timedelta(hours=20)
    assert cs._touch_gap(sched, 3) == timedelta(days=3)
