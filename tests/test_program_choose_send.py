"""Choose & Send: everyone can be loaded; only the contacts a manager ticks get outreach."""
from app.models.models import CadenceState, Lead
from app.models.program_models import CampaignFamily, OutreachProgram, ProgramSourceRecord
from app.services import send_source
from app.services.programs import identity, promote, send_approval
from tests.test_outreach_program import _admin, _h, program  # noqa: F401


def _load(db, org):
    promote.promote(db, org, apply=True)
    db.commit()
    rows = (db.query(ProgramSourceRecord).filter(ProgramSourceRecord.organization_id == org.id,
                                                 ProgramSourceRecord.lead_id.isnot(None)).all())
    return {r.source_lead_id: db.query(Lead).get(r.lead_id) for r in rows}


def test_list_shows_every_loaded_contact_and_sends_nothing(client, program):
    db, org = program["db"], program["org"]
    leads = _load(db, org)
    r = client.get("/program/send/contacts", headers=_h(db, program["admin"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] >= 3 and body["approval_mode"] is False
    assert all(i["approved"] is False for i in body["items"])
    assert db.query(CadenceState).count() == 0
    assert leads


def test_preview_shows_the_first_text(client, program):
    db, org = program["db"], program["org"]
    leads = _load(db, org)
    lead = leads["L001"]
    r = client.post("/program/send/preview", json={"lead_ids": [lead.id]}, headers=_h(db, program["admin"]))
    assert r.status_code == 200, r.text
    item = r.json()["items"][0]
    assert "Eastern Gate Memorial Gardens" in item["text"] and item["text"].endswith("Reply STOP to opt out.")
    assert db.query(CadenceState).count() == 0


def test_approve_sends_only_to_the_ticked_contacts(client, program):
    db, org = program["db"], program["org"]
    leads = _load(db, org)
    picked, other = leads["L001"], leads["L007"]
    h = _h(db, program["admin"])
    # wrong confirmation -> nothing happens
    bad = client.post("/program/send/approve", json={"lead_ids": [picked.id], "confirm": "yes"}, headers=h)
    assert bad.status_code == 422
    assert db.query(CadenceState).count() == 0
    r = client.post("/program/send/approve", json={"lead_ids": [picked.id], "confirm": "SEND 1"}, headers=h)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["approved"] == 1 and out["texting"] == 1
    db.expire_all()
    prog = db.query(OutreachProgram).filter_by(organization_id=org.id).one()
    assert prog.require_send_approval is True
    assert db.query(CadenceState).filter_by(lead_id=picked.id).count() == 1
    assert db.query(CadenceState).filter_by(lead_id=other.id).count() == 0
    # the picked contact's campaign is on, and only the picked contact may get automated sends
    assert identity.send_refusal(db, db.query(Lead).get(picked.id), send_source.CADENCE) is None
    fam_other = identity.source_record_for_lead(db, other).campaign_family
    fam = db.query(CampaignFamily).filter_by(organization_id=org.id, key=fam_other).one()
    fam.is_active = True
    db.commit()
    assert identity.send_refusal(db, other, send_source.CADENCE) == send_approval.NOT_APPROVED_REFUSAL
    # a person replying by hand is never blocked by approval
    assert identity.send_refusal(db, other, send_source.MANUAL) is None


def test_held_contact_cannot_be_approved(client, program):
    db, org = program["db"], program["org"]
    leads = _load(db, org)
    lead = leads["L001"]
    for rec in identity.records_for_lead(db, lead):
        rec.on_hold = True
    db.commit()
    r = client.post("/program/send/approve", json={"lead_ids": [lead.id], "confirm": "SEND 1"},
                    headers=_h(db, program["admin"]))
    assert r.status_code == 200 and r.json()["approved"] == 0
    assert r.json()["skipped"][0]["reason"] == "On hold"
    assert db.query(CadenceState).count() == 0


def test_unapprove_stops_automated_sends(client, program):
    db, org = program["db"], program["org"]
    leads = _load(db, org)
    lead = leads["L001"]
    h = _h(db, program["admin"])
    client.post("/program/send/approve", json={"lead_ids": [lead.id], "confirm": "SEND 1"}, headers=h)
    r = client.post("/program/send/unapprove", json={"lead_ids": [lead.id]}, headers=h)
    assert r.status_code == 200 and r.json()["unapproved"] == 1
    db.expire_all()
    assert identity.send_refusal(db, db.query(Lead).get(lead.id), send_source.CADENCE) == send_approval.NOT_APPROVED_REFUSAL
    assert db.query(CadenceState).filter_by(lead_id=lead.id).one().status == "cancelled"


def test_approve_needs_a_manager(client, program, db_session, sample_org):
    from app.models.models import User
    from app.services.auth_service import hash_password
    db, org = program["db"], program["org"]
    leads = _load(db, org)
    adv = User(organization_id=org.id, email="adv-pick@example.com", password_hash=hash_password("Pass12345!"),
               full_name="Adv", role="advisor", is_active=True, must_change_password=False)
    db.add(adv)
    db.commit()
    r = client.post("/program/send/approve", json={"lead_ids": [leads["L001"].id], "confirm": "SEND 1"},
                    headers=_h(db, adv))
    assert r.status_code == 403
    assert db.query(CadenceState).count() == 0
