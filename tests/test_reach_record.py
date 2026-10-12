"""'Reaches by' and 'Last sent' on the person's record."""
from datetime import datetime

from app.models.models import Message
from app.services.programs import send_approval as sa
from tests.test_outreach_program import _lead_for, program  # noqa: F401


def test_reach_for_lead_shows_mode_and_last_text(program):
    db, org, admin, prog = program["db"], program["org"], program["admin"], program["prog"]
    lead = _lead_for(db, org, admin, "L007", phone="2145550107", first="Rosa", email="rd@example.com")
    out = sa.reach_for_lead(db, prog, lead.id)
    assert out["reaches"] == "Text + Email" and out["last_text_at"] is None
    db.add(Message(lead_id=lead.id, sender_id=admin.id, body="hi", sent_at=datetime(2026, 10, 1, 15, 0),
                   delivery_status="delivered"))
    db.add(Message(lead_id=lead.id, sender_id=admin.id, body="x", sent_at=datetime(2026, 10, 5, 15, 0),
                   delivery_status="failed"))
    db.commit()
    out = sa.reach_for_lead(db, prog, lead.id)
    assert out["last_text_at"].startswith("2026-10-01")      # a failed text doesn't count
    assert sa.reach_for_lead(db, prog, "no-such-lead") is None
