# -*- coding: utf-8 -*-
"""Production 2026-10-20 (second report): wholesale@ reached the GENERAL
EvoSys platform dashboard for its Wholesale workspace, whose sidebar carried
SCI's "Family Service Center". The redirect alone was not isolation.

Pins, server-side:
  * every workspace context names its experience (wholesale/sci/roofing/
    energy/platform) for THIS person;
  * while a Wholesale-seat login works in its Wholesale workspace, the
    general platform APIs are refused (403) and Wholesale + the shared shell
    core keep working - direct API access, not just menus;
  * the same login, switched into SCI or Atlantis, gets those workspaces as
    before (no Wholesale lock bleeding into them);
  * SCI staff, other users of the same org without a seat, and god/super_admin
    are never locked.
"""
import itertools
import json
import uuid

import pytest

from app.models.models import Organization, User
from app.models.sales_models import Membership
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)
EVO_FEATURES = ["ai_assist", "availability", "booking", "branding_settings", "cadences", "calendar", "campaigns",
                "compliance", "crm", "email", "imports", "leads", "master_dashboard", "reports", "sms", "users",
                "wholesale_real_estate"]
# General platform APIs a Wholesale login must not reach in its Wholesale workspace.
PLATFORM_APIS = ["/leads/", "/leads/status-funnel", "/pipeline/forecast", "/outcomes/summary", "/campaigns/",
                 "/communications/replies", "/reports/cadence-outcomes", "/activity/sent?limit=8&days=7"]
# What the Wholesale shell needs.
SHELL_APIS = ["/wholesale/settings", "/notifications/", "/settings/profile", "/settings/my-capabilities",
              "/branding/org", "/auth/my-contexts", "/program/status", "/workspace-views"]


def _org(db, name, slug, industry, feats):
    o = Organization(name=name, slug=slug if slug == "evo-integrated-solutions-llc" else "%s-%s" % (slug, uuid.uuid4().hex[:4]),
                     plan="trial", industry=industry, is_active=True, enabled_features=json.dumps(feats))
    db.add(o)
    db.commit()
    return o


