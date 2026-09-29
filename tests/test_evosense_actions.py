"""EvoSense next actions: every finding says what can be done next, truthfully,
and the manual path (owner -> contact -> promote) reaches Deal Operations
through the canonical Wholesale services, exactly once, without writing consent
or calling any provider."""
import json
import socket

import pytest

from app.models.evosense_models import EvoSenseCostEntry, EvoSenseFeedback, EvoSenseProperty
from app.models.models import Lead, Organization, User
from app.models.wholesale_models import WholesaleDeal, WholesaleProperty
from app.services.auth_service import create_access_token, hash_password
from app.services.evosense import sandbox_seed as SS

BASE = "/wholesale/evosense"


def ok(r):
    assert r.status_code in (200, 201), "%s %s" % (r.status_code, r.text[:500])
    return r.json()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    # Loopback stays allowed: on Windows the event loop's self-pipe is a
    # socketpair() that connects to 127.0.0.1, which is not a network call.
    real_connect = socket.socket.connect
    real_create = socket.create_connection

    def _loopback(addr):
        host = addr[0] if isinstance(addr, tuple) else addr
        return host in ("127.0.0.1", "::1", "localhost")

    def refuse_connect(self, addr, *a, **k):
        if _loopback(addr):
            return real_connect(self, addr, *a, **k)
        raise AssertionError("network call attempted during an EvoSense actions test")

    def refuse_create(addr, *a, **k):
        if _loopback(addr):
            return real_create(addr, *a, **k)
        raise AssertionError("network call attempted during an EvoSense actions test")
    monkeypatch.setattr(socket.socket, "connect", refuse_connect)
    monkeypatch.setattr(socket, "create_connection", refuse_create)


@pytest.fixture()
def org_b(db_session):
    org = Organization(name="Org B Actions", slug="org-b-actions", plan="standard", industry="real_estate")
    db_session.add(org)
    db_session.commit()
    user = User(organization_id=org.id, email="b@orgb-actions.test", password_hash=hash_password("TestPass123!"),
                full_name="B Admin", role="org_admin", must_change_password=False)
    db_session.add(user)
    db_session.commit()
    return org, user, {"Authorization": "Bearer %s" % create_access_token(user, db_session)}


def _new_finding(client, headers, street="4410 Actions Test Ln"):
    out = ok(client.post(BASE + "/properties", headers=headers,
                         json={"street_address": street, "city": "Dallas", "state": "TX",
                               "zip_code": "75216", "county": "Dallas", "signals": ["VACANT", "TAX_DELINQUENT"]}))
    assert out["property_id"]
    return out["property_id"]


def _acts(d):
    return {a["key"]: a for a in d["actions"]}


def test_actions_are_truthful_for_a_finding_with_no_owner(client, auth_headers, db_session):
    pid = _new_finding(client, auth_headers)
    d = ok(client.get(BASE + "/properties/%s" % pid, headers=auth_headers))
    a = _acts(d)
    for a_ in d["actions"]:
        assert set(a_) >= {"key", "label", "enabled", "reason_if_disabled", "effect_description", "endpoint"}
        assert a_["enabled"] or a_["reason_if_disabled"], a_["key"]
    assert a["add_owner"]["enabled"] and a["add_owner"]["endpoint"].endswith("/owner")
    assert not a["run_enrichment"]["enabled"]
    assert "no owner" in a["run_enrichment"]["reason_if_disabled"].lower()
    assert not a["add_contact_manual"]["enabled"]
    assert a["promote"]["enabled"]                         # identity confirmed; contact only recommended
    assert "open_deal" not in a
    assert d["next"]["key"] == "add_owner"
    s = d["summary"]
    assert s["found"] and s["next"] and s["after"]
    assert any("Owner of record" in m for m in s["missing"])
    assert any("ARV" in m for m in s["missing"])


def test_lookup_says_not_configured_when_no_provider_would_be_called(client, auth_headers, db_session):
    pid = _new_finding(client, auth_headers)
    ok(client.post(BASE + "/properties/%s/owner" % pid, headers=auth_headers, json={"name": "Jordan Q Testowner"}))
    d = ok(client.get(BASE + "/properties/%s" % pid, headers=auth_headers))
    a = _acts(d)
    assert not a["run_enrichment"]["enabled"]
    assert "NOT CONFIGURED" in a["run_enrichment"]["reason_if_disabled"]
    assert a["add_contact_manual"]["enabled"] and d["next"]["key"] == "add_contact_manual"
    # a second owner is refused (correction is a different act)
    r = client.post(BASE + "/properties/%s/owner" % pid, headers=auth_headers, json={"name": "Someone Else"})
    assert r.status_code == 409


