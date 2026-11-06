"""RESTLAND CHANNELS (Issue #507): the shared toll-free sender, inbound SMS
routing, STOP handling and voice routing, with the production configuration
shape (org number +18553601164, toll-free; lead phones stored as 11 digits
without "+"). Signed webhooks; nothing reaches Twilio."""
from unittest.mock import patch

import pytest

from app.models.models import Lead, Message, Reply
from app.models.telephony_models import InboundCallLog
from app.models.program_models import ProgramResponse
from app.services import sms_service
from app.services.programs import single_location_cutover as C

SENDER = "+18553601164"


@pytest.fixture()
def rl(db_session, sample_org, sample_advisor):
    db = db_session
    sample_org.org_twilio_phone_number = SENDER
    sample_org.org_twilio_number_type = "toll_free"
    sample_advisor.twilio_phone_number = None
    fam = Lead(organization_id=sample_org.id, first_name="Avery", last_name="Synth", phone="12145550001",
               status="new", tier="pre_need")
    db.add(fam)
    db.commit()
    C.apply(db, sample_org, hero_title="Restland")
    return {"db": db, "org": sample_org, "fam": fam, "advisor": sample_advisor}


def test_sender_resolves_to_the_shared_toll_free_number(rl):
    d = sms_service.describe_sms_sender(rl["advisor"], rl["db"])
    assert d.get("from_number", d.get("phone")) == SENDER or SENDER in str(d)


def test_inbound_sms_routes_to_restland_and_sends_nothing(twilio_webhook, rl):
    db = rl["db"]
    with patch("app.services.sms_service.Client") as tw:
        r = twilio_webhook("/sms/webhook/inbound", {"To": SENDER, "From": "+12145550001", "Body": "What times do you have?",
                                                    "MessageSid": "SMrl1"})
        assert r.status_code == 200, r.text
        assert not tw.return_value.messages.create.called          # texts off: no auto-reply
    rep = db.query(Reply).filter(Reply.lead_id == rl["fam"].id).one()
    assert rep.body.startswith("What times")
    assert db.query(ProgramResponse).filter_by(lead_id=rl["fam"].id).count() == 1
    assert db.query(Message).count() == 0


def test_unsigned_inbound_is_refused(twilio_webhook, rl):
    r = twilio_webhook("/sms/webhook/inbound", {"To": SENDER, "From": "+12145550001", "Body": "hi", "MessageSid": "SMrl0"}, sign=False)
    assert r.status_code == 403
    assert rl["db"].query(Reply).count() == 0


def test_stop_suppresses(twilio_webhook, rl):
    db = rl["db"]
    with patch("app.services.sms_service.Client"):
        r = twilio_webhook("/sms/webhook/inbound", {"To": SENDER, "From": "+12145550001", "Body": "STOP",
                                                    "MessageSid": "SMrl2"})
    assert r.status_code == 200
    db.refresh(rl["fam"])
    assert rl["fam"].status == "dnc"


def test_voice_call_to_the_sender_resolves_to_restland(twilio_webhook, rl):
    r = twilio_webhook("/voice/inbound", {"To": SENDER, "From": "+12145550001", "CallSid": "CArl1"})
    assert r.status_code == 200, r.text
    log = rl["db"].query(InboundCallLog).filter_by(call_sid="CArl1").one()
    assert log.organization_id == rl["org"].id and log.lead_id == rl["fam"].id
    # What the caller hears is recorded for the report (no registry row = no voicemail capability).
    print("VOICE TWIML:", r.text[:400])
