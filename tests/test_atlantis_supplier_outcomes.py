# -*- coding: utf-8 -*-
"""Supplier hand-offs: one open hand-off per supplier, and outcomes that keep
"our staff reported" apart from "the supplier confirmed" - without ever
marking a customer enrolled. Synthetic records only; nothing is sent."""
import itertools
import json
import uuid

import pytest

from app.models.models import EmailMessage, Lead, Message, Organization, User
from app.models.work_models import LeadTask
from app.services import energy_suppliers as es
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


@pytest.fixture(autouse=True)
def _mounted():
    from app.main import app
    from app.routers.energy_ops_router import router as ops
    if "/energy-ops/queues" not in {getattr(r, "path", "") for r in app.routes}:
        app.include_router(ops)
    yield


def _org(db, name):
    o = Organization(name=name, slug="o-%s" % uuid.uuid4().hex[:8], plan="enterprise",
                     industry="energy", is_active=True)
    db.add(o)
    db.commit()
    return o


def _user(db, org, role):
    u = User(organization_id=org.id, email="%s-%d@so.example.test" % (role, next(_SEQ)),
             password_hash=hash_password("Pass12345!"), full_name=role.title(), role=role,
             is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _h(db, u):
    return {"Authorization": "Bearer %s" % create_access_token(u, db)}


def _lead(db, org, owner=None, cf=None):
    l = Lead(organization_id=org.id, first_name="Sam", last_name="Synthetic", tier="new_inquiry",
             status="new", assigned_to_id=owner.id if owner else None,
             custom_fields=json.dumps(cf) if cf is not None else None)
    db.add(l)
    db.commit()
    return l


@pytest.fixture()
def w(db_session):
    db = db_session
    org, other = _org(db, "Atlantis T"), _org(db, "Other T")
    return dict(db=db, org=org, other=other, admin=_user(db, org, "org_admin"),
                adv=_user(db, org, "advisor"), fadmin=_user(db, other, "org_admin"))


def _handoff(client, w, lead, supplier="comparepower", user=None):
    return client.post("/energy-ops/leads/%s/handoff" % lead.id,
                       headers=_h(w["db"], user or w["admin"]), json={"supplier": supplier, "note": "sent"})


def _quiet(db):
    from app.models.provider_transaction_models import ProviderTransaction
    assert db.query(Message).count() == 0 and db.query(EmailMessage).count() == 0
    assert db.query(ProviderTransaction).count() == 0


def test_double_click_records_one_handoff_and_one_task(client, w):
    db, lead = w["db"], _lead(w["db"], w["org"])
    a, b = _handoff(client, w, lead), _handoff(client, w, lead)
    assert a.status_code == b.status_code == 200
    assert a.json()["duplicate"] is False and b.json()["duplicate"] is True
    assert b.json()["handoff"]["id"] == a.json()["handoff"]["id"]
    assert b.json()["task_id"] == a.json()["task_id"]
    db.refresh(lead)
    assert len(json.loads(lead.custom_fields)["supplier_handoffs"]) == 1
    assert db.query(LeadTask).filter_by(lead_id=lead.id, source="supplier_handoff").count() == 1
    _quiet(db)


def test_a_different_supplier_is_a_separate_handoff(client, w):
    db, lead = w["db"], _lead(w["db"], w["org"])
    _handoff(client, w, lead, "comparepower")
    assert _handoff(client, w, lead, "gexa").json()["duplicate"] is False
    assert db.query(LeadTask).filter_by(lead_id=lead.id, source="supplier_handoff").count() == 2


def _outcome(client, w, lead, hid, outcome, source="staff_report", note="Customer told me", user=None):
    return client.post("/energy-ops/leads/%s/handoffs/%s/outcome" % (lead.id, hid),
                       headers=_h(w["db"], user or w["admin"]),
                       json={"outcome": outcome, "source": source, "note": note})


def test_interim_outcome_keeps_the_task_open_and_listed(client, w):
    db, lead = w["db"], _lead(w["db"], w["org"])
    hid = _handoff(client, w, lead).json()["handoff"]["id"]
    r = _outcome(client, w, lead, hid, "customer_action_pending")
    assert r.status_code == 200 and r.json()["tasks_closed"] == 0
    assert db.query(LeadTask).filter_by(lead_id=lead.id, status="open").count() == 1
    lst = client.get("/energy-ops/supplier-handoffs", headers=_h(db, w["admin"])).json()
    assert [i["handoff"]["status"] for i in lst["items"]] == ["customer_action_pending"]


def test_staff_reported_confirmation_closes_task_but_never_enrolls(client, w):
    db, lead = w["db"], _lead(w["db"], w["org"])
    hid = _handoff(client, w, lead).json()["handoff"]["id"]
    r = _outcome(client, w, lead, hid, "confirmed", "staff_report", "Customer says they signed up")
    body = r.json()
    assert r.status_code == 200 and body["tasks_closed"] == 1 and body["enrolled"] is False
    assert body["handoff"]["status_source"] == "staff_report" and body["next_step"] is None
    db.refresh(lead)
    assert lead.tier == "new_inquiry" and lead.enrolled_at is None
    assert client.get("/energy-ops/supplier-handoffs",
                      headers=_h(db, w["admin"])).json()["total"] == 0
    # final outcomes are final; a new hand-off is a new event
    assert _outcome(client, w, lead, hid, "cancelled").status_code == 422
    assert _handoff(client, w, lead).json()["duplicate"] is False
    _quiet(db)


def test_supplier_confirmation_points_to_the_enroll_action(client, w):
    db, lead = w["db"], _lead(w["db"], w["org"])
    hid = _handoff(client, w, lead).json()["handoff"]["id"]
    body = _outcome(client, w, lead, hid, "confirmed", "supplier_confirmation",
                    "ComparePower affiliate dashboard shows the enrollment").json()
    assert body["enrolled"] is False and "Enroll" in body["next_step"]
    db.refresh(lead)
    assert lead.tier == "new_inquiry"


def test_outcome_input_is_checked(client, w):
    lead = _lead(w["db"], w["org"])
    hid = _handoff(client, w, lead).json()["handoff"]["id"]
    assert _outcome(client, w, lead, hid, "enrolled").status_code == 422
    assert _outcome(client, w, lead, hid, "confirmed", "rumour").status_code == 422
    assert _outcome(client, w, lead, hid, "confirmed", note="  ").status_code == 422
    assert _outcome(client, w, lead, "nope", "confirmed").status_code == 404


def test_outcomes_are_scoped_to_the_workspace_and_owner(client, w):
    db, lead = w["db"], _lead(w["db"], w["org"], owner=w["admin"])
    hid = _handoff(client, w, lead).json()["handoff"]["id"]
    assert _outcome(client, w, lead, hid, "confirmed", user=w["fadmin"]).status_code == 404
    assert _outcome(client, w, lead, hid, "confirmed", user=w["adv"]).status_code in (403, 404)
    assert client.get("/energy-ops/supplier-handoffs",
                      headers=_h(db, w["fadmin"])).json()["total"] == 0
    assert client.get("/energy-ops/leads/%s/handoffs" % lead.id,
                      headers=_h(db, w["fadmin"])).status_code == 404


def test_hand_offs_written_before_outcomes_existed_still_work(client, w):
    db = w["db"]
    lead = _lead(db, w["org"], cf={"supplier_handoffs": [
        {"supplier": "gexa", "at": "2026-10-19T00:00:00Z", "by": None, "note": "old"}, "junk"]})
    items = client.get("/energy-ops/leads/%s/handoffs" % lead.id,
                       headers=_h(db, w["admin"])).json()["items"]
    assert items[0]["id"] == "legacy-0" and items[0]["status"] == "referred"
    assert _handoff(client, w, lead, "gexa").json()["duplicate"] is True
    assert _outcome(client, w, lead, "legacy-0", "not_completed").status_code == 200
    db.refresh(lead)
    stored = json.loads(lead.custom_fields)["supplier_handoffs"]
    assert stored[0]["status"] == "not_completed" and stored[1] == "junk"
