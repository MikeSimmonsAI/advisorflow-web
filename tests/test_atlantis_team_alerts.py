# -*- coding: utf-8 -*-
"""ATLANTIS TEAM & ALERTS + the shared NEW REQUESTS inbox (synthetic people).

The owner's required demonstration, end to end over HTTP:
  website inquiry -> shared queue -> the selected team is alerted -> an advisor
  claims it -> the first-contact task is theirs;
  field referral -> the same queue -> a manager assigns it -> that advisor works it;
  a manager changes who is alerted in the app -> the NEXT request follows it.
Plus: one alert per request (retries / repeat forms), default advisor, email
shown as not configured until the shared mailbox is verified (and then only
the team is emailed, test records never), access control and tenant isolation.
No customer is ever messaged; the email provider is a recording fake.
"""
import itertools
import json
import uuid

import pytest

from app.models.models import EmailMessage, Lead, Message, Notification, Organization, User
from app.models.work_models import LeadTask
from app.services import customer_sites as cs
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)
PAGE = "<!DOCTYPE html><html><head><title>A</title></head><body>x</body></html>"


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


@pytest.fixture()
def sent(monkeypatch):
    """Recording fake for the email provider - nothing leaves the machine."""
    out = []

    def fake(to, subject, html, **kw):
        out.append({"to": to, "subject": subject, "html": html})
        return {"success": True, "provider_message_id": "fake-%d" % len(out), "error": None}
    monkeypatch.setattr("app.services.email_service.send_email_via_provider", fake)
    monkeypatch.delenv("OUTBOUND_EMAIL_ENERGY_TEAM_ALERT", raising=False)
    return out


def _org(db, name, industry="energy"):
    o = Organization(name=name, slug="ta-%s" % uuid.uuid4().hex[:8], plan="enterprise",
                     industry=industry, is_active=True)
    db.add(o)
    db.commit()
    return o


