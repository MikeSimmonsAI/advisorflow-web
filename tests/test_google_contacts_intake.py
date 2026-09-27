# -*- coding: utf-8 -*-
"""Google Contacts is a SOURCE of Universal Intake, not an importer of its own.

Reading Google produces ONE staged intake batch. Nothing becomes a contact or
a lead until the operator analyzes and commits it, exactly like an uploaded
file - so matching, dedupe, review, audit and rollback are the canonical ones.
No request leaves the process: Google is faked.
"""
import pytest

from app.models.import_models import ImportBatch
from app.models.intake_models import OrgContact
from app.models.models import Lead, Organization, User
from app.services import google_contacts_service as GC
from app.services.auth_service import create_access_token, hash_password

PEOPLE = [
    {"resourceName": "people/c1", "names": [{"givenName": "Ann", "familyName": "One"}],
     "phoneNumbers": [{"value": "(214) 555-0101", "type": "home"},
                      {"value": "214-555-0199", "type": "mobile"}],
     "emailAddresses": [{"value": "ann@acmewidgets.com"}],
     "organizations": [{"name": "Acme", "title": "Owner"}]},
    {"resourceName": "people/c2", "names": [{"displayName": "Bob Only"}],
     "emailAddresses": [{"value": "bob@acmewidgets.com"}]},
    {"resourceName": "people/c3"},                       # nothing usable - skipped
]


@pytest.fixture(autouse=True)
def _inline(monkeypatch):
    monkeypatch.setenv("INTAKE_INLINE_JOBS", "1")


def _org(db, name, slug):
    o = Organization(name=name, slug=slug, plan="enterprise", industry="energy")
    db.add(o)
    db.commit()
    return o


