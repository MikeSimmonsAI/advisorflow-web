# -*- coding: utf-8 -*-
"""ATLANTIS website -> advisor, the four Phase 1 customer journeys (synthetic).

Each journey posts exactly what public-sites/atlantis-light-and-power posts to
/site/{slug}/inquiry, then checks what an Atlantis advisor would see:

  lead in THIS workspace only -> New on Rate Requests -> request kind/segment
  -> one open follow-up task -> in-app alert to the right person
  (-> Move Concierge record for a move) -> nothing emailed or texted.

All people here are invented (@example.test). No supplier, sender or
carrier is called.
"""
import itertools
import json
import uuid

import pytest

import app.models.energy_models  # noqa: F401  (registers energy_move_requests)
from app.models.energy_models import EnergyMoveRequest
from app.models.models import EmailMessage, Lead, Message, Notification, Organization, User
from app.models.work_models import LeadTask
from app.services import customer_sites as cs
from app.services import energy_site_intake as esi
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


def _org(db, name, industry="energy"):
    o = Organization(name=name, slug="atl-%s" % uuid.uuid4().hex[:8], plan="enterprise",
                     industry=industry, is_active=True)
    db.add(o)
    db.commit()
    return o


def _user(db, org, role, label):
    u = User(organization_id=org.id, email="%s-%d@atl.example.test" % (label, next(_SEQ)),
             password_hash=hash_password("Pass12345!"), full_name=label.title(),
             role=role, is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _h(db, u):
    return {"Authorization": "Bearer %s" % create_access_token(u, db)}


@pytest.fixture()
def w(db_session):
    db = db_session
    org = _org(db, "Atlantis Test Power")
    other = _org(db, "Other Energy Tenant")
    w = dict(db=db, org=org, other=other,
             admin=_user(db, org, "org_admin", "admin"),
             adv=_user(db, org, "advisor", "adv"),
             fadmin=_user(db, other, "org_admin", "fadmin"))
    cs.publish(db, organization=org, slug="atl-site-%d" % next(_SEQ), html=PAGE, title=org.name)
    w["slug"] = db.query(cs.CustomerSite).filter_by(organization_id=org.id).one().slug
    cs.publish(db, organization=other, slug="other-site-%d" % next(_SEQ), html=PAGE, title="o")
    return w


def _base(first, **kw):
    p = {"first_name": first, "last_name": "Synthetic",
         "email": "%s.%d@example.test" % (first.lower(), next(_SEQ)),
         "phone": "(214) 555-%04d" % (1000 + next(_SEQ)), "consent": True,
         "page_url": "https://example.test/site/atlantis", "form_started_at": 1}
    p.update(kw)
    return p


def _post(client, slug, payload):
    r = client.post("/site/%s/inquiry" % slug, json=payload)
    assert r.status_code == 201, r.text
    assert set(r.json()) == {"success", "message"}      # nothing internal leaks to the visitor
    return r


def _only_lead(db, org):
    return db.query(Lead).filter(Lead.organization_id == org.id).one()


def _rr_ids(client, db, user, **params):
    r = client.get("/rate-requests/", headers=_h(db, user), params=params)
    assert r.status_code == 200, r.text
    body = r.json()
    return {i["id"]: i for i in body.get("items", body.get("results", []))}


def _nothing_sent(db):
    assert db.query(Message).count() == 0
    assert db.query(EmailMessage).count() == 0


# ── JOURNEY 1: residential rate review ─────────────────────────────────────

def test_journey_residential_rate_review(client, w):
    db = w["db"]
    p = _base("Rhea", request_type="Compare electricity options",
              service_area="75201", monthly_usage="1,100 kWh")
    _post(client, w["slug"], p)

    lead = _only_lead(db, w["org"])
    cf = json.loads(lead.custom_fields)
    assert lead.tier == "new_inquiry"
    assert cf["segment"] == "Residential" and cf["request_kind"] == "rate_review"
    assert cf["monthly_usage"] == "1,100 kWh" and lead.zip_code == "75201"
    assert "annual_usage_kwh" not in cf          # monthly is never turned into a made-up annual

    # Visible to the advisor's manager on Rate Requests under New.
    row = _rr_ids(client, db, w["admin"], status="new")[lead.id]
    assert row["segment"] == "Residential" and row["status"] == "new"

    # Unassigned -> the workspace's admins are alerted, nobody else.
    notes = db.query(Notification).all()
    assert {n.user_id for n in notes} == {w["admin"].id}
    assert notes[0].lead_id == lead.id and lead.id in notes[0].link
    task = db.query(LeadTask).filter_by(lead_id=lead.id).one()
    assert task.status == "open" and task.source == "website"

    # Repeat visitor: same person, still one open follow-up task.
    _post(client, w["slug"], dict(p, message="Following up"))
    assert db.query(Lead).filter(Lead.organization_id == w["org"].id).count() == 1
    assert db.query(LeadTask).filter_by(lead_id=lead.id, status="open").count() == 1

    # Isolation: the other tenant has nothing and cannot open it.
    assert db.query(Lead).filter(Lead.organization_id == w["other"].id).count() == 0
    assert client.get("/rate-requests/%s" % lead.id,
                      headers=_h(db, w["fadmin"])).status_code == 404
    _nothing_sent(db)


# ── JOURNEY 2: commercial quote, routed to the page's default advisor ──────

def test_journey_commercial_quote_goes_to_default_advisor(client, w):
    db = w["db"]
    site = db.query(cs.CustomerSite).filter_by(slug=w["slug"]).one()
    site.default_owner_user_id = w["adv"].id
    db.commit()
    _post(client, w["slug"], _base("Cole", request_type="Commercial energy procurement",
                                   company="Synthetic Diner LLC", locations="3 sites, 4 meters"))

    lead = _only_lead(db, w["org"])
    cf = json.loads(lead.custom_fields)
    assert lead.assigned_to_id == w["adv"].id and lead.tier == "new_inquiry"
    assert cf["segment"] == "Commercial" and cf["request_kind"] == "commercial"
    assert cf["locations"] == "3 sites, 4 meters"
    assert lead.id in _rr_ids(client, db, w["adv"], status="new")   # the advisor sees their own
    assert {n.user_id for n in db.query(Notification)} == {w["adv"].id}
    assert db.query(LeadTask).filter_by(lead_id=lead.id).one().assigned_to_id == w["adv"].id
    _nothing_sent(db)


# ── JOURNEY 3: moving -> Move Concierge record ─────────────────────────────

def test_journey_move_opens_one_move_concierge_record(client, w):
    db = w["db"]
    p = _base("Milo", request_type="I'm moving", service_area="76102",
              move_date="2026-11-14", services_requested="Electricity, Internet, Security")
    _post(client, w["slug"], p)
    _post(client, w["slug"], p)                                # double-submit

    lead = _only_lead(db, w["org"])
    assert json.loads(lead.custom_fields)["request_kind"] == "move"
    moves = db.query(EnergyMoveRequest).filter_by(lead_id=lead.id).all()
    assert len(moves) == 1
    m = moves[0]
    assert m.organization_id == w["org"].id and m.status == "requested"
    assert str(m.move_date) == "2026-11-14"
    assert json.loads(m.services) == ["electricity_start", "internet", "other"]
    lst = client.get("/energy-ops/moves?status=open", headers=_h(db, w["admin"])).json()
    assert m.id in {i["id"] for i in lst["items"]}
    assert lead.id in _rr_ids(client, db, w["admin"], status="new")
    _nothing_sent(db)


# ── JOURNEY 4: partner referral ────────────────────────────────────────────

def test_journey_partner_referral(client, w):
    db = w["db"]
    _post(client, w["slug"], _base("Paz", request_type="Partner / referral program",
                                   company="Synthetic Realty",
                                   message="Referring a buyer closing next month."))
    lead = _only_lead(db, w["org"])
    cf = json.loads(lead.custom_fields)
    assert cf["request_kind"] == "partner"
    assert "segment" not in cf                 # the partner is not the customer
    assert lead.id in _rr_ids(client, db, w["admin"], status="new")
    assert db.query(Notification).filter_by(user_id=w["admin"].id).count() == 1
    assert db.query(EnergyMoveRequest).count() == 0
    _nothing_sent(db)


# ── edges ──────────────────────────────────────────────────────────────────

def test_a_lead_someone_advanced_is_not_moved_back(client, w):
    db = w["db"]
    p = _base("Ada", request_type="Compare electricity options")
    _post(client, w["slug"], p)
    lead = _only_lead(db, w["org"])
    lead.tier = "proposal_sent"
    db.commit()
    _post(client, w["slug"], p)
    db.refresh(lead)
    assert lead.tier == "proposal_sent"


def test_a_non_energy_workspace_is_untouched(client, db_session):
    db = db_session
    org = _org(db, "Plain Co", industry="insurance")
    _user(db, org, "org_admin", "padmin")
    cs.publish(db, organization=org, slug="plain-site-%d" % next(_SEQ), html=PAGE, title="p")
    slug = db.query(cs.CustomerSite).filter_by(organization_id=org.id).one().slug
    _post(client, slug, _base("Nia", request_type="Compare electricity options"))
    lead = _only_lead(db, org)
    assert lead.tier != "new_inquiry"
    assert db.query(Notification).count() == 0 and db.query(LeadTask).count() == 0


def test_a_failed_follow_up_step_never_loses_the_request(client, w, monkeypatch):
    db = w["db"]
    monkeypatch.setattr(esi, "move_services", lambda _t: (_ for _ in ()).throw(RuntimeError("x")))
    _post(client, w["slug"], _base("Fay", request_type="I'm moving", services_requested="TV"))
    lead = _only_lead(db, w["org"])
    assert lead.tier == "new_inquiry"
    assert db.query(EnergyMoveRequest).count() == 0             # the broken step is skipped
    assert db.query(Notification).count() == 1                  # the rest still happens


def test_request_kind_mapping():
    assert esi.request_kind({"request_type": "Analyze my current bill"}) == "bill_review"
    assert esi.request_kind({"request_type": "something else"}) == "advisor"
    assert esi.move_services("Electricity, TV, Home Warranty") == ["electricity_start", "internet", "other"]


# ── supplier hand-off (manual; nothing is sent to any supplier) ─────────────

def test_residential_request_handed_off_records_and_opens_follow_up(client, w):
    db = w["db"]
    _post(client, w["slug"], _base("Hal", request_type="Compare electricity options"))
    lead = _only_lead(db, w["org"])
    cat = client.get("/energy-ops/suppliers", headers=_h(db, w["admin"])).json()["suppliers"]
    chariot = next(s for s in cat if s["key"] == "chariot")
    assert chariot["online_enrollment"] is False            # Phase 2 switch stays off
    r = client.post("/energy-ops/leads/%s/handoff" % lead.id, headers=_h(db, w["admin"]),
                    json={"supplier": "comparepower", "note": "Sent link"})
    assert r.status_code == 200, r.text
    db.refresh(lead)
    cf = json.loads(lead.custom_fields)
    assert cf["supplier_handoffs"][0]["supplier"] == "comparepower"
    assert lead.tier == "new_inquiry"                      # a hand-off is not an enrollment
    titles = {t.title for t in db.query(LeadTask).filter_by(lead_id=lead.id)}
    assert "Confirm outcome with ComparePower" in titles
    _nothing_sent(db)


def test_business_cannot_be_handed_to_residential_only_chariot(client, w):
    db = w["db"]
    _post(client, w["slug"], _base("Bo", request_type="Commercial energy procurement",
                                   company="Synthetic Shop", locations="1"))
    lead = _only_lead(db, w["org"])
    h = _h(db, w["admin"])
    assert client.post("/energy-ops/leads/%s/handoff" % lead.id, headers=h,
                       json={"supplier": "chariot"}).status_code == 409
    assert client.post("/energy-ops/leads/%s/handoff" % lead.id, headers=h,
                       json={"supplier": "gexa"}).status_code == 200


def test_handoff_is_scoped_to_the_workspace_and_the_owner(client, w):
    db = w["db"]
    _post(client, w["slug"], _base("Ivy", request_type="Compare electricity options"))
    lead = _only_lead(db, w["org"])
    body = {"supplier": "gexa"}
    assert client.post("/energy-ops/leads/%s/handoff" % lead.id, headers=_h(db, w["fadmin"]),
                       json=body).status_code == 404       # other tenant
    assert client.post("/energy-ops/leads/%s/handoff" % lead.id, headers=_h(db, w["adv"]),
                       json=body).status_code in (403, 404)  # not this advisor's customer
    assert client.post("/energy-ops/leads/%s/handoff" % lead.id, headers=_h(db, w["admin"]),
                       json={"supplier": "nobody"}).status_code == 422
