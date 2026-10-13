"""Supplier catalog says only what is true; a manual handoff is recorded with
a follow-up task and sends nothing; Chariot is refused for a business."""
import json

from app.models.models import EmailMessage, Lead, Message, User
from app.models.work_models import LeadTask
from app.services.auth_service import create_access_token, hash_password


def _admin(db, org):
    u = User(organization_id=org.id, email="handoff-admin@example.com", password_hash=hash_password("x"),
             full_name="Advisor", role="org_admin", is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return {"Authorization": "Bearer %s" % create_access_token(u, db)}, u


def _lead(db, org, request_type):
    lead = Lead(organization_id=org.id, first_name="Dana", last_name="Test", status="new",
                custom_fields=json.dumps({"request_type": request_type}))
    db.add(lead)
    db.commit()
    return lead


def test_catalog_is_honest(client, db_session, sample_org, monkeypatch):
    monkeypatch.delenv("COMPAREPOWER_AFFILIATE_URL", raising=False)
    h, _ = _admin(db_session, sample_org)
    sups = {s["key"]: s for s in client.get("/energy-ops/suppliers", headers=h).json()["suppliers"]}
    assert sups["chariot"]["commercial"] is False and sups["chariot"]["online_enrollment"] is False
    assert sups["comparepower"]["link_configured"] is False and sups["comparepower"]["link"] is None
    assert sups["gexa"]["online_enrollment"] is False
    monkeypatch.setenv("COMPAREPOWER_AFFILIATE_URL", "https://example.com/atlantis")
    sups = {s["key"]: s for s in client.get("/energy-ops/suppliers", headers=h).json()["suppliers"]}
    assert sups["comparepower"]["link"] == "https://example.com/atlantis"


def test_handoff_records_and_opens_a_task_and_sends_nothing(client, db_session, sample_org):
    h, user = _admin(db_session, sample_org)
    lead = _lead(db_session, sample_org, "Compare electricity options")
    r = client.post("/energy-ops/leads/%s/handoff" % lead.id, headers=h,
                    json={"supplier": "comparepower", "note": "Sent the ComparePower link by phone"})
    assert r.status_code == 200, r.text
    db_session.refresh(lead)
    hand = json.loads(lead.custom_fields)["supplier_handoffs"]
    assert hand[0]["supplier"] == "comparepower" and hand[0]["by"] == user.id
    task = db_session.query(LeadTask).filter(LeadTask.lead_id == lead.id).one()
    assert "ComparePower" in task.title and task.status == "open" and task.due_at is not None
    assert db_session.query(Message).count() == 0 and db_session.query(EmailMessage).count() == 0


def test_chariot_handoff_refused_for_a_business(client, db_session, sample_org):
    h, _ = _admin(db_session, sample_org)
    biz = _lead(db_session, sample_org, "Commercial energy procurement")
    r = client.post("/energy-ops/leads/%s/handoff" % biz.id, headers=h, json={"supplier": "chariot"})
    assert r.status_code == 409 and "residential only" in r.json()["detail"]
    home = _lead(db_session, sample_org, "Compare electricity options")
    assert client.post("/energy-ops/leads/%s/handoff" % home.id, headers=h,
                       json={"supplier": "chariot"}).status_code == 200


def test_unknown_supplier_and_other_workspace(client, db_session, sample_org):
    h, _ = _admin(db_session, sample_org)
    lead = _lead(db_session, sample_org, "I'm moving")
    assert client.post("/energy-ops/leads/%s/handoff" % lead.id, headers=h,
                       json={"supplier": "nobody"}).status_code == 422
    from app.models.models import Organization
    other = Organization(name="Other", slug="other-energy", plan="standard", industry="energy")
    db_session.add(other)
    db_session.commit()
    theirs = _lead(db_session, other, "I'm moving")
    assert client.post("/energy-ops/leads/%s/handoff" % theirs.id, headers=h,
                       json={"supplier": "move_concierge"}).status_code == 404
