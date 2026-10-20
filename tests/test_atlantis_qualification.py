# -*- coding: utf-8 -*-
"""Advisor qualification: from a real website request (synthetic people), the
advisor sees what is known, what is missing, the supported next step and the
eligible suppliers - with no prices and nothing sent."""
import itertools
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

    def org(name):
        o = Organization(name=name, slug="q-%s" % uuid.uuid4().hex[:8], plan="enterprise",
                         industry="energy", is_active=True)
        db.add(o)
        db.commit()
        return o

    def user(o, role):
        u = User(organization_id=o.id, email="%s-%d@q.example.test" % (role, next(_SEQ)),
                 password_hash=hash_password("x"), full_name=role, role=role,
                 is_active=True, must_change_password=False)
        db.add(u)
        db.commit()
        return u

    a, b = org("Atl Q"), org("Other Q")
    out = dict(db=db, org=a, admin=user(a, "org_admin"), fadmin=user(b, "org_admin"))
    out["slug"] = "q-site-%d" % next(_SEQ)
    cs.publish(db, organization=a, slug=out["slug"], html=PAGE, title="Atl Q")
    return out


def _h(w, u=None):
    return {"Authorization": "Bearer %s" % create_access_token(u or w["admin"], w["db"])}


def _site(client, w, **kw):
    p = {"first_name": "Q", "last_name": "Person%d" % next(_SEQ),
         "email": "q%d@gmail.com" % next(_SEQ), "form_started_at": 1}
    p.update(kw)
    assert client.post("/site/%s/inquiry" % w["slug"], json=p).status_code == 201
    return w["db"].query(Lead).filter_by(organization_id=w["org"].id).order_by(Lead.created_at.desc()).first()


def _q(client, w, lead):
    r = client.get("/energy-ops/leads/%s/qualification" % lead.id, headers=_h(w))
    assert r.status_code == 200, r.text
    return r.json()


def _set(client, w, lead, fields=None, bill=None):
    return client.post("/energy-ops/leads/%s/qualification" % lead.id, headers=_h(w),
                       json={"fields": fields or {}, "bill": bill})


def _own(w, lead):
    lead.assigned_to_id = w["admin"].id
    w["db"].commit()


def _sup(q):
    return {s["key"]: s for s in q["suppliers"]}


def test_rate_review_ready_once_owned(client, w):
    lead = _site(client, w, request_type="Compare electricity options", service_area="75201",
                 monthly_usage="1,100 kWh")
    q = _q(client, w, lead)
    assert q["segment"] == "residential" and q["next_step"]["code"] == "assign_owner"
    _own(w, lead)
    q = _q(client, w, lead)
    assert q["ready"] and q["next_step"]["code"] == "choose_supplier"
    s = _sup(q)
    assert s["chariot"]["eligible"] and not s["chariot"]["online_enrollment"]
    assert s["gexa"]["eligible"] and not s["move_concierge"]["eligible"]
    assert s["comparepower"]["eligible"] and "tracking link" in s["comparepower"]["note"]
    priced = {"price", "rate", "savings", "saving", "commission", "estimated_savings"}
    assert not priced & set(q) and not any(priced & set(s) for s in q["suppliers"])


def test_bill_review_needs_usage_and_records_bill_lines_by_hand(client, w):
    lead = _site(client, w, request_type="Analyze my current bill", service_area="75204")
    _own(w, lead)
    q = _q(client, w, lead)
    assert "usage" in q["missing"] and q["next_step"]["code"] == "request_info"
    r = _set(client, w, lead, fields={"current_supplier": "Some REP"},
             bill={"total_amount": "142.10", "usage_kwh": "1180", "values_are": "actual",
                   "period_start": "2026-09-01", "period_end": "2026-09-30"})
    assert r.status_code == 200
    q = r.json()
    assert q["ready"] and q["bill"]["usage_kwh"] == "1180" and "no rate" in q["bill_note"]
    assert _set(client, w, lead, bill={"values_are": "guessed"}).status_code == 422


def test_move_needs_a_move_date_then_points_to_move_concierge(client, w):
    lead = _site(client, w, request_type="I'm moving", service_area="76102", monthly_usage="900")
    _own(w, lead)
    assert "move_date" in _q(client, w, lead)["missing"]
    q = _set(client, w, lead, fields={"move_date": "2026-11-20"}).json()
    assert q["next_step"]["code"] == "move_concierge" and _sup(q)["move_concierge"]["eligible"]


def test_cannot_quote_yet_gives_one_task_that_is_updated_not_duplicated(client, w):
    lead = _site(client, w, request_type="Compare electricity options")
    _own(w, lead)
    url = "/energy-ops/leads/%s/qualification/request-info" % lead.id
    a = client.post(url, headers=_h(w)).json()
    assert a["created"] is True and any("Usage" in m for m in a["missing"])
    _set(client, w, lead, fields={"service_area": "75201"})
    b = client.post(url, headers=_h(w)).json()
    assert b["created"] is False and b["task_id"] == a["task_id"]
    assert w["db"].query(LeadTask).filter_by(lead_id=lead.id, source="qualification", status="open").count() == 1
    _set(client, w, lead, fields={"monthly_usage": "1000"})
    assert client.post(url, headers=_h(w)).status_code == 409        # nothing missing now


def test_general_question_needs_classifying_by_a_person(client, w):
    lead = _site(client, w, request_type="Something else entirely")
    _own(w, lead)
    q = _q(client, w, lead)
    assert q["segment"] == "unknown" and q["next_step"]["code"] == "classify"
    assert not any(s["eligible"] for s in q["suppliers"])


def test_commercial_needs_decision_maker_and_contract_end_then_quotes(client, w):
    lead = _site(client, w, request_type="Commercial energy procurement", company="Synthetic Diner",
                 locations="2 sites", service_area="Dallas")
    _own(w, lead)
    q = _q(client, w, lead)
    assert q["segment"] == "commercial"
    assert {"decision_maker", "usage", "contract_end_date"} <= set(q["missing"])
    assert "company" not in q["missing"]
    q = _set(client, w, lead, fields={"decision_maker": "Owner, Pat Synthetic",
                                     "contract_end_date": "2027-03-31", "annual_usage_kwh": "180000"}).json()
    assert q["ready"] and q["next_step"]["code"] == "request_supplier_quotes"
    s = _sup(q)
    assert not s["chariot"]["eligible"] and s["chariot"]["note"] == "Homes only."
    assert s["gexa"]["eligible"] and s["commercial"]["eligible"]


def test_partner_is_not_treated_as_the_customer(client, w):
    lead = _site(client, w, request_type="Partner / referral program", company="Synthetic Realty",
                 message="Referring a buyer closing Nov 20")
    _own(w, lead)
    q = _q(client, w, lead)
    assert q["segment"] == "partner" and q["next_step"]["code"] == "contact_referred_customer"
    assert not any(s["eligible"] for s in q["suppliers"])


def test_input_and_scope_are_checked(client, w):
    lead = _site(client, w, request_type="Compare electricity options")
    assert _set(client, w, lead, fields={"commission": "5%"}).status_code == 422
    assert client.get("/energy-ops/leads/%s/qualification" % lead.id,
                      headers=_h(w, w["fadmin"])).status_code == 404
    assert client.post("/energy-ops/leads/%s/qualification" % lead.id, headers=_h(w, w["fadmin"]),
                       json={"fields": {"current_supplier": "x"}}).status_code == 404
    db = w["db"]
    assert db.query(Message).count() == 0 and db.query(EmailMessage).count() == 0
