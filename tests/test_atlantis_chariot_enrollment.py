# -*- coding: utf-8 -*-
"""ATLANTIS: "Enroll online with Chariot" beside "Advisor enrolls manually".

NOT ONE BYTE LEAVES THIS PROCESS: the Chariot transport is a recording fake.
Synthetic people only. Proves: switched off = nothing sent and nothing
recorded; homes only; one submission per person/meter, recorded BEFORE the
call; a timeout is "outcome unknown - do not resubmit"; identity values are
never stored anywhere; a person confirms every outcome and "enrolled" runs
the same Enroll action as a manual enrollment; scope and isolation.
"""
import itertools
import json
import uuid

import pytest

from app.models.models import AuditLogEntry, EmailMessage, Lead, Message, Organization, User
from app.models.provider_transaction_models import ProviderTransaction
from app.services import customer_sites as cs
from app.services.auth_service import create_access_token, hash_password
from app.services.providers import chariot

_SEQ = itertools.count(1)
PAGE = "<!DOCTYPE html><html><head><title>A</title></head><body>x</body></html>"
SSN, DOB, DL = "666730809", "12/12/1990", "X1234567"


class _Resp:
    def __init__(self, body, status_code=200):
        self._body, self.status_code, self.text = body, status_code, json.dumps(body)

    def json(self):
        return self._body


@pytest.fixture(autouse=True)
def _mounted(monkeypatch):
    from app.main import app
    from app.routers.energy_ops_router import router as ops
    from app.routers.rate_requests_router import router as rr
    paths = {getattr(r, "path", "") for r in app.routes}
    if "/energy-ops/queues" not in paths:
        app.include_router(ops)
    if "/rate-requests/summary" not in paths:
        app.include_router(rr)
    monkeypatch.setenv(chariot.ENV_API_KEY, "test-key-not-a-real-credential")
    monkeypatch.setenv(chariot.ENV_ENVIRONMENT, "test")
    monkeypatch.delenv(chariot.ENV_ALLOW_TRANSACTIONS, raising=False)
    yield


@pytest.fixture()
def wire():
    calls, queue = [], []

    def fake(method, url, *, params=None, files=None, timeout=None):
        calls.append({"method": method, "url": url, "params": params, "files": files, "timeout": timeout})
        nxt = queue.pop(0) if queue else {}
        if isinstance(nxt, Exception):
            raise nxt
        return _Resp(nxt)
    prev = chariot.set_transport(fake)
    fake.calls, fake.queue = calls, queue
    yield fake
    chariot.set_transport(prev)


@pytest.fixture()
def w(db_session):
    db = db_session

    def org(n):
        o = Organization(name=n, slug="ch-%s" % uuid.uuid4().hex[:8], plan="enterprise",
                         industry="energy", is_active=True)
        db.add(o)
        db.commit()
        return o

    def user(o, role, name):
        u = User(organization_id=o.id, email="%s-%d@example.com" % (name, next(_SEQ)),
                 password_hash=hash_password("x"), full_name=name.title(), role=role,
                 is_active=True, must_change_password=False)
        db.add(u)
        db.commit()
        return u
    a, b = org("Atlantis CH"), org("Other CH")
    w = dict(db=db, org=a, mgr=user(a, "org_admin", "morgan"), adv=user(a, "advisor", "ava"),
             other_adv=user(a, "advisor", "ben"), omgr=user(b, "org_admin", "olga"))
    w["slug"] = "ch-site-%d" % next(_SEQ)
    cs.publish(db, organization=a, slug=w["slug"], html=PAGE, title=a.name)
    return w


def _h(w, u):
    return {"Authorization": "Bearer %s" % create_access_token(u, w["db"])}


def _lead(client, w, kind="Compare electricity options", **kw):
    p = {"first_name": "Hana", "last_name": "Synthetic", "email": "hana.%d@gmail.com" % next(_SEQ),
         "phone": "(214) 555-%04d" % (4000 + next(_SEQ)), "consent": True, "request_type": kind,
         "service_area": "75201", "form_started_at": 1}
    p.update(kw)
    assert client.post("/site/%s/inquiry" % w["slug"], json=p).status_code == 201
    lead = w["db"].query(Lead).filter_by(organization_id=w["org"].id, email=p["email"]).one()
    lead.assigned_to_id = w["adv"].id
    w["db"].commit()
    return lead


