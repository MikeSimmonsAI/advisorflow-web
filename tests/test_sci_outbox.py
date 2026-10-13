"""Outbox: today's texts and emails, newest first, with channel, message and
status; and families still scheduled later today."""
from datetime import datetime, timedelta

from app.models.models import EmailMessage, Message
from app.models.program_models import ProgramSourceRecord
from tests.test_outreach_program import _h as _hdr, _lead_for, program  # noqa: F401


def test_outbox_shows_what_went_out_and_what_is_next(client, program):
    db, org, admin = program["db"], program["org"], program["admin"]
    rosa = _lead_for(db, org, admin, "L007", first="Rosa")
    pat = _lead_for(db, org, admin, "L003", phone="2145550103", first="Pat", email="pat@example.com")
    now = datetime.utcnow()
    db.add(Message(lead_id=rosa.id, sender_id=admin.id, body="Hi Rosa, this is Kerry Allan.",
                   sent_at=now - timedelta(minutes=5), delivery_status="delivered"))
    db.add(EmailMessage(lead_id=rosa.id, sender_id=admin.id, subject="Following up, Rosa",
                        body_html="<p>Hi Rosa, It&#x27;s Kerry.</p>", status="sent",
                        sent_at=now - timedelta(minutes=3)))
    rec = db.query(ProgramSourceRecord).filter_by(organization_id=org.id, source_lead_id="L003").one()
    rec.approved_to_send_at = now - timedelta(hours=10)
    rec.scheduled_start_at = now + timedelta(minutes=20)
    db.commit()
    r = client.get("/program/outbox", headers=_hdr(db, admin))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["texts_sent"] == 1 and d["emails_sent"] == 1 and d["failed"] == 0
    assert [s["channel"] for s in d["sent"]] == ["email", "text"]          # newest first
    assert d["sent"][0]["subject"] == "Following up, Rosa" and "It's Kerry" in d["sent"][0]["preview"]
    assert d["sent"][1]["status"] == "delivered"
    from zoneinfo import ZoneInfo
    ct = ZoneInfo("America/Chicago")
    utc = ZoneInfo("UTC")
    same_day = (now.replace(tzinfo=utc).astimezone(ct).date()
                == (now + timedelta(minutes=20)).replace(tzinfo=utc).astimezone(ct).date())
    names = [u["name"] for u in d["upcoming"]]
    if same_day:
        assert any(n.startswith("Pat") for n in names)
        pat_row = next(u for u in d["upcoming"] if u["name"].startswith("Pat"))
        assert pat_row["what"] == "First message" and pat_row["channels"]
    assert client.get("/program/outbox?day=bad", headers=_hdr(db, admin)).status_code == 422


def test_a_test_contacts_booking_is_not_counted_on_the_dashboard(client, program):
    from app.models.models import BookingLink, Lead
    db, org, admin = program["db"], program["org"], program["admin"]
    lead = _lead_for(db, org, admin, "L007", first="Rosa")
    lead.is_test = True
    db.add(BookingLink(lead_id=lead.id, user_id=admin.id, status="booked",
                       booked_time=datetime.utcnow() + timedelta(days=1)))
    db.commit()
    d = client.get("/program/dashboard", headers=_hdr(db, admin)).json()
    assert d["reporting"]["appointments"] == 0
