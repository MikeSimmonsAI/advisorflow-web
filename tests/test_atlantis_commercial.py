# -*- coding: utf-8 -*-
"""Commercial energy sale, advisor-led, end to end on synthetic records:
business inquiry -> discovery -> information -> usage -> quote -> proposal ->
contract -> supplier confirmed -> won; several service locations under one
business; lost and deferred with history. No supplier call, no quote sent,
no price stored, no enrollment set."""
import itertools
import json
import uuid

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
        o = Organization(name=n, slug="c-%s" % uuid.uuid4().hex[:8], plan="enterprise",
                         industry="energy", is_active=True)
        db.add(o)
        db.commit()
        return o

    def user(o):
        u = User(organization_id=o.id, email="a-%d@c.example.test" % next(_SEQ), password_hash=hash_password("x"),
                 full_name="A", role="org_admin", is_active=True, must_change_password=False)
        db.add(u)
        db.commit()
        return u

    a, b = org("Atl C"), org("Other C")
    w = dict(db=db, org=a, admin=user(a), fadmin=user(b), slug="c-site-%d" % next(_SEQ))
    cs.publish(db, organization=a, slug=w["slug"], html=PAGE, title="Atl C")
    w["h"] = {"Authorization": "Bearer %s" % create_access_token(w["admin"], db)}
    return w


def _biz(client, w, **kw):
    p = {"first_name": "Pat", "last_name": "Owner%d" % next(_SEQ), "email": "pat%d@gmail.com" % next(_SEQ),
         "request_type": "Commercial energy procurement", "company": "Synthetic Diner LLC",
         "locations": "3 sites", "service_area": "Dallas", "form_started_at": 1}
    p.update(kw)
    assert client.post("/site/%s/inquiry" % w["slug"], json=p).status_code == 201
    lead = w["db"].query(Lead).filter_by(organization_id=w["org"].id).order_by(Lead.created_at.desc()).first()
    lead.assigned_to_id = w["admin"].id
    w["db"].commit()
    return lead


def _stage(client, w, lead, stage, **kw):
    return client.post("/energy-ops/leads/%s/commercial/stage" % lead.id, headers=w["h"],
                       json=dict({"stage": stage}, **kw))


def _open(w, lead):
    return sorted(t.title for t in w["db"].query(LeadTask).filter_by(lead_id=lead.id, source="commercial",
                                                                     status="open"))


def test_commercial_opportunity_from_inquiry_to_supplier_confirmed_win(client, w):
    db, lead = w["db"], _biz(client, w)
    v = client.get("/energy-ops/leads/%s/commercial" % lead.id, headers=w["h"]).json()
    assert v["stage"] == "discovery" and v["company"] == "Synthetic Diner LLC" and v["tier"] == "new_inquiry"

    assert _stage(client, w, lead, "information_requested").status_code == 200
    assert _stage(client, w, lead, "information_requested").status_code == 200   # repeat
    assert _open(w, lead) == ["Get usage / bills from the business"]

    r = _stage(client, w, lead, "usage_received", expected_annual_kwh="240000").json()
    assert r["tier"] == "rate_review" and _open(w, lead) == []
    _stage(client, w, lead, "quote_requested")
    assert _open(w, lead) == ["Follow up on supplier quote"]
    assert _stage(client, w, lead, "won").status_code == 422           # not before supplier confirmation

    # a supplier hand-off is recorded - it never moves the commercial stage
    client.post("/energy-ops/leads/%s/handoff" % lead.id, headers=w["h"], json={"supplier": "gexa"})
    assert client.get("/energy-ops/leads/%s/commercial" % lead.id,
                      headers=w["h"]).json()["stage"] == "quote_requested"

    for s in ("quote_received", "proposal_presented", "decision_pending", "contract_submitted",
              "supplier_confirmed"):
        assert _stage(client, w, lead, s, expected_decision_date="2026-12-01").status_code == 200
    r = _stage(client, w, lead, "won", note="Gexa rep confirmed the signed contract by email").json()
    assert r["stage"] == "won" and r["enrolled"] is False and r["tier"] == "proposal_sent"
    assert [h["to"] for h in r["history"]][-1] == "won" and len(r["history"]) == 10
    db.refresh(lead)
    assert lead.tier == "proposal_sent" and lead.enrolled_at is None
    assert _stage(client, w, lead, "discovery").status_code == 422     # closed stays closed
    assert db.query(Message).count() == 0 and db.query(EmailMessage).count() == 0