def _form(**kw):
    f = {"product_id": "23639", "zip": "75201", "esiid": "10032789426879101", "service_type": "switching",
         "service_start_date": "11/20/2026", "dob": DOB, "ssn": SSN, "customer_authorized": True,
         "plan": {"product_id": "23639", "title": "Synthetic Saver 12", "term_months": 12,
                  "efl_url": "https://example.com/efl.pdf"}}
    f.update(kw)
    return f


def _anywhere(db, needle):
    """Every place an identity value could have been written."""
    blobs = []
    for r in db.query(ProviderTransaction).all():
        blobs += [r.request_echo or "", r.response_body or "", r.plan_snapshot or "", r.raw_message or "",
                  r.resolution_note or ""]
    for e in db.query(AuditLogEntry).all():
        blobs.append(e.details if isinstance(e.details, str) else json.dumps(e.details or {}))
    for l in db.query(Lead).all():
        blobs += [l.custom_fields or "", l.notes or ""]
    from app.models.work_models import LeadTask
    for t in db.query(LeadTask).all():
        blobs += [t.title or "", t.details or ""]
    return any(needle in b for b in blobs)


def test_switched_off_nothing_is_sent_and_nothing_recorded(client, w, wire):
    lead = _lead(client, w)
    info = client.get("/energy-ops/leads/%s/chariot" % lead.id, headers=_h(w, w["adv"])).json()
    assert info["eligible"] is True and info["status"]["submissions_enabled"] is False
    r = client.post("/energy-ops/leads/%s/chariot/enroll" % lead.id, headers=_h(w, w["adv"]), json=_form())
    assert r.status_code == 409 and "switched off" in r.json()["detail"]
    assert wire.calls == [] and w["db"].query(ProviderTransaction).count() == 0
    assert "SSN" not in r.text and SSN not in r.text


def test_homes_only_business_and_partner_are_refused(client, w, wire, monkeypatch):
    monkeypatch.setenv(chariot.ENV_ALLOW_TRANSACTIONS, "true")
    biz = _lead(client, w, "Commercial energy procurement", company="Synthetic Diner LLC")
    info = client.get("/energy-ops/leads/%s/chariot" % biz.id, headers=_h(w, w["adv"])).json()
    assert info["eligible"] is False and "homes only" in info["reason"]
    r = client.post("/energy-ops/leads/%s/chariot/enroll" % biz.id, headers=_h(w, w["adv"]), json=_form())
    assert r.status_code == 409 and wire.calls == []
    partner = _lead(client, w, "Partner / referral program", company="Synthetic Realty")
    assert client.get("/energy-ops/leads/%s/chariot" % partner.id,
                      headers=_h(w, w["adv"])).json()["eligible"] is False


def test_online_enrollment_is_recorded_once_and_never_stores_identity(client, w, wire, monkeypatch):
    monkeypatch.setenv(chariot.ENV_ALLOW_TRANSACTIONS, "true")
    db = w["db"]
    lead = _lead(client, w)
    wire.queue.append({"Status": "Enrollment has been completed",
                       "Message": "Passed CreditCheck & No Deposit required", "RefrenceNumber": "R-1001"})
    r = client.post("/energy-ops/leads/%s/chariot/enroll" % lead.id, headers=_h(w, w["adv"]), json=_form())
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "provider_confirmed" and out["provider_reference"] == "R-1001"
    assert "confirm with the customer" in out["meaning"]
    assert len(wire.calls) == 1 and wire.calls[0]["timeout"] == chariot.TRANSACTION_TIMEOUT
    sent = dict(wire.calls[0]["files"]) if isinstance(wire.calls[0]["files"], list) else wire.calls[0]["files"]
    assert any(SSN in str(v) for v in (sent or {}).values())          # it WAS sent to Chariot...
    for secret in (SSN, DOB):                                          # ...and stored NOWHERE
        assert not _anywhere(db, secret), secret
    row = db.query(ProviderTransaction).one()
    assert row.needs_human is True and row.resolved_at is None        # a person still confirms it
    db.refresh(lead)
    assert lead.enrolled_at is None                                    # never enrolled by an API answer
    h = json.loads(lead.custom_fields)["supplier_handoffs"]
    assert h[0]["supplier"] == "chariot" and h[0]["status"] == "referred"
    # one submission per person / meter
    again = client.post("/energy-ops/leads/%s/chariot/enroll" % lead.id, headers=_h(w, w["adv"]), json=_form())
    assert again.status_code == 409 and "already submitted" in again.json()["detail"]
    assert len(wire.calls) == 1
    q = client.get("/energy-ops/enrollments", headers=_h(w, w["mgr"])).json()
    assert q["total"] == 1 and q["items"][0]["name"] == "Hana Synthetic"
    assert db.query(Message).count() == 0 and db.query(EmailMessage).count() == 0


