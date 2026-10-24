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
                                         organization_id=sample_org.id,
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


# ── Oct 16: 13 false "texts too close" findings ───────────────────────────────
def _fam(db, org, admin, first):
    l = Lead(organization_id=org.id, first_name=first, last_name="T", status="sent", assigned_to_id=admin.id)
    db.add(l); db.commit()
    return l


def _txt(db, lead, admin, hours_ago, now, *, status="delivered", source="cadence", sid=None):
    db.add(Message(lead_id=lead.id, sender_id=admin.id, body="x", twilio_sid=sid or "S%s%s" % (lead.first_name, hours_ago),
                   sent_at=now - timedelta(hours=hours_ago), delivery_status=status, send_source=source))
    db.commit()


def test_resend_after_a_carrier_refused_text_is_not_too_close(db_session, sample_org):
    """10 families: text 3 undelivered (30007), resent the next day."""
    admin = _program(db_session, sample_org)
    now = datetime.utcnow()
    l = _fam(db_session, sample_org, admin, "Paula")
    _txt(db_session, l, admin, 70, now)                         # text 2, delivered
    _txt(db_session, l, admin, 19, now, status="undelivered")   # text 3, carrier refused
    _txt(db_session, l, admin, 1, now)                          # text 3 resent
    assert sc.texts_too_close(db_session, sample_org.id, now)["ok"] is True


def test_answering_a_family_who_wrote_back_is_not_too_close(db_session, sample_org):
    """Ronald Eller (AI reply) / Thomas Calloway (a person's reply)."""
    from app.models.program_models import ProgramResponse
    admin = _program(db_session, sample_org)
    now = datetime.utcnow()
    for first, src in (("Ronald", "sci_appointment_setter"), ("Thomas", "manual")):
        l = _fam(db_session, sample_org, admin, first)
        _txt(db_session, l, admin, 5, now)
        db_session.add(ProgramResponse(organization_id=sample_org.id, lead_id=l.id, body_excerpt="yes please", channel="sms",
                                       response_class="interested", received_at=now - timedelta(hours=3)))
        db_session.commit()
        _txt(db_session, l, admin, 2, now, source=src)
    assert sc.texts_too_close(db_session, sample_org.id, now)["ok"] is True


def test_a_real_premature_cadence_text_is_still_caught(db_session, sample_org):
    admin = _program(db_session, sample_org)
    now = datetime.utcnow()
    l = _fam(db_session, sample_org, admin, "Edna")
    _txt(db_session, l, admin, 5, now)
    _txt(db_session, l, admin, 2, now)
    c = sc.texts_too_close(db_session, sample_org.id, now)
    assert c["ok"] is False and c["people"] == ["Edna T"]


def test_shared_mailbox_ndrs_without_an_org_are_not_false_sci_failures(db_session, sample_org):
    """The shared support mailbox receives delivery notices across tenants.

    No organization was resolved for this notice; SCI's per-program health
    check must not claim it is an SCI family's bounce.
    """
    from app.models.inbound_mailbox_models import InboundMailbox, InboundMailboxMessage
    box = InboundMailbox(address="shared-poc@example.test", is_active=True,
                         last_polled_at=datetime.utcnow())
    db_session.add(box)
    db_session.commit()
    db_session.add(InboundMailboxMessage(mailbox_id=box.id, graph_message_id="unassigned-ndr",
                                         organization_id=None, outcome="no_lead",
                                         subject="Undeliverable: Shared-mailbox notice",
                                         received_at=datetime.utcnow()))
    db_session.commit()
    assert sc.bounces_unrecorded(db_session, sample_org.id, datetime.utcnow())["ok"] is True
