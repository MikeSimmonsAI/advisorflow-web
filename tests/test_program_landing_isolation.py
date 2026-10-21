# -*- coding: utf-8 -*-
"""Production incident 2026-10-20: wholesale@ logged in and saw
"Roofing isn't turned on for this workspace."

Root cause: (1) the EvoSys Pro Roofing org exists but its Roofing program was
never switched on, and (2) routing sent this login there anyway - by slug, by
a returned-to page, and because the account had no home of its own.

These tests pin the fix on the server, which is the authority for where a
login lands and what each workspace may open:
  * wholesale@ (named Wholesale seat on its own home org) lands in WHOLESALE,
    through the verified product hand-off on its home org - never SCI/Roofing;
  * a Roofing org opens the Roofing app ONLY when roofing_program is on;
  * SCI-only and multi-workspace logins land exactly where they did before;
  * cross-tenant: a selected workspace never lends its data or features to
    another program's endpoints.
Shape mirrors production (names / slugs / feature lists), no customer data.
"""
import itertools
import json
import uuid

import pytest

from app.models.models import Organization, User
from app.models.sales_models import Membership
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)
EVO_FEATURES = ["ai_assist", "audit_log", "availability", "booking", "branding_settings", "cadences", "calendar",
                "campaigns", "case_files", "compliance", "crm", "crm_connectors", "email", "imports", "lead_cleanup",
                "leads", "master_dashboard", "reports", "sms", "tier_config", "users", "voice",
                "wholesale_real_estate"]
ROOF_OFF = ["availability", "booking", "branding_settings", "calendar", "imports", "leads", "reports", "users"]


def _org(db, name, slug, industry, features):
    o = Organization(name=name, slug="%s-%s" % (slug, uuid.uuid4().hex[:4]) if slug != "evo-integrated-solutions-llc"
                     else slug, plan="trial", industry=industry, is_active=True,
                     enabled_features=json.dumps(features))
    db.add(o)
    db.commit()
    return o


