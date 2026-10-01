# -*- coding: utf-8 -*-
"""Entitlement matrix for the two reusable vertical features (spec section 50).

    wholesale_real_estate   /wholesale/*  and  /wholesale/evosense/*
    insurance_agency        /agency/*

For each: an entitled workspace uses it; a non-entitled one is refused BY THE
API (402/403), not merely hidden in the menu; a BookaBoost-brand org may hold
Wholesale when entitled (and loses it when the brand switches it off); guessed
ids from another tenant are 404; counts never include another tenant; a
dual-role person switching workspace gets each workspace's own answer.
"""
import itertools
import json
import uuid

import pytest

import app.models.agency_models  # noqa: F401
import app.models.wholesale_models  # noqa: F401
from app.models.models import Lead, Organization, Platform, User
from app.models.sales_models import Membership
from app.models.wholesale_models import WholesaleDeal, WholesaleProperty
from app.services import entitlement_resolver as er
from app.services import entitlements as ent
from app.services.auth_service import create_access_token, hash_password

import agency_support as AG

_SEQ = itertools.count(1)


@pytest.fixture(autouse=True)
def _mount():
    AG.mount()
    yield


def _org(db, name, features, platform=None):
    o = Organization(name=name, slug="em-%s" % uuid.uuid4().hex[:8], plan="enterprise",
                     is_active=True, platform_id=platform.id if platform else None,
                     enabled_features=json.dumps(list(features)))
    db.add(o)
    db.commit()
    return o


