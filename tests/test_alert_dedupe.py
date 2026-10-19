"""ONE REPLY, ONE ALERT (Oct 19). Regression tests for the ROOT CAUSES found in
production Oct 16-19 (not the symptom):

  * every text of a back-and-forth paged again           (Tony Ellis: 4 in 3 min)
  * the AI hand-off paged a second time for the same reply (Teresita Roque)
  * the "not handled" reminder paged 3 more times          (Emily Kaelin)
  * the generic hot-reply email doubled the program alert
plus the guarantees the order asks for: duplicate webhooks, concurrent
claims, safe retry, distinct replies kept, useful accurate text.
"""
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.models.program_models import ProgramAlert, ProgramResponse
from app.services import sms_programs
from app.services.programs import alert_ledger as L
from app.services.programs import responses as R
from tests.test_outreach_program import _lead_for, program  # noqa: F401 (fixture)

OWNER_PHONE, OWNER_EMAIL = "+18435550000", "owner@example.com"
KERRY, SCHL, SCHL_EMAIL = "+14105550100", "+14105550200", "mps@example.com"


@pytest.fixture
def wired(program, monkeypatch):
    db, org, prog = program["db"], program["org"], program["prog"]
    org.org_twilio_phone_number = None
    prog.alert_phone, prog.alert_email = OWNER_PHONE, OWNER_EMAIL
    prog.management_recipients = json.dumps([
        {"name": "Kerry Allan", "role": "team", "phone": KERRY},
        {"name": "Michael Schlueter", "role": "team", "phone": SCHL, "email": SCHL_EMAIL}])
    prog.staff_sms_alerts_enabled = True
    db.commit()
    sent = []
    client = MagicMock()
    n = {"i": 0}

    def create(**kw):
        n["i"] += 1
        sent.append(("sms", kw["to"], kw["body"]))
        return SimpleNamespace(sid="SM%04d" % n["i"], status="queued", error_code=None)
    client.messages.create.side_effect = create

    def email(to, subject, body, org=None, **kw):
        sent.append(("email", to, subject + "\n" + body))
        return {"success": True}
    monkeypatch.setattr(sms_programs, "is_sci_org", lambda db_, oid: True)
    monkeypatch.setattr(sms_programs, "sci_sender", lambda: (client, "+18449172171"))
    monkeypatch.setattr("app.services.email_service.send_email_via_provider", email)
    monkeypatch.setenv("APP_BASE_URL", "")
    return dict(program, sent=sent, client=client)


def _per_recipient(sent):
    out = {}
    for ch, to, _ in sent:
        out[(ch, to)] = out.get((ch, to), 0) + 1
    return out


def _reply(db, lead, text, sid, now=None):
    """A stored inbound reply, then the program's handling - as the webhook does."""
    from app.models.models import Reply
    r = Reply(lead_id=lead.id, body=text, twilio_sid=sid)
    db.add(r)
    db.commit()
    return R.on_inbound(db, lead, text, "sms", reply_id=r.id, now=now)


# 1 ─ one reply, one logical event, one text + one email per person ─────────────

