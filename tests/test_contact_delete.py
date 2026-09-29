"""Contacts screen Delete (2026-09-29): owner "I just can't delete any contact".

    DELETE /intake/contacts/{id}
    POST   /intake/contacts/bulk-delete {ids}

Rules pinned here: acting-workspace only (foreign id = 404 / not_found, never
touched); a linked lead is KEPT and detached; a DNC/opted-out number is written
to suppression first; alternate source ids go with the contact; FK references
are detached; audit per contact; import rollback of the contact's batch still
works afterwards; an advisor without the manage capability is refused.
"""
import uuid

import pytest
from sqlalchemy import text

from app.models.intake_models import OrgContact, OrgContactSourceId
from app.models.models import AuditLogEntry, Base, Lead, Organization, User
from app.services.auth_service import create_access_token


def _t(name):
    return Base.metadata.tables[name]


@pytest.fixture()
def admin(db_session, sample_advisor):
    sample_advisor.role = "org_admin"
    db_session.commit()
    return sample_advisor


@pytest.fixture()
def h(db_session, admin):
    return {"Authorization": "Bearer " + create_access_token(admin, db_session)}


def _contact(db, org_id, **kw):
    kw.setdefault("first_name", "Zz")
    kw.setdefault("last_name", "Contact")
    c = OrgContact(organization_id=org_id, **kw)
    db.add(c)
    db.commit()
    return c


def _gone(db, cid):
    db.expire_all()
    return db.query(OrgContact).filter(OrgContact.id == cid).first() is None


def test_delete_one_contact_and_it_leaves_the_list(client, db_session, sample_org, admin, h):
    db_session.execute(text("PRAGMA foreign_keys=ON"))
    c = _contact(db_session, sample_org.id, phone="12145550301")
    db_session.add(OrgContactSourceId(organization_id=sample_org.id, org_contact_id=c.id,
                                      source_system="csv", source_record_id="r1"))
    db_session.commit()
    r = client.delete("/intake/contacts/%s" % c.id, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] is True
    assert _gone(db_session, c.id)
    assert db_session.query(OrgContactSourceId).filter(
        OrgContactSourceId.org_contact_id == c.id).count() == 0
    body = client.get("/intake/contacts?per_page=200", headers=h).json()
    rows = body.get("contacts") or body.get("items") or body.get("results") or []
    assert c.id not in {x["id"] for x in rows}
    assert db_session.query(AuditLogEntry).filter(AuditLogEntry.action == "contact.delete",
                                                  AuditLogEntry.target_id == c.id).count() == 1


def test_a_linked_lead_is_kept_and_detached(client, db_session, sample_org, admin, h):
    lead = Lead(organization_id=sample_org.id, first_name="Keep", last_name="Me", phone="12145550302")
    db_session.add(lead)
    db_session.commit()
    c = _contact(db_session, sample_org.id, lead_id=lead.id, phone="12145550302")
    lead.org_contact_id = c.id
    db_session.commit()
    assert client.delete("/intake/contacts/%s" % c.id, headers=h).status_code == 200
    db_session.expire_all()
    kept = db_session.query(Lead).get(lead.id)
    assert kept is not None and kept.org_contact_id is None


def test_a_dnc_contact_keeps_its_opt_out(client, db_session, sample_org, admin, h):
    c = _contact(db_session, sample_org.id, phone="(214) 555-0303", mobile_phone="214-555-0304",
                 sms_status="dnc")
    assert client.delete("/intake/contacts/%s" % c.id, headers=h).status_code == 200
    sup = _t("suppression_entries")
    phones = {x.phone for x in db_session.execute(
        sup.select().where(sup.c.organization_id == sample_org.id)).fetchall()}
    assert phones == {"12145550303", "12145550304"}


def test_another_workspaces_contact_is_404_and_untouched(client, db_session, sample_org, admin, h):
    other = Organization(name="Other Co", slug="other-co-%s" % uuid.uuid4().hex[:6])
    db_session.add(other)
    db_session.commit()
    c = _contact(db_session, other.id)
    assert client.delete("/intake/contacts/%s" % c.id, headers=h).status_code == 404
    r = client.post("/intake/contacts/bulk-delete", json={"ids": [c.id]}, headers=h)
    assert r.status_code == 200 and r.json()["deleted"] == 0 and r.json()["not_found"] == [c.id]
    assert not _gone(db_session, c.id)


def test_bulk_delete(client, db_session, sample_org, admin, h):
    ids = [_contact(db_session, sample_org.id, first_name="B%d" % i).id for i in range(4)]
    r = client.post("/intake/contacts/bulk-delete", json={"ids": ids + ["nope"]}, headers=h)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["deleted"] == 4 and set(out["deleted_ids"]) == set(ids)
    assert out["not_found"] == ["nope"] and out["failed"] == []
    assert all(_gone(db_session, i) for i in ids)
    assert client.post("/intake/contacts/bulk-delete", json={"ids": []}, headers=h).status_code == 400


def test_an_advisor_without_the_capability_cannot_delete(client, db_session, sample_org,
                                                         sample_advisor, auth_headers):
    c = _contact(db_session, sample_org.id)
    assert client.delete("/intake/contacts/%s" % c.id, headers=auth_headers).status_code == 403
    assert client.post("/intake/contacts/bulk-delete", json={"ids": [c.id]},
                       headers=auth_headers).status_code == 403
    assert not _gone(db_session, c.id)


def test_rollback_plan_after_a_contact_was_deleted(db_session, sample_org, admin):
    """The contact's import batch can still be planned/rolled back: 'already gone'."""
    from app.models.import_models import ImportBatch
    from app.models.intake_models import ImportRecordVersion
    from app.services.intake import rollback as RB
    from app.services import contact_deletion
    b = ImportBatch(organization_id=sample_org.id, batch_code="ZZX-DEL-1", status="committed",
                    source_type="csv", source_filename="zz.csv")
    db_session.add(b)
    db_session.commit()
    c = _contact(db_session, sample_org.id, import_batch_id=b.id)
    db_session.add(ImportRecordVersion(organization_id=sample_org.id, batch_id=b.id,
                                       target_type="org_contact", target_id=c.id, action="created"))
    db_session.commit()
    contact_deletion.delete_contact(db_session, c, admin.id)
    plan = RB.plan(db_session, b)
    assert [it["reason"] for it in plan["items"]] == ["already gone"]
