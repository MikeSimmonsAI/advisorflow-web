"""SCI staff-alert integrity (P2 audit, Oct 20): an opted-out family is never
paged as hot or reminded about, demo families never page, a held reply is
sent once (or handed to a person), retries respect the alerts switch, and a
booking pages the team exactly once."""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.models.program_models import ProgramAlert, ProgramConversation, ProgramConversationTurn
from app.services.programs import alert_ledger as L
from app.services.programs import responses as R
from tests.test_alert_dedupe import OWNER_PHONE, _reply, wired  # noqa: F401
from tests.test_outreach_program import _lead_for, program  # noqa: F401


def _owner_texts(sent):
    return [b for c, t, b in sent if c == "sms" and t == OWNER_PHONE]


# A. A hard STOP that also mentions a visit/price is DNC on the platform but HOT in SCI.
@pytest.mark.parametrize("body", [
    "Not interested in a visit, stop texting me.",
    "Please stop texts. How much does it cost?",
    "Unsubscribe. I already scheduled a visit with your office",
])
def test_hard_stop_reply_does_not_page_as_hot(wired, sample_advisor, body):
    from app.routers.sms_router import process_inbound_sms
    db, org, sent = wired["db"], wired["org"], wired["sent"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    with patch("app.services.reply_classification_service.classify_reply",
               return_value={"classification": "neutral", "confidence": "high", "reasoning": "x"}):
        process_inbound_sms(db, org_id=org.id, advisor=None, From="+12145550101", Body=body,
                            MessageSid="SM_STOP_%d" % abs(hash(body)))
    db.refresh(lead)
    assert lead.status == "dnc"                                  # platform: legal opt-out
    from app.models.program_models import ProgramResponse
    resp = db.query(ProgramResponse).filter_by(lead_id=lead.id).one()
    hot_pages = _owner_texts(sent)
    assert resp.response_class == "opt_out" and hot_pages == [], (resp.response_class, hot_pages)


# B. HOT reply, then STOP two minutes later: the SLA sweep still pages "Still Waiting".
def test_sla_reminder_after_family_opted_out(wired, sample_advisor):
    db, org, sent = wired["db"], wired["org"], wired["sent"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    t0 = datetime.utcnow() - timedelta(hours=1)
    _reply(db, lead, "Yes I want to come in and see you Friday at 2", "SM_B1", now=t0)
    _reply(db, lead, "STOP", "SM_B2", now=t0 + timedelta(minutes=2))
    before = len(_owner_texts(sent))
    R.sla_sweep(db, now=t0 + timedelta(minutes=20))
    after = _owner_texts(sent)[before:]
    assert after == [], after


# C. A made-up staging demo family never pages anyone (module contract) - but the SLA sweep does.
def test_demo_family_sla_never_pages(wired, sample_advisor):
    from app.services.sci_staging_demo import DEMO_SOURCE
    db, org, sent = wired["db"], wired["org"], wired["sent"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    lead.source = DEMO_SOURCE
    db.commit()
    t0 = datetime.utcnow() - timedelta(hours=1)
    _reply(db, lead, "Yes I want to come in and see you Friday at 2", "SM_C1", now=t0)
    assert sent == []                                            # the reply itself: correctly silent
    R.sla_sweep(db, now=t0 + timedelta(minutes=20))
    assert sent == [], sent


# D. Held-reply sweep: a later failure rolls back an earlier turn's "sent" -> re-sent next sweep.
def test_held_sweep_never_sends_twice(wired, sample_advisor, monkeypatch):
    from app.services.programs import ai_setter
    db, org, prog = wired["db"], wired["org"], wired["prog"]
    prog.ai_setter_mode = "auto"
    db.commit()
    a = _lead_for(db, org, sample_advisor, "L001")
    b = _lead_for(db, org, sample_advisor, "L002", phone="2145550102", first="Bea")
    turns = []
    for lead in (a, b):
        conv = ProgramConversation(organization_id=org.id, lead_id=lead.id, state="offered")
        db.add(conv)
        db.flush()
        t = ProgramConversationTurn(conversation_id=conv.id, organization_id=org.id, lead_id=lead.id,
                                    role="ai", text="Would Tue 10 AM work?", action="offer_times",
                                    delivery="held")
        db.add(t)
        db.flush()
        turns.append(t.id)
    db.commit()
    texts = []

    def fake_send(db_, advisor, lead, body, **kw):
        if lead.id == b.id:
            raise ValueError("spacing: too soon")
        texts.append(lead.id)
        db_.commit()                                             # send_sms commits its Message row
        return SimpleNamespace(id="M%d" % len(texts))
    monkeypatch.setattr("app.services.sms_service.send_sms", fake_send)
    monkeypatch.setattr("app.services.contact_hours.check", lambda lead, now: {"permitted": True})
    monkeypatch.setattr("app.services.programs.appointments.program_advisor", lambda db_, p: sample_advisor)
    ai_setter.sweep(db)
    ai_setter.sweep(db)
    assert texts.count(a.id) == 1, "family A texted %d times" % texts.count(a.id)


# E. Held reply that is refused at send time: family never answered, and nobody is told.
def test_held_reply_refused_at_sweep_alerts_staff(wired, sample_advisor, monkeypatch):
    from app.services.programs import ai_setter
    db, org, prog, sent = wired["db"], wired["org"], wired["prog"], wired["sent"]
    prog.ai_setter_mode = "auto"
    db.commit()
    a = _lead_for(db, org, sample_advisor, "L001")
    conv = ProgramConversation(organization_id=org.id, lead_id=a.id, state="offered")
    db.add(conv)
    db.flush()
    db.add(ProgramConversationTurn(conversation_id=conv.id, organization_id=org.id, lead_id=a.id, role="ai",
                                   text="Would Tue 10 AM work?", action="offer_times", delivery="held"))
    db.commit()

    def refuse(*a_, **k):
        raise ValueError("carrier refused")
    monkeypatch.setattr("app.services.sms_service.send_sms", refuse)
    monkeypatch.setattr("app.services.contact_hours.check", lambda lead, now: {"permitted": True})
    monkeypatch.setattr("app.services.programs.appointments.program_advisor", lambda db_, p: sample_advisor)
    ai_setter.sweep(db)
    t = db.query(ProgramConversationTurn).filter_by(lead_id=a.id).one()
    assert t.delivery == "blocked"
    assert _owner_texts(sent), "held reply refused at send time - no staff alert, conv state=%s" % \
        db.query(ProgramConversation).get(conv.id).state


# F. retry_failed ignores the program's alert switch.
def test_retry_respects_alerts_switched_off(wired, sample_advisor):
    db, org, prog, sent, client = wired["db"], wired["org"], wired["prog"], wired["sent"], wired["client"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    ok_create = client.messages.create.side_effect

    def flaky(**kw):
        if kw["to"] == OWNER_PHONE:
            raise RuntimeError("Twilio down")
        return ok_create(**kw)
    client.messages.create.side_effect = flaky
    _reply(db, lead, "Please call me", "SM_F1")
    client.messages.create.side_effect = ok_create
    prog.staff_sms_alerts_enabled = False                        # owner switches alerts off
    db.commit()
    L.retry_failed(db, min_age_seconds=0)
    assert _owner_texts(sent) == [], _owner_texts(sent)


# G. An already-booked family confirming the same time again pages "SCI Booked" again.
def test_reconfirming_existing_booking_does_not_page_booked_again(wired, sample_advisor, monkeypatch):
    from app.models.models import BookingLink
    from app.services.programs import ai_setter
    db, org, prog, sent = wired["db"], wired["org"], wired["prog"], wired["sent"]
    prog.ai_setter_mode = "auto"
    prog.booking_user_id = sample_advisor.id
    db.commit()
    lead = _lead_for(db, org, sample_advisor, "L001")
    slot = (datetime.utcnow() + timedelta(days=3)).replace(hour=14, minute=0, second=0, microsecond=0)
    db.add(BookingLink(lead_id=lead.id, user_id=sample_advisor.id, status="booked", booked_time=slot,
                       token="tok-probe-g"))
    conv = ProgramConversation(organization_id=org.id, lead_id=lead.id, state="booked", booked_time=slot,
                               offered_slots='["%s"]' % slot.isoformat(), last_ai_at=datetime.utcnow())
    db.add(conv)
    db.commit()
    # first: the original booking page, an hour ago
    from app.services.programs import appointments as A
    A.notify_team_booked(db, prog, lead, slot, A.VISIT)
    db.query(ProgramAlert).update({"created_at": datetime.utcnow() - timedelta(hours=1)})
    db.commit()
    n0 = len(_owner_texts(sent))
    monkeypatch.setattr(ai_setter, "decide", lambda *a, **k: {"action": "book", "slot_choice": 1, "reply": "",
                                                              "confidence": 1.0})
    monkeypatch.setattr(ai_setter, "validate_decision", lambda *a, **k: [])
    monkeypatch.setattr(ai_setter, "choice_evidence", lambda *a, **k: True)
    monkeypatch.setattr(ai_setter, "_over_limit", lambda *a, **k: None)
    monkeypatch.setattr(ai_setter, "_human_took_over", lambda *a, **k: False)
    monkeypatch.setattr(ai_setter, "decline_context", lambda *a, **k: None)
    _reply(db, lead, "Yes 2pm Thursday still works, see you then", "SM_G1")
    new = _owner_texts(sent)[n0:]
    assert not any(t.startswith("SCI Booked") for t in new), new


def test_a_booking_page_booking_pages_the_team_once_and_never_by_the_legacy_path(program, sample_advisor):
    """P2 #8: an SCI family booking on the page gets ONE ledger page; the
    advisor's legacy booking text / email never fire for SCI families."""
    import asyncio
    from datetime import date, datetime, timedelta
    from unittest.mock import patch
    from app.models.program_models import ProgramAlert
    from app.routers.calendar_router import booking_confirmed_webhook
    from app.services.programs import appointments as A
    from app.services.sms_service import get_or_create_booking_link
    from tests.test_outreach_program import _lead_for
    db, prog = program["db"], program["prog"]
    prog.booking_user_id = sample_advisor.id
    db.commit()
    lead = _lead_for(db, program["org"], sample_advisor, "L003", phone="2145550103", first="Page")
    lead.phone = None
    db.commit()
    day = date.today() + timedelta(days=3)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    slot = datetime(day.year, day.month, day.day, 11, 0)
    link = get_or_create_booking_link(db, lead, sample_advisor)
    db.commit()
    with patch("app.routers.calendar_router._send_booking_notification_email") as legacy_mail, \
         patch.object(A, "notify_team_booked") as page:
        asyncio.run(booking_confirmed_webhook(A._InternalRequest(
            {"booking_token": link.token, "slot_display": slot.strftime("%Y-%m-%dT%H:%M:00")}), db))
    assert legacy_mail.call_count == 0
    assert page.call_count == 1
    # the setter's own booking is paged by the setter, not twice
    lead2 = _lead_for(db, program["org"], sample_advisor, "L004", phone="2145550104", first="Setter")
    lead2.phone = None
    db.commit()
    with patch.object(A, "notify_team_booked") as page2:
        assert A.book(db, prog, lead2, slot.replace(hour=13), A.VISIT)["status"] == "confirmed"
    assert page2.call_count == 0
    assert ProgramAlert is not None


def test_a_deliberately_blocked_held_reply_pages_nobody(wired, sample_advisor, monkeypatch):
    """Review fix: campaign off / DNC is a policy block, not a failure - no page storm."""
    from app.services.programs import ai_setter
    db, org, prog, sent = wired["db"], wired["org"], wired["prog"], wired["sent"]
    prog.ai_setter_mode = "auto"
    db.commit()
    a = _lead_for(db, org, sample_advisor, "L001")
    conv = ProgramConversation(organization_id=org.id, lead_id=a.id, state="offered")
    db.add(conv)
    db.flush()
    db.add(ProgramConversationTurn(conversation_id=conv.id, organization_id=org.id, lead_id=a.id, role="ai",
                                   text="Would Tue 10 AM work?", action="offer_times", delivery="held"))
    db.commit()

    def refuse(*a_, **k):
        raise ValueError("Lead x: CAMPAIGN OFF: this contact's campaign is not switched on")
    monkeypatch.setattr("app.services.sms_service.send_sms", refuse)
    monkeypatch.setattr("app.services.contact_hours.check", lambda lead, now: {"permitted": True})
    monkeypatch.setattr("app.services.programs.appointments.program_advisor", lambda db_, p: sample_advisor)
    before = len(_owner_texts(sent))
    ai_setter.sweep(db)
    assert db.query(ProgramConversationTurn).filter_by(lead_id=a.id).one().delivery == "blocked"
    assert _owner_texts(sent)[before:] == []


def test_a_text_stop_does_not_close_a_later_email_asking_to_come_in(wired, sample_advisor):
    """Review fix: the reminder sweep honours the opt-out of the reply's own channel."""
    db, org = wired["db"], wired["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    t0 = datetime.utcnow() - timedelta(hours=1)
    _reply(db, lead, "STOP", "SM_C1", now=t0)
    with patch("app.services.programs.responses._deliver_staff_alert", return_value=(True, None)):
        resp = R.on_inbound(db, lead, "I changed my mind - I want to come in and see you Friday at 2", "email",
                            reply_id="EM_C2", now=t0 + timedelta(minutes=5))
    assert resp.response_class == "hot" and resp.handling_status == "new"
    R.sla_sweep(db, now=t0 + timedelta(minutes=30))
    db.refresh(resp)
    assert resp.handling_status == "new"
