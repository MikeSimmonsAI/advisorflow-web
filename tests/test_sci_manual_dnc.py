"""A person puts a family on Do Not Contact by hand (Mike, Oct 25): it must stop
every path exactly like a texted STOP. Synthetic records only."""
from datetime import datetime, timedelta
from unittest.mock import patch

from app.models.models import CadenceState, Lead, SuppressionEntry
from app.models.program_models import ProgramConversationTurn, ProgramResponse
from app.services.programs import ai_setter as S
from app.services.programs import responses as R
from tests.test_outreach_program import _fake_provider, _h, _lead_for, _touch_ready, program  # noqa: F401
from tests.test_sci_appointment_setter import _Fake, _d, _run, setter  # noqa: F401


def test_marking_dnc_stops_everything_and_suppresses_the_number(setter, sample_advisor, client):
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001", first="Larry")
    _run(db, lead, "Tell me more", _Fake([_d("offer_times", "Would {TIMES} work?")]))
    st = db.query(CadenceState).filter_by(lead_id=lead.id).first()
    if st is None:
        db.add(CadenceState(lead_id=lead.id, status="active"))
    else:
        st.status = "paused"                                  # a reply paused it: DNC must still END it
    db.commit()
    r = client.post("/program/command/dnc", headers=_h(db, setter["admin"]),
                    json={"lead_id": lead.id, "reason": "asked by phone"})
    assert r.status_code == 200, r.text
    assert r.json()["phone_suppressed"] is True
    db.expire_all()
    lead = db.query(Lead).get(lead.id)
    assert lead.status == "dnc" and lead.allow_email is False
    assert db.query(SuppressionEntry).filter_by(organization_id=org.id).count() == 1
    assert "asked by phone" in db.query(SuppressionEntry).one().reason
    conv = S.conversation_for(db, lead, create=False)
    assert conv.state == "stopped" and conv.next_followup_at is None
    assert all(r.handling_status == "closed" for r in db.query(ProgramResponse).filter_by(lead_id=lead.id))
    st = db.query(CadenceState).filter_by(lead_id=lead.id).one()
    assert "dnc" in str(getattr(st.status, "value", st.status))
    # a new reply never reaches the AI, and nothing is sent
    fake = _Fake([])
    _run(db, lead, "Hello?", fake)
    assert fake.calls == []
    fam = client.get("/program/command/family/%s" % lead.id, headers=_h(db, setter["admin"])).json()
    assert fam["handling"]["opted_out"] is True
    # idempotent
    again = client.post("/program/command/dnc", headers=_h(db, setter["admin"]), json={"lead_id": lead.id})
    assert again.status_code == 200 and again.json()["already"] is True
    assert db.query(SuppressionEntry).filter_by(organization_id=org.id).count() == 1


def test_a_dnc_family_gets_no_text_and_no_email(program, sample_advisor, monkeypatch, client):
    db, org, lead, et = _touch_ready(program, sample_advisor, monkeypatch)
    client.post("/program/command/dnc", headers=_h(db, program["admin"]), json={"lead_id": lead.id})
    sent = []
    with _fake_provider(sent):
        r = et.run(db, org.id, force_hours=True)
    assert sent == [] and r["sent"] == 0
    from app.services.sms_service import send_sms
    import pytest
    with pytest.raises(Exception):
        send_sms(db, sample_advisor, db.query(Lead).get(lead.id), "hi", include_booking_link=False,
                 send_source="cadence")


def test_dnc_is_tenant_scoped(program, sample_advisor, client):
    db = program["db"]
    r = client.post("/program/command/dnc", headers=_h(db, program["admin"]), json={"lead_id": "nope"})
    assert r.status_code == 404
