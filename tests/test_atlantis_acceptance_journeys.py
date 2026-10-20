# -*- coding: utf-8 -*-
"""ATLANTIS ACCEPTANCE SUITE - the 16 journeys of the V4 overnight order, each
run end to end through the real HTTP endpoints with synthetic people
(@example.test / 555 numbers) in an isolated test database.

Every journey ends with _nothing_sent(): no text, no email, no supplier
transaction. Enrollment is only ever the existing Enroll action, which no
journey calls - so "never falsely enrolled" is checked on every path.
"""
import itertools
import json
import uuid
from datetime import date, timedelta

import pytest

import app.models.energy_models  # noqa: F401
from app.models.energy_models import EnergyMoveRequest
from app.models.models import EmailMessage, Lead, Message, Notification, Organization, User
from app.models.work_models import LeadTask
from app.services import customer_sites as cs
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)
PAGE = "<!DOCTYPE html><html><head><title>Atlantis</title></head><body>x</body></html>"


@pytest.fixture(autouse=True)
def _mounted():
    from app.main import app
    from app.routers.energy_ops_router import router as ops
    from app.routers.rate_requests_router import router as rr
    paths = {getattr(r, "path", "") for r in app.routes}
    if "/energy-ops/queues" not in paths:
        app.include_router(ops)
    if "/rate-requests/summary" not in paths:
        app.include_router(rr)
    yield


def _org(db, name):
    o = Organization(name=name, slug="acc-%s" % uuid.uuid4().hex[:8], plan="enterprise",
                     industry="energy", is_active=True)
    db.add(o)
    db.commit()
    return o