def test_a_timeout_is_outcome_unknown_and_blocks_a_resubmit(client, w, wire, monkeypatch):
    monkeypatch.setenv(chariot.ENV_ALLOW_TRANSACTIONS, "true")
    lead = _lead(client, w)
    wire.queue.append(TimeoutError("read timed out"))
    out = client.post("/energy-ops/leads/%s/chariot/enroll" % lead.id, headers=_h(w, w["adv"]), json=_form()).json()
    assert out["status"] == "pending_provider" and out["reason"] == chariot.REASON_OUTCOME_UNKNOWN
    assert "Do NOT resubmit" in out["meaning"]
    r = client.post("/energy-ops/leads/%s/chariot/enroll" % lead.id, headers=_h(w, w["adv"]), json=_form())
    assert r.status_code == 409 and len(wire.calls) == 1


def test_incomplete_or_unauthorized_requests_never_reach_chariot(client, w, wire, monkeypatch):
    monkeypatch.setenv(chariot.ENV_ALLOW_TRANSACTIONS, "true")
    lead = _lead(client, w)
    h = _h(w, w["adv"])
    assert client.post("/energy-ops/leads/%s/chariot/enroll" % lead.id, headers=h,
                       json=_form(customer_authorized=False)).status_code == 409
    assert client.post("/energy-ops/leads/%s/chariot/enroll" % lead.id, headers=h,
                       json=_form(esiid="")).status_code == 409
    r = client.post("/energy-ops/leads/%s/chariot/enroll" % lead.id, headers=h, json=_form(ssn=""))
    assert r.status_code == 409 and "SSN or DriversLicence" in r.json()["detail"]
    assert wire.calls == [] and w["db"].query(ProviderTransaction).count() == 0
    # a driver licence instead of an SSN is accepted - and not stored either
    wire.queue.append({"Status": "Enrollment has been completed with Pending Status",
                       "Message": "Deposit Waiver has been requested"})
    ok = client.post("/energy-ops/leads/%s/chariot/enroll" % lead.id, headers=h,
                     json=_form(ssn="", drivers_licence=DL))
    assert ok.status_code == 200 and ok.json()["status"] == "pending_provider"
    assert not _anywhere(w["db"], DL)


def test_a_person_confirms_and_enrolled_runs_the_same_enroll_action(client, w, wire, monkeypatch):
    monkeypatch.setenv(chariot.ENV_ALLOW_TRANSACTIONS, "true")
    db = w["db"]
    lead = _lead(client, w)
    wire.queue.append({"Status": "Enrollment has been completed",
                       "Message": "Passed CreditCheck & No Deposit required", "RefrenceNumber": "R-2002"})
    tx = client.post("/energy-ops/leads/%s/chariot/enroll" % lead.id, headers=_h(w, w["adv"]), json=_form()).json()
    url = "/energy-ops/enrollments/%s/reconcile" % tx["id"]
    assert client.post(url, headers=_h(w, w["adv"]), json={"outcome": "enrolled", "note": " "}).status_code == 422
    assert client.post(url, headers=_h(w, w["adv"]), json={"outcome": "maybe", "note": "x"}).status_code == 422
    r = client.post(url, headers=_h(w, w["adv"]), json={"outcome": "enrolled",
                                                        "note": "Customer confirmed the Chariot welcome email"})
    assert r.status_code == 200 and r.json()["enrolled"] is True and r.json()["verified_outcome"] == "enrolled"
    db.expire_all()
    lead = db.query(Lead).get(lead.id)
    assert lead.enrolled_at is not None and lead.tier == "contract_signed"
    assert json.loads(lead.custom_fields)["current_supplier"] == "Chariot Energy"
    assert json.loads(lead.custom_fields)["supplier_handoffs"][0]["status"] == "confirmed"
    assert client.post(url, headers=_h(w, w["adv"]), json={"outcome": "enrolled", "note": "again"}).status_code == 422
    assert client.get("/energy-ops/enrollments", headers=_h(w, w["mgr"])).json()["total"] == 0


