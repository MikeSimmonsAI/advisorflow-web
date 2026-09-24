# -*- coding: utf-8 -*-
"""The /intake API: organization context, God-admin acting context, tenant
isolation, permissions, the typed confirmation, and the audit trail."""
import io
import json

import pytest

from app.models.import_models import ImportBatch
from app.models.intake_models import OrgContact
from app.models.models import AuditLogEntry, Lead, Organization, User
from app.services.auth_service import create_access_token, hash_password

CSV = (b"First Name,Last Name,Email,Phone,HubSpot Record ID,Import Segment\n"
       b"Ann,One,ann@acmewidgets.com,2145550101,1,Previous Customer - Win-Back\n"
       b"Bob,Two,bob@acmewidgets.com,2145550102,2,General Contact Database\n"
       b"Cy,Three,,,3,General Contact Database\n")


@pytest.fixture(autouse=True)
def _inline(monkeypatch):
    monkeypatch.setenv("INTAKE_INLINE_JOBS", "1")


def _org(db, name, slug):
    o = Organization(name=name, slug=slug, plan="enterprise", industry="energy")
    db.add(o)
    db.commit()
    return o


def _headers(db, user, org_override=None):
    h = {"Authorization": f"Bearer {create_access_token(user, db)}"}
    if org_override:
        h["X-Org-Override"] = org_override
    return h


def _user(db, org, role, email):
    u = User(organization_id=org.id if org else None, email=email,
             password_hash=hash_password("Pass12345!"), full_name=email.split("@")[0],
             role=role, must_change_password=False)
    db.add(u)
    db.commit()
    return u


@pytest.fixture()
def orgs(db_session):
    a = _org(db_session, "Atlantis Test Energy", "atl-api")
    b = _org(db_session, "Other Customer Co", "other-api")
    return a, b


def _upload(client, headers, content=CSV, **form):
    data = {"source": "hubspot", "source_detail": "Test export"} | form
    return client.post("/intake/batches", headers=headers, data=data,
                       files={"file": ("contacts.csv", io.BytesIO(content), "text/csv")})


def test_god_admin_with_no_org_selected_is_refused(client, db_session, orgs):
    god = _user(db_session, None, "god_admin", "god@platform.test")
    r = _upload(client, _headers(db_session, god))
    assert r.status_code == 409
    assert db_session.query(ImportBatch).count() == 0


def test_god_admin_acting_for_an_org_imports_into_that_org_only(client, db_session, orgs):
    a, b = orgs
    god = _user(db_session, None, "god_admin", "god2@platform.test")
    h = _headers(db_session, god, org_override=a.id)
    ctx = client.get("/intake/context", headers=h).json()
    assert ctx["organization_name"] == "Atlantis Test Energy"
    assert ctx["acting_as_platform_owner"] is True
    r = _upload(client, h)
    assert r.status_code == 200, r.text
    batch = r.json()
    assert batch["organization_id"] == a.id and batch["acted_as_platform_owner"] is True
    assert batch["batch_code"].startswith("ATL-")
    r = client.post(f"/intake/batches/{batch['id']}/analyze", headers=h)
    assert r.status_code == 202, r.text
    got = client.get(f"/intake/batches/{batch['id']}", headers=h).json()
    assert got["status"] == "ready_for_review"
    assert got["analysis"]["total_rows"] == 3
    # commit needs the organization's name typed
    r = client.post(f"/intake/batches/{batch['id']}/commit", headers=h,
                    json={"mode": "ready_only", "confirm_organization_name": "wrong"})
    assert r.status_code == 400
    r = client.post(f"/intake/batches/{batch['id']}/commit", headers=h,
                    json={"mode": "ready_only", "include_enrichment": True,
                          "confirm_organization_name": "atlantis test energy"})
    assert r.status_code == 202, r.text
    assert db_session.query(OrgContact).filter(OrgContact.organization_id == a.id).count() == 3
    assert db_session.query(OrgContact).filter(OrgContact.organization_id == b.id).count() == 0
    assert db_session.query(OrgContact).filter(
        OrgContact.organization_id.notin_([a.id])).count() == 0
    assert db_session.query(Lead).filter(Lead.organization_id == a.id).count() == 1
    ev = db_session.query(AuditLogEntry).filter(AuditLogEntry.target_id == batch["id"]).all()
    actions = {e.action for e in ev}
    assert {"intake.file_uploaded", "intake.analysis_started", "intake.analysis_completed",
            "intake.batch_committed"} <= actions
    details = json.loads([e for e in ev if e.action == "intake.batch_committed"][0].details)
    assert details["role"] == "god_admin" and details["acting_as_platform_owner"] is True
    assert details["acting_organization"] == "Atlantis Test Energy"
    assert all(e.organization_id == a.id for e in ev)