def test_several_locations_stay_under_one_business(client, w):
    lead = _biz(client, w)
    url = "/energy-ops/leads/%s/commercial/locations" % lead.id
    a = client.post(url, headers=w["h"], json={"address": "100 Main St, Dallas", "meter_id": "1044372000111",
                                                "contract_end_date": "2027-05-31", "utility": "Oncor"}).json()
    b = client.post(url, headers=w["h"], json={"address": "200 Elm St, Dallas",
                                                "contract_end_date": "2027-02-28"}).json()
    again = client.post(url, headers=w["h"], json={"address": "200  elm st dallas"}).json()
    assert a["duplicate"] is False and b["duplicate"] is False and again["duplicate"] is True
    assert again["location_count"] == 2 and again["earliest_contract_end"] == "2027-02-28"
    r = client.patch(url + "/" + b["location"]["id"], headers=w["h"], json={"quote_status": "requested"}).json()
    assert r["quotes"]["requested"] == 1 and r["quotes"]["not_requested"] == 1
    assert client.patch(url + "/" + b["location"]["id"], headers=w["h"],
                        json={"quote_status": "won"}).status_code == 422
    assert client.post(url, headers=w["h"], json={"utility": "Oncor"}).status_code == 422
    assert w["db"].query(Lead).filter_by(organization_id=w["org"].id).count() == 1     # one customer
    q = client.get("/energy-ops/leads/%s/qualification" % lead.id, headers=w["h"]).json()
    locs = next(f for f in q["fields"] if f["key"] == "locations")
    assert locs["value"] == "2 recorded"
    assert next(f for f in q["fields"] if f["key"] == "contract_end_date")["value"] == "2027-02-28"


def test_lost_needs_a_reason_and_keeps_history(client, w):
    db, lead = w["db"], _biz(client, w)
    _stage(client, w, lead, "quote_requested")
    assert _stage(client, w, lead, "lost").status_code == 422
    r = _stage(client, w, lead, "lost", reason="Renewed with incumbent before quotes came back").json()
    assert r["stage"] == "lost" and r["lost_reason"].startswith("Renewed") and _open(w, lead) == []
    db.refresh(lead)
    assert lead.pipeline_lost_reason.startswith("Renewed") and lead.enrolled_at is None
    assert [h["to"] for h in r["history"]] == ["quote_requested", "lost"]


def test_deferred_needs_a_revisit_date_and_opens_one_task(client, w):
    lead = _biz(client, w)
    assert _stage(client, w, lead, "deferred").status_code == 422
    assert _stage(client, w, lead, "deferred", revisit_on="31/01/2027").status_code == 422
    r = _stage(client, w, lead, "deferred", revisit_on="2027-01-31", note="Contract runs to 2027-06").json()
    assert r["revisit_on"] == "2027-01-31"
    assert _open(w, lead) == ["Revisit deferred commercial opportunity"]
    _stage(client, w, lead, "discovery")                                # picked back up
    assert _open(w, lead) == []


def test_only_commercial_records_and_only_this_workspace(client, w):
    db = w["db"]
    assert client.post("/site/%s/inquiry" % w["slug"], json={
        "first_name": "Home", "last_name": "Owner", "email": "home@gmail.com",
        "request_type": "Compare electricity options", "form_started_at": 1}).status_code == 201
    home = db.query(Lead).filter_by(organization_id=w["org"].id).one()
    assert client.get("/energy-ops/leads/%s/commercial" % home.id, headers=w["h"]).status_code == 409
    biz = _biz(client, w)
    fh = {"Authorization": "Bearer %s" % create_access_token(w["fadmin"], db)}
    assert client.get("/energy-ops/leads/%s/commercial" % biz.id, headers=fh).status_code == 404
    assert client.post("/energy-ops/leads/%s/commercial/stage" % biz.id, headers=fh,
                       json={"stage": "lost", "reason": "x"}).status_code == 404
    assert _stage(client, w, biz, "teleported").status_code == 422


def test_a_repeated_site_fills_in_the_missing_meter(client, w):
    lead = _biz(client, w)
    url = "/energy-ops/leads/%s/commercial/locations" % lead.id
    a = client.post(url, headers=w["h"], json={"address": "300 Oak St, Dallas"}).json()
    b = client.post(url, headers=w["h"], json={"address": "300 oak st dallas", "meter_id": "1044372000999",
                                                "utility": "Oncor"}).json()
    assert b["duplicate"] is True and sorted(b["filled"]) == ["meter_id", "utility"]
    assert b["location"]["id"] == a["location"]["id"] and b["location"]["meter_id"] == "1044372000999"
    v = client.get("/energy-ops/leads/%s/commercial" % lead.id, headers=w["h"]).json()
    assert v["location_count"] == 1
    assert v["locations"][0]["meter_id"] == "1044372000999"