def _user(db, org, role="org_admin"):
    u = User(organization_id=org.id if org else None,
             email="em-%d-%s@t.test" % (next(_SEQ), uuid.uuid4().hex[:4]),
             password_hash=hash_password("Pass12345!"), full_name="EM User", role=role,
             is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _h(db, u, workspace=None):
    h = {"Authorization": "Bearer " + create_access_token(u, db)}
    if workspace:
        h["X-Workspace-Id"] = workspace
    return h


def _deal(db, org, addr):
    p = WholesaleProperty(organization_id=org.id, street_address=addr, city="Dallas",
                          state="TX", zip_code="75201")
    db.add(p)
    db.flush()
    d = WholesaleDeal(organization_id=org.id, property_id=p.id, stage="new_property")
    db.add(d)
    db.commit()
    return p, d


WS = ("leads", "wholesale_real_estate")
AGY = ("leads", "insurance_agency")

WHOLESALE_GETS = ("/wholesale/deals", "/wholesale/properties", "/wholesale/dashboard",
                  "/wholesale/evosense/command-center", "/wholesale/evosense/inbox")
AGENCY_GETS = ("/agency/summary", "/agency/prospects", "/agency/applications",
               "/agency/policies", "/agency/recruits", "/agency/attention")


def _refused(r):
    return r.status_code in (402, 403)


# ── entitled vs not entitled, on the API ────────────────────────────────────

def test_wholesale_entitled_uses_it_and_unentitled_is_refused_by_api(client, db_session):
    db = db_session
    yes = _user(db, _org(db, "WS Yes", WS))
    no = _user(db, _org(db, "WS No", ("leads",)))
    for path in WHOLESALE_GETS:
        assert client.get(path, headers=_h(db, yes)).status_code == 200, path
        assert _refused(client.get(path, headers=_h(db, no))), path
    # mutations are gated too, before any body validation
    assert _refused(client.post("/wholesale/evosense/properties", headers=_h(db, no), json={}))
    assert ent.org_has_feature(db.query(Organization).get(no.organization_id),
                               "wholesale_real_estate") is False


def test_agency_entitled_uses_it_and_unentitled_is_refused_by_api(client, db_session):
    db = db_session
    yes = _user(db, _org(db, "AG Yes", AGY))
    no = _user(db, _org(db, "AG No", ("leads", "wholesale_real_estate")))
    for path in AGENCY_GETS:
        assert client.get(path, headers=_h(db, yes)).status_code == 200, path
        assert _refused(client.get(path, headers=_h(db, no))), path
    assert _refused(client.post("/agency/recruits", headers=_h(db, no), json={"name": "X"}))
    assert _refused(client.post("/agency/ask", headers=_h(db, no), json={"question": "x"}))


def test_navigation_matches_api(client, db_session):
    """What /branding/org reports as enabled is what the API lets through."""
    db = db_session
    for feats in (WS, AGY, ("leads",)):
        u = _user(db, _org(db, "Nav %s" % "-".join(feats), feats))
        body = client.get("/branding/org", headers=_h(db, u)).json()
        nav = json.dumps(body)
        for key, path in (("wholesale_real_estate", "/wholesale/deals"),
                          ("insurance_agency", "/agency/summary")):
            api_ok = client.get(path, headers=_h(db, u)).status_code == 200
            assert api_ok == (key in feats), (feats, key)
            # the feature key is only advertised where the API serves it
            if not api_ok:
                enabled = body.get("enabled_features") or body.get("features") or []
                if isinstance(enabled, list):
                    assert key not in enabled, (feats, key, nav[:300])


# ── BookaBoost brand ─────────────────────────────────────────────────────────

def test_bookaboost_brand_org_can_hold_wholesale_and_brand_disable_reaches_it(client, db_session):
    db = db_session
    bb = Platform(name="BookaBoost", slug="bb-%s" % uuid.uuid4().hex[:6])
    db.add(bb)
    db.commit()
    god = _user(db, None)
    god.role = "god_admin"
    db.commit()
    bb_ws = _user(db, _org(db, "BB Wholesaler", WS, platform=bb))
    bb_plain = _user(db, _org(db, "BB Plain", ("leads",), platform=bb))
    assert client.get("/wholesale/deals", headers=_h(db, bb_ws)).status_code == 200
    assert _refused(client.get("/wholesale/deals", headers=_h(db, bb_plain)))
    # BookaBoost-only data stays BookaBoost's: another brand-less tenant sees none of it
    _deal(db, db.query(Organization).get(bb_ws.organization_id), "1 Boost St")
    other = _user(db, _org(db, "Mike WS", WS))
    _deal(db, db.query(Organization).get(other.organization_id), "9 Mike Rd")
    assert "1 Boost St" not in client.get("/wholesale/deals", headers=_h(db, other)).text
    assert "9 Mike Rd" not in client.get("/wholesale/deals", headers=_h(db, bb_ws)).text
    # the brand switches Wholesale off -> every org of the brand loses the API
    er.set_override(db, scope="brand", scope_id=bb.id, feature_key="wholesale_real_estate",
                    state="disabled", actor_user_id=god.id)
    db.commit()
    assert _refused(client.get("/wholesale/deals", headers=_h(db, bb_ws)))
    # ...and only that brand's orgs
    assert client.get("/wholesale/deals", headers=_h(db, other)).status_code == 200


# ── cross-tenant ids and counts ──────────────────────────────────────────────

def test_wholesale_guessed_ids_404_and_counts_are_tenant_only(client, db_session):
    db = db_session
    a = _user(db, _org(db, "WS A", WS))
    b = _user(db, _org(db, "WS B", WS))
    oa = db.query(Organization).get(a.organization_id)
    ob = db.query(Organization).get(b.organization_id)
    _deal(db, oa, "1 A St")
    _deal(db, oa, "2 A St")
    pb, db_ = _deal(db, ob, "7 B St")
    ha = _h(db, a)
    assert client.get(f"/wholesale/deals/{db_.id}", headers=ha).status_code == 404
    assert client.get(f"/wholesale/deals/{db_.id}/offers", headers=ha).status_code == 404
    assert client.get(f"/wholesale/properties/{pb.id}/enrichment", headers=ha).status_code == 404
    deals = client.get("/wholesale/deals", headers=ha).json()
    text = json.dumps(deals)
    assert "7 B St" not in text and "1 A St" in text
    total = deals.get("total") if isinstance(deals, dict) else len(deals)
    if total is not None:
        assert total == 2
    assert client.get("/wholesale/evosense/properties/does-not-exist", headers=ha).status_code == 404


def test_agency_guessed_ids_404_and_counts_are_tenant_only(client, db_session):
    db = db_session
    oa, ob = _org(db, "AG A", AGY), _org(db, "AG B", AGY)
    a, b = _user(db, oa), _user(db, ob)
    for i in range(3):
        AG.lead(db, oa, first="A%d" % i)
    lb = AG.lead(db, ob, first="Bee")
    r = client.post("/agency/applications", headers=_h(db, b), json={"prospect_id": lb.id, "agent_id": b.id})
    assert r.status_code == 201
    app_b = r.json()["id"]
    ha = _h(db, a)
    assert client.get(f"/agency/prospects/{lb.id}", headers=ha).status_code == 404
    assert client.get(f"/agency/applications/{app_b}", headers=ha).status_code == 404
    assert client.post(f"/agency/applications/{app_b}/transition", headers=ha,
                       json={"to": "submitted"}).status_code == 404
    assert client.get("/agency/prospects", headers=ha).json()["total"] == 3
    assert client.get("/agency/applications", headers=ha).json()["total"] == 0
    s = client.get("/agency/summary", headers=ha)
    assert s.status_code == 200 and "Bee" not in s.text


# ── dual-role person switching workspace context ─────────────────────────────

def test_dual_role_user_gets_each_workspaces_own_answer(client, db_session):
    from app.services import workspace_access
    db = db_session
    ws_org = _org(db, "Dual WS", WS)
    ag_org = _org(db, "Dual AG", AGY)
    p = _user(db, ws_org)
    workspace_access.grant_workspace_membership(db, p.id, ws_org.id, role="org_admin")
    workspace_access.grant_workspace_membership(db, p.id, ag_org.id, role="org_admin")
    AG.lead(db, ag_org, first="AgencyOnly")
    _deal(db, ws_org, "5 Dual Ln")
    # wholesale workspace selected
    hw = _h(db, p, workspace=ws_org.id)
    assert client.get("/wholesale/deals", headers=hw).status_code == 200
    assert _refused(client.get("/agency/summary", headers=hw))
    # agency workspace selected
    hg = _h(db, p, workspace=ag_org.id)
    assert client.get("/agency/summary", headers=hg).status_code == 200
    assert _refused(client.get("/wholesale/deals", headers=hg))
    pros = client.get("/agency/prospects", headers=hg).json()
    assert pros["total"] == 1 and pros["items"][0]["name"].startswith("AgencyOnly")
    # selecting a workspace the person does NOT hold grants nothing
    stranger = _org(db, "Stranger AG", AGY)
    AG.lead(db, stranger, first="StrangerLead")
    r = client.get("/agency/prospects", headers=_h(db, p, workspace=stranger.id))
    assert "StrangerLead" not in r.text


def test_customer_workspace_role_is_not_back_office(client, db_session):
    """An org_admin of an entitled customer is still refused platform-owner routes."""
    db = db_session
    u = _user(db, _org(db, "Cust", WS + ("insurance_agency",)))
    assert client.get("/god/email/inbound-mailboxes", headers=_h(db, u)).status_code in (401, 403)
    assert client.get("/god/entitlements/matrix", headers=_h(db, u),
                      params={"scope": "platform"}).status_code in (401, 403, 404)
