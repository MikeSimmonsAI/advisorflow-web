"""P0 (2026-09-29): deleting a lead from the Leads screen failed in production
with "Unable to reach the server".

Two root causes, both pinned here:
  1. `db.delete(lead)` relied on database cascades, but several tables reference
     leads.id with no cascade (voice_calls and pipeline_conversations are NOT
     NULL). Any such row raised an IntegrityError -> 500.
  2. That 500 left the app WITHOUT CORS headers (Starlette's Exception handler
     runs outside CORSMiddleware), so the browser hid it and the page reported
     a dead server.

The test database enforces foreign keys (PRAGMA foreign_keys=ON) so a missing
cascade fails here exactly as it does on Postgres.
"""
import uuid

import pytest
from sqlalchemy import text

from app.models.models import Base, Lead, User
from app.services.auth_service import create_access_token


def _t(name):
    return Base.metadata.tables[name]


def _fk_on(db):
    db.execute(text("PRAGMA foreign_keys=ON"))


@pytest.fixture()
def admin(db_session, sample_advisor):
    sample_advisor.role = "org_admin"
    db_session.commit()
    return sample_advisor


@pytest.fixture()
def h(db_session, admin):
    return {"Authorization": "Bearer " + create_access_token(admin, db_session)}


def _lead(db, org_id, **kw):
    lead = Lead(organization_id=org_id, first_name=kw.pop("first_name", "Del"),
                last_name=kw.pop("last_name", "Target"), phone=kw.pop("phone", "12145550199"), **kw)
    db.add(lead)
    db.commit()
    return lead


def _ins(db, table, **values):
    values.setdefault("id", str(uuid.uuid4()))
    db.execute(_t(table).insert().values(**values))
    db.commit()
    return values["id"]


def test_a_lead_with_history_in_every_non_cascading_table_deletes(client, db_session, sample_org, admin, h):
    _fk_on(db_session)
    lead = _lead(db_session, sample_org.id, assigned_to_id=admin.id)
    other = _lead(db_session, sample_org.id, first_name="Dup", duplicate_of_lead_id=lead.id)
    _ins(db_session, "messages", lead_id=lead.id, sender_id=admin.id, body="hi")
    _ins(db_session, "replies", lead_id=lead.id, body="yes")
    _ins(db_session, "voice_calls", lead_id=lead.id, advisor_id=admin.id,
         organization_id=sample_org.id, to_phone="+12145550199")
    _ins(db_session, "pipeline_conversations", organization_id=sample_org.id,
         lead_id=lead.id, advisor_id=admin.id)
    crm = _ins(db_session, "crm_contacts", organization_id=sample_org.id, lead_id=lead.id)
    _ins(db_session, "notifications", user_id=admin.id, type="x", message="m", lead_id=lead.id)
    oc = _ins(db_session, "org_contacts", organization_id=sample_org.id, lead_id=lead.id)
    consent = _ins(db_session, "sms_consent_records", organization_id=sample_org.id,
                   program="default", phone_normalized="12145550199", lead_id=lead.id)
    _ins(db_session, "lead_notes", organization_id=sample_org.id, lead_id=lead.id, body="note")
    _ins(db_session, "lead_tasks", organization_id=sample_org.id, lead_id=lead.id, title="call")

    r = client.delete("/leads/%s" % lead.id, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] is True
    assert db_session.query(Lead).filter(Lead.id == lead.id).first() is None
    # Lead-owned history went with it.
    for t in ("messages", "replies", "voice_calls", "pipeline_conversations", "lead_notes", "lead_tasks"):
        assert db_session.execute(_t(t).select().where(_t(t).c.lead_id == lead.id)).first() is None, t
    # Independent records survive, detached.
    for t, rid in (("crm_contacts", crm), ("org_contacts", oc), ("sms_consent_records", consent)):
        row = db_session.execute(_t(t).select().where(_t(t).c.id == rid)).first()
        assert row is not None and row.lead_id is None, t
    db_session.expire_all()
    assert db_session.query(Lead).get(other.id).duplicate_of_lead_id is None


