"""SCI spacing across EVERY automated path (Oct 14 audit: 23 families got two
texts under 20 hours apart, all cadence touch 1 -> touch 2 after an overnight
hold). The gap counts from the last text that really went to the family, by
any path; send_sms refuses automated SCI texts inside it; a refusal is a hold,
never a failed attempt; people texting by hand and replies after the family
wrote back are not limited."""
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.models.models import CadenceState, CadenceTouchLog, Message
from app.services import cadence_service as cs
from app.services import send_source as ss
from app.services.programs import spacing
from tests.test_cadence_engine import AT, _due, _lead, _logs, _sending_on, _twilio


def _text(db, lead, advisor, at, source=None):
    m = Message(lead_id=lead.id, sender_id=advisor.id, body="earlier", twilio_sid="SMx",
                sent_at=at, send_source=source)
    db.add(m)
    db.commit()
    return m


# ── the rule ────────────────────────────────────────────────────────────────

def test_rule_refuses_automated_inside_the_gap_only(db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor)
    _text(db_session, lead, sample_advisor, AT - timedelta(hours=7), ss.CADENCE)
    for src in (ss.CADENCE, ss.CAMPAIGN, ss.BULK, ss.AI_CONVERSATION, None):
        assert spacing.refusal(db_session, lead, src, now=AT).startswith(spacing.TOO_SOON), src
    for src in (ss.MANUAL, ss.AUTO_SEND, ss.APPOINTMENT_FOLLOWUP):
        assert spacing.refusal(db_session, lead, src, now=AT) is None, src
    assert spacing.refusal(db_session, lead, ss.CADENCE, now=AT + timedelta(hours=14)) is None


def test_a_family_reply_reopens_the_conversation(db_session, sample_org, sample_advisor):
    from app.models.program_models import ProgramResponse
    lead = _lead(db_session, sample_org, sample_advisor)
    _text(db_session, lead, sample_advisor, AT - timedelta(hours=3), ss.CADENCE)
    db_session.add(ProgramResponse(organization_id=sample_org.id, lead_id=lead.id, channel="sms",
                                   response_class="active", received_at=AT - timedelta(hours=1)))
    db_session.commit()
    assert spacing.refusal(db_session, lead, ss.AI_CONVERSATION, now=AT) is None


# ── the send backstop (send_sms, SCI) ───────────────────────────────────────

def _as_sci(client):
    return [patch("app.services.sms_programs.is_sci_org", return_value=True),
            patch("app.services.sms_programs.sci_send_refusal", return_value=None),
            patch("app.services.sms_programs.sci_sender", return_value=(client, "+18449172171")),
            patch("app.services.sms_service._final_send_guards", return_value=None)]


def test_send_sms_refuses_an_automated_sci_text_inside_the_gap(db_session, sample_org, sample_advisor):
    from app.services import sms_service
    lead = _lead(db_session, sample_org, sample_advisor)
    _text(db_session, lead, sample_advisor, cs.datetime.utcnow() - timedelta(hours=5), ss.CADENCE)
    tw = _twilio()
    ps = _as_sci(tw)
    for p in ps:
        p.start()
    try:
        with pytest.raises(sms_service.SciSmsBlocked) as e:
            sms_service.send_sms(db_session, sample_advisor, lead, "Hello", send_source=ss.CADENCE)
        assert "TOO_SOON" in str(e.value)
        assert tw.messages.create.call_count == 0                 # nothing reached Twilio
        sms_service.send_sms(db_session, sample_advisor, lead, "Kerry here", send_source=ss.MANUAL)
        assert tw.messages.create.call_count == 1                 # a person may still text
    finally:
        for p in ps:
            p.stop()


# ── the cadence ─────────────────────────────────────────────────────────────

def test_cadence_waits_for_a_text_sent_by_another_path(db_session, sample_org, sample_advisor, monkeypatch):
    _sending_on(monkeypatch)
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145552401")
    state = _due(db_session, lead)                       # touch 1 due
    _text(db_session, lead, sample_advisor, AT - timedelta(hours=2), None)   # e.g. a campaign blast
    tw = _twilio()
    with patch("app.services.sms_service._resolve_twilio_creds", return_value=(tw, "+19998887777", None)):
        r = cs.run_due_cadences(db_session, now=AT)
        assert r["sent"] == 0 and tw.messages.create.call_count == 0
        db_session.refresh(state)
        assert state.next_touch_due_at == AT - timedelta(hours=2) + timedelta(hours=20)
        held = [l for l in _logs(db_session, lead) if l.outcome == cs.OUTCOME_SKIPPED]
        assert held and held[0].reason.startswith("spacing")
        # once the gap has passed it goes, exactly once
        r = cs.run_due_cadences(db_session, now=AT + timedelta(hours=24))   # in contact hours
    assert r["sent"] == 1 and tw.messages.create.call_count == 1


def test_a_spacing_refusal_is_a_hold_not_a_failed_attempt(db_session, sample_org, sample_advisor, monkeypatch):
    from app.services import sms_service
    _sending_on(monkeypatch)
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145552402")
    state = _due(db_session, lead)
    with patch("app.services.sms_service.send_sms",
               side_effect=sms_service.SciSmsBlocked(["TOO_SOON"])), \
         patch("app.services.cadence_service._spacing_hold_until", return_value=None):
        cs.run_due_cadences(db_session, now=AT)
    logs = _logs(db_session, lead)
    assert [l.outcome for l in logs] == [cs.OUTCOME_SKIPPED]
    assert cs._failed_attempts(db_session, state, 1) == 0


def test_oct13_replay_touch2_never_lands_hours_after_a_late_touch1(db_session, sample_org, sample_advisor,
                                                                   monkeypatch):
    """The real case: cadence started at night, touch 1 held to the next
    afternoon, touch 2 due by the start-day schedule that same evening."""
    _sending_on(monkeypatch)
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145552403")
    state = _due(db_session, lead)
    state.cadence_started_at = AT - timedelta(hours=17)       # 22:00 the night before
    db_session.commit()
    tw = _twilio()
    with patch("app.services.sms_service._resolve_twilio_creds", return_value=(tw, "+19998887777", None)):
        assert cs.run_due_cadences(db_session, now=AT)["sent"] == 1          # touch 1, late
        db_session.refresh(state)
        state.next_touch_due_at = AT + timedelta(hours=7)                     # start-day schedule says "due"
        db_session.commit()
        for h in (8, 12, 19):
            assert cs.run_due_cadences(db_session, now=AT + timedelta(hours=h))["sent"] == 0
        sends = db_session.query(Message).filter(Message.lead_id == lead.id).order_by(Message.sent_at).all()
    assert len(sends) == 1 and tw.messages.create.call_count == 1
    db_session.refresh(state)
    assert state.next_touch_due_at >= AT + timedelta(hours=20)
