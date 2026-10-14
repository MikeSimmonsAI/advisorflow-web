"""Oct 14 alert format: "EvoSys Pro SCI POC" header, how they replied, what
they said, and both phone and email (the way they replied first)."""
import json
from unittest.mock import patch

from app.models.program_models import ProgramResponse
from app.services.programs import responses
from tests.test_outreach_program import _lead_for, program  # noqa: F401 (fixture)


def test_email_reply_alert_has_header_both_contacts_email_first(program, sample_advisor, client):
    db, org, prog = program["db"], program["org"], program["prog"]
    prog.alert_title = "EvoSys Pro SCI POC"
    prog.alert_phone, prog.alert_email = "+18435550000", "owner@example.com"
    prog.management_recipients = json.dumps([{"name": "Kerry", "phone": "+14105550100", "role": "team"}])
    prog.staff_sms_alerts_enabled = True
    db.commit()
    lead = _lead_for(db, org, sample_advisor, "L001", email="larry@example.com")
    sent = []

    def fake_create(**kw):
        sent.append(("sms", kw["to"], kw["body"]))

    def fake_email(to, subject, body, org=None, **kw):
        sent.append(("email", to, subject + "\n" + body))
        return {"success": True}
    with patch("app.services.sms_service._org_twilio_credentials", return_value=("AC", "tok")), \
         patch("app.services.sms_service.Client") as C, \
         patch("app.services.email_service.send_email_via_provider", side_effect=fake_email):
        org.org_twilio_phone_number = "+18449172171"
        db.commit()
        C.return_value.messages.create.side_effect = fake_create
        responses.on_inbound(db, lead, "Yes I will get back with you", "email")
    sms = [b for c, to, b in sent if c == "sms"]
    assert sms and all(b.startswith("EvoSys Pro SCI POC - New Reply") for b in sms)
    body = sms[0]
    assert "Replied by: Email" in body and "larry@example.com" in body
    assert body.index("Email larry@example.com") < body.index("Phone")
    assert {to for c, to, b in sent} >= {"+18435550000", "owner@example.com", "+14105550100"}
    em = [b for c, to, b in sent if c == "email"][0]
    assert em.startswith("EvoSys Pro SCI POC - New reply from") and "(Email)" in em.split("\n")[0]
    resp = db.query(ProgramResponse).filter_by(lead_id=lead.id).one()
    print("\n" + responses.alert_content(db, prog, resp, resp.response_class)["sms"])


def test_sci_staff_text_uses_the_toll_free_line(program, monkeypatch):
    from unittest.mock import MagicMock
    from app.services import sms_programs
    db, prog = program["db"], program["prog"]
    program["org"].org_twilio_phone_number = None
    db.commit()
    client = MagicMock()
    monkeypatch.setattr(sms_programs, "is_sci_org", lambda db_, oid: True)
    monkeypatch.setattr(sms_programs, "sci_sender", lambda: (client, "+18449172171"))
    ok, why = responses._deliver_staff_alert(db, prog, "sms", "+18435550000", "hello", kind="active")
    assert ok and why is None
    kw = client.messages.create.call_args.kwargs
    assert kw["from_"] == "+18449172171" and kw["to"] == "+18435550000"


def test_accepted_text_is_not_delivered_until_twilio_says_so(program, sample_advisor, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from app.models.program_models import ProgramAlert
    from app.services import sms_programs
    db, org, prog = program["db"], program["org"], program["prog"]
    org.org_twilio_phone_number = None
    prog.alert_phone, prog.alert_email = "+18435550000", None
    prog.staff_sms_alerts_enabled = True
    db.commit()
    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(sid="SMtest123", status="queued", error_code=None)
    monkeypatch.setattr(sms_programs, "is_sci_org", lambda db_, oid: True)
    monkeypatch.setattr(sms_programs, "sci_sender", lambda: (client, "+18449172171"))
    lead = _lead_for(db, org, sample_advisor, "L001")
    responses.on_inbound(db, lead, "Yes please call me", "sms")
    a = db.query(ProgramAlert).filter_by(channel="sms", recipient="+18435550000").one()
    assert a.provider_id == "SMtest123" and a.delivered is False and "waiting" in a.reason
    assert responses.apply_sms_receipt(db, "SMtest123", "undelivered", "30007", "Carrier filtered")
    db.commit(); db.refresh(a)
    assert a.delivered is False and "30007" in a.reason
    responses.apply_sms_receipt(db, "SMtest123", "delivered", None, None)
    db.commit(); db.refresh(a)
    assert a.delivered is True


def test_owner_test_sms_sends_exact_text_to_owner_only(program, client, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from app.models.program_models import ProgramAlert
    from app.services import sms_programs
    from tests.test_outreach_program import _h
    db, prog = program["db"], program["prog"]
    prog.alert_phone = "+18435550000"
    prog.management_recipients = '[{"name":"K","phone":"+14105550100","role":"team"}]'
    db.commit()
    tw = MagicMock()
    tw.messages.create.return_value = SimpleNamespace(sid="SMx1", status="queued")
    monkeypatch.setattr(sms_programs, "sci_sender", lambda: (tw, "+18449172171"))
    msg = "EvoSys Pro SCI POC: A new family response needs attention. Please check your SCI Responses inbox."
    r = client.post("/program/alerts/test-sms", headers=_h(db, program["admin"]), json={"message": msg}).json()
    assert r["accepted"] and r["sid"] == "SMx1"
    kw = tw.messages.create.call_args.kwargs
    assert kw["body"] == msg and kw["to"] == "+18435550000" and tw.messages.create.call_count == 1
    a = db.query(ProgramAlert).filter_by(provider_id="SMx1").one()
    assert a.kind == "test" and a.delivered is False
