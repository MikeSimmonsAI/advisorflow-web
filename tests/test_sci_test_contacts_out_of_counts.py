"""Synthetic / test contacts never reach production reply, alert, health or
setter numbers - but their records are kept for audit."""
from app.models.program_models import ProgramAlert, ProgramResponse
from app.services.programs import responses as R
from tests.test_outreach_program import _h, _lead_for, program  # noqa: F401 (fixture)


def test_test_replies_and_alerts_are_kept_but_not_counted(program, sample_advisor, client):
    db, org = program["db"], program["org"]
    real = _lead_for(db, org, sample_advisor, "L001")
    test = _lead_for(db, org, sample_advisor, "L002", phone="2145550102", email="t@example.com")
    test.is_test = True
    db.commit()
    R.on_inbound(db, real, "Can I schedule a visit?", "sms")
    R.on_inbound(db, test, "Can I schedule a visit?", "sms")
    h = _h(db, program["admin"])
    lst = client.get("/program/responses", headers=h).json()
    ids = {i.get("lead_id") for i in lst.get("items", lst if isinstance(lst, list) else [])}
    assert real.id in ids and test.id not in ids
    assert client.get("/program/responses?include_test=true", headers=h).json()["total"] == 2
    dash = client.get("/program/dashboard", headers=h).json()
    assert db.query(ProgramResponse).count() == 2                      # audit kept
    alerts = client.get("/program/alerts", headers=h).json()
    test_resp = db.query(ProgramResponse).filter_by(lead_id=test.id).one()
    assert not any(a.get("id") in {x.id for x in db.query(ProgramAlert).filter_by(response_id=test_resp.id)}
                   for a in alerts)
    stats = client.get("/program/setter/stats", headers=h).json()
    assert stats["families_replied"] == 1 and stats["setter_mode"] == "off"
    assert dash is not None


def test_an_auto_reply_is_not_counted_as_a_reply(program, sample_advisor, client):
    db, org = program["db"], program["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    R.on_inbound(db, lead, "I'm driving with Do Not Disturb on. I'll see your message when I get where I'm going.", "sms")
    stats = client.get("/program/setter/stats", headers=_h(db, program["admin"])).json()
    assert stats["families_replied"] == 0
