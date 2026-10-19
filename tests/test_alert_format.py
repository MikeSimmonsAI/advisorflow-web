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
    # Oct 19: the text says what happened - type, name, their words, location -
    # with no phone number, email or link, in one segment.
    assert sms and all(b.startswith('SCI Reply | Ollie Testcase: "Yes I will get back with you"') for b in sms)
    assert all("larry@example.com" not in b and "555" not in b and len(b) <= 160 for b in sms)
    assert len(sms) == 2                                  # owner + Kerry, once each
    assert {to for c, to, b in sent} >= {"+18435550000", "owner@example.com", "+14105550100"}
    em = [b for c, to, b in sent if c == "email"][0]
    assert em.startswith("EvoSys Pro SCI POC - New reply from") and "(Email)" in em.split("\n")[0]
    assert "larry@example.com" in em and "Yes I will get back" in em     # details stay in the email


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



def test_simulated_reply_runs_the_real_alert_path_once_per_person(program, client, monkeypatch):
    import json as _j
    from unittest.mock import patch as _p
    from tests.test_outreach_program import _h
    db, prog = program["db"], program["prog"]
    prog.alert_title = "EvoSys Pro SCI POC"
    prog.alert_phone, prog.alert_email = "+18435550000", "owner@example.com"
    prog.management_recipients = _j.dumps([{"name": "K", "phone": "+14105550100", "role": "team"},
                                           {"name": "M", "phone": "+15405550100", "email": "m@example.com",
                                            "role": "team"}])
    prog.staff_sms_alerts_enabled = True
    db.commit()
    calls = []

    def fake(db_, prog_, ch, to, msg, **kw):
        calls.append((ch, to))
        return True, None
    with _p("app.services.programs.responses._deliver_staff_alert", side_effect=fake):
        r = client.post("/program/alerts/simulate-reply", headers=_h(db, program["admin"]),
                        json={"text": "What does the guide include?", "channel": "sms"}).json()
        assert r["class"] in ("active", "low")
        assert sorted(calls) == sorted([("sms", "+18435550000"), ("email", "owner@example.com"),
                                        ("sms", "+14105550100"), ("sms", "+15405550100"),
                                        ("email", "m@example.com")])        # exactly one each
        calls.clear()
        r = client.post("/program/alerts/simulate-reply", headers=_h(db, program["admin"]),
                        json={"text": "STOP", "channel": "sms"}).json()
        assert r["class"] == "opt_out" and calls == []                       # opt-outs alert nobody


def test_synthetic_contact_alerts_are_marked_test(program, sample_advisor):
    db, org, prog = program["db"], program["org"], program["prog"]
    prog.alert_title = "EvoSys Pro SCI POC"
    db.commit()
    from app.models.models import Lead
    lead = Lead(organization_id=org.id, first_name="SCI Alert", last_name="Test (synthetic)", status="new",
                is_test=True, assigned_to_id=sample_advisor.id)
    db.add(lead)
    db.commit()
    resp = ProgramResponse(organization_id=org.id, lead_id=lead.id, channel="sms", response_class="active",
                           body_excerpt="What does the guide include?")
    db.add(resp)
    db.commit()
    c = responses.alert_content(db, prog, resp, "active")
    assert c["sms"].startswith("TEST SCI Reply | ") and '"What does the guide include?"' in c["sms"]
    assert c["subject"].startswith("TEST - EvoSys Pro SCI POC")
    assert "TEST ONLY - not a real family" in c["html"]
    lead.is_test = False
    db.commit()
    c = responses.alert_content(db, prog, resp, "active")
    assert not c["sms"].startswith("TEST") and not c["subject"].startswith("TEST")