def _user(db, org, email, role="org_admin"):
    u = User(organization_id=org.id if org else None, email=email, full_name=email.split("@")[0], role=role,
             password_hash=hash_password("Pass12345!"), is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _m(db, u, scope_type, scope_id, role="org_admin"):
    db.add(Membership(user_id=u.id, scope_type=scope_type, scope_id=scope_id, role=role, is_active=True))
    db.commit()


def _h(db, u, ws=None):
    h = {"Authorization": "Bearer %s" % create_access_token(u, db)}
    if ws:
        h["X-Workspace-Id"] = ws
    return h


@pytest.fixture()
def shape(db_session):
    db = db_session
    evo = _org(db, "EVO Integrated Solutions LLC", "evo-integrated-solutions-llc", "generic", EVO_FEATURES)
    atl = _org(db, "Atlantis Light & Power", "atlantis-light-power", "energy", ["leads", "reports", "users"])
    sci = _org(db, "Service Corporation International", "service-corporation-international", "funeral",
               ["audit_log", "branding_settings", "cadences", "email", "imports", "leads", "reports", "sms", "users"])
    roof = _org(db, "EvoSys Pro Roofing", "evosys-pro-roofing", "generic", ROOF_OFF)
    from app.services.programs import setup as PS
    PS.ensure_program(db, sci, name="SCI Family Service Center")
    db.commit()
    w = _user(db, evo, "wholesale-%d@example.com" % next(_SEQ))
    for o in (evo, atl, sci, roof):
        _m(db, w, "customer_org", o.id)
    _m(db, w, "product", "wholesale", "member")
    sci_only = _user(db, sci, "sci-staff-%d@example.com" % next(_SEQ))
    _m(db, sci_only, "customer_org", sci.id)
    multi = _user(db, atl, "multi-%d@example.com" % next(_SEQ))
    _m(db, multi, "customer_org", atl.id)
    _m(db, multi, "customer_org", sci.id)
    return dict(db=db, evo=evo, atl=atl, sci=sci, roof=roof, w=w, sci_only=sci_only, multi=multi)


def _ctx(client, db, u):
    r = client.get("/auth/my-contexts", headers=_h(db, u))
    assert r.status_code == 200, r.text
    return r.json()


# ── WHOLESALE ───────────────────────────────────────────────────────────────
def test_wholesale_login_lands_in_wholesale_on_its_home_org(client, shape):
    db, w, evo = shape["db"], shape["w"], shape["evo"]
    c = _ctx(client, db, w)
    d = c["default_context"]
    assert d["type"] == "product" and d["key"] == "wholesale"
    assert d["organization_id"] == evo.id
    assert d["path"] == "/workspace/%s?product=wholesale" % evo.id
    assert c["program_only"] is False
    # every workspace it held is still offered (nothing revoked)
    assert {x["organization_id"] for x in c["workspace_contexts"]} == {
        evo.id, shape["atl"].id, shape["sci"].id, shape["roof"].id}
    # and the product is hosted ONLY on its home org
    assert [p["organization_id"] for p in c["product_contexts"]] == [evo.id]


def test_wholesale_endpoints_run_in_evo_and_refuse_other_tenants(client, shape):
    db, w = shape["db"], shape["w"]
    assert client.get("/wholesale/settings", headers=_h(db, w, ws=shape["evo"].id)).status_code == 200
    for other in ("sci", "roof", "atl"):
        r = client.get("/wholesale/settings", headers=_h(db, w, ws=shape[other].id))
        assert r.status_code in (402, 403), (other, r.status_code)


# ── ROOFING ─────────────────────────────────────────────────────────────────
def test_roofing_org_without_program_is_not_routed_to_roofing_app(client, shape):
    db, w, roof = shape["db"], shape["w"], shape["roof"]
    entry = next(x for x in _ctx(client, db, w)["workspace_contexts"] if x["organization_id"] == roof.id)
    assert entry["program_route"] is None
    # the server still refuses the roofing API there - no data, no bypass
    assert client.get("/roofing-ops/status", headers=_h(db, w, ws=roof.id)).status_code in (402, 403)


def test_roofing_org_with_program_on_opens_roofing_and_stays_isolated(client, shape):
    db, w, roof = shape["db"], shape["w"], shape["roof"]
    roof.enabled_features = json.dumps(ROOF_OFF + ["roofing_program"])
    db.commit()
    entry = next(x for x in _ctx(client, db, w)["workspace_contexts"] if x["organization_id"] == roof.id)
    assert entry["program_route"] == "/program/roofing"
    st = client.get("/roofing-ops/status", headers=_h(db, w, ws=roof.id))
    assert st.status_code == 200 and st.json()["organization_id"] == roof.id
    # login still lands in Wholesale - Roofing is a switch away, not the home
    assert _ctx(client, db, w)["default_context"]["key"] == "wholesale"
    # roofing never answers for another workspace
    for other in ("evo", "sci", "atl"):
        assert client.get("/roofing-ops/status", headers=_h(db, w, ws=shape[other].id)).status_code in (402, 403)


# ── SCI ─────────────────────────────────────────────────────────────────────
def test_sci_routing_unchanged(client, shape):
    db, sci = shape["db"], shape["sci"]
    c = _ctx(client, db, shape["sci_only"])
    assert c["default_context"]["type"] == "workspace" and c["default_context"]["organization_id"] == sci.id
    assert c["product_contexts"] == []
    entry = c["workspace_contexts"][0]
    assert entry["is_program"] is True and entry["program_route"] is None
    # multi-workspace login without a product seat still gets the selector
    assert _ctx(client, db, shape["multi"])["default_context"]["type"] == "workspace_selector"
    # the SCI program API answers inside SCI only
    assert client.get("/program/status", headers=_h(db, shape["sci_only"])).json().get("active") is True


# ── CROSS-TENANT ────────────────────────────────────────────────────────────
def test_cross_tenant_selection_is_ignored_server_side(client, shape):
    db = shape["db"]
    # SCI staff naming EVO (not theirs) -> header discarded -> SCI, never EVO data/features
    assert client.get("/wholesale/settings", headers=_h(db, shape["sci_only"], ws=shape["evo"].id)).status_code in (402, 403)
    assert client.get("/roofing-ops/status", headers=_h(db, shape["sci_only"], ws=shape["roof"].id)).status_code in (402, 403)
    # an outsider never sees the Wholesale product, even naming EVO
    out = _ctx(client, db, shape["multi"])
    assert out["product_contexts"] == []
    assert shape["evo"].id not in {x["organization_id"] for x in out["workspace_contexts"]}


def test_god_home_is_unchanged(client, shape):
    db = shape["db"]
    god = _user(db, None, "god-%d@example.com" % next(_SEQ), role="god_admin")
    d = _ctx(client, db, god)["default_context"]
    assert d["type"] != "product"