def test_not_received_clears_the_way_for_one_fresh_submission(client, w, wire, monkeypatch):
    monkeypatch.setenv(chariot.ENV_ALLOW_TRANSACTIONS, "true")
    lead = _lead(client, w)
    h = _h(w, w["adv"])
    wire.queue.append(TimeoutError("connection reset"))
    first = client.post("/energy-ops/leads/%s/chariot/enroll" % lead.id, headers=h, json=_form()).json()
    client.post("/energy-ops/enrollments/%s/reconcile" % first["id"], headers=h,
                json={"outcome": "not_received", "note": "Chariot support has no record of it"})
    wire.queue.append({"Status": "Enrollment has been completed",
                       "Message": "Passed CreditCheck & No Deposit required", "RefrenceNumber": "R-3003"})
    second = client.post("/energy-ops/leads/%s/chariot/enroll" % lead.id, headers=h, json=_form())
    assert second.status_code == 200 and second.json()["provider_reference"] == "R-3003"
    assert len(wire.calls) == 2


def test_lookups_need_the_key_and_stay_read_only(client, w, wire, monkeypatch):
    lead = _lead(client, w)
    h = _h(w, w["adv"])
    wire.queue.append([{"UtilityId": 1, "UtilityName": "Oncor", "Status": "Success",
                        "Plans": [{"ProductId": 23639, "Title": "Synthetic Saver 12", "Terms": 12,
                                   "kWh1000": "13.9", "EFL": "https://example.com/efl.pdf"}]}])
    p = client.get("/energy-ops/leads/%s/chariot/plans?zip=75201" % lead.id, headers=h).json()
    assert p["ok"] is True and p["data"]["plans"][0]["title"] == "Synthetic Saver 12"
    assert wire.calls[0]["method"] == "GET"
    assert client.get("/energy-ops/leads/%s/chariot/plans?zip=abc" % lead.id, headers=h).status_code == 422
    monkeypatch.delenv(chariot.ENV_API_KEY, raising=False)
    r = client.get("/energy-ops/leads/%s/chariot/plans?zip=75201" % lead.id, headers=h)
    assert r.status_code == 409 and "not connected" in r.json()["detail"]
    assert w["db"].query(ProviderTransaction).count() == 0              # lookups record nothing


def test_scope_other_advisors_and_other_workspaces_cannot_touch_it(client, w, wire, monkeypatch):
    monkeypatch.setenv(chariot.ENV_ALLOW_TRANSACTIONS, "true")
    lead = _lead(client, w)
    wire.queue.append({"Status": "Enrollment has been completed",
                       "Message": "Passed CreditCheck & No Deposit required", "RefrenceNumber": "R-4004"})
    tx = client.post("/energy-ops/leads/%s/chariot/enroll" % lead.id, headers=_h(w, w["adv"]), json=_form()).json()
    hb, ho = _h(w, w["other_adv"]), _h(w, w["omgr"])
    assert client.get("/energy-ops/leads/%s/chariot" % lead.id, headers=hb).status_code == 404
    assert client.post("/energy-ops/leads/%s/chariot/enroll" % lead.id, headers=hb, json=_form()).status_code == 404
    assert client.get("/energy-ops/enrollments", headers=hb).json()["total"] == 0
    assert client.post("/energy-ops/enrollments/%s/reconcile" % tx["id"], headers=hb,
                       json={"outcome": "enrolled", "note": "x"}).status_code == 404
    assert client.get("/energy-ops/leads/%s/chariot" % lead.id, headers=ho).status_code == 404
    assert client.get("/energy-ops/enrollments", headers=ho).json()["total"] == 0
    assert client.post("/energy-ops/enrollments/%s/reconcile" % tx["id"], headers=ho,
                       json={"outcome": "enrolled", "note": "x"}).status_code == 404
    assert len(wire.calls) == 1


