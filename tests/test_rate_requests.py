# -*- coding: utf-8 -*-
"""Rate Requests work queue (WS4).

    GET   /rate-requests/            filters, search, sort, pagination
    GET   /rate-requests/summary     per-status counts (+ unassigned, booked)
    GET   /rate-requests/{id}        detail + timeline
    POST  /rate-requests/            explicit create -> Lead(tier new_inquiry)
    PATCH /rate-requests/{id}        status / assignment / fields

Every read and write is on the ACTING workspace; a foreign id is 404. Creating
a request records no consent, enrols nothing and sends nothing.
"""
import itertools
import json
import uuid
from datetime import datetime, timedelta

import pytest

from app.models.models import (AuditLogEntry, BookingLink, CadenceState, Lead, Message,
                               Organization, User)
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


@pytest.fixture(autouse=True)
def _mounted():
    from app.main import app
    from app.routers.rate_requests_router import router
    if not any(getattr(r, "path", "") == "/rate-requests/summary" for r in app.routes):
        app.include_router(router)
    yield


def _org(db, name, industry="energy"):
    o = Organization(name=name, slug="rr-%s" % uuid.uuid4().hex[:8], plan="enterprise",
                     industry=industry, is_active=True)
    db.add(o)
    db.commit()
    return o


def _user(db, org, role, label="u"):
    u = User(organization_id=org.id, email="%s-%d@rr.test" % (label, next(_SEQ)),
             password_hash=hash_password("Pass12345!"), full_name=label.title(),
             role=role, is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _h(db, user):
    return {"Authorization": "Bearer %s" % create_access_token(user, db)}


def _lead(db, org, owner, tier, first="Ann", last="Archer", **kw):
    cf = kw.pop("cf", None)
    l = Lead(organization_id=org.id, assigned_to_id=owner.id if owner else None,
             first_name=first, last_name=last, phone=kw.pop("phone", None), tier=tier,
             status=kw.pop("status", "new"),
             custom_fields=json.dumps(cf) if cf else None, **kw)
    db.add(l)
    db.commit()
    return l


@pytest.fixture()
def world(db_session):
    db = db_session
    org = _org(db, "Energy Co")
    other = _org(db, "Other Energy")
    admin = _user(db, org, "org_admin", "admin")
    adv = _user(db, org, "advisor", "adv")
    adv2 = _user(db, org, "advisor", "adv2")
    foreign_admin = _user(db, other, "org_admin", "fadmin")
    return dict(db=db, org=org, other=other, admin=admin, adv=adv, adv2=adv2,
                foreign_admin=foreign_admin)


# ── create ──────────────────────────────────────────────────────────────────

def test_create_makes_a_new_inquiry_lead_with_no_consent_cadence_or_send(client, world):
    db, adv = world["db"], world["adv"]
    r = client.post("/rate-requests/", headers=_h(db, adv), json={
        "first_name": "Maria", "last_name": "Sanchez", "phone": "2145550101",
        "service_address": "456 Oak Ave, Austin, TX", "current_supplier": "Reliant",
        "annual_usage_kwh": 14200, "contract_end_date": "2027-03-31",
        "segment": "Residential", "source": "Phone"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "new" and body["tier"] == "new_inquiry"
    assert body["service_address"] == "456 Oak Ave, Austin, TX"
    assert body["current_supplier"] == "Reliant" and body["annual_usage_kwh"] == 14200
    assert body["assigned_to_id"] == adv.id and body["source"] == "phone"

    lead = db.query(Lead).filter(Lead.id == body["id"]).one()
    assert lead.organization_id == world["org"].id
    assert lead.sms_consent in (False, None) and lead.sms_consent_timestamp is None
    assert lead.allow_sms is None
    assert json.loads(lead.custom_fields)["contract_end_date"] == "2027-03-31"
    assert db.query(CadenceState).filter(CadenceState.lead_id == lead.id).count() == 0
    assert db.query(Message).filter(Message.lead_id == lead.id).count() == 0
    audit = db.query(AuditLogEntry).filter(AuditLogEntry.action == "rate_request.created",
                                           AuditLogEntry.target_id == lead.id).one()
    assert audit.organization_id == world["org"].id and audit.actor_user_id == adv.id


def test_create_validates_input(client, world):
    db, adv = world["db"], world["adv"]
    h = _h(db, adv)
    assert client.post("/rate-requests/", headers=h, json={"first_name": "X"}).status_code == 422
    assert client.post("/rate-requests/", headers=h, json={
        "first_name": "X", "phone": "2145550102", "contract_end_date": "03/31/2027"}).status_code == 422


def test_advisor_cannot_create_for_someone_else_manager_can(client, world):
    db = world["db"]
    r = client.post("/rate-requests/", headers=_h(db, world["adv"]), json={
        "first_name": "A", "phone": "2145550103", "assign_to": world["adv2"].id})
    assert r.status_code == 403
    r = client.post("/rate-requests/", headers=_h(db, world["admin"]), json={
        "first_name": "A", "phone": "2145550103", "assign_to": "unassigned"})
    assert r.status_code == 201 and r.json()["assigned_to_id"] is None
    r = client.post("/rate-requests/", headers=_h(db, world["admin"]), json={
        "first_name": "A", "phone": "2145550104", "assign_to": world["foreign_admin"].id})
    assert r.status_code == 404


def test_create_refused_where_the_pipeline_has_no_new_inquiry_stage(client, db_session):
    org = _org(db_session, "Funeral Home", industry="funeral")
    admin = _user(db_session, org, "org_admin", "fh")
    r = client.post("/rate-requests/", headers=_h(db_session, admin),
                    json={"first_name": "A", "phone": "2145550105"})
    assert r.status_code == 409
    s = client.get("/rate-requests/summary", headers=_h(db_session, admin)).json()
    assert s["configured"] is False
    assert s["counts"]["new"] is None and s["open"] is None


# ── list + summary ──────────────────────────────────────────────────────────

def _seed(world):
    db, org, adv, adv2 = world["db"], world["org"], world["adv"], world["adv2"]
    a = _lead(db, org, adv, "new_inquiry", "John", "Davis", source="website",
              cf={"service_address": "123 Main St, Dallas, TX", "current_supplier": "TXU"})
    b = _lead(db, org, adv2, "rate_review", "Maria", "Sanchez", source="referral")
    c = _lead(db, org, None, "proposal_sent", "Robert", "King", source="phone")
    d = _lead(db, org, adv, "contract_signed", "James", "Thompson")
    _lead(db, org, adv, "new_inquiry", "Dee", "Enc", status="dnc")          # excluded
    _lead(db, org, adv, "renewal_due", "Not", "Queue")                       # not a request
    _lead(db, world["other"], None, "new_inquiry", "Foreign", "Lead")        # other tenant
    db.add(BookingLink(lead_id=b.id, user_id=adv2.id, status="booked",
                       booked_time=datetime.utcnow() + timedelta(days=2)))
    db.commit()
    return a, b, c, d


def test_summary_counts_are_the_real_counts(client, world):
    _seed(world)
    s = client.get("/rate-requests/summary", headers=_h(world["db"], world["admin"])).json()
    assert s["counts"] == {"new": 1, "in_review": 1, "options_sent": 1, "completed": 1,
                           "booked": 1}
    assert s["open"] == 3 and s["unassigned"] == 1 and s["configured"] is True
    assert {x["key"] for x in s["statuses"]} >= {"new", "in_review", "options_sent", "booked"}


def test_list_filters_search_and_pagination(client, world):
    a, b, c, d = _seed(world)
    h = _h(world["db"], world["admin"])

    def ids(**params):
        r = client.get("/rate-requests/", headers=h, params=params)
        assert r.status_code == 200, r.text
        return r.json()

    assert {x["id"] for x in ids()["items"]} == {a.id, b.id, c.id}
    assert {x["id"] for x in ids(status="all")["items"]} == {a.id, b.id, c.id, d.id}
    assert [x["id"] for x in ids(status="new")["items"]] == [a.id]
    assert [x["id"] for x in ids(status="booked")["items"]] == [b.id]
    assert [x["id"] for x in ids(status="completed")["items"]] == [d.id]
    assert [x["id"] for x in ids(assigned="unassigned")["items"]] == [c.id]
    assert [x["id"] for x in ids(source="referral")["items"]] == [b.id]
    # search reaches the custom-field JSON (supplier / service address)
    row = ids(search="TXU")["items"]
    assert [x["id"] for x in row] == [a.id]
    assert row[0]["service_address"] == "123 Main St, Dallas, TX"
    assert row[0]["current_supplier"] == "TXU" and row[0]["next_step"] == "Review request"
    assert [x["id"] for x in ids(search="sanchez")["items"]] == [b.id]
    booked = ids(status="booked")["items"][0]
    assert booked["booked"] is True and booked["next_step"] == "Hold consultation"
    page = ids(page_size=2, page=2)
    assert page["total"] == 3 and len(page["items"]) == 1
    tomorrow = (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d")
    assert ids(date_from=tomorrow)["total"] == 0
    assert client.get("/rate-requests/", headers=h,
                      params={"date_from": "bad"}).status_code == 422


def test_advisor_sees_only_their_own_requests(client, world):
    a, b, c, d = _seed(world)
    h = _h(world["db"], world["adv"])
    assert {x["id"] for x in client.get("/rate-requests/", headers=h).json()["items"]} == {a.id}
    s = client.get("/rate-requests/summary", headers=h).json()
    assert s["open"] == 1 and s["counts"]["in_review"] == 0 and s["scope"] == "own_leads"
    assert client.get("/rate-requests/%s" % b.id, headers=h).status_code == 404


# ── update ──────────────────────────────────────────────────────────────────

def test_status_change_is_validated_and_audited(client, world):
    a, *_ = _seed(world)
    db = world["db"]
    h = _h(db, world["adv"])
    r = client.patch("/rate-requests/%s" % a.id, headers=h, json={"status": "in_review"})
    assert r.status_code == 200 and r.json()["tier"] == "rate_review"
    assert db.query(AuditLogEntry).filter(AuditLogEntry.action == "rate_request.status_changed",
                                          AuditLogEntry.target_id == a.id).count() == 1
    assert client.patch("/rate-requests/%s" % a.id, headers=h,
                        json={"status": "booked"}).status_code == 400
    assert client.patch("/rate-requests/%s" % a.id, headers=h,
                        json={"status": "nonsense"}).status_code == 400


def test_assignment_is_a_manager_capability(client, world):
    a, *_ = _seed(world)
    db = world["db"]
    r = client.patch("/rate-requests/%s" % a.id, headers=_h(db, world["adv"]),
                     json={"assign_to": world["adv2"].id})
    assert r.status_code == 403
    r = client.patch("/rate-requests/%s" % a.id, headers=_h(db, world["admin"]),
                     json={"assign_to": world["adv2"].id})
    assert r.status_code == 200 and r.json()["assigned_to_id"] == world["adv2"].id
    assert db.query(AuditLogEntry).filter(AuditLogEntry.action == "rate_request.assigned").count() == 1
    r = client.patch("/rate-requests/%s" % a.id, headers=_h(db, world["admin"]),
                     json={"assign_to": world["foreign_admin"].id})
    assert r.status_code == 404


def test_field_update_merges_custom_fields(client, world):
    a, *_ = _seed(world)
    db = world["db"]
    r = client.patch("/rate-requests/%s" % a.id, headers=_h(db, world["adv"]),
                     json={"fields": {"annual_usage_kwh": "9800", "current_supplier": ""}})
    assert r.status_code == 200, r.text
    cf = json.loads(db.query(Lead).get(a.id).custom_fields)
    assert cf["annual_usage_kwh"] == 9800 and "current_supplier" not in cf
    assert cf["service_address"] == "123 Main St, Dallas, TX"
    assert client.patch("/rate-requests/%s" % a.id, headers=_h(db, world["adv"]),
                        json={"fields": {"ssn": "x"}}).status_code == 400


# ── tenant isolation ────────────────────────────────────────────────────────

def test_cross_tenant_is_404_everywhere(client, world):
    a, *_ = _seed(world)
    db = world["db"]
    h = _h(db, world["foreign_admin"])
    assert client.get("/rate-requests/%s" % a.id, headers=h).status_code == 404
    assert client.patch("/rate-requests/%s" % a.id, headers=h,
                        json={"status": "in_review"}).status_code == 404
    items = client.get("/rate-requests/", headers=h).json()["items"]
    assert [x["name"] for x in items] == ["Foreign Lead"]
    assert db.query(Lead).get(a.id).tier == "new_inquiry"


def test_detail_carries_fields_and_timeline(client, world):
    a, *_ = _seed(world)
    db = world["db"]
    h = _h(db, world["admin"])
    client.patch("/rate-requests/%s" % a.id, headers=h, json={"status": "in_review"})
    d = client.get("/rate-requests/%s" % a.id, headers=h).json()
    assert d["custom_fields"]["current_supplier"] == "TXU"
    assert d["timeline"][0]["action"] == "rate_request.status_changed"
    assert any(s["key"] == "options_sent" and s["available"] for s in d["statuses"])


def test_unauthenticated_is_refused(client, world):
    assert client.get("/rate-requests/").status_code in (401, 403)
    assert client.post("/rate-requests/", json={"first_name": "A"}).status_code in (401, 403)