def _user(db, org, role, name):
    u = User(organization_id=org.id, email="%s-%d@team.example.test" % (name, next(_SEQ)),
             password_hash=hash_password("x"), full_name=name.title(), role=role,
             is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


@pytest.fixture()
def w(db_session):
    db = db_session
    org, other = _org(db, "Atlantis Team"), _org(db, "Other Energy")
    w = dict(db=db, org=org, other=other, mgr=_user(db, org, "org_admin", "morgan"),
             ava=_user(db, org, "advisor", "ava"), ben=_user(db, org, "advisor", "ben"),
             rep=_user(db, org, "advisor", "rita"), omgr=_user(db, other, "org_admin", "olga"))
    w["slug"] = "ta-site-%d" % next(_SEQ)
    cs.publish(db, organization=org, slug=w["slug"], html=PAGE, title=org.name)
    return w


def _h(w, u):
    return {"Authorization": "Bearer %s" % create_access_token(u, w["db"])}


def _form(client, w, first="Wendy", **kw):
    p = {"first_name": first, "last_name": "Synthetic", "email": "%s.%d@gmail.com" % (first.lower(), next(_SEQ)),
         "phone": "(214) 555-%04d" % (3000 + next(_SEQ)), "consent": True,
         "request_type": "Compare electricity options", "service_area": "75201", "form_started_at": 1}
    p.update(kw)
    assert client.post("/site/%s/inquiry" % w["slug"], json=p).status_code == 201
    return p, w["db"].query(Lead).filter_by(organization_id=w["org"].id, email=p["email"].lower()).one()


def _alerted(w, lead):
    return {n.user_id for n in w["db"].query(Notification).filter_by(lead_id=lead.id)}


def _events(w, lead):
    from app.models.energy_models import EnergyRequestAlert
    return w["db"].query(EnergyRequestAlert).filter_by(organization_id=w["org"].id, lead_id=lead.id).all()


def _set(client, w, user, **flags):
    r = client.patch("/energy-ops/team/members/%s" % user.id, headers=_h(w, w["mgr"]), json=flags)
    assert r.status_code == 200, r.text
    return r.json()


def _quiet(w):
    assert w["db"].query(Message).count() == 0 and w["db"].query(EmailMessage).count() == 0


def _task_in_queues(client, w, user, task_id):
    for key in ("follow_up_due", "upcoming", "overdue"):
        r = client.get("/energy-ops/queues/%s" % key, headers=_h(w, user), params={"per_page": 100})
        if r.status_code == 200 and task_id in {i["id"] for i in r.json().get("items", [])}:
            return key
    return None


# 1 ── website inquiry -> shared queue -> selected team alerted -> claim -> task
def test_website_inquiry_reaches_the_team_and_an_advisor_claims_it(client, w, sent):
    db = w["db"]
    _set(client, w, w["ava"], in_app=True)                 # Ava wants alerts; Ben does not
    p, lead = _form(client, w)
    assert lead.assigned_to_id is None and lead.tier == "new_inquiry"
    assert _alerted(w, lead) == {w["mgr"].id, w["ava"].id}
    assert len(_events(w, lead)) == 1
    note = db.query(Notification).filter_by(lead_id=lead.id, user_id=w["ava"].id).one()
    assert "Unclaimed" in note.message and note.link.startswith("/energy/follow-up?view=new")

    inbox = client.get("/energy-ops/new-requests", headers=_h(w, w["ava"])).json()
    row = next(i for i in inbox["items"] if i["lead_id"] == lead.id)
    assert row["origin"] == "website" and row["phone"] is None and row["can_open"] is False
    assert inbox["can_claim"] is True and inbox["counts"]["unclaimed"] == 1

    r = client.post("/energy-ops/new-requests/%s/claim" % lead.id, headers=_h(w, w["ava"]))
    assert r.status_code == 200 and r.json()["assigned_to_id"] == w["ava"].id
    db.refresh(lead)
    assert lead.assigned_to_id == w["ava"].id
    task = db.query(LeadTask).filter_by(lead_id=lead.id, source="website").one()
    assert task.status == "open" and task.assigned_to_id == w["ava"].id
    assert _task_in_queues(client, w, w["ava"], task.id) is not None          # task appears for Ava
    assert lead.id in {i["id"] for i in client.get("/rate-requests/", headers=_h(w, w["ava"]),
                                                   params={"status": "new"}).json()["items"]}
    mine = client.get("/energy-ops/new-requests?state=mine", headers=_h(w, w["ava"])).json()
    assert mine["items"][0]["phone"] and mine["items"][0]["can_open"] is True

    # one accountable advisor: Ben cannot take it from her
    r = client.post("/energy-ops/new-requests/%s/claim" % lead.id, headers=_h(w, w["ben"]))
    assert r.status_code == 409 and "Ava" in r.json()["detail"]
    assert client.post("/energy-ops/new-requests/%s/claim" % lead.id,
                       headers=_h(w, w["ava"])).json()["changed"] is False          # idempotent
    assert sent == [] and _quiet(w) is None


# 2 ── field referral -> same queue -> manager assigns -> advisor works it
def test_field_referral_joins_the_same_queue_and_the_assigned_advisor_works_it(client, w, sent):
    db = w["db"]
    _set(client, w, w["ava"], in_app=True)
    r = client.post("/energy-ops/field/visits", headers=_h(w, w["rep"]), json={
        "outcome": "interested", "first_name": "Door", "last_name": "Synthetic", "phone": "(817) 555-0701",
        "permission_to_contact": True, "address": "71 Field Ln", "zip_code": "76104"}).json()
    lead = db.query(Lead).get(r["lead_id"])
    assert _alerted(w, lead) == {w["mgr"].id, w["ava"].id} and len(_events(w, lead)) == 1
    inbox = client.get("/energy-ops/new-requests", headers=_h(w, w["mgr"])).json()
    row = next(i for i in inbox["items"] if i["lead_id"] == lead.id)
    assert row["origin"] == "field" and row["field_rep"] == "Rita"
    assert {a["user_id"] for a in inbox["assignable"]} >= {w["ava"].id, w["ben"].id}

    r = client.post("/energy-ops/new-requests/%s/assign" % lead.id, headers=_h(w, w["mgr"]),
                    json={"user_id": w["ben"].id})
    assert r.status_code == 200
    db.refresh(lead)
    assert lead.assigned_to_id == w["ben"].id
    assert db.query(Notification).filter_by(lead_id=lead.id, user_id=w["ben"].id).count() == 1   # "assigned you"
    t = db.query(LeadTask).filter_by(lead_id=lead.id, source="field_referral").one()
    assert t.assigned_to_id == w["ben"].id
    hb = _h(w, w["ben"])
    assert client.get("/energy-ops/leads/%s/qualification" % lead.id, headers=hb).status_code == 200
    q = client.post("/energy-ops/leads/%s/qualification" % lead.id, headers=hb,
                    json={"fields": {"current_supplier": "Synthetic Power Co"}})
    assert q.status_code == 200
    assert json.loads(lead.custom_fields)["field_rep_id"] == w["rep"].id                  # rep still credited
    assert sent == [] and _quiet(w) is None


# 3 ── change recipients in the app -> the NEXT request follows it
def test_the_next_request_follows_the_recipient_change(client, w, sent):
    _set(client, w, w["ava"], in_app=True)
    _, first = _form(client, w, "First")
    assert _alerted(w, first) == {w["mgr"].id, w["ava"].id}
    _set(client, w, w["ava"], in_app=False)                # remove Ava
    _set(client, w, w["ben"], in_app=True)                 # add Ben
    _, second = _form(client, w, "Second")
    assert _alerted(w, second) == {w["mgr"].id, w["ben"].id}
    _set(client, w, w["mgr"], in_app=False)                # manager steps out too
    _, third = _form(client, w, "Third")
    assert _alerted(w, third) == {w["ben"].id}
    _set(client, w, w["ben"], in_app=False)                # nobody selected -> managers, never nobody
    _, fourth = _form(client, w, "Fourth")
    assert _alerted(w, fourth) == {w["mgr"].id}
    assert sent == []


# 4 ── one request, one alert event
def test_retries_and_repeat_forms_do_not_alert_again(client, w, sent):
    db = w["db"]
    p, lead = _form(client, w, "Rhea")
    _form(client, w, "Rhea", email=p["email"], phone=p["phone"])           # browser retry / double submit
    _form(client, w, "Rhea", email=p["email"], phone=p["phone"], monthly_usage="900 kWh")   # came back later
    assert db.query(Lead).filter_by(organization_id=w["org"].id).count() == 1
    assert db.query(Notification).filter_by(lead_id=lead.id).count() == 1     # one per recipient (manager)
    assert len(_events(w, lead)) == 1
    assert db.query(LeadTask).filter_by(lead_id=lead.id, source="website", status="open").count() == 1
    # once that request is handled, a NEW request from the same person is new work
    t = db.query(LeadTask).filter_by(lead_id=lead.id, source="website").one()
    t.status = "done"
    db.commit()
    _form(client, w, "Rhea", email=p["email"], phone=p["phone"], request_type="Analyze my current bill")
    assert len(_events(w, lead)) == 2
    v = {"outcome": "interested", "phone": "(817) 555-0702", "permission_to_contact": True,
         "address": "72 Field Ln", "first_name": "Dup"}
    a = client.post("/energy-ops/field/visits", headers=_h(w, w["rep"]), json=v).json()
    b = client.post("/energy-ops/field/visits", headers=_h(w, w["rep"]), json=v).json()
    assert b["duplicate"] is True and b["lead_id"] == a["lead_id"]
    assert db.query(Notification).filter_by(lead_id=a["lead_id"]).count() == 1


# 5 ── default advisor, set in the app
def test_default_advisor_takes_new_requests_and_is_always_told(client, w, sent):
    db = w["db"]
    h = _h(w, w["mgr"])
    assert client.put("/energy-ops/team/default-advisor", headers=h, json={"user_id": w["ben"].id}).status_code == 200
    _, lead = _form(client, w, "Dora")
    assert lead.assigned_to_id == w["ben"].id
    assert w["ben"].id in _alerted(w, lead)                 # the owner hears even with alerts off
    assert db.query(LeadTask).filter_by(lead_id=lead.id, source="website").one().assigned_to_id == w["ben"].id
    assert lead.id not in {i["lead_id"] for i in client.get("/energy-ops/new-requests", headers=h).json()["items"]}
    # switching Ben to "cannot be assigned" clears the default -> back to the shared queue
    out = _set(client, w, w["ben"], assignable=False)
    assert out["note"] and client.get("/energy-ops/team", headers=h).json()["default_advisor_id"] is None
    _, nxt = _form(client, w, "Next")
    assert nxt.assigned_to_id is None
    assert client.put("/energy-ops/team/default-advisor", headers=h,
                      json={"user_id": w["ben"].id}).status_code == 422
    assert client.put("/energy-ops/team/default-advisor", headers=h,
                      json={"user_id": w["omgr"].id}).status_code == 422          # not on this team
    # the public website was not touched
    assert db.query(cs.CustomerSite).filter_by(organization_id=w["org"].id).one().default_owner_user_id is None


# 6 ── access control and isolation
def test_permissions_and_tenant_isolation(client, w, sent):
    _, lead = _form(client, w, "Iso")
    ha, hb, ho = _h(w, w["ava"]), _h(w, w["ben"]), _h(w, w["omgr"])
    assert client.get("/energy-ops/team", headers=ha).status_code == 403
    assert client.patch("/energy-ops/team/members/%s" % w["ava"].id, headers=ha, json={"in_app": True}).status_code == 403
    assert client.put("/energy-ops/team/default-advisor", headers=ha, json={"user_id": w["ava"].id}).status_code == 403
    assert client.post("/energy-ops/new-requests/%s/assign" % lead.id, headers=ha,
                       json={"user_id": w["ava"].id}).status_code == 403
    _set(client, w, w["ben"], assignable=False)
    assert client.get("/energy-ops/new-requests", headers=hb).status_code == 403
    assert client.post("/energy-ops/new-requests/%s/claim" % lead.id, headers=hb).status_code == 403
    # another energy workspace: its own (empty) team, never Atlantis's data
    t = client.get("/energy-ops/team", headers=ho).json()
    assert {m["user_id"] for m in t["members"]} == {w["omgr"].id}
    assert client.get("/energy-ops/new-requests", headers=ho).json()["counts"]["all"] == 0
    assert client.post("/energy-ops/new-requests/%s/claim" % lead.id, headers=ho).status_code == 404
    assert client.post("/energy-ops/new-requests/%s/assign" % lead.id, headers=ho,
                       json={"user_id": w["omgr"].id}).status_code in (404, 422)
    assert client.patch("/energy-ops/team/members/%s" % w["ava"].id, headers=ho,
                        json={"in_app": True}).status_code == 404
    w["db"].refresh(lead)
    assert lead.assigned_to_id is None
    # a non-energy workspace has no such screen
    fun = _org(w["db"], "Funeral Co", industry="funeral")
    fadm = _user(w["db"], fun, "org_admin", "fay")
    assert client.get("/energy-ops/team", headers=_h(w, fadm)).status_code == 404


# 7 ── email: honest until verified, then only the team, never test records
def test_email_is_not_configured_until_the_shared_mailbox_is_verified(client, w, sent, monkeypatch):
    db = w["db"]
    h = _h(w, w["mgr"])
    st = client.get("/energy-ops/team", headers=h).json()["email"]
    assert st["state"] == "not_configured" and st["label"] == "Email not configured" and st["reasons"]
    _set(client, w, w["ava"], email=True)
    _, lead = _form(client, w, "Pre")
    ev = json.loads(_events(w, lead)[0].details)
    assert ev["email"]["state"] == "not_configured" and sent == []          # nothing pretended
    assert client.put("/energy-ops/team/mailbox", headers=h, json={"address": "not an email"}).status_code == 422
    assert client.put("/energy-ops/team/mailbox", headers=h,
                      json={"address": "alerts@atlantis.example.test"}).json()["mailbox_verified"] is False
    assert client.post("/energy-ops/team/mailbox/send-test", headers=h).status_code == 409   # no sender / switch

    # the workspace gets a working sender and the server switch is turned on
    monkeypatch.setattr("app.services.email_service.describe_email_sender",
                        lambda db_, org_id: {"ready": True, "from_email": "team@atlantis.example.test", "reason": None})
    monkeypatch.setenv("OUTBOUND_EMAIL_ENERGY_TEAM_ALERT", "true")
    st = client.get("/energy-ops/team", headers=h).json()["email"]
    assert st["state"] == "not_configured" and any("verified" in r for r in st["reasons"])
    assert client.post("/energy-ops/team/mailbox/send-test", headers=h).status_code == 200
    code = sent[-1]["subject"].split(": ")[-1]
    assert sent[-1]["to"] == "alerts@atlantis.example.test"
    assert client.post("/energy-ops/team/mailbox/confirm", headers=h, json={"code": "000000" if code != "000000" else "111111"}).status_code == 422
    st = client.post("/energy-ops/team/mailbox/confirm", headers=h, json={"code": code}).json()
    assert st["state"] == "ready" and st["mailbox_verified"] is True

    before = len(sent)
    _, real = _form(client, w, "Post")
    to = {m["to"] for m in sent[before:]}
    assert to == {"alerts@atlantis.example.test", w["ava"].email}           # mailbox + Ava only
    assert all(real.email not in m["to"] for m in sent)                     # never the customer
    ev = json.loads(_events(w, real)[0].details)
    assert ev["email"]["state"] == "attempted" and ev["email"]["sent"] == 2
    _, test_rec = _form(client, w, "Qa", email="qa.%d@example.com" % next(_SEQ))
    assert json.loads(_events(w, test_rec)[0].details)["email"]["state"] == "skipped"
    # a different address must be verified again before anything is sent to it
    st = client.put("/energy-ops/team/mailbox", headers=h, json={"address": "new@atlantis.example.test"}).json()
    assert st["state"] == "not_configured" and st["mailbox_verified"] is False
    # a failed provider send is reported as failed, never as delivered
    monkeypatch.setattr("app.services.email_service.send_email_via_provider",
                        lambda *a, **k: {"success": False, "provider_message_id": None, "error": "bounced"})
    assert client.post("/energy-ops/team/mailbox/send-test", headers=h).status_code == 502
    assert _quiet(w) is None


# 8 ── add a teammate with the existing invitation, then manage them
def test_add_teammate_uses_the_existing_invitation(client, w, sent):
    h = _h(w, w["mgr"])
    db = w["db"]
    r = client.post("/admin/users", headers=h, json={"email": "nina.%d@example.com" % next(_SEQ),
                                                     "full_name": "Nina New", "role": "advisor"})
    assert r.status_code in (200, 201), r.text
    assert r.json()["setup_url"]
    nina = next(m for m in client.get("/energy-ops/team", headers=h).json()["members"] if m["name"] == "Nina New")
    assert nina["setup_pending"] is True and nina["assignable"] is True and nina["in_app"] is False
    _set(client, w, w["db"].query(User).get(nina["user_id"]), in_app=True)
    _, lead = _form(client, w, "ForNina")
    assert nina["user_id"] in _alerted(w, lead)
    # an advisor cannot add people (existing rule, unchanged)
    assert client.post("/admin/users", headers=_h(w, w["ava"]),
                       json={"email": "x%d@example.com" % next(_SEQ), "full_name": "X"}).status_code == 403
    # someone who leaves stops getting alerts
    u = w["db"].query(User).get(nina["user_id"])
    u.is_active = False
    w["db"].commit()
    _, later = _form(client, w, "AfterNina")
    assert nina["user_id"] not in _alerted(w, later)


# 9 ── review fixes: only genuine open requests are in the inbox or claimable
def test_only_open_requests_are_in_the_inbox_or_claimable(client, w, sent):
    db = w["db"]
    imported = Lead(organization_id=w["org"].id, first_name="Imported", last_name="Row", tier="new_inquiry",
                    status="new", phone="(214) 555-0999", email="imported@gmail.com")
    db.add(imported)
    db.commit()
    _, real = _form(client, w, "Real")
    items = {i["lead_id"] for i in client.get("/energy-ops/new-requests?state=all", headers=_h(w, w["mgr"])).json()["items"]}
    assert real.id in items and imported.id not in items
    assert client.post("/energy-ops/new-requests/%s/claim" % imported.id, headers=_h(w, w["ava"])).status_code == 404
    assert client.post("/energy-ops/new-requests/%s/assign" % imported.id, headers=_h(w, w["mgr"]),
                       json={"user_id": w["ava"].id}).status_code == 404
    db.refresh(imported)
    assert imported.assigned_to_id is None
    # once the first contact is done, the request leaves the inbox
    client.post("/energy-ops/new-requests/%s/claim" % real.id, headers=_h(w, w["ava"]))
    t = db.query(LeadTask).filter_by(lead_id=real.id, source="website").one()
    t.status = "done"
    db.commit()
    assert real.id not in {i["lead_id"] for i in client.get("/energy-ops/new-requests?state=all",
                                                             headers=_h(w, w["mgr"])).json()["items"]}


def test_advisors_see_unclaimed_and_their_own_but_not_colleagues(client, w, sent):
    _, a = _form(client, w, "ForAva")
    _, b = _form(client, w, "ForBen")
    _, c = _form(client, w, "Open")
    client.post("/energy-ops/new-requests/%s/claim" % a.id, headers=_h(w, w["ava"]))
    client.post("/energy-ops/new-requests/%s/claim" % b.id, headers=_h(w, w["ben"]))
    seen = {i["lead_id"] for i in client.get("/energy-ops/new-requests?state=all", headers=_h(w, w["ava"])).json()["items"]}
    assert seen == {a.id, c.id}
    mgr = {i["lead_id"] for i in client.get("/energy-ops/new-requests?state=all", headers=_h(w, w["mgr"])).json()["items"]}
    assert {a.id, b.id, c.id} <= mgr


def test_someone_whose_membership_was_turned_off_is_off_the_team(client, w, sent):
    from app.models.sales_models import Membership, SCOPE_CUSTOMER_ORG
    db = w["db"]
    _set(client, w, w["ava"], in_app=True)
    db.add(Membership(user_id=w["ava"].id, scope_type=SCOPE_CUSTOMER_ORG, scope_id=w["org"].id,
                      role="advisor", is_active=False))
    db.commit()
    team = client.get("/energy-ops/team", headers=_h(w, w["mgr"])).json()
    assert w["ava"].id not in {m["user_id"] for m in team["members"]}
    _, lead = _form(client, w, "AfterLeave")
    assert w["ava"].id not in _alerted(w, lead)
    assert client.post("/energy-ops/new-requests/%s/claim" % lead.id, headers=_h(w, w["ava"])).status_code == 403


def test_mailbox_code_expires_and_locks_after_five_wrong_tries(client, w, sent, monkeypatch):
    h = _h(w, w["mgr"])
    monkeypatch.setattr("app.services.email_service.describe_email_sender",
                        lambda db_, org_id: {"ready": True, "from_email": "team@atlantis.example.test", "reason": None})
    monkeypatch.setenv("OUTBOUND_EMAIL_ENERGY_TEAM_ALERT", "true")
    client.put("/energy-ops/team/mailbox", headers=h, json={"address": "alerts@atlantis.example.test"})
    client.post("/energy-ops/team/mailbox/send-test", headers=h)
    code = sent[-1]["subject"].split(": ")[-1]
    wrong = "%06d" % ((int(code) + 1) % 1000000)
    for _ in range(5):
        assert client.post("/energy-ops/team/mailbox/confirm", headers=h, json={"code": wrong}).status_code == 422
    r = client.post("/energy-ops/team/mailbox/confirm", headers=h, json={"code": code})
    assert r.status_code == 422 and "Send a new test code" in r.json()["detail"]
    # a fresh code works; an old one (expired) does not
    client.post("/energy-ops/team/mailbox/send-test", headers=h)
    code2 = sent[-1]["subject"].split(": ")[-1]
    from app.services import energy_team
    data = energy_team.load(w["db"], w["org"].id)
    data["mailbox"]["test_sent_at"] = "2020-01-01T00:00:00Z"
    energy_team.save(w["db"], w["org"].id, data, w["mgr"].id)
    w["db"].commit()
    assert client.post("/energy-ops/team/mailbox/confirm", headers=h, json={"code": code2}).status_code == 422


def test_team_email_goes_out_only_after_the_request_is_saved(client, w, sent, monkeypatch):
    from app.models.energy_models import EnergyRequestAlert
    from app.services import energy_team
    h = _h(w, w["mgr"])
    monkeypatch.setattr("app.services.email_service.describe_email_sender",
                        lambda db_, org_id: {"ready": True, "from_email": "team@atlantis.example.test", "reason": None})
    monkeypatch.setenv("OUTBOUND_EMAIL_ENERGY_TEAM_ALERT", "true")
    client.put("/energy-ops/team/mailbox", headers=h, json={"address": "alerts@atlantis.example.test"})
    client.post("/energy-ops/team/mailbox/send-test", headers=h)
    client.post("/energy-ops/team/mailbox/confirm", headers=h, json={"code": sent[-1]["subject"].split(": ")[-1]})
    db = w["db"]
    before = len(sent)
    # a request whose transaction is rolled back emails nobody
    lead = Lead(organization_id=w["org"].id, first_name="Rolled", last_name="Back", tier="new_inquiry", status="new")
    db.add(lead)
    db.flush()
    out = energy_team.alert_new_request(db, org=w["org"], lead=lead, event_key="website:test:rollback", origin="website")
    assert out["email"]["state"] == "queued" and len(sent) == before       # nothing sent yet
    db.rollback()
    db.commit()
    assert len(sent) == before
    assert db.query(EnergyRequestAlert).filter_by(event_key="website:test:rollback").count() == 0
    # a committed one sends, and records the result
    _, real = _form(client, w, "Saved")
    assert len(sent) == before + 1 and sent[-1]["to"] == "alerts@atlantis.example.test"
    ev = db.query(EnergyRequestAlert).filter_by(lead_id=real.id).one()
    db.refresh(ev)
    assert json.loads(ev.details)["email"]["state"] == "attempted"


# 10 ── the real-database case the shared in-memory test DB cannot show: a
# website alert is raised INSIDE a savepoint (energy_site_intake._step). Its
# email must wait for the request's real commit - not fire on the savepoint
# release, when another connection cannot see the event yet.
def test_queued_email_waits_for_the_real_commit_on_a_file_database(tmp_path, sent, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models.models import Base
    from app.models.energy_models import EnergyRequestAlert
    from app.services import energy_team
    eng = create_engine("sqlite:///%s" % (tmp_path / "alerts.db"))
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng, autoflush=False)()
    org = Organization(name="File DB", slug="fd-%s" % uuid.uuid4().hex[:6], plan="enterprise",
                       industry="energy", is_active=True)
    db.add(org)
    db.commit()
    mgr = User(organization_id=org.id, email="m-%s@example.com" % uuid.uuid4().hex[:5], password_hash="x",
               full_name="M", role="org_admin", is_active=True, must_change_password=False)
    db.add(mgr)
    db.commit()
    monkeypatch.setattr("app.services.email_service.describe_email_sender",
                        lambda db_, org_id: {"ready": True, "from_email": "team@atlantis.example.test", "reason": None})
    monkeypatch.setenv("OUTBOUND_EMAIL_ENERGY_TEAM_ALERT", "true")
    energy_team.save(db, org.id, {"members": {}, "mailbox": {
        "address": "alerts@atlantis.example.test", "verified_at": "2026-10-20T12:00:00Z",
        "verified_address": "alerts@atlantis.example.test", "verified_from": "team@atlantis.example.test"}}, mgr.id)
    db.commit()

    lead = Lead(organization_id=org.id, first_name="Sav", last_name="Point", tier="new_inquiry", status="new")
    db.add(lead)
    db.flush()
    sp = db.begin_nested()                               # exactly what _step does
    out = energy_team.alert_new_request(db, org=org, lead=lead, event_key="website:file:1", origin="website")
    db.flush()
    sp.commit()
    assert out["email"]["state"] == "queued" and sent == []        # savepoint release: nothing yet
    db.commit()                                                       # the request is really saved
    assert [m["to"] for m in sent] == ["alerts@atlantis.example.test"]
    db.expire_all()
    ev = db.query(EnergyRequestAlert).filter_by(event_key="website:file:1").one()
    assert json.loads(ev.details)["email"]["state"] == "attempted"
    db.commit()
    assert len(sent) == 1                                             # once only

    # rolled back, then a later commit: nothing is ever sent for it
    lead2 = Lead(organization_id=org.id, first_name="Roll", last_name="Back", tier="new_inquiry", status="new")
    db.add(lead2)
    db.flush()
    sp = db.begin_nested()
    energy_team.alert_new_request(db, org=org, lead=lead2, event_key="website:file:2", origin="website")
    db.flush()
    sp.commit()
    db.rollback()
    db.add(Lead(organization_id=org.id, first_name="Other", last_name="Work", tier="new_inquiry", status="new"))
    db.commit()
    assert len(sent) == 1

    # two alerts in one transaction: each is sent once at the commit
    for n in (3, 4):
        l = Lead(organization_id=org.id, first_name="Two%d" % n, last_name="Alerts", tier="new_inquiry", status="new")
        db.add(l)
        db.flush()
        energy_team.alert_new_request(db, org=org, lead=l, event_key="website:file:%d" % n, origin="website")
    db.commit()
    assert len(sent) == 3 and all(m["to"] == "alerts@atlantis.example.test" for m in sent)
    db.close()
    eng.dispose()
