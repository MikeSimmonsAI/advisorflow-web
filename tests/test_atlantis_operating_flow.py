# -*- coding: utf-8 -*-
"""ATLANTIS (retail energy) operating flow, end to end on the API.

CONTACT -> explicit promotion -> LEAD -> PIPELINE -> RATE REQUEST
-> ENROLLMENT / CUSTOMER -> FOLLOW-UP -> RENEWAL, plus Move Concierge,
Communications (simulated inbound reply), DNC/consent visibility, tenant
isolation and identity leakage. No message is sent anywhere.
"""
import itertools
import json
import os
import re
import uuid
from datetime import date, datetime, timedelta

import pytest

import app.models.energy_models  # noqa: F401  (registers energy_move_requests before create_all)
from app.models.energy_models import EnergyMoveRequest
from app.models.intake_models import OrgContact, RecordClass
from app.models.models import AuditLogEntry, Lead, Message, Organization, Reply, User
from app.models.work_models import LeadTask
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
    o = Organization(name=name, slug="atl-%s" % uuid.uuid4().hex[:8], plan="enterprise",
                     industry="energy", is_active=True)
    db.add(o)
    db.commit()
    return o


def _user(db, org, role, label):
    u = User(organization_id=org.id, email="%s-%d@atl.test" % (label, next(_SEQ)),
             password_hash=hash_password("Pass12345!"), full_name=label.title(),
             role=role, is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _h(db, u):
    return {"Authorization": "Bearer %s" % create_access_token(u, db)}


def _lead(db, org, owner, tier="new_inquiry", first="Lena", last="Lane", **kw):
    cf = kw.pop("cf", None)
    l = Lead(organization_id=org.id, assigned_to_id=owner.id if owner else None,
             first_name=first, last_name=last, tier=tier, status=kw.pop("status", "new"),
             custom_fields=json.dumps(cf) if cf else None, **kw)
    db.add(l)
    db.commit()
    return l


def _contact(db, org, first="Cora", last="Cole", rc=RecordClass.CONTACT, **kw):
    c = OrgContact(organization_id=org.id, first_name=first, last_name=last,
                   full_name=f"{first} {last}", record_class=rc,
                   email=kw.pop("email", f"{first.lower()}.{next(_SEQ)}@example.test"),
                   source="HubSpot", source_system="hubspot",
                   source_record_id=str(next(_SEQ)), **kw)
    db.add(c)
    db.commit()
    return c


@pytest.fixture()
def w(db_session):
    db = db_session
    org = _org(db, "Atlantis Test Power")
    other = _org(db, "Other Tenant Energy")
    admin = _user(db, org, "org_admin", "admin")
    adv = _user(db, org, "advisor", "adv")
    fadmin = _user(db, other, "org_admin", "fadmin")
    return dict(db=db, org=org, other=other, admin=admin, adv=adv, fadmin=fadmin)


# ── contact stays contact; promotion is deliberate ─────────────────────────

def test_imported_contact_stays_contact_until_promoted(client, w):
    db, org = w["db"], w["org"]
    c = _contact(db, org)
    leads_before = db.query(Lead).filter(Lead.organization_id == org.id).count()
    # Reading, listing and queue views never create a lead.
    assert client.get(f"/intake/contacts/{c.id}", headers=_h(db, w["admin"])).status_code == 200
    assert client.get("/energy-ops/queues", headers=_h(db, w["admin"])).status_code == 200
    db.refresh(c)
    assert c.record_class == RecordClass.CONTACT and c.lead_id is None
    assert db.query(Lead).filter(Lead.organization_id == org.id).count() == leads_before

    r = client.post(f"/intake/contacts/{c.id}/promote", headers=_h(db, w["admin"]), json={})
    assert r.status_code == 201, r.text
    db.refresh(c)
    assert c.lead_id
    lead = db.query(Lead).filter(Lead.id == c.lead_id).one()
    assert lead.organization_id == org.id
    assert not lead.sms_consent
    assert db.query(Message).filter(Message.lead_id == lead.id).count() == 0


# ── lead -> pipeline -> rate request -> enrollment ─────────────────────────

def test_lead_to_rate_request_to_enrolled_customer(client, w):
    db, org, adv = w["db"], w["org"], w["adv"]
    c = _contact(db, org, "Eddie", "Enroll")
    r = client.post(f"/intake/contacts/{c.id}/promote", headers=_h(db, w["admin"]),
                    json={"assigned_to_id": adv.id})
    assert r.status_code == 201, r.text
    db.refresh(c)
    lead = db.query(Lead).filter(Lead.id == c.lead_id).one()
    lead.tier = "new_inquiry"
    db.commit()

    # pipeline sees the lead in the workspace
    assert client.get("/pipeline/summary", headers=_h(db, w["admin"])).status_code == 200

    h = _h(db, adv)
    assert client.patch(f"/rate-requests/{lead.id}", headers=h,
                        json={"status": "in_review"}).json()["status"] == "in_review"
    r = client.post(f"/rate-requests/{lead.id}/enroll", headers=h,
                    json={"current_supplier": "Example Supplier", "contract_end_date": "2027-06-30"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "completed" and body["tier"] == "contract_signed"
    assert body["contact_reclassified"] == {"from": "contact", "to": "customer"}
    db.refresh(lead)
    db.refresh(c)
    assert lead.relationship_type == "customer"
    assert json.loads(lead.custom_fields)["contract_end_date"] == "2027-06-30"
    assert c.record_class == RecordClass.CUSTOMER
    assert db.query(AuditLogEntry).filter(AuditLogEntry.action == "rate_request.enrolled",
                                          AuditLogEntry.target_id == lead.id).count() == 1
    # enrolled customer appears in the customers queue
    q = client.get("/energy-ops/queues/customers", headers=h).json()
    assert lead.id in [i["id"] for i in q["items"]]
    # bad date is rejected, nothing estimated
    assert client.post(f"/rate-requests/{lead.id}/enroll", headers=h,
                       json={"contract_end_date": "June 2027"}).status_code == 422


def test_enroll_refuses_dnc_and_foreign(client, w):
    db = w["db"]
    dnc = _lead(db, w["org"], w["adv"], status="dnc")
    assert client.post(f"/rate-requests/{dnc.id}/enroll", headers=_h(db, w["admin"]),
                       json={}).status_code == 409
    mine = _lead(db, w["org"], w["adv"])
    assert client.post(f"/rate-requests/{mine.id}/enroll", headers=_h(db, w["fadmin"]),
                       json={}).status_code == 404


# ── follow-up and renewal queues ───────────────────────────────────────────

def test_follow_up_queues_reconcile_with_lists(client, w):
    db, org, adv = w["db"], w["org"], w["adv"]
    l = _lead(db, org, adv)
    now = datetime.utcnow()
    today_noon = datetime(now.year, now.month, now.day, 12)
    for title, due in (("due today", today_noon), ("overdue 1d", today_noon - timedelta(days=1)),
                       ("overdue 5d", today_noon - timedelta(days=5)),
                       ("upcoming", today_noon + timedelta(days=3)),
                       ("far", today_noon + timedelta(days=30))):
        db.add(LeadTask(organization_id=org.id, lead_id=l.id, title=title, due_at=due,
                        status="open", assigned_to_id=adv.id))
    db.add(LeadTask(organization_id=org.id, lead_id=l.id, title="done", due_at=today_noon,
                    status="done", assigned_to_id=adv.id))
    db.commit()
    h = _h(db, w["admin"])
    s = {q["key"]: q["count"] for q in client.get("/energy-ops/queues", headers=h).json()["queues"]}
    assert s["follow_up_due"] == 1 and s["overdue"] == 2 and s["upcoming"] == 1
    assert s["escalation"] == 1  # only the 5-day-overdue task
    for key in ("follow_up_due", "overdue", "upcoming", "escalation", "customers",
                "renewal_window", "previous_customers", "reactivation", "no_response"):
        body = client.get(f"/energy-ops/queues/{key}", headers=h).json()
        assert body["total"] == s[key], key
        assert len(body["items"]) == min(s[key], 25)
    titles = [i["title"] for i in client.get("/energy-ops/queues/overdue", headers=h).json()["items"]]
    assert titles == ["overdue 5d", "overdue 1d"]


def test_renewal_queue_uses_only_real_dates(client, w):
    db, org, adv = w["db"], w["org"], w["adv"]
    from app.services.activity_reporting import org_local_date
    today = org_local_date(db, org.id)          # the workspace's day, not UTC's
    soon = (today + timedelta(days=45)).isoformat()
    far = (today + timedelta(days=400)).isoformat()
    a = _lead(db, org, adv, tier="contract_signed", first="Soon", cf={"contract_end_date": soon})
    _lead(db, org, adv, tier="contract_signed", first="Far", cf={"contract_end_date": far})
    _lead(db, org, adv, tier="contract_signed", first="NoDate")
    body = client.get("/energy-ops/queues", headers=_h(db, adv)).json()
    counts = {q["key"]: q["count"] for q in body["queues"]}
    assert counts["renewal_window"] == 1
    assert body["renewal_date_missing"] == 1
    items = client.get("/energy-ops/queues/renewal_window", headers=_h(db, adv)).json()["items"]
    assert [i["id"] for i in items] == [a.id]
    assert items[0]["contract_end_date"] == soon and items[0]["days_to_renewal"] == 45


def test_previous_customer_and_reactivation_queues_are_manager_only(client, w):
    db, org = w["db"], w["org"]
    _contact(db, org, "Prev", "One", rc=RecordClass.PREVIOUS_CUSTOMER)
    lead = _lead(db, org, w["adv"])
    _contact(db, org, "Prev", "Two", rc=RecordClass.PREVIOUS_CUSTOMER, lead_id=lead.id)
    _contact(db, w["other"], "Foreign", "Prev", rc=RecordClass.PREVIOUS_CUSTOMER)
    counts = {q["key"]: q for q in client.get("/energy-ops/queues",
                                               headers=_h(db, w["admin"])).json()["queues"]}
    assert counts["previous_customers"]["count"] == 2
    assert counts["reactivation"]["count"] == 1
    adv_counts = {q["key"]: q for q in client.get("/energy-ops/queues",
                                                   headers=_h(db, w["adv"])).json()["queues"]}
    assert adv_counts["previous_customers"]["available"] is False
    assert adv_counts["previous_customers"]["count"] is None
    # listing reactivation candidates promotes nobody
    client.get("/energy-ops/queues/reactivation", headers=_h(db, w["admin"]))
    assert db.query(OrgContact).filter(OrgContact.organization_id == org.id,
                                       OrgContact.lead_id.isnot(None)).count() == 1


def test_no_response_queue(client, w):
    db, org, adv = w["db"], w["org"], w["adv"]
    old = datetime.utcnow() - timedelta(days=10)
    silent = _lead(db, org, adv, first="Silent", last_messaged_at=old)
    answered = _lead(db, org, adv, first="Answered", last_messaged_at=old)
    db.add(Reply(lead_id=answered.id, body="yes call me", source="sms",
                 received_at=old + timedelta(days=1)))
    _lead(db, org, adv, first="Recent", last_messaged_at=datetime.utcnow() - timedelta(days=2))
    db.commit()
    ids = [i["id"] for i in client.get("/energy-ops/queues/no_response",
                                       headers=_h(db, adv)).json()["items"]]
    assert ids == [silent.id]


# ── move concierge ─────────────────────────────────────────────────────────

def test_move_concierge_workflow(client, w):
    db, org, adv = w["db"], w["org"], w["adv"]
    lead = _lead(db, org, adv, first="Mover")
    h = _h(db, adv)
    meta = client.get("/energy-ops/moves/meta", headers=h).json()
    assert meta["vendor_integrations"]["status"] == "not_configured"
    r = client.post("/energy-ops/moves", headers=h, json={
        "lead_id": lead.id, "to_address": "12 New St", "services": ["electricity_start", "internet"]})
    assert r.status_code == 201, r.text
    m = r.json()
    assert m["move_date"] is None  # not provided -> not invented
    assert m["status"] == "requested" and m["assigned_to_id"] == adv.id
    assert m["checklist_total"] == 4 and m["checklist_done"] == 0
    mid = m["id"]
    r = client.post(f"/energy-ops/moves/{mid}/checklist", headers=h,
                    json={"key": "svc_internet", "done": True})
    assert r.json()["checklist_done"] == 1 and r.json()["status"] == "in_progress"
    r = client.patch(f"/energy-ops/moves/{mid}", headers=h,
                     json={"move_date": "2026-11-15", "notes": "Gate code 1234",
                           "services": ["electricity_start", "internet", "water"]})
    assert r.status_code == 200 and r.json()["move_date"] == "2026-11-15"
    assert r.json()["checklist_total"] == 5
    t = client.post(f"/energy-ops/moves/{mid}/tasks", headers=h,
                    json={"title": "Call customer about water"})
    assert t.status_code == 201 and t.json()["lead_id"] == lead.id
    d = client.get(f"/energy-ops/moves/{mid}", headers=h).json()
    assert [x["title"] for x in d["tasks"]] == ["Call customer about water"]
    acts = {a["action"] for a in d["activity"]}
    assert {"move_request.created", "move_request.checklist", "move_request.updated",
            "move_request.task_added"} <= acts
    assert client.patch(f"/energy-ops/moves/{mid}", headers=h,
                        json={"status": "teleported"}).status_code == 400
    lst = client.get("/energy-ops/moves?status=open", headers=h).json()
    assert lst["total"] == 1 and lst["summary"]["open"] == 1
    assert client.post("/energy-ops/moves", headers=h,
                       json={"services": ["helicopter"], "contact_name": "X"}).status_code == 400
    # no outbound message of any kind
    assert db.query(Message).filter(Message.lead_id == lead.id).count() == 0


def test_move_concierge_tenant_isolation(client, w):
    db = w["db"]
    m = EnergyMoveRequest(organization_id=w["org"].id, contact_name="Mine", status="requested")
    db.add(m)
    db.commit()
    fh = _h(db, w["fadmin"])
    assert client.get(f"/energy-ops/moves/{m.id}", headers=fh).status_code == 404
    assert client.patch(f"/energy-ops/moves/{m.id}", headers=fh, json={"notes": "x"}).status_code == 404
    assert client.get("/energy-ops/moves", headers=fh).json()["total"] == 0
    foreign_lead = _lead(db, w["other"], w["fadmin"])
    assert client.post("/energy-ops/moves", headers=_h(db, w["admin"]),
                       json={"lead_id": foreign_lead.id}).status_code == 404
    # an advisor sees only their own moves
    assert client.get(f"/energy-ops/moves/{m.id}", headers=_h(db, w["adv"])).status_code == 404


def test_queues_tenant_isolation(client, w):
    db = w["db"]
    l = _lead(db, w["org"], w["adv"], tier="contract_signed")
    db.add(LeadTask(organization_id=w["org"].id, lead_id=l.id, title="t",
                    due_at=datetime.utcnow() - timedelta(days=2), status="open"))
    db.commit()
    fh = _h(db, w["fadmin"])
    counts = {q["key"]: q["count"] for q in client.get("/energy-ops/queues", headers=fh).json()["queues"]}
    assert counts["overdue"] == 0 and counts["customers"] == 0


# ── communications: simulated inbound reply, DNC / consent visible ─────────

def test_simulated_inbound_reply_shows_dnc_and_consent_and_converts_nothing(client, w):
    db, org, adv = w["db"], w["org"], w["adv"]
    ok = _lead(db, org, adv, first="Consented", sms_consent=True)
    dnc = _lead(db, org, adv, first="Stopper", status="dnc")
    # Simulated inbound event (no provider): a Reply row, as the webhook would write.
    db.add(Reply(lead_id=ok.id, body="What rate can you get me?", source="sms"))
    db.add(Reply(lead_id=dnc.id, body="STOP", source="sms"))
    db.commit()
    leads_before = db.query(Lead).filter(Lead.organization_id == org.id).count()
    r = client.get("/communications/replies", headers=_h(db, w["admin"]))
    assert r.status_code == 200, r.text
    rows = {i["lead_id"]: i for i in r.json()["items"]}
    assert rows[ok.id]["sms_consent"] is True and rows[ok.id]["is_dnc"] is False
    assert rows[dnc.id]["is_dnc"] is True and rows[dnc.id]["sms_consent"] is False
    assert db.query(Lead).filter(Lead.organization_id == org.id).count() == leads_before
    # foreign tenant sees neither
    fr = client.get("/communications/replies", headers=_h(db, w["fadmin"])).json()
    assert not {ok.id, dnc.id} & {i["lead_id"] for i in fr["items"]}


# ── identity: no Wholesale text on energy screens ──────────────────────────

ENERGY_FILES = [
    "frontend/src/pages/vertical/EnergyOverview.jsx",
    "frontend/src/pages/vertical/RateRequests.jsx",
    "frontend/src/pages/energy/FollowUpQueues.jsx",
    "frontend/src/pages/energy/MoveConcierge.jsx",
]


def _visible_strings(src):
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"^\s*//.*$", "", src, flags=re.M)
    return src


@pytest.mark.parametrize("rel", ENERGY_FILES)
def test_no_wholesale_identity_in_energy_pages(rel):
    path = os.path.join(ROOT, rel)
    if not os.path.exists(path):
        pytest.skip("not present")
    text = _visible_strings(open(path, encoding="utf-8").read())
    assert not re.search(r"wholesal|max life|EvoSys Wholesale", text, re.I), rel


def test_energy_nav_has_no_wholesale_labels():
    src = open(os.path.join(ROOT, "frontend/src/verticals/workspaceVertical.js"), encoding="utf-8").read()
    block = src[src.index("const ENERGY = {"):src.index("export const VERTICAL_CLEANING")]
    labels = re.findall(r"label:\s*'([^']+)'", block)
    assert labels and not any(re.search(r"wholesal|deal|evosense|max life", l, re.I) for l in labels)


def test_renewal_days_and_move_window_use_the_workspace_day_in_the_evening(client, w, monkeypatch):
    """22:30 Central on Oct 1 is 03:30 UTC on Oct 2. "Days to renewal" was
    counted from UTC's date - one short every evening - and the 14-day move
    window started a day late. Found by the final verification run at 23:01 CT."""
    from app.routers import energy_ops_router as R
    db, org, adv = w["db"], w["org"], w["adv"]
    adv.booking_timezone = "America/Chicago"
    db.commit()
    evening_utc = datetime(2026, 10, 2, 3, 30)          # = 2026-10-01 22:30 CT
    monkeypatch.setattr(R, "_now", lambda: evening_utc)
    _lead(db, org, adv, tier="contract_signed", first="Nov15", cf={"contract_end_date": "2026-11-15"})
    items = client.get("/energy-ops/queues/renewal_window", headers=_h(db, adv)).json()["items"]
    row = [i for i in items if i["contract_end_date"] == "2026-11-15"][0]
    assert row["days_to_renewal"] == 45                  # Oct 1 -> Nov 15, not 44
    # A move dated "today" (Oct 1, local) is still inside the 14-day window.
    db.add(EnergyMoveRequest(organization_id=org.id, contact_name="Evening Move (TEST)",
                             move_date=date(2026, 10, 1), status="requested", created_by_id=adv.id))
    db.commit()
    summary = client.get("/energy-ops/moves", headers=_h(db, adv)).json()["summary"]
    assert summary["moving_in_14_days"] == 1

