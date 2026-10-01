# -*- coding: utf-8 -*-
"""Focused security review of the code added 2026-09-29..10-01 (stream S7).

Each test pins one finding (or one control that was verified and must not
regress): object-level access, tenant filters, role checks, escaping. No
message is sent anywhere; providers are never called.
"""
import itertools
import json
import uuid
from datetime import datetime, timedelta

import pytest

import app.models.agency_models  # noqa: F401
import app.models.energy_models  # noqa: F401
from app.models.agency_models import AgencyRecruit
from app.models.energy_models import EnergyMoveRequest
from app.models.intake_models import OrgContact, RecordClass
from app.models.models import AuditLogEntry, EmailMessage, Lead
from app.models.work_models import LeadTask

import agency_support as AG
from agency_support import agency_feature, world  # noqa: F401  (fixtures)

_SEQ = itertools.count(1)


@pytest.fixture(autouse=True)
def _mount():
    from app.main import app
    AG.mount()
    paths = {getattr(r, "path", "") for r in app.routes}
    if "/energy-ops/queues" not in paths:
        from app.routers.energy_ops_router import router as ops
        app.include_router(ops)
    yield


# ── F1  agency: an advisor cannot create work FOR a colleague ───────────────

def test_advisor_cannot_create_application_or_appointment_for_colleague(client, world):
    db, maya, ben, mgr = world["db"], world["maya"], world["ben"], world["mgr"]
    lead = AG.lead(db, world["org"], owner=maya)
    hm = AG.h(db, maya)
    r = client.post("/agency/applications", headers=hm,
                    json={"prospect_id": lead.id, "agent_id": ben.id})
    assert r.status_code == 403, r.text
    r = client.post("/agency/appointments", headers=hm,
                    json={"prospect_id": lead.id, "agent_id": ben.id,
                          "starts_at": "2026-10-10T15:00:00Z"})
    assert r.status_code == 403, r.text
    r = client.post(f"/agency/prospects/{lead.id}/tasks", headers=hm,
                    json={"title": "Call", "assigned_user_id": ben.id})
    assert r.status_code == 403, r.text
    # for themself: allowed
    assert client.post("/agency/applications", headers=hm,
                       json={"prospect_id": lead.id, "agent_id": maya.id}).status_code == 201
    # a manager may hand work to anyone in the workspace
    assert client.post("/agency/applications", headers=AG.h(db, mgr),
                       json={"prospect_id": lead.id, "agent_id": ben.id}).status_code == 201
    # ...but never to a user of another tenant (404, not a leak)
    assert client.post("/agency/applications", headers=AG.h(db, mgr),
                       json={"prospect_id": lead.id, "agent_id": world["fadv"].id}).status_code == 404


# ── F2  agency detail views are narrowed to one row (no full-book scan) ─────

def test_agency_detail_views_scope_and_cross_tenant_404(client, world):
    db = world["db"]
    mine = AG.lead(db, world["org"], owner=world["maya"])
    AG.lead(db, world["org"], owner=world["ben"])
    theirs = AG.lead(db, world["other"], owner=world["fadv"])
    hm = AG.h(db, world["maya"])
    r = client.get(f"/agency/prospects/{mine.id}", headers=hm)
    assert r.status_code == 200 and r.json()["id"] == mine.id and r.json().get("name")
    assert client.get(f"/agency/prospects/{theirs.id}", headers=hm).status_code == 404
    rec = AgencyRecruit(organization_id=world["other"].id, name="Foreign", stage="prospect",
                        recruiter_user_id=world["fmgr"].id)
    db.add(rec)
    db.commit()
    assert client.get(f"/agency/recruits/{rec.id}", headers=AG.h(db, world["mgr"])).status_code == 404
    r = client.post("/agency/recruits", headers=AG.h(db, world["mgr"]), json={"name": "Rita Recruit"})
    assert r.status_code == 201 and r.json()["name"] == "Rita Recruit"
    assert client.get(f"/agency/recruits/{r.json()['id']}", headers=hm).status_code == 404  # not hers


def test_agency_requires_entitlement_on_api_not_just_menu(client, db_session):
    o = AG.org(db_session, "No Agency", features=("leads",))
    u = AG.user(db_session, o, "org_admin", "noag")
    for path in ("/agency/summary", "/agency/prospects", "/agency/applications"):
        assert client.get(path, headers=AG.h(db_session, u)).status_code in (402, 403), path


# ── F3  move concierge: a move's tasks follow the LEAD's scope ──────────────

def test_move_detail_hides_tasks_of_a_lead_the_caller_cannot_read(client, world):
    db, maya, ben = world["db"], world["maya"], world["ben"]
    bens_lead = AG.lead(db, world["org"], owner=ben)
    db.add(LeadTask(organization_id=world["org"].id, lead_id=bens_lead.id, title="Private to Ben",
                    details="gate code 9999", due_at=datetime.utcnow(), status="open",
                    assigned_to_id=ben.id))
    m = EnergyMoveRequest(organization_id=world["org"].id, lead_id=bens_lead.id,
                          contact_name="Moved", status="requested", assigned_to_id=maya.id,
                          created_by_id=maya.id)
    db.add(m)
    db.commit()
    d = client.get(f"/energy-ops/moves/{m.id}", headers=AG.h(db, maya))
    assert d.status_code == 200
    assert d.json()["tasks"] == [] and d.json()["tasks_available"] is False
    assert "gate code" not in d.text
    # the lead's own advisor / a manager still sees them
    dm = client.get(f"/energy-ops/moves/{m.id}", headers=AG.h(db, world["mgr"])).json()
    assert [t["title"] for t in dm["tasks"]] == ["Private to Ben"]


