# -*- coding: utf-8 -*-
"""Manager intake view (synthetic): unassigned New Rate Requests, and time to
first contact measured from recorded task completion - test records excluded,
advisors refused, other workspaces invisible. Read-only; nothing is sent."""
import itertools
import uuid
from datetime import datetime, timedelta

import pytest

from app.models.models import EmailMessage, Lead, Message, Organization, User
from app.models.work_models import LeadTask
from app.services import customer_sites as cs
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)
PAGE = "<!DOCTYPE html><html><head><title>A</title></head><body>x</body></html>"


@pytest.fixture(autouse=True)
def _mounted():
    from app.main import app
    from app.routers.energy_ops_router import router as ops
    if "/energy-ops/queues" not in {getattr(r, "path", "") for r in app.routes}:
        app.include_router(ops)
    yield


@pytest.fixture()
def w(db_session):
    db = db_session

    def org(n):
        o = Organization(name=n, slug="ih-%s" % uuid.uuid4().hex[:8], plan="enterprise",
                         industry="energy", is_active=True)
        db.add(o)
        db.commit()
        return o

    def user(o, role):
        u = User(organization_id=o.id, email="%s-%d@ih.example.test" % (role, next(_SEQ)),
                 password_hash=hash_password("x"), full_name=role.title(), role=role,
                 is_active=True, must_change_password=False)
        db.add(u)
        db.commit()
        return u

    a, b = org("Atl IH"), org("Other IH")
    w = dict(db=db, org=a, mgr=user(a, "org_admin"), adv=user(a, "advisor"), omgr=user(b, "org_admin"),
             slug="ih-site-%d" % next(_SEQ))
    cs.publish(db, organization=a, slug=w["slug"], html=PAGE, title="Atl IH")
    return w


def _h(w, u):
    return {"Authorization": "Bearer %s" % create_access_token(u, w["db"])}


def _form(client, w, email, phone):
    p = {"first_name": "Ina", "last_name": "Take%d" % next(_SEQ), "email": email, "phone": phone,
         "request_type": "Compare electricity options", "service_area": "76102", "form_started_at": 1}
    assert client.post("/site/%s/inquiry" % w["slug"], json=p).status_code == 201


def test_manager_sees_unassigned_and_recorded_time_to_first_contact(client, w):
    db = w["db"]
    _form(client, w, "one@gmail.com", "(817) 555-0201")
    _form(client, w, "two@gmail.com", "(817) 555-0202")
    _form(client, w, "qa@example.com", "(817) 555-0203")           # test record - excluded
    leads = {l.email: l for l in db.query(Lead).filter_by(organization_id=w["org"].id)}
    one = leads["one@gmail.com"]
    one.assigned_to_id = w["adv"].id
    t = db.query(LeadTask).filter_by(lead_id=one.id, source="website").one()
    now = datetime.utcnow()
    t.created_at, t.completed_at, t.status = now - timedelta(hours=5), now - timedelta(hours=3), "done"
    db.commit()

    r = client.get("/energy-ops/intake-health", headers=_h(w, w["mgr"]))
    assert r.status_code == 200, r.text
    body = r.json()
    web = next(c for c in body["first_contact"] if c["source"] == "website")
    assert web["requests"] == 2 and web["contacted"] == 1 and web["open"] == 1
    assert web["median_hours"] == 2.0 and web["p90_hours"] == 2.0
    assert body["unassigned"]["count"] == 1
    assert [x["lead_id"] for x in body["unassigned"]["oldest"]] == [leads["two@gmail.com"].id]
    assert "not a verified call time" in body["basis"]
    assert db.query(Message).count() == 0 and db.query(EmailMessage).count() == 0


def test_advisors_are_refused_and_other_workspaces_see_nothing(client, w):
    _form(client, w, "three@gmail.com", "(817) 555-0204")
    assert client.get("/energy-ops/intake-health", headers=_h(w, w["adv"])).status_code == 403
    other = client.get("/energy-ops/intake-health", headers=_h(w, w["omgr"])).json()
    assert other["unassigned"]["count"] == 0
    assert all(c["requests"] == 0 for c in other["first_contact"])


def test_field_referrals_are_counted_and_overdue_shows_by_owner(client, w):
    db = w["db"]
    r = client.post("/energy-ops/field/visits", headers=_h(w, w["adv"]),
                    json={"outcome": "interested", "phone": "(817) 555-0210", "first_name": "Door",
                          "permission_to_contact": True, "address": "7 Synthetic Ct", "zip_code": "76102"})
    assert r.status_code == 200, r.text
    t = db.query(LeadTask).filter_by(lead_id=r.json()["lead_id"], source="field_referral").one()
    t.due_at = datetime.utcnow() - timedelta(hours=1)
    db.commit()
    body = client.get("/energy-ops/intake-health", headers=_h(w, w["mgr"])).json()
    field = next(c for c in body["first_contact"] if c["source"] == "field_referral")
    assert field["requests"] == 1 and field["overdue"] == 1 and field["median_hours"] is None
    assert sum(o["overdue"] for o in body["open_by_owner"]) == 1
    assert client.get("/energy-ops/intake-health?days=0", headers=_h(w, w["mgr"])).status_code == 422


def test_forwarded_visits_and_closed_leads_do_not_distort_the_numbers(client, w):
    db = w["db"]
    _form(client, w, "four@gmail.com", "(817) 555-0220")
    known = db.query(Lead).filter_by(email="four@gmail.com").one()
    known.assigned_to_id = w["mgr"].id
    db.commit()
    # the advisor cannot see this lead: their visit is forwarded to its owner
    r = client.post("/energy-ops/field/visits", headers=_h(w, w["adv"]),
                    json={"outcome": "do_not_contact", "phone": "817-555-0220", "address": "8 Synthetic Ct",
                          "notes": "Resident said stop knocking"}).json()
    assert r["result"] == "forwarded_to_owner"
    t = db.query(LeadTask).filter_by(lead_id=known.id, source="field_forward").one()
    assert t.title == "Review do-not-contact request from the field"
    _form(client, w, "five@gmail.com", "(817) 555-0221")
    lost = db.query(Lead).filter_by(email="five@gmail.com").one()
    lost.status = "lost"
    db.commit()
    body = client.get("/energy-ops/intake-health", headers=_h(w, w["mgr"])).json()
    field = next(c for c in body["first_contact"] if c["source"] == "field_referral")
    assert field["requests"] == 0 and field["open"] == 0
    assert body["unassigned"]["count"] == 0

    from app.models.models import AuditLogEntry
    e = db.query(AuditLogEntry).filter_by(action="energy.field_visit").order_by(AuditLogEntry.id.desc()).first()
    details = e.details if isinstance(e.details, dict) else __import__("json").loads(e.details)
    assert details["note"] == "Resident said stop knocking"