def _user(db, org, role, label):
    u = User(organization_id=org.id, email="%s-%d@acc.example.test" % (label, next(_SEQ)),
             password_hash=hash_password("Pass12345!"), full_name=label.title(), role=role,
             is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


@pytest.fixture()
def w(db_session):
    db = db_session
    org, other = _org(db, "Atlantis Acceptance"), _org(db, "Other Tenant")
    w = dict(db=db, org=org, other=other, admin=_user(db, org, "org_admin", "admin"),
             adv=_user(db, org, "advisor", "adv"), rep=_user(db, org, "advisor", "rep"),
             fadmin=_user(db, other, "org_admin", "fadmin"))
    w["slug"] = "acc-site-%d" % next(_SEQ)
    cs.publish(db, organization=org, slug=w["slug"], html=PAGE, title=org.name)
    return w


def _h(w, u):
    return {"Authorization": "Bearer %s" % create_access_token(u, w["db"])}


def _form(client, w, first, request_type, **kw):
    p = {"first_name": first, "last_name": "Synthetic", "email": "%s.%d@example.test" % (first.lower(), next(_SEQ)),
         "phone": "(214) 555-%04d" % (2000 + next(_SEQ)), "consent": True, "request_type": request_type,
         "form_started_at": 1}
    p.update(kw)
    r = client.post("/site/%s/inquiry" % w["slug"], json=p)
    assert r.status_code == 201, r.text
    return p


def _lead(w, email):
    return w["db"].query(Lead).filter_by(organization_id=w["org"].id, email=email.lower()).one()


def _cf(lead):
    return json.loads(lead.custom_fields or "{}")


def _own(w, lead, user):
    lead.assigned_to_id = user.id
    w["db"].commit()


def _nothing_sent(db):
    from app.models.provider_transaction_models import ProviderTransaction
    assert db.query(Message).count() == 0
    assert db.query(EmailMessage).count() == 0
    assert db.query(ProviderTransaction).count() == 0
    assert db.query(Lead).filter(Lead.enrolled_at.isnot(None)).count() == 0


def _open_titles(w, lead, source=None):
    q = w["db"].query(LeadTask).filter_by(lead_id=lead.id, status="open")
    if source:
        q = q.filter_by(source=source)
    return sorted(t.title for t in q)


def _rr_new(client, w, user):
    r = client.get("/rate-requests/", headers=_h(w, user), params={"status": "new"})
    assert r.status_code == 200, r.text
    b = r.json()
    return {i["id"] for i in b.get("items", b.get("results", []))}


# J1 website -> CRM -> New -> alert -> qualification -> hand-off -> confirmation task
def test_j01_residential_rate_review(client, w):
    db = w["db"]
    p = _form(client, w, "Rhea", "Compare electricity options", service_area="75201", monthly_usage="1,100 kWh")
    lead = _lead(w, p["email"])
    assert lead.tier == "new_inquiry" and _cf(lead)["segment"] == "Residential"
    assert lead.id in _rr_new(client, w, w["admin"])
    assert {n.user_id for n in db.query(Notification).filter_by(lead_id=lead.id)} == {w["admin"].id}
    _own(w, lead, w["adv"])
    q = client.get("/energy-ops/leads/%s/qualification" % lead.id, headers=_h(w, w["adv"])).json()
    state = {f["key"]: f["state"] for f in q["fields"]}
    assert q["segment"] == "residential" and state["service_location"] == "have" and state["usage"] == "have"
    assert q["ready"] is True and q["next_step"]["code"] == "choose_supplier"
    assert state["current_supplier"] != "have"
    q = client.post("/energy-ops/leads/%s/qualification" % lead.id, headers=_h(w, w["adv"]),
                    json={"fields": {"current_supplier": "Synthetic Power Co"}}).json()
    assert {f["key"]: f["state"] for f in q["fields"]}["current_supplier"] == "have"
    assert "comparepower" in {s["key"] for s in q["suppliers"]}
    r = client.post("/energy-ops/leads/%s/handoff" % lead.id, headers=_h(w, w["adv"]),
                    json={"supplier": "comparepower", "note": "Sent the comparison link"}).json()
    assert r["duplicate"] is False
    assert any(t.startswith("Confirm outcome with") for t in _open_titles(w, lead, "supplier_handoff"))
    _nothing_sent(db)


# J2 bill review: information tracked -> advisor task -> follow-up
def test_j02_bill_review(client, w):
    p = _form(client, w, "Bill", "Analyze my current bill", service_area="75204")
    lead = _lead(w, p["email"])
    assert _cf(lead)["request_kind"] == "bill_review"
    _own(w, lead, w["adv"])
    h = _h(w, w["adv"])
    assert client.post("/energy-ops/leads/%s/qualification/request-info" % lead.id, headers=h).status_code == 200
    client.post("/energy-ops/leads/%s/qualification/request-info" % lead.id, headers=h)     # repeat
    assert _open_titles(w, lead, "qualification") == ["Get missing details"]
    q = client.post("/energy-ops/leads/%s/qualification" % lead.id, headers=h, json={
        "bill": {"total_amount": "142.10", "usage_kwh": "1180", "period_end": "2026-09-30", "values_are": "actual"}}).json()
    assert q["bill"]["usage_kwh"] == "1180" and q["bill"]["values_are"] == "actual"
    assert client.post("/energy-ops/leads/%s/qualification" % lead.id, headers=h,
                       json={"bill": {"total_amount": "1", "values_are": "guessed"}}).status_code == 422
    assert "website" in {t.source for t in w["db"].query(LeadTask).filter_by(lead_id=lead.id, status="open")}
    _nothing_sent(w["db"])


def _biz(client, w, first="Pat", **kw):
    p = _form(client, w, first, "Commercial energy procurement", company="Synthetic Diner LLC",
              locations="2 sites", service_area="Dallas", **kw)
    lead = _lead(w, p["email"])
    _own(w, lead, w["adv"])
    return lead


def _stage(client, w, lead, stage, **kw):
    return client.post("/energy-ops/leads/%s/commercial/stage" % lead.id, headers=_h(w, w["adv"]),
                       json=dict({"stage": stage}, **kw))


# J3 business request -> Commercial -> queue -> discovery -> quote tracking
def test_j03_commercial_inquiry(client, w):
    lead = _biz(client, w)
    assert _cf(lead)["segment"] == "Commercial" and lead.tier == "new_inquiry"
    assert lead.id in _rr_new(client, w, w["admin"])
    v = client.get("/energy-ops/leads/%s/commercial" % lead.id, headers=_h(w, w["adv"])).json()
    assert v["stage"] == "discovery" and v["company"] == "Synthetic Diner LLC"
    assert _stage(client, w, lead, "quote_requested").json()["stage"] == "quote_requested"
    assert _open_titles(w, lead, "commercial") == ["Follow up on supplier quote"]
    _nothing_sent(w["db"])


# J4 one business, several service locations, one account
def test_j04_multi_location_account(client, w):
    lead = _biz(client, w)
    url = "/energy-ops/leads/%s/commercial/locations" % lead.id
    h = _h(w, w["adv"])
    a = client.post(url, headers=h, json={"address": "100 Main St, Dallas", "contract_end_date": "2027-05-31"}).json()
    client.post(url, headers=h, json={"address": "200 Elm St, Dallas", "contract_end_date": "2027-02-28"})
    dup = client.post(url, headers=h, json={"address": "100 main st dallas", "meter_id": "1044372000111"}).json()
    assert dup["duplicate"] is True and dup["location"]["id"] == a["location"]["id"]
    assert dup["location_count"] == 2 and dup["earliest_contract_end"] == "2027-02-28"
    client.patch(url + "/" + a["location"]["id"], headers=h, json={"quote_status": "requested"})
    v = client.get("/energy-ops/leads/%s/commercial" % lead.id, headers=h).json()
    assert v["quotes"]["requested"] == 1 and v["locations"][0]["meter_id"] == "1044372000111"
    assert w["db"].query(Lead).filter_by(organization_id=w["org"].id).count() == 1
    _nothing_sent(w["db"])


# J5 moving form -> move record -> checklist -> assigned task -> follow-up -> complete
def test_j05_move_concierge(client, w):
    p = _form(client, w, "Mo", "I'm moving", service_area="76102", move_date="2026-11-14",
              services_requested="Electricity, Internet")
    db = w["db"]
    m = db.query(EnergyMoveRequest).filter_by(organization_id=w["org"].id).one()
    h = _h(w, w["admin"])
    assert client.patch("/energy-ops/moves/%s" % m.id, headers=h, json={"assign_to": w["adv"].id}).status_code == 200
    t = client.post("/energy-ops/moves/%s/tasks" % m.id, headers=h,
                    json={"title": "Confirm internet install window", "due_at": "2026-11-10T15:00:00"}).json()
    assert t["id"]
    row = client.get("/energy-ops/moves/%s" % m.id, headers=h).json()
    for i in row["checklist"]:
        assert client.post("/energy-ops/moves/%s/checklist" % m.id, headers=h,
                           json={"key": i["key"], "done": True}).status_code == 200
    r = client.patch("/energy-ops/moves/%s" % m.id, headers=h, json={"status": "completed"}).json()
    assert r["status"] == "completed" and r["pending_items"] == []
    assert _lead(w, p["email"]).id == m.lead_id
    _nothing_sent(db)


# J6 the same customer again -> one person, one open first-contact task
def test_j06_returning_visitor(client, w):
    p = _form(client, w, "Ret", "Compare electricity options", service_area="75201")
    _form(client, w, "Ret", "Compare electricity options", email=p["email"], phone=p["phone"],
          service_area="75201", monthly_usage="900 kWh")
    db = w["db"]
    assert db.query(Lead).filter_by(organization_id=w["org"].id).count() == 1
    lead = _lead(w, p["email"])
    assert _open_titles(w, lead, "website") == ["Contact website request"]
    _nothing_sent(db)


# J7 a business cannot be sent down a residential-only supplier path
def test_j07_supplier_eligibility(client, w):
    lead = _biz(client, w)
    r = client.post("/energy-ops/leads/%s/handoff" % lead.id, headers=_h(w, w["adv"]), json={"supplier": "chariot"})
    assert r.status_code == 409 and "residential" in r.text.lower()
    w["db"].refresh(lead)
    assert not _cf(lead).get("supplier_handoffs")
    q = client.get("/energy-ops/leads/%s/qualification" % lead.id, headers=_h(w, w["adv"])).json()
    chariot = next(s for s in q["suppliers"] if s["key"] == "chariot")
    assert chariot["eligible"] is False
    _nothing_sent(w["db"])


# J8 partner inquiry is a partner, not an electricity customer
def test_j08_partner_referral(client, w):
    p = _form(client, w, "Part", "Partner / referral program", company="Synthetic Realty")
    lead = _lead(w, p["email"])
    assert _cf(lead)["request_kind"] == "partner"
    q = client.get("/energy-ops/leads/%s/qualification" % lead.id, headers=_h(w, w["admin"])).json()
    assert q["segment"] == "partner"
    assert client.get("/energy-ops/leads/%s/commercial" % lead.id, headers=_h(w, w["admin"])).status_code == 409
    _nothing_sent(w["db"])


# J9 no default advisor -> admin alerted, task open, lead in New (not lost)
def test_j09_missing_default_advisor(client, w):
    site = w["db"].query(cs.CustomerSite).filter_by(organization_id=w["org"].id).one()
    assert site.default_owner_user_id is None
    # a non-reserved address, so this is the real (non-test) path
    p = _form(client, w, "Nodef", "Compare electricity options", email="nodef.%d@gmail.com" % next(_SEQ))
    lead = _lead(w, p["email"])
    assert lead.is_test is False and lead.assigned_to_id is None and lead.id in _rr_new(client, w, w["admin"])
    assert {n.user_id for n in w["db"].query(Notification).filter_by(lead_id=lead.id)} == {w["admin"].id}
    assert _open_titles(w, lead, "website") == ["Contact website request"]
    ih = client.get("/energy-ops/intake-health", headers=_h(w, w["admin"])).json()
    assert lead.id in {x["lead_id"] for x in ih["unassigned"]["oldest"]}
    _nothing_sent(w["db"])


# J10 another workspace: every Atlantis record is invisible and unchangeable
def test_j10_another_tenant(client, w):
    lead = _biz(client, w)
    client.post("/energy-ops/leads/%s/handoff" % lead.id, headers=_h(w, w["adv"]), json={"supplier": "gexa"})
    _form(client, w, "Mover", "I'm moving", move_date="2026-12-01", services_requested="Electricity")
    m = w["db"].query(EnergyMoveRequest).filter_by(organization_id=w["org"].id).one()
    f = _h(w, w["fadmin"])
    for url in ("/energy-ops/leads/%s/qualification", "/energy-ops/leads/%s/commercial",
                "/energy-ops/leads/%s/handoffs"):
        r = client.get(url % lead.id, headers=f)
        assert r.status_code == 404 and "Synthetic" not in r.text
    for url, body in (("/energy-ops/leads/%s/handoff", {"supplier": "gexa"}),
                      ("/energy-ops/leads/%s/commercial/stage", {"stage": "lost", "reason": "x"}),
                      ("/energy-ops/leads/%s/qualification", {"fields": {"company": "Hijack"}})):
        assert client.post(url % lead.id, headers=f, json=body).status_code == 404
    assert client.get("/energy-ops/moves/%s" % m.id, headers=f).status_code == 404
    assert client.get("/energy-ops/supplier-handoffs", headers=f).json()["total"] == 0
    ih = client.get("/energy-ops/intake-health", headers=f).json()
    assert ih["unassigned"]["count"] == 0 and all(c["requests"] == 0 for c in ih["first_contact"])
    assert client.get("/energy-ops/field/summary", headers=f).json()["reps"] == []
    w["db"].refresh(lead)
    assert _cf(lead).get("company") != "Hijack" and len(_cf(lead)["supplier_handoffs"]) == 1
    _nothing_sent(w["db"])


# J11 repeated click / retry -> no duplicate acquisition work
def test_j11_duplicate_submission(client, w):
    db = w["db"]
    p = _form(client, w, "Dup", "Compare electricity options")
    _form(client, w, "Dup", "Compare electricity options", email=p["email"], phone=p["phone"])
    lead = _lead(w, p["email"])
    assert db.query(Lead).filter_by(organization_id=w["org"].id).count() == 1
    _own(w, lead, w["adv"])
    h = _h(w, w["adv"])
    a = client.post("/energy-ops/leads/%s/handoff" % lead.id, headers=h, json={"supplier": "comparepower"}).json()
    b = client.post("/energy-ops/leads/%s/handoff" % lead.id, headers=h, json={"supplier": "comparepower"}).json()
    assert a["duplicate"] is False and b["duplicate"] is True and a["handoff"]["id"] == b["handoff"]["id"]
    assert len(_open_titles(w, lead, "supplier_handoff")) == 1
    v = {"outcome": "door_hanger_left", "address": "9 Retry Rd", "zip_code": "76102"}
    assert client.post("/energy-ops/field/visits", headers=_h(w, w["rep"]), json=v).json()["duplicate"] is False
    assert client.post("/energy-ops/field/visits", headers=_h(w, w["rep"]), json=v).json()["duplicate"] is True
    _nothing_sent(db)


# J12 hand-off that did not go ahead -> recorded, task closed, never enrolled
def test_j12_supplier_handoff_failure(client, w):
    p = _form(client, w, "Fail", "Compare electricity options")
    lead = _lead(w, p["email"])
    _own(w, lead, w["adv"])
    h = _h(w, w["adv"])
    hid = client.post("/energy-ops/leads/%s/handoff" % lead.id, headers=h,
                      json={"supplier": "comparepower"}).json()["handoff"]["id"]
    url = "/energy-ops/leads/%s/handoffs/%s/outcome" % (lead.id, hid)
    assert client.post(url, headers=h, json={"outcome": "enrolled", "source": "staff_report", "note": "x"}).status_code == 422
    r = client.post(url, headers=h, json={"outcome": "not_completed", "source": "staff_report",
                                          "note": "Customer stopped at the credit check"}).json()
    assert r["enrolled"] is False and r["handoff"]["status"] == "not_completed"
    assert _open_titles(w, lead, "supplier_handoff") == []
    assert client.post(url, headers=h, json={"outcome": "confirmed", "source": "staff_report", "note": "y"}).status_code == 422
    assert client.get("/energy-ops/supplier-handoffs", headers=h).json()["total"] == 0
    w["db"].refresh(lead)
    assert lead.tier != "contract_signed"
    _nothing_sent(w["db"])


# J13 no consent: recorded accurately, contact restricted, nothing sent
def test_j13_no_consent(client, w):
    p = _form(client, w, "Nocon", "Compare electricity options", consent=False)
    lead = _lead(w, p["email"])
    assert lead.sms_consent is False and lead.tier == "new_inquiry"
    assert _open_titles(w, lead, "website") == ["Contact website request"]
    _nothing_sent(w["db"])


# J14 field rep -> qualified resident -> Atlantis advisor queue
def test_j14_field_referral(client, w):
    r = client.post("/energy-ops/field/visits", headers=_h(w, w["rep"]), json={
        "outcome": "interested", "first_name": "Door", "last_name": "Synthetic", "phone": "(817) 555-0401",
        "permission_to_contact": True, "address": "14 Field Ln", "zip_code": "76104"}).json()
    assert r["result"] == "new_referral"
    lead = w["db"].query(Lead).get(r["lead_id"])
    assert lead.organization_id == w["org"].id and lead.tier == "new_inquiry" and lead.sms_consent is False
    assert _cf(lead)["field_rep_id"] == w["rep"].id
    assert lead.id in _rr_new(client, w, w["admin"])
    assert _open_titles(w, lead, "field_referral") == ["Contact field referral"]
    s = client.get("/energy-ops/field/summary", headers=_h(w, w["admin"])).json()
    assert {x["rep_id"]: x for x in s["reps"]}[w["rep"].id]["new_referrals"] == 1
    _nothing_sent(w["db"])


# J15 discovery -> quote pending -> quote received -> manually confirmed contract
def test_j15_commercial_conversion(client, w):
    lead = _biz(client, w)
    for s in ("information_requested", "usage_received", "quote_requested", "quote_received",
              "proposal_presented", "decision_pending", "contract_submitted"):
        assert _stage(client, w, lead, s).status_code == 200, s
    assert _stage(client, w, lead, "won").status_code == 422          # not before the supplier confirms
    _stage(client, w, lead, "supplier_confirmed", note="Supplier rep emailed the countersigned contract")
    r = _stage(client, w, lead, "won").json()
    assert r["stage"] == "won" and r["enrolled"] is False
    w["db"].refresh(lead)
    assert lead.tier == "proposal_sent" and lead.enrolled_at is None
    assert [h["to"] for h in r["history"]][-2:] == ["supplier_confirmed", "won"]
    _nothing_sent(w["db"])


# J16 deferred and lost: honest disposition, history kept, nothing enrolled
def test_j16_deferred_or_lost(client, w):
    a = _biz(client, w, first="Later")
    assert _stage(client, w, a, "deferred").status_code == 422
    r = _stage(client, w, a, "deferred", revisit_on=(date.today() + timedelta(days=90)).isoformat()).json()
    assert r["stage"] == "deferred"
    assert _open_titles(w, a, "commercial") == ["Revisit deferred commercial opportunity"]
    b = _biz(client, w, first="Gone")
    _stage(client, w, b, "quote_requested")
    assert _stage(client, w, b, "lost").status_code == 422
    r = _stage(client, w, b, "lost", reason="Renewed with incumbent").json()
    assert r["stage"] == "lost" and [h["to"] for h in r["history"]] == ["quote_requested", "lost"]
    assert _open_titles(w, b, "commercial") == []
    w["db"].refresh(b)
    assert b.pipeline_lost_reason == "Renewed with incumbent"
    _nothing_sent(w["db"])
