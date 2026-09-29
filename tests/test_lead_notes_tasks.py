"""Lead notes and tasks (WS5): CRUD, author rules, filters, tenant isolation."""
from datetime import datetime, timedelta

import pytest

from app.models.models import Lead, Organization, User
from app.models.work_models import LeadNote, LeadTask
from app.services.auth_service import create_access_token, hash_password


def _mount():
    from app.main import app
    from app.routers.work_router import router
    if not any(getattr(r, "path", "") == "/communications/replies" for r in app.routes):
        app.include_router(router)


_mount()


def _h(db, user):
    return {"Authorization": "Bearer %s" % create_access_token(user, db)}


@pytest.fixture()
def admin(db_session, sample_org):
    u = User(organization_id=sample_org.id, email="boss@restland.com",
             password_hash=hash_password("x"), full_name="Boss Admin", role="org_admin",
             must_change_password=False)
    db_session.add(u)
    db_session.commit()
    return u


@pytest.fixture()
def foreign(db_session):
    org = Organization(name="Foreign Org", slug="foreign-org", plan="trial")
    db_session.add(org)
    db_session.commit()
    u = User(organization_id=org.id, email="f@foreign.com", password_hash=hash_password("x"),
             full_name="Foreign Admin", role="org_admin", must_change_password=False)
    db_session.add(u)
    db_session.commit()
    lead = Lead(organization_id=org.id, assigned_to_id=u.id, first_name="F", last_name="L",
                phone="+12145550009")
    db_session.add(lead)
    db_session.commit()
    return {"org": org, "user": u, "lead": lead}


def test_notes_crud(client, db_session, sample_lead, auth_headers):
    url = "/work/leads/%s/notes" % sample_lead.id
    r = client.post(url, json={"body": "Prefers afternoons"}, headers=auth_headers)
    assert r.status_code == 201, r.text
    note = r.json()
    assert note["kind"] == "note" and note["author_name"] == "Advisor One" and note["mine"]
    client.post(url, json={"body": "Internal only", "kind": "internal", "pinned": True}, headers=auth_headers)
    items = client.get(url, headers=auth_headers).json()["items"]
    assert [i["body"] for i in items] == ["Internal only", "Prefers afternoons"]   # pinned first
    assert client.post(url, json={"body": "x", "kind": "sms"}, headers=auth_headers).status_code == 400
    assert client.post(url, json={"body": "   "}, headers=auth_headers).status_code == 400
    r = client.patch("%s/%s" % (url, note["id"]), json={"pinned": True}, headers=auth_headers)
    assert r.json()["pinned"] is True
    assert client.delete("%s/%s" % (url, note["id"]), headers=auth_headers).status_code == 204
    assert db_session.query(LeadNote).count() == 1
    # Lead.notes (the legacy field) is untouched
    db_session.refresh(sample_lead)
    assert sample_lead.notes is None


def test_only_author_deletes_note(client, db_session, sample_lead, admin, auth_headers):
    url = "/work/leads/%s/notes" % sample_lead.id
    nid = client.post(url, json={"body": "mine"}, headers=auth_headers).json()["id"]
    assert client.delete("%s/%s" % (url, nid), headers=_h(db_session, admin)).status_code == 403


def test_notes_cross_tenant_404(client, db_session, admin, foreign):
    h = _h(db_session, admin)
    url = "/work/leads/%s/notes" % foreign["lead"].id
    assert client.get(url, headers=h).status_code == 404
    assert client.post(url, json={"body": "x"}, headers=h).status_code == 404
    db_session.add(LeadNote(organization_id=foreign["org"].id, lead_id=foreign["lead"].id,
                            author_user_id=foreign["user"].id, body="theirs"))
    db_session.commit()
    nid = db_session.query(LeadNote).first().id
    assert client.delete("%s/%s" % (url, nid), headers=h).status_code == 404


def test_advisor_cannot_note_colleagues_lead(client, db_session, sample_org, second_advisor, auth_headers):
    other = Lead(organization_id=sample_org.id, assigned_to_id=second_advisor.id, first_name="O")
    db_session.add(other)
    db_session.commit()
    assert client.post("/work/leads/%s/notes" % other.id, json={"body": "x"},
                       headers=auth_headers).status_code == 404


def test_tasks_create_list_filter_update(client, db_session, sample_lead, admin, second_advisor, foreign):
    h = _h(db_session, admin)
    past = (datetime.utcnow() - timedelta(days=1)).isoformat()
    future = (datetime.utcnow() + timedelta(days=3)).isoformat()
    t1 = client.post("/work/tasks", json={"title": "Overdue call", "lead_id": sample_lead.id,
                                          "due_at": past}, headers=h)
    assert t1.status_code == 201, t1.text
    assert t1.json()["overdue"] is True and t1.json()["assigned_to_id"] == admin.id
    t2 = client.post("/work/tasks", json={"title": "Later", "lead_id": sample_lead.id, "due_at": future,
                                          "assigned_to_id": second_advisor.id}, headers=h).json()
    client.post("/work/tasks", json={"title": "No lead, no date"}, headers=h)
    # cannot assign across tenants, cannot attach a foreign lead
    assert client.post("/work/tasks", json={"title": "x", "assigned_to_id": foreign["user"].id},
                       headers=h).status_code == 400
    assert client.post("/work/tasks", json={"title": "x", "lead_id": foreign["lead"].id},
                       headers=h).status_code == 404

    def titles(**p):
        return sorted(i["title"] for i in client.get("/work/tasks", params=p, headers=h).json()["items"])
    assert titles() == ["Later", "No lead, no date", "Overdue call"]
    assert titles(due="overdue") == ["Overdue call"]
    assert titles(due="upcoming") == ["Later"]
    assert titles(due="none") == ["No lead, no date"]
    assert titles(assigned=second_advisor.id) == ["Later"]
    assert titles(assigned="me") == ["No lead, no date", "Overdue call"]

    r = client.patch("/work/tasks/%s" % t1.json()["id"], json={"status": "done"}, headers=h)
    assert r.json()["status"] == "done" and r.json()["completed_at"]
    assert titles() == ["Later", "No lead, no date"]
    assert titles(status="done") == ["Overdue call"]
    r = client.patch("/work/tasks/%s" % t2["id"], json={"unassign": True}, headers=h)
    assert r.json()["assigned_to_id"] is None
    assert client.patch("/work/tasks/%s" % t2["id"], json={"status": "bogus"}, headers=h).status_code == 400


def test_tasks_cross_tenant_and_owner_scope(client, db_session, sample_lead, second_advisor, foreign, auth_headers):
    ft = LeadTask(organization_id=foreign["org"].id, lead_id=foreign["lead"].id, title="theirs",
                  status="open", assigned_to_id=foreign["user"].id)
    db_session.add(ft)
    # a colleague's private task on a lead that is not ours
    colleague_lead = Lead(organization_id=sample_lead.organization_id,
                          assigned_to_id=second_advisor.id, first_name="C")
    db_session.add(colleague_lead)
    db_session.commit()
    ct = LeadTask(organization_id=sample_lead.organization_id, lead_id=colleague_lead.id,
                  title="colleague's", status="open", assigned_to_id=second_advisor.id)
    db_session.add(ct)
    db_session.commit()
    assert client.get("/work/tasks", headers=auth_headers).json()["total"] == 0
    assert client.patch("/work/tasks/%s" % ft.id, json={"status": "done"},
                        headers=auth_headers).status_code == 404
    assert client.patch("/work/tasks/%s" % ct.id, json={"status": "done"},
                        headers=auth_headers).status_code == 404
