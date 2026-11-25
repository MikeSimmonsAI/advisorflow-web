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


class _Line:
    def __init__(self, kind):
        self.kind = kind

    def validate_phone(self, d):
        return {"line_type": self.kind}


def _gun(rl, monkeypatch, phone="18173606862"):
    from app.models.models import CadenceState
    _launch(rl)
    db, org = rl["db"], rl["org"].id
    prog = db.query(OutreachProgram).filter(OutreachProgram.organization_id == org).first()
    lead = db.query(Lead).filter(Lead.organization_id == org).first()
    lead.phone, lead.status, lead.phone_line_type, lead.phone_line_number = phone, "replied", "mobile", "8173606862"
    from unittest.mock import patch
    from app.models.models import Reply
    from app.services.programs import responses as R
    rep = Reply(lead_id=lead.id, body="+1 (214) 392-5105 Gun Mai Maran", source="sms", received_at=datetime.utcnow())
    db.add(rep)
    db.commit()
    with patch("app.services.programs.responses._deliver_staff_alert", return_value=(True, None)):
        R.on_inbound(db, lead, rep.body, "sms", reply_id=rep.id, run_setter=False)
    st = db.query(CadenceState).filter(CadenceState.lead_id == lead.id).first()
    if st is None:
        st = CadenceState(lead_id=lead.id)
        db.add(st)
    st.status, st.current_touch_number = "stopped_replied", 1
    st.cadence_started_at, st.completed_at = datetime(2026, 11, 24, 23, 0), datetime(2026, 11, 24, 23, 5)
    db.commit()
    monkeypatch.setattr(OC, "GUN", lead.id)
    return db, org, prog, lead


def test_gun_new_number_replaces_the_old_one_and_starts_day_1(rl, monkeypatch):
    from app.models.models import CadenceState
    from app.models.work_models import LeadNote
    from app.services.programs import ai_setter
    db, org, prog, lead = _gun(rl, monkeypatch)
    now = datetime(2026, 11, 25, 1, 0)
    out = OC.restland_gun_new_number(db, prog, now, line_adapter=_Line("mobile"))
    assert out["changed"] and out["enrolled"], out
    db.refresh(lead)
    assert (lead.first_name, lead.last_name, lead.phone) == ("Gun Mai", "Maran", "12143925105")
    assert lead.phone_line_number == "2143925105" and lead.phone_line_type == "mobile"
    sts = db.query(CadenceState).filter(CadenceState.lead_id == lead.id).all()
    assert len(sts) == 1 and sts[0].status == "active" and sts[0].current_touch_number == 0
    assert sts[0].cadence_started_at == now                               # Day 1, a new run
    assert ai_setter.conversation_for(db, lead).state != "human"
    assert any("(817) 360-6862" in n.body and "(214) 392-5105" in n.body
               for n in db.query(LeadNote).filter_by(lead_id=lead.id))
    assert OC._ledger(db, org, OC.GUN_KEY) is not None
    # the relative's reply from the old number does not stop the new texts
    from app.services.programs import rotation
    assert rotation.reply_counts_for_cadence(db, lead) is False


def test_gun_stops_when_the_new_number_is_someone_else_or_not_a_cell(rl, monkeypatch):
    from app.models.models import CadenceState
    db, org, prog, lead = _gun(rl, monkeypatch)
    other = db.query(Lead).filter(Lead.organization_id == org, Lead.id != lead.id).first()
    other.phone = "12143925105"
    db.commit()
    out = OC.restland_gun_new_number(db, prog, datetime(2026, 11, 25, 1, 0), line_adapter=_Line("mobile"))
    assert "another contact" in out["blocker"] and not out["changed"]
    db.refresh(lead)
    assert lead.phone == "18173606862"                                    # nothing touched


def test_gun_landline_is_updated_but_not_texted(rl, monkeypatch):
    from app.models.models import CadenceState
    db, org, prog, lead = _gun(rl, monkeypatch)
    out = OC.restland_gun_new_number(db, prog, datetime(2026, 11, 25, 1, 0), line_adapter=_Line("landline"))
    assert out["changed"] and not out["enrolled"] and "landline" in out["blocker"]
    st = db.query(CadenceState).filter(CadenceState.lead_id == lead.id).one()
    assert st.status != "active"
