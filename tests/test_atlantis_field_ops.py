# -*- coding: utf-8 -*-
"""Residential field acquisition (synthetic reps and residents): door outcomes
are recorded as what the rep said happened; only a resident who gave a
contact AND permission becomes a lead; the lead lands in the normal Atlantis
queue credited to the rep; managers see every rep, reps see themselves.
No location verification is claimed and nothing is sent."""
import itertools
import json
import uuid
from datetime import datetime

import pytest

from app.models.models import EmailMessage, Lead, Message, Notification, Organization, User
from app.models.work_models import LeadTask
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


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
        o = Organization(name=n, slug="f-%s" % uuid.uuid4().hex[:8], plan="enterprise",
                         industry="energy", is_active=True)
        db.add(o)
        db.commit()
        return o

    def user(o, role, name):
        u = User(organization_id=o.id, email="%s-%d@f.example.test" % (name, next(_SEQ)),
                 password_hash=hash_password("x"), full_name=name.title(), role=role,
                 is_active=True, must_change_password=False)
        db.add(u)
        db.commit()
        return u

    a, b = org("Atl F"), org("Other F")
    return dict(db=db, org=a, mgr=user(a, "org_admin", "manager"), rep=user(a, "advisor", "rita"),
                rep2=user(a, "advisor", "ray"), other=user(b, "org_admin", "outsider"))


def _h(w, u):
    return {"Authorization": "Bearer %s" % create_access_token(u, w["db"])}


def _visit(client, w, u, **kw):
    body = {"outcome": "no_answer", "address": "%d Synthetic Ln, Fort Worth" % next(_SEQ), "zip_code": "76102"}
    body.update(kw)
    return client.post("/energy-ops/field/visits", headers=_h(w, u), json=body)


def test_door_outcomes_are_recorded_once_and_never_become_leads(client, w):
    db = w["db"]
    r1 = _visit(client, w, w["rep"], outcome="no_answer", address="1 Oak St")
    r2 = _visit(client, w, w["rep"], outcome="no_answer", address="1 oak st")     # same door again
    assert r1.json()["duplicate"] is False and r2.json()["duplicate"] is True
    _visit(client, w, w["rep"], outcome="door_hanger_left", address="2 Oak St")
    _visit(client, w, w["rep"], outcome="declined", address="3 Oak St")
    assert db.query(Lead).filter_by(organization_id=w["org"].id).count() == 0
    assert _visit(client, w, w["rep"], outcome="teleported").status_code == 422
    assert _visit(client, w, w["rep"], address="").status_code == 422


def test_interested_resident_needs_contact_and_permission(client, w):
    assert _visit(client, w, w["rep"], outcome="interested", phone="(817) 555-0101").status_code == 422
    assert _visit(client, w, w["rep"], outcome="interested", permission_to_contact=True).status_code == 422
    assert w["db"].query(Lead).count() == 0


def test_field_referral_reaches_the_advisor_queue_credited_to_the_rep(client, w):
    db = w["db"]
    r = _visit(client, w, w["rep"], outcome="interested", first_name="Hana", last_name="Synthetic",
               phone="(817) 555-0102", permission_to_contact=True, address="10 Elm St", zip_code="76104")
    assert r.status_code == 200 and r.json()["result"] == "new_referral"
    lead = db.query(Lead).filter_by(id=r.json()["lead_id"]).one()
    cf = json.loads(lead.custom_fields)
    assert lead.organization_id == w["org"].id and lead.tier == "new_inquiry"
    assert lead.source == "Atlantis Field Referral" and lead.sms_consent is False
    assert cf["field_rep_id"] == w["rep"].id and cf["contact_permission"]["how"].startswith("verbal")
    task = db.query(LeadTask).filter_by(lead_id=lead.id).one()
    assert task.title == "Contact field referral" and task.status == "open"
    assert {n.user_id for n in db.query(Notification).filter_by(lead_id=lead.id)} == {w["mgr"].id}
    rr = client.get("/rate-requests/", headers=_h(w, w["mgr"]), params={"status": "new"})
    if rr.status_code == 200:                      # mounted in this app build
        assert lead.id in {i["id"] for i in rr.json()["items"]}
    # the same resident again, from a rep who cannot see that lead: no duplicate,
    # no id revealed - the owner gets a task instead
    again = _visit(client, w, w["rep2"], outcome="interested", phone="817-555-0102",
                   permission_to_contact=True, address="10 Elm St").json()
    assert again["result"] == "forwarded_to_owner" and not again.get("lead_id")
    assert db.query(Lead).filter_by(organization_id=w["org"].id).count() == 1
    assert db.query(LeadTask).filter_by(lead_id=lead.id, title="Field rep reported interest").count() == 1
    # a manager who can see it is linked to it
    seen = _visit(client, w, w["mgr"], outcome="interested", phone="817 555 0102",
                  permission_to_contact=True, address="10 Elm Street").json()
    assert seen["result"] == "existing_customer" and seen["lead_id"] == lead.id
    assert db.query(Message).count() == 0 and db.query(EmailMessage).count() == 0