def test_manual_path_promotes_exactly_one_deal_with_the_seller_and_no_consent(client, auth_headers,
                                                                             db_session, sample_org):
    pid = _new_finding(client, auth_headers)
    ok(client.post(BASE + "/properties/%s/owner" % pid, headers=auth_headers, json={"name": "Jordan Q Testowner"}))
    ok(client.post(BASE + "/properties/%s/contacts" % pid, headers=auth_headers,
                   json={"kind": "phone", "value": "(214) 555-0142", "person_name": "Jordan Testowner",
                         "role": "owner"}))
    d = ok(client.get(BASE + "/properties/%s" % pid, headers=auth_headers))
    a = _acts(d)
    assert d["readiness"]["ready"] and a["promote"]["enabled"] and a["promote"]["primary"]
    assert "seller_contact" in a and "No consent" in a["seller_contact"]["effect_description"]
    rows = ok(client.get(BASE + "/inbox", headers=auth_headers, params={"q": "Actions Test"}))["items"]
    assert rows and rows[0]["next_step"]["key"] == "promote"

    leads_before = db_session.query(Lead).filter(Lead.organization_id == sample_org.id).count()
    out = ok(client.post(BASE + "/properties/%s/promote" % pid, headers=auth_headers, json={"note": "t"}))
    assert not out["already"]
    again = ok(client.post(BASE + "/properties/%s/promote" % pid, headers=auth_headers, json={}))
    assert again["already"] and again["deal_id"] == out["deal_id"]
    db_session.expire_all()
    deals = (db_session.query(WholesaleDeal).join(WholesaleProperty, WholesaleProperty.id == WholesaleDeal.property_id)
             .filter(WholesaleProperty.organization_id == sample_org.id,
                     WholesaleProperty.street_address == "4410 Actions Test Ln").all())
    assert len(deals) == 1 and deals[0].id == out["deal_id"]
    prop = db_session.query(EvoSenseProperty).filter(EvoSenseProperty.id == pid).first()
    assert prop.promoted_deal_id == out["deal_id"] and prop.promoted_property_id == deals[0].property_id
    assert deals[0].seller_lead_id
    lead = db_session.query(Lead).filter(Lead.id == deals[0].seller_lead_id).first()
    assert lead.organization_id == sample_org.id and lead.first_name == "Jordan"
    assert not lead.sms_consent and lead.sms_consent_timestamp is None
    assert db_session.query(Lead).filter(Lead.organization_id == sample_org.id).count() == leads_before + 1

    deal = ok(client.get("/wholesale/deals/%s" % out["deal_id"], headers=auth_headers))
    assert deal["deal"]["id"] == out["deal_id"]
    d2 = ok(client.get(BASE + "/properties/%s" % pid, headers=auth_headers))
    a2 = _acts(d2)
    assert a2["open_deal"]["enabled"] and a2["open_deal"]["endpoint"] == "/wholesale/deals/%s" % out["deal_id"]
    assert "promote" not in a2 and not a2["add_contact_manual"]["enabled"]
    assert d2["next"]["key"] == "open_deal"
    assert db_session.query(EvoSenseCostEntry).filter(EvoSenseCostEntry.property_id == pid).count() == 0


def test_dismiss_records_the_reason(client, auth_headers, db_session):
    pid = _new_finding(client, auth_headers)
    assert client.post(BASE + "/properties/%s/dismiss" % pid, headers=auth_headers,
                       json={"reason": "  "}).status_code == 422
    ok(client.post(BASE + "/properties/%s/dismiss" % pid, headers=auth_headers,
                   json={"reason": "Owner already listed with an agent", "kind": "BAD_FIT"}))
    fb = db_session.query(EvoSenseFeedback).filter(EvoSenseFeedback.property_id == pid).all()
    assert len(fb) == 1 and fb[0].kind == "BAD_FIT" and fb[0].reason == "Owner already listed with an agent"
    assert fb[0].user_id
    d = ok(client.get(BASE + "/properties/%s" % pid, headers=auth_headers))
    assert d["dismissed"]["reason"] == "Owner already listed with an agent"
    assert not _acts(d)["dismiss"]["enabled"]
    rows = ok(client.get(BASE + "/inbox", headers=auth_headers, params={"q": "Actions Test"}))["items"]
    assert rows[0]["next_step"]["key"] == "dismissed"


def test_sandbox_finding_offers_the_sandbox_lookup_without_charge_claims(client, auth_headers, db_session,
                                                                        sample_org, sample_advisor):
    SS.seed_review(db_session, sample_org.id, sample_advisor, replies=True)
    db_session.commit()
    pid = SS.prop_at(db_session, sample_org.id, "1418 Cedar Springs Rd").id
    d = ok(client.get(BASE + "/properties/%s" % pid, headers=auth_headers))
    a = _acts(d)
    assert "review_evidence" in a and a["promote"]["enabled"]
    assert "acknowledge_handoff" in a                       # the flagship has an open hand-off


def test_cross_tenant_is_404_for_every_action(client, auth_headers, db_session, org_b):
    pid = _new_finding(client, auth_headers)
    org, _, hb = org_b
    org.enabled_features = db_session.query(Organization).filter(
        Organization.id != org.id, Organization.slug == "restland").first().enabled_features
    db_session.commit()
    for method, path, body in (("get", "/properties/%s" % pid, None),
                               ("post", "/properties/%s/owner" % pid, {"name": "X Y"}),
                               ("post", "/properties/%s/dismiss" % pid, {"reason": "x"}),
                               ("post", "/properties/%s/promote" % pid, {})):
        r = getattr(client, method)(BASE + path, headers=hb, **({"json": body} if body is not None else {}))
        assert r.status_code == 404, (path, r.status_code)