def test_one_reply_pages_each_person_once(wired, sample_advisor):
    db, org, sent = wired["db"], wired["org"], wired["sent"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    resp = _reply(db, lead, "Can we meet Thursday afternoon?", "SM_A")
    assert _per_recipient(sent) == {("sms", OWNER_PHONE): 1, ("email", OWNER_EMAIL): 1,
                                    ("sms", KERRY): 1, ("sms", SCHL): 1, ("email", SCHL_EMAIL): 1}
    rows = db.query(ProgramAlert).filter(ProgramAlert.channel != "in_app").all()
    assert {r.dedupe_key for r in rows} == {"reply:%s" % resp.reply_id}
    assert all(r.send_state == "sent" and r.lead_id == lead.id for r in rows)
    assert all(r.provider_id for r in rows if r.channel == "sms")          # auditable: Twilio SID kept


# 2 ─ the same message delivered three times pages nobody again ────────────────

def test_three_webhook_deliveries_of_one_message_page_once(wired, sample_advisor):
    from app.routers.sms_router import process_inbound_sms
    db, org, sent = wired["db"], wired["org"], wired["sent"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    with patch("app.services.reply_classification_service.classify_reply",
               return_value={"classification": "interested", "confidence": "high", "reasoning": "x"}), \
         patch("app.services.notification_service.notify_hot_reply") as legacy:
        for _ in range(3):
            process_inbound_sms(db, org_id=org.id, advisor=None, From="+12145550101",
                                Body="Yes, can someone call me tomorrow?", MessageSid="SMdup1")
    assert db.query(ProgramResponse).filter_by(lead_id=lead.id).count() == 1
    assert all(v == 1 for v in _per_recipient(sent).values()) and len(sent) == 5
    legacy.assert_not_called()                     # no second email from the generic hot-reply path


def test_reprocessing_the_same_reply_does_not_alert_again(wired, sample_advisor):
    db, org, sent = wired["db"], wired["org"], wired["sent"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    first = _reply(db, lead, "Is Saturday possible?", "SM_B")
    again = R.on_inbound(db, lead, "Is Saturday possible?", "sms", reply_id=first.reply_id)
    assert again.id == first.id and len(sent) == 5


# 3 ─ concurrent claims: only one wins ─────────────────────────────────────────

def test_a_page_already_claimed_elsewhere_is_not_sent_again(wired, sample_advisor):
    db, org, prog, sent = wired["db"], wired["org"], wired["prog"], wired["sent"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    # Another worker has claimed the owner's text for this event (committed).
    db.add(ProgramAlert(organization_id=org.id, lead_id=lead.id, kind="active", audience="primary",
                        channel="sms", recipient=OWNER_PHONE, dedupe_key="reply:R1", send_state="sending"))
    db.commit()
    L.page(db, prog, lead_id=lead.id, key="reply:R1", kind="active", sms_text="t", subject="s", html="h",
           coalesce=False)
    db.commit()
    assert ("sms", OWNER_PHONE) not in _per_recipient(sent)
    assert db.query(ProgramAlert).filter_by(dedupe_key="reply:R1", channel="sms", recipient=OWNER_PHONE).count() == 1
    L.page(db, prog, lead_id=lead.id, key="reply:R1", kind="active", sms_text="t", subject="s", html="h",
           coalesce=False)
    db.commit()
    assert all(v == 1 for v in _per_recipient(sent).values())          # a repeat run sends nothing new


def test_the_database_enforces_one_claim_per_key_channel_recipient(wired):
    from sqlalchemy.exc import IntegrityError
    db, org = wired["db"], wired["org"]
    for _ in range(2):
        db.add(ProgramAlert(organization_id=org.id, kind="active", audience="primary", channel="sms",
                            recipient=OWNER_PHONE, dedupe_key="reply:X"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


# 4 ─ a failed page is retried on its own row, never duplicated ─────────────────

def test_failed_page_is_retried_safely(wired, sample_advisor):
    db, org, prog, sent, client = wired["db"], wired["org"], wired["prog"], wired["sent"], wired["client"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    ok_create = client.messages.create.side_effect
    failed = []

    def flaky(**kw):
        if kw["to"] == OWNER_PHONE and not failed:
            failed.append(1)
            raise RuntimeError("Twilio down")
        return ok_create(**kw)
    client.messages.create.side_effect = flaky
    _reply(db, lead, "Please call me", "SM_C")
    row = db.query(ProgramAlert).filter_by(channel="sms", recipient=OWNER_PHONE).one()
    assert row.send_state == "failed" and "Twilio down" in row.reason
    assert L.retry_failed(db, min_age_seconds=0) == 1
    db.refresh(row)
    assert row.send_state == "sent" and row.attempts == 2
    assert db.query(ProgramAlert).filter_by(channel="sms", recipient=OWNER_PHONE).count() == 1
    assert L.retry_failed(db, min_age_seconds=0) == 0                  # nothing left to retry
    assert sum(1 for c, t, _ in sent if c == "sms" and t == OWNER_PHONE) == 1
    assert "Please call me" in [b for c, t, b in sent if c == "sms" and t == OWNER_PHONE][0]


# 5 ─ genuine replies stay separate; a conversation pages once ──────────────────

def test_back_and_forth_pages_once_but_every_reply_is_kept(wired, sample_advisor):
    db, org, sent = wired["db"], wired["org"], wired["sent"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    t0 = datetime.utcnow()
    for i, words in enumerate(["What does the guide cover?", "Is it free?", "Ok thanks", "Got it"]):
        _reply(db, lead, words, "SM_T%d" % i, now=t0 + timedelta(seconds=40 * i))
    assert db.query(ProgramResponse).filter_by(lead_id=lead.id).count() == 4      # nothing discarded
    assert _per_recipient(sent)[("sms", OWNER_PHONE)] == 1                         # was 4 (Tony Ellis)
    merged = db.query(ProgramAlert).filter_by(send_state="merged", channel="sms", recipient=OWNER_PHONE).count()
    assert merged == 3                                                             # each one recorded


def test_two_replies_far_apart_are_two_pages(wired, sample_advisor, monkeypatch):
    db, org, sent = wired["db"], wired["org"], wired["sent"]
    monkeypatch.setenv("PROGRAM_ALERT_COALESCE_MINUTES", "0")
    lead = _lead_for(db, org, sample_advisor, "L001")
    _reply(db, lead, "Is Tuesday open?", "SM_D1")
    _reply(db, lead, "Actually Wednesday works better", "SM_D2")
    assert _per_recipient(sent)[("sms", OWNER_PHONE)] == 2


def test_a_more_important_reply_still_pages(wired, sample_advisor):
    db, org, sent = wired["db"], wired["org"], wired["sent"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    _reply(db, lead, "Ok", "SM_E1")
    _reply(db, lead, "Yes I want to come in and see you Friday at 2", "SM_E2")
    owner = [b for c, t, b in sent if c == "sms" and t == OWNER_PHONE]
    assert len(owner) == 2 and owner[1].startswith("SCI Appointment Request | Ollie Testcase")


# 6 ─ the text says what happened, accurately ───────────────────────────────────

def test_sms_is_specific_accurate_and_one_segment(wired, sample_advisor):
    db, org, sent = wired["db"], wired["org"], wired["sent"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    _reply(db, lead, "Can we meet Thursday afternoon? my cell is 214-555-0199, email ollie@example.com", "SM_F")
    text = [b for c, t, b in sent if c == "sms" and t == OWNER_PHONE][0]
    # A request to meet and a number to call is an appointment request.
    assert text.startswith('SCI Appointment Request | Ollie Testcase: "Can we meet Thursday afternoon?')
    assert "214" not in text and "ollie@example.com" not in text
    assert "Location: Eastern Gate Memorial Gardens." in text and text.endswith("Review in EvoSys Pro.")
    assert len(text) <= 160 and all(ord(c) < 128 for c in text)


def test_sms_carries_a_direct_link_only_when_links_are_on(wired, monkeypatch):
    monkeypatch.setenv("APP_BASE_URL", "https://app.example.com")
    monkeypatch.setattr(sms_programs, "sms_links_enabled", lambda: True)
    assert L.family_link("abc") == "https://app.example.com/program/live?f=abc"
    line = L.sms_line("hot", "Mary Johnson", words="Can we meet Thursday?", location="Restland",
                      link=L.family_link("abc"))
    assert line.endswith("https://app.example.com/program/live?f=abc") and len(line) <= 306
    monkeypatch.setattr(sms_programs, "sms_links_enabled", lambda: False)
    assert L.family_link("abc") is None


def test_preview_never_invents_words():
    assert L.preview("Yes — Thursday \U0001F642 works", 60) == "Yes - Thursday works"
    long = "word " * 80
    p = L.preview(long, 40)
    assert len(p) <= 40 and p.endswith("...") and set(p[:-3].split()) == {"word"}


# 7 ─ email stays descriptive, and is not duplicated ────────────────────────────

def test_email_is_descriptive_and_sent_once(wired, sample_advisor):
    db, org, sent = wired["db"], wired["org"], wired["sent"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    _reply(db, lead, "Can we meet Thursday afternoon?", "SM_G")
    emails = [b for c, t, b in sent if c == "email" and t == OWNER_EMAIL]
    assert len(emails) == 1
    assert "Ollie Testcase" in emails[0] and "Can we meet Thursday afternoon?" in emails[0]
    assert "ollie@example.com" in emails[0]                         # full detail stays in the email


# 8 ─ hand-off, booking and the reminder: one page, not several ─────────────────

def test_handoff_during_a_reply_is_one_page_not_two(wired, sample_advisor):
    db, org, prog, sent = wired["db"], wired["org"], wired["prog"], wired["sent"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    from app.services.programs import ai_setter

    def setter(db_, prog_, lead_, resp_, body, **kw):
        conv = SimpleNamespace(id="C1", state="ai_active", handoff_reason=None)
        ai_setter._handoff(db_, prog_, lead_, conv, "the family wants a person", notify=True)
        assert conv.state == "human"
    with patch.object(ai_setter, "on_family_reply", side_effect=setter):
        _reply(db, lead, "Can a real person call me?", "SM_H")
    owner = [b for c, t, b in sent if c == "sms" and t == OWNER_PHONE]
    assert len(owner) == 1 and owner[0].startswith("SCI Needs Attention | Ollie Testcase")
    em = [b for c, t, b in sent if c == "email" and t == OWNER_EMAIL]
    assert len(em) == 1 and "needs a person" in em[0] and "the family wants a person" in em[0]


def test_booking_during_a_reply_is_one_page(wired, sample_advisor):
    db, org, sent = wired["db"], wired["org"], wired["sent"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    from app.services.programs import ai_setter, appointments

    def setter(db_, prog_, lead_, resp_, body, **kw):
        appointments.notify_team_booked(db_, prog_, lead_, datetime(2026, 10, 23, 14, 0), appointments.VISIT)
    with patch.object(ai_setter, "on_family_reply", side_effect=setter):
        _reply(db, lead, "Thursday at 2 works", "SM_I")
    owner = [b for c, t, b in sent if c == "sms" and t == OWNER_PHONE]
    assert len(owner) == 1 and owner[0].startswith("SCI Booked | Ollie Testcase: booked a ")


def test_booking_from_the_app_pages_once_per_time(wired, sample_advisor):
    db, org, prog, sent = wired["db"], wired["org"], wired["prog"], wired["sent"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    from app.services.programs import appointments
    for _ in range(2):
        appointments.notify_team_booked(db, prog, lead, datetime(2026, 10, 23, 14, 0), appointments.VISIT)
        db.commit()
    assert _per_recipient(sent)[("sms", OWNER_PHONE)] == 1


def test_unhandled_hot_reply_reminds_once_not_three_times(wired, sample_advisor):
    db, org, sent = wired["db"], wired["org"], wired["sent"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    t0 = datetime.utcnow() - timedelta(hours=2)
    _reply(db, lead, "Yes I want to come in and see you Friday at 2", "SM_J", now=t0)
    for k in range(4):
        R.sla_sweep(db, now=t0 + timedelta(minutes=16 + 16 * k))
    owner = [b for c, t, b in sent if c == "sms" and t == OWNER_PHONE]
    assert len(owner) == 2 and owner[1].startswith("SCI Still Waiting | Ollie Testcase")


def test_no_reminder_while_the_ai_is_answering(wired, sample_advisor):
    from app.models.program_models import ProgramConversation
    db, org, sent = wired["db"], wired["org"], wired["sent"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    t0 = datetime.utcnow() - timedelta(hours=2)
    _reply(db, lead, "Yes I want to come in and see you Friday at 2", "SM_K", now=t0)
    db.add(ProgramConversation(organization_id=org.id, lead_id=lead.id, state="offered",
                               last_ai_at=t0 + timedelta(seconds=20)))
    db.commit()
    R.sla_sweep(db, now=t0 + timedelta(minutes=16))
    assert len([1 for c, t, b in sent if c == "sms" and t == OWNER_PHONE]) == 1


def test_same_phone_under_two_names_is_texted_once(wired):
    prog = wired["prog"]
    prog.management_recipients = json.dumps([{"name": "Mike", "role": "team", "phone": "(843) 555-0000"}])
    t = L.staff_targets(prog)
    assert [x for x in t if x[1] == "sms"] == [("primary", "sms", OWNER_PHONE)]


def test_alerts_switched_off_send_nothing_but_are_recorded(wired, sample_advisor):
    db, org, prog, sent = wired["db"], wired["org"], wired["prog"], wired["sent"]
    prog.staff_sms_alerts_enabled = False
    db.commit()
    lead = _lead_for(db, org, sample_advisor, "L001")
    _reply(db, lead, "Hello", "SM_L")
    assert sent == []
    assert db.query(ProgramAlert).filter_by(send_state="skipped").count() == 5