# ── F4  record-sent stores person-typed text escaped ────────────────────────

def test_record_sent_escapes_markup(client, db_session, sample_lead, sample_advisor):
    from app.services.auth_service import create_access_token
    sample_lead.email = "jane@example.com"
    sample_advisor.role = "org_admin"
    db_session.commit()
    h = {"Authorization": "Bearer " + create_access_token(sample_advisor, db_session)}
    r = client.post(f"/email/record-sent/{sample_lead.id}", headers=h, json={
        "subject": "Recorded", "body": "Hi <script>alert(1)</script>\nthere",
        "sent_at": "2026-09-29T19:29:00Z", "note": "<img src=x onerror=1>"})
    assert r.status_code == 200, r.text
    row = db_session.query(EmailMessage).get(r.json()["email_id"])
    assert "<script>" not in row.body_html and "&lt;script&gt;" in row.body_html
    assert "<img" not in row.body_html and "<br>" in row.body_html
    assert row.send_source == "recorded"
    assert db_session.query(AuditLogEntry).filter(
        AuditLogEntry.action == "email.recorded_after_send").count() == 1


def test_record_sent_foreign_lead_is_404(client, db_session, sample_advisor):
    from app.services.auth_service import create_access_token
    o = AG.org(db_session, "Elsewhere")
    foreign = Lead(organization_id=o.id, first_name="F", email="f@x.test", status="new")
    db_session.add(foreign)
    sample_advisor.role = "org_admin"
    db_session.commit()
    h = {"Authorization": "Bearer " + create_access_token(sample_advisor, db_session)}
    r = client.post(f"/email/record-sent/{foreign.id}", headers=h, json={
        "subject": "S", "body": "B", "sent_at": "2026-09-29T19:29:00Z"})
    assert r.status_code == 404


# ── verified controls: contacts bulk actions never touch another tenant ─────

def _contact(db, org, **kw):
    c = OrgContact(organization_id=org.id, first_name=kw.pop("first", "Cora"), last_name="Cole",
                   email=kw.pop("email", "c%d@example.test" % next(_SEQ)),
                   record_class=RecordClass.CONTACT, **kw)
    db.add(c)
    db.commit()
    return c


def _imports_on(db, *orgs):
    for o in orgs:
        o.enabled_features = json.dumps(["leads", "insurance_agency", "imports"])
    db.commit()


def test_contacts_bulk_delete_and_promote_ignore_foreign_ids(client, world):
    db = world["db"]
    _imports_on(db, world["org"], world["other"])
    mine = _contact(db, world["org"])
    foreign = _contact(db, world["other"])
    hm = AG.h(db, world["mgr"])
    r = client.post("/intake/contacts/bulk-delete", headers=hm, json={"ids": [foreign.id]})
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] == 0 and r.json()["not_found"] == [foreign.id]
    assert db.query(OrgContact).filter(OrgContact.id == foreign.id).count() == 1
    r = client.post("/intake/contacts/bulk-promote", headers=hm, json={"ids": [foreign.id]})
    assert r.status_code == 200, r.text
    assert r.json()["promoted"] == 0
    assert r.json()["skipped"][0]["reason"] == "Not a contact in this workspace"
    assert db.query(Lead).filter(Lead.organization_id == world["other"].id).count() == 0
    # ids endpoint and detail are tenant-scoped
    ids = client.get("/intake/contacts/ids", headers=hm).json()["ids"]
    assert mine.id in ids and foreign.id not in ids
    assert client.get(f"/intake/contacts/{foreign.id}", headers=hm).status_code == 404
    assert client.delete(f"/intake/contacts/{foreign.id}", headers=hm).status_code == 404


def test_contacts_bulk_delete_is_not_open_to_advisors(client, world):
    db = world["db"]
    _imports_on(db, world["org"])
    c = _contact(db, world["org"])
    r = client.post("/intake/contacts/bulk-delete", headers=AG.h(db, world["maya"]), json={"ids": [c.id]})
    assert r.status_code == 403
    assert db.query(OrgContact).filter(OrgContact.id == c.id).count() == 1


def test_contact_delete_preserves_opt_out_and_audits(client, world):
    from app.models.models import SuppressionEntry
    db = world["db"]
    _imports_on(db, world["org"])
    c = _contact(db, world["org"], phone="+12145550199", sms_status="opted_out")
    r = client.delete(f"/intake/contacts/{c.id}", headers=AG.h(db, world["mgr"]))
    assert r.status_code == 200, r.text
    assert r.json()["suppression_preserved"] is True
    assert db.query(SuppressionEntry).filter(
        SuppressionEntry.organization_id == world["org"].id).count() == 1
    assert db.query(AuditLogEntry).filter(AuditLogEntry.action == "contact.delete",
                                          AuditLogEntry.target_id == c.id).count() == 1


# ── verified controls: platform-owner mailbox routes ────────────────────────

def test_inbound_mailbox_routes_are_platform_owner_only(client, world):
    h = AG.h(world["db"], world["mgr"])
    assert client.get("/god/email/inbound-mailboxes", headers=h).status_code in (401, 403)
    assert client.post("/god/email/inbound-mailboxes/x/poll-now", headers=h).status_code in (401, 403)
    assert client.post("/god/email/inbound-mailboxes/x/active?active=false",
                       headers=h).status_code in (401, 403)
