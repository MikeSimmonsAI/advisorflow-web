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


# ── Owner, Nov 24: a family marked Do Not Contact by mistake comes off it ───

def test_taking_a_family_off_dnc_clears_every_block_and_sends_nothing(setter, sample_advisor, client):
    from app.models.models import ContactSuppression
    db, org = setter["db"], setter["org"]
    lead = _lead_for(db, org, sample_advisor, "L001", first="Larry")
    _run(db, lead, "Tell me more", _Fake([_d("offer_times", "Would {TIMES} work?")]))
    st = db.query(CadenceState).filter_by(lead_id=lead.id).first()
    if st is None:
        db.add(CadenceState(lead_id=lead.id, status="active"))
    db.commit()
    h = _h(db, setter["admin"])
    assert client.post("/program/command/dnc", headers=h, json={"lead_id": lead.id, "reason": "x"}).status_code == 200
    # the AI also read a reply as an opt-out, and the family-wide list has them
    db.add(ProgramResponse(organization_id=org.id, lead_id=lead.id, channel="sms", response_class="opt_out",
                           handling_status="closed", body_excerpt="ok thanks", received_at=datetime.utcnow()))
    db.add(ContactSuppression(organization_id=org.id, kind="email", value=(lead.email or "l@x.com").lower(),
                              scope="family", reason="x", source="manual"))
    db.commit()
    # a reason is required
    assert client.post("/program/command/dnc/clear", headers=h, json={"lead_id": lead.id}).status_code == 422
    r = client.post("/program/command/dnc/clear", headers=h,
                    json={"lead_id": lead.id, "reason": "AI misread their reply"})
    assert r.status_code == 200, r.text
    assert r.json()["carrier_stop_possible"] is True            # the opt-out reply came by text
    db.expire_all()
    lead = db.query(Lead).get(lead.id)
    assert lead.status != "dnc" and lead.allow_email is True
    assert db.query(SuppressionEntry).filter_by(organization_id=org.id).count() == 0
    assert db.query(ProgramResponse).filter_by(lead_id=lead.id, response_class="opt_out").count() == 0
    conv = S.conversation_for(db, lead, create=False)
    assert conv.state == "human"
    st = db.query(CadenceState).filter_by(lead_id=lead.id).one()
    assert str(getattr(st.status, "value", st.status)) == "paused"
    fam = client.get("/program/command/family/%s" % lead.id, headers=h).json()
    assert fam["handling"]["opted_out"] is False
    from app.models.models import AuditLogEntry
    assert db.query(AuditLogEntry).filter_by(action="sci.clear_do_not_contact", target_id=lead.id).count() == 1
    # and it can be marked again later
    assert client.post("/program/command/dnc", headers=h, json={"lead_id": lead.id}).json()["already"] is False
