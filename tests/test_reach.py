"""How each person is reached; campaign type on Good to go."""
from app.models.models import CadenceState
from app.models.program_models import ProgramSourceRecord
from app.services.programs import send_approval as sa
from tests.test_outreach_program import _lead_for, program  # noqa: F401


def test_reaches_labels(program):
    db, org, admin = program["db"], program["org"], program["admin"]
    lead = _lead_for(db, org, admin, "L007", phone="2145550107", first="Rosa", email="rd@example.com")
    assert sa.reaches(lead, None) == "Text + Email"
    assert sa.reaches(lead, "email_only") == "Email only"
    assert sa.reaches(lead, "call") == "Call only"
    lead.phone_line_type = "landline"
    assert sa.reaches(lead, None) == "Email only"
    assert sa.blocker(db, lead) is None              # landline WITH email: still reachable by email
    lead.email = ""
    assert sa.blocker(db, lead) == "Landline - call instead"


def test_email_only_mode_starts_no_texts(program):
    db, org, admin = program["db"], program["org"], program["admin"]
    lead = _lead_for(db, org, admin, "L007", phone="2145550107", first="Rosa", email="rd@example.com")
    db.query(CadenceState).filter_by(lead_id=lead.id).delete()
    db.commit()
    out = sa.approve(db, program["prog"], admin, [lead.id], mode="email_only")
    assert out["approved"] == 1 and out["texting"] == 0 and out["mode"] == "email_only"
    rec = db.query(ProgramSourceRecord).filter_by(lead_id=lead.id).first()
    assert rec.send_mode == "email_only"
    assert db.query(CadenceState).filter_by(lead_id=lead.id).count() == 0
