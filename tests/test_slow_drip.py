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