def test_business_met_in_the_field_becomes_a_commercial_opportunity(client, w):
    assert _visit(client, w, w["rep"], outcome="business_lead", phone="(817) 555-0110",
                  permission_to_contact=True).status_code == 422          # no business name
    r = _visit(client, w, w["rep"], outcome="business_lead", phone="(817) 555-0110", company="Synthetic Tacos",
               permission_to_contact=True, first_name="Owner").json()
    lead_id = r["lead_id"]
    q = client.get("/energy-ops/leads/%s/qualification" % lead_id, headers=_h(w, w["mgr"])).json()
    assert q["segment"] == "commercial"
    assert client.get("/energy-ops/leads/%s/commercial" % lead_id, headers=_h(w, w["mgr"])).status_code == 200


def test_do_not_contact_at_the_door_marks_the_known_person(client, w):
    db = w["db"]
    lid = _visit(client, w, w["rep"], outcome="interested", phone="(817) 555-0120",
                 permission_to_contact=True).json()["lead_id"]
    # a rep who cannot see the lead cannot change it: the owner reviews it
    r = _visit(client, w, w["rep2"], outcome="do_not_contact", phone="817.555.0120", address="99 Pine St").json()
    assert r["result"] == "forwarded_to_owner" and not r.get("lead_id")
    assert db.query(Lead).get(lid).status != "dnc"
    assert db.query(LeadTask).filter_by(lead_id=lid, title="Review do-not-contact request from the field").count() == 1
    r = _visit(client, w, w["mgr"], outcome="do_not_contact", phone="817.555.0120", address="98 Pine St").json()
    assert r["result"] == "marked_dnc" and r["lead_id"] == lid
    assert db.query(Lead).get(lid).status == "dnc"


def test_other_workspace_people_are_never_matched(client, w):
    db = w["db"]
    stranger = Lead(organization_id=w["other"].organization_id, first_name="Ex", last_name="Ternal",
                    phone="(817) 555-0190", email="ext@gmail.com", status="new")
    db.add(stranger)
    db.commit()
    r = _visit(client, w, w["mgr"], outcome="do_not_contact", phone="817-555-0190").json()
    assert r["result"] != "marked_dnc" and r.get("lead_id") in (None, "")
    assert db.query(Lead).get(stranger.id).status == "new"


def test_two_residents_at_one_address_are_two_referrals(client, w):
    db = w["db"]
    a = _visit(client, w, w["rep"], outcome="interested", phone="(817) 555-0141", first_name="Unit",
               last_name="One", permission_to_contact=True, address="500 Tower Apts").json()
    b = _visit(client, w, w["rep"], outcome="interested", phone="(817) 555-0142", first_name="Unit",
               last_name="Two", permission_to_contact=True, address="500 Tower Apts").json()
    assert a["result"] == b["result"] == "new_referral" and a["lead_id"] != b["lead_id"]
    assert db.query(Lead).filter_by(organization_id=w["org"].id).count() == 2
    c = _visit(client, w, w["rep"], outcome="interested", phone="817-555-0142", first_name="Unit",
               last_name="Two", permission_to_contact=True, address="500 tower apts").json()
    assert c["duplicate"] is True


def test_manager_sees_every_rep_reps_see_themselves_others_see_nothing(client, w):
    db = w["db"]
    _visit(client, w, w["rep"], outcome="no_answer")
    _visit(client, w, w["rep"], outcome="door_hanger_left")
    lid = _visit(client, w, w["rep"], outcome="interested", phone="(817) 555-0130",
                 permission_to_contact=True).json()["lead_id"]
    _visit(client, w, w["rep2"], outcome="declined")
    m = client.get("/energy-ops/field/summary", headers=_h(w, w["mgr"])).json()
    rows = {r["rep_id"]: r for r in m["reps"]}
    assert m["scope"] == "all_reps" and set(rows) == {w["rep"].id, w["rep2"].id}
    rita = rows[w["rep"].id]
    assert rita["visits"] == 3 and rita["new_referrals"] == 1 and rita["door_hangers"] == 1
    assert rita["referral_rate"] == round(1 / 3, 3) and rita["enrolled"] == 0
    assert "not verified" in m["basis"]
    # enrollment counts only from the Enroll action (enrolled_at)
    db.query(Lead).get(lid).enrolled_at = datetime.utcnow()
    db.commit()
    m = client.get("/energy-ops/field/summary", headers=_h(w, w["mgr"])).json()
    assert {r["rep_id"]: r for r in m["reps"]}[w["rep"].id]["enrolled"] == 1
    own = client.get("/energy-ops/field/summary", headers=_h(w, w["rep2"])).json()
    assert own["scope"] == "own" and [r["rep_id"] for r in own["reps"]] == [w["rep2"].id]
    assert client.get("/energy-ops/field/summary", headers=_h(w, w["other"])).json()["reps"] == []
