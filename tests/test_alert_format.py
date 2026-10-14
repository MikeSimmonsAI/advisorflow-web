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
