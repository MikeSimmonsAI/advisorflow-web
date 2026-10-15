"""SCI staging demo families: staging-only, fills SCI Command, rebuilt not
doubled, never texted, never built outside the SCI staging database."""
import pytest

from app.models.models import BookingLink, Lead
from app.models.program_models import ProgramResponse
from app.services import environment, sci_staging_bootstrap, sci_staging_demo as demo
from tests.test_outreach_program import _h, program  # noqa: F401 (fixture)


@pytest.fixture
def staging(monkeypatch, program):
    monkeypatch.setattr(environment, "current", lambda: environment.ENV_STAGING)
    monkeypatch.setattr(sci_staging_bootstrap, "database_is_sci_staging", lambda url=None: True)
    program["org"].name = demo.ORG_NAME
    program["db"].commit()
    return program


def test_refuses_outside_sci_staging(program, monkeypatch):
    monkeypatch.setattr(environment, "current", lambda: environment.ENV_PRODUCTION)
    assert demo.ensure(program["db"])["reason"] == "not_staging_environment"
    monkeypatch.setattr(environment, "current", lambda: environment.ENV_STAGING)
    monkeypatch.setattr(sci_staging_bootstrap, "database_is_sci_staging", lambda url=None: False)
    assert demo.ensure(program["db"])["reason"] == "database_not_sci_staging"
    assert program["db"].query(Lead).filter(Lead.source == demo.DEMO_SOURCE).count() == 0


def test_fills_command_and_rebuilds_without_doubling(staging, client):
    db, org = staging["db"], staging["org"]
    out = demo.ensure(db)
    assert out["ok"] and out["families"] == len(demo.FAMILIES) + len(demo.QUIET)
    assert demo.ensure(db)["skipped"] == "already_today"
    again = demo.ensure(db, force=True)
    assert again["replaced"] == out["families"]
    assert db.query(Lead).filter(Lead.source == demo.DEMO_SOURCE).count() == out["families"]
    # nothing can page anyone: no SLA clock, no reminders pending
    assert all(r.sla_due_at is None for r in db.query(ProgramResponse).all())
    assert all(b.reminder_24hr_sent and b.reminder_1hr_sent for b in db.query(BookingLink).all())
    r = client.get("/program/command", headers=_h(db, staging["admin"])).json()
    assert len(r["inbox"]) >= 15 and r["counts"]["needs_attention"] >= 5
    assert r["counts"]["replies_today"] >= 10 and r["counts"]["contacted_today"] >= 20
    fam = client.get("/program/command/family/%s" % r["inbox"][0]["lead_id"],
                     headers=_h(db, staging["admin"])).json()
    assert fam["thread"] and fam["location"]


def test_demo_family_is_never_texted(staging):
    from app.services import sms_service
    db = staging["db"]
    demo.ensure(db)
    lead = db.query(Lead).filter(Lead.source == demo.DEMO_SOURCE).first()
    with pytest.raises(sms_service.SciSmsBlocked) as e:
        sms_service.send_sms(db, staging["admin"], lead, "hello", include_booking_link=False)
    assert e.value.reasons == ["DEMO_NUMBER"]


def test_demo_family_is_never_emailed(staging):
    from app.services import email_service
    db = staging["db"]
    demo.ensure(db)
    lead = db.query(Lead).filter(Lead.source == demo.DEMO_SOURCE, Lead.email.isnot(None)).first()
    with pytest.raises(ValueError) as e:
        email_service.send_email_to_lead(db, staging["admin"], lead, "Hi", "Hello")
    assert "DEMO_FAMILY" in str(e.value)


def test_demo_identity_guards_every_outward_path(staging, client):
    from app.models.models import Message
    from app.models.program_models import ProgramSourceRecord
    from app.services.programs import appointments as A
    db = staging["db"]
    demo.ensure(db)
    lead = db.query(Lead).filter(Lead.source == demo.DEMO_SOURCE).first()
    # source rows are on hold: never promoted, batched or sent
    assert all(r.on_hold for r in db.query(ProgramSourceRecord).filter(ProgramSourceRecord.lead_id.isnot(None))
               .filter(ProgramSourceRecord.source_lead_id.like("DEMO-%")))
    # booking is simulated, never the calendar
    from datetime import datetime, timedelta
    staging["prog"].booking_user_id = staging["admin"].id
    db.commit()
    from unittest.mock import patch
    with patch.object(A, "still_free", return_value=True):
        out = A.book(db, staging["prog"], lead, datetime.utcnow() + timedelta(days=2), "visit")
    assert out["status"] == "simulated" and out["simulated"]
    # the booking webhook refuses a demo family's link
    link = BookingLink(lead_id=lead.id, user_id=staging["admin"].id)
    db.add(link)
    db.commit()
    r = client.post("/calendar/booking-confirmed", json={"booking_token": link.token, "slot_display": "2030-01-08T10:00"})
    assert r.status_code == 409
    # Command reply to a demo family is recorded as simulated, nothing sent
    h = _h(db, staging["admin"])
    r = client.post("/program/command/demo-send", headers=h, json={"lead_id": lead.id, "text": "Hello"})
    assert r.status_code == 200 and r.json()["simulated"]
    m = db.query(Message).filter(Message.lead_id == lead.id, Message.send_source == "demo").one()
    assert m.delivery_status == "simulated" and m.twilio_sid is None
    f = client.get("/program/command/family/%s" % lead.id, headers=h).json()
    assert f["is_demo"] is True


def test_daily_refresh_moves_forward_and_keeps_tester_changes(staging, monkeypatch):
    from datetime import timedelta
    from app.models.models import Message
    db = staging["db"]
    demo.ensure(db)
    lead = db.query(Lead).filter(Lead.source == demo.DEMO_SOURCE).first()
    db.add(Message(lead_id=lead.id, sender_id=staging["admin"].id, body="tester note", send_source="demo",
                   delivery_status="simulated"))
    db.commit()
    before = max(r.received_at for r in db.query(ProgramResponse).all())
    today = demo._central_today()
    monkeypatch.setattr(demo, "_central_today", lambda: today + timedelta(days=2))
    out = demo.ensure(db)
    assert out["rolled_forward_days"] == 2
    after = max(r.received_at for r in db.query(ProgramResponse).all())
    assert after - before == timedelta(days=2)
    assert db.query(Message).filter(Message.body == "tester note").count() == 1      # kept
    assert demo.ensure(db)["skipped"] == "already_today"