def _user(db, org, email, role="org_admin"):
    u = User(organization_id=org.id if org else None, email=email, full_name=email.split("@")[0], role=role,
             password_hash=hash_password("Pass12345!"), is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _m(db, u, st, sid, role="org_admin"):
    db.add(Membership(user_id=u.id, scope_type=st, scope_id=sid, role=role, is_active=True))
    db.commit()


def _h(db, u, ws=None):
    h = {"Authorization": "Bearer %s" % create_access_token(u, db)}
    if ws:
        h["X-Workspace-Id"] = ws
    return h


@pytest.fixture()
def w(db_session):
    db = db_session
    evo = _org(db, "EVO Integrated Solutions LLC", "evo-integrated-solutions-llc", "generic", EVO_FEATURES)
    atl = _org(db, "Atlantis Light & Power", "atlantis-light-power", "energy", ["leads", "reports", "users"])
    sci = _org(db, "Service Corporation International", "service-corporation-international", "funeral",
               ["leads", "reports", "users", "sms", "email", "cadences"])
    from app.services.programs import setup as PS
    PS.ensure_program(db, sci, name="SCI Family Service Center")
    db.commit()
    ws = _user(db, evo, "wholesale-%d@example.com" % next(_SEQ))
    for o in (evo, atl, sci):
        _m(db, ws, "customer_org", o.id)
    _m(db, ws, "product", "wholesale", "member")
    evo_staff = _user(db, evo, "evo-staff-%d@example.com" % next(_SEQ))      # same org, no seat
    _m(db, evo_staff, "customer_org", evo.id)
    sci_staff = _user(db, sci, "sci-%d@example.com" % next(_SEQ))
    _m(db, sci_staff, "customer_org", sci.id)
    sup = _user(db, evo, "super-%d@example.com" % next(_SEQ), role="super_admin")
    return dict(db=db, evo=evo, atl=atl, sci=sci, ws=ws, evo_staff=evo_staff, sci_staff=sci_staff, sup=sup)


def test_contexts_name_each_workspace_experience(client, w):
    db = w["db"]
    c = client.get("/auth/my-contexts", headers=_h(db, w["ws"])).json()
    exp = {x["organization_id"]: x["experience"] for x in c["workspace_contexts"]}
    assert exp == {w["evo"].id: "wholesale", w["atl"].id: "energy", w["sci"].id: "sci"}
    c2 = client.get("/auth/my-contexts", headers=_h(db, w["evo_staff"])).json()
    assert c2["workspace_contexts"][0]["experience"] == "platform"     # no seat -> not Wholesale


def test_wholesale_workspace_refuses_general_platform_apis(client, w):
    db, ws, evo = w["db"], w["ws"], w["evo"]
    for path in PLATFORM_APIS:
        for hdr in (_h(db, ws), _h(db, ws, ws=evo.id)):          # header or home fallback
            r = client.get(path, headers=hdr)
            assert r.status_code == 403, (path, r.status_code)
            assert "Wholesale" in r.json()["detail"]


def test_wholesale_shell_and_screens_still_work(client, w):
    db, ws, evo = w["db"], w["ws"], w["evo"]
    for path in SHELL_APIS:
        r = client.get(path, headers=_h(db, ws, ws=evo.id))
        assert r.status_code == 200, (path, r.status_code, r.text[:160])


def test_switching_into_sci_or_atlantis_is_not_locked(client, w):
    db, ws = w["db"], w["ws"]
    st = client.get("/program/status", headers=_h(db, ws, ws=w["sci"].id)).json()
    assert st.get("active") is True                                   # SCI opens SCI
    assert client.get("/program/me", headers=_h(db, ws, ws=w["sci"].id)).status_code == 200
    assert client.get("/leads/", headers=_h(db, ws, ws=w["atl"].id)).status_code == 200
    # SCI's program API never answers from the Wholesale workspace
    assert client.get("/program/me", headers=_h(db, ws, ws=w["evo"].id)).status_code in (403, 404)


def test_others_are_never_locked(client, w):
    db = w["db"]
    assert client.get("/leads/", headers=_h(db, w["evo_staff"])).status_code == 200
    assert client.get("/leads/", headers=_h(db, w["sci_staff"])).status_code == 200
    assert client.get("/program/status", headers=_h(db, w["sci_staff"])).json().get("active") is True
    assert client.get("/leads/", headers=_h(db, w["sup"])).status_code == 200
    god = _user(db, None, "god-%d@example.com" % next(_SEQ), role="god_admin")
    assert client.get("/leads/", headers={**_h(db, god), "X-Org-Override": w["evo"].id}).status_code == 200


def test_cross_program_direct_api_isolation(client, w):
    db = w["db"]
    # SCI staff cannot reach Wholesale or EVO by naming it
    assert client.get("/wholesale/settings", headers=_h(db, w["sci_staff"], ws=w["evo"].id)).status_code in (402, 403)
    # Wholesale cannot read SCI data while standing in Wholesale
    assert client.get("/program/command", headers=_h(db, w["ws"], ws=w["evo"].id)).status_code in (403, 404)


def test_insurance_workspace_is_named_from_its_explicit_entitlement(client, w):
    """Unified launcher: an agency workspace that EXPLICITLY holds insurance_agency
    is the Insurance program; an insurance-industry org with a legacy NULL list is
    not. Naming the experience grants nothing - membership still decides."""
    db = w["db"]
    ins = _org(db, "Max Life Demo Agency (DEMO)", "max-life-demo-agency", "insurance",
               ["leads", "insurance_agency", "users"])
    legacy = Organization(name="Legacy Agency", slug="legacy-%s" % uuid.uuid4().hex[:4], plan="trial",
                          industry="insurance", is_active=True, enabled_features=None)
    db.add(legacy)
    db.commit()
    _m(db, w["ws"], "customer_org", ins.id)
    _m(db, w["ws"], "customer_org", legacy.id)
    c = client.get("/auth/my-contexts", headers=_h(db, w["ws"])).json()
    exp = {x["organization_id"]: x["experience"] for x in c["workspace_contexts"]}
    assert exp[ins.id] == "insurance" and exp[legacy.id] == "platform"
    assert exp[w["sci"].id] == "sci" and exp[w["atl"].id] == "energy"
    # someone without a membership never sees it
    c2 = client.get("/auth/my-contexts", headers=_h(db, w["sci_staff"])).json()
    assert ins.id not in {x["organization_id"] for x in c2["workspace_contexts"]}
