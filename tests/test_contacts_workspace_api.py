# -*- coding: utf-8 -*-
"""Workspace contacts API (Atlantis contract, 2026-09-28).

    GET  /intake/contacts            filters, search, sort, pagination
    GET  /intake/contacts/summary    new counts
    GET  /intake/contacts/{id}       provenance, contactability, lead, history
    POST /intake/contacts/{id}/promote   explicit contact -> lead
    GET  /leads/workspace-summary    truthful workspace numbers (null = unknown)

Every read and write is on the ACTING workspace only; a foreign id is 404.
Promotion never grants consent, never enrolls, never sends.
"""
import itertools
import json
import uuid
from datetime import datetime, timedelta

import pytest

from app.models.billing_models import BrandBillingPlan
from app.models.import_models import ImportBatch, ImportStagedRow
from app.models.intake_models import ImportRecordVersion, OrgContact, OrgContactSourceId
from app.models.models import (AuditLogEntry, BookingLink, CadenceState, Lead, Message,
                               Organization, PipelineConversation, Platform, User,
                               UserCapabilityGrant)
from app.models.sales_models import Membership
from app.services.auth_service import create_access_token, hash_password
from app.services.workspace_access import SCOPE_CUSTOMER_ORG, WORKSPACE_HEADER

_SEQ = itertools.count(1)


# ── builders ────────────────────────────────────────────────────────────────

def _org(db, name, industry="energy", **kw):
    o = Organization(name=name, slug="cw-%s" % uuid.uuid4().hex[:8], plan=kw.pop("plan", "enterprise"),
                     industry=industry, is_active=True, **kw)
    db.add(o)
    db.commit()
    return o


