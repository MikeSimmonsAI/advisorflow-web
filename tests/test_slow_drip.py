"""Slow drip: approved now, each contact starts at its own later time."""
from datetime import datetime, timedelta

from app.models.models import CadenceState
from app.models.program_models import ProgramSourceRecord
from app.services.programs import email_touches
from app.services.programs import send_approval as sa
from tests.test_outreach_program import _lead_for, program  # noqa: F401


def test_drip_staggers_texts_and_holds_email(program):
    db, org, admin, prog = program["db"], program["org"], program["admin"], program["prog"]
    a = _lead_for(db, org, admin, "L007", phone="2145550107", first="Rosa", email="rd@example.com")
    for lid in (a.id,):
        db.query(CadenceState).filter_by(lead_id=lid).delete()
    db.commit()
    start = datetime.utcnow() + timedelta(hours=15)
    out = sa.approve(db, prog, admin, [a.id], mode="text_email", start_at=start, spacing_minutes=20)
    assert out["approved"] == 1 and out["start_at"]
    rec = db.query(ProgramSourceRecord).filter_by(lead_id=a.id).first()
    assert rec.scheduled_start_at == start
    st = db.query(CadenceState).filter_by(lead_id=a.id).first()
    assert st is not None and st.next_touch_due_at.replace(tzinfo=None) == start
    due = [i for i in email_touches.plan(db, prog) if i["lead"].id == a.id]
    assert due == []                                   # no email before the start time
    later = [i for i in email_touches.plan(db, prog, now=start + timedelta(minutes=1)) if i["lead"].id == a.id]
    assert len(later) == 1
    item = sa.list_contacts(db, prog, search="Rosa")["items"][0]
    assert item["starts_at"]


def test_a_family_already_texted_is_past_its_start_for_email(program):
    """#482 Phase 2 (Nov 3): two Batch 1 families texted Oct 31 kept a Nov 4
    start date from the Nov 1 resume, so their Email 2 (due Nov 2) waited.
    Once a text of the journey has gone, the start is past - the follow-up
    goes on its own rule. A family not yet texted still waits (above)."""
    from app.models.program_models import ProgramEmailTouch
    db, org, admin, prog = program["db"], program["org"], program["admin"], program["prog"]
    a = _lead_for(db, org, admin, "L007", phone="2145550107", first="Rosa", email="rd@example.com")
    db.query(CadenceState).filter_by(lead_id=a.id).delete()
    db.commit()
    start = datetime.utcnow() + timedelta(days=1)
    sa.approve(db, prog, admin, [a.id], mode="text_email", start_at=start, spacing_minutes=20)
    sent = datetime.utcnow() - timedelta(days=email_touches.followup_days(), hours=1)
    rec = db.query(ProgramSourceRecord).filter_by(lead_id=a.id).first()
    db.add(ProgramEmailTouch(organization_id=org.id, lead_id=a.id, source_record_id=rec.id,
                             campaign_family=rec.campaign_family, touch_number=1, status="sent",
                             attempted_at=sent))
    db.commit()
    assert [i for i in email_touches.plan(db, prog) if i["lead"].id == a.id] == []   # not texted: waits
    st = db.query(CadenceState).filter_by(lead_id=a.id).first()
    st.current_touch_number = 1                       # text 1 of the journey went
    db.commit()
    due = [i for i in email_touches.plan(db, prog) if i["lead"].id == a.id]
    assert len(due) == 1 and due[0]["touch"] == 2     # Email 2, never Email 1 again