# ── review fixes ─────────────────────────────────────────────────────────────
def test_a_lead_not_known_to_be_a_home_is_refused_even_by_direct_post(client, w, wire, monkeypatch):
    monkeypatch.setenv(chariot.ENV_ALLOW_TRANSACTIONS, "true")
    lead = _lead(client, w)
    cf = json.loads(lead.custom_fields)
    cf["segment"], cf["request_kind"] = "Commercial ", None          # odd spelling still means business
    lead.custom_fields = json.dumps(cf)
    w["db"].commit()
    assert client.post("/energy-ops/leads/%s/chariot/enroll" % lead.id, headers=_h(w, w["adv"]),
                       json=_form()).status_code == 409
    cf["segment"] = None                                               # unknown: not eligible either
    lead.custom_fields = json.dumps(cf)
    w["db"].commit()
    info = client.get("/energy-ops/leads/%s/chariot" % lead.id, headers=_h(w, w["adv"])).json()
    assert info["eligible"] is False
    assert client.post("/energy-ops/leads/%s/chariot/enroll" % lead.id, headers=_h(w, w["adv"]),
                       json=_form()).status_code == 409
    assert wire.calls == []


def test_a_bad_identity_value_is_refused_by_name_never_echoed(client, w, wire, monkeypatch):
    monkeypatch.setenv(chariot.ENV_ALLOW_TRANSACTIONS, "true")
    lead = _lead(client, w)
    long_ssn = "6667308099999999999999999"
    r = client.post("/energy-ops/leads/%s/chariot/enroll" % lead.id, headers=_h(w, w["adv"]),
                    json=_form(ssn=long_ssn))
    assert r.status_code == 409 and long_ssn not in r.text and "ssn" in r.text.lower()
    r = client.post("/energy-ops/leads/%s/chariot/enroll" % lead.id, headers=_h(w, w["adv"]),
                    json=_form(dob="x" * 40))
    assert r.status_code == 409 and "x" * 40 not in r.text
    assert wire.calls == []


def test_the_api_key_never_reaches_the_browser_on_a_connection_error(client, w, monkeypatch):
    lead = _lead(client, w)
    key = "test-key-not-a-real-credential"

    def boom(method, url, *, params=None, files=None, timeout=None):
        raise ConnectionError("Max retries exceeded with url: /api/broker/GetPlans?Zip=75201&Key=%s" % key)
    prev = chariot.set_transport(boom)
    try:
        r = client.get("/energy-ops/leads/%s/chariot/plans?zip=75201" % lead.id, headers=_h(w, w["adv"]))
    finally:
        chariot.set_transport(prev)
    assert r.status_code == 200 and r.json()["ok"] is False and key not in r.text
    assert chariot._scrub(ConnectionError("x?Key=%s&y" % key)).count(key) == 0


def test_switched_off_mid_request_is_recorded_as_nothing_sent(client, w, wire, monkeypatch):
    monkeypatch.setenv(chariot.ENV_ALLOW_TRANSACTIONS, "true")
    lead = _lead(client, w)

    def disarmed(payload):
        raise chariot.ChariotTransactionsDisarmed("off")
    monkeypatch.setattr(chariot, "submit_enrollment", disarmed)
    r = client.post("/energy-ops/leads/%s/chariot/enroll" % lead.id, headers=_h(w, w["adv"]), json=_form())
    assert r.status_code == 409 and "Nothing was sent" in r.json()["detail"]
    row = w["db"].query(ProviderTransaction).one()
    assert row.resolved_at is not None and row.resolution_note.startswith("[not_received]")
    monkeypatch.undo()


def test_enrolled_is_refused_cleanly_when_the_customer_cannot_be_enrolled(client, w, wire, monkeypatch):
    monkeypatch.setenv(chariot.ENV_ALLOW_TRANSACTIONS, "true")
    db = w["db"]
    lead = _lead(client, w)
    wire.queue.append({"Status": "Enrollment has been completed",
                       "Message": "Passed CreditCheck & No Deposit required", "RefrenceNumber": "R-5005"})
    tx = client.post("/energy-ops/leads/%s/chariot/enroll" % lead.id, headers=_h(w, w["adv"]), json=_form()).json()
    lead = db.query(Lead).get(lead.id)
    lead.status = "dnc"
    db.commit()
    r = client.post("/energy-ops/enrollments/%s/reconcile" % tx["id"], headers=_h(w, w["adv"]),
                    json={"outcome": "enrolled", "note": "Customer confirmed"})
    assert r.status_code == 409
    db.expire_all()
    row = db.query(ProviderTransaction).get(tx["id"])
    assert row.resolved_at is None                                      # nothing was closed
    assert json.loads(db.query(Lead).get(lead.id).custom_fields)["supplier_handoffs"][0]["status"] == "referred"