def test_customer_admin_imports_into_own_org_and_cannot_see_others(client, db_session, orgs):
    a, b = orgs
    admin_a = _user(db_session, a, "org_admin", "admin@atl.test")
    admin_b = _user(db_session, b, "org_admin", "admin@other.test")
    r = _upload(client, _headers(db_session, admin_a))
    assert r.status_code == 200, r.text
    bid = r.json()["id"]
    assert r.json()["acted_as_platform_owner"] is False
    hb = _headers(db_session, admin_b)
    assert client.get(f"/intake/batches/{bid}", headers=hb).status_code == 404
    assert client.get(f"/intake/batches/{bid}/rows", headers=hb).status_code == 404
    assert client.post(f"/intake/batches/{bid}/analyze", headers=hb).status_code == 404
    assert client.get("/intake/batches", headers=hb).json()["total"] == 0
    # an org admin cannot point the import at another org with the override header
    h = _headers(db_session, admin_a, org_override=b.id)
    r = _upload(client, h)
    assert r.json()["organization_id"] == a.id


def test_advisor_without_capability_cannot_import(client, db_session, orgs):
    a, _ = orgs
    adv = _user(db_session, a, "advisor", "adv@atl.test")
    assert _upload(client, _headers(db_session, adv)).status_code == 403


def test_stage_only_default_and_category_drilldown(client, db_session, orgs):
    a, _ = orgs
    admin = _user(db_session, a, "org_admin", "admin2@atl.test")
    h = _headers(db_session, admin)
    bid = _upload(client, h).json()["id"]
    client.post(f"/intake/batches/{bid}/analyze", headers=h)
    rows = client.get(f"/intake/batches/{bid}/rows?category=coverage:no_direct_contact",
                      headers=h).json()
    assert rows["total"] == 1 and rows["rows"][0]["first_name"] == "Cy"
    r = client.post(f"/intake/batches/{bid}/commit", headers=h, json={})
    assert r.status_code == 202 and r.json()["status"] == "staged"
    assert db_session.query(OrgContact).count() == 0
    assert db_session.query(Lead).filter(Lead.organization_id == a.id).count() == 0


def test_mapping_edit_forces_reanalysis_and_unmapped_columns_are_listed(client, db_session,
                                                                        orgs):
    a, _ = orgs
    admin = _user(db_session, a, "org_admin", "admin3@atl.test")
    h = _headers(db_session, admin)
    content = b"Email,Mystery Column\nann@acmewidgets.com,42\n"
    bid = _upload(client, h, content).json()["id"]
    prev = client.get(f"/intake/batches/{bid}/preview", headers=h).json()
    col = {c["header"]: c for c in prev["columns"]}
    assert col["Mystery Column"]["mapping"]["kind"] == "source"
    client.post(f"/intake/batches/{bid}/analyze", headers=h)
    r = client.put(f"/intake/batches/{bid}/mapping", headers=h, json={"mapping": {
        "Email": {"kind": "standard", "target": "email"},
        "Mystery Column": {"kind": "ignore"}}})
    assert r.status_code == 200 and r.json()["status"] == "mapping"
    bad = client.put(f"/intake/batches/{bid}/mapping", headers=h, json={"mapping": {
        "Email": {"kind": "standard", "target": "email"},
        "Mystery Column": {"kind": "standard", "target": "email"}}})
    assert bad.status_code == 422


def test_rollback_requires_the_batch_code(client, db_session, orgs):
    a, _ = orgs
    admin = _user(db_session, a, "org_admin", "admin4@atl.test")
    h = _headers(db_session, admin)
    b = _upload(client, h).json()
    client.post(f"/intake/batches/{b['id']}/analyze", headers=h)
    client.post(f"/intake/batches/{b['id']}/commit", headers=h,
                json={"mode": "ready_only", "confirm_organization_name": a.name})
    plan = client.get(f"/intake/batches/{b['id']}/rollback-plan", headers=h).json()
    assert plan["fully_reversible"] is True
    assert client.post(f"/intake/batches/{b['id']}/rollback", headers=h,
                       json={"confirm_batch_code": "nope"}).status_code == 400
    r = client.post(f"/intake/batches/{b['id']}/rollback", headers=h,
                    json={"confirm_batch_code": b["batch_code"]})
    assert r.status_code == 200 and r.json()["batch"]["status"] == "rolled_back"
    assert db_session.query(OrgContact).filter(OrgContact.organization_id == a.id).count() == 0
    assert db_session.query(Lead).filter(Lead.organization_id == a.id).count() == 0


def test_contacts_summary_keeps_leads_meaningful(client, db_session, orgs):
    a, _ = orgs
    admin = _user(db_session, a, "org_admin", "admin5@atl.test")
    h = _headers(db_session, admin)
    b = _upload(client, h).json()
    client.post(f"/intake/batches/{b['id']}/analyze", headers=h)
    client.post(f"/intake/batches/{b['id']}/commit", headers=h,
                json={"mode": "ready_only", "confirm_organization_name": a.name})
    s = client.get("/intake/contacts/summary", headers=h).json()
    assert s["contacts"] == 3
    assert s["active_leads"] == 1              # not 3
    assert s["previous_customers"] == 1
    assert s["needs_enrichment"] == 1
    assert s["sms_ready"] == 0
