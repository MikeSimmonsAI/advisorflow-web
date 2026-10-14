"""Self-check compares the plan with what happened and names the problem."""
from datetime import datetime, timedelta

from app.models.inbound_mailbox_models import InboundMailbox, InboundMailboxMessage
from app.models.models import Lead, Message
from app.services.programs import self_check as sc
from tests.test_program_load_source import _program
from tests.test_switch_import_booking import _h


def test_two_texts_hours_apart_are_flagged(db_session, sample_org):
    admin = _program(db_session, sample_org)
    l = Lead(organization_id=sample_org.id, first_name="James", last_name="D", status="sent", assigned_to_id=admin.id)
    db_session.add(l); db_session.commit()
    now = datetime.utcnow()
    for h, sid in ((3, "A"), (1, "B")):
        db_session.add(Message(lead_id=l.id, sender_id=admin.id, body="x", twilio_sid=sid, sent_at=now - timedelta(hours=h)))
    db_session.commit()
    c = sc.texts_too_close(db_session, sample_org.id, now)
    assert c["ok"] is False and c["people"] == ["James D"]


def test_unrecorded_bounce_is_flagged_and_endpoint_reports(client, db_session, sample_org):
    admin = _program(db_session, sample_org)
    box = InboundMailbox(address="support@evosyspro.live", is_active=True, last_polled_at=datetime.utcnow())
    db_session.add(box); db_session.commit()
    db_session.add(InboundMailboxMessage(mailbox_id=box.id, graph_message_id="g", outcome="no_lead",
                                         subject="Undeliverable: Following up, Paula", received_at=datetime.utcnow()))
    db_session.commit()
    r = client.get("/program/self-check", headers=_h(db_session, admin))
    assert r.status_code == 200, r.text
    body = r.json()
    by = {c["key"]: c for c in body["checks"]}
    assert by["bounces_unrecorded"]["ok"] is False
    assert by["texts_too_close"]["ok"] is True
    assert body["ok"] is False and body["problems"] >= 1


def test_got_it_hides_a_problem_until_it_changes(client, db_session, sample_org):
    admin = _program(db_session, sample_org)
    h = _h(db_session, admin)
    l = Lead(organization_id=sample_org.id, first_name="Ann", last_name="B", status="sent", assigned_to_id=admin.id)
    db_session.add(l); db_session.commit()
    now = datetime.utcnow()
    for hrs, sid in ((3, "A1"), (1, "A2")):
        db_session.add(Message(lead_id=l.id, sender_id=admin.id, body="x", twilio_sid=sid, sent_at=now - timedelta(hours=hrs)))
    db_session.commit()
    c = {x["key"]: x for x in client.get("/program/self-check", headers=h).json()["checks"]}["texts_too_close"]
    assert c["ok"] is False and c["acknowledged"] is False
    after = client.post("/program/self-check/ack", headers=h, json={"key": c["key"], "signature": c["signature"]}).json()
    c2 = {x["key"]: x for x in after["checks"]}["texts_too_close"]
    assert c2["acknowledged"] is True and after["acknowledged"] >= 1
    # A NEW person with the same problem changes the signature: it shows again.
    l2 = Lead(organization_id=sample_org.id, first_name="Bo", last_name="C", status="sent", assigned_to_id=admin.id)
    db_session.add(l2); db_session.commit()
    for hrs, sid in ((3, "B1"), (1, "B2")):
        db_session.add(Message(lead_id=l2.id, sender_id=admin.id, body="x", twilio_sid=sid, sent_at=now - timedelta(hours=hrs)))
    db_session.commit()
    c3 = {x["key"]: x for x in client.get("/program/self-check", headers=h).json()["checks"]}["texts_too_close"]
    assert c3["acknowledged"] is False
