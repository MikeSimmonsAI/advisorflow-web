"""The spacing audit is read-only and finds every contact with two texts too close."""
from datetime import datetime, timedelta

from app.models.models import Message
from tests.test_outreach_program import _h, _lead_for, program  # noqa: F401 (fixture)


def test_spacing_audit_lists_close_texts_and_sends_nothing(program, sample_advisor, client):
    db, org = program["db"], program["org"]
    lead = _lead_for(db, org, sample_advisor, "L001", email="f@example.com")
    now = datetime.utcnow()
    for h in (10, 6):
        db.add(Message(lead_id=lead.id, sender_id=sample_advisor.id, body="hi", twilio_sid="SM%d" % h,
                       sent_at=now - timedelta(hours=h)))
    db.commit()
    r = client.get("/program/audit/spacing?days=2", headers=_h(db, program["admin"])).json()
    assert r["contacts"] == 1
    item = r["items"][0]
    assert item["close_pairs"][0]["hours_apart"] == 4.0 and len(item["texts"]) == 2
    assert db.query(Message).filter(Message.lead_id == lead.id).count() == 2


def test_lead_audit_shows_texts_and_is_org_scoped(program, sample_advisor, client):
    db, org = program["db"], program["org"]
    lead = _lead_for(db, org, sample_advisor, "L002", email="g@example.com")
    db.add(Message(lead_id=lead.id, sender_id=sample_advisor.id, body="hi", twilio_sid="SMx",
                   sent_at=datetime.utcnow()))
    db.commit()
    r = client.get("/program/audit/lead/%s" % lead.id, headers=_h(db, program["admin"]))
    assert r.status_code == 200 and len(r.json()["texts"]) == 1
    assert client.get("/program/audit/lead/nope", headers=_h(db, program["admin"])).status_code == 404
