"""Engagement rate: of the people texted or emailed, the share who replied
(not an opt-out), opened their booking page or booked. Test records never
count; opt-outs are reported apart and do not raise the rate."""
from datetime import datetime, timedelta

from app.models.models import BookingLink, EmailMessage, Lead, Message
from app.models.program_models import BookingPageEvent, ProgramResponse
from tests.test_program_load_source import _program
from tests.test_switch_import_booking import _h


def _lead(db, org, admin, name, test=False):
    l = Lead(organization_id=org.id, first_name=name, last_name="X", status="sent",
             assigned_to_id=admin.id, is_test=test)
    db.add(l); db.commit()
    return l


def test_engagement_rate(client, db_session, sample_org):
    admin = _program(db_session, sample_org)
    now = datetime.utcnow()
    people = [_lead(db_session, sample_org, admin, "P%d" % i) for i in range(10)]
    tester = _lead(db_session, sample_org, admin, "Tess", test=True)
    for i, l in enumerate(people + [tester]):
        if i % 2:
            db_session.add(Message(lead_id=l.id, sender_id=admin.id, body="hi", twilio_sid="SM%d" % i,
                                   twilio_status="delivered", sent_at=now - timedelta(days=1)))
        else:
            db_session.add(EmailMessage(lead_id=l.id, sender_id=admin.id, subject="s", body_html="b",
                                        status="sent", sent_at=now - timedelta(days=1)))
    db_session.commit()
    def resp(l, cls):
        db_session.add(ProgramResponse(organization_id=sample_org.id, lead_id=l.id, channel="sms",
                                       response_class=cls, received_at=now))
    resp(people[0], "active")
    resp(people[1], "opt_out")
    resp(tester, "hot")
    db_session.add(BookingPageEvent(organization_id=sample_org.id, lead_id=people[2].id, event="opened"))
    db_session.add(BookingPageEvent(organization_id=sample_org.id, lead_id=people[0].id, event="opened"))
    db_session.add(BookingLink(lead_id=people[3].id, user_id=admin.id, status="booked",
                               booked_time=now + timedelta(days=1)))
    db_session.commit()

    r = client.get("/program/engagement", headers=_h(db_session, admin))
    assert r.status_code == 200, r.text
    a = r.json()["all"]
    assert a["contacted"] == 10                      # the test record is not counted
    assert a["engaged"] == 3                         # P0 (reply+open), P2 (open), P3 (booked)
    assert a["engaged_pct"] == 30.0
    assert a["opted_out"] == 1 and a["responded"] == 4 and a["responded_pct"] == 40.0
    assert a["replied"] == 1 and a["opened_booking"] == 2 and a["booked"] == 1
    assert r.json()["today"]["contacted"] == 0       # everyone was contacted yesterday


def test_message_totals(client, db_session, sample_org):
    admin = _program(db_session, sample_org)
    l = _lead(db_session, sample_org, admin, "Q")
    now = datetime.utcnow()
    db_session.add(Message(lead_id=l.id, sender_id=admin.id, body="a", twilio_sid="S1", twilio_status="delivered",
                           delivery_status="delivered", sent_at=now))
    db_session.add(Message(lead_id=l.id, sender_id=admin.id, body="b", twilio_sid="S2", twilio_status="undelivered",
                           delivery_status="undelivered", sent_at=now))
    db_session.add(EmailMessage(lead_id=l.id, sender_id=admin.id, subject="s", body_html="b", status="bounced", sent_at=now))
    db_session.add(EmailMessage(lead_id=l.id, sender_id=admin.id, subject="s", body_html="b", status="sent", sent_at=now))
    db_session.commit()
    a = client.get("/program/engagement", headers=_h(db_session, admin)).json()["all"]
    assert (a["texts_sent"], a["texts_delivered"], a["emails_sent"], a["failed"]) == (2, 1, 1, 2)
