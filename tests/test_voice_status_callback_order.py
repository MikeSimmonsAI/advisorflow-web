"""Twilio /voice/status callbacks: replayed and out-of-order deliveries.

Only a terminal status ends a call (sets ended_at), and the stored
twilio_status never moves backwards (queued < initiated < ringing <
in-progress < terminal). No provider is contacted: these are signed inbound
webhooks posted through the test client.
"""
from datetime import datetime

from app.models.models import VoiceCall
from app.routers.voice_router import apply_twilio_call_status


def _call(db, org, lead, advisor, **kw):
    c = VoiceCall(lead_id=lead.id, advisor_id=advisor.id, organization_id=org.id,
                  to_phone="12145550199", status="initiating", direction="outbound",
                  call_sid=kw.pop("call_sid", "CA_status_order_1"), **kw)
    db.add(c)
    db.commit()
    return c


def _post(twilio_webhook, call, status):
    r = twilio_webhook("/voice/status?call_id=%s" % call.id,
                       {"CallSid": call.call_sid, "CallStatus": status})
    assert r.status_code == 200, r.text
    return r


def test_non_terminal_callbacks_do_not_end_the_call(twilio_webhook, db_session, sample_org,
                                                    sample_lead, sample_advisor):
    call = _call(db_session, sample_org, sample_lead, sample_advisor)
    for st in ("queued", "initiated", "ringing", "in-progress"):
        _post(twilio_webhook, call, st)
        db_session.refresh(call)
        assert call.twilio_status == st
        assert call.ended_at is None
    _post(twilio_webhook, call, "completed")
    db_session.refresh(call)
    assert call.twilio_status == "completed"
    assert call.status == "completed"
    assert call.ended_at is not None


def test_late_callbacks_never_regress_status_or_ended_at(twilio_webhook, db_session, sample_org,
                                                         sample_lead, sample_advisor):
    call = _call(db_session, sample_org, sample_lead, sample_advisor, call_sid="CA_status_order_2")
    _post(twilio_webhook, call, "in-progress")
    _post(twilio_webhook, call, "ringing")          # late
    db_session.refresh(call)
    assert call.twilio_status == "in-progress"
    _post(twilio_webhook, call, "completed")
    db_session.refresh(call)
    ended = call.ended_at
    assert ended is not None
    # Replays and stragglers after the terminal status change nothing.
    for st in ("ringing", "in-progress", "initiated", "completed", "failed", "no-answer"):
        _post(twilio_webhook, call, st)
        db_session.refresh(call)
        assert call.twilio_status == "completed"
        assert call.status == "completed"
        assert call.outcome == "completed"
        assert call.ended_at == ended


def test_terminal_first_then_ringing(twilio_webhook, db_session, sample_org, sample_lead, sample_advisor):
    call = _call(db_session, sample_org, sample_lead, sample_advisor, call_sid="CA_status_order_3")
    _post(twilio_webhook, call, "no-answer")
    _post(twilio_webhook, call, "ringing")
    db_session.refresh(call)
    assert call.twilio_status == "no-answer"
    assert call.outcome == "no_answer"
    assert call.ended_at is not None


def test_pure_helper_order_and_unknown_status():
    class C:
        twilio_status = None
        status = "initiating"
        outcome = None
        ended_at = None
    c = C()
    assert apply_twilio_call_status(c, "bogus") is False
    assert c.twilio_status is None
    assert apply_twilio_call_status(c, "ringing") is True
    assert c.ended_at is None
    assert apply_twilio_call_status(c, "queued") is False
    assert c.twilio_status == "ringing"
    t = datetime(2026, 10, 1, 12, 0, 0)
    assert apply_twilio_call_status(c, "failed", now=t) is True
    assert (c.status, c.outcome, c.ended_at) == ("failed", "failed", t)
    assert apply_twilio_call_status(c, "completed") is False
    assert c.status == "failed"