def test_deleting_a_dnc_lead_keeps_the_opt_out(client, db_session, sample_org, admin, h):
    _fk_on(db_session)
    lead = _lead(db_session, sample_org.id, status="dnc", phone="(214) 555-0177")
    r = client.delete("/leads/%s" % lead.id, headers=h)
    assert r.status_code == 200, r.text
    sup = _t("suppression_entries")
    rows = db_session.execute(sup.select().where(sup.c.organization_id == sample_org.id)).fetchall()
    assert [x.phone for x in rows] == ["12145550177"]


def test_a_wholesale_seller_lead_is_refused_with_a_reason(client, db_session, sample_org, admin, h):
    _fk_on(db_session)
    lead = _lead(db_session, sample_org.id)
    db_session.execute(text("PRAGMA foreign_keys=OFF"))  # property row not needed for this check
    _ins(db_session, "wholesale_deals", organization_id=sample_org.id,
         property_id=str(uuid.uuid4()), seller_lead_id=lead.id)
    r = client.delete("/leads/%s" % lead.id, headers=h)
    assert r.status_code == 409 and "wholesale deal" in r.json()["detail"]
    assert db_session.query(Lead).filter(Lead.id == lead.id).first() is not None


def test_another_tenants_lead_cannot_be_deleted(client, db_session, sample_org, admin, h):
    from app.models.models import Organization
    other = Organization(name="Other Co", slug="other-co")
    db_session.add(other)
    db_session.commit()
    lead = _lead(db_session, other.id)
    assert client.delete("/leads/%s" % lead.id, headers=h).status_code == 404
    assert db_session.query(Lead).filter(Lead.id == lead.id).first() is not None


def test_an_advisor_cannot_delete_someone_elses_lead(client, db_session, sample_org, sample_advisor, auth_headers):
    colleague = User(organization_id=sample_org.id, email="c@x.test", password_hash="x",
                     full_name="Colleague", role="advisor")
    db_session.add(colleague)
    db_session.commit()
    lead = _lead(db_session, sample_org.id, assigned_to_id=colleague.id)
    assert client.delete("/leads/%s" % lead.id, headers=auth_headers).status_code in (403, 404)
    assert db_session.query(Lead).filter(Lead.id == lead.id).first() is not None


def test_bulk_delete_and_the_lead_leaves_every_list(client, db_session, sample_org, admin, h):
    _fk_on(db_session)
    ids = []
    for i in range(3):
        lead = _lead(db_session, sample_org.id, first_name="Bulk%d" % i, phone="1214555020%d" % i,
                     assigned_to_id=admin.id)
        _ins(db_session, "voice_calls", lead_id=lead.id, advisor_id=admin.id,
             organization_id=sample_org.id, to_phone="+1214555020%d" % i)
        ids.append(lead.id)
    test_lead = _lead(db_session, sample_org.id, first_name="Sandbox", phone="12145550210",
                      is_test=True) if hasattr(Lead, "is_test") else None
    if test_lead is not None:
        ids.append(test_lead.id)
    for lid in ids:
        assert client.delete("/leads/%s" % lid, headers=h).status_code == 200
    def _ids(body):
        if isinstance(body, dict):
            for k in ("leads", "items", "results"):
                if isinstance(body.get(k), list):
                    return {r["id"] for r in body[k]}
            return set()
        return {r["id"] for r in body}
    r = client.get("/leads/?limit=200", headers=h)
    assert r.status_code == 200
    assert not (_ids(r.json()) & set(ids))
    r = client.get("/leads/?search=Bulk", headers=h)
    assert r.status_code == 200 and not _ids(r.json())


def test_a_server_error_still_carries_cors_headers(db_session, sample_org):
    """The 500 must be readable by the page, not reported as a dead server."""
    from fastapi.testclient import TestClient
    from app.main import app, ALLOWED_ORIGINS
    origin = next(o for o in ALLOWED_ORIGINS if o.startswith("https://"))

    from fastapi import APIRouter
    probe = APIRouter()

    @probe.get("/__probe_boom")
    def _boom():
        raise RuntimeError("boom")

    if not any(getattr(r, "path", None) == "/__probe_boom" for r in app.routes):
        app.include_router(probe)
    r = TestClient(app, raise_server_exceptions=False).get("/__probe_boom", headers={"Origin": origin})
    assert r.status_code == 500
    assert r.headers.get("access-control-allow-origin") == origin
    assert "boom" not in r.text