def _user(db, org, role, email):
    u = User(organization_id=org.id, email=email, password_hash=hash_password("Pass12345!"),
             full_name=email.split("@")[0], role=role, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _h(db, user):
    return {"Authorization": f"Bearer {create_access_token(user, db)}"}


@pytest.fixture()
def orgs(db_session):
    return _org(db_session, "Google Test Co", "gc-a"), _org(db_session, "Other Co", "gc-b")


@pytest.fixture()
def fake_google(monkeypatch):
    calls = []

    def fetch(user, **kw):
        calls.append(user.id)
        return PEOPLE
    monkeypatch.setattr(GC, "fetch_google_people", fetch)
    return calls


def test_rows_use_the_intake_vocabulary_and_keep_the_google_label_as_source_data():
    rows = GC.google_people_rows(PEOPLE)
    assert len(rows) == 2
    ann, bob = rows
    assert ann["Source Record ID"] == "people/c1"
    assert ann["Phone"] == "2145550101" and ann["Mobile Phone"] == "2145550199"
    assert ann["Google Phone Label"] == "home"          # never a carrier line type
    assert ann["Company"] == "Acme" and ann["Job Title"] == "Owner"
    assert bob["First Name"] == "Bob Only" and bob["Phone"] == ""
    csv = GC.rows_to_csv(rows).decode()
    assert csv.splitlines()[0].split(",") == GC.GOOGLE_CSV_HEADERS
    assert "Phone Line Type" not in csv


def test_google_import_stages_one_batch_and_writes_no_contact_or_lead(client, db_session, orgs,
                                                                       fake_google):
    a, _ = orgs
    admin = _user(db_session, a, "org_admin", "gadmin@a.test")
    r = client.post("/intake/batches/google-contacts", headers=_h(db_session, admin), json={})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["organization_id"] == a.id and b["status"] == "mapping"
    batch = db_session.query(ImportBatch).filter(ImportBatch.id == b["id"]).one()
    assert batch.source_system == "google_contacts" and batch.original_row_count == 2
    assert "gadmin@a.test" in batch.source_detail
    assert db_session.query(OrgContact).count() == 0
    assert db_session.query(Lead).filter(Lead.organization_id == a.id).count() == 0
    # the canonical mapping recognises every column it should
    prev = client.get(f"/intake/batches/{b['id']}/preview", headers=_h(db_session, admin)).json()
    mapped = {c["header"]: (c.get("mapping") or {}).get("target") for c in prev["columns"]}
    for col, field in (("Source Record ID", "source_record_id"), ("Email", "email"),
                       ("Phone", "phone"), ("Mobile Phone", "mobile_phone")):
        assert mapped.get(col) == field, (col, mapped)
    kinds = {c["header"]: (c.get("mapping") or {}).get("kind") for c in prev["columns"]}
    assert kinds["Google Phone Label"] == "source"         # kept as source data, not a line type


def test_google_batch_goes_through_analysis_and_commit_like_any_file(client, db_session, orgs,
                                                                      fake_google):
    a, _ = orgs
    admin = _user(db_session, a, "org_admin", "gadmin2@a.test")
    h = _h(db_session, admin)
    bid = client.post("/intake/batches/google-contacts", headers=h, json={}).json()["id"]
    assert client.post(f"/intake/batches/{bid}/analyze", headers=h).status_code == 202
    r = client.post(f"/intake/batches/{bid}/commit", headers=h, json={})
    assert r.status_code == 202 and r.json()["status"] == "staged"   # stage-only by default
    assert db_session.query(Lead).filter(Lead.organization_id == a.id).count() == 0


def test_legacy_endpoint_no_longer_writes_leads(client, db_session, orgs, fake_google):
    a, _ = orgs
    admin = _user(db_session, a, "org_admin", "gadmin3@a.test")
    r = client.post("/google-contacts/import", headers=_h(db_session, admin), json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["staged"] is True and body["imported"] == 0 and body["next"] == f"/imports/{body['batch_id']}"
    assert db_session.query(Lead).filter(Lead.organization_id == a.id).count() == 0
    assert db_session.query(OrgContact).count() == 0


def test_permissions_and_tenant_isolation(client, db_session, orgs, fake_google):
    a, b = orgs
    adv = _user(db_session, a, "advisor", "gadv@a.test")
    assert client.post("/intake/batches/google-contacts", headers=_h(db_session, adv), json={}).status_code == 403
    assert client.post("/google-contacts/import", headers=_h(db_session, adv), json={}).status_code == 403
    assert fake_google == []                                  # Google is never read without permission
    admin_a = _user(db_session, a, "org_admin", "ga@a.test")
    admin_b = _user(db_session, b, "org_admin", "gb@b.test")
    bid = client.post("/intake/batches/google-contacts", headers=_h(db_session, admin_a), json={}).json()["id"]
    assert client.get(f"/intake/batches/{bid}", headers=_h(db_session, admin_b)).status_code == 404
    assert client.get("/intake/batches", headers=_h(db_session, admin_b)).json()["total"] == 0


def test_not_connected_and_empty_are_clean_400s(client, db_session, orgs, monkeypatch):
    a, _ = orgs
    admin = _user(db_session, a, "org_admin", "gnc@a.test")
    def boom(user, **kw):
        raise ValueError("Google account not connected. Please connect Google in Settings first.")
    monkeypatch.setattr(GC, "fetch_google_people", boom)
    r = client.post("/intake/batches/google-contacts", headers=_h(db_session, admin), json={})
    assert r.status_code == 400 and "not connected" in r.json()["detail"]
    monkeypatch.setattr(GC, "fetch_google_people", lambda user, **kw: [{"resourceName": "people/x"}])
    r = client.post("/intake/batches/google-contacts", headers=_h(db_session, admin), json={})
    assert r.status_code == 400
    assert db_session.query(ImportBatch).count() == 0


class _Resp:
    def __init__(self, data, status=200):
        self._d, self.status_code, self.ok = data, status, status < 400

    def json(self):
        return self._d


class _Session:
    def __init__(self, pages):
        self.pages, self.params = pages, []

    def get(self, url, params=None, headers=None, timeout=None):
        self.params.append(dict(params or {}))
        return self.pages.pop(0)


def test_fetch_follows_pages_and_reports_permission_errors(monkeypatch):
    monkeypatch.setattr(GC, "_get_access_token", lambda user: "tok")
    s = _Session([_Resp({"connections": [{"resourceName": "people/1"}], "nextPageToken": "p2"}),
                  _Resp({"connections": [{"resourceName": "people/2"}]})])
    out = GC.fetch_google_people(object(), session=s)
    assert [p["resourceName"] for p in out] == ["people/1", "people/2"]
    assert s.params[1]["pageToken"] == "p2"
    with pytest.raises(ValueError, match="permission"):
        GC.fetch_google_people(object(), session=_Session([_Resp({}, 403)]))