def _user(db, org, role, label="u"):
    u = User(organization_id=org.id if org else None,
             email="%s-%d-%s@cw.test" % (label, next(_SEQ), uuid.uuid4().hex[:4]),
             password_hash=hash_password("Pass12345!"), full_name=label.title(),
             role=role, is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _member(db, user, org, role):
    db.add(Membership(user_id=user.id, scope_type=SCOPE_CUSTOMER_ORG, scope_id=org.id,
                      role=role, is_active=True))
    db.commit()


def _grant(db, user, org, cap):
    db.add(UserCapabilityGrant(user_id=user.id, organization_id=org.id, capability=cap,
                               scope_type=SCOPE_CUSTOMER_ORG, scope_id=org.id,
                               is_active=True))
    db.commit()


def _h(db, user, workspace=None, override=None):
    h = {"Authorization": "Bearer " + create_access_token(user, db)}
    if workspace is not None:
        h[WORKSPACE_HEADER] = workspace.id
    if override is not None:
        h["X-Org-Override"] = override.id
    return h


def _contact(db, org, **kw):
    n = next(_SEQ)
    kw.setdefault("first_name", "Pat%d" % n)
    kw.setdefault("last_name", "Person")
    c = OrgContact(organization_id=org.id, **kw)
    db.add(c)
    db.commit()
    return c


def _batch(db, org, code="ATL-20260928-001", filename="hubspot.csv"):
    b = ImportBatch(organization_id=org.id, source_type="csv", source_filename=filename,
                    status="committed", pipeline="universal", batch_code=code,
                    acting_user_name="Importer Ian", source_label="HubSpot")
    db.add(b)
    db.commit()
    return b


def _lead(db, org, **kw):
    kw.setdefault("first_name", "Lee")
    kw.setdefault("last_name", "Lead%d" % next(_SEQ))
    kw.setdefault("status", "new")
    lead = Lead(organization_id=org.id, **kw)
    db.add(lead)
    db.commit()
    return lead


@pytest.fixture()
def world(db_session):
    db = db_session
    a = _org(db, "Atlantis Energy")
    b = _org(db, "Other Customer")
    admin_a = _user(db, a, "org_admin", "admina")
    admin_b = _user(db, b, "org_admin", "adminb")
    return {"db": db, "a": a, "b": b, "admin_a": admin_a, "admin_b": admin_b}


def _get(client, h, **params):
    r = client.get("/intake/contacts", headers=h, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _ids(body):
    return {c["id"] for c in body["contacts"]}


# ── list: filters ───────────────────────────────────────────────────────────

@pytest.fixture()
def populated(world):
    db, a, b = world["db"], world["a"], world["b"]
    bt = _batch(db, a)
    c = {
        "ann": _contact(db, a, first_name="Ann", last_name="Archer", company="Acme Widgets",
                        company_norm="acme widgets", email="ann@acme.test", email_status="ready",
                        phone="+12145550101", sms_status="ready", record_class="customer",
                        classification="win_back", historical_customer=True,
                        import_batch_id=bt.id, source="HubSpot", source_system="hubspot",
                        source_record_id="101"),
        "bob": _contact(db, a, first_name="Bob", last_name="Baker", company="Zeta Corp",
                        company_norm="zeta corp", mobile_phone="+14695550199",
                        sms_status="invalid", record_class="contact",
                        classification="general"),
        "cy": _contact(db, a, first_name="Cy", last_name="Cole", lifecycle="needs_enrichment",
                       record_class="contact", historical_customer=False),
        "dee": _contact(db, a, first_name="Dee", last_name="Dunn", email="dee@x.test",
                        email_status="unsubscribed", phone="+19725550123",
                        phone_line_type="mobile", record_class="previous_customer"),
        "arch": _contact(db, a, first_name="Old", last_name="Archived", lifecycle="archived",
                         archived_at=datetime.utcnow()),
        "foreign": _contact(db, b, first_name="Ann", last_name="Archer", company="Acme Widgets",
                            email="ann@other.test", phone="+12145550101"),
    }
    lead = _lead(db, a, org_contact_id=c["dee"].id)
    c["dee"].lead_id = lead.id
    db.commit()
    world.update(c=c, batch=bt, dee_lead=lead)
    return world


def test_list_is_tenant_scoped_and_shaped(client, populated):
    w = populated
    body = _get(client, _h(w["db"], w["admin_a"]))
    assert body["total"] == 4 and body["page"] == 1 and body["per_page"] == 50
    assert w["c"]["foreign"].id not in _ids(body) and w["c"]["arch"].id not in _ids(body)
    ann = [x for x in body["contacts"] if x["id"] == w["c"]["ann"].id][0]
    for k in ("id", "first_name", "last_name", "full_name", "company", "email", "phone",
              "mobile_phone", "street_address", "city", "state", "zip_code", "record_class",
              "classification", "lifecycle", "needs_enrichment", "sms_status", "email_status",
              "historical_customer", "lead_id", "source", "source_detail", "source_system",
              "source_record_id", "import_batch_id", "batch_code", "created_at"):
        assert k in ann, k
    assert ann["full_name"] == "Ann Archer" and ann["batch_code"] == "ATL-20260928-001"
    bob = [x for x in body["contacts"] if x["id"] == w["c"]["bob"].id][0]
    assert bob["phone"] is None and bob["mobile_phone"] == "+14695550199"


@pytest.mark.parametrize("params,expect", [
    ({"record_class": "customer"}, {"ann"}),
    ({"classification": "general"}, {"bob"}),
    ({"lifecycle": "needs_enrichment"}, {"cy"}),
    ({"lifecycle": "archived"}, {"arch"}),
    ({"has_email": "true"}, {"ann", "dee"}),
    ({"has_email": "false"}, {"bob", "cy"}),
    ({"has_phone": "true"}, {"ann", "bob", "dee"}),
    ({"has_phone": "false"}, {"cy"}),
    ({"email_ready": "true"}, {"ann"}),
    ({"email_ready": "false"}, {"bob", "cy", "dee"}),
    ({"needs_enrichment": "true"}, {"cy"}),
    ({"needs_enrichment": "false"}, {"ann", "bob", "dee"}),
    ({"historical_customer": "true"}, {"ann"}),
    ({"historical_customer": "false"}, {"bob", "cy", "dee"}),
    ({"promoted": "true"}, {"dee"}),
    ({"promoted": "false"}, {"ann", "bob", "cy"}),
])
def test_each_filter(client, populated, params, expect):
    w = populated
    body = _get(client, _h(w["db"], w["admin_a"]), **params)
    assert _ids(body) == {w["c"][k].id for k in expect}
    assert body["total"] == len(expect)


def test_batch_filter(client, populated):
    w = populated
    body = _get(client, _h(w["db"], w["admin_a"]), batch_id=w["batch"].id)
    assert _ids(body) == {w["c"]["ann"].id}


@pytest.mark.parametrize("term,expect", [
    ("archer", {"ann"}),            # last name, case-insensitive
    ("ANN", {"ann"}),               # first name
    ("ann archer", {"ann"}),        # full name
    ("zeta", {"bob"}),              # company
    ("dee@x", {"dee"}),             # email
    ("(214) 555-0101", {"ann"}),    # phone digits vs E.164
    ("469.555", {"bob"}),           # mobile digits
    ("+1972", {"dee"}),             # raw E.164 fragment
    ("100%", set()),                # LIKE wildcards are literal
])
def test_search(client, populated, term, expect):
    w = populated
    body = _get(client, _h(w["db"], w["admin_a"]), search=term)
    assert _ids(body) == {w["c"][k].id for k in expect}


def test_search_never_crosses_tenants(client, populated):
    w = populated
    body = _get(client, _h(w["db"], w["admin_b"]), search="archer")
    assert _ids(body) == {w["c"]["foreign"].id}


def test_pagination_and_sort(client, world):
    db, a = world["db"], world["a"]
    names = ["Zed", "Amy", "Kim", "Bea", "Lou"]
    for i, n in enumerate(names):
        c = _contact(db, a, first_name="X", last_name=n, company="Co %s" % n,
                     company_norm="co %s" % n.lower())
        c.created_at = datetime(2026, 9, 1) + timedelta(days=i)
    db.commit()
    h = _h(db, world["admin_a"])
    p1 = _get(client, h, per_page=2, page=1)
    p3 = _get(client, h, per_page=2, page=3)
    assert p1["total"] == 5 and len(p1["contacts"]) == 2 and len(p3["contacts"]) == 1
    assert [c["last_name"] for c in p1["contacts"]] == ["Lou", "Bea"]      # recent first
    by_name = _get(client, h, sort="name", per_page=200)
    assert [c["last_name"] for c in by_name["contacts"]] == sorted(names)
    by_co = _get(client, h, sort="company", per_page=200)
    assert [c["company"] for c in by_co["contacts"]] == sorted("Co %s" % n for n in names)
    assert client.get("/intake/contacts", headers=h, params={"sort": "bogus"}).status_code == 422
    assert client.get("/intake/contacts", headers=h, params={"per_page": 201}).status_code == 422
    assert client.get("/intake/contacts", headers=h, params={"page": 0}).status_code == 422


# ── summary ─────────────────────────────────────────────────────────────────

def test_summary_new_fields_and_existing_keys(client, populated):
    w = populated
    s = client.get("/intake/contacts/summary", headers=_h(w["db"], w["admin_a"])).json()
    for k in ("contacts", "active_leads", "customers", "previous_customers", "renewals",
              "by_record_class", "sms_ready", "email_ready", "needs_enrichment",
              "historical_customers"):
        assert k in s, k
    assert s["contacts"] == 4
    assert s["with_email"] == 2          # ann, dee
    assert s["with_phone"] == 3          # ann, bob(mobile), dee
    assert s["valid_phones"] == 2        # bob's number is invalid
    assert s["mobile"] == 2              # bob (mobile column), dee (line type)
    assert s["promoted"] == 1            # dee
    sb = client.get("/intake/contacts/summary", headers=_h(w["db"], w["admin_b"])).json()
    assert sb["contacts"] == 1 and sb["promoted"] == 0


# ── detail ──────────────────────────────────────────────────────────────────

def test_detail_provenance_contactability_history(client, populated):
    w = populated
    db, ann, bt = w["db"], w["c"]["ann"], w["batch"]
    ann.custom_fields = json.dumps({"account_no": "A-1"})
    ann.source_fields = json.dumps({"Owner": "Sam"})
    db.add(OrgContactSourceId(organization_id=w["a"].id, org_contact_id=ann.id,
                              source_system="hubspot", source_record_id="101",
                              import_batch_id=bt.id, is_primary=True))
    db.add(ImportRecordVersion(organization_id=w["a"].id, batch_id=bt.id,
                               target_type="org_contact", target_id=ann.id, action="created"))
    db.commit()
    r = client.get(f"/intake/contacts/{ann.id}", headers=_h(db, w["admin_a"]))
    assert r.status_code == 200, r.text
    d = r.json()
    for k in ("address_line2", "country", "job_title", "owner_name", "last_activity_date",
              "custom_fields", "vertical_fields", "source_fields", "alternate_source_ids",
              "created_at", "updated_at"):
        assert k in d["contact"], k
    assert d["contact"]["custom_fields"] == {"account_no": "A-1"}
    assert d["contact"]["alternate_source_ids"][0]["source_record_id"] == "101"
    p = d["provenance"]
    assert p["batch_code"] == "ATL-20260928-001" and p["batch_filename"] == "hubspot.csv"
    assert p["imported_by_name"] == "Importer Ian" and p["imported_at"]
    assert p["source_system"] == "hubspot" and p["source_record_id"] == "101"
    ct = d["contactability"]
    # sms_status "ready" and a phone present are NOT consent.
    assert ct["sms_status"] == "ready" and ct["phone_present"] is True
    assert ct["sms_consent"] is False
    assert ct["note"] == "Having a phone number is not SMS permission."
    assert d["lead"] is None
    assert any(h["action"] == "import.created" for h in d["history"])


def test_detail_consent_true_only_with_real_evidence(client, populated):
    w = populated
    db, dee, lead = w["db"], w["c"]["dee"], w["dee_lead"]
    h = _h(db, w["admin_a"])
    lead.sms_consent = True     # a flag without a recorded opt-in is not evidence
    db.commit()
    assert client.get(f"/intake/contacts/{dee.id}", headers=h).json()[
        "contactability"]["sms_consent"] is False
    lead.sms_consent_timestamp = datetime.utcnow()
    db.commit()
    d = client.get(f"/intake/contacts/{dee.id}", headers=h).json()
    assert d["contactability"]["sms_consent"] is True
    assert d["lead"]["id"] == lead.id


def test_detail_cross_tenant_and_unknown_are_404(client, populated):
    w = populated
    h = _h(w["db"], w["admin_a"])
    assert client.get(f"/intake/contacts/{w['c']['foreign'].id}", headers=h).status_code == 404
    assert client.get("/intake/contacts/does-not-exist", headers=h).status_code == 404
    # the summary route still wins over /{contact_id}
    assert client.get("/intake/contacts/summary", headers=h).status_code == 200


# ── promote ─────────────────────────────────────────────────────────────────

_CONTACT_FIELDS = ("first_name", "last_name", "email", "phone", "mobile_phone", "company",
                   "record_class", "classification", "lifecycle", "sms_status",
                   "email_status", "historical_customer", "source", "import_batch_id",
                   "custom_fields", "updated_at", "archived_at")


def test_promote_happy_path(client, populated):
    w = populated
    db, ann, a = w["db"], w["c"]["ann"], w["a"]
    before = {f: getattr(ann, f) for f in _CONTACT_FIELDS}
    r = client.post(f"/intake/contacts/{ann.id}/promote", headers=_h(db, w["admin_a"]),
                    json={"note": "Asked for a rate review"})
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["contact_id"] == ann.id and out["tier"] == "new_inquiry"
    assert out["held_over_capacity"] is False
    db.expire_all()
    lead = db.query(Lead).filter(Lead.id == out["lead_id"]).one()
    c = db.query(OrgContact).filter(OrgContact.id == ann.id).one()
    assert lead.organization_id == a.id and lead.org_contact_id == ann.id
    assert c.lead_id == lead.id
    assert {f: getattr(c, f) for f in _CONTACT_FIELDS} == before
    assert lead.first_name == "Ann" and lead.email == "ann@acme.test"
    assert lead.source == "contact_promotion"
    # not stamped: capture reads Lead.import_batch_id as "created by that batch"
    assert lead.import_batch_id is None
    assert lead.status == "new" and lead.tier == "new_inquiry"
    assert lead.assigned_to_id == w["admin_a"].id
    # no consent / permission granted
    assert not lead.sms_consent and lead.sms_consent_timestamp is None
    assert lead.allow_sms is None and lead.allow_email is None
    assert lead.allow_bulk_email is None and lead.allow_voice is None
    assert lead.message_track is None
    # nothing enrolled, queued or sent
    assert db.query(CadenceState).filter(CadenceState.lead_id == lead.id).count() == 0
    assert db.query(Message).filter(Message.lead_id == lead.id).count() == 0
    assert db.query(PipelineConversation).filter(
        PipelineConversation.lead_id == lead.id).count() == 0
    ev = db.query(AuditLogEntry).filter(AuditLogEntry.action == "contact.promoted_to_lead").all()
    assert len(ev) == 1 and ev[0].organization_id == a.id
    assert ev[0].target_type == "org_contact" and ev[0].target_id == ann.id
    det = json.loads(ev[0].details)
    assert det["lead_id"] == lead.id and det["tier"] == "new_inquiry"
    # the detail view now shows the lead and the promotion in history
    d = client.get(f"/intake/contacts/{ann.id}", headers=_h(db, w["admin_a"])).json()
    assert d["lead"]["id"] == lead.id
    assert d["history"][0]["action"] == "contact.promoted_to_lead"
    assert d["contactability"]["sms_consent"] is False


def test_promote_carries_denials_never_grants(client, populated):
    w = populated
    db = w["db"]
    c = _contact(db, w["a"], email="no@x.test", email_status="unsubscribed",
                 phone="+12145550999", sms_status="opted_out")
    r = client.post(f"/intake/contacts/{c.id}/promote", headers=_h(db, w["admin_a"]), json={})
    assert r.status_code == 201, r.text
    lead = db.query(Lead).filter(Lead.id == r.json()["lead_id"]).one()
    assert lead.allow_email is False and lead.allow_sms is False
    assert not lead.sms_consent


def test_promote_twice_is_409_with_lead_id(client, populated):
    w = populated
    db, ann = w["db"], w["c"]["ann"]
    h = _h(db, w["admin_a"])
    first = client.post(f"/intake/contacts/{ann.id}/promote", headers=h, json={})
    assert first.status_code == 201
    again = client.post(f"/intake/contacts/{ann.id}/promote", headers=h, json={})
    assert again.status_code == 409
    assert again.json()["lead_id"] == first.json()["lead_id"] and again.json()["detail"]
    # a contact that was already linked before
    dee = client.post(f"/intake/contacts/{w['c']['dee'].id}/promote", headers=h, json={})
    assert dee.status_code == 409 and dee.json()["lead_id"] == w["dee_lead"].id
    assert db.query(Lead).filter(Lead.org_contact_id == ann.id).count() == 1


def test_promote_cross_tenant_is_404_and_writes_nothing(client, populated):
    w = populated
    db = w["db"]
    n = db.query(Lead).count()
    r = client.post(f"/intake/contacts/{w['c']['foreign'].id}/promote",
                    headers=_h(db, w["admin_a"]), json={})
    assert r.status_code == 404
    assert db.query(Lead).count() == n
    db.expire_all()
    assert db.query(OrgContact).filter(OrgContact.id == w["c"]["foreign"].id).one().lead_id is None


def test_promote_refused_without_lead_create_permission(client, populated):
    w = populated
    db, a = w["db"], w["a"]
    viewer = _user(db, a, "viewer", "viewer")
    _grant(db, viewer, a, "lead_import_review")      # may SEE contacts
    h = _h(db, viewer)
    assert client.get("/intake/contacts", headers=h).status_code == 200
    r = client.post(f"/intake/contacts/{w['c']['ann'].id}/promote", headers=h, json={})
    assert r.status_code == 403
    assert db.query(Lead).filter(Lead.org_contact_id == w["c"]["ann"].id).count() == 0


def test_promote_permission_is_workspace_aware(client, populated):
    """org_admin at home, only a viewer of the workspace they are standing in."""
    w = populated
    db, a = w["db"], w["a"]
    home = _org(db, "Home Co")
    split = _user(db, home, "org_admin", "split")
    _member(db, split, home, "org_admin")
    _member(db, split, a, "viewer")
    r = client.post(f"/intake/contacts/{w['c']['ann'].id}/promote",
                    headers=_h(db, split, workspace=a), json={})
    assert r.status_code == 403
    # ...and a real workspace admin (advisor at home) is allowed there
    boss = _user(db, home, "advisor", "boss")  # org_admin of `a`: review by role
    _member(db, boss, home, "advisor")
    _member(db, boss, a, "org_admin")
    r = client.post(f"/intake/contacts/{w['c']['ann'].id}/promote",
                    headers=_h(db, boss, workspace=a), json={})
    assert r.status_code == 201, r.text
    lead = db.query(Lead).filter(Lead.id == r.json()["lead_id"]).one()
    assert lead.organization_id == a.id
    ev = db.query(AuditLogEntry).filter(AuditLogEntry.action == "contact.promoted_to_lead").one()
    assert ev.organization_id == a.id and ev.actor_user_id == boss.id


def test_advisor_promotes_for_self_only(client, populated):
    w = populated
    db, a = w["db"], w["a"]
    adv = _user(db, a, "advisor", "adv")
    other = _user(db, a, "advisor", "other")
    _grant(db, adv, a, "lead_import_review")
    h = _h(db, adv)
    r = client.post(f"/intake/contacts/{w['c']['bob'].id}/promote", headers=h,
                    json={"assigned_to_id": other.id})
    assert r.status_code == 403
    r = client.post(f"/intake/contacts/{w['c']['bob'].id}/promote", headers=h, json={})
    assert r.status_code == 201, r.text
    assert db.query(Lead).filter(Lead.id == r.json()["lead_id"]).one().assigned_to_id == adv.id


def test_promote_tier_and_assignee_validation(client, populated):
    w = populated
    db, a = w["db"], w["a"]
    h = _h(db, w["admin_a"])
    cid = w["c"]["ann"].id
    bad = client.post(f"/intake/contacts/{cid}/promote", headers=h, json={"tier": "pre_need"})
    assert bad.status_code in (400, 422)
    assert "new_inquiry" in json.dumps(bad.json())
    outsider = w["admin_b"]
    r = client.post(f"/intake/contacts/{cid}/promote", headers=h,
                    json={"assigned_to_id": outsider.id})
    assert r.status_code == 400
    assert db.query(Lead).filter(Lead.org_contact_id == cid).count() == 0
    rep = _user(db, a, "advisor", "rep")
    r = client.post(f"/intake/contacts/{cid}/promote", headers=h,
                    json={"tier": "rate_review", "assigned_to_id": rep.id})
    assert r.status_code == 201, r.text
    lead = db.query(Lead).filter(Lead.id == r.json()["lead_id"]).one()
    assert lead.tier == "rate_review" and lead.assigned_to_id == rep.id


def test_promote_uses_org_vertical_default_tier(client, db_session):
    db = db_session
    fh = _org(db, "Funeral Home", industry="funeral")
    admin = _user(db, fh, "org_admin", "fh")
    c = _contact(db, fh, email="f@x.test")
    r = client.post(f"/intake/contacts/{c.id}/promote", headers=_h(db, admin), json={})
    assert r.status_code == 201, r.text
    assert r.json()["tier"] == "pre_need"
    bad = client.post(f"/intake/contacts/{_contact(db, fh).id}/promote",
                      headers=_h(db, admin), json={"tier": "contract_signed"})
    assert bad.status_code in (400, 422)


def test_promote_over_capacity_holds_not_refuses(client, db_session):
    db = db_session
    p = Platform(name="CapBrand", slug="capb-%s" % uuid.uuid4().hex[:6])
    db.add(p)
    db.commit()
    db.add(BrandBillingPlan(platform_id=p.id, key="tiny", name="Tiny", monthly_cents=100,
                            currency="usd", is_purchasable=True, is_active=True,
                            sort_order=1, max_leads=1))
    db.commit()
    org = _org(db, "Capped Energy", plan="tiny", platform_id=p.id,
               billing_plan_key="tiny", billing_status="active")
    admin = _user(db, org, "org_admin", "cap")
    _lead(db, org)                                   # the one seat is used
    c = _contact(db, org, phone="+12145550777")
    r = client.post(f"/intake/contacts/{c.id}/promote", headers=_h(db, admin), json={})
    assert r.status_code == 201, r.text
    assert r.json()["held_over_capacity"] is True
    lead = db.query(Lead).filter(Lead.id == r.json()["lead_id"]).one()
    assert lead.capacity_state == "over_capacity"
    db.expire_all()
    assert db.query(OrgContact).filter(OrgContact.id == c.id).one().lead_id == lead.id


def test_rollback_keeps_a_contact_a_person_promoted(client, populated):
    from app.services.intake import rollback as RB
    from app.services.intake.context import IntakeContext
    w = populated
    db, ann, bt = w["db"], w["c"]["ann"], w["batch"]
    db.add(ImportRecordVersion(organization_id=w["a"].id, batch_id=bt.id,
                               target_type="org_contact", target_id=ann.id, action="created"))
    db.commit()
    r = client.post(f"/intake/contacts/{ann.id}/promote", headers=_h(db, w["admin_a"]), json={})
    assert r.status_code == 201
    db.expire_all()
    item = [i for i in RB.plan(db, bt)["items"] if i["target_id"] == ann.id][0]
    assert item["outcome"] == "keep" and "promoted" in item["reason"]
    ctx = IntakeContext(org_id=w["a"].id, org_name="Atlantis Energy", org_slug=None,
                        actor_id=w["admin_a"].id, actor_name="Admin", actor_email=None,
                        role="org_admin", acting_as_platform_owner=False)
    RB.execute(db, bt, ctx)
    db.commit()
    db.expire_all()
    c = db.query(OrgContact).filter(OrgContact.id == ann.id).one()
    assert c.archived_at is None and c.lifecycle == "active"
    assert c.lead_id == r.json()["lead_id"]
    assert db.query(Lead).filter(Lead.id == c.lead_id).count() == 1


def test_promote_refuses_do_not_contact(client, populated):
    w = populated
    db = w["db"]
    c = _contact(db, w["a"], phone="+12145550888", sms_status="dnc")
    r = client.post(f"/intake/contacts/{c.id}/promote", headers=_h(db, w["admin_a"]), json={})
    assert r.status_code == 409 and r.json()["detail"] == "do_not_contact"
    assert db.query(Lead).filter(Lead.org_contact_id == c.id).count() == 0


def _staged(db, org, batch, contact, n, **consent):
    row = ImportStagedRow(batch_id=batch.id, organization_id=org.id, row_number=n,
                          committed_contact_id=contact.id, **consent)
    db.add(row)
    db.commit()
    return row


def test_promote_carries_source_denials_from_the_staged_row(client, populated):
    w = populated
    db, a, bt = w["db"], w["a"], w["batch"]
    c = _contact(db, a, phone="+12145550444", email="d@x.test", import_batch_id=bt.id)
    old = _staged(db, a, bt, c, 1, consent_voice=True, consent_bulk_email=True)
    old.created_at = datetime(2026, 1, 1)
    db.commit()
    _staged(db, a, bt, c, 2, consent_voice=False, consent_bulk_email=False,
            consent_sms=True, consent_email=None)
    r = client.post(f"/intake/contacts/{c.id}/promote", headers=_h(db, w["admin_a"]), json={})
    assert r.status_code == 201, r.text
    lead = db.query(Lead).filter(Lead.id == r.json()["lead_id"]).one()
    assert lead.allow_voice is False and lead.allow_bulk_email is False
    # a source "yes" is never carried as a grant
    assert lead.allow_sms is None and lead.allow_email is None and not lead.sms_consent


def test_promote_suppressed_denies_voice_and_bulk(client, populated):
    w = populated
    db = w["db"]
    c = _contact(db, w["a"], phone="+12145550333", sms_status="suppressed")
    r = client.post(f"/intake/contacts/{c.id}/promote", headers=_h(db, w["admin_a"]), json={})
    assert r.status_code == 201, r.text
    lead = db.query(Lead).filter(Lead.id == r.json()["lead_id"]).one()
    assert lead.allow_sms is False and lead.allow_voice is False
    assert lead.allow_bulk_email is False and lead.allow_email is None


def test_promote_requires_import_review(client, populated):
    w = populated
    db, a = w["db"], w["a"]
    adv = _user(db, a, "advisor", "nogrant")
    r = client.post(f"/intake/contacts/{w['c']['bob'].id}/promote", headers=_h(db, adv), json={})
    assert r.status_code == 403
    assert db.query(Lead).filter(Lead.org_contact_id == w["c"]["bob"].id).count() == 0


def test_409_names_the_lead_only_to_someone_who_can_see_it(client, populated):
    w = populated
    db, a = w["db"], w["a"]
    adv = _user(db, a, "advisor", "seer")
    _grant(db, adv, a, "lead_import_review")
    # dee's lead is unassigned: outside an advisor's scope
    r = client.post(f"/intake/contacts/{w['c']['dee'].id}/promote", headers=_h(db, adv), json={})
    assert r.status_code == 409 and "lead_id" not in r.json()
    w["dee_lead"].assigned_to_id = adv.id
    db.commit()
    r = client.post(f"/intake/contacts/{w['c']['dee'].id}/promote", headers=_h(db, adv), json={})
    assert r.status_code == 409 and r.json()["lead_id"] == w["dee_lead"].id


# ── workspace summary ───────────────────────────────────────────────────────

def test_workspace_summary_numbers(client, world):
    db, a, b = world["db"], world["a"], world["b"]
    admin = world["admin_a"]
    now = datetime.utcnow()
    l1 = _lead(db, a, tier="new_inquiry", source="web", assigned_to_id=admin.id)
    _lead(db, a, tier="contract_signed", source="web", last_messaged_at=now)
    _lead(db, a, tier="rate_review", status="dnc", source="import")
    _lead(db, a, tier="rate_review", status="not_interested")
    # internal test record: identical shape, must never count
    _lead(db, a, tier="contract_signed", source="web", is_test=True, last_messaged_at=now)
    # another tenant's lead: never counted
    _lead(db, b, tier="contract_signed", source="web")
    db.add(BookingLink(lead_id=l1.id, user_id=admin.id, status="booked",
                       booked_time=now + timedelta(days=2)))
    db.add(BookingLink(lead_id=l1.id, user_id=admin.id, status="booked",
                       booked_time=now - timedelta(days=2)))      # past: not upcoming
    db.commit()
    _contact(db, a)
    _contact(db, a, lead_id=l1.id)
    _contact(db, b)
    r = client.get("/leads/workspace-summary", headers=_h(db, admin))
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["total_leads"] == 4
    assert s["lost"] == 2 and s["active_leads"] == 2
    assert s["by_tier"]["contract_signed"] == 1 and s["by_tier"]["rate_review"] == 2
    assert s["by_tier"]["proposal_sent"] == 0
    assert [t["key"] for t in s["tiers"]][:5] == ["new_inquiry", "rate_review",
                                                   "proposal_sent", "contract_signed",
                                                   "renewal_due"]
    assert s["under_contract"] == 1
    assert s["closed_won"] is None
    assert s["appointments_upcoming"] == 1
    assert s["recently_active_7d"] == 1
    assert s["total_contacts"] == 2 and s["contacts_not_promoted"] == 1
    assert {x["source"]: x["count"] for x in s["sources_30d"]} == {
        "web": 2, "import": 1, "unknown": 1}
    assert s["generated_at"]


def test_workspace_summary_null_when_vertical_has_no_contract_tier(client, db_session):
    db = db_session
    dental = _org(db, "Smile Dental", industry="dental")
    admin = _user(db, dental, "org_admin", "dent")
    _lead(db, dental, tier="new_patient")
    s = client.get("/leads/workspace-summary", headers=_h(db, admin)).json()
    assert s["under_contract"] is None and s["closed_won"] is None
    assert s["total_leads"] == 1


def test_workspace_summary_follows_selected_workspace(client, world):
    db, a, b = world["db"], world["a"], world["b"]
    _lead(db, a)
    _lead(db, b)
    _lead(db, b)
    dual = _user(db, a, "org_admin", "dual")
    _member(db, dual, a, "org_admin")
    _member(db, dual, b, "org_admin")
    sa = client.get("/leads/workspace-summary", headers=_h(db, dual)).json()
    sb = client.get("/leads/workspace-summary", headers=_h(db, dual, workspace=b)).json()
    assert sa["total_leads"] == 1 and sa["organization_id"] == a.id
    assert sb["total_leads"] == 2 and sb["organization_id"] == b.id
    only_b = client.get("/leads/workspace-summary", headers=_h(db, world["admin_b"])).json()
    assert only_b["total_leads"] == 2
    # a header naming a workspace the caller does not hold changes nothing
    forged = client.get("/leads/workspace-summary",
                        headers=_h(db, world["admin_b"], workspace=a)).json()
    assert forged["organization_id"] == b.id and forged["total_leads"] == 2


def test_workspace_summary_refuses_without_a_customer(client, db_session):
    god = _user(db_session, None, "god_admin", "god")
    r = client.get("/leads/workspace-summary", headers=_h(db_session, god))
    assert r.status_code in (403, 409), r.text
    assert "total_leads" not in r.text


def test_workspace_summary_replies_are_tenant_scoped(client, world):
    from app.models.models import Reply
    db, a, b = world["db"], world["a"], world["b"]
    la = _lead(db, a)
    lb = _lead(db, b)
    t = _lead(db, a, is_test=True)
    for lead in (lb, t):
        db.add(Reply(lead_id=lead.id, body="hi", received_at=datetime.utcnow()))
    db.commit()
    s = client.get("/leads/workspace-summary", headers=_h(db, world["admin_a"])).json()
    assert s["recently_active_7d"] == 0
    db.add(Reply(lead_id=la.id, body="hi", received_at=datetime.utcnow()))
    db.commit()
    s = client.get("/leads/workspace-summary", headers=_h(db, world["admin_a"])).json()
    assert s["recently_active_7d"] == 1
    # god acting inside tenant b sees b only
    god = _user(db, None, "god_admin", "god2")
    sb = client.get("/leads/workspace-summary", headers=_h(db, god, override=b)).json()
    assert sb["organization_id"] == b.id and sb["total_leads"] == 1
