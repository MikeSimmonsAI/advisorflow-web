"""Owner, Nov 23 9:05 PM: emailed replies that reached Mike's NSMG inbox go into
the Conversations inbox as EMAIL replies; Beverly is added. Synthetic data."""
from tests.test_sci_appointment_setter import setter  # noqa: F401,E402
from tests.test_outreach_program import program  # noqa: F401,E402
from datetime import datetime

from app.models.models import Lead
from app.models.program_models import OutreachProgram, ProgramResponse
from app.services.programs import owner_changes as OC
from tests.test_restland_booking_cadence import rl  # noqa: F401
from tests.test_restland_launch_slots import _launch


def test_replies_land_in_the_email_inbox_and_beverly_is_added(rl, monkeypatch):
    _launch(rl)
    db, org = rl["db"], rl["org"].id
    prog = db.query(OutreachProgram).filter(OutreachProgram.organization_id == org).first()
    leads = db.query(Lead).filter(Lead.organization_id == org).limit(2).all()
    a, b = leads[0], leads[1]
    monkeypatch.setattr(OC, "EMAILED_2", [
        (a.id, "closed", "Will not attend", None, "No, I will not be attending.", datetime(2026, 11, 20), None),
        (b.id, "human", "POA - email only", None, "Email only please.", datetime(2026, 11, 22), "note")])
    monkeypatch.setattr(OC, "EMAIL_ONLY_2", (b.id,))
    monkeypatch.setattr(OC, "EMAILED", [])
    out = OC.restland_emailed_replies_2(db, prog, datetime(2026, 11, 24, 3, 0))
    assert out["inbox"] == 3 and out["beverly"] == "added"
    em = db.query(ProgramResponse).filter(ProgramResponse.organization_id == org,
                                          ProgramResponse.channel == "email").count()
    assert em == 3
    db.refresh(b)
    assert b.contact_channel == "email_only"
    bev = db.query(Lead).filter(Lead.organization_id == org, Lead.email == "bev@gsteps.com").one()
    assert bev.last_name == "Schmedthorst" and bev.city == "Flower Mound"
    assert OC._ledger(db, org, OC.REPLIES2_KEY) is not None          # runs once


def test_anne_visit_becomes_a_phone_call(rl, monkeypatch):
    from datetime import timedelta
    from app.models.models import BookingLink
    _launch(rl)
    db, org = rl["db"], rl["org"].id
    prog = db.query(OutreachProgram).filter(OutreachProgram.organization_id == org).first()
    lead = db.query(Lead).filter(Lead.organization_id == org).first()
    now = datetime(2026, 11, 24, 3, 0)
    bk = BookingLink(lead_id=lead.id, user_id=rl["mike"].id, status="booked", booked_time=now + timedelta(hours=13),
                     meeting_type="office", appt_label="Family File Review")
    db.add(bk); db.commit()
    monkeypatch.setattr(OC, "ANNE", lead.id)
    out = OC.restland_anne_phone(db, prog, now)
    db.refresh(bk)
    assert out.get("changed") and bk.meeting_type == "remote" and "phone" in bk.appt_label


def test_nov24_nsmg_replies_enter_the_inbox_once(rl, monkeypatch):
    _launch(rl)
    db, org = rl["db"], rl["org"].id
    prog = db.query(OutreachProgram).filter(OutreachProgram.organization_id == org).first()
    a, b = db.query(Lead).filter(Lead.organization_id == org).limit(2).all()
    monkeypatch.setattr(OC, "NSMG3", [(a.id, "Wants the form", "Please send me the form.", datetime(2026, 11, 24, 17)),
                                      (b.id, "Meet next week", "Can we meet next week?", datetime(2026, 11, 24, 18))])
    out = OC.restland_nsmg_replies(db, prog, datetime(2026, 11, 24, 18, 30))
    assert sorted(out["entered"]) == sorted([a.id, b.id])
    assert db.query(ProgramResponse).filter(ProgramResponse.organization_id == org,
                                            ProgramResponse.channel == "email").count() == 2
    from app.services.programs import ai_setter
    assert ai_setter.conversation_for(db, a).state == "human"
    assert OC._ledger(db, org, OC.NSMG3_KEY) is not None


def test_nov24_forwarded_reply_is_recorded_without_touching_the_booking(rl, monkeypatch):
    _launch(rl)
    db, org = rl["db"], rl["org"].id
    prog = db.query(OutreachProgram).filter(OutreachProgram.organization_id == org).first()
    a = db.query(Lead).filter(Lead.organization_id == org).first()
    from app.services.programs import ai_setter
    before = ai_setter.conversation_for(db, a).state
    monkeypatch.setattr(OC, "FWD", [(a.id, "Hi, I'm here for your call.", datetime(2026, 11, 24, 16, 4))])
    out = OC.restland_forwarded_anne(db, prog, datetime(2026, 11, 24, 23, 30))
    assert out["entered"] == [a.id]
    assert db.query(ProgramResponse).filter(ProgramResponse.organization_id == org,
                                            ProgramResponse.channel == "email").count() == 1
    assert ai_setter.conversation_for(db, a).state == before
    assert OC._ledger(db, org, OC.FWD_KEY) is not None
